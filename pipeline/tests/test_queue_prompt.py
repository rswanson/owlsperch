"""Tests for `owlsperch.queue.prompt` (and `owlsperch queue prompt <seg_id>`),
per B5 acceptance criterion 2: the rendered prompt must contain the segment
text verbatim, the kind hint, book metadata, the candidate schema(s)
(rendered from the JSON, not hand-copied), the exact output contract
(absolute output directory, id/slug/citation rules, extraction block, schema
version), the exact response contract, and the text_md / no-invented-fields
instructions.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from owlsperch.queue.prompt import prompt_path_for, render_prompt, render_prompt_to_file
from owlsperch.schemas import load_registry
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
    assert f"schema_version {table_version}" in rules_section_text

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
