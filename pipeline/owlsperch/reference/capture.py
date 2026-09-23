"""Immutable bounded Poppler source capture with original XHTML retained."""

from __future__ import annotations

import hashlib
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from owlsperch.reference.evaluate import DocumentError
from owlsperch.text.bbox import Block, parse_bbox_xhtml

CAPTURE_FORMAT_VERSION = 1


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _box(item: Any) -> list[float]:
    return [item.x_min, item.y_min, item.x_max, item.y_max]


def _block_text(block: Block) -> str:
    return "\n".join(line.text for line in block.lines)


def run_pdftotext(pdf_path: Path, output: Path, first: int, last: int) -> str:
    """Run the same Poppler bbox extraction as the legacy runner, retaining warnings."""
    try:
        result = subprocess.run(
            [
                "pdftotext",
                "-bbox-layout",
                "-f",
                str(first),
                "-l",
                str(last),
                str(pdf_path),
                str(output),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
    except FileNotFoundError as exc:
        raise DocumentError("pdftotext (poppler) is required for reference capture") from exc
    except subprocess.TimeoutExpired as exc:
        raise DocumentError(f"pdftotext timed out for {pdf_path}") from exc
    if result.returncode:
        raise DocumentError(f"pdftotext failed for {pdf_path}: {result.stderr.strip()}")
    return result.stderr.strip()


def capture_source(
    book_id: str,
    pdf_path: Path,
    page_range: tuple[int, int],
    *,
    extractor: str,
    source_page_count: int | None = None,
) -> tuple[dict[str, Any], bytes]:
    """Capture an exact PDF range; never persist a clamped/partial extraction."""
    first, last = page_range
    if first < 1 or last < first:
        raise DocumentError("pages must satisfy 1 <= first <= last")
    if not pdf_path.is_file():
        raise DocumentError(f"PDF not found: {pdf_path}")
    if source_page_count is not None and last > source_page_count:
        raise DocumentError(f"pages {first}-{last} exceed PDF page count {source_page_count}")
    pdf_sha = _sha_file(pdf_path)
    with tempfile.TemporaryDirectory(prefix="owlsperch-reference-") as temp:
        output = Path(temp) / "bbox.xhtml"
        warnings = run_pdftotext(pdf_path, output, first, last)
        artifact = output.read_bytes()
        try:
            pages = parse_bbox_xhtml(output)
        except (ET.ParseError, KeyError, ValueError) as exc:
            raise DocumentError(f"invalid Poppler bbox output for {pdf_path}: {exc}") from exc
    if len(pages) != last - first + 1:
        raise DocumentError(
            f"pages {first}-{last}: Poppler returned {len(pages)} pages; refusing partial capture"
        )
    if _sha_file(pdf_path) != pdf_sha:
        raise DocumentError(f"PDF changed during capture: {pdf_path}")
    identity = (
        f"{CAPTURE_FORMAT_VERSION}\0{book_id}\0{pdf_sha}\0{extractor}\0"
        f"{first}-{last}\0{_sha(artifact)}"
    ).encode()
    snapshot_id = _sha(identity)
    page_docs: list[dict[str, Any]] = []
    for page_offset, page in enumerate(pages):
        block_docs: list[dict[str, Any]] = []
        for block_index, block in enumerate(page.blocks):
            words = [
                {"text": word.text, "bbox": _box(word)}
                for line in block.lines
                for word in line.words
            ]
            block_docs.append(
                {
                    "block_id": f"{snapshot_id[:20]}-p{first + page_offset:04d}-b{block_index:04d}",
                    "text": _block_text(block),
                    "bbox": _box(block),
                    "words": words,
                }
            )
        page_docs.append(
            {
                "pdf_page_index": first + page_offset,
                "width": page.width,
                "height": page.height,
                "text_empty": not any(block["text"].strip() for block in block_docs),
                "blocks": block_docs,
            }
        )
    return (
        {
            "snapshot_id": snapshot_id,
            "capture_format_version": CAPTURE_FORMAT_VERSION,
            "book_id": book_id,
            "source_sha256": pdf_sha,
            "extractor": extractor,
            "artifact_sha256": _sha(artifact),
            "page_range": [first, last],
            "source_page_count": source_page_count,
            "extractor_warnings": warnings or "",
            "pages": page_docs,
        },
        artifact,
    )
