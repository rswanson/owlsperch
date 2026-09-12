"""Tests for `owlsperch schema show <type>` (B4 acceptance criterion 7)."""

from __future__ import annotations

from pathlib import Path

import pytest

from owlsperch.schemas import SchemaError, load_registry, render_schema_show


def _repo_schemas_dir() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "schemas"


def test_render_schema_show_prints_path_and_field_table() -> None:
    registry = load_registry(_repo_schemas_dir())
    output = render_schema_show("spell", registry)

    assert "spell.json" in output
    assert "school" in output
    assert "levels" in output
    assert "School" in output  # the x-ui label


def test_render_schema_show_unknown_type_raises() -> None:
    registry = load_registry(_repo_schemas_dir())
    with pytest.raises(SchemaError):
        render_schema_show("not_a_type", registry)


def test_cli_schema_show_prints_output(capsys: pytest.CaptureFixture[str]) -> None:
    from owlsperch.cli import main

    exit_code = main(["schema", "show", "spell"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "spell.json" in output
    assert "school" in output


def test_cli_help_lists_validate_and_schema_commands() -> None:
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "owlsperch", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "validate" in result.stdout
    assert "schema" in result.stdout
