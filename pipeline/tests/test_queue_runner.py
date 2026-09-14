"""Tests for `owlsperch.queue.runner` (the `owlsperch queue next|prompt|
complete|summary|reset` CLI subcommands) and an end-to-end dry-run of the
extract loop: a synthetic record written to the expected path plus a result
JSON, `queue complete` then `validate`, asserting the segment ends up
`done` with `records` populated (B5 acceptance criterion 7).
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from owlsperch.queue.runner import (
    run_queue_audit,
    run_queue_complete,
    run_queue_next,
    run_queue_prompt,
    run_queue_reset,
    run_queue_summary,
)
from owlsperch.segment.runner import Segment
from owlsperch.validate.runner import run_validate


def _repo_schemas_dir() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "schemas"


def _write_manifest(tmp_path: Path, book_id: str = "book") -> Path:
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text(
        f"""
entries:
  - book_id: {book_id}
    title: "Test Book"
    file: "{book_id}.pdf"
    edition: "3.5"
    kind: rulebook
"""
    )
    return manifest_path


def _write_segment(data_dir: Path, book_id: str, seg_id: str, **overrides: object) -> None:
    defaults: dict[str, Any] = dict(
        seg_id=seg_id,
        book_id=book_id,
        pages=[10],
        printed_pages=[10],
        kind_hint="spell",
        heading="Fireball",
        text="Fireball\n\nEvocation Level: Sor/Wiz 3. Deals fire damage in a burst.",
        status="pending",
        tier="haiku",
        created_at="2026-01-01T00:00:00+00:00",
    )
    defaults.update(overrides)
    segment = Segment(**defaults)
    seg_dir = data_dir / "segments" / book_id
    seg_dir.mkdir(parents=True, exist_ok=True)
    (seg_dir / f"{seg_id}.json").write_text(segment.model_dump_json(indent=2))


def _read_segment(data_dir: Path, book_id: str, seg_id: str) -> dict[str, Any]:
    path = data_dir / "segments" / book_id / f"{seg_id}.json"
    raw: dict[str, Any] = json.loads(path.read_text())
    return raw


def _valid_spell_record(*, seg_id: str) -> dict[str, Any]:
    return {
        "id": "spell:book:fireball",
        "type": "spell",
        "name": "Fireball",
        "slug": "fireball",
        "aliases": [],
        "book_id": "book",
        "pages": [10],
        "citation": "Test Book p. 10",
        "text_md": "Deals fire damage in a burst.",
        "fields": {
            "school": "Evocation",
            "subschool": None,
            "descriptors": ["Fire"],
            "levels": [{"class": "Sorcerer", "level": 3}],
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


# ---------------------------------------------------------------------------
# queue next / prompt
# ---------------------------------------------------------------------------


def test_run_queue_next_json_output(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(data_dir, "book", "book-p0010-01")

    out = io.StringIO()
    exit_code = run_queue_next(
        "book",
        tier="haiku",
        limit=5,
        kind="spell",
        json_output=True,
        data_dir=data_dir,
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
        out=out,
    )

    assert exit_code == 0
    parsed = json.loads(out.getvalue())
    assert len(parsed) == 1
    assert parsed[0]["seg_id"] == "book-p0010-01"
    assert "prompt_path" in parsed[0]


def test_run_queue_next_human_output_when_nothing_pending(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    manifest_path = _write_manifest(tmp_path)

    out = io.StringIO()
    exit_code = run_queue_next(
        "book",
        tier="haiku",
        limit=5,
        kind="spell",
        json_output=False,
        data_dir=data_dir,
        manifest_path=manifest_path,
        out=out,
    )

    assert exit_code == 0
    assert "no pending" in out.getvalue().lower()


def test_run_queue_next_dry_run_json_matches_real_selection_and_writes_nothing(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(data_dir, "book", "book-p0010-01")
    _write_segment(data_dir, "book", "book-p0011-01")

    dry_out = io.StringIO()
    exit_code = run_queue_next(
        "book",
        tier="haiku",
        limit=5,
        kind="spell",
        json_output=True,
        dry_run=True,
        data_dir=data_dir,
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
        out=dry_out,
    )
    assert exit_code == 0
    dry_payload = json.loads(dry_out.getvalue())

    for seg_id in ("book-p0010-01", "book-p0011-01"):
        segment = _read_segment(data_dir, "book", seg_id)
        assert segment["status"] == "pending"
    prompts_dir = data_dir / "prompts"
    assert not prompts_dir.exists() or list(prompts_dir.rglob("*.md")) == []

    real_out = io.StringIO()
    exit_code = run_queue_next(
        "book",
        tier="haiku",
        limit=5,
        kind="spell",
        json_output=True,
        dry_run=False,
        data_dir=data_dir,
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
        out=real_out,
    )
    assert exit_code == 0
    real_payload = json.loads(real_out.getvalue())

    dry_ids_and_prompts = {(item["seg_id"], item["prompt_path"]) for item in dry_payload}
    real_ids_and_prompts = {(item["seg_id"], item["prompt_path"]) for item in real_payload}
    assert dry_ids_and_prompts == real_ids_and_prompts

    for seg_id in ("book-p0010-01", "book-p0011-01"):
        segment = _read_segment(data_dir, "book", seg_id)
        assert segment["status"] == "in_progress"


def test_run_queue_prompt_prints_path(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(data_dir, "book", "book-p0010-01")

    out = io.StringIO()
    exit_code = run_queue_prompt(
        "book-p0010-01",
        data_dir=data_dir,
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
        out=out,
    )

    assert exit_code == 0
    printed_path = Path(out.getvalue().strip())
    assert printed_path.is_file()


def test_run_queue_prompt_unknown_seg_id_is_an_error(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)

    exit_code = run_queue_prompt("does-not-exist", data_dir=data_dir)

    assert exit_code == 1


def test_run_queue_next_reports_lock_timeout_as_error_exit_1(tmp_path: Path) -> None:
    import fcntl
    import os

    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(data_dir, "book", "book-p0010-01")

    seg_dir = data_dir / "segments" / "book"
    lock_path = seg_dir / ".queue.lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)

        exit_code = run_queue_next(
            "book",
            tier="haiku",
            limit=5,
            kind="spell",
            data_dir=data_dir,
            manifest_path=manifest_path,
            lock_timeout=0.2,
            out=io.StringIO(),
        )

        assert exit_code == 1
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def test_run_queue_next_renders_explicit_model_into_prompt(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(data_dir, "book", "book-p0010-01")

    out = io.StringIO()
    run_queue_next(
        "book",
        tier="haiku",
        limit=5,
        kind="spell",
        model="claude-opus-4-6",
        json_output=True,
        data_dir=data_dir,
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
        out=out,
    )

    parsed = json.loads(out.getvalue())
    prompt_text = Path(parsed[0]["prompt_path"]).read_text()
    assert '"model": "claude-opus-4-6"' in prompt_text


# ---------------------------------------------------------------------------
# queue complete
# ---------------------------------------------------------------------------


def test_run_queue_complete_reads_result_from_file(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0010-01", status="in_progress")
    record_dir = data_dir / "records" / "book" / "spell"
    record_dir.mkdir(parents=True)
    (record_dir / "fireball.json").write_text("{}")
    result_path = tmp_path / "result.json"
    result_path.write_text(
        json.dumps(
            {
                "seg_id": "book-p0010-01",
                "records": ["records/book/spell/fireball.json"],
                "no_content": None,
                "notes": "",
            }
        )
    )

    out = io.StringIO()
    exit_code = run_queue_complete("book-p0010-01", str(result_path), data_dir=data_dir, out=out)

    assert exit_code == 0
    segment = _read_segment(data_dir, "book", "book-p0010-01")
    assert segment["pending_records"] == ["records/book/spell/fireball.json"]


def test_run_queue_complete_reads_result_from_stdin(tmp_path: Path, monkeypatch: object) -> None:
    import sys as sys_module

    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0010-01", status="in_progress")
    payload = json.dumps(
        {"seg_id": "book-p0010-01", "records": [], "no_content": {"reason": "art"}}
    )
    sys_module.stdin = io.StringIO(payload)

    exit_code = run_queue_complete("book-p0010-01", "-", data_dir=data_dir, out=io.StringIO())

    assert exit_code == 0
    segment = _read_segment(data_dir, "book", "book-p0010-01")
    assert segment["outcome"] == "no_content"


def test_run_queue_complete_unknown_seg_id_is_an_error(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)

    exit_code = run_queue_complete("does-not-exist", "{}", data_dir=data_dir)

    assert exit_code == 1


def test_run_queue_complete_missing_result_file_is_an_error_not_a_traceback(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0010-01", status="in_progress")

    # No exception should escape -- a clean "error: ..." exit 1 instead.
    exit_code = run_queue_complete(
        "book-p0010-01", str(tmp_path / "does-not-exist.json"), data_dir=data_dir, out=io.StringIO()
    )

    assert exit_code == 1


def test_run_queue_complete_unreadable_result_path_is_an_error(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0010-01", status="in_progress")
    # A directory can't be read as a result file.
    result_dir = tmp_path / "a-directory"
    result_dir.mkdir()

    exit_code = run_queue_complete(
        "book-p0010-01", str(result_dir), data_dir=data_dir, out=io.StringIO()
    )

    assert exit_code == 1


# ---------------------------------------------------------------------------
# queue summary
# ---------------------------------------------------------------------------


def test_run_queue_summary_json(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0010-01")

    out = io.StringIO()
    exit_code = run_queue_summary("book", json_output=True, data_dir=data_dir, out=out)

    assert exit_code == 0
    parsed = json.loads(out.getvalue())
    assert parsed["counts_by_status"]["pending"] == 1


def test_run_queue_summary_text(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0010-01")

    out = io.StringIO()
    exit_code = run_queue_summary("book", json_output=False, data_dir=data_dir, out=out)

    assert exit_code == 0
    assert "pending" in out.getvalue()


# ---------------------------------------------------------------------------
# queue audit
# ---------------------------------------------------------------------------


def test_run_queue_audit_json(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(
        data_dir,
        "book",
        "book-p0010-01",
        status="done",
        outcome="validated",
        records=["records/book/rules_section/class-features.json"],
    )
    record_path = data_dir / "records" / "book" / "rules_section" / "class-features.json"
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.write_text(json.dumps({"extraction": {"segment_id": "book-p0011-01"}}))

    out = io.StringIO()
    exit_code = run_queue_audit("book", fix=False, json_output=True, data_dir=data_dir, out=out)

    assert exit_code == 0
    parsed = json.loads(out.getvalue())
    assert parsed["book_id"] == "book"
    assert len(parsed["stale_claims"]) == 1
    assert "fixed" not in parsed


def test_run_queue_audit_text(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0010-01")

    out = io.StringIO()
    exit_code = run_queue_audit("book", fix=False, json_output=False, data_dir=data_dir, out=out)

    assert exit_code == 0
    assert "book:" in out.getvalue()


def test_run_queue_audit_fix_soft_resets_and_reports_it(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(
        data_dir,
        "book",
        "book-p0010-01",
        status="done",
        outcome="validated",
        records=["records/book/rules_section/class-features.json"],
    )
    record_path = data_dir / "records" / "book" / "rules_section" / "class-features.json"
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.write_text(json.dumps({"extraction": {"segment_id": "book-p0011-01"}}))

    out = io.StringIO()
    exit_code = run_queue_audit("book", fix=True, json_output=False, data_dir=data_dir, out=out)

    assert exit_code == 0
    segment = _read_segment(data_dir, "book", "book-p0010-01")
    assert segment["status"] == "pending"
    assert segment["records"] == []
    output = out.getvalue()
    # Criterion 7: one line per segment touched, plus a final count of
    # segments actually reset (not pruned-only or left-in-human).
    assert "book-p0010-01: reset" in output
    assert "1 segment(s) reset" in output


def test_run_queue_audit_fix_text_reports_pruned_and_reset_counts_separately(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    # A `done` segment with a stale claim -- gets soft-reset.
    _write_segment(
        data_dir,
        "book",
        "book-p0010-01",
        status="done",
        outcome="validated",
        records=["records/book/rules_section/class-features.json"],
    )
    record_path = data_dir / "records" / "book" / "rules_section" / "class-features.json"
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.write_text(json.dumps({"extraction": {"segment_id": "book-p0011-01"}}))

    # A `pending` segment with a dangling claim -- gets pruned only, no
    # reset, and must not be lumped into the reset count.
    _write_segment(
        data_dir,
        "book",
        "book-p0012-01",
        status="pending",
        pending_records=["records/book/spell/missing-spell.json"],
    )

    out = io.StringIO()
    exit_code = run_queue_audit("book", fix=True, json_output=False, data_dir=data_dir, out=out)

    assert exit_code == 0
    output = out.getvalue()
    assert "book-p0010-01: reset" in output
    assert "book-p0012-01: pruned" in output
    assert "records/book/spell/missing-spell.json" in output
    # Only one of the two touched segments was actually reset.
    assert "1 segment(s) reset" in output


def test_run_queue_audit_fix_prints_released_and_moved_counts(tmp_path: Path) -> None:
    """Batch B10c-mand2 criterion 5: `--fix`'s non-JSON output names how
    many claims were released and how many record files were actually
    moved to `superseded/`."""
    data_dir = tmp_path / "data"
    _write_segment(
        data_dir,
        "book",
        "book-p0036-01",
        kind_hint="table",
        status="done",
        outcome="validated",
        superseded_by="book-class-p0034",
        records=["records/book/table/table-3-8-the-druid.json"],
    )
    record_path = data_dir / "records" / "book" / "table" / "table-3-8-the-druid.json"
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.write_text(json.dumps({"extraction": {"segment_id": "book-p0036-01"}}))

    out = io.StringIO()
    exit_code = run_queue_audit("book", fix=True, json_output=False, data_dir=data_dir, out=out)

    assert exit_code == 0
    output = out.getvalue()
    assert "book-p0036-01: released" in output
    assert "1 claim(s) released, 1 record file(s) moved to superseded/" in output

    moved = data_dir / "superseded" / "book" / "table" / "table-3-8-the-druid.json"
    assert moved.is_file()


# ---------------------------------------------------------------------------
# queue reset
# ---------------------------------------------------------------------------


def test_run_queue_reset_returns_segments_to_pending(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(
        data_dir,
        "book",
        "book-p0010-01",
        status="in_progress",
        in_progress_since="2026-01-01T00:05:00+00:00",
    )

    out = io.StringIO()
    exit_code = run_queue_reset(["book-p0010-01"], data_dir=data_dir, out=out)

    assert exit_code == 0
    segment = _read_segment(data_dir, "book", "book-p0010-01")
    assert segment["status"] == "pending"
    assert segment["in_progress_since"] is None


def test_run_queue_reset_unknown_seg_id_reports_error_but_exits_nonzero(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)

    exit_code = run_queue_reset(["does-not-exist"], data_dir=data_dir, out=io.StringIO())

    assert exit_code == 1


def test_run_queue_reset_soft_leaves_records_and_notes_untouched(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    record_dir = data_dir / "records" / "book" / "spell"
    record_dir.mkdir(parents=True)
    (record_dir / "fireball.json").write_text("{}")
    _write_segment(
        data_dir,
        "book",
        "book-p0010-01",
        status="in_progress",
        records=["records/book/spell/fireball.json"],
        notes=["a note"],
    )

    exit_code = run_queue_reset(["book-p0010-01"], data_dir=data_dir, out=io.StringIO())

    assert exit_code == 0
    segment = _read_segment(data_dir, "book", "book-p0010-01")
    assert segment["status"] == "pending"
    assert segment["records"] == ["records/book/spell/fireball.json"]
    assert segment["notes"] == ["a note"]
    assert (record_dir / "fireball.json").exists()


def test_run_queue_reset_hard_clears_state_and_deletes_record_files(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    record_dir = data_dir / "records" / "book" / "spell"
    record_dir.mkdir(parents=True)
    (record_dir / "fireball.json").write_text("{}")
    (record_dir / "icy-bolt.json").write_text("{}")
    _write_segment(
        data_dir,
        "book",
        "book-p0010-01",
        status="in_progress",
        in_progress_since="2026-01-01T00:05:00+00:00",
        attempts=[{"tier": "haiku", "timestamp": "2026-01-01T00:00:00+00:00", "errors": ["x"]}],
        records=["records/book/spell/fireball.json"],
        pending_records=["records/book/spell/icy-bolt.json"],
        notes=["a note"],
        outcome="no_content",
        outcome_reason="art",
    )

    out = io.StringIO()
    exit_code = run_queue_reset(["book-p0010-01"], hard=True, data_dir=data_dir, out=out)

    assert exit_code == 0
    assert "hard" in out.getvalue()
    segment = _read_segment(data_dir, "book", "book-p0010-01")
    assert segment["status"] == "pending"
    assert segment["in_progress_since"] is None
    assert segment["attempts"] == []
    assert segment["pending_records"] == []
    assert segment["records"] == []
    assert segment["notes"] == []
    assert segment["outcome"] is None
    assert segment["outcome_reason"] is None
    assert not (record_dir / "fireball.json").exists()
    assert not (record_dir / "icy-bolt.json").exists()


def test_run_queue_reset_hard_never_deletes_files_outside_the_book_records_dir(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    other_book_dir = data_dir / "records" / "other-book" / "spell"
    other_book_dir.mkdir(parents=True)
    (other_book_dir / "fireball.json").write_text("{}")
    _write_segment(
        data_dir,
        "book",
        "book-p0010-01",
        status="in_progress",
        records=["records/other-book/spell/fireball.json"],
    )

    exit_code = run_queue_reset(["book-p0010-01"], hard=True, data_dir=data_dir, out=io.StringIO())

    assert exit_code == 0
    assert (other_book_dir / "fireball.json").exists()


# ---------------------------------------------------------------------------
# queue reset on a human/ segment (batch B8)
# ---------------------------------------------------------------------------


def _write_human_segment(data_dir: Path, book_id: str, seg_id: str, **overrides: object) -> None:
    defaults: dict[str, Any] = dict(
        seg_id=seg_id,
        book_id=book_id,
        pages=[10],
        printed_pages=[10],
        kind_hint="spell",
        heading="Fireball",
        text="Fireball text.",
        status="human",
        tier="opus",
        outcome="escalation_exhausted",
        created_at="2026-01-01T00:00:00+00:00",
        attempts=[
            {"tier": "haiku", "timestamp": "2026-01-01T00:00:00+00:00", "errors": ["e1"]},
            {"tier": "sonnet", "timestamp": "2026-01-01T00:00:01+00:00", "errors": ["e2"]},
            {"tier": "opus", "timestamp": "2026-01-01T00:00:02+00:00", "errors": ["e3"]},
        ],
    )
    defaults.update(overrides)
    segment = Segment(**defaults)
    human_dir = data_dir / "human" / book_id
    human_dir.mkdir(parents=True, exist_ok=True)
    (human_dir / f"{seg_id}.json").write_text(segment.model_dump_json(indent=2))


def test_run_queue_reset_moves_human_segment_back_to_pending_on_its_tier(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_human_segment(data_dir, "book", "book-p0010-01")

    exit_code = run_queue_reset(["book-p0010-01"], data_dir=data_dir, out=io.StringIO())

    assert exit_code == 0
    human_path = data_dir / "human" / "book" / "book-p0010-01.json"
    assert not human_path.exists()
    segment = _read_segment(data_dir, "book", "book-p0010-01")
    assert segment["status"] == "pending"
    assert segment["tier"] == "opus"  # stays on its current tier
    assert segment["outcome"] is None
    assert len(segment["attempts"]) == 3  # kept


def test_run_queue_reset_hard_on_human_segment_also_resets_tier_to_haiku(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_human_segment(data_dir, "book", "book-p0010-01")

    exit_code = run_queue_reset(["book-p0010-01"], hard=True, data_dir=data_dir, out=io.StringIO())

    assert exit_code == 0
    assert not (data_dir / "human" / "book" / "book-p0010-01.json").exists()
    segment = _read_segment(data_dir, "book", "book-p0010-01")
    assert segment["status"] == "pending"
    assert segment["tier"] == "haiku"
    assert segment["attempts"] == []
    assert segment["outcome"] is None


def test_run_queue_reset_unknown_seg_id_also_checks_human_dir(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)

    exit_code = run_queue_reset(["does-not-exist"], data_dir=data_dir, out=io.StringIO())

    assert exit_code == 1


# ---------------------------------------------------------------------------
# queue next: --tier default omitted, stale-reset stderr note
# ---------------------------------------------------------------------------


def test_run_queue_next_without_tier_uses_lowest_pending_tier(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(data_dir, "book", "book-p0010-01", tier="sonnet")

    out = io.StringIO()
    exit_code = run_queue_next(
        "book",
        limit=5,
        kind="spell",
        json_output=True,
        data_dir=data_dir,
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
        out=out,
    )

    assert exit_code == 0
    parsed = json.loads(out.getvalue())
    assert len(parsed) == 1
    assert parsed[0]["tier"] == "sonnet"
    assert parsed[0]["model"] == "claude-sonnet-5"


def test_run_queue_next_reports_stale_reset_on_stderr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(
        data_dir,
        "book",
        "book-p0010-01",
        status="in_progress",
        in_progress_since="2020-01-01T00:00:00+00:00",
    )

    exit_code = run_queue_next(
        "book",
        limit=5,
        kind="spell",
        data_dir=data_dir,
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
        out=io.StringIO(),
    )

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "reset 1 stale in_progress segment" in captured.err


# ---------------------------------------------------------------------------
# queue run CLI wiring
# ---------------------------------------------------------------------------


def test_cli_help_lists_queue_run_subcommand() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "owlsperch", "queue", "run", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "--dry-run" in result.stdout
    assert "--fixtures" in result.stdout


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------


def test_cli_help_lists_queue_command() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "owlsperch", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "queue" in result.stdout


# ---------------------------------------------------------------------------
# Acceptance criterion 7: dry-run integration test (fake subagent)
# ---------------------------------------------------------------------------


def test_dry_run_next_complete_validate_marks_segment_done(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(data_dir, "book", "book-p0010-01")

    # 1. queue next selects and marks in_progress, renders the prompt.
    next_out = io.StringIO()
    run_queue_next(
        "book",
        tier="haiku",
        limit=5,
        kind="spell",
        json_output=True,
        data_dir=data_dir,
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
        out=next_out,
    )
    selected = json.loads(next_out.getvalue())
    assert len(selected) == 1

    # 2. Fake subagent: writes a valid synthetic record to the expected path.
    record_dir = data_dir / "records" / "book" / "spell"
    record_dir.mkdir(parents=True)
    (record_dir / "fireball.json").write_text(
        json.dumps(_valid_spell_record(seg_id="book-p0010-01"))
    )
    result_path = tmp_path / "result.json"
    result_path.write_text(
        json.dumps(
            {
                "seg_id": "book-p0010-01",
                "records": ["records/book/spell/fireball.json"],
                "no_content": None,
                "notes": "found Fireball",
            }
        )
    )

    # 3. queue complete ingests the result.
    complete_exit = run_queue_complete(
        "book-p0010-01", str(result_path), data_dir=data_dir, out=io.StringIO()
    )
    assert complete_exit == 0
    mid_segment = _read_segment(data_dir, "book", "book-p0010-01")
    assert mid_segment["status"] == "pending"
    assert mid_segment["pending_records"] == ["records/book/spell/fireball.json"]
    assert mid_segment["records"] == []

    # 4. validate promotes the record and marks the segment done.
    validate_exit = run_validate(
        "book", data_dir=data_dir, schemas_dir=_repo_schemas_dir(), out=io.StringIO()
    )
    assert validate_exit == 0

    final_segment = _read_segment(data_dir, "book", "book-p0010-01")
    assert final_segment["status"] == "done"
    assert final_segment["outcome"] == "validated"
    assert final_segment["records"] == ["records/book/spell/fireball.json"]
    assert final_segment["pending_records"] == []
