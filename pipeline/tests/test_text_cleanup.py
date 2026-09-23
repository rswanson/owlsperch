"""Unit tests for `owlsperch.text.cleanup`: running header/footer detection,
printed page-number detection, and dehyphenation."""

from __future__ import annotations

from owlsperch.text.bbox import Block, Line, Page, Word
from owlsperch.text.cleanup import (
    BandedLine,
    band_for_line,
    dehyphenate_lines,
    display_page_number,
    find_running_keys,
    load_wordlist,
    median_line_height,
    normalize_for_repetition,
    standalone_page_number,
)


def test_normalize_strips_digits_collapses_whitespace_lowercases() -> None:
    assert normalize_for_repetition("  PLAYER'S   Handbook  12 ") == "player's handbook"
    assert normalize_for_repetition("118") == ""


def test_standalone_page_number_matches_whole_line_only() -> None:
    assert standalone_page_number("118") == 118
    assert standalone_page_number("  118  ") == 118
    assert standalone_page_number("Chapter 118") is None
    assert standalone_page_number("") is None


def test_band_for_line_top_and_bottom_eight_percent() -> None:
    page_height = 800.0

    header = Line(x_min=0, y_min=0, x_max=100, y_max=30)  # within top 8% (64)
    footer = Line(x_min=0, y_min=760, x_max=100, y_max=780)  # within bottom 8% (736-800)
    body = Line(x_min=0, y_min=400, x_max=100, y_max=420)

    assert band_for_line(header, page_height) == "header"
    assert band_for_line(footer, page_height) == "footer"
    assert band_for_line(body, page_height) is None


def test_find_running_keys_requires_30_percent_of_pages() -> None:
    # 4 pages: a footer reading "PLAYER'S HANDBOOK" on 3/4 pages (75% >= 30%)
    # and one-off footer text appearing on only 1/4 pages (25% < 30%).
    lines = [
        BandedLine(1, "footer", "Player's Handbook"),
        BandedLine(2, "footer", "Player's Handbook"),
        BandedLine(3, "footer", "Player's Handbook"),
        BandedLine(4, "footer", "Unique One-Off Footer"),
    ]

    running = find_running_keys(lines, page_count=4)

    assert ("footer", "player's handbook") in running
    assert ("footer", "unique one-off footer") not in running


def test_find_running_keys_ignores_duplicate_lines_within_one_page() -> None:
    # Same normalized text appearing twice on one page must not count twice
    # towards the page-frequency threshold.
    lines = [
        BandedLine(1, "header", "Repeat"),
        BandedLine(1, "header", "Repeat"),
    ]
    running = find_running_keys(lines, page_count=4)
    # Only 1 of 4 pages (25%) actually carries it -- below the 30% threshold.
    assert ("header", "repeat") not in running


def test_dehyphenate_joins_when_word_is_known() -> None:
    known = {"composite"}
    result = dehyphenate_lines(["a com-", "posite bow"], known)
    assert result == "a composite bow"


def test_dehyphenate_keeps_hyphen_when_word_is_unknown() -> None:
    known: set[str] = set()
    result = dehyphenate_lines(["a com-", "posite bow"], known)
    assert result == "a com-posite bow"


def test_dehyphenate_preserves_multiple_lines_without_hyphenation() -> None:
    result = dehyphenate_lines(["Line one text", "Line two text"], set())
    assert result == "Line one text Line two text"


def test_dehyphenate_strips_continuation_punctuation_for_known_word_lookup() -> None:
    # "cross-" + "bow," must match "crossbow" in known_words even though the
    # continuation carries a trailing comma -- known_words itself holds
    # punctuation-stripped words, so the lookup key must be stripped too.
    # The comma is still preserved in the joined output.
    known = {"crossbow"}
    result = dehyphenate_lines(["a cross-", "bow, ready"], known)
    assert result == "a crossbow, ready"


def test_wordlist_is_bundled_and_nonempty() -> None:
    words = load_wordlist()
    assert len(words) > 500
    assert all(w == w.lower() for w in words)


def test_wordlist_has_at_least_4000_common_words() -> None:
    words = load_wordlist()
    assert len(words) >= 4000
    assert "anybody" in words
    assert "therefore" in words


def test_find_running_keys_short_window_requires_minimum_three_pages() -> None:
    # A 5-page window: 30% of 5 is 1.5, so the raw percentage rule alone
    # would treat a line recurring on just 2 pages as a running
    # header/footer. The short-window floor (>= 3 pages) must prevent that.
    two_pages = [
        BandedLine(1, "footer", "Repeat"),
        BandedLine(2, "footer", "Repeat"),
    ]
    assert ("footer", "repeat") not in find_running_keys(two_pages, page_count=5)

    three_pages = [
        BandedLine(1, "footer", "Repeat"),
        BandedLine(2, "footer", "Repeat"),
        BandedLine(3, "footer", "Repeat"),
    ]
    assert ("footer", "repeat") in find_running_keys(three_pages, page_count=5)


# ---------------------------------------------------------------------------
# Batch B12: the oversize outer-margin display page number
# ---------------------------------------------------------------------------

_PAGE_HEIGHT = 783.0


def _line(text: str, *, y_min: float, height: float, x_min: float = 10.0) -> Line:
    return Line(
        x_min=x_min,
        y_min=y_min,
        x_max=x_min + 30.0,
        y_max=y_min + height,
        words=[Word(x_min, y_min, x_min + 30.0, y_min + height, text)],
    )


def _block(*lines: Line) -> Block:
    return Block(
        x_min=min(x.x_min for x in lines),
        y_min=min(x.y_min for x in lines),
        x_max=max(x.x_max for x in lines),
        y_max=max(x.y_max for x in lines),
        lines=list(lines),
    )


def _display(block: Block, body: float = 9.5) -> int | None:
    return display_page_number(block, page_height=_PAGE_HEIGHT, body_line_height=body)


def test_display_page_number_finds_the_mm_style_margin_number() -> None:
    # The Monster Manual's own geometry: a one-line, digits-only block at
    # yMin 713.3 (above the 8% footer band, which starts at 720.4) set ~3.2x
    # the body line height.
    assert _display(_block(_line("100", y_min=713.3, height=30.4))) == 100


def test_display_page_number_rejects_a_body_size_digit_in_the_same_band() -> None:
    # MM p0300's advancement table prints "15" at body size, low on the page
    # -- only the height test tells it apart from a page number.
    assert _display(_block(_line("15", y_min=693.0, height=8.4))) is None


def test_display_page_number_rejects_a_number_too_high_on_the_page() -> None:
    assert _display(_block(_line("100", y_min=400.0, height=30.4))) is None


def test_display_page_number_rejects_a_block_with_other_content() -> None:
    block = _block(
        _line("100", y_min=713.3, height=30.4),
        _line("ALLIP", y_min=745.0, height=30.4),
    )
    assert _display(block) is None
    assert _display(_block(_line("Chapter 100", y_min=713.3, height=30.4))) is None


def test_display_page_number_with_no_body_baseline_skips_the_height_test() -> None:
    # A page with nothing but the number on it has no median to measure
    # against; the band + digits-only tests still apply.
    assert _display(_block(_line("7", y_min=713.3, height=30.4)), body=0.0) == 7


def test_median_line_height_ignores_blank_lines() -> None:
    page = Page(
        width=594.0,
        height=_PAGE_HEIGHT,
        blocks=[
            _block(_line("body", y_min=100.0, height=9.5)),
            _block(_line("body", y_min=120.0, height=9.5)),
            _block(_line("   ", y_min=140.0, height=40.0)),
        ],
    )
    assert median_line_height(page) == 9.5
    assert median_line_height(Page(width=594.0, height=_PAGE_HEIGHT, blocks=[])) == 0.0
