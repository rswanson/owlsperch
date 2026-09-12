# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Current state

`owlsperch` is a Python (uv-managed) pipeline plus, as of batch B6, a
FastAPI server, plus, as of batch B7, a Vite/React/TypeScript web frontend.
It has: the `owlsperch` CLI and package (`pipeline/owlsperch/`), the curated
PDF manifest (`pipeline/manifest.yaml`), `manifest check`, `text`
(column-repaired per-page text extraction for text-layer books, plus a
`.meta.json` sidecar of paragraph font-size stats), `segment` (splits a
book's text into candidate spell/stat_block/feat/table/rules_section
segments), the `schemas/` type registry (envelope + spell schema, JSON
Schema draft 2020-12) with `validate` and `schema show`, the `queue`
extraction-queue CLI plus the `/extract` Claude Code skill (haiku tier only;
see "Extraction" and "Architecture" below), `build-db` (builds
`db/owlsperch.sqlite` from validated records), `serve` (starts the
`owlsperch_server` FastAPI app -- `/search`, `/records/{type}/{slug}`,
`/schemas`, `/health`, `/stats`), `dev` (runs `serve` and `web/`'s Vite dev
server together), `fixture-db <dir>` (builds a small synthetic database for
local UI/e2e testing), and `web/` (the search-box + record-page frontend).
CI. Everything else is a future-batch stub (`check-completeness`,
`coverage`, `schema review`, `sample`, and `web/`'s own `/browse`,
`/tools/*` routes from spec 4.10).

### Commands

Run from the repo root (a uv workspace with `pipeline` and `server` as its
members -- see the comment in the root `pyproject.toml`):

```sh
uv sync                              # install deps (both workspace members)
uv run owlsperch manifest check
uv run owlsperch text <book_id|all> [--force] [--pages A-B]
uv run owlsperch segment <book_id|all> [--force] [--pages A-B]
uv run owlsperch validate <book_id|all> [--json] [--stale] [--bump-compatible]
uv run owlsperch schema show <type>
uv run owlsperch queue next <book_id> --tier haiku --limit N [--kind spell] [--model M] [--lock-timeout S] [--json]
uv run owlsperch queue prompt <seg_id> [--model M]
uv run owlsperch queue complete <seg_id> --result <json-file-or-'-'>
uv run owlsperch queue summary <book_id> [--json]
uv run owlsperch queue reset <seg_id>... [--hard]
uv run owlsperch build-db [--strict] # (re)builds $OWLSPERCH_DATA/db/owlsperch.sqlite
uv run owlsperch serve [--host H] [--port P]  # FastAPI on 127.0.0.1:8000 by default
uv run owlsperch dev                  # serve + `npm run dev` in web/, together (Ctrl-C stops both)
uv run owlsperch fixture-db <dir>     # small synthetic DB into <dir>, for local UI/e2e testing
uv run ruff check .
uv run ruff format --check .
uv run mypy pipeline server
uv run pytest pipeline/tests server/tests   # run all tests
uv run pytest pipeline/tests/test_manifest.py::test_35_book_is_in_scope  # single test
```

`web/` (batch B7, spec 4.10): the frontend, at `http://localhost:5173` once
`uv run owlsperch dev` (or `make dev`) is running.

```sh
cd web && npm install
cd web && npm run lint          # eslint (typescript-eslint, react-hooks)
cd web && npm run typecheck     # tsc --noEmit
cd web && npm test -- --run     # vitest
cd web && npx vitest run src/components/__tests__/SearchBox.test.tsx -t "debounces"  # single test
cd web && npm run e2e           # Playwright: desktop flow A + a 400px project, against a fixture DB
cd web && npx playwright test e2e/smoke.spec.ts  # single Playwright test (there's only the one)
```

`web/vite.config.ts`'s dev server proxies `/api/*` to the FastAPI server on
`127.0.0.1:8000`, stripping the `/api` prefix -- `web/src/api.ts` always
calls `/api/...`, so the same frontend code works dev or (behind a future
reverse proxy) in production, as long as whatever's in front of it honors
the same convention. `make dev`/`make test`/`make lint` at the repo root are
thin wrappers combining the Python and `web/` commands above.

Some tests are marked `@pytest.mark.corpus`: they run `manifest check`,
`text`, and `segment` on page ranges of the real Player's Handbook (book_id
`phb1`), against the real `$OWLSPERCH_PDFS`/`~/D_D` directory and
`pdftotext` (poppler); and `server/tests/test_corpus.py` builds a database
from whatever `phb1` spell records exist under `$OWLSPERCH_DATA` and checks
`/search` finds one. All are skipped (not failed) when the corpus or
`pdftotext` isn't present.

### Architecture

- `pipeline/owlsperch/manifest.py` -- the `ManifestEntry` pydantic schema,
  YAML loading/validation (raises `ManifestError` naming the offending
  book_id and field), in-scope/status derivation, and the `manifest check`
  report.
- `pipeline/owlsperch/cli.py` / `__main__.py` -- the `owlsperch` argparse CLI.
- `pipeline/manifest.yaml` -- the curated manifest: one entry per file in
  `$OWLSPERCH_PDFS`, with judgment calls marked by a plain `#` comment and
  genuinely uncertain ones marked `# REVIEW:`.
- `pipeline/owlsperch/text/` -- the `text` subcommand: `bbox.py` parses
  `pdftotext -bbox-layout` XHTML (stripping invalid XML control characters
  a handful of real PDFs' fonts produce); `columns.py` reconstructs reading
  order (column clustering, wide-block column breaks, rotated marginalia
  dropped, and table-group detection); `cleanup.py` removes running
  headers/footers, detects printed page numbers, and rejoins hyphenated
  line breaks; `runner.py` orchestrates the `pdftotext` subprocess and
  writes `text/<book_id>/p{NNNN}.txt` + `p{NNNN}.meta.json` (per-paragraph
  `kind`/`median_word_height`/`max_word_height`/`line_count`, for
  `segment`'s heading detection) + `pages.json` under `$OWLSPERCH_DATA`.
  `wordlist.txt` is the bundled fallback word list used by dehyphenation.
  Table detection excludes prose-like blocks from grouping, requires
  mutual (not merely transitive) vertical overlap among a candidate
  group's blocks -- overlap of at least 70% of the shorter block's height
  *and* at least 50% of the taller block's height, and at least 2
  non-blank lines to even be a candidate -- and -- for a single block
  whose gappy rows split into long, sentence-like cells (median > 4
  words/cell) rather than short table cells -- splits that block into
  separate column blocks at the gap instead of reading it row-wise. The
  two-sided overlap test (not just the shorter block's) is what rejects a
  short, single-line block (e.g. a heading) that happens to sit fully
  nested inside a much taller neighboring block's y-range (e.g. PHB pp.
  197, 204's "Acid Fog" heading between the "Acid Splash"/"Air Walk" stat
  blocks) without a real row-aligned table underneath it. Known remaining
  real-corpus limitation: this does *not* catch the case where several
  real, unrelated blocks of *similar* height and short, label:value-style
  lines (e.g. two or three side-by-side spell stat blocks, each a
  Level:/Components:/Casting Time:/... list) sit in the same y-range --
  they mutually satisfy both overlap fractions and still get merged into
  one bogus tab-joined table row (e.g. PHB pp. 254, 272's "Mind Fog" and
  "Repel Wood" spells) -- not fixed as part of this B3 follow-up.
- `pipeline/owlsperch/segment/` -- the `segment` subcommand: `headings.py`
  defines the book-wide `Paragraph` stream (a page's `.txt` paragraphs
  joined with their `.meta.json` stats) and heading detection (font-size
  vs. the book's body median, or an all-caps short line); `anchors.py`
  detects spell/stat_block/feat/table pattern anchors; `splitter.py` walks
  the stream once to produce the final segment spans (anchors, plus
  `rules_section` for everything between them, split at headings);
  `runner.py` loads a book's text+meta, calls the splitter, and writes
  `segments/<book_id>/<seg_id>.json`.

- `schemas/` (repo root, not under `pipeline/`) -- the single source of truth
  for record types (spec 4.6, 4.14): `envelope.json` is the common record
  envelope, `registry.json` lists every type (schema file, label, plural
  label, schema version), and `<type>.json` (currently just `spell.json`)
  defines that type's `fields`, each carrying an `x-ui` hint (label,
  filterable, sortable, group, order). All JSON Schema draft 2020-12.
  `pipeline/owlsperch/schemas.py` finds this directory by walking up from
  the package to one containing `schemas/registry.json` (overridable via
  `$OWLSPERCH_SCHEMAS`), loads the registry, and renders `schema show`.
  `schemas/examples/<type>.json` (currently just `spell.json`) is an
  invented, schema-valid example record for that type, rendered verbatim
  into extraction prompts (`owlsperch.queue.prompt`) and checked against its
  own schema by a self-test in `test_schemas.py`.
- `pipeline/owlsperch/validate/` -- the `validate` subcommand: `loader.py`
  discovers record/segment files and compiles the envelope/type JSON Schema
  validators once per run; `checks.py` holds the id/slug/type-directory/
  schema_version consistency checks plus type-specific field checks (spell:
  non-empty `school` and `levels`); `runner.py` orchestrates conformance +
  consistency + the page-within-segment-span check (segment looked up by
  `extraction.segment_id`), prints PASS/FAIL (or `--json`/`--stale`), and
  writes the outcome back to the originating segment (via the `Segment`
  model) idempotently.

- `pipeline/owlsperch/queue/` -- the `queue` subcommand (`next`, `prompt`,
  `complete`, `summary`, `reset`), the Python side of the `/extract` skill
  (spec 4.5, batch B5): `select.py` picks pending segments for a tier/kind
  and marks them `in_progress`, holding an exclusive `flock` on
  `segments/<book_id>/.queue.lock` for the whole select-and-mark operation
  (`--lock-timeout`, default 30s) so two concurrent `queue next` runs can't
  race on the same segment, and skips any segment that already has an
  attempt recorded at the requested tier (it's waiting for a B8 tier
  escalation -- `queue summary`'s `awaiting_escalation` count surfaces
  these, and this is what guarantees `queue next` eventually returns `[]`);
  `prompt.py` renders a segment's subagent prompt (book metadata, segment
  text, the candidate schema(s) -- including nested `object`/array-of-object
  properties -- rendered live from `schemas/`, a complete EXAMPLE RECORD
  loaded from `schemas/examples/<kind>.json` when one exists, the output
  contract, and `extraction.model` from `--model`, default
  `claude-haiku-4-5`) to `prompts/<book_id>/<seg_id>.md`; `complete.py`
  ingests a subagent's final JSON (records -> `pending_records` after
  checking each path resolves inside `records/<book_id>/` and exists on
  disk, `no_content`, or a malformed reply -- including a `seg_id` mismatch
  -- and merges `notes` (a list of strings) onto the segment); `summary.py`
  reports counts; `runner.py` wires all of it into the CLI. `owlsperch
  validate` promotes a path from `pending_records` to `records` on PASS and
  drops it on FAIL. The skill itself is `.claude/skills/extract/SKILL.md`
  -- a short Claude-Code-facing loop over these commands plus Agent-tool
  subagent launches; all the logic that can be unit tested lives in
  `queue/` instead of the skill doc.

- `pipeline/owlsperch/build_db/` -- the `build-db` subcommand (spec 4.8,
  batch B6): `runner.py` re-validates every `records/<book_id>/<type>/
  *.json` file with `owlsperch.validate.runner.validate_record` (a pure,
  no-write-back variant of the check `owlsperch validate` runs, factored out
  for this purpose -- build-db is read-only and must never mutate segment
  files), skips FAILs (counted), and loads the rest into a fresh SQLite
  database written to a temp file and atomically renamed into place:
  `books` (one row per manifest entry), `records` (id, type, name, slug,
  book_id, canonical, macro_eligible, the record as `json`, plus an
  `aliases` and a `record_id` column needed only so the FTS5 external-content
  link below has real columns to read), `record_fields` (the record's
  `fields` flattened to one row per scalar -- `flatten_fields` handles
  scalars, lists of scalars, and array-of-object fields like spell `levels`,
  which get one row per `<key>.<subkey>` plus a combined `"Cleric 3"`-style
  row), `record_pages`, and `names_fts` (FTS5 over name/aliases,
  `content='records'`/`content_rowid='rowid'`). Every record is
  `canonical = 1` and `macro_eligible = 0` in this batch -- precedence
  (B11) and macro eligibility (B22) are future work. Skipping is expected
  while extraction is still in progress, so `run_build_db` always exits 0
  regardless of `skipped_invalid` unless `--strict` is passed (then exit 1
  on any skip); either way, a nonzero `skipped_invalid` always prints a
  WARNING to stderr naming the count and the first 5 skipped paths with
  each one's first error, so a skip is never silent.
- `pipeline/owlsperch/serve.py` -- the `serve` subcommand: imports
  `uvicorn` and `owlsperch_server.app.create_app` lazily (inside
  `run_serve`) so importing `owlsperch.cli` never requires either to be
  installed, then runs uvicorn on 127.0.0.1:8000 by default.
- `server/` -- a second uv workspace member, the `owlsperch_server` package
  (spec 4.9, batch B6, plus `/stats` from B7): FastAPI + uvicorn, depending
  on the `owlsperch` pipeline package (via `[tool.uv.sources]` workspace =
  true) for schema/registry loading and data-dir resolution -- not the other
  way around, so `pipeline` has no formal dependency on `server` even though
  `owlsperch serve` imports it (both are installed into the one shared
  workspace virtual environment by `uv sync`). `app.py`'s `create_app()`
  serves `/search` (an FTS5 prefix query per word, falling back to a
  case-insensitive substring scan over `records.name` when FTS returns
  fewer than `limit` hits, deduplicated, grouped by type, canonical only),
  `/records/{type}/{slug}` (the stored record JSON plus `variants` --
  other canonical records sharing type+slug across books, picking the
  latest-published book's as the main response -- and placeholder `links`/
  `referenced_by`/`tables` for later batches), `/schemas` (reusing
  `owlsperch.schemas`), `/stats` (canonical record counts by type, for the
  web UI's home-page hint), and `/health`. The database is opened read-only
  (`mode=ro` URI) once per request, not pooled (spec D1: single-user,
  localhost only). A missing database makes `/search`,
  `/records/{type}/{slug}`, and `/stats` answer 503 naming `owlsperch
  build-db`; a present but corrupt database file (one `sqlite3.connect`
  opens fine but that raises `sqlite3.DatabaseError` on the first real
  read, since SQLite only validates the file header lazily) answers 503
  with a distinct "database unreadable" detail naming the same rebuild
  command, via the `_query_db` helper every data endpoint routes its DB
  work through; `/health` and `/schemas` don't touch the database and
  always answer normally.
- `pipeline/owlsperch/dev.py` -- the `dev` subcommand (spec 4.10, batch B7):
  `build_dev_commands` decides what to run (`python -m owlsperch serve`,
  then `npm run dev` in `web/`) and where; `run_dev` spawns both as child
  processes, threads their stdout to this process's stdout with
  `[api]`/`[web]` prefixes, and shuts both down on Ctrl-C (SIGINT, handled
  by Python's default `KeyboardInterrupt` behavior) or `SIGTERM` (remapped
  to the same `KeyboardInterrupt` path explicitly, since its default
  disposition would otherwise skip the child-process cleanup).
- `pipeline/owlsperch/fixture_db.py` -- `owlsperch fixture-db <dir>` (spec
  4.10, batch B7): a pytest-free equivalent of
  `server/tests/conftest.py`'s `built_data_dir` fixture -- writes a small
  synthetic, schema-valid manifest/segment/record set into `<dir>` and
  builds `<dir>/db/owlsperch.sqlite` from it via
  `owlsperch.build_db.runner.build_db`. Used by `web/e2e/serve-fixture.py`
  (the Playwright smoke test's backend) and usable standalone for poking at
  the UI locally without the real PDF corpus.
- `web/` -- the frontend (spec 4.10, batch B7): Vite + React 18 +
  TypeScript, React Router for `/` (search) and `/r/:type/:slug` (record
  detail). `src/api.ts` has typed wrappers for every server endpoint;
  `src/components/SearchBox.tsx` is the debounced (150ms), cancellable
  (`AbortController`), keyboard-navigable (arrows/Enter/Escape) typeahead;
  `src/components/FieldGroups.tsx` renders a record's `fields` grouped and
  ordered by `/schemas`'s `x-ui` hints (`buildFieldGroups`/
  `formatFieldValue` are plain functions, unit tested separately from the
  component); `src/pages/RecordPage.tsx` renders `text_md` with
  `react-markdown` + `remark-gfm` (no raw HTML). `vite.config.ts`'s dev
  server proxies `/api/*` to the FastAPI server on 127.0.0.1:8000 (path
  rewrite strips `/api`) and binds `127.0.0.1` explicitly (Node's default
  `"localhost"` host can resolve to the IPv6 loopback only on some systems,
  which breaks anything that probes `127.0.0.1` directly, e.g. Playwright's
  `webServer.url` check). `web/e2e/` has one Playwright smoke test
  (`smoke.spec.ts`, flow A: type a prefix, Enter, land on the record page)
  run by `playwright.config.ts`'s `webServer` against two freshly started
  servers: `e2e/serve-fixture.py` (the fixture DB + FastAPI, see
  `fixture_db.py` above) and `npm run dev` (Vite, which proxies to it).

See `docs/specs/2026-09-12-dnd-reference-site-spec.md` (especially "Scope
boundaries" and sections 4.1-4.4) for the full design, and
`docs/batches/2026-09-12-dnd-reference-site-batches.md` for the batch/build
order this and future work follows.

## Keeping this file useful

When the project takes shape, replace the "Current state" section with:

- The commands to build, lint, run tests, and run a single test.
- The high-level architecture: the main entry points, how the major modules relate, and any non-obvious conventions that require reading several files to understand.
