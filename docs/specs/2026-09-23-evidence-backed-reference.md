# Evidence-backed structured reference

Status: implementation authorized in conversation, 2026-09-23.

## Product contract

Structured reference entries and filters are essential from the first usable
release. A searchable collection of PDF pages is not a substitute. Preserve
original source evidence beneath those entries so extraction errors can be
detected and repaired without losing source content. Keep the existing local
SQLite/FastAPI/React site usable during migration.

The first implementation milestone is a reproducible, isolated ingestion pilot
with structured candidates, filtering, independent inventories, and adversarial
evaluation. It must establish measurable quality before corpus-wide publication.
This milestone is not a claim that the corpus has been migrated or certified.

## Problems being addressed

Existing schema validation accepts fabricated spell text/fields. Table cell
membership does not detect values moved between rows. Repeated headings collide
under name-derived IDs. Page membership is mistaken for coverage. Geometry and
extraction lineage are discarded. Errata attachments are not resolved rule fields.

## Architecture

1. Immutable source snapshots retain PDF SHA-256, extractor/version, PDF page
   indices, dimensions, block text, bounding boxes, and word geometry. Capture
   native text with the existing Poppler parser; never use cleaned legacy text as
   independent ground truth. Keep original extraction output as an artifact.
2. An independently authored benchmark inventory enumerates expected occurrences
   before model extraction. Occurrence IDs are supplied by the inventory, not
   derived from names. Two occurrences with the same name remain distinct.
3. Candidates contain name, type, readable text, typed fields, and exact evidence
   spans into snapshot blocks. Snapshot identity includes PDF and parser versions.
4. Evaluation separates inventory recall/precision, expected-field correctness,
   evidence validity, readable-text correctness when a reference is supplied, and
   review status. Exact spans establish provenance, not semantic entailment.
5. Transactional storage retains evaluated runs and their candidates. Filtered
   browsing uses JSON pointers and typed equality, returns only passing candidates
   by default, and reports missing/rejected/unreviewed counts for the whole run.
6. Later production migration links these occurrences to existing typed records,
   adds source previews to the UI, and introduces reviewed entity/version relations
   and explicit errata patches. Original records are never silently overwritten.

## Pilot interfaces

Use `owlsperch reference` subcommands. Every command requires an explicit `--db`
path; never default writes into the existing `~/owlsperch-data` corpus.

- `capture BOOK --pages A-B --db PATH`: only eligible manifest books; bounded PDF
  page range; preserve raw word/block evidence; record empty pages explicitly.
  Reject missing/out-of-range/invalid inputs without partially accepting a capture.
- `evaluate INVENTORY CANDIDATES --db PATH --run-id ID`: ingest one benchmark run,
  report JSON metrics/diagnostics; nonzero when acceptance fails. Run IDs are
  immutable: identical submissions are idempotent; changed submissions conflict.
- `browse --db PATH --run-id ID [--type TYPE] [--field POINTER --equals JSON]`:
  structured result list with coverage and review information, not names-only search.
- `source --db PATH --snapshot-id ID`: export the captured snapshot as JSON for
  bounded extraction workers; include source coordinates and immutable block IDs.

Inventory JSON format:

```json
{
  "version": 1,
  "dataset_id": "smoke-v1",
  "review_status": "provisional",
  "cases": [{
    "case_id": "book-p0010-spell-01",
    "snapshot_id": "capture-result-id",
    "name": "Example Spell",
    "type": "spell",
    "block_ids": ["captured-block-id"],
    "expected": {"/fields/school": "Evocation", "/fields/levels/0/level": 3},
    "required_evidence": ["/name", "/fields/school", "/fields/levels/0/level"]
  }]
}
```

Candidate JSON format:

```json
{
  "version": 1,
  "model": "explicit-model-id",
  "prompt_version": "reference-pilot-v1",
  "candidates": [{
    "case_id": "book-p0010-spell-01",
    "name": "Example Spell",
    "type": "spell",
    "text_md": "Source-preserving readable entry.",
    "fields": {"school": "Evocation", "levels": [{"class": "Wizard", "level": 3}]},
    "evidence": {"/name": [{"block_id": "captured-block-id", "start": 0, "end": 13, "quote": "Example Spell"}]}
  }]
}
```

Offsets are Unicode codepoint offsets into the exact captured block text. Evidence
must be nonempty, match the exact slice, and belong to the case's snapshot AND
allowed blocks. Invalid/extra evidence is rejected, not only missing required
evidence. Required evidence pointers must resolve in the candidate. Missing
expected fields fail; JSON comparisons preserve types (`true` is not `1`).
Expected pointers may address full arrays/tables to detect omission and cell swaps.
Duplicate case IDs, unknown case IDs, unexpected candidate entries, empty
inventories, unresolved snapshots, invalid JSON pointers, and malformed documents
must not silently pass. Missing candidates remain visible in the denominator.

`review_status` is `provisional` or `reviewed`. Provisional data can measure
agreement but never yields `release_ready: true`; model-authored references remain
provisional until independently checked against rendered pages. Readable text is
not certified by field scores. Release readiness requires reviewed references,
complete candidate recall, no extras/duplicates, correct expected fields and
evidence, and a reference assertion for `/text_md` in every case. This is readiness
for the bounded dataset, never a claim of whole-book completeness.

## Quality and cost policy

Use deterministic code for bookkeeping, hashing, equality, and source checks.
Use Luna for bounded inventory/extraction experiments, Sol for implementation and
challenging extraction, and Astra for architecture and final adversarial review.
Judge cheap extraction against independently prepared references; do not accept
self-reported confidence or schema validity as quality. Record actual model and
prompt version; record usage only if supplied by a trusted runner, never invented.
Do not automatically launch paid API jobs in this milestone.

## Safety and compatibility

Python >=3.12; existing dependencies only. New subsystem under
`pipeline/owlsperch/reference/`. No mutation of source PDFs, legacy records,
segments, queues, or existing database. No schema/envelope loosening. No automatic
canonicalization, correction application, or publication of pilot candidates.
No source book passages committed to Git. Synthetic fixtures may be committed.
Real snapshots, candidates, and references live in ignored `owlsperch-data/pilot/`
inside the isolated worktree. Reports in docs contain metrics and identifiers only.

## Acceptance

Regression tests must reject fabricated expected fields, missing entries, swapped
table cells, evidence from another source/block, forged quotes, invalid offsets,
stale source identities, and booleans masquerading as numbers. They must preserve
same-name occurrences, empty-page accounting, transaction rollback, and run
idempotency. Run the full existing Python suite plus lint, formatting, and typing.
Exercise the real smoke capture and at least two explicitly selected extraction
models, report measured limitations, then decide routing from evidence. Wider
150-250 page capture coverage is an extraction/layout sample, not an annotated gold
dataset; never confuse those denominators.
