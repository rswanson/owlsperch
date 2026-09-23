"""Unit tests for `owlsperch.validate.checks` -- the id/slug convention,
type-directory match, schema_version match, and the spell-specific field
checks."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

import pytest

from owlsperch.validate.checks import (
    NULL_CONTEXT,
    ValidationContext,
    _bab_progression_cell,
    _column_break_continuation,
    _is_heading_shaped,
    _normalize_special_token,
    _run_in_headings,
    _save_progression_cell,
    _special_token_matches_feature,
    _split_special_cell,
    check_class_fields,
    check_class_segment_coverage,
    check_envelope_consistency,
    check_errata_entry_fields,
    check_feat_fields,
    check_monster_fields,
    check_monster_segment_coverage,
    check_pages_within_segment,
    check_rules_section_fields,
    check_spell_fields,
    check_table_cells_in_segment,
    check_table_fields,
    check_template_fields,
    check_update_entry_fields,
    expected_id,
    slugify,
)


def test_slugify_ascii_folds_and_kebab_cases() -> None:
    assert slugify("Glimmerfrost Ward") == "glimmerfrost-ward"
    assert slugify("Melf's Acid Arrow") == "melfs-acid-arrow"
    assert slugify("Protection from Evil/Good") == "protection-from-evil-good"


def test_slugify_maps_unicode_dash_punctuation_to_hyphen() -> None:
    """B10 criterion 9: an en dash (category Pd) in a table title must
    become a hyphen, not be dropped by the ASCII fold and merge its
    neighboring digits together."""
    assert slugify("Table 3–8: The Druid") == "table-3-8-the-druid"
    # Em dash, another Pd character, gets the same treatment.
    assert slugify("Table 1—1: Sable Ranks") == "table-1-1-sable-ranks"


def test_expected_id_format() -> None:
    assert expected_id("spell", "phb1", "fireball") == "spell:phb1:fireball"


def test_check_envelope_consistency_passes_for_matching_record() -> None:
    record = {
        "id": "spell:phb1:fireball",
        "type": "spell",
        "name": "Fireball",
        "slug": "fireball",
        "book_id": "phb1",
        "schema_version": 1,
    }
    assert check_envelope_consistency(record, type_dir="spell", registry_version=1) == []


def test_check_envelope_consistency_flags_type_dir_mismatch() -> None:
    record = {
        "id": "spell:phb1:fireball",
        "type": "spell",
        "name": "Fireball",
        "slug": "fireball",
        "book_id": "phb1",
        "schema_version": 1,
    }
    errors = check_envelope_consistency(record, type_dir="feat", registry_version=1)
    assert any("type directory" in e for e in errors)


def test_check_envelope_consistency_flags_slug_mismatch() -> None:
    record = {
        "id": "spell:phb1:fireball",
        "type": "spell",
        "name": "Fireball",
        "slug": "not-the-slug",
        "book_id": "phb1",
        "schema_version": 1,
    }
    errors = check_envelope_consistency(record, type_dir="spell", registry_version=1)
    assert any("slug" in e for e in errors)


def test_check_envelope_consistency_flags_id_mismatch() -> None:
    record = {
        "id": "spell:phb1:wrong-id",
        "type": "spell",
        "name": "Fireball",
        "slug": "fireball",
        "book_id": "phb1",
        "schema_version": 1,
    }
    errors = check_envelope_consistency(record, type_dir="spell", registry_version=1)
    assert any("id" in e for e in errors)


def test_check_envelope_consistency_flags_stale_schema_version() -> None:
    record = {
        "id": "spell:phb1:fireball",
        "type": "spell",
        "name": "Fireball",
        "slug": "fireball",
        "book_id": "phb1",
        "schema_version": 1,
    }
    errors = check_envelope_consistency(record, type_dir="spell", registry_version=2)
    assert any("schema_version" in e for e in errors)


def test_check_spell_fields_requires_nonempty_levels() -> None:
    errors = check_spell_fields({"fields": {"school": "Evocation", "levels": []}})
    assert any("levels" in e for e in errors)


def test_check_spell_fields_requires_nonempty_school() -> None:
    errors = check_spell_fields(
        {"fields": {"school": "", "levels": [{"class": "Wizard", "level": 3}]}}
    )
    assert any("school" in e for e in errors)


def test_check_spell_fields_rejects_wrong_type_school() -> None:
    errors = check_spell_fields(
        {"fields": {"school": 3, "levels": [{"class": "Wizard", "level": 3}]}}
    )
    assert any("school" in e for e in errors)


def test_check_spell_fields_passes_for_valid_fields() -> None:
    errors = check_spell_fields(
        {"fields": {"school": "Evocation", "levels": [{"class": "Wizard", "level": 3}]}}
    )
    assert errors == []


def test_check_pages_within_segment_passes_when_all_pages_in_span() -> None:
    segment = {"seg_id": "book-p0010-01", "pages": [10, 11]}
    assert check_pages_within_segment({"pages": [10, 11]}, segment) == []


def test_check_pages_within_segment_flags_out_of_span_pages() -> None:
    segment = {"seg_id": "book-p0010-01", "pages": [10, 11]}
    errors = check_pages_within_segment({"pages": [10, 99]}, segment)
    assert len(errors) == 1
    assert "99" in errors[0]
    assert "book-p0010-01" in errors[0]


def test_check_pages_within_segment_flags_missing_segment() -> None:
    errors = check_pages_within_segment({"pages": [10]}, None)
    assert any("segment" in e for e in errors)


# ---------------------------------------------------------------------------
# check_feat_fields
# ---------------------------------------------------------------------------


def test_check_feat_fields_requires_nonempty_benefit() -> None:
    errors = check_feat_fields({"fields": {"benefit": ""}})
    assert any("benefit" in e for e in errors)


def test_check_feat_fields_rejects_whitespace_only_benefit() -> None:
    errors = check_feat_fields({"fields": {"benefit": "   "}})
    assert any("benefit" in e for e in errors)


def test_check_feat_fields_passes_for_valid_fields() -> None:
    assert check_feat_fields({"fields": {"benefit": "You gain a +2 bonus."}}) == []


def test_check_feat_fields_flags_missing_fields_object() -> None:
    assert check_feat_fields({}) == ["fields is missing or not an object"]


# ---------------------------------------------------------------------------
# check_rules_section_fields
# ---------------------------------------------------------------------------


def test_check_rules_section_fields_requires_nonempty_topic() -> None:
    errors = check_rules_section_fields({"fields": {"topic": ""}})
    assert any("topic" in e for e in errors)


def test_check_rules_section_fields_passes_for_valid_fields() -> None:
    assert check_rules_section_fields({"fields": {"topic": "Grappling"}}) == []


def test_check_rules_section_fields_flags_missing_fields_object() -> None:
    assert check_rules_section_fields({}) == ["fields is missing or not an object"]


# ---------------------------------------------------------------------------
# check_table_fields
# ---------------------------------------------------------------------------


def test_check_table_fields_requires_nonempty_columns() -> None:
    errors = check_table_fields({"fields": {"columns": [], "rows": []}})
    assert any("columns" in e for e in errors)


def test_check_table_fields_passes_when_rows_match_column_count() -> None:
    fields = {"columns": ["A", "B"], "rows": [["1", "2"], ["3", "4"]]}
    assert check_table_fields({"fields": fields}) == []


def test_check_table_fields_flags_row_with_wrong_cell_count() -> None:
    fields = {"columns": ["A", "B", "C"], "rows": [["1", "2", "3"], ["4", "5"]]}
    errors = check_table_fields({"fields": fields})
    assert len(errors) == 1
    assert "row 1" in errors[0]
    assert "2 cell" in errors[0]
    assert "expected 3" in errors[0]


def test_check_table_fields_flags_non_list_row() -> None:
    fields = {"columns": ["A"], "rows": ["not-a-list"]}
    errors = check_table_fields({"fields": fields})
    assert any("row 0" in e for e in errors)


def test_check_table_fields_flags_missing_fields_object() -> None:
    assert check_table_fields({}) == ["fields is missing or not an object"]


# ---------------------------------------------------------------------------
# Batch B10c, design decision D9: check_class_fields (registered for both
# `class` and `prestige_class` in TYPE_CONTEXT_CHECKS).
# ---------------------------------------------------------------------------


def _valid_table_record() -> dict[str, Any]:
    return {
        "id": "table:phb1:table-x-the-testclass",
        "type": "table",
        "name": "Table X: The Testclass",
        "book_id": "phb1",
        "pages": [1],
        "fields": {
            "columns": [
                "Level",
                "Base Attack Bonus",
                "Fort Save",
                "Ref Save",
                "Will Save",
                "Special",
            ],
            "rows": [
                ["1st", "+1", "+2", "+0", "+0", "Rage 1/day"],
                ["2nd", "+2", "+3", "+0", "+0", "Uncanny dodge"],
                ["3rd", "+3", "+3", "+1", "+1", "Trap sense +1"],
            ],
        },
    }


def _valid_class_record() -> dict[str, Any]:
    return {
        "id": "class:phb1:testclass",
        "type": "class",
        "name": "Testclass",
        "book_id": "phb1",
        "tables": ["table:phb1:table-x-the-testclass"],
        "fields": {
            "hit_die": "d12",
            "bab_progression": "good",
            "save_progressions": {"fort": "good", "ref": "poor", "will": "poor"},
            "max_level": 3,
            "class_skills": [
                {"skill": "Climb", "key_ability": "Str"},
                {"skill": "Knowledge (arcana)", "key_ability": "Int"},
            ],
            "level_table": "table:phb1:table-x-the-testclass",
            "class_features": [
                {"name": "Rage", "level": 1, "text_md": "You rage."},
                {"name": "Uncanny Dodge", "level": 2, "text_md": "You dodge uncannily."},
                {"name": "Trap Sense", "level": 3, "text_md": "Your senses grow wary of traps."},
            ],
        },
    }


def _context(
    records: dict[str, dict[str, Any]], spell_classes: dict[str, set[str]] | None = None
) -> ValidationContext:
    resolved_spell_classes = spell_classes or {}
    return ValidationContext(
        record_by_id=lambda record_id: records.get(record_id),
        spell_list_classes=lambda book_id: resolved_spell_classes.get(book_id, set()),
    )


def _default_context() -> ValidationContext:
    table = _valid_table_record()
    return _context({table["id"]: table})


def test_check_class_fields_passes_for_a_valid_record() -> None:
    assert check_class_fields(_valid_class_record(), _default_context()) == []


def test_check_class_fields_flags_invalid_hit_die() -> None:
    record = copy.deepcopy(_valid_class_record())
    record["fields"]["hit_die"] = "d3"
    errors = check_class_fields(record, _default_context())
    assert any("hit_die" in e for e in errors)


def test_check_class_fields_flags_unresolvable_level_table() -> None:
    record = copy.deepcopy(_valid_class_record())
    record["fields"]["level_table"] = "table:phb1:does-not-exist"
    errors = check_class_fields(record, _default_context())
    assert any("not found" in e for e in errors)


def test_check_class_fields_flags_level_table_not_in_tables_array() -> None:
    record = copy.deepcopy(_valid_class_record())
    record["tables"] = []
    errors = check_class_fields(record, _default_context())
    assert any("not listed in this record's own tables array" in e for e in errors)


def test_check_class_fields_flags_row_count_mismatch() -> None:
    table = _valid_table_record()
    table["fields"]["rows"] = table["fields"]["rows"][:2]  # drop the 3rd-level row
    record = _valid_class_record()
    errors = check_class_fields(record, _context({table["id"]: table}))
    assert any("has 2 row(s), expected max_level 3" in e for e in errors)


def test_check_class_fields_flags_bab_mismatch() -> None:
    table = _valid_table_record()
    table["fields"]["rows"][2][1] = "+2"  # 3rd level should be +3 for a good progression
    record = _valid_class_record()
    errors = check_class_fields(record, _context({table["id"]: table}))
    assert any("base attack bonus" in e and "expected '+3'" in e for e in errors)


def test_check_class_fields_flags_save_mismatch() -> None:
    table = _valid_table_record()
    table["fields"]["rows"][0][2] = "+0"  # 1st-level fort (good) should be +2
    record = _valid_class_record()
    errors = check_class_fields(record, _context({table["id"]: table}))
    assert any("fort save" in e and "expected '+2'" in e for e in errors)


def test_check_class_fields_flags_special_entry_with_no_matching_feature() -> None:
    record = copy.deepcopy(_valid_class_record())
    record["fields"]["class_features"] = [
        f for f in record["fields"]["class_features"] if f["name"] != "Rage"
    ]
    errors = check_class_fields(record, _default_context())
    assert any("Rage 1/day" in e and "no matching class_features entry" in e for e in errors)


def test_check_class_fields_flags_class_feature_level_not_in_table() -> None:
    record = copy.deepcopy(_valid_class_record())
    record["fields"]["class_features"].append({"name": "Bonus Feat", "level": 6, "text_md": ""})
    errors = check_class_fields(record, _default_context())
    assert any("level 6 is not in the level table" in e for e in errors)


def test_check_class_fields_flags_unknown_skill() -> None:
    record = copy.deepcopy(_valid_class_record())
    record["fields"]["class_skills"].append(
        {"skill": "Underwater Basketweaving", "key_ability": "Str"}
    )
    errors = check_class_fields(record, _default_context())
    assert any("not a recognized skill" in e for e in errors)


def test_check_class_fields_flags_spell_list_not_matching_any_spell() -> None:
    record = copy.deepcopy(_valid_class_record())
    record["fields"]["spellcasting"] = {
        "kind": "arcane",
        "ability": "Int",
        "type": "prepared",
        "spell_list": "Testclass",
    }
    table = _valid_table_record()
    context = _context({table["id"]: table}, spell_classes={"phb1": {"Wizard", "Sorcerer"}})
    errors = check_class_fields(record, context)
    assert any("spellcasting.spell_list" in e for e in errors)


def test_check_class_fields_skips_spell_list_check_when_book_has_no_spells_yet() -> None:
    """No spell records for the book yet is not an error -- the check is
    silently skipped rather than failing every class record. The table
    needs its own spells-per-day column (Part 3b) so this test keeps
    asserting what it is named for, rather than failing on that separate
    check."""
    record = copy.deepcopy(_valid_class_record())
    record["fields"]["spellcasting"] = {
        "kind": "arcane",
        "ability": "Int",
        "type": "prepared",
        "spell_list": "Anything At All",
    }
    record["fields"]["class_features"].append(
        {"name": "Spells", "level": 1, "text_md": "A testclass casts arcane spells."}
    )
    table = _valid_table_record()
    table["fields"]["columns"].append("Spells per Day 1st")
    for row in table["fields"]["rows"]:
        row.append("1")
    errors = check_class_fields(record, _context({table["id"]: table}))
    assert errors == []


# ---------------------------------------------------------------------------
# Batch B10c-mand3 Part 3a: plural-/bonus-insensitive Special matching.
# ---------------------------------------------------------------------------


def test_check_class_fields_special_bonus_feat_matches_bonus_feats_feature() -> None:
    """The fighter prints the heading "Bonus Feats:" but its Special cell
    reads "Bonus feat" -- the match must be plural-insensitive so the
    record doesn't have to misname the feature to validate."""
    table = _valid_table_record()
    table["fields"]["rows"][0][5] = "Bonus feat"
    record = copy.deepcopy(_valid_class_record())
    record["fields"]["class_features"][0] = {
        "name": "Bonus Feats",
        "level": 1,
        "text_md": "You gain a bonus feat.",
    }
    errors = check_class_fields(record, _context({table["id"]: table}))
    assert not any("Bonus feat" in e and "no matching" in e for e in errors)


def test_check_class_fields_special_entry_with_truly_no_match_still_errors() -> None:
    """The plural/bonus tolerance must not make the check vacuous -- a
    Special token naming a feature that plainly isn't in class_features at
    all still errors."""
    table = _valid_table_record()
    table["fields"]["rows"][0][5] = "Whirlwind Attack"
    record = copy.deepcopy(_valid_class_record())
    errors = check_class_fields(record, _context({table["id"]: table}))
    assert any("Whirlwind Attack" in e and "no matching class_features entry" in e for e in errors)


def test_check_class_fields_special_still_matches_dice_bonus_and_frequency() -> None:
    """Regression: `_normalize_special_token`'s new trailing-bonus strip
    and plural folding must not break the existing dice-bonus ("Sneak
    Attack +1d6") or per-day-frequency ("Rage 1/day") matches."""
    assert check_class_fields(_valid_class_record(), _default_context()) == []


# ---------------------------------------------------------------------------
# B10c-mand8: the Special-token matcher must accept a feature named from the
# printed Class Features heading even when the level table's Special cell
# is longer ("Summon familiar" vs "Familiar") or pluralized with "-ies"
# ("Special ability" vs "Special Abilities"). These three real-corpus pairs
# kept rogue/sorcerer/wizard in human/phb1/ after the 2026-09-14 drain.
# ---------------------------------------------------------------------------


def _class_with_special_and_feature(special_cell: str, feature_name: str) -> list[str]:
    table = _valid_table_record()
    table["fields"]["rows"][0][5] = special_cell
    record = copy.deepcopy(_valid_class_record())
    record["fields"]["class_features"][0] = {
        "name": feature_name,
        "level": 1,
        "text_md": "Described under its own printed heading.",
    }
    return check_class_fields(record, _context({table["id"]: table}))


def test_check_class_fields_summon_familiar_matches_familiar_feature() -> None:
    """Sorcerer/wizard: Special cell "Summon familiar", heading "Familiar"."""
    errors = _class_with_special_and_feature("Summon familiar", "Familiar")
    assert not any("Summon familiar" in e and "no matching" in e for e in errors)


def test_check_class_fields_special_ability_matches_special_abilities_feature() -> None:
    """Rogue: Special cell "Special ability", heading "Special Abilities"."""
    errors = _class_with_special_and_feature("Special ability", "Special Abilities")
    assert not any("Special ability" in e and "no matching" in e for e in errors)


def test_check_class_fields_prefix_direction_still_matches() -> None:
    """The pre-existing direction (feature name is a prefix of the token)
    keeps working."""
    errors = _class_with_special_and_feature("Sneak attack +1d6", "Sneak Attack (Ex)")
    assert not any("Sneak attack" in e and "no matching" in e for e in errors)


def test_check_class_fields_containment_is_word_bounded_not_substring() -> None:
    """Containment must not turn into substring matching: a token "Defeat"
    is not described by a feature named "Feat", and a feature "Rage" does
    not cover a Special cell reading "Courage"."""
    errors = _class_with_special_and_feature("Defeat", "Feat")
    assert any("Defeat" in e and "no matching class_features entry" in e for e in errors)
    errors = _class_with_special_and_feature("Courage", "Rage")
    assert any("Courage" in e and "no matching class_features entry" in e for e in errors)


def test_check_class_fields_tiered_feature_is_not_masked_by_its_base() -> None:
    """Review finding: a record missing the "Greater Rage"/"Improved
    Evasion" entry must still fail -- the token's tier modifier means it
    is its own printed heading, never covered by the base feature."""
    errors = _class_with_special_and_feature("Greater rage", "Rage")
    assert any("Greater rage" in e and "no matching class_features entry" in e for e in errors)
    errors = _class_with_special_and_feature("Improved evasion", "Evasion")
    assert any("Improved evasion" in e and "no matching" in e for e in errors)
    errors = _class_with_special_and_feature("Improved uncanny dodge", "Uncanny Dodge")
    assert any("Improved uncanny dodge" in e and "no matching" in e for e in errors)


def test_check_class_fields_longer_feature_never_covers_truncated_token() -> None:
    """A truncated Special cell ("Courage" for "Inspire courage +2", "Rage"
    when only "Greater Rage" was recorded) still errors: only the
    token-contains-feature direction matches."""
    errors = _class_with_special_and_feature("Courage", "Inspire Courage")
    assert any("Courage" in e and "no matching" in e for e in errors)
    errors = _class_with_special_and_feature("Rage", "Greater Rage")
    assert any("'Rage'" in e and "no matching" in e for e in errors)


def test_special_token_matches_feature_direct_cases() -> None:
    assert not _special_token_matches_feature("", "familiar")
    assert not _special_token_matches_feature("summon familiar", "")
    assert _special_token_matches_feature("summon familiar", "familiar")
    assert _special_token_matches_feature("special ability", "special ability")
    assert _special_token_matches_feature("bonus feat", "bonus feat")
    assert not _special_token_matches_feature("familiar", "summon familiar")
    assert not _special_token_matches_feature("familiar spirit", "summon familiar")
    assert not _special_token_matches_feature("greater rage", "rage")
    assert not _special_token_matches_feature("mighty rage", "rage")
    assert not _special_token_matches_feature("rage", "greater rage")


def test_fold_trailing_plural_singularizes_ies_and_sibilant_es() -> None:
    assert _normalize_special_token("Special Abilities") == "special ability"
    assert _normalize_special_token("Special ability") == "special ability"
    assert _normalize_special_token("Classes") == "class"
    assert _normalize_special_token("Bonus Feats") == "bonus feat"
    assert _normalize_special_token("Boxes") == "box"
    # A four-letter "-ies" word is a plain "-s" plural ("ties" -> "tie"),
    # and the ss/us/is exceptions still hold.
    assert _normalize_special_token("Ties") == "tie"
    assert _normalize_special_token("Bonus") == "bonus"
    assert _normalize_special_token("Class") == "class"


# ---------------------------------------------------------------------------
# Batch B10c-mand3 Part 3b: a caster class needs a spells-per-day/known
# column on its level_table.
# ---------------------------------------------------------------------------


def test_check_class_fields_flags_caster_with_no_spell_column() -> None:
    record = copy.deepcopy(_valid_class_record())
    record["fields"]["spellcasting"] = {
        "kind": "divine",
        "ability": "Wis",
        "type": "prepared",
        "spell_list": "Testclass",
    }
    errors = check_class_fields(record, _default_context())
    assert any(
        "spellcasting is set" in e
        and "no spells-per-day/known column" in e
        and "table:phb1:table-x-the-testclass" in e
        for e in errors
    )


def test_check_class_fields_caster_with_spell_column_passes() -> None:
    record = copy.deepcopy(_valid_class_record())
    record["fields"]["spellcasting"] = {
        "kind": "divine",
        "ability": "Wis",
        "type": "prepared",
        "spell_list": "Testclass",
    }
    record["fields"]["class_features"].append(
        {"name": "Spells", "level": 1, "text_md": "A testclass casts divine spells."}
    )
    table = _valid_table_record()
    table["fields"]["columns"].append("Spells Known")
    for row in table["fields"]["rows"]:
        row.append("2")
    errors = check_class_fields(record, _context({table["id"]: table}))
    assert not any("no spells-per-day/known column" in e for e in errors)


def test_check_class_fields_with_null_context_reports_unresolvable_table() -> None:
    record = _valid_class_record()
    errors = check_class_fields(record, NULL_CONTEXT)
    assert any("not found" in e for e in errors)


# ---------------------------------------------------------------------------
# Batch B11: check_errata_entry_fields / check_update_entry_fields
# ---------------------------------------------------------------------------


def test_check_errata_entry_fields_passes_for_qualified_name_with_page() -> None:
    record = {
        "name": "Glibness (p. 236)",
        "fields": {
            "target_book": "phb1",
            "target_page": 236,
            "target_name": "Glibness",
            "replacement_text": "If a magical effect is used against you, it fails.",
        },
    }
    assert check_errata_entry_fields(record) == []


def test_check_errata_entry_fields_passes_for_bare_name_without_page() -> None:
    record = {
        "name": "Sable Strike",
        "fields": {
            "target_book": "fixture-book",
            "target_name": "Sable Strike",
            "replacement_text": "Change the bonus from +2 to +4.",
        },
    }
    assert check_update_entry_fields(record) == []


def test_check_errata_entry_fields_rejects_mismatched_name() -> None:
    record = {
        "name": "Glibness",
        "fields": {
            "target_book": "phb1",
            "target_page": 236,
            "target_name": "Glibness",
            "replacement_text": "Some replacement text.",
        },
    }
    errors = check_errata_entry_fields(record)
    assert any("name" in e and "Glibness (p. 236)" in e for e in errors)


def test_check_errata_entry_fields_requires_nonempty_target_book() -> None:
    record = {
        "name": "Glibness",
        "fields": {
            "target_book": "",
            "target_name": "Glibness",
            "replacement_text": "Some replacement text.",
        },
    }
    errors = check_errata_entry_fields(record)
    assert any("target_book" in e for e in errors)


def test_check_errata_entry_fields_requires_nonempty_target_name() -> None:
    record = {
        "name": "",
        "fields": {
            "target_book": "phb1",
            "target_name": "  ",
            "replacement_text": "Some replacement text.",
        },
    }
    errors = check_errata_entry_fields(record)
    assert any("target_name" in e for e in errors)


def test_check_errata_entry_fields_requires_nonempty_replacement_text() -> None:
    record = {
        "name": "Glibness",
        "fields": {
            "target_book": "phb1",
            "target_name": "Glibness",
            "replacement_text": "   ",
        },
    }
    errors = check_errata_entry_fields(record)
    assert any("replacement_text" in e for e in errors)


def test_check_errata_entry_fields_rejects_non_positive_target_page() -> None:
    record = {
        "name": "Glibness (p. 0)",
        "fields": {
            "target_book": "phb1",
            "target_page": 0,
            "target_name": "Glibness",
            "replacement_text": "Some replacement text.",
        },
    }
    errors = check_errata_entry_fields(record)
    assert any("target_page" in e for e in errors)


def test_check_errata_entry_fields_flags_missing_fields_object() -> None:
    assert check_errata_entry_fields({}) == ["fields is missing or not an object"]


# ---------------------------------------------------------------------------
# B10c-mand4 criterion 1: parenthesis-aware Special-cell splitting.
# ---------------------------------------------------------------------------


def test_split_special_cell_does_not_split_inside_parentheses() -> None:
    cell = "Wild shape (Huge elemental, 2/day), Venom immunity"
    assert _split_special_cell(cell) == [
        "Wild shape (Huge elemental, 2/day)",
        "Venom immunity",
    ]


def test_split_special_cell_clamps_depth_on_stray_close_paren() -> None:
    """An unbalanced stray ')' must not swallow the rest of the cell --
    depth clamps at 0 instead of going negative."""
    cell = "Sneak attack), Uncanny dodge"
    assert _split_special_cell(cell) == ["Sneak attack)", "Uncanny dodge"]


def test_check_class_fields_paren_aware_special_split_matches_both_features() -> None:
    """Regression: on main, splitting this cell on every comma (including
    the one inside the parenthetical) produces a bogus third token
    ('2/day)') that raises a spurious 'no matching class_features entry'."""
    table = _valid_table_record()
    table["fields"]["rows"][0][5] = "Wild shape (Huge elemental, 2/day), Venom immunity"
    record = copy.deepcopy(_valid_class_record())
    record["fields"]["class_features"] = [
        {"name": "Wild Shape", "level": 1, "text_md": "You wild shape."},
        {"name": "Venom Immunity", "level": 1, "text_md": "You are immune to venom."},
        {"name": "Uncanny Dodge", "level": 2, "text_md": "You dodge uncannily."},
        {"name": "Trap Sense", "level": 3, "text_md": "You sense traps."},
    ]
    errors = check_class_fields(record, _context({table["id"]: table}))
    assert errors == []


# ---------------------------------------------------------------------------
# B10c-mand4 criterion 2: ordinal/distance/frequency stripping in
# _normalize_special_token.
# ---------------------------------------------------------------------------


def test_normalize_special_token_strips_ordinal_distance_and_frequency() -> None:
    assert _normalize_special_token("2nd favored enemy") == "favored enemy"
    assert _normalize_special_token("slow fall 30 ft.") == "slow fall"
    assert _normalize_special_token("Wild shape (Huge elemental, 2/day)") == "wild shape"
    assert _normalize_special_token("Rage 1/day") == "rage"


def test_normalize_special_token_keeps_existing_dice_frequency_and_plural_behavior() -> None:
    assert _normalize_special_token("Sneak attack +1d6") == _normalize_special_token("Sneak Attack")
    assert _normalize_special_token("Damage reduction 1/—").startswith(
        _normalize_special_token("Damage Reduction (Ex)")
    )
    assert _normalize_special_token("Bonus feat") == _normalize_special_token("Bonus Feats")


# ---------------------------------------------------------------------------
# B10c-mand4 criterion 3: Special/class_features matching runs once per
# DISTINCT normalized token across the whole table, not once per row.
# ---------------------------------------------------------------------------


def _favored_enemy_table_and_record(
    *, include_feature: bool
) -> tuple[dict[str, Any], dict[str, Any]]:
    columns = ["Level", "Base Attack Bonus", "Fort Save", "Ref Save", "Will Save", "Special"]
    rows = []
    for level in range(1, 11):
        if level == 1:
            special = "1st favored enemy"
        elif level == 5:
            special = "2nd favored enemy"
        elif level == 10:
            special = "3rd favored enemy"
        else:
            special = "-"
        rows.append(
            [
                str(level),
                _bab_progression_cell("good", level),
                _save_progression_cell("poor", level),
                _save_progression_cell("poor", level),
                _save_progression_cell("poor", level),
                special,
            ]
        )
    table: dict[str, Any] = {
        "id": "table:phb1:table-y-the-testranger",
        "type": "table",
        "name": "Table Y: The Testranger",
        "book_id": "phb1",
        "pages": [1],
        "fields": {"columns": columns, "rows": rows},
    }
    class_features = (
        [{"name": "Favored Enemy", "level": 1, "text_md": "You gain favored enemies."}]
        if include_feature
        else []
    )
    record: dict[str, Any] = {
        "id": "class:phb1:testranger",
        "type": "class",
        "name": "Testranger",
        "book_id": "phb1",
        "tables": [table["id"]],
        "fields": {
            "hit_die": "d8",
            "bab_progression": "good",
            "save_progressions": {"fort": "poor", "ref": "poor", "will": "poor"},
            "max_level": 10,
            "class_skills": [{"skill": "Climb", "key_ability": "Str"}],
            "level_table": table["id"],
            "class_features": class_features,
        },
    }
    return table, record


def test_check_class_fields_distinct_token_progression_validates_clean() -> None:
    table, record = _favored_enemy_table_and_record(include_feature=True)
    errors = check_class_fields(record, _context({table["id"]: table}))
    assert errors == []


def test_check_class_fields_distinct_token_unmatched_reports_exactly_one_error() -> None:
    table, record = _favored_enemy_table_and_record(include_feature=False)
    errors = check_class_fields(record, _context({table["id"]: table}))
    matching = [e for e in errors if "favored enemy" in e.lower()]
    assert len(matching) == 1
    assert "level 1" in matching[0]
    assert "1st favored enemy" in matching[0]


# ---------------------------------------------------------------------------
# B10c-mand4 criterion 4: an empty/missing/whitespace-only text_md is an
# error.
# ---------------------------------------------------------------------------


def test_check_class_fields_flags_whitespace_only_text_md() -> None:
    record = copy.deepcopy(_valid_class_record())
    record["fields"]["class_features"][0]["text_md"] = "   "
    errors = check_class_fields(record, _default_context())
    assert any("Rage" in e and "empty text_md" in e for e in errors)


def test_check_class_fields_flags_missing_text_md_key() -> None:
    record = copy.deepcopy(_valid_class_record())
    del record["fields"]["class_features"][1]["text_md"]
    errors = check_class_fields(record, _default_context())
    assert any("Uncanny Dodge" in e and "empty text_md" in e for e in errors)


# ---------------------------------------------------------------------------
# B10c-mand4 criterion 5: two class_features entries that normalize to the
# same name is an error.
# ---------------------------------------------------------------------------


def test_check_class_fields_flags_duplicate_normalized_feature_names() -> None:
    record = copy.deepcopy(_valid_class_record())
    record["fields"]["class_features"].append(
        {"name": "Rage (Su)", "level": 2, "text_md": "More rage."}
    )
    errors = check_class_fields(record, _default_context())
    assert any("duplicate" in e.lower() and "rage" in e.lower() for e in errors)


def test_check_class_fields_passes_with_all_distinct_feature_names() -> None:
    assert check_class_fields(_valid_class_record(), _default_context()) == []


# ---------------------------------------------------------------------------
# B10c-mand4 criterion 6: a caster (spellcasting set) must carry a `Spells`
# class_features entry.
# ---------------------------------------------------------------------------


def test_check_class_fields_flags_caster_without_spells_feature() -> None:
    record = copy.deepcopy(_valid_class_record())
    record["fields"]["spellcasting"] = {
        "kind": "arcane",
        "ability": "Int",
        "type": "prepared",
        "spell_list": "Testclass",
    }
    table = _valid_table_record()
    table["fields"]["columns"].append("Spells per Day 1st")
    for row in table["fields"]["rows"]:
        row.append("1")
    errors = check_class_fields(record, _context({table["id"]: table}))
    assert any("no 'Spells' entry" in e for e in errors)


def _caster_record_with_slots(slots: list[str], class_type: str = "base") -> list[str]:
    record = copy.deepcopy(_valid_class_record())
    record["fields"]["class_type"] = class_type
    record["fields"]["spellcasting"] = {
        "kind": "divine",
        "ability": "Wis",
        "type": "prepared",
        "spell_list": "Anything At All",
    }
    record["fields"]["class_features"].append(
        {"name": "Spells", "level": 1, "text_md": "A testclass casts divine spells."}
    )
    table = _valid_table_record()
    for slot in slots:
        table["fields"]["columns"].append(f"Spells per Day {slot}")
        for row in table["fields"]["rows"]:
            row.append("1")
    return check_class_fields(record, _context({table["id"]: table}))


# ---------------------------------------------------------------------------
# B10c-mand10: a base caster reaching 5th-level spells must carry a
# "Spells per Day 0" column (the cleric's dropped-orisons-column miss).
# ---------------------------------------------------------------------------


def test_check_class_fields_cleric_shaped_table_missing_level_0_column_fails() -> None:
    errors = _caster_record_with_slots(["1st", "2nd", "3rd", "4th", "5th", "6th"])
    assert any("has no 'Spells per Day 0' column" in e and "6th-level" in e for e in errors)


def test_check_class_fields_full_caster_with_level_0_column_passes() -> None:
    assert _caster_record_with_slots(["0", "1st", "2nd", "3rd", "4th", "5th"]) == []


def test_check_class_fields_half_caster_topping_out_at_4th_needs_no_level_0() -> None:
    """Paladin/ranger shape: 1st-4th only, no 0 column, is correct."""
    assert _caster_record_with_slots(["1st", "2nd", "3rd", "4th"]) == []


def test_check_class_fields_level_0_rule_skips_non_base_class_type() -> None:
    errors = _caster_record_with_slots(["1st", "2nd", "3rd", "4th", "5th"], class_type="npc")
    assert not any("Spells per Day 0" in e for e in errors)


def test_check_class_fields_caster_with_spells_feature_passes() -> None:
    record = copy.deepcopy(_valid_class_record())
    record["fields"]["spellcasting"] = {
        "kind": "arcane",
        "ability": "Int",
        "type": "prepared",
        "spell_list": "Testclass",
    }
    record["fields"]["class_features"].append(
        {"name": "Spells", "level": 1, "text_md": "A testclass casts arcane spells."}
    )
    table = _valid_table_record()
    table["fields"]["columns"].append("Spells per Day 1st")
    for row in table["fields"]["rows"]:
        row.append("1")
    errors = check_class_fields(record, _context({table["id"]: table}))
    assert errors == []


# ---------------------------------------------------------------------------
# B10c-mand4 criterion 7: check_table_fields flags a column that mixes
# blank ("") and dash ("—") empty-cell styles.
# ---------------------------------------------------------------------------


def test_check_table_fields_passes_when_empty_cells_all_blank() -> None:
    fields = {"columns": ["A", "Special"], "rows": [["1", ""], ["2", ""], ["3", "Foo"]]}
    assert check_table_fields({"fields": fields}) == []


def test_check_table_fields_passes_when_empty_cells_all_dash() -> None:
    fields = {"columns": ["A", "Special"], "rows": [["1", "—"], ["2", "—"], ["3", "Foo"]]}
    assert check_table_fields({"fields": fields}) == []


def test_check_table_fields_flags_mixed_empty_cell_styles() -> None:
    fields = {"columns": ["A", "Special"], "rows": [["1", "—"], ["2", ""], ["3", "Foo"]]}
    errors = check_table_fields({"fields": fields})
    matching = [e for e in errors if "mixes" in e.lower()]
    assert len(matching) == 1
    assert "1" in matching[0]
    assert "Special" in matching[0]


# ---------------------------------------------------------------------------
# Batch B10c-mand20 (judge round 4, blocker 1): check_table_cells_in_segment
# -- every table cell must be traceable to the owning segment's own text, so
# an extractor can't invent a plausible-looking grid to satisfy the "each
# printed grid is its own table" rule when its segment is truncated before
# the real one.
# ---------------------------------------------------------------------------


def _table_record(
    columns: list[str], rows: list[list[str]], *, name: str = "Sample Table"
) -> dict[str, Any]:
    return {
        "id": f"table:phb1:{slugify(name)}",
        "type": "table",
        "name": name,
        "slug": slugify(name),
        "book_id": "phb1",
        "fields": {"caption": name, "columns": columns, "rows": rows},
    }


def test_check_table_cells_in_segment_passes_when_every_cell_is_present() -> None:
    """Every cell (including a dash placeholder and a stacked, merged
    header) is traceable to the segment's own text."""
    text = (
        "Table 3-6: The Cleric\n\n"
        "Base\nAttack Bonus\nLevel\tSpecial\n"
        "1st\t+0\tBonus feat\n"
        "2nd\t+1\t—\n"
    )
    record = _table_record(
        columns=["Level", "Base Attack Bonus", "Special"],
        rows=[["1st", "+0", "Bonus feat"], ["2nd", "+1", "—"]],
    )
    segment = _segment(text)
    assert check_table_cells_in_segment(record, segment) == []


def test_check_table_cells_in_segment_flags_invented_cells() -> None:
    """The real-corpus defect this check exists for: a haiku extractor
    handed a truncated 639-character sidebar segment invented a whole
    progression grid (`table:phb1:the-paladins-mount`) -- "Natural Armor
    Adj." values and a "Regeneration 1/round" Special entry that appear
    nowhere in the segment's own text."""
    text = (
        "THE PALADIN'S MOUNT\n\n"
        "The paladin's mount is superior to a normal mount of its kind and "
        "has special powers, as described below."
    )
    record = _table_record(
        columns=["Paladin Level", "Bonus HD", "Natural Armor Adj.", "Special"],
        rows=[
            ["5th-7th", "+2", "+2", "Improved evasion"],
            ["15th-20th", "+8", "+8", "Regeneration 1/round"],
        ],
        name="The Paladin's Mount",
    )
    segment = _segment(text)
    errors = check_table_cells_in_segment(record, segment)
    assert len(errors) == 1
    assert "cells are not in the owning segment's text" in errors[0]
    assert "11 of 12" in errors[0]
    assert "Paladin Level" in errors[0]


def test_check_table_cells_in_segment_rejects_a_vacuous_short_cell_match() -> None:
    """B10c-mand20 review finding 1: the token-subsequence fallback's
    bidirectional prefix tolerance let a short/numeric cell like "3rd"
    vacuously match an unrelated token sharing its prefix (e.g. "3" from
    "Table 3-12") -- a real invented row label ("1st-2nd", "3rd-4th", ...
    in `familiar-progression`) only partly failed under the old rule. A
    cell with no alphabetic word of >= 3 letters must now match by literal
    substring alone."""
    text = "Table 3-12: The Sample\n\nLevel\tSpecial\nSome unrelated prose."
    record = _table_record(
        columns=["Level", "Special"],
        rows=[["3rd", "Something not in the text"]],
    )
    segment = _segment(text)
    errors = check_table_cells_in_segment(record, segment)
    assert len(errors) == 1
    assert "2 of 4" in errors[0]
    assert "3rd" in errors[0]


def test_check_table_cells_in_segment_collapses_a_space_after_slash() -> None:
    """B10c-mand20 review finding 1: the real PHB barbarian's iterative-
    attack cell ("+18/+13/+8/+3") is reconstructed with a stray space
    after each slash ("+18/ +13/ +8/ +3") -- normalization must collapse
    that away rather than fail a real, correctly-extracted cell."""
    text = "Level\tBase Attack Bonus\n20th\t+18/ +13/ +8/ +3\n"
    record = _table_record(
        columns=["Level", "Base Attack Bonus"],
        rows=[["20th", "+18/+13/+8/+3"]],
    )
    segment = _segment(text)
    assert check_table_cells_in_segment(record, segment) == []


def test_check_table_cells_in_segment_skips_a_missing_segment() -> None:
    record = _table_record(columns=["A"], rows=[["invented value"]])
    assert check_table_cells_in_segment(record, None) == []


def test_check_table_cells_in_segment_skips_a_textless_segment() -> None:
    record = _table_record(columns=["A"], rows=[["invented value"]])
    assert check_table_cells_in_segment(record, {"seg_id": "x", "pages": [1]}) == []


# ---------------------------------------------------------------------------
# The real corpus: pin the B10c-mand20 calibration against every real
# `table` record under phb1. Skipped (not failed) unless the real data dir
# has them.
# ---------------------------------------------------------------------------


def _real_phb1_table_records() -> list[tuple[str, dict[str, Any], dict[str, Any] | None]]:
    """Loads every real phb1 `table` record with its owning segment
    resolved the same way `owlsperch.validate.runner._resolve_segment_for_
    record` does in production -- `load_segment` (`segments/<book_id>/
    <seg_id>.json` only) -- so this calibration can't diverge from what
    `owlsperch validate`/`build-db` actually see."""
    from owlsperch.validate.loader import load_segment

    data_dir = Path(os.environ.get("OWLSPERCH_DATA", str(Path.home() / "owlsperch-data")))
    records_dir = data_dir / "records" / "phb1" / "table"
    if not records_dir.is_dir():
        return []
    loaded: list[tuple[str, dict[str, Any], dict[str, Any] | None]] = []
    for path in sorted(records_dir.glob("*.json")):
        record = json.loads(path.read_text())
        extraction = record.get("extraction") or {}
        seg_id = extraction.get("segment_id")
        segment = load_segment(data_dir, "phb1", seg_id) if isinstance(seg_id, str) else None
        loaded.append((path.stem, record, segment))
    return loaded


@pytest.mark.corpus
def test_phb1_real_corpus_table_cells_calibration() -> None:
    loaded = _real_phb1_table_records()
    if not loaded:
        pytest.skip("the real phb1 table records are not present")

    failing = {
        stem for stem, record, segment in loaded if check_table_cells_in_segment(record, segment)
    }
    # The real data dir is LIVE, so this is a no-false-positive guard, not a
    # pin of a data state that is meant to go away -- the same semantics
    # `test_phb1_real_corpus_class_validator_calibration` uses. Any table
    # this check reports must be one of the two the rule was calibrated on
    # in 2026-09-21 (`the-paladins-mount`, the fabricated Paladin's Mount
    # grid, and `familiar-progression`, a second, smaller instance of the
    # same defect -- generic D&D level-bracket row labels guessed for a
    # table its own segment's text never reaches); once a table is
    # re-extracted its failure disappears, and an EMPTY failure set is the
    # success state, not a regression. The named tables below must still
    # pass, which is what keeps the rule from going vacuously quiet.
    assert failing <= {"the-paladins-mount", "familiar-progression"}, failing
    assert "table-3-6-the-cleric" not in failing
    assert "familiar-bonuses" not in failing


# ---------------------------------------------------------------------------
# Batch B10c-mand12 rule 1: fields.source_pages is a PDF page span and must
# agree with the record's own envelope `pages`.
# ---------------------------------------------------------------------------


def test_check_class_fields_passes_when_source_pages_match_pages() -> None:
    record = copy.deepcopy(_valid_class_record())
    record["pages"] = [34, 35, 36, 37, 38]
    record["fields"]["source_pages"] = {"start": 34, "end": 38}
    assert check_class_fields(record, _default_context()) == []


def test_check_class_fields_flags_printed_source_pages() -> None:
    """The real-corpus catch: the PHB druid's `source_pages` were written as
    the PRINTED span 33-37 while its `pages` are pdf 34-38."""
    record = copy.deepcopy(_valid_class_record())
    record["pages"] = [34, 35, 36, 37, 38]
    record["fields"]["source_pages"] = {"start": 33, "end": 37}
    errors = [e for e in check_class_fields(record, _default_context()) if "source_pages" in e]
    assert len(errors) == 1
    assert "33-37" in errors[0]
    assert "34-38" in errors[0]


def test_check_class_fields_skips_source_pages_when_pages_missing() -> None:
    record = copy.deepcopy(_valid_class_record())
    record["fields"]["source_pages"] = {"start": 33, "end": 37}
    assert [e for e in check_class_fields(record, _default_context()) if "source_pages" in e] == []


# ---------------------------------------------------------------------------
# Batch B10c-mand12 rule 2: check_class_segment_coverage -- the class checks
# whose evidence lives in the owning segment's own `text`.
# ---------------------------------------------------------------------------

#: One synthetic class segment text in the shape the real PHB class segments
#: have: a flavor run-in paragraph, the GAME RULE INFORMATION block, a
#: "Class Features" heading paragraph, the features prose, then the
#: `Ex-<Class>` and `<Race> <Class> Starting Package` sections.
_SEGMENT_TEXT = "\n".join(
    [
        "TESTCLASS",
        "",
        "Testclasses are brave. Adventures: They adventure. Alignment: Any.",
        "",
        "GAME RULE INFORMATION",
        "",
        "Testclasses have the following game statistics. Abilities: Strength matters.",
        "",
        "Class Features",
        "",
        (
            "All of the following are class features of the testclass. "
            "Weapon and Armor Proficiency: A testclass is proficient with all simple "
            "weapons. "
            "Rage: Once per day a testclass may fly into a rage. "
            "Uncanny Dodge: A testclass retains her Dexterity bonus to AC. "
            "Trap Sense: A testclass gains an intuitive sense for traps."
        ),
        "",
        "Ex-Testclasses",
        "",
        "A testclass who becomes lawful loses the ability to rage.",
        "",
        "Half-Orc Testclass Starting Package",
        "",
        "Armor: Studded leather. Weapons: Greataxe. Skill Selection: Pick four skills.",
    ]
)


def _coverage_record() -> dict[str, Any]:
    """A class record whose `class_features`/`description_sections` cover
    every printed heading in `_SEGMENT_TEXT`, verbatim."""
    record = copy.deepcopy(_valid_class_record())
    record["fields"]["description_sections"] = [
        {"heading": "Adventures", "text_md": "They adventure."},
        {"heading": "Abilities", "text_md": "Strength matters."},
        {"heading": "Ex-Testclasses", "text_md": "A testclass who becomes lawful loses it."},
        {
            "heading": "Half-Orc Testclass Starting Package",
            "text_md": "Armor: Studded leather.",
        },
    ]
    record["fields"]["class_features"] = [
        {
            "name": "Rage",
            "level": 1,
            "text_md": "Once per day a testclass may fly into a rage.",
        },
        {
            "name": "Uncanny Dodge",
            "level": 2,
            "text_md": "A testclass retains her Dexterity bonus to AC.",
        },
        {
            "name": "Trap Sense",
            "level": 3,
            "text_md": "A testclass gains an intuitive sense for traps.",
        },
    ]
    return record


def _segment(text: str = _SEGMENT_TEXT) -> dict[str, Any]:
    return {"seg_id": "phb1-class-p0001", "pages": [1], "text": text}


def test_check_class_segment_coverage_passes_for_a_complete_record() -> None:
    assert check_class_segment_coverage(_coverage_record(), _segment()) == []


def test_check_class_segment_coverage_skips_a_missing_segment() -> None:
    assert check_class_segment_coverage(_coverage_record(), None) == []


def test_check_class_segment_coverage_skips_a_textless_segment() -> None:
    assert check_class_segment_coverage(_coverage_record(), {"seg_id": "x", "pages": [1]}) == []


def test_check_class_segment_coverage_flags_a_dropped_starting_package() -> None:
    """Rule (a), the real-corpus paladin/sorcerer defect: the page prints a
    Starting Package section the record never records."""
    record = _coverage_record()
    record["fields"]["description_sections"] = [
        section
        for section in record["fields"]["description_sections"]
        if "Starting Package" not in section["heading"]
    ]
    errors = check_class_segment_coverage(record, _segment())
    assert len(errors) == 1
    assert "Half-Orc Testclass Starting Package" in errors[0]


def test_check_class_segment_coverage_flags_a_dropped_ex_class_section() -> None:
    record = _coverage_record()
    record["fields"]["description_sections"] = [
        section
        for section in record["fields"]["description_sections"]
        if not section["heading"].startswith("Ex-")
    ]
    errors = check_class_segment_coverage(record, _segment())
    assert len(errors) == 1
    assert "Ex-Testclasses" in errors[0]


def test_check_class_segment_coverage_accepts_an_ex_class_filed_as_a_feature() -> None:
    """The real PHB cleric files its "Ex-Clerics" section as a
    `class_features` entry rather than a `description_sections` one -- an
    equally faithful placement, so either satisfies rule (a)."""
    record = _coverage_record()
    record["fields"]["description_sections"] = [
        section
        for section in record["fields"]["description_sections"]
        if not section["heading"].startswith("Ex-")
    ]
    record["fields"]["class_features"].append(
        {"name": "Ex-Testclasses", "level": 1, "text_md": "You lose it."}
    )
    assert check_class_segment_coverage(record, _segment()) == []


def test_check_class_segment_coverage_flags_a_nested_run_in_heading() -> None:
    """Rule (b), the real-corpus cleric defect: a printed run-in heading
    nested inside a sibling feature's own text_md instead of becoming its
    own class_features entry."""
    record = _coverage_record()
    features = record["fields"]["class_features"]
    dodge = next(f for f in features if f["name"] == "Uncanny Dodge")
    rage = next(f for f in features if f["name"] == "Rage")
    rage["text_md"] = f"{rage['text_md']}\n\n**Uncanny Dodge:** {dodge['text_md']}"
    features.remove(dodge)
    errors = check_class_segment_coverage(record, _segment())
    assert len(errors) == 1
    assert "Uncanny Dodge" in errors[0]
    assert "no class_features entry of its own" in errors[0]


def test_check_class_segment_coverage_does_not_demand_non_feature_labels() -> None:
    """The GAME RULE INFORMATION/flavor/starting-package run-in labels
    ("Abilities:", "Adventures:", "Skill Selection:") are never class
    features, and the printed window never has to cover a sidebar's own
    headings either -- an ALL-CAPS heading closes it."""
    text = _SEGMENT_TEXT.replace(
        "Ex-Testclasses",
        "THE TESTCLASS'S ANIMAL COMPANION\n\nTotem Bond: The companion bonds.\n\nEx-Testclasses",
        1,
    )
    assert check_class_segment_coverage(_coverage_record(), _segment(text)) == []


def test_check_class_segment_coverage_allows_a_generic_class_features_run_in() -> None:
    """The chapter intro prints "Class Features: Special characteristics of
    the class..." as a run-in, and a class segment's back-extended text
    routinely carries it -- the window must not start there."""
    text = (
        "Class Features: Special characteristics of the class. Nonsense: leaks.\n\n" + _SEGMENT_TEXT
    )
    assert check_class_segment_coverage(_coverage_record(), _segment(text)) == []


def test_check_class_segment_coverage_flags_a_condensed_feature_text() -> None:
    """Rule (c), the real-corpus bard defect: a feature's text_md is a
    condensed paraphrase of the printed span."""
    record = _coverage_record()
    rage = next(f for f in record["fields"]["class_features"] if f["name"] == "Rage")
    rage["text_md"] = "A testclass rages."
    errors = check_class_segment_coverage(record, _segment())
    assert len(errors) == 1
    assert "Rage" in errors[0]
    assert "ratio" in errors[0]


def test_check_class_segment_coverage_stitches_a_column_break() -> None:
    """The printed span of the LAST feature in a paragraph cut off at a
    column break continues in another paragraph (out of reading order, as
    `owlsperch.text.columns` emits it). Without the stitch the span is a
    fraction of the printed one and a paraphrase there is invisible."""
    text = "\n".join(
        [
            "Class Features",
            "",
            (
                "den traps, and she never loses her bearings in the deep woods even "
                "after many days of travel without rest, food, or the light of the sun "
                "above her head, which is more than most can say for themselves."
            ),
            "",
            (
                "All of the following are class features of the testclass. "
                "Rage: Once per day a testclass may fly into a rage. "
                "Trap Sense: A testclass gains an intuitive sense for hid-"
            ),
        ]
    )
    record = _coverage_record()
    record["fields"]["description_sections"] = []
    record["fields"]["class_features"] = [
        {
            "name": "Rage",
            "level": 1,
            "text_md": "Once per day a testclass may fly into a rage.",
        },
        {"name": "Trap Sense", "level": 3, "text_md": "A testclass senses traps."},
    ]
    errors = check_class_segment_coverage(record, _segment(text))
    assert len(errors) == 1
    assert "Trap Sense" in errors[0]
    # The stitched span is the truncated tail plus the continuation's whole
    # prefix (47 words), not just the 8 the truncated paragraph itself holds.
    assert "against 47 printed word(s)" in errors[0]


# ---------------------------------------------------------------------------
# Batch B10c-mand16 rule (d): the printed "Abilities:" run-in under GAME
# RULE INFORMATION is its own description_sections entry.
# ---------------------------------------------------------------------------


def test_check_class_segment_coverage_passes_with_an_abilities_section() -> None:
    """`_coverage_record()` already records the "Abilities:" run-in
    `_SEGMENT_TEXT` prints under GAME RULE INFORMATION."""
    assert check_class_segment_coverage(_coverage_record(), _segment()) == []


def test_check_class_segment_coverage_flags_a_dropped_abilities_section() -> None:
    record = _coverage_record()
    record["fields"]["description_sections"] = [
        section
        for section in record["fields"]["description_sections"]
        if section["heading"] != "Abilities"
    ]
    errors = check_class_segment_coverage(record, _segment())
    assert len(errors) == 1
    assert "Abilities" in errors[0]
    assert "GAME RULE INFORMATION" in errors[0]


def test_check_class_segment_coverage_skips_abilities_with_no_gri_window() -> None:
    """A segment printing no "GAME RULE INFORMATION" heading at all (a page
    layout this window can't read, or simply a segment with none) must not
    be reported as a missing Abilities section."""
    record = _coverage_record()
    record["fields"]["description_sections"] = [
        section
        for section in record["fields"]["description_sections"]
        if section["heading"] != "Abilities"
    ]
    text = _SEGMENT_TEXT.replace("GAME RULE INFORMATION\n\n", "")
    assert check_class_segment_coverage(record, _segment(text)) == []


def test_check_class_segment_coverage_skips_abilities_with_no_marker() -> None:
    """A GAME RULE INFORMATION window that never prints an "Abilities:"
    run-in at all (a prestige class need not have one) is not a failure."""
    record = _coverage_record()
    record["fields"]["description_sections"] = [
        section
        for section in record["fields"]["description_sections"]
        if section["heading"] != "Abilities"
    ]
    text = _SEGMENT_TEXT.replace(
        "Testclasses have the following game statistics. Abilities: Strength matters.",
        "Testclasses have the following game statistics.",
    )
    assert check_class_segment_coverage(record, _segment(text)) == []


# ---------------------------------------------------------------------------
# B10c-mand12 review: `_is_heading_shaped` keeps an ordinary mid-paragraph
# clause that ends in a colon from being read as a run-in heading.
# ---------------------------------------------------------------------------


def test_is_heading_shaped_accepts_the_longest_real_phb_headings() -> None:
    for heading in (
        "Tongue of the Sun and Moon (Ex)",
        "Chaotic, Evil, Good, and Lawful Spells",
        "Deity, Domains, and Domain Spells",
        "Turn or Rebuke Undead (Su)",
        "Hide in Plain Sight (Ex)",
        "Skill Points at Each Additional Level",
        "Weapon and Armor Proficiency",
        "Resist Nature’s Lure (Ex)",
        "A Thousand Faces (Su)",
    ):
        assert _is_heading_shaped(heading), heading


def test_is_heading_shaped_rejects_sentence_clauses() -> None:
    for clause in (
        "If she has a familiar, the following apply",
        "Her options for new forms include",
        "The following restrictions apply to a paladin who wishes to keep her mount",
    ):
        assert not _is_heading_shaped(clause), clause


def test_run_in_headings_ignores_a_mid_sentence_clause_colon() -> None:
    paragraph = (
        "Rage: A testclass may fly into a rage. "
        "If she has a familiar, the following apply: it gains HD. "
        "Her options for new forms include: bear, wolf. "
        "Trap Sense: She senses traps."
    )
    assert [heading for _s, _e, heading in _run_in_headings(paragraph)] == [
        "Rage",
        "Trap Sense",
    ]


def test_check_class_segment_coverage_ignores_a_clause_colon_in_feature_prose() -> None:
    """The clause is neither reported as a missing feature nor allowed to
    cut the real feature's printed span short."""
    text = "\n".join(
        [
            "Class Features",
            "",
            (
                "All of the following are class features of the testclass. "
                "Rage: Once per day a testclass may fly into a rage. "
                "If she has a familiar, the following apply: the familiar rages too. "
                "Trap Sense: A testclass gains an intuitive sense for traps."
            ),
        ]
    )
    record = _coverage_record()
    record["fields"]["description_sections"] = []
    record["fields"]["class_features"] = [
        {
            "name": "Rage",
            "level": 1,
            "text_md": (
                "Once per day a testclass may fly into a rage. "
                "If she has a familiar, the following apply: the familiar rages too."
            ),
        },
        {
            "name": "Trap Sense",
            "level": 3,
            "text_md": "A testclass gains an intuitive sense for traps.",
        },
    ]
    assert check_class_segment_coverage(record, _segment(text)) == []


# ---------------------------------------------------------------------------
# B10c-mand12 review: the column-break stitch is corroborated and capped --
# a WRONG pairing would append unrelated prose and lower the ratio, so it
# takes a mid-word hyphen, a unique pairing, and stops at the
# continuation's own first run-in heading.
# ---------------------------------------------------------------------------

_TRUNCATED = (
    "Trap Sense: A testclass gains an intuitive sense for traps and other "
    "hidden hazards of the deep places of the world, so that she is rarely "
    "caught out by a pit or a falling block of ma-"
)
_CONTINUATION = (
    "sonry that a less wary soul would walk straight into without any warning "
    "at all, whatever the light. "
    "Uncanny Dodge: She keeps her Dexterity bonus to Armor Class even when "
    "caught flat-footed, which is a different feature entirely and must not "
    "be appended to the span of the feature before it under any circumstance."
)


def _stitch_window(*paragraphs: str) -> list[str]:
    return ["Class Features", *paragraphs]


def test_column_break_continuation_stitches_a_hyphenated_break() -> None:
    stitch = _column_break_continuation(_stitch_window(_CONTINUATION, _TRUNCATED))
    assert stitch is not None
    index, prefix = stitch
    assert index == 2
    # Capped at the continuation's own first run-in heading: the following
    # feature's prose is never appended to this one's span.
    assert "Uncanny Dodge" not in prefix
    assert prefix.strip().startswith("sonry")


def test_column_break_continuation_needs_a_mid_word_hyphen() -> None:
    """A paragraph that merely ends without a full stop is routine in a
    reconstructed column and is no evidence of WHICH paragraph continues
    it -- the PHB monk's and druid's windows both end that way."""
    truncated = _TRUNCATED.replace("of ma-", "of masonry and")
    assert _column_break_continuation(_stitch_window(_CONTINUATION, truncated)) is None


def test_column_break_continuation_suppressed_by_two_truncated_paragraphs() -> None:
    second = (
        "Bonus Languages: A testclass may choose any language as a bonus "
        "language, including the secret tongues of the wild places, and she "
        "learns them faster than anyone else could ever hope to-"
    )
    assert _column_break_continuation(_stitch_window(_CONTINUATION, _TRUNCATED, second)) is None


def test_column_break_continuation_suppressed_by_two_continuations() -> None:
    second = (
        "and so on, for as long as she keeps her concentration on the task "
        "at hand, whatever else may be happening around her at the time of "
        "the attempt, without any further penalty at all."
    )
    assert _column_break_continuation(_stitch_window(_CONTINUATION, _TRUNCATED, second)) is None


# ---------------------------------------------------------------------------
# B10c-mand12 review: TYPE_SEGMENT_CHECKS is really dispatched by
# `validate_record`, and rule (a) still runs on a window-less segment.
# ---------------------------------------------------------------------------


def test_check_class_segment_coverage_rule_a_runs_without_a_window() -> None:
    """A segment whose text has no recognizable Class Features section
    still gets rule (a) -- a dropped Starting Package is visible without
    one -- while (b)/(c) are skipped."""
    text = "\n".join(
        [
            "TESTCLASS",
            "",
            "Testclasses are brave. Rage: They rage a great deal, at length, and often.",
            "",
            "Half-Orc Testclass Starting Package",
            "",
            "Armor: Studded leather.",
        ]
    )
    record = _coverage_record()
    record["fields"]["description_sections"] = []
    errors = check_class_segment_coverage(record, _segment(text))
    assert len(errors) == 1
    assert "Half-Orc Testclass Starting Package" in errors[0]


def test_validate_record_dispatches_type_segment_checks() -> None:
    """`TYPE_SEGMENT_CHECKS` is wired into `owlsperch.validate.runner
    .validate_record`, so `validate`, `build-db` and `find_bump_candidates`
    all see these failures."""
    from owlsperch.validate.loader import CompiledSchemas
    from owlsperch.validate.runner import validate_record

    compiled = CompiledSchemas.load()
    record = copy.deepcopy(_valid_class_record())
    record["type"] = "prestige_class"
    record["id"] = "prestige_class:phb1:testclass"
    segment = _segment(
        "TESTCLASS\n\nEx-Testclasses\n\nA testclass who breaks her oath loses everything."
    )
    errors = validate_record(record, type_dir="prestige_class", compiled=compiled, segment=segment)
    assert any("Ex-Testclasses" in error for error in errors)


# ---------------------------------------------------------------------------
# The real corpus: pin the B10c-mand12 calibration (plus B10c-mand16's
# Abilities rule) against the 11 real PHB class records, so a later change
# to any of these rules that alters which of them fail shows up here.
# Skipped (not failed) unless the real data dir has all 11 records and
# their owning segments.
# ---------------------------------------------------------------------------

#: The exact real-data outcome each rule must produce, per class record, as
#: measured on 2026-09-21 against `$OWLSPERCH_DATA/records/phb1/class/`.
_EXPECTED_CORPUS_FAILURES = {
    "source_pages": {"druid"},
    "extra_sections": {"paladin", "sorcerer"},
    "run_in_headings": {"cleric"},
    "feature_coverage": {"bard"},
    "abilities": {"barbarian", "fighter", "monk", "ranger", "rogue", "wizard"},
}


def _real_phb1_class_records() -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    data_dir = Path(os.environ.get("OWLSPERCH_DATA", str(Path.home() / "owlsperch-data")))
    records_dir = data_dir / "records" / "phb1" / "class"
    if not records_dir.is_dir():
        return []
    loaded: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    for path in sorted(records_dir.glob("*.json")):
        record = json.loads(path.read_text())
        extraction = record.get("extraction") or {}
        seg_id = extraction.get("segment_id")
        if not isinstance(seg_id, str):
            return []
        segment_path = data_dir / "segments" / "phb1" / f"{seg_id}.json"
        if not segment_path.is_file():
            return []
        loaded.append((path.stem, record, json.loads(segment_path.read_text())))
    return loaded


@pytest.mark.corpus
def test_phb1_real_corpus_class_validator_calibration() -> None:
    loaded = _real_phb1_class_records()
    if len(loaded) != 11:
        pytest.skip("the real phb1 class records/segments are not all present")

    failures: dict[str, set[str]] = {key: set() for key in _EXPECTED_CORPUS_FAILURES}
    for stem, record, segment in loaded:
        # An empty context is enough here: the source_pages check runs before
        # `check_class_fields` gives up on an unresolvable `level_table`.
        for error in check_class_fields(record, _context({})):
            if "source_pages" in error:
                failures["source_pages"].add(stem)
        for error in check_class_segment_coverage(record, segment):
            if "that no description_sections/class_features entry records" in error:
                failures["extra_sections"].add(stem)
            elif "no class_features entry of its own" in error:
                failures["run_in_headings"].add(stem)
            elif "looks condensed rather than verbatim" in error:
                failures["feature_coverage"].add(stem)
            elif 'with heading "Abilities" records' in error:
                failures["abilities"].add(stem)

    # The real data dir is LIVE: `_EXPECTED_CORPUS_FAILURES` is the set the
    # rules were calibrated on (2026-09-21, before those five classes were
    # re-extracted under the B10c-mand13 prompt rules). Once a class is
    # fixed its failure disappears, so this is a no-false-positive guard --
    # any failure the rules report must be one of the calibrated ones --
    # rather than a pin of a data state that is meant to go away.
    for key, stems in failures.items():
        assert stems <= _EXPECTED_CORPUS_FAILURES[key], (key, stems)


# ---------------------------------------------------------------------------
# Batch B12: monster/npc/template checks. `check_monster_fields` is what a
# wrong stat-block extraction has to get past -- the numbers agreeing with
# their own printed line, the classification coming from the committed
# reference lists, and every printed Special Attacks/Qualities token
# accounted for -- and `check_monster_segment_coverage` is what an INVENTED
# ability or creature has to get past.
# ---------------------------------------------------------------------------


def _monster_fields(**overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "size": "Medium",
        "type": "Undead",
        "subtypes": ["Incorporeal"],
        "hd": {"count": 4, "die": 12, "text": "4d12 (26 hp)"},
        "hp": 26,
        "initiative": 5,
        "speed": {"text": "Fly 30 ft. (perfect) (6 squares)", "modes": []},
        "ac": {
            "total": 15,
            "touch": 15,
            "flat_footed": 14,
            "text": "15 (+1 Dex, +4 deflection), touch 15, flat-footed 14",
        },
        "bab": 2,
        "grapple": None,
        "attack": {"text": "Incorporeal touch +3 melee (1d4 Wisdom drain)", "attacks": []},
        "full_attack": {"text": "Incorporeal touch +3 melee (1d4 Wisdom drain)", "attacks": []},
        "space_reach": {"space_ft": 5, "reach_ft": 5, "text": "5 ft./5 ft."},
        "special_attacks": ["Babble"],
        "special_qualities": ["Darkvision 60 ft.", "+2 turn resistance", "undead traits"],
        "special_abilities": [
            {"name": "Babble", "kind": "Su", "text_md": "It constantly mutters and whines."}
        ],
        "saves": {"fort": 1, "ref": 4, "will": 4},
        "abilities": {"str": None, "dex": 12, "con": None, "int": 11, "wis": 11, "cha": 18},
        "skills": {"text": "Hide +8, Listen +7", "entries": []},
        "feats": ["Improved Initiative"],
        "environment": "Any",
        "organization": "Solitary",
        "cr": 3,
        "cr_text": "3",
        "treasure": "None",
        "alignment": "Always neutral evil",
        "advancement": "5-12 HD (Medium)",
        "level_adjustment": {"value": None, "text": "—"},
        "group": None,
        "variant_label": None,
        "description_sections": [],
        "source_pages": {"start": 10, "end": 10},
    }
    fields.update(overrides)
    return fields


def _monster_record(name: str = "Testwraith", **overrides: Any) -> dict[str, Any]:
    return {
        "id": f"monster:tb:{slugify(name)}",
        "type": "monster",
        "name": name,
        "slug": slugify(name),
        "book_id": "tb",
        "pages": [10],
        "citation": "TB p. 10",
        "text_md": "A shape without features drifts toward you.",
        "fields": _monster_fields(**overrides),
        "schema_version": 1,
        "extraction": {
            "tier": "sonnet",
            "model": "m",
            "segment_id": "tb-monster-p0010",
            "timestamp": "2026-01-01T00:00:00+00:00",
        },
    }


#: A segment whose text prints the record above: its heading, the stat
#: block's Abilities line (with the dashes that justify its `null` scores)
#: and the "Combat" section's one run-in ability heading.
_MONSTER_SEGMENT_TEXT = (
    "TESTWRAITH\n\n"
    "Medium Undead (Incorporeal) Hit Dice: 4d12 (26 hp) "
    "Abilities: Str —, Dex 12, Con —, Int 11, Wis 11, Cha 18 "
    "Challenge Rating: 3 Level Adjustment: —\n\n"
    "COMBAT\n\n"
    "It keeps flailing away at enemies. Babble (Su): It constantly mutters "
    "and whines to itself, creating a hypnotic effect."
)


def _monster_segment(text: str = _MONSTER_SEGMENT_TEXT) -> dict[str, Any]:
    return {"seg_id": "tb-monster-p0010", "pages": [10], "text": text}


def test_check_monster_fields_passes_a_well_formed_stat_block() -> None:
    assert check_monster_fields(_monster_record()) == []


def test_check_monster_fields_flags_an_hd_count_that_contradicts_its_own_text() -> None:
    record = _monster_record(hd={"count": 5, "die": 12, "text": "4d12 (26 hp)"})
    errors = check_monster_fields(record)
    assert any("hd.count 5" in e and "4d12" in e for e in errors), errors


def test_check_monster_fields_accepts_a_half_hit_die() -> None:
    record = _monster_record(hd={"count": 0.5, "die": 10, "text": "1/2 d10 (2 hp)"}, hp=2)
    assert check_monster_fields(record) == []


def test_check_monster_fields_flags_hp_that_contradicts_its_own_line() -> None:
    record = _monster_record(hp=30)
    errors = check_monster_fields(record)
    assert any("hp 30" in e for e in errors), errors


def test_check_monster_fields_flags_a_die_size_that_contradicts_its_own_text() -> None:
    record = _monster_record(hd={"count": 4, "die": 8, "text": "4d12 (26 hp)"})
    errors = check_monster_fields(record)
    assert any("hd.die 8" in e for e in errors), errors


def test_check_monster_fields_normalizes_a_printed_cr_fraction() -> None:
    assert check_monster_fields(_monster_record(cr=0.5, cr_text="1/2")) == []
    assert check_monster_fields(_monster_record(cr=0.333, cr_text="1/3")) == []


def test_check_monster_fields_flags_a_cr_that_contradicts_its_own_text() -> None:
    errors = check_monster_fields(_monster_record(cr=1, cr_text="1/2"))
    assert any("cr 1 does not match cr_text" in e for e in errors), errors


def test_check_monster_fields_flags_an_unparseable_cr_text() -> None:
    errors = check_monster_fields(_monster_record(cr_text="see text"))
    assert any("cr_text 'see text'" in e for e in errors), errors


def test_check_monster_fields_flags_an_unknown_creature_type() -> None:
    errors = check_monster_fields(_monster_record(type="Celestial"))
    assert any("type 'Celestial'" in e for e in errors), errors


def test_check_monster_fields_flags_an_unknown_subtype() -> None:
    errors = check_monster_fields(_monster_record(subtypes=["Incorporeal", "Spooky"]))
    assert any("subtype 'Spooky'" in e for e in errors), errors


def test_check_monster_fields_flags_an_unknown_size() -> None:
    errors = check_monster_fields(_monster_record(size="Really Big"))
    assert any("size 'Really Big'" in e for e in errors), errors


def test_check_monster_fields_matches_the_reference_lists_case_insensitively() -> None:
    record = _monster_record(size="medium", type="undead", subtypes=["incorporeal"])
    assert check_monster_fields(record) == []


def test_check_monster_fields_flags_a_missing_save() -> None:
    errors = check_monster_fields(_monster_record(saves={"fort": 1, "ref": 4}))
    assert any("saves.will" in e for e in errors), errors


def test_check_monster_fields_flags_a_missing_ability_score() -> None:
    errors = check_monster_fields(
        _monster_record(abilities={"str": None, "dex": 12, "con": None, "int": 11, "wis": 11})
    )
    assert any("abilities.cha is missing" in e for e in errors), errors


def test_check_monster_fields_flags_a_special_attack_with_no_ability_entry() -> None:
    errors = check_monster_fields(
        _monster_record(special_attacks=["Babble", "Wisdom drain"]),
    )
    assert any("'Wisdom drain' has no matching special_abilities entry" in e for e in errors), (
        errors
    )


def test_check_monster_fields_allows_the_generic_quality_tokens() -> None:
    """ "Darkvision 60 ft.", "+2 turn resistance" and "undead traits" are in
    the base record's own `special_qualities` and have no
    `special_abilities` entry -- the MM never describes them as run-ins."""
    record = _monster_record(
        special_qualities=[
            "Darkvision 60 ft.",
            "low-light vision",
            "damage reduction 10/evil",
            "spell resistance 30",
            "immunity to acid, cold, and petrification",
            "fast healing 5",
            "incorporeal traits",
        ]
    )
    assert check_monster_fields(record) == []


def test_check_monster_fields_flags_two_abilities_with_the_same_name() -> None:
    record = _monster_record(
        special_abilities=[
            {"name": "Babble", "kind": "Su", "text_md": "One."},
            {"name": "Babbles", "kind": "Su", "text_md": "Two."},
        ]
    )
    errors = check_monster_fields(record)
    assert any("two entries whose names normalize to the same thing" in e for e in errors), errors


def test_check_monster_fields_accepts_a_grouped_entry() -> None:
    record = _monster_record("Angel, Astral Deva", group="Angel", variant_label="Astral Deva")
    assert check_monster_fields(record) == []


def test_check_monster_fields_flags_a_grouped_name_that_does_not_match() -> None:
    record = _monster_record("Astral Deva", group="Angel", variant_label="Astral Deva")
    errors = check_monster_fields(record)
    assert any("does not match" in e and "Angel, Astral Deva" in e for e in errors), errors


def test_check_monster_fields_flags_a_variant_label_without_a_group() -> None:
    record = _monster_record("Huge", variant_label="Huge")
    errors = check_monster_fields(record)
    assert any("must be set together" in e for e in errors), errors


def test_check_monster_fields_runs_for_npc_records_too() -> None:
    from owlsperch.validate.checks import TYPE_FIELD_CHECKS

    assert TYPE_FIELD_CHECKS["npc"] is check_monster_fields
    assert TYPE_FIELD_CHECKS["monster"] is check_monster_fields


# --- segment-aware monster checks ------------------------------------------


def test_check_monster_segment_coverage_passes_against_its_own_segment() -> None:
    assert check_monster_segment_coverage(_monster_record(), _monster_segment()) == []


def test_check_monster_segment_coverage_flags_a_creature_the_segment_never_prints() -> None:
    errors = check_monster_segment_coverage(
        _monster_record("Gelatinous Testcube"), _monster_segment()
    )
    assert any("heading is not printed" in e for e in errors), errors


def test_check_monster_segment_coverage_accepts_a_grouped_creature_by_group_and_variant() -> None:
    """A grouped entry's sub-block heading is often just the variant label
    ("Adult") under the group's own heading ("RED DRAGON"), so the full
    `"<group>, <variant>"` name never appears verbatim."""
    segment = _monster_segment("RED DRAGON\n\nAdult\n\nA red dragon is terrible.")
    record = _monster_record("Red Dragon, Adult", group="Red Dragon", variant_label="Adult")
    assert not any(
        "heading is not printed" in e for e in check_monster_segment_coverage(record, segment)
    )


def test_check_monster_segment_coverage_flags_an_invented_ability() -> None:
    record = _monster_record(
        special_attacks=["Babble"],
        special_abilities=[
            {"name": "Babble", "kind": "Su", "text_md": "One."},
            {"name": "Energy Drain", "kind": "Su", "text_md": "Invented from memory."},
        ],
    )
    errors = check_monster_segment_coverage(record, _monster_segment())
    assert any("'Energy Drain' is not printed as a run-in heading" in e for e in errors), errors


def test_check_monster_segment_coverage_flags_a_described_token_with_no_entry() -> None:
    record = _monster_record(special_attacks=["Babble"], special_abilities=[])
    errors = check_monster_segment_coverage(record, _monster_segment())
    assert any("IS described as a run-in" in e for e in errors), errors


def test_check_monster_segment_coverage_flags_an_unjustified_null_ability() -> None:
    segment = _monster_segment(
        _MONSTER_SEGMENT_TEXT.replace("Str —, Dex 12, Con —", "Str 10, Dex 12, Con 11")
    )
    errors = check_monster_segment_coverage(_monster_record(), segment)
    assert any("abilities.str is null but" in e for e in errors), errors
    assert any("abilities.con is null but" in e for e in errors), errors


def test_check_monster_segment_coverage_skips_without_a_segment() -> None:
    assert check_monster_segment_coverage(_monster_record(), None) == []
    assert check_monster_segment_coverage(_monster_record(), {"seg_id": "x", "pages": [10]}) == []


# --- template --------------------------------------------------------------


def _template_record(**overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "acquired_or_inherited": "acquired",
        "applies_to": "Any living corporeal creature",
        "cr_change": "Same as the base creature +1",
        "la_change": "Same as the base creature +2",
        "modifications": [
            {"section": "Size and Type", "text_md": "The creature's type does not change."},
            {"section": "Armor Class", "text_md": "+1 deflection bonus to AC."},
        ],
        "source_pages": {"start": 3, "end": 3},
    }
    fields.update(overrides)
    return {
        "id": "template:tb:testtouched",
        "type": "template",
        "name": "Testtouched",
        "slug": "testtouched",
        "book_id": "tb",
        "pages": [3],
        "citation": "TB p. 3",
        "text_md": "A testtouched creature is wrong in the colours.",
        "fields": fields,
        "schema_version": 1,
        "extraction": {
            "tier": "sonnet",
            "model": "m",
            "segment_id": "tb-template-p0003",
            "timestamp": "2026-01-01T00:00:00+00:00",
        },
    }


def test_check_template_fields_passes_a_well_formed_template() -> None:
    assert check_template_fields(_template_record()) == []


def test_check_template_fields_flags_an_empty_applies_to() -> None:
    errors = check_template_fields(_template_record(applies_to="   "))
    assert any("applies_to is missing or empty" in e for e in errors), errors


def test_check_template_fields_flags_empty_modifications() -> None:
    errors = check_template_fields(_template_record(modifications=[]))
    assert any("modifications is missing or empty" in e for e in errors), errors


def test_check_template_fields_flags_a_repeated_printed_section() -> None:
    errors = check_template_fields(
        _template_record(
            modifications=[
                {"section": "Special Qualities", "text_md": "One."},
                {"section": "Special Quality", "text_md": "Two."},
            ]
        )
    )
    assert any("two entries for the same printed section" in e for e in errors), errors


def test_check_template_fields_flags_an_empty_section_text() -> None:
    errors = check_template_fields(
        _template_record(modifications=[{"section": "Hit Dice", "text_md": "  "}])
    )
    assert any("has empty text_md" in e for e in errors), errors
