# owlsperch

A D&D 3.5e reference-data pipeline: it curates a manifest of the source PDFs,
extracts and segments their content, and eventually builds a queryable
SQLite/API/web reference site. See
`docs/specs/2026-09-12-dnd-reference-site-spec.md` for the full spec and
`docs/batches/2026-09-12-dnd-reference-site-batches.md` for the build order.

Batch B1 delivered the repo scaffold, the `owlsperch` CLI, and the curated
book manifest (`manifest check`). Batch B2 adds `text`, which extracts
column-repaired page text for text-layer (non-scanned) books.

## Environment variables

- `OWLSPERCH_PDFS` -- directory the source PDFs are read from (never copied).
  Default: `~/D_D`.
- `OWLSPERCH_DATA` -- directory where extracted JSON, the SQLite build, OCR
  output, and rendered page images are written. Default: `~/owlsperch-data`.

## Setup

Install [uv](https://docs.astral.sh/uv/), then from the repo root:

```sh
uv sync
```

This repo root is a uv *workspace*: the root `pyproject.toml` has no
`[project]` table of its own (a "virtual" workspace root) and just declares
`pipeline` and `server` as members. uv shares one lockfile/virtual
environment across the workspace, so the `owlsperch` console script that
`pipeline/pyproject.toml` declares is available from the repo root without
`cd`-ing into `pipeline/` -- and `server/`'s `owlsperch_server` package
(FastAPI + uvicorn, depending on `owlsperch` for schema/registry loading
and data-dir resolution) is installed into that same environment, so `uv
run owlsperch serve` can import it.

## Usage

```sh
uv run owlsperch manifest check
```

Validates `pipeline/manifest.yaml` against the files in `$OWLSPERCH_PDFS`
(default `~/D_D`) and prints counts by kind and by in-scope status (in-scope /
override / index / excluded / duplicate). An entry named in another entry's
`preferred_over` (a shadowed duplicate copy -- `preferred_over` accepts either
a single book_id or a list, for a book with more than one duplicate) is
reported as `duplicate` rather than as an independent in-scope book. Exits
non-zero and lists any file present in
the PDF directory but missing from the manifest, or any manifest entry whose
file is missing from the directory.

```sh
uv run owlsperch text <book_id|all> [--force] [--pages A-B]
```

Extracts one column-repaired text file per PDF page for a text-layer
(non-scanned) book, via `pdftotext -bbox-layout` (poppler). For each book:

- A `scanned: true` book is refused (OCR extraction is a future batch, B15)
  -- for a single `book_id` this is a non-zero exit; inside `all` it is
  printed as a per-book skip line and the run continues.
- A book whose manifest status (see `manifest check`) is not `in_scope` or
  `override` is skipped. Override sources (errata, update, web_enhancement)
  are still extracted, since they need their own text.
- `all` runs every eligible book in the manifest and prints one summary
  line per book (pages written / skipped, headers removed, page numbers
  found, or the reason it was skipped/refused).
- An unknown `book_id` is a clear error, exit 1.

`--pages A-B` limits extraction to that inclusive PDF page range (1-based)
instead of the whole book -- useful for trying a few pages by hand. Header/
footer detection and dehyphenation only consider the pages in that range.

`--force` re-writes page files that already exist; by default, a page whose
output file is already present is left alone and counted as "skipped" (the
command is idempotent -- rerunning it does no extra work).

Output layout, under `$OWLSPERCH_DATA` (default `~/owlsperch-data`):

- `text/<book_id>/p0001.txt`, ... -- one file per PDF page (1-based,
  4-digit), each page's text reconstructed in reading order (columns left
  to right, top to bottom within a column), with running headers/footers
  removed and hyphenated line breaks rejoined. Blocks are separated by a
  blank line (a paragraph break).
- `text/<book_id>/pages.json` -- maps a PDF page index (as a string) to the
  printed page number found in that page's header/footer band, e.g.
  `{"120": 119}`. A page where no printed number was found is simply absent
  from the map.
- `text/<book_id>/p0001.meta.json`, ... -- one sidecar per page text file,
  listing per-paragraph stats for `segment`'s heading detection (the plain
  `.txt` carries no font information). A JSON list with one entry per
  output paragraph, in the same order as the blank-line-separated
  paragraphs in the matching `.txt`: `{"kind": "prose" | "table",
  "median_word_height": <float>, "max_word_height": <float>, "line_count":
  <int>}`. `median_word_height`/`max_word_height` are the glyph-height
  stats (in PDF points) across the paragraph's words; `line_count` is how
  many physical PDF lines made up the paragraph before dehyphenation
  joined them (or how many table rows, for a `"table"` paragraph).

```sh
uv run owlsperch segment <book_id|all> [--force] [--pages A-B]
```

Splits a book's already-extracted text (`text/<book_id>/`, see `text`
above) into candidate segments: spans of text tagged with a `kind_hint`
that a later extraction step (batch B4/B5) will validate against a
schema. Requires that book's text output to already exist -- and that
every page it processes has a `.meta.json` sidecar (see above); a page
missing one is a clear error naming `owlsperch text <book_id> --force`.
Segmentation runs over the whole book's text as one continuous stream (not
page by page), so a segment may span a page boundary.

Four pattern anchors are recognized, per spec 4.4:

- **spell** -- a short name line immediately followed by a school line (one
  of the eight schools of magic plus "Universal", optionally with a
  parenthesised subschool and/or a bracketed descriptor, e.g. "Evocation
  [Fire]").
- **stat_block** -- a "Size/Type:" or "Hit Dice:" line; the segment's
  `heading` is the nearest preceding heading or short line (the monster's
  name).
- **feat** -- a short name line (optionally with a bracketed type, e.g.
  "Power Attack [General]") followed within two paragraphs by a line
  starting "Prerequisite" or "Benefit".
- **table** -- a "Table N-M:" caption; the segment runs through the
  tab-separated rows that follow, plus any footnote lines starting with a
  digit.

Everything else becomes a `rules_section` segment, split further at
headings -- a heading is a single-line paragraph whose `median_word_height`
is at least 1.15x the book's body-text median, or an all-uppercase line of
at most 8 words. Segments are never empty and every page with any text
lands in at least one segment (front matter before the first heading
included); `owlsperch segment` warns on stderr if that ever fails to hold.

`--force` and `--pages A-B` behave as they do for `text`: existing segment
files are kept unless `--force` is given, and `--pages` limits which pages
are read from `text/<book_id>/`.

Output layout, under `$OWLSPERCH_DATA`:

- `segments/<book_id>/<seg_id>.json` -- one file per segment. `seg_id` is
  `<book_id>-p<NNNN>-<NN>`: the segment's first PDF page, then a
  per-first-page ordinal, both assigned in stream order so rerunning
  produces identical ids. Fields: `book_id`, `pages` (every PDF page index
  the segment spans), `printed_pages` (same length and order as `pages`,
  from `pages.json`; `null` for a page with no detected printed number),
  `kind_hint` (`spell` | `stat_block` | `feat` | `table` | `rules_section`),
  `heading` (the section heading the segment falls under, or the anchor's
  name), `text`, `status` (`"pending"`), `tier` (`"haiku"`), `attempts`
  (`[]`), `created_at` (an ISO timestamp).

`segment all` iterates every in-scope/override book that has a `text/`
directory, printing one summary line per book (counts by `kind_hint`, or
why it was skipped) -- a book with no `text/` directory yet is skipped with
a message pointing at `owlsperch text`.

```sh
uv run owlsperch validate <book_id|all> [--json] [--stale]
```

Checks every record under `records/<book_id>/<type>/*.json` against
`schemas/` (JSON Schema draft 2020-12): the record envelope against
`schemas/envelope.json`, and its `fields` against the type's own schema
(e.g. `schemas/spell.json`), both loaded once via `schemas/registry.json`.
On top of schema conformance it checks that the record's `type` matches
the directory it was found in, that `slug`/`id` follow the
`<type>:<book_id>:<slug>` convention (`slug` is the ASCII-folded
kebab-case of `name`), that `schema_version` matches the type's current
registry version, type-specific consistency (e.g. a spell needs a
non-empty `school` and at least one entry in `levels`), and that every
page the record cites falls within its originating segment's page span
(the segment named by `extraction.segment_id`, under `segments/<book_id>/`
-- a missing segment file is a FAIL).

Prints one `PASS <path>` or `FAIL <path>: <reason>; <reason>` line per
record plus a final pass/fail summary, and exits 1 if any record failed.
`--json` prints a JSON array of `{path, status, errors, segment_id, type}`
to stdout instead (nothing else on stdout), for the extract skill.
`--stale` instead lists records whose `schema_version` is behind the
type's current registry version and always exits 0.

Validation writes back to the originating segment: PASS sets its `status`
to `done` and `outcome` to `validated` and appends the record's path to
its `records` list; FAIL appends `{tier, timestamp, errors}` to its
`attempts` and leaves `status` pending. Both are idempotent -- rerunning
does not add a duplicate record path or a duplicate identical attempt.

```sh
uv run owlsperch schema show <type>
```

Prints the type's schema file path and a table of its fields' `x-ui`
hints (label, filterable, sortable, group, order) -- useful for humans, and
the same schema files back the extract skill's prompt (see "Running
extraction" below).

The `schemas/` directory (repo root, alongside `pipeline/`) is the single
source of truth for record types: `schemas/envelope.json` is the common
record envelope (spec 4.6), `schemas/registry.json` lists every type with
its schema file, UI labels, and schema version, and `schemas/<type>.json`
(e.g. `schemas/spell.json`) defines that type's `fields`. Its location is
found by walking up from the `owlsperch` package to a directory containing
`schemas/registry.json`, overridable via `$OWLSPERCH_SCHEMAS`.

## Running extraction

```
/extract <book_id|all> [--limit N] [--parallel N=8] [--kind K]
```

In Claude Code, `/extract phb1 --limit 20` runs the `.claude/skills/extract/`
skill: it fans out haiku-tier Agent-tool subagents over `phb1`'s pending
`spell` segments (this batch only has a schema for `spell`; other
`kind_hint`s are skipped), validates what they write, and prints a summary.
Records land under `records/phb1/spell/`. Default parallelism is 8; pass
`all` to run every book with a `segments/` directory.

Under the hood, the skill drives a small CLI of its own, which is also
directly usable (e.g. to debug or resume a stuck run):

```sh
uv run owlsperch queue next <book_id> --tier haiku --limit N [--kind spell] --json
uv run owlsperch queue prompt <seg_id>
uv run owlsperch queue complete <seg_id> --result <json-file-or-'-'>
uv run owlsperch queue summary <book_id> [--json]
uv run owlsperch queue reset <seg_id>...
```

- `queue next` selects up to N `pending` segments on the given tier and
  kind_hint, marks them `in_progress` (with a timestamp), and prints
  `{seg_id, segment_path, kind_hint, prompt_path}` for each -- rendering
  every selected segment's subagent prompt as it goes.
- `queue prompt <seg_id>` (re-)renders one segment's prompt on demand,
  writing it to `prompts/<book_id>/<seg_id>.md` and printing that path. The
  prompt is generated from the segment, the manifest, and `schemas/` --
  book metadata, the segment text verbatim, the candidate schema(s)
  rendered live from their JSON files, and the exact output contract (the
  absolute output directory, `id`/`slug`/`citation` rules, the
  `extraction` block, `schema_version`, and the required
  `{"seg_id", "records", "no_content", "notes"}` response shape).
- `queue complete <seg_id> --result <file|->` ingests a subagent's final
  JSON reply (a file path, or `-` for stdin): claimed record paths go onto
  the segment's `pending_records` and its `status` returns to `pending` so
  `validate` can run; `no_content` marks the segment `done` with that
  outcome and reason; anything malformed (bad JSON, wrong shape) appends a
  `malformed_result` attempt and returns the segment to `pending` on the
  same tier.
- `owlsperch validate <book_id> --json` (see above) then promotes a path
  from `pending_records` to `records` on PASS, or drops it (without ever
  reaching `records`) on FAIL.
- `queue summary <book_id> [--json]` prints segment counts by
  status/tier/outcome/kind_hint plus how many record files exist.
- `queue reset <seg_id>...` is a manual escape hatch: puts one or more
  segments back to `pending` and clears `in_progress_since` (e.g. after
  killing a run partway through, or undoing a manual `queue next`). It is
  not part of the normal loop -- a stuck segment being auto-reset after a
  timeout is a future batch (B8).

```sh
uv run owlsperch toc <book_id|all> [--force]
```

Parses a text-layer book's own table of contents into
`$OWLSPERCH_DATA/toc/<book_id>.json`: dotted-leader entries ("Title ...
122") on the first ~12 pages become a chapter/section tree with pdf page
spans, and each entry gets a player-facing `category` (Character creation,
Races, Classes, Skills, Feats, Equipment, Combat, Adventuring, Magic,
Monsters, Running the game, Game basics, or Uncategorized -- the committed
list in `schemas/categories.json`) resolved from a generic title-pattern
table plus small per-book overrides in `pipeline/owlsperch/toc/categories.py`.
A book whose contents page can't be found, or that yields too few entries,
is reported as failed rather than writing an empty file. Idempotent;
`--force` re-parses. `build-db` (below) uses this to derive every record's
`category`/`chapter`/`section` -- run `toc` before (or after) `build-db`,
in either order, but a book with records and no toc file just means every
one of its records comes back `uncategorized` until you do.

```sh
uv run owlsperch build-db [--strict]
```

Builds `$OWLSPERCH_DATA/db/owlsperch.sqlite` from every record under
`records/<book_id>/<type>/*.json` that passes the same checks `owlsperch
validate` runs (an invalid record is skipped and counted, not loaded).
Always a full rebuild: writes to a temp file next to the target and
atomically renames it into place, so a running server never sees a
half-built database, and rerunning is idempotent. Prints records loaded by
type, how many were skipped as invalid, how many books, and the DB path.
Every record is `canonical: true` and `macro_eligible: false` in this batch
-- precedence (duplicate-copy resolution across books) and macro
eligibility are future batches.

Skipping invalid records is expected while extraction is still in
progress, so by default `build-db` still exits 0 even when
`Skipped (invalid)` is nonzero -- but it prints a `WARNING` to stderr
naming the count and the first 5 skipped paths with each one's first
error, so a skip is never silent. Pass `--strict` (e.g. for a release
build that must not ship with any skipped record) to make that same
situation exit 1 instead.

Tables (spec 4.8): `books` (one row per manifest entry, regardless of
whether it has records yet); `records` (id, type, name, slug, book_id,
canonical, macro_eligible, the full record as `json`, plus the derived
`toc_category`/`toc_chapter`/`toc_section`/`toc_path` columns -- built-in
pseudo-fields like `book_id`, resolved from `toc/<book_id>.json` per record,
never `record_fields` rows); `record_fields` (the record's `fields`
flattened to one row per scalar value -- a list of scalars is one row per
element, and a list of objects like spell `levels` is one row per
sub-field, e.g. `levels.class`/`levels.level`, plus one combined `levels`
row like `"Cleric 3"` so class+level pairs stay queryable together);
`record_pages` (one row per cited page); `names_fts` (FTS5 over name +
aliases, for `/search`).

```sh
uv run owlsperch serve [--host 127.0.0.1] [--port 8000]
```

Starts the `owlsperch_server` FastAPI app (`server/`, a second uv workspace
member alongside `pipeline/`) under uvicorn, reading the database from
`$OWLSPERCH_DATA`. `owlsperch_server` depends on the `owlsperch` pipeline
package for schema/registry loading and data-dir resolution; `uvicorn` and
`fastapi` are only in `server/`'s dependencies, imported lazily by `owlsperch
serve` so a pipeline-only checkout never needs them installed just to import
`owlsperch.cli`.

```sh
curl 'localhost:8000/health'
# {"status":"ok","db":true}

curl 'localhost:8000/search?q=fireb'
# {"groups":[{"type":"spell","label":"Spells","hits":[{"id":"spell:phb1:fireball", ...}]}]}

curl 'localhost:8000/records/spell/fireball'
# {"id":"spell:phb1:fireball", ..., "variants":[], "links":[], "referenced_by":[], "tables":[]}

curl 'localhost:8000/schemas'
# {"types":{"spell":{"label":"Spell","plural_label":"Spells","version":1,"fields":[...]}}}
```

`GET /search?q=<str>&types=<comma list>&limit=<int, default 20, max 50>`
runs an FTS5 prefix query (each word tokenized, `*`-suffixed) over
`names_fts`, falling back to a case-insensitive substring scan over
`records.name` when FTS returns fewer than `limit` hits (deduplicated);
canonical only; grouped by type. `q` shorter than 2 characters is a 400.

`GET /records/{type}/{slug}` returns the full record plus `variants`
(other canonical records sharing type+slug across books, before batch
B11's precedence resolution collapses them -- the one from the
latest-published book wins the main response) and placeholder `links`,
`referenced_by`, `tables` for later batches, plus (batch B10b) `book_title`
and a `toc` block (`category`, `category_label`, `chapter`, `section`,
`path`) derived from `toc/<book_id>.json`. Unknown type or slug is a 404
with a JSON `{"detail": ...}` body.

`GET /records/{type}` and `GET /facets/{type}` (filtered/sorted/paginated
browse lists and their facet counts, per the type schema's `x-ui` hints)
additionally accept `category=`/`chapter=` alongside the built-in `source=`
pseudo-field (batch B10b); `/facets/{type}` gains a `category` facet
ordered by `schemas/categories.json`'s own order, not by count. There is
deliberately no `chapter` facet -- the web UI's `/browse/rules_section`
category -> chapter -> section tree (`?view=tree`, the default for
`rules_section`, available for any type) is the chapter navigator instead.

A missing (not yet built) database makes `/search` and
`/records/{type}/{slug}` answer 503 with `{"detail": "database not built;
run: uv run owlsperch build-db"}`; `/health` and `/schemas` don't touch the
database and always answer normally (`/health`'s `db` field is `false`).

## Running the site

`web/` is a Vite + React + TypeScript app (spec 4.10, batch B7): `/` is a
search box, `/browse/:type` a filtered/sorted/paginated list with a facet
sidebar (batch B9) -- or, for `/browse/rules_section` (batch B10b), a
category -> chapter -> section tree instead, with a "Rules" link and
category quick links in the header nav -- and `/r/:type/:slug` a record
page with a `Book > Chapter > Section` breadcrumb.

```sh
uv run owlsperch dev
```

Starts both halves of local development together: the FastAPI server
(`uv run owlsperch serve`, on `127.0.0.1:8000`) and the Vite dev server
(`npm run dev` in `web/`, on `127.0.0.1:5173`), as child processes -- their
output is streamed to this one terminal with `[api]`/`[web]` prefixes, and
Ctrl-C stops both. `make dev` is a thin wrapper around the same command.
Open <http://localhost:5173>.

The Vite dev server proxies `/api/*` to the FastAPI server, stripping the
`/api` prefix (see `web/vite.config.ts`'s `server.proxy`) -- so the frontend
always calls e.g. `/api/search?q=...`, and that becomes `GET
127.0.0.1:8000/search?q=...`. This means the same frontend code works
whether it's served by Vite in dev or (in a future batch) built and served
behind a reverse proxy using the same `/api` convention.

The database has to exist first (`uv run owlsperch build-db`, see above) --
otherwise the home page shows an empty state naming that command instead of
a search box.

## Development

```sh
uv run ruff check .
uv run ruff format --check .
uv run mypy pipeline server
uv run pytest pipeline/tests server/tests
```

Run a single test:

```sh
uv run pytest pipeline/tests/test_manifest.py::test_35_book_is_in_scope
```

### `web/`

```sh
cd web
npm install
npm run lint        # eslint (typescript-eslint, react-hooks)
npm run typecheck   # tsc --noEmit
npm test -- --run   # vitest (add --run to skip watch mode)
npm run e2e         # Playwright, against a fresh fixture database
```

`make lint` and `make test` (from the repo root) run the Python and `web/`
checks together.

Run a single vitest test:

```sh
cd web && npx vitest run src/components/__tests__/SearchBox.test.tsx -t "debounces"
```

Run a single Playwright test (there's currently one spec file with one
test):

```sh
cd web && npx playwright test e2e/smoke.spec.ts
```

The Playwright smoke test starts its own backend, from scratch, against a
small synthetic database built by `uv run owlsperch fixture-db <dir>`
(`web/e2e/serve-fixture.py`; see `web/playwright.config.ts`'s `webServer`) --
it never touches `$OWLSPERCH_DATA` or the real PDF corpus.

Some tests are marked `@pytest.mark.corpus` and run against the real
`$OWLSPERCH_PDFS`/`~/D_D` directory (`manifest check`; `text phb1` over
pages 118-122; `segment phb1` over pages 195-230, which needs `pdftotext`
on `PATH`; and a `server/tests/test_corpus.py` test that builds a database
from whatever `phb1` spell records exist under `$OWLSPERCH_DATA` and checks
`/search` finds one); they are skipped automatically (not failed) when the
corpus or `pdftotext` isn't present, e.g. in CI.
