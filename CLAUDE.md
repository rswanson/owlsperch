# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Current state

`owlsperch` is a Python (uv-managed) pipeline. As of batch B1 it has: the
`owlsperch` CLI and package (`pipeline/owlsperch/`), the curated PDF manifest
(`pipeline/manifest.yaml`), and CI. Everything except `manifest check` is a
future-batch stub (`text`, `segment`, `validate`, `build-db`,
`check-completeness`, `coverage`, `schema review`, `sample`).

### Commands

Run from the repo root (a uv workspace with `pipeline` as its only member --
see the comment in the root `pyproject.toml`):

```sh
uv sync                              # install deps
uv run owlsperch manifest check      # the only implemented subcommand
uv run ruff check .
uv run ruff format --check .
uv run mypy pipeline
uv run pytest pipeline/tests         # run all tests
uv run pytest pipeline/tests/test_manifest.py::test_35_book_is_in_scope  # single test
```

One test is marked `@pytest.mark.corpus`: it runs `manifest check` against
the real `$OWLSPERCH_PDFS`/`~/D_D` directory and is skipped (not failed) when
that directory isn't present.

### Architecture

- `pipeline/owlsperch/manifest.py` -- the `ManifestEntry` pydantic schema,
  YAML loading/validation (raises `ManifestError` naming the offending
  book_id and field), in-scope/status derivation, and the `manifest check`
  report.
- `pipeline/owlsperch/cli.py` / `__main__.py` -- the `owlsperch` argparse CLI.
- `pipeline/manifest.yaml` -- the curated manifest: one entry per file in
  `$OWLSPERCH_PDFS`, with judgment calls marked by a plain `#` comment and
  genuinely uncertain ones marked `# REVIEW:`.

See `docs/specs/2026-09-12-dnd-reference-site-spec.md` (especially "Scope
boundaries" and sections 4.1-4.4) for the full design, and
`docs/batches/2026-09-12-dnd-reference-site-batches.md` for the batch/build
order this and future work follows.

## Keeping this file useful

When the project takes shape, replace the "Current state" section with:

- The commands to build, lint, run tests, and run a single test.
- The high-level architecture: the main entry points, how the major modules relate, and any non-obvious conventions that require reading several files to understand.
