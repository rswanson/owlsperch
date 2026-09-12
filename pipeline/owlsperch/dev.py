"""`owlsperch dev` (batch B7, spec 4.10): the one root command that starts
both halves of local development -- the FastAPI server (uvicorn, via
`owlsperch serve`) and the Vite dev server (`npm run dev` in `web/`) -- as
child processes, streams both processes' output with `[api]`/`[web]`
prefixes, and shuts both down together on Ctrl-C (or `SIGTERM`).

The heavy lifting (spawning processes, threading stdout to this process's
stdout, signal handling) is not unit-testable in any meaningful way without
actually starting uvicorn and npm; `build_dev_commands` -- the part that
decides *what* to run and *where* -- is factored out so it can be tested on
its own.
"""

from __future__ import annotations

import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from typing import IO

#: pipeline/owlsperch/dev.py -> pipeline/owlsperch -> pipeline -> repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]
WEB_DIR = REPO_ROOT / "web"


@dataclass(frozen=True)
class DevProcess:
    prefix: str
    command: list[str]
    cwd: Path


def build_dev_commands(*, repo_root: Path | None = None) -> list[DevProcess]:
    """The two child processes `owlsperch dev` runs: the API server (run the
    same way `owlsperch serve` documents, via `python -m owlsperch serve` so
    it works from any cwd/venv without relying on the `owlsperch` console
    script being on `PATH`) and the Vite dev server."""
    repo_root = repo_root if repo_root is not None else REPO_ROOT
    return [
        DevProcess(
            prefix="api",
            command=[sys.executable, "-m", "owlsperch", "serve"],
            cwd=repo_root,
        ),
        DevProcess(
            prefix="web",
            command=["npm", "run", "dev"],
            cwd=repo_root / "web",
        ),
    ]


def _stream_output(prefix: str, pipe: IO[bytes], out: IO[str]) -> None:
    for raw_line in iter(pipe.readline, b""):
        line = raw_line.decode(errors="replace").rstrip("\n")
        print(f"[{prefix}] {line}", file=out, flush=True)


def _raise_keyboard_interrupt(signum: int, frame: FrameType | None) -> None:
    # SIGINT already does this by default (Python's default SIGINT handler
    # raises KeyboardInterrupt); SIGTERM's default disposition is to just
    # terminate the process, which would skip the `finally` cleanup below
    # and leave the child `serve`/`npm run dev` processes running -- so
    # SIGTERM is remapped to the same KeyboardInterrupt path explicitly.
    raise KeyboardInterrupt


def run_dev(*, out: IO[str] | None = None) -> int:
    out = out if out is not None else sys.stdout
    processes = build_dev_commands()
    previous_sigterm_handler = signal.signal(signal.SIGTERM, _raise_keyboard_interrupt)

    popens: list[subprocess.Popen[bytes]] = []
    threads: list[threading.Thread] = []
    try:
        for proc in processes:
            popen = subprocess.Popen(
                proc.command,
                cwd=proc.cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            popens.append(popen)
            assert popen.stdout is not None
            thread = threading.Thread(
                target=_stream_output, args=(proc.prefix, popen.stdout, out), daemon=True
            )
            thread.start()
            threads.append(thread)

        print("[dev] both servers starting -- press Ctrl-C to stop both", file=out, flush=True)
        while all(popen.poll() is None for popen in popens):
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        print("[dev] shutting down both servers...", file=out, flush=True)
        for popen in popens:
            if popen.poll() is None:
                popen.terminate()
        for popen in popens:
            try:
                popen.wait(timeout=10)
            except subprocess.TimeoutExpired:
                popen.kill()
                popen.wait()
        for thread in threads:
            thread.join(timeout=2)
        signal.signal(signal.SIGTERM, previous_sigterm_handler)

    return 0
