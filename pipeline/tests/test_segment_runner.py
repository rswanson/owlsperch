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
