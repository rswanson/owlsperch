"""`owlsperch queue complete <seg_id> --result <json-file-or-'-'>` -- ingest a
subagent's final JSON reply, per spec 4.5 and B5 acceptance criterion 3.

The subagent's final message must be exactly one JSON object:
`{"seg_id": ..., "records": [...], "no_content": null | {"reason": ...},
"notes": "..."}`. Three outcomes:

- `no_content` is not null: the segment is done, `outcome` is `"no_content"`
  and `outcome_reason` records the reason.
- `no_content` is null and `records` is a list of strings: those paths are
  merged into the segment's `pending_records` (not `records` -- `owlsperch
  validate` promotes a path once the record actually conforms) and `status`
  goes back to `"pending"` so validate can run.
- Anything else (invalid JSON, not an object, missing `records`/`no_content`,
  wrong types, an empty `no_content.reason`): malformed. An attempt with
  error `"malformed_result: <detail>"` is appended and `status` goes back to
  `"pending"` (still on the same tier) -- this is not a validation failure of
  a real record, just a bad subagent reply, so it doesn't count as an
  escalation attempt in the spec 4.5 sense.

Every branch clears `in_progress_since` back to `None`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from owlsperch.fsutil import atomic_write_text
from owlsperch.queue.common import find_segment_path, now_iso
from owlsperch.segment.runner import Segment


class QueueError(Exception):
    """Raised when `seg_id` can't be resolved to a segment file at all."""


@dataclass
class CompleteOutcome:
    seg_id: str
    outcome: str  # "no_content" | "pending_records" | "malformed"
    detail: str


def _parse_result(text: str) -> tuple[dict[str, Any] | None, str | None]:
    """Parse and shape-check a subagent result. Returns `(parsed, None)` on
    success or `(None, "<reason>")` on any malformed input."""
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON: {exc}"

    if not isinstance(parsed, dict):
        return None, "result must be a JSON object"
    if "records" not in parsed or "no_content" not in parsed:
        return None, "result must have 'records' and 'no_content' keys"

    no_content = parsed["no_content"]
    if no_content is not None:
        if not isinstance(no_content, dict) or not isinstance(no_content.get("reason"), str):
            return None, "'no_content' must be null or an object with a string 'reason'"
        if not no_content["reason"].strip():
            return None, "'no_content.reason' must not be empty"
        return parsed, None

    records = parsed["records"]
    if not isinstance(records, list) or not all(isinstance(r, str) for r in records):
        return None, "'records' must be a list of strings when 'no_content' is null"
    return parsed, None


def complete_segment(seg_id: str, result_text: str, *, data_dir: Path) -> CompleteOutcome:
    path = find_segment_path(data_dir, seg_id)
    if path is None:
        raise QueueError(f"unknown segment '{seg_id}' (no segments/*/{seg_id}.json found)")

    segment = Segment.model_validate_json(path.read_text())
    segment.in_progress_since = None

    parsed, error = _parse_result(result_text)
    if error is not None:
        segment.status = "pending"
        error_msg = f"malformed_result: {error}"
        last = segment.attempts[-1] if segment.attempts else None
        already_recorded = isinstance(last, dict) and last.get("errors") == [error_msg]
        if not already_recorded:
            segment.attempts = [
                *segment.attempts,
                {"tier": segment.tier, "timestamp": now_iso(), "errors": [error_msg]},
            ]
        atomic_write_text(path, segment.model_dump_json(indent=2) + "\n")
        return CompleteOutcome(seg_id=seg_id, outcome="malformed", detail=error)

    assert parsed is not None  # `error is None` implies `_parse_result` returned a dict.
    no_content = parsed["no_content"]
    if no_content is not None:
        reason = no_content["reason"]
        segment.status = "done"
        segment.outcome = "no_content"
        segment.outcome_reason = reason
        atomic_write_text(path, segment.model_dump_json(indent=2) + "\n")
        return CompleteOutcome(seg_id=seg_id, outcome="no_content", detail=reason)

    records: list[str] = parsed["records"]
    merged = list(segment.pending_records)
    for record_path in records:
        if record_path not in merged:
            merged.append(record_path)
    segment.pending_records = merged
    segment.status = "pending"
    atomic_write_text(path, segment.model_dump_json(indent=2) + "\n")
    return CompleteOutcome(
        seg_id=seg_id, outcome="pending_records", detail=f"{len(records)} record(s) claimed"
    )
