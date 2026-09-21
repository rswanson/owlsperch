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


def _check_source_pages(record: dict[str, Any], fields: dict[str, Any], name: str) -> list[str]:
    """B10c-mand12 rule 1: `fields.source_pages` is a PDF page span --
    `owlsperch.build_db.runner._apply_superseding` reads it as one when it
    decides which `rules_section`/`table` records a class swallows -- so it
    must agree with the record's own (also pdf) envelope `pages`:
    `start == min(pages)` and `end == max(pages)`. The real-corpus catch is
    the PHB druid, whose `source_pages` was written as the PRINTED span
    33-37 while its `pages` are pdf 34-38, so the supersede pass looked one
    page too low at both ends. Skipped entirely when either side is
    missing or malformed -- the schema and `check_pages_within_segment`
    already report that."""
    source_pages = fields.get("source_pages")
    if not isinstance(source_pages, dict):
        return []
    start = source_pages.get("start")
    end = source_pages.get("end")
    if not isinstance(start, int) or isinstance(start, bool):
        return []
    if not isinstance(end, int) or isinstance(end, bool):
        return []
    pages = record.get("pages")
    if not isinstance(pages, list):
        return []
    page_numbers = [p for p in pages if isinstance(p, int) and not isinstance(p, bool)]
    if not page_numbers:
        return []
    expected_start, expected_end = min(page_numbers), max(page_numbers)
    if start == expected_start and end == expected_end:
        return []
    return [
        f"{name}: source_pages {start}-{end} does not match this record's pdf pages "
        f"{expected_start}-{expected_end} (source_pages is a PDF page span, not the "
        "printed one)"
    ]


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

    raw_name = record.get("name")
    name = raw_name if isinstance(raw_name, str) else "<unnamed>"

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

    errors.extend(_check_source_pages(record, fields, name))

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


# ---------------------------------------------------------------------------
# Batch B10c-mand12: segment-aware class/prestige_class coverage checks. The
# 2026-09-18 class quality judgement found three whole-record omissions that
# no per-record check could see, because the evidence they're missing lives
# in the OWNING SEGMENT's own `text`, not in the record: a dropped
# "<Race> <Class> Starting Package"/"Ex-<Class>" section, a printed run-in
# Class Features heading nested inside a sibling feature's `text_md` instead
# of becoming its own `class_features` entry, and a feature whose `text_md`
# is a condensed paraphrase of the printed span. These live in
# `TYPE_SEGMENT_CHECKS` and receive the same already-resolved segment dict
# `check_pages_within_segment` does.
# ---------------------------------------------------------------------------

#: A run-in heading inside a class's printed Class Features section:
#: `<Heading>:` at a sentence start, 2-60 characters of letters, an
#: apostrophe, a comma, parentheses, a slash, a space or a hyphen, beginning
#: with a capital -- which covers both the plain form ("Bonus Languages:")
#: and the supernatural-tagged one ("Wild Shape (Su):"). The character class
#: alone is not enough: it also matches an ordinary mid-paragraph clause that
#: happens to end in a colon ("If she has a familiar, the following apply:"),
#: so every match is additionally put through `_is_heading_shaped`.
_RUN_IN_HEADING_RE = re.compile(r"([A-Z][A-Za-z'’,()/ -]{1,59}):")

#: A printed run-in heading is title-cased and short. `_is_heading_shaped`
#: requires at most this many whitespace tokens -- the longest real PHB class
#: heading is "Tongue of the Sun and Moon (Ex)" at 7.
_MAX_HEADING_TOKENS = 8

#: ...and every token LONGER than this to start with a capital. Shorter ones
#: are exempt because a title-cased heading leaves its function words lower
#: ("of", "the", "and", "or", "in") and its tags bare ("(Ex)" -> "Ex").
_HEADING_SHORT_TOKEN_LEN = 3

#: Leading/trailing punctuation on one token of a heading, stripped before
#: the capitalization test so "(Ex)" tests as "Ex" and "Domains," as
#: "Domains".
_TOKEN_EDGE_PUNCTUATION_RE = re.compile(r"^[^0-9A-Za-z]+|[^0-9A-Za-z]+$")


def _is_heading_shaped(heading: str) -> bool:
    """Whether `heading` looks like a printed run-in HEADING rather than an
    ordinary sentence clause that merely ends in a colon.

    `_RUN_IN_HEADING_RE` alone accepts, for instance, "If she has a
    familiar, the following apply" and "Her options for new forms include"
    out of the middle of a feature's own prose -- each would then be
    reported as a missing class feature AND would cut the real feature's
    printed span short. Two conditions, calibrated so that every one of the
    ~150 real run-in headings across the 11 PHB class segments still
    passes: at most `_MAX_HEADING_TOKENS` tokens, and every token longer
    than `_HEADING_SHORT_TOKEN_LEN` characters (once its edge punctuation is
    stripped) capitalized."""
    tokens = heading.split()
    if not tokens or len(tokens) > _MAX_HEADING_TOKENS:
        return False
    for token in tokens:
        word = _TOKEN_EDGE_PUNCTUATION_RE.sub("", token)
        if len(word) <= _HEADING_SHORT_TOKEN_LEN:
            continue
        if not word[:1].isupper():
            return False
    return True


#: Sentence-final punctuation, allowing the closing bracket/quote a citation
#: puts after the period -- PHB's paladin prints "(See Turn or Rebuke
#: Undead, page 159.) Spells: ..." and a bare `\.$` test would refuse to see
#: `Spells:` as a run-in heading there at all.
_SENTENCE_END_RE = re.compile(r"[.!?][)\]\"”’]*$")

#: Printed run-in labels inside (or leaking into) a class entry that are NOT
#: class features: the GAME RULE INFORMATION labels, the flavor-section
#: labels that become `description_sections` instead, the starting-package
#: labels, and the in-prose clarifiers ("Exceptions:", "Note:") the PHB
#: prints mid-feature. Calibrated against all 11 real PHB class segments:
#: only "Exceptions" (cleric, twice) and "Skill Selection" (rogue, from the
#: starting-package paragraph) actually leak today; the rest are listed
#: pre-emptively because they are printed the same way elsewhere in the same
#: chapter. Deliberately NOT listed: "Feat"/"Feats", which the rogue prints
#: as a real special-ability heading.
_NON_FEATURE_RUN_IN_HEADINGS = (
    "Weapon and Armor Proficiency",
    "Note",
    "Notes",
    "Exception",
    "Exceptions",
    "Example",
    "Examples",
    "Special",
    "Abilities",
    "Alignment",
    "Hit Die",
    "Class Skills",
    "Class Features",
    "Other Features",
    "Skill Points at 1st Level",
    "Skill Points at Each Additional Level",
    "Adventures",
    "Characteristics",
    "Religion",
    "Background",
    "Races",
    "Other Classes",
    "Role",
    "Starting Package",
    "Skill Selection",
    "Armor",
    "Weapon",
    "Weapons",
    "Gear",
    "Spells Known",
    "Spells Prepared",
    "Deity/Domains",
)

#: A matched feature's `text_md` must carry at least this fraction of its
#: printed span's word count. Calibrated on the 90 real (heading, feature)
#: pairs across the 11 PHB class records: 87 land at >= 0.92 (most at
#: exactly 1.00 -- verbatim), one at 0.85 (the rogue's Trapfinding, which
#: drops a single printed sentence -- a MINOR the judgement did not raise),
#: and one at 0.67 (the bard's Spells, the condensed paraphrase the
#: judgement raised as a MAJOR). 0.75 sits in the widest gap in that
#: distribution; the 0.60 originally proposed catches nothing at all on the
#: real corpus, including the record the rule exists for.
_FEATURE_TEXT_COVERAGE_RATIO = 0.75

#: A paragraph broken mid-WORD at a column break: a letter followed by a
#: hard hyphen at the very end (the PHB bard's "...Cha 11 for 1st-"). This
#: is the only corroboration `_column_break_continuation` accepts for a
#: stitch, since a paragraph that merely ends without a full stop is
#: routine in a reconstructed column and proves nothing about WHICH other
#: paragraph continues it.
_COLUMN_BREAK_HYPHEN_RE = re.compile(r"[A-Za-z][-\u2010\u2011]$")

#: A paragraph must be at least this many words to count as prose for the
#: column-break stitch below -- a table row, a caption or a stray cell is
#: never a truncated paragraph or its continuation.
_MIN_PROSE_WORDS = 20


def _normalize_heading(text: str) -> str:
    """The comparison form for a printed heading against a
    `description_sections[].heading`/`class_features[].name`: drop every
    parenthetical group (so "Wild Shape (Su)" compares equal to a record
    that spells the feature without its tag), fold all other punctuation to
    a space (case/punctuation/whitespace-insensitive -- "Ex-Paladins" and
    "Ex Paladins" agree), lowercase, and singularize each word with
    `_fold_trailing_plural` (plural-tolerant -- "Ex-Druid"/"Ex-Druids").

    Unlike `_normalize_special_token` this keeps a trailing numeric bonus or
    ordinal, because a printed HEADING never carries the per-level rank a
    level table's Special cell does."""
    collapsed = _PAREN_RE.sub(" ", text)
    collapsed = re.sub(r"[^0-9A-Za-z]+", " ", collapsed).strip().lower()
    if not collapsed:
        return collapsed
    return " ".join(_fold_trailing_plural(word) for word in collapsed.split(" "))


_ALLOWED_RUN_IN_HEADINGS = frozenset(
    _normalize_heading(heading) for heading in _NON_FEATURE_RUN_IN_HEADINGS
)


def _is_caps_heading(line: str) -> bool:
    """Whether `line` is a standalone ALL-CAPS heading paragraph -- the
    marker `text`/`segment` emit for a sidebar or the next printed section
    ("THE DRUID'S ANIMAL COMPANION", "GAME RULE INFORMATION", "SCHOOL
    SPECIALIZATION"). Used to close the Class Features window, which is what
    keeps an animal companion's or a specialist school's own run-in headings
    from being demanded as class features of the class."""
    stripped = line.strip()
    if not stripped or "\t" in stripped or len(stripped) > 60:
        return False
    if not re.search(r"[A-Z]", stripped):
        return False
    return not re.search(r"[a-z]", stripped)


def _extra_section_patterns(class_name: str) -> tuple[re.Pattern[str], re.Pattern[str]]:
    """The two standalone-heading patterns rule (a) looks for in a class
    segment's own text: `<Words> <Class> Starting Package` and
    `Ex-<Class>[s]`, both anchored to a whole line and plural-tolerant."""
    escaped = re.escape(class_name)
    # "es" as well as "s": most class names pluralize with a bare "s"
    # ("Ex-Paladins", "Ex-Druids"), but a sibilant-stemmed one would not.
    package = re.compile(
        rf"(?:[A-Za-z'’-]+\s+)*{escaped}(?:es|s)?\s+Starting\s+Package",
        re.IGNORECASE,
    )
    ex_class = re.compile(rf"Ex[-‐-―]{escaped}(?:es|s)?", re.IGNORECASE)
    return package, ex_class


def _class_features_window(paragraphs: list[str], class_name: str) -> list[str] | None:
    """The stripped paragraphs of a class's printed Class Features section,
    or `None` when the segment has no recognizable one.

    Starts at the first paragraph that is exactly the "Class Features"
    heading or that opens "All of the following are class features" -- NOT
    merely one starting with the words "Class Features", since the chapter
    intro prints "Class Features: Special characteristics of the class..."
    as a run-in and the class segments' back-extended text routinely carries
    it. Ends at the first following `Ex-<Class>`/`... Starting Package`
    heading (those are `description_sections`, checked by rule (a)) or the
    first ALL-CAPS sidebar/section heading, whichever comes first."""
    package_re, ex_class_re = _extra_section_patterns(class_name)
    start: int | None = None
    for index, paragraph in enumerate(paragraphs):
        stripped = paragraph.strip()
        lowered = stripped.lower()
        if lowered == "class features" or lowered.startswith(
            "all of the following are class features"
        ):
            start = index
            break
    if start is None:
        return None
    end = len(paragraphs)
    for index in range(start + 1, len(paragraphs)):
        stripped = paragraphs[index].strip()
        if (
            ex_class_re.fullmatch(stripped)
            or package_re.fullmatch(stripped)
            or _is_caps_heading(stripped)
        ):
            end = index
            break
    return [paragraph.strip() for paragraph in paragraphs[start:end]]


def _run_in_headings(paragraph: str) -> list[tuple[int, int, str]]:
    """Every `(start, end, heading)` run-in heading in one paragraph: a
    `_RUN_IN_HEADING_RE` match that begins the paragraph or follows
    sentence-final punctuation AND is `_is_heading_shaped`."""
    found: list[tuple[int, int, str]] = []
    for match in _RUN_IN_HEADING_RE.finditer(paragraph):
        heading = match.group(1).strip()
        if not _is_heading_shaped(heading):
            continue
        preceding = paragraph[: match.start()].rstrip()
        if preceding == "" or _SENTENCE_END_RE.search(preceding):
            found.append((match.start(), match.end(), heading))
    return found


def _is_prose(paragraph: str) -> bool:
    return "\t" not in paragraph and len(paragraph.split()) >= _MIN_PROSE_WORDS


def _column_break_continuation(paragraphs: list[str]) -> tuple[int, str] | None:
    """The one corroborated column-break stitch in a Class Features window,
    as `(index of the truncated paragraph, the continuation's own prefix)`,
    or `None`.

    `owlsperch.text.columns` reconstructs reading order paragraph by
    paragraph, and a printed feature description that spans a column break
    arrives as two paragraphs -- often out of order (the PHB bard's "Spells:"
    body ends mid-word at "Cha 11 for 1st-" and continues in a paragraph
    printed EARLIER in the segment text, "level spells, and so forth)...").
    Without stitching them back together the last feature in the truncated
    paragraph is measured against a fraction of its own printed span, and
    rule (c) can't see a paraphrase there at all.

    Unlike leaving a window unstitched -- which only ever makes a span
    SHORTER and a ratio LARGER, and so can never produce a false failure --
    stitching the WRONG pair appends unrelated prose and LOWERS the ratio,
    which can. Three things bound that:

    * the window must hold EXACTLY ONE truncated prose paragraph (one not
      ending at a sentence boundary) and EXACTLY ONE prose paragraph that
      begins mid-sentence, so there is no pairing to choose between;
    * the truncated paragraph must end mid-WORD, on a hard hyphen
      (`_COLUMN_BREAK_HYPHEN_RE`), and the continuation must open with the
      lowercase remainder of that word -- the one unambiguous signature of a
      column break, and the same one `owlsperch.text.cleanup`'s
      dehyphenation rejoins within a paragraph. A paragraph that merely ends
      without a full stop (the PHB monk's and druid's windows both do) is
      NOT corroboration, and is left unstitched;
    * the appended text is capped at the continuation's own prefix BEFORE
      its first run-in heading, so at most one printed feature's worth of
      continuation is ever added, never the rest of the column.

    A word-count cap tied to the truncated paragraph is deliberately not
    used: the bard's truncated tail is 89 words against a 487-word
    continuation, so any such cap would hide the very paraphrase rule (c)
    exists to catch."""
    truncated = [
        index
        for index, paragraph in enumerate(paragraphs)
        if _is_prose(paragraph) and not _SENTENCE_END_RE.search(paragraph.strip())
    ]
    continuations = [
        index
        for index, paragraph in enumerate(paragraphs)
        if _is_prose(paragraph) and paragraph.strip()[:1].islower()
    ]
    if len(truncated) != 1 or len(continuations) != 1 or truncated[0] == continuations[0]:
        return None
    if not _COLUMN_BREAK_HYPHEN_RE.search(paragraphs[truncated[0]].strip()):
        return None
    continuation = paragraphs[continuations[0]]
    if not continuation.strip()[:1].isalpha():
        return None
    headings = _run_in_headings(continuation)
    prefix = continuation[: headings[0][0]] if headings else continuation
    return truncated[0], prefix


def _check_extra_sections(
    record: dict[str, Any], text: str, class_name: str, name: str
) -> list[str]:
    """Rule (a): every `<Words> <Class> Starting Package` and
    `Ex-<Class>[s]` heading the segment text prints on a line of its own
    must be recorded, as a `description_sections[].heading` or (the cleric's
    own "Ex-Clerics", which its record files under Class Features instead --
    an equally faithful placement) a `class_features[].name`."""
    fields = record.get("fields")
    if not isinstance(fields, dict):
        return []
    package_re, ex_class_re = _extra_section_patterns(class_name)

    printed: list[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if package_re.fullmatch(stripped) or ex_class_re.fullmatch(stripped):
            if stripped not in printed:
                printed.append(stripped)
    if not printed:
        return []

    recorded: set[str] = set()
    sections = fields.get("description_sections")
    if isinstance(sections, list):
        for section in sections:
            if isinstance(section, dict) and isinstance(section.get("heading"), str):
                recorded.add(_normalize_heading(section["heading"]))
    features = fields.get("class_features")
    if isinstance(features, list):
        for feature in features:
            if isinstance(feature, dict) and isinstance(feature.get("name"), str):
                recorded.add(_normalize_heading(feature["name"]))

    return [
        f"{name}: segment text prints a {heading!r} section that no "
        "description_sections/class_features entry records"
        for heading in printed
        if _normalize_heading(heading) not in recorded
    ]


def _game_rule_information_window(paragraphs: list[str]) -> list[str] | None:
    """The stripped paragraphs of a class's printed GAME RULE INFORMATION
    block (batch B10c-mand16), or `None` when the segment has no
    recognizable one.

    Starts at the paragraph that is exactly the "GAME RULE INFORMATION"
    heading; ends at the first following paragraph that is exactly the
    "Class Skills" heading or an ALL-CAPS sidebar/section heading
    (`_is_caps_heading`), whichever comes first -- the same windowing
    `_class_features_window` uses for its own end boundary. The real PHB
    wizard prints its own "Class Skills" heading BEFORE "GAME RULE
    INFORMATION" in this segment's reconstructed reading order; that's
    harmless here, since the printed "Abilities:" run-in always sits in the
    paragraph immediately after the GAME RULE INFORMATION heading itself,
    well before the next ALL-CAPS heading closes the window regardless of
    where "Class Skills" falls."""
    start: int | None = None
    for index, paragraph in enumerate(paragraphs):
        if paragraph.strip().lower() == "game rule information":
            start = index
            break
    if start is None:
        return None
    end = len(paragraphs)
    for index in range(start + 1, len(paragraphs)):
        stripped = paragraphs[index].strip()
        if stripped.lower() == "class skills" or _is_caps_heading(stripped):
            end = index
            break
    return [paragraph.strip() for paragraph in paragraphs[start:end]]


def _check_abilities_section(fields: dict[str, Any], text: str, name: str) -> list[str]:
    """Rule (d) (batch B10c-mand16, the 2026-09-21 class quality judgement's
    finding D): the printed "Abilities:" run-in under GAME RULE INFORMATION
    is its own `description_sections` entry (`heading: "Abilities"`, per
    the B10c-mand13 prompt rule) -- distinct from the `alignment` field and
    from the flavor "Alignment" section printed alongside it. Skipped
    silently when the segment has no recognizable GAME RULE INFORMATION
    window at all, or that window prints no "Abilities:" run-in of its own
    (a prestige class need not print one)."""
    window = _game_rule_information_window(text.split("\n"))
    if window is None:
        return []
    prints_abilities = any(
        _normalize_heading(heading) == _normalize_heading("Abilities")
        for paragraph in window
        for _start, _end, heading in _run_in_headings(paragraph)
    )
    if not prints_abilities:
        return []
    sections = fields.get("description_sections")
    if isinstance(sections, list):
        for section in sections:
            if (
                isinstance(section, dict)
                and isinstance(section.get("heading"), str)
                and _normalize_heading(section["heading"]) == _normalize_heading("Abilities")
            ):
                return []
    return [
        f'{name}: segment text prints an "Abilities:" paragraph under GAME RULE '
        'INFORMATION that no description_sections entry with heading "Abilities" records'
    ]


def check_class_segment_coverage(
    record: dict[str, Any], segment: dict[str, Any] | None
) -> list[str]:
    """Batch B10c-mand12 (plus B10c-mand16's rule (d)): the class/
    prestige_class checks that need the OWNING SEGMENT's own `text` as the
    evidence of what the page printed.

    (a) Every `<Words> <Class> Starting Package`/`Ex-<Class>` heading the
        text prints on its own line is recorded somewhere in the record
        (`_check_extra_sections`).
    (b) Every run-in heading inside the printed Class Features window
        (`_class_features_window`) either matches a `class_features[].name`
        (exactly, once both sides go through `_normalize_heading`) or is a
        known non-feature label (`_NON_FEATURE_RUN_IN_HEADINGS`). Exact
        normalized equality rather than `_special_token_matches_feature`'s
        containment on purpose: a heading like "Deity, Domains, and Domain
        Spells" CONTAINS the word "spell" and would be satisfied by the
        sibling "Spells" feature it was wrongly nested inside, which is the
        exact PHB cleric defect this rule exists to catch.
    (c) A matched feature's `text_md` carries at least
        `_FEATURE_TEXT_COVERAGE_RATIO` of the printed span's word count --
        the span running from its heading to the next run-in heading in the
        same paragraph, extended through `_column_break_continuation` when
        the paragraph is cut off at a column break.
    (d) A printed "Abilities:" run-in under GAME RULE INFORMATION
        (`_game_rule_information_window`) is recorded as a
        `description_sections[].heading` normalizing to "Abilities"
        (`_check_abilities_section`).

    The whole check is skipped silently (returns `[]`) when there is no
    segment, no `text`, or no usable record `name` -- a missing segment is
    already its own failure via `check_pages_within_segment`. When a segment
    HAS text but no recognizable Class Features window, only (b) and (c) are
    skipped: (a) and (d) work off the raw segment text and still run, since
    a dropped Starting Package section or Abilities entry is visible
    without one, and a page layout this can't read must not be reported as
    a missing feature."""
    if segment is None:
        return []
    text = segment.get("text")
    if not isinstance(text, str) or not text.strip():
        return []
    class_name = record.get("name")
    if not isinstance(class_name, str) or not class_name.strip():
        return []
    name = class_name

    errors = _check_extra_sections(record, text, class_name, name)

    fields = record.get("fields")
    if not isinstance(fields, dict):
        return errors
    errors.extend(_check_abilities_section(fields, text, name))
    features_by_name: dict[str, dict[str, Any]] = {}
    raw_features = fields.get("class_features")
    if isinstance(raw_features, list):
        for feature in raw_features:
            if isinstance(feature, dict) and isinstance(feature.get("name"), str):
                features_by_name.setdefault(_normalize_heading(feature["name"]), feature)

    paragraphs = _class_features_window(text.split("\n"), class_name)
    if paragraphs is None:
        return errors
    stitch = _column_break_continuation(paragraphs)

    for index, paragraph in enumerate(paragraphs):
        headings = _run_in_headings(paragraph)
        for position, (_start, heading_end, heading) in enumerate(headings):
            normalized = _normalize_heading(heading)
            feature = features_by_name.get(normalized)
            if feature is None:
                if normalized not in _ALLOWED_RUN_IN_HEADINGS:
                    errors.append(
                        f"{name}: printed Class Features heading {heading!r} has no "
                        "class_features entry of its own"
                    )
                continue
            if position + 1 < len(headings):
                span = paragraph[heading_end : headings[position + 1][0]]
            else:
                span = paragraph[heading_end:]
                if stitch is not None and stitch[0] == index:
                    span = f"{span} {stitch[1]}"
            span_words = len(span.split())
            if span_words == 0:
                continue
            text_md = feature.get("text_md")
            text_words = len(text_md.split()) if isinstance(text_md, str) else 0
            ratio = text_words / span_words
            if ratio < _FEATURE_TEXT_COVERAGE_RATIO:
                errors.append(
                    f"{name}: class_features {heading!r} text_md has {text_words} word(s) "
                    f"against {span_words} printed word(s) (ratio {ratio:.2f}, minimum "
                    f"{_FEATURE_TEXT_COVERAGE_RATIO:.2f}) -- looks condensed rather than "
                    "verbatim"
                )

    return errors


# ---------------------------------------------------------------------------
# Batch B10c-mand20 (judge round 4, blocker 1): a `table` record's own
# `check_table_fields` only checks INTERNAL shape (row/column lengths,
# empty-cell-style consistency) -- nothing checked that a printed grid a
# table record claims to transcribe actually appears in its owning
# segment's own text. A haiku extractor handed a truncated rules_section
# segment (`phb1-p0046-02`, 639 characters -- the sidebar's opening
# paragraph only) INVENTED a whole plausible-looking progression grid to
# satisfy the "each printed grid is its own table" prompt rule instead of
# answering `needs_context`: `table:phb1:the-paladins-mount` passed schema
# and `check_table_fields` while its "Natural Armor Adj." values and its
# "Regeneration 1/round"/"Immunity to magic sleep and animal friendship"
# Special entries appear nowhere in the book at all. This section adds the
# segment-aware check that catches it.
# ---------------------------------------------------------------------------

#: Superscript footnote digits/marks (or a dagger/double-dagger note
#: marker) that can appear glued to a printed table cell -- stripped before
#: comparing a cell against the owning segment's own text. A footnote
#: reference printed as a bare, non-superscript digit glued directly onto a
#: word (e.g. PHB p.163's "Additional" table printing "Obstacle1", with the
#: footnote text itself elsewhere in the segment) is NOT covered by this --
#: it's handled by `_table_cell_matches_text`'s prefix-tolerant token match
#: instead, since stripping a bare trailing digit here would just as easily
#: eat a real value like a table's "1st" row label.
_FOOTNOTE_MARK_CHARS = "¹²³⁰⁴⁵⁶⁷⁸⁹†‡"

#: Curly quotes/apostrophes and prime marks folded to their plain ASCII
#: equivalent before comparison (B10c-mand20 review finding 1) -- a cell's
#: own JSON string and the segment's own extracted text don't always agree
#: on which glyph a printed apostrophe/quote became.
_QUOTE_FOLD_TABLE = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "‚": "'",
        "‛": "'",
        "′": "'",
        "“": '"',
        "”": '"',
        "„": '"',
        "‟": '"',
        "″": '"',
    }
)


def _normalize_table_text(text: str) -> str:
    """The shared comparison form for `check_table_cells_in_segment`: fold
    every dash-like character (`slugify`'s own `Pd`-category rule, plus the
    Unicode minus -- the same fold `_normalize_cell` uses) to a plain `-`,
    drop footnote superscript marks (`_FOOTNOTE_MARK_CHARS`), fold curly
    quotes/primes to their ASCII equivalent (`_QUOTE_FOLD_TABLE`), casefold,
    collapse a space right after a `/` (B10c-mand20 review finding 1: the
    real PHB barbarian's iterative-attack cell "+18/+13/+8/+3" is
    reconstructed with a stray space after each slash, "+18/ +13/ +8/ +3"),
    and collapse ALL remaining whitespace -- including a paragraph break --
    to a single space, so a cell can be compared against the segment's
    whole text as one line regardless of where a printed line break
    falls."""
    folded = "".join(
        "-" if unicodedata.category(ch) == "Pd" or ch == _UNICODE_MINUS else ch for ch in text
    )
    folded = "".join(ch for ch in folded if ch not in _FOOTNOTE_MARK_CHARS)
    folded = folded.translate(_QUOTE_FOLD_TABLE)
    folded = folded.casefold()
    folded = re.sub(r"/\s+", "/", folded)
    return re.sub(r"\s+", " ", folded).strip()


#: A cell's alphanumeric "words", for the token-subsequence fallback below.
_TABLE_CELL_TOKEN_RE = re.compile(r"[a-z0-9]+")

#: A cell has a "real word" -- and so is eligible for the token-subsequence
#: fallback below -- only once it contains an alphabetic run of at least
#: this many letters. A short/numeric cell ("3rd", "+18/+13/+8/+3", "1st")
#: never does, and is instead held to a literal-substring match: the
#: fallback's per-token match is prefix-tolerant in both directions (needed
#: for a real word split across a footnote-glued digit, see below), which
#: over a whole-table-text token stream lets a short numeric cell like
#: "3rd" match a `startswith` hit on an unrelated "3" token (e.g. from
#: "Table 3-12") -- vacuous, and the exact real-corpus gap the B10c-mand20
#: review found: `familiar-progression`'s invented "1st-2nd"/"3rd-4th"/...
#: row labels only partly failed under the old rule.
_ALPHA_WORD_RE = re.compile(r"[a-z]{3,}")


def _table_cell_matches_text(cell: str, normalized_text: str, text_tokens: list[str]) -> bool:
    """Whether one table cell (a header or a row cell) is supported by the
    owning segment's own text.

    A cell that normalizes to nothing, or to ONLY dash-placeholder
    characters (`_DASH_PLACEHOLDER_CHARS` -- "no printed value"), always
    passes -- there's nothing to check it against. Otherwise it passes when
    its own normalized form appears as a literal substring of the
    segment's (also normalized, whitespace-collapsed) text.

    A cell with no "real word" -- no alphabetic run of >= 3 letters
    (`_ALPHA_WORD_RE`), i.e. a short or purely numeric cell -- is held to
    THAT test alone: nothing else is tight enough to check a value this
    short without becoming vacuous (see `_ALPHA_WORD_RE`'s own docstring).

    A cell WITH a real word additionally passes when every one of its
    alphanumeric tokens (`_TABLE_CELL_TOKEN_RE`) appears, in the SAME order
    (not necessarily contiguous), somewhere in the segment's own token
    stream. This is what a real, correctly-extracted word cell needs when
    its printed form doesn't survive as one contiguous run of text: a
    stacked two-line header merged into one column per the prompt's own
    rule (e.g. "Base" + "Attack Bonus" -> "Base Attack Bonus"), or a
    footnote printed as a bare digit glued onto a word with no space and no
    superscript codepoint (PHB p.163's "Obstacle1", whose footnote note the
    extractor correctly folds into the cell as "Obstacle (may require a
    skill check)" -- caught by the per-token match being prefix-tolerant in
    EITHER direction, so the cell token "obstacle" matches the text token
    "obstacle1"). Token order is required only WITHIN one cell, not across
    the whole table, so this stays tight enough to still fail a genuinely
    invented cell -- calibrated against every real table in the corpus,
    see `check_table_cells_in_segment`."""
    normalized_cell = _normalize_table_text(cell).strip(" .,;:")
    if not normalized_cell or all(ch in _DASH_PLACEHOLDER_CHARS for ch in normalized_cell):
        return True
    if normalized_cell in normalized_text:
        return True
    if not _ALPHA_WORD_RE.search(normalized_cell):
        return False
    cell_tokens = _TABLE_CELL_TOKEN_RE.findall(normalized_cell)
    if not cell_tokens:
        return True
    remaining = iter(text_tokens)
    for token in cell_tokens:
        for candidate in remaining:
            if candidate == token or candidate.startswith(token) or token.startswith(candidate):
                break
        else:
            return False
    return True


def check_table_cells_in_segment(
    record: dict[str, Any], segment: dict[str, Any] | None
) -> list[str]:
    """(B10c-mand20): every non-empty `table` cell -- every `columns`
    header and every `rows` cell -- must be traceable to the OWNING
    SEGMENT's own text, not merely schema-shaped (see the module comment
    above for the real-corpus defect this exists to catch).

    Skipped silently -- same convention as `check_class_segment_coverage`
    -- when there is no segment, no segment `text`, no usable `fields`, or
    no cells to check at all. One combined error per table (never one per
    cell, which would let a single bad table dominate a `validate --json`
    run's error list), naming the first 3 offending cells in printed order
    plus the total offending/checked count, e.g. "The Paladin's Mount: 28
    of 30 cells are not in the owning segment's text (e.g. 'Paladin
    Level', 'Bonus HD', 'Natural Armor Adj.')".

    Calibrated against every real `table` record under phb1 (65 tables):
    besides the fabricated Paladin's Mount, this also catches
    `familiar-progression` -- its "Master Class Level" header and
    "1st-2nd"/"3rd-4th"/... row labels are a generic D&D level-bracket
    guess for a table the segment's own text never reaches (it ends mid-
    description, before the real numeric grid), the same failure mode as
    the Paladin's Mount with mostly-blank data cells instead of invented
    ones -- left here as a second real finding, not suppressed. Every
    other real table passes, including the ones that need the widened
    normalization above (e.g. `additional`'s footnote-folded "Obstacle
    (may require a skill check)" cell)."""
    if segment is None:
        return []
    text = segment.get("text")
    if not isinstance(text, str) or not text.strip():
        return []
    fields = record.get("fields")
    if not isinstance(fields, dict):
        return []

    name_raw = record.get("name")
    name = name_raw if isinstance(name_raw, str) and name_raw.strip() else record.get("id")

    cells: list[str] = []
    columns = fields.get("columns")
    if isinstance(columns, list):
        cells.extend(cell for cell in columns if isinstance(cell, str))
    rows = fields.get("rows")
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, list):
                cells.extend(cell for cell in row if isinstance(cell, str))
    if not cells:
        return []

    normalized_text = _normalize_table_text(text)
    text_tokens = _TABLE_CELL_TOKEN_RE.findall(normalized_text)

    offenders = [
        cell for cell in cells if not _table_cell_matches_text(cell, normalized_text, text_tokens)
    ]
    if not offenders:
        return []

    examples = ", ".join(repr(cell) for cell in offenders[:3])
    return [
        f"{name}: {len(offenders)} of {len(cells)} cells are not in the owning "
        f"segment's text (e.g. {examples})"
    ]


#: Segment-aware checks (B10c-mand12, plus B10c-mand20's table check),
#: keyed by type name. Like `TYPE_CONTEXT_CHECKS` these are dispatched by
#: `owlsperch.validate.runner.validate_record`, which already resolves the
#: originating segment for `check_pages_within_segment`.
TYPE_SEGMENT_CHECKS: dict[str, Callable[[dict[str, Any], dict[str, Any] | None], list[str]]] = {
    "class": check_class_segment_coverage,
    "prestige_class": check_class_segment_coverage,
    "table": check_table_cells_in_segment,
}
