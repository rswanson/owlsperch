"""`owlsperch queue summary <book_id>` -- progress counts, per spec 4.5 and
B5 acceptance criterion 5."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from owlsperch.queue.common import has_attempt_at_tier
from owlsperch.segment.runner import Segment


@dataclass
class QueueSummary:
    book_id: str
    counts_by_status: dict[str, int] = field(default_factory=dict)
    counts_by_tier: dict[str, int] = field(default_factory=dict)
    counts_by_outcome: dict[str, int] = field(default_factory=dict)
    counts_by_kind: dict[str, int] = field(default_factory=dict)
    records_written: int = 0
    #: Pending segments that already have an attempt recorded at their
    #: current tier -- `owlsperch.queue.select.select_and_mark` never
    #: (re)selects these; they're waiting for B8's tier escalation, not
    #: stuck. Surfaced separately so an operator can tell that from "queue
    #: next returned [] but summary still shows pending segments" alone.
    awaiting_escalation: int = 0

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
            f"  records written: {self.records_written}",
            f"  awaiting_escalation: {self.awaiting_escalation}",
        ]
        return "\n".join(lines)

    def to_json(self) -> dict[str, Any]:
        return {
            "book_id": self.book_id,
            "counts_by_status": self.counts_by_status,
            "counts_by_tier": self.counts_by_tier,
            "counts_by_outcome": self.counts_by_outcome,
            "counts_by_kind": self.counts_by_kind,
            "records_written": self.records_written,
            "awaiting_escalation": self.awaiting_escalation,
        }


def compute_summary(book_id: str, *, data_dir: Path) -> QueueSummary:
    seg_dir = data_dir / "segments" / book_id
    segments: list[Segment] = []
    if seg_dir.is_dir():
        segments = [
            Segment.model_validate_json(p.read_text())
            for p in sorted(seg_dir.glob(f"{book_id}-*.json"))
        ]

    records_dir = data_dir / "records" / book_id
    records_written = len(list(records_dir.glob("*/*.json"))) if records_dir.is_dir() else 0

    awaiting_escalation = sum(
        1 for s in segments if s.status == "pending" and has_attempt_at_tier(s, s.tier)
    )

    return QueueSummary(
        book_id=book_id,
        counts_by_status=dict(Counter(s.status for s in segments)),
        counts_by_tier=dict(Counter(s.tier for s in segments)),
        counts_by_outcome=dict(Counter(s.outcome for s in segments if s.outcome is not None)),
        counts_by_kind=dict(Counter(s.kind_hint for s in segments)),
        records_written=records_written,
        awaiting_escalation=awaiting_escalation,
    )
