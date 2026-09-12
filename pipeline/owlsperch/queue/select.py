"""`owlsperch queue next` -- selecting pending segments for a subagent wave
and marking them `in_progress`, per spec 4.5 and B5 acceptance criterion 1.

The whole select-and-mark operation for a book holds an exclusive
`fcntl.flock` on `segments/<book_id>/.queue.lock` (see `_book_lock`), so two
concurrent `queue next` invocations for the same book can never both select
the same pending segment (a check-then-act race otherwise possible between
reading a segment's `status` and writing it back as `in_progress`).

A segment that already has an attempt recorded at the requested tier is
never (re)selected here -- it is waiting for a tier escalation (B8), and
selecting it again would either loop forever re-running the same tier or
require the caller to notice and stop; `queue summary`'s `awaiting_escalation`
count (`owlsperch.queue.summary`) surfaces these separately so an operator
can see why `queue next` legitimately returned fewer segments than pending.
"""

from __future__ import annotations

import fcntl
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from owlsperch.fsutil import atomic_write_text
from owlsperch.queue.common import now_iso
from owlsperch.queue.prompt import DEFAULT_MODEL, render_prompt_to_file
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

    def to_json(self) -> dict[str, Any]:
        return {
            "seg_id": self.seg_id,
            "segment_path": self.segment_path,
            "kind_hint": self.kind_hint,
            "prompt_path": self.prompt_path,
        }


def _has_attempt_at_tier(segment: Segment, tier: str) -> bool:
    """Whether `segment` already carries an attempt recorded at `tier` --
    such a segment waits for B8's tier escalation and is never (re)selected
    for the same tier (see module docstring)."""
    return any(isinstance(a, dict) and a.get("tier") == tier for a in segment.attempts)


def select_and_mark(
    book_id: str,
    *,
    data_dir: Path,
    tier: str = "haiku",
    limit: int,
    kind: str = "spell",
    manifest_path: Path | None = None,
    schemas_dir: Path | None = None,
    model: str = DEFAULT_MODEL,
    lock_timeout: float = DEFAULT_LOCK_TIMEOUT,
) -> list[SelectedSegment]:
    """Select up to `limit` segments of `book_id` that are `status ==
    "pending"`, `tier == tier`, `kind_hint == kind` (default "spell" --
    this batch only has a schema for that kind; other kinds are simply never
    selected, and show up in `owlsperch queue summary`'s per-kind counts
    instead), and have no attempt already recorded at `tier` (those are
    waiting for a tier escalation -- see module docstring and
    `owlsperch.queue.summary`'s `awaiting_escalation` count). Each selected
    segment is atomically marked `in_progress` with an `in_progress_since`
    timestamp, and has its subagent prompt rendered to
    `prompts/<book_id>/<seg_id>.md` before being returned -- an
    already-`in_progress` segment is never reselected by a later call.

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

    with _book_lock(seg_dir, timeout=lock_timeout):
        selected: list[SelectedSegment] = []
        for path in sorted(seg_dir.glob(f"{book_id}-*.json")):
            if len(selected) >= limit:
                break

            segment = Segment.model_validate_json(path.read_text())
            if (
                segment.status != "pending"
                or segment.tier != tier
                or segment.kind_hint != kind
                or _has_attempt_at_tier(segment, tier)
            ):
                continue

            segment.status = "in_progress"
            segment.in_progress_since = now_iso()
            atomic_write_text(path, segment.model_dump_json(indent=2) + "\n")

            prompt_path = render_prompt_to_file(
                segment,
                data_dir=data_dir,
                manifest_path=manifest_path,
                schemas_dir=schemas_dir,
                model=model,
            )
            selected.append(
                SelectedSegment(
                    seg_id=segment.seg_id,
                    segment_path=path.relative_to(data_dir).as_posix(),
                    kind_hint=segment.kind_hint,
                    prompt_path=str(prompt_path),
                )
            )

    return selected
