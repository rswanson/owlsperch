"""Tests for `owlsperch.queue.summary` (and `owlsperch queue summary`), per
B5 acceptance criterion 5: counts by status/tier/outcome and by kind_hint,
plus records written.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from owlsperch.queue.summary import compute_summary
from owlsperch.segment.runner import Segment


def _write_segment(data_dir: Path, book_id: str, seg_id: str, **overrides: object) -> None:
    defaults: dict[str, Any] = dict(
        seg_id=seg_id,
        book_id=book_id,
        pages=[10],
        printed_pages=[10],
        kind_hint="spell",
        heading="Fireball",
        text="Fireball text.",
        status="pending",
        tier="haiku",
        created_at="2026-01-01T00:00:00+00:00",
    )
    defaults.update(overrides)
    segment = Segment(**defaults)
    seg_dir = data_dir / "segments" / book_id
    seg_dir.mkdir(parents=True, exist_ok=True)
    (seg_dir / f"{seg_id}.json").write_text(segment.model_dump_json(indent=2))


def _write_record(data_dir: Path, book_id: str, type_dir: str, slug: str) -> None:
    out_dir = data_dir / "records" / book_id / type_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{slug}.json").write_text(json.dumps({"slug": slug}))


def test_counts_by_status_tier_outcome_and_kind(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0001-01", status="pending", kind_hint="spell")
    _write_segment(
        data_dir,
        "book",
        "book-p0002-01",
        status="done",
        outcome="validated",
        kind_hint="spell",
    )
    _write_segment(
        data_dir,
        "book",
        "book-p0003-01",
        status="done",
        outcome="no_content",
        kind_hint="stat_block",
    )
    _write_segment(data_dir, "book", "book-p0004-01", status="in_progress", kind_hint="feat")

    summary = compute_summary("book", data_dir=data_dir)

    assert summary.counts_by_status["pending"] == 1
    assert summary.counts_by_status["done"] == 2
    assert summary.counts_by_status["in_progress"] == 1
    assert summary.counts_by_tier["haiku"] == 4
    assert summary.counts_by_outcome["validated"] == 1
    assert summary.counts_by_outcome["no_content"] == 1
    assert summary.counts_by_kind["spell"] == 2
    assert summary.counts_by_kind["stat_block"] == 1
    assert summary.counts_by_kind["feat"] == 1


def test_records_written_counts_files_under_records_dir(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_record(data_dir, "book", "spell", "fireball")
    _write_record(data_dir, "book", "spell", "magic-missile")

    summary = compute_summary("book", data_dir=data_dir)

    assert summary.records_written == 2


def test_no_segments_or_records_gives_zero_counts(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)

    summary = compute_summary("book", data_dir=data_dir)

    assert summary.counts_by_status == {}
    assert summary.records_written == 0


def test_render_and_to_json(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0001-01", status="pending")

    summary = compute_summary("book", data_dir=data_dir)

    assert "pending" in summary.render()
    payload = summary.to_json()
    assert payload["counts_by_status"]["pending"] == 1


# ---------------------------------------------------------------------------
# awaiting_escalation: pending segments with an attempt at their current tier
# ---------------------------------------------------------------------------


def test_awaiting_escalation_counts_pending_segments_with_attempt_at_current_tier(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    _write_segment(
        data_dir,
        "book",
        "book-p0001-01",
        status="pending",
        tier="haiku",
        attempts=[{"tier": "haiku", "timestamp": "2026-01-01T00:00:00+00:00", "errors": []}],
    )
    # Pending but no attempt yet -- not awaiting escalation.
    _write_segment(data_dir, "book", "book-p0002-01", status="pending", tier="haiku")
    # An attempt, but at a different (earlier) tier -- not awaiting escalation.
    _write_segment(
        data_dir,
        "book",
        "book-p0003-01",
        status="pending",
        tier="sonnet",
        attempts=[{"tier": "haiku", "timestamp": "2026-01-01T00:00:00+00:00", "errors": []}],
    )
    # done, not pending -- not counted even though it has an attempt.
    _write_segment(
        data_dir,
        "book",
        "book-p0004-01",
        status="done",
        tier="haiku",
        outcome="validated",
        attempts=[{"tier": "haiku", "timestamp": "2026-01-01T00:00:00+00:00", "errors": []}],
    )

    summary = compute_summary("book", data_dir=data_dir)

    assert summary.awaiting_escalation == 1
    assert "awaiting_escalation: 1" in summary.render()
    assert summary.to_json()["awaiting_escalation"] == 1
