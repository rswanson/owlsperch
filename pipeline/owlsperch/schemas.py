"""Loading the JSON Schema type registry and schema files from `schemas/`
(spec 4.6, 4.14).

`schemas/` lives at the repo root, not inside `pipeline/`, so its location
relative to this module is found by walking up the directory tree from this
file until a directory containing `schemas/registry.json` turns up.
`$OWLSPERCH_SCHEMAS` overrides that search entirely.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class SchemaError(Exception):
    """Raised when the schema registry or a schema file is missing or
    malformed."""


def default_schemas_dir() -> Path:
    env = os.environ.get("OWLSPERCH_SCHEMAS")
    if env:
        return Path(env)

    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "schemas"
        if (candidate / "registry.json").is_file():
            return candidate

    raise SchemaError(
        f"could not find schemas/registry.json walking up from {here} "
        "(set $OWLSPERCH_SCHEMAS to override)"
    )


@dataclass(frozen=True)
class TypeInfo:
    """One `schemas/registry.json` entry: a record type's schema file and UI
    labels."""

    type_name: str
    schema_file: str
    label: str
    plural_label: str
    version: int


@dataclass
class Registry:
    schemas_dir: Path
    types: dict[str, TypeInfo]
    envelope_schema: dict[str, Any]

    def type_schema_path(self, type_name: str) -> Path:
        return self.schemas_dir / self.types[type_name].schema_file

    def load_type_schema(self, type_name: str) -> dict[str, Any]:
        raw: dict[str, Any] = json.loads(self.type_schema_path(type_name).read_text())
        return raw


def _load_json(path: Path, *, what: str) -> dict[str, Any]:
    if not path.is_file():
        raise SchemaError(f"{what} not found: {path}")
    try:
        raw: dict[str, Any] = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise SchemaError(f"{what} is not valid JSON: {path}: {exc}") from exc
    return raw


@dataclass(frozen=True)
class Category:
    """One `schemas/categories.json` entry (batch B10b, design decision D8):
    a player-facing rules-taxonomy bucket (`character-creation`, `combat`,
    ...) that `owlsperch.toc.categories` resolves table-of-contents entries
    into, and that `owlsperch_server.browse` reuses for the `category`
    facet's order and labels."""

    key: str
    label: str
    order: int
    description: str
    record_types: list[str]


def load_categories(schemas_dir: Path | None = None) -> list[Category]:
    """Load `categories.json` from `schemas_dir` (default:
    `default_schemas_dir()`), sorted by `order` (categories.json's own
    array order already matches `order`, but callers shouldn't rely on
    that)."""
    schemas_dir = schemas_dir if schemas_dir is not None else default_schemas_dir()
    raw = _load_json(schemas_dir / "categories.json", what="categories file")
    raw_categories = raw.get("categories", [])
    if not isinstance(raw_categories, list):
        raise SchemaError("categories.json 'categories' must be an array")

    categories: list[Category] = []
    for entry in raw_categories:
        try:
            categories.append(
                Category(
                    key=entry["key"],
                    label=entry["label"],
                    order=entry["order"],
                    description=entry.get("description", ""),
                    record_types=list(entry.get("record_types", [])),
                )
            )
        except (KeyError, TypeError) as exc:
            raise SchemaError(
                f"categories.json: an entry is missing a required field: {exc}"
            ) from exc

    return sorted(categories, key=lambda c: c.order)


def load_registry(schemas_dir: Path | None = None) -> Registry:
    """Load `registry.json` and `envelope.json` from `schemas_dir` (default:
    `default_schemas_dir()`)."""
    schemas_dir = schemas_dir if schemas_dir is not None else default_schemas_dir()

    raw_registry = _load_json(schemas_dir / "registry.json", what="registry file")
    types_raw = raw_registry.get("types", {})
    if not isinstance(types_raw, dict):
        raise SchemaError("registry.json 'types' must be an object")

    types: dict[str, TypeInfo] = {}
    for name, info in types_raw.items():
        try:
            types[name] = TypeInfo(
                type_name=name,
                schema_file=info["schema"],
                label=info["label"],
                plural_label=info["plural_label"],
                version=info["version"],
            )
        except (KeyError, TypeError) as exc:
            raise SchemaError(
                f"registry.json: type '{name}' is missing a required field: {exc}"
            ) from exc

    envelope_schema = _load_json(schemas_dir / "envelope.json", what="envelope schema")

    return Registry(schemas_dir=schemas_dir, types=types, envelope_schema=envelope_schema)


#: Column order for `render_schema_show`'s field table (header text; widths
#: are computed from actual content, see `render_schema_show`).
_TABLE_HEADERS: tuple[str, ...] = ("field", "label", "filterable", "sortable", "group", "order")

#: Padding added after the longest cell (header or value) in each column, so
#: columns stay visually separated instead of butting up against each other.
_COLUMN_PADDING = 2


def render_schema_show(type_name: str, registry: Registry) -> str:
    """Render the schema path and an `x-ui` field table for `type_name`, per
    B4 acceptance criterion 7 (`owlsperch schema show <type>`)."""
    if type_name not in registry.types:
        known = ", ".join(sorted(registry.types)) or "(none registered)"
        raise SchemaError(f"unknown type '{type_name}' (known types: {known})")

    info = registry.types[type_name]
    schema = registry.load_type_schema(type_name)
    path = registry.type_schema_path(type_name)

    rows: list[dict[str, str]] = []
    for field_name, prop in schema.get("properties", {}).items():
        x_ui = prop.get("x-ui", {})
        rows.append(
            {
                "field": field_name,
                "label": str(x_ui.get("label", "")),
                "filterable": str(x_ui.get("filterable", "")),
                "sortable": str(x_ui.get("sortable", "")),
                "group": str(x_ui.get("group", "")),
                "order": str(x_ui.get("order", "")),
            }
        )

    widths = {
        header: max(len(header), max((len(row[header]) for row in rows), default=0))
        + _COLUMN_PADDING
        for header in _TABLE_HEADERS
    }

    lines = [f"{path} (label: {info.label}, version {schema.get('version')})", ""]
    lines.append("".join(header.ljust(widths[header]) for header in _TABLE_HEADERS))
    for row in rows:
        lines.append("".join(row[header].ljust(widths[header]) for header in _TABLE_HEADERS))

    return "\n".join(lines)
