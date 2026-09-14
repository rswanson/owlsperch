"""Type-specific and envelope-level consistency checks for `validate`, per
spec 4.5's "Validation" paragraph and B4 acceptance criterion 2. These run
after JSON Schema conformance (see `owlsperch.validate.runner`) and give more
specific, human-readable failure reasons than a bare schema error would.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
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


# ---------------------------------------------------------------------------
# Batch B10c, design decision D9: class/prestige_class checks need
# cross-record lookups (the owned level table; the book's own spell records)
# that a plain `dict[str, Any] -> list[str]` check can't do on its own.
# `ValidationContext` carries those lookups in as callables so this module
# stays free of any *new* filesystem coupling of its own -- the disk-backed
# instance is built by `owlsperch.validate.runner` (which already has
# `data_dir`) and `owlsperch.build_db.runner` (which builds one the same
# way for its own read-only pass).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ValidationContext:
    """Cross-record lookups a single-record check can't do alone (D9).
    `record_by_id`: `"<type>:<book_id>:<slug>"` -> that record's dict, or
    `None` if it doesn't resolve. `spell_list_classes`: `book_id` -> every
    distinct `levels[].class` value across that book's own spell records
    (an empty set when the book has no spell records yet, in which case the
    spellcasting `spell_list` check below is skipped rather than failed)."""

    record_by_id: Callable[[str], dict[str, Any] | None]
    spell_list_classes: Callable[[str], set[str]]


#: A context with no real lookups -- every class-record check that needs
#: `record_by_id`/`spell_list_classes` degrades to "can't resolve"/"no
#: constraint" rather than crashing. Used by any caller (mostly tests) that
#: doesn't have a `data_dir` to build a real context from.
NULL_CONTEXT = ValidationContext(
    record_by_id=lambda _record_id: None,
    spell_list_classes=lambda _book_id: set(),
)

_VALID_HIT_DICE = {"d4", "d6", "d8", "d10", "d12"}
_VALID_BAB_PROGRESSIONS = {"good", "average", "poor"}
_VALID_SAVE_PROGRESSIONS = {"good", "poor"}

#: Every Unicode dash-punctuation character (category `Pd`) and the Unicode
#: minus sign, folded to a plain ASCII hyphen -- reused from `slugify`'s own
#: dash handling so a cell like "+6 /+1" or "+12/ +7/ +2" compares equal to
#: "+6/+1"/"+12/+7/+2" regardless of stray spaces or an exotic dash glyph.
_UNICODE_MINUS = "−"


def _normalize_cell(text: str) -> str:
    """Strip, remove ALL internal whitespace, and fold every dash-like
    character to a plain `-` -- the shared comparison form for a level
    table cell (design decision D9)."""
    folded = "".join(
        "-" if unicodedata.category(ch) == "Pd" or ch == _UNICODE_MINUS else ch for ch in text
    )
    return re.sub(r"\s+", "", folded.strip())


_LEVEL_CELL_RE = re.compile(r"^(\d+)\s*(?:st|nd|rd|th)?$", re.IGNORECASE)


def _parse_level_cell(cell: str) -> int | None:
    """Parse a Level-column cell ("1st", "2nd", "20th", or a bare "1") to
    its integer value, or `None` if it doesn't look like one at all."""
    match = _LEVEL_CELL_RE.match(cell.strip())
    return int(match.group(1)) if match else None


def _find_column(columns: list[Any], keyword: str) -> int | None:
    """Index of the first column whose header contains `keyword`
    case-insensitively, or `None` if no column matches."""
    needle = keyword.lower()
    for i, column in enumerate(columns):
        if isinstance(column, str) and needle in column.lower():
            return i
    return None


def _bab_progression_cell(progression: str, level: int) -> str:
    """The expected, normalized base-attack-bonus cell for `level` under
    `progression` (design decision D9's verified formula): `good` -> level,
    `average` -> `level * 3 // 4`, `poor` -> `level // 2`; then one
    iterative attack every 5 points while it stays positive (at most 4
    total), `"+0"` when the base itself is 0."""
    if progression == "good":
        base = level
    elif progression == "average":
        base = level * 3 // 4
    else:
        base = level // 2
    iteratives = [base - 5 * i for i in range(4) if base - 5 * i > 0]
    if not iteratives:
        return "+0"
    return "/".join(f"+{value}" for value in iteratives)


def _save_progression_cell(progression: str, level: int) -> str:
    """The expected, normalized save cell for `level` under `progression`:
    `good` -> `2 + level // 2`, `poor` -> `level // 3`."""
    value = 2 + level // 2 if progression == "good" else level // 3
    return f"+{value}"


_PAREN_RE = re.compile(r"\([^)]*\)")


def _normalize_special_token(token: str) -> str:
    """Lowercase, drop every parenthetical group (`(Ex)`, `(Su)`, ...), and
    collapse whitespace -- the shared comparison form for both a Special
    cell's own comma-separated tokens and a `class_features[].name`."""
    stripped = _PAREN_RE.sub("", token)
    return re.sub(r"\s+", " ", stripped).strip().lower()


def _split_special_cell(cell: str) -> list[str]:
    """A Special-column cell's own comma-separated tokens, with empty
    entries and a bare em/en-dash/hyphen placeholder ("no special ability
    this level") dropped."""
    tokens = [t.strip() for t in cell.split(",")]
    return [t for t in tokens if t and t not in ("—", "–", "-", "--")]


_SKILLS_CACHE: list[str] | None = None


def _skill_names() -> set[str]:
    """The committed `schemas/skills.json` list, case-folded, cached after
    the first call. A late, module-level import of `owlsperch.schemas`
    avoids a real import cycle (`schemas.py` doesn't import this module),
    but is deferred anyway since every OTHER function in this file is pure
    over its arguments -- this is the one exception, reading a static,
    repo-committed game-term list (like `owlsperch.queue.abbrev
    .CLASS_ABBREVIATIONS`), not user data."""
    global _SKILLS_CACHE
    if _SKILLS_CACHE is None:
        from owlsperch.schemas import load_skills

        _SKILLS_CACHE = [s.casefold() for s in load_skills()]
    return set(_SKILLS_CACHE)


def _strip_trailing_parens(skill_name: str) -> str:
    """Strip every trailing parenthetical group, iteratively (a skill can
    carry more than one, e.g. a sub-skill AND a restated key ability)."""
    stripped = skill_name.strip()
    previous = None
    while previous != stripped:
        previous = stripped
        stripped = re.sub(r"\s*\([^)]*\)\s*$", "", stripped).strip()
    return stripped


def check_class_fields(record: dict[str, Any], context: ValidationContext) -> list[str]:
    """The class/prestige_class consistency checks from design decision D9:
    a valid `hit_die`, a `level_table` that resolves to a real `table`
    record cross-linked both ways, that table's row count/Level column/
    base-attack-bonus/save columns matching the class's own progressions,
    every Special-column entry accounted for in `class_features` (and vice
    versa), every `class_skills` entry a recognized 3.5e skill, and (when
    the book has any spell records yet) a `spellcasting.spell_list` that
    matches an actual spell class name. Registered for both `class` and
    `prestige_class` in `TYPE_CONTEXT_CHECKS` -- the two schemas share every
    field this function inspects."""
    errors: list[str] = []
    fields = record.get("fields")
    if not isinstance(fields, dict):
        return ["fields is missing or not an object"]

    name = record.get("name") if isinstance(record.get("name"), str) else "<unnamed>"

    hit_die = fields.get("hit_die")
    if hit_die not in _VALID_HIT_DICE:
        errors.append(f"{name}: hit_die {hit_die!r} is not one of {sorted(_VALID_HIT_DICE)}")

    bab_progression = fields.get("bab_progression")
    if bab_progression not in _VALID_BAB_PROGRESSIONS:
        errors.append(
            f"{name}: bab_progression {bab_progression!r} is not one of "
            f"{sorted(_VALID_BAB_PROGRESSIONS)}"
        )

    save_progressions = fields.get("save_progressions")
    parsed_saves: dict[str, str] = {}
    if not isinstance(save_progressions, dict):
        errors.append(f"{name}: save_progressions is missing or not an object")
    else:
        for key in ("fort", "ref", "will"):
            value = save_progressions.get(key)
            if value not in _VALID_SAVE_PROGRESSIONS:
                errors.append(
                    f"{name}: save_progressions.{key} {value!r} is not one of "
                    f"{sorted(_VALID_SAVE_PROGRESSIONS)}"
                )
            else:
                parsed_saves[key] = value

    max_level = fields.get("max_level")
    if not isinstance(max_level, int):
        errors.append(f"{name}: max_level is missing or not an integer")

    class_skills = fields.get("class_skills")
    if isinstance(class_skills, list):
        known_skills = _skill_names()
        for item in class_skills:
            if not isinstance(item, dict):
                continue
            skill_name = item.get("skill")
            if not isinstance(skill_name, str):
                continue
            stripped = _strip_trailing_parens(skill_name)
            if stripped.casefold() not in known_skills:
                errors.append(
                    f"{name}: class_skills entry {skill_name!r} is not a recognized skill "
                    f"(got {stripped!r})"
                )

    spellcasting = fields.get("spellcasting")
    if isinstance(spellcasting, dict):
        spell_list = spellcasting.get("spell_list")
        if isinstance(spell_list, str):
            book_id_raw = record.get("book_id")
            book_id = book_id_raw if isinstance(book_id_raw, str) else ""
            known_classes = context.spell_list_classes(book_id)
            if known_classes and spell_list not in known_classes:
                errors.append(
                    f"{name}: spellcasting.spell_list {spell_list!r} does not match any "
                    "spell's levels[].class for this book"
                )

    level_table_id = fields.get("level_table")
    if not isinstance(level_table_id, str):
        errors.append(f"{name}: level_table is missing or not a string")
        return errors

    table_record = context.record_by_id(level_table_id)
    if table_record is None or table_record.get("type") != "table":
        errors.append(f"{name}: level_table {level_table_id!r} not found")
        return errors

    tables_list = record.get("tables")
    if not isinstance(tables_list, list) or level_table_id not in tables_list:
        errors.append(
            f"{name}: level_table {level_table_id!r} is not listed in this record's own "
            "tables array"
        )

    table_fields = table_record.get("fields")
    if not isinstance(table_fields, dict):
        errors.append(f"{name}: level_table {level_table_id!r} has no fields")
        return errors

    columns = table_fields.get("columns")
    rows = table_fields.get("rows")
    if not isinstance(columns, list) or not isinstance(rows, list):
        errors.append(f"{name}: level_table {level_table_id!r} has malformed columns/rows")
        return errors

    level_idx = _find_column(columns, "level")
    bab_idx = _find_column(columns, "attack")
    fort_idx = _find_column(columns, "fort")
    ref_idx = _find_column(columns, "ref")
    will_idx = _find_column(columns, "will")
    special_idx = _find_column(columns, "special")

    for label, idx in (
        ("Level", level_idx),
        ("Base Attack Bonus", bab_idx),
        ("Fort Save", fort_idx),
        ("Ref Save", ref_idx),
        ("Will Save", will_idx),
    ):
        if idx is None:
            errors.append(f"{name}: level_table {level_table_id!r} is missing a {label} column")

    if not isinstance(max_level, int) or level_idx is None:
        return errors

    def _cell(row: Any, idx: int) -> str | None:
        if not isinstance(row, list) or idx >= len(row):
            return None
        return str(row[idx])

    parsed_levels: list[int] = []
    for row in rows:
        cell = _cell(row, level_idx)
        parsed = _parse_level_cell(cell) if cell is not None else None
        if parsed is not None:
            parsed_levels.append(parsed)

    if len(rows) != max_level:
        errors.append(
            f"{name}: level_table {level_table_id!r} has {len(rows)} row(s), "
            f"expected max_level {max_level}"
        )

    expected_levels = list(range(1, max_level + 1))
    if parsed_levels != expected_levels:
        errors.append(
            f"{name}: level_table {level_table_id!r} Level column is {parsed_levels}, "
            f"expected {expected_levels}"
        )
    parsed_level_set = set(parsed_levels)

    if bab_progression in _VALID_BAB_PROGRESSIONS and bab_idx is not None:
        for row in rows:
            level_cell = _cell(row, level_idx)
            bab_cell = _cell(row, bab_idx)
            level = _parse_level_cell(level_cell) if level_cell is not None else None
            if level is None or bab_cell is None:
                continue
            expected = _bab_progression_cell(bab_progression, level)
            actual = _normalize_cell(bab_cell)
            if actual != expected:
                errors.append(
                    f"{name}: level {level} base attack bonus {bab_cell!r} does not match "
                    f"{bab_progression} progression (expected {expected!r})"
                )

    for save_key, idx in (("fort", fort_idx), ("ref", ref_idx), ("will", will_idx)):
        progression = parsed_saves.get(save_key)
        if progression is None or idx is None:
            continue
        for row in rows:
            level_cell = _cell(row, level_idx)
            save_cell = _cell(row, idx)
            level = _parse_level_cell(level_cell) if level_cell is not None else None
            if level is None or save_cell is None:
                continue
            expected = _save_progression_cell(progression, level)
            actual = _normalize_cell(save_cell)
            if actual != expected:
                errors.append(
                    f"{name}: level {level} {save_key} save {save_cell!r} does not match "
                    f"{progression} progression (expected {expected!r})"
                )

    class_features = fields.get("class_features")
    feature_entries: list[tuple[str, Any]] = []
    if isinstance(class_features, list):
        feature_entries = [
            (f["name"], f.get("level"))
            for f in class_features
            if isinstance(f, dict) and isinstance(f.get("name"), str)
        ]

    if special_idx is not None:
        for row in rows:
            level_cell = _cell(row, level_idx)
            special_cell = _cell(row, special_idx)
            level = _parse_level_cell(level_cell) if level_cell is not None else None
            if level is None or special_cell is None:
                continue
            for token in _split_special_cell(special_cell):
                normalized_token = _normalize_special_token(token)
                matched = any(
                    normalized_token.startswith(_normalize_special_token(feature_name))
                    for feature_name, _feature_level in feature_entries
                )
                if not matched:
                    errors.append(
                        f"{name}: level {level} Special entry {token!r} has no matching "
                        "class_features entry"
                    )

    for feature_name, feature_level in feature_entries:
        if isinstance(feature_level, int) and feature_level not in parsed_level_set:
            errors.append(
                f"{name}: class_features {feature_name!r} level {feature_level} is not in "
                "the level table"
            )

    return errors


#: Context-dependent checks (D9), keyed by type name -- both class types
#: share `check_class_fields` since their schemas share every field it
#: inspects.
TYPE_CONTEXT_CHECKS: dict[str, Callable[[dict[str, Any], ValidationContext], list[str]]] = {
    "class": check_class_fields,
    "prestige_class": check_class_fields,
}
