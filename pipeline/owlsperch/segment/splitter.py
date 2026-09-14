"""Splits a book-wide paragraph stream into segments (spec 4.4; batch B3).

`build_segments` walks `owlsperch.segment.headings.Paragraph`s once, left to
right: at each anchor trigger (`owlsperch.segment.anchors.find_triggers`) it
closes out any pending plain-text run as one or more `rules_section`
segments (split at headings, per acceptance criterion 2), then emits the
anchor's own segment running until whichever comes first of the next
trigger or the next heading (a table anchor instead uses its own
self-terminating extent, capped at the next trigger as a safety net -- see
`anchors.Trigger.table_end`). Any trailing plain-text run after the last
trigger is flushed the same way. This is what makes segments page-spanning
"for free": the whole book (or `--pages` range) is one flat stream, so an
anchor or heading only ends a segment when it is actually reached, page
boundaries notwithstanding (acceptance criterion 4).

A `RawSegment` never has `start == end` (a zero-length span is never
emitted); the caller (`owlsperch.segment.runner`) additionally drops any
segment whose joined text is whitespace-only, per acceptance criterion 5.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from owlsperch.segment.anchors import Trigger, find_triggers
from owlsperch.segment.headings import Paragraph, is_heading

#: Batch B10c adds "class"/"prestige_class" -- these are never produced by
#: `build_segments` itself (they're a separate, toc-driven pass -- see
#: `owlsperch.segment.runner`'s class-span post-pass); the literal is
#: widened here only so `Segment.kind_hint` accepts them too.
KindHint = Literal[
    "spell", "stat_block", "feat", "table", "rules_section", "class", "prestige_class"
]


@dataclass(frozen=True)
class RawSegment:
    kind: KindHint
    start: int
    end: int  # exclusive
    heading: str


def _index_of_next_heading(
    paragraphs: list[Paragraph], start: int, limit: int, body_median: float
) -> int:
    """First index in `[start, limit)` that is a heading, else `limit`."""
    for i in range(start, limit):
        if is_heading(paragraphs[i], body_median):
            return i
    return limit


def _flush_rules_section_run(
    segments: list[RawSegment],
    paragraphs: list[Paragraph],
    start: int,
    end: int,
    heading_state: str,
    body_median: float,
) -> str:
    """Emit `[start, end)` as one or more `rules_section` segments, split at
    every heading paragraph inside it. Returns the heading in effect at
    `end` (for the caller to carry into whatever comes next)."""
    seg_start = start
    current_heading = heading_state
    for i in range(start, end):
        if is_heading(paragraphs[i], body_median):
            if i > seg_start:
                segments.append(RawSegment("rules_section", seg_start, i, current_heading))
            current_heading = paragraphs[i].text.strip()
            seg_start = i
    if end > seg_start:
        segments.append(RawSegment("rules_section", seg_start, end, current_heading))
    return current_heading


def build_segments(paragraphs: list[Paragraph], body_median: float) -> list[RawSegment]:
    """Every segment (anchors and the `rules_section`s between them) for
    `paragraphs`, in stream order."""
    n = len(paragraphs)
    triggers = find_triggers(paragraphs, body_median)
    trigger_by_start: dict[int, Trigger] = {t.start: t for t in triggers}
    trigger_starts = sorted(trigger_by_start)

    def next_trigger_start(after: int) -> int:
        for s in trigger_starts:
            if s >= after:
                return s
        return n

    segments: list[RawSegment] = []
    heading_state = ""
    pending_start = 0
    cursor = 0

    while cursor < n:
        trigger = trigger_by_start.get(cursor)
        if trigger is None:
            cursor += 1
            continue

        if cursor > pending_start:
            heading_state = _flush_rules_section_run(
                segments, paragraphs, pending_start, cursor, heading_state, body_median
            )

        limit = next_trigger_start(cursor + 1)
        if trigger.kind == "table":
            end = min(trigger.table_end, limit)
        else:
            end = _index_of_next_heading(paragraphs, cursor + 1, limit, body_median)
        segments.append(RawSegment(trigger.kind, cursor, end, trigger.heading))
        cursor = end
        pending_start = end

    if n > pending_start:
        _flush_rules_section_run(segments, paragraphs, pending_start, n, heading_state, body_median)

    return segments
