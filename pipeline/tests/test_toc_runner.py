"""Tests for `owlsperch toc <book_id|all>` (`owlsperch.toc.runner`, batch
B10b acceptance criterion 1): writing `toc/<book_id>.json`, idempotency/
`--force`, `all`'s no-text-dir skip, an unknown book_id, and a
`TocParseError` book being reported (not silently swallowed) -- plus a
`corpus`-marked test against the real PHB (criterion 5)."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from owlsperch.toc.runner import run_toc

_CONTENTS_TEXT = "\n".join(
    [
        "Contents",
        "Introduction .......................... 3",
        "Chapter 1: Abilities .......... 5",
        "Ability Scores .......... 5",
        "Ability Modifiers .......... 6",
        "Chapter 2: Skills .......... 10",
        "Skill Checks .......... 10",
        "Skill Descriptions .......... 12",
    ]
)


def _write_manifest(tmp_path: Path, book_ids: list[str]) -> Path:
    manifest_path = tmp_path / "manifest.yaml"
    entries = "\n".join(
        f"  - book_id: {book_id}\n"
        f'    title: "{book_id}"\n'
        f'    file: "{book_id}.pdf"\n'
        f'    edition: "3.5"\n'
        f"    kind: rulebook"
        for book_id in book_ids
    )
    manifest_path.write_text(f"entries:\n{entries}\n")
    return manifest_path


def _write_contents(data_dir: Path, book_id: str) -> Path:
    text_dir = data_dir / "text" / book_id
    text_dir.mkdir(parents=True, exist_ok=True)
    (text_dir / "p0001.txt").write_text(_CONTENTS_TEXT)
    return text_dir


def test_run_toc_writes_toc_file(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path, ["book"])
    _write_contents(data_dir, "book")

    out = io.StringIO()
    exit_code = run_toc("book", data_dir=data_dir, manifest_path=manifest_path, out=out)

    assert exit_code == 0
    toc_path = data_dir / "toc" / "book.json"
    assert toc_path.is_file()
    toc = json.loads(toc_path.read_text())
    assert toc["book_id"] == "book"
    assert len(toc["entries"]) == 7
    # Introduction, Chapter 1, and Chapter 2 are all level-1 ("chapters");
    # the remaining four entries are level-2 ("sections").
    assert "3 chapters" in out.getvalue()
    assert "4 sections" in out.getvalue()


def test_run_toc_is_idempotent_without_force(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path, ["book"])
    _write_contents(data_dir, "book")

    run_toc("book", data_dir=data_dir, manifest_path=manifest_path)
    toc_path = data_dir / "toc" / "book.json"
    first_write_time = toc_path.stat().st_mtime_ns

    out = io.StringIO()
    exit_code = run_toc("book", data_dir=data_dir, manifest_path=manifest_path, out=out)
    assert exit_code == 0
    assert "already exists" in out.getvalue()
    assert toc_path.stat().st_mtime_ns == first_write_time


def test_run_toc_force_reparses(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path, ["book"])
    text_dir = _write_contents(data_dir, "book")

    run_toc("book", data_dir=data_dir, manifest_path=manifest_path)

    # Change the contents page and re-run with --force.
    (text_dir / "p0001.txt").write_text(_CONTENTS_TEXT + "\nChapter 3: Combat .......... 20\n")
    exit_code = run_toc("book", data_dir=data_dir, manifest_path=manifest_path, force=True)
    assert exit_code == 0

    toc = json.loads((data_dir / "toc" / "book.json").read_text())
    assert any(e["title"] == "Chapter 3: Combat" for e in toc["entries"])


def test_run_toc_unknown_book_id_is_a_clear_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path, ["book"])

    exit_code = run_toc("does-not-exist", data_dir=data_dir, manifest_path=manifest_path)
    assert exit_code == 1
    assert "does-not-exist" in capsys.readouterr().err


def test_run_toc_all_skips_book_with_no_text_dir(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path, ["has-text", "no-text"])
    _write_contents(data_dir, "has-text")

    out = io.StringIO()
    exit_code = run_toc("all", data_dir=data_dir, manifest_path=manifest_path, out=out)

    output = out.getvalue()
    assert exit_code == 0
    assert "has-text:" in output
    assert "no-text:" in output and "no text output" in output


def test_run_toc_reports_failure_instead_of_writing_empty_file(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path, ["book"])
    text_dir = data_dir / "text" / "book"
    text_dir.mkdir(parents=True, exist_ok=True)
    (text_dir / "p0001.txt").write_text("Just prose, no table of contents here.\n")

    out = io.StringIO()
    exit_code = run_toc("book", data_dir=data_dir, manifest_path=manifest_path, out=out)

    assert exit_code == 1
    assert "error" in out.getvalue().lower()
    assert not (data_dir / "toc" / "book.json").exists()


def test_run_toc_reports_failure_for_table_index_only_contents_page(tmp_path: Path) -> None:
    """A contents page whose only dotted-leader lines are numbered-table
    index entries ("Table 1-N: ...") must be reported as a parse failure,
    not written out as an all-empty `toc/<book_id>.json`."""
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path, ["book"])
    text_dir = data_dir / "text" / "book"
    text_dir.mkdir(parents=True, exist_ok=True)
    table_index_text = "\n".join(
        f"Table 1–1{i}: Some Index Row .......... {10 + i}" for i in range(1, 8)
    )
    (text_dir / "p0001.txt").write_text(table_index_text)

    out = io.StringIO()
    exit_code = run_toc("book", data_dir=data_dir, manifest_path=manifest_path, out=out)

    assert exit_code == 1
    assert "error" in out.getvalue().lower()
    assert not (data_dir / "toc" / "book.json").exists()


def test_run_toc_by_id_fails_when_text_dir_is_missing(tmp_path: Path) -> None:
    """`owlsperch toc <book_id>` (an explicit id, not `all`) must exit
    non-zero when that book has no `text/<book_id>/` directory at all --
    the same "no text output" note is printed, but it's a failure for a
    single named book, not a silent no-op."""
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path, ["no-text"])

    out = io.StringIO()
    exit_code = run_toc("no-text", data_dir=data_dir, manifest_path=manifest_path, out=out)

    assert exit_code == 1
    assert "no text output" in out.getvalue()
    assert not (data_dir / "toc" / "no-text.json").exists()


def test_run_toc_all_reports_one_failed_book_but_keeps_going(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path, ["bad-book", "good-book"])
    bad_text_dir = data_dir / "text" / "bad-book"
    bad_text_dir.mkdir(parents=True, exist_ok=True)
    (bad_text_dir / "p0001.txt").write_text("No contents page at all.\n")
    _write_contents(data_dir, "good-book")

    out = io.StringIO()
    exit_code = run_toc("all", data_dir=data_dir, manifest_path=manifest_path, out=out)

    assert exit_code == 1
    assert (data_dir / "toc" / "good-book.json").is_file()
    assert not (data_dir / "toc" / "bad-book.json").exists()


# ---------------------------------------------------------------------------
# Real corpus (acceptance criteria 1, 2, 5): phb1's actual table of contents
# ---------------------------------------------------------------------------


@pytest.mark.corpus
def test_phb1_real_toc_has_sixteen_chapters_and_zero_uncategorized() -> None:
    from owlsperch.manifest import default_manifest_path, load_manifest
    from owlsperch.text.runner import default_data_dir
    from owlsperch.toc.parser import TocParseError, parse_book_toc

    data_dir = default_data_dir()
    text_dir = data_dir / "text" / "phb1"
    if not text_dir.is_dir():
        pytest.skip(f"real PHB text/ not present at {text_dir}")

    entries = load_manifest(default_manifest_path())
    if not any(e.book_id == "phb1" for e in entries):
        pytest.skip("phb1 not in the manifest")

    try:
        parsed = parse_book_toc(text_dir, book_id="phb1")
    except TocParseError as exc:
        pytest.fail(f"expected phb1's real contents page(s) to parse cleanly: {exc}")

    chapters = [e for e in parsed.entries if e.level == 1]
    sections = [e for e in parsed.entries if e.level >= 2]
    assert len(chapters) == 16
    assert len(sections) == 77
    assert parsed.uncategorized_count == 0


@pytest.mark.corpus
def test_phb1_attacks_of_opportunity_resolves_to_combat_category() -> None:
    """Criterion 5: `rules_section/attacks-of-opportunity` (if present)
    resolves to category `combat` via its containing Chapter 8 span, even
    though the section's own TOC page number and the record's actual page
    aren't a pixel-perfect match (a known, harmless real-corpus TOC
    imprecision -- see the batch's design-decision notes)."""
    from owlsperch.manifest import default_manifest_path, load_manifest
    from owlsperch.text.runner import default_data_dir
    from owlsperch.toc.lookup import entry_for_page
    from owlsperch.toc.parser import TocParseError, parse_book_toc

    data_dir = default_data_dir()
    text_dir = data_dir / "text" / "phb1"
    if not text_dir.is_dir():
        pytest.skip(f"real PHB text/ not present at {text_dir}")

    entries = load_manifest(default_manifest_path())
    if not any(e.book_id == "phb1" for e in entries):
        pytest.skip("phb1 not in the manifest")

    record_path = data_dir / "records" / "phb1" / "rules_section" / "attacks-of-opportunity.json"
    if not record_path.is_file():
        pytest.skip("records/phb1/rules_section/attacks-of-opportunity.json not extracted yet")

    record = json.loads(record_path.read_text())
    page = min(record["pages"])

    try:
        parsed = parse_book_toc(text_dir, book_id="phb1")
    except TocParseError as exc:
        pytest.fail(f"expected phb1's real contents page(s) to parse cleanly: {exc}")

    from owlsperch.toc.parser import Toc

    toc = Toc(
        book_id="phb1",
        generated_at="2026-01-01T00:00:00+00:00",
        contents_pages=parsed.contents_pages,
        entries=parsed.entries,
    )
    entry = entry_for_page(toc, page)
    assert entry is not None
    assert entry.category == "combat"
