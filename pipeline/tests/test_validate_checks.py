"""Unit tests for `owlsperch.validate.checks` -- the id/slug convention,
type-directory match, schema_version match, and the spell-specific field
checks."""

from __future__ import annotations

from owlsperch.validate.checks import (
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
