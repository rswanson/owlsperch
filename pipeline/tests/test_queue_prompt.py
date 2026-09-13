"""Tests for `owlsperch.queue.prompt` (and `owlsperch queue prompt <seg_id>`),
per B5 acceptance criterion 2: the rendered prompt must contain the segment
text verbatim, the kind hint, book metadata, the candidate schema(s)
(rendered from the JSON, not hand-copied), the exact output contract
(absolute output directory, id/slug/citation rules, extraction block, schema
version), the exact response contract, and the text_md / no-invented-fields
instructions.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from owlsperch.queue.prompt import prompt_path_for, render_prompt, render_prompt_to_file
from owlsperch.schemas import Registry, load_registry
from owlsperch.segment.runner import Segment


def _repo_schemas_dir() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "schemas"


def _segment(**overrides: object) -> Segment:
    defaults: dict[str, Any] = dict(
        seg_id="phb1-p0257-04",
        book_id="phb1",
        pages=[257],
        printed_pages=[256],
        kind_hint="spell",
        heading="Mount",
        text=(
            "Mount\n\nConjuration (Summoning) Level: Sor/Wiz 1 Components: V, S, M "
            "Casting Time: 1 round Range: Close (25 ft. + 5 ft./2 levels) "
            "Effect: One mount Duration: 2 hours/level (D) Saving Throw: None "
            "Spell Resistance: No\n\nYou summon a light horse or a pony."
        ),
        status="in_progress",
        tier="haiku",
        created_at="2026-01-01T00:00:00+00:00",
    )
    defaults.update(overrides)
    return Segment(**defaults)


def _write_manifest(tmp_path: Path, *, short_title: str | None = "PHB") -> Path:
    manifest_path = tmp_path / "manifest.yaml"
    short_title_line = f'    short_title: "{short_title}"\n' if short_title else ""
    manifest_path.write_text(
        f"""
entries:
  - book_id: phb1
    title: "Player's Handbook (Core Rulebook I)"
{short_title_line}    file: "phb1.pdf"
    edition: "3.5"
    kind: rulebook
"""
    )
    return manifest_path


def test_prompt_contains_segment_text_verbatim(tmp_path: Path) -> None:
    segment = _segment()
    manifest_path = _write_manifest(tmp_path)

    text = render_prompt(
        segment,
        data_dir=tmp_path / "data",
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
    )

    assert segment.text in text


def test_prompt_contains_kind_hint_and_book_metadata(tmp_path: Path) -> None:
    segment = _segment()
    manifest_path = _write_manifest(tmp_path)

    text = render_prompt(
        segment,
        data_dir=tmp_path / "data",
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
    )

    assert "spell" in text
    assert "phb1" in text
    assert "Player's Handbook (Core Rulebook I)" in text
    assert "256" in text  # printed page number


def test_prompt_renders_schema_enum_values_from_json(tmp_path: Path) -> None:
    segment = _segment()
    manifest_path = _write_manifest(tmp_path)

    text = render_prompt(
        segment,
        data_dir=tmp_path / "data",
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
    )

    # Spell schema's `school` enum -- proves the schema is rendered from the
    # JSON file, not hand-copied into the prompt template.
    assert "Evocation" in text
    assert "Abjuration" in text
    assert "Universal" in text
    # Components item enum.
    assert "DF" in text and "XP" in text


def test_prompt_contains_absolute_output_directory(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    segment = _segment()
    manifest_path = _write_manifest(tmp_path)

    text = render_prompt(
        segment, data_dir=data_dir, manifest_path=manifest_path, schemas_dir=_repo_schemas_dir()
    )

    expected_dir = (data_dir / "records" / "phb1" / "spell").resolve()
    assert str(expected_dir) in text
    assert expected_dir.is_absolute()


def test_prompt_contains_exact_response_contract(tmp_path: Path) -> None:
    segment = _segment()
    manifest_path = _write_manifest(tmp_path)

    text = render_prompt(
        segment,
        data_dir=tmp_path / "data",
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
    )

    assert '"seg_id"' in text
    assert '"records"' in text
    assert '"no_content"' in text
    assert '"notes"' in text


def test_prompt_contains_id_slug_and_citation_rules(tmp_path: Path) -> None:
    segment = _segment()
    manifest_path = _write_manifest(tmp_path)

    text = render_prompt(
        segment,
        data_dir=tmp_path / "data",
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
    )

    assert "spell:phb1:<slug>" in text
    assert "kebab-case" in text
    assert "PHB p." in text  # short_title-derived citation prefix


def test_prompt_uses_book_id_upper_when_no_short_title(tmp_path: Path) -> None:
    segment = _segment(book_id="testbook", seg_id="testbook-p0001-01")
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text(
        """
entries:
  - book_id: testbook
    title: "Test Book"
    file: "testbook.pdf"
    edition: "3.5"
    kind: rulebook
"""
    )

    text = render_prompt(
        segment,
        data_dir=tmp_path / "data",
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
    )

    assert "TESTBOOK p." in text


def test_prompt_contains_extraction_block_and_schema_version_instructions(
    tmp_path: Path,
) -> None:
    segment = _segment()
    manifest_path = _write_manifest(tmp_path)

    text = render_prompt(
        segment,
        data_dir=tmp_path / "data",
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
    )

    assert '"tier"' in text and "haiku" in text
    assert '"segment_id"' in text and "phb1-p0257-04" in text
    assert "schema_version" in text
    assert '"model"' in text
    assert '"timestamp"' in text


def test_prompt_instructs_faithful_markdown_and_no_invented_fields(tmp_path: Path) -> None:
    segment = _segment()
    manifest_path = _write_manifest(tmp_path)

    text = render_prompt(
        segment,
        data_dir=tmp_path / "data",
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
    )

    assert "text_md" in text
    assert "not a summary" in text.lower() or "not a summary" in text
    assert "never invent" in text.lower()


def test_prompt_path_for_matches_data_dir_prompts_layout(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    assert prompt_path_for(data_dir, "phb1", "phb1-p0257-04") == (
        data_dir / "prompts" / "phb1" / "phb1-p0257-04.md"
    )


def test_render_prompt_to_file_writes_file_and_returns_path(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    segment = _segment()
    manifest_path = _write_manifest(tmp_path)

    path = render_prompt_to_file(
        segment, data_dir=data_dir, manifest_path=manifest_path, schemas_dir=_repo_schemas_dir()
    )

    assert path == data_dir / "prompts" / "phb1" / "phb1-p0257-04.md"
    assert path.is_file()
    assert segment.text in path.read_text()


def test_prompt_renders_nested_array_item_schema_for_levels(tmp_path: Path) -> None:
    segment = _segment()
    manifest_path = _write_manifest(tmp_path)

    text = render_prompt(
        segment,
        data_dir=tmp_path / "data",
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
    )

    levels_index = text.index("`levels`")
    class_index = text.index("`class`")
    level_index = text.index("`level`", class_index)
    # Both nested item properties are rendered indented right after the
    # `levels` bullet itself (not, say, in an unrelated place in the file).
    assert levels_index < class_index < level_index
    next_top_level_bullet = text.index("`components`")
    assert class_index < next_top_level_bullet
    assert level_index < next_top_level_bullet


def test_prompt_contains_example_record_for_spell(tmp_path: Path) -> None:
    segment = _segment()
    manifest_path = _write_manifest(tmp_path)

    text = render_prompt(
        segment,
        data_dir=tmp_path / "data",
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
    )

    assert "EXAMPLE RECORD" in text
    # The example fixture's invented spell shows up verbatim, and its
    # `levels` array's keys are the quoted JSON keys (not the schema's
    # backtick-rendered nested property bullets).
    assert "Sable Bloom" in text
    assert '"class"' in text
    assert '"level"' in text


def test_prompt_omits_example_record_section_for_kind_without_one(tmp_path: Path) -> None:
    segment = _segment(kind_hint="stat_block")
    manifest_path = _write_manifest(tmp_path)

    text = render_prompt(
        segment,
        data_dir=tmp_path / "data",
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
    )

    assert "EXAMPLE RECORD" not in text


def test_prompt_citation_falls_back_to_pdf_page_when_not_detected(tmp_path: Path) -> None:
    segment = _segment(printed_pages=[None], pages=[197])
    manifest_path = _write_manifest(tmp_path)

    text = render_prompt(
        segment,
        data_dir=tmp_path / "data",
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
    )

    assert "pdf p. 197" in text
    assert "not detected" not in text.lower()


def test_prompt_default_model_is_claude_haiku_4_5(tmp_path: Path) -> None:
    segment = _segment()
    manifest_path = _write_manifest(tmp_path)

    text = render_prompt(
        segment,
        data_dir=tmp_path / "data",
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
    )

    assert '"model": "claude-haiku-4-5"' in text


def test_prompt_honors_explicit_model_argument(tmp_path: Path) -> None:
    segment = _segment()
    manifest_path = _write_manifest(tmp_path)

    text = render_prompt(
        segment,
        data_dir=tmp_path / "data",
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
        model="claude-opus-4-6",
    )

    assert '"model": "claude-opus-4-6"' in text
    # The "## Book" header line renders the same explicit override too --
    # not just the extraction block (the EXAMPLE RECORD fixture's own
    # `extraction.model` is a separate, unrelated occurrence of the default
    # string, so it isn't asserted against here).
    assert "Extraction model for this task: claude-opus-4-6" in text


def test_prompt_instructs_aliases_pages_and_unnamed_entity_rule(tmp_path: Path) -> None:
    segment = _segment()
    manifest_path = _write_manifest(tmp_path)

    text = render_prompt(
        segment,
        data_dir=tmp_path / "data",
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
    )

    assert "alternate spellings" in text
    assert "`pages` is set authoritatively by the pipeline" in text
    assert "Never invent a name" in text
    assert "unnamed_entity:" in text


def test_prompt_states_pages_are_pdf_indices_with_literal_example(tmp_path: Path) -> None:
    segment = _segment()
    manifest_path = _write_manifest(tmp_path)

    text = render_prompt(
        segment,
        data_dir=tmp_path / "data",
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
    )

    assert '"pages": [197, 198]' in text
    assert '"citation": "PHB p. 196"' in text
    assert "PDF page indices" in text
    assert "verbatim" in text.lower()


def test_prompt_contains_class_abbreviation_table(tmp_path: Path) -> None:
    segment = _segment()
    manifest_path = _write_manifest(tmp_path)

    text = render_prompt(
        segment,
        data_dir=tmp_path / "data",
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
    )

    assert "`Adp` -> `Adept`" in text
    assert "`Sor` -> `Sorcerer`" in text
    assert "`Wiz` -> `Wizard`" in text
    assert "`Clr` -> `Cleric`" in text
    assert "TWO entries" in text
    assert "Sorcerer" in text and "Wizard" in text
    assert "prestige class" in text.lower()


def test_prompt_says_extraction_values_are_placeholders(tmp_path: Path) -> None:
    segment = _segment()
    manifest_path = _write_manifest(tmp_path)

    text = render_prompt(
        segment,
        data_dir=tmp_path / "data",
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
    )

    assert "placeholder" in text.lower()
    assert "queue complete" in text.lower()
    assert "authoritatively" in text.lower() or "overwrites" in text.lower()


def test_prompt_procedure_section_instructs_write_then_read_back(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    segment = _segment()
    manifest_path = _write_manifest(tmp_path)

    text = render_prompt(
        segment, data_dir=data_dir, manifest_path=manifest_path, schemas_dir=_repo_schemas_dir()
    )

    expected_dir = (data_dir / "records" / "phb1" / "spell").resolve()

    # Rendered near the top, before the segment text / schemas, so a reader
    # cannot miss it.
    procedure_index = text.index("## Procedure")
    book_index = text.index("## Book")
    assert procedure_index < book_index

    procedure_section = text[procedure_index:book_index]
    assert "Read the file back" in procedure_section
    assert str(expected_dir) in procedure_section
    assert "STEP 1" in procedure_section and "STEP 2" in procedure_section
    assert "STEP 3" in procedure_section
    assert "retried on a more" in procedure_section
    assert "Do not describe the record in your reply" in procedure_section

    # Repeated again immediately before the reply contract.
    respond_index = text.index("## How to respond")
    second_procedure_index = text.rindex("## Procedure", 0, respond_index)
    second_procedure_section = text[second_procedure_index:respond_index]
    assert "Read the file back" in second_procedure_section
    assert str(expected_dir) in second_procedure_section
    assert second_procedure_index > procedure_index


def test_prompt_slug_rule_preserves_word_order_and_apostrophe_no_hyphen(tmp_path: Path) -> None:
    segment = _segment()
    manifest_path = _write_manifest(tmp_path)

    text = render_prompt(
        segment,
        data_dir=tmp_path / "data",
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
    )

    assert "VERBATIM" in text
    assert "glyph-of-warding-greater" in text
    assert "leomunds-tiny-hut" in text


# ---------------------------------------------------------------------------
# Batch B8: retries (prior attempts), adjacent context, previous/next
# segment ids, needs_context/proposed_type contract.
# ---------------------------------------------------------------------------


def test_prompt_omits_prior_attempts_section_when_no_attempts(tmp_path: Path) -> None:
    segment = _segment()
    manifest_path = _write_manifest(tmp_path)

    text = render_prompt(
        segment,
        data_dir=tmp_path / "data",
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
    )

    assert "## Prior attempts" not in text


def test_prompt_renders_prior_attempts_section_for_a_retry(tmp_path: Path) -> None:
    segment = _segment(
        tier="sonnet",
        attempts=[
            {
                "tier": "haiku",
                "timestamp": "2026-01-01T00:00:00+00:00",
                "errors": ["fields.school: 'Not A School' is not one of [...]"],
                "kind": "validation",
            }
        ],
    )
    manifest_path = _write_manifest(tmp_path)

    text = render_prompt(
        segment,
        data_dir=tmp_path / "data",
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
    )

    prior_index = text.index("## Prior attempts")
    segment_index = text.index("## Segment")
    assert prior_index < segment_index
    prior_section = text[prior_index:segment_index]
    assert "tier=haiku" in prior_section
    assert "kind=validation" in prior_section
    assert "fields.school" in prior_section
    assert "do not repeat" in prior_section.lower()


def test_prompt_renders_previous_and_next_segment_ids(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    for seg_id, page in [("phb1-p0256-01", 256), ("phb1-p0257-04", 257), ("phb1-p0258-01", 258)]:
        seg = _segment(seg_id=seg_id, pages=[page], printed_pages=[page])
        seg_dir = data_dir / "segments" / "phb1"
        seg_dir.mkdir(parents=True, exist_ok=True)
        (seg_dir / f"{seg_id}.json").write_text(seg.model_dump_json(indent=2))

    text = render_prompt(
        _segment(),  # seg_id phb1-p0257-04, the middle one
        data_dir=data_dir,
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
    )

    assert "- Previous segment: phb1-p0256-01" in text
    assert "- Next segment: phb1-p0258-01" in text


def test_prompt_previous_and_next_segment_ids_none_when_at_the_edges(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    seg_dir = data_dir / "segments" / "phb1"
    seg_dir.mkdir(parents=True, exist_ok=True)
    (seg_dir / "phb1-p0257-04.json").write_text(_segment().model_dump_json(indent=2))

    text = render_prompt(
        _segment(), data_dir=data_dir, manifest_path=manifest_path, schemas_dir=_repo_schemas_dir()
    )

    assert "- Previous segment: (none)" in text
    assert "- Next segment: (none)" in text


def test_prompt_renders_adjacent_context_block_for_context_seg_ids(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    context_seg = _segment(
        seg_id="phb1-p0258-01", pages=[258], printed_pages=[257], text="Continuation text here."
    )
    seg_dir = data_dir / "segments" / "phb1"
    seg_dir.mkdir(parents=True, exist_ok=True)
    (seg_dir / "phb1-p0258-01.json").write_text(context_seg.model_dump_json(indent=2))
    (seg_dir / "phb1-p0257-04.json").write_text(_segment().model_dump_json(indent=2))
    segment = _segment(context_seg_ids=["phb1-p0258-01"])

    text = render_prompt(
        segment, data_dir=data_dir, manifest_path=manifest_path, schemas_dir=_repo_schemas_dir()
    )

    assert "### Adjacent context (segment phb1-p0258-01, pdf p. 258)" in text
    assert "Continuation text here." in text


def test_prompt_missing_context_segment_file_is_silently_skipped(tmp_path: Path) -> None:
    segment = _segment(context_seg_ids=["phb1-p9999-01"])
    manifest_path = _write_manifest(tmp_path)

    text = render_prompt(
        segment,
        data_dir=tmp_path / "data",
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
    )

    assert "phb1-p9999-01" not in text


def test_prompt_how_to_respond_includes_needs_context_and_proposed_type(tmp_path: Path) -> None:
    segment = _segment()
    manifest_path = _write_manifest(tmp_path)

    text = render_prompt(
        segment,
        data_dir=tmp_path / "data",
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
    )

    assert '"needs_context"' in text
    assert '"proposed_type"' in text
    assert "truncated" in text.lower()
    assert "no candidate schema" in text.lower() or "no existing schema" in text.lower()


# ---------------------------------------------------------------------------
# Batch B10: per-kind extraction rules, never-null instruction, table-
# writing convention, and the slug rule's dash clause.
# ---------------------------------------------------------------------------


def test_spell_prompt_does_not_instruct_bulleting_the_stat_block(tmp_path: Path) -> None:
    """B8 follow-up (criterion 7): the spell rule no longer tells the
    subagent to re-emit the stat-block lines as a bullet list -- those
    already live in `fields` and the site renders them from there."""
    segment = _segment()  # kind_hint spell
    manifest_path = _write_manifest(tmp_path)

    text = render_prompt(
        segment,
        data_dir=tmp_path / "data",
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
    )

    assert "each item bold-labeled" not in text
    assert "**Level:**" not in text
    assert "begins at the descriptive body" in text
    assert "do NOT repeat School, Level, Components" in text


def test_prompt_renders_extraction_rules_section_for_feat(tmp_path: Path) -> None:
    segment = _segment(kind_hint="feat", heading="Power Attack [General]")
    manifest_path = _write_manifest(tmp_path)

    text = render_prompt(
        segment,
        data_dir=tmp_path / "data",
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
    )

    assert "## Extraction rules for `feat`" in text
    assert "NAME [TYPE]" in text
    assert "Prerequisite:" in text
    assert "Benefit:" in text
    # A feat prompt must not carry spell class-level instructions.
    assert "`Sor` -> `Sorcerer`" not in text
    assert "levels[].class" not in text


def test_prompt_renders_extraction_rules_section_for_rules_section(tmp_path: Path) -> None:
    segment = _segment(kind_hint="rules_section")
    manifest_path = _write_manifest(tmp_path)

    text = render_prompt(
        segment,
        data_dir=tmp_path / "data",
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
    )

    assert "## Extraction rules for `rules_section`" in text
    assert "`topic` is the section's own heading" in text
    assert "table of contents entry" in text


def test_prompt_renders_extraction_rules_section_for_table(tmp_path: Path) -> None:
    segment = _segment(kind_hint="table")
    manifest_path = _write_manifest(tmp_path)

    text = render_prompt(
        segment,
        data_dir=tmp_path / "data",
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
    )

    assert "## Extraction rules for `table`" in text
    assert "printed table title VERBATIM" in text
    assert "caption-only" in text
    # A table segment has nothing else to cross-link to.
    assert "Tables belonging to this entity" not in text


def test_prompt_table_writing_convention_prints_both_output_dirs(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    segment = _segment(kind_hint="feat")
    manifest_path = _write_manifest(tmp_path)

    text = render_prompt(
        segment, data_dir=data_dir, manifest_path=manifest_path, schemas_dir=_repo_schemas_dir()
    )

    feat_dir = (data_dir / "records" / "phb1" / "feat").resolve()
    table_dir = (data_dir / "records" / "phb1" / "table").resolve()
    assert "### Tables belonging to this entity" in text
    assert str(feat_dir) in text
    assert str(table_dir) in text
    assert "fields.parent_record" in text
    assert "`tables` array" in text or "owning record's `tables`" in text


def test_prompt_never_null_instruction_is_explicit(tmp_path: Path) -> None:
    """B8 follow-up (criterion 8): the prompt states a `fields` property
    with no supported value must be OMITTED, never written as `null`."""
    segment = _segment()
    manifest_path = _write_manifest(tmp_path)

    text = render_prompt(
        segment,
        data_dir=tmp_path / "data",
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
    )

    assert "never write it as `null`" in text
    assert "OMIT it" in text
    assert "macro_eligible" in text and "may simply be omitted" in text


def test_prompt_slug_rule_documents_dash_punctuation(tmp_path: Path) -> None:
    segment = _segment()
    manifest_path = _write_manifest(tmp_path)

    text = render_prompt(
        segment,
        data_dir=tmp_path / "data",
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
    )

    assert "en dash or em dash counts as a hyphen" in text
    assert "table-3-8-the-druid" in text


def test_unknown_kind_hint_notes_no_schema_instead_of_crashing(tmp_path: Path) -> None:
    # stat_block has no registered schema (still a future batch) -- the only
    # kind_hint left this batch that exercises the "no schema" branch, now
    # that spell/feat/table/rules_section are all registered.
    segment = _segment(kind_hint="stat_block")
    manifest_path = _write_manifest(tmp_path)

    text = render_prompt(
        segment,
        data_dir=tmp_path / "data",
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
    )

    assert "stat_block" in text
    assert "no schema" in text.lower()


# Batch B10 improvement: a non-`table` prompt must show the subagent the
# `table` type's own fields schema, extraction rules, and example record --
# not just an output directory and cross-link bullets -- since it is told to
# write a SECOND, `table`-typed record alongside its own.


def test_non_table_prompt_renders_the_table_fields_schema_and_example(tmp_path: Path) -> None:
    registry = load_registry(_repo_schemas_dir())
    table_version = registry.types["table"].version

    rules_section_text = render_prompt(
        _segment(kind_hint="rules_section"),
        data_dir=tmp_path / "data",
        manifest_path=_write_manifest(tmp_path),
        schemas_dir=_repo_schemas_dir(),
    )

    assert "### Tables belonging to this entity" in rules_section_text
    assert "`columns`" in rules_section_text
    assert "`rows`" in rules_section_text
    assert '"columns": [' in rules_section_text
    assert "pad a short row" in rules_section_text
    # Pinned to the distinct-version sentence itself (criterion 4), not to
    # the pre-existing "`fields` schema for type `rules_section` (schema_
    # version 1):" heading, which already contains the substring
    # "schema_version 1" with or without this feature -- rules_section and
    # table happen to share schema_version 1 today, so a substring-only
    # assertion would pass even if this distinct-version sentence were
    # removed or broken.
    assert (
        "The table record's own `schema_version` is the current registered\n"
        f"version for type `table`: {table_version} -- NOT the same value as"
    ) in rules_section_text

    table_text = render_prompt(
        _segment(kind_hint="table"),
        data_dir=tmp_path / "data",
        manifest_path=_write_manifest(tmp_path),
        schemas_dir=_repo_schemas_dir(),
    )
    assert "### Tables belonging to this entity" not in table_text


def test_every_kind_that_is_told_to_write_a_table_is_shown_the_table_schema(
    tmp_path: Path,
) -> None:
    """Generic guard (retro proposal 9): any prompt that tells its subagent
    to write "a `table` record" must also show it the table schema's
    `columns`/`rows` fields -- catches a future kind added to the cross-link
    convention without also rendering the table schema for it."""
    registry = load_registry(_repo_schemas_dir())
    manifest_path = _write_manifest(tmp_path)

    for kind in registry.types:
        text = render_prompt(
            _segment(kind_hint=kind),
            data_dir=tmp_path / "data",
            manifest_path=manifest_path,
            schemas_dir=_repo_schemas_dir(),
        )
        if "a `table` record" in text:
            assert "`columns`" in text, (
                f"kind_hint={kind!r} tells the subagent to write "
                "a `table` record but never shows it the table schema"
            )


# ---------------------------------------------------------------------------
# B10 retrospective (mand1): a generic rules_section heading must be
# qualified with its enclosing entity, plus a prompt-rendering coverage test
# for every registered kind (retro proposals 4 and 9).
# ---------------------------------------------------------------------------


def test_rules_section_prompt_states_generic_heading_qualification_rule(tmp_path: Path) -> None:
    """Acceptance criteria 1-4: the rule names the recurring generic
    headings, gives the literal worked example, says where the enclosing
    entity comes from (and the `needs_context` fallback), and states how
    `slug`/`id` follow from the qualified `name` -- never from qualifying
    the slug alone."""
    segment = _segment(kind_hint="rules_section")
    manifest_path = _write_manifest(tmp_path)

    text = render_prompt(
        segment,
        data_dir=tmp_path / "data",
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
    )

    rules_index = text.index("## Extraction rules for `rules_section`")
    next_heading_index = text.index("\n## ", rules_index)
    rules_section = text[rules_index:next_heading_index]

    # Criterion 1: recurring generic headings named, worked example given.
    for heading in ["Class Features", "Class Skills", "Game Rule Information", "Description"]:
        assert heading in rules_section, heading
    assert "Class Features (Barbarian)" in rules_section
    assert "GENERIC" in rules_section

    # Criterion 2: where the enclosing entity comes from, and the
    # needs_context fallback naming the previous segment id.
    assert "nearest preceding" in rules_section
    assert "### Adjacent context" in rules_section
    assert (
        "If none of those name it, answer `needs_context` naming the\n"
        "previous segment id instead of guessing at an entity."
    ) in rules_section

    # Criterion 3: slug/id follow from the QUALIFIED name; qualifying the
    # slug alone while name stays generic is wrong and fails validation --
    # this is the exact observed `class-features-barbarian` failure shape.
    assert "`slug` and `id` follow from the QUALIFIED `name`" in rules_section
    assert "class-features-barbarian" in rules_section
    assert "rules_section:<book_id>:class-features-barbarian" in rules_section
    assert "Qualifying the\nslug alone while leaving `name` GENERIC is WRONG and fails" in (
        rules_section
    )

    # Criterion 4: topic/parent_section split, unchanged meanings.
    assert "`fields.topic` still stays the bare heading" in rules_section
    assert "`fields.parent_section` carries the enclosing" in rules_section
    assert "`topic` is the section's own heading/subject" in rules_section
    assert "`parent_section` is" in rules_section
    assert "the enclosing section's heading, ONLY when the text makes it" in rules_section


def test_spell_feat_table_prompts_do_not_carry_rules_section_qualification_rule(
    tmp_path: Path,
) -> None:
    """Acceptance criterion 6: kind-specific instructions stay kind-specific
    -- pinned on the rule's distinctive sentence, not on a word (like "Class
    Features") that also appears only inside the rules_section EXAMPLE
    RECORD, which spell/feat/table prompts never render."""
    manifest_path = _write_manifest(tmp_path)
    marker = "`slug` and `id` follow from the QUALIFIED `name`"

    for kind in ["spell", "feat", "table"]:
        text = render_prompt(
            _segment(kind_hint=kind),
            data_dir=tmp_path / "data",
            manifest_path=manifest_path,
            schemas_dir=_repo_schemas_dir(),
        )
        assert marker not in text, kind
        assert "Class Features (Barbarian)" not in text, kind


def test_rules_section_example_fixture_models_the_qualification_convention() -> None:
    """Acceptance criterion 5: the EXAMPLE RECORD rendered verbatim into
    every rules_section prompt itself demonstrates the convention."""
    path = _repo_schemas_dir() / "examples" / "rules_section.json"
    example = json.loads(path.read_text())

    assert example["name"] == "Class Features (Sable Knight)"
    assert example["slug"] == "class-features-sable-knight"
    assert example["id"] == "rules_section:example:class-features-sable-knight"
    assert example["fields"]["topic"] == "Class Features"
    assert example["fields"]["parent_section"] == "Sable Knight"


# ---------------------------------------------------------------------------
# Acceptance criteria 7-8: a per-registered-kind prompt-rendering coverage
# guard (retro proposal 9). `_prompt_coverage_failures` returns a list of
# human-readable gap descriptions for one rendered prompt -- empty means no
# gaps found. This is a guard, not a red-then-green test for criteria 7
# (already true on main for all four registered types); criterion 8's
# negative test is what proves it actually bites.
# ---------------------------------------------------------------------------

_REPLY_CONTRACT_KEYS = {"seg_id", "records", "no_content", "needs_context", "proposed_type", "notes"}
_JSON_LITERALS = {"null", "true", "false"}
_BACKTICK_RE = re.compile(r"`([^`\n]+)`")
_FIELD_TOKEN_RE = re.compile(r"^[a-z_][a-z0-9_]*(\[\])?(\.[a-z_][a-z0-9_]*)?$")


def _schema_property_names(schema: dict[str, Any]) -> set[str]:
    """All property names in `schema`, recursing into nested array-of-object
    and object properties the same way `_render_schema_properties` does."""
    names: set[str] = set()
    for name, prop in schema.get("properties", {}).items():
        if not isinstance(prop, dict):
            continue
        names.add(name)
        items = prop.get("items")
        if isinstance(items, dict) and items.get("type") == "object" and items.get("properties"):
            names |= _schema_property_names(items)
        if prop.get("type") == "object" and prop.get("properties"):
            names |= _schema_property_names(prop)
    return names


def _section_text(text: str, marker: str) -> str | None:
    """The text of the section starting at `marker` up to (not including)
    the next real `## ` (exactly two hashes) heading -- `### `/`#### `
    sub-headings inside it don't end the section, since a literal `\\n## `
    only matches a true two-hash heading."""
    if marker not in text:
        return None
    start = text.index(marker)
    try:
        end = text.index("\n## ", start)
    except ValueError:
        end = len(text)
    return text[start:end]


def _prompt_coverage_failures(kind: str, text: str, registry: Registry) -> list[str]:
    """Human-readable coverage gaps for a rendered `kind` prompt: every
    instruction naming a field or a record type must be backed by a
    schema/example actually rendered in this same prompt (retro proposal 9 --
    the mechanical counter-measure to B10's table-cross-link defect)."""
    failures: list[str] = []
    if kind not in registry.types:
        return failures

    type_schema = registry.load_type_schema(kind)
    type_props = _schema_property_names(type_schema)
    envelope_props = _schema_property_names(registry.envelope_schema)

    for prop_name in sorted(type_props):
        if f"`{prop_name}`" not in text:
            failures.append(f"{kind}: fields property `{prop_name}` is not rendered in the prompt")
    for prop_name in sorted(envelope_props):
        if f"`{prop_name}`" not in text:
            failures.append(f"{kind}: envelope property `{prop_name}` is not rendered in the prompt")

    example_path = registry.schemas_dir / "examples" / f"{kind}.json"
    if not example_path.is_file():
        failures.append(f"{kind}: schemas/examples/{kind}.json does not exist")
    else:
        example = json.loads(example_path.read_text())
        if json.dumps(example, indent=2) not in text:
            failures.append(f"{kind}: its own example record is not rendered verbatim in the prompt")

    from owlsperch.queue.prompt import _KIND_RULES

    if kind not in _KIND_RULES:
        failures.append(f"{kind}: no entry in _KIND_RULES")
    elif f"## Extraction rules for `{kind}`" not in text:
        failures.append(f"{kind}: '## Extraction rules for `{kind}`' heading is not rendered")

    mentioned_types = {t for t in registry.types if t != kind and f"`{t}`" in text}
    grounded = type_props | envelope_props | _REPLY_CONTRACT_KEYS | _JSON_LITERALS | set(
        registry.types
    )

    for other in sorted(mentioned_types):
        other_props = _schema_property_names(registry.load_type_schema(other))
        grounded |= other_props
        for prop_name in sorted(other_props):
            if f"`{prop_name}`" not in text:
                failures.append(
                    f"{kind}: cross-type `{other}` property `{prop_name}` is not rendered "
                    "in the prompt"
                )
        other_example_path = registry.schemas_dir / "examples" / f"{other}.json"
        if not other_example_path.is_file():
            failures.append(f"{kind}: cross-type `{other}` has no schemas/examples/{other}.json")
        else:
            other_example = json.loads(other_example_path.read_text())
            if json.dumps(other_example, indent=2) not in text:
                failures.append(
                    f"{kind}: cross-type `{other}` example record is not rendered verbatim "
                    "in the prompt"
                )

    for marker in [f"## Extraction rules for `{kind}`", "### Tables belonging to this entity"]:
        section = _section_text(text, marker)
        if section is None:
            continue
        for match in _BACKTICK_RE.finditer(section):
            token = match.group(1)
            if not _FIELD_TOKEN_RE.match(token):
                continue
            first_segment = token.split(".", 1)[0]
            if first_segment.endswith("[]"):
                first_segment = first_segment[:-2]
            if first_segment not in grounded:
                failures.append(
                    f"{kind}: {marker!r} references `{token}` (via `{first_segment}`) which "
                    "does not resolve to any known field, reply-contract key, JSON literal, "
                    "or registered type"
                )

    return failures


def test_every_registered_kind_prompt_has_zero_coverage_gaps(tmp_path: Path) -> None:
    """Acceptance criterion 7 (guard, not red-then-green -- see criterion 8's
    negative test for proof this actually bites): renders a prompt for every
    type in `schemas/registry.json`, never a hard-coded type list."""
    registry = load_registry(_repo_schemas_dir())
    manifest_path = _write_manifest(tmp_path)

    for kind in registry.types:
        text = render_prompt(
            _segment(kind_hint=kind),
            data_dir=tmp_path / "data",
            manifest_path=manifest_path,
            schemas_dir=_repo_schemas_dir(),
        )
        failures = _prompt_coverage_failures(kind, text, registry)
        assert failures == [], f"kind={kind!r}: {failures}"


def test_coverage_guard_catches_a_missing_cross_type_example(tmp_path: Path) -> None:
    """Acceptance criterion 8: reproduces the shape of the B10 defect (an
    instruction to write a `table` record with no backing table material
    rendered) against a doctored schemas dir missing `examples/table.json`,
    and shows the coverage helper now catches it."""
    doctored_schemas = tmp_path / "schemas"
    shutil.copytree(_repo_schemas_dir(), doctored_schemas)
    (doctored_schemas / "examples" / "table.json").unlink()

    registry = load_registry(doctored_schemas)
    manifest_path = _write_manifest(tmp_path)

    text = render_prompt(
        _segment(kind_hint="rules_section"),
        data_dir=tmp_path / "data",
        manifest_path=manifest_path,
        schemas_dir=doctored_schemas,
    )
    assert "a `table` record" in text  # the cross-link instruction is still there

    failures = _prompt_coverage_failures("rules_section", text, registry)
    assert any(
        "cross-type `table`" in f and "examples/table.json" in f for f in failures
    ), failures
