"""fprime-openc3: install the generated COSMOS plugin and run fprime-comm-bridge

The plugin is regenerated from the dictionary on every run. COSMOS is only asked to install it when the
dictionary digest differs from the plugin already installed, so repeated launches are quick. Arguments
not recognized here are forwarded to fprime-comm-bridge (for example --communication-selection).
"""

from __future__ import annotations

import argparse
import importlib.util
import ipaddress
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

from fprime_openc3.cosmos_api import CosmosApiError, CosmosClient
from fprime_openc3.dictionary import DictionaryError, FprimeDictionary
from fprime_openc3.generate.__main__ import add_dictionary_arguments
from fprime_openc3.items import UnsupportedTypeError
from fprime_openc3.plugin_builder import PluginArtifacts, build_plugin

BRIDGE_EXECUTABLE = "fprime-comm-bridge"
BRIDGE_MODULE = "fprime_gds.executables.comm_bridge"
DOCKER_INTERFACE_PREFIXES = ("docker", "br-")
# COSMOS receives plain F Prime packets, so the bridge strips Space Packet and Space Data Link framing
DEFAULT_FRAMING = "space-packet-space-data-link"
PASSWORD_ENVIRONMENT = "OPENC3_API_PASSWORD"  # noqa: S105 - name of the variable, not a secret


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fprime-openc3", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--dictionary", type=Path, required=True, help="F Prime JSON topology dictionary")
    add_dictionary_arguments(parser)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("openc3-plugin"),
        help="Where the plugin is generated (default: %(default)s)",
    )
    cosmos = parser.add_argument_group("COSMOS")
    cosmos.add_argument("--cosmos-url", default="http://localhost:2900", help="COSMOS base URL (default: %(default)s)")
    cosmos.add_argument(
        "--cosmos-password",
        default=os.environ.get(PASSWORD_ENVIRONMENT),
        help=f"COSMOS password (default: ${PASSWORD_ENVIRONMENT})",
    )
    cosmos.add_argument("--cosmos-scope", default="DEFAULT", help="COSMOS scope (default: %(default)s)")
    cosmos.add_argument(
        "--cosmos-variable",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="Override a plugin variable (repeatable)",
    )
    cosmos.add_argument(
        "--force-install", action="store_true", help="Reinstall the plugin even if this dictionary is already installed"
    )
    cosmos.add_argument("--skip-install", action="store_true", help="Generate the plugin but do not talk to COSMOS")
    bridge = parser.add_argument_group("bridge")
    bridge.add_argument("--skip-bridge", action="store_true", help="Do not start fprime-comm-bridge")
    bridge.add_argument(
        "--framing-selection", default=DEFAULT_FRAMING, help="fprime-comm-bridge framing (default: %(default)s)"
    )
    return parser


def parse_variables(pairs: list[str]) -> dict[str, str]:
    variables = {}
    for pair in pairs:
        name, separator, value = pair.partition("=")
        if not separator or not name:
            raise ValueError(f"Plugin variable must be NAME=VALUE, got '{pair}'")
        variables[name] = value
    return variables


def ensure_installed(client: CosmosClient, artifacts: PluginArtifacts, variables: dict[str, str], force: bool) -> None:
    """Install the gem unless a plugin built from the same dictionary digest is already present"""
    client.authenticate()
    if not force and client.find_plugin(artifacts.plugin_prefix):
        print(
            f"[INFO] COSMOS already has {artifacts.plugin_prefix}; skipping install (use --force-install to override)"
        )
        return
    existing = client.find_plugin(f"{artifacts.gem_name}-")
    action = f"Upgrading {existing}" if existing else "Installing"
    print(f"[INFO] {action} {artifacts.gem_path.name} into COSMOS scope {client.scope}")
    name = client.install_gem(str(artifacts.gem_path), variables, existing)
    print(f"[INFO] Installed {name}")


def docker_gateway_addresses() -> list[str]:
    """IPv4 addresses of local Docker bridge interfaces"""
    try:
        output = subprocess.run(["ip", "-4", "-o", "addr"], check=True, capture_output=True, text=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return []
    addresses = []
    for line in output.splitlines():
        fields = line.split()
        if len(fields) >= 4 and fields[1].startswith(DOCKER_INTERFACE_PREFIXES):
            addresses.append(str(ipaddress.ip_interface(fields[3]).ip))
    return addresses


def docker_container_addresses() -> list[str]:
    """IPv4 addresses of the running Docker containers, i.e. the sources COSMOS sends commands from"""
    template = "{{range .NetworkSettings.Networks}}{{println .IPAddress}}{{end}}"
    try:
        containers = subprocess.run(["docker", "ps", "-q"], check=True, capture_output=True, text=True).stdout.split()
        if not containers:
            return []
        command = ["docker", "inspect", "--format", template, *containers]
        output = subprocess.run(command, check=True, capture_output=True, text=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return []
    return [line.strip() for line in output.splitlines() if line.strip()]


def docker_source_addresses() -> list[str]:
    """Addresses a local Docker COSMOS may send from: container addresses plus the bridge gateways"""
    return list(dict.fromkeys([*docker_container_addresses(), *docker_gateway_addresses()]))


def bridge_defaults(cosmos_url: str, extra: list[str]) -> list[str]:
    """Bridge arguments letting a COSMOS running in local Docker containers reach the bridge

    Only applied when COSMOS is local and the user has not configured the UDP side themselves.
    """
    host = cosmos_url.split("://", 1)[-1].split("/", 1)[0].rsplit(":", 1)[0]
    try:
        local = host in ("localhost", "127.0.0.1") or socket.gethostbyname(host) == socket.gethostbyname(
            socket.gethostname()
        )
    except OSError:
        local = False
    if not local or any(
        argument.startswith(("--udp-fast-bind-address", "--udp-fast-allowed-source")) for argument in extra
    ):
        return []
    sources = docker_source_addresses()
    if not sources:
        return []
    return ["--udp-fast-bind-address", "0.0.0.0", "--udp-fast-allowed-source", *sources]  # noqa: S104


def bridge_command() -> list[str] | None:
    """Prefer the bridge from this interpreter's environment so the launcher works without PATH activation."""
    if importlib.util.find_spec(BRIDGE_MODULE) is not None:
        return [sys.executable, "-m", BRIDGE_MODULE]
    executable = shutil.which(BRIDGE_EXECUTABLE)
    return None if executable is None else [executable]


def run_bridge(dictionary: FprimeDictionary, framing: str, extra: list[str]) -> int:
    command = bridge_command()
    if command is None:
        print(f"[ERROR] {BRIDGE_EXECUTABLE} not found; install fprime-gds", file=sys.stderr)
        return 1
    command += ["--dictionary", str(dictionary.path), "--framing-selection", framing, *extra]
    print(f"[INFO] Starting: {' '.join(command)}")
    try:
        return subprocess.call(command)
    except KeyboardInterrupt:
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args, bridge_arguments = parser.parse_known_args(argv)
    try:
        variables = parse_variables(args.cosmos_variable)
        dictionary = FprimeDictionary(args.dictionary, args.packet_set_name)
        artifacts = build_plugin(dictionary, args.output, args.target_name)
        print(f"[INFO] Generated {artifacts.gem_path}")
        if not args.skip_install:
            client = CosmosClient(args.cosmos_url, args.cosmos_password, args.cosmos_scope)
            ensure_installed(client, artifacts, variables, args.force_install)
    except (DictionaryError, UnsupportedTypeError, CosmosApiError, ValueError) as error:
        print(f"[ERROR] {error}", file=sys.stderr)
        return 1
    if args.skip_bridge:
        return 0
    bridge_arguments = [*bridge_defaults(args.cosmos_url, bridge_arguments), *bridge_arguments]
    return run_bridge(dictionary, args.framing_selection, bridge_arguments)


if __name__ == "__main__":
    sys.exit(main())
