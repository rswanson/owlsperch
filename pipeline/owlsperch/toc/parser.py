"""Contents-page detection, dotted-leader entry parsing, chapter/section
nesting, and pdf page-span derivation for `owlsperch toc` (batch B10b,
design decisions D2-D6).

`parse_book_toc` is the single entry point: given a book's `text/<book_id>/`
directory and its `book_id` (for `toc.categories`' per-book overrides), it
returns a `ParsedToc` or raises `TocParseError` when no usable contents page
was found.

**D1 (`pages.json` direction -- easy to get backwards):**
`text/<book_id>/pages.json` maps PDF page (string key) -> printed page (int
value). This module inverts it: on a printed-page collision (two pdf pages
claiming the same printed page, e.g. a misdetected page number) the pdf page
whose own offset (`pdf - printed`) matches the book's MODAL offset wins; a
printed page missing from the inverse falls back to `printed + modal_offset`;
an empty/missing `pages.json` makes every `pdf_page_start` (and therefore
`pdf_page_end`) `None` for the whole book.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from owlsperch.toc.categories import resolve_chapter_category, resolve_section_category

#: A dotted-leader contents entry: "Title ..... 122". Consecutive matches
#: consume a line left to right, so "Armor ..... 122 Goods and Services /
#: ..... 126" (two entries column-repair glued onto one line) yields two
#: entries naturally (D3).
ENTRY_RE = re.compile(r"(?P<title>[^\n]*?)\s*\.{3,}\s*(?P<page>\d{1,4})")

#: The numbered-table index's own entries ("Table 1-1: Ability Modifiers
#: and Bonus Spells") must never become sections -- matched ANYWHERE in the
#: title, with the real text's EN DASH (not a hyphen), because column
#: repair glues the list's own header onto its first entry (D4).
_TABLE_INDEX_RE = re.compile(r"Table\s+\d+[–—-]\s?\d+")

#: A level-1 entry is a "Chapter N:" line or one of these top-level names
#: (checked after the same whitespace/dot normalization as every other
#: entry); "Appendix" is a startswith check since a book can have several
#: (D5).
_CHAPTER_RE = re.compile(r"^Chapter\s+\d+\s*:", re.IGNORECASE)
_TOP_LEVEL_NAMES = {
    "introduction",
    "prologue",
    "foreword",
    "glossary",
    "index",
    "character sheet",
    "character creation summary",
}

#: A contents page must have at least this many dotted-leader matches to be
#: considered one at all (D2).
_MIN_MATCHES_PER_PAGE = 3
#: Combined raw matches across every qualifying contents page must reach
#: this many, or the whole book is reported as failed instead of writing an
#: (almost) empty file (D2).
_MIN_TOTAL_ENTRIES = 5
#: Only the first this-many pdf pages are scanned for a contents page (D2).
_CONTENTS_SCAN_LIMIT = 12


class TocParseError(Exception):
    """A book's table of contents could not be found or didn't yield enough
    entries to be worth writing (`owlsperch toc` reports this instead of
    writing an empty/near-empty `toc/<book_id>.json`)."""


class TocEntry(BaseModel):
    """One `toc/<book_id>.json` entry (design decision D7): a chapter
    (`level` 1) or section (`level` >= 2), its printed/pdf page span, its
    title chain (`path`), and its resolved player-facing `category`.
    `printed_page`/`pdf_page_start`/`pdf_page_end` are `None` only when
    unresolvable (D18) -- `owlsperch.toc.lookup.entry_for_page` skips an
    entry with no `pdf_page_start`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    title: str
    level: int
    printed_page: int | None
    pdf_page_start: int | None
    pdf_page_end: int | None
    path: list[str]
    category: str


class Toc(BaseModel):
    """The whole `toc/<book_id>.json` file (D7)."""

    model_config = ConfigDict(extra="forbid")

    book_id: str
    generated_at: str
    contents_pages: list[int]
    entries: list[TocEntry]


@dataclass(frozen=True)
class ParsedToc:
    book_id: str
    contents_pages: list[int]
    entries: list[TocEntry]
    #: "Table N-M" index entries dropped by `_TABLE_INDEX_RE` (D4).
    dropped_table_entries: int
    #: Entries whose resolved `category` fell all the way through to
    #: `uncategorized` (criterion 2's "prints how many fell through").
    uncategorized_count: int


@dataclass(frozen=True)
class _RawEntry:
    title: str
    printed_page: int
    #: Parse order, for a stable sort when two entries share a page.
    order: int


@dataclass
class _BuiltEntry:
    """One entry mid-construction: category and `pdf_page_start` are known
    up front, `pdf_page_end` is filled in by the page-span pass once every
    entry's `pdf_page_start` (and sort order) is known."""

    raw: _RawEntry
    level: int
    path: list[str]
    category: str
    pdf_page_start: int | None
    pdf_page_end: int | None = None


def _normalize_title(raw: str) -> str:
    title = re.sub(r"\s+", " ", raw).strip()
    return title.strip(" .")


def find_contents_pages(text_dir: Path) -> list[int]:
    """Every pdf page in 1..`_CONTENTS_SCAN_LIMIT` whose `p{NNNN}.txt` has
    at least `_MIN_MATCHES_PER_PAGE` dotted-leader matches, in page order
    (D2)."""
    pages: list[int] = []
    for idx in range(1, _CONTENTS_SCAN_LIMIT + 1):
        path = text_dir / f"p{idx:04d}.txt"
        if not path.is_file():
            continue
        matches = ENTRY_RE.findall(path.read_text())
        if len(matches) >= _MIN_MATCHES_PER_PAGE:
            pages.append(idx)
    return pages


def _parse_raw_entries(text_dir: Path, contents_pages: list[int]) -> tuple[list[_RawEntry], int]:
    entries: list[_RawEntry] = []
    order = 0
    raw_count = 0
    for idx in contents_pages:
        text = (text_dir / f"p{idx:04d}.txt").read_text()
        for match in ENTRY_RE.finditer(text):
            raw_count += 1
            title = _normalize_title(match.group("title"))
            if not title:
                continue
            page = int(match.group("page"))
            entries.append(_RawEntry(title=title, printed_page=page, order=order))
            order += 1
    return entries, raw_count


def _level(title: str) -> int:
    if _CHAPTER_RE.match(title):
        return 1
    if title.casefold() in _TOP_LEVEL_NAMES:
        return 1
    if title.lower().startswith("appendix"):
        return 1
    return 2


def _load_pages_json(text_dir: Path) -> dict[int, int]:
    path = text_dir / "pages.json"
    if not path.is_file():
        return {}
    raw: dict[str, int] = json.loads(path.read_text())
    return {int(k): v for k, v in raw.items()}


def _build_printed_to_pdf(pages_json: dict[int, int]) -> tuple[dict[int, int], int]:
    """Inverts `pages_json` (pdf -> printed) into printed -> pdf, per this
    module's docstring's D1 recap. Returns `({}, 0)` for an empty map."""
    if not pages_json:
        return {}, 0

    offsets = Counter(pdf - printed for pdf, printed in pages_json.items())
    modal_offset = offsets.most_common(1)[0][0]

    inverse: dict[int, int] = {}
    inverse_offset: dict[int, int] = {}
    for pdf, printed in pages_json.items():
        offset = pdf - printed
        if printed not in inverse or inverse_offset[printed] != modal_offset:
            inverse[printed] = pdf
            inverse_offset[printed] = offset
    return inverse, modal_offset


def _max_text_page(text_dir: Path) -> int | None:
    indices = [int(p.stem[1:]) for p in text_dir.glob("p*.txt") if p.stem[1:].isdigit()]
    return max(indices) if indices else None


def parse_book_toc(text_dir: Path, *, book_id: str) -> ParsedToc:
    contents_pages = find_contents_pages(text_dir)
    raw_entries, raw_count = _parse_raw_entries(text_dir, contents_pages)
    if raw_count < _MIN_TOTAL_ENTRIES:
        raise TocParseError(
            f"could not find a usable table of contents for '{book_id}' "
            f"(scanned pdf pages 1-{_CONTENTS_SCAN_LIMIT} of its text/ dir, "
            f"found {raw_count} dotted-leader entries across "
            f"{len(contents_pages)} candidate page(s), need >= {_MIN_TOTAL_ENTRIES})"
        )

    dropped_table_entries = 0
    filtered: list[_RawEntry] = []
    for entry in raw_entries:
        if _TABLE_INDEX_RE.search(entry.title):
            dropped_table_entries += 1
            continue
        filtered.append(entry)

    pages_json = _load_pages_json(text_dir)
    printed_to_pdf, modal_offset = _build_printed_to_pdf(pages_json)
    max_pdf_page = _max_text_page(text_dir)

    def to_pdf(printed_page: int) -> int | None:
        if not pages_json:
            return None
        if printed_page in printed_to_pdf:
            return printed_to_pdf[printed_page]
        return printed_page + modal_offset

    # D5: nesting comes from the chapter prefix / top-level-name rule, NOT
    # from the jumbled reading order column repair emits the contents page
    # in -- level-1 entries are sorted by printed page, and each level-2
    # entry is assigned to the LAST chapter whose printed page is <= its
    # own.
    levels = {id(e): _level(e.title) for e in filtered}
    chapters = sorted((e for e in filtered if levels[id(e)] == 1), key=lambda e: e.printed_page)

    def chapter_for(entry: _RawEntry) -> _RawEntry | None:
        found: _RawEntry | None = None
        for chapter in chapters:
            if chapter.printed_page <= entry.printed_page:
                found = chapter
            else:
                break
        return found

    # Resolve every chapter's category first -- sections inherit it (D9).
    chapter_category_by_title: dict[str, str] = {
        chapter.title: resolve_chapter_category(book_id, chapter.title) for chapter in chapters
    }

    built: list[_BuiltEntry] = []
    for entry in filtered:
        level = levels[id(entry)]
        if level == 1:
            path = [entry.title]
            category = chapter_category_by_title[entry.title]
        else:
            chapter = chapter_for(entry)
            chapter_title = chapter.title if chapter is not None else None
            path = [chapter_title, entry.title] if chapter_title is not None else [entry.title]
            chapter_category = (
                chapter_category_by_title.get(chapter_title) if chapter_title is not None else None
            )
            category = resolve_section_category(book_id, entry.title, chapter_category)

        built.append(
            _BuiltEntry(
                raw=entry,
                level=level,
                path=path,
                category=category,
                pdf_page_start=to_pdf(entry.printed_page),
            )
        )

    # D6: sort by (pdf_page_start, level) -- entries with no resolvable pdf
    # page (whole-book pages.json missing/empty) fall back to printed-page
    # order so the file still has a deterministic, sensible entry order.
    def sort_key(item: _BuiltEntry) -> tuple[int, int]:
        start = item.pdf_page_start if item.pdf_page_start is not None else item.raw.printed_page
        return (start, item.level)

    ordered = sorted(built, key=sort_key)

    for i, item in enumerate(ordered):
        start = item.pdf_page_start
        if start is None:
            item.pdf_page_end = None
            continue
        end: int | None = None
        for later in ordered[i + 1 :]:
            if later.pdf_page_start is None:
                continue
            if later.level <= item.level:
                end = later.pdf_page_start - 1
                break
        if end is None:
            end = max_pdf_page if max_pdf_page is not None else start
        item.pdf_page_end = max(end, start)

    entries: list[TocEntry] = []
    uncategorized_count = 0
    for item in ordered:
        if item.category == "uncategorized":
            uncategorized_count += 1
        entries.append(
            TocEntry(
                title=item.raw.title,
                level=item.level,
                printed_page=item.raw.printed_page,
                pdf_page_start=item.pdf_page_start,
                pdf_page_end=item.pdf_page_end,
                path=item.path,
                category=item.category,
            )
        )

    return ParsedToc(
        book_id=book_id,
        contents_pages=contents_pages,
        entries=entries,
        dropped_table_entries=dropped_table_entries,
        uncategorized_count=uncategorized_count,
    )
