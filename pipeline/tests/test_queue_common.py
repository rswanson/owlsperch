"""Tests for `owlsperch.queue.common`, per B8 follow-up 3: `move_segment_to_
human` must never leave the segment on disk in both `segments/` and
`human/` at once (write-new-then-delete-old can, if it crashes in between;
`find_segment_path` globs `segments/` first, so a leftover stale copy there
would be found and acted on instead of the real, moved-to-human one).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from owlsperch.queue.common import move_segment_to_human
from owlsperch.segment.runner import Segment


def _segment(**overrides: object) -> Segment:
    defaults: dict[str, Any] = dict(
        seg_id="book-p0010-01",
        book_id="book",
        pages=[10],
        printed_pages=[10],
        kind_hint="spell",
        heading="Fireball",
        text="Fireball text.",
        status="in_progress",
        tier="opus",
        created_at="2026-01-01T00:00:00+00:00",
        attempts=[{"tier": "opus", "timestamp": "2026-01-01T00:00:00+00:00", "errors": ["e1"]}],
    )
    defaults.update(overrides)
    return Segment(**defaults)


def test_move_segment_to_human_leaves_source_gone_and_destination_updated(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    seg_dir = data_dir / "segments" / "book"
    seg_dir.mkdir(parents=True)
    source_path = seg_dir / "book-p0010-01.json"
    segment = _segment()
    source_path.write_text(segment.model_dump_json(indent=2))

    move_segment_to_human(data_dir, source_path, segment, outcome="escalation_exhausted")

    assert not source_path.exists()
    human_path = data_dir / "human" / "book" / "book-p0010-01.json"
    on_disk = json.loads(human_path.read_text())
    assert on_disk["status"] == "human"
    assert on_disk["outcome"] == "escalation_exhausted"
    assert on_disk["in_progress_since"] is None
    # The rest of the segment (attempts, tier, ...) survives the move as-is.
    assert on_disk["tier"] == "opus"
    assert len(on_disk["attempts"]) == 1
    assert on_disk["attempts"][0]["errors"] == ["e1"]


def test_move_segment_to_human_with_proposal_updates_destination_only(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    seg_dir = data_dir / "segments" / "book"
    seg_dir.mkdir(parents=True)
    source_path = seg_dir / "book-p0020-01.json"
    segment = _segment(seg_id="book-p0020-01", tier="haiku", attempts=[])
    source_path.write_text(segment.model_dump_json(indent=2))
    proposal = {"name": "trap", "reason": "no schema for mechanical traps yet"}

    move_segment_to_human(
        data_dir, source_path, segment, outcome="proposed_type", proposal=proposal
    )

    assert not source_path.exists()
    human_path = data_dir / "human" / "book" / "book-p0020-01.json"
    on_disk = json.loads(human_path.read_text())
    assert on_disk["status"] == "human"
    assert on_disk["outcome"] == "proposed_type"
    assert on_disk["proposal"] == proposal
