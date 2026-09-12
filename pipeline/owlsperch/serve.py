"""`owlsperch serve`: starts the `owlsperch_server` FastAPI app under
uvicorn (spec 4.9, batch B6).

`owlsperch_server` (and its `fastapi`/`uvicorn` dependencies) lives in the
sibling `server/` workspace member, not in this package's own dependency
list -- `pipeline` has no formal dependency on `server` (that would make the
two packages depend on each other, since `server` already depends on
`pipeline` for schema/registry loading and data-dir resolution). Both are
installed into the one shared uv workspace virtual environment, so the
import below works after `uv sync` at the repo root; it's done lazily,
inside `run_serve`, so importing `owlsperch.cli` never requires uvicorn or
FastAPI to be installed (e.g. a minimal pipeline-only environment).
"""

from __future__ import annotations

import sys

#: Matches spec 4.9 ("FastAPI on localhost:8000").
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


def run_serve(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> int:
    try:
        import uvicorn
    except ImportError:
        print(
            "error: uvicorn is not installed -- run `uv sync` from the repo "
            "root (it's a dependency of the server/ workspace member)",
            file=sys.stderr,
        )
        return 1

    from owlsperch_server.app import create_app

    uvicorn.run(create_app(), host=host, port=port)
    return 0
