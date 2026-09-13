"""Orchestration for `owlsperch toc <book_id|all>` (batch B10b).

Per book:

1. Look the book up in the manifest (unknown `book_id` is a hard error, like
   `segment`); `all` considers only in-scope/override books and prints a
   "no text output" skip line for one missing `text/<book_id>/` entirely,
   without failing the whole run for it. An explicit single `book_id` (not
   `all`) treats that same missing `text/<book_id>/` as a failure instead
   -- there's no "the rest of the run" to keep going for, so naming one
   book directly should exit non-zero rather than silently no-op.
2. Skip (print a note, exit 0) if `toc/<book_id>.json` already exists and
   `--force` wasn't given -- idempotent, like every other subcommand here.
3. `owlsperch.toc.parser.parse_book_toc` does the actual parsing; a
   `TocParseError` (no usable contents page, or too few entries) is reported
   per-book rather than raising past `all`.
4. Otherwise writes `toc/<book_id>.json` (via `atomic_write_text`) and
   prints a one-line report: contents page(s) found, chapter/section
   counts, how many "Table N-M" index entries were dropped, and how many
   entries fell through to `uncategorized` (criterion 2's "prints how many
   fell through").
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from owlsperch.fsutil import atomic_write_text
from owlsperch.manifest import ManifestEntry, default_manifest_path, load_manifest, status_for
from owlsperch.text.runner import default_data_dir
from owlsperch.toc.parser import ParsedToc, Toc, TocParseError, parse_book_toc


@dataclass
class BookTocSummary:
    book_id: str
    parsed: ParsedToc | None = None
    note: str = ""

    def render(self) -> str:
        if self.note:
            return f"{self.book_id}: {self.note}"
        assert self.parsed is not None
        chapters = sum(1 for e in self.parsed.entries if e.level == 1)
        sections = sum(1 for e in self.parsed.entries if e.level >= 2)
        pages = ", ".join(str(p) for p in self.parsed.contents_pages)
        return (
            f"{self.book_id}: contents page(s) [{pages}], {chapters} chapters, "
            f"{sections} sections, {self.parsed.dropped_table_entries} table-index "
            f"entries dropped, {self.parsed.uncategorized_count} uncategorized"
        )


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def toc_book(
    entry: ManifestEntry,
    *,
    data_dir: Path,
    force: bool = False,
    missing_text_is_error: bool = False,
) -> tuple[BookTocSummary, bool]:
    """Returns `(summary, failed)` -- `failed` is `True` for a
    `TocParseError`, or (when `missing_text_is_error` is set) for a missing
    `text/<book_id>/` directory (an unknown book_id is a hard error the
    caller raises itself, matching `owlsperch segment`). `run_toc`'s `all`
    loop leaves `missing_text_is_error` at its default `False` -- a book
    with no text output yet is just skipped, not a run failure -- while its
    single-book-by-id path passes `True`, since there's no "the rest of the
    run" to keep going for."""
    text_dir = data_dir / "text" / entry.book_id
    if not text_dir.is_dir():
        note = "no text output -- run `owlsperch text` first"
        return BookTocSummary(entry.book_id, note=note), missing_text_is_error

    out_path = data_dir / "toc" / f"{entry.book_id}.json"
    if out_path.exists() and not force:
        note = "toc already exists (use --force to reparse)"
        return BookTocSummary(entry.book_id, note=note), False

    try:
        parsed = parse_book_toc(text_dir, book_id=entry.book_id)
    except TocParseError as exc:
        return BookTocSummary(entry.book_id, note=f"error: {exc}"), True

    out_path.parent.mkdir(parents=True, exist_ok=True)
    toc = Toc(
        book_id=entry.book_id,
        generated_at=_now_iso(),
        contents_pages=parsed.contents_pages,
        entries=parsed.entries,
    )
    atomic_write_text(out_path, toc.model_dump_json(indent=2) + "\n")

    return BookTocSummary(entry.book_id, parsed=parsed), False


def run_toc(
    book_id: str,
    *,
    data_dir: Path | None = None,
    manifest_path: Path | None = None,
    force: bool = False,
    out: Any = None,
) -> int:
    out = out if out is not None else sys.stdout
    data_dir = data_dir if data_dir is not None else default_data_dir()
    manifest_path = manifest_path if manifest_path is not None else default_manifest_path()

    entries = load_manifest(manifest_path)
    by_id = {e.book_id: e for e in entries}

    if book_id == "all":
        exit_code = 0
        for entry in entries:
            if status_for(entry, entries) not in ("in_scope", "override"):
                continue
            summary, failed = toc_book(entry, data_dir=data_dir, force=force)
            print(summary.render(), file=out)
            if failed:
                exit_code = 1
        return exit_code

    resolved_entry = by_id.get(book_id)
    if resolved_entry is None:
        print(f"error: unknown book_id '{book_id}'", file=sys.stderr)
        return 1

    summary, failed = toc_book(
        resolved_entry, data_dir=data_dir, force=force, missing_text_is_error=True
    )
    print(summary.render(), file=out)
    return 1 if failed else 0
