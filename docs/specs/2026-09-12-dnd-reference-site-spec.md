# D&D 3.5e reference site — spec

Status: APPROVED
Date: 2026-09-12

## Context

`owlsperch` is an empty repository. The user owns roughly 233 D&D PDFs in `~/D_D`
(about 30,150 pages, 3.0 GB). About 184 have a text layer; about 20 large books
are image scans. Text-layer extraction returns columns out of order and mixes
running headers into paragraphs. Many titles exist in two to four copies (scan
plus text-layer, or 3.0 original plus 3.5 Update booklet). The folder also holds
non-D&D and personal files.

The user is the only operator and the only reader.

## Goals / Non-goals

### Goals

- Every D&D 3.5e source book the user owns becomes typed, queryable records in
  one database, holding the rule text as written after errata, 3.5 Update
  overlays, and Rules Compendium precedence are applied.
- A local webapp lets a player find any rule element in seconds by name or by
  filter, read it in full, follow links to related elements, and keep a pinned
  set visible during a session.
- Three table tools built on the records: a DMG point-buy calculator, Magic Item
  Compendium style item-by-slot cost tables, and a Roll20 macro generator that
  targets Roll20's built-in D&D 3.5E character sheet.
- Extraction is driven by Claude Code Agent-tool subagents on a
  haiku → sonnet → opus ladder with file-based state, runnable one book at a
  time across many sessions and resumable after interruption.
- Schemas are a growth surface. Every rule type in the corpus is intended to
  gain a schema over time, and the UI derives from schemas so new types and
  fields need no UI code.

### Non-goals

- No public or multi-user hosting, no accounts, no auth. Localhost only.
- No full-text page search. Typed records are the only search surface.
- No 3.0-only books, adventures, map folios, non-D&D material, or personal
  documents.
- No page-image provenance in the UI. Citation text only.
- No errata diff view. Precedence is applied; superseded printings are stored
  as variants but not rendered as a comparison.
- No character sheet, encounter builder, or dice roller beyond what the three
  named tools need.

## Users & flows

Single user: the repo owner, at a desk or laptop, during play or prep.

- **Flow A, lookup mid-combat.** Type "grapple" into the always-visible search
  box. Typeahead lists matching records grouped by type (rules section, feat,
  special attack). Enter opens the Rules Compendium grapple section, with links
  to Improved Grapple, the grappled condition, and related skills.
- **Flow B, filtered browse.** Open Spells, filter Cleric level 3, school
  Conjuration, any source. Sort by name. Open a spell, pin it. The pinned panel
  keeps it visible on every page until unpinned.
- **Flow C, shopping.** Open Magic Items → By Slot. Choose Hands. See every
  glove and gauntlet across all books sorted by price, with source. Click
  through to the record.
- **Flow D, character creation.** Open Point Buy. Pick a 32-point budget and
  race Gray Elf. Adjust scores and see remaining points, modifiers, and
  race-adjusted totals.
- **Flow E, Roll20 prep.** Open a spell, click "Roll20 macro", enter caster
  level and DC or leave the sheet's attribute references in place, copy the
  macro, paste into Roll20.
- **Flow F, extraction session (operator).** In Claude Code, run the extract
  skill on one book. Subagents fan out, records land in the data directory, and
  a summary reports how many segments validated on haiku, sonnet, and opus, and
  how many are queued for human review. Re-running resumes where it stopped.

## Scope boundaries

### Books

**In scope:** every D&D 3.5e rulebook, supplement, campaign setting, regional
book, DM-side guide (Stronghold Builder's Guidebook, Book of Challenges, Hero
Builder's Guidebook, and similar), and magazine rules compendium in `~/D_D`;
all errata PDFs and web enhancements; 3.0 books that have a 3.5 Update booklet,
with the booklet applied as an overlay; the DnD3.5Index PDFs, used only as
completeness checklists, never as sources.

**Out of scope:** 3.0 books with no 3.5 Update; adventures; map folios;
Dragon or Dungeon single issues that are pure adventure content; non-D&D files
(Call of Cthulhu, Spartan Rulebook, D&D For Dummies); personal files (Aphelion
documents, Survival_In_Soti.rtf, Borderlands, FillInCharSHEET.pdf,
IMG_2390.png).

The exact in/out list is the curated manifest described in Behavior 4.3. It is
drafted in the first batch and reviewed by the user.

### Entity types

Spells, psionic powers, feats, base classes, prestige classes, races, skills,
equipment, magic items, rules sections, monsters and NPC stat blocks,
templates, deities and domains, organizations, locations, planes, timeline
entries, named NPCs, tables as first-class records, and errata and update
entries. New types are added through the schema-growth mechanism in 4.14.

### Duplicates and precedence

Where a title exists as both a scan and a text-layer copy, the text-layer copy
is the source. Errata supersede the original book. 3.5 Update booklets
supersede their 3.0 book. The Rules Compendium supersedes any competing rules
section. Among what remains, the latest publication date wins and other
printings are stored as variants.

### Storage

Code lives in the repo. Extracted JSON, the SQLite build, OCR output, and
rendered page images live in a data directory outside the repo, default
`~/owlsperch-data`, overridable by environment variable. PDFs are read in place
from `~/D_D` and never copied.

## Behavior (interfaces, data, commands)

### 4.1 Repository layout

| Path | Contents |
|---|---|
| `pipeline/` | Python package managed by uv. CLI entry `owlsperch`. |
| `pipeline/manifest.yaml` | Curated book manifest. Contains no copyrighted text. |
| `schemas/` | One JSON Schema per entity type plus the common envelope and a type registry. |
| `.claude/skills/extract/` | Claude Code skill that orchestrates extraction subagents. |
| `server/` | FastAPI app. |
| `web/` | Vite + React + TypeScript app. |
| `docs/specs/` | This spec and its successors. |

### 4.2 Data directory

Root is `$OWLSPERCH_DATA`, default `~/owlsperch-data`. PDFs are read from
`$OWLSPERCH_PDFS`, default `~/D_D`.

| Path | Contents |
|---|---|
| `text/<book_id>/p0001.txt` | Column-repaired text, one file per page. |
| `pages/<book_id>/p0001.png` | 300 dpi renders. Scanned books only. |
| `segments/<book_id>/<seg_id>.json` | Candidate entity or section: page span, kind hint, text, status, attempt log. |
| `records/<book_id>/<type>/<slug>.json` | Validated records. |
| `human/<book_id>/<seg_id>.json` | Segments that failed on opus, and unmatched override entries. |
| `db/owlsperch.sqlite` | Built from records. Disposable; always rebuilt from scratch. |
| `reports/` | Completeness, coverage, proposals, and sample checklists. |

### 4.3 Manifest entry

Each file in the PDF directory has exactly one entry. Fields:

- `book_id` — short stable id, e.g. `phb`, `spell-compendium`.
- `title`
- `file` — filename relative to the PDF directory.
- `edition` — `3.0` or `3.5`.
- `kind` — one of `rulebook`, `supplement`, `setting`, `guide`, `magazine`,
  `errata`, `update`, `web_enhancement`, `index`, `excluded`.
- `published` — `YYYY-MM`.
- `applies_to` — `book_id` this errata or update modifies.
- `scanned` — boolean.
- `preferred_over` — `book_id` of a duplicate copy this one replaces.
- `exclude_reason` — required when `kind` is `excluded`.

A book is in scope when `kind` is neither `excluded` nor `index`, and either
`edition` is `3.5` or an `update` entry has `applies_to` pointing at it. The
manifest check fails if any file in the directory lacks an entry.

### 4.4 Pipeline commands

```
owlsperch manifest check
owlsperch text <book_id|all>          # pdftotext -bbox-layout → column repair; scanned → pdftoppm + tesseract
owlsperch segment <book_id|all>       # pages → segments with kind hints
owlsperch validate <book_id|all> [--stale]
owlsperch build-db                    # records → precedence → SQLite, full rebuild
owlsperch check-completeness          # DnD3.5Index name lists vs canonical records
owlsperch coverage <book_id|all>      # pages with no record and no explicit no-content mark
owlsperch schema review               # group field/type proposals by frequency
owlsperch sample <N>                  # random canonical records → verification checklist
```

**Column repair** groups text blocks by x-position into columns, orders
columns left to right, drops running headers and footers by repetition across
pages, rejoins hyphenated line breaks, and records the printed page number
read from the footer as page metadata.

**OCR** renders scanned pages at 300 dpi and runs tesseract in automatic page
segmentation mode, then applies the same column repair.

**Segmentation** uses heading detection from font size in the bbox output plus
pattern anchors: spell blocks start at a name line followed by a school line;
stat blocks at a "Size/Type" line or "Hit Dice:"; feats at a name followed by
"Prerequisite" or "Benefit"; tables at a "Table X–Y:" caption. Text between
anchors becomes a rules-section segment under the nearest heading. Segments
may span pages. Each segment carries a kind hint the subagent may override.

### 4.5 Extraction skill

```
/extract <book_id|all> [--tier haiku|sonnet|opus] [--parallel N] [--limit N]
```

Default tier is the lowest tier with pending segments. Default parallelism is
8.

For every segment pending at the given tier, the skill launches Agent-tool
subagents with `model` set to that tier, N at a time. Each subagent receives:
the segment text, kind hint, book metadata, the JSON Schemas for the candidate
types, the extraction rules, and prior validation errors when this is a retry.
On sonnet and opus tiers for scanned books it also receives the page PNG path.

The subagent writes zero or more record files under `records/` and returns
their paths, or marks the segment no-content with a reason (art, blank, table
of contents), or returns `needs_context` naming adjacent segment ids, or
returns `proposed_type` when the segment fits no schema. It may attach
`proposed_fields` to any record.

The orchestrator then runs validation. Pass marks the segment done, recording
tier and model on each record. Fail attaches the errors and moves the segment
to the next tier. `needs_context` triggers one retry at the same tier with the
merged text before escalation. Failure after opus, or a `proposed_type`, moves
the segment to `human/`. Segments marked in-progress for more than 60 minutes
are reset to pending on the next run. Rerunning skips done segments. Each run
prints counts per tier and per outcome.

**Validation** is schema conformance plus type-specific consistency checks,
for example: a spell has at least one class level and a school; a monster's HD
count matches its HP expression and CR is numeric; a feat has a benefit; a
table has equal-length rows; every record cites at least one page inside the
segment's span; every record carries the current schema version.

### 4.6 Record envelope

Every record has: `id`, `type`, `name`, `slug`, `aliases`, `book_id`, `pages`,
`citation` (e.g. "PHB p. 120"), `text_md` (rule text as Markdown), `fields`
(type-specific), `tables` (table record ids), `canonical` (set at build),
`variant_of`, `applied_overrides` (errata and update entry ids),
`macro_eligible` (set at build), `schema_version`, and `extraction` (tier,
model, segment id, timestamp).

Initial type-specific `fields`. This list is expected to grow substantially
through the schema-growth mechanism in 4.14.

| Type | Fields |
|---|---|
| spell | school, subschool, descriptors, levels [{class, level}], components, casting_time, range, target_effect_area, duration, saving_throw, spell_resistance, costs (material, focus, xp) |
| psionic_power | discipline, subdiscipline, descriptors, levels, display, manifesting_time, range, target_effect_area, duration, saving_throw, power_resistance, power_points, augment |
| feat | feat_type, prerequisites, benefit, normal, special |
| class, prestige_class | hit_die, alignment, requirements, class_skills, skill_points, bab_progression, save_progressions, spellcasting, level_table, class_features |
| race | size, type, ability_adjustments, speed, favored_class, level_adjustment, traits |
| skill | key_ability, trained_only, armor_check, synergies, dc_table |
| equipment | category, cost, weight; weapons: damage, critical, range, damage_type; armor: bonus, max_dex, check_penalty, spell_failure |
| magic_item | category, slot, price, weight, caster_level, aura, prerequisites, activation |
| monster, npc | size, type, subtypes, hd, hp, initiative, speed, ac, bab, grapple, attack, full_attack, space_reach, special_attacks, special_qualities, saves, abilities, skills, feats, environment, organization, cr, treasure, alignment, advancement, level_adjustment |
| template | acquired_or_inherited, applies_to, cr_change, la_change, modifications |
| rules_section | topic, parent_section, chapter |
| deity | pantheon, alignment, domains, portfolio, favored_weapon, rank |
| organization, location, plane, timeline_entry | category, region_or_setting; planes add traits; timeline_entry adds year and calendar |
| table | caption, columns, rows, parent_record |
| errata_entry, update_entry | target_book, target_page, target_name, replacement_text |

### 4.7 Precedence at build time

1. Group canonical candidates by type and normalized name.
2. Apply errata entries and update entries to their targets, appending to
   `applied_overrides`. An entry whose target cannot be matched goes to
   `human/` with the candidates the matcher considered.
3. For rules sections, a Rules Compendium record with a matching topic becomes
   canonical over any other book. A report lists Rules Compendium sections that
   matched nothing.
4. Among what remains, the latest `published` wins. Others get `variant_of`
   pointing at the winner.
5. Search and browse return canonical records only. Detail pages list variants
   under "other printings".

### 4.8 SQLite schema

- `books` — manifest rows plus per-book counts.
- `records` — id, type, name, slug, book_id, canonical, macro_eligible, json.
- `record_fields` — record_id, key, text_value, num_value. Key/value so new
  fields need no migration.
- `record_links` — from_id, to_id. Built by longest-match scan of `text_md`
  against canonical names that are unique across canonical records, plus
  typed mentions ("the spell X").
- `record_pages` — record_id, book_id, page.
- `tables` — table records with columns and rows.
- `names_fts` — FTS5 over name and aliases only, for typeahead. No page text
  and no field values are indexed.

### 4.9 API (FastAPI on localhost:8000)

| Endpoint | Purpose |
|---|---|
| `GET /search?q=&types=` | Typeahead over names and aliases, grouped by type, canonical only. |
| `GET /records/{type}?<field>=&sort=&page=` | Filtered, sorted, paginated list. Filter and sort keys come from the schema's UI hints. |
| `GET /facets/{type}` | Filter keys and distinct values with counts. |
| `GET /records/{type}/{slug}` | Full record, variants, outgoing links, referenced-by, tables. |
| `GET /tools/items-by-slot?slot=` | Canonical magic items for a slot sorted by price. |
| `GET /tools/point-buy` | DMG cost table, budgets, races with ability adjustments. |
| `POST /tools/roll20-macro` | Record id plus parameters in, macro text out. |
| `GET /books` | Manifest with per-book record and coverage counts. |
| `GET /schemas` | Type registry and UI hints, consumed by the frontend. |

### 4.10 Web UI (Vite React on localhost:5173)

Routes: `/` search, `/browse/:type` with a facet sidebar generated from schema
hints, `/r/:type/:slug` detail with field groups generated from schema hints,
`/tools/point-buy`, `/tools/items-by-slot`. A pinned panel is always visible
and persists in localStorage. Record text renders cross-links inline. Records
with `macro_eligible` show a "Roll20 macro" button that opens a dialog for
caster level and DC inputs and a copy button.

### 4.11 Roll20 macro

Target is Roll20's built-in D&D 3.5E character sheet. The roll template and
attribute names are read from that sheet's source in the Roll20 character
sheets repository during the tools batch, recorded in the decision log, and
held in one config file so a sheet change is a config edit.

**Eligibility** is computed at build time. A record is macro-eligible when any
holds: it has an `activation`, `casting_time`, or `manifesting_time` field; its
text contains a dice expression; or it has a saving throw or attack roll.

**Content:** name; level and school (or discipline, or feat type) line;
activation or casting time; range; target; duration; save line with DC as
`10 + level + <sheet casting-ability modifier attribute>` unless a fixed DC is
entered; spell or power resistance; full description. Damage and scaling
expressions recognized from text become inline rolls that scale with the
sheet's caster level attribute: "XdY per [N] caster level[s] (maximum Z)",
fixed "XdY", and "XdY + N per level". Unrecognized expressions stay as
description text with a note in the dialog. Rendering is template-per-type
with a generic fallback that emits name, activation, recognized rolls, save
line, and description.

### 4.12 Point buy

DMG 3.5 cost table (score 8 costs 0 points, 18 costs 16). Budget selector 15,
22, 25, 28, 32, or custom positive integer. Six scores from 8 to 18 with plus
and minus controls, remaining points, modifiers, and an optional race picker
that applies `ability_adjustments` from race records and shows adjusted totals.

### 4.13 Items by slot

Groups canonical magic items by `slot`, sorted by `price` ascending, showing
name, price, caster level, citation, and a link to the record. Items with no
slot appear under "slotless".

### 4.14 Schema growth

Schemas are the single source of truth for types and fields. Everything
downstream derives from them.

- Each JSON Schema carries UI hints per field: label, filterable, sortable,
  display group, order. Browse pages, facets, and detail layouts are generated
  from these hints. Adding a field is a schema edit plus re-extraction of
  affected segments, never a UI code change.
- `record_fields` is key/value, so new fields need no database migration.
- Subagents may return `proposed_fields` (name, example value, rationale) on
  any record and `proposed_type` on any segment. Proposals accumulate in
  `reports/proposals.jsonl`. `owlsperch schema review` groups them by
  frequency for acceptance into a schema in one edit.
- Schemas are versioned. Each record stores the schema version it validated
  against. `owlsperch validate --stale` lists records behind the current
  version so they can be re-queued.
- Adding a type is: create the schema file, register it in the type registry,
  add segment anchors if the type has a recognizable layout, run segment and
  extract. The stated intent is that every rule type in the corpus eventually
  has a schema.

## Edge cases & failure modes

- **Entity split across pages or columns.** The segmenter merges by
  continuation heuristics. A subagent that sees a truncated entity returns
  `needs_context`; the orchestrator retries once at the same tier with merged
  text before escalating.
- **Same name, different things** (Shadow the monster, Shadow the template,
  shadow the descriptor). Ids and routes include the type. Typeahead shows a
  type badge. Cross-links are created only for names unique among canonical
  records, or where the text names the type. Ambiguous mentions stay plain
  text.
- **OCR garbage.** Validation fails, the segment escalates with the page image
  attached, and if opus fails too it goes to `human/`.
- **Errata or update target not found.** The entry goes to `human/` with the
  candidates the matcher considered.
- **Rules Compendium topic with no counterpart.** Nothing is overridden. A
  report lists unmatched Rules Compendium sections so the topic map can be
  tuned.
- **Duplicate copies of one book.** `preferred_over` marks the copy to use. The
  other copy is never processed.
- **Printed page number versus PDF index.** Citations use the printed number
  read from the footer. If none is found, the citation falls back to
  "pdf p. N".
- **Subagent returns malformed output** or writes to the wrong path. Treated
  as a validation failure and escalated.
- **Interrupted session.** Segment status is on disk. Segments in-progress for
  more than 60 minutes are reset to pending on the next run.
- **Record references a table that failed.** The record stays valid. The table
  goes through its own ladder. The UI shows a "table pending" marker until it
  lands.
- **Non-ASCII names** such as Faerûn. Slugs are ASCII-folded; the original
  spelling is kept in `aliases` so typeahead matches both.
- **Very large books.** Spell Compendium alone yields roughly a thousand
  segments. `--limit` plus resumability handle it across sessions.
- **Point buy.** Scores below 8 or above 18 are rejected, matching the DMG
  table. Custom budgets accept any positive integer.
- **Roll20.** Unrecognized damage or scaling text is left as description with
  a note. Sheet attribute names live in one config file.
- **Missing data directory or empty database.** The CLI exits naming the
  environment variable to set. The webapp shows an empty state naming the
  build command.

## Testing & acceptance

### Automated, in CI

GitHub Actions on every PR. Main must stay green.

- **Pipeline (pytest):** column repair, header/footer removal, dehyphenation,
  printed-page-number detection, segment anchors, every validator with valid
  and invalid fixtures per type, precedence resolution (errata override,
  update override, Rules Compendium override, latest-wins), build-db round
  trip. Fixtures are synthetic text, never book excerpts.
- **Extraction skill:** dry-run mode with a fake subagent returning canned
  outputs. Tests cover pass, fail-then-escalate, needs-context merge, opus
  failure to human, proposed-type to human, stale in-progress reset, and
  resume after interruption.
- **Server (pytest):** fixture database built from fixture records, one test
  per endpoint including filter, sort, pagination, facets, and schema hints.
- **Web (Vitest):** point-buy math, dice-expression parsing, macro templates as
  pure functions. **Playwright** smoke tests for flows A through E against the
  fixture database.
- **Lint and types:** ruff and mypy for Python; eslint and tsc for TypeScript.

### Manual gates against the real corpus

- `owlsperch check-completeness` reports 100% of names in the DnD3.5Index
  spell, feat, monster, magic item, class, race, skill, and deity lists
  resolved to a canonical record of the matching type.
- `owlsperch coverage all` reports zero orphan pages across in-scope books.
- `owlsperch sample 200` writes a checklist of 200 random canonical records
  across types. The user verifies each against the PDF. At least 190 pass on
  every field.
- Typeahead responds in under 200 ms locally, measured by the Playwright
  smoke test run against the real database.
- Every in-scope book has zero pending segments, and `human/` is empty or
  every item in it carries a resolution note.

## Assumption ledger

| # | Question | Proposed default | Status |
|---|----------|------------------|--------|

No open entries.

## Decision log

Every entry was resolved by the user in the specifying interview on
2026-09-12.

| # | Decision | Resolution |
|---|---|---|
| D1 | Audience and hosting | Single user, localhost only, no auth. |
| D2 | Index depth | Fully structured, every rule element typed. Full-text page search explicitly rejected. |
| D3 | Extraction method | Deterministic pre-pass (OCR, column repair, segmentation) plus LLM extraction. |
| D4 | Extraction driver | Claude Code Agent-tool subagents, cheapest sufficient model first, escalate on failure. Not an API pipeline. |
| D5 | Corpus scope | All 3.5e source books, campaign settings, DM guides, magazine rules content, errata, web enhancements. Skip 3.0-only, adventures, map folios, non-D&D, personal files. |
| D6 | Precedence rules | Errata supersede the base book. Rules Compendium supersedes competing rules. Then latest publication wins; older printings kept as variants. |
| D7 | 3.0 books with a 3.5 Update booklet | Index the 3.0 book, apply the update as an overlay. |
| D8 | Entity types | Player-facing core set, creatures, setting and lore entities, tables as first-class records. |
| D9 | Stack | Python pipeline (uv), FastAPI server, Vite React TypeScript frontend, SQLite. |
| D10 | Webapp features | Search, browse, filter, detail; cross-linking; bookmarks and pinned panel; point-buy calculator; items by slot sorted by cost; Roll20 macro generator. Errata diff view not selected. |
| D11 | Provenance | Citation text only, no page images in the UI. |
| D12 | Point buy | DMG 3.5 table, budgets 15/22/25/28/32 plus custom, racial adjustments from race records. |
| D13 | Roll20 target | Roll20's built-in D&D 3.5E sheet; template and attribute names captured during the tools batch. |
| D14 | Macro eligibility | By content (activation, dice expression, save, or attack), not by type. |
| D15 | Extraction ladder | haiku → sonnet → opus, file-based queue, schema-validated, DnD3.5Index as completeness checklist. |
| D16 | Acceptance criteria | 100% DnD3.5Index completeness, at least 190 of 200 sampled records fully correct, typeahead under 200 ms, zero orphan pages. |
| D17 | OCR | Tesseract first; page image to subagent only on validation failure. |
| D18 | Generated data location | Nothing generated is committed. JSON, DB, OCR output live in `$OWLSPERCH_DATA`, default `~/owlsperch-data`. |
| D19 | Schema growth | Field list is expected to grow substantially; schema-driven UI, subagent proposals, versioned schemas. |
| L1 | Default subagent parallelism | 8. |
| L2 | Typeahead scope | Names and aliases only; field values reached through filters. |
| L3 | Macro-eligible types | Superseded by D14. |
| L4 | build-db behavior | Always a full rebuild from JSON; the SQLite file is disposable. |
| L5 | Cross-link ambiguity | Link only names unique among canonical records, plus typed mentions. |
| L6 | In-progress reset timeout | 60 minutes. |
| L7 | Playwright in CI | Fixture DB only; the real-DB latency check is a manual gate. |
