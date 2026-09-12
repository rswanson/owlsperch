"""Orchestration for `owlsperch segment <book_id|all>`.

Per book:

1. Look the book up in the manifest (unknown `book_id` is a hard error). The
   book's `text/<book_id>/` directory (written by `owlsperch text`) must
   already exist; `all` silently considers only in-scope/override books
   (mirroring `owlsperch.text.runner`) and prints a "no text output" skip
   line for any of those missing a text dir, instead of erroring.
2. Read every `p{NNNN}.txt` in the (optionally `--pages`-limited) range into
   paragraphs, joined with their `p{NNNN}.meta.json` sidecar stats into one
   flat, book-wide `owlsperch.segment.headings.Paragraph` stream (a segment
   may span a page boundary, so segmentation never works page by page). A
   page whose `.txt` exists but whose `.meta.json` is missing (or does not
   have one entry per paragraph) is a hard error naming every such page and
   `owlsperch text <book_id> --force`.
3. Compute the book's body-median word height and split the stream into
   segments (`owlsperch.segment.splitter.build_segments`): pattern anchors
   (spell, stat_block, feat, table) plus `rules_section` for everything
   between them, split further at headings.
4. For each non-empty segment, compute its `seg_id` (`<book_id>-p<NNNN>-<NN>`
   -- first page spanned, then a per-first-page ordinal, both assigned in
   stream order so reruns reproduce the same ids) and write
   `segments/<book_id>/<seg_id>.json` unless it already exists and `--force`
   was not given. Prints one line per book: counts per `kind_hint`, and how
   many segment files were written vs already present.
5. As a coverage safety net (every page with any text should land in at
   least one segment -- acceptance criterion 5), warns on stderr listing any
   page in the processed range that ended up in no segment's `pages`.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from owlsperch.manifest import (
    ManifestEntry,
    default_manifest_path,
    load_manifest,
    status_for,
)
from owlsperch.segment.headings import Paragraph, compute_body_median
from owlsperch.segment.splitter import KindHint, RawSegment, build_segments
from owlsperch.text.runner import default_data_dir

#: Fixed display order for the per-kind summary line (only kinds that
#: actually occurred are printed).
_KIND_ORDER: tuple[KindHint, ...] = ("spell", "stat_block", "feat", "table", "rules_section")

#: The tier every freshly-written segment starts on (spec 4.5's ladder).
SEGMENT_TIER = "haiku"


class SegmentError(Exception):
    """A hard failure segmenting one book (e.g. a page missing its
    `.meta.json` sidecar)."""


class Segment(BaseModel):
    """One `segments/<book_id>/<seg_id>.json` file (spec 4.2, 4.4)."""

    model_config = ConfigDict(extra="forbid")

    seg_id: str
    book_id: str
    pages: list[int]
    printed_pages: list[int]
    kind_hint: KindHint
    heading: str
    text: str
    status: str = "pending"
    tier: str = SEGMENT_TIER
    attempts: list[Any] = []
    created_at: str


@dataclass
class BookSegmentSummary:
    book_id: str
    counts: dict[str, int] = field(default_factory=dict)
    written: int = 0
    skipped: int = 0
    note: str = ""

    def render(self) -> str:
        if self.note:
            return f"{self.book_id}: {self.note}"
        counts_str = " / ".join(
            f"{kind} {self.counts[kind]}" for kind in _KIND_ORDER if kind in self.counts
        )
        return f"{self.book_id}: {counts_str} ({self.written} written, {self.skipped} skipped)"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _load_pages_json(text_dir: Path) -> dict[int, int]:
    path = text_dir / "pages.json"
    if not path.exists():
        return {}
    raw: dict[str, int] = json.loads(path.read_text())
    return {int(k): v for k, v in raw.items()}


def _discover_text_pages(text_dir: Path, page_range: tuple[int, int] | None) -> list[int]:
    indices: list[int] = []
    for p in text_dir.glob("p*.txt"):
        digits = p.stem[1:]
        if not digits.isdigit():
            continue
        idx = int(digits)
        if page_range is not None and not (page_range[0] <= idx <= page_range[1]):
            continue
        indices.append(idx)
    return sorted(indices)


def _load_paragraphs(text_dir: Path, page_indices: list[int], book_id: str) -> list[Paragraph]:
    paragraphs: list[Paragraph] = []
    missing_meta: list[int] = []

    for idx in page_indices:
        content = (text_dir / f"p{idx:04d}.txt").read_text()
        blocks = [b for b in content.split("\n\n") if b.strip()]
        if not blocks:
            continue

        meta_path = text_dir / f"p{idx:04d}.meta.json"
        if not meta_path.exists():
            missing_meta.append(idx)
            continue
        meta: list[dict[str, Any]] = json.loads(meta_path.read_text())
        if len(meta) != len(blocks):
            missing_meta.append(idx)
            continue

        for block_text, entry in zip(blocks, meta, strict=True):
            paragraphs.append(
                Paragraph(
                    page=idx,
                    text=block_text,
                    kind=entry["kind"],
                    median_word_height=entry["median_word_height"],
                    max_word_height=entry["max_word_height"],
                    line_count=entry["line_count"],
                )
            )

    if missing_meta:
        pages_str = ", ".join(str(i) for i in sorted(missing_meta))
        raise SegmentError(
            f"missing .meta.json for page(s) {pages_str} of '{book_id}' -- "
            f"run `owlsperch text {book_id} --force`"
        )
    return paragraphs


def _pages_spanned(paragraphs: list[Paragraph], start: int, end: int) -> list[int]:
    pages: list[int] = []
    seen: set[int] = set()
    for p in paragraphs[start:end]:
        if p.page not in seen:
            seen.add(p.page)
            pages.append(p.page)
    return pages


def segment_book(
    entry: ManifestEntry,
    *,
    data_dir: Path,
    force: bool = False,
    page_range: tuple[int, int] | None = None,
) -> BookSegmentSummary:
    text_dir = data_dir / "text" / entry.book_id
    if not text_dir.is_dir():
        return BookSegmentSummary(
            entry.book_id, note="no text output -- run `owlsperch text` first"
        )

    page_indices = _discover_text_pages(text_dir, page_range)
    if not page_indices:
        return BookSegmentSummary(entry.book_id, note="no page text in range")

    paragraphs = _load_paragraphs(text_dir, page_indices, entry.book_id)
    if not paragraphs:
        return BookSegmentSummary(entry.book_id, note="no page text in range")

    body_median = compute_body_median(paragraphs)
    raw_segments = build_segments(paragraphs, body_median)

    out_dir = data_dir / "segments" / entry.book_id
    out_dir.mkdir(parents=True, exist_ok=True)
    pages_json = _load_pages_json(text_dir)

    summary = BookSegmentSummary(entry.book_id)
    first_page_counters: dict[int, int] = {}
    covered_pages: set[int] = set()

    for raw in raw_segments:
        _write_one_segment(
            raw,
            paragraphs,
            entry,
            out_dir,
            pages_json,
            first_page_counters,
            covered_pages,
            summary,
            force,
        )

    # Only pages that actually contributed a paragraph are expected to be
    # covered -- a page whose .txt is blank (no text at all, e.g. a pure
    # image page) never enters the stream, and can't be a coverage gap.
    pages_with_content = {p.page for p in paragraphs}
    uncovered = sorted(pages_with_content - covered_pages)
    if uncovered:
        print(
            f"warning: {entry.book_id}: page(s) with text but no segment "
            f"coverage: {', '.join(str(p) for p in uncovered)}",
            file=sys.stderr,
        )

    return summary


def _write_one_segment(
    raw: RawSegment,
    paragraphs: list[Paragraph],
    entry: ManifestEntry,
    out_dir: Path,
    pages_json: dict[int, int],
    first_page_counters: dict[int, int],
    covered_pages: set[int],
    summary: BookSegmentSummary,
    force: bool,
) -> None:
    text = "\n\n".join(p.text for p in paragraphs[raw.start : raw.end])
    if not text.strip():
        return

    pages = _pages_spanned(paragraphs, raw.start, raw.end)
    covered_pages.update(pages)
    first_page = pages[0]
    first_page_counters[first_page] = first_page_counters.get(first_page, 0) + 1
    seg_id = f"{entry.book_id}-p{first_page:04d}-{first_page_counters[first_page]:02d}"

    summary.counts[raw.kind] = summary.counts.get(raw.kind, 0) + 1

    seg_path = out_dir / f"{seg_id}.json"
    if seg_path.exists() and not force:
        summary.skipped += 1
        return

    printed_pages = [pages_json[p] for p in pages if p in pages_json]
    segment = Segment(
        seg_id=seg_id,
        book_id=entry.book_id,
        pages=pages,
        printed_pages=printed_pages,
        kind_hint=raw.kind,
        heading=raw.heading,
        text=text,
        created_at=_now_iso(),
    )
    seg_path.write_text(segment.model_dump_json(indent=2) + "\n")
    summary.written += 1


def run_segment(
    book_id: str,
    *,
    data_dir: Path | None = None,
    manifest_path: Path | None = None,
    force: bool = False,
    page_range: tuple[int, int] | None = None,
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
            try:
                summary = segment_book(entry, data_dir=data_dir, force=force, page_range=page_range)
            except SegmentError as exc:
                print(f"{entry.book_id}: error: {exc}", file=out)
                exit_code = 1
                continue
            print(summary.render(), file=out)
        return exit_code

    resolved_entry = by_id.get(book_id)
    if resolved_entry is None:
        print(f"error: unknown book_id '{book_id}'", file=sys.stderr)
        return 1

    try:
        summary = segment_book(
            resolved_entry, data_dir=data_dir, force=force, page_range=page_range
        )
    except SegmentError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(summary.render(), file=out)
    return 0
