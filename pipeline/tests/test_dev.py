"""Tests for `owlsperch.dev` (batch B7, spec 4.10): `build_dev_commands`,
the part of `owlsperch dev` that decides what to run and where -- actually
spawning uvicorn/npm and streaming their output isn't meaningfully
unit-testable, so it's factored out on its own (see module docstring)."""

from __future__ import annotations

import sys
from pathlib import Path

from owlsperch.dev import build_dev_commands


def test_build_dev_commands_runs_api_serve_then_web_dev(tmp_path: Path) -> None:
    processes = build_dev_commands(repo_root=tmp_path)
    assert [p.prefix for p in processes] == ["api", "web"]


def test_build_dev_commands_api_process_runs_owlsperch_serve(tmp_path: Path) -> None:
    processes = build_dev_commands(repo_root=tmp_path)
    api_proc = processes[0]
    assert api_proc.command == [sys.executable, "-m", "owlsperch", "serve"]
    assert api_proc.cwd == tmp_path


def test_build_dev_commands_web_process_runs_npm_run_dev_in_web_dir(tmp_path: Path) -> None:
    processes = build_dev_commands(repo_root=tmp_path)
    web_proc = processes[1]
    assert web_proc.command == ["npm", "run", "dev"]
    assert web_proc.cwd == tmp_path / "web"
