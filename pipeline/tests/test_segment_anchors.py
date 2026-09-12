"""Unit tests for `owlsperch.segment.anchors` -- pattern detection for each
anchor kind, isolated from the splitter and full runner."""

from __future__ import annotations

from owlsperch.segment.anchors import find_triggers
from owlsperch.segment.headings import Paragraph


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


def test_spell_anchor_name_then_school() -> None:
    paragraphs = [
        _para("Fireball"),
        _para("Evocation [Fire]"),
        _para("Body text of the spell.", line_count=3),
    ]
    triggers = find_triggers(paragraphs, body_median=10.0)
    assert len(triggers) == 1
    assert triggers[0].kind == "spell"
    assert triggers[0].start == 0
    assert triggers[0].heading == "Fireball"


def test_spell_anchor_school_with_subschool_and_descriptor() -> None:
    paragraphs = [_para("Acid Fog"), _para("Conjuration (Creation) [Acid]")]
    triggers = find_triggers(paragraphs, body_median=10.0)
    assert [t.kind for t in triggers] == ["spell"]


def test_non_school_line_does_not_trigger_spell() -> None:
    paragraphs = [_para("Fireball"), _para("A regular sentence follows here.")]
    assert find_triggers(paragraphs, body_median=10.0) == []


def test_spell_school_merged_with_stat_block_still_triggers() -> None:
    # Real-world column repair often merges the school line together with
    # the spell's Level/Components/... lines into one multi-line paragraph
    # instead of keeping it alone -- as long as it starts with a school and
    # contains a "Level:" cue, it should still count.
    paragraphs = [
        _para("Antipathy"),
        _para(
            "Divination (Scrying) Level: Sor/Wiz 4 Components: V, S, M Casting Time: 1 hour",
            line_count=9,
        ),
    ]
    triggers = find_triggers(paragraphs, body_median=10.0)
    assert [t.kind for t in triggers] == ["spell"]
    assert triggers[0].heading == "Antipathy"


def test_ordinary_sentence_starting_with_universal_is_not_a_school_line() -> None:
    # "Universal" is the one school name that is also a plain English word;
    # without a "Level:" cue, a merged multi-line paragraph starting with it
    # should not be mistaken for a spell's school line.
    paragraphs = [
        _para("Special Rule"),
        _para("Universal rules apply to every creature in the game world.", line_count=3),
    ]
    assert find_triggers(paragraphs, body_median=10.0) == []


def test_feat_anchor_name_then_prerequisite_within_lookahead() -> None:
    paragraphs = [
        _para("Power Attack [General]"),
        _para("You hit harder with melee weapons.", line_count=3),
        _para("Prerequisite: Str 13."),
    ]
    triggers = find_triggers(paragraphs, body_median=10.0)
    assert len(triggers) == 1
    assert triggers[0].kind == "feat"
    assert triggers[0].start == 0
    assert triggers[0].heading == "Power Attack [General]"


def test_feat_anchor_benefit_directly_after_name() -> None:
    paragraphs = [_para("Toughness"), _para("Benefit: You gain +3 hit points.")]
    triggers = find_triggers(paragraphs, body_median=10.0)
    assert [t.kind for t in triggers] == ["feat"]


def test_feat_anchor_benefit_mid_paragraph_after_lead_sentence() -> None:
    # Realistic column-repair shape (PHB p.101 "Shield Proficiency"): a
    # lead-in sentence shares the paragraph with "Benefit:", which no longer
    # starts it. Must still trigger a feat anchor.
    paragraphs = [
        _para("SHIELD PROFICIENCY [GENERAL]"),
        _para(
            "You are proficient with bucklers, small shields, and large shields. "
            "Benefit: You can use a shield and take only the standard penalties.",
            line_count=3,
        ),
    ]
    triggers = find_triggers(paragraphs, body_median=10.0)
    assert [t.kind for t in triggers] == ["feat"]
    assert triggers[0].start == 0
    assert triggers[0].heading == "SHIELD PROFICIENCY [GENERAL]"


def test_feat_anchor_prerequisite_mid_paragraph_after_lead_sentence() -> None:
    paragraphs = [
        _para("Improved Shield Bash [General]"),
        _para(
            "You can bash with a shield while retaining its shield bonus to your "
            "Armor Class. Prerequisite: Shield Proficiency.",
            line_count=3,
        ),
    ]
    triggers = find_triggers(paragraphs, body_median=10.0)
    assert [t.kind for t in triggers] == ["feat"]
    assert triggers[0].heading == "Improved Shield Bash [General]"


def test_feat_lookahead_too_far_does_not_trigger() -> None:
    paragraphs = [
        _para("Toughness"),
        _para("Flavor line one.", line_count=3),
        _para("Flavor line two.", line_count=3),
        _para("Benefit: You gain +3 hit points."),
    ]
    assert find_triggers(paragraphs, body_median=10.0) == []


def test_stat_block_backdates_to_preceding_short_name_line() -> None:
    paragraphs = [
        _para("Owlbear"),
        _para("A bearlike creature with the head of an owl.", line_count=3),
        _para("Size/Type: Large Magical Beast"),
        _para("Hit Dice: 5d10+20 (52 hp)"),
    ]
    triggers = find_triggers(paragraphs, body_median=10.0)
    assert len(triggers) == 1
    assert triggers[0].kind == "stat_block"
    assert triggers[0].start == 0
    assert triggers[0].heading == "Owlbear"


def test_stat_block_backdates_to_preceding_heading() -> None:
    paragraphs = [
        _para("OWLBEAR", height=20.0),  # font-size heading
        _para("Size/Type: Large Magical Beast"),
    ]
    triggers = find_triggers(paragraphs, body_median=10.0)
    assert triggers[0].start == 0
    assert triggers[0].heading == "OWLBEAR"


def test_stat_block_falls_back_to_own_line_when_no_name_found() -> None:
    paragraphs = [
        _para("A long body paragraph with no obvious name line before it at all.", line_count=5),
        _para("Size/Type: Large Magical Beast"),
    ]
    triggers = find_triggers(paragraphs, body_median=10.0)
    assert triggers[0].start == 1
    assert triggers[0].heading == "Size/Type: Large Magical Beast"


def test_table_anchor_extent_includes_rows_and_footnotes() -> None:
    paragraphs = [
        _para("Table 3-1: Simple Weapons"),
        _para("Dagger\t2 gp\t1d4", kind="table", line_count=2),
        _para("1 A footnote about the table.", line_count=1),
        _para("COMBAT"),
    ]
    triggers = find_triggers(paragraphs, body_median=10.0)
    assert len(triggers) == 1
    trigger = triggers[0]
    assert trigger.kind == "table"
    assert trigger.start == 0
    assert trigger.heading == "Table 3-1: Simple Weapons"
    assert trigger.table_end == 3  # stops before "COMBAT"


def test_table_caption_en_dash_variant() -> None:
    paragraphs = [_para("Table 3–1: Simple Weapons"), _para("row", kind="table")]
    triggers = find_triggers(paragraphs, body_median=10.0)
    assert triggers[0].kind == "table"
