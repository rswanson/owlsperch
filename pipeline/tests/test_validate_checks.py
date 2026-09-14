"""Unit tests for `owlsperch.validate.checks` -- the id/slug convention,
type-directory match, schema_version match, and the spell-specific field
checks."""

from __future__ import annotations

import copy
from typing import Any

from owlsperch.validate.checks import (
    NULL_CONTEXT,
    ValidationContext,
    check_class_fields,
    check_envelope_consistency,
    check_feat_fields,
    check_pages_within_segment,
    check_rules_section_fields,
    check_spell_fields,
    check_table_fields,
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
                {"name": "Trap Sense", "level": 3, "text_md": ""},
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
