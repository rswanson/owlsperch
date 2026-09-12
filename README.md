# owlsperch

A D&D 3.5e reference-data pipeline: it curates a manifest of the source PDFs,
extracts and segments their content, and eventually builds a queryable
SQLite/API/web reference site. See
`docs/specs/2026-09-12-dnd-reference-site-spec.md` for the full spec and
`docs/batches/2026-09-12-dnd-reference-site-batches.md` for the build order.

Batch B1 delivered the repo scaffold, the `owlsperch` CLI, and the curated
book manifest (`manifest check`). Batch B2 adds `text`, which extracts
column-repaired page text for text-layer (non-scanned) books.

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

```sh
uv run owlsperch text <book_id|all> [--force] [--pages A-B]
```

Extracts one column-repaired text file per PDF page for a text-layer
(non-scanned) book, via `pdftotext -bbox-layout` (poppler). For each book:

- A `scanned: true` book is refused (OCR extraction is a future batch, B15)
  -- for a single `book_id` this is a non-zero exit; inside `all` it is
  printed as a per-book skip line and the run continues.
- A book whose manifest status (see `manifest check`) is not `in_scope` or
  `override` is skipped. Override sources (errata, update, web_enhancement)
  are still extracted, since they need their own text.
- `all` runs every eligible book in the manifest and prints one summary
  line per book (pages written / skipped, headers removed, page numbers
  found, or the reason it was skipped/refused).
- An unknown `book_id` is a clear error, exit 1.

`--pages A-B` limits extraction to that inclusive PDF page range (1-based)
instead of the whole book -- useful for trying a few pages by hand. Header/
footer detection and dehyphenation only consider the pages in that range.

`--force` re-writes page files that already exist; by default, a page whose
output file is already present is left alone and counted as "skipped" (the
command is idempotent -- rerunning it does no extra work).

Output layout, under `$OWLSPERCH_DATA` (default `~/owlsperch-data`):

- `text/<book_id>/p0001.txt`, ... -- one file per PDF page (1-based,
  4-digit), each page's text reconstructed in reading order (columns left
  to right, top to bottom within a column), with running headers/footers
  removed and hyphenated line breaks rejoined. Blocks are separated by a
  blank line (a paragraph break).
- `text/<book_id>/pages.json` -- maps a PDF page index (as a string) to the
  printed page number found in that page's header/footer band, e.g.
  `{"120": 119}`. A page where no printed number was found is simply absent
  from the map.
- `text/<book_id>/p0001.meta.json`, ... -- one sidecar per page text file,
  listing per-paragraph stats for `segment`'s heading detection (the plain
  `.txt` carries no font information). A JSON list with one entry per
  output paragraph, in the same order as the blank-line-separated
  paragraphs in the matching `.txt`: `{"kind": "prose" | "table",
  "median_word_height": <float>, "max_word_height": <float>, "line_count":
  <int>}`. `median_word_height`/`max_word_height` are the glyph-height
  stats (in PDF points) across the paragraph's words; `line_count` is how
  many physical PDF lines made up the paragraph before dehyphenation
  joined them (or how many table rows, for a `"table"` paragraph).

```sh
uv run owlsperch segment <book_id|all> [--force] [--pages A-B]
```

Splits a book's already-extracted text (`text/<book_id>/`, see `text`
above) into candidate segments: spans of text tagged with a `kind_hint`
that a later extraction step (batch B4/B5) will validate against a
schema. Requires that book's text output to already exist -- and that
every page it processes has a `.meta.json` sidecar (see above); a page
missing one is a clear error naming `owlsperch text <book_id> --force`.
Segmentation runs over the whole book's text as one continuous stream (not
page by page), so a segment may span a page boundary.

Four pattern anchors are recognized, per spec 4.4:

- **spell** -- a short name line immediately followed by a school line (one
  of the eight schools of magic plus "Universal", optionally with a
  parenthesised subschool and/or a bracketed descriptor, e.g. "Evocation
  [Fire]").
- **stat_block** -- a "Size/Type:" or "Hit Dice:" line; the segment's
  `heading` is the nearest preceding heading or short line (the monster's
  name).
- **feat** -- a short name line (optionally with a bracketed type, e.g.
  "Power Attack [General]") followed within two paragraphs by a line
  starting "Prerequisite" or "Benefit".
- **table** -- a "Table N-M:" caption; the segment runs through the
  tab-separated rows that follow, plus any footnote lines starting with a
  digit.

Everything else becomes a `rules_section` segment, split further at
headings -- a heading is a single-line paragraph whose `median_word_height`
is at least 1.15x the book's body-text median, or an all-uppercase line of
at most 8 words. Segments are never empty and every page with any text
lands in at least one segment (front matter before the first heading
included); `owlsperch segment` warns on stderr if that ever fails to hold.

`--force` and `--pages A-B` behave as they do for `text`: existing segment
files are kept unless `--force` is given, and `--pages` limits which pages
are read from `text/<book_id>/`.

Output layout, under `$OWLSPERCH_DATA`:

- `segments/<book_id>/<seg_id>.json` -- one file per segment. `seg_id` is
  `<book_id>-p<NNNN>-<NN>`: the segment's first PDF page, then a
  per-first-page ordinal, both assigned in stream order so rerunning
  produces identical ids. Fields: `book_id`, `pages` (every PDF page index
  the segment spans), `printed_pages` (same length and order as `pages`,
  from `pages.json`; `null` for a page with no detected printed number),
  `kind_hint` (`spell` | `stat_block` | `feat` | `table` | `rules_section`),
  `heading` (the section heading the segment falls under, or the anchor's
  name), `text`, `status` (`"pending"`), `tier` (`"haiku"`), `attempts`
  (`[]`), `created_at` (an ISO timestamp).

`segment all` iterates every in-scope/override book that has a `text/`
directory, printing one summary line per book (counts by `kind_hint`, or
why it was skipped) -- a book with no `text/` directory yet is skipped with
a message pointing at `owlsperch text`.

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

Some tests are marked `@pytest.mark.corpus` and run against the real
`$OWLSPERCH_PDFS`/`~/D_D` directory (`manifest check`; `text phb1` over
pages 118-122; and `segment phb1` over pages 195-230, which needs
`pdftotext` on `PATH`); they are skipped automatically (not failed) when
the corpus or `pdftotext` isn't present, e.g. in CI.
