# Structured reference pilot: measured results

Date: 2026-09-23. Scope: isolated ingestion pilot, not a production corpus release.

## What is implemented

`owlsperch reference capture`, `source`, `evaluate`, and `browse` provide a working
pipeline from PDF page evidence to independently scored structured candidates and
typed field queries. Every operation requires an explicit database path. The
existing extraction queue, records, and published site are unchanged.

Captures retain PDF and artifact hashes, Poppler and capture-format versions,
one-based physical PDF pages, raw XHTML, block text and coordinates, word
coordinates, and successful-extraction warnings. SQLite stores immutable captures
and evaluation runs. It refuses unrelated databases. Read-only operations do not
create a database.

Evaluation distinguishes occurrence accounting, typed expected-field agreement,
valid source spans, text presence, independently checked text, and reference review
status. Model-authored reference answers and unscored prose do not certify release
readiness. A stored run is immutable: reuse of the same run ID and inputs returns
the original report; use a new run ID for a new evaluation experiment.

## Source sample

The machine-readable selection is [pilot-sources.json](pilot-sources.json).
Ranges were checked against actual manifest eligibility and PDF page counts.

| Source | PDF pages sampled | Native-text pages | Text-empty pages |
|---|---:|---:|---:|
| Player's Handbook | 62 | 62 | 0 |
| Spell Compendium | 30 | 30 | 0 |
| Monster Manual I | 30 | 30 | 0 |
| Tome of Magic | 30 | 30 | 0 |
| Dragonlance Campaign Setting | 15 | 0 | 15 |
| Complete Warrior (preferred text copy) | 15 | 15 | 0 |
| Player's Handbook errata | 3 | 3 | 0 |
| Spell Compendium errata | 1 | 1 | 0 |
| Total | 186 | 171 | 15 |

The 15 scan pages remain explicitly unresolved; capture is not OCR. The initial
sample request incorrectly included a second Spell Compendium errata page. The
page-bound check rejected it, and the selection was corrected. An initial
override-eligibility implementation defect was also caught on actual PHB errata
and fixed with a regression test. Repeated native captures retained identical IDs;
changing the capture-format version produced new IDs.

These counts measure captured pages, not extracted entities or rule completeness.
The native-text pages have not all been visually checked for layout fidelity.

## Blinded structured extraction comparison

Eight occurrences were independently selected from five physical pages across
three books: Fireball, Flame Blade, Align Fang, Allegro, Power Attack, Quick Draw,
Aboleth, and Aboleth Mage. They test four spells, two feats, and two adjacent
monster stat columns. Expected field values were authored by the controller after
inspecting rendered source pages, before candidate extraction.

Both workers received identical raw block text/coordinates, occurrence IDs and
names, a field normalization contract, and exact evidence-span requirements.
Expected answers were withheld. Each worker ran once at medium reasoning, without
evaluation feedback or retry. Offsets could be computed with deterministic code.
Neither worker abstained. These are explicitly selected occurrences, so this is
candidate completion coverage, not independent entity-discovery recall.

| Measure | GPT-6 Luna | GPT-6 Sol |
|---|---:|---:|
| Requested occurrences returned | 8/8 | 8/8 |
| Expected field assertions correct | 46/46 | 46/46 |
| Cases with valid required source spans | 8/8 | 8/8 |
| Missing / extra / duplicate candidates | 0 / 0 / 0 | 0 / 0 / 0 |
| Cases with independently scored full prose | 0/8 | 0/8 |
| Release ready | No | No |

An assertion may compare an entire array/object (spell levels or ability scores),
not necessarily one scalar. Evidence allowlists cover each case's selected pages,
not tightly cropped entry regions. Exact spans prove where the quote came from;
they do not independently prove semantic support or correct column attribution.
The separately authored expected values check that attribution for scored fields.

The inventory remains provisional. This tiny selected sample supplies no credible
corpus-wide error-rate estimate. It does not compare parser alternatives, OCR,
cross-book identity resolution, errata application, full prose preservation, or
unseen complex entity families. The smoke used immutable captures made before the
capture-format metadata was added; those originals remain readable in the pilot
database. The 186-page sample was recaptured with explicit format version 1.

## Adversarial checks and structured queries

The following mutations of the real Luna submission were independently evaluated
without changing its original artifact. All six corruptions reduced the passing
case count and remained ineligible for release:

- Fabricated spell school.
- Missing inventoried occurrence.
- Aboleth and Aboleth Mage hit points exchanged between their columns.
- A valid name quote borrowed from a different source snapshot.
- A forged source quote.
- Integer `1` substituted for boolean `true`.

A seventh probe deliberately fabricated readable prose while leaving scored
fields unchanged. Field agreement still passed, but `correct_text` remained zero
and `release_ready` remained false. This is an explicit remaining limitation,
not evidence that prose fabrication is detected by field checks.

Actual CLI browse results from the Luna run:

| Typed query | Results |
|---|---|
| spell, `/fields/school` equals `"Evocation"` | Fireball; Flame Blade |
| monster, `/fields/cr` equals `7` | Aboleth |
| feat, `/fields/fighter_bonus` equals `true` | Power Attack; Quick Draw |

Every browse response also includes the whole run's coverage/review counts, so a
filtered subset cannot imply the source collection is complete.

## Artifacts and reproduction

Real-book artifacts are intentionally outside Git, under the worktree's ignored
`owlsperch-data/pilot/`: `reference.sqlite`, `snapshots/`, rendered `pages/`,
`reference-seed.json`, `inventory.json`, `worker-input.json`,
`extraction-contract.md`, `luna-candidates.json`, `sol-candidates.json`,
`luna-report.json`, `sol-report.json`, and `experiment-results.json`.
Local driver scripts record source capture, worker-input preparation, evaluation,
mutations, and queries. Do not regenerate reference answers from model candidates.

From a configured checkout, the public commands are:

```sh
uv run owlsperch reference capture phb1 --pages 232-232 --db owlsperch-data/pilot/reference.sqlite
uv run owlsperch reference evaluate owlsperch-data/pilot/inventory.json owlsperch-data/pilot/luna-candidates.json --run-id smoke-luna-v2 --db owlsperch-data/pilot/reference.sqlite
uv run owlsperch reference browse --run-id smoke-luna-v2 --type spell --field /fields/school --equals '"Evocation"' --db owlsperch-data/pilot/reference.sqlite
```

The provisional evaluation intentionally returns exit 1 with a JSON report. It is
not a CLI failure: no bounded release has been certified. The source collection
and local experiment artifacts are prerequisites for reproducing these exact runs;
ordinary repository tests use synthetic fixtures and need no book files.

## Implementation verification

Task review found and fixed two integrity gaps: duplicate JSON keys silently
collapsed conflicting assertions, and source changes during extraction could
mislabel PDF lineage. Strict JSON decoding now rejects duplicate keys and
non-finite constants; streamed pre/post extraction hashes reject changed PDFs.
Both fixes have regression tests and passed scoped independent re-review.
The real extraction experiment was rerun with fresh `v2` run IDs after these fixes,
with the same results above rather than reused cached evaluations.

The full Python suite passed with 1,025 tests and one skip; the two existing
Starlette/httpx and anyio deprecation warnings remain. Ruff lint, formatting,
and mypy checks passed. No web files or production data were changed.
Final Astra review approved the bounded pilot with no remaining blockers and
independently reproduced both model reports; it did not visually inspect every
captured page.

## Model routing and next acceptance gates

Use Luna as the provisional first choice for bounded field extraction of the
tested kinds, with deterministic evidence/field checks and independent sampling.
Escalate ambiguous layouts, incomplete context, and failed attribution to Sol;
reserve Astra for architecture and adversarial review. Do not infer that Sol is
unnecessary for other layouts, or that either model's untested prose is reliable.
No token-usage or billing totals were exposed by the agent tool, so no dollar
savings or per-entry cost is claimed. No external paid API batch was launched.

Before broad publication, extend independent reference coverage to full entry
boundaries and prose, add held-out books and difficult types, benchmark OCR/layout
alternatives, implement reviewed entity/printing relations and errata patches,
then integrate accepted occurrences and evidence previews into the existing site.
The CLI pilot is a measurement foundation; the structured web experience remains
the required product, not a replacement PDF search interface.
