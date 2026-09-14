"""End-to-end tests for `owlsperch.segment.runner` (and `owlsperch segment`
via `run_segment`), against synthetic `p{NNNN}.txt` + `p{NNNN}.meta.json`
fixtures written straight into a temp `$OWLSPERCH_DATA`-shaped directory --
no book text and no `pdftotext` involved (per the batch's fixture
convention).

Covers acceptance criteria 1 (segment file fields), 2 (each anchor kind), 3
(heading extension is exercised implicitly through every other test, since
there is no other source of headings), 4 (page-spanning segment), 5
(never-empty segments, front-matter coverage), 6 (idempotency/--force,
`--pages`, `all`), and 7 (missing-meta error, stable seg_ids across
reruns).
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

from owlsperch.manifest import ManifestEntry
from owlsperch.segment.runner import SegmentError, run_segment, segment_book

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _para(
    text: str,
    *,
    kind: str = "prose",
    height: float = 10.0,
    max_height: float | None = None,
    line_count: int | None = None,
) -> dict[str, Any]:
    return {
        "text": text,
        "kind": kind,
        "median_word_height": height,
        "max_word_height": max_height if max_height is not None else height,
        "line_count": line_count if line_count is not None else 1,
    }


def _write_book(
    data_dir: Path, book_id: str, pages: dict[int, list[dict[str, Any]]], *, meta: bool = True
) -> Path:
    text_dir = data_dir / "text" / book_id
    text_dir.mkdir(parents=True, exist_ok=True)
    for idx, paragraphs in pages.items():
        content = "\n\n".join(p["text"] for p in paragraphs)
        (text_dir / f"p{idx:04d}.txt").write_text(content + "\n" if content else "")
        if meta:
            meta_list = [
                {
                    "kind": p["kind"],
                    "median_word_height": p["median_word_height"],
                    "max_word_height": p["max_word_height"],
                    "line_count": p["line_count"],
                }
                for p in paragraphs
            ]
            (text_dir / f"p{idx:04d}.meta.json").write_text(json.dumps(meta_list))
    return text_dir


def _entry(book_id: str) -> ManifestEntry:
    return ManifestEntry(
        book_id=book_id,
        title="Test Book",
        file=f"{book_id}.pdf",
        edition="3.5",
        kind="rulebook",
    )


def _segment_files(data_dir: Path, book_id: str) -> list[dict[str, Any]]:
    seg_dir = data_dir / "segments" / book_id
    return [json.loads(p.read_text()) for p in sorted(seg_dir.glob("*.json"))]


def _by_kind(segments: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [s for s in segments if s["kind_hint"] == kind]


# ---------------------------------------------------------------------------
# Acceptance criterion 1: segment file fields
# ---------------------------------------------------------------------------


def test_segment_file_has_all_required_fields(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_book(
        data_dir,
        "book",
        {1: [_para("Front matter body text goes on for a good while here.", line_count=3)]},
    )

    segment_book(_entry("book"), data_dir=data_dir)

    segments = _segment_files(data_dir, "book")
    assert len(segments) == 1
    seg = segments[0]
    assert seg["seg_id"] == "book-p0001-01"
    assert seg["book_id"] == "book"
    assert seg["pages"] == [1]
    assert seg["printed_pages"] == [None]
    assert seg["kind_hint"] == "rules_section"
    assert seg["heading"] == ""
    assert "Front matter" in seg["text"]
    assert seg["status"] == "pending"
    assert seg["tier"] == "haiku"
    assert seg["attempts"] == []
    assert "created_at" in seg and seg["created_at"]


def test_printed_pages_pulled_from_pages_json(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    text_dir = _write_book(
        data_dir, "book", {5: [_para("Some body text that runs on a while.", line_count=3)]}
    )
    (text_dir / "pages.json").write_text(json.dumps({"5": 42}))

    segment_book(_entry("book"), data_dir=data_dir)

    segments = _segment_files(data_dir, "book")
    assert segments[0]["printed_pages"] == [42]


def test_printed_pages_stays_aligned_with_pages_when_some_are_missing(tmp_path: Path) -> None:
    # A page-spanning segment where only some of its pages have a detected
    # printed number: printed_pages must stay the same length and order as
    # pages, with None (not a dropped entry) for the page with no number.
    data_dir = tmp_path / "data"
    text_dir = _write_book(
        data_dir,
        "book",
        {
            10: [
                _para("Fireball"),
                _para("Evocation [Fire]"),
                _para("Level: Sor/Wiz 3. Explanation begins here on page ten.", line_count=3),
            ],
            11: [
                _para("The explanation continues describing area and damage here.", line_count=3),
            ],
        },
    )
    (text_dir / "pages.json").write_text(json.dumps({"10": 200}))

    segment_book(_entry("book"), data_dir=data_dir)

    segments = _segment_files(data_dir, "book")
    spells = _by_kind(segments, "spell")
    assert len(spells) == 1
    assert spells[0]["pages"] == [10, 11]
    assert spells[0]["printed_pages"] == [200, None]


# ---------------------------------------------------------------------------
# Acceptance criterion 2: each anchor kind
# ---------------------------------------------------------------------------


def test_spell_anchor_segment(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_book(
        data_dir,
        "book",
        {
            1: [
                _para("Intro text before anything else in the book begins here.", line_count=3),
                _para("Fireball"),
                _para("Evocation [Fire]"),
                _para("Level: Sorcerer/Wizard 3. Deals fire damage in a burst.", line_count=3),
                _para("COMBAT"),
                _para("Combat rules text continues on here for a while.", line_count=3),
            ]
        },
    )

    segment_book(_entry("book"), data_dir=data_dir)

    segments = _segment_files(data_dir, "book")
    spells = _by_kind(segments, "spell")
    assert len(spells) == 1
    assert spells[0]["heading"] == "Fireball"
    assert "Evocation" in spells[0]["text"]
    assert "COMBAT" not in spells[0]["text"]
    assert spells[0]["pages"] == [1]


def test_stat_block_anchor_segment(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_book(
        data_dir,
        "book",
        {
            1: [
                _para("Owlbear"),
                _para("A bearlike creature with the head of an owl.", line_count=3),
                _para("Size/Type: Large Magical Beast"),
                _para("Hit Dice: 5d10+20 (52 hp)"),
                _para("COMBAT"),
            ]
        },
    )

    segment_book(_entry("book"), data_dir=data_dir)

    segments = _segment_files(data_dir, "book")
    stat_blocks = _by_kind(segments, "stat_block")
    assert len(stat_blocks) == 1
    assert stat_blocks[0]["heading"] == "Owlbear"
    assert "Hit Dice" in stat_blocks[0]["text"]
    assert "bearlike creature" in stat_blocks[0]["text"]


def test_feat_anchor_segment(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_book(
        data_dir,
        "book",
        {
            1: [
                _para("Power Attack [General]"),
                _para("Prerequisite: Str 13."),
                _para("Benefit: Subtract a number from attack rolls.", line_count=3),
                _para("COMBAT MANEUVERS"),
            ]
        },
    )

    segment_book(_entry("book"), data_dir=data_dir)

    segments = _segment_files(data_dir, "book")
    feats = _by_kind(segments, "feat")
    assert len(feats) == 1
    assert feats[0]["heading"] == "Power Attack [General]"
    assert "Benefit" in feats[0]["text"]
    assert "COMBAT MANEUVERS" not in feats[0]["text"]


def test_table_anchor_segment_includes_rows_and_footnote(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_book(
        data_dir,
        "book",
        {
            1: [
                _para("Table 3-1: Simple Weapons"),
                _para("Dagger\t2 gp\t1d4\nClub\t-\t1d6", kind="table", line_count=2),
                _para("1 This weapon deals bludgeoning damage.", line_count=1),
                _para("COMBAT"),
            ]
        },
    )

    segment_book(_entry("book"), data_dir=data_dir)

    segments = _segment_files(data_dir, "book")
    tables = _by_kind(segments, "table")
    assert len(tables) == 1
    assert tables[0]["heading"] == "Table 3-1: Simple Weapons"
    assert "Dagger" in tables[0]["text"]
    assert "bludgeoning" in tables[0]["text"]
    assert "COMBAT" not in tables[0]["text"]


def test_heading_split_produces_multiple_rules_sections(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_book(
        data_dir,
        "book",
        {
            1: [
                _para("Intro text before any heading shows up in this book.", line_count=3),
                _para("CHAPTER ONE"),
                _para("First section body text goes on for a while here.", line_count=3),
                _para("CHAPTER TWO"),
                _para("Second section body text goes on for a while here too.", line_count=3),
            ]
        },
    )

    segment_book(_entry("book"), data_dir=data_dir)

    segments = _segment_files(data_dir, "book")
    rules_sections = _by_kind(segments, "rules_section")
    assert [s["heading"] for s in rules_sections] == ["", "CHAPTER ONE", "CHAPTER TWO"]
    assert "First section" in rules_sections[1]["text"]
    assert "Second section" in rules_sections[2]["text"]


# ---------------------------------------------------------------------------
# Acceptance criterion 4: page-spanning segment
# ---------------------------------------------------------------------------


def test_segment_spans_a_page_boundary(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_book(
        data_dir,
        "book",
        {
            10: [
                _para("Fireball"),
                _para("Evocation [Fire]"),
                _para(
                    "Level: Sorcerer/Wizard 3. Explanation begins here on page ten.", line_count=3
                ),
            ],
            11: [
                _para("The explanation continues describing area and damage here.", line_count=3),
                _para("COMBAT"),
            ],
        },
    )

    segment_book(_entry("book"), data_dir=data_dir)

    segments = _segment_files(data_dir, "book")
    spells = _by_kind(segments, "spell")
    assert len(spells) == 1
    assert spells[0]["pages"] == [10, 11]
    assert "Level: Sorcerer" in spells[0]["text"]
    assert "explanation continues" in spells[0]["text"]
    assert spells[0]["seg_id"] == "book-p0010-01"


# ---------------------------------------------------------------------------
# Acceptance criterion 5: never-empty segments, total front-matter coverage
# ---------------------------------------------------------------------------


def test_every_page_with_text_is_covered_including_front_matter(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data_dir = tmp_path / "data"
    _write_book(
        data_dir,
        "book",
        {
            1: [_para("Front matter page one text goes on for a while.", line_count=3)],
            2: [_para("Front matter page two text goes on for a while too.", line_count=3)],
            3: [],  # a genuinely blank page: no text at all
        },
    )

    segment_book(_entry("book"), data_dir=data_dir)

    segments = _segment_files(data_dir, "book")
    covered = {page for seg in segments for page in seg["pages"]}
    assert covered == {1, 2}
    assert "warning" not in capsys.readouterr().err


def test_whitespace_only_span_produces_no_segment(tmp_path: Path) -> None:
    # Two headings back to back with nothing between them: no zero-length or
    # whitespace-only rules_section should be written.
    data_dir = tmp_path / "data"
    _write_book(
        data_dir,
        "book",
        {1: [_para("CHAPTER ONE"), _para("CHAPTER TWO"), _para("Body text.", line_count=3)]},
    )

    segment_book(_entry("book"), data_dir=data_dir)

    for seg in _segment_files(data_dir, "book"):
        assert seg["text"].strip()


# ---------------------------------------------------------------------------
# Acceptance criterion 6: idempotency/--force, --pages, `all`
# ---------------------------------------------------------------------------


def test_existing_segment_files_are_kept_unless_force(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_book(
        data_dir,
        "book",
        {1: [_para("Some body text here that runs on for a while.", line_count=3)]},
    )
    entry = _entry("book")

    first = segment_book(entry, data_dir=data_dir)
    assert first.written == 1

    seg_path = data_dir / "segments" / "book" / "book-p0001-01.json"
    original = seg_path.read_text()
    seg_path.write_text(original.replace('"pending"', '"SENTINEL"'))

    second = segment_book(entry, data_dir=data_dir)
    assert second.written == 0
    assert second.skipped == 1
    assert "SENTINEL" in seg_path.read_text()

    third = segment_book(entry, data_dir=data_dir, force=True)
    assert third.written == 1
    assert "SENTINEL" not in seg_path.read_text()


def test_force_removes_stale_segment_files_in_processed_range(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_book(
        data_dir,
        "book",
        {1: [_para("Page one body text runs on for a while here.", line_count=3)]},
    )
    entry = _entry("book")

    seg_dir = data_dir / "segments" / "book"
    seg_dir.mkdir(parents=True)
    orphan = seg_dir / "book-p0005-01.json"
    orphan.write_text("{}")

    # Without --force, an orphan whose page is no longer produced is kept.
    segment_book(entry, data_dir=data_dir, page_range=(1, 10))
    assert orphan.exists()

    # With --force, it is removed before writing since page 5 falls inside
    # the processed range (1-10).
    segment_book(entry, data_dir=data_dir, page_range=(1, 10), force=True)
    assert not orphan.exists()


def test_pages_option_limits_range(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_book(
        data_dir,
        "book",
        {
            1: [_para("Page one body text runs on for a while here.", line_count=3)],
            2: [_para("Page two body text runs on for a while here too.", line_count=3)],
        },
    )

    summary = segment_book(_entry("book"), data_dir=data_dir, page_range=(1, 1))

    segments = _segment_files(data_dir, "book")
    assert all(seg["pages"] == [1] for seg in segments)
    assert summary.written == len(segments)


def test_run_segment_all_skips_book_with_no_text_dir(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text(
        """
entries:
  - book_id: has-text
    title: "Has Text"
    file: "has-text.pdf"
    edition: "3.5"
    kind: rulebook
  - book_id: no-text
    title: "No Text"
    file: "no-text.pdf"
    edition: "3.5"
    kind: rulebook
"""
    )
    _write_book(
        data_dir,
        "has-text",
        {1: [_para("Some body text here that runs on for a while.", line_count=3)]},
    )

    out = io.StringIO()
    exit_code = run_segment("all", data_dir=data_dir, manifest_path=manifest_path, out=out)

    output = out.getvalue()
    assert exit_code == 0
    assert "has-text:" in output
    assert "no-text:" in output and "no text output" in output


def test_run_segment_unknown_book_id_is_a_clear_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data_dir = tmp_path / "data"
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text(
        """
entries:
  - book_id: book
    title: "Book"
    file: "book.pdf"
    edition: "3.5"
    kind: rulebook
"""
    )

    exit_code = run_segment("does-not-exist", data_dir=data_dir, manifest_path=manifest_path)

    assert exit_code == 1
    assert "does-not-exist" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Acceptance criterion 7: missing-meta error, stable seg_ids across reruns
# ---------------------------------------------------------------------------


def test_missing_meta_json_is_a_clear_error(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_book(
        data_dir,
        "book",
        {1: [_para("Some body text here that runs on for a while.", line_count=3)]},
        meta=False,
    )

    with pytest.raises(SegmentError) as exc_info:
        segment_book(_entry("book"), data_dir=data_dir)

    message = str(exc_info.value)
    assert "meta.json" in message
    assert "owlsperch text book --force" in message


def test_run_segment_reports_missing_meta_error_with_exit_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data_dir = tmp_path / "data"
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text(
        """
entries:
  - book_id: book
    title: "Book"
    file: "book.pdf"
    edition: "3.5"
    kind: rulebook
"""
    )
    _write_book(
        data_dir,
        "book",
        {1: [_para("Some body text here that runs on for a while.", line_count=3)]},
        meta=False,
    )

    exit_code = run_segment("book", data_dir=data_dir, manifest_path=manifest_path)

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "owlsperch text book --force" in captured.err


def test_seg_ids_stable_across_reruns(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_book(
        data_dir,
        "book",
        {
            1: [
                _para("Intro text before anything else in the book begins here.", line_count=3),
                _para("Fireball"),
                _para("Evocation [Fire]"),
                _para("Level: Sorcerer/Wizard 3. Deals fire damage in a burst.", line_count=3),
                _para("COMBAT"),
                _para("Combat rules text continues on here for a while.", line_count=3),
            ]
        },
    )
    entry = _entry("book")

    segment_book(entry, data_dir=data_dir)
    first_ids = sorted(seg["seg_id"] for seg in _segment_files(data_dir, "book"))

    segment_book(entry, data_dir=data_dir, force=True)
    second_ids = sorted(seg["seg_id"] for seg in _segment_files(data_dir, "book"))

    assert first_ids == second_ids
    assert first_ids == ["book-p0001-01", "book-p0001-02", "book-p0001-03"]


# ---------------------------------------------------------------------------
# The real corpus (acceptance criterion 7's corpus marker)
# ---------------------------------------------------------------------------


@pytest.mark.corpus
def test_phb1_real_corpus_spell_segments() -> None:
    """Segments a real page range of the Player's Handbook (pdf pages
    200-239, the spell-description chapter, chosen to contain Fireball) and
    asserts spell anchors are found there.

    Batch B3's column-grouping fix (prose-like blocks are excluded from
    table-group detection, and a table group now requires every member to
    mutually -- not just transitively -- overlap every other member) turns
    this book's three-column spell-description pages from mostly bogus
    tab-joined "table" paragraphs back into clean prose, so spell anchors
    are found at the rate a clean scan should find them.
    """
    import shutil
    import tempfile

    from owlsperch.manifest import default_manifest_path, default_pdf_dir, load_manifest
    from owlsperch.text.runner import run_text

    pdf_dir = default_pdf_dir()
    if not pdf_dir.is_dir():
        pytest.skip(f"real PDF corpus not present at {pdf_dir}")
    if shutil.which("pdftotext") is None:
        pytest.skip("pdftotext (poppler) not installed")

    entries = load_manifest(default_manifest_path())
    book_id = "phb1" if any(e.book_id == "phb1" for e in entries) else "phb"
    page_range = (200, 239)

    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        text_exit = run_text(
            book_id, pdf_dir=pdf_dir, data_dir=data_dir, page_range=page_range, force=True
        )
        assert text_exit == 0

        segment_exit = run_segment(book_id, data_dir=data_dir, page_range=page_range, force=True)
        assert segment_exit == 0

        segments = _segment_files(data_dir, book_id)
        spells = _by_kind(segments, "spell")
        assert len(spells) >= 30
        assert "Fireball" in {s["heading"] for s in spells}

        # Total real-page coverage still holds (acceptance criterion 5).
        covered = {page for seg in segments for page in seg["pages"]}
        assert covered == set(range(page_range[0], page_range[1] + 1))


# ---------------------------------------------------------------------------
# Batch B10c: toc-driven class/prestige_class segments (design decisions
# D1-D3).
# ---------------------------------------------------------------------------


def _write_toc(data_dir: Path, book_id: str, entries: list[dict[str, Any]]) -> None:
    toc_dir = data_dir / "toc"
    toc_dir.mkdir(parents=True, exist_ok=True)
    toc = {
        "book_id": book_id,
        "generated_at": "2026-01-01T00:00:00+00:00",
        "contents_pages": [0],
        "entries": entries,
    }
    (toc_dir / f"{book_id}.json").write_text(json.dumps(toc))


def _classes_toc_entries() -> list[dict[str, Any]]:
    return [
        {
            "title": "Chapter 3: Classes",
            "level": 1,
            "printed_page": 1,
            "pdf_page_start": 1,
            "pdf_page_end": 4,
            "path": ["Chapter 3: Classes"],
            "category": "classes",
        },
        {
            # No "Hit Die: dN" marker anywhere on its own page -- must NOT
            # become a class segment (design decision D1).
            "title": "The Classes",
            "level": 2,
            "printed_page": 1,
            "pdf_page_start": 1,
            "pdf_page_end": 1,
            "path": ["Chapter 3: Classes", "The Classes"],
            "category": "classes",
        },
        {
            "title": "Barbarian",
            "level": 2,
            "printed_page": 2,
            "pdf_page_start": 2,
            "pdf_page_end": 2,
            "path": ["Chapter 3: Classes", "Barbarian"],
            "category": "classes",
        },
        {
            "title": "Bard",
            "level": 2,
            "printed_page": 3,
            "pdf_page_start": 3,
            "pdf_page_end": 4,
            "path": ["Chapter 3: Classes", "Bard"],
            "category": "classes",
        },
    ]


def _write_classes_book(data_dir: Path, book_id: str = "book") -> None:
    # Part 1 (B10c-mand3): page 2 begins with a chapter-intro paragraph
    # ABOVE the "BARBARIAN" heading, and page 3 begins with a barbarian
    # tail paragraph BEFORE the "BARD" heading -- the two-classes-sharing-
    # a-page case: a heading-anchored class span must exclude prose that
    # precedes its own heading on its own start page (the chapter intro),
    # while still picking up its own trailing prose on a page it shares
    # with the NEXT class (the barbarian tail), because that prose comes
    # before the next class's own heading.
    _write_book(
        data_dir,
        book_id,
        {
            1: [_para("Front matter opening text for the whole chapter goes here.", line_count=3)],
            2: [
                _para(
                    "Read the chapter introduction before you choose a class for your "
                    "character here.",
                    line_count=3,
                ),
                _para("BARBARIAN"),
                _para(
                    "Hit Die: d12. A barbarian is a fierce warrior, savage and strong in "
                    "battle here.",
                    line_count=3,
                ),
            ],
            3: [
                _para(
                    "Barbarians continue raging fiercely across every battlefield they "
                    "enter for a while.",
                    line_count=3,
                ),
                _para("BARD"),
                _para(
                    "Hit Die: d6. Bards are trained in music and magic together for adventuring.",
                    line_count=3,
                ),
            ],
            4: [
                _para(
                    "Bards continue channeling their magic across the land for a while "
                    "longer here.",
                    line_count=3,
                ),
            ],
        },
    )
    _write_toc(data_dir, book_id, _classes_toc_entries())


def test_class_segments_are_discovered_from_toc_and_hit_die_marker(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_classes_book(data_dir)

    segment_book(_entry("book"), data_dir=data_dir)

    segments = _segment_files(data_dir, "book")
    class_segments = _by_kind(segments, "class")
    # Exactly Barbarian and Bard -- "The Classes" (no Hit Die marker) must
    # not become a class segment even though it's under the same
    # toc-resolved "classes" category (design decision D1).
    assert {s["heading"] for s in class_segments} == {"Barbarian", "Bard"}

    by_heading = {s["heading"]: s for s in class_segments}
    barbarian = by_heading["Barbarian"]
    bard = by_heading["Bard"]

    assert barbarian["seg_id"] == "book-class-p0002"
    assert barbarian["tier"] == "sonnet"  # starting_tier("class")
    # Part 1 (B10c-mand3): the span's TEXT is anchored to the "BARBARIAN"
    # heading paragraph, not the whole toc page range -- it still spans
    # pages [2, 3] because the barbarian's own trailing prose on page 3
    # (before "BARD" begins) legitimately belongs to it.
    assert barbarian["pages"] == [2, 3]
    # B10c-mand6: the span's TEXT START is now back-extended to the top of
    # its own heading's page (page 2) -- page 2's own chapter-intro
    # paragraph ("Read the chapter introduction...") precedes "BARBARIAN"
    # and there is no still-earlier class heading to bound the extension
    # against, so it gets pulled in too. A chapter intro bleeding into the
    # first class's segment is the accepted cost of never losing a class's
    # own pre-heading flavor tail (see PHB p0050 in the module docstring
    # and `_back_extend_start_index`'s own docstring) -- what matters is
    # that the heading itself is present and nothing PAST it is lost.
    assert "Read the chapter introduction" in barbarian["text"]
    assert "BARBARIAN" in barbarian["text"]
    assert barbarian["text"].index("BARBARIAN") > 0
    assert "Barbarians continue raging" in barbarian["text"]
    assert "BARD" not in barbarian["text"]
    assert "trained in music and magic" not in barbarian["text"]

    assert bard["seg_id"] == "book-class-p0003"
    assert bard["tier"] == "sonnet"
    # Bard's own pdf_page_end (4) is already the book's last page, so the
    # +1 extension is capped there, not pushed past the end of the book.
    assert bard["pages"] == [3, 4]
    # B10c-mand6: bard's own back-extended start reaches to the top of
    # page 3 (bounded only by barbarian's own heading, which is on page 2,
    # well before page 3 starts) -- so bard's segment now ALSO contains
    # page 3's barbarian tail. This overlap with barbarian's own segment
    # (which already contained that same tail via its unchanged,
    # heading-anchored end cap) is deliberate, not a regression: see the
    # module docstring's point 9 and `_back_extend_start_index`'s own
    # docstring for why there is no single cut point that avoids it.
    assert "Barbarians continue raging" in bard["text"]
    assert "BARD" in bard["text"]
    assert bard["text"].index("BARD") > 0
    assert "Read the chapter introduction" not in bard["text"]


def test_class_span_start_tolerates_a_trailing_plural_heading(tmp_path: Path) -> None:
    """PHB 3.5 prints "WIZARDS" for the toc's "Wizard" -- `_heading_matches_
    title` must tolerate a trailing plural "s" on either side (Part 1)."""
    data_dir = tmp_path / "data"
    _write_book(
        data_dir,
        "book",
        {
            1: [_para("Front matter opening text for the whole chapter goes here.", line_count=3)],
            2: [
                _para("WIZARDS"),
                _para(
                    "Hit Die: d4. A wizard learns arcane magic through diligent study here.",
                    line_count=3,
                ),
            ],
        },
    )
    _write_toc(
        data_dir,
        "book",
        [
            {
                "title": "Wizard",
                "level": 2,
                "printed_page": 2,
                "pdf_page_start": 2,
                "pdf_page_end": 2,
                "path": ["Chapter 3: Classes", "Wizard"],
                "category": "classes",
            },
        ],
    )

    segment_book(_entry("book"), data_dir=data_dir)

    segments = _segment_files(data_dir, "book")
    class_segments = _by_kind(segments, "class")
    assert len(class_segments) == 1
    wizard = class_segments[0]
    assert wizard["heading"] == "Wizard"
    assert wizard["text"].startswith("WIZARDS")


def test_class_span_falls_back_to_whole_page_when_heading_never_matches(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A class is never dropped: when no paragraph in the span matches the
    toc title at all (a column-reconstruction oddity, or a toc title that
    just doesn't match the printed heading), the class segment falls back
    to today's whole-page-range text and a warning is printed -- but the
    segment is still written (Part 1)."""
    data_dir = tmp_path / "data"
    _write_book(
        data_dir,
        "book",
        {
            1: [_para("Front matter opening text for the whole chapter goes here.", line_count=3)],
            2: [
                _para("THE ARCANE SPELLCASTER"),  # doesn't match "Wizard" at all
                _para(
                    "Hit Die: d4. A wizard learns arcane magic through diligent study here.",
                    line_count=3,
                ),
            ],
        },
    )
    _write_toc(
        data_dir,
        "book",
        [
            {
                "title": "Wizard",
                "level": 2,
                "printed_page": 2,
                "pdf_page_start": 2,
                "pdf_page_end": 2,
                "path": ["Chapter 3: Classes", "Wizard"],
                "category": "classes",
            },
        ],
    )

    segment_book(_entry("book"), data_dir=data_dir)

    segments = _segment_files(data_dir, "book")
    class_segments = _by_kind(segments, "class")
    assert len(class_segments) == 1  # never dropped
    wizard = class_segments[0]
    assert wizard["heading"] == "Wizard"
    assert "THE ARCANE SPELLCASTER" in wizard["text"]  # whole-page fallback

    captured = capsys.readouterr()
    assert "warning" in captured.err
    assert "book" in captured.err
    assert "Wizard" in captured.err


def test_class_span_back_extends_to_recover_a_pre_heading_flavor_tail(tmp_path: Path) -> None:
    """B10c-mand6 criterion 2 (judgement finding 5): a class's OWN
    flavor run-in sections (Alignment/Religion/...) can be printed on the
    heading's own page but BEFORE the heading itself -- exactly PHB
    p0050's paragraph order for rogue: [0] rogue's own flavor sections,
    [1]-[2] ranger's Starting Package, [3] "ROGUE" heading. Modeled here
    with three classes: Barbarian (page 2), Bard (page 3, whose own
    Alignment/Religion paragraph is printed before "BARD"), Cleric
    (page 4)."""
    data_dir = tmp_path / "data"
    _write_book(
        data_dir,
        "book",
        {
            1: [_para("Front matter opening text for the whole chapter goes here.", line_count=3)],
            2: [
                _para("BARBARIAN"),
                _para(
                    "Hit Die: d12. A barbarian is a fierce warrior, savage and strong in "
                    "battle here.",
                    line_count=3,
                ),
            ],
            3: [
                _para(
                    "Barbarians continue raging fiercely across every battlefield they "
                    "enter for a while.",
                    line_count=3,
                ),
                _para(
                    "Alignment: Any nonlawful. Religion: Bards revere whichever deity "
                    "best matches their own personal wanderlust here.",
                    line_count=3,
                ),
                _para("BARD"),
                _para(
                    "Hit Die: d6. Bards are trained in music and magic together for adventuring.",
                    line_count=3,
                ),
            ],
            4: [
                _para(
                    "Bards continue channeling their magic across the land for a while "
                    "longer here.",
                    line_count=3,
                ),
                _para("CLERIC"),
                _para(
                    "Hit Die: d8. A cleric is a master of divine magic and skilled with "
                    "weapons here.",
                    line_count=3,
                ),
            ],
        },
    )
    _write_toc(
        data_dir,
        "book",
        [
            {
                "title": "Barbarian",
                "level": 2,
                "printed_page": 2,
                "pdf_page_start": 2,
                "pdf_page_end": 2,
                "path": ["Chapter 3: Classes", "Barbarian"],
                "category": "classes",
            },
            {
                "title": "Bard",
                "level": 2,
                "printed_page": 3,
                "pdf_page_start": 3,
                "pdf_page_end": 3,
                "path": ["Chapter 3: Classes", "Bard"],
                "category": "classes",
            },
            {
                "title": "Cleric",
                "level": 2,
                "printed_page": 4,
                "pdf_page_start": 4,
                "pdf_page_end": 4,
                "path": ["Chapter 3: Classes", "Cleric"],
                "category": "classes",
            },
        ],
    )

    segment_book(_entry("book"), data_dir=data_dir)

    segments = {s["heading"]: s for s in _by_kind(_segment_files(data_dir, "book"), "class")}
    barbarian, bard, cleric = segments["Barbarian"], segments["Bard"], segments["Cleric"]

    # Bard's own pre-heading flavor paragraph is now recovered into its
    # own segment ...
    assert "Alignment: Any nonlawful" in bard["text"]
    assert "Bards revere whichever deity" in bard["text"]
    # ... and it still excludes the THIRD class's own heading/content.
    assert "CLERIC" not in bard["text"]
    assert "master of divine magic" not in bard["text"]

    # Barbarian's own (heading-anchored, UNCHANGED) end cap still reaches
    # up to Bard's own heading, so it still contains barbarian's own
    # trailing prose from that shared page-3 tail ...
    assert "Barbarians continue raging" in barbarian["text"]
    # ... but (judgement finding 1, B10c-mand6 follow-up) NOT bard's own
    # flavor paragraph any more -- that paragraph carries BOTH an
    # "Alignment:" and a "Religion:" marker (see
    # `_is_class_flavor_paragraph`), which is what makes it recognizable
    # as bard's own opening flavor content and excludes it from
    # barbarian's end-capped range, even though its raw paragraph index
    # still falls inside that range.
    assert "Alignment: Any nonlawful" not in barbarian["text"]
    assert "Bards revere whichever deity" not in barbarian["text"]
    assert "BARD" not in barbarian["text"]

    # Cleric is unaffected -- nothing precedes "CLERIC" on page 4 that
    # belongs to a different class in this fixture.
    assert cleric["text"].startswith("Bards continue channeling")
    assert "CLERIC" in cleric["text"]


def test_back_extend_start_index_bounds_at_previous_class_heading(tmp_path: Path) -> None:
    """B10c-mand6 criterion 3: two class headings on the same page. The
    back-extension for the SECOND heading is bounded at the first
    heading's own index (never reaching further back, e.g. into a chapter
    intro two classes earlier) -- exercised directly against
    `_back_extend_start_index` since two real class headings printed on
    the very same page never happens in the real corpus (spacing is
    always at least a page), but the bound's correctness still matters."""
    from owlsperch.segment.headings import Paragraph
    from owlsperch.segment.runner import _back_extend_start_index

    paragraphs = [
        Paragraph(
            page=1,
            text="Chapter intro.",
            kind="prose",
            median_word_height=10.0,
            max_word_height=10.0,
            line_count=1,
        ),
        Paragraph(
            page=2,
            text="BARBARIAN",
            kind="prose",
            median_word_height=10.0,
            max_word_height=10.0,
            line_count=1,
        ),
        Paragraph(
            page=2,
            text="Hit Die: d12.",
            kind="prose",
            median_word_height=10.0,
            max_word_height=10.0,
            line_count=1,
        ),
        Paragraph(
            page=2,
            text="BARD",
            kind="prose",
            median_word_height=10.0,
            max_word_height=10.0,
            line_count=1,
        ),
        Paragraph(
            page=2,
            text="Hit Die: d6.",
            kind="prose",
            median_word_height=10.0,
            max_word_height=10.0,
            line_count=1,
        ),
    ]
    barbarian_index, bard_index = 1, 3
    resolved_starts = [barbarian_index, bard_index]

    # Barbarian: no still-earlier class heading -- back-extends all the
    # way to the top of its own page (page 2, index 1 -- there is no
    # page-1 content on page 2 to reach past).
    assert _back_extend_start_index(paragraphs, barbarian_index, resolved_starts) == 1

    # Bard: bounded at barbarian's own heading index + 1 -- it reaches
    # back onto the SAME page (as intended, to recover its own pre-heading
    # tail) but never as far back as barbarian's own heading paragraph or
    # page 1's chapter intro.
    assert _back_extend_start_index(paragraphs, bard_index, resolved_starts) == barbarian_index + 1


def test_class_span_supersedes_fragment_segments_but_not_unrelated_ones(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_classes_book(data_dir)

    segment_book(_entry("book"), data_dir=data_dir)

    segments = {s["seg_id"]: s for s in _segment_files(data_dir, "book")}

    front_matter = segments["book-p0001-01"]
    # The plain (page-order, heading-split) segmenter doesn't know about
    # class spans -- its own front-matter fragment absorbs page 2's
    # chapter-intro paragraph too, since no heading closes it out until
    # "BARBARIAN". It still isn't superseded: page 1 falls outside every
    # class span, so the fragment as a whole doesn't fall ENTIRELY inside
    # one.
    assert front_matter["pages"] == [1, 2]
    assert front_matter["superseded_by"] is None  # outside every class span

    barbarian_fragment = next(
        s
        for s in segments.values()
        if s["kind_hint"] == "rules_section"
        and s["pages"] == [2, 3]
        and s["heading"] == "BARBARIAN"
    )
    assert barbarian_fragment["superseded_by"] == "book-class-p0002"

    bard_fragment = next(
        s
        for s in segments.values()
        if s["kind_hint"] == "rules_section" and s["pages"] == [3, 4] and s["heading"] == "BARD"
    )
    assert bard_fragment["superseded_by"] == "book-class-p0003"

    # The class segments themselves are never superseded by each other.
    assert segments["book-class-p0002"]["superseded_by"] is None
    assert segments["book-class-p0003"]["superseded_by"] is None


def test_segment_without_released_records_key_still_loads(tmp_path: Path) -> None:
    """Batch B10c-mand2 criterion 2: `Segment` is `extra="forbid"`, so a
    segment JSON file written before `released_records` existed must still
    load, defaulting to an empty list."""
    from owlsperch.segment.runner import Segment

    raw = {
        "seg_id": "book-p0010-01",
        "book_id": "book",
        "pages": [10],
        "printed_pages": [10],
        "kind_hint": "spell",
        "heading": "Fireball",
        "text": "Fireball text.",
        "status": "pending",
        "tier": "haiku",
        "attempts": [],
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    segment = Segment.model_validate_json(json.dumps(raw))
    assert segment.released_records == []


def test_class_span_release_moves_a_fragments_claimed_record_to_superseded(
    tmp_path: Path,
) -> None:
    """Batch B10c-mand2 criterion 4: the class-span pass releases a NEWLY
    stamped fragment's record claims in the same atomic write that sets
    `superseded_by` -- mirroring the real corpus's history, where
    extraction (and the record claims that came with it) happened before
    class detection existed at all, so the very first `segment` run after
    a toc appears must both stamp AND release in one pass."""
    data_dir = tmp_path / "data"
    _write_book(
        data_dir,
        "book",
        {
            1: [_para("Front matter opening text for the whole chapter goes here.", line_count=3)],
            2: [
                _para("BARBARIAN"),
                _para(
                    "Hit Die: d12. A barbarian is a fierce warrior, savage and strong in "
                    "battle here.",
                    line_count=3,
                ),
            ],
            3: [
                _para("BARD"),
                _para(
                    "Hit Die: d6. Bards are trained in music and magic together for adventuring.",
                    line_count=3,
                ),
            ],
            4: [
                _para(
                    "Bards continue channeling their magic across the land for a while "
                    "longer here.",
                    line_count=3,
                ),
            ],
        },
    )

    # First pass: no toc yet -- fragments are created but no class segments
    # exist and nothing is superseded yet.
    segment_book(_entry("book"), data_dir=data_dir)

    seg_dir = data_dir / "segments" / "book"
    fragment_path = next(
        p
        for p in seg_dir.glob("*.json")
        if json.loads(p.read_text())["pages"] == [2]
        and json.loads(p.read_text())["kind_hint"] == "rules_section"
    )
    fragment = json.loads(fragment_path.read_text())
    fragment_seg_id = fragment["seg_id"]

    record_rel_path = "records/book/rules_section/barbarian-table.json"
    record_path = data_dir / record_rel_path
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.write_text(
        json.dumps(
            {
                "extraction": {
                    "tier": "haiku",
                    "model": "claude-haiku-4-5",
                    "segment_id": fragment_seg_id,
                    "timestamp": "2026-01-01T00:00:00+00:00",
                }
            }
        )
    )
    fragment["records"] = [record_rel_path]
    fragment_path.write_text(json.dumps(fragment))

    # Now the toc appears -- the next (plain, non-`--force`) run discovers
    # the class span and stamps + releases the fragment's claim for the
    # first time.
    _write_toc(data_dir, "book", _classes_toc_entries())
    summary = segment_book(_entry("book"), data_dir=data_dir)

    assert summary.superseded == 2  # Barbarian + Bard fragments
    assert summary.released == 1
    assert "1 record claim(s) released" in summary.render()

    updated = json.loads(fragment_path.read_text())
    assert updated["superseded_by"] == "book-class-p0002"
    assert updated["records"] == []
    assert updated["pending_records"] == []
    assert len(updated["released_records"]) == 1
    assert updated["released_records"][0]["path"] == record_rel_path
    assert updated["released_records"][0]["moved_to"] == (
        "superseded/book/rules_section/barbarian-table.json"
    )

    assert not record_path.is_file()
    moved = data_dir / "superseded" / "book" / "rules_section" / "barbarian-table.json"
    assert moved.is_file()

    # A further run stamps and releases nothing more, and moves no files.
    third_summary = segment_book(_entry("book"), data_dir=data_dir)
    assert third_summary.superseded == 0
    assert third_summary.released == 0
    assert moved.is_file()
    assert json.loads(fragment_path.read_text()) == updated


def test_class_segmentation_is_additive_and_idempotent(tmp_path: Path) -> None:
    """A plain (non `--force`) rerun both leaves every existing segment's
    bookkeeping alone and doesn't re-stamp/duplicate anything (design
    decision D3: this is deliberately what makes a bare `owlsperch segment
    <book_id>` do the whole class-segmentation job on the real corpus)."""
    data_dir = tmp_path / "data"
    _write_classes_book(data_dir)

    segment_book(_entry("book"), data_dir=data_dir)
    first_pass = {s["seg_id"]: s for s in _segment_files(data_dir, "book")}

    # Simulate extraction bookkeeping already accumulated on a fragment
    # segment before this batch's class pass ever ran.
    seg_dir = data_dir / "segments" / "book"
    fragment_path = next(
        p
        for p in seg_dir.glob("*.json")
        if json.loads(p.read_text())["seg_id"]
        == next(
            s["seg_id"]
            for s in first_pass.values()
            if s["kind_hint"] == "rules_section" and s["pages"] == [2, 3]
        )
    )
    fragment = json.loads(fragment_path.read_text())
    fragment["records"] = ["records/book/rules_section/barbarian-fluff.json"]
    fragment["outcome"] = "validated"
    fragment_path.write_text(json.dumps(fragment))

    summary = segment_book(_entry("book"), data_dir=data_dir)
    assert summary.superseded == 0  # already stamped -- nothing new to mark

    second_pass = {s["seg_id"]: s for s in _segment_files(data_dir, "book")}
    assert second_pass.keys() == first_pass.keys()
    stamped_fragment = second_pass[fragment["seg_id"]]
    assert stamped_fragment["superseded_by"] == "book-class-p0002"
    # The extraction bookkeeping added above must survive untouched.
    assert stamped_fragment["records"] == ["records/book/rules_section/barbarian-fluff.json"]
    assert stamped_fragment["outcome"] == "validated"


def test_kinds_class_only_runs_the_toc_driven_pass(tmp_path: Path) -> None:
    """B10c-mand6 criteria 6-7: `segment_book(..., kinds={"class"})` skips
    the whole-book `build_segments` pass entirely -- no `rules_section`
    fragment segments are produced -- and writes only the requested
    class/prestige_class segments, without ever touching (or even needing)
    stale-file removal."""
    data_dir = tmp_path / "data"
    _write_classes_book(data_dir)

    summary = segment_book(_entry("book"), data_dir=data_dir, kinds=frozenset({"class"}))

    segments = _segment_files(data_dir, "book")
    assert {s["heading"] for s in _by_kind(segments, "class")} == {"Barbarian", "Bard"}
    # No whole-book pass ran at all -- no rules_section/front-matter
    # fragments were produced.
    assert _by_kind(segments, "rules_section") == []
    assert "only the toc-driven class pass ran" in summary.class_note
    assert "only the toc-driven class pass ran" in summary.render()


def test_kinds_class_force_isolation_leaves_other_segments_untouched(tmp_path: Path) -> None:
    """B10c-mand6 criterion 7: a plain `segment_book` run first produces
    the normal fragment segments plus the class segments; a SECOND run
    with `kinds={"class"}, force=True` rewrites only the class segment
    files -- every fragment segment (and its own bookkeeping) is left
    completely untouched, and nothing is deleted as stale."""
    data_dir = tmp_path / "data"
    _write_classes_book(data_dir)

    segment_book(_entry("book"), data_dir=data_dir)
    before = {s["seg_id"]: s for s in _segment_files(data_dir, "book")}
    fragment_ids = {seg_id for seg_id, s in before.items() if s["kind_hint"] != "class"}
    assert fragment_ids  # sanity: the first pass did produce fragments

    # Simulate extraction bookkeeping on a fragment, to prove it survives.
    seg_dir = data_dir / "segments" / "book"
    some_fragment_id = next(iter(fragment_ids))
    fragment_path = seg_dir / f"{some_fragment_id}.json"
    fragment = json.loads(fragment_path.read_text())
    fragment["notes"] = ["untouched-by-kinds-rerun"]
    fragment_path.write_text(json.dumps(fragment))

    summary = segment_book(
        _entry("book"), data_dir=data_dir, force=True, kinds=frozenset({"class"})
    )

    after = {s["seg_id"]: s for s in _segment_files(data_dir, "book")}
    assert after.keys() == before.keys()  # nothing added, nothing removed
    for seg_id in fragment_ids:
        assert after[seg_id] == before[seg_id] or seg_id == some_fragment_id
    assert after[some_fragment_id]["notes"] == ["untouched-by-kinds-rerun"]
    # The class segments themselves are still exactly the two expected
    # ones, and `force` was honored for them (still present, not dropped).
    assert {s["heading"] for s in _by_kind(list(after.values()), "class")} == {
        "Barbarian",
        "Bard",
    }
    assert "only the toc-driven class pass ran" in summary.class_note


def test_kinds_rejects_a_non_class_kind_with_exit_1_and_no_writes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """B10c-mand6 criterion 6: any kind other than class/prestige_class is
    a hard error -- other kinds come from the single whole-book pass and
    can't be produced selectively. No manifest lookup or per-book work
    happens at all; nothing is written."""
    data_dir = tmp_path / "data"
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text(
        """
entries:
  - book_id: book
    title: "Book"
    file: "book.pdf"
    edition: "3.5"
    kind: rulebook
"""
    )
    _write_classes_book(data_dir)

    exit_code = run_segment(
        "book", data_dir=data_dir, manifest_path=manifest_path, kinds=frozenset({"spell"})
    )

    assert exit_code == 1
    err = capsys.readouterr().err
    assert "--kinds" in err
    assert "spell" in err
    assert not (data_dir / "segments" / "book").exists()


def test_no_toc_means_no_class_segments(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_book(
        data_dir,
        "book",
        {1: [_para("Front matter body text goes on for a good while here.", line_count=3)]},
    )
    # Deliberately no toc/book.json written.

    summary = segment_book(_entry("book"), data_dir=data_dir)

    segments = _segment_files(data_dir, "book")
    assert _by_kind(segments, "class") == []
    assert _by_kind(segments, "prestige_class") == []
    assert "no toc" in summary.class_note
    assert "no toc" in summary.render()


@pytest.mark.corpus
def test_phb1_real_corpus_class_segments() -> None:
    """Acceptance criterion 2 (batch B10c): against the real PHB text and
    its own toc, `segment` finds exactly the 11 base classes and marks
    every other segment inside a class's span as superseded. Skipped (not
    failed) without the real corpus or `pdftotext` -- see the sibling spell
    corpus test above for why. NOTE: the page range below is a generous
    guess at where PHB 3.5's "Chapter 3: Classes" (plus its own contents
    pages) falls in the pdf -- widen it if a real run shows the chapter
    extends past it."""
    import shutil
    import tempfile

    from owlsperch.manifest import default_manifest_path, default_pdf_dir, load_manifest
    from owlsperch.text.runner import run_text
    from owlsperch.toc.runner import run_toc

    pdf_dir = default_pdf_dir()
    if not pdf_dir.is_dir():
        pytest.skip(f"real PDF corpus not present at {pdf_dir}")
    if shutil.which("pdftotext") is None:
        pytest.skip("pdftotext (poppler) not installed")

    entries = load_manifest(default_manifest_path())
    book_id = "phb1" if any(e.book_id == "phb1" for e in entries) else "phb"
    page_range = (1, 80)

    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        text_exit = run_text(
            book_id, pdf_dir=pdf_dir, data_dir=data_dir, page_range=page_range, force=True
        )
        assert text_exit == 0

        toc_exit = run_toc(book_id, data_dir=data_dir, force=True)
        assert toc_exit == 0

        segment_exit = run_segment(book_id, data_dir=data_dir, page_range=page_range, force=True)
        assert segment_exit == 0

        segments = _segment_files(data_dir, book_id)
        classes = _by_kind(segments, "class")
        assert len(classes) == 11, sorted(s["heading"] for s in classes)
        assert {s["heading"] for s in classes} == {
            "Barbarian",
            "Bard",
            "Cleric",
            "Druid",
            "Fighter",
            "Monk",
            "Paladin",
            "Ranger",
            "Rogue",
            "Sorcerer",
            "Wizard",
        }
        for class_segment in classes:
            assert class_segment["tier"] == "sonnet"

        # B10c-mand6 criterion 5 (judgement finding 5): every class's OWN
        # segment now recovers its own printed Alignment/Religion run-in
        # flavor paragraph, back-extended from wherever it lands relative
        # to the ALL-CAPS heading -- rogue's, ranger's, and wizard's most
        # visibly (the judgement's own concrete examples), but verified
        # here for all 11.
        for class_segment in classes:
            assert "Alignment:" in class_segment["text"], class_segment["heading"]
            assert "Religion:" in class_segment["text"], class_segment["heading"]

        # Judgement finding 1 (blocker, B10c-mand6 follow-up): criterion 1
        # also requires that the PREVIOUS class's segment does NOT contain
        # the next class's own Alignment:/Religion: run-in paragraph.
        # Every PHB base class prints its own "Alignment:" marker exactly
        # TWICE (once in its flavor blurb, e.g. "Alignment: Barbarians are
        # never lawful...", once in the stat-block intro line right after
        # the heading, e.g. "Alignment: Any nonlawful. Hit Die: d12.") and
        # its own "Religion:" marker exactly ONCE (the flavor blurb only)
        # -- verified against the real corpus text directly, independent
        # of this segmenter, for all 11 classes. Before this follow-up,
        # PHB p0050's column-reconstruction bleed (see
        # `_back_extend_start_index`'s docstring) gave 4 of the 10
        # neighbouring class pairs (bard/cleric, paladin/ranger, ranger/
        # rogue, sorcerer/wizard) a THIRD "Alignment:"/SECOND "Religion:"
        # -- the following class's own flavor paragraph, back-extended
        # into ITS OWN segment but never excluded from the previous
        # class's (heading-anchored, unchanged) end-capped range. A
        # regression that reopens that bleed shows up here as a count of
        # 3/2 instead of 2/1 on whichever class comes right before the
        # regression.
        for class_segment in classes:
            text = class_segment["text"]
            assert text.count("Alignment:") == 2, (class_segment["heading"], text)
            assert text.count("Religion:") == 1, (class_segment["heading"], text)


# ---------------------------------------------------------------------------
# Batch B11: errata_entry/update_entry segments, gated by manifest kind
# ---------------------------------------------------------------------------


def _errata_entry(book_id: str) -> ManifestEntry:
    return ManifestEntry(
        book_id=book_id,
        title="Test Errata",
        file=f"{book_id}.pdf",
        edition="3.5",
        kind="errata",
        applies_to="book",
    )


def _update_entry(book_id: str) -> ManifestEntry:
    return ManifestEntry(
        book_id=book_id,
        title="Test Update",
        file=f"{book_id}.pdf",
        edition="3.5",
        kind="update",
        applies_to="book",
    )


def test_errata_book_produces_one_errata_entry_segment_per_paragraph(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_book(
        data_dir,
        "errata-book",
        {
            1: [
                _para("Errata Rule: Primary Sources take precedence.", line_count=2),
                _para(
                    "Glibness Player's Handbook, page 236 In second paragraph, change to X.",
                    line_count=3,
                ),
                _para(
                    "A Thousand Faces Player's Handbook, page 37 Replace alter self with "
                    "disguise self.",
                    line_count=3,
                ),
                _para(
                    "Overrun Player's Handbook, page 148 Change -1 to +1.",
                    line_count=2,
                ),
            ]
        },
    )

    segment_book(_errata_entry("errata-book"), data_dir=data_dir)

    segments = _segment_files(data_dir, "errata-book")
    entries = _by_kind(segments, "errata_entry")
    assert len(entries) == 3
    # The per-book common suffix ("Player's Handbook") is stripped (D3).
    assert {e["heading"] for e in entries} == {"Glibness", "A Thousand Faces", "Overrun"}


def test_update_book_produces_update_entry_segments(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_book(
        data_dir,
        "update-book",
        {1: [_para("Overrun Player's Handbook, page 148 Change -1 to +1.", line_count=2)]},
    )

    segment_book(_update_entry("update-book"), data_dir=data_dir)

    segments = _segment_files(data_dir, "update-book")
    entries = _by_kind(segments, "update_entry")
    assert len(entries) == 1
    assert entries[0]["kind_hint"] == "update_entry"


def test_rulebook_never_produces_errata_anchor_even_with_page_reference(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_book(
        data_dir,
        "book",
        {
            1: [
                _para(
                    "For more on grappling see the Player's Handbook, page 44 for the full "
                    "rules on the subject.",
                    line_count=3,
                )
            ]
        },
    )

    segment_book(_entry("book"), data_dir=data_dir)

    segments = _segment_files(data_dir, "book")
    assert _by_kind(segments, "errata_entry") == []
    assert _by_kind(segments, "update_entry") == []
