"""Unit tests for `owlsperch.validate.checks` -- the id/slug convention,
type-directory match, schema_version match, and the spell-specific field
checks."""

from __future__ import annotations

import copy
from typing import Any

from owlsperch.validate.checks import (
    NULL_CONTEXT,
    ValidationContext,
    _bab_progression_cell,
    _normalize_special_token,
    _save_progression_cell,
    _special_token_matches_feature,
    _split_special_cell,
    check_class_fields,
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


def test_check_class_fields_longer_feature_name_still_matches_shorter_token() -> None:
    """The pre-existing direction (feature name is a prefix of the token)
    keeps working, and so does the feature name being the LONGER one."""
    errors = _class_with_special_and_feature("Sneak attack +1d6", "Sneak Attack (Ex)")
    assert not any("Sneak attack" in e and "no matching" in e for e in errors)
    errors = _class_with_special_and_feature("Trapfinding", "Trapfinding (Ex) and Trap Sense")
    assert not any("Trapfinding" in e and "no matching" in e for e in errors)


def test_check_class_fields_containment_is_word_bounded_not_substring() -> None:
    """Bidirectional containment must not turn into substring matching:
    a token "Defeat" is not described by a feature named "Feat", and a
    feature "Rage" does not cover a Special cell reading "Courage"."""
    errors = _class_with_special_and_feature("Defeat", "Feat")
    assert any("Defeat" in e and "no matching class_features entry" in e for e in errors)
    errors = _class_with_special_and_feature("Courage", "Rage")
    assert any("Courage" in e and "no matching class_features entry" in e for e in errors)


def test_special_token_matches_feature_rejects_empty_names() -> None:
    assert not _special_token_matches_feature("", "familiar")
    assert not _special_token_matches_feature("summon familiar", "")
    assert _special_token_matches_feature("summon familiar", "familiar")
    assert _special_token_matches_feature("familiar", "summon familiar")
    assert not _special_token_matches_feature("familiar spirit", "summon familiar")


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
