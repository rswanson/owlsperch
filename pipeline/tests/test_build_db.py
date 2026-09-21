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

from owlsperch.build_db.runner import _class_owned_names, build_db, flatten_fields, run_build_db
from owlsperch.supersede import normalize_heading


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


#: A `table` record's own cells must now (B10c-mand20) be traceable to its
#: owning segment's text -- appended to the default segment text below for
#: any segment that also owns the default `_valid_table_record()` grid
#: (Rank/Title/1/Initiate/2/Adept), so the many pre-existing dangling-
#: parent/superseding fixtures that reuse it don't trip the new check.
_DEFAULT_TABLE_GRID_TEXT = "Table 1-1: Sable Ranks\nRank\tTitle\n1\tInitiate\n2\tAdept"

#: Likewise for `_owned_level_table_record()`'s own grid, owned by the
#: "book-class-p0002" segment alongside `_valid_class_record()`.
_OWNED_LEVEL_TABLE_GRID_TEXT = (
    "Testclass Level Progression\n"
    "Level\tBase Attack Bonus\tFort Save\tRef Save\tWill Save\tSpecial\n"
    "1st\t+1\t+2\t+0\t+0\tRage 1/day\n"
    "2nd\t+2\t+3\t+0\t+0\tUncanny dodge\n"
    "3rd\t+3\t+3\t+1\t+1\tTrap sense +1"
)


def _write_segment(
    data_dir: Path, book_id: str, seg_id: str, pages: list[int], *, text: str | None = None
) -> None:
    seg_dir = data_dir / "segments" / book_id
    seg_dir.mkdir(parents=True, exist_ok=True)
    segment = {
        "seg_id": seg_id,
        "book_id": book_id,
        "pages": pages,
        "printed_pages": pages,
        "kind_hint": "spell",
        "heading": "Test Spell",
        "text": text if text is not None else "Test Spell\n\nEvocation Level: Sor/Wiz 3.",
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
    _write_segment(data_dir, "book", "book-p0010-01", [10], text=_DEFAULT_TABLE_GRID_TEXT)
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
    _write_segment(data_dir, "book", "book-p0010-01", [10], text=_DEFAULT_TABLE_GRID_TEXT)
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


# ---------------------------------------------------------------------------
# Batch B10c-mand4: a `tables` row whose `parent_record` names an id that
# never made it into `records` (missing, invalid, skipped, or superseded
# away) renders fine in `/browse/table` while the entity it claims to
# belong to 404s -- with no warning at all before this. `BuildResult.
# dangling_parents` and `run_build_db`'s WARNING line report it.
# ---------------------------------------------------------------------------


def test_build_db_reports_dangling_table_parent(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(data_dir, "book", "book-p0010-01", [10], text=_DEFAULT_TABLE_GRID_TEXT)
    _write_record(
        data_dir,
        "book",
        "table",
        "table-1-1-sable-ranks",
        _valid_table_record(parent_record="rules_section:book:never-loaded"),
    )

    result = build_db(
        data_dir=data_dir, manifest_path=manifest_path, schemas_dir=_repo_schemas_dir()
    )

    assert len(result.dangling_parents) == 1
    dangling = result.dangling_parents[0]
    assert dangling.record_id == "table:book:table-1-1-sable-ranks"
    assert dangling.parent_record == "rules_section:book:never-loaded"


def test_build_db_reports_no_dangling_parent_when_the_owner_loads(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(data_dir, "book", "book-p0010-01", [10], text=_DEFAULT_TABLE_GRID_TEXT)
    _write_record(
        data_dir,
        "book",
        "rules_section",
        "sable-rites",
        _rules_section_record(
            book_id="book", slug="sable-rites", pages=[10], seg_id="book-p0010-01"
        ),
    )
    _write_record(
        data_dir,
        "book",
        "table",
        "table-1-1-sable-ranks",
        _valid_table_record(parent_record="rules_section:book:sable-rites"),
    )

    result = build_db(
        data_dir=data_dir, manifest_path=manifest_path, schemas_dir=_repo_schemas_dir()
    )

    assert result.dangling_parents == []


def test_run_build_db_prints_dangling_parent_warning(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(data_dir, "book", "book-p0010-01", [10], text=_DEFAULT_TABLE_GRID_TEXT)
    _write_record(
        data_dir,
        "book",
        "table",
        "table-1-1-sable-ranks",
        _valid_table_record(parent_record="rules_section:book:never-loaded"),
    )

    out = io.StringIO()
    err = io.StringIO()
    exit_code = run_build_db(
        data_dir=data_dir,
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
        out=out,
        err=err,
    )

    assert exit_code == 0  # not --strict
    assert "Dangling table parents: 1" in out.getvalue()
    err_text = err.getvalue()
    assert "WARNING: 1 table record(s) name a parent_record that is not loaded" in err_text
    assert "table:book:table-1-1-sable-ranks -> rules_section:book:never-loaded" in err_text


def test_run_build_db_strict_fails_on_dangling_parent_even_with_no_skipped_invalid(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(data_dir, "book", "book-p0010-01", [10], text=_DEFAULT_TABLE_GRID_TEXT)
    _write_record(
        data_dir,
        "book",
        "table",
        "table-1-1-sable-ranks",
        _valid_table_record(parent_record="rules_section:book:never-loaded"),
    )

    exit_code = run_build_db(
        data_dir=data_dir,
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
        strict=True,
        out=io.StringIO(),
        err=io.StringIO(),
    )

    assert exit_code == 1


def test_build_db_table_rows_produce_no_record_fields_rows(tmp_path: Path) -> None:
    """The `rows` grid must not also be flattened into `record_fields` --
    that would duplicate its content and defeat the point of the dedicated
    `tables` table."""
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(data_dir, "book", "book-p0010-01", [10], text=_DEFAULT_TABLE_GRID_TEXT)
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


# ---------------------------------------------------------------------------
# Batch B10b (design decision D10): derived toc_category/toc_chapter/
# toc_section/toc_path columns, and the missing-toc WARNING.
# ---------------------------------------------------------------------------


def _write_toc(data_dir: Path, book_id: str, entries: list[dict[str, Any]]) -> None:
    toc_dir = data_dir / "toc"
    toc_dir.mkdir(parents=True, exist_ok=True)
    toc = {
        "book_id": book_id,
        "generated_at": "2026-01-01T00:00:00+00:00",
        "contents_pages": [1],
        "entries": entries,
    }
    (toc_dir / f"{book_id}.json").write_text(json.dumps(toc, indent=2))


_CHAPTER_ENTRY = {
    "title": "Chapter 1: Magic",
    "level": 1,
    "printed_page": 1,
    "pdf_page_start": 1,
    "pdf_page_end": 20,
    "path": ["Chapter 1: Magic"],
    "category": "magic",
}
_SECTION_ENTRY = {
    "title": "Fireball Rules",
    "level": 2,
    "printed_page": 5,
    "pdf_page_start": 5,
    "pdf_page_end": 5,
    "path": ["Chapter 1: Magic", "Fireball Rules"],
    "category": "magic",
}


def test_build_db_derives_toc_category_chapter_section_for_a_page_inside_a_section(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(data_dir, "book", "book-p0010-01", [5])
    _write_record(
        data_dir,
        "book",
        "spell",
        "fireball",
        _valid_spell_record(seg_id="book-p0010-01", pages=[5]),
    )
    _write_toc(data_dir, "book", [_CHAPTER_ENTRY, _SECTION_ENTRY])

    result = build_db(
        data_dir=data_dir, manifest_path=manifest_path, schemas_dir=_repo_schemas_dir()
    )
    assert result.toc_missing_books == []

    conn = _connect(result.db_path)
    try:
        row = conn.execute(
            "SELECT toc_category, toc_chapter, toc_section, toc_path FROM records WHERE id = ?",
            ("spell:book:fireball",),
        ).fetchone()
    finally:
        conn.close()

    assert row["toc_category"] == "magic"
    assert row["toc_chapter"] == "Chapter 1: Magic"
    assert row["toc_section"] == "Fireball Rules"
    assert json.loads(row["toc_path"]) == ["Chapter 1: Magic", "Fireball Rules"]


def test_build_db_derives_chapter_only_when_page_has_no_deeper_section(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    # Page 12 is inside the chapter's span (1-20) but outside the one
    # section's span (5-5) -- the deepest containing entry is the chapter
    # itself.
    _write_segment(data_dir, "book", "book-p0010-01", [12])
    _write_record(
        data_dir,
        "book",
        "spell",
        "fireball",
        _valid_spell_record(seg_id="book-p0010-01", pages=[12]),
    )
    _write_toc(data_dir, "book", [_CHAPTER_ENTRY, _SECTION_ENTRY])

    result = build_db(
        data_dir=data_dir, manifest_path=manifest_path, schemas_dir=_repo_schemas_dir()
    )
    conn = _connect(result.db_path)
    try:
        row = conn.execute(
            "SELECT toc_category, toc_chapter, toc_section FROM records WHERE id = ?",
            ("spell:book:fireball",),
        ).fetchone()
    finally:
        conn.close()

    assert row["toc_category"] == "magic"
    assert row["toc_chapter"] == "Chapter 1: Magic"
    assert row["toc_section"] is None


def test_build_db_no_toc_file_yields_uncategorized_and_records_missing_book(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(data_dir, "book", "book-p0010-01", [5])
    _write_record(
        data_dir,
        "book",
        "spell",
        "fireball",
        _valid_spell_record(seg_id="book-p0010-01", pages=[5]),
    )
    # No toc/book.json written at all.

    result = build_db(
        data_dir=data_dir, manifest_path=manifest_path, schemas_dir=_repo_schemas_dir()
    )
    assert result.toc_missing_books == ["book"]

    conn = _connect(result.db_path)
    try:
        row = conn.execute(
            "SELECT toc_category, toc_chapter, toc_section, toc_path FROM records WHERE id = ?",
            ("spell:book:fireball",),
        ).fetchone()
    finally:
        conn.close()

    assert row["toc_category"] == "uncategorized"
    assert row["toc_chapter"] is None
    assert row["toc_section"] is None
    assert row["toc_path"] is None


def test_build_db_empty_toc_file_treated_same_as_missing(tmp_path: Path) -> None:
    """A present-but-empty `toc/<book_id>.json` (`"entries": []`) -- e.g.
    from a book whose only contents-like page turned out to be a
    numbered-table index -- must fall back to uncategorized/NULL and be
    counted in `toc_missing_books`, exactly like a book with no toc file
    at all."""
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(data_dir, "book", "book-p0010-01", [5])
    _write_record(
        data_dir,
        "book",
        "spell",
        "fireball",
        _valid_spell_record(seg_id="book-p0010-01", pages=[5]),
    )
    _write_toc(data_dir, "book", [])

    result = build_db(
        data_dir=data_dir, manifest_path=manifest_path, schemas_dir=_repo_schemas_dir()
    )
    assert result.toc_missing_books == ["book"]

    conn = _connect(result.db_path)
    try:
        row = conn.execute(
            "SELECT toc_category, toc_chapter, toc_section, toc_path FROM records WHERE id = ?",
            ("spell:book:fireball",),
        ).fetchone()
    finally:
        conn.close()

    assert row["toc_category"] == "uncategorized"
    assert row["toc_chapter"] is None
    assert row["toc_section"] is None
    assert row["toc_path"] is None


def test_run_build_db_warns_once_per_book_with_empty_toc_file(tmp_path: Path) -> None:
    """Same warning as the missing-file case, for a present-but-empty
    `toc/<book_id>.json` -- naming `--force` since plain `owlsperch toc
    book` is a no-op once the (empty) file already exists."""
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(data_dir, "book", "book-p0010-01", [5])
    _write_record(
        data_dir,
        "book",
        "spell",
        "fireball",
        _valid_spell_record(seg_id="book-p0010-01", pages=[5]),
    )
    _write_toc(data_dir, "book", [])

    out, err = io.StringIO(), io.StringIO()
    run_build_db(
        data_dir=data_dir,
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
        out=out,
        err=err,
    )

    warnings = [line for line in err.getvalue().splitlines() if "toc/book.json" in line]
    assert len(warnings) == 1
    assert "uv run owlsperch toc book --force" in warnings[0]


def test_run_build_db_warns_once_per_book_with_no_toc_file(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(data_dir, "book", "book-p0010-01", [5])
    _write_record(
        data_dir,
        "book",
        "spell",
        "fireball",
        _valid_spell_record(seg_id="book-p0010-01", pages=[5]),
    )
    _write_record(
        data_dir,
        "book",
        "spell",
        "alarm",
        _valid_spell_record(seg_id="book-p0010-01", pages=[5], name="Alarm", slug="alarm"),
    )

    out, err = io.StringIO(), io.StringIO()
    run_build_db(
        data_dir=data_dir,
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
        out=out,
        err=err,
    )

    warnings = [line for line in err.getvalue().splitlines() if "toc/book.json" in line]
    assert len(warnings) == 1
    assert "uv run owlsperch toc book --force" in warnings[0]


def test_build_db_does_not_write_record_fields_rows_for_toc_columns(tmp_path: Path) -> None:
    """The derived toc columns must never leak into `record_fields` -- that
    would collide with the extractor-written `rules_section.fields.chapter`
    key (D10)."""
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(data_dir, "book", "book-p0010-01", [5])
    _write_record(
        data_dir,
        "book",
        "spell",
        "fireball",
        _valid_spell_record(seg_id="book-p0010-01", pages=[5]),
    )
    _write_toc(data_dir, "book", [_CHAPTER_ENTRY, _SECTION_ENTRY])

    result = build_db(
        data_dir=data_dir, manifest_path=manifest_path, schemas_dir=_repo_schemas_dir()
    )
    conn = _connect(result.db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM record_fields WHERE key IN ('category', 'chapter', 'section')"
        ).fetchall()
    finally:
        conn.close()
    assert rows == []


# ---------------------------------------------------------------------------
# Batch B10c, design decision D11: superseding pass.
# ---------------------------------------------------------------------------


def _valid_class_record(*, book_id: str = "book") -> dict[str, Any]:
    return {
        "id": f"class:{book_id}:testclass",
        "type": "class",
        "name": "Testclass",
        "slug": "testclass",
        "aliases": [],
        "book_id": book_id,
        "pages": [2, 3],
        "citation": "Test Book pp. 2-3",
        "text_md": "Testclass overview.",
        "fields": {
            "hit_die": "d12",
            "class_type": "base",
            "max_level": 3,
            "alignment": "Any",
            "bab_progression": "good",
            "save_progressions": {"fort": "good", "ref": "poor", "will": "poor"},
            "class_skills": [{"skill": "Climb", "key_ability": "Str"}],
            "skill_points": {"base": 4, "ability": "Int"},
            "level_table": f"table:{book_id}:table-x-the-testclass",
            "class_features": [
                {"name": "Rage", "level": 1, "text_md": "You rage."},
                {"name": "Uncanny Dodge", "level": 2, "text_md": "You dodge."},
                {"name": "Trap Sense", "level": 3, "text_md": "Your senses grow wary of traps."},
            ],
            "description_sections": [{"heading": "Adventures", "text_md": "..."}],
            "weapon_and_armor_proficiency": "Simple weapons only.",
            "source_pages": {"start": 2, "end": 3},
        },
        "tables": [f"table:{book_id}:table-x-the-testclass"],
        "canonical": False,
        "variant_of": None,
        "applied_overrides": [],
        "macro_eligible": False,
        "schema_version": 3,
        "extraction": {
            "tier": "sonnet",
            "model": "claude-sonnet-test",
            "segment_id": "book-class-p0002",
            "timestamp": "2026-01-01T00:00:00+00:00",
        },
    }


def _owned_level_table_record(*, book_id: str = "book") -> dict[str, Any]:
    return {
        "id": f"table:{book_id}:table-x-the-testclass",
        "type": "table",
        "name": "Table X: The Testclass",
        "slug": "table-x-the-testclass",
        "aliases": [],
        "book_id": book_id,
        "pages": [2],
        "citation": "Test Book p. 2",
        "text_md": "",
        "fields": {
            "caption": "Table X: The Testclass",
            "columns": [
                "Level",
                "Base Attack Bonus",
                "Fort Save",
                "Ref Save",
                "Will Save",
                "Special",
            ],
            "rows": [
                ["1st", "+1", "+2", "+0", "+0", "Rage 1/day"],
                ["2nd", "+2", "+3", "+0", "+0", "Uncanny dodge"],
                ["3rd", "+3", "+3", "+1", "+1", "Trap sense +1"],
            ],
            "parent_record": f"class:{book_id}:testclass",
        },
        "tables": [],
        "canonical": False,
        "variant_of": None,
        "applied_overrides": [],
        "macro_eligible": False,
        "schema_version": 1,
        "extraction": {
            "tier": "sonnet",
            "model": "claude-sonnet-test",
            "segment_id": "book-class-p0002",
            "timestamp": "2026-01-01T00:00:00+00:00",
        },
    }


def _rules_section_record(
    *, book_id: str, slug: str, pages: list[int], seg_id: str, name: str | None = None
) -> dict[str, Any]:
    # `name` defaults to the slug read back as a title; pass it explicitly
    # for a record whose printed name matters (batch B10c-mand11: only a
    # CLASS-STRUCTURAL name is superseded inside a class span).
    name = name if name is not None else slug.replace("-", " ").title()
    return {
        "id": f"rules_section:{book_id}:{slug}",
        "type": "rules_section",
        "name": name,
        "slug": slug,
        "aliases": [],
        "book_id": book_id,
        "pages": pages,
        "citation": f"Test Book p. {pages[0]}",
        "text_md": "Some fragment text.",
        "fields": {"topic": name},
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


def test_build_db_supersedes_fragments_inside_a_class_span(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(data_dir, "book", "book-class-p0002", [2, 3], text=_OWNED_LEVEL_TABLE_GRID_TEXT)
    _write_segment(data_dir, "book", "book-p0003-01", [3])
    _write_segment(data_dir, "book", "book-p0010-01", [10])

    _write_record(data_dir, "book", "class", "testclass", _valid_class_record())
    _write_record(data_dir, "book", "table", "table-x-the-testclass", _owned_level_table_record())
    _write_record(
        data_dir,
        "book",
        "rules_section",
        "class-features-testclass",
        _rules_section_record(
            book_id="book",
            slug="class-features-testclass",
            pages=[3],
            seg_id="book-p0003-01",
            name="Class Features (Testclass)",
        ),
    )
    _write_record(
        data_dir,
        "book",
        "rules_section",
        "unrelated",
        _rules_section_record(book_id="book", slug="unrelated", pages=[10], seg_id="book-p0010-01"),
    )

    result = build_db(
        data_dir=data_dir, manifest_path=manifest_path, schemas_dir=_repo_schemas_dir()
    )
    assert result.skipped_invalid == 0, result.skipped
    assert result.superseded == 1

    conn = _connect(result.db_path)
    try:
        # A class-structural fragment name (batch B10c-mand11) -- the
        # parenthetical qualifier the prompt's naming rule adds is stripped
        # before the match, so this reads as "Class Features".
        fluff = conn.execute(
            "SELECT canonical, superseded_by FROM records WHERE id = ?",
            ("rules_section:book:class-features-testclass",),
        ).fetchone()
        assert fluff["canonical"] == 0
        assert fluff["superseded_by"] == "class:book:testclass"

        unrelated = conn.execute(
            "SELECT canonical, superseded_by FROM records WHERE id = ?",
            ("rules_section:book:unrelated",),
        ).fetchone()
        assert unrelated["canonical"] == 1
        assert unrelated["superseded_by"] is None

        # The class's own progression table must NEVER be superseded, even
        # though its own pages fall entirely inside the class's own span --
        # this is the most likely bug in this batch (D11).
        owned_table = conn.execute(
            "SELECT canonical, superseded_by FROM records WHERE id = ?",
            ("table:book:table-x-the-testclass",),
        ).fetchone()
        assert owned_table["canonical"] == 1
        assert owned_table["superseded_by"] is None

        class_row = conn.execute(
            "SELECT canonical, superseded_by FROM records WHERE id = ?",
            ("class:book:testclass",),
        ).fetchone()
        assert class_row["canonical"] == 1
        assert class_row["superseded_by"] is None
    finally:
        conn.close()


def test_build_db_leaves_a_sidebar_inside_a_class_span_canonical(tmp_path: Path) -> None:
    """Batch B10c-mand11: page span alone is no longer enough -- a printed
    sidebar sharing a class's pages ("Familiars") stays canonical, while a
    class-structural fragment on the very same page ("Class Features") is
    still superseded. Before this, the sidebar was demoted to
    `canonical = 0` and so existed in no canonical record at all."""
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(data_dir, "book", "book-class-p0002", [2, 3], text=_OWNED_LEVEL_TABLE_GRID_TEXT)
    _write_segment(data_dir, "book", "book-p0003-01", [3])
    _write_segment(data_dir, "book", "book-p0003-02", [3])

    _write_record(data_dir, "book", "class", "testclass", _valid_class_record())
    _write_record(data_dir, "book", "table", "table-x-the-testclass", _owned_level_table_record())
    _write_record(
        data_dir,
        "book",
        "rules_section",
        "class-features-testclass",
        _rules_section_record(
            book_id="book",
            slug="class-features-testclass",
            pages=[3],
            seg_id="book-p0003-01",
            name="Class Features (Testclass)",
        ),
    )
    _write_record(
        data_dir,
        "book",
        "rules_section",
        "familiars",
        _rules_section_record(book_id="book", slug="familiars", pages=[3], seg_id="book-p0003-02"),
    )

    result = build_db(
        data_dir=data_dir, manifest_path=manifest_path, schemas_dir=_repo_schemas_dir()
    )
    assert result.skipped_invalid == 0, result.skipped
    assert result.superseded == 1  # "Class Features", never "Familiars"

    conn = _connect(result.db_path)
    try:
        familiars = conn.execute(
            "SELECT canonical, superseded_by FROM records WHERE id = ?",
            ("rules_section:book:familiars",),
        ).fetchone()
        assert familiars["canonical"] == 1
        assert familiars["superseded_by"] is None

        features = conn.execute(
            "SELECT canonical, superseded_by FROM records WHERE id = ?",
            ("rules_section:book:class-features-testclass",),
        ).fetchone()
        assert features["canonical"] == 0
        assert features["superseded_by"] == "class:book:testclass"
    finally:
        conn.close()


def test_build_db_supersedes_a_class_records_own_feature_sections(tmp_path: Path) -> None:
    """Batch B10c-mand11 (follow-up): this pass has the class RECORD in
    hand, so a fragment named after one of the class's own printed
    feature/section headings ("Wild Shape", "Ex-Testclasses") is superseded even
    though the heading isn't class-structural on its own -- while a printed
    sidebar on the same pages ("Familiars") still stays canonical. A
    parenthetical suffix on the printed feature heading ("Wild Shape (Su)")
    normalizes away, so it matches the plainly named record."""
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(data_dir, "book", "book-class-p0002", [2, 3], text=_OWNED_LEVEL_TABLE_GRID_TEXT)
    for ordinal in range(1, 5):
        _write_segment(data_dir, "book", f"book-p0003-0{ordinal}", [3])

    class_record = _valid_class_record()
    # The level table's own Special column still has to reconcile (the
    # class validator checks it), so these two are ADDED to the default
    # feature list rather than replacing it.
    class_record["fields"]["class_features"] = [
        *class_record["fields"]["class_features"],
        {"name": "Wild Shape (Su)", "level": 1, "text_md": "You change shape."},
        {"name": "Venom Immunity (Ex)", "level": 2, "text_md": "You resist poison."},
    ]
    class_record["fields"]["description_sections"] = [
        {"heading": "Ex-Testclasses", "text_md": "A testclass who strays loses it all."}
    ]
    _write_record(data_dir, "book", "class", "testclass", class_record)
    _write_record(data_dir, "book", "table", "table-x-the-testclass", _owned_level_table_record())

    for slug, name, seg_id in (
        ("wild-shape", "Wild Shape", "book-p0003-01"),
        ("venom-immunity", "Venom Immunity", "book-p0003-02"),
        ("ex-testclasses", "Ex-Testclasses", "book-p0003-03"),
        ("familiars", "Familiars", "book-p0003-04"),
    ):
        _write_record(
            data_dir,
            "book",
            "rules_section",
            slug,
            _rules_section_record(book_id="book", slug=slug, pages=[3], seg_id=seg_id, name=name),
        )

    result = build_db(
        data_dir=data_dir, manifest_path=manifest_path, schemas_dir=_repo_schemas_dir()
    )
    assert result.skipped_invalid == 0, result.skipped
    # Wild Shape + Venom Immunity + Ex-Testclasses -- never Familiars.
    assert result.superseded == 3

    conn = _connect(result.db_path)
    try:
        for slug in ("wild-shape", "venom-immunity", "ex-testclasses"):
            row = conn.execute(
                "SELECT canonical, superseded_by FROM records WHERE id = ?",
                (f"rules_section:book:{slug}",),
            ).fetchone()
            assert row["canonical"] == 0, slug
            assert row["superseded_by"] == "class:book:testclass", slug

        familiars = conn.execute(
            "SELECT canonical, superseded_by FROM records WHERE id = ?",
            ("rules_section:book:familiars",),
        ).fetchone()
        assert familiars["canonical"] == 1
        assert familiars["superseded_by"] is None
    finally:
        conn.close()


def test_build_db_keeps_a_sidebars_own_table_canonical_with_its_parent(tmp_path: Path) -> None:
    """B10c-mand14: the real-corpus case -- the FAMILIARS sidebar inside the
    sorcerer's span stays canonical (B10c-mand11), but its own progression
    grid, a `table` whose `parent_record` names that sidebar, used to be
    demoted anyway (every table in a span passes the ownership predicate),
    leaving a canonical parent rendering a non-canonical table. A table
    follows its parent: the sidebar's grid stays canonical, while a table
    parented by a class-structural fragment ("Class Features (Testclass)")
    is still demoted with it."""
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(data_dir, "book", "book-class-p0002", [2, 3], text=_OWNED_LEVEL_TABLE_GRID_TEXT)
    for ordinal in range(4, 8):
        # ordinals 6/7 own the `familiar-progression`/`class-features-grid`
        # tables below (B10c-mand20: their default grid must be in the
        # segment's own text too).
        text = _DEFAULT_TABLE_GRID_TEXT if ordinal in (6, 7) else None
        _write_segment(data_dir, "book", f"book-p0003-0{ordinal}", [3], text=text)
    class_record = _valid_class_record()
    _write_record(data_dir, "book", "class", "testclass", class_record)
    _write_record(data_dir, "book", "table", "table-x-the-testclass", _owned_level_table_record())

    for slug, name, seg_id in (
        ("familiars", "Familiars", "book-p0003-04"),
        ("class-features-testclass", "Class Features (Testclass)", "book-p0003-05"),
    ):
        _write_record(
            data_dir,
            "book",
            "rules_section",
            slug,
            _rules_section_record(book_id="book", slug=slug, pages=[3], seg_id=seg_id, name=name),
        )
    for slug, name, parent, seg_id in (
        (
            "familiar-progression",
            "Familiar Progression",
            "rules_section:book:familiars",
            "book-p0003-06",
        ),
        (
            "class-features-grid",
            "Class Features Grid",
            "rules_section:book:class-features-testclass",
            "book-p0003-07",
        ),
    ):
        table = _valid_table_record(
            book_id="book", slug=slug, pages=[3], name=name, seg_id=seg_id, parent_record=parent
        )
        _write_record(data_dir, "book", "table", slug, table)

    result = build_db(
        data_dir=data_dir, manifest_path=manifest_path, schemas_dir=_repo_schemas_dir()
    )
    assert result.skipped_invalid == 0, result.skipped
    conn = _connect(result.db_path)
    try:
        rows = dict(
            conn.execute(
                "SELECT slug, canonical FROM records WHERE type IN ('rules_section', 'table')"
            ).fetchall()
        )
    finally:
        conn.close()
    assert rows["familiars"] == 1
    assert rows["familiar-progression"] == 1
    assert rows["class-features-testclass"] == 0
    assert rows["class-features-grid"] == 0
    assert rows["table-x-the-testclass"] == 1


def test_class_owned_names_excludes_generic_flavor_headings() -> None:
    """A class's "Alignment"/"Races"/... description headings are printed
    by every class, so they must not let one class demote its neighbour's
    same-named fragment on a shared page (B10c-mand11 review finding)."""
    record = {
        "name": "Testclass",
        "fields": {
            "class_features": [{"name": "Wild Shape (Su)"}],
            "description_sections": [
                {"heading": "Alignment"},
                {"heading": "Races"},
                {"heading": "Other Classes"},
                {"heading": "Ex-Testclasses"},
            ],
        },
    }
    names = _class_owned_names(record)
    assert normalize_heading("Wild Shape") in names
    assert normalize_heading("Ex-Testclasses") in names
    for generic in ("Alignment", "Races", "Other Classes", "Class Features"):
        assert normalize_heading(generic) not in names


def test_class_owned_names_collects_every_printed_heading() -> None:
    """The name set is built once per class record, from its own `name`,
    every `class_features[].name`, and every `description_sections[]
    .heading` -- all normalized (parentheticals dropped, case and
    punctuation folded)."""
    class_record = _valid_class_record()
    class_record["fields"]["class_features"] = [
        {"name": "Wild Shape (Su)", "level": 1, "text_md": "..."},
    ]
    class_record["fields"]["description_sections"] = [
        {"heading": "Ex-Testclasses", "text_md": "..."},
        {"heading": "Alignment", "text_md": "..."},  # generic: excluded
    ]

    assert _class_owned_names(class_record) == {
        "testclass",
        "wildshape",
        "extestclasses",
    }
    # A class record with neither list (a bare prestige-class shape) still
    # yields just its own name, never an empty-string entry.
    assert _class_owned_names({"name": "Testclass", "fields": {}}) == {"testclass"}
    assert _class_owned_names({}) == set()


def test_run_build_db_prints_superseded_count(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(data_dir, "book", "book-class-p0002", [2, 3], text=_OWNED_LEVEL_TABLE_GRID_TEXT)
    _write_segment(data_dir, "book", "book-p0003-01", [3])
    _write_toc(data_dir, "book", [_CHAPTER_ENTRY, _SECTION_ENTRY])

    _write_record(data_dir, "book", "class", "testclass", _valid_class_record())
    _write_record(data_dir, "book", "table", "table-x-the-testclass", _owned_level_table_record())
    _write_record(
        data_dir,
        "book",
        "rules_section",
        "class-features-testclass",
        _rules_section_record(
            book_id="book",
            slug="class-features-testclass",
            pages=[3],
            seg_id="book-p0003-01",
            name="Class Features (Testclass)",
        ),
    )

    out = io.StringIO()
    err = io.StringIO()
    exit_code = run_build_db(
        data_dir=data_dir,
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
        out=out,
        err=err,
    )
    assert exit_code == 0
    assert "Superseded" in out.getvalue()
    assert "1" in out.getvalue()
    assert "WARNING" not in err.getvalue()  # superseding is informational, not a problem


# ---------------------------------------------------------------------------
# Superseded-segment skip (batch B10c-mand2, criterion 6): belt-and-braces
# against a record file left behind (or restored by hand) even though its
# OWNING segment (looked up via the record's own `extraction.segment_id`)
# carries `superseded_by` -- it must never load into the database, but it
# must not count as an "invalid" record either (it may well be perfectly
# schema-valid).
# ---------------------------------------------------------------------------


def _write_raw_segment(data_dir: Path, book_id: str, seg_id: str, **overrides: Any) -> None:
    seg_dir = data_dir / "segments" / book_id
    seg_dir.mkdir(parents=True, exist_ok=True)
    segment: dict[str, Any] = {
        "seg_id": seg_id,
        "book_id": book_id,
        "pages": [11],
        "printed_pages": [11],
        "kind_hint": "spell",
        "heading": "Test Spell",
        "text": "text",
        "status": "done",
        "tier": "haiku",
        "attempts": [],
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    segment.update(overrides)
    (seg_dir / f"{seg_id}.json").write_text(json.dumps(segment))


def test_build_db_skips_a_record_whose_owning_segment_is_superseded(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(data_dir, "book", "book-p0010-01", [10])
    _write_raw_segment(data_dir, "book", "book-p0011-01", superseded_by="book-class-p0002")

    _write_record(
        data_dir,
        "book",
        "spell",
        "fireball",
        _valid_spell_record(book_id="book", seg_id="book-p0010-01", pages=[10]),
    )
    _write_record(
        data_dir,
        "book",
        "spell",
        "icy-bolt",
        _valid_spell_record(
            book_id="book",
            seg_id="book-p0011-01",
            pages=[11],
            name="Icy Bolt",
            slug="icy-bolt",
        ),
    )

    result = build_db(
        data_dir=data_dir, manifest_path=manifest_path, schemas_dir=_repo_schemas_dir()
    )

    assert result.skipped_invalid == 0, result.skipped
    assert result.skipped_superseded == 1
    assert result.counts_by_type.get("spell") == 1

    conn = _connect(result.db_path)
    try:
        assert (
            conn.execute("SELECT id FROM records WHERE id = ?", ("spell:book:icy-bolt",)).fetchone()
            is None
        )
        assert (
            conn.execute("SELECT id FROM records WHERE id = ?", ("spell:book:fireball",)).fetchone()
            is not None
        )
    finally:
        conn.close()


def test_run_build_db_prints_skipped_superseded_count_separately(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_toc(data_dir, "book", [_CHAPTER_ENTRY, _SECTION_ENTRY])
    _write_raw_segment(data_dir, "book", "book-p0011-01", superseded_by="book-class-p0002")
    _write_record(
        data_dir,
        "book",
        "spell",
        "icy-bolt",
        _valid_spell_record(
            book_id="book",
            seg_id="book-p0011-01",
            pages=[11],
            name="Icy Bolt",
            slug="icy-bolt",
        ),
    )

    out = io.StringIO()
    err = io.StringIO()
    exit_code = run_build_db(
        data_dir=data_dir,
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
        out=out,
        err=err,
    )

    assert exit_code == 0
    assert "Skipped (superseded segment): 1" in out.getvalue()
    assert "Skipped (invalid): 0" in out.getvalue()
    assert "WARNING" not in err.getvalue()


def test_build_db_ignores_records_under_superseded_directory(tmp_path: Path) -> None:
    """Criterion 7: `superseded/<book_id>/<type>/*.json` is not
    `records/<book_id>/<type>/*.json`, so build-db never even discovers a
    file living there -- no production code change is needed for this; the
    test is the guard."""
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(data_dir, "book", "book-p0010-01", [10])

    superseded_dir = data_dir / "superseded" / "book" / "spell"
    superseded_dir.mkdir(parents=True, exist_ok=True)
    (superseded_dir / "fireball.json").write_text(
        json.dumps(
            _valid_spell_record(book_id="book", seg_id="book-p0010-01", pages=[10]), indent=2
        )
    )

    result = build_db(
        data_dir=data_dir, manifest_path=manifest_path, schemas_dir=_repo_schemas_dir()
    )

    assert result.skipped_invalid == 0
    assert result.skipped_superseded == 0
    assert result.counts_by_type == {}


# ---------------------------------------------------------------------------
# Batch B11: variant_of/applied_overrides columns, and the errata/update
# toc-warning exemption (criterion 8, design decision D17).
# ---------------------------------------------------------------------------


def test_records_table_has_variant_of_and_applied_overrides_columns(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(data_dir, "book", "book-p0010-01", [10])
    _write_record(data_dir, "book", "spell", "fireball", _valid_spell_record())

    result = build_db(
        data_dir=data_dir, manifest_path=manifest_path, schemas_dir=_repo_schemas_dir()
    )

    conn = _connect(result.db_path)
    try:
        row = conn.execute(
            "SELECT variant_of, applied_overrides FROM records WHERE id = ?",
            ("spell:book:fireball",),
        ).fetchone()
    finally:
        conn.close()

    assert row["variant_of"] is None
    assert json.loads(row["applied_overrides"]) == []


def _errata_manifest(tmp_path: Path) -> Path:
    manifest = {
        "entries": [
            {
                "book_id": "target-book",
                "title": "Target Book",
                "short_title": "TB",
                "file": "targetbook.pdf",
                "edition": "3.5",
                "kind": "rulebook",
            },
            {
                "book_id": "errata-book",
                "title": "Target Book Errata",
                "short_title": "TBE",
                "file": "errata.pdf",
                "edition": "3.5",
                "kind": "errata",
                "applies_to": "target-book",
            },
            {
                "book_id": "supplement-book",
                "title": "A Supplement",
                "short_title": "SUP",
                "file": "supplement.pdf",
                "edition": "3.5",
                "kind": "supplement",
            },
        ]
    }
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(manifest))
    return path


def _valid_errata_entry_record(
    *,
    book_id: str = "errata-book",
    seg_id: str = "errata-book-p0001-01",
    slug: str = "glibness-p-236",
    target_name: str = "Glibness",
    target_page: int | None = 236,
) -> dict[str, Any]:
    name = f"{target_name} (p. {target_page})" if target_page is not None else target_name
    return {
        "id": f"errata_entry:{book_id}:{slug}",
        "type": "errata_entry",
        "name": name,
        "slug": slug,
        "aliases": [],
        "book_id": book_id,
        "pages": [1],
        "citation": "Target Book Errata pdf p. 1",
        "text_md": "Change the wording as follows: new wording.",
        "fields": {
            "target_book": "target-book",
            "target_page": target_page,
            "target_name": target_name,
            "replacement_text": "New wording.",
        },
        "tables": [],
        "canonical": False,
        "variant_of": None,
        "applied_overrides": [],
        "macro_eligible": False,
        "schema_version": 1,
        "extraction": {
            "tier": "sonnet",
            "model": "claude-sonnet-test",
            "segment_id": seg_id,
            "timestamp": "2026-01-01T00:00:00+00:00",
        },
    }


def _write_errata_segment(data_dir: Path, book_id: str, seg_id: str, pages: list[int]) -> None:
    seg_dir = data_dir / "segments" / book_id
    seg_dir.mkdir(parents=True, exist_ok=True)
    segment = {
        "seg_id": seg_id,
        "book_id": book_id,
        "pages": pages,
        "printed_pages": [],
        "kind_hint": "errata_entry",
        "heading": "Glibness",
        "text": "Glibness Target Book, page 236 change to read as follows: new wording.",
        "status": "pending",
        "tier": "sonnet",
        "attempts": [],
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    (seg_dir / f"{seg_id}.json").write_text(json.dumps(segment, indent=2))


def test_missing_toc_warning_skipped_for_errata_book_but_not_supplement(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _errata_manifest(tmp_path)

    _write_errata_segment(data_dir, "errata-book", "errata-book-p0001-01", [1])
    _write_record(
        data_dir, "errata-book", "errata_entry", "glibness-p-236", _valid_errata_entry_record()
    )

    _write_segment(data_dir, "supplement-book", "supplement-book-p0010-01", [10])
    _write_record(
        data_dir,
        "supplement-book",
        "spell",
        "fireball",
        _valid_spell_record(
            book_id="supplement-book", seg_id="supplement-book-p0010-01", pages=[10]
        ),
    )
    # No toc/errata-book.json and no toc/supplement-book.json written.

    result = build_db(
        data_dir=data_dir, manifest_path=manifest_path, schemas_dir=_repo_schemas_dir()
    )

    assert result.toc_missing_books == ["supplement-book"]
