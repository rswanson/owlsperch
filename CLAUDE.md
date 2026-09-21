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
Batch B10c adds the `class`/`prestige_class` record types: `segment` gains
a separate, toc-driven pass that turns a book's own class/prestige-class
toc entries (discriminated from same-chapter non-class sections by a `Hit
Die: dN` marker) into `class`/`prestige_class` segments starting at the
sonnet tier, and stamps every other segment their page span swallows as
`superseded_by` rather than deleting it; `validate` gains a class-specific
validator suite (level-table row count/columns/BAB/save progressions,
Special<->class_features reconciliation, class-skill names, and a
spell_list cross-check against the book's own spell records); `build-db`
marks a swallowed `rules_section`/`table` record `canonical = 0` with
`superseded_by` set (excluding a class's own owned table); and `web/`
renders a class/prestige_class record as a structured page (header facts,
description, skills, proficiency, the progression table, features, and a
live Spells section) instead of the generic field-groups body. Batch
B10c-mand4 fixes four pipeline data-integrity defects the B10c class-
quality judgement surfaced: `validate` writes each segment back exactly
once per run, atomically, for every record it owns
(`validate_record_files`) instead of once per record in file-discovery
order, so a passing
sibling record (e.g. a class's owned level table) can no longer silently
overwrite a failing sibling's (the class record's own) escalation with
`status: "done"`; a subagent's `queue complete` record claims must now
resolve inside that segment's own private `staging/<book_id>/<seg_id>/
records/<type>/` directory (`owlsperch.queue.prompt` tells it to write
there, never directly to the shared `records/<book_id>/`), so two
segments' subagents can no longer race on the same shared file before
`queue complete`'s ownership guard ever runs -- `queue complete` is the
only thing that moves an accepted file into `records/<book_id>/`, and only
after checking the destination isn't already owned by a different, still-
live segment; and `build-db` reports (and, with `--strict`, fails on) any
`table` record whose `parent_record` names an id that never made it into
`records`, since such a table previously rendered in `/browse/table` with
no warning while its owning record 404s; and a follow-up ladder-
idempotency defect this review left open -- a group failure with no
failing record's own `extraction.tier` to read (the pure
`missing_record_path` case) fell back to the segment's current `tier`,
which the *previous* run had just advanced, so re-validating an
unchanged segment walked the escalation ladder once per run -- is fixed
by a new `Segment.claim_tier` field (see `pipeline/owlsperch/validate/
runner.py` below) that anchors the attempt tier to something stable
across runs instead. Batch B11 adds the `errata_entry`/`update_entry`
record types (an errata or 3.5 Update booklet segments into one entry per
corrected entity) and a precedence pass inside `build-db`: errata/update
entries are applied to their targets, a Rules Compendium `rules_section`
becomes canonical over any other book's matching topic, and among what
remains the latest `published` book wins -- `build-db` now writes
`reports/precedence.md` naming every entry and Rules Compendium section it
couldn't match, and `web/` shows a record's "Other printings" and
"Overrides applied" (with citations). CI. Everything else is a
future-batch stub (`check-completeness`, `coverage`, `schema review`,
`sample`, and `web/`'s own `/tools/*` routes from spec 4.10).

### Commands

Run from the repo root (a uv workspace with `pipeline` and `server` as its
members -- see the comment in the root `pyproject.toml`):

```sh
uv sync                              # install deps (both workspace members)
uv run owlsperch manifest check
uv run owlsperch text <book_id|all> [--force] [--pages A-B]
uv run owlsperch segment <book_id|all> [--force] [--pages A-B] [--kinds class[,prestige_class]]
  # (batch B10c) also emits toc-driven `class`/`prestige_class` segments
  # (needs toc/<book_id>.json from `owlsperch toc` first) and stamps every
  # segment their page span swallows `superseded_by` -- additive, so a
  # plain (non --force) run is always safe to re-run. (B10c-mand6) `--kinds`
  # re-runs ONLY that toc-driven pass (only class/prestige_class accepted);
  # everything else about a plain run is unaffected.
uv run owlsperch validate <book_id|all> [--json] [--stale] [--bump-compatible]
uv run owlsperch schema show <type>
uv run owlsperch queue next <book_id> --limit N [--tier haiku|sonnet|opus] [--kind K] [--model M] [--lock-timeout S] [--json] [--dry-run]
  # `--kind class`/`--kind prestige_class`/`--kind errata_entry`/
  # `--kind update_entry` all resolve to the sonnet tier on their own
  # (batch B10c/B11: those kinds start above haiku, per
  # `owlsperch.queue.ladder.STARTING_TIERS`)
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
  "Repel Wood" spells) -- not fixed as part of this B3 follow-up. Batch
  B10c-mand7 adds a reassembly pass (module docstring step 2b) for a
  fragmented class-level table (e.g. PHB Table 3-6: The Cleric, p. 32):
  poppler sometimes splits one printed table into several per-column
  table-group cliques (one per short run of rows) plus a pile of orphan
  single-cell blocks -- one block per CELL -- for the rows in between,
  since those rows never meet the >= 2 non-blank line bar step 2's clique
  detection requires. This runs immediately after step 2's clique
  detection and before any leftover block reaches the single-block-table
  check above, alternating two moves to a joint fixpoint: merge two
  table-group candidates whose x-extents overlap by >= 80% of the
  narrower one's span and whose vertical gap is <= 2x their larger median
  line height, and absorb a leftover non-prose/non-label:value block
  whose x-extent sits entirely inside a group's x span and whose y-center
  sits within one median line height of the group's y span. Absorbing
  orphan rows can be what brings a too-wide gap under the merge
  threshold, so a single merge-then-absorb pass isn't enough -- see
  `owlsperch.text.columns`'s module docstring for the PHB p.32 walkthrough
  where this matters. Every merge/absorb is guarded by rebuilding the
  candidate and requiring its count of >= 2-cell rows not to drop.
  Deliberately out of scope: the phantom ", Bonus Feat" / ", BF" tokens
  PHB class tables print in their Special column are an ERRATA OVERLAY
  baked into the PDF's own text -- even `pdftotext -layout` prints them,
  and poppler's bbox output carries no font signal to distinguish them --
  so this pass does not try to strip them; that stays the extraction
  prompt's "drop a Special token with no printed feature heading" rule.
  Batch B10c-mand9 fixes the rotated-block test (step 1) that used to drop
  a class table's narrow spells-per-day columns: `is_vertical_block` now
  judges only lines of at least `VERTICAL_MIN_LINE_CHARS` (2) characters and
  never drops a block with no line long enough to judge, since a SINGLE
  glyph is taller than it is wide in horizontal text too. A column whose
  every cell is one digit (PHB p.56 Table 3-18: The Wizard's "Spells per
  Day" 0 and 1st columns, p.32 Table 3-6: The Cleric's 0 column) was
  therefore read as sideways marginalia and dropped whole, losing each
  level row's leading spell cell (and p.32's `0` header); sibling columns
  holding an em dash or a two-glyph `+1` were wider than tall and never
  affected, which is why only the leftmost spell columns went missing.
- `pipeline/owlsperch/segment/` -- the `segment` subcommand: `headings.py`
  defines the book-wide `Paragraph` stream (a page's `.txt` paragraphs
  joined with their `.meta.json` stats) and heading detection (font-size
  vs. the book's body median, or an all-caps short line); `anchors.py`
  detects spell/stat_block/feat/table pattern anchors; `splitter.py` walks
  the stream once to produce the final segment spans (anchors, plus
  `rules_section` for everything between them, split at headings);
  `runner.py` loads a book's text+meta, calls the splitter, and writes
  `segments/<book_id>/<seg_id>.json`. Batch B10c adds a SEPARATE, toc-driven
  pass at the end of `segment_book`: if `toc/<book_id>.json` exists, every
  level >= 2 toc entry whose resolved `category` is `classes`/
  `prestige-classes` AND whose own pdf pages contain a `Hit Die: dN` marker
  (the discriminator that tells an actual class entry from a same-chapter
  section that only talks ABOUT classes, e.g. "Multiclass Characters")
  becomes a `class`/`prestige_class` segment, id `<book_id>-<kind>-p<NNNN>`
  (`NNNN` = the entry's own first pdf page -- a form that never collides
  with the `<book_id>-p<NNNN>-<NN>` ordinal scheme, so it never renumbers
  existing segments), spanning the entry's own pdf pages extended ONE page
  past `pdf_page_end` (a class's last column routinely spills onto the page
  the toc assigns to the next class). Every OTHER segment whose `pages`
  fall entirely inside that span AND (batch B10c-mand11) whose own
  `(heading, kind_hint)` is class-structural for that class's title
  (`owlsperch.supersede.is_class_owned_fragment`) then gets `superseded_by`
  stamped in place (not deleted) if not already set; one that is NOT
  class-structural (a printed sidebar like "FAMILIARS", or the NEXT class's
  own heading fragment on a shared page, which is then left for that
  class's own span to stamp) is left COMPLETELY alone -- not stamped, no
  claims released -- and counted in the summary's own "N in-span segment(s)
  left live" line. This whole pass is additive and
  idempotent, which is what makes a plain (non `--force`) `owlsperch
  segment <book_id>` do the entire job on a book that already has segments:
  the handful of class segments get written and every existing
  class-chapter fragment gets stamped, without touching anyone's
  `outcome`/`attempts`. A toc-less book gets no class segments at
  all (reported in the summary), and a `--force` covering a class segment's
  first page still deletes and recreates it like any other segment. Batch
  B10c-mand2: a segment NEWLY stamped `superseded_by` this pass also has
  its record claims RELEASED in the same atomic write, via
  `pipeline/owlsperch/supersede.py`'s `release_segment_claims` (see that
  module below) -- this is what lets a class segment claim its own level
  table when that table shares the class's printed title (and so the same
  slug/id/record-file path) with a pre-existing fragment segment: without
  releasing the fragment's claim on that exact path first, `queue
  complete`'s ownership guard would refuse the class segment's claim as a
  collision and the class could never be extracted. A segment that already
  carried `superseded_by` (from an earlier run, before this
  release-at-stamp-time behavior existed) is skipped by the WHOLE pass
  (stamp and release both) -- `queue audit --fix` is the separate,
  retroactive path for those. Batch B10c-mand3 (Part 1) anchors a class
  span's own `text`/`pages` to paragraph indices instead of its whole toc
  page range: `_class_start_index` finds the first paragraph on the span's
  own toc start page whose text matches the toc entry's title
  (`_heading_matches_title` -- case/punctuation/whitespace-insensitive,
  tolerating a trailing plural "s" on either side, since PHB 3.5 prints
  "WIZARDS" for the toc's "Wizard"), falling back to the first such match
  anywhere in the span's page range; every span's start index is resolved
  FIRST, then each span's end index is capped at whichever comes first of
  the next span's own start index or the first paragraph past its own toc
  `pdf_page_end` -- so a class segment's text stops where the next class's
  own heading actually begins, even when they share a pdf page, rather
  than always swallowing D2's one-page extension whole. A heading that
  never matches anywhere in the span falls back to the old whole-page-range
  text (a class is never dropped) and prints a `warning:` line naming the
  book and heading. The page-based supersede pass itself, and `seg_id`
  derivation, are UNCHANGED by this. Batch B10c-mand6 fixes the reading-
  order limitation this section used to describe (the column reconstructor
  emitting a class's OWN flavor prose -- or even the previous class's
  Starting Package -- BEFORE that class's own heading on a shared page,
  e.g. PHB p0050's paragraph order for rogue: its own six flavor run-in
  sections, then ranger's Starting Package, then "ROGUE"): each resolved
  span's TEXT start is back-extended, via `_back_extend_start_index`, from
  its own heading paragraph to the top of the heading's own page, bounded
  so it never reaches past a still-earlier class's own heading. The END
  cap (the next class's own heading index) is UNCHANGED, so this raw index
  range still OVERLAPS the previous class's own on a shared page -- there
  is no single cut point that gives the previous class its own full
  trailing prose (its Starting Package) AND the next class its own
  pre-heading flavor sections. A judgement follow-up (B10c-mand6 follow-up,
  finding 1) resolves the one piece of that overlap that must not be
  duplicated: `_run_class_pass` identifies each span's own opening flavor
  run-in paragraph specifically -- the one carrying BOTH an "Alignment:"
  and a "Religion:" marker (`_is_class_flavor_paragraph`) -- and excludes
  it from the PREVIOUS class's own written text (`_write_class_segment`'s
  `exclude_indices`), so it survives in exactly the class it actually
  belongs to; verified against the real corpus for all 11 phb1 classes
  (`test_phb1_real_corpus_class_segments`). The rest of the overlap (e.g. a
  Starting Package section, which isn't flavor-marked) is left alone --
  the extraction prompt's own attribution rule (see
  `pipeline/owlsperch/queue/prompt.py` below) resolves that remaining
  ambiguity instead of the segmenter picking an owner. Also from
  B10c-mand6: `owlsperch segment <book_id> --kinds class[,prestige_class]`
  re-runs ONLY this toc-driven class/prestige_class pass -- no whole-book
  `build_segments` pass, no stale-segment-file removal, no coverage
  warning -- so a class-segmentation fix can be re-applied without
  resetting every other segment's extraction status; any other kind is a
  hard error (exit 1, nothing written), since every kind besides class/
  prestige_class comes from the single whole-book pass and can't be
  produced selectively. Rewriting a class segment file this way (`--force`)
  resets it to `pending` with no claims -- its previously claimed records
  are orphaned on disk unless deleted first (`queue reset --hard`).
  Batch B11 adds an errata/update anchor, gated entirely on the book's manifest
  `kind`: `anchors.find_triggers` takes a keyword-only `entry_kind`
  (`"errata_entry"`/`"update_entry"`, resolved by `segment/runner.py` from
  the book's own `ManifestEntry.kind` -- `errata`/`update`, else `None`),
  and produces that anchor ONLY when `entry_kind` isn't `None`, so a
  rulebook sentence like "(see the Player's Handbook, page 44)" can never
  create a bogus segment in a normal book. When enabled, every paragraph
  whose first 120 characters (after whitespace normalization) contain a
  ", page N" reference, with a non-empty prefix of at most 12 words before
  the match, is one entry -- the whole booklet segments into one entry per
  paragraph. These triggers are detected FIRST, before spell/feat/table/
  stat_block, so an entry body that happens to contain a "Benefit:" cue
  can't be stolen by the feat anchor. `strip_common_heading_suffix`
  cleans the raw heading: computed once per book from every anchor's own
  prefix, it finds the longest trailing word n-gram (1-4 words) common to
  at least half the entries (minimum 3) and strips it -- turning "Glibness
  Player's Handbook" into "Glibness" -- leaving a heading alone when fewer
  than 3 entries exist or no n-gram qualifies.

- `pipeline/owlsperch/supersede.py` (batch B10c-mand2, plus B10c-mand11) --
  `is_class_owned_fragment(heading, kind_hint, class_title)` is the shared,
  pure predicate that decides the SCOPE of superseding for all three places
  that used to go by page span alone (the class-span stamp pass above,
  `build-db`'s `_apply_superseding`, and `queue audit`'s
  `wrongly_superseded` restore): True for any `table`, and for a
  `rules_section` whose whole heading -- case/punctuation/whitespace-
  insensitively, with any parenthetical qualifier stripped first so a
  RECORD name like "Class Features (Barbarian)" reads the same as a
  segment's own bare heading -- is the class title (plural-tolerant, since
  PHB prints "WIZARDS" for the toc's "Wizard"), `game rule information`,
  `class skills`, `class features`, `ex-<title>` (plural-tolerant), or ends
  with `<title> starting package`; False for everything else, notably every
  printed sidebar inside a class's own pages (FAMILIARS, ARCANE SPELLS AND
  ARMOR, SCHOOL SPECIALIZATION, LEVEL ADVANCEMENT, THE PALADIN'S MOUNT,
  SAMPLE PALADIN'S MOUNTS, THE DRUID'S ANIMAL COMPANION, ALTERNATIVE ANIMAL
  COMPANIONS -- note that "THE PALADIN'S MOUNT" NAMES the class and still
  fails, since every match is on the WHOLE heading, never a substring) and
  for every other kind (a spell/feat fragment stranded in a class's span is
  never class-owned). `release_segment_
  claims(segment, *, data_dir)` is the shared helper behind both the
  class-span stamp pass above and `queue audit --fix` below: for ONE
  superseded segment, every path in its `records` + `pending_records`
  (deduplicated by resolved path) is MOVED (`os.replace`, never deleted)
  from `records/<book_id>/<type>/<file>.json` to
  `$OWLSPERCH_DATA/superseded/<book_id>/<type>/<file>.json` (parents
  created; a destination name already taken gets `-<seg_id>`, then
  `-<seg_id>-2`, ... appended rather than ever overwriting an existing file
  there), UNLESS the file's own `extraction.segment_id` names a different
  segment that is still live (not itself superseded) -- releasing must
  never steal a live segment's record, so that claim is pruned from the
  segment's own lists but the file is left exactly where it is. Either way
  the segment's `records`/`pending_records` are cleared and one
  `ReleasedRecord` (`{path, moved_to}`, `moved_to` null when the file
  wasn't moved) is appended per claimed path onto the segment's own
  `released_records` list. It mutates the `Segment` object passed to it
  but never writes it to disk -- the caller persists it. `validate` and
  `build-db` never look inside `superseded/` at all (they only ever glob
  `records/<book_id>/*/*.json`), so a file living there is invisible to
  both by construction.

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
  single source of truth for chapter filtering now. Batch B10c adds
  `class.json`/`prestige_class.json` (`hit_die`, `class_type`, `max_level`,
  `alignment`, `abbreviation`, `class_skills`, `skill_points`,
  `bab_progression`, `save_progressions`, `spellcasting` -- omitted
  entirely for a non-caster, never `null` -- `level_table`,
  `class_features`, `description_sections`,
  `weapon_and_armor_proficiency`, `source_pages`, plus `requirements` on
  the prestige schema only) and `schemas/skills.json` -- a committed,
  plain (non-JSON-Schema) list of the 36 3.5e skill names, loaded by a new
  `owlsperch.schemas.load_skills` beside `load_categories` and used by
  `validate/checks.py`'s class-skill check. `categories.json` gains a
  `prestige-classes` category (right after `classes`), and
  `owlsperch.toc.categories` gets a `Prestige Class(es)` pattern tried
  BEFORE the generic `Classes` one, so a prestige-class chapter/section
  resolves to `prestige-classes` instead. Batch B10c-mand3 (Part 4) bumps
  `class.json` (and `registry.json`'s `types.class.version`) to schema
  version 2 and adds an `allOf`/`if`/`then` conditional (draft 2020-12):
  when `fields.class_type` is `"base"`, `class_skills`, `skill_points`,
  `alignment` (now required as a plain string, not nullable), non-empty
  `description_sections`, and `weapon_and_armor_proficiency` (also
  required as a plain string) all become required -- a `prestige`/`npc`-
  typed `class` record is unaffected, and `prestige_class.json` itself is
  untouched at version 1 (its own `class_type` is never `"base"` in
  practice). `schemas/examples/class.json` gains `fields.abbreviation`
  and bumps to `schema_version: 2`. Batch B11 adds `errata_entry.json` and
  `update_entry.json` (byte-identical structure, version 1): `target_book`
  (the corrected book's `book_id`, never the printed title), `target_page`
  (nullable, the TARGET book's printed page), `target_name` (the entity
  name as the entry prints it, unqualified), and `replacement_text` (the
  literal new wording, or the entry's full body when it gives none) --
  neither touches `envelope.json` or bumps any existing type's version.
  `schemas/examples/errata_entry.json`/`update_entry.json` model the
  mandatory naming rule (see `validate/` below): the record `name` is
  `"<target_name> (p. <target_page>)"` when a page is present, else the
  bare `target_name`, so two entries in one errata file that happen to
  share a bare name (a real occurrence -- PHB's errata corrects "Overrun"
  twice, at two different pages) get distinct slugs/ids instead of one
  silently overwriting the other.
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
  model) idempotently -- batch B10c-mand4: `validate_record_files` checks
  every record file in one run first (`_check_record_file`, pure, no
  writes), then groups the results by `(book_id, segment_id)` and writes
  each segment back exactly ONCE for its whole group (`_write_back_group`):
  any FAIL in the group (or a `pending_records` claim this run's discovery
  never found at all, `missing_record_path: <p>`) fails the WHOLE group --
  one combined `record_failure` attempt, every group path dropped from
  `pending_records`, and any of them that was a live claim there and never
  reached `records` deleted from disk -- otherwise every passing path is
  promoted and the segment is `done`/`validated` only once
  `pending_records` is left empty. This is what stops a passing sibling
  record (e.g. a class's owned level table) from silently overwriting a
  failing sibling's (the class record's own) escalation, which is what
  buried 4 of 11 PHB classes before this fix. The group failure's own
  ladder-attempt `tier` is, in order: the first failing record's own
  `extraction.tier`; else the segment's `claim_tier` (a new `Segment`
  field, stamped by `owlsperch queue complete` to the segment's current
  `tier` whenever a reply actually claims at least one record path, and
  cleared by `queue reset --hard`); else the segment's current `tier`. The
  middle rung fixes a ladder-idempotency defect the B10c-mand4 review left
  open: a group whose only problem is a `missing_record_path` has no file
  on disk to read an `extraction.tier` from, and falling straight back to
  the segment's *current* `tier` picked up whatever tier the *previous*
  run had just advanced it to -- so re-validating an entirely unchanged
  segment walked the escalation ladder once per `validate` run instead of
  recording one idempotent repeat, eventually exhausting opus and landing
  the segment in `human/` with zero new extraction attempts. `claim_tier`
  is stable across runs (only `queue complete` moves it), so this stays
  idempotent; `owlsperch.queue.ladder`'s own stale-attempt guard (see its
  module docstring) is what stops the ladder from moving once the group's
  first real failure has already advanced it past `claim_tier`. A real
  `$OWLSPERCH_DATA` segment written before this field existed has no
  `claim_tier` (`None`) and still falls back to its current `tier`, same as
  before this fix, until it next goes through `queue complete` or a
  `queue reset --hard`. `validate_record_file` is
  kept as a single-record convenience wrapper around it; a caller checking
  several files that might share a segment (`run_validate`,
  `owlsperch.queue.driver`) must call `validate_record_files` directly with
  the whole list, or it loses this atomicity. Bumping a type's `schema_version` in
  `schemas/registry.json` requires following up with `uv run owlsperch
  validate <book_id|all> --bump-compatible` against the data dir --
  otherwise every older record stays stale, `owlsperch build-db` skips all
  of them, and `server/tests/test_corpus.py` fails (or skips, naming this
  command) until the migration is run. Batch B10c adds a `ValidationContext`
  dataclass (`record_by_id`, `spell_list_classes`) for the checks that need
  a cross-record lookup a single-record check can't do alone -- `checks.py`
  itself stays I/O-free (`NULL_CONTEXT` is the no-lookup default);
  `validate/runner.py`'s `build_validation_context(data_dir)` builds the
  real, disk-backed one (`record_by_id` parses `<type>:<book_id>:<slug>`
  into a records path; `spell_list_classes` scans a book's own spell
  records once, memoized) and both `validate_record_file` and
  `owlsperch.build_db.runner` thread it through `validate_record`. The
  class/`prestige_class` checks this feeds (`TYPE_CONTEXT_CHECKS`,
  `check_class_fields`): `hit_die` in the valid set; `level_table` resolves
  to a real `table` record cross-linked both ways; that table's row
  count/Level column matches `max_level`; its Base-Attack-Bonus and save
  columns match the class's own declared progressions (normalizing every
  dash glyph and stray space in a cell before comparing); every DISTINCT
  Special-column entry (B10c-mand4: `_split_special_cell` splits a cell on
  commas OUTSIDE parentheses only, so `"Wild shape (Huge elemental,
  2/day), Venom immunity"` yields two tokens, not three -- a stray
  unbalanced `)` clamps depth at 0 rather than swallowing the rest of the
  cell) matches a `class_features[].name` when the normalized feature
  name appears in the token as a whole-word sequence -- prefix, suffix,
  or infix, never substring, and never the reverse direction -- UNLESS
  one of the token's leftover words is a tier modifier (greater/
  improved/lesser/mighty/tireless/superior/mass/advanced,
  `_TIER_MODIFIER_WORDS`), so "Greater rage" is never satisfied by a
  bare "Rage" entry (B10c-mand8 -- `_special_token_matches_feature`;
  replaces a one-way prefix test that required the feature name to be
  a PREFIX of the token, which is what left "Summon familiar" vs the
  printed heading "Familiar" unmatched and kept sorcerer/wizard/rogue
  in `human/`), checked ONCE per
  normalized token across the WHOLE level table rather than once per row
  -- a repeated progression ("1st favored enemy"/"2nd favored enemy"/...,
  "Inspire courage +2"/"+3"/"+4") is one required `class_features` entry,
  not one per level/row it appears at, and the error this produces still
  names the level/token of that token's first appearance in the table.
  Every feature's `level` appears in the table; `class_features[].text_md`
  must be non-empty (both `class.json`/`prestige_class.json`'s own
  `minLength: 1` and this check reject `""`/missing/whitespace-only --
  B10c-mand4, reversing the earlier "may be empty" exception: a Special
  entry the book prints with no description of its own still gets a
  `class_features` entry, whose `text_md` is that Special cell's own
  printed wording, never an invented description); two `class_features`
  entries whose names normalize to the same thing (e.g. "Bonus Feat"/
  "Bonus Feats", "Inspire Courage"/"Inspire Courage +2") is also an error,
  one per duplicated name; and when `fields.spellcasting` is set, some
  `class_features` entry's normalized name must equal
  `_normalize_special_token("Spells")` -- a caster with no printed
  `Spells` class feature recorded at all now fails validation instead of
  silently passing. `class_skills[].skill` (trailing parentheticals
  stripped) must be a name from the committed `schemas/skills.json` list;
  `spellcasting.spell_list`, when present, must match a real
  `levels[].class` value from the book's own spell records -- but is
  skipped entirely (not failed) when the book has no spell records loaded
  yet, since that's simply not extracted yet, not wrong. Batch B10c-mand3
  (Part 3): `_normalize_special_token` (shared by the Special/
  `class_features[].name` prefix match above) strips every parenthetical,
  a trailing numeric bonus (`+N`, `+NdN`), and singularizes each word
  (`_fold_trailing_plural`: `ies` -> `y`, a sibilant-stem `es` dropped,
  else a trailing "s" stripped, skipping words ending `ss`/`us`/`is`, so
  "bonus"/"class" survive; the `ies`/`es` rules are B10c-mand8, so
  "Special Abilities" and "Special ability" both normalize to "special
  ability") before the containment comparison -- so a Special cell reading
  "Bonus feat" matches a `class_features` entry spelled "Bonus Feats" (the
  fighter's own printed heading) without the record having to misspell the
  feature to validate; B10c-mand4 additionally strips a leading ordinal
  (`2nd `), a trailing distance (`30 ft.`/`30 feet`), and a trailing
  use-frequency (`2/day`, `1/week`, `3/encounter`), which is what collapses
  a rising-value progression's per-level Special cells down to the one
  name its single `class_features` entry describes. `check_class_fields`
  also requires, whenever `fields.spellcasting` is set, that the resolved
  `level_table` has at least one column matching `/per day|known|points/i`
  (checked against the SAME "Spells per Day `<slot>`" naming convention
  `_KIND_RULES["class"]`/`["prestige_class"]` states in the prompt) -- a
  caster class whose level table has no spells-per-day/known/points column
  now fails validation instead of silently passing. B10c-mand10 adds, for a
  `class_type: "base"` caster only: when the table's "Spells per Day
  <slot>" columns reach 5th-level spells or higher, a "Spells per Day 0"
  column must also be present (`_spell_slot_levels`; paladin/ranger top
  out at 4th and have none) -- the real-corpus miss this catches is the
  cleric's Table 3-6, whose orisons column the text layer dropped
  entirely while every 1st..9th column was present. `check_table_fields`
  (B10c-mand4) also checks, per column, that cells with no printed value
  are all written the SAME way -- either every one blank (`""`) or every
  one a dash placeholder (`"—"`, `"–"`, `"-"`, `"--"`, or the Unicode minus
  `"−"`) -- never a mix of both within one column (the real-corpus catch
  this guards against: PHB table 3-15's rogue Special column writes "no
  special ability" as both `"—"` and `""`). Bumping `class`/
  `prestige_class`'s `schema_version` this way (2->3, 1->2) needs the same
  `--bump-compatible` migration named above; `find_bump_candidates`
  (`validate/runner.py`) builds a real `build_validation_context(data_dir)`
  and threads it into its own `validate_record` call (a bug fixed the same
  batch -- it used to fall back to `NULL_CONTEXT`, so every class/
  `prestige_class` candidate's `level_table` cross-reference reported "not
  found" and could never be bumped). Batch B11 adds
  `check_errata_entry_fields`/`check_update_entry_fields` (both registered
  in `TYPE_FIELD_CHECKS`): non-empty `target_book`/`target_name`/
  `replacement_text`, `target_page` either absent/null or a positive
  integer, and -- the mandatory rule schemas alone can't express -- the
  record's `name` matches exactly `"<target_name> (p. <target_page>)"`
  when a page is present, else the bare `target_name`. Batch B10c-mand12
  adds three class/`prestige_class` rules the 2026-09-18 class quality
  judgement found nothing caught. (1) In `check_class_fields`,
  `_check_source_pages`: `fields.source_pages` is a PDF page span (that's
  how `build_db._apply_superseding` reads it), so `start`/`end` must equal
  `min(pages)`/`max(pages)` of the record's own envelope `pages` -- the real
  catch is the PHB druid, whose `source_pages` were written as the PRINTED
  span 33-37 against pdf pages 34-38. (2)/(3) A new, SEGMENT-aware
  `check_class_segment_coverage(record, segment)`, dispatched from a third
  registry, `TYPE_SEGMENT_CHECKS` (`validate/runner.py`'s `validate_record`
  passes it the same already-resolved segment dict
  `check_pages_within_segment` gets, so `build-db` and
  `find_bump_candidates` get it for free too -- see the BLAST RADIUS note at
  the end of this paragraph), which reads the owning segment's own `text` as
  the evidence of what the page printed and returns `[]` silently when there
  is no segment, no `text`, or no usable record `name`; when a segment has
  text but no recognizable Class Features section only (b)/(c) are skipped,
  since (a) works off the raw text and still runs: (a) every
  `<Words> <Class> Starting Package`/`Ex-<Class>` heading printed on a line
  of its own must be recorded as a `description_sections[].heading` OR a
  `class_features[].name` (the real cleric files "Ex-Clerics" as a feature
  -- an equally faithful placement), which is what catches the paladin
  (both) and sorcerer (starting package) dropping theirs; (b) inside the
  printed Class Features WINDOW -- from the paragraph that is exactly the
  "Class Features" heading (never merely one STARTING with those words: the
  chapter intro prints "Class Features: Special characteristics of the
  class..." as a run-in and a class segment's back-extended text routinely
  carries it) or that opens "All of the following are class features", up to
  the first `Ex-<Class>`/`... Starting Package` heading or the first
  ALL-CAPS sidebar/section heading (`_is_caps_heading` -- this is what stops
  "THE DRUID'S ANIMAL COMPANION"'s and "SCHOOL SPECIALIZATION"'s own run-in
  headings from being demanded as class features) -- every run-in heading
  (`_RUN_IN_HEADING_RE`: `<Heading>:` at a paragraph start or after
  sentence-final punctuation, `_SENTENCE_END_RE` allowing the `.)` a
  trailing citation prints, 2-60 chars of `[A-Za-z'’,()/ -]` starting with a
  capital, AND `_is_heading_shaped`: at most 8 whitespace tokens with every
  token longer than 3 characters capitalized once its edge punctuation is
  stripped -- without which an ordinary mid-paragraph clause ending in a
  colon, e.g. "If she has a familiar, the following apply:", is read as a
  heading, reported as a missing feature AND cuts the real feature's printed
  span short; calibrated so all ~150 real PHB run-in headings, up to
  "Tongue of the Sun and Moon (Ex)" at 7 tokens, still pass) must equal,
  after `_normalize_heading` (parentheticals dropped,
  punctuation folded, lowercased, per-word singularized), either a
  `class_features[].name` or one of `_NON_FEATURE_RUN_IN_HEADINGS` (the
  printed GAME RULE INFORMATION/flavor-section/starting-package labels plus
  the in-prose "Exceptions:"/"Note:" clarifiers; only "Exceptions" and
  "Skill Selection" actually leak on the real corpus, and "Feat"/"Feats" are
  deliberately NOT listed since the rogue prints one as a real ability).
  EXACT normalized equality, not `_special_token_matches_feature`'s
  containment, on purpose: "Deity, Domains, and Domain Spells" CONTAINS
  "spell" and would be satisfied by the sibling `Spells` feature it was
  wrongly nested inside, which is precisely the cleric defect (3 printed
  run-in headings nested as bold paragraphs inside `Spells`) this rule
  exists to catch; and (c) each heading matched to a `class_features` entry
  must have a `text_md` whose word count is at least
  `_FEATURE_TEXT_COVERAGE_RATIO` (0.75) of its printed span's -- the span
  running from the heading to the next run-in heading in the SAME paragraph,
  extended by `_column_break_continuation` when the paragraph is cut off at
  a column break (the bard's printed "Spells:" body ends mid-word at "Cha 11
  for 1st-" and continues in a paragraph the column reconstructor emitted
  EARLIER). Leaving a window UNSTITCHED only ever shortens a span and raises
  a ratio, so it can never cause a false failure -- but stitching the WRONG
  pair appends unrelated prose and LOWERS the ratio, so a stitch takes three
  things: the window must hold exactly one truncated prose paragraph and
  exactly one that begins mid-sentence (no pairing to choose between); the
  truncated paragraph must end mid-WORD on a hard hyphen
  (`_COLUMN_BREAK_HYPHEN_RE`) with the continuation opening on that word's
  lowercase remainder -- a paragraph that merely ends without a full stop is
  routine in a reconstructed column (the monk's and druid's windows both do)
  and is NOT corroboration; and the appended text is capped at the
  continuation's own prefix BEFORE its first run-in heading, so at most one
  printed feature's worth is ever added. A word-count cap tied to the
  truncated paragraph is deliberately NOT used -- the bard's truncated tail
  is 89 words against a 487-word continuation, so any such cap would hide
  the very paraphrase rule (c) exists to catch. 0.75,
  not the 0.60 first proposed, because the real distribution over all 90
  (heading, feature) pairs in the 11 PHB class records is 87 at >= 0.92
  (mostly exactly 1.00 -- verbatim), one at 0.85 (the rogue's Trapfinding,
  which drops a single printed sentence -- a MINOR nobody raised) and one at
  0.67 (the bard's condensed `Spells`, the MAJOR this rule exists for): 0.60
  catches nothing at all. `pipeline/tests/test_validate_checks.py`'s
  `test_phb1_real_corpus_class_validator_calibration` (a `@pytest.mark.
  corpus` test, skipped unless `$OWLSPERCH_DATA` has all 11 phb1 class
  records and their segments) pins that calibration: druid fails (1),
  paladin + sorcerer fail (2a), cleric fails (2b), bard fails (2c), and
  nothing else fails anything. BLAST RADIUS: these are ordinary
  `validate_record` errors, so a class record that trips one is not just
  reported by `owlsperch validate` -- `owlsperch build-db` counts it in
  `skipped_invalid` and leaves it OUT of the database (its `/r/class/<slug>`
  page 404s and any table naming it as `parent_record` becomes a dangling
  parent), `build-db --strict` exits 1, and `find_bump_candidates` will not
  offer it for a `--bump-compatible` migration. The fix is always to
  re-extract the segment, never to loosen the check. Batch B10c-mand16
  adds a fourth `check_class_segment_coverage` rule for the 2026-09-21
  class quality judgement's finding D -- a printed "Abilities:" run-in
  inside the new `_game_rule_information_window` (from the "GAME RULE
  INFORMATION" heading paragraph to the "Class Skills" heading or the
  next ALL-CAPS heading, whichever comes first) must be recorded as a
  `description_sections[].heading` normalizing to "Abilities" -- which
  the real corpus calibration (a new `abilities` key in
  `_EXPECTED_CORPUS_FAILURES`) confirms fails exactly the six classes
  extracted before the B10c-mand13 prompt rule existed (barbarian,
  fighter, monk, ranger, rogue, wizard).

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
  `no_content` guidance, its (batch B10c-mand16, finding B) rule that a
  segment printing SEVERAL grids (e.g. the FAMILIARS sidebar's own
  progression grid AND its separate "Familiar | Special" list) writes each
  one as its own `table` record, in printed order, never inlined as
  markdown/prose -- a two-column label/value grid whose right cells are
  full sentences still counts, and (B10 retrospective) a rule that a GENERIC,
  cross-chapter heading (e.g. "Class Features") must have its record `name`
  qualified with the enclosing entity (`Class Features (Barbarian)`, never
  the bare heading) since `slug`/`id` derive from `name` and an unqualified
  generic name collides across chapters -- modeled by
  `schemas/examples/rules_section.json`'s own "Class Features (Sable
  Knight)" example; class/prestige_class's (batch B10c-mand6) pre-heading-
  tail attribution rule (this segment's own text may begin with a column-
  reconstruction tail from BEFORE its heading -- attribute each such
  passage by the class/entry it actually names, never by position),
  `class_features` sourced from the printed "Class Features" section's own
  run-in headings rather than the level table's Special column (a CROSS-
  CHECK only, whose bleed/precedence handling folds into the existing
  COLUMN BLEED paragraph), `weapon_and_armor_proficiency` filed apart from
  `class_features`, printed "<Race> <Class> Starting Package" sections kept
  as `description_sections` entries (class only), and `alignment` as the
  short verbatim GAME RULE INFORMATION line, distinct from the flavor
  "Alignment" `description_sections` entry of the same name; table's
  verbatim title, column/row padding, and caption-only-segment `no_content`
  guidance -- a kind_hint with no entry,
  e.g. `stat_block`, gets no rules section). Batch B10c-mand13 (the
  2026-09-18 class quality judgement) adds five more class/prestige_class
  rules: every printed heading in the span must land in the record, with a
  printed `Ex-<Class>` section and the printed `Abilities:` paragraph each
  called out by name as their own `description_sections` entries; a bold
  run-in heading printed INSIDE the Class Features section (e.g. the
  cleric's `Spontaneous Casting:`) is its own `class_features` entry, never
  nested inside a different feature's `text_md`; a feature's `text_md` is
  the complete printed text verbatim and in printed order, with a printed
  typo kept and noted rather than silently corrected; a titled sidebar
  (`FAMILIARS`, `THE PALADIN'S MOUNT`, ...) is extracted as its own
  rules_section, never copied into a feature, while an untitled grid inside
  the class's own feature text still gets its own `table` record; and (a
  fifth rule, in the shared table convention below since it applies to
  every owned table, not just the level table) a class's own secondary
  titled table is written as its own `table` record too, plus a
  `_KIND_RULES["table"]` rule folding a printed footnote into `caption` and
  keeping a wrapped two-line header's printed left-to-right column order.
  `schemas/examples/class.json` models the Abilities section and a
  run-in-turned-standalone feature ("Bonus Spells"). A shared "### Tables
  belonging to this entity" convention for every non-table kind (write a
  second `table` record alongside the entity's own when the segment's text
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
  Batch B10c-mand4: every output directory the prompt shows a subagent (its
  own `output_dir` and, for a non-`table` kind, the "Tables belonging to
  this entity" convention's second directory) is that segment's own
  private staging tree (`owlsperch.queue.common.staging_records_root` --
  `staging/<book_id>/<seg_id>/records/<type>/`), never
  `records/<book_id>/<type>/` directly, with one added sentence in the
  output contract explaining that `queue complete` moves an accepted file
  from there into the shared records directory itself.
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
  `pending_records` after checking each claimed path (batch B10c-mand4)
  resolves inside THIS segment's own staging records root
  (`resolve_staged_record_path`) and exists on disk there, and translates
  to a well-formed `<type>/<file>.json` destination
  (`destination_rel_path_for_staged` -- `records/<book_id>/<type>/
  <file>.json`; a bare `records/<book_id>/...` claim, with no staging
  prefix, is now `missing_record_path` too), and that DESTINATION is not
  already owned by a *different* segment of the same book --
  `_record_path_owners` scans every `segments/<book_id>/*.json` and
  `human/<book_id>/*.json` file's own `records`/`pending_records` for this,
  since the record file's own `extraction.segment_id` is only a
  subagent-copied placeholder by the time `queue complete` runs and would
  already name a thief on a just-clobbered file -- except a claimant whose
  own `superseded_by` is set (batch B10c-mand2), which no longer counts as
  an owner at all: that claim is meant to be RELEASED (see
  `pipeline/owlsperch/supersede.py` above), most commonly a level table
  sharing its superseding class's own printed title (and so the same
  slug/id/path), and this exemption is belt-and-braces for a claim that
  survives release for any reason. Additionally (batch B10c-mand4), a
  destination that already exists is refused if its own
  `extraction.segment_id` names a different, still-live (not superseded)
  segment -- catching an orphaned destination no live segment's index
  claims -- but a destination this same segment already wrote (a retry)
  may be overwritten. A missing or colliding
  path, like a malformed reply, escalates the segment via
  `ladder.record_failure` (both kinds folded into the one attempt for a
  single reply), moving it to `human/` if already on opus; a colliding
  destination is left completely untouched on disk. Only once every
  claimed path survives all these checks does `complete.py` actually move
  anything: `os.replace` (same filesystem, a rename) from staging to
  destination, then `_overwrite_authoritative_fields` as before. The
  segment's own `staging/<book_id>/<seg_id>/` directory is removed once
  the reply has been fully handled, on every branch (records, no_content,
  needs_context, proposed_type, malformed) -- `pipeline/owlsperch/queue/
  common.py`'s `staging_records_root`/`resolve_staged_record_path`/
  `destination_rel_path_for_staged` are the shared staging-path helpers,
  next to `resolve_record_path_under_book`. `summary.py` reports per-tier
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
  record file for a stale (non-superseded) claim. Batch B10c-mand2 adds a
  third pass, for the segments B10c's class-span stamp pass marked
  `superseded_by` before it released claims at stamp time: `audit_book`
  also reports `superseded_claims` -- every segment whose own
  `superseded_by` is set that still holds a claim -- and EXCLUDES a
  superseded segment from `stale_claims` entirely (so this pass can never
  soft-reset a segment B10c-mand2 deliberately freezes back to `pending`).
  `fix_book` releases every `superseded_claims` entry through the shared
  `owlsperch.supersede.release_segment_claims` helper (reported with
  `action: "released"`, the released paths, and a `moved` list of
  `{"from", "to"}` for the files actually moved), except one sitting in
  `human/`, which -- like a `stale_claims` victim there -- is reported but
  left completely untouched (`action: "left_in_human"`); this is `queue
  audit --fix`'s retroactive counterpart to the class-span pass's
  release-at-stamp-time behavior, for exactly the segments stamped before
  that behavior existed. Batch B10c-mand11 adds a FOURTH pass,
  `wrongly_superseded`, the retroactive counterpart to that batch's stamp-
  pass scoping: every segment whose `superseded_by` names a class/
  prestige_class segment of the same book whose own `(heading, kind_hint)`
  FAILS `owlsperch.supersede.is_class_owned_fragment` for that class
  segment's `heading` (a `superseded_by` naming a segment this book has no
  file for, or one that isn't a class at all, is hand-made and left
  entirely alone). Such a segment is EXCLUDED from `superseded_claims` --
  that pass would release the very claims this one restores -- and
  `fix_book` restores it (`action: "restored"`): clear `superseded_by`, move
  each `released_records` entry's file back (`os.replace`) from its
  `moved_to` to its original `records/<book_id>/` path, put every restored
  path back in the segment's own `records`, and leave `status`/`outcome`/
  `tier`/`attempts` exactly as they are (those records had already passed
  validation before the wrong stamp). A destination that already exists is
  never overwritten -- that file stays under `superseded/`, is reported in
  the entry's own `blocked` list with a reason, keeps its
  `released_records` entry, and the segment KEEPS its `superseded_by`
  stamp so it stays in `wrongly_superseded` and the next `--fix` retries
  it once the collision is cleared -- and a segment in
  `human/` is reported but left completely untouched, like everywhere else
  here. A second `--fix` is a no-op (the restored segment no longer carries
  `superseded_by`), and `queue summary`'s `superseded` count drops for it
  on its own, since that count just reads `superseded_by`. `runner.py`
  wires all of it (including `queue
  audit [--fix]`, whose non-JSON `--fix` output now also prints a "`N`
  claim(s) released, `M` record file(s) moved to superseded/" line and a
  "`N` segment(s) un-superseded, `M` record file(s) restored" line) into
  the CLI. Batch B10c-mand3 (Part 8): `queue reset --hard` resets a
  segment's `tier` via `ladder.starting_tier(segment.kind_hint)` rather
  than unconditionally to haiku, so a hard-reset `class`/`prestige_class`
  segment lands back on sonnet (its own `STARTING_TIERS` entry) instead of
  being demoted -- the mandated post-merge recovery hard-resets all 11
  class segments and needs them to stay on sonnet. `owlsperch validate`
  promotes a path from
  `pending_records` to `records` on PASS and drops it (deleting the file
  too, unless it's also in `records`) on FAIL. The skill itself is
  `.claude/skills/extract/SKILL.md`
  -- a short Claude-Code-facing loop over these commands plus Agent-tool
  subagent launches, using whatever tier `queue next` returns per item; all
  the logic that can be unit tested lives in `queue/` instead of the skill
  doc. Batch B10c adds `ladder.STARTING_TIERS` (`{"class": "sonnet",
  "prestige_class": "sonnet"}`) and `starting_tier(kind)`, which
  `segment/runner.py` uses instead of a flat `SEGMENT_TIER` constant when
  writing a fresh segment of any kind -- a class/prestige_class entry (a
  level table plus several structured sub-objects) is reliably too complex
  for haiku on a first attempt, so it starts one rung up; everything else
  still starts at haiku. Batch B11 adds `"errata_entry": "sonnet"` and
  `"update_entry": "sonnet"` to `STARTING_TIERS` -- a mis-read
  `target_name`/`target_page` doesn't fail schema validation, it silently
  fails to match at build time and lands in `human/`, which costs more
  than the cheaper tier saves. A segment with `superseded_by` set is
  frozen: `select.py`'s stale-reset/lazy-escalation heal pass and its
  selection filter both skip it outright (never reset, never escalated,
  never selected), and `summary.py` excludes it from `pending`/
  `pending_by_kind`/`pending_by_tier` while reporting a separate
  `superseded` count. `prompt.py`'s "## Book" section gains an "Applies to
  book ID" line (the book_id an errata/update booklet corrects, from
  `ManifestEntry.applies_to`, or "(none)" for every other book), and
  `_KIND_RULES` gains `"errata_entry"`/`"update_entry"` entries: copy
  `target_book` from that line verbatim (never the printed title);
  `target_page` is the number after "page" in the target reference (OMIT
  the key, never `null`, when the entry cites none -- the usual case for
  an update booklet entry); `target_name` is the heading words before that
  reference, with the per-book common suffix already stripped for the
  subagent; `replacement_text` is the literal wording after a "read as
  follows:"/"as follows:"/"with the following text:" marker, falling back
  to the entry's full body when there is none; `text_md` is always the
  full body, a separate field from `replacement_text` even when they'd
  read the same; and `name`/`slug`/`id` follow the qualified-naming rule
  above, called out explicitly since one errata file can correct the same
  bare name at two different pages.

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
  B10c-mand4 adds a read-only check, run once after every record is loaded
  and after `_apply_superseding`: `_find_dangling_parents` selects every
  `tables` row whose `parent_record` names an id that never made it into
  `records` (missing, invalid, skipped, or superseded away) -- such a
  table previously rendered fine in `/browse/table` while
  `GET /records/<type>/<slug>` 404s for the entity it claims to belong to,
  with no warning anywhere. `BuildResult.dangling_parents` (a
  `DanglingParent(record_id, parent_record)` list) gets its own "Dangling
  table parents: N" render line and its own `run_build_db` WARNING (first
  5, same shape as the skipped-invalid one); `--strict` now also exits 1
  when there's any dangling parent, in addition to `skipped_invalid > 0`.
  Batch B10b adds four more `records` columns -- `toc_category` (`NOT NULL
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
  Batch B10c adds a `superseded_by TEXT` column to `records` and a second
  pass run once every record is loaded (`_apply_superseding`): for every
  `class`/`prestige_class` record, `fields.source_pages` (falling back to
  `min(pages)..max(pages)`) gives its whole entry's pdf page span, and
  every `rules_section`/`table` record of the SAME book whose own `pages`
  fall entirely inside that span AND (batch B10c-mand11) that the class owns
  BY NAME gets `canonical = 0`/`superseded_by = <class id>` set -- except
  (B10c-mand14) a `table` whose `fields.parent_record` names a non-class
  record this pass leaves canonical (a sidebar's own grid, e.g. the
  Familiars progression table): a table follows its parent, since every
  table otherwise passes the ownership predicate and the sidebar would
  render a non-canonical table under a canonical parent. Owned by
  name means EITHER `owlsperch.supersede.is_class_owned_fragment(name, type,
  <class record name>)` -- the same predicate the segmenter's own stamp pass
  uses, read here against the record's `name`/`type` -- OR (this pass, unlike
  the segmenter, has the class RECORD in hand) the record's normalized name
  being one of that class record's own printed headings: `_class_owned_names`
  = every `fields.class_features[].name`, every `fields.
  description_sections[].heading`, and the class's own `name`, all through
  `owlsperch.supersede.normalize_heading` (parentheticals dropped, so a
  printed "Wild Shape (Su)" feature matches a record plainly named "Wild
  Shape"), built once per class record. That second rule is what demotes the
  druid's own "Wild Shape"/"Venom Immunity" `rules_section` records --
  extracted as separate sections before class records existed -- while
  leaving a sidebar on the same pages canonical. EXCEPT a `table` owned by
  some class in that book
  (its id in that class's own `tables` array, or its own
  `fields.parent_record` naming a class/prestige_class record), which is
  never superseded -- otherwise a class's own progression table would
  disappear from its own page (the most likely bug this pass could have);
  every other `table` in the span still passes the predicate, so only
  `rules_section` records are newly spared -- a printed sidebar sharing a
  class's pages ("Familiars", "Alternative Animal Companions", "The
  Paladin's Mount", "School Specialization", ...), which page span alone
  demoted to `canonical = 0`, leaving that content in no canonical record at
  all.
  A record already superseded by an earlier class is left alone. Printed
  as one informational line (never a WARNING -- superseding is expected).
  Batch B10c-mand2: belt-and-braces against a record file left behind (or
  restored by hand) after `pipeline/owlsperch/supersede.py`'s
  `release_segment_claims` should have moved it out of `records/` -- a
  record whose OWNING segment (via its own `extraction.segment_id`) itself
  carries `superseded_by` is skipped entirely, counted in its own
  `skipped_superseded` (its own "Skipped (superseded segment): N" render
  line), kept OUT of `skipped_invalid`/the "skipped N invalid record(s)"
  WARNING/`--strict`'s exit 1 (such a record may be perfectly schema-valid;
  it just belongs to a frozen segment). `validate` and `build-db` both only
  ever glob `records/<book_id>/*/*.json`, so `$OWLSPERCH_DATA/superseded/
  <book_id>/<type>/<file>.json` -- where `release_segment_claims` moves
  (never deletes) a released record file -- is invisible to both by
  construction, with no code change needed for that. Batch B11 adds two
  more `records` columns -- `variant_of TEXT` and `applied_overrides TEXT
  NOT NULL DEFAULT '[]'` (a JSON array of entry ids) -- and a new pass,
  `owlsperch.build_db.precedence.apply_precedence`, run once every record
  is loaded, right after `_apply_superseding` and before the `names_fts`
  insert. `variant_of`/`applied_overrides` are a SEPARATE axis from
  `superseded_by`: superseding is "a typed entity in the SAME book
  swallowed these pages" (a class absorbing its own fragments);
  `variant_of` is "this is a duplicate PRINTING of the same content,
  possibly in a different book" (an errata/update override target, a
  Rules Compendium override, or an older printing latest-wins demoted) --
  `apply_precedence` never touches `superseded_by`, and a `superseded_by`
  row is excluded from every precedence step's candidate pool (never a
  target, a winner, or a variant). The pool for every step is `records
  WHERE superseded_by IS NULL`; within it: (1) every `errata_entry`/
  `update_entry` record is matched against same-book candidates of the
  errata's own `fields.target_book`, NAME first (`slug ==
  slugify(target_name)`, appending the entry's id to every match's
  `applied_overrides`), else, if `target_page` is given, PAGE (records
  whose own `pages` contain it, narrowed to a normalized-name overlap when
  more than one, or accepted outright when exactly one) -- then, batch
  B10c-mand17 (the 2026-09-21 class-quality judgement's finding E: a class
  record absorbs its own feature-level `rules_section` fragments, so four
  PHB errata entries -- the druid's Wild Shape/A Thousand Faces/Animal
  Companion, the paladin's Special Mount -- matched nothing at all and
  their corrections never reached the class page), two FALLBACKS when no
  live record matched AT ALL -- an AMBIGUOUS live page match (two same-page
  candidates whose names overlap the target) still stays unmatched and
  reported, never falls through to a class-level apply: (a) the same NAME-then-PAGE match run over the
  target book's SUPERSEDED records (`superseded_by` set, so outside every
  pool), each hit resolved to whatever superseded it (`_superseding_root`
  -- walked and cycle-guarded like `_root_of`, and only counted when that
  root is itself a live non-override record, so no dangling
  `applied_overrides` id is ever written), and failing that (b) a UNIQUE
  same-book `class`/`prestige_class` record whose own
  `fields.class_features[].name` or `fields.description_sections[].heading`
  normalizes (`owlsperch.supersede.normalize_heading`, parentheticals
  dropped -- so "Special Mount (Sp)" matches a bare "Special Mount") to
  `target_name`, narrowed to classes whose own `pages` contain
  `target_page` when the entry gives one (the real "Animal Companion
  (p. 36)" case: the druid and the ranger both print that feature, the page
  picks the druid) and with `owlsperch.supersede`'s shared
  `GENERIC_CLASS_SECTION_HEADINGS` set subtracted first -- the same
  frozenset `build_db.runner._class_owned_names` subtracts, moved into
  `supersede.py` by B10c-mand17 so both callers share one definition -- so
  a heading every class prints ("Alignment", "Class Features") is never
  ownership evidence; `fields.class_skills` is deliberately NOT consulted (a
  class listing "Listen" as a class skill is not the target of an erratum
  correcting the Listen skill); (a) is deliberately tried first,
  since when both hit they name the same class record (the fragment was
  superseded BY that class precisely because the class owns a feature of
  that name) and the fragment's own stored `superseded_by` is the harder
  evidence of which record absorbed that printed text; a heading several
  classes print ("Spells"), or a heading with no `target_page` to narrow by,
  resolves to nothing rather than to an arbitrary one of them, and the superseded fragment itself is never written to (it stays
  outside the pool, so only the superseding record's own
  `applied_overrides` grows and `_merge_overrides_to_winners` carries it
  onward as for any other match) -- an entry matching none of these steps
  is written to `human/<book_id>/overrides/<entry-
  slug>.json` (a SUBDIRECTORY, deliberately: `queue/summary.py`,
  `queue/audit.py`, and `queue/complete.py`'s `_record_path_owners` all
  glob `human/<book_id>/*.json` non-recursively and would otherwise
  mis-parse a loose override file as a `Segment`) with the candidates it
  considered, and listed in `reports/precedence.md`; (2) `rules_section`
  records are grouped by `slugify(fields.topic)` -- a group containing a
  Rules Compendium (`rules-compendium`) record makes that record (lowest
  id) canonical over every other-book record in the group
  (`canonical = 0, variant_of = <RC id>`), and a Rules Compendium section
  matching no other book is listed in the report as unmatched, staying
  canonical; (3) among what's left (`variant_of IS NULL AND canonical =
  1`), records are grouped by `(type, slug)` and the highest `books.
  published` wins (ties broken by `book_id` then `id`, a real total order
  so a build is deterministic even when `published` is equal or missing)
  -- the rest get `canonical = 0, variant_of = <winner id>`. Because step
  (2) can demote a record to an RC winner that step (3) then demotes
  again (that RC winner sharing a `(type, slug)`, but not a normalized
  topic, with a later-published book -- so it was never grouped with the
  RC record in step (2) and stayed eligible for step (3)), a naive
  `variant_of` write would leave a 2-hop chain on disk; after all three
  passes settle `canonical`/`variant_of`, `_flatten_variant_chains`
  (via the shared `_root_of` walk, cycle- and dangling-pointer-safe)
  rewrites every non-root `variant_of` pointer to the ROOT of its chain,
  so the stored graph is always exactly one hop deep and no reader has to
  walk it. Because an override in step (1) is matched against one
  specific row, but steps (2)/(3) run afterward and can demote that exact
  row to a variant of a *different*, same-slug canonical winner,
  `_merge_overrides_to_winners` runs once `_flatten_variant_chains` has
  settled every `variant_of` pointer: it copies every demoted record's
  own `applied_overrides` onto its ultimate winner (via that same
  `_root_of` walk), de-duplicated, order preserved -- otherwise the
  override would sit unreachably on a `canonical = 0` row sharing the
  winner's own `(type, slug)`, which `/records/{type}/{slug}` never falls
  through to (its `canonical = 1` query for that slug always succeeds
  first). The demoted row keeps its own `applied_overrides` too, so the
  D12 direct-URL fallback below still shows exactly what was applied to
  it. `errata_entry`/`update_entry` records are excluded only from being
  override TARGETS (step (1)'s `targets_by_book` skips them); they are
  otherwise ordinary, searchable records and DO take part in step (3)'s
  latest-wins grouping, so two errata books carrying the same correction
  (the same `(type, slug)`) collapse to one canonical entry with the older
  one as its variant -- both are still applied to their target, and both
  still appear in that target's `applied_overrides` list. `write_precedence_
  report`/`write_unmatched_overrides` run AFTER `build_db()`'s temp file is
  atomically renamed into place, so a failed build leaves no stray report
  or `human/` file behind; `write_unmatched_overrides` also clears each
  errata/update source book's `human/<book_id>/overrides/*.json` first, so
  a since-fixed entry doesn't linger. `reports/precedence.md` (written via
  `owlsperch.fsutil.atomic_write_text`) has a summary line count plus two
  tables (unmatched overrides with their candidates, unmatched Rules
  Compendium sections); `BuildResult`/`render()` gain "Overrides applied",
  "Unmatched overrides", "Rules Compendium overrides", and "Variants"
  lines, and `run_build_db` prints one WARNING (never a build failure,
  even under `--strict`) when any override went unmatched, naming the
  report path. Batch B11 also exempts an `errata`/`update` book's manifest
  `kind` from the missing-toc `WARNING` (`_load_records` takes a
  `book_kinds` map from `build_db()`, which already loaded the manifest)
  -- those booklets have no table of contents by nature.
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
  `/schemas` don't touch the database and always answer normally. Batch
  B10c relaxes `/records/{type}/{slug}` to also resolve a superseded
  (`canonical = 0`) record when no canonical row matches, adding
  `superseded_by` (`null` otherwise) to the response body -- `/search`,
  `/records/{type}`, and `/facets/{type}` stay canonical-only (already
  true via their existing `canonical = 1` clauses; nothing needed there).
  `browse.py`'s `FilterableField` gains a `combined` flag: a filterable
  field typed `object` (not array-of-object), e.g. class `spellcasting`,
  now derives per-sub-property filters/facets the same way an
  array-of-object field's items do, but `combined` stays `False` for it
  since `build_db.runner.flatten_fields` writes no *combined*
  `record_fields` row for a plain dict -- every sub-param for it always
  goes through its own `<parent>.<sub>` key, never the array-of-object
  cross-product path. Batch B11 replaces `/records/{type}/{slug}`'s
  pre-precedence `variants` (bare id strings, computed by sorting every
  same-type+slug row by `published` at request time) with objects resolved
  from the `variant_of` column build-db's precedence pass now fills:
  `{id, book_id, book_title, citation, published}` for every record whose
  `variant_of` points at this one, DIRECTLY OR TRANSITIVELY -- `app.py`'s
  `_resolve_variants` walks the closure with a recursive CTE
  (`WITH RECURSIVE chain(id) AS (...)`, `UNION` rather than `UNION ALL` so
  a cycle in the stored data still terminates) over `records.variant_of`
  (indexed by `records_variant_of_idx`) instead of a single-hop equality
  query -- defence in depth, since `build_db.precedence
  ._flatten_variant_chains` already keeps every stored `variant_of` at
  most one hop from its root and a plain equality query would normally
  suffice, but this is robust to any data, including a hand-edited or
  pre-B11 database -- ordered by `published` DESC then `book_id` ASC
  (SQLite already sorts a NULL `published` last in DESC order).
  `applied_overrides` is resolved the same way, from the stored id
  list to `{id, name, type, book_id, book_title, citation, target_page,
  replacement_text}` objects in stored order, skipping an id with no
  matching row rather than raising -- the same defensive shape
  `_resolve_tables` already used for a pending table. `canonical` and
  `variant_of` are overlaid from the `records` COLUMNS onto the parsed
  record JSON (the stored blob's own values are extraction-time defaults
  and go stale the moment precedence runs); the `canonical = 0` fallback
  branch (previously only a class-superseded record's home) now also
  serves a precedence-demoted record whose own slug differs from its
  winner's (the Rules Compendium case), resolving `variant_of`/
  `applied_overrides` fully there too, not the pre-B11 empty shape.
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
  for the tree Playwright spec's flow C; plus, since batch B10c, an
  invented class ("Fixture Mage") with a 3-row level table it owns, class
  features, and `spellcasting.spell_list: "Wizard"` -- matching one of the
  `_SPELLS` entries' own `levels[].class` -- so the class Playwright
  spec's Spells section has a real fixture spell to render; plus, since
  batch B10c-mand3, a `description_sections` entry on the class record
  (class.json's base-class conditional requires it) and a "Spells per Day
  1st" column + cell on the level table (the caster spells-per-day/known/
  points column check), and a fourth toc chapter/section pair ("Chapter 4:
  Classes" / "Fixture Mage", both category `classes`, pdf page 5) so the
  class record resolves to a real `classes` toc category instead of
  `uncategorized`, giving the web tree's Classes branch a fixture to open;
  plus, since batch B10c-mand4, a real (non-empty) `text_md` on every
  class feature and a third feature, `"Spells"` (APPENDED at the end, so
  `web/e2e/class.spec.ts`'s index-addressed feature ids stay stable),
  satisfying the new empty-text_md/duplicate-name/required-Spells-feature
  checks, and `schema_version: 3` (the class schema's `text_md`
  `minLength: 1` bump); plus, since batch B11, a second book
  (`fixture-book-2`, published later) and an errata booklet
  (`fixture-errata`, `applies_to: fixture-book-2`) -- both print an
  invented "Ice Storm" spell, so `fixture-book-2`'s printing wins
  latest-wins and `fixture-book`'s becomes the variant, and
  `fixture-errata`'s one entry (matched to `fixture-book-2`'s printing by
  name) gives `/r/spell/ice-storm` real "Other printings"/"Overrides
  applied" content) into `<dir>` and builds
  `<dir>/db/owlsperch.sqlite` from it via `owlsperch.build_db.runner.build_db`.
  Used by `web/e2e/serve-fixture.py` (the Playwright tests' backend) and
  usable standalone for poking at the UI locally without the real PDF corpus.
- `web/` -- the frontend (spec 4.10, batch B7, plus `/browse/:type` from
  B9): Vite + React 18 + TypeScript, React Router for `/` (search),
  `/browse/:type` (browse + facets), and `/r/:type/:slug` (record detail).
  The toolchain floor is Vite 6 / Vitest 4, with CI on Node 20 (batch
  B10c-mand1, resolving 7 Dependabot alerts); Dependabot's proposed Vite 8/
  Vitest 5 are deliberately deferred since Vitest 5 requires Node >=22.12,
  which would drag a CI runtime bump into a security-fix change. Both
  `web/tsconfig.app.json` and `web/tsconfig.node.json` must keep
  `DOM.Iterable` in `lib`: `BrowsePage` iterates
  `URLSearchParams.entries()`/`.keys()`, which `lib.dom` alone doesn't
  declare -- under Vitest 2 these arrived implicitly via `@types/node`, and
  dropping `DOM.Iterable` silently re-breaks `npm run typecheck`.
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
  links to `/browse/<type>?source=<book_id>&view=tree`, Chapter to
  `/browse/<type>?category=<category>&chapter=<chapter>&view=tree`, Section
  is plain text; a record with no resolved chapter shows just the book
  crumb, and one with no `book_title` shows no breadcrumb at all. Both
  links carry `&view=tree` (batch B10b-mand2) because `/browse/:type`
  defaults to the flat, paginated `list` view for every type except
  `rules_section` -- without it, a spell/feat/table crumb would land on the
  list instead of the tree; it's a harmless no-op on `rules_section`, whose
  default is already `tree`.
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
  type EXCEPT `rules_section` from `/schemas`, linking to `/browse/<type>`
  (batch B10c: `class`/`prestige_class` are now registered types, so they
  get "Classes"/"Prestige classes" nav links this way for free);
  batch B10b adds a dedicated "Rules" link (`/browse/rules_section`) plus
  quick links -- fetched once from `/facets/rules_section`'s `category`
  facet -- for whichever of equipment/skills/races have count > 0
  (`QUICK_LINK_CATEGORIES`; batch B10c drops "classes" from this list --
  class has its own nav link now, and its rules_section fragments are
  non-canonical/superseded anyway),
  as `/browse/rules_section?category=<key>`; either fetch failing just
  leaves that part of the nav empty, same defensive pattern as the existing
  `/schemas` fetch. Batch B10c adds `src/components/ClassRecord.tsx`,
  rendered by `RecordPage` in place of the generic `FieldGroups`/`text_md`/
  `RecordTables` body whenever `record.type` is `class`/`prestige_class`:
  header facts (hit die, alignment, BAB progression, saves, skill points,
  requirements), `text_md` (opening overview), `description_sections`,
  class skills, weapon and armor proficiency, the progression table (the
  existing `RecordTables` component reused against the already-resolved
  `record.tables` -- no new table-rendering code), class features (an
  empty `text_md` renders no body, matching the extraction rule that
  allows one), and a live Spells section, in that order. The Spells
  section pages through `GET /records/spell?class=<spell_list>&page_size=
  200` until every result is collected, groups them by the level parsed
  out of each item's `facets.levels` -- `/records/spell` items carry
  `facets` as `{key: [values]}`, and `levels` holds the array-of-object
  field's COMBINED values (e.g. `"Wizard 3"`), so `levelForClass` looks for
  the entry starting with `"<spell_list> "` and parses the remainder as the
  level -- and links each spell to `/r/spell/<slug>`; renders nothing (not
  an error) when `fields.spellcasting` is absent. `RecordPage` also gains a
  `superseded_by` notice (any record type, not just class): when set, a
  short "Superseded by <link>" line renders above the heading, the link
  target parsed straight out of the id (`<type>:<book_id>:<slug>` ->
  `/r/<type>/<slug>`) rather than a second fetch. Batch B11 adds a
  `variant_of` notice next to it, reusing the same id-to-path parser
  (`supersededByLink`) rather than writing a second one, and two sections
  below `RecordTables`, each rendered only when non-empty: "Other
  printings" lists `record.variants` as plain text (book title/citation --
  never links, since every printing shares the one `/r/:type/:slug` URL),
  and "Overrides applied" lists `record.applied_overrides`, the entry's own
  name linking to its own record page (`supersededByLink` again) alongside
  its citation and `replacement_text`. `src/api.ts` gains `RecordVariant`/
  `AppliedOverride` interfaces mirroring the server's resolved shapes.
  `vite.config.ts`'s dev server proxies `/api/*` to the FastAPI server on
  127.0.0.1:8000 (path rewrite strips `/api`) and binds `127.0.0.1`
  explicitly (Node's default `"localhost"` host can resolve to the IPv6
  loopback only on some systems, which breaks anything that probes
  `127.0.0.1` directly, e.g. Playwright's `webServer.url` check). `web/e2e/`
  has Playwright specs -- `smoke.spec.ts` (flow A: type a prefix, Enter,
  land on the record page; plus, batch B10, opening the fixture
  rules_section record and asserting its owned table renders as a real
  `<table>` below the text; plus, batch B11, opening the fixture's
  duplicate "Ice Storm" printing and asserting both "Other printings" and
  "Overrides applied" render), `mobile.spec.ts` (flow A at 400px width),
  (batch B9) `browse.spec.ts` (flow B: nav to Spells, check Cleric/3/
  Conjuration in the facet sidebar, sort by name, open the one matching
  spell), (batch B10b) `tree.spec.ts` (flow C: follow the header nav's
  "Rules" link, expand Combat and its chapter, open "Grapple Ranks", see
  the breadcrumb; plus a header quick-link into a pre-filtered, auto-
  expanded category; plus, since B10c-mand3, expanding the Classes
  category and its "Chapter 4: Classes" chapter to open the fixture class
  record), and (batch B10c) `class.spec.ts` (flow D: nav
  Classes, open the fixture class, see its progression table and both
  class features render, click a Spells-section spell link through to the
  spell page) -- run by `playwright.config.ts`'s `webServer` against
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
