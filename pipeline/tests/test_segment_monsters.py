"""Unit tests for `owlsperch.segment.monsters` (batch B12): the pure,
toc-driven monster span discovery -- title matching, grouping, anchoring,
absorbing, the end cut and its stat-block fallback.
"""

from __future__ import annotations

from typing import Any

from owlsperch.segment.headings import Paragraph, compute_body_median
from owlsperch.segment.monsters import (
    MonsterSpan,
    discover_monster_spans,
    group_name,
    title_matches,
    title_variants,
)
from owlsperch.toc.parser import Toc, TocEntry

# ---------------------------------------------------------------------------
# Title matching
# ---------------------------------------------------------------------------


def test_title_matches_plain_and_plural() -> None:
    assert title_matches("ALLIP", "Allip")
    assert title_matches("WIZARDS", "Wizard")
    assert not title_matches("ANGEL", "Allip")


def test_title_matches_comma_permutation_in_both_directions() -> None:
    """The index and the printed heading routinely disagree on word order."""
    assert title_matches("ANGEL, ASTRAL DEVA", "Astral deva (angel)")
    assert title_matches("DIRE APE", "Ape, dire")
    assert title_matches("DEVIL, BARBED", "Barbed devil (hamatula)")
    assert title_matches("DEVIL, BARBED (HAMATULA)", "Barbed devil")


def test_title_variants_and_group_name() -> None:
    assert "astraldeva" in title_variants("Astral deva (angel)")
    assert "astraldevaangel" in title_variants("Astral deva (angel)")
    assert "astraldevaangel" in title_variants("ANGEL, ASTRAL DEVA")
    assert group_name("Solar (angel)") == "angel"
    assert group_name("Allip") is None


# ---------------------------------------------------------------------------
# Discovery fixtures
# ---------------------------------------------------------------------------

_STAT_BLOCK = (
    "Medium Undead (Incorporeal) Hit Dice: 4d12 (26 hp) Initiative: +5 "
    "Speed: Fly 30 ft. Armor Class: 15 Saves: Fort +1, Ref +4, Will +4"
)


def _para(text: str, page: int, *, height: float = 10.0, lines: int = 1) -> Paragraph:
    return Paragraph(
        page=page,
        text=text,
        kind="prose",
        median_word_height=height,
        max_word_height=height,
        line_count=lines,
    )


def _toc(entries: list[dict[str, Any]]) -> Toc:
    return Toc(
        book_id="mmtest",
        generated_at="2026-01-01T00:00:00+00:00",
        contents_pages=[1],
        entries=[TocEntry(**e) for e in entries],
    )


def _entry(title: str, page: int, *, end: int | None = None, level: int = 2) -> dict[str, Any]:
    return {
        "title": title,
        "level": level,
        "printed_page": page,
        "pdf_page_start": page,
        "pdf_page_end": end if end is not None else page,
        "path": ["Chapter 1: Monsters A to Z", title],
        "category": "monsters",
    }


def _three_entry_book() -> tuple[Toc, list[Paragraph]]:
    """A synthetic MM-shaped book: page 1 has two unrelated monsters plus a
    printed sidebar, page 2-3 are a GROUPED entry (its group heading, then
    two sub-blocks the index lists with a "(angel)" parenthetical)."""
    paragraphs = [
        _para("ALLIP", 1, height=18.0),
        _para(_STAT_BLOCK, 1, lines=6),
        _para("COMBAT", 1, height=12.0),
        _para("An allip is unable to cause physical harm.", 1, lines=4),
        _para("FAMILIARS", 1, height=12.0),
        _para("A printed sidebar that belongs to no monster at all.", 1, lines=4),
        _para("ANKHEG", 1, height=18.0),
        _para(_STAT_BLOCK, 1, lines=6),
        _para("ANGEL", 2, height=18.0),
        _para("Angels are a race of celestials.", 2, lines=4),
        _para("ANGEL, ASTRAL DEVA", 2, height=12.0),
        _para(_STAT_BLOCK, 2, lines=6),
        _para("ANGEL, PLANETAR", 3, height=12.0),
        _para(_STAT_BLOCK, 3, lines=6),
        _para("ZOMBIE", 4, height=18.0),
        _para(_STAT_BLOCK, 4, lines=6),
    ]
    toc = _toc(
        [
            _entry("Chapter 1: Monsters A to Z", 1, end=4, level=1),
            _entry("Allip", 1),
            _entry("Ankheg", 1),
            _entry("Angel", 2),
            _entry("Astral deva (angel)", 2),
            _entry("Planetar (angel)", 3),
            _entry("Zombie", 4),
        ]
    )
    return toc, paragraphs


def _discover(toc: Toc, paragraphs: list[Paragraph]) -> Any:
    page_text: dict[int, str] = {}
    for p in paragraphs:
        page_text[p.page] = page_text.get(p.page, "") + p.text + "\n\n"
    return discover_monster_spans(
        toc,
        paragraphs=paragraphs,
        body_median=compute_body_median(paragraphs),
        page_text=lambda page: page_text.get(page, ""),
        book_id="mmtest",
        last_page=max(p.page for p in paragraphs),
    )


def _span(spans: list[MonsterSpan], heading: str) -> MonsterSpan:
    matching = [s for s in spans if s.heading == heading]
    assert len(matching) == 1, f"{heading}: {[s.heading for s in spans]}"
    return matching[0]


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_one_span_per_entry_with_a_grouped_entry_collapsed() -> None:
    toc, paragraphs = _three_entry_book()
    found = _discover(toc, paragraphs)

    assert [s.heading for s in found.spans] == ["ALLIP", "ANKHEG", "ANGEL", "ZOMBIE"]
    # The two "(angel)" sub-entries fold into the group's one segment.
    assert found.grouped == 2
    angel = _span(found.spans, "ANGEL")
    assert "Astral deva (angel)" in angel.titles
    assert "Planetar (angel)" in angel.titles


def test_seg_ids_are_page_plus_per_page_ordinal() -> None:
    toc, paragraphs = _three_entry_book()
    found = _discover(toc, paragraphs)

    assert _span(found.spans, "ALLIP").seg_id == "mmtest-monster-p0001-01"
    assert _span(found.spans, "ANKHEG").seg_id == "mmtest-monster-p0001-02"
    assert _span(found.spans, "ANGEL").seg_id == "mmtest-monster-p0002-01"


def test_span_text_starts_at_its_own_heading_and_stops_at_the_next() -> None:
    toc, paragraphs = _three_entry_book()
    found = _discover(toc, paragraphs)

    allip = _span(found.spans, "ALLIP")
    text = "\n\n".join(p.text for p in paragraphs[allip.start_index : allip.end_index])
    assert text.startswith("ALLIP")
    assert "An allip is unable" in text
    assert "ANKHEG" not in text
    # The grouped entry keeps every sub-block.
    angel = _span(found.spans, "ANGEL")
    angel_text = "\n\n".join(p.text for p in paragraphs[angel.start_index : angel.end_index])
    assert "ANGEL, ASTRAL DEVA" in angel_text
    assert "ANGEL, PLANETAR" in angel_text
    assert "ZOMBIE" not in angel_text


def test_entry_without_a_stat_block_marker_is_not_a_candidate() -> None:
    toc, paragraphs = _three_entry_book()
    paragraphs.append(_para("APPENDIX NOTE", 5, height=18.0))
    paragraphs.append(_para("Prose with no stat block at all.", 5, lines=4))
    toc = _toc([*[e.model_dump() for e in toc.entries], _entry("Appendix note", 5)])

    found = _discover(toc, paragraphs)

    assert "Appendix note" in found.without_stat_block
    assert "APPENDIX NOTE" not in [s.heading for s in found.spans]


def test_unmatched_entry_is_absorbed_by_the_preceding_span() -> None:
    """A sub-block the book prints in body-size title case (no heading of its
    own) folds into the span that already covers its pages."""
    toc, paragraphs = _three_entry_book()
    entries = [e.model_dump() for e in toc.entries]
    entries.append(_entry("Elder zombie", 4))
    found = _discover(_toc(entries), paragraphs)

    assert found.absorbed == 1
    assert found.unmatched == []
    assert "Elder zombie" in _span(found.spans, "ZOMBIE").titles


def test_span_with_no_stat_block_in_its_cut_falls_back_to_the_page_range() -> None:
    """Poppler routinely emits a monster's heading away from its body; the
    heading-to-heading cut then has no stat block in it, so the span falls
    back to its whole page range (complete, if overlapping)."""
    paragraphs = [
        _para("OOZE", 1, height=18.0),
        _para("BLACK PUDDING", 1, height=18.0),
        _para("An ooze is a mindless creature. " + _STAT_BLOCK, 1, lines=6),
        _para("A black pudding is a hazard. " + _STAT_BLOCK, 1, lines=6),
    ]
    toc = _toc(
        [
            _entry("Chapter 1: Monsters A to Z", 1, level=1),
            _entry("Ooze", 1),
            _entry("Black pudding", 1),
        ]
    )
    found = _discover(toc, paragraphs)

    ooze = _span(found.spans, "OOZE")
    assert ooze.page_fallback is True
    text = "\n\n".join(p.text for p in paragraphs[ooze.start_index : ooze.end_index])
    assert "Hit Dice" in text
