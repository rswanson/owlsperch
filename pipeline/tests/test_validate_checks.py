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
    check_pages_within_segment,
    check_rules_section_fields,
    check_spell_fields,
    check_table_fields,
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
