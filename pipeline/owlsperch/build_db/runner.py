"""Orchestration for `owlsperch build-db` (spec 4.8, batch B6).

Builds `$OWLSPERCH_DATA/db/owlsperch.sqlite` from scratch, from records
under `records/<book_id>/<type>/*.json`:

1. `books` -- one row per manifest entry (book_id, title, short_title, kind,
   edition, published, and its `manifest.status_for` status), regardless of
   whether that book has any records yet.
2. Every record under `records/<book_id>/<type>/*.json` (across every book
   with a `records/` directory, not just manifest-listed ones) is
   re-validated with `owlsperch.validate.runner.validate_record` -- the same
   JSON Schema conformance, envelope/type consistency, and
   page-within-segment checks `owlsperch validate` runs, reusing the
   `owlsperch.validate.loader` machinery, but *without* validate's
   write-back to segment files: build-db is a read-only build step and must
   not mutate segments as a side effect. A record that fails is skipped and
   counted; only PASSing records are loaded.
3. `records` -- one row per loaded record (id, type, name, slug, book_id,
   canonical, macro_eligible, the full record `json`), plus an `aliases`
   column (space-joined, not part of the spec's column list but needed so
   `names_fts`'s `content='records'` external-content link has a real
   `aliases` column to read from).
4. `record_fields` -- the record's `fields` object flattened to one row per
   scalar value: a scalar field is one row; a list of scalars is one row per
   element (same key); a list of objects (e.g. spell `levels`) is one row
   per sub-field, keyed `"<key>.<subkey>"` (e.g. `levels.class`,
   `levels.level`), plus one *combined* row keyed `"<key>"` joining that
   object's non-null values with spaces (e.g. `"Cleric 3"`) so class+level
   pairs can be filtered together later; a plain nested object (e.g. spell
   `costs`) is flattened one level, `"<key>.<subkey>"`.

   The combined row's values are joined in SORTED subkey order, not the
   record JSON's own key order -- JSON Schema doesn't guarantee `items`'
   `properties` are declared (or an extracted record's keys written) in any
   particular order, so both sides of this contract need an order that
   needs no schema: `flatten_fields` here, and
   `owlsperch_server.browse.load_type_browse_schema`/`build_filter_clauses`
   on the read side, independently sort an item's sub-properties by name.
   For spell `levels` (`class` < `level` alphabetically) this happens to
   match the schema's declared order already, so no rebuild of existing
   records is required by this change -- it only matters if a future
   array-of-object field's sub-properties sort differently than declared,
   or a record's own JSON key order varies between items.
5. `record_pages` -- one row per page the record cites.
6. `names_fts` -- an FTS5 table over `name`/`aliases`, external-content
   linked to `records` by rowid (`content='records'`, `content_rowid`), with
   an unindexed `record_id` column for joins back to `records.id` (the FTS
   table's own `rowid` already equals `records.rowid` via the content link,
   but `record_id` gives callers `records.id` directly without a join).

Every record is `canonical = 1` and `macro_eligible = 0` in this batch --
precedence (B11) and macro eligibility (B22) are future work.

The whole build writes to a temp file in the same directory as the final
`db/owlsperch.sqlite` and only then atomically renames it into place
(`os.replace`), so a server reading the DB mid-build never sees a
half-written file -- it sees either the previous build or the new one,
never a partial one. Rerunning is idempotent: the DB is always fully
dropped and rebuilt from the current records on disk.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from owlsperch.manifest import ManifestEntry, default_manifest_path, load_manifest, status_for
from owlsperch.text.runner import default_data_dir
from owlsperch.validate.loader import (
    CompiledSchemas,
    LoadError,
    discover_books_with_records,
    discover_record_files,
    load_json,
    load_segment,
)
from owlsperch.validate.runner import validate_record

#: `db/owlsperch.sqlite`, relative to `$OWLSPERCH_DATA` (spec 4.2).
DB_RELATIVE_PATH = Path("db") / "owlsperch.sqlite"


def default_db_path(data_dir: Path | None = None) -> Path:
    data_dir = data_dir if data_dir is not None else default_data_dir()
    return data_dir / DB_RELATIVE_PATH


_SCHEMA_SQL = """
CREATE TABLE books (
    book_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    short_title TEXT,
    kind TEXT NOT NULL,
    edition TEXT NOT NULL,
    published TEXT,
    status TEXT NOT NULL
);

CREATE TABLE records (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    name TEXT NOT NULL,
    slug TEXT NOT NULL,
    book_id TEXT NOT NULL,
    aliases TEXT NOT NULL DEFAULT '',
    -- Duplicates `id`. names_fts is an FTS5 *external content* table
    -- (content='records'): every FTS5 column, indexed or not, is fetched
    -- straight from a same-named column of the content table on read, not
    -- stored in the FTS index itself. Its `record_id` column therefore
    -- needs a real `records.record_id` column to read from -- `id` itself
    -- can't fill that role since the FTS5 column has to be named
    -- `record_id` to match the acceptance criteria.
    record_id TEXT NOT NULL,
    canonical INTEGER NOT NULL,
    macro_eligible INTEGER NOT NULL,
    json TEXT NOT NULL
);
CREATE INDEX records_type_slug_idx ON records (type, slug);
CREATE INDEX records_book_id_idx ON records (book_id);

CREATE TABLE record_fields (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id TEXT NOT NULL,
    key TEXT NOT NULL,
    text_value TEXT,
    num_value REAL
);
CREATE INDEX record_fields_record_id_idx ON record_fields (record_id);
CREATE INDEX record_fields_key_idx ON record_fields (key);

CREATE TABLE record_pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id TEXT NOT NULL,
    book_id TEXT NOT NULL,
    page INTEGER NOT NULL
);
CREATE INDEX record_pages_record_id_idx ON record_pages (record_id);
CREATE INDEX record_pages_book_page_idx ON record_pages (book_id, page);

CREATE VIRTUAL TABLE names_fts USING fts5(
    name,
    aliases,
    record_id UNINDEXED,
    content='records',
    content_rowid='rowid'
);

CREATE TABLE tables (
    record_id TEXT PRIMARY KEY,
    book_id TEXT NOT NULL,
    caption TEXT,
    columns TEXT NOT NULL,
    rows TEXT NOT NULL,
    parent_record TEXT
);
CREATE INDEX tables_parent_record_idx ON tables (parent_record);
"""


@dataclass
class _FieldRow:
    key: str
    text_value: str | None
    num_value: float | None


def _scalar_row(key: str, value: Any) -> _FieldRow | None:
    """A single-value row for `value`, or `None` if `value` isn't a scalar
    (i.e. it's a list or dict the caller must recurse into instead)."""
    if isinstance(value, bool):
        return _FieldRow(key, "true" if value else "false", None)
    if isinstance(value, (int, float)):
        return _FieldRow(key, None, float(value))
    if isinstance(value, str):
        return _FieldRow(key, value, None)
    return None


def flatten_fields(key: str, value: Any) -> list[_FieldRow]:
    """Flatten one `fields` entry into `record_fields` rows, per this
    module's docstring point 4. The combined row for an array-of-object
    field is built from each item's non-null sub-values in SORTED subkey
    order -- see the docstring's note on that contract."""
    if value is None:
        return []

    scalar = _scalar_row(key, value)
    if scalar is not None:
        return [scalar]

    if isinstance(value, list):
        if not value:
            return []
        if all(isinstance(item, list) for item in value):
            # A list-of-lists (e.g. table `rows`, the grid body) produces NO
            # `record_fields` rows -- that content lives in the dedicated
            # `tables` table instead (populated separately in
            # `_insert_record` for `type == "table"` records), not flattened
            # into individually-searchable scalar rows here.
            return []
        if all(isinstance(item, dict) for item in value):
            # Array-of-object (e.g. spell `levels`): one row per sub-field,
            # plus one combined row so pairs (e.g. class+level) stay
            # queryable together. Sorted subkey order (not the item's own
            # JSON key order) keeps the combined value canonical -- see
            # module docstring.
            rows: list[_FieldRow] = []
            for item in value:
                parts: list[str] = []
                for subkey in sorted(item):
                    subvalue = item[subkey]
                    if subvalue is None:
                        continue
                    rows.extend(flatten_fields(f"{key}.{subkey}", subvalue))
                    parts.append(str(subvalue))
                if parts:
                    rows.append(_FieldRow(key, " ".join(parts), None))
            return rows
        rows = []
        for item in value:
            rows.extend(flatten_fields(key, item))
        return rows

    if isinstance(value, dict):
        rows = []
        for subkey, subvalue in value.items():
            rows.extend(flatten_fields(f"{key}.{subkey}", subvalue))
        return rows

    return []


@dataclass
class SkippedRecord:
    """One record skipped as invalid: its path (relative to `$OWLSPERCH_DATA`)
    and the first error that failed it -- either the load/parse error, or
    (when it loaded fine but didn't validate) the first entry of
    `validate_record`'s error list."""

    path: str
    error: str


@dataclass
class BuildResult:
    counts_by_type: dict[str, int] = field(default_factory=dict)
    skipped_invalid: int = 0
    #: Detail for every skipped record (see `SkippedRecord`), in discovery
    #: order. `run_build_db` prints the first few of these as a warning.
    skipped: list[SkippedRecord] = field(default_factory=list)
    books: int = 0
    db_path: Path = field(default_factory=Path)

    def render(self) -> str:
        lines = ["Records loaded by type:"]
        if self.counts_by_type:
            for type_name in sorted(self.counts_by_type):
                lines.append(f"  {type_name}: {self.counts_by_type[type_name]}")
        else:
            lines.append("  (none)")
        total = sum(self.counts_by_type.values())
        lines.append(f"  total: {total}")
        lines.append(f"Skipped (invalid): {self.skipped_invalid}")
        lines.append(f"Books: {self.books}")
        lines.append(f"DB: {self.db_path}")
        return "\n".join(lines)


def _load_books(conn: sqlite3.Connection, entries: list[ManifestEntry]) -> None:
    conn.executemany(
        "INSERT INTO books (book_id, title, short_title, kind, edition, published, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (
                e.book_id,
                e.title,
                e.short_title,
                e.kind,
                e.edition,
                e.published,
                status_for(e, entries),
            )
            for e in entries
        ],
    )


def _insert_record(conn: sqlite3.Connection, record: dict[str, Any]) -> None:
    record_id = record["id"]
    aliases = record.get("aliases") or []
    aliases_text = " ".join(a for a in aliases if isinstance(a, str))

    conn.execute(
        "INSERT INTO records "
        "(id, type, name, slug, book_id, aliases, record_id, canonical, macro_eligible, json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            record_id,
            record["type"],
            record["name"],
            record["slug"],
            record["book_id"],
            aliases_text,
            record_id,
            1,  # canonical: every record is canonical in this batch; precedence is B11.
            0,  # macro_eligible: computed in B22.
            json.dumps(record),
        ),
    )

    fields = record.get("fields")
    if isinstance(fields, dict):
        field_rows: list[_FieldRow] = []
        for key, value in fields.items():
            field_rows.extend(flatten_fields(key, value))
        if field_rows:
            conn.executemany(
                "INSERT INTO record_fields (record_id, key, text_value, num_value) "
                "VALUES (?, ?, ?, ?)",
                [(record_id, r.key, r.text_value, r.num_value) for r in field_rows],
            )

    pages = record.get("pages")
    if isinstance(pages, list):
        page_rows = [(record_id, record["book_id"], p) for p in pages if isinstance(p, int)]
        if page_rows:
            conn.executemany(
                "INSERT INTO record_pages (record_id, book_id, page) VALUES (?, ?, ?)",
                page_rows,
            )

    if record["type"] == "table" and isinstance(fields, dict):
        conn.execute(
            "INSERT INTO tables (record_id, book_id, caption, columns, rows, parent_record) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                record_id,
                record["book_id"],
                fields.get("caption"),
                json.dumps(fields.get("columns") or []),
                json.dumps(fields.get("rows") or []),
                fields.get("parent_record"),
            ),
        )


def _load_records(
    conn: sqlite3.Connection,
    *,
    data_dir: Path,
    compiled: CompiledSchemas,
    result: BuildResult,
) -> None:
    for book_id in discover_books_with_records(data_dir):
        for path in discover_record_files(data_dir, book_id):
            type_dir = path.parent.name
            rel_path = path.relative_to(data_dir).as_posix()
            try:
                record = load_json(path)
            except LoadError as exc:
                result.skipped_invalid += 1
                result.skipped.append(SkippedRecord(path=rel_path, error=str(exc)))
                continue

            segment_id: str | None = None
            extraction = record.get("extraction")
            if isinstance(extraction, dict):
                seg_id_raw = extraction.get("segment_id")
                segment_id = seg_id_raw if isinstance(seg_id_raw, str) else None

            segment = load_segment(data_dir, book_id, segment_id) if segment_id else None
            errors = validate_record(record, type_dir=type_dir, compiled=compiled, segment=segment)
            if errors:
                result.skipped_invalid += 1
                result.skipped.append(SkippedRecord(path=rel_path, error=errors[0]))
                continue

            _insert_record(conn, record)
            result.counts_by_type[type_dir] = result.counts_by_type.get(type_dir, 0) + 1


def build_db(
    *,
    data_dir: Path | None = None,
    manifest_path: Path | None = None,
    schemas_dir: Path | None = None,
    db_path: Path | None = None,
) -> BuildResult:
    data_dir = data_dir if data_dir is not None else default_data_dir()
    manifest_path = manifest_path if manifest_path is not None else default_manifest_path()
    db_path = db_path if db_path is not None else default_db_path(data_dir)

    entries = load_manifest(manifest_path)
    compiled = CompiledSchemas.load(schemas_dir)

    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = db_path.parent / f".{db_path.name}.tmp"
    if tmp_path.exists():
        tmp_path.unlink()

    result = BuildResult(books=len(entries), db_path=db_path)
    try:
        conn = sqlite3.connect(tmp_path)
        try:
            conn.executescript(_SCHEMA_SQL)
            _load_books(conn, entries)
            _load_records(conn, data_dir=data_dir, compiled=compiled, result=result)
            conn.execute(
                "INSERT INTO names_fts (rowid, name, aliases, record_id) "
                "SELECT rowid, name, aliases, record_id FROM records"
            )
            conn.commit()
        finally:
            conn.close()
        os.replace(tmp_path, db_path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise

    return result


def run_build_db(
    *,
    data_dir: Path | None = None,
    manifest_path: Path | None = None,
    schemas_dir: Path | None = None,
    strict: bool = False,
    out: Any = None,
    err: Any = None,
) -> int:
    """Run `build_db` and print its summary to `out`. Skipping invalid
    records is expected while extraction is still in progress (not every
    segment has a validated record yet), so this is a WARNING to `err`, not
    a failure, unless `strict` is set -- e.g. a release/CI build that must
    catch every record before shipping."""
    out = out if out is not None else sys.stdout
    err = err if err is not None else sys.stderr
    result = build_db(data_dir=data_dir, manifest_path=manifest_path, schemas_dir=schemas_dir)
    print(result.render(), file=out)

    if result.skipped_invalid > 0:
        print(
            f"WARNING: skipped {result.skipped_invalid} invalid record(s) "
            "(expected while extraction is in progress):",
            file=err,
        )
        for skipped in result.skipped[:5]:
            print(f"  {skipped.path}: {skipped.error}", file=err)

    if strict and result.skipped_invalid > 0:
        return 1
    return 0
