"""Unit tests for `owlsperch.text.cleanup`: running header/footer detection,
printed page-number detection, and dehyphenation."""

from __future__ import annotations

from owlsperch.text.bbox import Line
from owlsperch.text.cleanup import (
    BandedLine,
    band_for_line,
    dehyphenate_lines,
    find_running_keys,
    load_wordlist,
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
