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
    assert registry.types["spell"].version == 1
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


def test_every_schema_has_a_top_level_integer_version() -> None:
    schemas_dir = _repo_schemas_dir()
    for path in sorted(schemas_dir.glob("*.json")):
        if path.name == "registry.json":
            continue
        schema = json.loads(path.read_text())
        assert isinstance(schema.get("version"), int)
