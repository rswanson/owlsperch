"""The tier escalation ladder (spec 4.5; batch B8): pure state-transition
functions over a `Segment`'s `tier`/`attempts` fields. No I/O -- callers
(`owlsperch.validate.runner._write_back_fail`, `owlsperch.queue.complete`,
`owlsperch.queue.select`) decide what to persist and where (including moving
a segment to `human/`, which is I/O and lives in `owlsperch.queue.common`).

The ladder is `TIERS`: haiku -> sonnet -> opus. A failing attempt at a
segment's current tier normally advances it to the next tier immediately
(there is no "retry the same tier" for an ordinary validation/malformed
failure) -- criterion 1's "haiku -> sonnet -> opus" progression is one
attempt per tier. The lone exception is `needs_context` (criterion 3): the
*first* `needs_context` reply at a given tier keeps the segment on that same
tier (for one retry with the adjacent segment's text merged in -- see
`owlsperch.queue.complete`); only a *second* `needs_context` at the same
tier escalates like any other failure. A failure recorded once opus is
already exhausted reports `exhausted=True` instead of a tier -- the caller
moves the segment to `human/`.

`record_failure` is idempotent, but *only* for `kind == "validation"`, the
same way the pre-B8 write-back guard was: calling it again with the exact
same `(tier, kind, errors)` as the segment's last attempt is a no-op (no
duplicate attempt, no further tier movement) -- this is what a rerun of
`owlsperch validate` with nothing changed produces, and is also what stops a
*stale* re-validation of an old-tier record (one whose own `extraction.tier`
predates the segment's current tier, e.g. after a schema change makes a
previously-passing record fail again) from double-advancing the ladder: such
a call's `tier` argument no longer matches `segment.tier`, so `record_failure`
records the attempt for the record but never moves `segment.tier` for it.
This guard is deliberately *not* applied to `needs_context` or `malformed`:
every call for those kinds comes from a distinct `owlsperch queue complete`
of a distinct subagent reply, so an identical repeat (e.g. a second
`needs_context` reply naming the same adjacent segment id as the first) is a
genuinely new attempt, not a rerun of the same check -- it must still
retry-then-escalate (or escalate outright, for `malformed`) rather than be
swallowed as a no-op.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from owlsperch.queue.common import now_iso
from owlsperch.segment.runner import Segment

#: The escalation ladder, lowest tier first.
TIERS: tuple[str, ...] = ("haiku", "sonnet", "opus")

#: The model string rendered into a prompt's `extraction.model` (and used as
#: `queue next`'s `--model` default) for each tier.
TIER_MODELS: dict[str, str] = {
    "haiku": "claude-haiku-4-5",
    "sonnet": "claude-sonnet-5",
    "opus": "claude-opus-5",
}

#: Batch B10c: a `kind_hint` that starts higher than `TIERS[0]` (haiku) on
#: the ladder, because a class/prestige_class extraction (a level table plus
#: several structured sub-objects) is reliably too complex for haiku to get
#: right on a first attempt -- see `starting_tier`. Batch B11 adds
#: errata_entry/update_entry: a mis-read `target_name`/`target_page` does
#: not fail schema validation -- it silently fails to match at build time
#: and lands in `human/`, which costs more than the cheaper tier saves.
STARTING_TIERS: dict[str, str] = {
    "class": "sonnet",
    "prestige_class": "sonnet",
    "errata_entry": "sonnet",
    "update_entry": "sonnet",
}


def starting_tier(kind: str) -> str:
    """The tier a freshly-written segment of `kind` starts on:
    `STARTING_TIERS.get(kind, TIERS[0])` -- every kind not listed in
    `STARTING_TIERS` still starts at the bottom of the ladder (haiku)."""
    return STARTING_TIERS.get(kind, TIERS[0])


#: How long a segment may sit `in_progress` before the next `queue next`
#: resets it back to `pending` (spec 4.5).
STALE_AFTER = timedelta(minutes=60)

AttemptKind = Literal["validation", "malformed", "needs_context"]


def next_tier(tier: str) -> str | None:
    """The tier after `tier` in `TIERS`, or `None` if `tier` is already the
    last one (`"opus"`) -- the caller must move the segment to `human/` in
    that case."""
    try:
        idx = TIERS.index(tier)
    except ValueError:
        return None
    if idx + 1 >= len(TIERS):
        return None
    return TIERS[idx + 1]


def attempt_kind_of(attempt: Any) -> str:
    """The `kind` of an attempt dict, defaulting to `"validation"` for an
    attempt recorded before B8 (which never had a `kind` key at all -- every
    pre-B8 attempt was a validation failure)."""
    if isinstance(attempt, dict):
        kind = attempt.get("kind")
        if isinstance(kind, str):
            return kind
    return "validation"


def has_needs_context_attempt_at_tier(segment: Segment, tier: str) -> bool:
    """Whether `segment` already carries a `needs_context` attempt recorded
    at `tier` -- used to tell a first `needs_context` reply (retry the same
    tier) from a second one (escalate)."""
    return any(
        isinstance(a, dict) and a.get("tier") == tier and attempt_kind_of(a) == "needs_context"
        for a in segment.attempts
    )


def is_stale(segment: Segment, now: datetime) -> bool:
    """Whether `segment` has been `in_progress` for longer than
    `STALE_AFTER` as of `now` -- `owlsperch queue next` resets these back to
    `pending` before selecting (spec 4.5's "interrupted session" edge
    case)."""
    if segment.status != "in_progress" or segment.in_progress_since is None:
        return False
    try:
        started = datetime.fromisoformat(segment.in_progress_since)
    except ValueError:
        return False
    return now - started > STALE_AFTER


@dataclass
class FailureResult:
    """The outcome of `record_failure` (or `escalate_existing_attempt`):
    exactly one of `exhausted`, `escalated_to`, or `retried_same_tier` is
    the meaningful signal to the caller (an idempotent no-op call sets none
    of them)."""

    #: Opus was already the segment's tier when this failure landed -- the
    #: caller must move the segment to `human/`.
    exhausted: bool = False
    #: The new tier the segment was advanced to, or `None` if it wasn't
    #: advanced (idempotent no-op, a stale attempt-tier mismatch, or a
    #: same-tier `needs_context` retry).
    escalated_to: str | None = None
    #: True for a first `needs_context` at the segment's current tier --
    #: the segment stays on the same tier for one retry.
    retried_same_tier: bool = False


def record_failure(
    segment: Segment,
    errors: list[str],
    *,
    kind: AttemptKind = "validation",
    tier: str | None = None,
) -> FailureResult:
    """Append `{"tier", "timestamp", "errors", "kind"}` to `segment.attempts`
    (idempotent for `kind == "validation"` only -- see module docstring) and
    advance `segment.tier` to the next rung, unless this is a first
    `needs_context` at the current tier (stay put for a retry) or the
    attempt's own `tier` no longer matches `segment.tier` (a stale
    re-validation of an old-tier record -- recorded for the audit trail, but
    never allowed to move the ladder). Mutates `segment.attempts` and (when
    advancing) `segment.tier` in place; never touches `segment.status` or
    does any I/O -- the caller decides what to persist and where (including
    moving an exhausted segment to `human/`).
    """
    attempt_tier = tier if tier is not None else segment.tier

    last = segment.attempts[-1] if segment.attempts else None
    already_recorded = (
        kind == "validation"
        and isinstance(last, dict)
        and last.get("tier") == attempt_tier
        and last.get("errors") == errors
        and attempt_kind_of(last) == kind
    )
    if already_recorded:
        return FailureResult()

    # Whether a `needs_context` at this exact tier was already recorded
    # *before* this new attempt -- decides first-retry vs. second-escalates.
    prior_needs_context_at_tier = kind == "needs_context" and has_needs_context_attempt_at_tier(
        segment, attempt_tier
    )

    segment.attempts = [
        *segment.attempts,
        {"tier": attempt_tier, "timestamp": now_iso(), "errors": errors, "kind": kind},
    ]

    if attempt_tier != segment.tier:
        # Stale: this attempt's tier is behind the segment's current tier
        # (already advanced by an earlier, unrelated failure) -- recorded,
        # but must never move the ladder again.
        return FailureResult()

    if kind == "needs_context" and not prior_needs_context_at_tier:
        return FailureResult(retried_same_tier=True)

    nxt = next_tier(segment.tier)
    if nxt is None:
        return FailureResult(exhausted=True)
    segment.tier = nxt
    return FailureResult(escalated_to=nxt)


def escalate_existing_attempt(segment: Segment) -> FailureResult | None:
    """Lazy self-healing for a segment written before B8 (or otherwise left
    with a failed/malformed attempt already recorded at its own current
    tier without ever having advanced): `owlsperch queue next` applies this
    to every pending segment before selecting, so legacy data converges onto
    the same ladder instead of being skipped forever.

    Returns `None` when there's nothing to fix: no attempt at the segment's
    current tier at all, or the most recent one at that tier is a lone
    (first) `needs_context` -- a legitimate same-tier retry state, not a
    stuck segment. Otherwise behaves like `record_failure` for that
    attempt's own `kind`, except it never appends a new attempt (the
    attempt is already on disk) -- only `segment.tier` moves (or the
    segment is reported exhausted).
    """
    tier = segment.tier
    attempts_at_tier = [
        a for a in segment.attempts if isinstance(a, dict) and a.get("tier") == tier
    ]
    if not attempts_at_tier:
        return None

    last = attempts_at_tier[-1]
    kind = attempt_kind_of(last)
    if kind == "needs_context" and len(attempts_at_tier) < 2:
        return None  # awaiting its one same-tier retry -- leave it alone

    nxt = next_tier(tier)
    if nxt is None:
        return FailureResult(exhausted=True)
    segment.tier = nxt
    return FailureResult(escalated_to=nxt)
