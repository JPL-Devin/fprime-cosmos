"""fprime-to-cosmos: generate a COSMOS plugin (directory and gem) from an F Prime dictionary"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from fprime_cosmos.dictionary import DictionaryError, FprimeDictionary
from fprime_cosmos.items import UnsupportedTypeError
from fprime_cosmos.plugin_builder import STATIC_TARGET, build_plugin


def add_dictionary_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--packet-set-name", help="Telemetry packet set to generate when the dictionary defines several"
    )
    parser.add_argument(
        "--target-name", default=STATIC_TARGET, help="Default COSMOS target name (default: %(default)s)"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fprime-to-cosmos", description=__doc__)
    parser.add_argument("dictionary", type=Path, help="F Prime JSON topology dictionary")
    parser.add_argument(
        "-o", "--output", type=Path, default=Path("openc3-plugin"), help="Output directory (default: %(default)s)"
    )
    parser.add_argument("--no-gem", action="store_true", help="Write the plugin directory only; do not package a gem")
    add_dictionary_arguments(parser)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        dictionary = FprimeDictionary(args.dictionary, args.packet_set_name)
        artifacts = build_plugin(dictionary, args.output, args.target_name, gem=not args.no_gem)
    except (DictionaryError, UnsupportedTypeError) as error:
        print(f"[ERROR] {error}", file=sys.stderr)
        return 1
    print(f"[INFO] Plugin written to {artifacts.directory}")
    if artifacts.gem_path:
        print(f"[INFO] Gem written to {artifacts.gem_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
