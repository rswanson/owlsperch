"""Orchestration for `owlsperch text <book_id|all>`.

Per book:

1. Look the book up in the manifest. Unknown book_id is a hard error.
   A book whose `status_for` (see `owlsperch.manifest`) is not `in_scope` or
   `override` is skipped -- override sources (errata, update,
   web_enhancement) are still extracted, since they must be segmented in
   their own right. A `scanned: true` book is refused: OCR extraction is a
   future batch (B15), not this one.
2. Run `pdftotext -bbox-layout` (poppler) over the whole PDF, or the
   `--pages A-B` range, into a temporary XHTML file, and parse it
   (`owlsperch.text.bbox`).
3. Order each page's blocks into reading order (`owlsperch.text.columns`),
   which also detects table groups (a table's columns, each emitted by
   `pdftotext` as its own narrow block) and pulls them out to be rendered
   row-wise. Vertical/rotated blocks dropped by that step (chapter tabs,
   but also incidentally image credits) are counted, not just discarded,
   so the loss shows up in the summary line (`vertical_blocks_excluded`).
4. Across every page processed this run, find the running headers/footers
   and the printed page numbers, and dehyphenate line-wrapped words
   (`owlsperch.text.cleanup`) using a word set built from the book's own
   (header/footer-stripped) text plus the bundled word list. Table-group
   rows are never dehyphenated (their cells are single lines already) but
   do contribute their words to that word set.
5. Write one `text/<book_id>/p{NNNN}.txt` per page: each block becomes one
   paragraph (dehyphenated, word-joined) and each table group becomes one
   paragraph of tab-separated rows (one row per line), all separated by a
   blank line. Alongside it, write `text/<book_id>/p{NNNN}.meta.json` (B3):
   a list with one entry per output paragraph, in order, giving
   `{"kind": "prose"|"table", "median_word_height", "max_word_height",
   "line_count"}` -- the word-glyph-height stats and original line count
   `owlsperch.segment` uses for heading detection, since the .txt format
   itself carries no font information. Pages whose file already exists are
   skipped unless `--force` (the .meta.json sidecar is skipped along with
   it), and the printed page numbers found are merged into
   `text/<book_id>/pages.json` (a page missing a detected number is simply
   absent from the map).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from owlsperch.fsutil import atomic_write_text
from owlsperch.manifest import (
    ManifestEntry,
    default_manifest_path,
    default_pdf_dir,
    load_manifest,
    status_for,
)
from owlsperch.text.bbox import Block, Page, parse_bbox_xhtml
from owlsperch.text.cleanup import (
    BandedLine,
    band_for_line,
    dehyphenate_lines,
    find_running_keys,
    load_wordlist,
    normalize_for_repetition,
    standalone_page_number,
    strip_word_punctuation,
)
from owlsperch.text.columns import TableGroup, is_vertical_block, order_blocks

#: The batch that will add OCR support for scanned books.
OCR_BATCH = "B15"

#: Homebrew's poppler package provides the `pdftotext` binary this pipeline
#: shells out to.
_PDFTOTEXT_INSTALL_HINT = (
    "pdftotext (poppler) is not installed or not on PATH -- "
    "install it with `brew install poppler` (Homebrew) or your platform's "
    "poppler-utils package"
)


class TextExtractionError(Exception):
    """A hard failure extracting one book (e.g. `pdftotext` itself failed)."""


def default_data_dir() -> Path:
    return Path(os.environ.get("OWLSPERCH_DATA", str(Path.home() / "owlsperch-data")))


def run_pdftotext(
    pdf_path: Path,
    out_html_path: Path,
    first_page: int | None = None,
    last_page: int | None = None,
) -> None:
    cmd = ["pdftotext", "-bbox-layout"]
    if first_page is not None:
        cmd += ["-f", str(first_page)]
    if last_page is not None:
        cmd += ["-l", str(last_page)]
    cmd += [str(pdf_path), str(out_html_path)]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise TextExtractionError(_PDFTOTEXT_INSTALL_HINT) from exc
    if result.returncode != 0:
        raise TextExtractionError(
            f"pdftotext failed (exit {result.returncode}) for {pdf_path}: {result.stderr.strip()}"
        )


class Outcome(Enum):
    """The structured result of processing one book, so exit-code logic
    doesn't need to pattern-match message text (see MINOR 8 in review)."""

    OK = "ok"
    SKIPPED = "skipped"
    REFUSED = "refused"
    ERROR = "error"


@dataclass
class BookSummary:
    book_id: str
    pages_written: int = 0
    pages_skipped: int = 0
    headers_removed: int = 0
    page_numbers_found: int = 0
    vertical_blocks_excluded: int = 0
    note: str = ""
    outcome: Outcome = Outcome.OK

    def render(self) -> str:
        if self.note:
            return f"{self.book_id}: {self.note}"
        return (
            f"{self.book_id}: {self.pages_written} pages written, "
            f"{self.pages_skipped} pages skipped, "
            f"{self.headers_removed} headers removed, "
            f"{self.page_numbers_found} page numbers found, "
            f"{self.vertical_blocks_excluded} vertical blocks excluded"
        )


def _tokenize_words(text: str) -> list[str]:
    cleaned = [strip_word_punctuation(tok).lower() for tok in text.split()]
    return [tok for tok in cleaned if tok]


def extract_book(
    entry: ManifestEntry,
    *,
    pdf_dir: Path,
    data_dir: Path,
    force: bool = False,
    page_range: tuple[int, int] | None = None,
) -> BookSummary:
    summary = BookSummary(book_id=entry.book_id)
    pdf_path = pdf_dir / entry.file
    if not pdf_path.is_file():
        raise TextExtractionError(f"PDF file not found: {pdf_path}")

    out_dir = data_dir / "text" / entry.book_id
    out_dir.mkdir(parents=True, exist_ok=True)

    first_page, last_page = page_range if page_range is not None else (None, None)
    start_index = first_page if first_page is not None else 1

    with tempfile.TemporaryDirectory() as tmp_dir:
        html_path = Path(tmp_dir) / "bbox.html"
        run_pdftotext(pdf_path, html_path, first_page, last_page)
        pages = parse_bbox_xhtml(html_path)

    return _process_pages(
        pages, start_index=start_index, out_dir=out_dir, force=force, summary=summary
    )


@dataclass
class _PageUnit:
    """One output paragraph unit for a page: either `kind="prose"` (a
    block's kept lines, to be dehyphenated and joined into one paragraph),
    or `kind="table"` (a `TableGroup`'s already-composed row texts, one per
    output line, joined with newlines but never dehyphenated across cells).

    `median_word_height`/`max_word_height` are the glyph-height stats (see
    `p{NNNN}.meta.json`, batch B3) for the words that made up this unit --
    computed here, while word bboxes are still available, rather than
    recovered later from plain paragraph text."""

    kind: Literal["prose", "table"]
    lines: list[str] = field(default_factory=list)
    median_word_height: float = 0.0
    max_word_height: float = 0.0


def _process_pages(
    pages: list[Page],
    *,
    start_index: int,
    out_dir: Path,
    force: bool,
    summary: BookSummary,
) -> BookSummary:
    ordered_blocks_per_page: list[list[Block | TableGroup]] = [order_blocks(page) for page in pages]

    for page in pages:
        summary.vertical_blocks_excluded += sum(
            1 for block in page.blocks if is_vertical_block(block)
        )

    banded_lines: list[BandedLine] = []
    for offset, (page, ordered) in enumerate(zip(pages, ordered_blocks_per_page, strict=True)):
        pdf_index = start_index + offset
        for item in ordered:
            if isinstance(item, TableGroup):
                continue
            for line in item.lines:
                if not line.text.strip():
                    continue
                band = band_for_line(line, page.height)
                banded_lines.append(BandedLine(pdf_index, band, line.text))

    running_keys = find_running_keys(banded_lines, page_count=len(pages))

    page_numbers: dict[int, int] = {}
    for banded_line in banded_lines:
        if banded_line.band is None or banded_line.page_index in page_numbers:
            continue
        number = standalone_page_number(banded_line.text)
        if number is not None:
            page_numbers[banded_line.page_index] = number

    wordlist_words = load_wordlist()
    book_words: set[str] = set()
    units_per_page: list[list[_PageUnit]] = []

    for page, ordered in zip(pages, ordered_blocks_per_page, strict=True):
        page_units: list[_PageUnit] = []
        for item in ordered:
            if isinstance(item, TableGroup):
                row_texts = [row.text for row in item.rows if row.text.strip()]
                for row in item.rows:
                    for cell in row.cells:
                        book_words.update(_tokenize_words(cell))
                if row_texts:
                    page_units.append(
                        _PageUnit(
                            kind="table",
                            lines=row_texts,
                            median_word_height=item.median_word_height,
                            max_word_height=item.max_word_height,
                        )
                    )
                continue

            kept_lines: list[str] = []
            kept_word_heights: list[float] = []
            for line in item.lines:
                text = line.text
                if not text.strip():
                    continue
                band = band_for_line(line, page.height)
                if band is not None:
                    # A bare page number is always dropped as a page number,
                    # checked before the running-header/footer rule -- its
                    # digits-stripped normalization is the empty string, so
                    # it would otherwise also match the running-line key
                    # that most digit-only footers share and get miscounted
                    # as a removed *header*, not a page number.
                    if standalone_page_number(text) is not None:
                        continue
                    key = (band, normalize_for_repetition(text))
                    if key in running_keys:
                        summary.headers_removed += 1
                        continue
                kept_lines.append(text)
                kept_word_heights.extend(w.y_max - w.y_min for w in line.words if w.text)
                book_words.update(_tokenize_words(text))
            if kept_lines:
                sorted_heights = sorted(kept_word_heights)
                median_h = sorted_heights[len(sorted_heights) // 2] if sorted_heights else 0.0
                max_h = sorted_heights[-1] if sorted_heights else 0.0
                page_units.append(
                    _PageUnit(
                        kind="prose",
                        lines=kept_lines,
                        median_word_height=median_h,
                        max_word_height=max_h,
                    )
                )
        units_per_page.append(page_units)

    known_words = book_words | wordlist_words

    for offset, page_units in enumerate(units_per_page):
        pdf_index = start_index + offset
        out_path = out_dir / f"p{pdf_index:04d}.txt"
        meta_path = out_dir / f"p{pdf_index:04d}.meta.json"
        if out_path.exists() and not force:
            summary.pages_skipped += 1
            continue
        rendered = [
            (
                unit,
                "\n".join(unit.lines)
                if unit.kind == "table"
                else dehyphenate_lines(unit.lines, known_words),
            )
            for unit in page_units
        ]
        # Keep the meta-sidecar entries aligned 1:1 with the paragraphs that
        # actually make it into the .txt file -- a unit whose rendered text
        # is empty (whitespace-only lines) is dropped from both together.
        rendered = [(unit, text) for unit, text in rendered if text]
        paragraphs = [text for _, text in rendered]
        content = "\n\n".join(paragraphs)
        atomic_write_text(out_path, content + "\n" if content else "")
        meta = [
            {
                "kind": unit.kind,
                "median_word_height": unit.median_word_height,
                "max_word_height": unit.max_word_height,
                "line_count": len(unit.lines),
            }
            for unit, _ in rendered
        ]
        atomic_write_text(meta_path, json.dumps(meta, indent=2) + "\n")
        summary.pages_written += 1

    summary.page_numbers_found = len(page_numbers)
    _merge_pages_json(out_dir, page_numbers)
    return summary


def _merge_pages_json(out_dir: Path, page_numbers: dict[int, int]) -> None:
    if not page_numbers:
        return
    pages_json_path = out_dir / "pages.json"
    existing: dict[str, int] = {}
    if pages_json_path.exists():
        existing = json.loads(pages_json_path.read_text())
    existing.update({str(pdf_index): number for pdf_index, number in page_numbers.items()})
    ordered = {k: existing[k] for k in sorted(existing, key=int)}
    atomic_write_text(pages_json_path, json.dumps(ordered, indent=2) + "\n")


def _process_one(
    entry: ManifestEntry,
    entries: list[ManifestEntry],
    *,
    pdf_dir: Path,
    data_dir: Path,
    force: bool,
    page_range: tuple[int, int] | None,
) -> BookSummary:
    status = status_for(entry, entries)
    if status not in ("in_scope", "override"):
        return BookSummary(entry.book_id, note=f"skipped ({status})", outcome=Outcome.SKIPPED)
    if entry.scanned:
        return BookSummary(
            entry.book_id,
            note=f"refused: scanned book -- OCR support arrives in batch {OCR_BATCH}",
            outcome=Outcome.REFUSED,
        )
    try:
        return extract_book(
            entry, pdf_dir=pdf_dir, data_dir=data_dir, force=force, page_range=page_range
        )
    except TextExtractionError as exc:
        return BookSummary(entry.book_id, note=f"error: {exc}", outcome=Outcome.ERROR)


def run_text(
    book_id: str,
    *,
    pdf_dir: Path | None = None,
    data_dir: Path | None = None,
    manifest_path: Path | None = None,
    force: bool = False,
    page_range: tuple[int, int] | None = None,
    out: Any = None,
) -> int:
    out = out if out is not None else sys.stdout
    pdf_dir = pdf_dir if pdf_dir is not None else default_pdf_dir()
    data_dir = data_dir if data_dir is not None else default_data_dir()
    manifest_path = manifest_path if manifest_path is not None else default_manifest_path()

    entries = load_manifest(manifest_path)
    by_id = {e.book_id: e for e in entries}

    if book_id == "all":
        exit_code = 0
        for entry in entries:
            summary = _process_one(
                entry,
                entries,
                pdf_dir=pdf_dir,
                data_dir=data_dir,
                force=force,
                page_range=page_range,
            )
            print(summary.render(), file=out)
            if summary.outcome is Outcome.ERROR:
                exit_code = 1
        return exit_code

    resolved_entry = by_id.get(book_id)
    if resolved_entry is None:
        print(f"error: unknown book_id '{book_id}'", file=sys.stderr)
        return 1

    summary = _process_one(
        resolved_entry,
        entries,
        pdf_dir=pdf_dir,
        data_dir=data_dir,
        force=force,
        page_range=page_range,
    )
    print(summary.render(), file=out)
    return 1 if summary.outcome in (Outcome.REFUSED, Outcome.ERROR) else 0
