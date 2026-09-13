"""Unit tests for `owlsperch.toc.lookup` (batch B10b, acceptance criterion
5): `load_toc` and `entry_for_page`'s deepest-entry-wins boundary behavior,
nested levels, and a page before the first entry."""

from __future__ import annotations

import json
from pathlib import Path

from owlsperch.toc.lookup import entry_for_page, load_toc
from owlsperch.toc.parser import Toc, TocEntry


def _toc(entries: list[TocEntry]) -> Toc:
    return Toc(
        book_id="book",
        generated_at="2026-01-01T00:00:00+00:00",
        contents_pages=[1],
        entries=entries,
    )


def _entry(
    title: str,
    level: int,
    start: int | None,
    end: int | None,
    *,
    category: str = "combat",
    path: list[str] | None = None,
) -> TocEntry:
    return TocEntry(
        title=title,
        level=level,
        printed_page=start,
        pdf_page_start=start,
        pdf_page_end=end,
        path=path if path is not None else [title],
        category=category,
    )


# ---------------------------------------------------------------------------
# load_toc
# ---------------------------------------------------------------------------


def test_load_toc_returns_none_when_file_missing(tmp_path: Path) -> None:
    assert load_toc(tmp_path, "no-such-book") is None


def test_load_toc_round_trips_a_written_file(tmp_path: Path) -> None:
    toc_dir = tmp_path / "toc"
    toc_dir.mkdir()
    toc = _toc([_entry("Chapter 1: Combat", 1, 1, 10)])
    (toc_dir / "book.json").write_text(toc.model_dump_json())

    loaded = load_toc(tmp_path, "book")
    assert loaded is not None
    assert loaded.book_id == "book"
    assert len(loaded.entries) == 1
    assert loaded.entries[0].title == "Chapter 1: Combat"


# ---------------------------------------------------------------------------
# entry_for_page: boundaries, nested levels, page before the first entry
# ---------------------------------------------------------------------------


def test_entry_for_page_finds_the_containing_chapter() -> None:
    toc = _toc([_entry("Chapter 1: Combat", 1, 10, 20)])
    entry = entry_for_page(toc, 15)
    assert entry is not None
    assert entry.title == "Chapter 1: Combat"


def test_entry_for_page_inclusive_boundaries() -> None:
    toc = _toc([_entry("Chapter 1: Combat", 1, 10, 20)])
    assert entry_for_page(toc, 10) is not None
    assert entry_for_page(toc, 20) is not None
    assert entry_for_page(toc, 9) is None
    assert entry_for_page(toc, 21) is None


def test_entry_for_page_none_for_a_page_before_the_first_entry() -> None:
    toc = _toc([_entry("Chapter 1: Combat", 1, 10, 20)])
    assert entry_for_page(toc, 1) is None


def test_entry_for_page_prefers_the_deepest_nested_entry() -> None:
    chapter = _entry("Chapter 1: Combat", 1, 10, 20)
    section = _entry("Initiative", 2, 12, 15, path=["Chapter 1: Combat", "Initiative"])
    toc = _toc([chapter, section])

    # Inside the section's span: the section (level 2) wins over the
    # chapter (level 1) even though both contain the page.
    inside = entry_for_page(toc, 13)
    assert inside is not None
    assert inside.title == "Initiative"

    # Inside the chapter but outside the section: falls back to the chapter.
    outside_section = entry_for_page(toc, 18)
    assert outside_section is not None
    assert outside_section.title == "Chapter 1: Combat"


def test_entry_for_page_skips_entries_with_no_pdf_page_start() -> None:
    unresolved = _entry("Chapter 1: Combat", 1, None, None)
    toc = _toc([unresolved])
    assert entry_for_page(toc, 5) is None


def test_entry_for_page_defaults_end_to_start_when_pdf_page_end_is_none() -> None:
    entry = TocEntry(
        title="Orphan Section",
        level=2,
        printed_page=5,
        pdf_page_start=5,
        pdf_page_end=None,
        path=["Orphan Section"],
        category="uncategorized",
    )
    toc = _toc([entry])
    assert entry_for_page(toc, 5) is not None
    assert entry_for_page(toc, 6) is None


def test_load_toc_matches_json_dumps_shape(tmp_path: Path) -> None:
    """Sanity check against a hand-written JSON file (not just a
    round-trip through `model_dump_json`), matching design decision D7's
    documented shape."""
    toc_dir = tmp_path / "toc"
    toc_dir.mkdir()
    raw = {
        "book_id": "book",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "contents_pages": [4],
        "entries": [
            {
                "title": "Chapter 8: Combat",
                "level": 1,
                "printed_page": 133,
                "pdf_page_start": 134,
                "pdf_page_end": 200,
                "path": ["Chapter 8: Combat"],
                "category": "combat",
            }
        ],
    }
    (toc_dir / "book.json").write_text(json.dumps(raw))

    toc = load_toc(tmp_path, "book")
    assert toc is not None
    entry = entry_for_page(toc, 150)
    assert entry is not None
    assert entry.category == "combat"
