"""The book-wide paragraph stream and heading detection (spec 4.4; batch B3).

`owlsperch.text.runner` writes one `p{NNNN}.txt` (blank-line-separated
paragraphs) and one `p{NNNN}.meta.json` (one entry per paragraph, in the same
order, giving `{kind, median_word_height, max_word_height, line_count}`) per
page -- see that module's docstring. `Paragraph` here is the two joined: one
paragraph, tagged with the page it came from and its meta stats, so a whole
book (or `--pages`-limited range) can be treated as a single flat stream for
segmentation, since a segment may span a page boundary (acceptance criterion
4).

**Heading detection.** The plain `.txt` files carry no font information, so
a heading is defined entirely from the `.meta.json` stats: a single-line
prose paragraph (`kind == "prose"`, `line_count == 1`) is a heading when
either:

- its `median_word_height` is at least `HEADING_HEIGHT_FACTOR` (1.15) times
  the book's *body median* word height -- the median of `median_word_height`
  across every paragraph (prose or table) with `line_count >=
  BODY_MIN_LINE_COUNT` (3), a proxy for "ordinary running text" that a short
  heading or anchor line would not have enough lines to be counted in; or
- it is entirely uppercase and at most `HEADING_MAX_WORDS` (8) words,
  regardless of measured height -- catches headings the height heuristic
  alone might miss (e.g. a short window with too few body paragraphs to set
  a reliable baseline).

A paragraph that consists solely of a bracketed tag (`BRACKET_ONLY_TAG_RE`,
e.g. "[GENERAL]", "[METAMAGIC]", "[ITEM CREATION]") is never a heading,
regardless of height or the all-caps rule above. A long feat name's
column-repair can split its bracketed type onto its own paragraph right
after the name line (e.g. PHB p0101's "SHOT ON THE RUN" / "[GENERAL]"); such
a tag-only line is all-caps and short enough to otherwise match the all-caps
rule, which would wrongly end the feat's segment right after its name (see
`owlsperch.segment.anchors`, which folds the tag into the feat's own
heading instead).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

#: A paragraph's median_word_height must be at least this multiple of the
#: book's body median to count as a font-size heading.
HEADING_HEIGHT_FACTOR = 1.15

#: Minimum physical line count for a paragraph to count towards the body
#: median (see module docstring).
BODY_MIN_LINE_COUNT = 3

#: An all-uppercase single-line paragraph of at most this many words is a
#: heading regardless of its measured height.
HEADING_MAX_WORDS = 8

#: A paragraph consisting solely of a bracketed tag, e.g. "[GENERAL]" or
#: "[ITEM CREATION]" -- never a heading (see module docstring). Exposed
#: (not underscore-prefixed) so `owlsperch.segment.anchors` can recognize
#: the same shape when folding a feat's tag-only paragraph into its heading.
BRACKET_ONLY_TAG_RE = re.compile(r"^\[[A-Za-z ,]+\]$")

ParagraphKind = Literal["prose", "table"]


@dataclass(frozen=True)
class Paragraph:
    """One paragraph in the book-wide segmentation stream."""

    page: int
    text: str
    kind: ParagraphKind
    median_word_height: float
    max_word_height: float
    line_count: int


def compute_body_median(paragraphs: list[Paragraph]) -> float:
    """The body-text median word height for `paragraphs` (see module
    docstring). Falls back to the median across every paragraph when none
    has `line_count >= BODY_MIN_LINE_COUNT` (e.g. a very short `--pages`
    window with no multi-line paragraphs at all)."""
    candidates = [p.median_word_height for p in paragraphs if p.line_count >= BODY_MIN_LINE_COUNT]
    pool = candidates or [p.median_word_height for p in paragraphs]
    if not pool:
        return 0.0
    ordered = sorted(pool)
    return ordered[len(ordered) // 2]


def is_heading(paragraph: Paragraph, body_median: float) -> bool:
    """Whether `paragraph` is a heading (see module docstring)."""
    if paragraph.kind != "prose" or paragraph.line_count != 1:
        return False
    text = paragraph.text.strip()
    if not text:
        return False
    if BRACKET_ONLY_TAG_RE.match(text):
        return False
    if body_median > 0 and paragraph.median_word_height >= HEADING_HEIGHT_FACTOR * body_median:
        return True
    words = text.split()
    if len(words) > HEADING_MAX_WORDS:
        return False
    return text.upper() == text and any(c.isalpha() for c in text)
