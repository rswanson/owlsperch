"""`owlsperch queue summary <book_id>` -- progress counts, per spec 4.5 and
B5 acceptance criterion 5 (batch B8 replaces `awaiting_escalation`, which
stopped meaning anything once tier escalation landed, with per-tier ladder
counts, plus `needs_context_retries` and `human`; a B10 follow-up adds
`pending_by_kind`/`pending_by_tier`, the pending-only breakdown of
`counts_by_kind`/`counts_by_tier`, so a wave can be planned from this
read-only command instead of `queue next`, which selects and mutates)."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from owlsperch.queue.ladder import TIERS, attempt_kind_of, next_tier
from owlsperch.segment.runner import Segment


def _load_segments(seg_dir: Path, book_id: str) -> list[Segment]:
    if not seg_dir.is_dir():
        return []
    paths = sorted(seg_dir.glob(f"{book_id}-*.json"))
    return [Segment.model_validate_json(p.read_text()) for p in paths]


def _escalated_away_from_tier(segment: Segment, tier: str) -> bool:
    """Whether `segment` carries an attempt at `tier` that moved it on to
    the next rung (or to `human/`): any validation/malformed attempt at that
    tier, or a *second* `needs_context` attempt there (the first one is a
    same-tier retry, not an escalation)."""
    attempts_at_tier = [
        a for a in segment.attempts if isinstance(a, dict) and a.get("tier") == tier
    ]
    if any(attempt_kind_of(a) in ("validation", "malformed") for a in attempts_at_tier):
        return True
    needs_context_count = sum(1 for a in attempts_at_tier if attempt_kind_of(a) == "needs_context")
    return needs_context_count >= 2


@dataclass
class TierLine:
    tier: str
    passed: int = 0
    escalated: int = 0

    @property
    def next_label(self) -> str:
        return next_tier(self.tier) or "human"

    def render(self) -> str:
        return f"  {self.tier}: {self.passed} pass / {self.escalated} -> {self.next_label}"

    def to_json(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "pass": self.passed,
            "escalated": self.escalated,
            "escalated_to": self.next_label,
        }


@dataclass
class QueueSummary:
    book_id: str
    counts_by_status: dict[str, int] = field(default_factory=dict)
    counts_by_tier: dict[str, int] = field(default_factory=dict)
    counts_by_outcome: dict[str, int] = field(default_factory=dict)
    counts_by_kind: dict[str, int] = field(default_factory=dict)
    #: Counts of segments still `pending`, split by kind_hint and by tier
    #: -- i.e. the work that is LEFT, as opposed to `counts_by_kind`'s
    #: done+pending total. Added so a wave can be planned from `queue
    #: summary` (read-only) instead of `queue next` (which selects and
    #: marks segments `in_progress`).
    pending_by_kind: dict[str, int] = field(default_factory=dict)
    pending_by_tier: dict[str, int] = field(default_factory=dict)
    records_written: int = 0
    #: Per-tier pass/escalated counts, in `owlsperch.queue.ladder.TIERS`
    #: order (batch B8).
    ladder: list[TierLine] = field(default_factory=list)
    #: Total `needs_context` attempts recorded across every segment (both
    #: the first, same-tier-retry one and any second, escalating one).
    needs_context_retries: int = 0
    #: Segments currently sitting in `human/<book_id>/` (opus exhausted, or
    #: a `proposed_type`).
    human: int = 0

    def render(self) -> str:
        def _line(label: str, counts: dict[str, int]) -> str:
            if not counts:
                return f"{label}: (none)"
            body = ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
            return f"{label}: {body}"

        lines = [
            f"{self.book_id}:",
            _line("  by status", self.counts_by_status),
            _line("  by tier", self.counts_by_tier),
            _line("  by outcome", self.counts_by_outcome),
            _line("  by kind_hint", self.counts_by_kind),
            _line("  pending by kind_hint", self.pending_by_kind),
            _line("  pending by tier", self.pending_by_tier),
            f"  records written: {self.records_written}",
            "  ladder:",
            *[tier_line.render() for tier_line in self.ladder],
            f"  needs_context_retries: {self.needs_context_retries}",
            f"  human: {self.human}",
        ]
        return "\n".join(lines)

    def to_json(self) -> dict[str, Any]:
        return {
            "book_id": self.book_id,
            "counts_by_status": self.counts_by_status,
            "counts_by_tier": self.counts_by_tier,
            "counts_by_outcome": self.counts_by_outcome,
            "counts_by_kind": self.counts_by_kind,
            "pending_by_kind": self.pending_by_kind,
            "pending_by_tier": self.pending_by_tier,
            "records_written": self.records_written,
            "ladder": [tier_line.to_json() for tier_line in self.ladder],
            "needs_context_retries": self.needs_context_retries,
            "human": self.human,
        }


def compute_summary(book_id: str, *, data_dir: Path) -> QueueSummary:
    active_segments = _load_segments(data_dir / "segments" / book_id, book_id)
    human_segments = _load_segments(data_dir / "human" / book_id, book_id)
    segments = [*active_segments, *human_segments]

    records_dir = data_dir / "records" / book_id
    records_written = len(list(records_dir.glob("*/*.json"))) if records_dir.is_dir() else 0

    ladder: list[TierLine] = []
    for tier in TIERS:
        passed = sum(
            1
            for s in segments
            if s.tier == tier and s.status == "done" and s.outcome == "validated"
        )
        escalated = sum(1 for s in segments if _escalated_away_from_tier(s, tier))
        ladder.append(TierLine(tier=tier, passed=passed, escalated=escalated))

    needs_context_retries = sum(
        1
        for s in segments
        for a in s.attempts
        if isinstance(a, dict) and attempt_kind_of(a) == "needs_context"
    )

    pending = [s for s in segments if s.status == "pending"]

    return QueueSummary(
        book_id=book_id,
        counts_by_status=dict(Counter(s.status for s in segments)),
        counts_by_tier=dict(Counter(s.tier for s in segments)),
        counts_by_outcome=dict(Counter(s.outcome for s in segments if s.outcome is not None)),
        counts_by_kind=dict(Counter(s.kind_hint for s in segments)),
        pending_by_kind=dict(Counter(s.kind_hint for s in pending)),
        pending_by_tier=dict(Counter(s.tier for s in pending)),
        records_written=records_written,
        ladder=ladder,
        needs_context_retries=needs_context_retries,
        human=len(human_segments),
    )
