"""Acceptance tests for the `owlsperch` console script (acceptance criterion 1)."""

from __future__ import annotations

import subprocess
import sys


def test_help_lists_manifest_command() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "owlsperch", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "manifest" in result.stdout
