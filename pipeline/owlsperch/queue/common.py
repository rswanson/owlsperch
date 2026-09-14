"""Small helpers shared across `owlsperch.queue.*` modules."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from owlsperch.fsutil import atomic_write_text
from owlsperch.segment.runner import Segment

if TYPE_CHECKING:
    from owlsperch.queue.ladder import FailureResult


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def find_segment_path(data_dir: Path, seg_id: str) -> Path | None:
    """Find `segments/<book_id>/<seg_id>.json` for `seg_id` without already
    knowing `book_id` -- `book_id` itself may contain hyphens (e.g.
    `dmg1-building-a-city-we`), so a segment is looked up by its exact
    filename under any book directory rather than by splitting `seg_id`.
    Also searches `human/*/` (batch B8) -- a segment that failed on opus or
    carried a `proposed_type` lives there, and `owlsperch queue reset` needs
    to find it too."""
    matches = sorted(data_dir.glob(f"segments/*/{seg_id}.json"))
    if matches:
        return matches[0]
    matches = sorted(data_dir.glob(f"human/*/{seg_id}.json"))
    return matches[0] if matches else None


def human_segment_path(data_dir: Path, book_id: str, seg_id: str) -> Path:
    return data_dir / "human" / book_id / f"{seg_id}.json"


def move_segment_to_human(
    data_dir: Path,
    path: Path,
    segment: Segment,
    *,
    outcome: str,
    proposal: dict[str, Any] | None = None,
) -> None:
    """Move `segment` (currently on disk at `path`, under `segments/`) to
    `human/<book_id>/<seg_id>.json` (spec 4.2): sets `status: "human"`,
    `outcome`, and (for a `proposed_type` outcome) `proposal`, keeping every
    attempt. The updated JSON is written atomically to `path` first (the
    existing `atomic_write_text`), then a single `os.replace(path,
    human_path)` renames it into place under `human/` -- both are on the
    same filesystem (under `data_dir`), so there is never a moment where the
    segment exists at both `segments/` and `human/`, unlike write-new-then-
    delete-old, which can leave both copies behind if it crashes in
    between."""
    segment.status = "human"
    segment.outcome = outcome
    if proposal is not None:
        segment.proposal = proposal
    segment.in_progress_since = None

    human_path = human_segment_path(data_dir, segment.book_id, segment.seg_id)
    human_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, segment.model_dump_json(indent=2) + "\n")
    if path != human_path:
        os.replace(path, human_path)


def finish_after_failure(
    data_dir: Path, path: Path, segment: Segment, result: FailureResult
) -> None:
    """Persist `segment` after a `record_failure`/`escalate_existing_attempt`
    call: an exhausted result moves it to `human/` (opus failed); otherwise
    it's written back to `path` as `pending` (whatever tier
    `record_failure` left it on -- possibly unchanged, for a same-tier
    `needs_context` retry or an idempotent no-op)."""
    if result.exhausted:
        move_segment_to_human(data_dir, path, segment, outcome="escalation_exhausted")
        return
    segment.status = "pending"
    atomic_write_text(path, segment.model_dump_json(indent=2) + "\n")


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


def staging_records_root(data_dir: Path, book_id: str, seg_id: str) -> Path:
    """Batch B10c-mand4: the private staging directory a subagent writes its
    candidate record files into for one extraction attempt of `seg_id` --
    `staging/<book_id>/<seg_id>/records/<type>/<slug>.json`. Never in the
    repo, always under `$OWLSPERCH_DATA`. A subagent's claimed paths must
    resolve inside here (see `resolve_staged_record_path`); `owlsperch queue
    complete` moves an accepted file out of here into
    `records/<book_id>/<type>/<slug>.json` and removes this whole directory
    once it's done with the reply (see `owlsperch.queue.complete`)."""
    return data_dir / "staging" / book_id / seg_id / "records"


def resolve_staged_record_path(
    data_dir: Path, book_id: str, seg_id: str, rel_path: str
) -> Path | None:
    """Resolve `rel_path` (as claimed by a subagent, relative to `data_dir`,
    or an absolute path) and return it iff it falls inside THIS segment's
    own `staging_records_root` -- the same `.resolve()` + `relative_to`
    traversal guard as `resolve_record_path_under_book`, but scoped to one
    segment's own staging tree rather than a whole book's shared records
    directory. Returns `None` if `rel_path` resolves outside that directory
    (including a bare `records/<book_id>/...` path with no staging prefix
    at all -- batch B10c-mand4 accepts staging paths only). Existence on
    disk is not checked here; callers decide what else "valid" requires."""
    staging_root = staging_records_root(data_dir, book_id, seg_id).resolve()
    candidate = (data_dir / rel_path).resolve()
    try:
        candidate.relative_to(staging_root)
    except ValueError:
        return None
    return candidate


def destination_rel_path_for_staged(root: Path, staged: Path, book_id: str) -> str | None:
    """Map a resolved staged file path (`<root>/<type>/<file>.json`, `root`
    being that segment's own resolved `staging_records_root`) to its final
    home once `owlsperch queue complete` accepts it:
    `records/<book_id>/<type>/<file>.json`. Returns `None` if `staged` is
    not exactly two path components under `root` (a wrong-depth claim, e.g.
    missing the type directory or nesting an extra one) -- the shape
    `owlsperch.queue.prompt` tells every subagent to write."""
    try:
        rel = staged.relative_to(root)
    except ValueError:
        return None
    parts = rel.parts
    if len(parts) != 2:
        return None
    type_dir, filename = parts
    return f"records/{book_id}/{type_dir}/{filename}"
