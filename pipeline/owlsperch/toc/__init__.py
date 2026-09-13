"""`owlsperch toc <book_id|all>` (batch B10b, spec 4.6/4.8/4.9): parses a
text-layer book's own table of contents into `toc/<book_id>.json` -- a
chapter/section tree with page spans and a player-facing `category` per
entry -- so `build-db` (batch B10b's `owlsperch.build_db.runner` changes)
can derive `category`/`chapter`/`section` for every record instead of the
flat, extractor-guessed `rules_section.fields.chapter`.

- `parser.py` -- contents-page detection, dotted-leader entry parsing,
  level/chapter-nesting assignment, and pdf page-span derivation
  (`parse_book_toc`), plus the `Toc`/`TocEntry` pydantic models the JSON
  file round-trips through.
- `categories.py` -- the book-agnostic category rule table (generic title
  patterns, per-book overrides) and category resolution
  (`resolve_chapter_category`, `resolve_section_category`).
- `lookup.py` -- `load_toc`/`entry_for_page`, the read side `build_db` uses.
- `runner.py` -- `run_toc`, the CLI orchestration that writes
  `toc/<book_id>.json` and prints the per-book report.
"""

from __future__ import annotations
