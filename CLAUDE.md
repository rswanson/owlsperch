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
segments), the `schemas/` type registry (envelope + spell/feat/table/
rules_section schemas -- batch B10 adds the latter three -- JSON Schema
draft 2020-12) with `validate` and `schema show`, the `queue`
extraction-queue CLI plus the `/extract` Claude Code skill -- now (batch B8)
with a full haiku -> sonnet -> opus escalation ladder and a `human/` inbox
for what opus can't resolve, `queue complete` refusing to let one segment's
claimed record path silently overwrite a record another segment already
owns (a B10 mandated follow-up), and `queue audit <book_id> [--fix]` to
find and (re-)recover segments this already happened to before that guard
existed (see "Architecture" below), `toc <book_id|all>` (batch B10b --
parses a book's own table of contents into `toc/<book_id>.json`, a
chapter/section tree with a player-facing `category` per entry, per
`schemas/categories.json`), `build-db` (builds `db/owlsperch.sqlite` from
validated records, deriving each record's `category`/`chapter`/`section`
from its book's toc), `serve` (starts the `owlsperch_server` FastAPI app --
`/search`, `/records/{type}/{slug}`, `/records/{type}` and `/facets/{type}`
(batch B9 -- filtered/sorted/paginated browse lists and their facet
counts; batch B10b adds `category`/`chapter` filters and a `category`
facet), `/schemas`, `/health`, `/stats`), `dev` (runs `serve` and `web/`'s
Vite dev server together), `fixture-db <dir>` (builds a small synthetic
database for local UI/e2e testing), and `web/` (search box, record page
with a `Book > Chapter > Section` breadcrumb, and -- batch B9 --
`/browse/:type` with a facet sidebar, or -- batch B10b, `?view=tree` and
the default for `/browse/rules_section` -- a category -> chapter ->
section tree, plus a "Rules" link and category quick links in the header
nav). Batch B10 adds the feat/table/rules_section record types end to
end: schemas, per-kind extraction rules, a `tables` SQLite table,
record-detail table resolution, and web rendering of a record's owned
tables (see "Architecture" below). Batch B10b reorganizes
`/browse/rules_section` around each book's own table of contents instead
of the flat, extractor-guessed `chapter` field (which stops being a facet).
CI. Everything else is a future-batch stub (`check-completeness`,
`coverage`, `schema review`, `sample`, and `web/`'s own `/tools/*`
routes from spec 4.10).

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
uv run owlsperch queue next <book_id> --limit N [--tier haiku|sonnet|opus] [--kind K] [--model M] [--lock-timeout S] [--json] [--dry-run]
uv run owlsperch queue prompt <seg_id> [--model M]
uv run owlsperch queue complete <seg_id> --result <json-file-or-'-'>
uv run owlsperch queue summary <book_id> [--json]
uv run owlsperch queue reset <seg_id>... [--hard]
uv run owlsperch queue audit <book_id> [--fix] [--json]
uv run owlsperch queue run <book_id> --dry-run --fixtures DIR [--tier T] [--limit N] [--kind K] [--json]
uv run owlsperch toc <book_id|all> [--force]  # parses toc/<book_id>.json (batch B10b)
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
  label, schema version), and `<type>.json` -- `spell.json`, plus (batch
  B10) `feat.json` (`feat_type`, `prerequisites`, `benefit` -- required,
  `normal`, `special`), `rules_section.json` (`topic` -- required,
  `parent_section`, `chapter`), and `table.json` (`caption`, `columns` --
  `minItems: 1`, `rows` -- an array of string arrays, `parent_record`, the
  owning record's `id`) -- defines that type's `fields`, each carrying an
  `x-ui` hint (label, filterable, sortable, group, order). All JSON Schema
  draft 2020-12. `pipeline/owlsperch/schemas.py` finds this directory by
  walking up from the package to one containing `schemas/registry.json`
  (overridable via `$OWLSPERCH_SCHEMAS`), loads the registry, and renders
  `schema show`. `schemas/examples/<type>.json` (spell, plus B10's feat/
  rules_section/table) is an invented, schema-valid example record for that
  type, rendered verbatim into extraction prompts (`owlsperch.queue.prompt`)
  and checked against its own schema by a self-test in `test_schemas.py` --
  the rules_section and table examples cross-reference each other via
  `tables`/`fields.parent_record`, modeling the convention below.
  `schemas/categories.json` (batch B10b) is a separate, plain (non-JSON-
  Schema) data file: the committed, book-agnostic player-facing rules
  taxonomy (`key`, `label`, `order`, `description`, `record_types`) --
  character-creation, races, classes, skills, feats, equipment, combat,
  adventuring, magic, monsters, running-the-game, basics, uncategorized (in
  that `order`) -- loaded by `owlsperch.schemas.load_categories` and reused
  by `owlsperch.toc.categories`' resolution and by
  `owlsperch_server.browse`'s `category` facet (for its order and labels).
  `rules_section.json`'s `chapter` field is `x-ui.filterable: false` as of
  B10b -- the derived `toc_chapter` column (see `build_db/` below) is the
  single source of truth for chapter filtering now.
- `pipeline/owlsperch/validate/` -- the `validate` subcommand: `loader.py`
  discovers record/segment files and compiles the envelope/type JSON Schema
  validators once per run; `checks.py` holds the id/slug/type-directory/
  schema_version consistency checks plus type-specific field checks (spell:
  non-empty `school` and `levels`; batch B10 adds feat: non-empty `benefit`,
  rules_section: non-empty `topic`, table: `columns` non-empty and every
  `rows` entry the same length as `columns`). `checks.slugify` (B10) maps
  every Unicode dash-punctuation character (category `Pd` -- an en dash,
  em dash, etc., not just the ASCII hyphen) to a plain `-` before the ASCII
  fold, so "Table 3–8: The Druid" (en dash) slugifies to
  `table-3-8-the-druid` rather than losing the dash and merging into
  `table-38-the-druid`; `runner.py` orchestrates conformance +
  consistency + the page-within-segment-span check (segment looked up by
  `extraction.segment_id`), prints PASS/FAIL (or `--json`/`--stale`), and
  writes the outcome back to the originating segment (via the `Segment`
  model) idempotently. Bumping a type's `schema_version` in
  `schemas/registry.json` requires following up with `uv run owlsperch
  validate <book_id|all> --bump-compatible` against the data dir --
  otherwise every older record stays stale, `owlsperch build-db` skips all
  of them, and `server/tests/test_corpus.py` fails (or skips, naming this
  command) until the migration is run.

- `pipeline/owlsperch/queue/` -- the `queue` subcommand (`next`, `prompt`,
  `complete`, `summary`, `reset`, `audit`, `run`), the Python side of the
  `/extract` skill (spec 4.5, batches B5/B8): `ladder.py` is the pure haiku -> sonnet ->
  opus escalation state machine (`TIERS`, `TIER_MODELS`, `STALE_AFTER` = 60
  min, `record_failure` appends `{tier, timestamp, errors, kind}` to
  `attempts` -- idempotent on an identical repeat, and never double-advances
  a stale re-validation of an old-tier record -- and advances `tier` unless
  it's a first `needs_context` at that tier, reporting `exhausted` once opus
  fails too; `escalate_existing_attempt` is the same logic applied lazily,
  at selection time, to a pending segment that already has an attempt at its
  own tier); `select.py` (`select_and_mark`) resets any segment `in_progress`
  for >60 min back to `pending`, lazily escalates legacy/stuck pending
  segments, then picks segments for `tier` (default: `None` -- the lowest
  tier in `TIERS` with pending work) and `kind` (default, batch B10:
  `None`, which resolves to every kind_hint with a registered schema --
  `set(load_registry(schemas_dir).types)`, currently spell/feat/table/
  rules_section -- so a kind with no schema yet, e.g. `stat_block`, is
  never selected unless `--kind stat_block` is passed explicitly; one wave
  can mix kinds this way, each `SelectedSegment` still carrying its own
  `kind_hint`), marking them `in_progress` under an exclusive `flock` on
  `segments/<book_id>/.queue.lock` (`--lock-timeout`, default 30s) so two
  concurrent `queue next` runs can't race on the same segment.
  `select_and_mark(..., dry_run=True)` (`queue next --dry-run`, B10-mand3)
  previews the exact same selection -- including whatever the stale-reset/
  lazy-escalation heal pass would un-stick or escalate -- with no write
  side effects at all: nothing is marked `in_progress` and no prompts are
  rendered, so inspecting the queue (e.g. `--limit 100000` to see
  everything pending) no longer flips real work into `in_progress` the way
  a plain `queue next` used as a look-only inspector did during B10;
  `prompt.py` renders a segment's subagent prompt (book metadata, segment text, the
  candidate schema(s) -- including nested `object`/array-of-object
  properties -- rendered live from `schemas/`, a complete EXAMPLE RECORD
  loaded from `schemas/examples/<kind>.json` when one exists, a
  "## Prior attempts" section on a retry, "### Adjacent context" blocks
  for `context_seg_ids`, previous/next segment ids, a per-kind
  "## Extraction rules for `<kind>`" section from the module-level
  `_KIND_RULES` dict (batch B10 -- spell's class-abbreviation table and its
  "text_md begins at the descriptive body, don't repeat the stat block"
  rule; feat's `NAME [TYPE]` heading and `Prerequisite:`/`Benefit:`/
  `Normal:`/`Special:` marker-splitting rules; rules_section's `topic`/
  `parent_section`/`chapter` rules, its table-of-contents/index-fragment
  `no_content` guidance, and (B10 retrospective) a rule that a GENERIC,
  cross-chapter heading (e.g. "Class Features") must have its record `name`
  qualified with the enclosing entity (`Class Features (Barbarian)`, never
  the bare heading) since `slug`/`id` derive from `name` and an unqualified
  generic name collides across chapters -- modeled by
  `schemas/examples/rules_section.json`'s own "Class Features (Sable
  Knight)" example; table's verbatim title, column/row padding, and
  caption-only-segment `no_content` guidance -- a kind_hint with no entry,
  e.g. `stat_block`, gets no rules section), a shared "### Tables belonging
  to this entity" convention for every non-table kind (write a second
  `table` record alongside the entity's own when the segment's text
  contains a table belonging to it, printing both absolute output
  directories, cross-linked via `fields.parent_record`/`tables` -- this
  convention also renders the `table` type's own `fields` schema,
  `schema_version`, `_KIND_RULES["table"]`, and its own EXAMPLE RECORD
  alongside the segment's own kind's, since the subagent is being told to
  write a record of a different type than the rest of the prompt is about),
  an explicit never-`null` instruction for `fields` (write nothing rather
  than `null` -- envelope build-time keys may simply be omitted), the output
  contract including `needs_context`/`proposed_type`, and
  `extraction.model` from `--model`) to `prompts/<book_id>/<seg_id>.md`.
  `pipeline/tests/test_queue_prompt.py` carries a per-registered-kind
  prompt-coverage guard (`_prompt_coverage_failures`, B10 retrospective
  proposal 9): it renders a prompt for every type in `schemas/registry.json`
  and asserts every field/type instruction in it is backed by a
  schema/example actually rendered in that same prompt -- a new kind, or a
  new cross-type instruction like the table cross-link convention, must
  satisfy it too. `complete.py` ingests a subagent's final JSON:
  `proposed_type` moves the
  segment straight to `human/<book_id>/` with the proposal; `needs_context`
  (ids must exist under `segments/<book_id>/`) merges into
  `context_seg_ids` and retries the same tier once before escalating;
  `no_content` marks the segment done; otherwise `records` ->
  `pending_records` after checking each path resolves inside
  `records/<book_id>/`, exists on disk, AND (B10 mandated follow-up) is not
  already owned by a *different* segment of the same book --
  `_record_path_owners` scans every `segments/<book_id>/*.json` and
  `human/<book_id>/*.json` file's own `records`/`pending_records` for this,
  since the record file's own `extraction.segment_id` is only a
  subagent-copied placeholder by the time `queue complete` runs and would
  already name a thief on a just-clobbered file. A missing or colliding
  path, like a malformed reply, escalates the segment via
  `ladder.record_failure` (both kinds folded into the one attempt for a
  single reply), moving it to `human/` if already on opus; a colliding path
  is left completely untouched on disk. `summary.py` reports per-tier
  pass/escalated counts (from both `segments/` and `human/`) plus
  `needs_context_retries`, `human`, and `pending_by_kind`/`pending_by_tier`
  (pending-only, so a wave can be planned without `queue next`);
  `driver.py` (`queue run <book_id> --dry-run --fixtures DIR`) drives the
  whole select/subagent/complete/validate loop in-process against a
  `FixtureSubagent` (canned `{"files": ..., "reply": ...}` JSON per call,
  `<fixtures_dir>/<seg_id>/
  <n>.json`) so the state machine is unit tested without launching real
  Agent-tool subagents; `common.py` has `find_segment_path` (now also
  searching `human/*/`), `move_segment_to_human`, and `finish_after_failure`
  (the shared write-back-or-move-to-human step `complete.py` and
  `validate/runner.py` both call); `audit.py` (B10 mandated follow-up, for
  segments a stolen-record collision already happened to before the
  `complete.py` guard existed): `audit_book` builds the same segment-index
  claim map as that guard and reports `collisions` (a path claimed by 2 or more
  segments right now) plus `stale_claims` -- one entry per segment claiming
  a path it doesn't actually own, decided this time from the record FILE's
  own `extraction.segment_id` (the last writer's stamp, and so the
  legitimate owner of a surviving file) rather than the segment index,
  reason `"owned_by_other"` or (the file is gone entirely) `"missing"`;
  `fix_book` prunes exactly the stale paths from each affected segment's
  own `records`/`pending_records` and soft-resets a `done` victim back to
  `pending` (clearing `outcome`/`outcome_reason`, keeping `tier`/`attempts`)
  so it re-extracts under the new guard and under B10-mand1's
  name-qualification prompt rule -- a `pending`/`in_progress` segment just
  gets the pruning, and a segment sitting in `human/` is reported but left
  completely untouched; `fix_book` never deletes, moves, or rewrites a
  record file itself. `runner.py` wires all of it (including `queue audit
  [--fix]`) into the CLI. `owlsperch validate` promotes a path from
  `pending_records` to `records` on PASS and drops it (deleting the file
  too, unless it's also in `records`) on FAIL. The skill itself is
  `.claude/skills/extract/SKILL.md`
  -- a short Claude-Code-facing loop over these commands plus Agent-tool
  subagent launches, using whatever tier `queue next` returns per item; all
  the logic that can be unit tested lives in `queue/` instead of the skill
  doc.

- `pipeline/owlsperch/toc/` -- the `toc` subcommand (batch B10b): `parser.py`
  scans pdf pages 1-12 of `text/<book_id>/` for a page with >= 3
  `"Title ..... N"` dotted-leader matches (a contents page), parses every
  such page's entries, drops the numbered-table index (`Table N–M`,
  EN DASH, matched anywhere in the title -- column repair glues the list's
  own header onto its first entry), and inverts `text/<book_id>/pages.json`
  (which maps **pdf page -> printed page**, easy to get backwards) into
  printed -> pdf using the book's MODAL `pdf - printed` offset, so a
  misdetected page number or one missing mapping doesn't shift the whole
  book. Nesting comes from PRINTED PAGE ORDER, never the reading order
  column repair emits entries in (a book's later chapters routinely appear
  before earlier ones in the raw text): a level-1 entry is a `"Chapter N:"`
  line or a top-level name (Introduction, Appendix, Glossary, Index,
  Character Sheet, ...); every level-2 entry is assigned to the LAST
  level-1 entry whose printed page is <= its own. `categories.py` then
  resolves each entry's player-facing `category`: a chapter tries a
  per-book override, then a generic title-pattern table (most specific
  first, e.g. `Classes|Class Descriptions|Prestige Class` -> `classes`),
  then falls back to `uncategorized`; a SECTION tries its own override,
  then **its already-resolved chapter's category**, THEN the generic
  pattern table -- deliberately in that order, so e.g. "Movement, Position,
  And Distance" (a Combat-chapter section that would otherwise generically
  match Adventuring's "Movement" pattern) stays under Combat, matching
  where a player at the table would actually look for it. `lookup.py`
  (`load_toc`, `entry_for_page`) is the read side `build_db` uses --
  `entry_for_page` picks the DEEPEST entry (greatest level, then greatest
  `pdf_page_start`) containing a page. `runner.py` writes
  `toc/<book_id>.json` (`{book_id, generated_at, contents_pages, entries:
  [{title, level, printed_page, pdf_page_start, pdf_page_end, path,
  category}]}`) and prints a report (contents page(s) found, chapter/
  section counts, table-index entries dropped, entries fallen through to
  `uncategorized`); a book whose contents page can't be found, or that
  yields fewer than 5 USABLE entries, is reported as failed rather than
  writing an empty file -- the minimum-entry guard (B10b mandated
  follow-up) is applied AFTER the table-index filter, not on the raw
  dotted-leader match count, so a contents-like page that is entirely a
  numbered-tables index fails this guard too instead of parsing to a
  silent `entries: []`. `owlsperch toc <book_id>` (an explicit id) exits
  non-zero for either a parse failure or a missing `text/<book_id>/`
  directory; `owlsperch toc all` only fails the run for a parse failure,
  still printing a skip line (exit 0) for a book with no text yet. Note:
  `--force` never deletes a stale empty toc file left behind by a FAILED
  re-parse -- the non-zero exit plus the parse error is the intended
  signal, not automatic cleanup. Verified against the real PHB: 16
  chapters, 77 sections, zero fall-through to `uncategorized`.
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
  row -- a list-of-lists value, e.g. table `rows`, gets NO `record_fields`
  rows at all, batch B10: that grid content lives in the `tables` table
  instead, not flattened into individually-searchable scalar rows),
  `record_pages`, `tables` (batch B10 -- `record_id` primary key, `book_id`,
  `caption`, `columns`/`rows` as JSON-encoded lists, `parent_record`, an
  index on `parent_record`; populated in `_insert_record` for every
  `type == "table"` record, name/slug/citation staying in `records` for the
  server to join), and `names_fts` (FTS5 over name/aliases,
  `content='records'`/`content_rowid='rowid'`). Every record is
  `canonical = 1` and `macro_eligible = 0` in this batch -- precedence
  (B11) and macro eligibility (B22) are future work. Skipping is expected
  while extraction is still in progress, so `run_build_db` always exits 0
  regardless of `skipped_invalid` unless `--strict` is passed (then exit 1
  on any skip); either way, a nonzero `skipped_invalid` always prints a
  WARNING to stderr naming the count and the first 5 skipped paths with
  each one's first error, so a skip is never silent. A record that passes
  `validate_record` on its own but collides on `id` with one already
  loaded (B10-mand3 -- the corpus has genuine name/slug collisions across
  segments) is caught as `sqlite3.IntegrityError` around `_insert_record`
  and counted the same way, rather than aborting the whole build. Batch
  B10b adds four more `records` columns -- `toc_category` (`NOT NULL
  DEFAULT 'uncategorized'`), `toc_chapter`, `toc_section`, `toc_path` (a
  JSON array) -- derived, per record, from `owlsperch.toc.lookup.load_toc`
  (cached per book_id) and `entry_for_page` against `min(record["pages"])`;
  these are built-in pseudo-fields like `book_id`, never `record_fields`
  rows (a `record_fields` row keyed `chapter` would collide with the
  extractor-written `rules_section.fields.chapter`). A book with records
  but no USABLE `toc/<book_id>.json` -- missing entirely, OR present but
  parsed to zero entries (B10b mandated follow-up) -- yields
  `uncategorized`/`NULL` for all of them plus exactly one `WARNING` line on
  stderr naming `uv run owlsperch toc <book_id> --force` (plain `owlsperch
  toc <book_id>` is a no-op once an, even empty, toc file already exists).
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
  latest-published book's as the main response -- placeholder `links`/
  `referenced_by` for later batches, and (batch B10) `tables` resolved to
  one uniform object per id in the record's own `tables` list, in that
  order: a resolved id joins `tables`+`records` for
  `{id, pending: false, name, slug, caption, columns, rows, citation,
  book_id}`; an id with no matching `tables` row comes back as
  `{id, pending: true, ...null/empty}` (spec edge case: a table that failed
  extraction shows a "table pending" marker instead of failing the whole
  record) -- malformed stored `columns`/`rows` JSON falls back to `[]`
  rather than raising), `/schemas` (reusing
  `owlsperch.schemas`), `/stats` (canonical record counts by type, for the
  web UI's home-page hint), `/health`, plus (batch B9) `/records/{type}`
  and `/facets/{type}` -- the SQL/param-parsing for both lives in
  `browse.py`, kept out of `app.py`. Filterable/sortable fields come only
  from the type schema's `x-ui` hints (`name` is always an allowed/default
  sort key even though it's an envelope field, not a `fields` one; `source`
  is a built-in pseudo-field backed by `records.book_id`, not
  `record_fields`); an array-of-object filterable field (spell `levels`,
  items `class`/`level`) exposes each item sub-property as its own query
  param, derived generically from `items.properties` -- given two or more
  of a parent's sub-params at once, they must describe the SAME item, so
  that's matched against `build_db.runner.flatten_fields`'s *combined*
  `record_fields` row (`levels` -> `"Cleric 3"`) rather than ANDing
  independent single-field matches. `category` and `chapter` (batch B10b)
  are two more built-in pseudo-fields alongside `source`, backed by
  `records.toc_category`/`toc_chapter` (chapter matched case-insensitively)
  -- never `record_fields`. `/records/{type}` returns
  `{type, total, page, page_size, items}`, each item carrying a `facets`
  map read straight off `record_fields` (school, levels, etc., without
  loading the full record JSON), plus (B10b) a `toc` block (`category`,
  `category_label`, `chapter`, `section`) and the record's first `page`
  (int or null, so the web tree can order chapter/section groups without a
  second request); `/facets/{type}` returns distinct values + counts per
  filterable field (one facet per sub-property for an array-of-object
  field) plus `source` and (B10b) `category` -- the latter ordered by
  `schemas/categories.json`'s own `order`, NOT by count, so the sidebar's
  category list always reads in the same player-facing order; there is
  deliberately no `chapter` facet, since the web tree itself is the chapter
  navigator. `/records/{type}/{slug}` gains (B10b) `book_title`
  (`COALESCE(short_title, title)`) and a `toc` block with a `path` array
  too. Every facet's counts honor every current filter except its own
  field. The database is opened read-only
  (`mode=ro` URI) once per request, not pooled (spec D1: single-user,
  localhost only). A missing database makes `/search`,
  `/records/{type}/{slug}`, `/records/{type}`, `/facets/{type}`, and
  `/stats` answer 503 naming `owlsperch build-db`; a present but corrupt
  database file (one `sqlite3.connect` opens fine but that raises
  `sqlite3.DatabaseError` on the first real read, since SQLite only
  validates the file header lazily) answers 503 with a distinct "database
  unreadable" detail naming the same rebuild command, via the `_query_db`
  helper every data endpoint routes its DB work through; `/health` and
  `/schemas` don't touch the database and always answer normally.
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
  synthetic, schema-valid manifest/segment/record set (three spells --
  Fireball/Evocation, Alarm/Abjuration, and, since batch B9, Summon Monster
  III/Conjuration with a Cleric-3 `levels` item, for the browse Playwright
  spec's facet-filter flow; plus, since batch B10, an invented feat
  ("Power Strike"), a rules_section ("Grapple Ranks") that owns a table via
  its `tables` list, and that table record, for the smoke Playwright spec's
  rendered-table flow; plus, since batch B10b, a second rules_section
  ("Hauling Gear") and a matching `toc/fixture-book.json` -- three chapters
  (Magic pdf 1-2, Combat pdf 3-3, Equipment pdf 4-4), each with one
  level-2 section -- so "Grapple Ranks" resolves to category combat and
  "Hauling Gear" to equipment, giving the web tree two populated branches
  for the tree Playwright spec's flow C) into `<dir>` and builds
  `<dir>/db/owlsperch.sqlite` from it via `owlsperch.build_db.runner.build_db`.
  Used by `web/e2e/serve-fixture.py` (the Playwright tests' backend) and
  usable standalone for poking at the UI locally without the real PDF corpus.
- `web/` -- the frontend (spec 4.10, batch B7, plus `/browse/:type` from
  B9): Vite + React 18 + TypeScript, React Router for `/` (search),
  `/browse/:type` (browse + facets), and `/r/:type/:slug` (record detail).
  `src/api.ts` has typed wrappers for every server endpoint;
  `src/components/SearchBox.tsx` is the debounced (150ms), cancellable
  (`AbortController`), keyboard-navigable (arrows/Enter/Escape) typeahead;
  `src/components/FieldGroups.tsx` renders a record's `fields` grouped and
  ordered by `/schemas`'s `x-ui` hints (`buildFieldGroups`/
  `formatFieldValue` are plain functions, unit tested separately from the
  component; batch B10 adds an optional `hiddenFields` prop so a
  `type === "table"` record's own `columns`/`rows` fields aren't ALSO
  rendered here as a comma-joined string); `src/pages/RecordPage.tsx`
  renders `text_md` with `react-markdown` + `remark-gfm` (no raw HTML),
  then (batch B10) `src/components/RecordTables.tsx` below it: one real
  HTML `<table>` (caption, header row, body rows) per resolved entry in
  `record.tables`, or a "Table pending" marker naming the id for an
  unresolved one; for a `type === "table"` record itself,
  `RecordTables.buildOwnTable` prepends a table built from the record's
  own `fields.caption`/`columns`/`rows`, and `RecordPage` hides those two
  field names from `FieldGroups` so the grid isn't also dumped as text.
  Batch B10b adds a `Book > Chapter > Section` breadcrumb above the heading
  (`RecordBreadcrumb`, built from the record's `book_title`/`toc`): Book
  links to `/browse/<type>?source=<book_id>`, Chapter to `/browse/<type>
  ?category=<category>&chapter=<chapter>`, Section is plain text; a record
  with no resolved chapter shows just the book crumb, and one with no
  `book_title` shows no breadcrumb at all.
  `src/pages/BrowsePage.tsx` (batch B9) keeps every filter/sort/page/view value in the URL query string via
  `useSearchParams` (reload/back restore the view), fetches `/records/{type}`
  and `/facets/{type}` on every change, and keeps the previous facets/results
  mounted (a `loading` flag, not a full state-machine swap) while a refetch
  is in flight -- so a just-checked checkbox never briefly disappears from
  the DOM; `src/components/FacetSidebar.tsx` renders one checkbox group per
  facet, labeling `source` values with the book title instead of its raw
  `book_id`. Batch B10b adds `?view=tree`: the effective view is
  `searchParams.get("view") ?? (type === "rules_section" ? "tree" : "list")`,
  with a List/Tree toggle that writes `?view=`; `"view"` is in
  `RESERVED_PARAMS` so it's never forwarded to `/records/{type}`/
  `/facets/{type}` (an unrecognized param is a 400). Tree mode fetches
  every matching record across sequential `page_size=200` pages (capped at
  2000; a "showing the first N of M" line beyond the cap) instead of the
  user-facing page size, then hands them to
  `src/components/RecordTree.tsx`: `buildTree` (pure, unit tested like
  `buildFieldGroups`) groups items into category -> chapter -> section,
  categories in the `category` facet's own order, chapters/sections by
  minimum page then title, an "(Unsectioned)" leaf for a missing chapter/
  section; `RecordTree` renders nested `<details>/<summary>` groups with
  per-group counts, auto-expanding when exactly one category is selected in
  the URL. `src/components/Layout.tsx`'s header nav lists every registered
  type EXCEPT `rules_section` from `/schemas`, linking to `/browse/<type>`;
  batch B10b adds a dedicated "Rules" link (`/browse/rules_section`) plus
  quick links -- fetched once from `/facets/rules_section`'s `category`
  facet -- for whichever of classes/equipment/skills/races have count > 0,
  as `/browse/rules_section?category=<key>`; either fetch failing just
  leaves that part of the nav empty, same defensive pattern as the existing
  `/schemas` fetch.
  `vite.config.ts`'s dev server proxies `/api/*` to the FastAPI server on
  127.0.0.1:8000 (path rewrite strips `/api`) and binds `127.0.0.1`
  explicitly (Node's default `"localhost"` host can resolve to the IPv6
  loopback only on some systems, which breaks anything that probes
  `127.0.0.1` directly, e.g. Playwright's `webServer.url` check). `web/e2e/`
  has Playwright specs -- `smoke.spec.ts` (flow A: type a prefix, Enter,
  land on the record page; plus, batch B10, opening the fixture
  rules_section record and asserting its owned table renders as a real
  `<table>` below the text), `mobile.spec.ts` (flow A at 400px width),
  (batch B9) `browse.spec.ts` (flow B: nav to Spells, check Cleric/3/
  Conjuration in the facet sidebar, sort by name, open the one matching
  spell), and (batch B10b) `tree.spec.ts` (flow C: follow the header nav's
  "Rules" link, expand Combat and its chapter, open "Grapple Ranks", see
  the breadcrumb; plus a header quick-link into a pre-filtered, auto-
  expanded category) -- run by `playwright.config.ts`'s `webServer` against
  two freshly started servers: `e2e/serve-fixture.py` (the fixture DB +
  FastAPI, see `fixture_db.py` above) and `npm run dev` (Vite, which
  proxies to it).

See `docs/specs/2026-09-12-dnd-reference-site-spec.md` (especially "Scope
boundaries" and sections 4.1-4.4) for the full design, and
`docs/batches/2026-09-12-dnd-reference-site-batches.md` for the batch/build
order this and future work follows.

## Keeping this file useful

When the project takes shape, replace the "Current state" section with:

- The commands to build, lint, run tests, and run a single test.
- The high-level architecture: the main entry points, how the major modules relate, and any non-obvious conventions that require reading several files to understand.
