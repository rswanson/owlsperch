"""`owlsperch queue next` -- selecting pending segments for a subagent wave
and marking them `in_progress`, per spec 4.5 and B5 acceptance criterion 1
(batch B8 adds the tier-escalation-aware selection rules below).

The whole select-and-mark operation for a book holds an exclusive
`fcntl.flock` on `segments/<book_id>/.queue.lock` (see `_book_lock`), so two
concurrent `queue next` invocations for the same book can never both select
the same pending segment (a check-then-act race otherwise possible between
reading a segment's `status` and writing it back as `in_progress`).

Before selecting anything, the whole book's segments (every kind/tier, not
just the ones matching this call's filters) get two passes, still inside
the lock:

1. Stale reset (spec 4.5's "interrupted session" edge case): any segment
   `in_progress` for more than `owlsperch.queue.ladder.STALE_AFTER` (60
   minutes) is reset to `pending` with `in_progress_since` cleared.
2. Lazy escalation (`owlsperch.queue.ladder.escalate_existing_attempt`): a
   pending segment already carrying a failed/malformed attempt at its own
   current tier -- either a single same-tier `needs_context` retry (left
   alone) or anything else, including B8-pre-dating data that was written
   before tier escalation existed at all -- is advanced along the ladder (or
   moved to `human/`) so it converges onto the same state machine a fresh
   failure would have produced. This is what makes `--tier` selection safe
   to reuse across a schema/behavior upgrade: nothing is ever skipped
   forever waiting on a tier bump that never happens.

Only after both passes does `select_and_mark` pick segments to run: pending,
matching `tier` and `kind`, in filename (book) order. `tier=None` (the
`queue next` default, criterion 6) picks the lowest tier in
`owlsperch.queue.ladder.TIERS` that has at least one pending segment of
`kind` for this book, after the two passes above -- if nothing is pending at
any tier, `select_and_mark` returns `[]` without selecting anything.

`dry_run=True` (B10-mand3, `queue next --dry-run`) previews this exact
selection with no write side effects. The stale-reset/lazy-escalation pass
still runs -- entirely in memory -- so the preview is faithful: it shows
whatever a real call with the same arguments would un-stick or escalate
before selecting, including a segment that pass would move to `human/`
(excluded from the preview either way, since a real run would have made it
unselectable). The selection loop then builds the same `SelectedSegment`s
without mutating a `Segment`, writing it back, or rendering its prompt --
`prompt_path` still names where `owlsperch.queue.prompt.prompt_path_for`
would render it, which may not exist on disk yet. The per-book queue lock
is still taken in dry-run mode (for a consistent snapshot); its `.queue.lock`
file is the one filesystem effect dry-run mode has.
"""

from __future__ import annotations

import fcntl
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from owlsperch.fsutil import atomic_write_text
from owlsperch.queue.common import move_segment_to_human, now_iso
from owlsperch.queue.ladder import TIER_MODELS, TIERS, escalate_existing_attempt, is_stale
from owlsperch.queue.prompt import prompt_path_for, render_prompt_to_file
from owlsperch.schemas import load_registry
from owlsperch.segment.runner import Segment

#: Default timeout (seconds) `select_and_mark` waits to acquire the
#: per-book queue lock before giving up (`--lock-timeout` on `queue next`).
DEFAULT_LOCK_TIMEOUT = 30.0

#: How long to sleep between retries while polling for the lock.
_LOCK_POLL_INTERVAL = 0.05


class LockTimeoutError(Exception):
    """Raised when the per-book queue lock could not be acquired within the
    configured timeout -- another `queue next` (or a test) is holding it."""


@contextmanager
def _book_lock(seg_dir: Path, *, timeout: float) -> Iterator[None]:
    """Hold an exclusive `flock` on `seg_dir / ".queue.lock"` for the
    duration of the `with` block, polling up to `timeout` seconds before
    raising `LockTimeoutError`."""
    seg_dir.mkdir(parents=True, exist_ok=True)
    lock_path = seg_dir / ".queue.lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise LockTimeoutError(
                        f"timed out after {timeout}s waiting for lock {lock_path}"
                    ) from None
                time.sleep(_LOCK_POLL_INTERVAL)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


@dataclass
class SelectedSegment:
    seg_id: str
    #: Relative to `$OWLSPERCH_DATA`.
    segment_path: str
    kind_hint: str
    #: Absolute path (as a string) to the rendered subagent prompt.
    prompt_path: str
    #: The tier this segment was selected at (batch B8) -- the caller
    #: (the `/extract` skill) launches its Agent-tool subagent with this as
    #: `model`.
    tier: str
    model: str

    def to_json(self) -> dict[str, Any]:
        return {
            "seg_id": self.seg_id,
            "segment_path": self.segment_path,
            "kind_hint": self.kind_hint,
            "prompt_path": self.prompt_path,
            "tier": self.tier,
            "model": self.model,
        }


def _reset_stale_and_heal(
    data_dir: Path, seg_dir: Path, book_id: str, *, dry_run: bool = False
) -> tuple[int, list[tuple[Path, Segment]]]:
    """Walk every segment file for `book_id` (any kind/tier) once: reset a
    stale `in_progress` segment back to `pending`, then lazily escalate a
    pending segment that already has an attempt at its own tier. Returns
    `(stale_count, segments)`, where `segments` is the post-heal
    `(path, Segment)` list still living in `seg_dir` (i.e. never moved to
    `human/`), in filename order -- callers select from this list instead of
    re-globbing/re-reading. Must be called while holding the book's queue
    lock.

    `dry_run=True` runs the exact same in-memory logic (so the returned
    `segments` reflect what a real call would have healed) but performs no
    filesystem writes: no `atomic_write_text`, no `move_segment_to_human`. A
    segment that would have been moved to `human/` (exhausted) is still
    omitted from the returned list either way, since a real run would have
    made it unselectable."""
    now = datetime.now(UTC)
    stale_count = 0
    segments: list[tuple[Path, Segment]] = []

    for path in sorted(seg_dir.glob(f"{book_id}-*.json")):
        segment = Segment.model_validate_json(path.read_text())
        changed = False

        if is_stale(segment, now):
            segment.status = "pending"
            segment.in_progress_since = None
            changed = True
            stale_count += 1

        exhausted = False
        if segment.status == "pending":
            result = escalate_existing_attempt(segment)
            if result is not None:
                if result.exhausted:
                    exhausted = True
                    if not dry_run:
                        move_segment_to_human(
                            data_dir, path, segment, outcome="escalation_exhausted"
                        )
                    changed = False  # already written by move_segment_to_human (real run)
                else:
                    changed = True

        if exhausted:
            continue  # moved to human/ (or would be under dry_run) -- never selectable

        if changed and not dry_run:
            atomic_write_text(path, segment.model_dump_json(indent=2) + "\n")

        segments.append((path, segment))

    return stale_count, segments


def _lowest_pending_tier(segments: list[tuple[Path, Segment]], kinds: set[str]) -> str | None:
    pending_tiers = {
        segment.tier
        for _, segment in segments
        if segment.status == "pending" and segment.kind_hint in kinds
    }
    for candidate in TIERS:
        if candidate in pending_tiers:
            return candidate
    return None


def select_and_mark(
    book_id: str,
    *,
    data_dir: Path,
    tier: str | None = None,
    limit: int,
    kind: str | None = None,
    manifest_path: Path | None = None,
    schemas_dir: Path | None = None,
    model: str | None = None,
    lock_timeout: float = DEFAULT_LOCK_TIMEOUT,
    stats: dict[str, int] | None = None,
    dry_run: bool = False,
) -> list[SelectedSegment]:
    """Select up to `limit` segments of `book_id` that are `status ==
    "pending"`, `tier == tier`, and whose `kind_hint` is in the resolved
    kind set. `kind=None` (the default, criterion 6) resolves to every
    kind_hint with a registered schema (`set(load_registry(schemas_dir)
    .types)` -- currently spell/feat/table/rules_section); a kind with no
    registered schema (e.g. `stat_block`) is then never selected by
    default, so segments a later batch needs aren't burned as
    `no_content`. Pass an explicit `kind` (e.g. `"stat_block"`) to restrict
    to exactly that one kind_hint regardless of whether it has a schema.
    Each selected segment is atomically marked `in_progress` with an
    `in_progress_since` timestamp, and has its subagent prompt rendered to
    `prompts/<book_id>/<seg_id>.md` before being returned (using that
    segment's own `kind_hint`, since one wave may mix kinds when `kind` is
    the resolved set) -- an already-`in_progress` segment is never
    reselected by a later call.

    `tier=None` (the default) picks the lowest tier in
    `owlsperch.queue.ladder.TIERS` with at least one pending segment whose
    kind_hint is in the resolved kind set, after this call's own
    stale-reset and lazy-escalation passes (see module docstring) -- if
    nothing is pending, returns `[]` without selecting anything.
    `model=None` defaults to `owlsperch.queue.ladder.TIER_MODELS[tier]` for
    whichever tier was actually used. When `stats` is given,
    `stats["stale_reset"]` is set to the number of segments reset from a
    stale `in_progress` (spec 4.5) -- `owlsperch.queue.runner.run_queue_next`
    uses this to print a note.

    `dry_run=True` (B10-mand3) previews the exact same selection with no
    write side effects -- see the module docstring.

    The whole operation holds an exclusive lock on
    `segments/<book_id>/.queue.lock` (see `_book_lock`); `LockTimeoutError`
    is raised if it can't be acquired within `lock_timeout` seconds.

    Segment files are visited in filename order (`<book_id>-p<NNNN>-<NN>`),
    i.e. book order, so a rerun with the same arguments makes deterministic
    progress through the book.
    """
    seg_dir = data_dir / "segments" / book_id
    if not seg_dir.is_dir() or limit <= 0:
        return []

    kinds = {kind} if kind is not None else set(load_registry(schemas_dir).types)

    with _book_lock(seg_dir, timeout=lock_timeout):
        stale_reset_count, healed_segments = _reset_stale_and_heal(
            data_dir, seg_dir, book_id, dry_run=dry_run
        )
        if stats is not None:
            stats["stale_reset"] = stale_reset_count

        resolved_tier = tier if tier is not None else _lowest_pending_tier(healed_segments, kinds)
        if resolved_tier is None:
            return []
        resolved_model = (
            model if model is not None else TIER_MODELS.get(resolved_tier, resolved_tier)
        )

        selected: list[SelectedSegment] = []
        for path, segment in healed_segments:
            if len(selected) >= limit:
                break

            if (
                segment.status != "pending"
                or segment.tier != resolved_tier
                or segment.kind_hint not in kinds
            ):
                continue

            if dry_run:
                # Preview only: no mutation, no write-back, no prompt
                # rendered -- `prompt_path_for`'s result may not exist on
                # disk yet.
                selected.append(
                    SelectedSegment(
                        seg_id=segment.seg_id,
                        segment_path=path.relative_to(data_dir).as_posix(),
                        kind_hint=segment.kind_hint,
                        prompt_path=str(
                            prompt_path_for(data_dir, segment.book_id, segment.seg_id)
                        ),
                        tier=resolved_tier,
                        model=resolved_model,
                    )
                )
                continue

            segment.status = "in_progress"
            segment.in_progress_since = now_iso()
            # Authoritative record of the model this wave's prompt was
            # rendered for -- `owlsperch queue complete` copies it into an
            # accepted record's `extraction.model` (B5 follow-up 3).
            segment.model = resolved_model
            atomic_write_text(path, segment.model_dump_json(indent=2) + "\n")

            prompt_path = render_prompt_to_file(
                segment,
                data_dir=data_dir,
                manifest_path=manifest_path,
                schemas_dir=schemas_dir,
                model=resolved_model,
            )
            selected.append(
                SelectedSegment(
                    seg_id=segment.seg_id,
                    segment_path=path.relative_to(data_dir).as_posix(),
                    kind_hint=segment.kind_hint,
                    prompt_path=str(prompt_path),
                    tier=resolved_tier,
                    model=resolved_model,
                )
            )

    return selected
