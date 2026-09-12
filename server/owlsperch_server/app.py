"""The `owlsperch_server` FastAPI app (spec 4.9, batch B6): `/search`,
`/records/{type}/{slug}`, `/schemas`, `/health`, reading the SQLite database
`owlsperch build-db` produces (`owlsperch.build_db.runner`).

The DB is opened read-only (`mode=ro` URI) once per request and closed
again -- this is a single-user localhost app (spec D1), so a connection pool
would be pure ceremony. A missing (not yet built) database makes every
data-serving endpoint (`/search`, `/records/{type}/{slug}`) answer 503 with
a JSON body naming the build command; `/health` and `/schemas` don't touch
the database at all (schemas come from the `schemas/` files, not the DB) and
always answer normally.

A *present but corrupt* database file is a different failure mode:
`sqlite3.connect` alone never validates the file (SQLite only checks the
header lazily, on the first real read), so a garbage file opens fine and
only raises `sqlite3.DatabaseError` once a query actually runs. Every data
endpoint routes its DB work through `_query_db`, which catches that and
answers 503 with a distinct "database unreadable" detail instead of letting
it surface as an unhandled 500.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query

from owlsperch.build_db.runner import default_db_path
from owlsperch.schemas import load_registry
from owlsperch.text.runner import default_data_dir

#: Matches "words" (Unicode letters/digits/underscore runs) in a search
#: query, tokenized the same way for the FTS5 prefix query.
_WORD_RE = re.compile(r"\w+", re.UNICODE)

_DEFAULT_LIMIT = 20
_MAX_LIMIT = 50

_DB_MISSING_DETAIL = "database not built; run: uv run owlsperch build-db"
_DB_UNREADABLE_DETAIL = "database unreadable; rebuild with: uv run owlsperch build-db"


def create_app(data_dir: Path | None = None) -> FastAPI:
    """Build the FastAPI app. `data_dir` defaults to `$OWLSPERCH_DATA`
    (`owlsperch.text.runner.default_data_dir`); tests pass an explicit temp
    directory instead."""
    resolved_data_dir = data_dir if data_dir is not None else default_data_dir()

    app = FastAPI(title="owlsperch")
    app.state.data_dir = resolved_data_dir

    @app.get("/health")
    def health() -> dict[str, Any]:
        db_path = default_db_path(app.state.data_dir)
        return {"status": "ok", "db": db_path.is_file()}

    @app.get("/schemas")
    def schemas() -> dict[str, Any]:
        return _render_schemas()

    @app.get("/search")
    def search(
        q: str = Query(...),
        types: str | None = Query(default=None),
        limit: int = Query(default=_DEFAULT_LIMIT),
    ) -> dict[str, Any]:
        if len(q.strip()) < 2:
            raise HTTPException(status_code=400, detail="q must be at least 2 characters")
        clamped_limit = max(1, min(limit, _MAX_LIMIT))
        type_filter = [t.strip() for t in types.split(",") if t.strip()] if types else None

        hits = _query_db(
            app.state.data_dir, lambda conn: _search(conn, q, type_filter, clamped_limit)
        )
        return {"groups": _group_hits(hits)}

    @app.get("/records/{type_name}/{slug}")
    def record_detail(type_name: str, slug: str) -> dict[str, Any]:
        registry = load_registry()
        if type_name not in registry.types:
            raise HTTPException(status_code=404, detail="unknown type")

        detail = _query_db(app.state.data_dir, lambda conn: _record_detail(conn, type_name, slug))
        if detail is None:
            raise HTTPException(status_code=404, detail="unknown slug")
        return detail

    return app


def _require_db(data_dir: Path) -> sqlite3.Connection:
    db_path = default_db_path(data_dir)
    if not db_path.is_file():
        raise HTTPException(status_code=503, detail=_DB_MISSING_DETAIL)
    try:
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    except sqlite3.OperationalError as exc:
        raise HTTPException(status_code=503, detail=_DB_MISSING_DETAIL) from exc
    conn.row_factory = sqlite3.Row
    return conn


def _query_db[T](data_dir: Path, fn: Callable[[sqlite3.Connection], T]) -> T:
    """Open a connection via `_require_db` (a missing DB raises 503 there,
    before `fn` ever runs), run `fn` against it, and always close it
    afterwards. A corrupt DB file can't be caught at `connect()` time --
    SQLite only validates the file header lazily, on the first real read --
    so `fn` raising `sqlite3.DatabaseError` here (rather than during
    `_require_db`) becomes a 503 naming the rebuild command too, instead of
    an unhandled 500."""
    conn = _require_db(data_dir)
    try:
        return fn(conn)
    except sqlite3.DatabaseError as exc:
        raise HTTPException(status_code=503, detail=_DB_UNREADABLE_DETAIL) from exc
    finally:
        conn.close()


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _search(
    conn: sqlite3.Connection, q: str, type_filter: list[str] | None, limit: int
) -> list[sqlite3.Row]:
    words = _WORD_RE.findall(q)
    if not words:
        return []
    fts_query = " ".join(f"{word}*" for word in words)

    type_clause = ""
    fts_params: list[Any] = [fts_query]
    if type_filter:
        placeholders = ", ".join("?" for _ in type_filter)
        type_clause = f" AND r.type IN ({placeholders})"
        fts_params.extend(type_filter)
    fts_params.append(limit)

    fts_sql = (
        "SELECT r.id, r.type, r.name, r.slug, r.book_id, r.json "
        "FROM names_fts f JOIN records r ON r.rowid = f.rowid "
        "WHERE names_fts MATCH ? AND r.canonical = 1" + type_clause + " LIMIT ?"
    )
    try:
        rows = list(conn.execute(fts_sql, fts_params))
    except sqlite3.OperationalError:
        # A malformed FTS5 query string (shouldn't happen -- tokens are
        # word-only) falls back to the substring pass below instead of 500ing.
        rows = []

    seen = {row["id"] for row in rows}
    if len(rows) < limit:
        substr_clause = ""
        substr_params: list[Any] = [f"%{_escape_like(q)}%"]
        if type_filter:
            placeholders = ", ".join("?" for _ in type_filter)
            substr_clause = f" AND type IN ({placeholders})"
            substr_params.extend(type_filter)
        substr_params.append(limit - len(rows))

        substr_sql = (
            "SELECT id, type, name, slug, book_id, json FROM records "
            "WHERE canonical = 1 AND name LIKE ? ESCAPE '\\'" + substr_clause + " LIMIT ?"
        )
        for row in conn.execute(substr_sql, substr_params):
            if row["id"] not in seen:
                rows.append(row)
                seen.add(row["id"])

    return rows[:limit]


def _group_hits(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    registry = load_registry()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        record = json.loads(row["json"])
        grouped.setdefault(row["type"], []).append(
            {
                "id": row["id"],
                "type": row["type"],
                "name": row["name"],
                "slug": row["slug"],
                "citation": record.get("citation"),
                "book_id": row["book_id"],
            }
        )

    groups = []
    for type_name in sorted(grouped):
        info = registry.types.get(type_name)
        label = info.plural_label if info is not None else type_name
        groups.append({"type": type_name, "label": label, "hits": grouped[type_name]})
    return groups


def _record_detail(conn: sqlite3.Connection, type_name: str, slug: str) -> dict[str, Any] | None:
    rows = list(
        conn.execute(
            "SELECT r.id, r.json, b.published FROM records r "
            "LEFT JOIN books b ON b.book_id = r.book_id "
            "WHERE r.type = ? AND r.slug = ? AND r.canonical = 1",
            (type_name, slug),
        )
    )
    if not rows:
        return None

    # Spec 4.5/4.7 step 5: before precedence collapses duplicates (B11),
    # more than one canonical record can share type+slug across books --
    # pick the one from the latest-published book, list the rest as variants.
    rows.sort(key=lambda row: row["published"] or "", reverse=True)

    winner: dict[str, Any] = json.loads(rows[0]["json"])
    winner["variants"] = [row["id"] for row in rows[1:]]
    winner["links"] = []
    winner["referenced_by"] = []
    winner.setdefault("tables", [])
    return winner


def _render_schemas() -> dict[str, Any]:
    registry = load_registry()
    types: dict[str, Any] = {}
    for type_name, info in registry.types.items():
        schema = registry.load_type_schema(type_name)
        fields = [
            {"name": field_name, "x-ui": prop.get("x-ui", {})}
            for field_name, prop in schema.get("properties", {}).items()
        ]
        types[type_name] = {
            "label": info.label,
            "plural_label": info.plural_label,
            "version": info.version,
            "fields": fields,
        }
    return {"types": types}


def main() -> None:
    """Console-script entry point (`owlsperch-server`); `uv run owlsperch
    serve` is the documented way to start this, but the script is handy for
    running the server package standalone (e.g. `uv run --package
    owlsperch-server owlsperch-server`)."""
    import uvicorn

    uvicorn.run(create_app(), host="127.0.0.1", port=8000)
