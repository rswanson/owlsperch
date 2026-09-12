"""Tests for `owlsperch.queue.complete` (and `owlsperch queue complete`), per
B5 acceptance criterion 3: ingest the subagent's final JSON, covering the
`no_content`, `records` (-> `pending_records`), and malformed-JSON branches.
"""

from __future__ import annotations

import json
from pathlib import Path

from owlsperch.queue.complete import QueueError, complete_segment
from owlsperch.segment.runner import Segment


def _write_segment(data_dir: Path, book_id: str, seg_id: str, **overrides: object) -> Path:
    defaults: dict[str, object] = dict(
        seg_id=seg_id,
        book_id=book_id,
        pages=[10],
        printed_pages=[10],
        kind_hint="spell",
        heading="Fireball",
        text="Fireball\n\nEvocation Level: Sor/Wiz 3.",
        status="in_progress",
        tier="haiku",
        created_at="2026-01-01T00:00:00+00:00",
        in_progress_since="2026-01-01T00:05:00+00:00",
    )
    defaults.update(overrides)
    segment = Segment(**defaults)  # type: ignore[arg-type]
    seg_dir = data_dir / "segments" / book_id
    seg_dir.mkdir(parents=True, exist_ok=True)
    path = seg_dir / f"{seg_id}.json"
    path.write_text(segment.model_dump_json(indent=2))
    return path


def _read_segment(data_dir: Path, book_id: str, seg_id: str) -> dict[str, object]:
    path = data_dir / "segments" / book_id / f"{seg_id}.json"
    raw: dict[str, object] = json.loads(path.read_text())
    return raw


# ---------------------------------------------------------------------------
# records -> pending_records
# ---------------------------------------------------------------------------


def test_records_result_sets_pending_records_and_status_pending(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0010-01")
    result = json.dumps(
        {
            "seg_id": "book-p0010-01",
            "records": ["records/book/spell/fireball.json"],
            "no_content": None,
            "notes": "found one spell",
        }
    )

    outcome = complete_segment("book-p0010-01", result, data_dir=data_dir)

    assert outcome.outcome == "pending_records"
    segment = _read_segment(data_dir, "book", "book-p0010-01")
    assert segment["status"] == "pending"
    assert segment["pending_records"] == ["records/book/spell/fireball.json"]
    assert segment["in_progress_since"] is None


def test_records_result_merges_without_duplicating(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(
        data_dir,
        "book",
        "book-p0010-01",
        pending_records=["records/book/spell/fireball.json"],
    )
    result = json.dumps(
        {
            "seg_id": "book-p0010-01",
            "records": ["records/book/spell/fireball.json", "records/book/spell/icy-bolt.json"],
            "no_content": None,
            "notes": "",
        }
    )

    complete_segment("book-p0010-01", result, data_dir=data_dir)

    segment = _read_segment(data_dir, "book", "book-p0010-01")
    assert segment["pending_records"] == [
        "records/book/spell/fireball.json",
        "records/book/spell/icy-bolt.json",
    ]


# ---------------------------------------------------------------------------
# no_content
# ---------------------------------------------------------------------------


def test_no_content_marks_segment_done_with_reason(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0010-01")
    result = json.dumps(
        {
            "seg_id": "book-p0010-01",
            "records": [],
            "no_content": {"reason": "table of contents entry, no rule text"},
            "notes": "",
        }
    )

    outcome = complete_segment("book-p0010-01", result, data_dir=data_dir)

    assert outcome.outcome == "no_content"
    segment = _read_segment(data_dir, "book", "book-p0010-01")
    assert segment["status"] == "done"
    assert segment["outcome"] == "no_content"
    assert segment["outcome_reason"] == "table of contents entry, no rule text"
    assert segment["in_progress_since"] is None


# ---------------------------------------------------------------------------
# malformed
# ---------------------------------------------------------------------------


def test_invalid_json_is_malformed_result(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0010-01")

    outcome = complete_segment("book-p0010-01", "{not json", data_dir=data_dir)

    assert outcome.outcome == "malformed"
    segment = _read_segment(data_dir, "book", "book-p0010-01")
    assert segment["status"] == "pending"
    assert segment["tier"] == "haiku"
    assert len(segment["attempts"]) == 1
    assert "malformed_result" in segment["attempts"][0]["errors"][0]  # type: ignore[index]


def test_missing_required_keys_is_malformed_result(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0010-01")

    outcome = complete_segment("book-p0010-01", json.dumps({"notes": "oops"}), data_dir=data_dir)

    assert outcome.outcome == "malformed"
    segment = _read_segment(data_dir, "book", "book-p0010-01")
    assert segment["status"] == "pending"


def test_non_object_json_is_malformed_result(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0010-01")

    outcome = complete_segment("book-p0010-01", json.dumps([1, 2, 3]), data_dir=data_dir)

    assert outcome.outcome == "malformed"


def test_records_not_a_list_of_strings_is_malformed_result(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0010-01")
    result = json.dumps({"seg_id": "book-p0010-01", "records": [1, 2], "no_content": None})

    outcome = complete_segment("book-p0010-01", result, data_dir=data_dir)

    assert outcome.outcome == "malformed"


def test_no_content_without_reason_is_malformed_result(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0010-01")
    result = json.dumps({"seg_id": "book-p0010-01", "records": [], "no_content": {}})

    outcome = complete_segment("book-p0010-01", result, data_dir=data_dir)

    assert outcome.outcome == "malformed"


def test_malformed_result_does_not_add_duplicate_attempts_on_rerun_of_same_error(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0010-01")

    complete_segment("book-p0010-01", "{not json", data_dir=data_dir)
    complete_segment("book-p0010-01", "{not json", data_dir=data_dir)

    segment = _read_segment(data_dir, "book", "book-p0010-01")
    assert len(segment["attempts"]) == 1  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Segment lookup (hyphenated book_ids, unknown seg_id)
# ---------------------------------------------------------------------------


def test_finds_segment_for_hyphenated_book_id(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "dmg1-building-a-city-we", "dmg1-building-a-city-we-p0001-01")
    result = json.dumps({"seg_id": "x", "records": [], "no_content": {"reason": "art"}})

    outcome = complete_segment(
        "dmg1-building-a-city-we-p0001-01", result, data_dir=data_dir
    )

    assert outcome.outcome == "no_content"


def test_unknown_seg_id_raises_queue_error(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)

    try:
        complete_segment("does-not-exist", "{}", data_dir=data_dir)
        raise AssertionError("expected QueueError")
    except QueueError:
        pass
