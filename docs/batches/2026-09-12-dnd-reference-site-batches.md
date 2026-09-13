# D&D 3.5e reference site — batch plan

Spec: `docs/specs/2026-09-12-dnd-reference-site-spec.md` (APPROVED 2026-09-12)
Repo: `rswanson/owlsperch`, base branch `main`.
Batches are strictly serial. Batch N assumes batches 1..N−1 are merged.
"Data dir" means `$OWLSPERCH_DATA` (default `~/owlsperch-data`); "PDF dir"
means `$OWLSPERCH_PDFS` (default `~/D_D`). CLI commands run as
`uv run owlsperch ...` from the repo root.

B10b was inserted on 2026-09-13 from direct user feedback, out of the
original numbering, and is built before B11.

Conventions for every batch: ruff, mypy, and pytest pass in CI; from B7 on,
eslint, tsc, vitest, and Playwright also pass. Tests use synthetic fixtures
only. Tests that need the real PDF dir are marked `@pytest.mark.corpus` and
skipped when the directory is absent, so CI never depends on the PDFs.

---

## B1: Repo scaffold, curated manifest, `manifest check`, CI
- **Status:** merged
- **Branch base:** `spec/dnd-reference-site` (carries the spec and this plan to main).
- **User-visible outcome:** `uv run owlsperch manifest check` reports every
  file in the PDF dir as in-scope, override, index, or excluded, and exits
  non-zero naming any file without a manifest entry. CI runs on the PR.
- **Acceptance criteria:**
  1. `pipeline/pyproject.toml` (uv, Python ≥3.12) defines the `owlsperch`
     console script; `uv run owlsperch --help` lists `manifest`.
  2. `pipeline/manifest.yaml` has exactly one entry for each of the 238 files
     in the PDF dir, with all fields from spec 4.3. Every `errata`, `update`,
     and `web_enhancement` entry has `applies_to` pointing at an existing
     `book_id`. Every `excluded` entry has `exclude_reason`. Every scan that
     duplicates a text-layer copy has `preferred_over` on the preferred copy.
  3. `manifest check` prints counts by kind and by in-scope status, lists
     files present in the PDF dir but absent from the manifest and files in
     the manifest absent from the dir, and exits 1 if either list is
     non-empty.
  4. Manifest schema is validated on load; a malformed entry fails with the
     entry's `book_id` and field named.
  5. `.github/workflows/ci.yml` runs ruff, mypy, and pytest on push and PR;
     unit tests cover in-scope derivation (3.5 in, 3.0 out, 3.0 with update
     in, excluded out, index out) and both mismatch directions with a
     temporary directory.
  6. `README.md` documents the two environment variables and the command.
  7. `CLAUDE.md` is updated with the real commands.
- **How to observe:** `uv run owlsperch manifest check` prints the summary
  table and exits 0 against `~/D_D`.
- **Touches:** `pipeline/`, `pipeline/manifest.yaml`, `.github/workflows/ci.yml`,
  `README.md`, `CLAUDE.md`, `.gitignore`.

## B2: `text` for text-layer books
- **Status:** merged
- **User-visible outcome:** `owlsperch text phb` writes one repaired text
  file per page under `text/phb/`, in reading order, without running
  headers, with a sidecar of printed page numbers.
- **Acceptance criteria:**
  1. `text <book_id|all>` refuses scanned books with a message naming B15's
     command path, and skips books that are not in scope.
  2. Uses `pdftotext -bbox-layout`; blocks are assigned to columns by
     x-position clustering, columns emitted left to right, blocks within a
     column top to bottom.
  3. Running headers and footers (lines repeating at the same y-band on ≥30%
     of pages) are removed from text and the printed page number, when found
     in the footer band, is written to `text/<book_id>/pages.json` mapping
     pdf index → printed number.
  4. Hyphenated line breaks are rejoined when the joined word appears
     elsewhere in the book or matches a word list; otherwise the hyphen is
     kept.
  5. Output is idempotent and skips pages whose file exists unless `--force`.
  6. Unit tests on synthetic bbox XML: two-column ordering, header removal,
     dehyphenation, page-number detection. A `corpus` test asserts PHB page
     120 text contains "Longbow, Composite" before "Longspear:" and does not
     contain "CHAPTER 7:".
- **How to observe:** `cat ~/owlsperch-data/text/phb/p0120.txt` reads as
  continuous prose in column order.
- **Touches:** `pipeline/owlsperch/text/`, tests, README.

## B3: `segment`
- **Status:** merged
- **User-visible outcome:** `owlsperch segment phb` writes segment JSON files
  under `segments/phb/` and prints counts per kind hint.
- **Acceptance criteria:**
  1. Segment file fields: `seg_id`, `book_id`, `pages` (pdf indices),
     `kind_hint`, `text`, `status` (`pending`), `tier` (`haiku`),
     `attempts` (empty list), `created_at`.
  2. Anchors implemented: spell (name line followed by a school line),
     stat block ("Size/Type" line or "Hit Dice:"), feat (name followed by
     "Prerequisite" or "Benefit"), table ("Table X–Y:" caption). Text between
     anchors becomes `rules_section` segments split at font-size headings
     from the bbox output.
  3. A segment whose anchor's block continues onto the next page includes
     that page's text up to the next anchor.
  4. Segments are never empty; whitespace-only spans are dropped.
  5. Rerunning is idempotent: existing segments are kept unless `--force`.
  6. Unit tests on synthetic pages cover each anchor, page-spanning, and
     heading splits. A `corpus` test asserts PHB produces ≥ 500 spell
     segments and that a segment named "Fireball" exists with kind `spell`.
- **How to observe:** `owlsperch segment phb` prints a table like
  `spell 606 / feat 110 / table 120 / rules_section 900`.
- **Touches:** `pipeline/owlsperch/segment/`, tests.

## B4: Envelope, type registry, spell schema, `validate`
- **Status:** merged
- **User-visible outcome:** `owlsperch validate phb` checks every record
  under `records/phb/` and prints pass/fail per record with reasons.
- **Acceptance criteria:**
  1. `schemas/envelope.json` defines the envelope fields from spec 4.6
     including `schema_version`; `schemas/registry.json` lists types with
     their schema file and UI hints; `schemas/spell.json` defines spell fields
     from spec 4.6, each with `x-ui` hints (label, filterable, sortable,
     group, order).
  2. `validate` runs JSON Schema conformance then consistency checks. For
     spell: ≥1 entry in `levels`, non-empty `school`, `pages` within the
     segment's page span, `schema_version` equals the current version.
  3. Output lists each record path with PASS or FAIL and error messages;
     exits 1 if any FAIL. `--json` emits machine-readable results for the
     extract skill.
  4. Validation writes back to the originating segment: PASS sets segment
     `status: done`; FAIL appends an attempt with errors (the skill decides
     escalation in B5/B8).
  5. Unit tests: valid spell passes; missing `levels`, wrong `school` type,
     page outside span, stale `schema_version` each fail with the expected
     message.
- **How to observe:** copy a hand-written spell JSON into
  `records/phb/spell/`, run `owlsperch validate phb`, see PASS.
- **Touches:** `schemas/`, `pipeline/owlsperch/validate/`, tests.

## B5: `/extract` skill, haiku tier only
- **Status:** merged
- **User-visible outcome:** `/extract phb --limit 20` in Claude Code fans out
  haiku subagents over pending spell segments, records land under
  `records/phb/spell/`, and a summary reports pass/fail counts.
- **Acceptance criteria:**
  1. `.claude/skills/extract/SKILL.md` documents the command and arguments
     from spec 4.5; a helper `owlsperch queue next --tier haiku --limit N`
     returns the next pending segment paths and marks them in-progress with a
     timestamp.
  2. Each subagent prompt includes segment text, kind hint, book metadata,
     the candidate schemas, extraction rules, and the exact output path
     convention `records/<book_id>/<type>/<slug>.json`.
  3. The subagent may return record paths or `no_content` with a reason;
     `no_content` marks the segment done with `outcome: no_content`.
  4. After each wave the skill runs `owlsperch validate --json` on the new
     records; PASS marks done; FAIL leaves the segment pending on haiku with
     errors attached (escalation arrives in B8).
  5. Default parallelism 8; `--parallel` and `--limit` honored.
  6. Summary prints segments attempted, passed, failed, no-content, and
     records written.
  7. `owlsperch queue` has unit tests for next/in-progress marking; the skill
     doc includes a manual verification checklist.
- **How to observe:** run `/extract phb --limit 20`, then
  `ls ~/owlsperch-data/records/phb/spell/` shows validated spell files.
- **Touches:** `.claude/skills/extract/`, `pipeline/owlsperch/queue/`, tests.

## B6: `build-db` and API search + detail
- **Status:** merged
- **User-visible outcome:** `owlsperch build-db` produces `db/owlsperch.sqlite`
  from records; `uv run owlsperch serve` starts FastAPI; curl returns
  typeahead results and a full record.
- **Acceptance criteria:**
  1. `build-db` deletes and recreates the SQLite file, creating `books`,
     `records`, `record_fields`, `record_pages`, `names_fts` (name + aliases
     only) per spec 4.8. Marks every record `canonical: true` (precedence
     arrives in B11).
  2. `GET /search?q=fire&types=spell` returns ≤50 hits grouped by type with
     id, type, name, slug, citation; prefix and substring matches on names
     and aliases; canonical only.
  3. `GET /records/spell/fireball` returns the envelope, fields, and citation;
     404 with a JSON body for unknown slugs.
  4. `GET /schemas` returns the registry and per-field UI hints.
  5. Server reads `$OWLSPERCH_DATA`; a missing DB returns 503 with the build
     command in the message.
  6. pytest builds a fixture DB from synthetic records and covers search
     (prefix, alias, type filter, limit), record detail, 404, 503, schemas.
- **How to observe:** `curl localhost:8000/search?q=fireb` returns Fireball.
- **Touches:** `pipeline/owlsperch/build_db/`, `server/`, tests.

## B7: Web UI: search box and record page
- **Status:** merged
- **User-visible outcome:** open `localhost:5173`, type in the search box,
  pick a hit, read the spell on its record page.
- **Acceptance criteria:**
  1. `web/` is a Vite + React + TypeScript app; `npm run dev` proxies `/api`
     to the FastAPI server; one root command (`make dev` or `uv run owlsperch
     dev`) starts both.
  2. `/` shows a search box; typeahead queries `/search` after 2 characters
     with 150 ms debounce, groups results by type with a type badge, and
     supports arrow keys and Enter.
  3. `/r/:type/:slug` renders name, citation, `text_md` as Markdown, and
     field groups in the order given by schema UI hints from `/schemas`.
  4. Empty DB shows an empty state naming `owlsperch build-db`.
  5. CI adds eslint, tsc, vitest, and Playwright against the fixture DB; the
     Playwright test performs flow A up to opening a record.
  6. Layout works at 400 px width.
- **How to observe:** browser: type "fireb", press Enter, see the Fireball
  page.
- **Touches:** `web/`, `.github/workflows/ci.yml`, root dev script.

## B8: Escalation ladder
- **Status:** merged
- **User-visible outcome:** `/extract` retries haiku failures on sonnet,
  then opus, then moves them to `human/`; the summary shows per-tier counts.
- **Acceptance criteria:**
  1. After validation FAIL, segment `tier` advances haiku → sonnet → opus with
     the errors stored on the attempt; after opus FAIL the segment file moves
     to `human/<book_id>/` with all attempts.
  2. Retries include prior validation errors in the subagent prompt.
  3. `needs_context` (adjacent seg ids) triggers one same-tier retry with the
     merged text; a second `needs_context` escalates.
  4. `proposed_type` moves the segment to `human/` with the proposal.
  5. Segments in-progress for > 60 minutes are reset to pending on the next
     `queue next`.
  6. `--tier` default is the lowest tier with pending work; `--tier opus`
     processes only opus-tier segments.
  7. `owlsperch queue` has a `--dry-run` mode with a fake subagent (canned
     outputs from a fixture directory) so the state machine is unit tested:
     pass, fail-escalate ×2, human, needs-context merge then pass, second
     needs-context escalates, stale reset.
  8. Summary prints counts per tier and per outcome.
- **How to observe:** run `/extract phb` after B5; summary shows
  `haiku 480 pass / 40 → sonnet; sonnet 35 pass / 5 → opus; ...`.
- **Touches:** `.claude/skills/extract/`, `pipeline/owlsperch/queue/`, tests.

## B9: Browse pages and facets from schema hints
- **Status:** merged
- **User-visible outcome:** `/browse/spell` lists spells with a facet
  sidebar (class, level, school, source) and sort controls.
- **Acceptance criteria:**
  1. `GET /records/{type}` supports `?<field>=value` for every field marked
     filterable, repeated params as OR within a field and AND across fields,
     `sort=<field>|-<field>` for sortable fields, `page` and `page_size`
     (default 50), and returns total count.
  2. Array fields like `levels` filter on nested values (`class=Cleric&level=3`
     matches a spell with that pair).
  3. `GET /facets/{type}` returns filterable fields with distinct values and
     counts, honoring current filters.
  4. Browse page renders the sidebar from `/schemas` hints, updates the URL
     query string, and paginates.
  5. Header nav lists every registered type.
  6. pytest covers filters, OR/AND semantics, nested pairs, sort, pagination;
     Playwright performs flow B up to opening a spell.
- **How to observe:** browser: Spells → Cleric, 3, Conjuration → list
  narrows.
- **Touches:** `server/`, `web/`, tests.

## B10: Feat, rules section, and table types
- **Status:** merged
- **User-visible outcome:** feats and rules sections are searchable and
  browsable; tables render as HTML tables on the records that own them.
- **Acceptance criteria:**
  1. `schemas/feat.json`, `rules_section.json`, `table.json` with fields from
     spec 4.6 and UI hints; registry updated.
  2. Validators: feat has non-empty `benefit`; table rows all have the column
     count; rules_section has `topic`.
  3. Extraction rules for these types added to the skill prompt; a record's
     `tables` array holds table record ids written by the same subagent.
  4. `build-db` creates the `tables` table; record detail returns tables; UI
     renders them below the text.
  5. Unit tests per validator; a `corpus` test asserts `/extract phb --limit`
     over feat segments yields a "Power Attack" record (manual gate, not CI).
- **How to observe:** browser: search "Power Attack", open it; open
  "Fighter" rules section and see its level table rendered.
- **Touches:** `schemas/`, `pipeline/owlsperch/validate/`,
  `.claude/skills/extract/`, `server/`, `web/`.

## B10-imp1: render the table schema into every non-table prompt
- **Status:** merged
- **Follow-up to:** B10 (criterion 3 -- the "tables belonging to this
  entity" cross-link convention).
- **User-visible outcome:** a feat/rules_section/spell subagent that is told
  to write a second, `table`-typed record is now shown that type's own
  `fields` schema, `schema_version`, extraction rules, and example record,
  instead of having to invent the shape from an output directory and a few
  cross-link bullets.
- **Acceptance criteria:**
  1. A non-`table` prompt renders the `table` type's `fields` schema live
     from `schemas/` (never hand-copied), so `columns`/`rows` are named.
  2. `_KIND_RULES["table"]` and `schemas/examples/table.json` are rendered
     into the same prompt, in the existing EXAMPLE RECORD style.
  3. A `table` segment's own prompt is unchanged (the convention block is
     still omitted entirely for `kind_hint == "table"`).
  4. The cross-linked table record's own `schema_version` is stated
     explicitly as the registry's `table` version, distinct from the owning
     record's.
  5. A missing `table` type in a custom `$OWLSPERCH_SCHEMAS` still renders a
     prompt instead of crashing; a generic test asserts every kind told to
     write a `table` record is also shown the table schema.
- **How to observe:** `uv run owlsperch queue prompt <rules_section seg_id>`
  and read the `### Tables belonging to this entity` block.
- **Touches:** `pipeline/owlsperch/queue/prompt.py`, tests, `CLAUDE.md`.

## B10-imp2: expose pending counts by kind and tier in `queue summary`
- **Status:** merged
- **Follow-up to:** B10 (the extraction wave loop -- `queue summary` was the
  only read-only view of progress, and it had no pending-only breakdown).
- **User-visible outcome:** a wave planner can size the next wave from
  `uv run owlsperch queue summary <book_id>` alone, instead of abusing
  `queue next --limit 100000 --kind <k> --json` as a counter -- which
  selects and flips every matching segment to `in_progress`, each needing a
  full `queue reset` to undo.
- **Acceptance criteria:**
  1. `QueueSummary` gains `pending_by_kind` and `pending_by_tier`, counted
     over segments whose `status` is `pending` only (not the done+pending
     total `counts_by_kind`/`counts_by_tier` report).
  2. Both render in `queue summary`'s text output (`pending by kind_hint`,
     `pending by tier`) and in `--json`.
  3. They are computed from the segments `compute_summary` already loads --
     no extra scan, no new I/O, no mutation of any segment file.
  4. A unit test covers a mixed pending/done/`human/` fixture and asserts
     the pending-only split plus both render lines.
  5. `.claude/skills/extract/SKILL.md` points wave planning at the new
     fields and says explicitly not to use `queue next` for counting.
- **How to observe:** `uv run owlsperch queue summary phb1` and read the
  `pending by kind_hint` / `pending by tier` lines.
- **Touches:** `pipeline/owlsperch/queue/summary.py`, tests,
  `.claude/skills/extract/SKILL.md`, `CLAUDE.md`.

## B10-mand1: qualify generic `rules_section` names in the prompt
- **Status:** merged
- **Follow-up to:** B10 (the B10 extraction-wave retrospective -- proposals
  4 and 9).
- **User-visible outcome:** a `rules_section` subagent extracting a generic,
  cross-chapter heading (e.g. "Class Features", which recurs verbatim in
  every class chapter) is now told to qualify the record's `name` with the
  enclosing entity -- `Class Features (Barbarian)` -- instead of writing a
  bare generic name whose `slug`/`id` collide across chapters and silently
  overwrite another chapter's record.
- **Acceptance criteria:**
  1. The `rules_section` extraction rules name the recurring generic
     headings ("Class Features", "Class Skills", "Game Rule Information",
     "Description") and give the literal worked example
     `Class Features (Barbarian)`.
  2. The rule states where the enclosing entity comes from, in order (the
     nearest preceding entity name in the segment's own text, the segment's
     own heading, an `### Adjacent context` block), and falls back to
     `needs_context` naming the previous segment id rather than guessing.
  3. The rule states that `slug`/`id` follow from the QUALIFIED `name`
     (`class-features-barbarian`,
     `rules_section:<book_id>:class-features-barbarian`), and that
     qualifying the slug alone while leaving `name` generic is wrong and
     fails validation.
  4. `fields.topic` still carries the bare heading and
     `fields.parent_section` the enclosing entity -- only `name` gains the
     qualifier; neither field's meaning changes.
  5. `schemas/examples/rules_section.json` -- rendered verbatim into every
     `rules_section` prompt -- itself models the convention
     (`Class Features (Sable Knight)` / `class-features-sable-knight` /
     `topic: Class Features` / `parent_section: Sable Knight`).
  6. The rule stays kind-specific: `spell`, `feat`, and `table` prompts do
     not carry it.
  7. A per-registered-kind prompt-rendering coverage guard renders a prompt
     for every type in `schemas/registry.json` (never a hard-coded type
     list) and asserts every field/type instruction in it is backed by a
     schema/example actually rendered in that same prompt -- zero gaps for
     all registered kinds.
  8. A negative test proves the guard bites: against a doctored schemas dir
     missing `examples/table.json`, it reproduces the shape of the B10
     table-cross-link defect (an instruction to write a `table` record with
     no backing table material rendered) and the guard reports it.
  9. Every `schemas/examples/*.json` fixture has `slug == slugify(name)` and
     `id == "<type>:<book_id>:<slug>"`, so a fixture modeling a qualified
     name is caught if its slug/id ever drift.
- **How to observe:** `uv run owlsperch queue prompt <rules_section seg_id>`
  and read the generic-heading paragraph under the
  "Extraction rules for `rules_section`" heading.
- **Touches:** `pipeline/owlsperch/queue/prompt.py`,
  `schemas/examples/rules_section.json`, tests, `CLAUDE.md`.

## B10-mand2: `queue complete` refuses to overwrite another segment's record
- **Status:** merged
- **Follow-up to:** B10 (the B10 extraction-wave retrospective -- the
  companion fix to B10-mand1: mand1 stops generic names from colliding in
  the first place, mand2 stops a collision that still happens from
  silently destroying the earlier segment's work, and recovers the
  segments this already happened to).
- **User-visible outcome:** a subagent reply claiming a record path another
  segment already owns is now rejected and escalated instead of silently
  overwriting that segment's extracted content -- and
  `uv run owlsperch queue audit <book_id> [--fix]` finds and re-queues the
  segments whose output was already lost this way before the guard existed.
- **Acceptance criteria:**
  1. `queue complete` adds a third check to the existing resolve-inside-
     `records/<book_id>/` and exists-on-disk checks: the path must not
     already be owned by a *different* segment of the same book. A
     colliding path escalates the segment via `ladder.record_failure` with
     error `record_path_collision: <path> is owned by segment <seg_id>`,
     exactly as a missing path does -- both kinds of error fold into the
     one attempt for a single reply.
  2. A colliding path is left completely untouched on disk: neither its
     content nor its `extraction` block is rewritten, so the earlier
     claimant's content survives.
  3. Ownership comes from the SEGMENT index (`_record_path_owners` scans
     every `segments/<book_id>/*.json` and `human/<book_id>/*.json` file's
     own `records`/`pending_records`), never from the record file's own
     `extraction.segment_id` -- that value is a subagent-copied placeholder
     and on a just-clobbered file already names the thief. Paths are keyed
     by their RESOLVED spelling, so `records/x.json`, an absolute spelling
     and a `..`-containing spelling all compare equal.
  4. A segment re-claiming a path it already owns itself is still allowed:
     an idempotent retry of the same claim is not a collision. The
     ownership index is built only when there is at least one candidate
     path, so a `no_content`/`needs_context`/`proposed_type` reply never
     pays for the scan.
  5. `queue audit <book_id>` is read-only and reports `collisions` (paths
     claimed by 2+ segments, from the segment index) and `stale_claims`
     (segments claiming a path they no longer own, decided from the record
     FILE's `extraction.segment_id`, reason `owned_by_other` or `missing`),
     in both plain text and `--json`.
  6. `queue audit --fix` prunes exactly the stale paths from each affected
     segment's own `records`/`pending_records` and soft-resets a `done`
     victim back to `pending` (clearing `outcome`/`outcome_reason`/
     `in_progress_since`, keeping `tier`/`attempts`/`notes`/
     `context_seg_ids`) so it re-extracts under the new guard and under
     B10-mand1's name-qualification rule. A `pending`/`in_progress` segment
     just gets the pruning; a segment in `human/` is reported but left
     untouched. `--fix` never deletes, moves, or rewrites a record file,
     and is idempotent (a second run finds nothing left to do).
  7. Tests cover: the collision guard (including a colliding path's file
     being byte-identical afterwards), an opus-tier collision moving the
     segment to `human/`, a differently-spelled claim on an owned path, a
     self-reclaim being allowed, the audit report's two sources of truth,
     the plain-text render naming collision owners and stale segments,
     `--fix` idempotency, and `attempts`/`tier` surviving a soft reset.
- **How to observe:** `uv run owlsperch queue audit phb1`, then
  `uv run owlsperch queue audit phb1 --fix`, then
  `uv run owlsperch queue summary phb1` to see the re-queued segments.
- **Touches:** `pipeline/owlsperch/queue/complete.py`,
  `pipeline/owlsperch/queue/audit.py`,
  `pipeline/owlsperch/queue/runner.py`, `pipeline/owlsperch/cli.py`,
  tests, `CLAUDE.md`.

## B10-mand3: `queue next --dry-run` and a `build-db` duplicate-id guard
- **Status:** merged
- **Follow-up to:** B10 (the B10 extraction-wave retrospective -- proposals
  3 and 5: the two remaining `apply_now` chores).
- **User-visible outcome:** the queue can be inspected without being
  disturbed (`uv run owlsperch queue next <book_id> --limit N --dry-run`
  previews exactly what a real call would pick, marking nothing
  `in_progress` -- during B10 a wave agent twice used `queue next
  --limit 100000` as a look-only inspector and flipped 729, then 547, real
  pending segments to `in_progress`), and `owlsperch build-db` no longer
  dies on the corpus's genuine `id` collisions: a duplicate record `id`
  is skipped and counted like any other invalid record instead of
  aborting the whole build with an unhandled `sqlite3.IntegrityError`.
- **Acceptance criteria:**
  1. `queue next` takes `--dry-run` (default off), threaded through
     `run_queue_next` to `select_and_mark(..., dry_run=True)`.
  2. A dry run has no write side effects: no segment is marked
     `in_progress`, no segment file is rewritten by the stale-reset/
     lazy-escalation heal pass, nothing is moved to `human/`, and no
     prompt file is rendered. The per-book `.queue.lock` file (taken for a
     consistent snapshot) is the only filesystem effect.
  3. The preview is faithful: the heal pass still runs in memory, so a
     stale `in_progress` segment a real call would reset back to `pending`
     appears in the preview, a lazily escalated segment previews at its
     escalated tier, and a segment the pass would exhaust into `human/`
     is omitted -- the same selection, tier and model a real call would
     resolve.
  4. `--dry-run --json` prints byte-for-byte what the same real call would
     print (same seg_ids, prompt_paths, tiers, models); the dry-run
     notices go to stderr only, so `--json` consumers are unaffected.
     `prompt_path` names where the prompt *would* be rendered
     (`prompt_path_for`), which need not exist on disk.
  5. `build-db` catches `sqlite3.IntegrityError` around `_insert_record`
     for a record that passes `validate_record` on its own but collides on
     `id` with one already loaded, counting it in `skipped_invalid` with a
     `duplicate record id ...` message -- so it flows through the existing
     stderr WARNING, `--strict` exit-1, and non-strict exit-0 behavior
     like any other skip. The `records` INSERT (id TEXT PRIMARY KEY) runs
     first, so nothing partial is left in the child tables.
  6. Tests cover: the `--dry-run` flag parsing/default, a dry run's JSON
     matching the real selection while writing nothing, a dry-run preview
     including a stale `in_progress` segment, and the duplicate-id skip at
     both the `build_db` and `run_build_db` (`--strict` and default)
     levels.
  7. `.claude/skills/extract/SKILL.md` points wave planning at
     `queue summary` first and at `queue next --dry-run` when `queue
     next`'s own view is genuinely needed.
- **How to observe:** `uv run owlsperch queue next phb1 --limit 5 --dry-run
  --json`, then `uv run owlsperch queue summary phb1` -- `in_progress` is
  still 0.
- **Touches:** `pipeline/owlsperch/queue/select.py`,
  `pipeline/owlsperch/queue/runner.py`, `pipeline/owlsperch/cli.py`,
  `pipeline/owlsperch/build_db/runner.py`, tests,
  `.claude/skills/extract/SKILL.md`, `CLAUDE.md`.

## B10b: Rules taxonomy from tables of contents
- **Status:** in-progress
- **Why (user feedback, 2026-09-13):** "The rules section of this site is
  terrible. The contents has a mix of actual rules and random assortments
  from things that should be their own sections (such as classes, equipment,
  etc). You should be able to use each book's tables of contents for some
  rough direction on categories and then use judgement for emergent groupings
  that may be useful to a player."
- **User-visible outcome:** `/browse/rules_section` is organized the way a
  player thinks: a tree of player-facing categories (Character creation,
  Races, Classes, Skills, Feats, Equipment, Combat, Adventuring, Magic,
  Running the game, ...) -> the book's own chapters -> the book's own
  sections -> records. Class write-ups sit under Classes, gear under
  Equipment, and Combat contains only combat rules. The header nav offers
  quick links into the categories that today hold content that will later
  become its own record type (Classes, Equipment, Skills, Races). Every
  record page shows a breadcrumb: Book > Chapter > Section.
- **Acceptance criteria:**
  1. `owlsperch toc <book_id|all>` parses a text-layer book's table of
     contents (scan the first ~12 pages of `text/<book_id>/` for the
     "Contents" page(s); entries are `Title ....... <printed page>` runs,
     often two or more per line after column repair, sometimes with a
     "Chapter N:" prefix; nesting comes from the chapter prefix and, where
     available, `.meta.json` font sizes / indentation, else every
     non-chapter entry is a level-2 section of the preceding chapter) into
     `$OWLSPERCH_DATA/toc/<book_id>.json`: an ordered list of entries
     `{title, level, printed_page, pdf_page_start, pdf_page_end, path}`
     where pdf pages come from `text/<book_id>/pages.json` (printed ->
     pdf), `pdf_page_end` is derived from the next entry at the same or
     shallower level, and `path` is the chapter/section title chain. Books
     whose contents page can't be found or yields < 5 entries are reported
     (not silently empty). Idempotent; `--force` re-parses.
  2. A committed, book-agnostic category list `schemas/categories.json`
     (`key`, `label`, `order`, `description`, and which record types it is a
     natural home for) covering at least: character-creation, races,
     classes, skills, feats, equipment, combat, adventuring, magic,
     running-the-game, monsters, uncategorized. A committed rule table
     (Python module or YAML under `pipeline/owlsperch/toc/`) maps TOC
     entry titles to categories: generic title patterns first
     (e.g. "Combat" -> combat, "Equipment"/"Goods and Services" ->
     equipment, "Abilities"/"Description"/"Alignment" -> character-creation,
     "Magic"/"Spells" -> magic, "Adventuring"/"Movement"/"Exploration" ->
     adventuring), then optional per-book overrides keyed by `book_id` +
     entry title. Use judgment for emergent, player-useful groupings (this
     is the "judgement" half of the feedback): e.g. a chapter that mixes
     gear and services can still be one category; sections that a player
     looks up in play (conditions, actions in combat, saving throws, resting,
     carrying capacity) must land where a player would look for them, even if
     the book's own chapter placement is odd. Every phb1 chapter and every
     phb1 TOC section resolves to a category (fallback: the chapter's
     category; last resort: uncategorized, and `owlsperch toc` prints how many
     fell through). Only chapter/section TITLES and page numbers may be
     committed as test fixtures -- never body text (public repo).
  3. `build-db` derives, for EVERY record of every type, `category`,
     `chapter`, and `section` from the record's first page and the book's
     `toc/<book_id>.json` (deepest TOC entry containing that page), without
     mutating record files and without a schema_version bump: these are
     built-in derived facets like `source`, exposed by `/records/{type}`
     items, `/facets/{type}`, and the record detail (`toc: {category,
     chapter, section, path}`), filterable via `category=`/`chapter=` query
     params. A book with no toc file yields `uncategorized`/null and
     build-db prints a WARNING naming `owlsperch toc <book_id>`. The
     extractor-written `rules_section.fields.chapter` stops being a facet
     (x-ui filterable false) so the derived one is the single source of truth.
  4. Web: `/browse/rules_section` renders the category -> chapter ->
     section -> records tree (collapsible groups, counts per group, the
     existing facet sidebar keeps `source` and gains `category`; a chosen
     category or chapter filter narrows the tree). The same tree component
     works for any type when `?view=tree` is set, and the flat list stays the
     default for the other types. The header nav gets "Rules" plus quick
     links to `/browse/rules_section?category=classes|equipment|skills|races`
     (rendered only for categories that have records). Record pages show a
     `Book > Chapter > Section` breadcrumb linking back into the tree.
  5. Tests: unit tests for the TOC parser on synthetic contents text (dotted
     leaders, two entries per line, "Chapter N:" prefixes, a missing printed
     page), for page -> entry lookup (boundaries, nested levels, page before
     the first entry), and for the category rule table (patterns, per-book
     override, fallback); server tests for the derived facets/filters and the
     detail `toc` block; vitest for the tree grouping function; Playwright
     flow C on the fixture DB (fixture-db gains a small synthetic toc):
     open Rules, expand Combat, open a section, see the breadcrumb. A
     `corpus` test: phb1's toc has a Combat chapter and the record
     `rules_section/attacks-of-opportunity` (if present) resolves to
     category combat.
  6. CLAUDE.md "Current state" documents `toc`, the categories file, the
     derived facets, and the tree view.
- **How to observe:** `uv run owlsperch toc phb1 && uv run owlsperch
  build-db`, then http://localhost:5173/browse/rules_section shows PHB
  rules grouped by category; Classes contains the class sections and nothing
  else; Combat contains only combat rules; a class section page shows
  "Player's Handbook > Chapter 3: Classes > Barbarian".
- **Touches:** `pipeline/owlsperch/toc/` (new), `schemas/categories.json`,
  `pipeline/owlsperch/build_db/`, `pipeline/owlsperch/fixture_db.py`,
  `server/`, `web/`, CLAUDE.md, README.

## B11: Precedence: errata and update entries, Rules Compendium, latest-wins
- **Status:** pending
- **User-visible outcome:** duplicate records collapse to one canonical
  entry; detail pages show applied overrides and "other printings".
- **Acceptance criteria:**
  1. `schemas/errata_entry.json` and `update_entry.json`; extraction rules
     for errata and update PDFs produce entries with `target_book`,
     `target_page`, `target_name`, `replacement_text`.
  2. `build-db` groups by type + normalized name; applies errata and update
     entries to matched targets (name match within `target_book`, else page
     match), appending ids to `applied_overrides`; unmatched entries are
     written to `human/<book_id>/` and listed in `reports/precedence.md`.
  3. Rules Compendium `rules_section` records with a matching normalized
     topic become canonical over any other book; unmatched RC sections are
     listed in `reports/precedence.md`.
  4. Among remaining duplicates, highest manifest `published` is canonical;
     others get `variant_of`.
  5. Search and browse return canonical only; detail returns `variants` and
     `applied_overrides`; UI shows an "Other printings" list and an
     "Overrides applied" list with citations.
  6. pytest: errata override, update override, RC override, latest-wins,
     unmatched to human, canonical-only search.
- **How to observe:** after extracting PHB and Spell Compendium, open
  "Fireball": Spell Compendium is canonical, PHB listed under other printings.
- **Touches:** `schemas/`, `pipeline/owlsperch/build_db/`, `server/`, `web/`.

## B12: Monster, NPC, and template types
- **Status:** pending
- **User-visible outcome:** Monster Manual stat blocks are browsable by CR,
  type, subtype, size, environment.
- **Acceptance criteria:**
  1. `schemas/monster.json`, `npc.json`, `template.json` with fields from spec
     4.6 and UI hints; numeric `cr`, `hd`, `hp` stored as `num_value` for
     range filters.
  2. Validators: HD count in `hd` matches the dice count in `hp`; `cr`
     numeric (fractions like 1/2 normalized to 0.5); `type` from the 3.5
     creature type list.
  3. Range filters `cr_min`/`cr_max` on the list endpoint for numeric fields
     marked rangeable.
  4. Extraction rules cover stat block layout differences between MM I and
     MM III+ formats.
  5. Unit tests for validators and range filters.
- **How to observe:** browser: Monsters → CR 5–7, Outsider → list; open one,
  full stat block fields grouped per hints.
- **Touches:** `schemas/`, validators, skill prompt, `server/`, `web/`.

## B13: Class, prestige class, race, skill, equipment, magic item types
- **Status:** pending
- **User-visible outcome:** those six types are searchable and browsable;
  class pages show their level tables; magic items filter by slot and price.
- **Acceptance criteria:**
  1. Six schema files with fields from spec 4.6 and UI hints; `price`,
     `cost`, `weight`, `caster_level` stored numeric and rangeable.
  2. Validators: class has `hit_die` and a `level_table` id that exists;
     race has six `ability_adjustments` keys (zero allowed); magic_item has
     numeric `price`; equipment weapons have `damage`.
  3. Extraction rules per type; class extraction writes the level table as a
     table record and references it.
  4. Unit tests per validator.
- **How to observe:** browser: Magic Items → slot Hands, sort by price;
  Classes → Wizard shows its level table.
- **Touches:** `schemas/`, validators, skill prompt.

## B14: Psionic power and lore types
- **Status:** pending
- **User-visible outcome:** psionic powers, deities, organizations,
  locations, planes, and timeline entries are searchable and browsable.
- **Acceptance criteria:**
  1. Six schema files with fields from spec 4.6 and UI hints.
  2. Validators: power has `discipline` and ≥1 level and numeric
     `power_points`; deity has `alignment` and ≥1 domain; timeline_entry has
     `year`.
  3. Extraction rules per type, including the Expanded Psionics Handbook
     power block layout.
  4. Unit tests per validator.
- **How to observe:** browser: Powers → Psion 3; Deities → domain Fire.
- **Touches:** `schemas/`, validators, skill prompt.

## B15: OCR for scanned books plus page-image escalation
- **Status:** pending
- **User-visible outcome:** `owlsperch text complete-psionic` produces text
  from a scanned book; extraction on sonnet and opus tiers receives the page
  image for scanned books.
- **Acceptance criteria:**
  1. Scanned books (manifest `scanned: true`) render pages at 300 dpi with
     `pdftoppm` to `pages/<book_id>/pNNNN.png`, run `tesseract --psm 1`, then
     the B2 column repair.
  2. Page renders and OCR are idempotent and resumable per page.
  3. `queue next` includes `page_images` for segments from scanned books;
     the skill passes the paths to subagents on sonnet and opus tiers only.
  4. A `corpus` test asserts Complete Psionic page 20 text has ≥ 500
     characters and contains at least one of "power", "psion", "manifest".
  5. Unit tests: image-path attachment only on sonnet/opus; per-page resume.
- **How to observe:** `owlsperch text complete-psionic` then
  `cat text/complete-psionic/p0020.txt` shows readable text.
- **Touches:** `pipeline/owlsperch/text/`, `pipeline/owlsperch/queue/`,
  `.claude/skills/extract/`.

## B16: Cross-links and referenced-by
- **Status:** pending
- **User-visible outcome:** names of other records inside record text are
  links; detail pages list "Referenced by".
- **Acceptance criteria:**
  1. `build-db` creates `record_links` by longest-match, word-boundary scan of
     `text_md` against canonical names that are unique across canonical
     records (case-insensitive), plus typed mentions of the form
     "the <type label> <Name>" which link even when the name is ambiguous.
  2. Self-links and links inside the record's own name are skipped.
  3. Detail endpoint returns `links` (outgoing) and `referenced_by` with
     type, name, slug.
  4. UI renders links inline in `text_md` and a "Referenced by" list.
  5. pytest: unique-name link, ambiguous name not linked, typed mention
     linked, self-link skipped, referenced_by symmetry.
- **How to observe:** open the grapple rules section; "Improved Grapple" is
  a link; open Improved Grapple; grapple appears under Referenced by.
- **Touches:** `pipeline/owlsperch/build_db/`, `server/`, `web/`.

## B17: Pinned panel
- **Status:** pending
- **User-visible outcome:** a pin button on every record adds it to a
  right-hand panel that persists across pages and reloads.
- **Acceptance criteria:**
  1. Pin/unpin toggle on record pages, browse rows, and search hits.
  2. Panel lists pinned records with type badge and name; click opens the
     record; unpin from the panel.
  3. State is stored in localStorage under one key; reads and writes are
     wrapped in try/catch and the app works when storage is unavailable.
  4. Panel collapses to a toggle at < 900 px.
  5. Vitest for the pin store; Playwright: pin, navigate, reload, still
     pinned, unpin.
- **How to observe:** flow B end to end.
- **Touches:** `web/`.

## B18: `check-completeness`, `coverage`, `sample`
- **Status:** pending
- **User-visible outcome:** three reports that gate acceptance.
- **Acceptance criteria:**
  1. `check-completeness` parses the DnD3.5Index PDFs (spells, feats,
     monsters, magic items, classes, races, skills, deities) into name lists,
     resolves each against canonical records by normalized name and type,
     and writes `reports/completeness.md` with per-type totals and every
     unresolved name; exits 1 if any unresolved.
  2. `coverage <book_id|all>` lists pages with no record page reference and
     no `no_content` segment, writes `reports/coverage.md`, exits 1 if any.
  3. `sample N` writes `reports/sample-<date>.md` with N random canonical
     records across types, each with citation and a pass/fail checkbox, plus
     a `sample score <file>` subcommand that reads the marks and prints the
     pass rate.
  4. Unit tests with synthetic index text and synthetic records for each
     command.
- **How to observe:** `owlsperch check-completeness` prints
  `spells: 2200/2312 resolved` and the unresolved names.
- **Touches:** `pipeline/owlsperch/reports/`, tests.

## B19: Schema growth: proposals, `schema review`, versioning, `validate --stale`
- **Status:** pending
- **User-visible outcome:** subagent field and type proposals are collected
  and reviewable; records behind the current schema version are listed for
  re-extraction.
- **Acceptance criteria:**
  1. Subagents may add `proposed_fields` on records and `proposed_type` on
     segments; the skill appends them to `reports/proposals.jsonl` and strips
     `proposed_fields` from the record before validation.
  2. `schema review` groups proposals by type and field name with counts
     and example values, printing the top N.
  3. Each schema has a `version`; `validate --stale` lists records whose
     `schema_version` is behind and `queue requeue --stale` resets their
     segments to pending on haiku.
  4. Registry additions are picked up by `/schemas`, browse nav, facets, and
     detail layout with no UI change (Playwright test adds a fixture type
     and sees it in nav).
  5. Unit tests for proposal collection, grouping, stale listing, requeue.
- **How to observe:** `owlsperch schema review` prints proposals;
  `owlsperch validate phb --stale` lists records after bumping a version.
- **Touches:** `.claude/skills/extract/`, `pipeline/owlsperch/`, `web/`.

## B20: Items by slot tool
- **Status:** pending
- **User-visible outcome:** `/tools/items-by-slot` shows magic items grouped
  by slot, sorted by price.
- **Acceptance criteria:**
  1. `GET /tools/items-by-slot?slot=` returns canonical magic items for the
     slot sorted by numeric price ascending with name, price, caster level,
     citation, slug; no slot param returns the slot list with counts; items
     without a slot are under `slotless`.
  2. UI: slot tabs, sortable table, links to records.
  3. pytest for sorting, slotless, unknown slot 404; Playwright flow C.
- **How to observe:** flow C.
- **Touches:** `server/`, `web/`.

## B21: Point-buy calculator
- **Status:** pending
- **User-visible outcome:** `/tools/point-buy` computes remaining points,
  modifiers, and race-adjusted scores.
- **Acceptance criteria:**
  1. `GET /tools/point-buy` returns the DMG cost table (8→0 … 18→16), budgets
     [15, 22, 25, 28, 32], and canonical races with `ability_adjustments`.
  2. UI: budget selector plus custom positive integer; six scores clamped
     8–18 with +/−; remaining points (may go negative, shown red); modifier
     per score; optional race picker applying adjustments and showing final
     scores and modifiers.
  3. Cost and modifier math live in a pure module with vitest covering the
     full table and clamping; Playwright flow D.
- **How to observe:** flow D.
- **Touches:** `server/`, `web/`.

## B22: Roll20 macro generator
- **Status:** pending
- **User-visible outcome:** eligible records show a "Roll20 macro" button
  that produces sheet-targeted macro text to copy.
- **Acceptance criteria:**
  1. `build-db` sets `macro_eligible` per spec 4.11 (activation,
     casting_time, or manifesting_time present; dice expression in text; save
     or attack present).
  2. `server/roll20_sheet.yaml` holds the D&D 3.5E sheet's roll template name
     and attribute names for caster level, casting ability modifiers, and
     spell DC, sourced from the Roll20 character sheets repository; the
     source URL and commit are recorded in the spec decision log in the same
     PR.
  3. `POST /tools/roll20-macro` takes record id, optional caster level, and
     optional fixed DC; returns macro text per spec 4.11 using per-type
     templates (spell, psionic_power, feat, monster special attack) and a
     generic fallback.
  4. Dice parsing recognizes "XdY per [N] caster level[s] (maximum Z)",
     fixed "XdY", and "XdY + N per level", emitting inline rolls scaled by
     the caster level attribute or the entered value; unrecognized text is
     returned in `notes`.
  5. UI dialog with CL and DC inputs, preview, copy button.
  6. Vitest/pytest for dice parsing and each template; Playwright flow E.
- **How to observe:** flow E; paste into Roll20 and the macro rolls.
- **Touches:** `pipeline/owlsperch/build_db/`, `server/`, `web/`,
  `docs/specs/` (decision log entry).

## B23: Books page with extraction stats
- **Status:** pending
- **User-visible outcome:** `/books` lists every manifest entry with scope
  status, record counts by type, pending segments per tier, human-queue
  count, and orphan pages.
- **Acceptance criteria:**
  1. `build-db` computes per-book stats from segments and records into the
     `books` table.
  2. `GET /books` returns them; UI renders a sortable table with links to a
     per-book browse filter (`/browse/:type?book=<id>`).
  3. pytest for stats computation; Playwright opens `/books`.
- **How to observe:** browser `/books` shows PHB fully extracted and Spell
  Compendium partially.
- **Touches:** `pipeline/owlsperch/build_db/`, `server/`, `web/`.
