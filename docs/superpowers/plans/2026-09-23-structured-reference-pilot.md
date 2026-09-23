# Structured reference pilot implementation plan

> **For agentic workers:** Use superpowers:subagent-driven-development for the
> implementation and review. User authorized a cost-aware multi-model workflow.

**Goal:** Implement and exercise an isolated structured-ingestion evaluation pilot.

**Architecture:** Immutable raw source snapshots, independently supplied occurrence
inventories, exact field/evidence evaluation, transactional candidate runs and
structured filtering. The existing site and corpus remain unchanged.

**Tech Stack:** Python 3.12+, sqlite3, existing Poppler bbox parser, argparse,
pytest, existing ruff/mypy configuration.

**Spec:** docs/specs/2026-09-23-evidence-backed-reference.md

**Execution status (2026-09-23):** Tasks 1 and 2 implemented and exercised;
the final Astra whole-branch review approved the bounded pilot with no blockers.
The checklist below preserves the
original work breakdown. Measured results and remaining product gates are in
`docs/benchmarks/2026-09-23-reference-pilot.md`.

## Global Constraints

- Python >=3.12; existing dependencies only.
- Every new CLI command requires an explicit `--db` path.
- No mutation of source PDFs, legacy records, segments, queues, or existing database.
- No source book passages committed to Git. Synthetic fixtures may be committed.
- Evidence offsets are Unicode codepoint offsets into exact captured block text.
- Provisional references never yield `release_ready: true`.
- Structured entries and typed filtering are required in this pilot.

## Review Focus

1. Same names at different source occurrences must not collide (Task 1 identity test).
2. Partial or empty output must not improve recall/acceptance (Task 1 omission test).
3. Evidence from the right page but wrong source/block must fail (Task 1 source test).
4. Table values in wrong cells must fail (Task 1 full-array expected-value test).
5. Changed sources or run inputs must not reuse stale evidence/results (Task 1 stale and immutable-run tests).

## Task 1: Implement the coherent reference pilot

**Files:** create `pipeline/owlsperch/reference/{__init__,capture,store,evaluate,runner}.py`,
`pipeline/tests/test_reference.py`; modify `pipeline/owlsperch/cli.py`.
Keep modules focused; split test modules only if the suite becomes hard to read.

**Interfaces:** consumes `ManifestEntry`, `load_manifest`, `status_for`,
`default_pdf_dir`, `run_pdftotext`, `parse_bbox_xhtml`. Produces the exact CLI and
document interfaces in the spec; internal Python function signatures may be chosen
to keep persistence separate from pure validation.

- [ ] Write and run failing behavioral tests for the spec's acceptance cases.
  Minimal adversarial assertion pattern (using the evaluator's chosen function):

  ```python
  original = candidate_fixture()
  altered = copy.deepcopy(original)
  altered["fields"]["school"] = "Necromancy"
  baseline = evaluate(inventory_fixture(), [original], snapshot_fixture())
  corrupted = evaluate(inventory_fixture(), [altered], snapshot_fixture())
  assert baseline["counts"]["passed"] == 1
  assert corrupted["counts"]["passed"] == 0
  ```

  Fixtures are independently hand-authored synthetic source blocks with exact
  offsets and expected values. Also test missing candidates, extras, duplicate
  IDs, cell swaps, Unicode offsets, cross-snapshot quotes, false/0 distinctions,
  missing readable text, and reviewed vs provisional release gates.
- [ ] Implement pure JSON-pointer resolution and type-strict expected comparison,
  source-span validation, inventory reconciliation and scored diagnostics. Count
  denominator cases even when no candidate arrives. Validate document shapes and
  fail with actionable messages. Evidence validity is distinct from field accuracy.
- [ ] Implement source capture with source/extractor hashes, raw artifact retained
  in SQLite, page/block/word geometry retained. Reject partial ranges and record
  text-empty pages explicitly. In tests use generated bbox XHTML and fake PDF
  bytes with only subprocess boundary substituted; also exercise real Poppler in
  the corpus smoke outside ordinary tests.
- [ ] Implement transactional SQLite persistence for snapshots and immutable run
  documents/results. An exception must rollback the entire import/run. Repeating
  an identical run ID/content is idempotent; changed input conflicts.
- [ ] Implement CLI capture/evaluate/source/browse and typed JSON-pointer filtering.
  `evaluate` prints JSON report on valid but failed submissions and returns 1;
  malformed inputs produce a readable error and nonzero exit without a saved run.
  `browse` reports coverage/review information alongside matching passing candidates.
- [ ] Run focused tests, then the full Python suite, ruff check/format, and mypy.
  Commands use the existing interpreter without altering main's environment:

  ```sh
  PYTHONPATH=pipeline:server /Users/swanpro/git/owlsperch/.venv/bin/python -m pytest -q
  /Users/swanpro/git/owlsperch/.venv/bin/ruff check .
  /Users/swanpro/git/owlsperch/.venv/bin/ruff format --check .
  PYTHONPATH=pipeline:server /Users/swanpro/git/owlsperch/.venv/bin/mypy pipeline server
  ```
- [ ] Record RED/GREEN evidence and limitations in the implementation report, then
  complete a fresh task review before real extraction experiments.

## Task 2: Exercise real sources and document measured routing

**Files:** create `docs/benchmarks/2026-09-23-reference-pilot.md`; update README with
pilot commands. Real artifacts go only under ignored `owlsperch-data/pilot/`.

**Interfaces:** consumes Task 1 CLI; produces reproducible capture commands,
provisional inventory, candidate submissions for Luna and Sol, evaluation reports,
and a scoped findings report. The larger sample selects eligible sources only.

- [ ] Capture a bounded representative source sample with the new CLI, export
  selected snapshots, and independently identify a small set of complete entries.
- [ ] Prepare reference field values before model candidate extraction; annotate
  provenance and keep references provisional unless rendered pages were reviewed.
- [ ] Give Luna and Sol the same source context and output contract without expected
  answers. Store responses separately and evaluate them deterministically.
- [ ] Run structured filter queries and adversarial mutation probes against those
  actual candidate artifacts. Record error categories and denominators separately.
- [ ] Publish measured results, reproducible commands, routing recommendation and
  remaining gates. Do not infer generalization or total cost from the smoke sample.
- [ ] Perform final whole-branch adversarial review on Astra, fix material findings,
  rerun applicable checks, and leave the isolated branch reviewable. No push/PR
  until the user confirms the base branch as their repository instructions require.
