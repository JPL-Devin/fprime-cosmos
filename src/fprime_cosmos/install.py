"""Installing a generated plugin into a running COSMOS, shared by fprime-to-cosmos --install and fprime-cosmos"""

from __future__ import annotations

import argparse
import os

from fprime_cosmos.cosmos_api import CosmosClient
from fprime_cosmos.plugin_builder import GEM_NAME_PREFIX, PluginArtifacts

TARGET_VARIABLE = "fprime_target_name"
PASSWORD_ENVIRONMENT = "OPENC3_API_PASSWORD"  # noqa: S105 - name of the variable, not a secret


def add_cosmos_arguments(parser: argparse.ArgumentParser) -> argparse._ArgumentGroup:
    """COSMOS connection and plugin options; the caller decides whether installing is the default"""
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
    return cosmos


def parse_variables(pairs: list[str]) -> dict[str, str]:
    variables = {}
    for pair in pairs:
        name, separator, value = pair.partition("=")
        if not separator or not name:
            raise ValueError(f"Plugin variable must be NAME=VALUE, got '{pair}'")
        variables[name] = value
    return variables


def installed_plugin_for_target(client: CosmosClient, target: str) -> str | None:
    """The fprime plugin currently serving `target`, whichever dictionary it was generated from"""
    for name in client.plugins():
        if not name.startswith(f"{GEM_NAME_PREFIX}-"):
            continue
        variable = client.plugin(name).get("variables", {}).get(TARGET_VARIABLE)
        value = variable.get("value") if isinstance(variable, dict) else variable
        if value == target:
            return name
    return None


def ensure_installed(
    client: CosmosClient, artifacts: PluginArtifacts, variables: dict[str, str], target: str, force: bool
) -> None:
    """Install the gem unless a plugin built from the same dictionary digest already serves the target"""
    client.authenticate()
    existing = installed_plugin_for_target(client, target)
    if existing and existing.startswith(f"{artifacts.plugin_prefix}.gem__") and not force:
        print(f"[INFO] COSMOS target {target} already runs {existing}; skipping install (--force-install overrides)")
        return
    gem = artifacts.gem_path.name
    action = f"Upgrading {existing} to {gem}" if existing else f"Installing {gem}"
    print(f"[INFO] {action} in COSMOS scope {client.scope}")
    client.install_gem(str(artifacts.gem_path), variables, existing)
    print(f"[INFO] Target {target} now runs {gem}")


def install_from_arguments(args: argparse.Namespace, artifacts: PluginArtifacts) -> None:
    """Install `artifacts` into the COSMOS described by the options from add_cosmos_arguments

    Raises ValueError for malformed --cosmos-variable pairs and CosmosApiError for COSMOS failures.
    """
    variables = parse_variables(args.cosmos_variable)
    client = CosmosClient(args.cosmos_url, args.cosmos_password, args.cosmos_scope)
    target = variables.get(TARGET_VARIABLE, args.target_name)
    ensure_installed(client, artifacts, variables, target, args.force_install)
