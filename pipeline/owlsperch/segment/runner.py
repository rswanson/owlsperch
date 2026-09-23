"""Orchestration for `owlsperch segment <book_id|all>`.

Per book:

1. Look the book up in the manifest (unknown `book_id` is a hard error). The
   book's `text/<book_id>/` directory (written by `owlsperch text`) must
   already exist; `all` silently considers only in-scope/override books
   (mirroring `owlsperch.text.runner`) and prints a "no text output" skip
   line for any of those missing a text dir, instead of erroring.
2. Read EVERY `p{NNNN}.txt` the book has into paragraphs, joined with their
   `p{NNNN}.meta.json` sidecar stats into one flat, book-wide
   `owlsperch.segment.headings.Paragraph` stream (a segment may span a page
   boundary, so segmentation never works page by page). A page whose `.txt`
   exists but whose `.meta.json` is missing (or does not have one entry per
   paragraph) is a hard error naming every such page and `owlsperch text
   <book_id> --force`. (Batch B10c-mand19) `--pages A-B` does NOT restrict
   this stream: segmentation always sees the whole book, exactly as a plain
   run does, and `--pages` restricts ONLY which of the resulting segments
   are written and deleted (points 4/5 below). Building from a
   page-restricted stream instead -- what this used to do -- silently
   truncated every segment that began before the range (the real-corpus
   damage: `owlsperch segment phb1 --force --pages 46-46` re-cut the paladin
   class segment, pages 43-47, down to page 46 alone) and left a headingless
   page-top fragment wherever a section started on the page before.
3. Compute the book's body-median word height and split the stream into
   segments (`owlsperch.segment.splitter.build_segments`): pattern anchors
   (spell, stat_block, feat, table) plus `rules_section` for everything
   between them, split further at headings.
4. If `--force` was given, first delete every existing
   `segments/<book_id>/<seg_id>.json` whose FIRST page falls inside the
   `--pages` range (the whole book when no `--pages` was given) -- otherwise
   a page whose segmentation changed (e.g. it used to produce two segments
   and now produces one) would leave a stale orphan file behind alongside
   the freshly written ones. A segment whose first page is OUTSIDE the range
   is never deleted, even when it spans into the range.
5. For each non-empty segment, compute its `seg_id` (`<book_id>-p<NNNN>-<NN>`
   -- first page spanned, then a per-first-page ordinal, both assigned in
   stream order so reruns reproduce the same ids) and write
   `segments/<book_id>/<seg_id>.json` unless it already exists and `--force`
   was not given. (Batch B10c-mand19) With `--pages A-B` a segment is
   written only when its own first page (`min(pages)`) lies in `[A, B]` --
   the same rule point 4 deletes by, so write and delete stay symmetric. A
   segment whose first page lies before the range is left exactly as it is
   on disk (so a class segment starting on page 43 survives `--pages 46-46`
   untouched), and a segment starting inside the range is written WHOLE,
   including whatever pages it continues onto past `B`. Prints one line per
   book: counts per `kind_hint`, and how many segment files were written vs
   already present.
6. As a coverage safety net (every page with any text should land in at
   least one segment -- acceptance criterion 5), records any page in the
   processed range that is in no segment's `pages` onto
   `BookSegmentSummary.uncovered_pages`, which `render` prints as its own
   (single) `warning:` line. (Batch B10c-mand19) Coverage is read back off
   the segment files actually on disk once the run has finished -- live or
   `superseded_by`-stamped, under `segments/<book_id>/` or the
   `human/<book_id>/` inbox, and including segments this run didn't rewrite
   -- so it is meaningful for a `--pages` run (processed range `[A, B]`) and
   a `--kinds` run (processed range: the whole book) too, not just a plain
   one.
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
   span AND (batch B10c-mand11) whose own `(heading, kind_hint)` is
   class-structural for that class's title (`owlsperch.supersede.
   is_class_owned_fragment` -- the class title itself, "Game Rule
   Information", "Class Skills", "Class Features", "Ex-<Title>", "<Race>
   <Title> Starting Package", or any `table`) gets `superseded_by` set to
   the class segment's id (if not already set) -- an in-place stamp, not a
   delete/rewrite, so a plain (non-`--force`)
   `owlsperch segment <book_id>` run both writes the handful of new class
   segments AND stamps every existing class-chapter fragment segment,
   without touching their own `outcome`/`attempts` bookkeeping. (Batch
   B10c-mand2) A segment NEWLY stamped this way also has its record claims
   RELEASED in the same pass, via `owlsperch.supersede.
   release_segment_claims`: every path in its `records`/`pending_records`
   is moved to `superseded/<book_id>/<type>/<file>.json` (never deleted)
   and those two lists are cleared, with one `ReleasedRecord` appended to
   `released_records` per path -- freeing the class segment being stamped
   in to claim the same path (most often a level table sharing the class's
   own printed title, and so the same slug/id/path) without
   `owlsperch.queue.complete`'s ownership guard refusing it as a collision.
   A segment that already carried `superseded_by` from an earlier run is
   skipped entirely by this whole pass (stamp AND release), which is
   exactly why `owlsperch queue audit --fix` exists as a separate,
   retroactive path for segments stamped before this release-at-stamp-time
   behavior existed. (Batch B10c-mand11) A segment inside the span whose
   heading is NOT class-structural is left COMPLETELY alone -- not
   stamped, claims not released -- and counted in the summary's own
   "N in-span segment(s) left live" line: page span alone used to swallow
   printed SIDEBARS sharing a class's pages (the real PHB's "FAMILIARS",
   "THE PALADIN'S MOUNT", "SCHOOL SPECIALIZATION", ...) into no canonical
   record at all. Since the predicate is per-class, a fragment that is the
   NEXT class's own heading on a shared page is now left for that class's
   own span to stamp instead of being claimed by whichever span ran first.
   `owlsperch queue audit`'s `wrongly_superseded` pass is the retroactive
   counterpart, for data stamped before this scoping existed.
9. (Batch B10c-mand6) A class/prestige_class span's TEXT is back-extended
   from its own heading paragraph to the top of the heading's own page
   (`_back_extend_start_index`) -- the real corpus's column
   reconstruction routinely prints the tail of a page (a class's own
   pre-heading flavor sections, or even the PREVIOUS class's Starting
   Package) before the current class's ALL-CAPS heading. The extension
   never reaches past a still-earlier class's own heading. It still
   overlaps the previous class's own (heading-anchored) end on a shared
   page for non-flavor content (e.g. a Starting Package section, resolved
   by the extraction prompt's own attribution rule) -- but (judgement
   finding 1, follow-up) a class's own opening flavor run-in paragraph
   specifically (the one carrying BOTH an "Alignment:" and a "Religion:"
   marker -- `_is_class_flavor_paragraph`) is excluded from the previous
   class's own written text, so it survives in exactly one segment: the
   class it actually belongs to. See the function's own docstring for
   why the rest of the overlap can't be resolved the same way.
   `--kinds class[,prestige_class]` (`owlsperch segment <book_id> --kinds
   class`) re-runs ONLY this toc-driven pass, for exactly the requested
   kind(s): no whole-book `build_segments` pass and no stale-segment-file
   removal (the coverage warning of point 6 still runs, since B10c-mand19
   reads it off the segment files on disk rather than off this run's own
   `build_segments` output). Every other kind_hint comes from that
   single whole-book pass and can't be produced selectively, so any other
   value is a hard error (exit 1, nothing written). Rewriting a class
   segment file this way resets it to `pending` with no claims -- delete
   any records it previously claimed first (`queue reset --hard`) or they
   are left orphaned on disk.
10. (Batch B10c-mand18) The end cap in point 9 can fall BETWEEN a class's
   own printed level-table caption and the grid that caption belongs to:
   the real PHB p0040 prints "Table 3-9: The Fighter", then "MONK" (the
   next class's heading), then the fighter's own grid, so the fighter's
   segment ended with a bare caption and NO level table at all -- the
   extractor could not fill `level_table`/`bab_progression`/
   `save_progressions` and replied `needs_context`. So after the end cap is
   resolved, `_class_own_table_indices` scans the bounded window
   `[end_index, page_cap_index)` (never past the span's own D2-extended
   page range) and adds back every caption-or-grid paragraph whose caption
   names THIS class -- from the paragraph's own first-line caption, else
   from the caption in scope, seeded from the cut's own page inside the
   span's own text. A caption's scope covers its own (possibly
   poppler-fragmented) consecutive grid paragraphs and closes at the first
   prose after them, so one caption can't adopt every later uncaptioned
   grid on the page. The next class's own start index is deliberately
   unchanged (its text may still hold the same grid -- the extraction
   prompt's pre-heading attribution rule resolves that, as it already does
   for every other shared-page overlap).
11. (Batch B10c-mand21) The same end cap also strands a class's own
   class-structural TAIL sections. On the real PHB p0040 the fighter's own
   "Dwarf Fighter Starting Package" CONTINUES past "MONK" (its Feat:/Bonus
   Feat:/Gear:/Gold: lines) and its whole "Human Fighter Starting Package"
   is printed after that heading too, so both were in no record at all. So
   `_class_own_tail_indices` scans the same bounded window
   `[end_index, page_cap_index)` and adds back (a) any run headed by a
   class-structural heading NAMING this class ("<Race> <Title> Starting
   Package", "Ex-<Title>" -- `owlsperch.supersede.is_class_owned_fragment`,
   minus the three generic headings every class prints), up to the next
   printed heading, and (b) the continuation of a Starting Package section
   the cut fell inside (the cut heading itself does not close it; every
   other heading does). Only package-shaped paragraphs are collected -- a
   label-led run-in or a grid -- so the next class's interleaved body prose
   is skipped rather than ending the run, and a different class's own
   structural heading ("Human Monk Starting Package") stays out entirely.
   As in point 10, the next class's own start index is unchanged.
12. (Batch B12) A THIRD pass, the same shape as points 7-9 but for the
   Monster Manual: every level >= 2 toc entry whose category is `monsters`
   and whose own pages carry a `Hit Dice` marker becomes one `monster`
   segment, id `<book_id>-monster-p<NNNN>-<NN>` (`NNNN` = the page its own
   printed heading is on, `<NN>` a per-page ordinal -- unlike a class, three
   monsters routinely share a page, so the class pass's page-only id form
   would collide). `owlsperch.segment.monsters` holds all of the discovery
   logic (which entries collapse into one grouped segment, where each span's
   text starts and stops, and when a span falls back to its whole page
   range); this module writes the files and stamps `superseded_by` on the
   in-span fragments the monster entry owns
   (`owlsperch.supersede.is_monster_owned_fragment`, the monster counterpart
   of point 8's predicate, with the same left-live behavior for a sidebar).
   `--kinds monster` re-runs only this pass, `--pages` restricts it, and the
   coverage warning covers it, exactly as for `--kinds class`.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Callable
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
from owlsperch.segment.anchors import Kind
from owlsperch.segment.headings import Paragraph, compute_body_median, is_heading
from owlsperch.segment.monsters import (
    MONSTER_KIND,
    MonsterSpan,
    discover_monster_spans,
)
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
    "monster",
    "npc",
    "template",
    "errata_entry",
    "update_entry",
)

#: Batch B11, design decision D1: a book's manifest `kind` gates whether
#: `owlsperch.segment.anchors.find_triggers` ever produces an errata/update
#: anchor at all. Any other manifest kind maps to `None`, so a rulebook
#: sentence like "(see the Player's Handbook, page 44)" can never create a
#: bogus segment.
_ENTRY_KIND_TO_ANCHOR_KIND: dict[str, Kind] = {
    "errata": "errata_entry",
    "update": "update_entry",
}

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


class ReleasedRecord(BaseModel):
    """One record claim released from a superseded segment (batch
    B10c-mand2, see `owlsperch.supersede.release_segment_claims`): `path` is
    the record path (relative to `$OWLSPERCH_DATA`), exactly as the segment
    spelled it in its own `records`/`pending_records` list, that the
    segment used to claim; `moved_to` is where the file now lives under
    `superseded/<book_id>/<type>/<file>.json`, or `None` when the file was
    left exactly where it was (already missing on disk, or still
    legitimately owned by a different, still-live segment -- releasing
    must never steal a live segment's record)."""

    model_config = ConfigDict(extra="forbid")

    path: str
    moved_to: str | None = None


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
    #: Batch B10c-mand2: record claims released when this segment was
    #: stamped `superseded_by` (by the class-span pass below, at stamp
    #: time) or, retroactively, by `owlsperch queue audit --fix` -- see
    #: `owlsperch.supersede.release_segment_claims`. Empty for a segment
    #: that has never held a claim released this way. A segment JSON file
    #: written before this field existed still loads fine (defaulting to
    #: `[]`), since `Segment` is `extra="forbid"` and this field has a
    #: default.
    released_records: list[ReleasedRecord] = []
    #: Batch B10c-mand4: the tier this segment's current `pending_records`
    #: claim was registered at, stamped by `owlsperch queue complete` when it
    #: accepts record paths. `owlsperch validate`'s group write-back uses it
    #: as the attempt tier when NO failing record in the group carries a
    #: readable `extraction.tier` of its own (the `missing_record_path` case
    #: -- the file isn't there to read one from), so re-validating an
    #: unchanged segment stays idempotent instead of walking the ladder once
    #: per run. `None` for a segment that has never had a claim accepted
    #: (and for every segment written before this field existed).
    claim_tier: str | None = None


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
    #: Batch B10c-mand2: how many record claims were released (via
    #: `owlsperch.supersede.release_segment_claims`) from segments NEWLY
    #: stamped `superseded_by` this run -- 0 whenever `superseded` is 0, and
    #: also 0 for a run that stamps segments holding no claims at all.
    released: int = 0
    #: Batch B10c-mand11: how many segments fell entirely inside a class
    #: span this run and were still LEFT LIVE, because their own heading
    #: isn't class-structural for that class (`owlsperch.supersede.
    #: is_class_owned_fragment`) -- a printed sidebar like "FAMILIARS".
    #: Counted once per segment across every span (a segment one span
    #: leaves live can be stamped by a later span that does own it, and
    #: then isn't counted here at all).
    left_live: int = 0
    #: Batch B10c: set (instead of left "") when the book has records but
    #: no usable `toc/<book_id>.json` -- printed as a second summary line
    #: rather than replacing the main one, since ordinary segmentation still
    #: ran normally.
    class_note: str = ""
    #: Batch B12: the monster pass's own note line (a toc-less book, the
    #: counts of grouped/absorbed/fallback spans, or the toc entries it had
    #: to skip) -- printed the same way as `class_note`.
    monster_note: str = ""
    #: Batch B10c-mand19: every page in the PROCESSED range (`--pages A-B`,
    #: else the whole book) that has text but is in no segment's `pages` on
    #: disk once the run has finished -- live or superseded, this run's
    #: writes or an earlier run's. Also printed as a `warning:` line on
    #: stderr; exposed here so a caller (and the tests) can assert on it
    #: without capturing stderr.
    uncovered_pages: list[int] = field(default_factory=list)

    def render(self) -> str:
        if self.note:
            return f"{self.book_id}: {self.note}"
        counts_str = " / ".join(
            f"{kind} {self.counts[kind]}" for kind in _KIND_ORDER if kind in self.counts
        )
        lines = [f"{self.book_id}: {counts_str} ({self.written} written, {self.skipped} skipped)"]
        if self.superseded:
            lines.append(f"{self.book_id}: {self.superseded} segment(s) marked superseded_by")
        if self.released:
            lines.append(f"{self.book_id}: {self.released} record claim(s) released")
        if self.left_live:
            lines.append(
                f"{self.book_id}: {self.left_live} in-span segment(s) left live"
                " (not entity-structural)"
            )
        if self.class_note:
            lines.append(f"{self.book_id}: {self.class_note}")
        if self.monster_note:
            lines.append(f"{self.book_id}: {self.monster_note}")
        if self.uncovered_pages:
            pages_str = ", ".join(str(p) for p in self.uncovered_pages)
            lines.append(
                f"warning: {self.book_id}: page(s) with text but no segment coverage: {pages_str}"
            )
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
    """Delete every existing `segments/<book_id>/*.json` whose FIRST page
    falls inside the `--pages` range, so a page whose segmentation changed
    (e.g. it used to produce two segments and now produces one) doesn't
    leave a stale orphan file behind. Only called when `--force` is given;
    the whole book counts as in range when no `--pages` was given.

    Batch B10c-mand19: this is exactly the rule `_in_write_range` applies
    to what gets WRITTEN, so delete and write stay symmetric -- a segment
    whose first page lies before the range is neither deleted here nor
    rewritten below, even when it spans into the range."""
    prefix = f"{book_id}-"
    for path in out_dir.glob(f"{book_id}-*.json"):
        match = _SEG_FILENAME_RE.match(path.name.removeprefix(prefix))
        if match is None:
            continue
        if _in_write_range(int(match.group(1)), page_range):
            path.unlink()


def _in_write_range(first_page: int, page_range: tuple[int, int] | None) -> bool:
    """Batch B10c-mand19: the single rule deciding whether a segment is
    this run's business at all -- its own FIRST page (`min(pages)`, the one
    its `seg_id` encodes and the one `_remove_stale_segments` keys its
    deletions off) must lie inside `--pages`. No `--pages` means the whole
    book, so a plain run is unaffected."""
    return page_range is None or page_range[0] <= first_page <= page_range[1]


def _pages_covered_on_disk(data_dir: Path, book_id: str) -> set[int]:
    """Every page named by any of this book's segment files currently on
    disk -- live or `superseded_by`-stamped, written by this run or an
    earlier one (batch B10c-mand19: the coverage check reads the result off
    disk, so it is meaningful for a `--pages` or `--kinds` run, neither of
    which produces a whole-book `build_segments` output of its own to check
    against).

    Both segment homes count: `segments/<book_id>/` AND `human/<book_id>/`,
    where `owlsperch.queue.common.move_segment_to_human` parks a segment the
    escalation ladder couldn't resolve. Such a segment still covers its
    pages perfectly well -- it's awaiting a human extraction decision, not
    missing -- so leaving the inbox out would report its pages as coverage
    gaps (`human/<book_id>/overrides/*.json`, B11's unmatched-override
    files, live in a SUBDIRECTORY and so are never globbed here).

    A file that isn't parseable JSON with a `pages` list contributes nothing
    rather than raising -- coverage is a warning-only safety net, never a
    reason to fail a run."""
    covered: set[int] = set()
    for parent in ("segments", "human"):
        for path in (data_dir / parent / book_id).glob(f"{book_id}-*.json"):
            try:
                raw = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            if not isinstance(raw, dict):
                continue
            pages = raw.get("pages")
            if not isinstance(pages, list):
                continue
            covered.update(p for p in pages if isinstance(p, int))
    return covered


def _report_coverage(
    data_dir: Path,
    book_id: str,
    paragraphs: list[Paragraph],
    page_range: tuple[int, int] | None,
    summary: BookSegmentSummary,
) -> None:
    """Record every page in the PROCESSED range that has text but is in no
    segment's `pages` (batch B10c-mand19 -- runs for a plain, a `--pages`
    and a `--kinds` run alike) onto `summary.uncovered_pages`, which
    `BookSegmentSummary.render` prints as its own `warning:` line. Only
    pages that actually contributed a paragraph are expected to be covered:
    a page whose `.txt` is blank (no text at all, e.g. a pure image page)
    never enters the stream and can't be a coverage gap."""
    pages_with_content = {p.page for p in paragraphs if _in_write_range(p.page, page_range)}
    summary.uncovered_pages = sorted(pages_with_content - _pages_covered_on_disk(data_dir, book_id))


def _pages_spanned(paragraphs: list[Paragraph], start: int, end: int) -> list[int]:
    pages: list[int] = []
    seen: set[int] = set()
    for p in paragraphs[start:end]:
        if p.page not in seen:
            seen.add(p.page)
            pages.append(p.page)
    return pages


def _pages_spanned_indices(paragraphs: list[Paragraph], indices: list[int]) -> list[int]:
    """Like `_pages_spanned`, but over an explicit, possibly non-contiguous
    list of paragraph indices (judgement finding 1, B10c-mand6 follow-up --
    `_write_class_segment` excludes specific paragraphs from a contiguous
    `[start, end)` range rather than shrinking the range itself)."""
    pages: list[int] = []
    seen: set[int] = set()
    for i in indices:
        page = paragraphs[i].page
        if page not in seen:
            seen.add(page)
            pages.append(page)
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


#: Matches every character that isn't a lowercase letter or digit --
#: `_heading_matches_title`'s normalization strips punctuation/whitespace
#: entirely rather than collapsing it, so "Chapter 3: Classes" and
#: "chapter3classes" compare equal.
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]")


def _heading_matches_title(text: str, title: str) -> bool:
    """A printed class heading paragraph matched against its toc section
    title: case/punctuation/whitespace-insensitive, tolerating a trailing
    plural "s" on either side -- PHB 3.5 prints "WIZARDS" for the toc's
    "Wizard"."""

    def norm(s: str) -> str:
        return _NON_ALNUM_RE.sub("", s.casefold())

    a = norm(text)
    b = norm(title)
    if not a or not b:
        return False
    return a == b or a == b + "s" or b == a + "s"


def _class_start_index(paragraphs: list[Paragraph], span: _ClassSpan) -> int | None:
    """The paragraph index this class span's own text should start at (Part
    1, B10c-mand3): the first paragraph on the span's own toc start page
    whose text matches the span's heading (see `_heading_matches_title`);
    failing that, the first such match anywhere in the span's page range
    (a heading printed a page later than the toc says, still better than
    nothing); else `None` (never matched -- the caller falls back to the
    whole-page-range behavior rather than dropping the class). Paragraphs
    are in non-decreasing page order, so a single left-to-right scan
    naturally prefers an on-start-page match over a later one."""
    fallback: int | None = None
    for i, p in enumerate(paragraphs):
        if not (span.start <= p.page <= span.end):
            continue
        if not _heading_matches_title(p.text, span.heading):
            continue
        if p.page == span.start:
            return i
        if fallback is None:
            fallback = i
    return fallback


def _first_index_after_page(paragraphs: list[Paragraph], page: int) -> int:
    """The first paragraph index whose page is greater than `page`, or
    `len(paragraphs)` if none (used to cap a class span's end index at its
    own toc page range when no later class span's start comes sooner)."""
    for i, p in enumerate(paragraphs):
        if p.page > page:
            return i
    return len(paragraphs)


def _page_start_index(paragraphs: list[Paragraph], page: int) -> int:
    """The index of the first paragraph on `page` (paragraphs are in
    non-decreasing page order, so a single left-to-right scan finds it).
    Returns `len(paragraphs)` if `page` has no paragraphs at all -- callers
    only ever pass a page a heading paragraph is already known to be on, so
    that case doesn't arise in practice."""
    for i, p in enumerate(paragraphs):
        if p.page == page:
            return i
    return len(paragraphs)


#: A class's own opening flavor run-in paragraph (Alignment:/Religion:/
#: Background:/Races:/Other Classes:/Role:, all folded into one printed
#: paragraph) is identified by carrying BOTH of these two markers --
#: `_run_class_pass` uses this to tell a class's own pre-heading flavor
#: text apart from unrelated content (e.g. a Starting Package section)
#: that a column-reconstruction quirk also happens to strand in the same
#: ambiguous, shared-page zone (judgement finding 1, B10c-mand6 follow-up),
#: so the flavor paragraph can be excluded from the PREVIOUS class's own
#: end-capped range instead of surviving in both.
_CLASS_FLAVOR_MARKERS = ("Alignment:", "Religion:")


def _is_class_flavor_paragraph(text: str) -> bool:
    return all(marker in text for marker in _CLASS_FLAVOR_MARKERS)


def _back_extend_start_index(
    paragraphs: list[Paragraph], heading_index: int, resolved_starts: list[int]
) -> int:
    """B10c-mand6, judgement finding 5: back-extend a class span's TEXT
    start from its own heading paragraph to the top of the page the
    heading is printed on. The real corpus shows a class's own pre-heading
    column tail routinely lands on the shared page: PHB p0050's paragraph
    order is [0] rogue's own six flavor run-in sections (Alignment/
    Religion/Background/Races/Other Classes/Role, all in one paragraph),
    [1]-[2] ranger's Starting Package section, [3] "ROGUE", [4] rogue's
    body -- today's heading-anchored start (paragraph 3) drops rogue's own
    flavor sections entirely, leaving them stranded at the tail of
    ranger's segment instead. Back-extending to the page's own start
    paragraph recovers them.

    Never extend past a still-earlier class's own heading -- bounded by
    `previous_heading_index` (the greatest entry in `resolved_starts`
    strictly below `heading_index`, if any) -- so back-extension from one
    class can't reach all the way into a class two spans back. This still
    OVERLAPS the previous class's own (heading-anchored, unchanged) end
    index on a shared-page boundary: both the ranger and rogue segments'
    raw index ranges cover p0050's paragraphs 0-2. `_run_class_pass`
    resolves the one piece of that overlap that matters exclusively
    (rogue's own paragraph 0, its flavor run-in -- see
    `_is_class_flavor_paragraph`) by excluding it from ranger's own
    written text; the rest of the overlap (paragraphs 1-2, ranger's own
    Starting Package, which also falls in rogue's back-extended range) is
    left alone -- there is no single cut point that gives ranger its own
    Starting Package AND rogue its own flavor paragraph AND excludes every
    duplicate, so that residual non-flavor overlap is resolved by the
    extraction prompt's own attribution rule (see `owlsperch.queue.prompt`'s
    `class`/`prestige_class` rules) rather than by picking one owner here."""
    page_start = _page_start_index(paragraphs, paragraphs[heading_index].page)
    earlier = [i for i in resolved_starts if i < heading_index]
    previous_heading_index = max(earlier) if earlier else None
    if previous_heading_index is None:
        return page_start
    return max(page_start, previous_heading_index + 1)


#: A printed table caption line, e.g. "Table 3-9: The Fighter" (any dash
#: glyph -- the real PHB prints an EN DASH -- and an optional leading "The"
#: in the title, stripped by `_table_caption_title` so the remainder can be
#: compared with `_heading_matches_title` against a toc class title).
_TABLE_CAPTION_RE = re.compile(
    r"^\s*Table\s+\d+\s*[\u2010-\u2015\-]\s*\d+\s*:\s*(?P<title>.+?)\s*$"
)


def _table_caption_title(text: str) -> str | None:
    """The entity a paragraph's own FIRST line captions, when that line is a
    printed "Table N-M: <title>" caption (with a leading "The " stripped),
    else `None`. Used by `_class_own_table_indices` below both for a
    caption-only paragraph and for a caption glued onto its own grid."""
    lines = text.strip().splitlines()
    if not lines:
        return None
    match = _TABLE_CAPTION_RE.match(lines[0])
    if match is None:
        return None
    title = match.group("title").strip()
    if title[:4].casefold() == "the ":
        title = title[4:].strip()
    return title or None


def _is_table_paragraph(text: str) -> bool:
    """A tab-joined grid, the shape `owlsperch.text.columns` writes a
    detected table group as (one row per line, cells tab-separated)."""
    return any("\t" in line for line in text.splitlines())


def _class_own_table_indices(
    paragraphs: list[Paragraph],
    *,
    heading: str,
    text_start_index: int,
    end_index: int,
    page_cap_index: int,
) -> frozenset[int]:
    """B10c-mand18: the indices of THIS class's own printed table
    paragraphs that sit at or past its end cap, and so would otherwise be
    lost from its text entirely.

    The real PHB p0040's paragraph order is [0] "Table 3-9: The Fighter"
    (the fighter's own level-table caption), [1] "MONK" (the next class's
    heading), [2] the fighter's level-table body ("Level\tAttack Bonus..."
    through "20th\t+20/+15/+10/+5..."). The end cap (min of the next
    class's own heading index and the first paragraph past the span's toc
    `pdf_page_end` + 1) therefore cut the fighter's text right after the
    bare caption, leaving its segment with NO level table at all -- the
    extractor cannot fill `level_table`/`bab_progression`/
    `save_progressions` and replies `needs_context`.

    So, over the bounded window `[end_index, page_cap_index)` (never past
    the span's own D2-extended page range), every caption-or-grid paragraph
    whose caption names THIS class is pulled back into this span's text.
    Ownership comes from the paragraph's own first-line caption when it has
    one, else from the caption currently in scope -- seeded from the same
    page as the cut point inside this span's own text (exactly the
    fighter's separate, pre-heading caption), and replaced by any later
    caption in the window, so a control case's "Table 3-10: The Monk" grid
    is never pulled into the fighter. A grid with no caption in scope at
    all is left alone.

    A caption's scope covers its own (possibly poppler-fragmented, hence
    consecutive rather than single) grid paragraphs and ENDS at the first
    non-grid, non-caption paragraph after them -- the intervening heading/
    prose before the grid is skipped, but once the grid has been collected,
    prose closes the caption out. Otherwise one caption would keep adopting
    every later uncaptioned grid on the page (the real p0040's "Human
    Fighter Starting Package" skill grid, printed after the monk's own
    opening prose).

    The next class's own start index is deliberately NOT changed: its
    back-extended text may still contain the same grid, and the extraction
    prompt's own pre-heading attribution rule resolves that the way it
    already does for every other shared-page overlap."""
    if end_index >= page_cap_index:
        return frozenset()

    # Seed the caption from this span's OWN text, but only from the page the
    # cut falls on -- the shared-page case this exists for. Reaching further
    # back could let a stale caption of this class adopt an unrelated grid.
    caption_title: str | None = None
    cut_page = paragraphs[end_index].page
    for i in range(end_index - 1, text_start_index - 1, -1):
        if paragraphs[i].page != cut_page:
            break
        title = _table_caption_title(paragraphs[i].text)
        if title is not None:
            caption_title = title
            break

    extra: set[int] = set()
    collecting = False
    for i in range(end_index, page_cap_index):
        text = paragraphs[i].text
        own_caption = _table_caption_title(text)
        is_grid = _is_table_paragraph(text)
        if own_caption is not None:
            caption_title = own_caption
            collecting = False
        elif collecting and not is_grid:
            # The caption's own grid is over -- close its scope so it can't
            # adopt a later, unrelated uncaptioned grid.
            caption_title = None
            collecting = False
            continue
        if caption_title is None or not _heading_matches_title(caption_title, heading):
            continue
        if own_caption is not None or is_grid:
            extra.add(i)
            collecting = collecting or is_grid
    return frozenset(extra)


#: The normalized tail of a "<Race> <Title> Starting Package" heading --
#: the same literal `owlsperch.supersede` matches on, in the same
#: `normalize_heading` form (punctuation and whitespace deleted).
_STARTING_PACKAGE_TAIL = "startingpackage"

#: A Starting Package BODY paragraph's own first line: the PHB prints every
#: one of them as a run of short printed labels ("Armor: ...", "Weapons:
#: ...", "Skill Selection: ...", "Feat: ...", "Gear: ...", "Gold: ..."),
#: never as ordinary prose. A generic label test rather than an enumerated
#: list, so a package printing an unexpected label still matches; ordinary
#: body prose ("Dotted across the landscape are monasteries -- small,
#: walled cloisters ...") has no such early colon, and keeping it out is
#: exactly what this is for (see `_class_own_tail_indices`).
_PACKAGE_BODY_LABEL_RE = re.compile(r"^[A-Z][A-Za-z’'()\- ]{0,28}:")

#: A class LEVEL table's own header row ("Level<TAB>Attack Bonus<TAB>..."),
#: in the tab-joined form `owlsperch.text.columns` writes a grid as. WHICH
#: class a stranded level table belongs to is decided by its printed
#: caption, in `_class_own_table_indices` (B10c-mand18), so the tail pass
#: below never collects one on its own: a neighbouring class's level table
#: routinely lands in the same window (the real PHB's rogue table inside
#: the ranger's window, its wizard table inside the sorcerer's).
_LEVEL_TABLE_ROW_RE = re.compile(r"^[ \t]*Level\t", re.MULTILINE)


def _is_level_table_paragraph(text: str) -> bool:
    return _LEVEL_TABLE_ROW_RE.search(text) is not None


#: Every printed Starting Package ends with its own "Gold: NdN gp." line
#: (run into the end of its last paragraph), which is what BOUNDS a
#: collected package run in `_class_own_tail_indices`: without it, the NEXT
#: class's own package body paragraphs -- which reading order routinely
#: prints BEFORE that class's own package heading on a shared page, the
#: same bleed `_back_extend_start_index` documents -- would be collected as
#: this class's continuation.
_GOLD_LABEL_RE = re.compile(r"\bGold:")


def _first_line(text: str) -> str:
    """A paragraph's first non-blank line (a printed heading is always a
    single line, so this is the line every heading test below reads)."""
    for line in text.strip().splitlines():
        if line.strip():
            return line
    return ""


def _is_package_body_paragraph(text: str) -> bool:
    """Whether a paragraph looks like part of a printed Starting Package's
    BODY -- a label-led run-in paragraph (`_PACKAGE_BODY_LABEL_RE`) or the
    package's own printed skill grid."""
    return _is_table_paragraph(text) or bool(_PACKAGE_BODY_LABEL_RE.match(_first_line(text)))


def _is_own_starting_package_heading(text: str, class_title: str) -> bool:
    """Whether a paragraph's own heading line is one of THIS class's
    printed "<Race> <Title> Starting Package" headings."""
    from owlsperch.supersede import (  # lazy: see `_supersede_segments_in_span`.
        is_class_owned_fragment,
        normalize_heading,
    )

    first = _first_line(text)
    if not normalize_heading(first).endswith(_STARTING_PACKAGE_TAIL):
        return False
    return is_class_owned_fragment(first, "rules_section", class_title)


def _is_own_class_structural_heading(text: str, class_title: str) -> bool:
    """Whether a paragraph's own heading line is a class-structural heading
    that NAMES this class -- "<Race> <Title> Starting Package",
    "Ex-<Title>", or the class title itself (`owlsperch.supersede.
    is_class_owned_fragment`, the same predicate the supersede pass scopes
    itself by).

    The three GENERIC class-structural headings every class prints ("Game
    Rule Information", "Class Skills", "Class Features") are deliberately
    excluded here: past this class's own end cap they are, if anything, the
    NEXT class's, and nothing in their wording says otherwise."""
    from owlsperch.supersede import (  # lazy: see `_supersede_segments_in_span`.
        CLASS_STRUCTURAL_HEADINGS,
        is_class_owned_fragment,
        normalize_heading,
    )

    first = _first_line(text)
    if normalize_heading(first) in CLASS_STRUCTURAL_HEADINGS:
        return False
    return is_class_owned_fragment(first, "rules_section", class_title)


def _class_own_tail_indices(
    paragraphs: list[Paragraph],
    *,
    heading: str,
    text_start_index: int,
    end_index: int,
    page_cap_index: int,
    body_median: float,
) -> frozenset[int]:
    """B10c-mand21: the indices of THIS class's own class-structural TAIL
    sections sitting at or past its end cap, which would otherwise be lost
    from its text entirely -- the same rescue `_class_own_table_indices`
    performs for a stranded level table, applied to the sections a printed
    class entry ends with.

    The real PHB p0040's paragraph order is [0] "Table 3-9: The Fighter",
    [1] "MONK" (the next class's heading, and so the fighter's end cap),
    [2] the fighter's own level grid, [3] the CONTINUATION of the fighter's
    own "Dwarf Fighter Starting Package" (its Feat:/Bonus Feat:/Gear:/Gold:
    lines -- that section's heading and first paragraph are back on p0039,
    inside the span), [4] "Human Fighter Starting Package", [5]/[7]/[8]
    that package's own body, skill grid and Feat:/Gear:/Gold: lines, with
    the monk's own opening flavor prose printed in between at [6].
    Everything from [3] on was in no record at all: `class:phb1:fighter`
    had half a Dwarf Fighter Starting Package and no Human one.

    So, over the same bounded window `[end_index, page_cap_index)`
    `_class_own_table_indices` uses (never past the span's own D2-extended
    page range), this adds back:

    (a) any paragraph run headed by a class-structural heading NAMING this
        class (`_is_own_class_structural_heading`): the heading itself plus
        the package-body paragraphs after it (`_is_package_body_paragraph`),
        ending at this class's own printed "Gold: NdN gp." line or the next
        printed heading, whichever comes first (an "Ex-<Title>" run, which
        prints no such line, ends at the next heading). A DIFFERENT class's
        own structural heading ("Human Monk Starting Package") never starts
        a run, and closes an open one, so it and its body stay out.
    (b) the CONTINUATION of a section the cut fell inside: when the last
        printed heading before the cut, within this span's own text, is one
        of this class's own Starting Package headings, collection starts
        open at the window's first paragraph and is bounded the same way --
        up to AND INCLUDING the first paragraph carrying a `Gold:` label.
        The cut heading itself (the next class's own, at exactly
        `end_index`) is what stranded that continuation, so it alone does
        not close the run; every other heading in the window does.

    That `Gold:` bound is what keeps a run from running on into the NEXT
    class's own package body paragraphs, which reading order routinely
    prints BEFORE that class's own package heading on a shared page (the
    same bleed `_back_extend_start_index` documents).

    Only package-shaped paragraphs are collected -- a label-led run-in or a
    grid -- so non-package prose inside a run, e.g. the next class's own
    opening flavor prose, which reading order interleaves with the package
    on the shared page, is SKIPPED rather than ending the run (the real
    p0040's [6]: skipping it, instead of stopping there, is what still
    brings [7]-[8], the rest of the Human Fighter package, back, up to its
    own Gold line). A class LEVEL table (`_is_level_table_paragraph`) is
    never collected here -- which class a stranded one belongs to is
    decided by its printed caption in `_class_own_table_indices`, and a
    neighbouring class's routinely lands in this window (the real PHB's
    rogue table inside the ranger's window, its wizard table inside the
    sorcerer's). A paragraph that is a class's own opening flavor run-in
    (`_is_class_flavor_paragraph`, which B10c-mand6 deliberately gives to
    exactly one class) is never collected either.

    As with `_class_own_table_indices`, the NEXT class's own start index is
    deliberately unchanged: its back-extended text may still contain these
    paragraphs, and the extraction prompt's own pre-heading attribution
    rule resolves that the way it already does for every other shared-page
    overlap."""
    if end_index >= page_cap_index:
        return frozenset()

    # (b) Does the cut fall INSIDE one of this class's own Starting Package
    # sections? -- i.e. is the last printed heading before it, within this
    # span's own text, that section's own heading?
    collecting = False
    gold_bounded = False
    for i in range(end_index - 1, text_start_index - 1, -1):
        if not is_heading(paragraphs[i], body_median):
            continue
        collecting = _is_own_starting_package_heading(paragraphs[i].text, heading)
        gold_bounded = collecting
        break

    extra: set[int] = set()
    for i in range(end_index, page_cap_index):
        text = paragraphs[i].text
        if is_heading(paragraphs[i], body_median):
            if _is_own_class_structural_heading(text, heading):
                extra.add(i)
                collecting = True
                gold_bounded = _is_own_starting_package_heading(text, heading)
            elif i != end_index:
                collecting = False
            continue
        if not collecting or _is_class_flavor_paragraph(text):
            continue
        if _is_package_body_paragraph(text) and not _is_level_table_paragraph(text):
            extra.add(i)
            if gold_bounded and _GOLD_LABEL_RE.search(text):
                # The package's own printed last line -- everything after
                # it belongs to whatever comes next, not to this run.
                collecting = False
    return frozenset(extra)


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
    summary: BookSegmentSummary,
    force: bool,
    *,
    start_index: int | None,
    end_index: int | None,
    exclude_indices: frozenset[int] = frozenset(),
    extra_indices: frozenset[int] = frozenset(),
) -> None:
    # Lazy import: `owlsperch.queue.ladder` imports `Segment` from this
    # module at module scope, so importing `starting_tier` from it up top
    # here would be a circular import. By call time both modules are fully
    # loaded, so a local import is safe.
    from owlsperch.queue.ladder import starting_tier

    if start_index is not None and end_index is not None:
        # Part 1 (B10c-mand3): the span's own heading was matched --
        # anchor the TEXT to paragraph indices so a neighbouring class's
        # prose is never included (the supersede pass below stays
        # page-based on purpose; only this text/pages pair changes).
        # `exclude_indices` (judgement finding 1, B10c-mand6 follow-up)
        # drops specific paragraphs from THIS span's own range that a
        # later class's back-extension has already claimed as its own
        # opening flavor paragraph -- see `_run_class_pass`'s own comments.
        # `extra_indices` (B10c-mand18, `_class_own_table_indices`) adds
        # back THIS class's own table paragraph(s) stranded at or past the
        # end cap; a set union, so a paragraph already inside the range is
        # never duplicated, and sorting keeps printed order.
        kept_indices = sorted(
            (set(range(start_index, end_index)) - exclude_indices) | extra_indices
        )
        pages = _pages_spanned_indices(paragraphs, kept_indices)
        text = "\n\n".join(paragraphs[i].text for i in kept_indices)
    else:
        # Fallback: the heading was never matched anywhere in the span (a
        # column-reconstruction oddity, or a toc title that just doesn't
        # match the printed heading) -- never drop the class; use today's
        # whole-page-range behavior instead. The caller has already
        # printed a warning naming the book and heading.
        pages = _pages_in_range(paragraphs, span.start, span.end)
        text = "\n\n".join(p.text for p in paragraphs if span.start <= p.page <= span.end)
    if not pages or not text.strip():
        return

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
    out_dir: Path,
    book_id: str,
    class_seg_id: str,
    start: int,
    end: int,
    *,
    entity_title: str,
    data_dir: Path,
    owned: Callable[[str, str, str], bool] | None = None,
) -> tuple[set[str], int, set[str]]:
    """Stamp `superseded_by = class_seg_id` on every OTHER segment of
    `book_id` whose `pages` fall ENTIRELY inside `[start, end]` AND whose
    own `(heading, kind_hint)` is class-structural for `class_title`
    (batch B10c-mand11, `owlsperch.supersede.is_class_owned_fragment`) --
    an in-place stamp, so every other field (`outcome`, `attempts`,
    `tier`, ...) is preserved except `records`/`pending_records`, which are
    RELEASED (batch B10c-mand2, `owlsperch.supersede.
    release_segment_claims`): moved to `superseded/<book_id>/<type>/
    <file>.json` and cleared, so the class segment being stamped in for it
    is free to claim the same path (most often a level table sharing the
    class's own printed title, and so the same slug/id/path) without
    `owlsperch.queue.complete`'s ownership guard refusing it as a
    collision. A segment inside the span that is NOT class-structural (a
    printed sidebar, or the NEXT class's own heading fragment on a shared
    page) is left COMPLETELY alone -- not stamped, claims not released --
    so it stays canonical, and so the class span that does own it gets its
    own chance to stamp it. Idempotent: a segment that already carries a
    `superseded_by` (from this or an earlier class span) is left completely
    untouched -- its claims, if any, were already released the first time
    it was stamped (or need `owlsperch queue audit --fix`'s retroactive pass
    if it predates this behavior entirely). Returns `(seg_ids newly
    stamped, record claims released, seg_ids left live by the predicate)`."""
    # Lazy import: `owlsperch.supersede` imports from `owlsperch.queue.
    # common`, which imports `Segment` from this module at module scope --
    # a module-level import here would be circular. By call time this
    # module is fully loaded, so a local import is safe (same pattern as
    # `_write_class_segment`/`_write_one_segment`'s `starting_tier` import).
    from owlsperch.supersede import is_class_owned_fragment, release_segment_claims

    is_owned = owned if owned is not None else is_class_owned_fragment
    stamped: set[str] = set()
    released = 0
    left_live: set[str] = set()
    for path in sorted(out_dir.glob(f"{book_id}-*.json")):
        if path.stem == class_seg_id:
            continue
        segment = Segment.model_validate_json(path.read_text())
        if segment.seg_id == class_seg_id or segment.superseded_by is not None:
            continue
        if not segment.pages or not all(start <= p <= end for p in segment.pages):
            continue
        if not is_owned(segment.heading, segment.kind_hint, entity_title):
            left_live.add(segment.seg_id)
            continue
        segment.superseded_by = class_seg_id
        newly_released = release_segment_claims(segment, data_dir=data_dir)
        released += len(newly_released)
        atomic_write_text(path, segment.model_dump_json(indent=2) + "\n")
        stamped.add(segment.seg_id)
    return stamped, released, left_live


#: `segment --kinds` only accepts these two, since every other kind comes
#: from the single whole-book `build_segments` pass and can't be produced
#: selectively (B10c-mand6 criterion 6).
_SELECTABLE_KINDS: frozenset[str] = frozenset({"class", "prestige_class", MONSTER_KIND})


def _run_class_pass(
    entry: ManifestEntry,
    *,
    paragraphs: list[Paragraph],
    text_dir: Path,
    out_dir: Path,
    pages_json: dict[int, int],
    data_dir: Path,
    force: bool,
    summary: BookSegmentSummary,
    kinds: frozenset[str] | None,
    page_range: tuple[int, int] | None,
) -> None:
    """Batch B10c's toc-driven class/prestige_class pass (see this module's
    docstring, point 7), factored out (B10c-mand6) so it can run either as
    the tail of a normal `segment_book` call (`kinds=None`, right after the
    whole-book `build_segments` pass -- entirely additive, never touching
    what that pass produced) or ALONE, for `owlsperch segment --kinds
    class[,prestige_class]` (see `segment_book`'s own branch for what
    "alone" skips). `kinds`, when given, only narrows which spans get
    WRITTEN (and superseded/released) below -- `start_indices`/
    `resolved_starts` are still resolved from every discovered class span
    regardless, so a class segment's own end cap and back-extension bound
    (`_back_extend_start_index`) stay correct even when a neighbouring
    prestige_class span (say) isn't one of the requested `kinds`.

    Batch B10c-mand19: `page_range` (`--pages A-B`) narrows `spans_to_write`
    the same way and for the same reason -- a span is this run's business
    only when its OWN first page (`span.start`, the toc start page its
    `seg_id` encodes and the one `_remove_stale_segments` deletes by) is in
    range, so `owlsperch segment phb1 --force --pages 46-46` leaves the
    paladin's span (pages 43-47) completely alone rather than re-cutting it
    down to page 46. Since the supersede/release loop below iterates
    `spans_to_write` too, stamping only ever happens for spans this run
    actually wrote. Discovery and index resolution stay whole-book, so an
    in-range span's end cap is still the next class's real heading even when
    that next class is itself out of range.

    The supersede/release loop below is deliberately WIDER than
    `spans_to_write` (B10c-mand19 review follow-up): it runs for every span
    that has a segment file on disk at all, in range or not, so a fragment
    a `--force --pages` run just re-cut inside an out-of-range class span is
    stamped again in that same run rather than waiting for a later plain
    one -- which is also what keeps a `superseded_by` from ever naming a
    class segment that isn't there. Mutates `summary` in place; returns
    nothing."""
    toc = load_toc(data_dir, entry.book_id)
    if toc is None or not toc.entries:
        summary.class_note = "no toc -- no class/prestige_class segments"
        return

    # Computed here rather than passed in: this pass also runs standalone
    # (`--kinds class`), where no `build_segments` call has computed it.
    body_median = compute_body_median(paragraphs)

    last_page = max(_discover_text_pages(text_dir, None), default=None)
    if last_page is None:
        return

    class_spans = _discover_class_spans(
        toc, text_dir=text_dir, book_id=entry.book_id, last_page=last_page
    )

    # Part 1 (B10c-mand3): resolve every span's own start index FIRST, so
    # each span's end index can be capped at the next span's start (never
    # swallowing a neighbour's class), then write each span with its
    # resolved [start_index, end_index) text range -- or fall back to the
    # whole-page-range behavior (with a warning) when its own heading was
    # never matched anywhere in its page range.
    start_indices: dict[str, int | None] = {
        span.seg_id: _class_start_index(paragraphs, span) for span in class_spans
    }
    resolved_starts = sorted(i for i in start_indices.values() if i is not None)

    # Judgement finding 1 (B10c-mand6 follow-up): resolve every span's own
    # back-extended TEXT start and its own exclusive pre-heading "flavor"
    # paragraphs (Alignment:/Religion:/...) up front, from EVERY discovered
    # span regardless of `kinds` -- same rationale as `start_indices`/
    # `resolved_starts` above -- so that below, a class's own flavor
    # paragraph can be excluded from the PREVIOUS class's end-capped range
    # even when that previous span itself isn't one of the requested
    # `kinds`. A span's "flavor indices" are whichever of its own
    # back-extended pre-heading paragraphs look like the class-opening
    # flavor run-in (see `_is_class_flavor_paragraph`) -- NOT the whole
    # back-extended prefix, since that prefix can also hold unrelated
    # content (e.g. the PREVIOUS class's own Starting Package section) that
    # must stay available to whichever span the heading-anchored end cap
    # already puts it in.
    text_start_indices: dict[str, int] = {}
    flavor_indices_by_span: dict[str, frozenset[int]] = {}
    for span in class_spans:
        start_index = start_indices[span.seg_id]
        if start_index is None:
            continue
        text_start_index = _back_extend_start_index(paragraphs, start_index, resolved_starts)
        text_start_indices[span.seg_id] = text_start_index
        flavor_indices_by_span[span.seg_id] = frozenset(
            i
            for i in range(text_start_index, start_index)
            if _is_class_flavor_paragraph(paragraphs[i].text)
        )
    all_flavor_indices: frozenset[int] = frozenset().union(*flavor_indices_by_span.values())

    spans_to_write = [
        s
        for s in class_spans
        if (kinds is None or s.kind in kinds) and _in_write_range(s.start, page_range)
    ]

    for span in spans_to_write:
        start_index = start_indices[span.seg_id]
        if start_index is None:
            print(
                f"warning: {entry.book_id}: no heading match for class span "
                f"'{span.heading}' (toc page {span.start}) -- using whole-page fallback",
                file=sys.stderr,
            )
            _write_class_segment(
                span,
                paragraphs,
                entry,
                out_dir,
                pages_json,
                summary,
                force,
                start_index=None,
                end_index=None,
            )
            continue

        next_starts = [i for i in resolved_starts if i > start_index]
        page_cap_index = _first_index_after_page(paragraphs, span.end)
        end_index = min(min(next_starts), page_cap_index) if next_starts else page_cap_index

        # B10c-mand6: the TEXT start is back-extended to the top of the
        # heading's own page (see `_back_extend_start_index`'s docstring
        # for the PHB p0050 evidence) -- `start_indices`/`resolved_starts`
        # themselves stay the heading anchors, since the end cap above
        # must stay the NEXT class's heading index: capping at the next
        # class's own back-extended start would take its pre-heading tail
        # away from THIS class again, losing whatever it back-extended for
        # (e.g. a Starting Package section).
        text_start_index = text_start_indices[span.seg_id]

        # Judgement finding 1: exclude any OTHER span's own flavor
        # paragraph that the heading-anchored end cap above would
        # otherwise still sweep into this span's range -- e.g. the next
        # class's own Alignment:/Religion: paragraph, back-extended into
        # ITS segment above, must not also survive in THIS (previous)
        # span's text.
        own_flavor_indices = flavor_indices_by_span.get(span.seg_id, frozenset())
        exclude_indices = frozenset(
            i
            for i in all_flavor_indices
            if text_start_index <= i < end_index and i not in own_flavor_indices
        )

        # B10c-mand18: the end cap above can fall BETWEEN this class's own
        # printed level-table caption and its own grid (PHB p0040 prints
        # "Table 3-9: The Fighter", then "MONK", then the fighter's grid),
        # leaving the class with no table at all. Pull its own table
        # paragraph(s) back in -- see `_class_own_table_indices`.
        # B10c-mand21: the same end cap also strands this class's own
        # class-structural TAIL sections (PHB p0040 prints the rest of the
        # fighter's Dwarf Fighter Starting Package, and its whole Human
        # Fighter Starting Package, after "MONK"). Pull those back in too
        # -- see `_class_own_tail_indices`.
        extra_indices = _class_own_table_indices(
            paragraphs,
            heading=span.heading,
            text_start_index=text_start_index,
            end_index=end_index,
            page_cap_index=page_cap_index,
        ) | _class_own_tail_indices(
            paragraphs,
            heading=span.heading,
            text_start_index=text_start_index,
            end_index=end_index,
            page_cap_index=page_cap_index,
            body_median=body_median,
        )

        _write_class_segment(
            span,
            paragraphs,
            entry,
            out_dir,
            pages_json,
            summary,
            force,
            start_index=text_start_index,
            end_index=end_index,
            exclude_indices=exclude_indices,
            extra_indices=extra_indices,
        )

    superseded_total = 0
    released_total = 0
    # B10c-mand11: a segment one span leaves live (not class-structural for
    # THAT class) can still be stamped by a LATER span that does own it --
    # the two classes sharing a page case, e.g. PHB p0050's "ROGUE"
    # fragment sitting inside ranger's own one-page extension. So the
    # reported count is the set difference, resolved after every span has
    # had its turn, not a per-span sum.
    left_live_ids: set[str] = set()
    stamped_ids: set[str] = set()
    # B10c-mand19 (review follow-up): stamp for every span that HAS a
    # segment file on disk, not only the ones this run wrote. Otherwise a
    # `--force --pages` run that re-cuts a fragment inside an out-of-range
    # class span leaves it unstamped until some later plain run -- and a
    # fragment's `superseded_by` should never be able to lag behind (or,
    # worse, point at) a class segment file that isn't there. Stamping is
    # idempotent and touches no extraction bookkeeping, so widening it is
    # safe; the `kinds` filter still applies, since a kind the caller
    # didn't ask for is none of this run's business.
    spans_to_stamp = [
        s
        for s in class_spans
        if (kinds is None or s.kind in kinds)
        and (s in spans_to_write or (out_dir / f"{s.seg_id}.json").is_file())
    ]
    for span in spans_to_stamp:
        stamped, released, left_live = _supersede_segments_in_span(
            out_dir,
            entry.book_id,
            span.seg_id,
            span.start,
            span.end,
            entity_title=span.heading,
            data_dir=data_dir,
        )
        superseded_total += len(stamped)
        released_total += released
        left_live_ids |= left_live
        stamped_ids |= stamped
    # `+=`, not `=`: batch B12 runs a second, monster pass over the same
    # summary in a plain run, and either pass may stamp fragments.
    summary.superseded += superseded_total
    summary.released += released_total
    summary.left_live += len(left_live_ids - stamped_ids)


def _write_monster_segment(
    span: MonsterSpan,
    paragraphs: list[Paragraph],
    entry: ManifestEntry,
    out_dir: Path,
    pages_json: dict[int, int],
    summary: BookSegmentSummary,
    force: bool,
) -> None:
    """Write one `monster` segment file (batch B12). Its text is the
    paragraph range `owlsperch.segment.monsters` resolved -- unlike the class
    writer there is no whole-page-range fallback branch here, because that
    fallback is already folded into the span itself (`MonsterSpan.
    page_fallback`)."""
    # Lazy import: see `_write_class_segment`'s identical comment -- avoids a
    # `segment.runner` <-> `queue.ladder` circular import.
    from owlsperch.queue.ladder import starting_tier

    indices = list(range(span.start_index, span.end_index))
    pages = _pages_spanned_indices(paragraphs, indices)
    text = "\n\n".join(paragraphs[i].text for i in indices)
    if not pages or not text.strip():
        return

    summary.counts[MONSTER_KIND] = summary.counts.get(MONSTER_KIND, 0) + 1

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
        kind_hint=MONSTER_KIND,
        heading=span.heading,
        text=text,
        tier=starting_tier(MONSTER_KIND),
        created_at=_now_iso(),
    )
    atomic_write_text(seg_path, segment.model_dump_json(indent=2) + "\n")
    summary.written += 1


def _run_monster_pass(
    entry: ManifestEntry,
    *,
    paragraphs: list[Paragraph],
    text_dir: Path,
    out_dir: Path,
    pages_json: dict[int, int],
    data_dir: Path,
    force: bool,
    summary: BookSegmentSummary,
    page_range: tuple[int, int] | None,
) -> None:
    """Batch B12's toc-driven `monster` pass (see this module's docstring,
    point 12). Structurally identical to `_run_class_pass`: discovery and
    index resolution are always whole-book, `--pages` narrows only which
    spans are written (`_in_write_range` on the span's own first page, the
    one its `seg_id` encodes), and the supersede loop runs for every span
    with a segment file on disk -- in range or not -- so a fragment can
    never carry a `superseded_by` naming a segment file that isn't there.
    Mutates `summary` in place."""
    from owlsperch.supersede import is_monster_owned_fragment

    def owned_by(span: MonsterSpan) -> Callable[[str, str, str], bool]:
        """`is_monster_owned_fragment` widened to every name this span
        covers -- its printed heading AND every toc title folded into it, so
        a grouped entry's sub-block whose printed heading shares no words
        with the group ("TIEFLING" under "PLANETOUCHED") is still recognized
        as the group's own fragment."""
        names = (span.heading, *span.titles)

        def owned(heading: str, kind_hint: str, entity_title: str) -> bool:
            return any(is_monster_owned_fragment(heading, kind_hint, n) for n in names)

        return owned

    toc = load_toc(data_dir, entry.book_id)
    if toc is None or not toc.entries:
        summary.monster_note = "no toc -- no monster segments"
        return

    last_page = max(_discover_text_pages(text_dir, None), default=None)
    if last_page is None:
        return

    discovery = discover_monster_spans(
        toc,
        paragraphs=paragraphs,
        body_median=compute_body_median(paragraphs),
        page_text=lambda page: _page_text(text_dir, page),
        book_id=entry.book_id,
        last_page=last_page,
    )
    if not discovery.spans and not discovery.candidates:
        return

    spans_to_write = [s for s in discovery.spans if _in_write_range(s.page_start, page_range)]
    for span in spans_to_write:
        _write_monster_segment(span, paragraphs, entry, out_dir, pages_json, summary, force)

    left_live_ids: set[str] = set()
    stamped_ids: set[str] = set()
    for span in discovery.spans:
        if span not in spans_to_write and not (out_dir / f"{span.seg_id}.json").is_file():
            continue
        stamped, released, left_live = _supersede_segments_in_span(
            out_dir,
            entry.book_id,
            span.seg_id,
            span.page_start,
            span.page_end,
            entity_title=span.heading,
            data_dir=data_dir,
            owned=owned_by(span),
        )
        summary.released += released
        left_live_ids |= left_live
        stamped_ids |= stamped
    summary.superseded += len(stamped_ids)
    summary.left_live += len(left_live_ids - stamped_ids)

    note = (
        f"monster pass: {len(discovery.spans)} span(s) from {discovery.candidates} "
        f"toc entr(ies) ({discovery.grouped} grouped, {discovery.absorbed} absorbed, "
        f"{sum(1 for s in discovery.spans if s.page_fallback)} page-range fallback)"
    )
    skipped = len(discovery.without_stat_block)
    if skipped:
        note += f", {skipped} entr(ies) with no stat block in their own pages"
    summary.monster_note = note
    if discovery.unmatched:
        print(
            f"warning: {entry.book_id}: no heading match and no enclosing monster span for: "
            + ", ".join(discovery.unmatched),
            file=sys.stderr,
        )


def segment_book(
    entry: ManifestEntry,
    *,
    data_dir: Path,
    force: bool = False,
    page_range: tuple[int, int] | None = None,
    kinds: frozenset[str] | None = None,
) -> BookSegmentSummary:
    text_dir = data_dir / "text" / entry.book_id
    if not text_dir.is_dir():
        return BookSegmentSummary(
            entry.book_id, note="no text output -- run `owlsperch text` first"
        )

    # Batch B10c-mand19: ALWAYS the whole book's pages, never just
    # `--pages`' -- segmentation must see the same paragraph stream a plain
    # run sees, or every segment beginning before the range comes back
    # truncated. `--pages` restricts writes/deletes only (`_in_write_range`).
    page_indices = _discover_text_pages(text_dir, None)
    if not page_indices or not any(_in_write_range(i, page_range) for i in page_indices):
        return BookSegmentSummary(entry.book_id, note="no page text in range")

    paragraphs = _load_paragraphs(text_dir, page_indices, entry.book_id)
    if not paragraphs:
        return BookSegmentSummary(entry.book_id, note="no page text in range")

    out_dir = data_dir / "segments" / entry.book_id
    out_dir.mkdir(parents=True, exist_ok=True)
    if force and kinds is None:
        _remove_stale_segments(out_dir, entry.book_id, page_range)
    pages_json = _load_pages_json(text_dir)

    if kinds is not None:
        # B10c-mand6 criterion 6: `--kinds class[,prestige_class]` runs
        # ONLY the toc-driven class pass below -- no whole-book
        # `build_segments` pass and no stale-file removal (a class
        # segment's id derives from the toc start page and so never goes
        # stale the way a page-ordinal fragment id can under a plain
        # rerun). Writing over an existing class segment file (`--force`)
        # resets it to `pending` with no claims -- any records it
        # previously claimed are left orphaned on disk unless deleted
        # first (`queue reset --hard`) before re-segmenting.
        summary = BookSegmentSummary(entry.book_id)
        class_kinds = kinds - {MONSTER_KIND}
        if class_kinds:
            _run_class_pass(
                entry,
                paragraphs=paragraphs,
                text_dir=text_dir,
                out_dir=out_dir,
                pages_json=pages_json,
                data_dir=data_dir,
                force=force,
                summary=summary,
                kinds=class_kinds,
                page_range=page_range,
            )
        # Batch B12: `--kinds monster` re-runs only the monster pass, the
        # same way `--kinds class` re-runs only the class one; `--kinds
        # class,monster` runs both and nothing else.
        if MONSTER_KIND in kinds:
            _run_monster_pass(
                entry,
                paragraphs=paragraphs,
                text_dir=text_dir,
                out_dir=out_dir,
                pages_json=pages_json,
                data_dir=data_dir,
                force=force,
                summary=summary,
                page_range=page_range,
            )
        if class_kinds and not summary.class_note:
            kinds_str = ",".join(sorted(class_kinds))
            summary.class_note = f"--kinds {kinds_str}: only the toc-driven class pass ran"
        # B10c-mand19: the coverage safety net runs for a `--kinds` run
        # too -- it reads back the segment files on disk, so it no longer
        # needs this run's own `build_segments` output (which a `--kinds`
        # run never produces).
        _report_coverage(data_dir, entry.book_id, paragraphs, page_range, summary)
        return summary

    body_median = compute_body_median(paragraphs)
    entry_kind = _ENTRY_KIND_TO_ANCHOR_KIND.get(entry.kind)
    raw_segments = build_segments(paragraphs, body_median, entry_kind=entry_kind)

    summary = BookSegmentSummary(entry.book_id)
    first_page_counters: dict[int, int] = {}

    for raw in raw_segments:
        _write_one_segment(
            raw,
            paragraphs,
            entry,
            out_dir,
            pages_json,
            first_page_counters,
            summary,
            force,
            page_range,
        )

    # Batch B10c: a separate, toc-driven pass for class/prestige_class
    # segments (see this module's docstring, point 7) -- entirely additive,
    # never touching what build_segments produced above.
    _run_class_pass(
        entry,
        paragraphs=paragraphs,
        text_dir=text_dir,
        out_dir=out_dir,
        pages_json=pages_json,
        data_dir=data_dir,
        force=force,
        summary=summary,
        kinds=None,
        page_range=page_range,
    )

    # Batch B12: and a third, likewise additive toc-driven pass for monsters
    # (see this module's docstring, point 12).
    _run_monster_pass(
        entry,
        paragraphs=paragraphs,
        text_dir=text_dir,
        out_dir=out_dir,
        pages_json=pages_json,
        data_dir=data_dir,
        force=force,
        summary=summary,
        page_range=page_range,
    )

    # Batch B10c-mand19: coverage last, once BOTH passes have written --
    # a class segment can be the only thing covering a page, and the check
    # reads the segment files on disk rather than this run's own output, so
    # a page covered by a segment an earlier run wrote (and this `--pages`
    # run deliberately left alone) is correctly not reported as a gap.
    _report_coverage(data_dir, entry.book_id, paragraphs, page_range, summary)
    return summary


def _write_one_segment(
    raw: RawSegment,
    paragraphs: list[Paragraph],
    entry: ManifestEntry,
    out_dir: Path,
    pages_json: dict[int, int],
    first_page_counters: dict[int, int],
    summary: BookSegmentSummary,
    force: bool,
    page_range: tuple[int, int] | None,
) -> None:
    # Lazy import: see `_write_class_segment`'s identical comment -- avoids
    # a `segment.runner` <-> `queue.ladder` circular import.
    from owlsperch.queue.ladder import starting_tier

    text = "\n\n".join(p.text for p in paragraphs[raw.start : raw.end])
    if not text.strip():
        return

    pages = _pages_spanned(paragraphs, raw.start, raw.end)
    first_page = pages[0]
    # The per-first-page ordinal is advanced for EVERY segment the
    # whole-book stream produces, in stream order, including the ones
    # `--pages` then declines to write -- that's what keeps a seg_id
    # identical between a plain run and a `--pages` run (batch B10c-mand19).
    first_page_counters[first_page] = first_page_counters.get(first_page, 0) + 1
    seg_id = f"{entry.book_id}-p{first_page:04d}-{first_page_counters[first_page]:02d}"

    # Batch B10c-mand19: `--pages A-B` restricts which segments this run
    # touches, NOT the stream they were built from. A segment whose own
    # first page is outside the range is this run's business in no way at
    # all: not written, not skipped-counted, not deleted above.
    if not _in_write_range(first_page, page_range):
        return

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
    kinds: frozenset[str] | None = None,
    out: Any = None,
) -> int:
    out = out if out is not None else sys.stdout
    data_dir = data_dir if data_dir is not None else default_data_dir()
    manifest_path = manifest_path if manifest_path is not None else default_manifest_path()

    # B10c-mand6 criterion 6: validated up front, before any per-book work
    # (or even the manifest load below matters) -- other kinds come from
    # the single whole-book `build_segments` pass and can't be produced
    # selectively, so a bad `--kinds` value must cause no writes at all.
    if kinds is not None:
        invalid = sorted(kinds - _SELECTABLE_KINDS)
        if invalid:
            selectable = ", ".join(sorted(_SELECTABLE_KINDS))
            print(
                f"error: --kinds only supports {selectable} (every other kind "
                f"comes from the single whole-book segmentation pass and can't "
                f"be produced selectively), got: {', '.join(invalid)}",
                file=sys.stderr,
            )
            return 1

    entries = load_manifest(manifest_path)
    by_id = {e.book_id: e for e in entries}

    if book_id == "all":
        exit_code = 0
        for entry in entries:
            if status_for(entry, entries) not in ("in_scope", "override"):
                continue
            try:
                summary = segment_book(
                    entry, data_dir=data_dir, force=force, page_range=page_range, kinds=kinds
                )
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
            resolved_entry, data_dir=data_dir, force=force, page_range=page_range, kinds=kinds
        )
    except SegmentError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(summary.render(), file=out)
    return 0
