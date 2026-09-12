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

from owlsperch.queue.runner import (
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
        "schema_version": 1,
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
