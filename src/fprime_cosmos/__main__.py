"""fprime-cosmos: start an F Prime deployment against COSMOS

Generates the COSMOS plugin from the dictionary, installs it when COSMOS does not already run a plugin
built from the same dictionary digest, starts fprime-comm-bridge and, with --app, the deployment binary
connected to the bridge. Arguments not recognized here are forwarded to fprime-comm-bridge (for example
--communication-selection or --tcp-fast-port).
"""

from __future__ import annotations

import argparse
import importlib.util
import ipaddress
import shlex
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

from fprime_cosmos.cosmos_api import CosmosApiError
from fprime_cosmos.dictionary import DictionaryError, FprimeDictionary
from fprime_cosmos.generate.__main__ import add_dictionary_arguments
from fprime_cosmos.install import add_cosmos_arguments, install_from_arguments
from fprime_cosmos.items import UnsupportedTypeError
from fprime_cosmos.plugin_builder import build_plugin

BRIDGE_EXECUTABLE = "fprime-comm-bridge"
BRIDGE_MODULE = "fprime_gds.executables.comm_bridge"
DOCKER_INTERFACE_PREFIXES = ("docker", "br-")
DOCKER_HOST_INTERFACE = "docker"  # host.docker.internal resolves to the default bridge (docker0) gateway
BIND_ANY = "0.0.0.0"  # noqa: S104 - last resort when the Docker host address cannot be determined
# COSMOS receives plain F Prime packets, so the bridge strips Space Packet and Space Data Link framing
DEFAULT_FRAMING = "space-packet-space-data-link"
# fprime-comm-bridge defaults: a tcp-fast-server the deployment's TcpClient connects to
TCP_FAST_ADDRESS_OPTION = "--tcp-fast-address"
TCP_FAST_PORT_OPTION = "--tcp-fast-port"
TCP_FAST_DEFAULT_PORT = "50000"
LOOPBACK = "127.0.0.1"
UNSPECIFIED_ADDRESSES = ("", "0.0.0.0", "::")  # noqa: S104 - recognised, not bound
APP_START_DELAY = 1.0  # let the bridge open its server socket before the deployment connects
SHUTDOWN_TIMEOUT = 5.0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fprime-cosmos", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--dictionary", type=Path, required=True, help="F Prime JSON topology dictionary")
    add_dictionary_arguments(parser)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("openc3-plugin"),
        help="Where the plugin is generated (default: %(default)s)",
    )
    cosmos = add_cosmos_arguments(parser)
    cosmos.add_argument("--skip-install", action="store_true", help="Generate the plugin but do not talk to COSMOS")
    bridge = parser.add_argument_group("bridge")
    bridge.add_argument("--skip-bridge", action="store_true", help="Do not start fprime-comm-bridge")
    bridge.add_argument(
        "--framing-selection", default=DEFAULT_FRAMING, help="fprime-comm-bridge framing (default: %(default)s)"
    )
    app = parser.add_argument_group("deployment")
    app.add_argument("--app", type=Path, help="Deployment binary to start once the bridge is up")
    app.add_argument(
        "--app-arguments",
        help="Arguments for --app (default: '-a <bridge tcp-fast address> -p <bridge tcp-fast port>')",
    )
    app.add_argument("--logs", type=Path, help="Directory for the deployment log (default: <output>/logs)")
    return parser


def docker_interfaces() -> list[tuple[str, str]]:
    """(interface, IPv4 address) of the local Docker bridge interfaces"""
    try:
        output = subprocess.run(["ip", "-4", "-o", "addr"], check=True, capture_output=True, text=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return []
    interfaces = []
    for line in output.splitlines():
        fields = line.split()
        if len(fields) >= 4 and fields[1].startswith(DOCKER_INTERFACE_PREFIXES):
            interfaces.append((fields[1], str(ipaddress.ip_interface(fields[3]).ip)))
    return interfaces


def docker_gateway_addresses() -> list[str]:
    """IPv4 addresses of local Docker bridge interfaces"""
    return [address for _interface, address in docker_interfaces()]


def docker_host_address() -> str | None:
    """The address containers reach the host at (host.docker.internal), so the bridge need not bind every interface"""
    return next(
        (address for interface, address in docker_interfaces() if interface.startswith(DOCKER_HOST_INTERFACE)), None
    )


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
        local = host in ("localhost", LOOPBACK) or socket.gethostbyname(host) == socket.gethostbyname(
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
    bind = docker_host_address() or BIND_ANY
    return ["--udp-fast-bind-address", bind, "--udp-fast-allowed-source", *sources]


def bridge_command() -> list[str] | None:
    """Prefer the bridge from this interpreter's environment so the launcher works without PATH activation."""
    if importlib.util.find_spec(BRIDGE_MODULE) is not None:
        return [sys.executable, "-m", BRIDGE_MODULE]
    executable = shutil.which(BRIDGE_EXECUTABLE)
    return None if executable is None else [executable]


def option_value(arguments: list[str], option: str, default: str) -> str:
    """Value of `option` in a forwarded argument list (`--opt value` or `--opt=value`), else `default`"""
    for index, argument in enumerate(arguments):
        if argument == option and index + 1 < len(arguments):
            return arguments[index + 1]
        if argument.startswith(f"{option}="):
            return argument.split("=", 1)[1]
    return default


def app_command(app: Path, app_arguments: str | None, bridge_arguments: list[str]) -> list[str]:
    """The deployment command line: explicit --app-arguments, else connect to the bridge's tcp-fast server"""
    if app_arguments is not None:
        return [str(app.resolve()), *shlex.split(app_arguments)]
    address = option_value(bridge_arguments, TCP_FAST_ADDRESS_OPTION, LOOPBACK)
    if address in UNSPECIFIED_ADDRESSES:
        address = LOOPBACK
    port = option_value(bridge_arguments, TCP_FAST_PORT_OPTION, TCP_FAST_DEFAULT_PORT)
    return [str(app.resolve()), "-a", address, "-p", port]


def stop(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(SHUTDOWN_TIMEOUT)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def run_processes(bridge: list[str], app: list[str] | None, log_directory: Path) -> int:
    """Run the bridge and, optionally, the deployment until either exits or the user interrupts"""
    print(f"[INFO] Starting: {' '.join(bridge)}")
    processes = [subprocess.Popen(bridge)]
    log = None
    try:
        if app is not None:
            time.sleep(APP_START_DELAY)
            log_directory.mkdir(parents=True, exist_ok=True)
            log_path = log_directory / f"{Path(app[0]).name}.log"
            log = log_path.open("wb")
            print(f"[INFO] Starting: {' '.join(app)} (log: {log_path})")
            processes.append(subprocess.Popen(app, cwd=Path(app[0]).parent, stdout=log, stderr=subprocess.STDOUT))
        while all(process.poll() is None for process in processes):
            time.sleep(0.5)
        finished = next(process for process in processes if process.poll() is not None)
        print(f"[INFO] {Path(finished.args[0]).name} exited with {finished.returncode}; shutting down")
        return finished.returncode
    except KeyboardInterrupt:
        return 0
    finally:
        for process in reversed(processes):
            stop(process)
        if log is not None:
            log.close()


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args, bridge_arguments = parser.parse_known_args(argv)
    if args.app is not None and not args.app.is_file():
        parser.error(f"--app {args.app} is not a file")
    try:
        dictionary = FprimeDictionary(args.dictionary, args.packet_set_name)
        artifacts = build_plugin(dictionary, args.output, args.target_name)
        print(f"[INFO] Generated {artifacts.gem_path}")
        if not args.skip_install:
            install_from_arguments(args, artifacts)
    except (DictionaryError, UnsupportedTypeError, CosmosApiError, ValueError) as error:
        print(f"[ERROR] {error}", file=sys.stderr)
        return 1
    if args.skip_bridge:
        return 0
    bridge = bridge_command()
    if bridge is None:
        print(f"[ERROR] {BRIDGE_EXECUTABLE} not found; install fprime-gds", file=sys.stderr)
        return 1
    bridge_arguments = [*bridge_defaults(args.cosmos_url, bridge_arguments), *bridge_arguments]
    bridge += ["--dictionary", str(dictionary.path), "--framing-selection", args.framing_selection, *bridge_arguments]
    app = None if args.app is None else app_command(args.app, args.app_arguments, bridge_arguments)
    return run_processes(bridge, app, args.logs or args.output / "logs")


if __name__ == "__main__":
    sys.exit(main())
