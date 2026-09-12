"""Small helpers shared across `owlsperch.queue.*` modules."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from owlsperch.segment.runner import Segment


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def find_segment_path(data_dir: Path, seg_id: str) -> Path | None:
    """Find `segments/<book_id>/<seg_id>.json` for `seg_id` without already
    knowing `book_id` -- `book_id` itself may contain hyphens (e.g.
    `dmg1-building-a-city-we`), so a segment is looked up by its exact
    filename under any book directory rather than by splitting `seg_id`."""
    matches = sorted(data_dir.glob(f"segments/*/{seg_id}.json"))
    return matches[0] if matches else None


def resolve_record_path_under_book(data_dir: Path, book_id: str, rel_path: str) -> Path | None:
    """Resolve `rel_path` (as claimed by a subagent, relative to `data_dir`)
    and return it iff it falls inside `records/<book_id>/` under `data_dir`
    -- guards against a claimed path escaping (via `..` or an absolute path)
    this segment's own book's records directory. Returns `None` if
    `rel_path` resolves outside that directory. Existence on disk is not
    checked here; callers decide what else "valid" requires."""
    records_root = (data_dir / "records" / book_id).resolve()
    candidate = (data_dir / rel_path).resolve()
    try:
        candidate.relative_to(records_root)
    except ValueError:
        return None
    return candidate


def has_attempt_at_tier(segment: Segment, tier: str) -> bool:
    """Whether `segment` already carries an attempt recorded at `tier` --
    such a segment is waiting for a tier escalation rather than being
    (re)selected again at the same tier (see `owlsperch.queue.select`)."""
    return any(isinstance(a, dict) and a.get("tier") == tier for a in segment.attempts)
