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
    # Batch B10c-mand19: `--pages` restricts which segments are written by
    # each segment's own FIRST page -- not which pages the paragraph stream
    # is built from.
    data_dir = tmp_path / "data"
    _write_book(
        data_dir,
        "book",
        {
            1: [
                _para("PAGE ONE SECTION"),
                _para("Page one body text runs on for a while here.", line_count=3),
            ],
            2: [
                _para("PAGE TWO SECTION"),
                _para("Page two body text runs on for a while here too.", line_count=3),
            ],
        },
    )

    summary = segment_book(_entry("book"), data_dir=data_dir, page_range=(1, 1))

    segments = _segment_files(data_dir, "book")
    assert all(min(seg["pages"]) == 1 for seg in segments)
    assert summary.written == len(segments)
    # Page 2 is outside the PROCESSED range, so its (deliberate) lack of a
    # segment is not a coverage gap this run is responsible for.
    assert summary.uncovered_pages == []


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


# ---------------------------------------------------------------------------
# Batch B10c-mand11: the stamp pass is scoped by heading, not page span
# alone -- a printed sidebar inside a class's own pages is left completely
# alone (not stamped, claims not released), so its content stays canonical.
# ---------------------------------------------------------------------------


def _sidebar_toc_entries() -> list[dict[str, Any]]:
    return [
        {
            "title": "Chapter 3: Classes",
            "level": 1,
            "printed_page": 1,
            "pdf_page_start": 1,
            "pdf_page_end": 3,
            "path": ["Chapter 3: Classes"],
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
    ]


def _write_sidebar_book(data_dir: Path, book_id: str = "book") -> None:
    """One class (Barbarian, toc pdf page 2, so its span is pages 2-3 after
    D2's one-page extension) whose span also contains page 3's printed
    "FAMILIARS" sidebar -- the shape of the real PHB defect (the sorcerer's
    span swallowing p0053's FAMILIARS sidebar)."""
    _write_book(
        data_dir,
        book_id,
        {
            1: [_para("Front matter opening text for the whole chapter goes here.", line_count=3)],
            2: [
                _para("BARBARIAN"),
                _para(
                    "Hit Die: d12. A barbarian is a fierce warrior, savage and strong in "
                    "battle here.",
                    line_count=3,
                ),
                _para("CLASS SKILLS"),
                _para(
                    "The barbarian's class skills are listed here for every ability.", line_count=3
                ),
            ],
            3: [
                _para("FAMILIARS"),
                _para(
                    "A familiar is a magical beast that resembles a small animal and is "
                    "unusually tough and intelligent.",
                    line_count=3,
                ),
            ],
        },
    )
    _write_toc(data_dir, book_id, _sidebar_toc_entries())


def test_class_span_leaves_a_sidebar_segment_inside_it_live(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_sidebar_book(data_dir)

    summary = segment_book(_entry("book"), data_dir=data_dir)

    segments = {s["seg_id"]: s for s in _segment_files(data_dir, "book")}
    by_heading = {s["heading"]: s for s in segments.values() if s["kind_hint"] == "rules_section"}

    # Class-structural fragments inside the span are still stamped ...
    assert by_heading["BARBARIAN"]["superseded_by"] == "book-class-p0002"
    assert by_heading["CLASS SKILLS"]["superseded_by"] == "book-class-p0002"
    # ... and the printed sidebar, whose pages fall entirely inside the very
    # same span, is left completely alone.
    familiars = by_heading["FAMILIARS"]
    assert familiars["pages"] == [3]
    assert familiars["superseded_by"] is None

    assert summary.superseded == 2
    assert summary.left_live == 1
    assert "1 in-span segment(s) left live" in summary.render()


def test_class_span_does_not_release_a_sidebar_segments_claims(tmp_path: Path) -> None:
    """The other half of B10c-mand11: a sidebar left live keeps its own
    record claim, and its record file is never moved into `superseded/` --
    that move is exactly what left the real PHB's sidebars in no canonical
    record at all."""
    data_dir = tmp_path / "data"
    _write_sidebar_book(data_dir)

    # First pass: no toc yet, so nothing is stamped and the sidebar
    # fragment gets its record claim the way extraction would have.
    (data_dir / "toc" / "book.json").unlink()
    segment_book(_entry("book"), data_dir=data_dir)

    seg_dir = data_dir / "segments" / "book"
    sidebar_path = next(
        p for p in seg_dir.glob("*.json") if json.loads(p.read_text())["heading"] == "FAMILIARS"
    )
    sidebar = json.loads(sidebar_path.read_text())

    record_rel_path = "records/book/rules_section/familiars.json"
    record_path = data_dir / record_rel_path
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.write_text(
        json.dumps(
            {
                "extraction": {
                    "tier": "haiku",
                    "model": "claude-haiku-4-5",
                    "segment_id": sidebar["seg_id"],
                    "timestamp": "2026-01-01T00:00:00+00:00",
                }
            }
        )
    )
    sidebar["records"] = [record_rel_path]
    sidebar["status"] = "done"
    sidebar["outcome"] = "validated"
    sidebar_path.write_text(json.dumps(sidebar))

    # Now the toc appears and the class span is discovered.
    _write_toc(data_dir, "book", _sidebar_toc_entries())
    summary = segment_book(_entry("book"), data_dir=data_dir)

    assert summary.left_live == 1
    updated = json.loads(sidebar_path.read_text())
    assert updated["superseded_by"] is None
    assert updated["records"] == [record_rel_path]
    assert updated["released_records"] == []
    assert updated["status"] == "done"
    assert record_path.is_file()
    assert not (data_dir / "superseded").exists()


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
# Batch B10c-mand18: a class's own level table, stranded past its end cap
# ---------------------------------------------------------------------------

#: The fighter's own level-table body as the real PHB p0040 prints it (one
#: tab-joined grid paragraph). Truncated to the first and last rows -- the
#: rule under test is about WHICH paragraph lands in which segment, not the
#: grid's contents.
_FIGHTER_GRID = "Level\tBase Attack Bonus\tFort Save\n1st\t+1\t+2\n20th\t+20/+15/+10/+5\t+12"

#: A grid belonging to the NEXT class, with its own caption glued on --
#: the control case: it must NOT be pulled back into the fighter.
_MONK_GRID = "Table 3\u201310: The Monk\nLevel\tBase Attack Bonus\n20th\t+15/+10/+5"

#: An uncaptioned grid printed LATER on the shared page, after the monk's
#: own opening prose (the real p0040's "Human Fighter Starting Package"
#: skill grid): the fighter's own caption's scope has closed by then, so it
#: must not adopt this one either.
_LATE_UNCAPTIONED_GRID = "Skill\tRanks\tAbility\nClimb\t4\tStr"


def _fighter_monk_toc_entries() -> list[dict[str, Any]]:
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
            "title": "Fighter",
            "level": 2,
            "printed_page": 2,
            "pdf_page_start": 2,
            "pdf_page_end": 2,
            "path": ["Chapter 3: Classes", "Fighter"],
            "category": "classes",
        },
        {
            "title": "Monk",
            "level": 2,
            "printed_page": 3,
            "pdf_page_start": 3,
            "pdf_page_end": 3,
            "path": ["Chapter 3: Classes", "Monk"],
            "category": "classes",
        },
    ]


def _write_fighter_monk_book(data_dir: Path, *, second_grid: str) -> None:
    """The real PHB p0040 paragraph order, in miniature: the fighter's own
    level-table CAPTION, then the NEXT class's heading, then the grid the
    caption belongs to (or, for the control case, a grid carrying the
    monk's own caption), then the monk's own body, then a later
    uncaptioned grid."""
    _write_book(
        data_dir,
        "book",
        {
            1: [_para("Front matter opening text for the whole chapter goes here.", line_count=3)],
            2: [
                _para("FIGHTER"),
                _para(
                    "Hit Die: d10. Alignment: Any. Religion: Fighters revere whichever "
                    "deity favors battle here.",
                    line_count=3,
                ),
            ],
            3: [
                _para("Table 3\u20139: The Fighter"),
                _para("MONK"),
                _para(second_grid, kind="table", line_count=3),
                _para(
                    "Hit Die: d8. Alignment: Any lawful. Religion: Monks revere "
                    "whichever deity teaches discipline here.",
                    line_count=3,
                ),
                _para(_LATE_UNCAPTIONED_GRID, kind="table", line_count=2),
            ],
            4: [
                _para(
                    "Monks continue perfecting their bodies across the land for a while "
                    "longer here.",
                    line_count=3,
                ),
            ],
        },
    )
    _write_toc(data_dir, "book", _fighter_monk_toc_entries())


def test_class_span_recovers_its_own_level_table_from_past_the_end_cap(tmp_path: Path) -> None:
    """B10c-mand18: PHB p0040 prints "Table 3-9: The Fighter", then "MONK",
    then the fighter's own level-table grid. The end cap (the next class's
    heading index) left the fighter with the bare caption and NO table at
    all, so the extractor could not fill `level_table`/`bab_progression`/
    `save_progressions`. The grid must be pulled back into the fighter's
    own text, without moving the monk's start."""
    data_dir = tmp_path / "data"
    _write_fighter_monk_book(data_dir, second_grid=_FIGHTER_GRID)

    segment_book(_entry("book"), data_dir=data_dir)

    classes = _by_kind(_segment_files(data_dir, "book"), "class")
    by_heading = {s["heading"]: s for s in classes}
    assert sorted(by_heading) == ["Fighter", "Monk"]

    fighter = by_heading["Fighter"]
    # Its own caption AND its own grid, each exactly once, in printed order,
    # with the grid last (nothing past it was pulled in).
    assert fighter["text"].count("Table 3\u20139: The Fighter") == 1
    assert fighter["text"].count(_FIGHTER_GRID) == 1
    assert fighter["text"].index("Table 3\u20139: The Fighter") < fighter["text"].index(
        _FIGHTER_GRID
    )
    assert fighter["text"].rstrip().endswith(_FIGHTER_GRID)
    # The next class's heading and body are still not the fighter's.
    assert "MONK" not in fighter["text"]
    assert "Monks revere" not in fighter["text"]
    # A caption's scope closes at the prose after its own grid, so the
    # later uncaptioned grid on the same page is left alone.
    assert _LATE_UNCAPTIONED_GRID not in fighter["text"]
    assert fighter["pages"] == [2, 3]

    # The monk's own start is unchanged: still back-extended to the top of
    # its own page, so its text begins with that page's first paragraph
    # (the fighter's caption) and still holds the grid too -- the
    # extraction prompt's pre-heading attribution rule resolves that
    # overlap, exactly as it already does for every other shared page.
    monk = by_heading["Monk"]
    assert monk["text"].startswith("Table 3\u20139: The Fighter")
    assert _FIGHTER_GRID in monk["text"]
    assert monk["pages"] == [3, 4]


def test_class_span_does_not_pull_in_the_next_classs_own_table(tmp_path: Path) -> None:
    """The control case for the rule above: the grid printed after the
    "MONK" heading carries the MONK's own caption ("Table 3-10: The
    Monk"), so it belongs to the monk and must never be pulled back into
    the fighter -- even though the fighter's own caption is the last
    caption before the cut."""
    data_dir = tmp_path / "data"
    _write_fighter_monk_book(data_dir, second_grid=_MONK_GRID)

    segment_book(_entry("book"), data_dir=data_dir)

    by_heading = {s["heading"]: s for s in _by_kind(_segment_files(data_dir, "book"), "class")}
    fighter = by_heading["Fighter"]
    assert "Table 3\u201310: The Monk" not in fighter["text"]
    assert "20th\t+15/+10/+5" not in fighter["text"]
    # Its own caption is still there (it was already inside the span).
    assert "Table 3\u20139: The Fighter" in fighter["text"]
    assert _MONK_GRID in by_heading["Monk"]["text"]


@pytest.mark.corpus
def test_phb1_real_corpus_every_class_segment_has_its_own_level_table() -> None:
    """B10c-mand18 acceptance, against the REAL extracted text (copied
    read-only out of `$OWLSPERCH_DATA`, never re-extracted -- this needs no
    PDFs and no `pdftotext`, only a data dir that `owlsperch text`/`toc`
    have already run against). Every one of the 11 PHB base classes must
    end up with its own printed level table's last row in its own segment
    text; before this fix the fighter's (PHB p0040, whose caption and grid
    straddle the "MONK" heading) was missing entirely. Skipped, not
    failed, when that data dir isn't present."""
    import re
    import shutil
    import tempfile

    from owlsperch.manifest import default_manifest_path, load_manifest
    from owlsperch.text.runner import default_data_dir

    real_data = default_data_dir()
    book_id = "phb1"
    real_text = real_data / "text" / book_id
    real_toc = real_data / "toc" / f"{book_id}.json"
    if not real_text.is_dir() or not real_toc.is_file():
        pytest.skip(f"real extracted text/toc for {book_id} not present under {real_data}")

    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        (data_dir / "text").mkdir(parents=True)
        (data_dir / "toc").mkdir(parents=True)
        shutil.copytree(real_text, data_dir / "text" / book_id)
        shutil.copy(real_toc, data_dir / "toc" / f"{book_id}.json")

        entries = [e for e in load_manifest(default_manifest_path()) if e.book_id == book_id]
        assert entries, f"{book_id} is not in the manifest"
        segment_book(entries[0], data_dir=data_dir, force=True)

        classes = _by_kind(_segment_files(data_dir, book_id), "class")
        by_heading = {s["heading"]: s["text"] for s in classes}
        assert len(by_heading) == 11, sorted(by_heading)

        # Derive each class's expected last table row from the real text
        # itself rather than hard-coding a guess: find its printed
        # "Table N-M: The <Class>" caption, then the first grid paragraph
        # at or after it on that same page that has a level-20 row.
        caption_re = re.compile(
            r"^\s*Table\s+\d+\s*[\u2010-\u2015\-]\s*\d+\s*:\s*The\s+(?P<title>.+?)\s*$"
        )
        expected: dict[str, str] = {}
        for page_path in sorted((data_dir / "text" / book_id).glob("p*.txt")):
            paragraphs = page_path.read_text().split("\n\n")
            for i, paragraph in enumerate(paragraphs):
                lines = paragraph.strip().splitlines()
                if not lines:
                    continue
                match = caption_re.match(lines[0])
                if match is None or match.group("title") not in by_heading:
                    continue
                for later in paragraphs[i:]:
                    rows = [r for r in later.split("\n") if r.startswith("20th\t")]
                    if rows:
                        expected.setdefault(match.group("title"), rows[-1])
                        break

        assert sorted(expected) == sorted(by_heading), sorted(expected)
        # The fighter's own last row (the concrete defect this fixes).
        assert expected["Fighter"].startswith("20th\t+20/+15/+10/+5")
        for heading, row in expected.items():
            assert row in by_heading[heading], (heading, row)

        # B10c-mand21, against the same real text: the fighter's own
        # Starting Package sections also straddle that "MONK" heading --
        # p0040 prints the tail of its Dwarf Fighter Starting Package
        # (ending "Gold: 4d4 gp.") and its whole Human Fighter Starting
        # Package after it, both of which used to be in no record at all.
        assert "Gold: 4d4 gp." in by_heading["Fighter"]
        assert "Human Fighter Starting Package" in by_heading["Fighter"]
        # ...without swallowing the monk's own opening flavor prose, which
        # reading order prints in the middle of that package.
        assert "Dotted across the landscape" not in by_heading["Fighter"]
        assert "Dotted across the landscape" in by_heading["Monk"]


# ---------------------------------------------------------------------------
# Batch B10c-mand21: a class's own class-structural TAIL sections, stranded
# past its end cap
# ---------------------------------------------------------------------------

#: The fighter's own "Dwarf Fighter Starting Package", continued past the
#: cut: the real PHB p0040 prints these lines AFTER the "MONK" heading,
#: while the section's own heading and first paragraph are back on p0039.
_DWARF_PACKAGE_TAIL = (
    "Feat: Weapon Focus (dwarven waraxe). Bonus Feat (Fighter): If Strength is 13 or "
    "higher, Power Attack. Gear: Backpack with waterskin. Gold: 4d4 gp."
)

#: The second package's own body, skill grid and closing line -- printed
#: after its heading, with the next class's opening prose in between.
_HUMAN_PACKAGE_BODY = (
    "Armor: Scale mail (+4 AC, armor check penalty -4, speed 20 ft., 30 lb.). Weapons: "
    "Greatsword (2d6, crit 19-20/x2, 8 lb., two-handed, slashing)."
)
_HUMAN_PACKAGE_GRID = "Skill\tRanks\tAbility\nClimb\t4\tStr\nJump\t4\tStr"
_HUMAN_PACKAGE_TAIL = (
    "Feat: Weapon Focus (greatsword). Bonus Feat (Human): Blind-Fight. Gold: 2d4 gp."
)

#: The next class's own opening flavor prose, printed BETWEEN that package's
#: body and its skill grid (the real p0040's reading order). It must be
#: skipped -- not treated as the end of the fighter's package.
_MONK_OPENING_PROSE = (
    "Dotted across the landscape are monasteries, small walled cloisters inhabited by "
    "monks who pursue personal perfection through action as well as contemplation."
)

#: The NEXT class's own package body, printed BEFORE that class's own
#: package heading (the shared-page bleed `_back_extend_start_index`
#: documents) and after this class's own package ended at its Gold line:
#: package-SHAPED, but the monk's. The `Gold:` bound is what keeps it out.
_MONK_PACKAGE_BODY = (
    "Armor: None (monks are not proficient with any armor). Weapons: Quarterstaff (1d6/1d6)."
)
_MONK_PACKAGE_FEAT = "Feat: Improved Initiative. Gear: Backpack with waterskin and bedroll."


def _fighter_package_toc_entries() -> list[dict[str, Any]]:
    """The fighter/monk toc above, with the monk's entry running one page
    further: the page it shares with the fighter carries only its heading
    and opening prose, and its own "Hit Die: d8" line (the marker design
    decision D1 discriminates a real class entry by) is printed on its
    next page -- exactly as the real PHB prints the monk's Game Rule
    Information on p0041, never on p0040."""
    entries = _fighter_monk_toc_entries()
    monk = {**entries[-1], "pdf_page_end": 4}
    return [*entries[:-1], monk]


def _write_fighter_package_book(
    data_dir: Path, *, package_heading: str, monk_package_after: bool = False
) -> None:
    """The real PHB p0039/p0040 paragraph order, in miniature: the fighter's
    own "Dwarf Fighter Starting Package" heading and first paragraph, then
    (on the shared page) its level-table caption, the NEXT class's heading,
    its own grid, the REST of the dwarf package, a second Starting Package
    heading (this class's own, or -- the control -- the monk's), that
    package's body, the monk's opening prose, and the package's own skill
    grid and closing line. With `monk_package_after`, the MONK's own
    package body is printed after that closing line and before the monk's
    own package heading -- the shared-page bleed order
    `_back_extend_start_index` documents, package-shaped but not this
    class's."""
    trailing = (
        [
            _para(_MONK_PACKAGE_BODY, line_count=3),
            _para(_MONK_PACKAGE_FEAT, line_count=3),
            _para("Human Monk Starting Package", height=12.0),
        ]
        if monk_package_after
        else []
    )
    _write_book(
        data_dir,
        "book",
        {
            1: [_para("Front matter opening text for the whole chapter goes here.", line_count=3)],
            2: [
                _para("FIGHTER"),
                _para(
                    "Hit Die: d10. Alignment: Any. Religion: Fighters revere whichever "
                    "deity favors battle here.",
                    line_count=3,
                ),
                _para("Dwarf Fighter Starting Package", height=12.0),
                _para(
                    "Armor: Scale mail (+4 AC). Weapons: Dwarven waraxe (1d10). Skill "
                    "Selection: Pick a number of skills equal to 2 + Int modifier.",
                    line_count=3,
                ),
            ],
            3: [
                _para("Table 3–9: The Fighter"),
                _para("MONK"),
                _para(_FIGHTER_GRID, kind="table", line_count=3),
                _para(_DWARF_PACKAGE_TAIL, line_count=3),
                _para(package_heading, height=12.0),
                _para(_HUMAN_PACKAGE_BODY, line_count=3),
                _para(_MONK_OPENING_PROSE, line_count=5),
                _para(_HUMAN_PACKAGE_GRID, kind="table", line_count=3),
                _para(_HUMAN_PACKAGE_TAIL, line_count=3),
                *trailing,
            ],
            4: [
                _para(
                    "Hit Die: d8. Alignment: Any lawful. Religion: Monks revere "
                    "whichever deity teaches discipline here.",
                    line_count=3,
                ),
            ],
        },
    )
    _write_toc(data_dir, "book", _fighter_package_toc_entries())


def test_class_span_recovers_its_own_starting_packages_from_past_the_end_cap(
    tmp_path: Path,
) -> None:
    """B10c-mand21: on PHB p0040 the fighter's own Dwarf Fighter Starting
    Package CONTINUES past the "MONK" heading, and its whole Human Fighter
    Starting Package is printed after it -- so the fighter's text ended at
    its level-table caption and neither section was in any record. Both
    must come back, in printed order, without the monk's own opening prose
    (printed in between) coming with them."""
    data_dir = tmp_path / "data"
    _write_fighter_package_book(data_dir, package_heading="Human Fighter Starting Package")

    segment_book(_entry("book"), data_dir=data_dir)

    by_heading = {s["heading"]: s for s in _by_kind(_segment_files(data_dir, "book"), "class")}
    fighter = by_heading["Fighter"]["text"]

    # The cut-off continuation of the section that began inside the span...
    assert fighter.count(_DWARF_PACKAGE_TAIL) == 1
    # ...and the whole package printed after the next class's heading.
    assert fighter.count("Human Fighter Starting Package") == 1
    for part in (_HUMAN_PACKAGE_BODY, _HUMAN_PACKAGE_GRID, _HUMAN_PACKAGE_TAIL):
        assert fighter.count(part) == 1
    # Printed order is preserved, and the text ends with that package.
    assert (
        fighter.index(_DWARF_PACKAGE_TAIL)
        < fighter.index("Human Fighter Starting Package")
        < fighter.index(_HUMAN_PACKAGE_BODY)
        < fighter.index(_HUMAN_PACKAGE_GRID)
        < fighter.index(_HUMAN_PACKAGE_TAIL)
    )
    assert fighter.rstrip().endswith(_HUMAN_PACKAGE_TAIL)
    # The next class's own heading and body are still not the fighter's --
    # its opening prose is skipped over, not collected, even though the
    # package continues past it.
    assert "MONK" not in fighter
    assert _MONK_OPENING_PROSE not in fighter
    assert by_heading["Fighter"]["pages"] == [2, 3]

    # The next class's own start is unchanged (still back-extended to the
    # top of its own page); the extraction prompt's pre-heading attribution
    # rule resolves the overlap, exactly as before this change.
    monk = by_heading["Monk"]
    assert monk["text"].startswith("Table 3–9: The Fighter")
    assert _MONK_OPENING_PROSE in monk["text"]
    assert monk["pages"] == [3, 4]


def test_class_span_does_not_pull_in_another_classs_starting_package(tmp_path: Path) -> None:
    """The control case for the rule above: the package printed after the
    cut is the MONK's ("Human Monk Starting Package"), so neither it nor
    its body belongs to the fighter -- and a different class's structural
    heading also CLOSES the continuation, so nothing after it is collected
    either. The fighter's own cut-off continuation, printed before that
    heading, still comes back."""
    data_dir = tmp_path / "data"
    _write_fighter_package_book(data_dir, package_heading="Human Monk Starting Package")

    segment_book(_entry("book"), data_dir=data_dir)

    by_heading = {s["heading"]: s for s in _by_kind(_segment_files(data_dir, "book"), "class")}
    fighter = by_heading["Fighter"]["text"]

    assert _DWARF_PACKAGE_TAIL in fighter
    assert "Human Monk Starting Package" not in fighter
    for part in (_HUMAN_PACKAGE_BODY, _HUMAN_PACKAGE_GRID, _HUMAN_PACKAGE_TAIL):
        assert part not in fighter

    monk = by_heading["Monk"]
    assert monk["text"].startswith("Table 3–9: The Fighter")
    assert "Human Monk Starting Package" in monk["text"]
    assert monk["pages"] == [3, 4]


def test_class_span_tail_stops_at_its_own_packages_gold_line(tmp_path: Path) -> None:
    """A collected package run is bounded by the package's own printed
    "Gold: NdN gp." line: the NEXT class's own package body paragraphs are
    routinely printed BEFORE that class's own package heading on a shared
    page (the bleed `_back_extend_start_index` documents), and they are
    package-SHAPED, so without that bound they would be collected as this
    class's continuation."""
    data_dir = tmp_path / "data"
    _write_fighter_package_book(
        data_dir,
        package_heading="Human Fighter Starting Package",
        monk_package_after=True,
    )

    segment_book(_entry("book"), data_dir=data_dir)

    by_heading = {s["heading"]: s for s in _by_kind(_segment_files(data_dir, "book"), "class")}
    fighter = by_heading["Fighter"]["text"]

    # The fighter's own two packages, whole, ending at its own Gold line.
    assert _DWARF_PACKAGE_TAIL in fighter
    assert _HUMAN_PACKAGE_TAIL in fighter
    assert fighter.rstrip().endswith(_HUMAN_PACKAGE_TAIL)
    # The monk's own package body, printed after that line, is not the
    # fighter's -- nor is the heading it belongs to.
    assert _MONK_PACKAGE_BODY not in fighter
    assert _MONK_PACKAGE_FEAT not in fighter
    assert "Human Monk Starting Package" not in fighter

    monk = by_heading["Monk"]["text"]
    assert _MONK_PACKAGE_BODY in monk
    assert _MONK_PACKAGE_FEAT in monk


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


# ---------------------------------------------------------------------------
# Batch B10c-mand19: `--pages A-B` builds from the WHOLE-BOOK paragraph
# stream and restricts only which segments are written/deleted (by each
# segment's own first page), plus the coverage warning firing for every kind
# of run.
# ---------------------------------------------------------------------------


def _write_mand19_book(data_dir: Path, book_id: str = "book") -> None:
    """A three-page book with:

    * a class span covering pages 1-3 (toc pdf pages 1-2, extended by one
      under design decision D2),
    * a printed SIDEBAR section that starts on page 2 and continues onto
      page 3 (non-class-structural, so the class pass leaves it live), and
    * a further section starting on page 3.

    That's the exact shape the real phb1 defect had: `--pages 3-3` used to
    both truncate the class segment down to page 3 and re-cut the page-2
    sidebar into a headingless page-top fragment.
    """
    _write_book(
        data_dir,
        book_id,
        {
            1: [
                _para("SABLE KNIGHT"),
                _para(
                    "Hit Die: d10. A sable knight is a sworn defender of the realm and its people.",
                    line_count=3,
                ),
            ],
            2: [
                _para(
                    "The sable knight's oath continues to bind them through every trial they face.",
                    line_count=3,
                ),
                _para("FAMILIARS"),
                _para(
                    "A familiar is a magical beast that resembles a small animal and is "
                    "unusually tough.",
                    line_count=3,
                ),
            ],
            3: [
                _para(
                    "The familiar grants its master a bonus that improves as the master "
                    "gains levels.",
                    line_count=3,
                ),
                _para("PAGE THREE SECTION"),
                _para(
                    "A section that begins on the third page and belongs entirely to it alone.",
                    line_count=3,
                ),
            ],
        },
    )
    _write_toc(
        data_dir,
        book_id,
        [
            {
                "title": "Chapter 3: Classes",
                "level": 1,
                "printed_page": 1,
                "pdf_page_start": 1,
                "pdf_page_end": 3,
                "path": ["Chapter 3: Classes"],
                "category": "classes",
            },
            {
                "title": "Sable Knight",
                "level": 2,
                "printed_page": 1,
                "pdf_page_start": 1,
                "pdf_page_end": 2,
                "path": ["Chapter 3: Classes", "Sable Knight"],
                "category": "classes",
            },
        ],
    )


def _seg_by_heading(data_dir: Path, book_id: str, heading: str) -> Path:
    seg_dir = data_dir / "segments" / book_id
    matches = [
        p for p in sorted(seg_dir.glob("*.json")) if json.loads(p.read_text())["heading"] == heading
    ]
    assert len(matches) == 1, f"expected exactly one {heading!r} segment, got {matches}"
    return matches[0]


def test_pages_run_leaves_segments_starting_before_the_range_untouched(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_mand19_book(data_dir)
    entry = _entry("book")

    segment_book(entry, data_dir=data_dir)

    class_path = data_dir / "segments" / "book" / "book-class-p0001.json"
    assert json.loads(class_path.read_text())["pages"] == [1, 2, 3]
    sidebar_path = _seg_by_heading(data_dir, "book", "FAMILIARS")
    assert json.loads(sidebar_path.read_text())["pages"] == [2, 3]
    page_three_path = _seg_by_heading(data_dir, "book", "PAGE THREE SECTION")

    class_before = class_path.read_bytes()
    sidebar_before = sidebar_path.read_bytes()
    # A sentinel in the page-3 segment proves it really was rewritten.
    page_three_path.write_text(page_three_path.read_text().replace('"pending"', '"SENTINEL"'))

    summary = segment_book(entry, data_dir=data_dir, force=True, page_range=(3, 3))

    # The class segment (first page 1) and the sidebar (first page 2) are
    # byte-identical: neither deleted, nor re-cut, nor truncated.
    assert class_path.read_bytes() == class_before
    assert sidebar_path.read_bytes() == sidebar_before
    # Only the page-3 segment was rewritten -- and it still has a heading.
    assert "SENTINEL" not in page_three_path.read_text()
    assert json.loads(page_three_path.read_text())["pages"] == [3]
    assert summary.written == 1
    assert summary.uncovered_pages == []


def test_pages_run_rewrites_a_section_with_its_next_page_continuation(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_mand19_book(data_dir)
    entry = _entry("book")

    segment_book(entry, data_dir=data_dir)
    class_before = (data_dir / "segments" / "book" / "book-class-p0001.json").read_bytes()
    sidebar_path = _seg_by_heading(data_dir, "book", "FAMILIARS")
    sidebar_path.write_text(sidebar_path.read_text().replace('"pending"', '"SENTINEL"'))

    summary = segment_book(entry, data_dir=data_dir, force=True, page_range=(2, 2))

    # The page-2 section is rewritten WHOLE -- its page-3 continuation
    # included, even though page 3 is outside the range.
    sidebar = json.loads(sidebar_path.read_text())
    assert "SENTINEL" not in sidebar_path.read_text()
    assert sidebar["pages"] == [2, 3]
    assert sidebar["heading"] == "FAMILIARS"
    assert "improves as the master" in sidebar["text"]
    # ... and the class segment, starting on page 1, is still untouched.
    assert (data_dir / "segments" / "book" / "book-class-p0001.json").read_bytes() == class_before
    assert summary.uncovered_pages == []


def test_pages_run_warns_about_a_page_left_with_no_segment(tmp_path: Path) -> None:
    # The real phb1 damage: `--pages 53-53` left pdf page 54 in no segment
    # at all, silently. The page-2 text here belongs to a segment whose
    # first page is 1, which a `--pages 2-2` run deliberately does not
    # write -- so page 2 really is uncovered, and must be reported.
    data_dir = tmp_path / "data"
    _write_book(
        data_dir,
        "book",
        {
            1: [
                _para("ONLY SECTION"),
                _para("Body text that starts on page one and keeps going.", line_count=3),
            ],
            2: [_para("The very same section continues on page two here.", line_count=3)],
        },
    )

    summary = segment_book(_entry("book"), data_dir=data_dir, page_range=(2, 2))

    assert summary.written == 0
    assert summary.uncovered_pages == [2]
    # Exactly one report of it, on the summary's own `warning:` line.
    rendered = summary.render()
    assert "warning: book: page(s) with text but no segment coverage: 2" in rendered
    assert rendered.count("no segment coverage") == 1


def test_kinds_run_also_reports_coverage_gaps(tmp_path: Path) -> None:
    # B10c-mand19: a `--kinds class` run produces no `build_segments`
    # output of its own, so the coverage check reads the segment files off
    # disk -- which is what lets it run here at all.
    data_dir = tmp_path / "data"
    _write_mand19_book(data_dir)

    summary = segment_book(_entry("book"), data_dir=data_dir, kinds=frozenset({"class"}))

    # Only the class segment exists (pages 1-3), so nothing is uncovered.
    assert summary.uncovered_pages == []

    # Delete it, then re-run without --force: now no segment covers anything.
    (data_dir / "segments" / "book" / "book-class-p0001.json").unlink()
    summary = segment_book(
        _entry("book"), data_dir=data_dir, kinds=frozenset({"class"}), force=False
    )
    assert summary.uncovered_pages == []  # the class pass rewrote it

    for path in (data_dir / "segments" / "book").glob("*.json"):
        path.unlink()
    (data_dir / "toc" / "book.json").unlink()
    summary = segment_book(_entry("book"), data_dir=data_dir, kinds=frozenset({"class"}))
    assert summary.uncovered_pages == [1, 2, 3]
    assert "warning: book: page(s) with text but no segment coverage: 1, 2, 3" in summary.render()


def test_coverage_counts_a_segment_parked_in_the_human_inbox(tmp_path: Path) -> None:
    # B10c-mand19 review follow-up: a segment the escalation ladder moved to
    # `human/<book_id>/` still covers its pages perfectly well -- it awaits a
    # human extraction decision, it isn't missing -- so it must not be
    # reported as a coverage gap.
    data_dir = tmp_path / "data"
    _write_book(
        data_dir,
        "book",
        {
            1: [
                _para("ONLY SECTION"),
                _para("Body text that starts on page one and keeps going here.", line_count=3),
            ]
        },
    )
    entry = _entry("book")
    segment_book(entry, data_dir=data_dir)

    seg_path = data_dir / "segments" / "book" / "book-p0001-01.json"
    human_dir = data_dir / "human" / "book"
    human_dir.mkdir(parents=True)
    seg_path.rename(human_dir / seg_path.name)
    # An unmatched-override file (B11) lives in a subdirectory and must not
    # be parsed as a segment by the coverage scan.
    (human_dir / "overrides").mkdir()
    (human_dir / "overrides" / "book-whatever.json").write_text('{"not": "a segment"}')

    summary = segment_book(entry, data_dir=data_dir)

    assert summary.uncovered_pages == []
    assert "no segment coverage" not in summary.render()


def test_pages_run_restamps_a_fragment_inside_an_out_of_range_class_span(
    tmp_path: Path,
) -> None:
    # B10c-mand19 review follow-up: the supersede pass runs for every span
    # with a segment file on disk, not only the spans this run wrote -- so a
    # `--force --pages` run that re-cuts a class-structural fragment inside
    # an out-of-range class span stamps it again in the SAME run.
    data_dir = tmp_path / "data"
    _write_mand19_book(data_dir)
    # An extra, class-STRUCTURAL section ("Ex-<Title>") entirely on page 3 --
    # inside the Sable Knight span (pdf pages 1-3), but not on the span's own
    # first page.
    text_dir = data_dir / "text" / "book"
    page_three = (text_dir / "p0003.txt").read_text().rstrip("\n")
    (text_dir / "p0003.txt").write_text(
        page_three + "\n\nEX-SABLE KNIGHTS\n\nA knight who breaks the oath loses "
        "every granted power until atonement.\n"
    )
    meta = json.loads((text_dir / "p0003.meta.json").read_text())
    meta += [
        {"kind": "prose", "median_word_height": 10.0, "max_word_height": 10.0, "line_count": 1},
        {"kind": "prose", "median_word_height": 10.0, "max_word_height": 10.0, "line_count": 3},
    ]
    (text_dir / "p0003.meta.json").write_text(json.dumps(meta))
    entry = _entry("book")

    segment_book(entry, data_dir=data_dir)

    fragment = _seg_by_heading(data_dir, "book", "EX-SABLE KNIGHTS")
    assert json.loads(fragment.read_text())["pages"] == [3]
    assert json.loads(fragment.read_text())["superseded_by"] == "book-class-p0001"
    class_path = data_dir / "segments" / "book" / "book-class-p0001.json"
    class_before = class_path.read_bytes()

    # Page 3 is inside the class's span but is NOT the span's own first page,
    # so the class segment itself is untouched -- while the fragment is
    # deleted, re-cut, and must come back stamped in this very same run.
    summary = segment_book(entry, data_dir=data_dir, force=True, page_range=(3, 3))

    assert class_path.read_bytes() == class_before
    fragment = _seg_by_heading(data_dir, "book", "EX-SABLE KNIGHTS")
    assert json.loads(fragment.read_text())["superseded_by"] == "book-class-p0001"
    assert summary.superseded >= 1


@pytest.mark.corpus
def test_phb1_real_corpus_pages_run_preserves_overlapping_segments(tmp_path: Path) -> None:
    """The real-corpus regression this batch exists for.

    Copies the real phb1 `text/`, `toc/` and `segments/` into a temp data
    dir (never touching `$OWLSPERCH_DATA` itself) and runs the two page
    ranges that did the damage:

    * `--force --pages 46-46` must leave the paladin class segment (pdf
      pages 43-47) byte-identical -- it used to come back truncated to
      page 46 alone -- and leave every page 43-47 covered.
    * `--force --pages 53-53` must write a FAMILIARS-headed segment
      spanning pages 53 AND 54 -- it used to be cut off at page 53, with
      page 54's own segments deleted and never recreated.
    """
    import shutil

    from owlsperch.text.runner import default_data_dir

    real_data = default_data_dir()
    src_text = real_data / "text" / "phb1"
    src_toc = real_data / "toc" / "phb1.json"
    src_segments = real_data / "segments" / "phb1"
    if not (src_text.is_dir() and src_toc.is_file() and src_segments.is_dir()):
        pytest.skip(f"real phb1 text/toc/segments not present under {real_data}")

    data_dir = tmp_path / "data"
    shutil.copytree(src_text, data_dir / "text" / "phb1")
    (data_dir / "toc").mkdir(parents=True)
    shutil.copy(src_toc, data_dir / "toc" / "phb1.json")
    shutil.copytree(src_segments, data_dir / "segments" / "phb1")

    paladin = data_dir / "segments" / "phb1" / "phb1-class-p0043.json"
    if not paladin.is_file():
        pytest.skip("phb1 paladin class segment not present in the real corpus")
    paladin_before = paladin.read_bytes()
    assert json.loads(paladin_before)["pages"] == [43, 44, 45, 46, 47]

    assert run_segment("phb1", data_dir=data_dir, force=True, page_range=(46, 46)) == 0

    assert paladin.read_bytes() == paladin_before
    covered = {page for seg in _segment_files(data_dir, "phb1") for page in seg["pages"]}
    assert set(range(43, 48)) <= covered

    assert run_segment("phb1", data_dir=data_dir, force=True, page_range=(53, 53)) == 0

    familiars = [seg for seg in _segment_files(data_dir, "phb1") if seg["heading"] == "FAMILIARS"]
    assert len(familiars) == 1
    assert familiars[0]["pages"] == [53, 54]
