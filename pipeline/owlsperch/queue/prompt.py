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
against its own schema).

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
        "## Segment",
        "",
        f"- Segment ID: {segment.seg_id}",
        f"- Kind hint: {segment.kind_hint}",
        f"- Heading: {segment.heading}",
        "",
        "### Segment text (verbatim)",
        "",
        "```",
        segment.text,
        "```",
        "",
        "## Candidate schema: record envelope (every record's common fields)",
        "",
        *envelope_lines,
        "",
        "## Candidate schema: `fields` for this segment's kind",
        "",
        *candidate_lines,
        "",
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
        "- `slug` is the ASCII-folded kebab-case of the entity's `name`:",
        "  lowercase it, strip apostrophes, fold accented characters to plain",
        "  ASCII, replace every run of characters that are not `a-z0-9` with a",
        "  single `-`, and trim leading/trailing `-`.",
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
        "- `text_md` is the entity's full rule text rewritten as faithful",
        "  Markdown, not a summary of it:",
        "  - its stat-block lines (Level, Components, Casting Time, Range,",
        "    Target/Effect/Area, Duration, Saving Throw, Spell Resistance,",
        "    etc.) become a bulleted list, each item bold-labeled, e.g.",
        "    `**Level:** Sor/Wiz 3`;",
        "  - followed by the description paragraph(s), reproduced faithfully;",
        "  - any tab-separated lines in the segment text (a table row) become",
        "    a Markdown table (`| cell | cell | ... |` with a header",
        "    separator row).",
        "- Never invent a name. If a stat block in this segment has no name",
        "  anywhere in the segment text (e.g. a second, unlabeled stat block",
        "  immediately preceding a later, named one), skip writing a record",
        "  for it entirely and report it in `notes` as",
        "  `unnamed_entity: <first 60 chars of its text>`.",
        "- `levels[].class` must use the FULL class name, never the",
        "  stat block's abbreviation -- expand every abbreviation using this",
        "  table:",
        "",
        *[f"  - `{abbr}` -> `{full}`" for abbr, full in CLASS_ABBREVIATIONS.items()],
        "",
        '  `"Sor/Wiz N"` (or any other "/"-joined run of abbreviations) becomes',
        "  TWO entries, one per class, both at the same level -- e.g.",
        '  `"Sor/Wiz 3"` becomes `{"class": "Sorcerer", "level": 3}` and',
        '  `{"class": "Wizard", "level": 3}`, never a single combined entry.',
        "  Anything not in the table above (a cleric domain like `Air` or",
        "  `Fire`, or a prestige class) is kept exactly as written, with only",
        "  its first letter capitalized.",
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
        "  reject unknown properties (`additionalProperties: false`). Omit a",
        "  field (or use its documented default) if the text does not support",
        "  a value for it, rather than guessing.",
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
