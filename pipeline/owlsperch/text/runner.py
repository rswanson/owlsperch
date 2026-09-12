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
3. Order each page's blocks into reading order (`owlsperch.text.columns`).
4. Across every page processed this run, find the running headers/footers
   and the printed page numbers, and dehyphenate line-wrapped words
   (`owlsperch.text.cleanup`) using a word set built from the book's own
   (header/footer-stripped) text plus the bundled word list.
5. Write one `text/<book_id>/p{NNNN}.txt` per page (blocks joined with a
   blank line as the paragraph break), skipping pages whose file already
   exists unless `--force`, and merge the printed page numbers found into
   `text/<book_id>/pages.json` (a page missing a detected number is simply
   absent from the map).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
)
from owlsperch.text.columns import order_blocks

#: The batch that will add OCR support for scanned books.
OCR_BATCH = "B15"

_WORD_PUNCT_RE = re.compile(r"^[^A-Za-z0-9]+|[^A-Za-z0-9]+$")


class TextExtractionError(Exception):
    """A hard failure extracting one book (e.g. `pdftotext` itself failed)."""


def default_data_dir() -> Path:
    return Path(os.environ.get("OWLSPERCH_DATA", str(Path.home() / "owlsperch-data")))


def strip_word_punctuation(word: str) -> str:
    """Strip leading/trailing non-alphanumeric characters (punctuation
    attached to a word by the PDF's word segmentation), keeping internal
    characters like a mid-word hyphen or apostrophe."""
    return _WORD_PUNCT_RE.sub("", word)


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
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise TextExtractionError(
            f"pdftotext failed (exit {result.returncode}) for {pdf_path}: {result.stderr.strip()}"
        )


@dataclass
class BookSummary:
    book_id: str
    pages_written: int = 0
    pages_skipped: int = 0
    headers_removed: int = 0
    page_numbers_found: int = 0
    note: str = ""
    hard_error: bool = False

    @property
    def is_scanned_refusal(self) -> bool:
        return self.note.startswith("refused:")

    def render(self) -> str:
        if self.note:
            return f"{self.book_id}: {self.note}"
        return (
            f"{self.book_id}: {self.pages_written} pages written, "
            f"{self.pages_skipped} pages skipped, "
            f"{self.headers_removed} headers removed, "
            f"{self.page_numbers_found} page numbers found"
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


def _process_pages(
    pages: list[Page],
    *,
    start_index: int,
    out_dir: Path,
    force: bool,
    summary: BookSummary,
) -> BookSummary:
    ordered_blocks_per_page: list[list[Block]] = [order_blocks(page) for page in pages]

    banded_lines: list[BandedLine] = []
    for offset, (page, ordered) in enumerate(zip(pages, ordered_blocks_per_page, strict=True)):
        pdf_index = start_index + offset
        for block in ordered:
            for line in block.lines:
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
    filtered_blocks_per_page: list[list[list[str]]] = []

    for offset, (page, ordered) in enumerate(zip(pages, ordered_blocks_per_page, strict=True)):
        pdf_index = start_index + offset
        page_blocks: list[list[str]] = []
        for block in ordered:
            kept_lines: list[str] = []
            for line in block.lines:
                text = line.text
                if not text.strip():
                    continue
                band = band_for_line(line, page.height)
                if band is not None:
                    key = (band, normalize_for_repetition(text))
                    if key in running_keys:
                        summary.headers_removed += 1
                        continue
                    if standalone_page_number(text) is not None:
                        continue
                kept_lines.append(text)
                book_words.update(_tokenize_words(text))
            if kept_lines:
                page_blocks.append(kept_lines)
        filtered_blocks_per_page.append(page_blocks)
        _ = pdf_index  # only needed for symmetry/debugging

    known_words = book_words | wordlist_words

    for offset, page_blocks in enumerate(filtered_blocks_per_page):
        pdf_index = start_index + offset
        out_path = out_dir / f"p{pdf_index:04d}.txt"
        if out_path.exists() and not force:
            summary.pages_skipped += 1
            continue
        paragraphs = [dehyphenate_lines(lines, known_words) for lines in page_blocks]
        paragraphs = [p for p in paragraphs if p]
        content = "\n\n".join(paragraphs)
        out_path.write_text(content + "\n" if content else "")
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
    pages_json_path.write_text(json.dumps(ordered, indent=2) + "\n")


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
        return BookSummary(entry.book_id, note=f"skipped ({status})")
    if entry.scanned:
        return BookSummary(
            entry.book_id,
            note=f"refused: scanned book -- OCR support arrives in batch {OCR_BATCH}",
            hard_error=True,
        )
    try:
        return extract_book(
            entry, pdf_dir=pdf_dir, data_dir=data_dir, force=force, page_range=page_range
        )
    except TextExtractionError as exc:
        return BookSummary(entry.book_id, note=f"error: {exc}", hard_error=True)


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
            if summary.hard_error and not summary.is_scanned_refusal:
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
    return 1 if summary.hard_error else 0
