"""Pattern-anchor detection for `owlsperch segment` (spec 4.4; batch B3).

Given the book-wide paragraph stream (`owlsperch.segment.headings.
Paragraph`), find every anchor: a spell, stat block, feat, or table
recognized from a small set of literal patterns in adjacent paragraphs.
Each `Trigger` names the kind, the paragraph index it starts at, the text
to use as the segment's `heading`, and (table only) the index its own
self-terminating extent runs to.

- **spell**: a short name line (`_is_short_line`) immediately followed by a
  school line -- one of the eight schools plus "Universal", optionally
  followed by a parenthesised subschool and/or a bracketed descriptor, e.g.
  "Evocation [Fire]" or "Conjuration (Creation)".
- **stat_block**: a line starting "Size/Type:" or "Hit Dice:". Its `start`
  is backdated to the nearest preceding heading or short line (the
  monster's name), scanning back at most `STAT_BLOCK_LOOKBACK` paragraphs;
  if none is found, the stat-block line itself is used as a last resort.
- **feat**: a short name line (optionally with a bracketed type, e.g.
  "Power Attack [General]") followed within `FEAT_LOOKAHEAD` (2) paragraphs
  by a line starting "Prerequisite" or "Benefit".
- **table**: a line matching `Table <N>-<M>:`. Its extent runs forward
  while the following paragraphs are table-kind or prose paragraphs
  starting with a digit (a footnote), stopping at the first paragraph that
  is neither -- independent of any heading that may follow (only anchor
  detection elsewhere and `owlsperch.segment.splitter` cap it at the next
  trigger, as a safety net).

A trigger whose `start` falls strictly inside an earlier trigger's span is
simply never reached by `splitter.build_segments`'s forward-only sweep, so
overlap resolution needs no special handling here: the earlier trigger wins.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from owlsperch.segment.headings import Paragraph, is_heading

Kind = Literal["spell", "stat_block", "feat", "table"]

_SCHOOL_RE = re.compile(
    r"^(?:Abjuration|Conjuration|Divination|Enchantment|Evocation|Illusion"
    r"|Necromancy|Transmutation|Universal)(?:\s*(?:\([^)]*\)|\[[^\]]*\]))*\s*$"
)
_STAT_BLOCK_RE = re.compile(r"^(Size/Type|Hit Dice)\s*:", re.IGNORECASE)
_PREREQ_RE = re.compile(r"^(Prerequisite|Benefit)\b", re.IGNORECASE)
_TABLE_CAPTION_RE = re.compile(r"^Table\s+\d+[–-]\d+\s*:")

#: A name/heading line candidate has at most this many words.
SHORT_LINE_MAX_WORDS = 8

#: How many paragraphs after a feat's name line to look for
#: "Prerequisite"/"Benefit".
FEAT_LOOKAHEAD = 2

#: How many paragraphs before a "Size/Type:"/"Hit Dice:" line to look back
#: for the monster's name (a heading or short line).
STAT_BLOCK_LOOKBACK = 6


@dataclass(frozen=True)
class Trigger:
    start: int
    kind: Kind
    heading: str
    #: Exclusive end of a table's self-terminating extent; unused (-1) for
    #: every other kind, whose extent is computed by the splitter instead.
    table_end: int = -1


def _is_short_line(paragraph: Paragraph) -> bool:
    if paragraph.kind != "prose" or paragraph.line_count != 1:
        return False
    text = paragraph.text.strip()
    if not text or len(text.split()) > SHORT_LINE_MAX_WORDS:
        return False
    # Exclude lines that are themselves another anchor's marker line -- e.g.
    # a "Size/Type:" line is a 4-word single line that would otherwise also
    # look like a perfectly good "short name line" to a neighboring spell's
    # or feat's own name/lookback check.
    if _STAT_BLOCK_RE.match(text) or _PREREQ_RE.match(text) or _TABLE_CAPTION_RE.match(text):
        return False
    return True


def _is_school_line(paragraph: Paragraph) -> bool:
    return (
        paragraph.kind == "prose"
        and paragraph.line_count == 1
        and bool(_SCHOOL_RE.match(paragraph.text.strip()))
    )


def _table_extent(paragraphs: list[Paragraph], start: int) -> int:
    """Exclusive end index of the table beginning right after its caption
    at `start` (see module docstring's **table** rule)."""
    i = start
    n = len(paragraphs)
    while i < n:
        p = paragraphs[i]
        if p.kind == "table":
            i += 1
            continue
        text = p.text.strip()
        if text and text[0].isdigit():
            i += 1
            continue
        break
    return i


def _stat_block_name(
    paragraphs: list[Paragraph], stat_line_index: int, body_median: float
) -> tuple[int, str]:
    """The (start index, heading text) for a stat-block anchor whose
    "Size/Type:"/"Hit Dice:" line is at `stat_line_index` (see module
    docstring's **stat_block** rule)."""
    lookback_floor = max(0, stat_line_index - STAT_BLOCK_LOOKBACK)
    for i in range(stat_line_index - 1, lookback_floor - 1, -1):
        candidate = paragraphs[i]
        if is_heading(candidate, body_median) or _is_short_line(candidate):
            return i, candidate.text.strip()
    return stat_line_index, paragraphs[stat_line_index].text.strip()


def find_triggers(paragraphs: list[Paragraph], body_median: float) -> list[Trigger]:
    """Every anchor trigger in `paragraphs`, sorted by start index. See the
    module docstring for the per-kind rules and how overlaps resolve."""
    n = len(paragraphs)
    found: dict[int, Trigger] = {}

    # Table: checked first (its caption pattern is specific, and its extent
    # should not be pre-empted by an accidental name/school match nearby).
    for i, p in enumerate(paragraphs):
        if p.kind == "prose" and p.line_count == 1 and _TABLE_CAPTION_RE.match(p.text.strip()):
            end = _table_extent(paragraphs, i + 1)
            found[i] = Trigger(start=i, kind="table", heading=p.text.strip(), table_end=end)

    # Spell: short name line immediately followed by a school line.
    for i in range(n - 1):
        if i in found:
            continue
        if _is_short_line(paragraphs[i]) and _is_school_line(paragraphs[i + 1]):
            found[i] = Trigger(start=i, kind="spell", heading=paragraphs[i].text.strip())

    # Feat: short name line, "Prerequisite"/"Benefit" within FEAT_LOOKAHEAD.
    for i in range(n):
        if i in found or not _is_short_line(paragraphs[i]):
            continue
        for j in range(i + 1, min(i + 1 + FEAT_LOOKAHEAD, n)):
            candidate = paragraphs[j]
            if candidate.kind == "prose" and _PREREQ_RE.match(candidate.text.strip()):
                found[i] = Trigger(start=i, kind="feat", heading=paragraphs[i].text.strip())
                break

    # Stat block: "Size/Type:"/"Hit Dice:" line, name backdated.
    for j, p in enumerate(paragraphs):
        if p.kind != "prose" or not _STAT_BLOCK_RE.match(p.text.strip()):
            continue
        start, heading = _stat_block_name(paragraphs, j, body_median)
        if start not in found:
            found[start] = Trigger(start=start, kind="stat_block", heading=heading)

    return sorted(found.values(), key=lambda t: t.start)
