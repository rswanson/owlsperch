"""Orchestration for `owlsperch queue next|prompt|complete|summary|reset`,
wired into `owlsperch.cli` (spec 4.5; batch B5). Each function mirrors the
`run_*` pattern used by `owlsperch.segment.runner.run_segment` and
`owlsperch.validate.runner.run_validate`: resolve defaults, do the work, and
print to `out` (default `sys.stdout`) so tests can capture output without
subprocessing.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from owlsperch.fsutil import atomic_write_text
from owlsperch.queue.common import find_segment_path, resolve_record_path_under_book
from owlsperch.queue.complete import QueueError, complete_segment
from owlsperch.queue.prompt import DEFAULT_MODEL, render_prompt_to_file
from owlsperch.queue.select import DEFAULT_LOCK_TIMEOUT, LockTimeoutError, select_and_mark
from owlsperch.queue.summary import compute_summary
from owlsperch.segment.runner import SEGMENT_TIER, Segment
from owlsperch.text.runner import default_data_dir


def run_queue_next(
    book_id: str,
    *,
    tier: str | None = None,
    limit: int,
    kind: str | None = None,
    model: str | None = None,
    lock_timeout: float = DEFAULT_LOCK_TIMEOUT,
    json_output: bool = False,
    data_dir: Path | None = None,
    manifest_path: Path | None = None,
    schemas_dir: Path | None = None,
    out: Any = None,
) -> int:
    out = out if out is not None else sys.stdout
    data_dir = data_dir if data_dir is not None else default_data_dir()

    stats: dict[str, int] = {}
    try:
        selected = select_and_mark(
            book_id,
            data_dir=data_dir,
            tier=tier,
            limit=limit,
            kind=kind,
            manifest_path=manifest_path,
            schemas_dir=schemas_dir,
            model=model,
            lock_timeout=lock_timeout,
            stats=stats,
        )
    except LockTimeoutError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    stale_reset = stats.get("stale_reset", 0)
    if stale_reset:
        print(f"reset {stale_reset} stale in_progress segment(s)", file=sys.stderr)

    if json_output:
        print(json.dumps([s.to_json() for s in selected]), file=out)
        return 0

    if not selected:
        tier_label = tier if tier is not None else "any"
        kind_label = kind if kind is not None else "any registered kind"
        print(f"{book_id}: no pending '{kind_label}' segments at tier '{tier_label}'", file=out)
    for item in selected:
        print(f"{item.seg_id} ({item.kind_hint}, tier {item.tier}) -> {item.prompt_path}", file=out)
    return 0


def run_queue_prompt(
    seg_id: str,
    *,
    model: str = DEFAULT_MODEL,
    data_dir: Path | None = None,
    manifest_path: Path | None = None,
    schemas_dir: Path | None = None,
    out: Any = None,
) -> int:
    out = out if out is not None else sys.stdout
    data_dir = data_dir if data_dir is not None else default_data_dir()

    path = find_segment_path(data_dir, seg_id)
    if path is None:
        print(f"error: unknown segment '{seg_id}'", file=sys.stderr)
        return 1

    segment = Segment.model_validate_json(path.read_text())
    prompt_path = render_prompt_to_file(
        segment,
        data_dir=data_dir,
        manifest_path=manifest_path,
        schemas_dir=schemas_dir,
        model=model,
    )
    print(str(prompt_path), file=out)
    return 0


def run_queue_complete(
    seg_id: str,
    result_arg: str,
    *,
    data_dir: Path | None = None,
    out: Any = None,
) -> int:
    out = out if out is not None else sys.stdout
    data_dir = data_dir if data_dir is not None else default_data_dir()

    # Fail fast on an unknown seg_id before reading (possibly large) stdin.
    if find_segment_path(data_dir, seg_id) is None:
        print(f"error: unknown segment '{seg_id}'", file=sys.stderr)
        return 1

    try:
        result_text = sys.stdin.read() if result_arg == "-" else Path(result_arg).read_text()
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    try:
        outcome = complete_segment(seg_id, result_text, data_dir=data_dir)
    except QueueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"{outcome.seg_id}: {outcome.outcome} ({outcome.detail})", file=out)
    return 0


def run_queue_summary(
    book_id: str,
    *,
    json_output: bool = False,
    data_dir: Path | None = None,
    out: Any = None,
) -> int:
    out = out if out is not None else sys.stdout
    data_dir = data_dir if data_dir is not None else default_data_dir()

    summary = compute_summary(book_id, data_dir=data_dir)
    if json_output:
        print(json.dumps(summary.to_json()), file=out)
    else:
        print(summary.render(), file=out)
    return 0


def _delete_record_file_under_book(data_dir: Path, book_id: str, record_path: str) -> None:
    """Delete `record_path` (relative to `data_dir`) iff it resolves inside
    `records/<book_id>/` -- mirrors `owlsperch.queue.complete`'s
    `_is_valid_record_path` check so `queue reset --hard` never deletes
    anything outside a book's own records directory, path traversal
    included. Silently does nothing for a path that doesn't resolve there or
    doesn't exist on disk."""
    candidate = resolve_record_path_under_book(data_dir, book_id, record_path)
    if candidate is not None and candidate.is_file():
        candidate.unlink()


def run_queue_reset(
    seg_ids: list[str],
    *,
    hard: bool = False,
    data_dir: Path | None = None,
    out: Any = None,
) -> int:
    """Reset one or more segments back to `status: "pending"` and clear
    `in_progress_since` -- for undoing a `queue next` mark by hand (e.g.
    after a manual smoke test), not part of the normal extract loop.
    Automatic stale (>60 min) reset happens inside `owlsperch queue next`
    itself (batch B8, see `owlsperch.queue.select`); this is an explicit,
    operator-invoked undo.

    A segment currently in `human/<book_id>/` (opus exhausted, or a
    `proposed_type`, batch B8) is moved back to `segments/<book_id>/` as
    `pending` on its current tier, with `outcome`/`outcome_reason`/
    `proposal` cleared (they no longer describe an active segment).

    `--hard` additionally clears `attempts`, `pending_records`, `records`,
    `notes`, `outcome`, `outcome_reason`, and `context_seg_ids` back to
    empty/`None`, resets `tier` back to haiku, and deletes every record
    file named in `records`/`pending_records` (only ones that actually
    resolve under `records/<book_id>/` -- see
    `_delete_record_file_under_book`) -- for fully discarding a trial run's
    state, not just unsticking an in-progress (or human) segment.
    """
    out = out if out is not None else sys.stdout
    data_dir = data_dir if data_dir is not None else default_data_dir()

    exit_code = 0
    for seg_id in seg_ids:
        path = find_segment_path(data_dir, seg_id)
        if path is None:
            print(f"error: unknown segment '{seg_id}'", file=sys.stderr)
            exit_code = 1
            continue

        segment = Segment.model_validate_json(path.read_text())
        was_human = path.parent.name == segment.book_id and path.parent.parent.name == "human"
        segment.status = "pending"
        segment.in_progress_since = None

        if hard:
            for record_path in [*segment.records, *segment.pending_records]:
                _delete_record_file_under_book(data_dir, segment.book_id, record_path)
            segment.attempts = []
            segment.pending_records = []
            segment.records = []
            segment.notes = []
            segment.outcome = None
            segment.outcome_reason = None
            segment.proposal = None
            segment.context_seg_ids = []
            segment.tier = SEGMENT_TIER
        elif was_human:
            segment.outcome = None
            segment.outcome_reason = None
            segment.proposal = None

        target_path = data_dir / "segments" / segment.book_id / f"{seg_id}.json"
        if was_human:
            # Write the updated segment atomically to its current (human/)
            # path first, then a single same-filesystem `os.replace` into
            # `segments/` -- never a moment with copies in both places (see
            # `owlsperch.queue.common.move_segment_to_human`, the same
            # pattern in the other direction).
            target_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(path, segment.model_dump_json(indent=2) + "\n")
            os.replace(path, target_path)
        else:
            atomic_write_text(path, segment.model_dump_json(indent=2) + "\n")

        suffix = " (hard)" if hard else ""
        print(f"{seg_id}: reset to pending{suffix}", file=out)

    return exit_code
