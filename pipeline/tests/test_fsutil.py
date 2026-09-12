"""Tests for `owlsperch.fsutil.atomic_write_text`."""

from __future__ import annotations

from pathlib import Path

import pytest

from owlsperch.fsutil import atomic_write_text


def test_atomic_write_text_writes_content(tmp_path: Path) -> None:
    path = tmp_path / "out.txt"
    atomic_write_text(path, "hello\n")
    assert path.read_text() == "hello\n"


def test_atomic_write_text_overwrites_existing_content(tmp_path: Path) -> None:
    path = tmp_path / "out.txt"
    path.write_text("old\n")
    atomic_write_text(path, "new\n")
    assert path.read_text() == "new\n"


def test_atomic_write_text_leaves_original_unchanged_when_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "out.txt"
    path.write_text("original\n")

    def _boom(src: str, dst: str) -> None:
        raise OSError("simulated os.replace failure")

    monkeypatch.setattr("owlsperch.fsutil.os.replace", _boom)

    with pytest.raises(OSError, match="simulated os.replace failure"):
        atomic_write_text(path, "new\n")

    assert path.read_text() == "original\n"
    # No leftover temp file from the failed write.
    leftovers = [p for p in tmp_path.iterdir() if p.name != "out.txt"]
    assert leftovers == []


def test_atomic_write_text_no_leftover_temp_file_on_success(tmp_path: Path) -> None:
    path = tmp_path / "out.txt"
    atomic_write_text(path, "content\n")
    assert list(tmp_path.iterdir()) == [path]
