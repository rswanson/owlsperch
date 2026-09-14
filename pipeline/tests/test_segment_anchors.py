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


def test_feat_name_followed_by_bracket_only_line_folds_tag_into_heading() -> None:
    # Column-repair artifact for a long name: the bracketed type lands on
    # its own paragraph right after the name line instead of staying on the
    # same line (see also
    # test_segment_headings.test_bracket_only_paragraph_is_never_a_heading).
    # The tag paragraph must fold into the feat's heading, and must not
    # itself burn one of the two Prerequisite/Benefit lookahead slots --
    # here the cue is two paragraphs after the tag, which only the extended
    # lookahead reaches.
    paragraphs = [
        _para("SHOT ON THE RUN"),
        _para("[GENERAL]"),
        _para("Flavor sentence with no cue at all in it here.", line_count=3),
        _para("Prerequisite: Dex 13, Point Blank Shot, base attack bonus +6."),
    ]
    triggers = find_triggers(paragraphs, body_median=10.0)
    assert [t.kind for t in triggers] == ["feat"]
    assert triggers[0].start == 0
    assert triggers[0].heading == "SHOT ON THE RUN [GENERAL]"


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


# --- Batch B11: errata_entry/update_entry anchors -------------------------


def test_errata_anchor_requires_entry_kind() -> None:
    paragraphs = [
        _para("Glibness Player's Handbook, page 236 Change the spell to read as follows."),
    ]
    assert find_triggers(paragraphs, body_median=10.0) == []
    triggers = find_triggers(paragraphs, body_median=10.0, entry_kind="errata_entry")
    assert len(triggers) == 1
    assert triggers[0].kind == "errata_entry"
    assert triggers[0].start == 0


def test_errata_anchor_one_per_paragraph() -> None:
    paragraphs = [
        _para("Errata Rule: Primary Sources take precedence over other material."),
        _para("Glibness Player's Handbook, page 236 In second paragraph, change to X."),
        _para("A Thousand Faces Player's Handbook, page 37 Replace alter self with disguise self."),
        _para("In Conclusion . . . that is all the errata for this printing."),
    ]
    triggers = find_triggers(paragraphs, body_median=10.0, entry_kind="errata_entry")
    assert [t.start for t in triggers] == [1, 2]
    assert [t.kind for t in triggers] == ["errata_entry", "errata_entry"]


def test_update_entry_kind_used_for_update_books() -> None:
    paragraphs = [_para("Overrun Player's Handbook, page 148 Change -1 to +1.")]
    triggers = find_triggers(paragraphs, body_median=10.0, entry_kind="update_entry")
    assert [t.kind for t in triggers] == ["update_entry"]


def test_no_errata_anchor_for_ordinary_rulebook_paragraph_with_page_reference() -> None:
    # Criterion 7: every other book kind is unaffected -- a rulebook
    # sentence mentioning "..., page 44" must never produce an errata
    # anchor when entry_kind is None (the manifest-level gate).
    paragraphs = [
        _para("For more on grappling see the Player's Handbook, page 44 for full rules."),
    ]
    assert find_triggers(paragraphs, body_median=10.0) == []


def test_errata_heading_prefix_too_long_is_not_an_anchor() -> None:
    long_prefix = " ".join(f"word{i}" for i in range(13))
    paragraphs = [_para(f"{long_prefix}, page 12 some body text follows here.")]
    assert find_triggers(paragraphs, body_median=10.0, entry_kind="errata_entry") == []


def test_errata_heading_common_suffix_stripped() -> None:
    from owlsperch.segment.anchors import strip_common_heading_suffix

    headings = [
        "Glibness Player's Handbook",
        "A Thousand Faces Player's Handbook",
        "Overrun Player's Handbook",
    ]
    assert strip_common_heading_suffix(headings) == [
        "Glibness",
        "A Thousand Faces",
        "Overrun",
    ]


def test_errata_heading_no_qualifying_suffix_left_alone() -> None:
    from owlsperch.segment.anchors import strip_common_heading_suffix

    headings = ["Alpha One", "Beta Two", "Gamma Three"]
    assert strip_common_heading_suffix(headings) == headings


def test_errata_heading_fewer_than_three_headings_left_alone() -> None:
    from owlsperch.segment.anchors import strip_common_heading_suffix

    headings = ["Glibness Player's Handbook", "Overrun Player's Handbook"]
    assert strip_common_heading_suffix(headings) == headings
