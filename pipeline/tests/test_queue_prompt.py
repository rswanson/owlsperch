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

    expected_dir = (data_dir / "staging" / "phb1" / "phb1-p0257-04" / "records" / "spell").resolve()
    assert str(expected_dir) in text
    assert expected_dir.is_absolute()


def test_prompt_example_reply_records_path_is_a_staging_path(tmp_path: Path) -> None:
    """AC12: the '## How to respond' example reply's `records` entry must be
    the data-dir-relative *staging* path, not the old direct
    `records/<book_id>/<kind_hint>/<slug>.json` shape -- so a reverted
    `example_result` f-string in prompt.py fails this test."""
    data_dir = tmp_path / "data"
    segment = _segment()
    manifest_path = _write_manifest(tmp_path)

    text = render_prompt(
        segment, data_dir=data_dir, manifest_path=manifest_path, schemas_dir=_repo_schemas_dir()
    )

    expected_example_path = (
        f"staging/{segment.book_id}/{segment.seg_id}/records/{segment.kind_hint}/<slug>.json"
    )
    assert expected_example_path in text
    # The old, pre-staging example path must not appear anywhere.
    old_example_path = f"records/{segment.book_id}/{segment.kind_hint}/<slug>.json"
    assert old_example_path not in text


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


def test_class_prompt_contains_bleed_rule_and_spell_column_convention(tmp_path: Path) -> None:
    """B10c-mand3 Part 2 (judge findings 1 and 3): a rendered `class` prompt
    must tell the subagent how to handle column bleed (a Special token
    bled in from a neighbouring class's own Special column) and must state
    the "Spells per Day <slot>" column-naming convention the validator
    (`check_class_fields`'s `_SPELL_COLUMN_RE`) looks for."""
    segment = _segment(kind_hint="class", heading="Bard")
    manifest_path = _write_manifest(tmp_path)

    text = render_prompt(
        segment,
        data_dir=tmp_path / "data",
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
    )

    assert "bleed" in text.lower()
    assert "Bonus Feat" in text  # the judge's own concrete example
    assert "Spells per Day" in text
    assert "needs_context" in text


def test_class_prompt_attributes_pre_heading_tail_by_the_class_it_names(
    tmp_path: Path,
) -> None:
    """B10c-mand6 rule (a): a rendered `class` prompt must tell the
    subagent that this segment's first paragraphs may be a pre-heading
    column tail, and that a passage there is attributed by the class it
    NAMES, never by its position in the segment."""
    segment = _segment(kind_hint="class", heading="Rogue")
    manifest_path = _write_manifest(tmp_path)

    text = render_prompt(
        segment,
        data_dir=tmp_path / "data",
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
    )

    assert "top of the page" in text.lower()
    assert "Starting Package" in text
    assert "never by" in text.lower()


def test_prestige_class_prompt_attributes_pre_heading_tail(tmp_path: Path) -> None:
    """B10c-mand6 rule (a), mirrored (entity-neutral) into `prestige_class`."""
    segment = _segment(kind_hint="prestige_class", heading="Sable Knight")
    manifest_path = _write_manifest(tmp_path)

    text = render_prompt(
        segment,
        data_dir=tmp_path / "data",
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
    )

    assert "top of the page" in text.lower()
    assert "never by" in text.lower()


def test_class_prompt_class_features_come_from_printed_headings(tmp_path: Path) -> None:
    """B10c-mand6 rules (b)-(d): `class_features` come from the printed
    "Class Features" run-in headings (not the level table's Special
    column), `Weapon and Armor Proficiency` is filed separately, and the
    printed heading's own spelling wins over the Special cell's wording
    when the two differ."""
    segment = _segment(kind_hint="class", heading="Wizard")
    manifest_path = _write_manifest(tmp_path)

    text = render_prompt(
        segment,
        data_dir=tmp_path / "data",
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
    )

    assert "Class Features" in text
    assert "(Ex)" in text and "(Su)" in text and "(Sp)" in text
    assert "Weapon and Armor Proficiency" in text
    assert "`weapon_and_armor_proficiency`" in text
    assert "CROSS-CHECK" in text
    assert "printed heading's spelling wins" in text
    assert '"Bonus Feat"' in text and '"Bonus Feats"' in text


def test_prestige_class_prompt_class_features_come_from_printed_headings(
    tmp_path: Path,
) -> None:
    """B10c-mand6 rules (b)-(d), mirrored (entity-neutral) into
    `prestige_class`."""
    segment = _segment(kind_hint="prestige_class", heading="Sable Knight")
    manifest_path = _write_manifest(tmp_path)

    text = render_prompt(
        segment,
        data_dir=tmp_path / "data",
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
    )

    assert "Class Features" in text
    assert "Weapon and Armor Proficiency" in text
    assert "`weapon_and_armor_proficiency`" in text
    assert "CROSS-CHECK" in text
    assert "printed heading's spelling wins" in text


def test_class_prompt_keeps_starting_package_sections(tmp_path: Path) -> None:
    """B10c-mand6 rule (e): a printed "<Race> <Class> Starting Package"
    section is a `description_sections` entry, attributed to the class it
    actually names."""
    segment = _segment(kind_hint="class", heading="Barbarian")
    manifest_path = _write_manifest(tmp_path)

    text = render_prompt(
        segment,
        data_dir=tmp_path / "data",
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
    )

    assert "Starting Package" in text
    assert "Half-Orc Barbarian Starting Package" in text
    assert "belongs to the neighbouring class" in text


def test_class_prompt_alignment_is_the_verbatim_game_rule_information_value(
    tmp_path: Path,
) -> None:
    """B10c-mand6 rule (f): `alignment` is the short printed GAME RULE
    INFORMATION line, verbatim minus the trailing period -- distinct from
    the flavor "Alignment" `description_sections` entry of the same
    name."""
    segment = _segment(kind_hint="class", heading="Ranger")
    manifest_path = _write_manifest(tmp_path)

    text = render_prompt(
        segment,
        data_dir=tmp_path / "data",
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
    )

    assert '"Any nonlawful"' in text
    assert '"Any lawful"' in text
    assert 'heading: "Alignment"' in text


def test_prestige_class_prompt_alignment_is_verbatim(tmp_path: Path) -> None:
    """B10c-mand6 rule (f), mirrored (entity-neutral) into `prestige_class`."""
    segment = _segment(kind_hint="prestige_class", heading="Sable Knight")
    manifest_path = _write_manifest(tmp_path)

    text = render_prompt(
        segment,
        data_dir=tmp_path / "data",
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
    )

    assert '"Any nonlawful"' in text
    assert 'heading: "Alignment"' in text


# ---------------------------------------------------------------------------
# Batch B10c-mand13: five class-quality-judgement (2026-09-18) prompt rules --
# coverage completeness (Ex-<Class>/Abilities), run-in Class Features
# headings as their own entries, verbatim/complete feature text, sidebars
# extracted separately, and the second-table convention covering every
# owned table, not only the level table.
# ---------------------------------------------------------------------------


def test_class_prompt_requires_ex_class_and_abilities_sections(tmp_path: Path) -> None:
    segment = _segment(kind_hint="class", heading="Paladin")
    manifest_path = _write_manifest(tmp_path)

    text = render_prompt(
        segment,
        data_dir=tmp_path / "data",
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
    )

    assert "coverage" in text.lower()
    assert "`Ex-<Class>`" in text
    assert '`heading: "Abilities"`' in text


def test_prestige_class_prompt_requires_ex_entry_and_abilities_sections(tmp_path: Path) -> None:
    segment = _segment(kind_hint="prestige_class", heading="Sable Knight")
    manifest_path = _write_manifest(tmp_path)

    text = render_prompt(
        segment,
        data_dir=tmp_path / "data",
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
    )

    assert "coverage" in text.lower()
    assert "`Ex-<Entry>`" in text
    assert '`heading: "Abilities"`' in text


def test_class_prompt_run_in_class_features_headings_are_own_entries(tmp_path: Path) -> None:
    segment = _segment(kind_hint="class", heading="Cleric")
    manifest_path = _write_manifest(tmp_path)

    text = render_prompt(
        segment,
        data_dir=tmp_path / "data",
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
    )

    assert "bold run-in heading" in text
    assert "Deity, Domains, and Domain Spells" in text
    assert "Spontaneous Casting" in text
    assert "never nested as a paragraph" in text


def test_prestige_class_prompt_run_in_class_features_headings_are_own_entries(
    tmp_path: Path,
) -> None:
    segment = _segment(kind_hint="prestige_class", heading="Sable Knight")
    manifest_path = _write_manifest(tmp_path)

    text = render_prompt(
        segment,
        data_dir=tmp_path / "data",
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
    )

    assert "bold run-in heading" in text
    assert "never nested as a paragraph" in text


def test_class_prompt_feature_text_is_complete_and_verbatim(tmp_path: Path) -> None:
    segment = _segment(kind_hint="class", heading="Bard")
    manifest_path = _write_manifest(tmp_path)

    text = render_prompt(
        segment,
        data_dir=tmp_path / "data",
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
    )

    assert "COMPLETE printed text" in text
    assert "Never condense, paraphrase, reorder, or drop" in text
    assert "never silently" in text.lower()
    assert "correct a printed typo" in text


def test_prestige_class_prompt_feature_text_is_complete_and_verbatim(tmp_path: Path) -> None:
    segment = _segment(kind_hint="prestige_class", heading="Sable Knight")
    manifest_path = _write_manifest(tmp_path)

    text = render_prompt(
        segment,
        data_dir=tmp_path / "data",
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
    )

    assert "COMPLETE printed text" in text
    assert "Never condense, paraphrase, reorder, or drop" in text


def test_class_prompt_sidebars_are_not_this_classs_content(tmp_path: Path) -> None:
    segment = _segment(kind_hint="class", heading="Druid")
    manifest_path = _write_manifest(tmp_path)

    text = render_prompt(
        segment,
        data_dir=tmp_path / "data",
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
    )

    assert "titled sidebar" in text.lower()
    assert "FAMILIARS" in text
    assert "THE PALADIN'S MOUNT" in text
    assert "extracted" in text and "by its own segment" in text.replace("\n", " ")
    assert "do not write" in text
    assert "UNTITLED grid" in text


def test_prestige_class_prompt_sidebars_are_not_this_entrys_content(tmp_path: Path) -> None:
    segment = _segment(kind_hint="prestige_class", heading="Sable Knight")
    manifest_path = _write_manifest(tmp_path)

    text = render_prompt(
        segment,
        data_dir=tmp_path / "data",
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
    )

    assert "titled sidebar" in text.lower()
    assert "extracted" in text and "by its own segment" in text.replace("\n", " ")
    assert "do not write" in text
    assert "UNTITLED grid" in text


def test_class_prompt_second_table_convention_is_not_limited_to_level_table(
    tmp_path: Path,
) -> None:
    """Judge finding 5's follow-up: the "Tables belonging to this entity"
    convention must not read as limited to a class's own level-progression
    table."""
    segment = _segment(kind_hint="class", heading="Bard")
    manifest_path = _write_manifest(tmp_path)

    text = render_prompt(
        segment,
        data_dir=tmp_path / "data",
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
    )

    assert "not only a class's level-progression table" in text
    assert "secondary" in text and "titled table" in text


def test_table_prompt_folds_footnote_into_caption_and_keeps_header_order(
    tmp_path: Path,
) -> None:
    segment = _segment(kind_hint="table", heading="Table 3-10: The Monk")
    manifest_path = _write_manifest(tmp_path)

    text = render_prompt(
        segment,
        data_dir=tmp_path / "data",
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
    )

    assert "footnote" in text.lower()
    assert "folded into this table's own `caption`" in text
    assert '"Unarmored Speed Bonus"' in text


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

    expected_dir = (data_dir / "staging" / "phb1" / "phb1-p0257-04" / "records" / "spell").resolve()

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

    feat_dir = (data_dir / "staging" / "phb1" / "phb1-p0257-04" / "records" / "feat").resolve()
    table_dir = (data_dir / "staging" / "phb1" / "phb1-p0257-04" / "records" / "table").resolve()
    assert "### Tables belonging to this entity" in text
    assert str(feat_dir) in text
    assert str(table_dir) in text
    assert "fields.parent_record" in text
    assert "`tables` array" in text or "owning record's `tables`" in text


def test_table_convention_forbids_inventing_a_grid_not_in_this_segment(tmp_path: Path) -> None:
    """B10c-mand20 (judge round 4, blocker 1): a haiku extractor handed a
    truncated segment invented a whole progression grid to satisfy the
    "each printed grid is its own table" rule instead of writing its
    entity record without the table. The "Tables belonging to this
    entity" convention block must tell a subagent to omit a table it
    can't actually see from `tables` (noting it) rather than invent one --
    and NOT to answer `needs_context` for a missing table alone, since
    that would discard the entity record it already extracted correctly
    (`complete.py` returns on `needs_context` before ingesting any
    `records`); `needs_context` is reserved for when the entity itself is
    truncated."""
    segment = _segment(kind_hint="rules_section")
    manifest_path = _write_manifest(tmp_path)

    text = render_prompt(
        segment,
        data_dir=tmp_path / "data",
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
    )

    convention_start = text.index("### Tables belonging to this entity")
    convention_end = text.index("#### `fields` schema for the table record you write")
    convention_text = " ".join(text[convention_start:convention_end].split())
    assert "grid whose cells are present in THIS segment's own text" in convention_text
    assert "never invented" in convention_text
    assert "write the entity's own record WITHOUT that table" in convention_text
    assert "omit its id from `tables`" in convention_text
    assert "do NOT answer `needs_context` for a missing table alone" in convention_text
    assert "ENTITY ITSELF is truncated" in convention_text


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


def test_rules_section_prompt_states_multiple_grids_rule(tmp_path: Path) -> None:
    """B10c-mand16 finding B: a rules_section segment printing several
    grids (e.g. the FAMILIARS sidebar's own progression grid AND its
    separate "Familiar | Special" list) must write each one as its own
    `table` record, in printed order, rather than inlining a later grid as
    markdown/prose -- including a two-column label/value grid whose right
    cells are full sentences."""
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

    assert "SEVERAL grids" in rules_section
    assert "Familiar | Special" in rules_section
    assert "each one is its own `table` record, in printed order" in rules_section
    assert "Tables belonging to this entity" in rules_section
    assert "never" in rules_section and "inlined as markdown or prose" in rules_section
    assert "still a table for this purpose" in rules_section


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

_REPLY_CONTRACT_KEYS = {
    "seg_id",
    "records",
    "no_content",
    "needs_context",
    "proposed_type",
    "notes",
}
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
            failures.append(
                f"{kind}: envelope property `{prop_name}` is not rendered in the prompt"
            )

    example_path = registry.schemas_dir / "examples" / f"{kind}.json"
    if not example_path.is_file():
        failures.append(f"{kind}: schemas/examples/{kind}.json does not exist")
    else:
        example = json.loads(example_path.read_text())
        if json.dumps(example, indent=2) not in text:
            failures.append(
                f"{kind}: its own example record is not rendered verbatim in the prompt"
            )

    from owlsperch.queue.prompt import _KIND_RULES

    if kind not in _KIND_RULES:
        failures.append(f"{kind}: no entry in _KIND_RULES")
    elif f"## Extraction rules for `{kind}`" not in text:
        failures.append(f"{kind}: '## Extraction rules for `{kind}`' heading is not rendered")

    # A registered type's name can coincide with an unrelated field name of
    # THIS SAME kind (batch B10c: spell's own `levels[].class` sub-property
    # is literally "class", the same string as the new `class` record
    # type) -- when the kind's own schema render is what put `` `class` ``
    # in the text, that is not a cross-type reference at all, so exclude
    # any type name already grounded by this kind's own fields/envelope
    # before deciding what counts as "mentioned".
    grounded = (
        type_props | envelope_props | _REPLY_CONTRACT_KEYS | _JSON_LITERALS | set(registry.types)
    )
    mentioned_types = {
        t
        for t in registry.types
        if t != kind and f"`{t}`" in text and t not in type_props and t not in envelope_props
    }

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
    assert any("cross-type `table`" in f and "examples/table.json" in f for f in failures), failures


# ---------------------------------------------------------------------------
# Batch B11: errata_entry prompt -- "Applies to book ID" line and rules.
# ---------------------------------------------------------------------------


def _write_errata_manifest(tmp_path: Path) -> Path:
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text(
        """
entries:
  - book_id: phb1
    title: "Player's Handbook (Core Rulebook I)"
    file: "phb1.pdf"
    edition: "3.5"
    kind: rulebook
  - book_id: phb1-errata
    title: "Player's Handbook Errata"
    file: "phb1-errata.pdf"
    edition: "3.5"
    kind: errata
    applies_to: phb1
"""
    )
    return manifest_path


def test_errata_entry_prompt_renders_applies_to_book_id_and_rules(tmp_path: Path) -> None:
    segment = _segment(
        seg_id="phb1-errata-p0001-01",
        book_id="phb1-errata",
        pages=[1],
        printed_pages=[],
        kind_hint="errata_entry",
        heading="Glibness",
        text=(
            "Glibness Player's Handbook, page 236 In second paragraph, change to read "
            "as follows: If a magical effect is used against you, it fails."
        ),
    )
    manifest_path = _write_errata_manifest(tmp_path)

    text = render_prompt(
        segment,
        data_dir=tmp_path / "data",
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
    )

    assert "- Applies to book ID: phb1" in text
    assert "## Extraction rules for `errata_entry`" in text
    assert "`target_book`" in text
    assert "`target_page`" in text
    assert "`replacement_text`" in text


def test_prompt_applies_to_book_id_none_for_ordinary_book(tmp_path: Path) -> None:
    segment = _segment()
    manifest_path = _write_manifest(tmp_path)

    text = render_prompt(
        segment,
        data_dir=tmp_path / "data",
        manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
    )

    assert "- Applies to book ID: (none)" in text


# ---------------------------------------------------------------------------
# Batch B12: the monster/npc/template extraction rules. The per-kind
# coverage guard above already proves every field and cross-type reference
# is backed by a rendered schema/example; these check the rules a subagent
# actually has to be told, and that they stay out of the other kinds'
# prompts.
# ---------------------------------------------------------------------------


def _rules_section_for(kind: str, tmp_path: Path) -> str:
    text = render_prompt(
        _segment(kind_hint=kind),
        data_dir=tmp_path / "data",
        manifest_path=_write_manifest(tmp_path),
        schemas_dir=_repo_schemas_dir(),
    )
    section = _section_text(text, f"## Extraction rules for `{kind}`")
    assert section is not None, f"no extraction rules section rendered for {kind}"
    return section


def test_monster_rules_name_both_printed_stat_block_layouts(tmp_path: Path) -> None:
    section = _rules_section_for("monster", tmp_path)
    # Layout A: every MM I label, in printed order, as a split point.
    for label in ("Hit Dice:", "Base Attack/Grapple:", "Space/Reach:", "Level Adjustment:"):
        assert label in section
    # Layout B: the MM III+ vertical list's own abbreviated labels.
    for label in ("Init/Senses", "Base Atk/Grp", "Immune/Resist/SR", "Fort/Ref/Will", "SQ"):
        assert label in section


def test_monster_rules_state_the_grouped_entry_naming_convention(tmp_path: Path) -> None:
    section = _rules_section_for("monster", tmp_path)
    assert "`group`" in section and "`variant_label`" in section
    assert "Angel, Astral Deva" in section
    assert "Animated Object, Huge" in section
    assert "one record per creature" in section.lower()


def test_monster_rules_say_the_multi_column_stat_table_is_not_a_table_record(
    tmp_path: Path,
) -> None:
    one_line = re.sub(r"\s+", " ", _rules_section_for("monster", tmp_path))
    assert "MULTI-COLUMN" in one_line
    assert "NOT also written as a `table` record" in one_line
    # (B12 review) The real text layer gives the shared stat table as
    # tab-separated ROWS whose first cell is the stat-block label.
    assert "TAB-SEPARATED ROWS" in one_line
    assert '"Hit Dice (hp)"' in one_line
    assert "Write ONE record per COLUMN" in one_line


def test_monster_rules_cover_a_multi_part_hit_dice_line(tmp_path: Path) -> None:
    """B12 review finding 4: the `plus` form a lycanthrope or any
    class-levelled creature prints."""
    one_line = re.sub(r"\s+", " ", _rules_section_for("monster", tmp_path))
    assert "`hd.groups` has ONE ENTRY PER PRINTED DICE TERM" in one_line
    assert '"1d8+1 plus 6d8+18 (50 hp)"' in one_line
    assert "`hd.count` 7" in one_line
    assert "Never keep only the first term" in one_line


def test_monster_rules_say_a_dash_only_line_is_an_empty_array(tmp_path: Path) -> None:
    """B12 review finding 3: "Special Attacks: —" appears 65 times."""
    one_line = re.sub(r"\s+", " ", _rules_section_for("monster", tmp_path))
    assert "means NONE: write an EMPTY array" in one_line
    assert '`["\u2014"]` is wrong' in one_line


def test_monster_rules_name_the_mm3_labels_for_every_required_field(tmp_path: Path) -> None:
    """B12 review finding 6: Speed, Space/Reach, CR and Alignment are all
    REQUIRED fields, so the MM III+ label map has to mention them."""
    one_line = re.sub(r"\s+", " ", _rules_section_for("monster", tmp_path))
    for label in ("Speed ->", "Space/Reach ->", "CR ->", "Alignment ->"):
        assert label in one_line, label
    assert "A field the schema above marks REQUIRED is different" in one_line


def test_monster_rules_cover_a_neighbouring_stat_block_and_a_missing_one(tmp_path: Path) -> None:
    """B12 review, real segmentation: 28 of 338 real spans fall back to whole
    pages, so a segment can hold a neighbour's whole stat block; and the
    aboleth's own block is missing from the PDF text layer entirely."""
    one_line = re.sub(r"\s+", " ", _rules_section_for("monster", tmp_path))
    assert "WHOLE PAGES" in one_line
    assert "by the creature it actually NAMES, never by its position" in one_line
    assert "A neighbouring creature's stat block here is NOT yours to extract" in one_line
    assert "answer `needs_context` naming the NEXT segment id" in one_line


def test_monster_rules_put_an_alias_parenthetical_in_aliases(tmp_path: Path) -> None:
    """B12 review: "Barbed devil (hamatula)" is an alias, not a group."""
    one_line = re.sub(r"\s+", " ", _rules_section_for("monster", tmp_path))
    assert '"Barbed devil (hamatula)"' in one_line
    assert "`aliases`" in one_line


def test_monster_rules_give_the_cr_fraction_normalization(tmp_path: Path) -> None:
    section = _rules_section_for("monster", tmp_path)
    for fragment in ('"1/2" -> `cr` 0.5', '"1/3" -> `cr` 0.333', '"1/8" -> `cr` 0.125'):
        assert fragment in section


def test_monster_rules_source_special_abilities_from_the_combat_run_ins(tmp_path: Path) -> None:
    section = _rules_section_for("monster", tmp_path)
    assert "Babble (Su):" in section
    assert "`special_abilities`" in section
    assert "generic" in section
    # The never-invent rule, and the never-`null` rule.
    assert "Never invent a value the text does not state" in section


def test_npc_rules_add_class_levels_and_possessions(tmp_path: Path) -> None:
    section = _rules_section_for("npc", tmp_path)
    assert "`class_levels` is REQUIRED" in section
    assert "`possessions`" in section
    # ...on top of the shared stat-block rules.
    assert "Hit Dice:" in section
    assert "Init/Senses" in section


def test_template_rules_cover_the_printed_modification_paragraphs(tmp_path: Path) -> None:
    section = _rules_section_for("template", tmp_path)
    assert "`acquired_or_inherited`" in section
    assert "`applies_to`" in section
    assert "`modifications` is ONE entry per printed labelled paragraph" in section
    # The printed-label list wraps across lines, so compare against the
    # section as one line.
    one_line = re.sub(r"\s+", " ", section)
    for label in ('"Size and Type"', '"Special Qualities"', '"Level Adjustment"'):
        assert label in one_line
    # A template has no stat block of its own, and a sample creature printed
    # under it is a separate record.
    assert "has no stat block" in section
    assert "SAMPLE CREATURE" in section


def test_monster_rules_do_not_leak_into_another_kinds_prompt(tmp_path: Path) -> None:
    for kind in ("spell", "feat", "table"):
        text = render_prompt(
            _segment(kind_hint=kind),
            data_dir=tmp_path / "data",
            manifest_path=_write_manifest(tmp_path),
            schemas_dir=_repo_schemas_dir(),
        )
        assert "Base Attack/Grapple:" not in text
        assert "Init/Senses" not in text
