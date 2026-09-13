"""Acceptance tests for the `owlsperch` console script (acceptance criterion 1)."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest


def test_cli_does_not_import_the_dead_segment_error_handler() -> None:
    # `run_segment` already catches its own `SegmentError` internally (see
    # `owlsperch.segment.runner.run_segment`), so a second handler for it in
    # `cli.py`'s `except` clause is unreachable dead code. Guard against
    # reintroducing the unused import alongside it.
    import owlsperch.cli as cli_module

    assert not hasattr(cli_module, "SegmentError")


def test_help_lists_manifest_command() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "owlsperch", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "manifest" in result.stdout


def test_help_lists_dev_and_fixture_db_commands() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "owlsperch", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "dev" in result.stdout
    assert "fixture-db" in result.stdout


def test_fixture_db_subcommand_builds_a_database(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    result = subprocess.run(
        [sys.executable, "-m", "owlsperch", "fixture-db", str(data_dir)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "spell: 3" in result.stdout


def test_queue_next_and_run_kind_default_to_none() -> None:
    """B10 criterion 6: `--kind` on `queue next`/`queue run` now defaults to
    `None` (every registered kind) instead of the hard-coded `"spell"`."""
    from owlsperch.cli import build_parser

    parser = build_parser()
    next_args = parser.parse_args(["queue", "next", "book", "--limit", "1"])
    assert next_args.kind is None

    run_args = parser.parse_args(["queue", "run", "book", "--dry-run"])
    assert run_args.kind is None


def test_queue_audit_subparser_wires_book_id_fix_and_json() -> None:
    from owlsperch.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(["queue", "audit", "book", "--fix", "--json"])
    assert args.command == "queue"
    assert args.queue_command == "audit"
    assert args.book_id == "book"
    assert args.fix is True
    assert args.json is True

    defaulted = parser.parse_args(["queue", "audit", "book"])
    assert defaulted.fix is False
    assert defaulted.json is False


def test_console_script_help_lists_manifest_command() -> None:
    owlsperch = shutil.which("owlsperch")
    if owlsperch is None:
        pytest.skip("owlsperch console script not found on PATH (not installed into this venv)")
    result = subprocess.run(
        [owlsperch, "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "manifest" in result.stdout
