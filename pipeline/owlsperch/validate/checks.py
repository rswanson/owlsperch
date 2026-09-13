"""Type-specific and envelope-level consistency checks for `validate`, per
spec 4.5's "Validation" paragraph and B4 acceptance criterion 2. These run
after JSON Schema conformance (see `owlsperch.validate.runner`) and give more
specific, human-readable failure reasons than a bare schema error would.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from typing import Any


def slugify(name: str) -> str:
    """ASCII-fold `name` to kebab-case, per spec 4.6's `id` convention
    (`<type>:<book_id>:<slug>`, `slug` = ASCII-folded kebab-case of `name`).

    Before the ASCII fold, every character in Unicode category `Pd` (dash
    punctuation -- e.g. the en dash "–" or em dash "—", not just
    the ASCII hyphen-minus) is replaced with a plain "-", so a title like
    "Table 3–8: The Druid" slugifies to `table-3-8-the-druid` rather
    than losing the dash entirely during NFKD/ASCII folding and merging
    into `table-38-the-druid`.
    """
    dash_normalized = "".join("-" if unicodedata.category(ch) == "Pd" else ch for ch in name)
    folded = (
        unicodedata.normalize("NFKD", dash_normalized).encode("ascii", "ignore").decode("ascii")
    )
    folded = folded.lower()
    folded = folded.replace("'", "")
    folded = re.sub(r"[^a-z0-9]+", "-", folded)
    return folded.strip("-")


def expected_id(type_name: str, book_id: str, slug: str) -> str:
    return f"{type_name}:{book_id}:{slug}"


def check_envelope_consistency(
    record: dict[str, Any], *, type_dir: str, registry_version: int | None
) -> list[str]:
    """Envelope-level checks common to every type: the record's `type`
    matches the directory it was found in, `slug` matches the ASCII-folded
    kebab-case of `name`, `id` matches `<type>:<book_id>:<slug>`, and
    `schema_version` matches the type's current registry version.

    `registry_version` is `None` in `--stale` mode, where the caller checks
    staleness itself instead of failing on it here.
    """
    errors: list[str] = []

    record_type = record.get("type")
    if record_type != type_dir:
        errors.append(f"type directory '{type_dir}' does not match record type {record_type!r}")

    name = record.get("name")
    slug = record.get("slug")
    if isinstance(name, str) and isinstance(slug, str):
        expected_slug = slugify(name)
        if slug != expected_slug:
            errors.append(
                f"slug {slug!r} does not match slugified name (expected {expected_slug!r})"
            )

    book_id = record.get("book_id")
    record_id = record.get("id")
    if isinstance(slug, str) and isinstance(book_id, str) and isinstance(record_type, str):
        expected = expected_id(record_type, book_id, slug)
        if record_id != expected:
            errors.append(f"id {record_id!r} does not match expected {expected!r}")

    if registry_version is not None:
        schema_version = record.get("schema_version")
        if schema_version != registry_version:
            errors.append(
                f"schema_version {schema_version!r} does not match current "
                f"type version {registry_version!r}"
            )

    return errors


def check_spell_fields(record: dict[str, Any]) -> list[str]:
    """Spell-specific consistency checks from spec 4.5: at least one class
    level, and a non-empty school."""
    errors: list[str] = []
    fields = record.get("fields")
    if not isinstance(fields, dict):
        return ["fields is missing or not an object"]

    levels = fields.get("levels")
    if not isinstance(levels, list) or len(levels) == 0:
        errors.append("levels must be a non-empty list")

    school = fields.get("school")
    if not isinstance(school, str) or not school.strip():
        errors.append("school must be a non-empty string")

    return errors


def check_feat_fields(record: dict[str, Any]) -> list[str]:
    """Feat-specific consistency check from spec 4.5: a feat has a benefit."""
    fields = record.get("fields")
    if not isinstance(fields, dict):
        return ["fields is missing or not an object"]

    benefit = fields.get("benefit")
    if not isinstance(benefit, str) or not benefit.strip():
        return ["benefit must be a non-empty string"]
    return []


def check_rules_section_fields(record: dict[str, Any]) -> list[str]:
    """rules_section-specific consistency check from spec 4.5: a
    rules_section has a topic."""
    fields = record.get("fields")
    if not isinstance(fields, dict):
        return ["fields is missing or not an object"]

    topic = fields.get("topic")
    if not isinstance(topic, str) or not topic.strip():
        return ["topic must be a non-empty string"]
    return []


def check_table_fields(record: dict[str, Any]) -> list[str]:
    """Table-specific consistency check from spec 4.5: a table has equal-
    length rows -- `columns` is a non-empty list, and every entry of `rows`
    is a list whose length equals `len(columns)`."""
    errors: list[str] = []
    fields = record.get("fields")
    if not isinstance(fields, dict):
        return ["fields is missing or not an object"]

    columns = fields.get("columns")
    if not isinstance(columns, list) or len(columns) == 0:
        errors.append("columns must be a non-empty list")
        return errors

    rows = fields.get("rows")
    if not isinstance(rows, list):
        errors.append("rows must be a list")
        return errors

    expected = len(columns)
    for i, row in enumerate(rows):
        if not isinstance(row, list) or len(row) != expected:
            actual = len(row) if isinstance(row, list) else "not a list"
            errors.append(
                f"row {i} has {actual} cell(s), expected {expected} (columns has {expected})"
            )

    return errors


def check_pages_within_segment(record: dict[str, Any], segment: dict[str, Any] | None) -> list[str]:
    """Every page a record cites must be within its originating segment's
    page span (spec 4.5). A missing segment is its own failure -- there's
    nothing to check the pages against."""
    if segment is None:
        return ["originating segment not found"]

    segment_pages = set(segment.get("pages", []))
    record_pages = record.get("pages", [])
    if not isinstance(record_pages, list):
        return ["pages must be a list"]

    out_of_span = [p for p in record_pages if p not in segment_pages]
    if out_of_span:
        return [
            f"pages {out_of_span} outside segment "
            f"{segment.get('seg_id')!r} page span {sorted(segment_pages)}"
        ]
    return []


#: Type-specific field-consistency checks, keyed by type name. Extending
#: `validate` to a new type (spec 4.14) is: add a schema, register it, and
#: (optionally) add an entry here for checks a JSON Schema can't express.
TYPE_FIELD_CHECKS: dict[str, Callable[[dict[str, Any]], list[str]]] = {
    "spell": check_spell_fields,
    "feat": check_feat_fields,
    "rules_section": check_rules_section_fields,
    "table": check_table_fields,
}
