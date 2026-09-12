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

from owlsperch.queue.prompt import prompt_path_for, render_prompt, render_prompt_to_file
from owlsperch.segment.runner import Segment


def _repo_schemas_dir() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "schemas"


def _segment(**overrides: object) -> Segment:
    defaults: dict[str, object] = dict(
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
    return Segment(**defaults)  # type: ignore[arg-type]


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
        segment, data_dir=tmp_path / "data", manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
    )

    assert segment.text in text


def test_prompt_contains_kind_hint_and_book_metadata(tmp_path: Path) -> None:
    segment = _segment()
    manifest_path = _write_manifest(tmp_path)

    text = render_prompt(
        segment, data_dir=tmp_path / "data", manifest_path=manifest_path,
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
        segment, data_dir=tmp_path / "data", manifest_path=manifest_path,
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
        segment, data_dir=tmp_path / "data", manifest_path=manifest_path,
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
        segment, data_dir=tmp_path / "data", manifest_path=manifest_path,
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
        segment, data_dir=tmp_path / "data", manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
    )

    assert "TESTBOOK p." in text


def test_prompt_contains_extraction_block_and_schema_version_instructions(
    tmp_path: Path,
) -> None:
    segment = _segment()
    manifest_path = _write_manifest(tmp_path)

    text = render_prompt(
        segment, data_dir=tmp_path / "data", manifest_path=manifest_path,
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
        segment, data_dir=tmp_path / "data", manifest_path=manifest_path,
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


def test_unknown_kind_hint_notes_no_schema_instead_of_crashing(tmp_path: Path) -> None:
    segment = _segment(kind_hint="rules_section")
    manifest_path = _write_manifest(tmp_path)

    text = render_prompt(
        segment, data_dir=tmp_path / "data", manifest_path=manifest_path,
        schemas_dir=_repo_schemas_dir(),
    )

    assert "rules_section" in text
    assert "no schema" in text.lower()
