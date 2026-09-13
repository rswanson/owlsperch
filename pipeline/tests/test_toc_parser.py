"""Unit tests for `owlsperch.toc.parser` (batch B10b, acceptance criterion 5
and design decisions D1-D6), on synthetic contents-page text -- no real book
text is committed here, only invented titles/page numbers (public repo)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from owlsperch.toc.parser import (
    ENTRY_RE,
    TocParseError,
    find_contents_pages,
    parse_book_toc,
)


def _write_page(text_dir: Path, idx: int, text: str) -> None:
    text_dir.mkdir(parents=True, exist_ok=True)
    (text_dir / f"p{idx:04d}.txt").write_text(text)


def _write_pages_json(text_dir: Path, mapping: dict[int, int]) -> None:
    (text_dir / "pages.json").write_text(json.dumps({str(k): v for k, v in mapping.items()}))


# ---------------------------------------------------------------------------
# find_contents_pages (D2)
# ---------------------------------------------------------------------------


def test_find_contents_pages_requires_at_least_three_matches(tmp_path: Path) -> None:
    text_dir = tmp_path
    _write_page(text_dir, 1, "Foo .......... 1\nBar .......... 2\n")  # only 2
    _write_page(text_dir, 2, "Foo .......... 1\nBar .......... 2\nBaz .......... 3\n")  # 3
    assert find_contents_pages(text_dir) == [2]


def test_find_contents_pages_only_scans_first_twelve_pdf_pages(tmp_path: Path) -> None:
    text_dir = tmp_path
    entries = "\n".join(f"Entry {i} .......... {i}" for i in range(1, 6))
    _write_page(text_dir, 13, entries)
    assert find_contents_pages(text_dir) == []


def test_find_contents_pages_can_return_more_than_one_page(tmp_path: Path) -> None:
    text_dir = tmp_path
    entries = "\n".join(f"Entry {i} .......... {i}" for i in range(1, 4))
    _write_page(text_dir, 4, entries)
    _write_page(text_dir, 5, entries)
    assert find_contents_pages(text_dir) == [4, 5]


# ---------------------------------------------------------------------------
# Entry regex shape (D3)
# ---------------------------------------------------------------------------


def test_entry_re_parses_two_entries_glued_onto_one_line() -> None:
    text = "Armor ..... 122 Goods and Services ..... 126"
    matches = list(ENTRY_RE.finditer(text))
    assert len(matches) == 2
    assert matches[0].group("title").strip(" .") == "Armor"
    assert matches[0].group("page") == "122"
    assert matches[1].group("title").strip() == "Goods and Services"
    assert matches[1].group("page") == "126"


def test_entry_re_does_not_match_a_leader_with_no_trailing_number() -> None:
    # A dangling leader run (column repair dropped the page number) simply
    # doesn't match -- no entry is invented for it (D3).
    text = "Dangling Entry ........."
    assert list(ENTRY_RE.finditer(text)) == []


# ---------------------------------------------------------------------------
# parse_book_toc: contents-page-not-found / too-few-entries (criterion 1)
# ---------------------------------------------------------------------------


def test_parse_book_toc_raises_when_no_contents_page_found(tmp_path: Path) -> None:
    _write_page(tmp_path, 1, "Just some prose, no dotted leaders here.\n")
    with pytest.raises(TocParseError):
        parse_book_toc(tmp_path, book_id="testbook")


def test_parse_book_toc_raises_when_fewer_than_five_entries(tmp_path: Path) -> None:
    # 4 entries on one page qualifies as A contents page (>= 3 matches) but
    # the BOOK is still reported as failed: fewer than 5 entries overall.
    entries = "\n".join(f"Entry {i} .......... {i}" for i in range(1, 5))
    _write_page(tmp_path, 1, entries)
    with pytest.raises(TocParseError):
        parse_book_toc(tmp_path, book_id="testbook")


def test_parse_book_toc_raises_when_only_table_index_entries_survive_filtering(
    tmp_path: Path,
) -> None:
    """A contents-like page whose only dotted-leader lines are numbered-
    table index entries clears the raw >= 5 threshold but must still fail:
    the guard applies to the FILTERED (post-`_TABLE_INDEX_RE`) count, or
    the book silently gets an all-empty `entries: []` toc (the bug this
    test guards against)."""
    entries = "\n".join(
        f"Table 1–{i}: Some Index Row .......... {10 + i}" for i in range(1, 8)
    )
    _write_page(tmp_path, 1, entries)
    with pytest.raises(TocParseError):
        parse_book_toc(tmp_path, book_id="testbook")


# ---------------------------------------------------------------------------
# parse_book_toc: levels, chapter nesting, table-index dropping (D4, D5)
# ---------------------------------------------------------------------------


_CONTENTS_TEXT = "\n".join(
    [
        "Contents",
        "Introduction .......................... 3",
        "List of Numbered Tables Table 1–1: Ability Modifiers .......... 4",
        "Chapter 1: Abilities .......... 5",
        "Ability Scores .......... 5 Ability Modifiers .......... 6",
        "Chapter 2: Skills .......... 10",
        "Skill Checks .......... 10",
        "Skill Descriptions .......... 12",
        "A Section With No Chapter Before It .......... 1",
    ]
)


def _build_book(tmp_path: Path, *, pages_json: dict[int, int] | None = None) -> Path:
    text_dir = tmp_path / "text" / "testbook"
    _write_page(text_dir, 1, _CONTENTS_TEXT)
    if pages_json is not None:
        _write_pages_json(text_dir, pages_json)
    return text_dir


def test_parse_book_toc_assigns_levels_and_chapter_nesting(tmp_path: Path) -> None:
    text_dir = _build_book(tmp_path)
    parsed = parse_book_toc(text_dir, book_id="testbook")

    by_title = {e.title: e for e in parsed.entries}

    assert by_title["Introduction"].level == 1
    assert by_title["Chapter 1: Abilities"].level == 1
    assert by_title["Chapter 2: Skills"].level == 1

    assert by_title["Ability Scores"].level == 2
    assert by_title["Ability Scores"].path == ["Chapter 1: Abilities", "Ability Scores"]
    assert by_title["Ability Modifiers"].path == ["Chapter 1: Abilities", "Ability Modifiers"]

    assert by_title["Skill Checks"].path == ["Chapter 2: Skills", "Skill Checks"]
    assert by_title["Skill Descriptions"].path == ["Chapter 2: Skills", "Skill Descriptions"]

    # Printed page 1 precedes every chapter (Introduction is printed page 3,
    # the first chapter is printed page 5) -- no chapter claims it.
    orphan = by_title["A Section With No Chapter Before It"]
    assert orphan.level == 2
    assert orphan.path == ["A Section With No Chapter Before It"]


def test_parse_book_toc_drops_numbered_table_index_entries(tmp_path: Path) -> None:
    text_dir = _build_book(tmp_path)
    parsed = parse_book_toc(text_dir, book_id="testbook")

    titles = {e.title for e in parsed.entries}
    assert not any("Table 1" in t for t in titles)
    assert parsed.dropped_table_entries == 1


def test_parse_book_toc_ignores_reading_order_uses_printed_page_for_nesting(
    tmp_path: Path,
) -> None:
    # Chapter 2 appears in the text BEFORE Chapter 1 (simulating column
    # repair's jumbled block order, D5) -- nesting must still follow printed
    # page order, not textual order.
    text = "\n".join(
        [
            "Chapter 2: Skills .......... 10",
            "Skill Checks .......... 10",
            "Chapter 1: Abilities .......... 5",
            "Ability Scores .......... 5",
            "Filler Entry One .......... 20",
            "Filler Entry Two .......... 21",
        ]
    )
    _write_page(tmp_path, 1, text)
    parsed = parse_book_toc(tmp_path, book_id="testbook")
    by_title = {e.title: e for e in parsed.entries}

    assert by_title["Ability Scores"].path == ["Chapter 1: Abilities", "Ability Scores"]
    assert by_title["Skill Checks"].path == ["Chapter 2: Skills", "Skill Checks"]


# ---------------------------------------------------------------------------
# pages.json direction / fallback (D1, D3's "missing printed page")
# ---------------------------------------------------------------------------


def test_parse_book_toc_converts_printed_to_pdf_pages_via_inverted_pages_json(
    tmp_path: Path,
) -> None:
    # pages.json is pdf -> printed; every pdf page here is printed + 1 (a
    # front-matter offset), i.e. modal offset (pdf - printed) is +1.
    pages_json = {i: i - 1 for i in range(1, 30)}
    text_dir = _build_book(tmp_path, pages_json=pages_json)
    parsed = parse_book_toc(text_dir, book_id="testbook")

    by_title = {e.title: e for e in parsed.entries}
    # Introduction is printed page 3 -> pdf page 4 (printed + 1, the inverse
    # of pdf -> printed - 1).
    assert by_title["Introduction"].pdf_page_start == 4
    assert by_title["Chapter 1: Abilities"].pdf_page_start == 6


def test_parse_book_toc_falls_back_to_modal_offset_for_an_unmapped_printed_page(
    tmp_path: Path,
) -> None:
    # Every pdf page maps to printed - 1 EXCEPT printed page 6 is missing
    # entirely from pages.json (D3's "entry whose printed page has no
    # pages.json mapping").
    pages_json = {i: i - 1 for i in range(1, 30) if i - 1 != 6}
    text_dir = _build_book(tmp_path, pages_json=pages_json)
    parsed = parse_book_toc(text_dir, book_id="testbook")

    by_title = {e.title: e for e in parsed.entries}
    # Falls back to printed_page + modal_offset (modal offset here is +1,
    # i.e. pdf = printed + 1).
    assert by_title["Ability Modifiers"].pdf_page_start == 7


def test_parse_book_toc_pdf_page_start_is_none_when_pages_json_missing(tmp_path: Path) -> None:
    text_dir = _build_book(tmp_path)  # no pages.json written at all
    parsed = parse_book_toc(text_dir, book_id="testbook")
    assert all(e.pdf_page_start is None for e in parsed.entries)
    assert all(e.pdf_page_end is None for e in parsed.entries)


def test_parse_book_toc_collision_prefers_the_pdf_page_matching_modal_offset(
    tmp_path: Path,
) -> None:
    # Two pdf pages both map to printed page 5: pdf 6 (offset +1, matches
    # the book's modal offset) and pdf 50 (a misdetected page number, an
    # outlier offset). The inverse must prefer pdf 6.
    pages_json = {i: i - 1 for i in range(1, 30)}
    pages_json[50] = 5
    text_dir = _build_book(tmp_path, pages_json=pages_json)
    parsed = parse_book_toc(text_dir, book_id="testbook")
    by_title = {e.title: e for e in parsed.entries}
    assert by_title["Chapter 1: Abilities"].pdf_page_start == 6


# ---------------------------------------------------------------------------
# Page spans (D6)
# ---------------------------------------------------------------------------


def test_parse_book_toc_page_end_derived_from_next_entry_same_or_shallower_level(
    tmp_path: Path,
) -> None:
    pages_json = {i: i for i in range(1, 30)}  # identity map -> modal offset 0
    text_dir = _build_book(tmp_path, pages_json=pages_json)
    for i in range(1, 25):
        (text_dir / f"p{i:04d}.txt").write_text("filler text\n")
    (text_dir / "p0001.txt").write_text(_CONTENTS_TEXT)

    parsed = parse_book_toc(text_dir, book_id="testbook")
    by_title = {e.title: e for e in parsed.entries}

    chapter1 = by_title["Chapter 1: Abilities"]
    # Chapter 1 starts at pdf 5, Chapter 2 (same level) starts at pdf 10 ->
    # Chapter 1 ends at pdf 9.
    assert chapter1.pdf_page_start == 5
    assert chapter1.pdf_page_end == 9

    ability_scores = by_title["Ability Scores"]
    # "Ability Scores" (pdf 5) ends right before "Ability Modifiers" (pdf 6).
    assert ability_scores.pdf_page_start == 5
    assert ability_scores.pdf_page_end == 5

    skill_descriptions = by_title["Skill Descriptions"]
    # The last entry (highest pdf_page_start) ends at the book's last text
    # page.
    assert skill_descriptions.pdf_page_start == 12
    assert skill_descriptions.pdf_page_end == 24


# ---------------------------------------------------------------------------
# Category resolution flows through parsing (D9, exercised at the integration
# level; the rule table itself is unit-tested in test_toc_categories.py)
# ---------------------------------------------------------------------------


def test_parse_book_toc_resolves_categories_generic_and_inherited(tmp_path: Path) -> None:
    text_dir = _build_book(tmp_path)
    parsed = parse_book_toc(text_dir, book_id="testbook")
    by_title = {e.title: e for e in parsed.entries}

    assert by_title["Chapter 1: Abilities"].category == "character-creation"
    assert by_title["Chapter 2: Skills"].category == "skills"
    assert by_title["Skill Checks"].category == "skills"
    assert by_title["Introduction"].category == "basics"

    # "A Section With No Chapter Before It" matches no generic pattern and
    # has no preceding chapter to inherit from -- the only entry that falls
    # all the way through to uncategorized in this fixture.
    orphan = by_title["A Section With No Chapter Before It"]
    assert orphan.category == "uncategorized"
    assert parsed.uncategorized_count == 1
