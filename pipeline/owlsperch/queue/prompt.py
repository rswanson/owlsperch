"""Rendering the subagent extraction prompt for `owlsperch queue prompt
<seg_id>` (and `queue next`, which renders one for every segment it selects),
per spec 4.5 and B5 acceptance criterion 2.

Everything the prompt asserts about a schema (required fields, enums,
descriptions) is generated from the actual `schemas/*.json` files via
`owlsperch.schemas.load_registry` -- never hand-copied -- so a schema edit
(spec 4.14's schema-growth mechanism) is picked up automatically the next
time a prompt is rendered.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from owlsperch.fsutil import atomic_write_text
from owlsperch.manifest import ManifestEntry, ManifestError, default_manifest_path, load_manifest
from owlsperch.schemas import Registry, load_registry
from owlsperch.segment.runner import Segment


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


def _printed_pages_str(segment: Segment) -> str:
    printed = [p for p in segment.printed_pages if p is not None]
    if not printed:
        return "not detected for this segment's page(s)"
    if len(printed) == 1:
        return str(printed[0])
    return f"{min(printed)}-{max(printed)}"


def _type_str(prop: dict[str, Any]) -> str:
    prop_type = prop.get("type", "any")
    if isinstance(prop_type, list):
        return "/".join(str(t) for t in prop_type)
    return str(prop_type)


def _render_schema_properties(schema: dict[str, Any]) -> list[str]:
    """One Markdown bullet per property: name, type, required/optional, its
    description (falling back to the `x-ui` label), and any enum -- on the
    property itself or, for an array, on its `items` -- so an operator or
    subagent never has to open the schema file to see what's allowed."""
    required = set(schema.get("required", []))
    lines: list[str] = []
    for name, prop in schema.get("properties", {}).items():
        if not isinstance(prop, dict):
            continue
        req = "required" if name in required else "optional"
        bits = [f"- `{name}` ({_type_str(prop)}, {req})"]

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


def render_prompt(
    segment: Segment,
    *,
    data_dir: Path,
    manifest_path: Path | None = None,
    schemas_dir: Path | None = None,
) -> str:
    entry = _lookup_entry(segment.book_id, manifest_path)
    title = entry.title if entry is not None else segment.book_id
    citation_prefix = _citation_prefix(segment.book_id, entry)

    registry = load_registry(schemas_dir)
    output_dir = (data_dir / "records" / segment.book_id / segment.kind_hint).resolve()
    envelope_lines = _render_schema_properties(registry.envelope_schema)
    candidate_lines, schema_version = _render_candidate_schema(segment.kind_hint, registry)

    example_id = f"{segment.kind_hint}:{segment.book_id}:<slug>"
    example_citation = f"{citation_prefix} p. {_printed_pages_str(segment)}"
    extraction_example = json.dumps(
        {
            "tier": segment.tier,
            "model": "<your own model name, e.g. claude-haiku-4-5>",
            "segment_id": segment.seg_id,
            "timestamp": "<ISO-8601 UTC timestamp, e.g. 2026-09-12T18:00:00+00:00>",
        }
    )
    example_result = json.dumps(
        {
            "seg_id": segment.seg_id,
            "records": [f"records/{segment.book_id}/{segment.kind_hint}/<slug>.json"],
            "no_content": None,
            "notes": "free-text notes, or an empty string",
        }
    )
    example_no_content = json.dumps(
        {
            "seg_id": segment.seg_id,
            "records": [],
            "no_content": {"reason": "why nothing was extracted, e.g. 'table of contents entry'"},
            "notes": "",
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
        "## Book",
        "",
        f"- Title: {title}",
        f"- Book ID: {segment.book_id}",
        f"- Printed page(s) for this segment: {_printed_pages_str(segment)}",
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
        f'- `citation` follows the pattern "{citation_prefix} p. <printed page>"',
        f'  (e.g. "{example_citation}"), or "{citation_prefix} pp. <A>-<B>" if',
        "  the entity spans more than one printed page. Use the printed page",
        "  number(s) the entity itself is on, not necessarily every page of",
        "  this segment.",
        "- `text_md` is the entity's rule text rewritten as faithful Markdown --",
        "  reproduce the rules text, not a summary of it.",
        schema_version_line,
        "- `extraction` is exactly:",
        "",
        f"      {extraction_example}",
        "",
        "- Never invent fields that are not in the schema above -- both schemas",
        "  reject unknown properties (`additionalProperties: false`). Omit a",
        "  field (or use its documented default) if the text does not support",
        "  a value for it, rather than guessing.",
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
) -> Path:
    text = render_prompt(
        segment, data_dir=data_dir, manifest_path=manifest_path, schemas_dir=schemas_dir
    )
    path = prompt_path_for(data_dir, segment.book_id, segment.seg_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, text)
    return path
