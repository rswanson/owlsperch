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
   counted; only PASSing records are loaded. A record that passes on its own
   but collides on `id` with one already loaded (B10-mand3 -- the corpus has
   genuine name/slug collisions across segments) is likewise skipped and
   counted, rather than crashing the whole build with an unhandled
   `sqlite3.IntegrityError`.
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
7. `records.toc_category`/`toc_chapter`/`toc_section`/`toc_path` (batch
   B10b, design decision D10) -- derived, built-in columns, not
   `record_fields` rows (that would collide with the extractor-written
   `rules_section.fields.chapter` and pollute a browse item's per-field
   `facets` map). For every record, `owlsperch.toc.lookup.load_toc` is
   loaded once per book_id (cached) and `entry_for_page` is looked up
   against `min(record["pages"])` -- the deepest TOC entry containing that
   page. A record with no `pages`, a book with no USABLE
   `toc/<book_id>.json` (missing, OR present but parsed to zero entries --
   e.g. a book whose only contents-like page turned out to be a
   numbered-table index), or a page before the TOC's first entry all
   resolve to `toc_category = "uncategorized"` and null chapter/section/
   path. A book with records but no usable toc file gets exactly one
   `WARNING` line (naming `uv run owlsperch toc <book_id> --force`) from
   `run_build_db`, not a crash or a per-record warning.

Every record is `canonical = 1` and `macro_eligible = 0` in this batch --
precedence (B11) and macro eligibility (B22) are future work.

8. (Batch B10c, design decision D11) A second pass, after every record is
   loaded: for every `class`/`prestige_class` record, `fields.source_pages`
   (falling back to `min(pages)..max(pages)` when absent) gives its whole
   entry's pdf page span. Every `rules_section`/`table` record of the SAME
   book whose OWN pages fall entirely inside that span AND (batch
   B10c-mand11) that the class owns BY NAME is a
   fragment the class record replaces. Owned by name means EITHER
   `owlsperch.supersede.is_class_owned_fragment(name, type, <class name>)`
   -- the shared
   predicate the segmenter's own stamp pass uses: the class name itself,
   "Game Rule Information", "Class Skills", "Class Features",
   "Ex-<Title>", "<Race> <Title> Starting Package", or any `table` -- OR
   (this pass has the class RECORD in hand, unlike the segmenter) the
   record's normalized name being one of that class record's own printed
   headings: `_class_owned_names`, i.e. every `fields.class_features[]
   .name`, every `fields.description_sections[].heading`, and the class's
   own `name`. So the druid's own "Wild Shape"/"Venom Immunity"
   `rules_section` records are superseded, while a sidebar on the same
   pages is not. Such a record is set `canonical = 0`,
   `superseded_by =
   <class record id>` (a `records` column derived at build time, like
   `toc_category`; no record file is ever touched). The one exception: a
   `table` record OWNED by some class (its id is in that class's own
   `tables` array, or its own `fields.parent_record` names a class/
   prestige_class record) is NEVER superseded this way -- otherwise a
   class's own progression table would disappear from its own page. A
   printed SIDEBAR sharing a class's pages ("Familiars", "Alternative
   Animal Companions", "The Paladin's Mount", "School Specialization", ...)
   matches neither B10c-mand11 rule and stays canonical -- before that,
   page span alone demoted it and it
   existed in no canonical record at all. A
   record already superseded by an earlier class in the same pass is left
   alone (first class wins; spans aren't expected to overlap in practice).
   `run_build_db` prints how many records were superseded as one
   informational line -- this is expected, not a problem, so it's never a
   WARNING.

9. (Batch B10c-mand2) A record whose OWNING segment (looked up the same
   way as step 8's `extraction.segment_id`, via `owlsperch.validate.loader.
   load_segment`) itself carries `superseded_by` is skipped entirely --
   never loaded into `records` at all. This is belt-and-braces for a
   record file left behind, or restored by hand, after `owlsperch.
   supersede.release_segment_claims` should have moved it out of
   `records/<book_id>/` into `superseded/<book_id>/<type>/<file>.json`
   (see `owlsperch.segment.runner`'s class-span pass and `owlsperch queue
   audit --fix`, which perform that release at stamp time and
   retroactively, respectively) -- in the ordinary case build-db never even
   sees such a file, since it isn't under `records/` any more. Counted in
   its own `skipped_superseded` counter, rendered as its own
   "Skipped (superseded segment): N" line, and kept OUT of
   `skipped_invalid`/the "skipped N invalid record(s)" WARNING/`--strict`'s
   exit 1 -- such a record may be perfectly schema-valid, it just belongs
   to a frozen segment.

10. (Batch B10c-mand4) After every record is loaded and after
    `_apply_superseding`, one query finds every `tables` row whose
    `parent_record` names an id that never made it into `records` at all
    (missing entirely, invalid, skipped, or -- see criterion 9 above --
    superseded away): a table left dangling like this renders fine in
    `/browse/table` while `GET /records/<type>/<slug>` 404s for the entity
    it claims to belong to, with no warning anywhere. `run_build_db` prints
    each as one `WARNING` line (first 5, same shape as the "skipped N
    invalid record(s)" warning below) naming both ids; `--strict` exits 1
    when there is any (in addition to `skipped_invalid > 0`), same as that
    warning. This is purely a read-time report -- no row is loaded
    differently or dropped because of it.

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
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from owlsperch.build_db.precedence import (
    apply_precedence,
    write_precedence_report,
    write_unmatched_overrides,
)
from owlsperch.manifest import ManifestEntry, default_manifest_path, load_manifest, status_for
from owlsperch.supersede import is_class_owned_fragment, normalize_heading
from owlsperch.text.runner import default_data_dir
from owlsperch.toc.lookup import entry_for_page, load_toc
from owlsperch.toc.parser import Toc
from owlsperch.validate.checks import ValidationContext
from owlsperch.validate.loader import (
    CompiledSchemas,
    LoadError,
    discover_books_with_records,
    discover_record_files,
    load_json,
    load_segment,
)
from owlsperch.validate.runner import build_validation_context, validate_record

#: The `toc_category` every record gets when it can't be resolved to a real
#: one (no toc file for its book, no `pages`, or a page before the toc's
#: first entry) -- must match `schemas/categories.json`'s `uncategorized`
#: key (D10).
UNCATEGORIZED = "uncategorized"

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
    json TEXT NOT NULL,
    -- Derived from toc/<book_id>.json (batch B10b, D10) -- built-in
    -- pseudo-fields like `book_id`, never `record_fields` rows.
    toc_category TEXT NOT NULL DEFAULT 'uncategorized',
    toc_chapter TEXT,
    toc_section TEXT,
    toc_path TEXT,
    -- Batch B10c, design decision D11: the id of the class/prestige_class
    -- record whose page span swallows this one, or NULL. Derived at build
    -- time in the same spirit as toc_category -- no record file is ever
    -- written to.
    superseded_by TEXT,
    -- Batch B11, design decision D11: a SEPARATE axis from superseded_by --
    -- the id of the record this one is a variant printing of (an errata/
    -- update override target, a Rules Compendium override, or an older
    -- printing demoted by latest-wins), or NULL for a canonical record.
    -- Set only by the precedence pass (owlsperch.build_db.precedence).
    variant_of TEXT,
    -- Batch B11: errata/update entry ids applied to this record, as a JSON
    -- array. Set only by the precedence pass.
    applied_overrides TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX records_type_slug_idx ON records (type, slug);
CREATE INDEX records_book_id_idx ON records (book_id);
CREATE INDEX records_toc_category_idx ON records (toc_category);
CREATE INDEX records_superseded_by_idx ON records (superseded_by);
CREATE INDEX records_variant_of_idx ON records (variant_of);

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
class DanglingParent:
    """One `tables` row whose `parent_record` names an id that never made it
    into `records` -- see `_find_dangling_parents` and `BuildResult.
    dangling_parents`."""

    record_id: str
    parent_record: str


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
    #: book_ids that had records but no USABLE `toc/<book_id>.json` --
    #: missing entirely, or present with `entries == []` (D10) --
    #: `run_build_db` prints one WARNING per entry, naming
    #: `owlsperch toc <book_id> --force`.
    toc_missing_books: list[str] = field(default_factory=list)
    #: Batch B10c, design decision D11: how many `rules_section`/`table`
    #: records got `canonical = 0`/`superseded_by` set because a class/
    #: prestige_class record's page span swallows them. Expected, not a
    #: problem -- `run_build_db` prints it as information, never a WARNING.
    superseded: int = 0
    #: Batch B10c-mand2: how many record files were skipped because their
    #: OWNING segment (looked up via the record's own
    #: `extraction.segment_id`) carries `superseded_by` -- belt-and-braces
    #: for a record file left behind, or restored by hand, after
    #: `owlsperch.supersede.release_segment_claims` should have moved it out
    #: of `records/`. Counted SEPARATELY from `skipped_invalid`: such a
    #: record may well be perfectly schema-valid, so it must never trigger
    #: the "skipped N invalid record(s)" WARNING or `--strict`'s exit 1.
    skipped_superseded: int = 0
    #: Batch B10c-mand4: every `tables` row whose `parent_record` names an
    #: id that never made it into `records` (missing, invalid, skipped, or
    #: superseded away). Purely a read-time report -- nothing about how
    #: rows are loaded changes because of this.
    dangling_parents: list[DanglingParent] = field(default_factory=list)
    #: Batch B11: the precedence pass's own counters (design decision D16).
    overrides_applied: int = 0
    records_with_overrides: int = 0
    unmatched_overrides: int = 0
    rc_overrides: int = 0
    unmatched_rc: int = 0
    variants: int = 0
    #: Where `reports/precedence.md` was written, once `build_db` has
    #: finished (design decision D12) -- `None` only if the build itself
    #: never reached that point (an exception before the atomic rename).
    precedence_report_path: Path | None = None

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
        lines.append(f"Skipped (superseded segment): {self.skipped_superseded}")
        lines.append(f"Superseded (class/prestige_class span): {self.superseded}")
        lines.append(f"Dangling table parents: {len(self.dangling_parents)}")
        lines.append(f"Overrides applied: {self.overrides_applied}")
        lines.append(f"Unmatched overrides: {self.unmatched_overrides}")
        lines.append(f"Rules Compendium overrides: {self.rc_overrides}")
        lines.append(f"Variants: {self.variants}")
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


@dataclass(frozen=True)
class _RecordToc:
    """The derived `toc_category`/`toc_chapter`/`toc_section`/`toc_path`
    for one record (D10)."""

    category: str = UNCATEGORIZED
    chapter: str | None = None
    section: str | None = None
    path_json: str | None = None


def _resolve_record_toc(toc: Toc | None, pages: Any) -> _RecordToc:
    if toc is None or not isinstance(pages, list) or not pages:
        return _RecordToc()

    numeric_pages = [p for p in pages if isinstance(p, int)]
    if not numeric_pages:
        return _RecordToc()

    entry = entry_for_page(toc, min(numeric_pages))
    if entry is None:
        return _RecordToc()

    chapter: str | None
    section: str | None
    if entry.level == 1:
        chapter, section = entry.title, None
    else:
        chapter = entry.path[0] if entry.path else None
        section = entry.title

    return _RecordToc(
        category=entry.category, chapter=chapter, section=section, path_json=json.dumps(entry.path)
    )


def _insert_record(conn: sqlite3.Connection, record: dict[str, Any], toc: _RecordToc) -> None:
    record_id = record["id"]
    aliases = record.get("aliases") or []
    aliases_text = " ".join(a for a in aliases if isinstance(a, str))

    conn.execute(
        "INSERT INTO records "
        "(id, type, name, slug, book_id, aliases, record_id, canonical, macro_eligible, json, "
        "toc_category, toc_chapter, toc_section, toc_path) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
            toc.category,
            toc.chapter,
            toc.section,
            toc.path_json,
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
    context: ValidationContext,
    book_kinds: Mapping[str, str] | None = None,
) -> None:
    toc_cache: dict[str, Toc | None] = {}
    book_kinds = book_kinds or {}

    for book_id in discover_books_with_records(data_dir):
        if book_id not in toc_cache:
            loaded = load_toc(data_dir, book_id)
            # A present-but-empty toc (entries == []) is treated exactly
            # like a missing one -- both fall back to uncategorized/None
            # via `_resolve_record_toc(None, ...)` below, and both get the
            # one-line WARNING -- except an errata/update booklet (design
            # decision D17, criterion 8), which has no table of contents by
            # nature and so must never trigger this warning.
            if loaded is None or not loaded.entries:
                toc_cache[book_id] = None
                if book_kinds.get(book_id) not in ("errata", "update"):
                    result.toc_missing_books.append(book_id)
            else:
                toc_cache[book_id] = loaded
        toc = toc_cache[book_id]

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
            if segment is not None:
                superseded_by = segment.get("superseded_by")
                if isinstance(superseded_by, str) and superseded_by:
                    # Batch B10c-mand2: belt-and-braces -- a record whose
                    # owning segment is frozen (superseded) must never load,
                    # even if the file itself is perfectly schema-valid, so
                    # this must not count as "invalid".
                    result.skipped_superseded += 1
                    continue

            errors = validate_record(
                record, type_dir=type_dir, compiled=compiled, segment=segment, context=context
            )
            if errors:
                result.skipped_invalid += 1
                result.skipped.append(SkippedRecord(path=rel_path, error=errors[0]))
                continue

            record_toc = _resolve_record_toc(toc, record.get("pages"))
            try:
                _insert_record(conn, record, record_toc)
            except sqlite3.IntegrityError as exc:
                # `_insert_record`'s INSERT into `records` (id TEXT PRIMARY
                # KEY) runs first, so a duplicate id fails before any child
                # rows (record_fields/record_pages/tables) are written --
                # nothing partial is left behind. Keep that INSERT first.
                # The corpus genuinely contains name/slug collisions (two
                # record files each valid on their own but sharing one
                # <type>:<book_id>:<slug> id) -- skip the loser like any
                # other invalid record instead of aborting the whole build.
                result.skipped_invalid += 1
                result.skipped.append(
                    SkippedRecord(
                        path=rel_path,
                        error=(
                            f"duplicate record id {record['id']!r} "
                            f"(already loaded from another record file): {exc}"
                        ),
                    )
                )
                continue

            result.counts_by_type[type_dir] = result.counts_by_type.get(type_dir, 0) + 1


def _class_owned_names(class_record: Mapping[str, Any]) -> set[str]:
    """Batch B10c-mand11: the normalized (`owlsperch.supersede.
    normalize_heading`) set of printed headings ONE class record owns by
    name -- its own `name`, every `fields.class_features[].name`, and every
    `fields.description_sections[].heading`. A fragment record inside the
    class's page span whose own name is in this set is that class's own
    printed feature/section (e.g. the druid's "Wild Shape", written by the
    extractor as a separate `rules_section` before class records existed),
    so it is superseded even though its heading isn't class-structural on
    its own. Built once per class record. A parenthetical suffix the book
    prints on a feature heading ("Wild Shape (Su)") is normalized away, so
    it matches the record named plainly."""
    names = {normalize_heading(str(class_record.get("name", "")))}
    fields = class_record.get("fields")
    if isinstance(fields, Mapping):
        for feature in fields.get("class_features") or []:
            if isinstance(feature, Mapping):
                names.add(normalize_heading(str(feature.get("name", ""))))
        for section in fields.get("description_sections") or []:
            if isinstance(section, Mapping):
                names.add(normalize_heading(str(section.get("heading", ""))))
    names.discard("")
    return names


def _apply_superseding(conn: sqlite3.Connection) -> int:
    """Design decision D11's superseding pass, run once after every record
    is loaded: for every `class`/`prestige_class` record, mark
    `canonical = 0`/`superseded_by = <that record's id>` on every
    `rules_section`/`table` record of the SAME book whose own `pages` fall
    entirely inside that class's page span (`fields.source_pages`, or
    `min(pages)..max(pages)` when absent) -- EXCEPT a `table` record the
    class itself owns (its id in the class's own `tables` array, or its own
    `fields.parent_record` naming a class/prestige_class record of this
    book), which must never disappear from its own class's page, and --
    batch B10c-mand11 -- EXCEPT a record the class doesn't own by NAME.
    Unlike the segmenter's own stamp pass, this one has the class RECORD in
    hand, so it decides per record on either of two things: the shared
    `owlsperch.supersede.is_class_owned_fragment(name, type, <class name>)`
    predicate (the class title, "Game Rule Information", "Class Skills",
    "Class Features", "Ex-<Title>", "<Race> <Title> Starting Package", or
    any `table`), OR the record's normalized `name` appearing in that class
    record's own printed heading set (`_class_owned_names`: every
    `fields.class_features[].name`, every `fields.description_sections[]
    .heading`, and the class's own `name`). So "Wild Shape" or "Venom
    Immunity" inside the druid's span -- a class feature the druid record
    itself describes -- is demoted, while a printed sidebar sharing the same
    pages ("Familiars", "Alternative Animal Companions", "The Paladin's
    Mount") stays canonical; page span alone demoted both, leaving the
    sidebars in no canonical record at all. Every `table` in the span still
    passes the predicate, so the table rule above is unchanged. A record
    already superseded by an earlier class is left alone. Returns how many
    records were newly superseded."""
    class_rows = conn.execute(
        "SELECT id, book_id, name, json FROM records WHERE type IN ('class', 'prestige_class')"
    ).fetchall()

    # Every class/prestige_class id per book, and every table id any class
    # in that book already claims via its own `tables` array -- computed up
    # front so the "owned table" exclusion checks against ALL classes in
    # the book, not just whichever one is being processed right now.
    class_ids_by_book: dict[str, set[str]] = {}
    owned_table_ids_by_book: dict[str, set[str]] = {}
    for class_id, book_id, _class_name, class_json in class_rows:
        class_ids_by_book.setdefault(book_id, set()).add(class_id)
        class_record = json.loads(class_json)
        owned = class_record.get("tables")
        if isinstance(owned, list):
            owned_table_ids_by_book.setdefault(book_id, set()).update(
                t for t in owned if isinstance(t, str)
            )

    superseded = 0
    for class_id, book_id, class_name, class_json in class_rows:
        class_record = json.loads(class_json)
        class_fields = class_record.get("fields")
        source_pages = class_fields.get("source_pages") if isinstance(class_fields, dict) else None
        if (
            isinstance(source_pages, dict)
            and isinstance(source_pages.get("start"), int)
            and isinstance(source_pages.get("end"), int)
        ):
            start, end = source_pages["start"], source_pages["end"]
        else:
            pages = class_record.get("pages")
            numeric_pages = (
                [p for p in pages if isinstance(p, int)] if isinstance(pages, list) else []
            )
            if not numeric_pages:
                continue
            start, end = min(numeric_pages), max(numeric_pages)

        owned_table_ids = owned_table_ids_by_book.get(book_id, set())
        class_ids = class_ids_by_book.get(book_id, set())
        # B10c-mand11: built once per class record, not per candidate.
        owned_names = _class_owned_names(class_record)

        candidates = conn.execute(
            "SELECT id, type, name, json FROM records WHERE book_id = ? AND type IN "
            "('rules_section', 'table') AND superseded_by IS NULL AND id != ?",
            (book_id, class_id),
        ).fetchall()
        for candidate_id, candidate_type, candidate_name, candidate_json in candidates:
            if candidate_id in owned_table_ids:
                continue
            # B10c-mand11: only a fragment this class owns BY NAME is its to
            # swallow -- class-structural, or one of its own printed
            # feature/section headings. A sidebar sharing its pages stays
            # canonical.
            if (
                not is_class_owned_fragment(candidate_name, candidate_type, class_name)
                and normalize_heading(candidate_name) not in owned_names
            ):
                continue
            candidate = json.loads(candidate_json)
            if candidate_type == "table":
                candidate_fields = candidate.get("fields")
                parent = (
                    candidate_fields.get("parent_record")
                    if isinstance(candidate_fields, dict)
                    else None
                )
                if parent in class_ids:
                    continue

            candidate_pages = candidate.get("pages")
            numeric_candidate_pages = (
                [p for p in candidate_pages if isinstance(p, int)]
                if isinstance(candidate_pages, list)
                else []
            )
            if not numeric_candidate_pages or not all(
                start <= p <= end for p in numeric_candidate_pages
            ):
                continue

            conn.execute(
                "UPDATE records SET canonical = 0, superseded_by = ? WHERE id = ?",
                (class_id, candidate_id),
            )
            superseded += 1

    return superseded


def _find_dangling_parents(conn: sqlite3.Connection) -> list[DanglingParent]:
    """Batch B10c-mand4: every `tables` row whose `parent_record` names an
    id that isn't loaded into `records` at all -- run once, after every
    record is loaded and after `_apply_superseding` (a table's parent could
    only ever go missing by never loading in the first place; superseding
    itself never removes a `records` row, only flips `canonical`/
    `superseded_by`, so ordering relative to it doesn't matter, but running
    last keeps this a simple final check over the finished tables)."""
    rows = conn.execute(
        "SELECT record_id, parent_record FROM tables "
        "WHERE parent_record IS NOT NULL AND TRIM(parent_record) != '' "
        "AND parent_record NOT IN (SELECT id FROM records) "
        "ORDER BY record_id"
    ).fetchall()
    return [DanglingParent(record_id=r[0], parent_record=r[1]) for r in rows]


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
    book_kinds = {e.book_id: e.kind for e in entries}

    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = db_path.parent / f".{db_path.name}.tmp"
    if tmp_path.exists():
        tmp_path.unlink()

    context = build_validation_context(data_dir)
    result = BuildResult(books=len(entries), db_path=db_path)
    try:
        conn = sqlite3.connect(tmp_path)
        try:
            conn.executescript(_SCHEMA_SQL)
            _load_books(conn, entries)
            _load_records(
                conn,
                data_dir=data_dir,
                compiled=compiled,
                result=result,
                context=context,
                book_kinds=book_kinds,
            )
            result.superseded = _apply_superseding(conn)
            precedence = apply_precedence(conn)
            result.dangling_parents = _find_dangling_parents(conn)
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

    # Design decision D12: the report and human/ files are written only
    # AFTER the database file is atomically in place, so a failed build
    # leaves no stray report behind.
    result.overrides_applied = precedence.overrides_applied
    result.records_with_overrides = precedence.records_with_overrides
    result.unmatched_overrides = len(precedence.unmatched_overrides)
    result.rc_overrides = precedence.rc_overrides
    result.unmatched_rc = len(precedence.unmatched_rc)
    result.variants = precedence.variants
    write_unmatched_overrides(data_dir, precedence)
    result.precedence_report_path = write_precedence_report(data_dir, precedence)

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

    for book_id in result.toc_missing_books:
        print(
            f"WARNING: '{book_id}' has records but no usable toc/{book_id}.json "
            "(missing, or present with no entries) -- every one of its records is "
            f"uncategorized; run `uv run owlsperch toc {book_id} --force`",
            file=err,
        )

    if result.dangling_parents:
        print(
            f"WARNING: {len(result.dangling_parents)} table record(s) name a "
            "parent_record that is not loaded:",
            file=err,
        )
        for dangling in result.dangling_parents[:5]:
            print(f"  {dangling.record_id} -> {dangling.parent_record}", file=err)

    if result.unmatched_overrides > 0:
        # Batch B11, design decision D16: expected mid-extraction (a target
        # not extracted yet, or a genuinely unmatchable entry) -- never a
        # build failure, even under --strict.
        print(
            f"WARNING: {result.unmatched_overrides} errata/update entry(ies) could not be "
            f"matched to a target -- see {result.precedence_report_path}",
            file=err,
        )

    if strict and (result.skipped_invalid > 0 or result.dangling_parents):
        return 1
    return 0
