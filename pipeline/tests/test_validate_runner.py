"""End-to-end tests for `owlsperch.validate.runner` (and `owlsperch validate`
via `run_validate`), against synthetic records + segments written straight
into a temp `$OWLSPERCH_DATA`-shaped directory, validated against the real
committed `schemas/` (envelope + spell + registry) -- no real book text
involved, per the batch's fixture convention.

Covers acceptance criteria 2 (JSON Schema conformance + spell consistency
checks), 3 (PASS/FAIL output, summary, exit codes, --json), 4 (write-back to
the originating segment, idempotent), and the `validate all` case.
"""

from __future__ import annotations

import copy
import io
import json
from pathlib import Path
from typing import Any

from owlsperch.validate.runner import run_validate

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _repo_schemas_dir() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "schemas"


def _write_segment(
    data_dir: Path,
    book_id: str,
    seg_id: str,
    pages: list[int],
    *,
    tier: str = "haiku",
    attempts: list[dict[str, Any]] | None = None,
    status: str = "pending",
    pending_records: list[str] | None = None,
) -> Path:
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
        "status": status,
        "tier": tier,
        "attempts": attempts or [],
        "created_at": "2026-01-01T00:00:00+00:00",
        "pending_records": pending_records or [],
    }
    path = seg_dir / f"{seg_id}.json"
    path.write_text(json.dumps(segment, indent=2))
    return path


def _read_segment(data_dir: Path, book_id: str, seg_id: str) -> dict[str, Any]:
    path = data_dir / "segments" / book_id / f"{seg_id}.json"
    raw: dict[str, Any] = json.loads(path.read_text())
    return raw


def _valid_spell_record(
    *,
    book_id: str = "book",
    seg_id: str = "book-p0010-01",
    pages: list[int] | None = None,
    name: str = "Fireball",
    slug: str = "fireball",
    schema_version: int = 1,
) -> dict[str, Any]:
    return {
        "id": f"spell:{book_id}:{slug}",
        "type": "spell",
        "name": name,
        "slug": slug,
        "aliases": [],
        "book_id": book_id,
        "pages": pages if pages is not None else [10],
        "citation": "Test Book p. 10",
        "text_md": "Deals fire damage in a burst.",
        "fields": {
            "school": "Evocation",
            "subschool": None,
            "descriptors": ["Fire"],
            "levels": [{"class": "Sorcerer", "level": 3}, {"class": "Wizard", "level": 3}],
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
        "schema_version": schema_version,
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


def _run(
    data_dir: Path,
    book_id: str = "book",
    *,
    json_output: bool = False,
    stale: bool = False,
) -> tuple[int, str]:
    out = io.StringIO()
    exit_code = run_validate(
        book_id,
        data_dir=data_dir,
        schemas_dir=_repo_schemas_dir(),
        json_output=json_output,
        stale=stale,
        out=out,
    )
    return exit_code, out.getvalue()


# ---------------------------------------------------------------------------
# Valid spell: PASS + segment write-back
# ---------------------------------------------------------------------------


def test_valid_spell_passes_and_writes_back_segment(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0010-01", [10])
    record_path = _write_record(data_dir, "book", "spell", "fireball", _valid_spell_record())

    exit_code, output = _run(data_dir)

    assert exit_code == 0
    assert f"PASS {record_path.relative_to(data_dir).as_posix()}" in output

    segment = _read_segment(data_dir, "book", "book-p0010-01")
    assert segment["status"] == "done"
    assert segment["outcome"] == "validated"
    assert segment["records"] == [record_path.relative_to(data_dir).as_posix()]


def test_pass_write_back_is_idempotent_no_duplicate_record_paths(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0010-01", [10])
    _write_record(data_dir, "book", "spell", "fireball", _valid_spell_record())

    _run(data_dir)
    _run(data_dir)

    segment = _read_segment(data_dir, "book", "book-p0010-01")
    assert segment["records"] == ["records/book/spell/fireball.json"]


# ---------------------------------------------------------------------------
# pending_records (B5 acceptance criterion 6: the extract skill's queue
# complete records claimed paths under pending_records; validate promotes or
# drops them)
# ---------------------------------------------------------------------------


def test_pass_moves_path_from_pending_records_to_records(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    record_rel = "records/book/spell/fireball.json"
    _write_segment(
        data_dir, "book", "book-p0010-01", [10], pending_records=[record_rel]
    )
    _write_record(data_dir, "book", "spell", "fireball", _valid_spell_record())

    exit_code, _ = _run(data_dir)

    assert exit_code == 0
    segment = _read_segment(data_dir, "book", "book-p0010-01")
    assert segment["records"] == [record_rel]
    assert segment["pending_records"] == []


def test_fail_removes_path_from_pending_records_without_promoting(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    record_rel = "records/book/spell/fireball.json"
    _write_segment(
        data_dir, "book", "book-p0010-01", [10], pending_records=[record_rel]
    )
    record = _valid_spell_record()
    record["fields"]["levels"] = []
    _write_record(data_dir, "book", "spell", "fireball", record)

    exit_code, _ = _run(data_dir)

    assert exit_code == 1
    segment = _read_segment(data_dir, "book", "book-p0010-01")
    assert segment["pending_records"] == []
    assert segment["records"] == []
    assert segment["status"] == "pending"


# ---------------------------------------------------------------------------
# FAIL cases
# ---------------------------------------------------------------------------


def test_missing_levels_fails(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0010-01", [10])
    record = _valid_spell_record()
    record["fields"]["levels"] = []
    _write_record(data_dir, "book", "spell", "fireball", record)

    exit_code, output = _run(data_dir)

    assert exit_code == 1
    assert "FAIL records/book/spell/fireball.json" in output
    assert "levels" in output

    segment = _read_segment(data_dir, "book", "book-p0010-01")
    assert segment["status"] == "pending"
    assert len(segment["attempts"]) == 1
    assert "levels" in " ".join(segment["attempts"][0]["errors"])


def test_fail_write_back_is_idempotent_no_duplicate_identical_attempts(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0010-01", [10])
    record = _valid_spell_record()
    record["fields"]["levels"] = []
    _write_record(data_dir, "book", "spell", "fireball", record)

    _run(data_dir)
    _run(data_dir)

    segment = _read_segment(data_dir, "book", "book-p0010-01")
    assert len(segment["attempts"]) == 1


def test_wrong_school_type_fails(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0010-01", [10])
    record = _valid_spell_record()
    record["fields"]["school"] = 123
    _write_record(data_dir, "book", "spell", "fireball", record)

    exit_code, output = _run(data_dir)

    assert exit_code == 1
    assert "FAIL records/book/spell/fireball.json" in output


def test_level_above_9_fails(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0010-01", [10])
    record = _valid_spell_record()
    record["fields"]["levels"] = [{"class": "Wizard", "level": 15}]
    _write_record(data_dir, "book", "spell", "fireball", record)

    exit_code, output = _run(data_dir)

    assert exit_code == 1
    assert "FAIL records/book/spell/fireball.json" in output


def test_page_outside_segment_span_fails(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0010-01", [10, 11])
    record = _valid_spell_record(pages=[10, 12])
    _write_record(data_dir, "book", "spell", "fireball", record)

    exit_code, output = _run(data_dir)

    assert exit_code == 1
    assert "12" in output
    assert "book-p0010-01" in output


def test_stale_schema_version_fails_normally_and_listed_by_stale(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0010-01", [10])
    record = _valid_spell_record(schema_version=0)
    record_path = _write_record(data_dir, "book", "spell", "fireball", record)

    exit_code, output = _run(data_dir)
    assert exit_code == 1
    assert "schema_version" in output

    stale_exit, stale_output = _run(data_dir, stale=True)
    assert stale_exit == 0
    assert record_path.relative_to(data_dir).as_posix() in stale_output


def test_stale_with_json_prints_only_a_json_array(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0010-01", [10])
    record = _valid_spell_record(schema_version=0)
    record_path = _write_record(data_dir, "book", "spell", "fireball", record)

    exit_code, output = _run(data_dir, stale=True, json_output=True)

    assert exit_code == 0
    parsed = json.loads(output)
    assert isinstance(parsed, list)
    assert len(parsed) == 1
    item = parsed[0]
    assert item["path"] == record_path.relative_to(data_dir).as_posix()
    assert item["type"] == "spell"
    assert item["schema_version"] == 0
    assert item["current_version"] == 1


def test_stale_with_json_and_nothing_stale_prints_empty_array(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0010-01", [10])
    _write_record(data_dir, "book", "spell", "fireball", _valid_spell_record())

    exit_code, output = _run(data_dir, stale=True, json_output=True)

    assert exit_code == 0
    assert json.loads(output) == []


def test_missing_segment_file_fails(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    # No segment file written for book-p0010-01 at all.
    _write_record(data_dir, "book", "spell", "fireball", _valid_spell_record())

    exit_code, output = _run(data_dir)

    assert exit_code == 1
    assert "FAIL records/book/spell/fireball.json" in output
    assert "segment" in output.lower()


def test_slug_id_mismatch_fails(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0010-01", [10])
    record = _valid_spell_record()
    record["slug"] = "not-fireball"
    _write_record(data_dir, "book", "spell", "fireball", record)

    exit_code, output = _run(data_dir)

    assert exit_code == 1
    assert "slug" in output.lower() or "id" in output.lower()


def test_unknown_top_level_key_fails(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0010-01", [10])
    record = _valid_spell_record()
    record["unexpected_extra_field"] = "surprise"
    _write_record(data_dir, "book", "spell", "fireball", record)

    exit_code, output = _run(data_dir)

    assert exit_code == 1
    assert "FAIL records/book/spell/fireball.json" in output
    assert "unexpected_extra_field" in output or "additional" in output.lower()


def test_unknown_type_dir_fails(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0010-01", [10])
    record = _valid_spell_record()
    record["type"] = "nonsense_type"
    _write_record(data_dir, "book", "nonsense_type", "fireball", record)

    exit_code, output = _run(data_dir)

    assert exit_code == 1
    assert "FAIL records/book/nonsense_type/fireball.json" in output
    assert "unknown type" in output.lower()


# ---------------------------------------------------------------------------
# --json
# ---------------------------------------------------------------------------


def test_json_output_shape_and_nothing_else_on_stdout(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0010-01", [10])
    _write_record(data_dir, "book", "spell", "fireball", _valid_spell_record())

    exit_code, output = _run(data_dir, json_output=True)

    assert exit_code == 0
    parsed = json.loads(output)
    assert isinstance(parsed, list)
    assert len(parsed) == 1
    item = parsed[0]
    assert item["path"] == "records/book/spell/fireball.json"
    assert item["status"] == "PASS"
    assert item["errors"] == []
    assert item["segment_id"] == "book-p0010-01"
    assert item["type"] == "spell"


def test_json_output_includes_errors_on_fail(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0010-01", [10])
    record = _valid_spell_record()
    record["fields"]["levels"] = []
    _write_record(data_dir, "book", "spell", "fireball", record)

    exit_code, output = _run(data_dir, json_output=True)

    assert exit_code == 1
    parsed = json.loads(output)
    assert parsed[0]["status"] == "FAIL"
    assert any("levels" in e for e in parsed[0]["errors"])


# ---------------------------------------------------------------------------
# `all`
# ---------------------------------------------------------------------------


def test_all_iterates_books_with_records_dir(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book-a", "book-a-p0010-01", [10])
    _write_record(
        data_dir,
        "book-a",
        "spell",
        "fireball",
        _valid_spell_record(book_id="book-a", seg_id="book-a-p0010-01"),
    )
    _write_segment(data_dir, "book-b", "book-b-p0020-01", [20])
    bad = _valid_spell_record(
        book_id="book-b", seg_id="book-b-p0020-01", name="Icy Bolt", slug="icy-bolt"
    )
    bad["fields"]["levels"] = []
    _write_record(data_dir, "book-b", "spell", "icy-bolt", bad)

    exit_code, output = _run(data_dir, book_id="all")

    assert exit_code == 1
    assert "PASS records/book-a/spell/fireball.json" in output
    assert "FAIL records/book-b/spell/icy-bolt.json" in output


def test_all_with_no_records_dirs_passes_trivially(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)

    exit_code, output = _run(data_dir, book_id="all")

    assert exit_code == 0


# ---------------------------------------------------------------------------
# The fixture record from acceptance criterion 6
# ---------------------------------------------------------------------------


def test_committed_fixture_record_passes(tmp_path: Path) -> None:
    fixtures = Path(__file__).resolve().parent / "fixtures"
    record = json.loads((fixtures / "records" / "spell-valid.json").read_text())
    segment = json.loads((fixtures / "segments" / "testbook-p0012-01.json").read_text())

    data_dir = tmp_path / "data"
    seg_dir = data_dir / "segments" / "testbook"
    seg_dir.mkdir(parents=True)
    (seg_dir / f"{segment['seg_id']}.json").write_text(json.dumps(segment))
    _write_record(data_dir, "testbook", "spell", record["slug"], copy.deepcopy(record))

    exit_code, output = _run(data_dir, book_id="testbook")

    assert exit_code == 0
    assert "PASS" in output
