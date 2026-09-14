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
  proficient with bucklers... Benefit: You can use a shield..."). A long
  name's column repair sometimes splits the bracketed type onto its own
  paragraph right after the name line instead of keeping it on the same
  line (e.g. PHB p0101's "SHOT ON THE RUN" / "[GENERAL]"); when the
  paragraph right after the name line is bracket-only
  (`owlsperch.segment.headings.BRACKET_ONLY_TAG_RE`), it is folded into the
  feat's heading ("SHOT ON THE RUN [GENERAL]") and the
  Prerequisite/Benefit lookahead starts after it, so the tag-only paragraph
  does not itself burn one of the `FEAT_LOOKAHEAD` slots.
- **table**: a line matching `Table <N>-<M>:`. Its extent runs forward
  while the following paragraphs are table-kind or prose paragraphs
  starting with a digit (a footnote), stopping at the first paragraph that
  is neither -- independent of any heading that may follow (only anchor
  detection elsewhere and `owlsperch.segment.splitter` cap it at the next
  trigger, as a safety net).
- **errata_entry**/**update_entry** (batch B11, design decisions D1/D2):
  gated entirely on the book's manifest `kind` (`errata`/`update`), passed
  in as `entry_kind`; every other book passes `entry_kind=None` and never
  produces this anchor at all, so a rulebook sentence like "(see the
  Player's Handbook, page 44)" can never create a bogus segment. When
  enabled, EVERY prose paragraph whose first `ERRATA_REF_MAX_PREFIX`
  characters (after whitespace normalization) contain a ", page <N>"
  reference, with a non-empty prefix of at most
  `ERRATA_HEADING_MAX_WORDS` words before the match, is one entry -- the
  whole errata/update booklet is one paragraph per entry (see the batch
  brief's ground truth). These triggers are detected FIRST, before table/
  spell/feat/stat_block, so an entry body that happens to contain a
  "Benefit:" cue can't be stolen by the feat anchor. The raw heading (the
  matched prefix) has a per-book common trailing phrase -- the target
  book's printed title -- stripped by `strip_common_heading_suffix`
  (design decision D3) before becoming the trigger's `heading`.

A trigger whose `start` falls strictly inside an earlier trigger's span is
simply never reached by `splitter.build_segments`'s forward-only sweep, so
overlap resolution needs no special handling here: the earlier trigger wins.
"""

from __future__ import annotations

import math
import re
import string
from dataclasses import dataclass
from typing import Literal

from owlsperch.segment.headings import BRACKET_ONLY_TAG_RE, Paragraph, is_heading

Kind = Literal["spell", "stat_block", "feat", "table", "errata_entry", "update_entry"]

#: Batch B11, design decision D2: the errata/update anchor's target
#: reference, e.g. ", page 236". Searched only within the first
#: `ERRATA_REF_MAX_PREFIX` characters of a paragraph's normalized text.
_ERRATA_REF_RE = re.compile(r",\s*page\s+(\d+)\b")

#: How many leading characters of a normalized paragraph to search for the
#: ", page N" reference.
ERRATA_REF_MAX_PREFIX = 120

#: The prefix before the ", page N" match must be non-empty and at most
#: this many words to count as a heading (rather than an incidental
#: mid-sentence page reference).
ERRATA_HEADING_MAX_WORDS = 12

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


def _normalize_word(word: str) -> str:
    """A word normalized for n-gram comparison in
    `strip_common_heading_suffix`: apostrophes removed, casefolded, then
    stripped of trailing punctuation."""
    word = word.replace("'", "").replace("’", "")
    return word.casefold().rstrip(string.punctuation)


def strip_common_heading_suffix(headings: list[str]) -> list[str]:
    """Batch B11, design decision D3: strip a per-book common trailing
    phrase (the target book's printed title, e.g. "Player's Handbook") from
    every errata/update anchor heading that ends with it. Computed
    deterministically from `headings` alone: for every trailing word n-gram
    (length 1..4) across all headings, the winner is the longest one whose
    count is at least `max(3, ceil(len(headings) / 2))`, ties broken by
    (count, then the n-gram tuple). With fewer than 3 headings, or no
    qualifying n-gram, nothing is stripped. A heading that would become
    empty after stripping is returned unstripped."""
    if len(headings) < 3:
        return list(headings)

    normalized = [h.split() for h in headings]
    normalized = [[_normalize_word(w) for w in words] for words in normalized]

    counts: dict[tuple[str, ...], int] = {}
    for words in normalized:
        for length in range(1, 5):
            if len(words) < length:
                continue
            ngram = tuple(words[-length:])
            counts[ngram] = counts.get(ngram, 0) + 1

    threshold = max(3, math.ceil(len(headings) / 2))
    winner: tuple[str, ...] | None = None
    for length in range(4, 0, -1):
        candidates = [
            (count, ngram)
            for ngram, count in counts.items()
            if len(ngram) == length and count >= threshold
        ]
        if not candidates:
            continue
        candidates.sort(key=lambda c: (-c[0], c[1]))
        winner = candidates[0][1]
        break

    if winner is None:
        return list(headings)

    result: list[str] = []
    for heading, words in zip(headings, normalized, strict=True):
        if tuple(words[-len(winner) :]) != winner:
            result.append(heading)
            continue
        stripped = heading.split()[: -len(winner)]
        result.append(" ".join(stripped) if stripped else heading)
    return result


def _find_errata_triggers(paragraphs: list[Paragraph], entry_kind: Kind | None) -> list[Trigger]:
    """Batch B11, design decisions D1/D2: every errata/update entry anchor
    in `paragraphs`, or `[]` when `entry_kind` is `None` (the book's
    manifest kind isn't `errata`/`update`)."""
    if entry_kind is None:
        return []

    raw: list[tuple[int, str]] = []
    for i, p in enumerate(paragraphs):
        if p.kind != "prose":
            continue
        text = " ".join(p.text.split())
        if not text:
            continue
        match = _ERRATA_REF_RE.search(text[:ERRATA_REF_MAX_PREFIX])
        if not match:
            continue
        prefix = text[: match.start()].strip()
        if not prefix or len(prefix.split()) > ERRATA_HEADING_MAX_WORDS:
            continue
        raw.append((i, prefix))

    if not raw:
        return []

    cleaned = strip_common_heading_suffix([prefix for _, prefix in raw])
    return [
        Trigger(start=i, kind=entry_kind, heading=heading)
        for (i, _), heading in zip(raw, cleaned, strict=True)
    ]


def find_triggers(
    paragraphs: list[Paragraph], body_median: float, *, entry_kind: Kind | None = None
) -> list[Trigger]:
    """Every anchor trigger in `paragraphs`, sorted by start index. See the
    module docstring for the per-kind rules and how overlaps resolve.
    `entry_kind` (batch B11) is `"errata_entry"`/`"update_entry"` for a book
    whose manifest `kind` is `errata`/`update`, else `None` -- gating
    whether an errata/update anchor can ever be produced (design decision
    D1)."""
    n = len(paragraphs)
    found: dict[int, Trigger] = {}

    # Errata/update: checked first, so an entry body that happens to
    # contain a "Benefit:" cue can't be stolen by the feat anchor.
    for trigger in _find_errata_triggers(paragraphs, entry_kind):
        found[trigger.start] = trigger

    # Table: checked first among the "regular" anchors (its caption pattern
    # is specific, and its extent should not be pre-empted by an accidental
    # name/school match nearby).
    for i, p in enumerate(paragraphs):
        if i in found:
            continue
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
    # paragraph as the cue). If the paragraph right after the name line is
    # itself a bracket-only tag (a long name's column repair split it onto
    # its own line), fold it into the heading and start the lookahead after
    # it instead of burning a lookahead slot on the tag-only paragraph.
    for i in range(n):
        if i in found or not _is_feat_name_line(paragraphs[i]):
            continue
        heading = paragraphs[i].text.strip()
        lookahead_start = i + 1
        if lookahead_start < n:
            tag_paragraph = paragraphs[lookahead_start]
            if (
                tag_paragraph.kind == "prose"
                and tag_paragraph.line_count == 1
                and BRACKET_ONLY_TAG_RE.match(tag_paragraph.text.strip())
            ):
                heading = f"{heading} {tag_paragraph.text.strip()}"
                lookahead_start += 1
        for j in range(lookahead_start, min(lookahead_start + FEAT_LOOKAHEAD, n)):
            candidate = paragraphs[j]
            if candidate.kind == "prose" and _PREREQ_RE.search(candidate.text):
                found[i] = Trigger(start=i, kind="feat", heading=heading)
                break

    # Stat block: "Size/Type:"/"Hit Dice:" line, name backdated.
    for j, p in enumerate(paragraphs):
        if p.kind != "prose" or not _STAT_BLOCK_RE.match(p.text.strip()):
            continue
        start, heading = _stat_block_name(paragraphs, j, body_median)
        if start not in found:
            found[start] = Trigger(start=start, kind="stat_block", heading=heading)

    return sorted(found.values(), key=lambda t: t.start)
