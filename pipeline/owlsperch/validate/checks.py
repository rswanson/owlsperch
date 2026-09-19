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


#: Characters that, alone or repeated, stand in for "no printed value" in a
#: table cell: a plain hyphen, an en/em dash, or the Unicode minus sign
#: (B10c-mand4 criterion 7). A cell classifies as this "dash" style only
#: when its stripped text consists ENTIRELY of these characters (so "—"
#: and "--" both count, but "2/—" -- a real value using a dash as part of
#: it, not as the whole cell -- does not).
_DASH_PLACEHOLDER_CHARS = frozenset("—–-−")


def check_table_fields(record: dict[str, Any]) -> list[str]:
    """Table-specific consistency check from spec 4.5: a table has equal-
    length rows -- `columns` is a non-empty list, and every entry of `rows`
    is a list whose length equals `len(columns)`. B10c-mand4 criterion 7
    adds a per-column empty-cell-consistency check: within one column,
    cells with no printed value must all be written the SAME way -- either
    every one blank (`""`) or every one a dash placeholder (`"—"`, `"–"`,
    `"-"`, `"--"`, or the Unicode minus `"−"`), never a mix of both in the
    same column (the real-corpus catch this guards against: PHB table
    3-15's rogue Special column writes the same "no special ability"
    entry as both `"—"` and `""` within one column)."""
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
    valid_rows: list[tuple[int, list[Any]]] = []
    for i, row in enumerate(rows):
        if not isinstance(row, list) or len(row) != expected:
            actual = len(row) if isinstance(row, list) else "not a list"
            errors.append(
                f"row {i} has {actual} cell(s), expected {expected} (columns has {expected})"
            )
        else:
            valid_rows.append((i, row))

    for col_idx in range(expected):
        blank_rows: list[int] = []
        dash_rows: list[int] = []
        for row_idx, row in valid_rows:
            cell = row[col_idx]
            if not isinstance(cell, str):
                continue
            stripped = cell.strip()
            if stripped == "":
                blank_rows.append(row_idx)
            elif all(ch in _DASH_PLACEHOLDER_CHARS for ch in stripped):
                dash_rows.append(row_idx)
        if blank_rows and dash_rows:
            header = columns[col_idx] if isinstance(columns[col_idx], str) else columns[col_idx]
            errors.append(
                f"column {col_idx} ({header!r}) mixes empty-cell styles: blank cells at "
                f"row(s) {blank_rows[:5]}, dash cells at row(s) {dash_rows[:5]}"
            )

    return errors


def _check_override_entry_fields(record: dict[str, Any]) -> list[str]:
    """Shared errata_entry/update_entry consistency checks (batch B11,
    design decision D4): `target_book`/`target_name`/`replacement_text` are
    non-empty strings, `target_page` (if present) is a positive integer,
    and `name` is exactly `"<target_name> (p. <target_page>)"` when
    `target_page` is present, else plain `"<target_name>"` -- this is what
    lets two entries in the same book that happen to share a bare target
    name (e.g. two unrelated "Overrun" corrections) get distinct slugs/ids
    instead of one silently overwriting the other."""
    errors: list[str] = []
    fields = record.get("fields")
    if not isinstance(fields, dict):
        return ["fields is missing or not an object"]

    target_book = fields.get("target_book")
    if not isinstance(target_book, str) or not target_book.strip():
        errors.append("target_book must be a non-empty string")

    target_name = fields.get("target_name")
    if not isinstance(target_name, str) or not target_name.strip():
        errors.append("target_name must be a non-empty string")

    replacement_text = fields.get("replacement_text")
    if not isinstance(replacement_text, str) or not replacement_text.strip():
        errors.append("replacement_text must be a non-empty string")

    target_page = fields.get("target_page")
    has_valid_page = (
        target_page is not None
        and not isinstance(target_page, bool)
        and isinstance(target_page, int)
        and target_page >= 1
    )
    if target_page is not None and not has_valid_page:
        errors.append("target_page must be a positive integer when present")

    if isinstance(target_name, str) and target_name.strip():
        expected_name = f"{target_name} (p. {target_page})" if has_valid_page else target_name
        name = record.get("name")
        if name != expected_name:
            errors.append(f"name {name!r} does not match expected {expected_name!r}")

    return errors


def check_errata_entry_fields(record: dict[str, Any]) -> list[str]:
    """errata_entry-specific consistency checks (batch B11, design decision
    D4). See `_check_override_entry_fields`."""
    return _check_override_entry_fields(record)


def check_update_entry_fields(record: dict[str, Any]) -> list[str]:
    """update_entry-specific consistency checks (batch B11, design decision
    D4). See `_check_override_entry_fields`."""
    return _check_override_entry_fields(record)


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
    "errata_entry": check_errata_entry_fields,
    "update_entry": check_update_entry_fields,
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


#: A level_table column header naming a caster's spells-per-day/known/
#: points progression (B10c-mand3 Part 3b) -- matched against `_KIND_RULES
#: ["class"]`'s prompt convention of naming such columns
#: "Spells per Day <slot>" (or a single "Spells per Day"); see
#: `check_class_fields`.
_SPELL_COLUMN_RE = re.compile(r"per day|known|points", re.IGNORECASE)

#: The slot ordinal at the end of a "Spells per Day <slot>" column header:
#: "0" or "1st".."9th" -- used by `_spell_slot_levels` (B10c-mand10).
_SPELL_SLOT_RE = re.compile(r"per day\s+(\d+)(?:st|nd|rd|th)?\s*$", re.IGNORECASE)

#: A base caster whose per-day progression reaches this spell level (or
#: higher) is a full caster with cantrips/orisons in 3.5e, so its level
#: table MUST also carry a level-0 spells-per-day column. Paladins and
#: rangers top out at 4th and have no 0-level column; bards (6th),
#: clerics, druids, sorcerers and wizards (9th) all do.
_SPELL_LEVEL_REQUIRING_ZERO_COLUMN = 5


def _spell_slot_levels(columns: list[Any]) -> set[int]:
    """The spell levels named by a level table's "Spells per Day <slot>"
    columns -- {0, 1, 2, ...} -- ignoring any other column."""
    levels: set[int] = set()
    for column in columns:
        if isinstance(column, str):
            match = _SPELL_SLOT_RE.search(column)
            if match:
                levels.add(int(match.group(1)))
    return levels


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

#: A trailing numeric bonus on a Special-cell token, e.g. "+1", "+1d6" --
#: stripped by `_normalize_special_token` so "Sneak Attack +1d6" compares
#: down to the same form as its `class_features[].name` ("Sneak Attack").
_TRAILING_BONUS_RE = re.compile(r"\+\d+(?:d\d+)?\s*$", re.IGNORECASE)

#: A leading ordinal on a Special-cell token, e.g. "2nd " in "2nd favored
#: enemy" -- a repeated progression's printed rank, not part of the
#: feature's own name. Stripped by `_normalize_special_token` (B10c-mand4
#: criterion 2) so "1st favored enemy"/"2nd favored enemy"/... all
#: normalize down to the same "favored enemy" a single class_features
#: entry can match once.
_LEADING_ORDINAL_RE = re.compile(r"^\d+(?:st|nd|rd|th)\s+", re.IGNORECASE)

#: A trailing use-frequency on a Special-cell token, e.g. "2/day", "1/week",
#: "3/encounter" -- stripped (B10c-mand4 criterion 2) so "Rage 1/day"
#: normalizes down to "rage".
_TRAILING_FREQUENCY_RE = re.compile(
    r"\d+/(?:day|week|month|year|encounter|round|hour|rest)\s*$", re.IGNORECASE
)

#: A trailing distance on a Special-cell token, e.g. "30 ft.", "30 ft",
#: "30 feet" -- stripped (B10c-mand4 criterion 2) so "Slow fall 30 ft."
#: normalizes down to "slow fall".
_TRAILING_DISTANCE_RE = re.compile(r"\d+\s*(?:ft\.?|feet)\.?\s*$", re.IGNORECASE)


def _fold_trailing_plural(word: str) -> str:
    """Singularize `word` for matching purposes: "ies" -> "y" ("abilities"
    -> "ability"), a sibilant-stem "es" is dropped ("classes" -> "class",
    "boxes" -> "box"), otherwise a trailing plural "s" is stripped except
    when the word ends in "ss", "us", or "is" (so "bonus", "class", "this"
    survive unfolded). Used by `_normalize_special_token` (B10c-mand3 Part
    3a) to make Special-cell/`class_features[].name` matching
    plural-insensitive: the fighter's printed feature heading is "Bonus
    Feats:" but its own Special cell reads "Bonus feat"; the rogue's is
    "Special Abilities:" against a Special cell reading "Special ability"
    (B10c-mand8 -- the naive "strip one s" fold left "abilitie" vs
    "ability" unmatched, which is what kept the rogue in `human/`)."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("es") and word[:-2].endswith(("ss", "x", "z", "ch", "sh")):
        return word[:-2]
    # Known gap: an "-oes" plural ("heroes") falls through to the plain
    # strip below and yields "heroe" -- harmless for matching, since both
    # sides go through the same fold, and no 3.5e class feature is named
    # that way.
    if word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


#: Words that mark a TIERED class feature sharing its stem with a lesser
#: one -- "Greater Rage"/"Rage", "Improved Evasion"/"Evasion", "Improved
#: Uncanny Dodge"/"Uncanny Dodge", "Mighty Rage", "Tireless Rage". Each of
#: these is its own printed Class Features heading, so a Special token
#: carrying one of them must match a `class_features` entry of its own;
#: `_special_token_matches_feature` refuses to let it fall through to the
#: base feature's entry (B10c-mand8 review finding).
_TIER_MODIFIER_WORDS = frozenset(
    {"greater", "improved", "lesser", "mighty", "tireless", "superior", "mass", "advanced"}
)


def _special_token_matches_feature(normalized_token: str, normalized_feature_name: str) -> bool:
    """Whether a level table's Special-column token (already through
    `_normalize_special_token`) is described by a `class_features[].name`
    (likewise normalized). True when the feature name appears in the token
    as a whole-word sequence -- so "summon familiar" (the sorcerer's/
    wizard's Special cell) matches the feature the printed Class Features
    heading names simply "Familiar" -- UNLESS one of the token's leftover
    words is a tier modifier (`_TIER_MODIFIER_WORDS`): "greater rage" must
    NOT be satisfied by a "Rage" entry, since "Greater Rage" is its own
    printed heading and a record missing it would otherwise validate.
    Word-boundary containment, not substring, so "feat" never matches
    inside "defeat"; and only THIS direction -- a feature name longer than
    the token ("inspire courage" for a truncated cell "courage") never
    matches, so a truncated cell still errors. An empty name on either side
    never matches. (B10c-mand8: replaces the earlier one-way `startswith`
    prefix test, which required the feature name to be a PREFIX of the
    token even though the prompt correctly names features from the printed
    headings -- "Familiar", not "Summon Familiar".)"""
    if not normalized_token or not normalized_feature_name:
        return False
    if f" {normalized_feature_name} " not in f" {normalized_token} ":
        return False
    token_words = normalized_token.split(" ")
    feature_words = normalized_feature_name.split(" ")
    # Leftover words = the token's words with ONE occurrence of the feature's
    # word sequence removed (it is present as a whole-word run, checked above).
    for i in range(len(token_words) - len(feature_words) + 1):
        if token_words[i : i + len(feature_words)] == feature_words:
            leftover = token_words[:i] + token_words[i + len(feature_words) :]
            break
    else:  # pragma: no cover -- unreachable given the containment check above
        return False
    return not any(word in _TIER_MODIFIER_WORDS for word in leftover)


def _normalize_special_token(token: str) -> str:
    """Lowercase, drop every parenthetical group (`(Ex)`, `(Su)`, ...),
    strip a leading ordinal (`2nd `), a trailing use-frequency (`2/day`), a
    trailing distance (`30 ft.`), a trailing numeric bonus (`+N`, `+NdN`),
    fold a trailing plural "s" per word (`_fold_trailing_plural`), and
    collapse whitespace -- the shared comparison form for both a Special
    cell's own comma-separated tokens and a `class_features[].name`
    (B10c-mand3 Part 3a; ordinal/distance/frequency stripping added by
    B10c-mand4 criterion 2 so a repeated progression -- "1st favored
    enemy"/"2nd favored enemy"/..., "Inspire courage +2"/"+3"/"+4" -- all
    collapse to the one name a single class_features entry describes)."""
    stripped = _PAREN_RE.sub("", token)
    stripped = _LEADING_ORDINAL_RE.sub("", stripped)
    stripped = _TRAILING_FREQUENCY_RE.sub("", stripped)
    stripped = _TRAILING_DISTANCE_RE.sub("", stripped)
    stripped = _TRAILING_BONUS_RE.sub("", stripped)
    collapsed = re.sub(r"\s+", " ", stripped).strip().lower()
    if not collapsed:
        return collapsed
    return " ".join(_fold_trailing_plural(word) for word in collapsed.split(" "))


def _split_special_cell(cell: str) -> list[str]:
    """A Special-column cell's own comma-separated tokens, with empty
    entries and a bare em/en-dash/hyphen placeholder ("no special ability
    this level") dropped. Splits only on commas OUTSIDE parentheses
    (B10c-mand4 criterion 1), so a token like "Wild shape (Huge elemental,
    2/day)" survives as one token instead of being torn in two at the
    comma inside its own parenthetical. A stray, unbalanced ')' clamps
    depth at 0 rather than going negative, so it can't swallow the rest of
    the cell as "inside parentheses"."""
    tokens: list[str] = []
    current: list[str] = []
    depth = 0
    for ch in cell:
        if ch == "(":
            depth += 1
            current.append(ch)
        elif ch == ")":
            depth = max(0, depth - 1)
            current.append(ch)
        elif ch == "," and depth == 0:
            tokens.append("".join(current))
            current = []
        else:
            current.append(ch)
    tokens.append("".join(current))
    stripped_tokens = [t.strip() for t in tokens]
    return [t for t in stripped_tokens if t and t not in ("—", "–", "-", "--")]


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

    if isinstance(spellcasting, dict) and not any(
        isinstance(c, str) and _SPELL_COLUMN_RE.search(c) for c in columns
    ):
        errors.append(
            f"{name}: spellcasting is set but level_table {level_table_id!r} has no "
            f"spells-per-day/known column (columns: {columns!r})"
        )

    # B10c-mand10: a base caster whose per-day progression reaches 5th-level
    # spells has 0-level spells too, so its table must carry the "Spells per
    # Day 0" column -- the real-corpus miss this catches is the cleric
    # (Table 3-6) whose orisons column the text layer dropped entirely, which
    # no other check noticed since the 1st..9th columns were all present.
    if isinstance(spellcasting, dict) and fields.get("class_type") == "base":
        slot_levels = _spell_slot_levels(columns)
        if (
            slot_levels
            and max(slot_levels) >= _SPELL_LEVEL_REQUIRING_ZERO_COLUMN
            and 0 not in slot_levels
        ):
            errors.append(
                f"{name}: level_table {level_table_id!r} reaches {max(slot_levels)}th-level "
                f"spells per day but has no 'Spells per Day 0' column (columns: {columns!r})"
            )

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
    feature_entries: list[tuple[str, Any, dict[str, Any]]] = []
    if isinstance(class_features, list):
        feature_entries = [
            (f["name"], f.get("level"), f)
            for f in class_features
            if isinstance(f, dict) and isinstance(f.get("name"), str)
        ]

    # B10c-mand4 criterion 3: match once per DISTINCT normalized Special
    # token across the whole table, not once per row -- a repeated
    # progression ("1st favored enemy", "2nd favored enemy", ...) is one
    # feature, not one error (and one required class_features entry) per
    # level it appears at. `seen_tokens` keeps the first (level, raw token)
    # a given normalized token was seen at, in table order, so the error
    # message still names that first occurrence.
    if special_idx is not None:
        seen_tokens: dict[str, tuple[int, str]] = {}
        for row in rows:
            level_cell = _cell(row, level_idx)
            special_cell = _cell(row, special_idx)
            level = _parse_level_cell(level_cell) if level_cell is not None else None
            if level is None or special_cell is None:
                continue
            for token in _split_special_cell(special_cell):
                normalized_token = _normalize_special_token(token)
                if normalized_token not in seen_tokens:
                    seen_tokens[normalized_token] = (level, token)

        # A feature name that normalizes to the empty string (e.g. a
        # stray "(Ex)") is rejected by `_special_token_matches_feature`
        # itself -- an empty name would otherwise be contained in every
        # token and make every distinct token match vacuously.
        normalized_feature_names = [
            _normalize_special_token(feature_name) for feature_name, _level, _f in feature_entries
        ]
        for normalized_token, (level, token) in seen_tokens.items():
            matched = any(
                _special_token_matches_feature(normalized_token, normalized_feature_name)
                for normalized_feature_name in normalized_feature_names
            )
            if not matched:
                errors.append(
                    f"{name}: level {level} Special entry {token!r} has no matching "
                    "class_features entry"
                )

    for feature_name, feature_level, _feature in feature_entries:
        if isinstance(feature_level, int) and feature_level not in parsed_level_set:
            errors.append(
                f"{name}: class_features {feature_name!r} level {feature_level} is not in "
                "the level table"
            )

    # B10c-mand4 criterion 4: a class_features entry with no real
    # description is the padding the judgement flagged (an entry
    # duplicated per level with an empty body instead of one real entry
    # describing the whole progression) -- write nothing rather than an
    # empty string.
    for feature_name, _feature_level, feature in feature_entries:
        text_md = feature.get("text_md")
        if not isinstance(text_md, str) or not text_md.strip():
            errors.append(f"{name}: class_features {feature_name!r} has an empty text_md")

    # B10c-mand4 criterion 5: two entries whose names normalize to the same
    # thing are the other half of that padding -- one feature, split across
    # several near-duplicate entries (e.g. "Inspire Courage"/"Inspire
    # Courage +2"/"+3"/"+4", or "Bonus Feat" repeated per level).
    normalized_name_counts: dict[str, int] = {}
    for feature_name, _feature_level, _feature in feature_entries:
        normalized_name = _normalize_special_token(feature_name)
        normalized_name_counts[normalized_name] = normalized_name_counts.get(normalized_name, 0) + 1
    for normalized_name, count in normalized_name_counts.items():
        if count > 1:
            errors.append(f"{name}: class_features has duplicate feature name {normalized_name!r}")

    # B10c-mand4 criterion 6: a caster must carry its printed `Spells`
    # class feature as a class_features entry of its own, not silently
    # omit it (the wizard/bard/sorcerer records the judgement flagged all
    # have `spellcasting` set but no `Spells` entry at all).
    if isinstance(spellcasting, dict):
        spells_normalized = _normalize_special_token("Spells")
        has_spells_feature = any(
            _normalize_special_token(feature_name) == spells_normalized
            for feature_name, _feature_level, _feature in feature_entries
        )
        if not has_spells_feature:
            errors.append(f"{name}: spellcasting is set but class_features has no 'Spells' entry")

    return errors


#: Context-dependent checks (D9), keyed by type name -- both class types
#: share `check_class_fields` since their schemas share every field it
#: inspects.
TYPE_CONTEXT_CHECKS: dict[str, Callable[[dict[str, Any], ValidationContext], list[str]]] = {
    "class": check_class_fields,
    "prestige_class": check_class_fields,
}
