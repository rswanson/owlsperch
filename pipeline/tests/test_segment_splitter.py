"""Unit tests for `owlsperch.segment.splitter.build_segments`: how anchors
and headings are combined into the final segment span list, isolated from
file I/O (see `test_segment_runner.py` for the end-to-end behavior)."""

from __future__ import annotations

from owlsperch.segment.headings import Paragraph
from owlsperch.segment.splitter import build_segments


def _para(
    text: str, *, kind: str = "prose", height: float = 10.0, line_count: int = 1, page: int = 1
) -> Paragraph:
    return Paragraph(
        page=page,
        text=text,
        kind=kind,  # type: ignore[arg-type]
        median_word_height=height,
        max_word_height=height,
        line_count=line_count,
    )


def test_rules_section_before_first_heading_is_kept() -> None:
    paragraphs = [_para("Front matter body text with no heading yet.", line_count=3)]
    segments = build_segments(paragraphs, body_median=10.0)
    assert len(segments) == 1
    assert segments[0].kind == "rules_section"
    assert segments[0].heading == ""
    assert (segments[0].start, segments[0].end) == (0, 1)


def test_headings_split_rules_section_runs() -> None:
    paragraphs = [
        _para("Intro text before any heading at all shows up here.", line_count=3),
        _para("CHAPTER ONE"),
        _para("First section body text goes on for a while about stuff.", line_count=3),
        _para("CHAPTER TWO"),
        _para("Second section body text goes on for a while about other stuff.", line_count=3),
    ]
    segments = build_segments(paragraphs, body_median=10.0)
    assert [(s.kind, s.heading, s.start, s.end) for s in segments] == [
        ("rules_section", "", 0, 1),
        ("rules_section", "CHAPTER ONE", 1, 3),
        ("rules_section", "CHAPTER TWO", 3, 5),
    ]


def test_anchor_segment_ends_at_next_heading() -> None:
    paragraphs = [
        _para("Fireball"),
        _para("Evocation [Fire]"),
        _para("Body text of the fireball spell explaining its effects.", line_count=3),
        _para("COMBAT"),
        _para("Combat rules text continues here for a while.", line_count=3),
    ]
    segments = build_segments(paragraphs, body_median=10.0)
    kinds = [(s.kind, s.start, s.end, s.heading) for s in segments]
    assert kinds[0] == ("spell", 0, 3, "Fireball")
    assert kinds[1] == ("rules_section", 3, 5, "COMBAT")


def test_anchor_segment_ends_at_next_anchor_with_no_intervening_heading() -> None:
    paragraphs = [
        _para("Fireball"),
        _para("Evocation [Fire]"),
        _para("Body text of the fireball spell.", line_count=3),
        _para("Acid Fog"),
        _para("Conjuration (Creation)"),
    ]
    segments = build_segments(paragraphs, body_median=10.0)
    assert [(s.kind, s.start, s.end) for s in segments] == [
        ("spell", 0, 3),
        ("spell", 3, 5),
    ]


def test_page_spanning_segment_is_one_contiguous_span() -> None:
    paragraphs = [
        _para("Fireball", page=1),
        _para("Evocation [Fire]", page=1),
        _para("Explanation continues describing area of effect.", line_count=3, page=2),
        _para("COMBAT", page=2),
    ]
    segments = build_segments(paragraphs, body_median=10.0)
    assert segments[0].kind == "spell"
    assert (segments[0].start, segments[0].end) == (0, 3)


def test_table_extent_is_capped_at_next_anchor() -> None:
    # A table whose footnote-digit rule would otherwise keep consuming
    # paragraphs is still capped at the next trigger, as a safety net.
    paragraphs = [
        _para("Table 3-1: Simple Weapons"),
        _para("Dagger\t2 gp\t1d4", kind="table", line_count=2),
        _para("Fireball"),
        _para("Evocation [Fire]"),
    ]
    segments = build_segments(paragraphs, body_median=10.0)
    assert [(s.kind, s.start, s.end) for s in segments] == [
        ("table", 0, 2),
        ("spell", 2, 4),
    ]


def test_feat_with_bracket_tag_on_its_own_line_stays_one_segment() -> None:
    # Column-repair artifact for a long feat name: the bracketed type lands
    # in its own paragraph after the name line (e.g. PHB p0101 "SHOT ON THE
    # RUN" / "[GENERAL]"). The tag-only paragraph must not be treated as a
    # heading (which would otherwise truncate the feat's segment right
    # after its name line) and must fold into the feat's own heading
    # instead, leaving one feat segment with a non-empty body and no
    # "[GENERAL]"-headed rules_section.
    paragraphs = [
        _para("SHOT ON THE RUN"),
        _para("[GENERAL]"),
        _para(
            "You may fire ranged weapons up to your normal number of ranged "
            "attacks any time during your movement.",
            line_count=3,
        ),
        _para("Prerequisite: Dex 13, Point Blank Shot, base attack bonus +6."),
        _para(
            "Benefit: You may move both before and after your ranged attack.",
            line_count=3,
        ),
        _para("COMBAT"),
    ]
    segments = build_segments(paragraphs, body_median=10.0)
    feat_segments = [s for s in segments if s.kind == "feat"]
    assert len(feat_segments) == 1
    feat = feat_segments[0]
    assert feat.heading == "SHOT ON THE RUN [GENERAL]"
    assert (feat.start, feat.end) == (0, 5)
    assert feat.end > feat.start + 1  # not just the name line -- has a body
    body_text = " ".join(paragraphs[i].text for i in range(feat.start + 1, feat.end))
    assert "Benefit:" in body_text
    assert not any(s.kind == "rules_section" and s.heading == "[GENERAL]" for s in segments)


def test_no_zero_length_segments_emitted() -> None:
    # Two headings with nothing between them produce no empty rules_section.
    paragraphs = [_para("CHAPTER ONE"), _para("CHAPTER TWO"), _para("Body text.", line_count=3)]
    segments = build_segments(paragraphs, body_median=10.0)
    for segment in segments:
        assert segment.end > segment.start
