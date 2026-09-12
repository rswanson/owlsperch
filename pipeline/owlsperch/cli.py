"""The `owlsperch` command-line entry point.

`manifest check`, `text`, and `segment` are implemented; the other
subcommands listed in the spec (validate, build-db, check-completeness,
coverage, schema review, sample) are future-batch stubs.
"""

from __future__ import annotations

import argparse
import sys

from owlsperch.manifest import ManifestError, run_check
from owlsperch.segment.runner import SegmentError, run_segment
from owlsperch.text.runner import run_text


def _parse_pages(value: str) -> tuple[int, int]:
    parts = value.split("-")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(f"--pages must be A-B, got {value!r}")
    try:
        first, last = int(parts[0]), int(parts[1])
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"--pages must be A-B (integers), got {value!r}") from exc
    if first < 1 or last < first:
        raise argparse.ArgumentTypeError(f"--pages range must have 1 <= A <= B, got {value!r}")
    return (first, last)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="owlsperch", description="D&D 3.5e reference-data pipeline."
    )
    subparsers = parser.add_subparsers(dest="command")

    manifest_parser = subparsers.add_parser("manifest", help="Work with the curated book manifest.")
    manifest_parser.set_defaults(manifest_parser=manifest_parser)
    manifest_subparsers = manifest_parser.add_subparsers(dest="manifest_command")
    manifest_subparsers.add_parser(
        "check", help="Validate the manifest against the PDF directory and print a summary."
    )

    text_parser = subparsers.add_parser(
        "text",
        help="Extract column-repaired page text for a text-layer book (or 'all').",
    )
    text_parser.add_argument("book_id", help="Manifest book_id, or 'all'.")
    text_parser.add_argument(
        "--force", action="store_true", help="Re-write page files that already exist."
    )
    text_parser.add_argument(
        "--pages",
        type=_parse_pages,
        default=None,
        metavar="A-B",
        help="Limit extraction to PDF page range A-B (1-based, inclusive).",
    )

    segment_parser = subparsers.add_parser(
        "segment",
        help="Split a book's extracted text into candidate segments with kind hints.",
    )
    segment_parser.add_argument("book_id", help="Manifest book_id, or 'all'.")
    segment_parser.add_argument(
        "--force", action="store_true", help="Re-write segment files that already exist."
    )
    segment_parser.add_argument(
        "--pages",
        type=_parse_pages,
        default=None,
        metavar="A-B",
        help="Limit segmentation to PDF page range A-B (1-based, inclusive).",
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

    if args.command == "manifest":
        args.manifest_parser.print_help()
        return 0

    if args.command == "text":
        try:
            return run_text(args.book_id, force=args.force, page_range=args.pages)
        except ManifestError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

    if args.command == "segment":
        try:
            return run_segment(args.book_id, force=args.force, page_range=args.pages)
        except (ManifestError, SegmentError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

    parser.print_help()
    return 0 if args.command is None else 1


if __name__ == "__main__":
    sys.exit(main())
