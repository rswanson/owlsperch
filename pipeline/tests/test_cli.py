"""Acceptance tests for the `owlsperch` console script (acceptance criterion 1)."""

from __future__ import annotations

import shutil
import subprocess
import sys

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
