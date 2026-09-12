"""Discovering record/segment files and compiling JSON Schema validators for
`owlsperch validate` (loaded once per run, per B4's acceptance criterion 2)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from owlsperch.schemas import Registry, load_registry


class LoadError(Exception):
    """A record or segment file exists but could not even be parsed as
    JSON."""


def records_dir(data_dir: Path) -> Path:
    return data_dir / "records"


def segments_dir(data_dir: Path) -> Path:
    return data_dir / "segments"


def discover_books_with_records(data_dir: Path) -> list[str]:
    """Every book_id with a `records/<book_id>/` directory, for `validate
    all`."""
    base = records_dir(data_dir)
    if not base.is_dir():
        return []
    return sorted(p.name for p in base.iterdir() if p.is_dir())


def discover_record_files(data_dir: Path, book_id: str) -> list[Path]:
    """Every `records/<book_id>/<type>/*.json` file, sorted for stable
    output."""
    book_dir = records_dir(data_dir) / book_id
    if not book_dir.is_dir():
        return []
    return sorted(book_dir.glob("*/*.json"))


def load_json(path: Path) -> dict[str, Any]:
    try:
        raw: dict[str, Any] = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise LoadError(f"invalid JSON: {exc}") from exc
    return raw


def segment_path(data_dir: Path, book_id: str, segment_id: str) -> Path:
    return segments_dir(data_dir) / book_id / f"{segment_id}.json"


def load_segment(data_dir: Path, book_id: str, segment_id: str) -> dict[str, Any] | None:
    path = segment_path(data_dir, book_id, segment_id)
    if not path.is_file():
        return None
    return load_json(path)


class CompiledSchemas:
    """The type registry plus compiled envelope/type-schema validators,
    built once per `validate` run and reused across every record."""

    def __init__(self, registry: Registry) -> None:
        self.registry = registry
        self._envelope_validator = Draft202012Validator(registry.envelope_schema)
        self._type_validators: dict[str, Draft202012Validator] = {}

    @classmethod
    def load(cls, schemas_dir: Path | None = None) -> CompiledSchemas:
        return cls(load_registry(schemas_dir))

    def envelope_validator(self) -> Draft202012Validator:
        return self._envelope_validator

    def type_validator(self, type_name: str) -> Draft202012Validator | None:
        if type_name not in self.registry.types:
            return None
        if type_name not in self._type_validators:
            schema = self.registry.load_type_schema(type_name)
            self._type_validators[type_name] = Draft202012Validator(schema)
        return self._type_validators[type_name]
