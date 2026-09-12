# owlsperch

A D&D 3.5e reference-data pipeline: it curates a manifest of the source PDFs,
extracts and segments their content, and eventually builds a queryable
SQLite/API/web reference site. See
`docs/specs/2026-09-12-dnd-reference-site-spec.md` for the full spec and
`docs/batches/2026-09-12-dnd-reference-site-batches.md` for the build order.

This batch (B1) delivers the repo scaffold, the `owlsperch` CLI, and the
curated book manifest (`manifest check` only -- everything else is a stub).

## Environment variables

- `OWLSPERCH_PDFS` -- directory the source PDFs are read from (never copied).
  Default: `~/D_D`.
- `OWLSPERCH_DATA` -- directory where extracted JSON, the SQLite build, OCR
  output, and rendered page images are written. Default: `~/owlsperch-data`.

## Setup

Install [uv](https://docs.astral.sh/uv/), then from the repo root:

```sh
uv sync
```

This repo root is a uv *workspace*: the root `pyproject.toml` has no
`[project]` table of its own (a "virtual" workspace root) and just declares
`pipeline` as a member. uv shares one lockfile/virtual environment across the
workspace, so the `owlsperch` console script that `pipeline/pyproject.toml`
declares is available from the repo root without `cd`-ing into `pipeline/`.

## Usage

```sh
uv run owlsperch manifest check
```

Validates `pipeline/manifest.yaml` against the files in `$OWLSPERCH_PDFS`
(default `~/D_D`) and prints counts by kind and by in-scope status (in-scope /
override / index / excluded / duplicate). An entry named in another entry's
`preferred_over` (a shadowed duplicate copy -- `preferred_over` accepts either
a single book_id or a list, for a book with more than one duplicate) is
reported as `duplicate` rather than as an independent in-scope book. Exits
non-zero and lists any file present in
the PDF directory but missing from the manifest, or any manifest entry whose
file is missing from the directory.

## Development

```sh
uv run ruff check .
uv run ruff format --check .
uv run mypy pipeline
uv run pytest pipeline/tests
```

Run a single test:

```sh
uv run pytest pipeline/tests/test_manifest.py::test_35_book_is_in_scope
```

One test is marked `@pytest.mark.corpus` and runs `manifest check` against
the real `$OWLSPERCH_PDFS`/`~/D_D` directory; it is skipped automatically
(not failed) when that directory isn't present, e.g. in CI.
