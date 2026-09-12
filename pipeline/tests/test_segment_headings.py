"""Unit tests for `owlsperch.segment.headings`: body-median computation and
heading detection from `.meta.json`-style paragraph stats."""

from __future__ import annotations

from owlsperch.segment.headings import Paragraph, compute_body_median, is_heading


def _para(
    text: str,
    *,
    kind: str = "prose",
    height: float = 10.0,
    max_height: float | None = None,
    line_count: int = 1,
    page: int = 1,
) -> Paragraph:
    return Paragraph(
        page=page,
        text=text,
        kind=kind,  # type: ignore[arg-type]
        median_word_height=height,
        max_word_height=max_height if max_height is not None else height,
        line_count=line_count,
    )


def test_body_median_uses_only_multi_line_paragraphs() -> None:
    paragraphs = [
        _para("A short heading line", height=20.0, line_count=1),
        _para("Body text one that runs on for a bit.", height=10.0, line_count=3),
        _para("Body text two that also runs on for a bit.", height=11.0, line_count=4),
        _para("Body text three, likewise long enough to count.", height=9.0, line_count=5),
    ]
    # Median of [10.0, 11.0, 9.0] sorted = [9.0, 10.0, 11.0] -> 10.0.
    assert compute_body_median(paragraphs) == 10.0


def test_body_median_falls_back_to_all_paragraphs_when_none_qualify() -> None:
    paragraphs = [
        _para("One", height=8.0, line_count=1),
        _para("Two", height=12.0, line_count=2),
    ]
    assert compute_body_median(paragraphs) == 12.0


def test_font_size_heading_detected() -> None:
    body_median = 10.0
    heading = _para("Combat and Movement", height=11.6, line_count=1)
    assert is_heading(heading, body_median)


def test_below_threshold_is_not_a_heading() -> None:
    body_median = 10.0
    not_heading = _para("Combat and Movement", height=11.0, line_count=1)
    assert not is_heading(not_heading, body_median)


def test_uppercase_short_line_is_a_heading_regardless_of_height() -> None:
    body_median = 10.0
    heading = _para("COMBAT", height=10.0, line_count=1)
    assert is_heading(heading, body_median)


def test_uppercase_but_too_many_words_is_not_a_heading() -> None:
    body_median = 10.0
    text = "THIS UPPERCASE LINE HAS FAR TOO MANY WORDS TO BE A HEADING"
    not_heading = _para(text, height=10.0, line_count=1)
    assert not is_heading(not_heading, body_median)


def test_multi_line_paragraph_is_never_a_heading() -> None:
    body_median = 10.0
    not_heading = _para("COMBAT", height=20.0, line_count=2)
    assert not is_heading(not_heading, body_median)


def test_table_kind_is_never_a_heading() -> None:
    body_median = 10.0
    not_heading = _para("COMBAT", kind="table", height=20.0, line_count=1)
    assert not is_heading(not_heading, body_median)
