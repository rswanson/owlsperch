"""Tests for `owlsperch.queue.summary` (and `owlsperch queue summary`), per
B5 acceptance criterion 5 (counts by status/tier/outcome and by kind_hint,
plus records written) and B8 acceptance criterion 8 (per-tier ladder
pass/escalated counts, `needs_context_retries`, and `human`, which replace
B5's `awaiting_escalation` -- that stopped meaning anything once a failed
attempt always advances the tier instead of stalling).
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
# Per-tier ladder counts (B8): pass/escalated per tier, needs_context_retries,
# human.
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
    )
    defaults.update(overrides)
    segment = Segment(**defaults)
    human_dir = data_dir / "human" / book_id
    human_dir.mkdir(parents=True, exist_ok=True)
    (human_dir / f"{seg_id}.json").write_text(segment.model_dump_json(indent=2))


def test_ladder_counts_pass_and_escalated_per_tier(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    # Passed at haiku.
    _write_segment(
        data_dir,
        "book",
        "book-p0001-01",
        status="done",
        outcome="validated",
        tier="haiku",
    )
    # Escalated from haiku to sonnet (a validation attempt at haiku, now on
    # sonnet).
    _write_segment(
        data_dir,
        "book",
        "book-p0002-01",
        status="pending",
        tier="sonnet",
        attempts=[
            {
                "tier": "haiku",
                "timestamp": "2026-01-01T00:00:00+00:00",
                "errors": ["bad"],
                "kind": "validation",
            }
        ],
    )
    # Passed at sonnet after that escalation.
    _write_segment(
        data_dir,
        "book",
        "book-p0003-01",
        status="done",
        outcome="validated",
        tier="sonnet",
    )

    summary = compute_summary("book", data_dir=data_dir)

    by_tier = {line.tier: line for line in summary.ladder}
    assert by_tier["haiku"].passed == 1
    assert by_tier["haiku"].escalated == 1
    assert by_tier["sonnet"].passed == 1
    assert by_tier["sonnet"].escalated == 0
    assert "haiku: 1 pass / 1 -> sonnet" in summary.render()
    assert "sonnet: 1 pass / 0 -> opus" in summary.render()

    payload = summary.to_json()
    haiku_json = next(t for t in payload["ladder"] if t["tier"] == "haiku")
    assert haiku_json == {"tier": "haiku", "pass": 1, "escalated": 1, "escalated_to": "sonnet"}


def test_first_needs_context_at_tier_is_not_counted_as_escalated_second_is(
    tmp_path: Path,
) -> None:
    # A lone (first) needs_context attempt at haiku -- a same-tier retry,
    # not an escalation.
    data_dir = tmp_path / "data"
    _write_segment(
        data_dir,
        "book",
        "book-p0001-01",
        status="pending",
        tier="haiku",
        attempts=[
            {
                "tier": "haiku",
                "timestamp": "2026-01-01T00:00:00+00:00",
                "errors": ["needs_context: book-p0002-01"],
                "kind": "needs_context",
            }
        ],
    )
    # A second needs_context at haiku -- escalated to sonnet.
    _write_segment(
        data_dir,
        "book",
        "book-p0002-01",
        status="pending",
        tier="sonnet",
        attempts=[
            {
                "tier": "haiku",
                "timestamp": "2026-01-01T00:00:00+00:00",
                "errors": ["needs_context: a"],
                "kind": "needs_context",
            },
            {
                "tier": "haiku",
                "timestamp": "2026-01-01T00:00:01+00:00",
                "errors": ["needs_context: b"],
                "kind": "needs_context",
            },
        ],
    )

    summary = compute_summary("book", data_dir=data_dir)

    by_tier = {line.tier: line for line in summary.ladder}
    assert by_tier["haiku"].escalated == 1  # only the second segment
    assert summary.needs_context_retries == 3  # 1 + 2 attempts total


def test_summary_reports_pending_counts_split_by_kind_and_tier(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(
        data_dir,
        "book",
        "book-p0001-01",
        status="pending",
        kind_hint="rules_section",
        tier="haiku",
    )
    _write_segment(
        data_dir,
        "book",
        "book-p0002-01",
        status="pending",
        kind_hint="rules_section",
        tier="haiku",
    )
    _write_segment(
        data_dir,
        "book",
        "book-p0003-01",
        status="pending",
        kind_hint="table",
        tier="sonnet",
    )
    _write_segment(
        data_dir,
        "book",
        "book-p0004-01",
        status="done",
        outcome="validated",
        kind_hint="spell",
        tier="haiku",
    )
    _write_human_segment(
        data_dir,
        "book",
        "book-p0005-01",
        kind_hint="feat",
        tier="opus",
    )

    summary = compute_summary("book", data_dir=data_dir)

    payload = summary.to_json()
    assert payload["pending_by_kind"] == {"rules_section": 2, "table": 1}
    assert payload["pending_by_tier"] == {"haiku": 2, "sonnet": 1}
    assert summary.counts_by_kind["spell"] == 1
    assert summary.counts_by_kind["feat"] == 1
    assert "pending by kind_hint" in summary.render()
    assert "pending by tier" in summary.render()


def test_human_segments_are_counted_and_included_in_status_and_ladder(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0001-01", status="pending", tier="haiku")
    _write_human_segment(
        data_dir,
        "book",
        "book-p0002-01",
        tier="opus",
        attempts=[
            {
                "tier": "opus",
                "timestamp": "2026-01-01T00:00:00+00:00",
                "errors": ["bad"],
                "kind": "validation",
            }
        ],
    )

    summary = compute_summary("book", data_dir=data_dir)

    assert summary.human == 1
    assert summary.counts_by_status["human"] == 1
    by_tier = {line.tier: line for line in summary.ladder}
    assert by_tier["opus"].escalated == 1
    assert "opus: 0 pass / 1 -> human" in summary.render()
    assert summary.to_json()["human"] == 1
