"""Unit tests for `owlsperch.validate.checks` -- the id/slug convention,
type-directory match, schema_version match, and the spell-specific field
checks."""

from __future__ import annotations

from owlsperch.validate.checks import (
    check_envelope_consistency,
    check_pages_within_segment,
    check_spell_fields,
    expected_id,
    slugify,
)


def test_slugify_ascii_folds_and_kebab_cases() -> None:
    assert slugify("Glimmerfrost Ward") == "glimmerfrost-ward"
    assert slugify("Melf's Acid Arrow") == "melfs-acid-arrow"
    assert slugify("Protection from Evil/Good") == "protection-from-evil-good"


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
    errors = check_spell_fields({"fields": {"school": "", "levels": [{"class": "Wizard", "level": 3}]}})
    assert any("school" in e for e in errors)


def test_check_spell_fields_rejects_wrong_type_school() -> None:
    errors = check_spell_fields({"fields": {"school": 3, "levels": [{"class": "Wizard", "level": 3}]}})
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
