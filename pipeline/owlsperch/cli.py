"""The `owlsperch` command-line entry point.

Only `manifest check` is implemented in this batch; the other subcommands
listed in the spec (text, segment, validate, build-db, check-completeness,
coverage, schema review, sample) are future-batch stubs.
"""

from __future__ import annotations

import argparse
import sys

from owlsperch.manifest import ManifestError, run_check


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="owlsperch", description="D&D 3.5e reference-data pipeline."
    )
    subparsers = parser.add_subparsers(dest="command")

    manifest_parser = subparsers.add_parser("manifest", help="Work with the curated book manifest.")
    manifest_subparsers = manifest_parser.add_subparsers(dest="manifest_command")
    manifest_subparsers.add_parser(
        "check", help="Validate the manifest against the PDF directory and print a summary."
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "manifest" and args.manifest_command == "check":
        try:
            return run_check()
        except ManifestError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

    parser.print_help()
    return 0 if args.command is None else 1


if __name__ == "__main__":
    sys.exit(main())
