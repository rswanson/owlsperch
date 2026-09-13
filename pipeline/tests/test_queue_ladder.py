"""Tests for `owlsperch.queue.ladder` -- the pure tier-escalation state
machine (spec 4.5; batch B8 acceptance criteria 1, 3, 5).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from owlsperch.queue.ladder import (
    STALE_AFTER,
    TIER_MODELS,
    TIERS,
    escalate_existing_attempt,
    is_stale,
    next_tier,
    record_failure,
)
from owlsperch.segment.runner import Segment


def _segment(**overrides: object) -> Segment:
    defaults: dict[str, Any] = dict(
        seg_id="book-p0010-01",
        book_id="book",
        pages=[10],
        printed_pages=[10],
        kind_hint="spell",
        heading="Fireball",
        text="Fireball text.",
        created_at="2026-01-01T00:00:00+00:00",
    )
    defaults.update(overrides)
    return Segment(**defaults)


# ---------------------------------------------------------------------------
# TIERS / TIER_MODELS / next_tier
# ---------------------------------------------------------------------------


def test_tiers_ladder_order() -> None:
    assert TIERS == ("haiku", "sonnet", "opus")


def test_tier_models_covers_every_tier() -> None:
    assert set(TIER_MODELS) == set(TIERS)
    assert all(isinstance(v, str) and v for v in TIER_MODELS.values())


def test_next_tier_progression() -> None:
    assert next_tier("haiku") == "sonnet"
    assert next_tier("sonnet") == "opus"
    assert next_tier("opus") is None


def test_next_tier_unknown_tier_returns_none() -> None:
    assert next_tier("bogus") is None


# ---------------------------------------------------------------------------
# record_failure: acceptance criterion 1 -- haiku -> sonnet -> opus -> human
# ---------------------------------------------------------------------------


def test_record_failure_advances_one_tier_per_failure() -> None:
    segment = _segment(tier="haiku")

    result = record_failure(segment, ["bad school"], kind="validation")

    assert result.escalated_to == "sonnet"
    assert not result.exhausted
    assert not result.retried_same_tier
    assert segment.tier == "sonnet"
    assert segment.attempts == [
        {
            "tier": "haiku",
            "timestamp": segment.attempts[0]["timestamp"],
            "errors": ["bad school"],
            "kind": "validation",
        }
    ]


def test_record_failure_at_opus_is_exhausted() -> None:
    segment = _segment(tier="opus")

    result = record_failure(segment, ["still bad"], kind="validation")

    assert result.exhausted
    assert result.escalated_to is None
    # `record_failure` never touches status/moves to human/ itself -- that's
    # the caller's job (`owlsperch.queue.common.finish_after_failure`).
    assert segment.tier == "opus"
    assert len(segment.attempts) == 1


def test_full_ladder_haiku_to_sonnet_to_opus_to_exhausted() -> None:
    segment = _segment(tier="haiku")

    r1 = record_failure(segment, ["e1"], kind="validation")
    r2 = record_failure(segment, ["e2"], kind="validation")
    r3 = record_failure(segment, ["e3"], kind="validation")

    assert r1.escalated_to == "sonnet"
    assert r2.escalated_to == "opus"
    assert r3.exhausted
    assert segment.tier == "opus"
    assert [a["tier"] for a in segment.attempts] == ["haiku", "sonnet", "opus"]


# ---------------------------------------------------------------------------
# Idempotency: identical (tier, kind, errors) as the last attempt is a no-op.
# ---------------------------------------------------------------------------


def test_record_failure_is_idempotent_for_identical_repeat_at_same_tier() -> None:
    segment = _segment(tier="haiku")
    record_failure(segment, ["same error"], kind="validation", tier="haiku")

    result = record_failure(segment, ["same error"], kind="validation", tier="haiku")

    assert result == type(result)()
    assert len(segment.attempts) == 1
    assert segment.tier == "sonnet"  # unchanged by the second (idempotent) call


def test_record_failure_idempotency_guard_is_validation_only() -> None:
    """The `(tier, kind, errors)` idempotency guard exists only so a rerun
    of `owlsperch validate` with nothing changed doesn't append a duplicate
    validation attempt. A repeated identical `needs_context` (e.g. a second
    subagent reply naming the same adjacent segment id as the first) is a
    genuinely new attempt from a genuinely new `queue complete` call, so it
    must still escalate -- not be swallowed as a no-op -- while an identical
    repeated `validation` failure is still a true no-op."""
    needs_context_segment = _segment(tier="haiku")
    record_failure(needs_context_segment, ["needs_context: book-p0011-01"], kind="needs_context")

    result = record_failure(
        needs_context_segment, ["needs_context: book-p0011-01"], kind="needs_context"
    )

    assert not result.retried_same_tier
    assert result.escalated_to == "sonnet"
    assert needs_context_segment.tier == "sonnet"
    assert len(needs_context_segment.attempts) == 2

    validation_segment = _segment(tier="haiku")
    record_failure(validation_segment, ["same error"], kind="validation", tier="haiku")

    result = record_failure(validation_segment, ["same error"], kind="validation", tier="haiku")

    assert result == type(result)()
    assert len(validation_segment.attempts) == 1
    assert validation_segment.tier == "sonnet"  # unchanged by the no-op repeat


def test_record_failure_stale_attempt_tier_behind_current_tier_never_double_advances() -> None:
    """A record whose own `extraction.tier` (passed explicitly here, mirroring
    `owlsperch.validate.runner._write_back_fail`) is behind the segment's
    current tier -- already advanced by an earlier, unrelated failure --
    gets its attempt recorded, but the ladder never moves for it."""
    segment = _segment(tier="haiku")
    record_failure(segment, ["first error"], kind="validation", tier="haiku")
    assert segment.tier == "sonnet"

    # A *different* error at the stale "haiku" tier (e.g. a schema change
    # made the same old record fail differently) -- not idempotent (errors
    # differ), but must not advance the ladder a second time.
    result = record_failure(segment, ["different error"], kind="validation", tier="haiku")

    assert not result.exhausted
    assert result.escalated_to is None
    assert segment.tier == "sonnet"
    assert len(segment.attempts) == 2


# ---------------------------------------------------------------------------
# needs_context: acceptance criterion 3 -- first retries same tier, second
# escalates.
# ---------------------------------------------------------------------------


def test_first_needs_context_at_tier_retries_same_tier() -> None:
    segment = _segment(tier="haiku")

    result = record_failure(segment, ["needs_context: book-p0011-01"], kind="needs_context")

    assert result.retried_same_tier
    assert not result.exhausted
    assert result.escalated_to is None
    assert segment.tier == "haiku"
    assert len(segment.attempts) == 1


def test_second_needs_context_at_same_tier_escalates() -> None:
    segment = _segment(tier="haiku")
    record_failure(segment, ["needs_context: a"], kind="needs_context")

    result = record_failure(segment, ["needs_context: b"], kind="needs_context")

    assert not result.retried_same_tier
    assert result.escalated_to == "sonnet"
    assert segment.tier == "sonnet"
    assert len(segment.attempts) == 2


def test_second_needs_context_at_opus_is_exhausted() -> None:
    segment = _segment(tier="opus")
    record_failure(segment, ["needs_context: a"], kind="needs_context")

    result = record_failure(segment, ["needs_context: b"], kind="needs_context")

    assert result.exhausted
    assert segment.tier == "opus"


# ---------------------------------------------------------------------------
# escalate_existing_attempt: lazy self-healing at selection time.
# ---------------------------------------------------------------------------


def test_escalate_existing_attempt_advances_legacy_pending_segment() -> None:
    segment = _segment(
        tier="haiku",
        status="pending",
        attempts=[
            {"tier": "haiku", "timestamp": "2026-01-01T00:00:00+00:00", "errors": []},
        ],
    )

    result = escalate_existing_attempt(segment)

    assert result is not None
    assert result.escalated_to == "sonnet"
    assert segment.tier == "sonnet"
    # No new attempt appended -- the existing one is left as-is.
    assert len(segment.attempts) == 1


def test_escalate_existing_attempt_returns_none_when_no_attempt_at_current_tier() -> None:
    segment = _segment(tier="haiku", attempts=[])
    assert escalate_existing_attempt(segment) is None

    segment2 = _segment(
        tier="sonnet",
        attempts=[{"tier": "haiku", "timestamp": "2026-01-01T00:00:00+00:00", "errors": []}],
    )
    assert escalate_existing_attempt(segment2) is None


def test_escalate_existing_attempt_leaves_lone_needs_context_retry_alone() -> None:
    segment = _segment(
        tier="haiku",
        attempts=[
            {
                "tier": "haiku",
                "timestamp": "2026-01-01T00:00:00+00:00",
                "errors": ["needs_context: a"],
                "kind": "needs_context",
            }
        ],
    )

    assert escalate_existing_attempt(segment) is None
    assert segment.tier == "haiku"


def test_escalate_existing_attempt_advances_a_second_needs_context_left_unadvanced() -> None:
    segment = _segment(
        tier="haiku",
        attempts=[
            {
                "tier": "haiku",
                "timestamp": "2026-01-01T00:00:00+00:00",
                "errors": ["needs_context: a"],
                "kind": "needs_context",
            },
            {
                "tier": "haiku",
                "timestamp": "2026-01-01T00:00:01+00:00",
                "errors": ["needs_context: b"],
                "kind": "needs_context",
            },
        ],
    )

    result = escalate_existing_attempt(segment)

    assert result is not None
    assert result.escalated_to == "sonnet"
    assert segment.tier == "sonnet"


def test_escalate_existing_attempt_at_opus_reports_exhausted() -> None:
    segment = _segment(
        tier="opus",
        attempts=[{"tier": "opus", "timestamp": "2026-01-01T00:00:00+00:00", "errors": ["x"]}],
    )

    result = escalate_existing_attempt(segment)

    assert result is not None
    assert result.exhausted


# ---------------------------------------------------------------------------
# is_stale
# ---------------------------------------------------------------------------


def test_is_stale_true_after_60_minutes_in_progress() -> None:
    now = datetime(2026, 1, 1, 1, 0, 0, tzinfo=UTC)
    started = now - STALE_AFTER - timedelta(minutes=1)
    segment = _segment(status="in_progress", in_progress_since=started.isoformat())

    assert is_stale(segment, now)


def test_is_stale_false_within_60_minutes() -> None:
    now = datetime(2026, 1, 1, 1, 0, 0, tzinfo=UTC)
    started = now - timedelta(minutes=30)
    segment = _segment(status="in_progress", in_progress_since=started.isoformat())

    assert not is_stale(segment, now)


def test_is_stale_false_when_not_in_progress() -> None:
    now = datetime(2026, 1, 1, 1, 0, 0, tzinfo=UTC)
    segment = _segment(status="pending", in_progress_since=None)

    assert not is_stale(segment, now)
