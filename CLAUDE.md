# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Current state

`owlsperch` is a Python (uv-managed) pipeline. As of batch B2 it has: the
`owlsperch` CLI and package (`pipeline/owlsperch/`), the curated PDF manifest
(`pipeline/manifest.yaml`), `manifest check`, `text` (column-repaired
per-page text extraction for text-layer books), and CI. Everything else is a
future-batch stub (`segment`, `validate`, `build-db`, `check-completeness`,
`coverage`, `schema review`, `sample`).

### Commands

Run from the repo root (a uv workspace with `pipeline` as its only member --
see the comment in the root `pyproject.toml`):

```sh
uv sync                              # install deps
uv run owlsperch manifest check
uv run owlsperch text <book_id|all> [--force] [--pages A-B]
uv run ruff check .
uv run ruff format --check .
uv run mypy pipeline
uv run pytest pipeline/tests         # run all tests
uv run pytest pipeline/tests/test_manifest.py::test_35_book_is_in_scope  # single test
```

Some tests are marked `@pytest.mark.corpus`: they run `manifest check`, and
`text` on a page range of the real Player's Handbook (book_id `phb1`),
against the real `$OWLSPERCH_PDFS`/`~/D_D` directory and `pdftotext`
(poppler); they are skipped (not failed) when the corpus or `pdftotext`
isn't present.

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
  `pdftotext -bbox-layout` XHTML; `columns.py` reconstructs reading order
  (column clustering, wide-block column breaks, rotated marginalia
  dropped); `cleanup.py` removes running headers/footers, detects printed
  page numbers, and rejoins hyphenated line breaks; `runner.py` orchestrates
  the `pdftotext` subprocess and writes `text/<book_id>/p{NNNN}.txt` +
  `pages.json` under `$OWLSPERCH_DATA`. `wordlist.txt` is the bundled
  fallback word list used by dehyphenation.

See `docs/specs/2026-09-12-dnd-reference-site-spec.md` (especially "Scope
boundaries" and sections 4.1-4.4) for the full design, and
`docs/batches/2026-09-12-dnd-reference-site-batches.md` for the batch/build
order this and future work follows.

## Keeping this file useful

When the project takes shape, replace the "Current state" section with:

- The commands to build, lint, run tests, and run a single test.
- The high-level architecture: the main entry points, how the major modules relate, and any non-obvious conventions that require reading several files to understand.
