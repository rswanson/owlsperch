"""Builds the fixture SQLite database (via `owlsperch.fixture_db`, batch B7)
into a fresh temporary data directory and serves it with the
`owlsperch_server` FastAPI app on 127.0.0.1:8000, for the Playwright smoke
test's backend (see `../playwright.config.ts`'s `webServer`).

Run via `uv run python e2e/serve-fixture.py` from `web/` -- `uv run` walks up
to the repo root's workspace `pyproject.toml`/`uv.lock` regardless of cwd, so
both `owlsperch` and `owlsperch_server` are already installed into that
shared environment.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import uvicorn

from owlsperch.fixture_db import write_fixture_data
from owlsperch_server.app import create_app


def main() -> None:
    data_dir = Path(tempfile.mkdtemp(prefix="owlsperch-e2e-"))
    write_fixture_data(data_dir)
    uvicorn.run(create_app(data_dir=data_dir), host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
