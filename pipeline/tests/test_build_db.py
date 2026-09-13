"""Tests for `owlsperch build-db` (spec 4.8, batch B6): table shapes, field
explosion (including array-of-object `levels` rows), skip-invalid handling,
counts, and idempotency. Fixtures are synthetic records + segments + a temp
manifest, validated against the real committed `schemas/` -- no real book
text involved, matching this repo's fixture convention (see
test_validate_runner.py).
"""

from __future__ import annotations

import copy
import io
import json
import sqlite3
from pathlib import Path
from typing import Any

import yaml

from owlsperch.build_db.runner import build_db, flatten_fields, run_build_db


def _repo_schemas_dir() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "schemas"


def _write_manifest(tmp_path: Path) -> Path:
    manifest = {
        "entries": [
            {
                "book_id": "book",
                "title": "Test Book",
                "short_title": "TB",
                "file": "testbook.pdf",
                "edition": "3.5",
                "kind": "rulebook",
                "published": "2003-07",
            },
            {
                "book_id": "otherbook",
                "title": "Other Test Book",
                "short_title": "OTB",
                "file": "otherbook.pdf",
                "edition": "3.5",
                "kind": "rulebook",
                "published": "2005-01",
            },
        ]
    }
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(manifest))
    return path


def _write_segment(data_dir: Path, book_id: str, seg_id: str, pages: list[int]) -> None:
    seg_dir = data_dir / "segments" / book_id
    seg_dir.mkdir(parents=True, exist_ok=True)
    segment = {
        "seg_id": seg_id,
        "book_id": book_id,
        "pages": pages,
        "printed_pages": pages,
        "kind_hint": "spell",
        "heading": "Test Spell",
        "text": "Test Spell\n\nEvocation Level: Sor/Wiz 3.",
        "status": "pending",
        "tier": "haiku",
        "attempts": [],
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    (seg_dir / f"{seg_id}.json").write_text(json.dumps(segment, indent=2))


def _valid_spell_record(
    *,
    book_id: str = "book",
    seg_id: str = "book-p0010-01",
    pages: list[int] | None = None,
    name: str = "Fireball",
    slug: str = "fireball",
    descriptors: list[str] | None = None,
    levels: list[dict[str, Any]] | None = None,
    aliases: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": f"spell:{book_id}:{slug}",
        "type": "spell",
        "name": name,
        "slug": slug,
        "aliases": aliases or [],
        "book_id": book_id,
        "pages": pages if pages is not None else [10],
        "citation": "Test Book p. 10",
        "text_md": "Deals fire damage in a burst.",
        "fields": {
            "school": "Evocation",
            "subschool": None,
            "descriptors": descriptors if descriptors is not None else ["Fire"],
            "levels": levels
            if levels is not None
            else [{"class": "Sorcerer", "level": 3}, {"class": "Wizard", "level": 3}],
            "components": ["V", "S", "M"],
            "casting_time": "1 standard action",
            "range": "Long",
            "target_effect_area": "20-ft.-radius burst",
            "duration": "Instantaneous",
            "saving_throw": "Reflex half",
            "spell_resistance": "Yes",
            "costs": {"material": None, "focus": None, "xp": None},
        },
        "tables": [],
        "canonical": False,
        "variant_of": None,
        "applied_overrides": [],
        "macro_eligible": False,
        "schema_version": 3,
        "extraction": {
            "tier": "haiku",
            "model": "claude-haiku-test",
            "segment_id": seg_id,
            "timestamp": "2026-01-01T00:00:00+00:00",
        },
    }


def _write_record(
    data_dir: Path, book_id: str, type_dir: str, slug: str, record: dict[str, Any]
) -> Path:
    out_dir = data_dir / "records" / book_id / type_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{slug}.json"
    path.write_text(json.dumps(record, indent=2))
    return path


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def test_build_db_creates_tables_and_loads_valid_record(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(data_dir, "book", "book-p0010-01", [10])
    _write_record(data_dir, "book", "spell", "fireball", _valid_spell_record())

    result = build_db(
        data_dir=data_dir, manifest_path=manifest_path, schemas_dir=_repo_schemas_dir()
    )

    assert result.db_path.is_file()
    assert result.counts_by_type == {"spell": 1}
    assert result.skipped_invalid == 0
    assert result.books == 2

    conn = _connect(result.db_path)
    try:
        table_names = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            )
        }
        for expected in ("books", "records", "record_fields", "record_pages", "names_fts"):
            assert expected in table_names

        record_row = conn.execute(
            "SELECT * FROM records WHERE id = ?", ("spell:book:fireball",)
        ).fetchone()
        assert record_row["type"] == "spell"
        assert record_row["name"] == "Fireball"
        assert record_row["slug"] == "fireball"
        assert record_row["book_id"] == "book"
        assert record_row["canonical"] == 1
        assert record_row["macro_eligible"] == 0
        stored = json.loads(record_row["json"])
        assert stored["id"] == "spell:book:fireball"

        book_row = conn.execute("SELECT * FROM books WHERE book_id = ?", ("book",)).fetchone()
        assert book_row["title"] == "Test Book"
        assert book_row["short_title"] == "TB"
        assert book_row["kind"] == "rulebook"
        assert book_row["edition"] == "3.5"
        assert book_row["published"] == "2003-07"
        assert book_row["status"] == "in_scope"

        page_rows = conn.execute(
            "SELECT page FROM record_pages WHERE record_id = ?", ("spell:book:fireball",)
        ).fetchall()
        assert sorted(r["page"] for r in page_rows) == [10]
    finally:
        conn.close()


def test_build_db_flattens_scalar_array_fields(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(data_dir, "book", "book-p0010-01", [10])
    _write_record(
        data_dir,
        "book",
        "spell",
        "fireball",
        _valid_spell_record(descriptors=["Fire", "Evil"]),
    )

    result = build_db(
        data_dir=data_dir, manifest_path=manifest_path, schemas_dir=_repo_schemas_dir()
    )
    conn = _connect(result.db_path)
    try:
        rows = conn.execute(
            "SELECT key, text_value, num_value FROM record_fields "
            "WHERE record_id = ? AND key = 'descriptors' ORDER BY text_value",
            ("spell:book:fireball",),
        ).fetchall()
    finally:
        conn.close()

    assert [dict(r) for r in rows] == [
        {"key": "descriptors", "text_value": "Evil", "num_value": None},
        {"key": "descriptors", "text_value": "Fire", "num_value": None},
    ]


def test_build_db_flattens_array_of_object_levels_field(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(data_dir, "book", "book-p0010-01", [10])
    _write_record(data_dir, "book", "spell", "fireball", _valid_spell_record())

    result = build_db(
        data_dir=data_dir, manifest_path=manifest_path, schemas_dir=_repo_schemas_dir()
    )
    conn = _connect(result.db_path)
    try:
        rows = conn.execute(
            "SELECT key, text_value, num_value FROM record_fields "
            "WHERE record_id = ? AND key IN ('levels', 'levels.class', 'levels.level') "
            "ORDER BY key, text_value, num_value",
            ("spell:book:fireball",),
        ).fetchall()
    finally:
        conn.close()

    rows_as_dicts = [dict(r) for r in rows]
    assert {"key": "levels.class", "text_value": "Sorcerer", "num_value": None} in rows_as_dicts
    assert {"key": "levels.class", "text_value": "Wizard", "num_value": None} in rows_as_dicts
    assert {"key": "levels.level", "text_value": None, "num_value": 3.0} in rows_as_dicts
    # Combined row so class+level pairs can be filtered together later:
    combined = [r for r in rows_as_dicts if r["key"] == "levels"]
    assert {"key": "levels", "text_value": "Sorcerer 3", "num_value": None} in combined
    assert {"key": "levels", "text_value": "Wizard 3", "num_value": None} in combined


def test_build_db_combined_row_uses_sorted_subkey_order_not_json_key_order(
    tmp_path: Path,
) -> None:
    """Regression for the B9 review finding: an item written with `level`
    before `class` in its own JSON must still produce the combined row
    `"Cleric 3"` (sorted subkey order: class < level), not `"3 Cleric"`."""
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(data_dir, "book", "book-p0010-01", [10])
    _write_record(
        data_dir,
        "book",
        "spell",
        "fireball",
        _valid_spell_record(levels=[{"level": 3, "class": "Cleric"}]),
    )

    result = build_db(
        data_dir=data_dir, manifest_path=manifest_path, schemas_dir=_repo_schemas_dir()
    )
    conn = _connect(result.db_path)
    try:
        rows = conn.execute(
            "SELECT text_value FROM record_fields WHERE record_id = ? AND key = 'levels'",
            ("spell:book:fireball",),
        ).fetchall()
    finally:
        conn.close()

    assert [r["text_value"] for r in rows] == ["Cleric 3"]


def test_build_db_skips_invalid_records(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(data_dir, "book", "book-p0010-01", [10])

    valid = _valid_spell_record()
    invalid = copy.deepcopy(_valid_spell_record(name="Broken Spell", slug="broken-spell"))
    invalid["fields"]["school"] = ""  # fails the non-empty-school check

    _write_record(data_dir, "book", "spell", "fireball", valid)
    _write_record(data_dir, "book", "spell", "broken-spell", invalid)

    result = build_db(
        data_dir=data_dir, manifest_path=manifest_path, schemas_dir=_repo_schemas_dir()
    )

    assert result.counts_by_type == {"spell": 1}
    assert result.skipped_invalid == 1

    conn = _connect(result.db_path)
    try:
        ids = {row["id"] for row in conn.execute("SELECT id FROM records")}
    finally:
        conn.close()
    assert ids == {"spell:book:fireball"}


def test_build_db_is_idempotent(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(data_dir, "book", "book-p0010-01", [10])
    _write_record(data_dir, "book", "spell", "fireball", _valid_spell_record())

    first = build_db(
        data_dir=data_dir, manifest_path=manifest_path, schemas_dir=_repo_schemas_dir()
    )
    second = build_db(
        data_dir=data_dir, manifest_path=manifest_path, schemas_dir=_repo_schemas_dir()
    )

    assert first.counts_by_type == second.counts_by_type == {"spell": 1}
    assert first.db_path == second.db_path

    conn = _connect(second.db_path)
    try:
        count = conn.execute("SELECT COUNT(*) AS n FROM records").fetchone()["n"]
    finally:
        conn.close()
    assert count == 1  # rebuilt from scratch each time, never appended to


def test_build_db_names_fts_prefix_and_alias_match(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(data_dir, "book", "book-p0010-01", [10])
    _write_record(data_dir, "book", "spell", "fireball", _valid_spell_record(aliases=["Fyre Ball"]))

    result = build_db(
        data_dir=data_dir, manifest_path=manifest_path, schemas_dir=_repo_schemas_dir()
    )
    conn = _connect(result.db_path)
    try:
        name_rows = conn.execute(
            "SELECT record_id FROM names_fts WHERE names_fts MATCH 'fireb*'"
        ).fetchall()
        assert [r["record_id"] for r in name_rows] == ["spell:book:fireball"]

        alias_rows = conn.execute(
            "SELECT record_id FROM names_fts WHERE names_fts MATCH 'fyre*'"
        ).fetchall()
        assert [r["record_id"] for r in alias_rows] == ["spell:book:fireball"]
    finally:
        conn.close()


def test_run_build_db_prints_counts_and_db_path(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(data_dir, "book", "book-p0010-01", [10])
    _write_record(data_dir, "book", "spell", "fireball", _valid_spell_record())

    out = io.StringIO()
    exit_code = run_build_db(
        data_dir=data_dir, manifest_path=manifest_path, schemas_dir=_repo_schemas_dir(), out=out
    )

    assert exit_code == 0
    output = out.getvalue()
    assert "spell: 1" in output
    assert "Skipped (invalid): 0" in output
    assert "Books: 2" in output
    assert str(data_dir / "db" / "owlsperch.sqlite") in output


# ---------------------------------------------------------------------------
# run_build_db: skipped-invalid warning + --strict exit code (batch B6
# follow-up -- skipping is expected while extraction is in progress, so it
# must never be silent, but must never fail the build either unless asked).
# ---------------------------------------------------------------------------


def _write_invalid_spell_record(data_dir: Path, book_id: str, slug: str, name: str) -> Path:
    invalid = copy.deepcopy(_valid_spell_record(name=name, slug=slug))
    invalid["fields"]["school"] = ""  # fails the non-empty-school check
    return _write_record(data_dir, book_id, "spell", slug, invalid)


def test_run_build_db_exits_0_without_strict_when_records_skipped(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(data_dir, "book", "book-p0010-01", [10])
    _write_invalid_spell_record(data_dir, "book", "broken-spell", "Broken Spell")

    out, err = io.StringIO(), io.StringIO()
    exit_code = run_build_db(
        data_dir=data_dir,
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
        out=out,
        err=err,
    )

    assert exit_code == 0
    assert "Skipped (invalid): 1" in out.getvalue()


def test_run_build_db_exits_1_with_strict_when_records_skipped(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(data_dir, "book", "book-p0010-01", [10])
    _write_invalid_spell_record(data_dir, "book", "broken-spell", "Broken Spell")

    out, err = io.StringIO(), io.StringIO()
    exit_code = run_build_db(
        data_dir=data_dir,
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
        strict=True,
        out=out,
        err=err,
    )

    assert exit_code == 1


def test_run_build_db_strict_still_exits_0_when_nothing_skipped(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(data_dir, "book", "book-p0010-01", [10])
    _write_record(data_dir, "book", "spell", "fireball", _valid_spell_record())

    exit_code = run_build_db(
        data_dir=data_dir,
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
        strict=True,
        out=io.StringIO(),
        err=io.StringIO(),
    )

    assert exit_code == 0


def test_run_build_db_warns_on_stderr_with_count_and_first_error(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(data_dir, "book", "book-p0010-01", [10])
    record_path = _write_invalid_spell_record(data_dir, "book", "broken-spell", "Broken Spell")

    out, err = io.StringIO(), io.StringIO()
    run_build_db(
        data_dir=data_dir,
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
        out=out,
        err=err,
    )

    stderr_output = err.getvalue()
    assert "WARNING" in stderr_output
    assert "skipped 1 invalid record" in stderr_output
    assert record_path.relative_to(data_dir).as_posix() in stderr_output
    assert "school" in stderr_output.lower()
    # Nothing about the skip leaks onto stdout -- it's a warning, not part
    # of the normal summary report.
    assert "WARNING" not in out.getvalue()


def test_run_build_db_warning_lists_at_most_first_5_skipped_paths(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(data_dir, "book", "book-p0010-01", [10])
    for i in range(7):
        _write_invalid_spell_record(data_dir, "book", f"broken-spell-{i}", f"Broken Spell {i}")

    out, err = io.StringIO(), io.StringIO()
    run_build_db(
        data_dir=data_dir,
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
        out=out,
        err=err,
    )

    stderr_output = err.getvalue()
    assert "skipped 7 invalid record" in stderr_output
    assert stderr_output.count("records/book/spell/broken-spell-") == 5


# ---------------------------------------------------------------------------
# B10-mand3: a duplicate record `id` (two schema-valid files claiming the
# same id) is skipped like any other invalid record, instead of crashing
# the whole build with a bare sqlite3.IntegrityError.
# ---------------------------------------------------------------------------


def test_build_db_skips_duplicate_record_id_instead_of_crashing(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(data_dir, "book", "book-p0010-01", [10])
    record = _valid_spell_record()
    _write_record(data_dir, "book", "spell", "fireball", record)
    _write_record(data_dir, "book", "spell", "fireball-copy", record)

    result = build_db(
        data_dir=data_dir, manifest_path=manifest_path, schemas_dir=_repo_schemas_dir()
    )

    with _connect(result.db_path) as conn:
        count = conn.execute("SELECT count(*) FROM records").fetchone()[0]
    assert count == 1
    assert result.counts_by_type["spell"] == 1
    assert result.skipped_invalid == 1
    assert len(result.skipped) == 1

    skipped = result.skipped[0]
    assert skipped.path in (
        "records/book/spell/fireball.json",
        "records/book/spell/fireball-copy.json",
    )
    assert "duplicate record id" in skipped.error
    assert "spell:book:fireball" in skipped.error


def test_run_build_db_duplicate_id_exits_0_and_warns(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(data_dir, "book", "book-p0010-01", [10])
    record = _valid_spell_record()
    _write_record(data_dir, "book", "spell", "fireball", record)
    _write_record(data_dir, "book", "spell", "fireball-copy", record)

    out, err = io.StringIO(), io.StringIO()
    exit_code = run_build_db(
        data_dir=data_dir,
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
        out=out,
        err=err,
    )

    assert exit_code == 0
    assert "duplicate record id" in err.getvalue()


def test_run_build_db_duplicate_id_exits_1_with_strict(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(data_dir, "book", "book-p0010-01", [10])
    record = _valid_spell_record()
    _write_record(data_dir, "book", "spell", "fireball", record)
    _write_record(data_dir, "book", "spell", "fireball-copy", record)

    exit_code = run_build_db(
        data_dir=data_dir,
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
        strict=True,
        out=io.StringIO(),
        err=io.StringIO(),
    )

    assert exit_code == 1


def test_cli_build_db_strict_flag_is_wired_up() -> None:
    from owlsperch.cli import build_parser

    parser = build_parser()
    assert parser.parse_args(["build-db"]).strict is False
    assert parser.parse_args(["build-db", "--strict"]).strict is True


# ---------------------------------------------------------------------------
# B10 criterion 10: `tables` SQLite table + `flatten_fields` exclusion of
# list-of-lists values (e.g. table `rows`).
# ---------------------------------------------------------------------------


def test_flatten_fields_excludes_list_of_lists() -> None:
    """A list-of-lists (table `rows`) produces NO `record_fields` rows --
    that grid content lives in the dedicated `tables` table instead."""
    assert flatten_fields("rows", [["1", "Initiate"], ["2", "Adept"]]) == []


def test_flatten_fields_still_flattens_list_of_scalars() -> None:
    """Unaffected by the list-of-lists exclusion: a plain scalar array (e.g.
    table `columns`, or spell `descriptors`) still gets one row per item."""
    rows = flatten_fields("columns", ["Rank", "Title"])
    assert [(r.key, r.text_value) for r in rows] == [("columns", "Rank"), ("columns", "Title")]


def _valid_table_record(
    *,
    book_id: str = "book",
    seg_id: str = "book-p0010-01",
    pages: list[int] | None = None,
    name: str = "Table 1-1: Sable Ranks",
    slug: str = "table-1-1-sable-ranks",
    columns: list[str] | None = None,
    rows: list[list[str]] | None = None,
    parent_record: str | None = None,
) -> dict[str, Any]:
    return {
        "id": f"table:{book_id}:{slug}",
        "type": "table",
        "name": name,
        "slug": slug,
        "aliases": [],
        "book_id": book_id,
        "pages": pages if pages is not None else [10],
        "citation": "Test Book p. 10",
        "text_md": "",
        "fields": {
            "caption": name,
            "columns": columns if columns is not None else ["Rank", "Title"],
            "rows": rows if rows is not None else [["1", "Initiate"], ["2", "Adept"]],
            "parent_record": parent_record,
        },
        "tables": [],
        "canonical": False,
        "variant_of": None,
        "applied_overrides": [],
        "macro_eligible": False,
        "schema_version": 1,
        "extraction": {
            "tier": "haiku",
            "model": "claude-haiku-test",
            "segment_id": seg_id,
            "timestamp": "2026-01-01T00:00:00+00:00",
        },
    }


def test_build_db_creates_tables_table(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(data_dir, "book", "book-p0010-01", [10])
    _write_record(data_dir, "book", "table", "table-1-1-sable-ranks", _valid_table_record())

    result = build_db(
        data_dir=data_dir, manifest_path=manifest_path, schemas_dir=_repo_schemas_dir()
    )
    assert result.counts_by_type == {"table": 1}

    conn = _connect(result.db_path)
    try:
        table_names = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            )
        }
        assert "tables" in table_names
    finally:
        conn.close()


def test_build_db_tables_row_round_trips_columns_and_rows_as_json(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(data_dir, "book", "book-p0010-01", [10])
    _write_record(
        data_dir,
        "book",
        "table",
        "table-1-1-sable-ranks",
        _valid_table_record(
            columns=["Rank", "Title"],
            rows=[["1", "Initiate"], ["2", "Adept"]],
            parent_record="rules_section:book:sable-rites",
        ),
    )

    result = build_db(
        data_dir=data_dir, manifest_path=manifest_path, schemas_dir=_repo_schemas_dir()
    )
    conn = _connect(result.db_path)
    try:
        row = conn.execute(
            "SELECT * FROM tables WHERE record_id = ?", ("table:book:table-1-1-sable-ranks",)
        ).fetchone()
    finally:
        conn.close()

    assert row is not None
    assert row["book_id"] == "book"
    assert row["caption"] == "Table 1-1: Sable Ranks"
    assert json.loads(row["columns"]) == ["Rank", "Title"]
    assert json.loads(row["rows"]) == [["1", "Initiate"], ["2", "Adept"]]
    assert row["parent_record"] == "rules_section:book:sable-rites"


def test_build_db_table_rows_produce_no_record_fields_rows(tmp_path: Path) -> None:
    """The `rows` grid must not also be flattened into `record_fields` --
    that would duplicate its content and defeat the point of the dedicated
    `tables` table."""
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(data_dir, "book", "book-p0010-01", [10])
    _write_record(data_dir, "book", "table", "table-1-1-sable-ranks", _valid_table_record())

    result = build_db(
        data_dir=data_dir, manifest_path=manifest_path, schemas_dir=_repo_schemas_dir()
    )
    conn = _connect(result.db_path)
    try:
        rows_field_rows = conn.execute(
            "SELECT * FROM record_fields WHERE record_id = ? AND key = 'rows'",
            ("table:book:table-1-1-sable-ranks",),
        ).fetchall()
    finally:
        conn.close()
    assert rows_field_rows == []
