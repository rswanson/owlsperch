"""Rendering the subagent extraction prompt for `owlsperch queue prompt
<seg_id>` (and `queue next`, which renders one for every segment it selects),
per spec 4.5 and B5 acceptance criterion 2.

Everything the prompt asserts about a schema (required fields, enums,
descriptions) is generated from the actual `schemas/*.json` files via
`owlsperch.schemas.load_registry` -- never hand-copied -- so a schema edit
(spec 4.14's schema-growth mechanism) is picked up automatically the next
time a prompt is rendered. Schema rendering recurses into nested `object`
properties and `array` properties whose `items` are an `object`, so e.g. a
spell's `levels` array shows its item properties (`class`, `level`) indented
under the `levels` bullet, not just the array's own type/enum.

The prompt also includes a complete, schema-valid EXAMPLE RECORD for the
segment's kind_hint, loaded verbatim from `schemas/examples/<kind>.json`
(invented data, never a real book's spell -- see `test_schemas.py`'s
schema self-test, which validates every registered type's examples file
against its own schema). A non-`table` prompt additionally renders the
`table` type's own schema, rules, and example alongside its own (see
`_table_convention_lines`), since it tells its subagent to write a SECOND,
`table`-typed record for any cross-linked table.

A `rules_section` prompt's rules additionally require a GENERIC,
cross-chapter heading (e.g. "Class Features") to be qualified with its
enclosing entity in the record's own `name` (`Class Features (Barbarian)`,
never the bare heading) -- since `slug`/`id` are derived from `name`, an
unqualified generic name collides across chapters and overwrites another
chapter's record of the same slug (see `_KIND_RULES["rules_section"]`; the
convention is modeled by `schemas/examples/rules_section.json`).

Default model string ("claude-haiku-4-5") is only ever a placeholder for
`--model`/`--tier` defaults on the CLI (`owlsperch queue next|prompt`); the
value actually rendered into a prompt's `extraction.model` example is
whatever the caller passed (or that default), never a generic instruction
to the subagent to substitute its own name.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from owlsperch.fsutil import atomic_write_text
from owlsperch.manifest import ManifestEntry, ManifestError, default_manifest_path, load_manifest
from owlsperch.queue.abbrev import CLASS_ABBREVIATIONS
from owlsperch.schemas import Registry, load_registry
from owlsperch.segment.runner import Segment

#: Default `extraction.model` value when the caller (`queue next`/`queue
#: prompt`) doesn't pass `--model` explicitly.
DEFAULT_MODEL = "claude-haiku-4-5"

#: Per-kind extraction rules (B10), rendered as a "## Extraction rules for
#: `<kind>`" section right after the candidate schema. Only spell's rules
#: reference `CLASS_ABBREVIATIONS` -- the other kinds have no class-level
#: concept at all, so that table stays out of their prompts entirely
#: (criterion 7: a feat prompt must not carry spell class-level
#: instructions). A kind_hint with no entry here (e.g. `stat_block`, which
#: has no registered schema yet either) simply gets no rules section.
_KIND_RULES: dict[str, list[str]] = {
    "spell": [
        "`text_md` begins at the descriptive body, NOT the stat block --",
        "do NOT repeat School, Level, Components, Casting Time, Range,",
        "Target/Effect/Area, Duration, Saving Throw, or Spell Resistance",
        "as a bulleted list or in any other form: those already live in",
        "`fields` and the site renders them from there. `text_md` is only",
        "the description paragraph(s) that follow the stat block, faithfully",
        "reproduced.",
        "",
        "`levels[].class` must use the FULL class name, never the stat",
        "block's abbreviation -- expand every abbreviation using this",
        "table:",
        "",
        *[f"- `{abbr}` -> `{full}`" for abbr, full in CLASS_ABBREVIATIONS.items()],
        "",
        '`"Sor/Wiz N"` (or any other "/"-joined run of abbreviations) becomes',
        "TWO entries, one per class, both at the same level -- e.g.",
        '`"Sor/Wiz 3"` becomes `{"class": "Sorcerer", "level": 3}` and',
        '`{"class": "Wizard", "level": 3}`, never a single combined entry.',
        "Anything not in the table above (a cleric domain like `Air` or",
        "`Fire`, or a prestige class) is kept exactly as written, with only",
        "its first letter capitalized.",
    ],
    "feat": [
        "The heading is `NAME [TYPE]`: `name` is the title-cased name (e.g.",
        '"Power Attack"); `feat_type` is the bracketed tag, title-cased',
        '(e.g. "General"). If the heading has no bracketed tag, omit',
        "`feat_type` entirely (never write it as `null`).",
        "",
        "The body is one paragraph with inline `Prerequisite:` /",
        "`Prerequisites:` / `Benefit:` / `Normal:` / `Special:` markers --",
        "split on those markers:",
        "",
        "- `prerequisites` is the list of comma-separated prerequisites as",
        '  written (e.g. `["Str 13"]`).',
        "- `benefit`, `normal`, and `special` are the text after their own",
        "  marker, with the marker itself removed.",
        "",
        "`text_md` is ONLY the descriptive lead-in before the first",
        "`Prerequisite:`/`Benefit:` marker (often a single sentence, and",
        "sometimes there is none at all) -- never repeat the benefit/normal/",
        "special text there.",
    ],
    "rules_section": [
        "`topic` is the section's own heading/subject. `parent_section` is",
        "the enclosing section's heading, ONLY when the text makes it",
        "explicit (otherwise omit it). `chapter` is the chapter name, ONLY",
        "when it's known from the text (otherwise omit it).",
        "",
        "Some section headings are GENERIC: they recur verbatim across many",
        'chapters or entities in this book -- "Class Features", "Class Skills",',
        '"Game Rule Information", "Description", and others like them. For a',
        "GENERIC heading, the record's `name` MUST be qualified with the",
        "enclosing entity, in the form `Heading (Entity)` -- e.g. a Barbarian",
        'chapter\'s "Class Features" section becomes the record name',
        "`Class Features (Barbarian)`, never the bare `Class Features`.",
        "`fields.topic` still stays the bare heading (`Class Features`);",
        "`fields.parent_section` carries the enclosing entity (`Barbarian`) --",
        "the meaning of `topic`/`parent_section` does not change, only `name`",
        "gains the qualifier.",
        "",
        "Find the enclosing entity from, in order: the nearest preceding",
        "entity name in this segment's own text, this segment's own heading",
        'shown above under "## Segment", or an "### Adjacent context"',
        "block. If none of those name it, answer `needs_context` naming the",
        "previous segment id instead of guessing at an entity.",
        "",
        "`slug` and `id` follow from the QUALIFIED `name`, via the slug rule",
        "below -- `Class Features (Barbarian)` slugifies to",
        "`class-features-barbarian`, giving `id` =",
        "`rules_section:<book_id>:class-features-barbarian`. Qualifying the",
        "slug alone while leaving `name` GENERIC is WRONG and fails",
        "validation -- `slug` must equal the slugified `name`, so the",
        "qualifier belongs on `name` first and the slug follows from it, not",
        "the other way around.",
        "",
        "`text_md` is the section's prose, faithfully reproduced.",
        "",
        "A segment that is a table of contents entry, an index fragment, a",
        "caption with no rule text of its own, or a stray line answers",
        "`no_content` instead of a record.",
    ],
    "table": [
        "`name` is the printed table title VERBATIM, including its number",
        '(e.g. "Table 3–8: The Druid"). `caption` is the same string',
        "(or the fuller descriptive caption, when the text gives one",
        "separately from the bare title).",
        "",
        "`columns` is the header row's cells, left to right. `rows` is one",
        "list of strings per body row, with EXACTLY as many cells as",
        '`columns` -- pad a short row with `""` rather than dropping cells.',
        "If the header row can't be reconstructed from the segment text,",
        "answer `no_content` rather than guessing at column names.",
        "",
        "Many table segments in this pipeline are caption-only (the table's",
        "body was absorbed into a different segment during text",
        "reconstruction) -- answering `no_content` for one of those is",
        "correct, not a mistake to work around.",
    ],
    "class": [
        "The segment's heading names the class. The segment may include the",
        "first page of the NEXT class's own entry (this book's column layout",
        "routinely spills one class's last column onto the page the table of",
        "contents assigns to the next class) -- extract ONLY the class named",
        'in the heading. A table captioned "Table N-M: The <this class>"',
        "belongs to THIS class even if it sits past the next class's own",
        "heading.",
        "",
        "The level progression table is a SECOND, `table`-typed record,",
        'written via the "Tables belonging to this entity" convention below',
        "-- its `columns` are the book's own column headers, MERGED from a",
        "two-line stacked header into one column each (e.g. a header printed",
        'as "Base" over "Attack Bonus" becomes one column literally titled',
        '"Base Attack Bonus"); `rows` has EXACTLY one row per class level,',
        "1 through `max_level`, in order. Some rows are split across several",
        "blank-line-separated blocks in the segment text (one cell per",
        "block, still in column order) -- reassemble them; a dropped row",
        "fails validation. The Special-column cell is reproduced verbatim,",
        "comma-separated exactly as printed -- never summarized. Put the",
        "table record's own id in BOTH this record's `level_table` field",
        "AND this record's own `tables` array.",
        "",
        "`class_skills[].skill` is the book's own skill name WITH its",
        'parenthetical sub-skill kept (e.g. "Knowledge (arcana)", never',
        'just "Knowledge"); `class_skills[].key_ability` is the bracketed',
        "ability abbreviation (Str/Dex/Con/Int/Wis/Cha). A stray",
        "illustration caption can bleed into the skill-list sentence in this",
        "book's extracted text -- drop any token that is not an actual",
        "skill name rather than including it as a skill.",
        "",
        '`skill_points` comes from the "Skill Points at 1st Level" / "at',
        'Each Additional Level" lines -- e.g. "(4 + Int modifier) x 4" and',
        '"4 + Int modifier" become',
        '`{"base": 4, "ability": "Int", "first_level_multiplier": 4}`.',
        "",
        "`spellcasting.spell_list` is the class's own name exactly as spell",
        "records spell it in their own levels list (the FULL class",
        'name, e.g. "Wizard", never an abbreviation). OMIT `spellcasting`',
        "ENTIRELY for a class that does not cast spells -- never write it",
        "as `null`.",
        "",
        "`description_sections` is one entry per printed flavor subsection",
        "this book actually prints (Adventures, Characteristics, Alignment,",
        "Religion, Background, Races, Other Classes, Role, or whatever this",
        "book calls them) -- `heading` verbatim, in the order printed.",
        "",
        "`text_md` is ONLY the class's opening overview paragraph(s), before",
        "the first flavor subsection -- do NOT repeat `hit_die`,",
        "`alignment`, `class_skills`, the level table, or `class_features`",
        "there; the site renders all of those from `fields` directly.",
        "",
        '`source_pages` is `{"start": <first pdf page>, "end": <last pdf',
        "page>}` for this segment's own pdf page range (shown above under",
        '"## Segment").',
        "",
        "Never invent a value not stated in the text: OMIT the key entirely",
        "-- never write `null`. The one allowed exception: if the Special",
        "column names something with no description anywhere in this",
        "class's own prose (a real, printed gap -- not your own omission),",
        "write its `class_features` entry anyway, with `text_md` as the",
        'empty string `""`, rather than inventing a description or leaving',
        "it out of `class_features` altogether -- every name printed in the",
        "Special column must have a matching `class_features` entry at its",
        "first level.",
    ],
    "prestige_class": [
        "The segment's heading names the prestige class. The segment may",
        "include the first page of the NEXT entry's own text -- extract",
        "ONLY the entity named in the heading. A table captioned",
        '"Table N-M: The <this prestige class>" belongs to THIS entry even',
        "if it sits past the next entry's own heading.",
        "",
        '`class_type` is always `"prestige"`. `requirements` is REQUIRED:',
        "one entry per printed entry requirement (a base attack bonus, a",
        "minimum skill rank, a feat, an alignment restriction, a special",
        'ability, ...), each `{"kind": "<a short label, e.g. \\"base',
        'attack bonus\\">", "text": "<the requirement exactly as printed>"}`.',
        "",
        "The level progression table is a SECOND, `table`-typed record,",
        'written via the "Tables belonging to this entity" convention below',
        "-- its `columns` are the book's own column headers, MERGED from a",
        "two-line stacked header into one column each (e.g. a header printed",
        'as "Base" over "Attack Bonus" becomes one column literally titled',
        '"Base Attack Bonus"); `rows` has EXACTLY one row per class level,',
        "1 through `max_level`, in order. Some rows are split across several",
        "blank-line-separated blocks in the segment text (one cell per",
        "block, still in column order) -- reassemble them; a dropped row",
        "fails validation. The Special-column cell is reproduced verbatim,",
        "comma-separated exactly as printed -- never summarized. Put the",
        "table record's own id in BOTH this record's `level_table` field",
        "AND this record's own `tables` array.",
        "",
        "`class_skills[].skill` is the book's own skill name WITH its",
        'parenthetical sub-skill kept (e.g. "Knowledge (arcana)", never',
        'just "Knowledge"); `class_skills[].key_ability` is the bracketed',
        "ability abbreviation (Str/Dex/Con/Int/Wis/Cha).",
        "",
        '`skill_points` comes from the "Skill Points at 1st Level" / "at',
        'Each Additional Level" lines the same way a base class prints',
        'them -- e.g. "(2 + Int modifier) x 4" and "2 + Int modifier" become',
        '`{"base": 2, "ability": "Int", "first_level_multiplier": 4}`.',
        "",
        "`spellcasting.spell_list` is this entry's own name exactly as spell",
        "records spell it in their own levels list. OMIT `spellcasting`",
        "ENTIRELY for a non-caster prestige class -- never write it as",
        "`null`.",
        "",
        "`description_sections` is one entry per printed flavor subsection",
        "this book actually prints -- `heading` verbatim, in the order",
        "printed.",
        "",
        "`text_md` is ONLY this entry's opening overview paragraph(s),",
        "before its first flavor subsection or its Requirements list --",
        "do NOT repeat `hit_die`, `requirements`, `class_skills`, the level",
        "table, or `class_features` there; the site renders all of those",
        "from `fields` directly.",
        "",
        '`source_pages` is `{"start": <first pdf page>, "end": <last pdf',
        "page>}` for this segment's own pdf page range (shown above under",
        '"## Segment").',
        "",
        "Never invent a value not stated in the text: OMIT the key entirely",
        "-- never write `null`. The one allowed exception: if the Special",
        "column names something with no description anywhere in this",
        "entry's own prose (a real, printed gap -- not your own omission),",
        "write its `class_features` entry anyway, with `text_md` as the",
        'empty string `""`, rather than inventing a description or leaving',
        "it out of `class_features` altogether -- every name printed in the",
        "Special column must have a matching `class_features` entry at its",
        "first level.",
    ],
}


def prompt_path_for(data_dir: Path, book_id: str, seg_id: str) -> Path:
    return data_dir / "prompts" / book_id / f"{seg_id}.md"


def _lookup_entry(book_id: str, manifest_path: Path | None) -> ManifestEntry | None:
    path = manifest_path if manifest_path is not None else default_manifest_path()
    try:
        entries = load_manifest(path)
    except ManifestError:
        return None
    return next((e for e in entries if e.book_id == book_id), None)


def _citation_prefix(book_id: str, entry: ManifestEntry | None) -> str:
    if entry is not None and entry.short_title:
        return entry.short_title
    return book_id.upper()


def _pdf_pages_str(segment: Segment) -> str:
    if len(segment.pages) == 1:
        return f"pdf p. {segment.pages[0]}"
    return f"pdf pp. {min(segment.pages)}-{max(segment.pages)}"


def _printed_pages_str(segment: Segment) -> str:
    """Human-readable printed page(s) for the "## Book" section. Falls back
    to the PDF page index (never the word "not detected") when no printed
    page number was recognized for this segment -- see `_example_citation`
    for the corresponding fallback in the actual citation instructions."""
    printed = [p for p in segment.printed_pages if p is not None]
    if not printed:
        return f"none printed -- {_pdf_pages_str(segment)}"
    if len(printed) == 1:
        return str(printed[0])
    return f"{min(printed)}-{max(printed)}"


def _example_citation(segment: Segment, citation_prefix: str) -> str:
    """The example `citation` value for the output contract. Per spec's
    "Printed page number versus PDF index" rule: if no printed page number
    was found, the citation falls back to "pdf p. <N>" (the raw PDF page
    index) instead of a book-prefixed printed-page citation."""
    printed = [p for p in segment.printed_pages if p is not None]
    if not printed:
        return _pdf_pages_str(segment)
    if len(printed) == 1:
        return f"{citation_prefix} p. {printed[0]}"
    return f"{citation_prefix} pp. {min(printed)}-{max(printed)}"


def _type_str(prop: dict[str, Any]) -> str:
    prop_type = prop.get("type", "any")
    if isinstance(prop_type, list):
        return "/".join(str(t) for t in prop_type)
    return str(prop_type)


def _render_schema_properties(schema: dict[str, Any], *, indent: int = 0) -> list[str]:
    """One Markdown bullet per property: name, type, required/optional, its
    description (falling back to the `x-ui` label), and any enum -- on the
    property itself or, for an array, on its `items` -- so an operator or
    subagent never has to open the schema file to see what's allowed.

    Recurses (at `indent + 1`) into a property's own nested properties: for
    an `array` property whose `items` is an `object` schema, the item's
    properties (e.g. `levels`' `class`/`level`) are listed indented under
    the array's own bullet; for an `object` property, its properties are
    listed indented the same way (e.g. `costs`' `material`/`focus`/`xp`)."""
    required = set(schema.get("required", []))
    prefix = "  " * indent
    lines: list[str] = []
    for name, prop in schema.get("properties", {}).items():
        if not isinstance(prop, dict):
            continue
        req = "required" if name in required else "optional"
        bits = [f"{prefix}- `{name}` ({_type_str(prop)}, {req})"]

        description = prop.get("description")
        x_ui = prop.get("x-ui")
        label = x_ui.get("label") if isinstance(x_ui, dict) else None
        if description:
            bits.append(f": {description}")
        elif label:
            bits.append(f": {label}")

        enum = prop.get("enum")
        if enum:
            bits.append(f" -- enum: {', '.join(str(e) for e in enum)}")

        items = prop.get("items")
        if isinstance(items, dict) and items.get("enum"):
            bits.append(f" -- each item enum: {', '.join(str(e) for e in items['enum'])}")

        lines.append("".join(bits))

        if isinstance(items, dict) and items.get("type") == "object" and items.get("properties"):
            lines.extend(_render_schema_properties(items, indent=indent + 1))
        if prop.get("type") == "object" and prop.get("properties"):
            lines.extend(_render_schema_properties(prop, indent=indent + 1))
    return lines


def _render_candidate_schema(kind_hint: str, registry: Registry) -> tuple[list[str], int | None]:
    if kind_hint not in registry.types:
        note = [
            f"No schema is registered yet for kind_hint `{kind_hint}` in this batch -- "
            "there is no candidate type for this segment. Respond with `no_content` "
            f"naming \"no schema registered for kind_hint '{kind_hint}'\" as the reason.",
        ]
        return note, None
    schema = registry.load_type_schema(kind_hint)
    version = registry.types[kind_hint].version
    lines = [
        f"`fields` schema for type `{kind_hint}` (schema_version {version}):",
        "",
        *_render_schema_properties(schema),
    ]
    return lines, version


def _procedure_lines(output_dir: Path) -> list[str]:
    """The file-writing procedure, rendered verbatim near the top of the
    prompt AND again just before the reply contract (see `render_prompt`).

    This exists because a live trial found subagents that read the prompt,
    composed a plausible `records` JSON reply, and never actually wrote the
    file -- so the procedure is stated as explicit, numbered steps (not
    folded into prose) and repeated immediately before the "How to respond"
    section, where a rushed reader is most likely to jump straight to
    composing the reply."""
    return [
        "STEP 1: Create each record file with your file-writing tool (Write)",
        f"at the absolute path shown below: {output_dir}",
        "STEP 2: Read the file back to confirm it exists and is valid JSON.",
        "STEP 3: Only then reply. A reply that names a file which does not",
        "exist is treated as a failure and the segment is retried on a more",
        "expensive model.",
        "",
        "Do not describe the record in your reply; the reply is only the",
        "JSON object.",
    ]


def _adjacent_seg_ids(data_dir: Path, book_id: str, seg_id: str) -> tuple[str | None, str | None]:
    """The previous/next segment id of the same book, by filename
    (book-order) sorting -- printed in the "## Segment" section and offered
    as the `needs_context` example, since a truncated entity almost always
    continues into one of these (batch B8)."""
    seg_dir = data_dir / "segments" / book_id
    if not seg_dir.is_dir():
        return None, None
    ids = sorted(p.stem for p in seg_dir.glob(f"{book_id}-*.json"))
    if seg_id not in ids:
        return None, None
    idx = ids.index(seg_id)
    prev_id = ids[idx - 1] if idx > 0 else None
    next_id = ids[idx + 1] if idx + 1 < len(ids) else None
    return prev_id, next_id


def _prior_attempts_lines(segment: Segment) -> list[str]:
    """A "## Prior attempts" section listing every attempt this segment has
    already made (tier, kind, and errors), followed by an explicit
    instruction not to repeat them -- rendered only when `segment.attempts`
    is non-empty, i.e. this prompt is for a retry (batch B8, criterion 2)."""
    if not segment.attempts:
        return []
    lines = [
        "## Prior attempts",
        "",
        "This segment has been attempted before and failed. Do not repeat",
        "any of the errors listed below:",
        "",
    ]
    for i, attempt in enumerate(segment.attempts, start=1):
        if not isinstance(attempt, dict):
            continue
        tier = attempt.get("tier", "?")
        kind = attempt.get("kind", "validation")
        errors = attempt.get("errors", [])
        lines.append(f"{i}. tier={tier}, kind={kind}:")
        for error in errors:
            lines.append(f"   - {error}")
    lines.append("")
    return lines


def _context_blocks(data_dir: Path, book_id: str, context_seg_ids: list[str]) -> list[str]:
    """One "### Adjacent context" block per id in `context_seg_ids`, each
    rendering that segment's own verbatim text -- rendered after the main
    segment text when `segment.context_seg_ids` is non-empty (batch B8,
    criterion 3: a `needs_context` retry's merged text). A missing segment
    file is silently skipped rather than erroring the whole prompt."""
    lines: list[str] = []
    for context_id in context_seg_ids:
        path = data_dir / "segments" / book_id / f"{context_id}.json"
        if not path.is_file():
            continue
        context_segment = Segment.model_validate_json(path.read_text())
        pdf_page = context_segment.pages[0] if context_segment.pages else "?"
        lines += [
            f"### Adjacent context (segment {context_id}, pdf p. {pdf_page})",
            "",
            "The entity you are extracting may continue into this adjacent",
            "segment's own text:",
            "",
            "```",
            context_segment.text,
            "```",
            "",
        ]
    return lines


def _kind_rules_lines(kind_hint: str) -> list[str]:
    """The "## Extraction rules for `<kind>`" section for `kind_hint`, or
    `[]` if no per-kind rules are registered for it (e.g. `stat_block`,
    which also has no schema yet -- see `_KIND_RULES`)."""
    rules = _KIND_RULES.get(kind_hint)
    if rules is None:
        return []
    return [f"## Extraction rules for `{kind_hint}`", "", *rules, ""]


def _table_convention_lines(
    data_dir: Path, book_id: str, kind_hint: str, *, registry: Registry
) -> list[str]:
    """The shared "tables belonging to this entity" convention (criterion
    3): for any kind other than `table` itself, a subagent that finds a
    tab-separated table in its segment's text belonging to the entity it is
    extracting writes a SECOND record -- a `table` record -- alongside the
    entity's own, and cross-links the two. Prints both absolute output
    directories so the subagent never has to guess the table's own output
    path. Omitted entirely for a `table` segment itself, which has nothing
    else to cross-link to.

    Since the subagent is being told to write a record of a DIFFERENT type
    than the one the rest of this prompt is about, this also renders the
    `table` type's own `fields` schema, its own `schema_version`, its own
    extraction rules, and a complete example table record -- exactly as
    `render_prompt` does for the segment's own `kind_hint` -- so the
    subagent never has to invent the table record's shape. Guarded on
    `"table" in registry.types` so a custom `$OWLSPERCH_SCHEMAS` without a
    `table` type still renders a prompt instead of crashing."""
    if kind_hint == "table":
        return []
    table_output_dir = (data_dir / "records" / book_id / "table").resolve()
    lines = [
        "### Tables belonging to this entity",
        "",
        "If this segment's text contains a table belonging to the entity",
        "you are extracting (tab-separated rows), write a SECOND record --",
        "a `table` record -- alongside the entity's own, to this exact",
        "directory (create it if it does not exist yet), named",
        "`<slug>.json` the same way:",
        "",
        f"    {table_output_dir}",
        "",
        "- Set the table record's `fields.parent_record` to the owning",
        "  record's `id`.",
        "- Add the table record's own `id`",
        "  (`table:<book_id>:<table-slug>`) to the owning record's `tables`",
        "  array.",
        "- List BOTH file paths in your reply's `records` array.",
        "",
    ]
    if "table" not in registry.types:
        return lines
    table_schema = registry.load_type_schema("table")
    table_version = registry.types["table"].version
    lines += [
        "#### `fields` schema for the table record you write",
        "",
        *_render_schema_properties(table_schema),
        "",
        "The table record's own `schema_version` is the current registered",
        f"version for type `table`: {table_version} -- NOT the same value as",
        "the owning record's own `schema_version` above; look it up",
        "separately for each record you write.",
        "",
        "#### Table extraction rules",
        "",
        *_KIND_RULES["table"],
        "",
    ]
    table_example = _load_example_record("table", registry)
    if table_example is not None:
        lines += [
            "#### Example table record",
            "",
            "A complete, schema-valid example table record (an invented",
            "table, not copied from any real book) -- the table record you",
            "write must have exactly this shape:",
            "",
            "```json",
            json.dumps(table_example, indent=2),
            "```",
            "",
        ]
    return lines


def _load_example_record(kind_hint: str, registry: Registry) -> dict[str, Any] | None:
    """The invented, schema-valid `schemas/examples/<kind_hint>.json` fixture
    for `kind_hint`, if one exists (only `spell` has one this batch)."""
    path = registry.schemas_dir / "examples" / f"{kind_hint}.json"
    if not path.is_file():
        return None
    raw: dict[str, Any] = json.loads(path.read_text())
    return raw


def render_prompt(
    segment: Segment,
    *,
    data_dir: Path,
    manifest_path: Path | None = None,
    schemas_dir: Path | None = None,
    model: str = DEFAULT_MODEL,
) -> str:
    entry = _lookup_entry(segment.book_id, manifest_path)
    title = entry.title if entry is not None else segment.book_id
    citation_prefix = _citation_prefix(segment.book_id, entry)
    prev_seg_id, next_seg_id = _adjacent_seg_ids(data_dir, segment.book_id, segment.seg_id)

    registry = load_registry(schemas_dir)
    output_dir = (data_dir / "records" / segment.book_id / segment.kind_hint).resolve()
    envelope_lines = _render_schema_properties(registry.envelope_schema)
    candidate_lines, schema_version = _render_candidate_schema(segment.kind_hint, registry)
    example_record = _load_example_record(segment.kind_hint, registry)

    example_id = f"{segment.kind_hint}:{segment.book_id}:<slug>"
    example_citation = _example_citation(segment, citation_prefix)
    extraction_example = json.dumps(
        {
            "tier": segment.tier,
            "model": model,
            "segment_id": segment.seg_id,
            "timestamp": "<ISO-8601 UTC timestamp, e.g. 2026-09-12T18:00:00+00:00>",
        }
    )
    example_result = json.dumps(
        {
            "seg_id": segment.seg_id,
            "records": [f"records/{segment.book_id}/{segment.kind_hint}/<slug>.json"],
            "no_content": None,
            "notes": ["free-text notes, e.g. an unnamed_entity note -- [] if none"],
        }
    )
    example_no_content = json.dumps(
        {
            "seg_id": segment.seg_id,
            "records": [],
            "no_content": {"reason": "why nothing was extracted, e.g. 'table of contents entry'"},
            "notes": [],
        }
    )
    example_needs_context = json.dumps(
        {
            "seg_id": segment.seg_id,
            "records": [],
            "no_content": None,
            "needs_context": [next_seg_id or prev_seg_id or "<adjacent-seg-id>"],
            "notes": [],
        }
    )
    example_proposed_type = json.dumps(
        {
            "seg_id": segment.seg_id,
            "records": [],
            "no_content": None,
            "proposed_type": {
                "name": "<a short name for the new type>",
                "reason": "<why no existing schema fits this segment>",
            },
            "notes": [],
        }
    )
    schema_version_line = (
        f"- `schema_version` is the current registered version for this type: {schema_version}."
        if schema_version is not None
        else "- there is no registered schema for this kind_hint yet; respond `no_content`."
    )

    lines = [
        f"# Extraction task: {segment.seg_id}",
        "",
        "You are extracting structured D&D 3.5e reference data from one segment",
        "of already-extracted rulebook text, so it can be validated against a",
        "JSON Schema and stored as a queryable record. Follow this procedure",
        "exactly.",
        "",
        "## Procedure (read this first)",
        "",
        *_procedure_lines(output_dir),
        "",
        "## Book",
        "",
        f"- Title: {title}",
        f"- Book ID: {segment.book_id}",
        f"- Printed page(s) for this segment: {_printed_pages_str(segment)}",
        f"- Extraction model for this task: {model}",
        "",
        *_prior_attempts_lines(segment),
        "## Segment",
        "",
        f"- Segment ID: {segment.seg_id}",
        f"- Kind hint: {segment.kind_hint}",
        f"- Heading: {segment.heading}",
        f"- Previous segment: {prev_seg_id if prev_seg_id else '(none)'}",
        f"- Next segment: {next_seg_id if next_seg_id else '(none)'}",
        "",
        "### Segment text (verbatim)",
        "",
        "```",
        segment.text,
        "```",
        "",
        *_context_blocks(data_dir, segment.book_id, segment.context_seg_ids),
        "## Candidate schema: record envelope (every record's common fields)",
        "",
        *envelope_lines,
        "",
        "## Candidate schema: `fields` for this segment's kind",
        "",
        *candidate_lines,
        "",
        *_kind_rules_lines(segment.kind_hint),
    ]

    if example_record is not None:
        lines += [
            "## EXAMPLE RECORD",
            "",
            "A complete, schema-valid example record for this type (an invented",
            "spell, not copied from any real book) -- your own output must have",
            "exactly this shape:",
            "",
            "```json",
            json.dumps(example_record, indent=2),
            "```",
            "",
        ]

    lines += [
        "## Output contract",
        "",
        "For every distinct entity you find in the segment text, write ONE JSON",
        "file per entity to this exact directory (create it if it does not",
        "exist yet), named `<slug>.json`:",
        "",
        f"    {output_dir}",
        "",
        f"- `id` is `<type>:<book_id>:<slug>`, e.g. `{example_id}`.",
        "- `slug` is the ASCII-folded kebab-case of the entity's `name`,",
        "  VERBATIM in the name's own word order -- never reorder words, not",
        '  even a trailing ", Greater"/", Lesser"/", Mass" suffix, e.g.',
        '  "Glyph of Warding, Greater" -> `glyph-of-warding-greater`, never',
        "  `greater-glyph-of-warding`: lowercase it, fold accented characters",
        "  to plain ASCII, remove every apostrophe with NO hyphen in its",
        '  place (e.g. "Leomund\'s Tiny Hut" -> `leomunds-tiny-hut`, never',
        "  `leomund-s-tiny-hut`), replace every remaining run of characters",
        "  that are not `a-z0-9` with a single `-`, and trim leading/trailing",
        "  `-`. An en dash or em dash counts as a hyphen for this purpose",
        '  (e.g. "Table 3–8: The Druid" -> `table-3-8-the-druid`), not as',
        "  a character to drop.",
        "- `aliases` is a list of alternate spellings or names for the entity",
        "  found in the segment text (e.g. a non-ASCII spelling, or a name the",
        "  text also uses elsewhere) -- use `[]` if the text gives none.",
        "- `pages` is set authoritatively by the pipeline, not by you: after",
        "  you respond, `owlsperch queue complete` overwrites it with this",
        "  segment's own `pages` list (PDF page indices) -- currently",
        f"  `{json.dumps(segment.pages)}` -- exactly like it already does for",
        "  `extraction`. You may leave whatever value you write here",
        "  unchanged from any example you were shown; it does not need to",
        "  be correct.",
        "  **`pages` are PDF page indices, not the printed page number shown",
        "  in the book text -- never substitute one for the other if you do",
        "  fill it in.** For example, if this segment's `pages` were",
        "  `[197, 198]` and the printed page number visible in the text",
        '  were 196, the record would end up with `"pages": [197, 198]`',
        "  (the PDF indices, copied verbatim from the segment by the",
        '  pipeline) even though `"citation": "PHB p. 196"` cites the',
        "  printed page number instead.",
        f'- `citation` follows the pattern "{citation_prefix} p. <printed page>"',
        f'  (e.g. "{example_citation}"), or "{citation_prefix} pp. <A>-<B>" if',
        "  the entity spans more than one printed page. Use the printed page",
        "  number(s) the entity itself is on, not necessarily every page of",
        "  this segment. If no printed page number was detected for this",
        '  segment (see "Printed page(s)" above), cite it as "pdf p. <N>"',
        "  using the raw PDF page index instead -- never invent a printed",
        "  page number.",
        "- `text_md` is the entity's rule text rewritten as faithful",
        "  Markdown, not a summary of it -- reproduced faithfully, not",
        '  paraphrased. See the "Extraction rules for `'
        + segment.kind_hint
        + "`\" section above for exactly what counts as this kind's",
        "  `text_md` and what must NOT be repeated there.",
        "- Never invent a name. If a stat block in this segment has no name",
        "  anywhere in the segment text (e.g. a second, unlabeled stat block",
        "  immediately preceding a later, named one), skip writing a record",
        "  for it entirely and report it in `notes` as",
        "  `unnamed_entity: <first 60 chars of its text>`.",
        "",
        *_table_convention_lines(data_dir, segment.book_id, segment.kind_hint, registry=registry),
        schema_version_line,
        "- `extraction` is exactly:",
        "",
        f"      {extraction_example}",
        "",
        "  These exact values don't matter -- `owlsperch queue complete`",
        "  overwrites `extraction` authoritatively with the real tier, model,",
        "  segment_id, and timestamp once you respond, so placeholder values",
        "  here are fine.",
        "",
        "- Never invent fields that are not in the schema above -- both schemas",
        "  reject unknown properties (`additionalProperties: false`). If you",
        "  have no supported value for a `fields` property, OMIT it",
        "  entirely -- **never write it as `null`.** Writing `null` for a",
        "  value you don't have is exactly as wrong as inventing one; leave",
        "  the key out of the JSON object altogether. (Envelope build-time",
        "  keys -- `canonical`, `variant_of`, `applied_overrides`,",
        "  `macro_eligible`, `aliases`, `tables` -- may simply be omitted",
        "  too; the pipeline fills them in.)",
        "",
        "## Procedure (repeated -- do this before you reply)",
        "",
        *_procedure_lines(output_dir),
        "",
        "## How to respond",
        "",
        "Your final message must be exactly one JSON object and nothing else --",
        "no prose before or after it, and no Markdown code fence around it:",
        "",
        f"    {example_result}",
        "",
        "or, if this segment has no extractable entity (art, a blank page, a",
        "table of contents entry, a cross-reference with no rule text of its",
        "own, ...):",
        "",
        f"    {example_no_content}",
        "",
        "or, if (and only if) the entity is clearly truncated at the very",
        "start or end of the segment text -- it's cut off mid-stat-block or",
        "mid-sentence, not merely short -- name the adjacent segment id it",
        'continues in/from (the "Previous segment"/"Next segment" ids',
        "printed above; never invent one):",
        "",
        f"    {example_needs_context}",
        "",
        "or, if no candidate schema above fits this segment's entity at all",
        "(not just a field or two missing -- the whole shape is wrong):",
        "",
        f"    {example_proposed_type}",
        "",
    ]
    return "\n".join(lines)


def render_prompt_to_file(
    segment: Segment,
    *,
    data_dir: Path,
    manifest_path: Path | None = None,
    schemas_dir: Path | None = None,
    model: str = DEFAULT_MODEL,
) -> Path:
    text = render_prompt(
        segment,
        data_dir=data_dir,
        manifest_path=manifest_path,
        schemas_dir=schemas_dir,
        model=model,
    )
    path = prompt_path_for(data_dir, segment.book_id, segment.seg_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, text)
    return path
