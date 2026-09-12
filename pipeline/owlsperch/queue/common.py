"""Small helpers shared across `owlsperch.queue.*` modules."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def find_segment_path(data_dir: Path, seg_id: str) -> Path | None:
    """Find `segments/<book_id>/<seg_id>.json` for `seg_id` without already
    knowing `book_id` -- `book_id` itself may contain hyphens (e.g.
    `dmg1-building-a-city-we`), so a segment is looked up by its exact
    filename under any book directory rather than by splitting `seg_id`."""
    matches = sorted(data_dir.glob(f"segments/*/{seg_id}.json"))
    return matches[0] if matches else None
