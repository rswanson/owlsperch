"""The `owlsperch` command-line entry point.

`manifest check`, `text`, `segment`, `validate`, `queue`, `schema show`,
`build-db`, and `serve` are implemented; the other subcommands listed in the
spec (check-completeness, coverage, schema review, sample) are future-batch
stubs.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from owlsperch.build_db.runner import run_build_db
from owlsperch.dev import run_dev
from owlsperch.fixture_db import run_fixture_db
from owlsperch.manifest import ManifestError, run_check
from owlsperch.queue.driver import run_queue_run
from owlsperch.queue.prompt import DEFAULT_MODEL
from owlsperch.queue.runner import (
    run_queue_complete,
    run_queue_next,
    run_queue_prompt,
    run_queue_reset,
    run_queue_summary,
)
from owlsperch.queue.select import DEFAULT_LOCK_TIMEOUT
from owlsperch.schemas import SchemaError, load_registry, render_schema_show
from owlsperch.segment.runner import run_segment
from owlsperch.serve import DEFAULT_HOST, DEFAULT_PORT, run_serve
from owlsperch.text.runner import run_text
from owlsperch.validate.runner import run_validate


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

    validate_parser = subparsers.add_parser(
        "validate",
        help="Check every record under records/<book_id>/ against its schema.",
    )
    validate_parser.add_argument("book_id", help="Manifest book_id, or 'all'.")
    validate_parser.add_argument(
        "--json", action="store_true", help="Print machine-readable JSON results instead."
    )
    validate_parser.add_argument(
        "--stale",
        action="store_true",
        help="List records whose schema_version is behind the current type version, and exit 0.",
    )
    validate_parser.add_argument(
        "--bump-compatible",
        action="store_true",
        help=(
            "For every stale record that otherwise validates against the current "
            "schema, rewrite schema_version to the current version in place."
        ),
    )

    queue_parser = subparsers.add_parser(
        "queue", help="The extraction queue used by the /extract skill."
    )
    queue_parser.set_defaults(queue_parser=queue_parser)
    queue_subparsers = queue_parser.add_subparsers(dest="queue_command")

    queue_next_parser = queue_subparsers.add_parser(
        "next", help="Select and mark in_progress the next pending segments for a subagent wave."
    )
    queue_next_parser.add_argument("book_id", help="Manifest book_id.")
    queue_next_parser.add_argument(
        "--tier",
        default=None,
        help="Only select segments on this tier (default: the lowest tier with pending work).",
    )
    queue_next_parser.add_argument(
        "--limit", type=int, required=True, help="Maximum number of segments to select."
    )
    queue_next_parser.add_argument(
        "--kind",
        default=None,
        help="Only select segments with this kind_hint "
        "(default: every kind_hint with a registered schema).",
    )
    queue_next_parser.add_argument(
        "--model",
        default=None,
        help="Model string rendered into each prompt's extraction.model "
        "(default: the resolved tier's model, e.g. claude-haiku-4-5 for haiku).",
    )
    queue_next_parser.add_argument(
        "--lock-timeout",
        type=float,
        default=DEFAULT_LOCK_TIMEOUT,
        metavar="SECONDS",
        help=f"Seconds to wait for the book's queue lock before giving up "
        f"(default: {DEFAULT_LOCK_TIMEOUT}).",
    )
    queue_next_parser.add_argument(
        "--json", action="store_true", help="Print a JSON array instead of human-readable lines."
    )

    queue_prompt_parser = queue_subparsers.add_parser(
        "prompt", help="Render (or re-render) one segment's subagent prompt and print its path."
    )
    queue_prompt_parser.add_argument("seg_id", help="Segment id, e.g. phb1-p0257-04.")
    queue_prompt_parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Model string rendered into the prompt's extraction.model "
        f"(default: {DEFAULT_MODEL}).",
    )

    queue_complete_parser = queue_subparsers.add_parser(
        "complete", help="Ingest a subagent's final JSON reply for one segment."
    )
    queue_complete_parser.add_argument("seg_id", help="Segment id, e.g. phb1-p0257-04.")
    queue_complete_parser.add_argument(
        "--result",
        required=True,
        metavar="FILE|-",
        help="Path to the subagent's JSON reply, or '-' to read it from stdin.",
    )

    queue_summary_parser = queue_subparsers.add_parser(
        "summary", help="Print segment/record counts for a book."
    )
    queue_summary_parser.add_argument("book_id", help="Manifest book_id.")
    queue_summary_parser.add_argument(
        "--json", action="store_true", help="Print JSON instead of human-readable lines."
    )

    queue_reset_parser = queue_subparsers.add_parser(
        "reset", help="Reset one or more in_progress segments back to pending."
    )
    queue_reset_parser.add_argument("seg_id", nargs="+", help="One or more segment ids.")
    queue_reset_parser.add_argument(
        "--hard",
        action="store_true",
        help=(
            "Also clear attempts/pending_records/records/notes/outcome and "
            "delete the record files they named (only ones under records/<book_id>/)."
        ),
    )

    queue_run_parser = queue_subparsers.add_parser(
        "run",
        help="Drive the whole select/subagent/complete/validate loop in-process (--dry-run only).",
    )
    queue_run_parser.add_argument("book_id", help="Manifest book_id.")
    queue_run_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Required in this batch: drive the loop against a FixtureSubagent instead of "
        "launching real Agent-tool subagents (that's the /extract skill's job).",
    )
    queue_run_parser.add_argument(
        "--fixtures",
        type=Path,
        default=None,
        metavar="DIR",
        help="Fixture directory for --dry-run: <DIR>/<seg_id>/<n>.json canned replies.",
    )
    queue_run_parser.add_argument(
        "--tier", default=None, help="Only run this tier (default: whatever tier has pending work)."
    )
    queue_run_parser.add_argument(
        "--limit", type=int, default=None, help="Cap total subagent calls across every wave."
    )
    queue_run_parser.add_argument(
        "--kind",
        default=None,
        help="Only run segments with this kind_hint "
        "(default: every kind_hint with a registered schema).",
    )
    queue_run_parser.add_argument(
        "--json", action="store_true", help="Print the final summary as JSON instead."
    )

    schema_parser = subparsers.add_parser("schema", help="Inspect record type schemas.")
    schema_parser.set_defaults(schema_parser=schema_parser)
    schema_subparsers = schema_parser.add_subparsers(dest="schema_command")
    schema_show_parser = schema_subparsers.add_parser(
        "show", help="Print a type's schema path and its x-ui field table."
    )
    schema_show_parser.add_argument("type", help="Registered type name, e.g. 'spell'.")

    build_db_parser = subparsers.add_parser(
        "build-db",
        help="Build $OWLSPERCH_DATA/db/owlsperch.sqlite from validated records.",
    )
    build_db_parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Exit 1 if any record was skipped as invalid (default: exit 0 and "
            "just warn, since skips are expected while extraction is in progress)."
        ),
    )

    serve_parser = subparsers.add_parser(
        "serve", help="Start the owlsperch_server FastAPI app (uvicorn)."
    )
    serve_parser.add_argument(
        "--host", default=DEFAULT_HOST, help=f"Host to bind (default: {DEFAULT_HOST})."
    )
    serve_parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help=f"Port to bind (default: {DEFAULT_PORT})."
    )

    subparsers.add_parser(
        "dev",
        help="Run the FastAPI server and the Vite dev server together (spec 4.10).",
    )

    fixture_db_parser = subparsers.add_parser(
        "fixture-db",
        help="Build a small synthetic SQLite database into <dir>, for local UI/e2e testing.",
    )
    fixture_db_parser.add_argument(
        "dir", type=Path, help="Directory to write the fixture $OWLSPERCH_DATA-shaped tree into."
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
        # Note: run_segment already catches its own SegmentError internally
        # (naming the failing book and returning exit code 1), so only
        # ManifestError (raised by load_manifest before any per-book work
        # starts) can actually reach this handler.
        try:
            return run_segment(args.book_id, force=args.force, page_range=args.pages)
        except ManifestError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

    if args.command == "validate":
        try:
            return run_validate(
                args.book_id,
                json_output=args.json,
                stale=args.stale,
                bump_compatible=args.bump_compatible,
            )
        except SchemaError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

    if args.command == "queue" and args.queue_command == "next":
        return run_queue_next(
            args.book_id,
            tier=args.tier,
            limit=args.limit,
            kind=args.kind,
            model=args.model,
            lock_timeout=args.lock_timeout,
            json_output=args.json,
        )

    if args.command == "queue" and args.queue_command == "prompt":
        return run_queue_prompt(args.seg_id, model=args.model)

    if args.command == "queue" and args.queue_command == "complete":
        return run_queue_complete(args.seg_id, args.result)

    if args.command == "queue" and args.queue_command == "summary":
        return run_queue_summary(args.book_id, json_output=args.json)

    if args.command == "queue" and args.queue_command == "reset":
        return run_queue_reset(args.seg_id, hard=args.hard)

    if args.command == "queue" and args.queue_command == "run":
        return run_queue_run(
            args.book_id,
            dry_run=args.dry_run,
            fixtures_dir=args.fixtures,
            tier=args.tier,
            limit=args.limit,
            kind=args.kind,
            json_output=args.json,
        )

    if args.command == "queue":
        args.queue_parser.print_help()
        return 0

    if args.command == "schema" and args.schema_command == "show":
        try:
            registry = load_registry()
            print(render_schema_show(args.type, registry))
            return 0
        except SchemaError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

    if args.command == "schema":
        args.schema_parser.print_help()
        return 0

    if args.command == "build-db":
        return run_build_db(strict=args.strict)

    if args.command == "serve":
        return run_serve(host=args.host, port=args.port)

    if args.command == "dev":
        return run_dev()

    if args.command == "fixture-db":
        return run_fixture_db(args.dir)

    parser.print_help()
    return 0 if args.command is None else 1


if __name__ == "__main__":
    sys.exit(main())
