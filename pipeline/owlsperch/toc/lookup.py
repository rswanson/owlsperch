"""The read side of `toc/<book_id>.json` (design decision D10): `load_toc`
(used by `owlsperch.build_db.runner` to derive `category`/`chapter`/
`section` for every record) and `entry_for_page` (the deepest TOC entry
containing a given pdf page)."""

from __future__ import annotations

import json
from pathlib import Path

from owlsperch.toc.parser import Toc, TocEntry


def load_toc(data_dir: Path, book_id: str) -> Toc | None:
    """Loads `data_dir/toc/<book_id>.json`, or `None` if it doesn't exist
    (a book with records but no toc file -- `build_db` warns once per book
    and falls back to `uncategorized`/`None`, per D10)."""
    path = data_dir / "toc" / f"{book_id}.json"
    if not path.is_file():
        return None
    return Toc.model_validate(json.loads(path.read_text()))


def entry_for_page(toc: Toc, page: int) -> TocEntry | None:
    """The deepest entry containing `page`: greatest `level`, then greatest
    `pdf_page_start`. `None` for a page before the first entry, or for a
    `toc` whose every entry has an unresolvable `pdf_page_start` (D18)."""
    best: TocEntry | None = None
    best_key: tuple[int, int] | None = None
    for entry in toc.entries:
        start = entry.pdf_page_start
        if start is None:
            continue
        end = entry.pdf_page_end if entry.pdf_page_end is not None else start
        if not (start <= page <= end):
            continue
        key = (entry.level, start)
        if best_key is None or key > best_key:
            best = entry
            best_key = key
    return best
