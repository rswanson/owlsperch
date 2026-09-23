"""Running header/footer removal, printed-page-number detection, and
hyphenated line-break repair.

**Header/footer removal.** For every line in the top or bottom
`BAND_FRACTION` (8%) of a page's height, its *normalized* text (digits
stripped, whitespace collapsed, lowercased -- `normalize_for_repetition`) is
counted per (band, normalized text) key across every page passed in for this
run. Any key recurring on at least `RUNNING_LINE_THRESHOLD` (30%) of the
pages is a running header/footer and every line producing that key is
dropped from the page text. Digit-only lines (a lone page number, e.g.
"118") normalize to the empty string, so a page-number-only line in the band
is removed by this same rule as soon as *most* pages carry one -- no special
case needed.

**Printed page number.** Independently of the frequency rule above, a line
in the header or footer band whose text (after stripping surrounding
whitespace) is exactly a run of digits is recorded as that PDF page's
printed page number, regardless of whether it recurs. Such lines are always
dropped from the body text (a bare page number is never body content).

**Display page number in the outer margin (batch B12).** The band rule above
finds nothing in a book that sets its page number as a large decorative
glyph *above* the footer band, out in the outer margin -- which is exactly
what the Monster Manual does: every body page carries its number as its own
one-line block, ~30pt tall (vs. a ~9.5pt body line), at yMin ~= 0.911 of the
page height, so `band_for_line` returns `None` for it and `owlsperch text
mm1` reported "0 page numbers found" for all 334 pages. `display_page_number`
is the narrow second rule for it: a block whose ONLY non-blank line is
exactly a run of digits, sitting in the bottom `DISPLAY_BAND_FRACTION` (12%)
of the page, whose line height is at least `DISPLAY_HEIGHT_RATIO` (1.8)
times the page's own median line height (`median_line_height`). Both extra
conditions are load-bearing: a numeric TABLE CELL low on a page is its own
digits-only block too (MM p0300 prints an advancement table's "5"/"10"/"15"
at 8.4pt), and only the height test rejects it. The band rule keeps
priority -- `display_page_number` is consulted only for a page it found no
number on -- so a book it already handles (the PHB, whose same decorative
number usually *does* fall inside the footer band) keeps exactly the numbers
it had. A block detected this way is dropped from the body text too, for the
same reason the band rule drops one: a bare page number is never body
content (in the MM it left a bare "100"/"200"/... paragraph on 236 pages,
since a 3-digit number's block is wider than it is tall and so isn't even
excluded as a rotated/vertical block).

**Dehyphenation.** A line ending in a word broken by a trailing hyphen
("com-") is joined with the next line's first word ("posite"). The
hyphen is dropped (`composite`) if the joined, lowercased word occurs
elsewhere in the book's own text (collected over the whole run) or in the
bundled word list (`wordlist.txt`); otherwise the hyphen is kept and the two
fragments are still joined with no space ("com-posite"). Either way the two
physical lines become one -- a literal line break at a hyphenated word is
never left in the output. The known-word lookup strips any punctuation
attached to the continuation word (e.g. the trailing comma in "bow,") first,
since `known_words` itself holds punctuation-stripped words; the joined
output word still carries that punctuation ("crossbow,"), it is just not
part of the lookup key.

**Running-header/footer threshold caveat.** The 30% frequency rule above
assumes a run of many pages. Over a short `--pages` window (fewer than 20
pages), 30% of the window can be as low as one page, which would treat any
line that merely happens to repeat once as a running header/footer. To
avoid that, a window of fewer than 20 pages instead requires a line to
recur on at least `MIN_RUNNING_PAGE_COUNT` (3) pages, regardless of what
30% of the window works out to.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from owlsperch.text.bbox import Block, Line, Page

Band = Literal["header", "footer"]

#: Top/bottom fraction of page height counted as the header/footer band.
BAND_FRACTION = 0.08

#: A (band, normalized text) recurring on at least this fraction of a run's
#: pages is treated as a running header/footer.
RUNNING_LINE_THRESHOLD = 0.30

#: Below this many pages in a run, the frequency rule alone is too easily
#: tripped by coincidence (see the module docstring's threshold caveat) --
#: a minimum absolute recurrence is required instead.
SHORT_WINDOW_PAGE_COUNT = 20

#: Minimum number of pages a line must recur on to count as a running
#: header/footer when the run is shorter than `SHORT_WINDOW_PAGE_COUNT`.
MIN_RUNNING_PAGE_COUNT = 3

#: Batch B12: bottom fraction of the page a DISPLAY page number (see the
#: module docstring) may sit in -- wider than `BAND_FRACTION` on purpose,
#: since the whole point is that this number sits above the footer band.
DISPLAY_BAND_FRACTION = 0.12

#: Batch B12: how many times the page's own median line height a display
#: page number's line must be. The Monster Manual's is ~3.2x; a numeric
#: table cell in the same band is ~0.9x.
DISPLAY_HEIGHT_RATIO = 1.8

_DIGIT_RE = re.compile(r"\d")
_WHITESPACE_RE = re.compile(r"\s+")
_STANDALONE_INTEGER_RE = re.compile(r"^\d+$")
_WORD_PUNCT_RE = re.compile(r"^[^A-Za-z0-9]+|[^A-Za-z0-9]+$")

_WORDLIST_PATH = Path(__file__).resolve().parent / "wordlist.txt"


def strip_word_punctuation(word: str) -> str:
    """Strip leading/trailing non-alphanumeric characters (punctuation
    attached to a word by the PDF's word segmentation), keeping internal
    characters like a mid-word hyphen or apostrophe."""
    return _WORD_PUNCT_RE.sub("", word)


def _running_threshold(page_count: int) -> float:
    """Minimum recurrence count for a (band, text) key to be treated as a
    running header/footer, given a run of `page_count` pages (see the
    module docstring's threshold caveat)."""
    if page_count < SHORT_WINDOW_PAGE_COUNT:
        return max(MIN_RUNNING_PAGE_COUNT, math.ceil(RUNNING_LINE_THRESHOLD * page_count))
    return RUNNING_LINE_THRESHOLD * page_count


def normalize_for_repetition(text: str) -> str:
    """Digits stripped, whitespace collapsed, lowercased."""
    stripped = _DIGIT_RE.sub("", text)
    collapsed = _WHITESPACE_RE.sub(" ", stripped).strip()
    return collapsed.lower()


def band_for_line(line: Line, page_height: float) -> Band | None:
    """Which header/footer band `line` falls in, if any."""
    if page_height <= 0:
        return None
    if line.y_max <= page_height * BAND_FRACTION:
        return "header"
    if line.y_min >= page_height * (1 - BAND_FRACTION):
        return "footer"
    return None


def standalone_page_number(text: str) -> int | None:
    """The printed page number if `text` (a whole line's text) is exactly a
    run of digits, else None."""
    candidate = text.strip()
    if _STANDALONE_INTEGER_RE.match(candidate):
        return int(candidate)
    return None


def median_line_height(page: Page) -> float:
    """The median height of every non-blank line on `page` -- the "ordinary
    body line" baseline `display_page_number` measures an oversize display
    glyph against. 0.0 for a page with no text at all."""
    heights = sorted(
        line.height for block in page.blocks for line in block.lines if line.text.strip()
    )
    if not heights:
        return 0.0
    return heights[len(heights) // 2]


def display_page_number(block: Block, *, page_height: float, body_line_height: float) -> int | None:
    """The printed page number if `block` is a DISPLAY page number set in the
    outer margin (batch B12, see the module docstring): its only non-blank
    line is exactly a run of digits, it sits in the bottom
    `DISPLAY_BAND_FRACTION` of the page, and that line is at least
    `DISPLAY_HEIGHT_RATIO` times as tall as `body_line_height` (the page's
    own median line height -- a `body_line_height` of 0, i.e. a page with no
    other text at all, skips that last test). Else `None`."""
    if page_height <= 0:
        return None
    lines = [line for line in block.lines if line.text.strip()]
    if len(lines) != 1:
        return None
    line = lines[0]
    number = standalone_page_number(line.text)
    if number is None:
        return None
    if line.y_min < page_height * (1 - DISPLAY_BAND_FRACTION):
        return None
    if body_line_height > 0 and line.height < DISPLAY_HEIGHT_RATIO * body_line_height:
        return None
    return number


@dataclass(frozen=True)
class BandedLine:
    """One line of a page, tagged with its band (if any) for the running
    header/footer pass."""

    page_index: int
    band: Band | None
    text: str


def find_running_keys(banded_lines: list[BandedLine], page_count: int) -> set[tuple[Band, str]]:
    """(band, normalized text) keys recurring on >= RUNNING_LINE_THRESHOLD of
    `page_count` pages."""
    if page_count == 0:
        return set()
    # Count each key at most once per page (a repeated line on the same page
    # in the same band should not inflate the count beyond that one page).
    seen_per_page: set[tuple[int, Band, str]] = set()
    counts: Counter[tuple[Band, str]] = Counter()
    for line in banded_lines:
        if line.band is None:
            continue
        key = (line.page_index, line.band, normalize_for_repetition(line.text))
        if key in seen_per_page:
            continue
        seen_per_page.add(key)
        counts[(line.band, normalize_for_repetition(line.text))] += 1

    threshold = _running_threshold(page_count)
    return {key for key, count in counts.items() if count >= threshold}


def load_wordlist(path: Path | None = None) -> set[str]:
    path = path if path is not None else _WORDLIST_PATH
    words = {w.strip().lower() for w in path.read_text().splitlines() if w.strip()}
    return words


def dehyphenate_lines(lines: list[str], known_words: set[str]) -> str:
    """Join `lines` (already header/footer-stripped, in reading order within
    one block) into one continuous string, rejoining hyphenated line breaks
    per the module docstring. `known_words` should already be lowercased.

    A physical line break is never left in the output: when the previous
    line's last word ends in `-`, it is always merged with the next line's
    first word, with or without the hyphen depending on `known_words`.
    """
    words: list[str] = []
    for line in lines:
        line_words = line.split()
        if not line_words:
            continue
        if words and len(words[-1]) > 1 and words[-1].endswith("-"):
            fragment = words[-1][:-1]
            continuation = line_words[0]
            # `known_words` holds punctuation-stripped words (see
            # `owlsperch.text.runner._tokenize_words`), so the lookup must
            # strip punctuation from the continuation too (e.g. "bow," ->
            # "bow") or a known word broken across a hyphen and followed by
            # punctuation would never match. The un-stripped `continuation`
            # is still used below to build the output, so its punctuation
            # (a trailing comma, say) is preserved either way.
            joined = (fragment + strip_word_punctuation(continuation)).lower()
            if joined in known_words:
                words[-1] = fragment + continuation
            else:
                # Keep the hyphen, but still merge the two lines into one
                # word: "com-" + "posite" -> "com-posite".
                words[-1] = words[-1] + continuation
            words.extend(line_words[1:])
        else:
            words.extend(line_words)
    return " ".join(words)
