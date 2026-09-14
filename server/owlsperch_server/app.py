"""The `owlsperch_server` FastAPI app (spec 4.9, batch B6): `/search`,
`/records/{type}/{slug}`, `/schemas`, `/health`, plus `/stats` (batch B7,
spec 4.10 -- canonical record counts by type, for the web UI's home-page
hint), `/records/{type}` and `/facets/{type}` (batch B9 -- filtered/sorted/
paginated browse lists and their facet counts, per the type schema's
`x-ui` hints; the SQL and param parsing live in `owlsperch_server.browse`,
kept out of this module), reading the SQLite database `owlsperch build-db`
produces (`owlsperch.build_db.runner`). `/records/{type}/{slug}` also
carries a `toc` block (`category`, `category_label`, `chapter`, `section`,
`path`) and `book_title`, derived by `build-db` from `toc/<book_id>.json`
(batch B10b) -- `category` is always a real key (`"uncategorized"` at
worst); `chapter`/`section`/`path` are `null`/`[]` when unresolved, but
always present as keys (design decision D18).

The DB is opened read-only (`mode=ro` URI) once per request and closed
again -- this is a single-user localhost app (spec D1), so a connection pool
would be pure ceremony. A missing (not yet built) database makes every
data-serving endpoint (`/search`, `/records/{type}/{slug}`, `/records/{type}`,
`/facets/{type}`) answer 503 with a JSON body naming the build command;
`/health` and `/schemas` don't touch the database at all (schemas come from
the `schemas/` files, not the DB) and always answer normally.

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

from fastapi import FastAPI, HTTPException, Query, Request

from owlsperch.build_db.runner import default_db_path
from owlsperch.schemas import load_categories, load_registry
from owlsperch.text.runner import default_data_dir
from owlsperch_server.browse import (
    BrowseError,
    compute_facets,
    list_records,
    load_type_browse_schema,
    parse_query,
)

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

    @app.get("/stats")
    def stats() -> dict[str, Any]:
        counts = _query_db(app.state.data_dir, _record_counts_by_type)
        return {"counts": counts}

    @app.get("/records/{type_name}")
    def records_list(type_name: str, request: Request) -> dict[str, Any]:
        registry = load_registry()
        if type_name not in registry.types:
            raise HTTPException(status_code=404, detail="unknown type")
        schema = load_type_browse_schema(registry, type_name)
        try:
            parsed = parse_query(schema, request.query_params.multi_items())
        except BrowseError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        return _query_db(
            app.state.data_dir, lambda conn: list_records(conn, type_name, schema, parsed)
        )

    @app.get("/facets/{type_name}")
    def facets_for_type(type_name: str, request: Request) -> dict[str, Any]:
        registry = load_registry()
        if type_name not in registry.types:
            raise HTTPException(status_code=404, detail="unknown type")
        schema = load_type_browse_schema(registry, type_name)
        try:
            parsed = parse_query(schema, request.query_params.multi_items())
        except BrowseError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        return _query_db(
            app.state.data_dir,
            lambda conn: compute_facets(conn, type_name, schema, parsed.filters),
        )

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


def _record_counts_by_type(conn: sqlite3.Connection) -> dict[str, int]:
    """Counts by type for `GET /stats` (spec 4.10, batch B7): canonical
    records only, matching what `/search` and browse would ever return."""
    rows = conn.execute("SELECT type, COUNT(*) AS n FROM records WHERE canonical = 1 GROUP BY type")
    return {row["type"]: row["n"] for row in rows}


def _resolve_tables(conn: sqlite3.Connection, table_ids: list[Any]) -> list[dict[str, Any]]:
    """Resolve a record's own `tables` id list (spec edge case: "Record
    references a table that failed") to one uniform object per id, IN THE
    RECORD'S OWN ORDER: a resolved table's `{id, pending: false, name, slug,
    caption, columns, rows, citation, book_id}` (name/slug/citation come
    from `records`, caption/columns/rows from `tables`, joined by
    `record_id`), or `{id, pending: true, ...empty}` for an id with no
    matching row (the table's own extraction hasn't landed yet -- the UI
    shows a "table pending" marker instead of erroring the whole record).
    A malformed stored `columns`/`rows` JSON value falls back to `[]` rather
    than raising."""
    ids = [t for t in table_ids if isinstance(t, str)]
    if not ids:
        return []

    placeholders = ", ".join("?" for _ in ids)
    rows_by_id: dict[str, sqlite3.Row] = {}
    for row in conn.execute(
        "SELECT t.record_id AS id, t.caption, t.columns, t.rows, t.parent_record, "
        "r.name, r.slug, r.book_id, r.json AS record_json "
        "FROM tables t JOIN records r ON r.id = t.record_id "
        f"WHERE t.record_id IN ({placeholders})",
        ids,
    ):
        rows_by_id[row["id"]] = row

    resolved: list[dict[str, Any]] = []
    for table_id in ids:
        row = rows_by_id.get(table_id)
        if row is None:
            resolved.append(
                {
                    "id": table_id,
                    "pending": True,
                    "name": None,
                    "slug": None,
                    "caption": None,
                    "columns": [],
                    "rows": [],
                    "citation": None,
                    "book_id": None,
                }
            )
            continue

        try:
            columns = json.loads(row["columns"])
            if not isinstance(columns, list):
                columns = []
        except (json.JSONDecodeError, TypeError):
            columns = []
        try:
            grid_rows = json.loads(row["rows"])
            if not isinstance(grid_rows, list):
                grid_rows = []
        except (json.JSONDecodeError, TypeError):
            grid_rows = []

        record = json.loads(row["record_json"])
        resolved.append(
            {
                "id": table_id,
                "pending": False,
                "name": row["name"],
                "slug": row["slug"],
                "caption": row["caption"],
                "columns": columns,
                "rows": grid_rows,
                "citation": record.get("citation"),
                "book_id": row["book_id"],
            }
        )

    return resolved


_RECORD_DETAIL_SELECT = (
    "SELECT r.id, r.json, r.toc_category, r.toc_chapter, r.toc_section, r.toc_path, "
    "r.superseded_by, r.canonical, r.variant_of, r.applied_overrides, "
    "b.published, COALESCE(b.short_title, b.title) AS book_title FROM records r "
    "LEFT JOIN books b ON b.book_id = r.book_id "
    "WHERE r.type = ? AND r.slug = ? AND r.canonical = ?"
)


def _resolve_variants(conn: sqlite3.Connection, record_id: str) -> list[dict[str, Any]]:
    """Batch B11, design decision D19: every OTHER printing of `record_id`
    -- records whose `variant_of` column points at it, directly OR
    transitively -- as `{id, book_id, book_title, citation, published}`,
    ordered by `published` DESC then `book_id` ASC (SQLite sorts NULL
    `published` last in DESC order already, matching the "no date" case).
    `build_db.precedence._flatten_variant_chains` keeps every stored
    `variant_of` at most one hop from its root, so a plain equality query
    would normally suffice; this walks the recursive closure anyway as
    defence in depth against a chain the pipeline no longer writes (a
    hand-edited or pre-B11 database, say). The recursive CTE walks
    `records.variant_of` (indexed by `records_variant_of_idx`) outward
    from `record_id`; `UNION` (not `UNION ALL`) de-dupes visited ids, so a
    cycle in the stored data still terminates instead of looping forever,
    and `WHERE r.id <> ?` excludes the seed itself in case some row's
    `variant_of` ever pointed at its own id."""
    rows = conn.execute(
        "WITH RECURSIVE chain(id) AS ("
        "SELECT id FROM records WHERE variant_of = ? "
        "UNION "
        "SELECT r.id FROM records r JOIN chain c ON r.variant_of = c.id"
        ") "
        "SELECT r.id, r.book_id, r.json, b.published, "
        "COALESCE(b.short_title, b.title) AS book_title "
        "FROM chain "
        "JOIN records r ON r.id = chain.id "
        "LEFT JOIN books b ON b.book_id = r.book_id "
        "WHERE r.id <> ? "
        "ORDER BY b.published DESC, r.book_id ASC, r.id ASC",
        (record_id, record_id),
    ).fetchall()
    variants = []
    for row in rows:
        record = json.loads(row["json"])
        variants.append(
            {
                "id": row["id"],
                "book_id": row["book_id"],
                "book_title": row["book_title"],
                "citation": record.get("citation"),
                "published": row["published"],
            }
        )
    return variants


def _resolve_applied_overrides(
    conn: sqlite3.Connection, override_ids: list[Any]
) -> list[dict[str, Any]]:
    """Batch B11, design decision D19: a record's stored `applied_overrides`
    id list resolved to one object per id, IN STORED ORDER: `{id, name,
    type, book_id, book_title, citation, target_page, replacement_text}`.
    An id with no matching record row is skipped (never raise) -- like
    `_resolve_tables`, malformed stored data degrades gracefully rather
    than failing the whole record."""
    ids = [i for i in override_ids if isinstance(i, str)]
    if not ids:
        return []

    placeholders = ", ".join("?" for _ in ids)
    rows_by_id: dict[str, sqlite3.Row] = {}
    for row in conn.execute(
        "SELECT r.id, r.name, r.type, r.book_id, r.json, "
        "COALESCE(b.short_title, b.title) AS book_title "
        "FROM records r LEFT JOIN books b ON b.book_id = r.book_id "
        f"WHERE r.id IN ({placeholders})",
        ids,
    ):
        rows_by_id[row["id"]] = row

    resolved: list[dict[str, Any]] = []
    for override_id in ids:
        row = rows_by_id.get(override_id)
        if row is None:
            continue
        record = json.loads(row["json"])
        fields = record.get("fields") if isinstance(record.get("fields"), dict) else {}
        resolved.append(
            {
                "id": row["id"],
                "name": row["name"],
                "type": row["type"],
                "book_id": row["book_id"],
                "book_title": row["book_title"],
                "citation": record.get("citation"),
                "target_page": fields.get("target_page"),
                "replacement_text": fields.get("replacement_text"),
            }
        )
    return resolved


def _applied_overrides_column(row: sqlite3.Row) -> list[Any]:
    try:
        value = json.loads(row["applied_overrides"])
    except (json.JSONDecodeError, TypeError):
        return []
    return value if isinstance(value, list) else []


def _record_detail(conn: sqlite3.Connection, type_name: str, slug: str) -> dict[str, Any] | None:
    rows = list(conn.execute(_RECORD_DETAIL_SELECT, (type_name, slug, 1)))
    if not rows:
        # Batch B10c, design decision D12: a record a class/prestige_class
        # span superseded (canonical = 0) still resolves by direct URL --
        # only search/browse/facets stay canonical-only. Batch B11: a
        # record demoted by precedence (a variant whose own slug differs
        # from its winner's -- the Rules Compendium case) ALSO lands here,
        # and must still resolve `variant_of`/`applied_overrides` fully,
        # not the pre-B11 empty shape.
        superseded_rows = list(conn.execute(_RECORD_DETAIL_SELECT, (type_name, slug, 0)))
        if not superseded_rows:
            return None
        row = superseded_rows[0]
        winner: dict[str, Any] = json.loads(row["json"])
        winner["canonical"] = bool(row["canonical"])
        winner["variant_of"] = row["variant_of"]
        winner["variants"] = _resolve_variants(conn, row["id"])
        winner["applied_overrides"] = _resolve_applied_overrides(
            conn, _applied_overrides_column(row)
        )
        winner["links"] = []
        winner["referenced_by"] = []
        winner["tables"] = _resolve_tables(conn, winner.get("tables") or [])
        winner["book_title"] = row["book_title"]
        winner["superseded_by"] = row["superseded_by"]
        category_labels = {c.key: c.label for c in load_categories()}
        winner["toc"] = {
            "category": row["toc_category"],
            "category_label": category_labels.get(row["toc_category"], row["toc_category"]),
            "chapter": row["toc_chapter"],
            "section": row["toc_section"],
            "path": json.loads(row["toc_path"]) if row["toc_path"] is not None else [],
        }
        return winner

    # Spec 4.5/4.7 step 5: precedence (B11) collapses same-type+slug
    # duplicates across books at build time, so this normally finds exactly
    # one row; kept as a harmless safety net for more than one canonical
    # row still sharing type+slug -- pick the latest-published, same as
    # before B11.
    rows.sort(key=lambda row: row["published"] or "", reverse=True)
    row = rows[0]

    winner = json.loads(row["json"])
    winner["canonical"] = bool(row["canonical"])
    winner["variant_of"] = row["variant_of"]
    winner["variants"] = _resolve_variants(conn, row["id"])
    winner["applied_overrides"] = _resolve_applied_overrides(conn, _applied_overrides_column(row))
    winner["links"] = []
    winner["referenced_by"] = []
    winner["tables"] = _resolve_tables(conn, winner.get("tables") or [])
    winner["book_title"] = row["book_title"]
    winner["superseded_by"] = row["superseded_by"]
    category_labels = {c.key: c.label for c in load_categories()}
    winner["toc"] = {
        "category": row["toc_category"],
        "category_label": category_labels.get(row["toc_category"], row["toc_category"]),
        "chapter": row["toc_chapter"],
        "section": row["toc_section"],
        "path": json.loads(row["toc_path"]) if row["toc_path"] is not None else [],
    }
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
