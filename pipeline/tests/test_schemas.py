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

from owlsperch.schemas import SchemaError, default_schemas_dir, load_registry

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
#: carry no top-level `version`: `registry.json` (the type index) and
#: `categories.json` (batch B10b's plain rules-taxonomy data file, D8).
_NON_SCHEMA_FILES = {"registry.json", "categories.json"}


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
