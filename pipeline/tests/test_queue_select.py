"""Tests for `owlsperch.queue.select` (and `owlsperch queue next` via
`run_queue_next`), per B5 acceptance criterion 1.

Segments are written straight into a temp `$OWLSPERCH_DATA`-shaped
directory, matching the batch's fixture convention (see
`test_segment_runner.py`, `test_validate_runner.py`).
"""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
from typing import Any

import pytest

from owlsperch.queue.select import LockTimeoutError, select_and_mark
from owlsperch.segment.runner import Segment

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _segment(
    seg_id: str,
    *,
    book_id: str = "book",
    pages: list[int] | None = None,
    kind_hint: str = "spell",
    status: str = "pending",
    tier: str = "haiku",
    attempts: list[Any] | None = None,
) -> Segment:
    pages = pages if pages is not None else [10]
    printed_pages: list[int | None] = list(pages)
    return Segment(
        seg_id=seg_id,
        book_id=book_id,
        pages=pages,
        printed_pages=printed_pages,
        kind_hint=kind_hint,  # type: ignore[arg-type]
        heading="Fireball",
        text="Fireball\n\nEvocation Level: Sor/Wiz 3. Deals fire damage.",
        status=status,
        tier=tier,
        attempts=attempts if attempts is not None else [],
        created_at="2026-01-01T00:00:00+00:00",
    )


def _write_segment(data_dir: Path, segment: Segment) -> Path:
    seg_dir = data_dir / "segments" / segment.book_id
    seg_dir.mkdir(parents=True, exist_ok=True)
    path = seg_dir / f"{segment.seg_id}.json"
    path.write_text(segment.model_dump_json(indent=2))
    return path


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


def _read_segment(data_dir: Path, book_id: str, seg_id: str) -> dict[str, Any]:
    path = data_dir / "segments" / book_id / f"{seg_id}.json"
    raw: dict[str, Any] = json.loads(path.read_text())
    return raw


# ---------------------------------------------------------------------------
# Selection / marking
# ---------------------------------------------------------------------------


def test_selects_pending_haiku_spell_segments_and_marks_in_progress(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(data_dir, _segment("book-p0010-01"))

    selected = select_and_mark(
        "book", data_dir=data_dir, tier="haiku", limit=5, kind="spell", manifest_path=manifest_path
    )

    assert len(selected) == 1
    item = selected[0]
    assert item.seg_id == "book-p0010-01"
    assert item.kind_hint == "spell"
    assert item.segment_path == "segments/book/book-p0010-01.json"
    assert Path(item.prompt_path).is_file()

    segment = _read_segment(data_dir, "book", "book-p0010-01")
    assert segment["status"] == "in_progress"
    assert segment["in_progress_since"]


def test_limit_caps_number_selected(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    for i in range(5):
        _write_segment(data_dir, _segment(f"book-p{i:04d}-01"))

    selected = select_and_mark(
        "book", data_dir=data_dir, tier="haiku", limit=2, kind="spell", manifest_path=manifest_path
    )

    assert len(selected) == 2


def test_already_in_progress_segment_is_not_reselected(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(data_dir, _segment("book-p0010-01"))

    first = select_and_mark(
        "book", data_dir=data_dir, tier="haiku", limit=5, kind="spell", manifest_path=manifest_path
    )
    second = select_and_mark(
        "book", data_dir=data_dir, tier="haiku", limit=5, kind="spell", manifest_path=manifest_path
    )

    assert len(first) == 1
    assert len(second) == 0


def test_wrong_tier_is_not_selected(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(data_dir, _segment("book-p0010-01", tier="sonnet"))

    selected = select_and_mark(
        "book", data_dir=data_dir, tier="haiku", limit=5, kind="spell", manifest_path=manifest_path
    )

    assert selected == []


def test_non_pending_status_is_not_selected(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(data_dir, _segment("book-p0010-01", status="done"))

    selected = select_and_mark(
        "book", data_dir=data_dir, tier="haiku", limit=5, kind="spell", manifest_path=manifest_path
    )

    assert selected == []


def test_default_kind_filter_restricts_to_spell_other_kinds_skipped(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(data_dir, _segment("book-p0010-01", kind_hint="spell"))
    _write_segment(data_dir, _segment("book-p0011-01", kind_hint="stat_block"))

    selected = select_and_mark(
        "book", data_dir=data_dir, tier="haiku", limit=5, kind="spell", manifest_path=manifest_path
    )

    assert len(selected) == 1
    assert selected[0].seg_id == "book-p0010-01"

    # The skipped stat_block segment is untouched.
    stat_block = _read_segment(data_dir, "book", "book-p0011-01")
    assert stat_block["status"] == "pending"


def test_kind_option_selects_a_different_kind(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(data_dir, _segment("book-p0011-01", kind_hint="stat_block"))

    selected = select_and_mark(
        "book",
        data_dir=data_dir,
        tier="haiku",
        limit=5,
        kind="stat_block",
        manifest_path=manifest_path,
    )

    assert len(selected) == 1
    assert selected[0].seg_id == "book-p0011-01"


def test_no_segments_dir_returns_empty_list(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    manifest_path = _write_manifest(tmp_path)

    selected = select_and_mark(
        "book", data_dir=data_dir, tier="haiku", limit=5, kind="spell", manifest_path=manifest_path
    )

    assert selected == []


def test_prompt_path_is_written_under_prompts_dir(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(data_dir, _segment("book-p0010-01"))

    selected = select_and_mark(
        "book", data_dir=data_dir, tier="haiku", limit=5, kind="spell", manifest_path=manifest_path
    )

    expected = data_dir / "prompts" / "book" / "book-p0010-01.md"
    assert Path(selected[0].prompt_path) == expected


# ---------------------------------------------------------------------------
# Starvation: a segment with an attempt already at the requested tier waits
# for tier escalation (B8) instead of being reselected forever.
# ---------------------------------------------------------------------------


def test_segment_with_attempt_at_requested_tier_is_not_selected(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(
        data_dir,
        _segment(
            "book-p0010-01",
            attempts=[{"tier": "haiku", "timestamp": "2026-01-01T00:00:00+00:00", "errors": []}],
        ),
    )

    selected = select_and_mark(
        "book", data_dir=data_dir, tier="haiku", limit=5, kind="spell", manifest_path=manifest_path
    )

    assert selected == []
    # Untouched: still pending, not marked in_progress.
    segment = _read_segment(data_dir, "book", "book-p0010-01")
    assert segment["status"] == "pending"


def test_segment_with_attempt_at_a_different_tier_is_still_selected(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(
        data_dir,
        _segment(
            "book-p0010-01",
            attempts=[{"tier": "sonnet", "timestamp": "2026-01-01T00:00:00+00:00", "errors": []}],
        ),
    )

    selected = select_and_mark(
        "book", data_dir=data_dir, tier="haiku", limit=5, kind="spell", manifest_path=manifest_path
    )

    assert len(selected) == 1


# ---------------------------------------------------------------------------
# TOCTOU: the whole select-and-mark operation holds an exclusive flock on
# segments/<book_id>/.queue.lock.
# ---------------------------------------------------------------------------


def test_select_and_mark_times_out_when_lock_already_held(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(data_dir, _segment("book-p0010-01"))

    seg_dir = data_dir / "segments" / "book"
    lock_path = seg_dir / ".queue.lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)

        with pytest.raises(LockTimeoutError):
            select_and_mark(
                "book",
                data_dir=data_dir,
                tier="haiku",
                limit=5,
                kind="spell",
                manifest_path=manifest_path,
                lock_timeout=0.2,
            )

        # Held the whole time -- nothing was marked in_progress.
        segment = _read_segment(data_dir, "book", "book-p0010-01")
        assert segment["status"] == "pending"
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def test_select_and_mark_succeeds_once_lock_is_released(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(data_dir, _segment("book-p0010-01"))

    seg_dir = data_dir / "segments" / "book"
    seg_dir.mkdir(parents=True, exist_ok=True)
    lock_path = seg_dir / ".queue.lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)
    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)

    selected = select_and_mark(
        "book",
        data_dir=data_dir,
        tier="haiku",
        limit=5,
        kind="spell",
        manifest_path=manifest_path,
        lock_timeout=2.0,
    )

    assert len(selected) == 1
