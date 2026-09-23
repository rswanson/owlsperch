"""Tests for `owlsperch.schemas` (registry/schema loading) and the schema
self-test from B4 acceptance criterion 5: every schema file parses, is a
valid JSON Schema draft 2020-12 schema, the registry references existing
files, and every property under a type's `fields` schema carries complete
`x-ui`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from owlsperch.schemas import (
    SchemaError,
    default_schemas_dir,
    load_creature_types,
    load_registry,
    load_sizes,
    load_skills,
)

_REQUIRED_X_UI_KEYS = {"label", "filterable", "sortable", "group", "order"}


def _repo_schemas_dir() -> Path:
    # pipeline/tests/test_schemas.py -> repo root -> schemas/
    return Path(__file__).resolve().parent.parent.parent / "schemas"


# ---------------------------------------------------------------------------
# default_schemas_dir / load_registry
# ---------------------------------------------------------------------------


def test_default_schemas_dir_finds_the_real_repo_schemas_dir() -> None:
    assert default_schemas_dir() == _repo_schemas_dir()


def test_default_schemas_dir_honors_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OWLSPERCH_SCHEMAS", str(tmp_path))
    assert default_schemas_dir() == tmp_path


def test_load_registry_loads_envelope_and_types() -> None:
    registry = load_registry(_repo_schemas_dir())
    assert "spell" in registry.types
    assert registry.types["spell"].schema_file == "spell.json"
    assert registry.types["spell"].version == 3
    assert registry.envelope_schema["title"] == "Record envelope"


def test_load_registry_missing_dir_raises_clear_error(tmp_path: Path) -> None:
    with pytest.raises(SchemaError):
        load_registry(tmp_path / "does-not-exist")


# ---------------------------------------------------------------------------
# Schema self-test (acceptance criterion 5)
# ---------------------------------------------------------------------------


def test_every_schema_file_parses_and_is_valid_2020_12() -> None:
    schemas_dir = _repo_schemas_dir()
    for path in sorted(schemas_dir.glob("*.json")):
        if path.name == "registry.json":
            continue
        schema = json.loads(path.read_text())
        Draft202012Validator.check_schema(schema)


def test_registry_references_existing_schema_files() -> None:
    schemas_dir = _repo_schemas_dir()
    registry = load_registry(schemas_dir)
    for type_name, info in registry.types.items():
        schema_path = schemas_dir / info.schema_file
        assert schema_path.is_file(), f"{type_name}: {schema_path} does not exist"


def test_every_type_schema_field_has_complete_x_ui() -> None:
    schemas_dir = _repo_schemas_dir()
    registry = load_registry(schemas_dir)
    for type_name in registry.types:
        schema = registry.load_type_schema(type_name)
        for field_name, prop in schema["properties"].items():
            x_ui = prop.get("x-ui")
            assert x_ui is not None, f"{type_name}.{field_name} has no x-ui"
            missing = _REQUIRED_X_UI_KEYS - x_ui.keys()
            assert not missing, f"{type_name}.{field_name} missing x-ui keys: {missing}"


#: Files under `schemas/` that aren't JSON Schema type/envelope files and so
#: carry no top-level `version`: `registry.json` (the type index),
#: `categories.json` (batch B10b's plain rules-taxonomy data file, D8),
#: `skills.json` (batch B10c's plain skill-name list, D9), and
#: `sizes.json`/`creature_types.json` (batch B12's plain size and
#: creature-type/subtype lists).
_NON_SCHEMA_FILES = {
    "registry.json",
    "categories.json",
    "skills.json",
    "sizes.json",
    "creature_types.json",
}


def test_every_schema_has_a_top_level_integer_version() -> None:
    schemas_dir = _repo_schemas_dir()
    for path in sorted(schemas_dir.glob("*.json")):
        if path.name in _NON_SCHEMA_FILES:
            continue
        schema = json.loads(path.read_text())
        assert isinstance(schema.get("version"), int)


# ---------------------------------------------------------------------------
# Example-record self-test (B5 follow-up): every registered type with a
# `schemas/examples/<type>.json` fixture must actually conform to its own
# schema (envelope + `fields`) and pass the same envelope-consistency and
# type-specific field checks `owlsperch validate` runs, so the EXAMPLE
# RECORD rendered into a subagent prompt (`owlsperch.queue.prompt`) is never
# itself invalid.
# ---------------------------------------------------------------------------


def test_every_type_examples_file_validates_against_its_schema() -> None:
    from jsonschema import Draft202012Validator

    from owlsperch.validate.checks import TYPE_FIELD_CHECKS, check_envelope_consistency

    schemas_dir = _repo_schemas_dir()
    registry = load_registry(schemas_dir)
    envelope_validator = Draft202012Validator(registry.envelope_schema)

    examples_dir = schemas_dir / "examples"
    example_files = sorted(examples_dir.glob("*.json")) if examples_dir.is_dir() else []
    assert example_files, "expected at least one schemas/examples/*.json fixture"

    for path in example_files:
        type_name = path.stem
        assert type_name in registry.types, f"{path}: '{type_name}' is not a registered type"
        record = json.loads(path.read_text())

        envelope_errors = sorted(envelope_validator.iter_errors(record), key=str)
        assert not envelope_errors, f"{path}: envelope errors: {envelope_errors}"

        type_schema = registry.load_type_schema(type_name)
        type_validator = Draft202012Validator(type_schema)
        field_errors = sorted(type_validator.iter_errors(record.get("fields", {})), key=str)
        assert not field_errors, f"{path}: fields errors: {field_errors}"

        consistency_errors = check_envelope_consistency(
            record, type_dir=type_name, registry_version=registry.types[type_name].version
        )
        assert not consistency_errors, f"{path}: consistency errors: {consistency_errors}"

        field_check = TYPE_FIELD_CHECKS.get(type_name)
        if field_check is not None:
            type_field_errors = field_check(record)
            assert not type_field_errors, f"{path}: type field errors: {type_field_errors}"


def test_every_examples_file_has_a_slug_and_id_consistent_with_its_name() -> None:
    """B10 retrospective follow-up (acceptance criterion 9): every
    `schemas/examples/*.json` fixture's `slug` is `slugify(name)` and `id` is
    `f"{type}:{book_id}:{slug}"` -- so a fixture that models a qualified name
    (e.g. rules_section's `Class Features (Sable Knight)`) is caught if its
    slug/id ever drift out of sync with the name."""
    from owlsperch.validate.checks import slugify

    schemas_dir = _repo_schemas_dir()
    examples_dir = schemas_dir / "examples"
    example_files = sorted(examples_dir.glob("*.json")) if examples_dir.is_dir() else []
    assert example_files, "expected at least one schemas/examples/*.json fixture"

    for path in example_files:
        record = json.loads(path.read_text())
        name = record["name"]
        slug = record["slug"]
        expected_slug = slugify(name)
        assert slug == expected_slug, f"{path}: slug {slug!r} != slugify(name) {expected_slug!r}"

        expected_id = f"{record['type']}:{record['book_id']}:{slug}"
        assert record["id"] == expected_id, f"{path}: id {record['id']!r} != {expected_id!r}"


# ---------------------------------------------------------------------------
# Batch B10c, design decision D9: load_skills.
# ---------------------------------------------------------------------------


def test_load_skills_returns_the_committed_skill_list() -> None:
    skills = load_skills(_repo_schemas_dir())
    assert "Climb" in skills
    assert "Knowledge" in skills
    assert len(skills) == 36
    assert len(set(skills)) == len(skills)  # no duplicates


# ---------------------------------------------------------------------------
# Batch B10c-mand3 Part 4: a base class (`class_type: "base"`) requires
# class_skills/skill_points/alignment/description_sections/
# weapon_and_armor_proficiency (a class.json `allOf`/`if`/`then`
# conditional); any other class_type does not.
# ---------------------------------------------------------------------------


def _minimal_class_fields(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "hit_die": "d6",
        "class_type": "base",
        "max_level": 3,
        "bab_progression": "poor",
        "save_progressions": {"fort": "poor", "ref": "poor", "will": "good"},
        "level_table": "table:example:table-1-1-the-testclass",
        "class_features": [{"name": "A Feature", "level": 1, "text_md": "You have a feature."}],
        "source_pages": {"start": 1, "end": 2},
    }
    fields.update(overrides)
    return fields


def test_base_class_requires_the_new_fields() -> None:
    schemas_dir = _repo_schemas_dir()
    registry = load_registry(schemas_dir)
    validator = Draft202012Validator(registry.load_type_schema("class"))

    errors = sorted(validator.iter_errors(_minimal_class_fields()), key=str)
    assert errors, "a base class missing class_skills/skill_points/alignment/... must fail"


def test_base_class_passes_with_the_new_fields_present() -> None:
    schemas_dir = _repo_schemas_dir()
    registry = load_registry(schemas_dir)
    validator = Draft202012Validator(registry.load_type_schema("class"))

    fields = _minimal_class_fields(
        class_skills=[{"skill": "Climb", "key_ability": "Str"}],
        skill_points={"base": 2, "ability": "Int"},
        alignment="Any",
        description_sections=[{"heading": "Adventures", "text_md": "..."}],
        weapon_and_armor_proficiency="Simple weapons only.",
    )
    errors = sorted(validator.iter_errors(fields), key=str)
    assert not errors, errors


def test_non_base_class_type_does_not_require_the_new_fields() -> None:
    """The `if`/`then` branch keys off `class_type == "base"`; a `class`
    record whose own `class_type` is "prestige" or "npc" is unaffected
    (this is about the shared `class.json` conditional, not the separate
    `prestige_class.json` schema, which B10c-mand3 leaves untouched at
    version 1 -- its own `class_type` is never "base" in practice)."""
    schemas_dir = _repo_schemas_dir()
    registry = load_registry(schemas_dir)
    validator = Draft202012Validator(registry.load_type_schema("class"))

    errors = sorted(validator.iter_errors(_minimal_class_fields(class_type="prestige")), key=str)
    assert not errors, errors


# ---------------------------------------------------------------------------
# Batch B12: sizes.json / creature_types.json, and the schema enums they must
# stay in sync with (a JSON Schema can't read an external list, so
# monster.json/npc.json spell the same values out inline).
# ---------------------------------------------------------------------------


def test_load_sizes_returns_the_nine_size_categories_smallest_first() -> None:
    sizes = load_sizes(_repo_schemas_dir())
    assert sizes == [
        "Fine",
        "Diminutive",
        "Tiny",
        "Small",
        "Medium",
        "Large",
        "Huge",
        "Gargantuan",
        "Colossal",
    ]


def test_load_creature_types_returns_the_15_types_and_the_subtypes() -> None:
    creature_types = load_creature_types(_repo_schemas_dir())
    assert len(creature_types.types) == 15
    assert len(set(creature_types.types)) == len(creature_types.types)
    assert "Magical Beast" in creature_types.types
    assert "Undead" in creature_types.types
    assert len(set(creature_types.subtypes)) == len(creature_types.subtypes)
    for subtype in ("Incorporeal", "Extraplanar", "Angel", "Reptilian", "Swarm"):
        assert subtype in creature_types.subtypes


def test_load_sizes_rejects_a_malformed_file(tmp_path: Path) -> None:
    (tmp_path / "sizes.json").write_text(json.dumps({"sizes": "Medium"}))
    with pytest.raises(SchemaError):
        load_sizes(tmp_path)


def test_load_creature_types_rejects_a_malformed_file(tmp_path: Path) -> None:
    (tmp_path / "creature_types.json").write_text(json.dumps({"types": [1, 2], "subtypes": []}))
    with pytest.raises(SchemaError):
        load_creature_types(tmp_path)


@pytest.mark.parametrize("type_name", ["monster", "npc"])
def test_monster_size_and_type_enums_match_the_committed_lists(type_name: str) -> None:
    schemas_dir = _repo_schemas_dir()
    registry = load_registry(schemas_dir)
    schema = registry.load_type_schema(type_name)

    assert schema["properties"]["size"]["enum"] == load_sizes(schemas_dir)
    assert schema["properties"]["type"]["enum"] == load_creature_types(schemas_dir).types


@pytest.mark.parametrize("field_name", ["cr", "hp"])
def test_monster_numeric_fields_are_marked_rangeable(field_name: str) -> None:
    """Acceptance criterion 1: the numeric fields the later range-filter PR
    keys off (`cr`, `hp`, and `hd.count`) carry `x-ui.rangeable`."""
    registry = load_registry(_repo_schemas_dir())
    schema = registry.load_type_schema("monster")
    assert schema["properties"][field_name]["x-ui"]["rangeable"] is True


def test_monster_hd_count_is_marked_rangeable() -> None:
    registry = load_registry(_repo_schemas_dir())
    schema = registry.load_type_schema("monster")
    hd_count = schema["properties"]["hd"]["properties"]["count"]
    assert hd_count["x-ui"]["rangeable"] is True


def test_npc_is_monster_plus_class_levels_and_possessions() -> None:
    registry = load_registry(_repo_schemas_dir())
    monster = registry.load_type_schema("monster")
    npc = registry.load_type_schema("npc")

    extra = set(npc["properties"]) - set(monster["properties"])
    assert extra == {"class_levels", "possessions"}
    assert set(monster["properties"]) - set(npc["properties"]) == set()
    assert "class_levels" in npc["required"]
