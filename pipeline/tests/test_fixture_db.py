"""Tests for `owlsperch.fixture_db` (batch B7, spec 4.10): the
pytest-free fixture-database helper behind `owlsperch fixture-db <dir>` and
`web/e2e/serve-fixture.py`."""

from __future__ import annotations

import io
import sqlite3
from pathlib import Path

from owlsperch.build_db.runner import default_db_path
from owlsperch.fixture_db import run_fixture_db, write_fixture_data


def test_write_fixture_data_builds_a_real_sqlite_db(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    result = write_fixture_data(data_dir)

    assert result.skipped_invalid == 0
    assert result.counts_by_type == {"spell": 2}
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


def test_write_fixture_data_creates_data_dir_if_missing(tmp_path: Path) -> None:
    data_dir = tmp_path / "does" / "not" / "exist" / "yet"
    write_fixture_data(data_dir)
    assert default_db_path(data_dir).is_file()


def test_run_fixture_db_returns_zero_and_prints_summary(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    out = io.StringIO()
    exit_code = run_fixture_db(data_dir, out=out)
    assert exit_code == 0
    assert "spell: 2" in out.getvalue()
