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
from datetime import UTC, datetime, timedelta
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


def test_default_kind_none_selects_every_registered_kind_never_stat_block(
    tmp_path: Path,
) -> None:
    """B10 criterion 6: `kind=None` (the new default) resolves to every
    kind_hint with a registered schema (spell, feat, table, rules_section)
    -- a book with pending spell + feat + stat_block segments selects the
    spell and feat ones and never the stat_block one (no schema registered
    for it), so it isn't burned as `no_content`."""
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(data_dir, _segment("book-p0010-01", kind_hint="spell"))
    _write_segment(data_dir, _segment("book-p0011-01", kind_hint="feat"))
    _write_segment(data_dir, _segment("book-p0012-01", kind_hint="stat_block"))

    selected = select_and_mark(
        "book", data_dir=data_dir, tier="haiku", limit=5, manifest_path=manifest_path
    )

    selected_ids = {item.seg_id for item in selected}
    assert selected_ids == {"book-p0010-01", "book-p0011-01"}

    stat_block = _read_segment(data_dir, "book", "book-p0012-01")
    assert stat_block["status"] == "pending"


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


def test_selected_segment_includes_tier_and_model(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(data_dir, _segment("book-p0010-01", tier="sonnet"))

    selected = select_and_mark(
        "book", data_dir=data_dir, tier="sonnet", limit=5, kind="spell", manifest_path=manifest_path
    )

    assert len(selected) == 1
    assert selected[0].tier == "sonnet"
    assert selected[0].model == "claude-sonnet-5"
    payload = selected[0].to_json()
    assert payload["tier"] == "sonnet"
    assert payload["model"] == "claude-sonnet-5"


def test_explicit_model_overrides_tier_default(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(data_dir, _segment("book-p0010-01"))

    selected = select_and_mark(
        "book",
        data_dir=data_dir,
        tier="haiku",
        limit=5,
        kind="spell",
        manifest_path=manifest_path,
        model="custom-model",
    )

    assert selected[0].model == "custom-model"


# ---------------------------------------------------------------------------
# Criterion 6: `--tier` default is the lowest tier with pending work;
# an explicit `--tier` restricts to only that tier.
# ---------------------------------------------------------------------------


def test_default_tier_picks_lowest_tier_with_pending_work(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(data_dir, _segment("book-p0010-01", tier="sonnet"))
    _write_segment(data_dir, _segment("book-p0011-01", tier="opus"))

    selected = select_and_mark(
        "book", data_dir=data_dir, tier=None, limit=5, kind="spell", manifest_path=manifest_path
    )

    assert len(selected) == 1
    assert selected[0].seg_id == "book-p0010-01"
    assert selected[0].tier == "sonnet"


def test_default_tier_prefers_haiku_when_available(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(data_dir, _segment("book-p0010-01", tier="haiku"))
    _write_segment(data_dir, _segment("book-p0011-01", tier="opus"))

    selected = select_and_mark(
        "book", data_dir=data_dir, tier=None, limit=5, kind="spell", manifest_path=manifest_path
    )

    assert len(selected) == 1
    assert selected[0].seg_id == "book-p0010-01"


def test_default_tier_returns_empty_when_nothing_pending(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(data_dir, _segment("book-p0010-01", status="done"))

    selected = select_and_mark(
        "book", data_dir=data_dir, tier=None, limit=5, kind="spell", manifest_path=manifest_path
    )

    assert selected == []


def test_explicit_tier_opus_only_selects_opus_segments(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(data_dir, _segment("book-p0010-01", tier="haiku"))
    _write_segment(data_dir, _segment("book-p0011-01", tier="opus"))

    selected = select_and_mark(
        "book", data_dir=data_dir, tier="opus", limit=5, kind="spell", manifest_path=manifest_path
    )

    assert len(selected) == 1
    assert selected[0].seg_id == "book-p0011-01"
    # The haiku segment is untouched.
    haiku_segment = _read_segment(data_dir, "book", "book-p0010-01")
    assert haiku_segment["status"] == "pending"


# ---------------------------------------------------------------------------
# Criterion 5: segments in-progress for >60 minutes are reset to pending on
# the next `queue next`.
# ---------------------------------------------------------------------------


def test_stale_in_progress_segment_is_reset_and_becomes_selectable(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    stale_since = (datetime.now(UTC) - timedelta(minutes=61)).isoformat()
    _write_segment(
        data_dir,
        _segment("book-p0010-01", status="in_progress"),
    )
    # `_segment` doesn't take in_progress_since -- patch it directly.
    seg_path = data_dir / "segments" / "book" / "book-p0010-01.json"
    raw = json.loads(seg_path.read_text())
    raw["in_progress_since"] = stale_since
    seg_path.write_text(json.dumps(raw))

    selected = select_and_mark(
        "book", data_dir=data_dir, tier="haiku", limit=5, kind="spell", manifest_path=manifest_path
    )

    assert len(selected) == 1
    assert selected[0].seg_id == "book-p0010-01"


def test_fresh_in_progress_segment_is_not_reset(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    fresh_since = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    _write_segment(data_dir, _segment("book-p0010-01", status="in_progress"))
    seg_path = data_dir / "segments" / "book" / "book-p0010-01.json"
    raw = json.loads(seg_path.read_text())
    raw["in_progress_since"] = fresh_since
    seg_path.write_text(json.dumps(raw))

    selected = select_and_mark(
        "book", data_dir=data_dir, tier="haiku", limit=5, kind="spell", manifest_path=manifest_path
    )

    assert selected == []
    segment = _read_segment(data_dir, "book", "book-p0010-01")
    assert segment["status"] == "in_progress"
    assert segment["in_progress_since"] == fresh_since


def test_stale_reset_count_is_reported_via_stats(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    stale_since = (datetime.now(UTC) - timedelta(minutes=90)).isoformat()
    _write_segment(data_dir, _segment("book-p0010-01", status="in_progress"))
    seg_path = data_dir / "segments" / "book" / "book-p0010-01.json"
    raw = json.loads(seg_path.read_text())
    raw["in_progress_since"] = stale_since
    seg_path.write_text(json.dumps(raw))

    stats: dict[str, int] = {}
    select_and_mark(
        "book",
        data_dir=data_dir,
        tier="haiku",
        limit=5,
        kind="spell",
        manifest_path=manifest_path,
        stats=stats,
    )

    assert stats["stale_reset"] == 1


# ---------------------------------------------------------------------------
# Lazy escalation: a pending segment with a failed/malformed attempt already
# recorded at its own current tier (legacy data, or the single needs_context
# retry) self-heals instead of being skipped forever.
# ---------------------------------------------------------------------------


def test_legacy_pending_segment_with_attempt_at_own_tier_is_lazily_escalated(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(
        data_dir,
        _segment(
            "book-p0010-01",
            attempts=[{"tier": "haiku", "timestamp": "2026-01-01T00:00:00+00:00", "errors": []}],
        ),
    )

    # Explicit haiku: no longer matches (it was escalated to sonnet first).
    at_haiku = select_and_mark(
        "book", data_dir=data_dir, tier="haiku", limit=5, kind="spell", manifest_path=manifest_path
    )
    assert at_haiku == []
    segment = _read_segment(data_dir, "book", "book-p0010-01")
    assert segment["status"] == "pending"
    assert segment["tier"] == "sonnet"

    at_sonnet = select_and_mark(
        "book", data_dir=data_dir, tier="sonnet", limit=5, kind="spell", manifest_path=manifest_path
    )
    assert len(at_sonnet) == 1
    assert at_sonnet[0].seg_id == "book-p0010-01"


def test_lone_needs_context_attempt_at_own_tier_is_still_selected_at_same_tier(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(
        data_dir,
        _segment(
            "book-p0010-01",
            attempts=[
                {
                    "tier": "haiku",
                    "timestamp": "2026-01-01T00:00:00+00:00",
                    "errors": ["needs_context: book-p0011-01"],
                    "kind": "needs_context",
                }
            ],
        ),
    )

    selected = select_and_mark(
        "book", data_dir=data_dir, tier="haiku", limit=5, kind="spell", manifest_path=manifest_path
    )

    assert len(selected) == 1
    assert selected[0].seg_id == "book-p0010-01"
    segment = _read_segment(data_dir, "book", "book-p0010-01")
    assert segment["tier"] == "haiku"


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
