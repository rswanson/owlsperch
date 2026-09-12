"""`owlsperch queue complete <seg_id> --result <json-file-or-'-'>` -- ingest a
subagent's final JSON reply, per spec 4.5 and B5 acceptance criterion 3.

The subagent's final message must be exactly one JSON object:
`{"seg_id": ..., "records": [...], "no_content": null | {"reason": ...},
"notes": [...] | "..." | omitted}` -- tolerantly extracted first (B5
follow-up 1): whitespace is stripped, a Markdown code fence's content is
used if present, otherwise the substring from the first `{` to the last `}`
is used, and only a `json.loads` failure on *that* is a malformed reply (see
`_extract_candidate_json`) -- two of four real haiku replies in the B5 trial
came back fenced and were wrongly rejected before this. Three outcomes:

- `no_content` is not null: the segment is done, `outcome` is `"no_content"`
  and `outcome_reason` records the reason.
- `no_content` is null and `records` is a list of strings: each path is
  checked to (a) resolve inside `records/<book_id>/` under `$OWLSPERCH_DATA`
  (this segment's own book, not any other) and (b) actually exist on disk.
  A path failing either check is never merged into `pending_records`;
  instead an attempt is appended with error `"missing_record_path: <path>"`
  for each such path. Every path that passes both checks has its own
  `extraction` overwritten with the authoritative `{tier, model, segment_id,
  timestamp}` (B5 follow-up 3 -- see `_overwrite_extraction`; `model` is
  whatever `owlsperch queue next` recorded on the segment at selection time,
  not whatever placeholder the subagent wrote), then is merged into the
  segment's `pending_records` (not `records` -- `owlsperch validate`
  promotes a path once the record actually conforms), and `status` goes
  back to `"pending"` so validate can run.
- Anything else (invalid JSON, not an object, missing `records`/`no_content`/
  `seg_id`, a `seg_id` that doesn't match the segment being completed, wrong
  types, an empty `no_content.reason`): malformed. An attempt with error
  `"malformed_result: <detail>"` is appended and `status` goes back to
  `"pending"` (still on the same tier) -- this is not a validation failure of
  a real record, just a bad subagent reply, so it doesn't count as an
  escalation attempt in the spec 4.5 sense.

`notes` is optional and, when present, is stored (merged, deduplicated) onto
the segment's own `notes` field (a list of strings) -- accepted as either a
single string or a list of strings for the subagent's convenience, always
normalized to a list on the segment.

Every branch clears `in_progress_since` back to `None`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from owlsperch.fsutil import atomic_write_text
from owlsperch.queue.common import find_segment_path, now_iso
from owlsperch.queue.prompt import DEFAULT_MODEL
from owlsperch.segment.runner import Segment


class QueueError(Exception):
    """Raised when `seg_id` can't be resolved to a segment file at all."""


@dataclass
class CompleteOutcome:
    seg_id: str
    outcome: str  # "no_content" | "pending_records" | "malformed"
    detail: str


#: Matches a Markdown-fenced block (```` ```json ... ``` ```` or plain
#: ```` ``` ... ``` ````), non-greedy so the first closing fence wins.
_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)```", re.DOTALL)


def _extract_candidate_json(text: str) -> str:
    """Best-effort extraction of the JSON object text out of a subagent's
    final message, tolerating the two shapes real replies actually came
    back in during the B5 trial: wrapped in a Markdown code fence, or
    preceded (and/or followed) by prose. Stripped whitespace first; if a
    fenced block is present its content is used; otherwise the substring
    from the first `{` to the last `}` is used; failing that, the
    whole (stripped) text is returned as-is so `json.loads` produces a
    normal decode error for it. `_parse_result` only ever reports
    `malformed_result` once `json.loads` on this candidate itself fails."""
    stripped = text.strip()

    fence_match = _FENCE_RE.search(stripped)
    if fence_match:
        return fence_match.group(1).strip()

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end != -1 and end > start:
        return stripped[start : end + 1]

    return stripped


def _parse_result(text: str, seg_id: str) -> tuple[dict[str, Any] | None, str | None]:
    """Parse and shape-check a subagent result against the segment it's
    completing. Returns `(parsed, None)` on success or `(None, "<reason>")`
    on any malformed input."""
    try:
        parsed = json.loads(_extract_candidate_json(text))
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON: {exc}"

    if not isinstance(parsed, dict):
        return None, "result must be a JSON object"
    if "records" not in parsed or "no_content" not in parsed:
        return None, "result must have 'records' and 'no_content' keys"

    if "seg_id" not in parsed:
        return None, "result must have a 'seg_id' key"
    result_seg_id = parsed["seg_id"]
    if not isinstance(result_seg_id, str):
        return None, "'seg_id' must be a string"
    if result_seg_id != seg_id:
        return None, f"seg_id mismatch: expected {seg_id!r}, got {result_seg_id!r}"

    notes = parsed.get("notes")
    if notes is not None:
        if isinstance(notes, str):
            pass
        elif isinstance(notes, list) and all(isinstance(n, str) for n in notes):
            pass
        else:
            return None, "'notes' must be a string or a list of strings"

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


def _normalize_notes(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw] if raw.strip() else []
    return [n for n in raw if n.strip()]


def _merge_notes(existing: list[str], new: list[str]) -> list[str]:
    return list(dict.fromkeys([*existing, *new]))


def _is_valid_record_path(data_dir: Path, book_id: str, record_path: str) -> bool:
    """Whether `record_path` (as claimed by a subagent) resolves inside
    `records/<book_id>/` under `data_dir` and exists on disk."""
    records_root = (data_dir / "records" / book_id).resolve()
    candidate = (data_dir / record_path).resolve()
    try:
        candidate.relative_to(records_root)
    except ValueError:
        return False
    return candidate.is_file()


def _overwrite_extraction(
    data_dir: Path, record_path: str, *, tier: str, model: str, segment_id: str
) -> None:
    """Overwrite `extraction` on an accepted claimed record file with the
    authoritative tier/model/segment_id/timestamp (B5 follow-up 3) -- a
    subagent's own `extraction` block is only a placeholder (see
    `owlsperch.queue.prompt`); this is the only place those values become
    real. Runs only for a path that already passed `_is_valid_record_path`,
    so it's known to resolve inside `records/<book_id>/` and exist."""
    path = data_dir / record_path
    record: dict[str, Any] = json.loads(path.read_text())
    record["extraction"] = {
        "tier": tier,
        "model": model,
        "segment_id": segment_id,
        "timestamp": now_iso(),
    }
    atomic_write_text(path, json.dumps(record, indent=2) + "\n")


def _append_attempt_if_new(segment: Segment, *, errors: list[str]) -> None:
    last = segment.attempts[-1] if segment.attempts else None
    already_recorded = isinstance(last, dict) and last.get("errors") == errors
    if not already_recorded:
        segment.attempts = [
            *segment.attempts,
            {"tier": segment.tier, "timestamp": now_iso(), "errors": errors},
        ]


def complete_segment(seg_id: str, result_text: str, *, data_dir: Path) -> CompleteOutcome:
    path = find_segment_path(data_dir, seg_id)
    if path is None:
        raise QueueError(f"unknown segment '{seg_id}' (no segments/*/{seg_id}.json found)")

    segment = Segment.model_validate_json(path.read_text())
    segment.in_progress_since = None

    parsed, error = _parse_result(result_text, seg_id)
    if error is not None:
        segment.status = "pending"
        error_msg = f"malformed_result: {error}"
        _append_attempt_if_new(segment, errors=[error_msg])
        atomic_write_text(path, segment.model_dump_json(indent=2) + "\n")
        return CompleteOutcome(seg_id=seg_id, outcome="malformed", detail=error)

    assert parsed is not None  # `error is None` implies `_parse_result` returned a dict.
    segment.notes = _merge_notes(segment.notes, _normalize_notes(parsed.get("notes")))

    no_content = parsed["no_content"]
    if no_content is not None:
        reason = no_content["reason"]
        segment.status = "done"
        segment.outcome = "no_content"
        segment.outcome_reason = reason
        atomic_write_text(path, segment.model_dump_json(indent=2) + "\n")
        return CompleteOutcome(seg_id=seg_id, outcome="no_content", detail=reason)

    records: list[str] = parsed["records"]
    valid_paths: list[str] = []
    invalid_paths: list[str] = []
    for record_path in records:
        if _is_valid_record_path(data_dir, segment.book_id, record_path):
            valid_paths.append(record_path)
        else:
            invalid_paths.append(record_path)

    # Extraction provenance is authoritative from here, not whatever
    # (possibly placeholder) values the subagent put in its own record.
    model = segment.model if segment.model is not None else DEFAULT_MODEL
    for record_path in valid_paths:
        _overwrite_extraction(
            data_dir, record_path, tier=segment.tier, model=model, segment_id=segment.seg_id
        )

    merged = list(segment.pending_records)
    for record_path in valid_paths:
        if record_path not in merged:
            merged.append(record_path)
    segment.pending_records = merged
    segment.status = "pending"

    if invalid_paths:
        _append_attempt_if_new(segment, errors=[f"missing_record_path: {p}" for p in invalid_paths])

    atomic_write_text(path, segment.model_dump_json(indent=2) + "\n")

    detail = f"{len(valid_paths)} record(s) claimed"
    if invalid_paths:
        detail += f", {len(invalid_paths)} invalid path(s)"
    return CompleteOutcome(seg_id=seg_id, outcome="pending_records", detail=detail)
