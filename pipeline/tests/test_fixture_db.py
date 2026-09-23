"""Tests for `owlsperch.fixture_db` (batch B7, spec 4.10): the
pytest-free fixture-database helper behind `owlsperch fixture-db <dir>` and
`web/e2e/serve-fixture.py`."""

from __future__ import annotations

import io
import json
import sqlite3
from pathlib import Path

from owlsperch.build_db.runner import default_db_path
from owlsperch.fixture_db import run_fixture_db, write_fixture_data


def test_write_fixture_data_builds_a_real_sqlite_db(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    result = write_fixture_data(data_dir)

    assert result.skipped_invalid == 0
    # Batch B11 adds a duplicate "Ice Storm" printing across fixture-book/
    # fixture-book-2, plus one errata_entry in fixture-errata; batch B12 adds
    # one monster.
    assert result.counts_by_type == {
        "spell": 5,
        "feat": 1,
        "rules_section": 2,
        "table": 2,
        "class": 1,
        "errata_entry": 1,
        "monster": 1,
    }
    assert default_db_path(data_dir).is_file()


def test_write_fixture_data_is_searchable_via_fts(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    write_fixture_data(data_dir)

    conn = sqlite3.connect(default_db_path(data_dir))
    conn.row_factory = sqlite3.Row
    try:
        rows = list(
            conn.execute(
                "SELECT r.id FROM names_fts f JOIN records r ON r.rowid = f.rowid "
                "WHERE names_fts MATCH 'fireb*'"
            )
        )
    finally:
        conn.close()
    assert any(row["id"] == "spell:fixture-book:fireball" for row in rows)


def test_write_fixture_data_writes_manifest_segment_and_records(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    write_fixture_data(data_dir)

    assert (data_dir / "manifest.yaml").is_file()
    assert (data_dir / "segments" / "fixture-book" / "fixture-book-p0001-01.json").is_file()
    assert (data_dir / "records" / "fixture-book" / "spell" / "fireball.json").is_file()
    assert (data_dir / "records" / "fixture-book" / "spell" / "alarm.json").is_file()


def test_write_fixture_data_writes_feat_rules_section_and_table_records(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    write_fixture_data(data_dir)

    feat_path = data_dir / "records" / "fixture-book" / "feat" / "power-strike.json"
    rules_section_path = (
        data_dir / "records" / "fixture-book" / "rules_section" / "grapple-ranks.json"
    )
    table_path = data_dir / "records" / "fixture-book" / "table" / "table-1-grapple-ranks.json"
    assert feat_path.is_file()
    assert rules_section_path.is_file()
    assert table_path.is_file()

    rules_section = json.loads(rules_section_path.read_text())
    table = json.loads(table_path.read_text())
    assert rules_section["tables"] == ["table:fixture-book:table-1-grapple-ranks"]
    assert table["fields"]["parent_record"] == "rules_section:fixture-book:grapple-ranks"


def test_write_fixture_data_creates_data_dir_if_missing(tmp_path: Path) -> None:
    data_dir = tmp_path / "does" / "not" / "exist" / "yet"
    write_fixture_data(data_dir)
    assert default_db_path(data_dir).is_file()


def test_run_fixture_db_returns_zero_and_prints_summary(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    out = io.StringIO()
    exit_code = run_fixture_db(data_dir, out=out)
    assert exit_code == 0
    assert "spell: 5" in out.getvalue()


# ---------------------------------------------------------------------------
# Batch B10b (design decision D16): toc/fixture-book.json and the second
# rules_section record ("Hauling Gear") it enables.
# ---------------------------------------------------------------------------


def test_write_fixture_data_writes_toc_file(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    write_fixture_data(data_dir)

    toc_path = data_dir / "toc" / "fixture-book.json"
    assert toc_path.is_file()
    toc = json.loads(toc_path.read_text())
    chapters = [e for e in toc["entries"] if e["level"] == 1]
    sections = [e for e in toc["entries"] if e["level"] == 2]
    # B10c-mand3 Part 6: a fourth chapter/section pair ("Chapter 4:
    # Classes" / "Fixture Mage", category "classes") gives the fixture
    # class record a real toc category instead of "uncategorized".
    # Batch B12: a fifth pair ("Chapter 5: Monsters" / "Fixture Beast",
    # category "monsters") does the same for the fixture monster record.
    assert len(chapters) == 5
    assert len(sections) == 5
    assert {e["category"] for e in toc["entries"]} == {
        "magic",
        "combat",
        "equipment",
        "classes",
        "monsters",
    }


def test_write_fixture_data_writes_hauling_gear_rules_section(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    result = write_fixture_data(data_dir)

    path = data_dir / "records" / "fixture-book" / "rules_section" / "hauling-gear.json"
    assert path.is_file()
    record = json.loads(path.read_text())
    assert record["pages"] == [4]
    # The toc file is written BEFORE build_db runs, so fixture-book must
    # never warn. Batch B11's fixture-book-2 has no toc file of its own
    # (it's a supplement, not exempt like the errata/update books), so it
    # DOES warn -- that's the expected, correct D17 behavior, not a bug.
    assert result.toc_missing_books == ["fixture-book-2"]


def test_write_fixture_data_derives_categories_for_both_rules_sections(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    write_fixture_data(data_dir)

    conn = sqlite3.connect(default_db_path(data_dir))
    conn.row_factory = sqlite3.Row
    try:
        rows = {
            row["id"]: (row["toc_category"], row["toc_chapter"], row["toc_section"])
            for row in conn.execute(
                "SELECT id, toc_category, toc_chapter, toc_section FROM records "
                "WHERE type = 'rules_section'"
            )
        }
    finally:
        conn.close()

    assert rows["rules_section:fixture-book:grapple-ranks"] == (
        "combat",
        "Chapter 2: Combat",
        "Grapple Ranks",
    )
    assert rows["rules_section:fixture-book:hauling-gear"] == (
        "equipment",
        "Chapter 3: Equipment",
        "Hauling Gear",
    )


def test_fixture_class_owns_its_level_table_and_matches_a_real_spell_list(tmp_path: Path) -> None:
    """Batch B10c, design decision D13: the fixture class validates cleanly
    (proving the class validators are satisfiable), owns a 3-row level
    table, has three class features (including its required `Spells`
    entry, B10c-mand4 criterion 6), and its `spellcasting.spell_list`
    points at a class name the fixture spells actually use -- so the web
    Spells section has something real to render."""
    data_dir = tmp_path / "data"
    result = write_fixture_data(data_dir)
    assert result.skipped_invalid == 0, result.skipped

    conn = sqlite3.connect(default_db_path(data_dir))
    conn.row_factory = sqlite3.Row
    try:
        class_row = conn.execute(
            "SELECT * FROM records WHERE id = ?", ("class:fixture-book:fixture-mage",)
        ).fetchone()
        assert class_row is not None
        record = json.loads(class_row["json"])
        assert record["fields"]["spellcasting"]["spell_list"] == "Wizard"
        assert len(record["fields"]["class_features"]) == 3
        assert record["tables"] == ["table:fixture-book:table-1-the-fixture-mage"]

        table_row = conn.execute(
            "SELECT * FROM tables WHERE record_id = ?",
            ("table:fixture-book:table-1-the-fixture-mage",),
        ).fetchone()
        assert table_row is not None
        assert len(json.loads(table_row["rows"])) == 3

        wizard_spell_count = conn.execute(
            "SELECT COUNT(*) FROM record_fields WHERE key = 'levels' AND "
            "LOWER(text_value) LIKE 'wizard%'"
        ).fetchone()[0]
        assert wizard_spell_count > 0
    finally:
        conn.close()


def test_fixture_class_resolves_to_the_classes_toc_category(tmp_path: Path) -> None:
    """B10c-mand3 Part 6: the fixture class record's own pages (5) fall
    inside the new "Chapter 4: Classes" / "Fixture Mage" toc entries, so it
    resolves to toc_category "classes" (not "uncategorized") -- the web
    tree's Classes branch has a fixture to open in Playwright."""
    data_dir = tmp_path / "data"
    write_fixture_data(data_dir)

    conn = sqlite3.connect(default_db_path(data_dir))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT toc_category, toc_chapter, toc_section FROM records WHERE id = ?",
            ("class:fixture-book:fixture-mage",),
        ).fetchone()
    finally:
        conn.close()

    assert row is not None
    assert (row["toc_category"], row["toc_chapter"], row["toc_section"]) == (
        "classes",
        "Chapter 4: Classes",
        "Fixture Mage",
    )
