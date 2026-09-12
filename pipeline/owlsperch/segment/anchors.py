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
- **feat**: a short (<= 8 words) name line, optionally ending in a bracketed
  type (e.g. "Power Attack [General]"), whose name portion is all-caps or
  Title Case, followed within `FEAT_LOOKAHEAD` (2) paragraphs by a paragraph
  that *contains* "Prerequisite:"/"Prerequisites:"/"Benefit:" anywhere --
  not necessarily at its start, since a book's column repair often keeps a
  lead-in sentence in the same paragraph as the cue (e.g. "You are
  proficient with bucklers... Benefit: You can use a shield...").
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
    r"|Necromancy|Transmutation|Universal)(?:\s*(?:\([^)]*\)|\[[^\]]*\]))*(?=\s|$)"
)
_LEVEL_CUE_RE = re.compile(r"\bLevel\s*:")
_STAT_BLOCK_RE = re.compile(r"^(Size/Type|Hit Dice)\s*:", re.IGNORECASE)
#: A feat's "Prerequisite:"/"Benefit:" cue, searched *anywhere* in a
#: paragraph -- not just at its start. A real book's column repair often
#: merges a lead-in sentence together with "Benefit:" (or "Prerequisite:")
#: into one paragraph, e.g. "You are proficient with bucklers... Benefit:
#: You can use a shield..." (see `_is_feat_name_line`/the **feat** rule in
#: the module docstring).
_PREREQ_RE = re.compile(r"\b(Prerequisites?|Benefit):", re.IGNORECASE)
_TABLE_CAPTION_RE = re.compile(r"^Table\s+\d+[–-]\d+\s*:")
#: A feat name line's optional trailing bracketed type, e.g. "[General]" or
#: "[Fighter]" (see `_is_feat_name_line`).
_BRACKET_SUFFIX_RE = re.compile(r"\s*\[[^\]]*\]$")

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


def _is_title_case_or_upper(text: str) -> bool:
    """Whether every word in `text` is capitalized (Title Case), or `text`
    is entirely uppercase -- the case shapes a feat's own name line takes
    (see `_is_feat_name_line`). Punctuation-only "words" (e.g. a lone "&")
    are ignored either way."""
    if not text:
        return False
    if text.upper() == text:
        return True
    for word in text.split():
        letters = [c for c in word if c.isalpha()]
        if letters and letters[0] != letters[0].upper():
            return False
    return True


def _is_feat_name_line(paragraph: Paragraph) -> bool:
    """Whether `paragraph` is a feat's own name line (see module docstring's
    **feat** rule): a short (<= `SHORT_LINE_MAX_WORDS`) single-line prose
    paragraph, optionally ending in a bracketed type (e.g. "[General]" or
    "[Fighter]"), whose name portion is either all-caps or Title Case. This
    is stricter than `_is_short_line` (which allows any case) specifically
    so that loosening the "Prerequisite:"/"Benefit:" lookahead check to
    search anywhere in a paragraph (not just its start) doesn't turn
    ordinary short sentences into false feat anchors."""
    if not _is_short_line(paragraph):
        return False
    text = paragraph.text.strip()
    name = _BRACKET_SUFFIX_RE.sub("", text).strip()
    if not name:
        return False
    return _is_title_case_or_upper(name)


def _is_school_line(paragraph: Paragraph) -> bool:
    """Whether `paragraph` opens with a school pattern (see module
    docstring's **spell** rule). Checked as a *prefix*, not a whole-paragraph
    match: a real book's column repair frequently merges the school line
    together with the spell's Level/Components/... stat-block lines into one
    multi-line paragraph rather than keeping it alone. A single-line
    paragraph that is only the school pattern is unambiguous; a longer one
    must also show a "Level:" cue somewhere, so an ordinary sentence that
    merely starts with a school-shaped word (only "Universal" is a real
    English word) is not mistaken for a spell header."""
    if paragraph.kind != "prose":
        return False
    text = paragraph.text.strip()
    if not _SCHOOL_RE.match(text):
        return False
    return paragraph.line_count == 1 or bool(_LEVEL_CUE_RE.search(text))


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

    # Feat: short name line, "Prerequisite:"/"Benefit:" anywhere within the
    # next FEAT_LOOKAHEAD paragraphs (not necessarily at their start -- a
    # real book's column repair often keeps a lead-in sentence in the same
    # paragraph as the cue).
    for i in range(n):
        if i in found or not _is_feat_name_line(paragraphs[i]):
            continue
        for j in range(i + 1, min(i + 1 + FEAT_LOOKAHEAD, n)):
            candidate = paragraphs[j]
            if candidate.kind == "prose" and _PREREQ_RE.search(candidate.text):
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
