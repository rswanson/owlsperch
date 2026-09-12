"""Acceptance tests for the `owlsperch` console script (acceptance criterion 1)."""

from __future__ import annotations

import shutil
import subprocess
import sys

import pytest


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
