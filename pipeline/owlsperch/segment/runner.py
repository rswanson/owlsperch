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
4. If `--force` was given, first delete every existing
   `segments/<book_id>/<seg_id>.json` whose first page falls inside the
   processed page range (the whole book when no `--pages` was given) --
   otherwise a page whose segmentation changed (e.g. it used to produce two
   segments and now produces one) would leave a stale orphan file behind
   alongside the freshly written ones.
5. For each non-empty segment, compute its `seg_id` (`<book_id>-p<NNNN>-<NN>`
   -- first page spanned, then a per-first-page ordinal, both assigned in
   stream order so reruns reproduce the same ids) and write
   `segments/<book_id>/<seg_id>.json` unless it already exists and `--force`
   was not given. Prints one line per book: counts per `kind_hint`, and how
   many segment files were written vs already present.
6. As a coverage safety net (every page with any text should land in at
   least one segment -- acceptance criterion 5), warns on stderr listing any
   page in the processed range that ended up in no segment's `pages`.
7. (Batch B10c) If `toc/<book_id>.json` exists, a SEPARATE, toc-driven pass
   looks for class/prestige_class entries: a level >= 2 toc entry whose
   resolved `category` is `classes`/`prestige-classes` AND whose own pages
   contain a `Hit Die: dN` marker (design decision D1 -- not every section
   under a Classes chapter is an actual class, e.g. "Multiclass
   Characters"). Each one becomes its own `class`/`prestige_class` segment,
   id `<book_id>-<kind>-p<NNNN>` (`NNNN` = the entry's own first pdf page --
   a form that never collides with the `<book_id>-p<NNNN>-<NN>` ordinal
   scheme above, so adding a class segment never renumbers existing ones),
   spanning the toc entry's own pdf pages EXTENDED BY ONE PAGE past
   `pdf_page_end` (design decision D2 -- a class's last column routinely
   spills onto the page the toc assigns to the next class). This pass is
   purely additive: it never deletes or renumbers anything `build_segments`
   produced, and is skipped (no error, no class segments) when the book has
   no usable toc file.
8. (Batch B10c) For each class/prestige_class span just discovered, every
   OTHER segment of the same book whose `pages` fall entirely inside that
   span gets `superseded_by` set to the class segment's id (if not already
   set) -- an in-place stamp, not a delete/rewrite, so a plain (non-`--force`)
   `owlsperch segment <book_id>` run both writes the handful of new class
   segments AND stamps every existing class-chapter fragment segment,
   without touching their own `records`/`outcome`/`attempts` bookkeeping.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from owlsperch.fsutil import atomic_write_text
from owlsperch.manifest import (
    ManifestEntry,
    default_manifest_path,
    load_manifest,
    status_for,
)
from owlsperch.segment.headings import Paragraph, compute_body_median
from owlsperch.segment.splitter import KindHint, RawSegment, build_segments
from owlsperch.text.runner import default_data_dir
from owlsperch.toc.lookup import load_toc
from owlsperch.toc.parser import Toc

#: Fixed display order for the per-kind summary line (only kinds that
#: actually occurred are printed).
_KIND_ORDER: tuple[KindHint, ...] = (
    "spell",
    "stat_block",
    "feat",
    "table",
    "rules_section",
    "class",
    "prestige_class",
)

#: The tier every freshly-written segment starts on, when `owlsperch.queue.
#: ladder.starting_tier` isn't available for some reason (a defensive
#: fallback only -- `_write_one_segment`/`_write_class_segment` both use
#: `starting_tier` directly; see their own lazy import, which exists to
#: avoid a `segment.runner` <-> `queue.ladder` import cycle).
SEGMENT_TIER = "haiku"

#: Batch B10c, design decision D1: the discriminator between an actual
#: class/prestige_class toc entry and a same-chapter section that merely
#: talks ABOUT classes (e.g. "Multiclass Characters", "Class Descriptions")
#: -- verified against the real corpus (phb1) to match exactly its 11 real
#: class entries and none of its other Classes-chapter sections.
_HIT_DIE_RE = re.compile(r"Hit Die[:\s]*d(4|6|8|10|12)\b")

#: A toc entry's resolved `category` (`owlsperch.toc.categories`) that makes
#: it a class-segment candidate, mapped to the `kind_hint` it becomes if the
#: `_HIT_DIE_RE` marker is found in its own pages.
_TOC_CATEGORY_TO_KIND: dict[str, KindHint] = {
    "classes": "class",
    "prestige-classes": "prestige_class",
}


class SegmentError(Exception):
    """A hard failure segmenting one book (e.g. a page missing its
    `.meta.json` sidecar)."""


class Segment(BaseModel):
    """One `segments/<book_id>/<seg_id>.json` file (spec 4.2, 4.4)."""

    model_config = ConfigDict(extra="forbid")

    seg_id: str
    book_id: str
    pages: list[int]
    #: Same length and order as `pages`; `None` where no printed page number
    #: was detected for that PDF page (see `owlsperch.text.runner`'s
    #: `pages.json`).
    printed_pages: list[int | None]
    kind_hint: KindHint
    heading: str
    text: str
    status: str = "pending"
    tier: str = SEGMENT_TIER
    attempts: list[Any] = []
    created_at: str
    #: Set by `owlsperch validate` (batch B4): a short outcome tag, e.g.
    #: "validated" on a passing record, "no_content" from the extract skill
    #: (B5).
    outcome: str | None = None
    #: Set alongside `outcome` for outcomes that carry a reason (currently
    #: just "no_content", set by `owlsperch queue complete`, batch B5).
    outcome_reason: str | None = None
    #: Paths (relative to `$OWLSPERCH_DATA`) of every record validated back
    #: to this segment. Set by `owlsperch validate` on PASS.
    records: list[str] = []
    #: Paths (relative to `$OWLSPERCH_DATA`) a subagent claimed to have
    #: written, recorded by `owlsperch queue complete` (batch B5) pending
    #: `owlsperch validate`'s conformance check -- PASS moves a path from
    #: here to `records`; FAIL just drops it from here.
    pending_records: list[str] = []
    #: Free-text notes accumulated from subagent replies (`owlsperch queue
    #: complete`, batch B5 follow-up) -- e.g. an `unnamed_entity: <snippet>`
    #: note for a stat block the subagent deliberately skipped rather than
    #: invent a name for. Merged (deduplicated, order preserved) across
    #: multiple `queue complete` calls for the same segment.
    notes: list[str] = []
    #: ISO timestamp set by `owlsperch queue next` when a segment is marked
    #: `in_progress` (batch B5); cleared back to `None` by `owlsperch queue
    #: complete` and `owlsperch queue reset`. Stale in-progress reset (>60
    #: min, spec 4.5) is applied by `owlsperch queue next` itself (batch B8,
    #: see `owlsperch.queue.select`) before it selects anything.
    in_progress_since: str | None = None
    #: The extraction model string (`--model`, default `claude-haiku-4-5`)
    #: recorded by `owlsperch queue next` (`select_and_mark`) at selection
    #: time -- the authoritative source `owlsperch queue complete` copies
    #: into an accepted record's `extraction.model` (B5 follow-up), since a
    #: subagent's own `extraction` block is only a placeholder (see
    #: `owlsperch.queue.prompt`). `None` for a segment that has never been
    #: through `queue next`.
    model: str | None = None
    #: Adjacent segment ids merged in by `owlsperch queue complete` on a
    #: `needs_context` reply (batch B8): each one's text is rendered into a
    #: later prompt's "Adjacent context" section (see
    #: `owlsperch.queue.prompt`) since the entity may continue there. Survives
    #: tier escalation; never cleared automatically.
    context_seg_ids: list[str] = []
    #: The `{"name": ..., "reason": ...}` a subagent proposed when no
    #: existing schema fit this segment (batch B8) -- set only alongside
    #: `outcome: "proposed_type"` when the segment is moved to `human/`.
    proposal: dict[str, Any] | None = None
    #: Batch B10c: the seg_id of the class/prestige_class segment whose page
    #: span fully contains this segment's own `pages` -- set by the
    #: class-span post-pass in `segment_book`, never cleared automatically.
    #: A segment with this set is frozen: `owlsperch.queue.select` never
    #: stale-resets, lazily escalates, or selects it, and it's excluded from
    #: `owlsperch queue summary`'s `pending` breakdown. `None` for every
    #: segment not superseded by a class span (which, pre-B10c, is every
    #: segment).
    superseded_by: str | None = None


@dataclass
class BookSegmentSummary:
    book_id: str
    counts: dict[str, int] = field(default_factory=dict)
    written: int = 0
    skipped: int = 0
    note: str = ""
    #: Batch B10c: how many OTHER segments got `superseded_by` stamped by
    #: the class-span post-pass this run (0 for a book with no usable toc,
    #: or one whose class-span discovery found nothing new to stamp).
    superseded: int = 0
    #: Batch B10c: set (instead of left "") when the book has records but
    #: no usable `toc/<book_id>.json` -- printed as a second summary line
    #: rather than replacing the main one, since ordinary segmentation still
    #: ran normally.
    class_note: str = ""

    def render(self) -> str:
        if self.note:
            return f"{self.book_id}: {self.note}"
        counts_str = " / ".join(
            f"{kind} {self.counts[kind]}" for kind in _KIND_ORDER if kind in self.counts
        )
        lines = [f"{self.book_id}: {counts_str} ({self.written} written, {self.skipped} skipped)"]
        if self.superseded:
            lines.append(f"{self.book_id}: {self.superseded} segment(s) marked superseded_by")
        if self.class_note:
            lines.append(f"{self.book_id}: {self.class_note}")
        return "\n".join(lines)


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


#: Matches a segment file's name with the book_id prefix already stripped:
#: either the ordinal form (`p<NNNN>-<NN>.json`) or (batch B10c) the
#: kind-prefixed class form (`class-p<NNNN>.json`, `prestige_class-
#: p<NNNN>.json`) -- capturing the first page it spans either way, so
#: `_remove_stale_segments` finds every existing segment file for a book
#: (of any kind) without depending on its JSON content.
_SEG_FILENAME_RE = re.compile(r"^(?:[a-z_]+-)?p(\d{4})(?:-\d+)?\.json$")


def _remove_stale_segments(out_dir: Path, book_id: str, page_range: tuple[int, int] | None) -> None:
    """Delete every existing `segments/<book_id>/*.json` whose first page
    falls inside the range about to be (re)processed, so a page whose
    segmentation changed (e.g. it used to produce two segments and now
    produces one) doesn't leave a stale orphan file behind. Only called
    when `--force` is given; the whole book counts as in range when no
    `--pages` was given."""
    prefix = f"{book_id}-"
    for path in out_dir.glob(f"{book_id}-*.json"):
        match = _SEG_FILENAME_RE.match(path.name.removeprefix(prefix))
        if match is None:
            continue
        first_page = int(match.group(1))
        if page_range is None or page_range[0] <= first_page <= page_range[1]:
            path.unlink()


def _pages_spanned(paragraphs: list[Paragraph], start: int, end: int) -> list[int]:
    pages: list[int] = []
    seen: set[int] = set()
    for p in paragraphs[start:end]:
        if p.page not in seen:
            seen.add(p.page)
            pages.append(p.page)
    return pages


def _pages_in_range(paragraphs: list[Paragraph], start_page: int, end_page: int) -> list[int]:
    """Every distinct page in `[start_page, end_page]` that at least one
    paragraph in `paragraphs` actually falls on, in encounter order (mirrors
    `_pages_spanned`'s convention, but filtering by page number rather than
    paragraph index range -- used for the toc-driven class span below,
    which is defined in pdf pages, not paragraph stream positions)."""
    pages: list[int] = []
    seen: set[int] = set()
    for p in paragraphs:
        if start_page <= p.page <= end_page and p.page not in seen:
            seen.add(p.page)
            pages.append(p.page)
    return pages


@dataclass(frozen=True)
class _ClassSpan:
    """One toc-driven class/prestige_class candidate (batch B10c, design
    decisions D1-D3): `start`/`end` are already the FINAL pdf page span
    (`end` already includes D2's one-page overlap)."""

    seg_id: str
    kind: KindHint
    heading: str
    start: int
    end: int


def _page_text(text_dir: Path, page: int) -> str:
    path = text_dir / f"p{page:04d}.txt"
    if not path.is_file():
        return ""
    return path.read_text()


def _discover_class_spans(
    toc: Toc, *, text_dir: Path, book_id: str, last_page: int
) -> list[_ClassSpan]:
    """Every level >= 2 toc entry whose resolved `category` is `classes`/
    `prestige-classes` AND whose own pdf pages contain the `Hit Die: dN`
    marker (design decision D1) -- the discriminator between a real class
    entry and a same-chapter section that only talks ABOUT classes (e.g.
    "Multiclass Characters"). Each qualifying entry's span is extended by
    one page past its own `pdf_page_end`, capped at `last_page` (design
    decision D2), since a class's last column routinely spills onto the
    page the toc assigns to the next entry. An entry with no resolvable
    `pdf_page_start`/`pdf_page_end` (D18 -- an unresolvable toc) is skipped
    rather than guessed at."""
    spans: list[_ClassSpan] = []
    for entry in toc.entries:
        if entry.level < 2:
            continue
        kind = _TOC_CATEGORY_TO_KIND.get(entry.category)
        if kind is None:
            continue
        if entry.pdf_page_start is None or entry.pdf_page_end is None:
            continue

        raw_text = "".join(
            _page_text(text_dir, p) for p in range(entry.pdf_page_start, entry.pdf_page_end + 1)
        )
        if not _HIT_DIE_RE.search(raw_text):
            continue

        start = entry.pdf_page_start
        end = min(entry.pdf_page_end + 1, last_page)
        spans.append(
            _ClassSpan(
                seg_id=f"{book_id}-{kind}-p{start:04d}",
                kind=kind,
                heading=entry.title,
                start=start,
                end=end,
            )
        )
    return spans


def _write_class_segment(
    span: _ClassSpan,
    paragraphs: list[Paragraph],
    entry: ManifestEntry,
    out_dir: Path,
    pages_json: dict[int, int],
    covered_pages: set[int],
    summary: BookSegmentSummary,
    force: bool,
) -> None:
    # Lazy import: `owlsperch.queue.ladder` imports `Segment` from this
    # module at module scope, so importing `starting_tier` from it up top
    # here would be a circular import. By call time both modules are fully
    # loaded, so a local import is safe.
    from owlsperch.queue.ladder import starting_tier

    pages = _pages_in_range(paragraphs, span.start, span.end)
    text = "\n\n".join(p.text for p in paragraphs if span.start <= p.page <= span.end)
    if not pages or not text.strip():
        return
    covered_pages.update(pages)

    summary.counts[span.kind] = summary.counts.get(span.kind, 0) + 1

    seg_path = out_dir / f"{span.seg_id}.json"
    if seg_path.exists() and not force:
        summary.skipped += 1
        return

    printed_pages: list[int | None] = [pages_json.get(p) for p in pages]
    segment = Segment(
        seg_id=span.seg_id,
        book_id=entry.book_id,
        pages=pages,
        printed_pages=printed_pages,
        kind_hint=span.kind,
        heading=span.heading,
        text=text,
        tier=starting_tier(span.kind),
        created_at=_now_iso(),
    )
    atomic_write_text(seg_path, segment.model_dump_json(indent=2) + "\n")
    summary.written += 1


def _supersede_segments_in_span(
    out_dir: Path, book_id: str, class_seg_id: str, start: int, end: int
) -> int:
    """Stamp `superseded_by = class_seg_id` on every OTHER segment of
    `book_id` whose `pages` fall ENTIRELY inside `[start, end]`, in place --
    every other field (`records`, `outcome`, `attempts`, `tier`, ...) is
    preserved. Idempotent: a segment that already carries a `superseded_by`
    (from this or an earlier class span) is left untouched. Returns how many
    segments were newly stamped."""
    count = 0
    for path in sorted(out_dir.glob(f"{book_id}-*.json")):
        if path.stem == class_seg_id:
            continue
        segment = Segment.model_validate_json(path.read_text())
        if segment.seg_id == class_seg_id or segment.superseded_by is not None:
            continue
        if segment.pages and all(start <= p <= end for p in segment.pages):
            segment.superseded_by = class_seg_id
            atomic_write_text(path, segment.model_dump_json(indent=2) + "\n")
            count += 1
    return count


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
    if force:
        _remove_stale_segments(out_dir, entry.book_id, page_range)
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

    # Batch B10c: a separate, toc-driven pass for class/prestige_class
    # segments (see this module's docstring, point 7) -- entirely additive,
    # never touching what build_segments produced above.
    toc = load_toc(data_dir, entry.book_id)
    if toc is None or not toc.entries:
        summary.class_note = "no toc -- no class/prestige_class segments"
        return summary

    last_page = max(_discover_text_pages(text_dir, None), default=None)
    if last_page is None:
        return summary

    class_spans = _discover_class_spans(
        toc, text_dir=text_dir, book_id=entry.book_id, last_page=last_page
    )
    for span in class_spans:
        _write_class_segment(
            span, paragraphs, entry, out_dir, pages_json, covered_pages, summary, force
        )

    superseded_total = 0
    for span in class_spans:
        superseded_total += _supersede_segments_in_span(
            out_dir, entry.book_id, span.seg_id, span.start, span.end
        )
    summary.superseded = superseded_total

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
    # Lazy import: see `_write_class_segment`'s identical comment -- avoids
    # a `segment.runner` <-> `queue.ladder` circular import.
    from owlsperch.queue.ladder import starting_tier

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

    printed_pages: list[int | None] = [pages_json.get(p) for p in pages]
    segment = Segment(
        seg_id=seg_id,
        book_id=entry.book_id,
        pages=pages,
        printed_pages=printed_pages,
        kind_hint=raw.kind,
        heading=raw.heading,
        text=text,
        tier=starting_tier(raw.kind),
        created_at=_now_iso(),
    )
    atomic_write_text(seg_path, segment.model_dump_json(indent=2) + "\n")
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
