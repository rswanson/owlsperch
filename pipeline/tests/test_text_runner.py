"""End-to-end tests for `owlsperch.text.runner` (and `owlsperch text` via
`run_text`), against hand-written `pdftotext -bbox-layout` XHTML fixtures.

`pdftotext` itself is never invoked: `runner.run_pdftotext` is monkeypatched
to write a fixture file to the requested path instead, per the batch's
"mock the pdftotext invocation" instruction.

Covers acceptance criteria 6(b)-(f): header/footer removal across pages,
dehyphenation (both branches), page-number detection into pages.json,
idempotency/--force, and scanned/non-in-scope handling via a temp manifest.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from owlsperch.manifest import ManifestEntry, load_manifest
from owlsperch.text import runner as runner_mod
from owlsperch.text.runner import extract_book, run_text

_NS = 'xmlns="http://www.w3.org/1999/xhtml"'
_PAGE_WIDTH = 612.0
_PAGE_HEIGHT = 792.0


def _words_xml(x_start: float, y_min: float, y_max: float, text: str) -> str:
    words = []
    x = x_start
    for token in text.split(" "):
        x_max = x + max(len(token) * 6.0, 10.0)
        words.append(
            f'<word xMin="{x}" yMin="{y_min}" xMax="{x_max}" yMax="{y_max}">{token}</word>'
        )
        x = x_max + 4.0
    return "".join(words)


def _line_xml(x_min: float, y_min: float, x_max: float, y_max: float, text: str) -> str:
    return (
        f'<line xMin="{x_min}" yMin="{y_min}" xMax="{x_max}" yMax="{y_max}">'
        + _words_xml(x_min, y_min, y_max, text)
        + "</line>"
    )


def _block_xml(x_min: float, y_min: float, x_max: float, lines: list[str]) -> str:
    line_height = 12.0
    line_xmls = []
    y = y_min
    for line_text in lines:
        line_xmls.append(_line_xml(x_min, y, x_max, y + line_height, line_text))
        y += line_height + 2.0
    y_max = y - 2.0
    lines_xml = "".join(line_xmls)
    return f'<block xMin="{x_min}" yMin="{y_min}" xMax="{x_max}" yMax="{y_max}">{lines_xml}</block>'


def _page_xml(
    header_text: str | None,
    footer_number: int | None,
    left_lines: list[str],
    right_lines: list[str],
) -> str:
    blocks = []
    if header_text is not None:
        blocks.append(_block_xml(34, 20, 400, [header_text]))
    blocks.append(_block_xml(34, 100, 290, left_lines))
    blocks.append(_block_xml(300, 100, 580, right_lines))
    if footer_number is not None:
        blocks.append(_block_xml(280, 750, 330, [str(footer_number)]))
    blocks_xml = "".join(blocks)
    return f'<page width="{_PAGE_WIDTH}" height="{_PAGE_HEIGHT}"><flow>{blocks_xml}</flow></page>'


def _doc_xml(pages: list[str]) -> str:
    return f"<html {_NS}><body><doc>{''.join(pages)}</doc></body></html>"


def _fake_run_pdftotext(content: str) -> Callable[[Path, Path, int | None, int | None], None]:
    def _fake(
        pdf_path: Path,
        out_html_path: Path,
        first_page: int | None = None,
        last_page: int | None = None,
    ) -> None:
        out_html_path.write_text(content)

    return _fake


@pytest.fixture
def book_dirs(tmp_path: Path) -> tuple[Path, Path]:
    pdf_dir = tmp_path / "pdfs"
    data_dir = tmp_path / "data"
    pdf_dir.mkdir()
    data_dir.mkdir()
    return pdf_dir, data_dir


def _manifest_entry_for(book_id: str) -> ManifestEntry:
    return ManifestEntry(
        book_id=book_id,
        title="Test Book",
        file=f"{book_id}.pdf",
        edition="3.5",
        kind="rulebook",
    )


# ---------------------------------------------------------------------------
# (b) header/footer removal, (c) dehyphenation, (d) page numbers -- combined
# on one 5-page fixture, since they interact (header/footer detection needs
# several pages; dehyphenation needs the book's own word occurrences).
# ---------------------------------------------------------------------------


def _combined_fixture() -> str:
    pages = []
    # Page 1: establishes "composite" as a known whole word in the book.
    pages.append(
        _page_xml(
            "PLAYERS HANDBOOK",
            100,
            ["This composite design is common.", "Second line of page one."],
            ["Right column page one text here."],
        )
    )
    # Page 2: same running header/footer pattern.
    pages.append(
        _page_xml(
            "PLAYERS HANDBOOK",
            101,
            ["Left column page two text."],
            ["Right column page two text."],
        )
    )
    # Page 3: hyphenated break of a word ("composite") that IS known.
    pages.append(
        _page_xml(
            "PLAYERS HANDBOOK",
            102,
            ["The item is a com-", "posite bow of power."],
            ["Right column page three."],
        )
    )
    # Page 4: hyphenated break of a word that is NOT known (kept with hyphen).
    pages.append(
        _page_xml(
            "PLAYERS HANDBOOK",
            103,
            ["This is zim-", "zowie magic here."],
            ["Right column page four."],
        )
    )
    # Page 5: a one-off, non-recurring header -- must NOT be removed.
    pages.append(
        _page_xml(
            "SPECIAL SECTION",
            104,
            ["Left column page five."],
            ["Right column page five."],
        )
    )
    return _doc_xml(pages)


def test_running_header_removed_but_unique_header_kept(
    monkeypatch: pytest.MonkeyPatch, book_dirs: tuple[Path, Path]
) -> None:
    pdf_dir, data_dir = book_dirs
    (pdf_dir / "book.pdf").write_text("stub")
    monkeypatch.setattr(runner_mod, "run_pdftotext", _fake_run_pdftotext(_combined_fixture()))

    entry = _manifest_entry_for("book")
    summary = extract_book(entry, pdf_dir=pdf_dir, data_dir=data_dir)

    assert summary.pages_written == 5
    assert summary.headers_removed > 0

    out_dir = data_dir / "text" / "book"
    for i in range(1, 5):
        text = (out_dir / f"p{i:04d}.txt").read_text()
        assert "PLAYERS HANDBOOK" not in text

    page5_text = (out_dir / "p0005.txt").read_text()
    assert "SPECIAL SECTION" in page5_text


def test_dehyphenation_known_word_joined_without_hyphen(
    monkeypatch: pytest.MonkeyPatch, book_dirs: tuple[Path, Path]
) -> None:
    pdf_dir, data_dir = book_dirs
    (pdf_dir / "book.pdf").write_text("stub")
    monkeypatch.setattr(runner_mod, "run_pdftotext", _fake_run_pdftotext(_combined_fixture()))

    entry = _manifest_entry_for("book")
    extract_book(entry, pdf_dir=pdf_dir, data_dir=data_dir)

    page3 = (data_dir / "text" / "book" / "p0003.txt").read_text()
    assert "composite bow" in page3
    assert "com-posite" not in page3


def test_dehyphenation_unknown_word_keeps_hyphen(
    monkeypatch: pytest.MonkeyPatch, book_dirs: tuple[Path, Path]
) -> None:
    pdf_dir, data_dir = book_dirs
    (pdf_dir / "book.pdf").write_text("stub")
    monkeypatch.setattr(runner_mod, "run_pdftotext", _fake_run_pdftotext(_combined_fixture()))

    entry = _manifest_entry_for("book")
    extract_book(entry, pdf_dir=pdf_dir, data_dir=data_dir)

    page4 = (data_dir / "text" / "book" / "p0004.txt").read_text()
    assert "zim-zowie" in page4


def test_page_numbers_written_to_pages_json(
    monkeypatch: pytest.MonkeyPatch, book_dirs: tuple[Path, Path]
) -> None:
    pdf_dir, data_dir = book_dirs
    (pdf_dir / "book.pdf").write_text("stub")
    monkeypatch.setattr(runner_mod, "run_pdftotext", _fake_run_pdftotext(_combined_fixture()))

    entry = _manifest_entry_for("book")
    summary = extract_book(entry, pdf_dir=pdf_dir, data_dir=data_dir)

    assert summary.page_numbers_found == 5
    pages_json = json.loads((data_dir / "text" / "book" / "pages.json").read_text())
    assert pages_json == {"1": 100, "2": 101, "3": 102, "4": 103, "5": 104}


# ---------------------------------------------------------------------------
# `p{NNNN}.meta.json` sidecar (batch B3): one entry per output paragraph,
# giving font-size-free segmentation the word-height/line-count stats the
# .txt format itself carries none of.
# ---------------------------------------------------------------------------


def test_meta_json_sidecar_written_per_page(
    monkeypatch: pytest.MonkeyPatch, book_dirs: tuple[Path, Path]
) -> None:
    pdf_dir, data_dir = book_dirs
    (pdf_dir / "book.pdf").write_text("stub")
    monkeypatch.setattr(runner_mod, "run_pdftotext", _fake_run_pdftotext(_combined_fixture()))

    entry = _manifest_entry_for("book")
    extract_book(entry, pdf_dir=pdf_dir, data_dir=data_dir)

    out_dir = data_dir / "text" / "book"
    for i in range(1, 6):
        meta_path = out_dir / f"p{i:04d}.meta.json"
        assert meta_path.exists()
        meta = json.loads(meta_path.read_text())
        assert isinstance(meta, list)
        assert len(meta) >= 1
        for entry_meta in meta:
            assert entry_meta["kind"] in ("prose", "table")
            assert isinstance(entry_meta["median_word_height"], (int, float))
            assert isinstance(entry_meta["max_word_height"], (int, float))
            assert entry_meta["median_word_height"] > 0
            assert entry_meta["max_word_height"] >= entry_meta["median_word_height"]
            assert entry_meta["line_count"] >= 1

    # Page one has two paragraphs (a two-line left block, a one-line right
    # block); the meta list has one entry per paragraph, in order, matching
    # the blank-line-separated units in the .txt file.
    page1_meta = json.loads((out_dir / "p0001.meta.json").read_text())
    page1_text = (out_dir / "p0001.txt").read_text()
    assert len(page1_meta) == len(page1_text.strip("\n").split("\n\n"))
    assert page1_meta[0]["line_count"] == 2  # left column: two physical lines
    assert page1_meta[1]["line_count"] == 1  # right column: one physical line


def test_meta_json_table_kind_and_line_count(
    monkeypatch: pytest.MonkeyPatch, book_dirs: tuple[Path, Path]
) -> None:
    pdf_dir, data_dir = book_dirs
    (pdf_dir / "book.pdf").write_text("stub")

    # A 3-column, 3-row table -- each column its own block, detected as a
    # multi-block table group by owlsperch.text.columns.
    def _table_col(x_min: float, x_max: float, values: list[str]) -> str:
        lines = []
        y = 100.0
        for value in values:
            lines.append(f'<line xMin="{x_min}" yMin="{y}" xMax="{x_max}" yMax="{y + 12}">')
            lines.append(_words_xml(x_min, y, y + 12, value))
            lines.append("</line>")
            y += 20.0
        lines_xml = "".join(lines)
        return f'<block xMin="{x_min}" yMin="100" xMax="{x_max}" yMax="{y}">{lines_xml}</block>'

    table_blocks = (
        _table_col(34, 100, ["Name", "Longsword", "Dagger"])
        + _table_col(150, 200, ["Cost", "15 gp", "2 gp"])
        + _table_col(250, 300, ["Dmg", "1d8", "1d4"])
    )
    page_xml = (
        f'<page width="{_PAGE_WIDTH}" height="{_PAGE_HEIGHT}"><flow>{table_blocks}</flow></page>'
    )
    monkeypatch.setattr(runner_mod, "run_pdftotext", _fake_run_pdftotext(_doc_xml([page_xml])))

    entry = _manifest_entry_for("book")
    extract_book(entry, pdf_dir=pdf_dir, data_dir=data_dir)

    meta = json.loads((data_dir / "text" / "book" / "p0001.meta.json").read_text())
    assert len(meta) == 1
    assert meta[0]["kind"] == "table"
    assert meta[0]["line_count"] == 3  # three rows
    assert meta[0]["median_word_height"] > 0


def test_meta_json_skipped_and_rewritten_with_force(
    monkeypatch: pytest.MonkeyPatch, book_dirs: tuple[Path, Path]
) -> None:
    pdf_dir, data_dir = book_dirs
    (pdf_dir / "book.pdf").write_text("stub")
    monkeypatch.setattr(runner_mod, "run_pdftotext", _fake_run_pdftotext(_combined_fixture()))
    entry = _manifest_entry_for("book")

    extract_book(entry, pdf_dir=pdf_dir, data_dir=data_dir)
    meta_path = data_dir / "text" / "book" / "p0001.meta.json"
    meta_path.write_text("SENTINEL")

    extract_book(entry, pdf_dir=pdf_dir, data_dir=data_dir)
    assert meta_path.read_text() == "SENTINEL"

    extract_book(entry, pdf_dir=pdf_dir, data_dir=data_dir, force=True)
    assert meta_path.read_text() != "SENTINEL"
    json.loads(meta_path.read_text())  # still valid JSON


# ---------------------------------------------------------------------------
# (e) idempotency / --force
# ---------------------------------------------------------------------------


def test_existing_page_files_are_skipped_unless_force(
    monkeypatch: pytest.MonkeyPatch, book_dirs: tuple[Path, Path]
) -> None:
    pdf_dir, data_dir = book_dirs
    (pdf_dir / "book.pdf").write_text("stub")
    monkeypatch.setattr(runner_mod, "run_pdftotext", _fake_run_pdftotext(_combined_fixture()))
    entry = _manifest_entry_for("book")

    first = extract_book(entry, pdf_dir=pdf_dir, data_dir=data_dir)
    assert first.pages_written == 5

    sentinel_path = data_dir / "text" / "book" / "p0001.txt"
    sentinel_path.write_text("SENTINEL - do not overwrite")

    second = extract_book(entry, pdf_dir=pdf_dir, data_dir=data_dir)
    assert second.pages_written == 0
    assert second.pages_skipped == 5
    assert sentinel_path.read_text() == "SENTINEL - do not overwrite"

    third = extract_book(entry, pdf_dir=pdf_dir, data_dir=data_dir, force=True)
    assert third.pages_written == 5
    assert sentinel_path.read_text() != "SENTINEL - do not overwrite"


# ---------------------------------------------------------------------------
# (f) scanned refusal and non-in-scope skip, via a temp manifest
# ---------------------------------------------------------------------------


def _write_manifest(tmp_path: Path, yaml_text: str) -> Path:
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text(yaml_text)
    return manifest_path


_MANIFEST_YAML = """
entries:
  - book_id: scanned-book
    title: "Scanned Book"
    file: "scanned-book.pdf"
    edition: "3.5"
    kind: rulebook
    scanned: true

  - book_id: out-of-scope-book
    title: "Out of Scope Book"
    file: "out-of-scope-book.pdf"
    edition: "3.0"
    kind: supplement

  - book_id: normal-book
    title: "Normal Book"
    file: "normal-book.pdf"
    edition: "3.5"
    kind: rulebook

  - book_id: normal-book-errata
    title: "Normal Book Errata"
    file: "normal-book-errata.pdf"
    edition: "3.5"
    kind: errata
    applies_to: normal-book
"""


def test_scanned_book_is_refused_with_b15_message(
    monkeypatch: pytest.MonkeyPatch, book_dirs: tuple[Path, Path], tmp_path: Path
) -> None:
    pdf_dir, data_dir = book_dirs
    manifest_path = _write_manifest(tmp_path, _MANIFEST_YAML)
    for name in ("scanned-book", "out-of-scope-book", "normal-book", "normal-book-errata"):
        (pdf_dir / f"{name}.pdf").write_text("stub")
    monkeypatch.setattr(runner_mod, "run_pdftotext", _fake_run_pdftotext(_combined_fixture()))

    exit_code = run_text(
        "scanned-book", pdf_dir=pdf_dir, data_dir=data_dir, manifest_path=manifest_path
    )

    assert exit_code == 1
    assert not (data_dir / "text" / "scanned-book").exists()


def test_out_of_scope_book_is_skipped_not_error(
    monkeypatch: pytest.MonkeyPatch, book_dirs: tuple[Path, Path], tmp_path: Path
) -> None:
    pdf_dir, data_dir = book_dirs
    manifest_path = _write_manifest(tmp_path, _MANIFEST_YAML)
    for name in ("scanned-book", "out-of-scope-book", "normal-book", "normal-book-errata"):
        (pdf_dir / f"{name}.pdf").write_text("stub")
    monkeypatch.setattr(runner_mod, "run_pdftotext", _fake_run_pdftotext(_combined_fixture()))

    exit_code = run_text(
        "out-of-scope-book", pdf_dir=pdf_dir, data_dir=data_dir, manifest_path=manifest_path
    )

    assert exit_code == 0
    assert not (data_dir / "text" / "out-of-scope-book").exists()


def test_override_source_is_processed_not_skipped(
    monkeypatch: pytest.MonkeyPatch, book_dirs: tuple[Path, Path], tmp_path: Path
) -> None:
    pdf_dir, data_dir = book_dirs
    manifest_path = _write_manifest(tmp_path, _MANIFEST_YAML)
    for name in ("scanned-book", "out-of-scope-book", "normal-book", "normal-book-errata"):
        (pdf_dir / f"{name}.pdf").write_text("stub")
    monkeypatch.setattr(runner_mod, "run_pdftotext", _fake_run_pdftotext(_combined_fixture()))

    exit_code = run_text(
        "normal-book-errata", pdf_dir=pdf_dir, data_dir=data_dir, manifest_path=manifest_path
    )

    assert exit_code == 0
    assert (data_dir / "text" / "normal-book-errata" / "p0001.txt").exists()


def test_unknown_book_id_is_a_clear_error(
    book_dirs: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pdf_dir, data_dir = book_dirs
    manifest_path = _write_manifest(tmp_path, _MANIFEST_YAML)

    exit_code = run_text(
        "does-not-exist", pdf_dir=pdf_dir, data_dir=data_dir, manifest_path=manifest_path
    )

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "does-not-exist" in captured.err


def test_all_iterates_every_eligible_book_and_prints_summary(
    monkeypatch: pytest.MonkeyPatch, book_dirs: tuple[Path, Path], tmp_path: Path
) -> None:
    import io

    pdf_dir, data_dir = book_dirs
    manifest_path = _write_manifest(tmp_path, _MANIFEST_YAML)
    for name in ("scanned-book", "out-of-scope-book", "normal-book", "normal-book-errata"):
        (pdf_dir / f"{name}.pdf").write_text("stub")
    monkeypatch.setattr(runner_mod, "run_pdftotext", _fake_run_pdftotext(_combined_fixture()))

    out = io.StringIO()
    exit_code = run_text(
        "all", pdf_dir=pdf_dir, data_dir=data_dir, manifest_path=manifest_path, out=out
    )

    output = out.getvalue()
    assert exit_code == 0
    assert "scanned-book" in output and "B15" in output
    assert "out-of-scope-book" in output and "skipped" in output
    assert "normal-book:" in output
    assert "normal-book-errata:" in output


# ---------------------------------------------------------------------------
# (MINOR 4) vertical-block exclusion is counted, not just silently discarded
# ---------------------------------------------------------------------------


def test_vertical_blocks_excluded_are_counted_in_summary(
    monkeypatch: pytest.MonkeyPatch, book_dirs: tuple[Path, Path]
) -> None:
    pdf_dir, data_dir = book_dirs
    (pdf_dir / "book.pdf").write_text("stub")

    # A narrow, tall single-line block: a rotated page-edge chapter tab (the
    # same shape image credits like "Illus. by ..." take, per the module
    # docstring -- excluded from reading order, but the loss should now be
    # visible in the summary).
    vertical_tab = (
        '<block xMin="587.7" yMin="145.4" xMax="599.5" yMax="210.8">'
        '<line xMin="587.7" yMin="145.4" xMax="599.5" yMax="210.8">'
        '<word xMin="587.7" yMin="145.4" xMax="599.5" yMax="210.8">CHAPTER 7:</word>'
        "</line></block>"
    )
    body_block = _block_xml(34, 100, 580, ["Some body text here."])
    page_xml = (
        f'<page width="{_PAGE_WIDTH}" height="{_PAGE_HEIGHT}">'
        f"<flow>{body_block}{vertical_tab}</flow></page>"
    )
    monkeypatch.setattr(runner_mod, "run_pdftotext", _fake_run_pdftotext(_doc_xml([page_xml])))

    entry = _manifest_entry_for("book")
    summary = extract_book(entry, pdf_dir=pdf_dir, data_dir=data_dir)

    assert summary.vertical_blocks_excluded == 1
    assert "1 vertical blocks excluded" in summary.render()
    text = (data_dir / "text" / "book" / "p0001.txt").read_text()
    assert "CHAPTER 7" not in text


# ---------------------------------------------------------------------------
# (MINOR 5) a digit-only footer line is counted as a page number, never as a
# removed running header/footer.
# ---------------------------------------------------------------------------


def test_page_numbers_are_not_counted_as_headers_removed(
    monkeypatch: pytest.MonkeyPatch, book_dirs: tuple[Path, Path]
) -> None:
    pdf_dir, data_dir = book_dirs
    (pdf_dir / "book.pdf").write_text("stub")

    # No running header/footer text anywhere -- only a footer page number on
    # every page. Its digits-stripped normalization is the empty string, so
    # the (band, "") key recurs on every page just like a real running
    # footer would; only the page-number check should claim these lines.
    pages = [
        _page_xml(None, 100 + i, [f"Left column page {i}."], [f"Right column page {i}."])
        for i in range(5)
    ]
    monkeypatch.setattr(runner_mod, "run_pdftotext", _fake_run_pdftotext(_doc_xml(pages)))

    entry = _manifest_entry_for("book")
    summary = extract_book(entry, pdf_dir=pdf_dir, data_dir=data_dir)

    assert summary.headers_removed == 0
    assert summary.page_numbers_found == 5


# ---------------------------------------------------------------------------
# (MINOR 6) a missing `pdftotext` binary is a clear, structured error.
# ---------------------------------------------------------------------------


def _raise_file_not_found(*args: object, **kwargs: object) -> None:
    raise FileNotFoundError("pdftotext")


def test_missing_pdftotext_binary_raises_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", _raise_file_not_found)

    with pytest.raises(runner_mod.TextExtractionError) as exc_info:
        runner_mod.run_pdftotext(Path("book.pdf"), Path("out.html"))

    message = str(exc_info.value)
    assert "pdftotext" in message
    assert "brew install poppler" in message


def test_missing_pdftotext_binary_all_continues_and_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch, book_dirs: tuple[Path, Path], tmp_path: Path
) -> None:
    import io

    pdf_dir, data_dir = book_dirs
    manifest_path = _write_manifest(tmp_path, _MANIFEST_YAML)
    for name in ("scanned-book", "out-of-scope-book", "normal-book", "normal-book-errata"):
        (pdf_dir / f"{name}.pdf").write_text("stub")

    monkeypatch.setattr(subprocess, "run", _raise_file_not_found)

    out = io.StringIO()
    exit_code = run_text(
        "all", pdf_dir=pdf_dir, data_dir=data_dir, manifest_path=manifest_path, out=out
    )

    output = out.getvalue()
    assert exit_code == 1
    # Both eligible real books were attempted (the run did not stop after
    # the first failure) and both reported the clear installation hint.
    assert "normal-book:" in output
    assert "normal-book-errata:" in output
    assert output.count("brew install poppler") == 2
    # The scanned book is still refused on its own terms, not blamed on the
    # missing binary (its manifest status is checked before extraction).
    assert "scanned-book" in output and "B15" in output


# ---------------------------------------------------------------------------
# The real corpus (acceptance criterion 6's corpus marker)
# ---------------------------------------------------------------------------


@pytest.mark.corpus
def test_phb_page_120_corpus() -> None:
    import shutil
    import tempfile

    from owlsperch.manifest import default_manifest_path, default_pdf_dir

    pdf_dir = default_pdf_dir()
    if not pdf_dir.is_dir():
        pytest.skip(f"real PDF corpus not present at {pdf_dir}")
    if shutil.which("pdftotext") is None:
        pytest.skip("pdftotext (poppler) not installed")

    entries = load_manifest(default_manifest_path())
    # The manifest names the real Player's Handbook I "phb1" (see B1); fall
    # back to "phb" (the spec's illustrative example id) if a manifest ever
    # uses that instead.
    book_id = "phb1" if any(e.book_id == "phb1" for e in entries) else "phb"

    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        exit_code = run_text(
            book_id,
            pdf_dir=pdf_dir,
            data_dir=data_dir,
            page_range=(118, 122),
        )
        assert exit_code == 0
        text = (data_dir / "text" / book_id / "p0120.txt").read_text()
        assert "CHAPTER 7:" not in text
        assert text.index("Longbow, Composite") < text.index("Longspear:")


@pytest.mark.corpus
def test_phb_page_32_cleric_table_reassembles_corpus() -> None:
    """Regression for B10c-mand7: PHB p.32's "Table 3-6: The Cleric" is a
    class-level table `pdftotext` fragments into several per-column table
    groups plus orphan single-cell blocks for the 4th/8th/12th/15th/16th/
    20th level rows (see `owlsperch.text.columns`'s module docstring, step
    2b). Before that step existed, those level rows came out as lone,
    tab-less lines; after it, every level row is part of the one
    reassembled table group, so each one's line has every column,
    tab-joined."""
    import shutil
    import tempfile

    from owlsperch.manifest import default_manifest_path, default_pdf_dir

    pdf_dir = default_pdf_dir()
    if not pdf_dir.is_dir():
        pytest.skip(f"real PDF corpus not present at {pdf_dir}")
    if shutil.which("pdftotext") is None:
        pytest.skip("pdftotext (poppler) not installed")

    entries = load_manifest(default_manifest_path())
    book_id = "phb1" if any(e.book_id == "phb1" for e in entries) else "phb"

    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        exit_code = run_text(
            book_id,
            pdf_dir=pdf_dir,
            data_dir=data_dir,
            page_range=(31, 32),
        )
        assert exit_code == 0
        text = (data_dir / "text" / book_id / "p0032.txt").read_text()
        lines = text.splitlines()

        # Every level row -- including the ones `pdftotext` used to split
        # off as orphan single-cell blocks -- now has its Level cell
        # tab-joined with the rest of its row, evidence it landed inside
        # the one reassembled table group rather than as a lone fragment.
        ordinals = [
            "1st",
            "2nd",
            "3rd",
            "4th",
            "5th",
            "6th",
            "7th",
            "8th",
            "9th",
            "10th",
            "11th",
            "12th",
            "13th",
            "14th",
            "15th",
            "16th",
            "17th",
            "18th",
            "19th",
            "20th",
        ]
        row_lines = {
            ordinal: next((line for line in lines if line.startswith(f"{ordinal}\t")), None)
            for ordinal in ordinals
        }
        missing = [ordinal for ordinal, line in row_lines.items() if line is None]
        assert not missing, (missing, lines)

        # Every level row is a FULL class-table row, not just the Level
        # cell plus a couple of others: at least 20 of the table's lines
        # (the 20 level rows) have at least 10 tab-separated cells.
        wide_rows = [line for line in lines if len(line.split("\t")) >= 10]
        assert len(wide_rows) >= 20, (len(wide_rows), lines)

        # The 20th-level row -- one of the ones `pdftotext` used to split
        # off as a lone orphan block -- has its Level/BAB/Fort/Ref/Will
        # cells intact and in order after reassembly.
        row_20th = row_lines["20th"]
        assert row_20th is not None
        assert row_20th.split("\t")[:5] == ["20th", "+15/+10/+5", "+12", "+6", "+12"], row_20th

        # The table's footnote -- sitting directly below it, at a similar
        # x-position and line pitch to the table's own rows -- must start
        # its own line rather than getting tab-joined into a table row by
        # the reassembly pass (it is prose, not a table row: `_is_prose_
        # like_block`/`_is_label_value_block` must keep it excluded).
        footnote_text = "In addition to the stated number of spells per day"
        footnote_lines = [line for line in lines if footnote_text in line]
        assert len(footnote_lines) == 1, footnote_lines
        assert "\t" not in footnote_lines[0], footnote_lines[0]


@pytest.mark.corpus
def test_phb_class_table_leading_spell_cells_corpus() -> None:
    """Regression for B10c-mand9: a class table's narrow "Spells per Day"
    columns whose cells are each a single digit (PHB p.56's Table 3-18: The
    Wizard 0/1st columns, p.32's Table 3-6: The Cleric 0 column) used to be
    dropped wholesale as rotated marginalia, since a lone digit is taller
    than it is wide (see `owlsperch.text.columns`'s module docstring, step
    1). Those cells must survive."""
    import shutil
    import tempfile

    from owlsperch.manifest import default_manifest_path, default_pdf_dir

    pdf_dir = default_pdf_dir()
    if not pdf_dir.is_dir():
        pytest.skip(f"real PDF corpus not present at {pdf_dir}")
    if shutil.which("pdftotext") is None:
        pytest.skip("pdftotext (poppler) not installed")

    entries = load_manifest(default_manifest_path())
    book_id = "phb1" if any(e.book_id == "phb1" for e in entries) else "phb"

    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        assert run_text(book_id, pdf_dir=pdf_dir, data_dir=data_dir, page_range=(32, 32)) == 0
        assert run_text(book_id, pdf_dir=pdf_dir, data_dir=data_dir, page_range=(56, 56)) == 0

        # The wizard table's 2nd-level row: its Will save (+3) is followed
        # by the 0-level and 1st-level spells-per-day cells (4 and 2).
        wizard_lines = (data_dir / "text" / book_id / "p0056.txt").read_text().splitlines()
        row_2nd = next((line for line in wizard_lines if line.startswith("2nd\t")), None)
        assert row_2nd is not None, wizard_lines
        cells = row_2nd.split("\t")
        will_index = cells.index("+3")
        assert cells[will_index + 1 : will_index + 3] == ["4", "2"], row_2nd

        # The cleric table's header keeps its "0" spells-per-day column,
        # and the 1st-level row's 0-level count (3) precedes its 1st-level
        # count (1+1).
        cleric_lines = (data_dir / "text" / book_id / "p0032.txt").read_text().splitlines()
        header = next((line for line in cleric_lines if "\tSpecial\t" in line), None)
        assert header is not None, cleric_lines
        header_cells = header.split("\t")
        special_index = header_cells.index("Special")
        assert header_cells[special_index + 1 : special_index + 3] == ["0", "1st"], header
        row_1st = next((line for line in cleric_lines if line.startswith("1st\t")), None)
        assert row_1st is not None, cleric_lines
        cleric_cells = row_1st.split("\t")
        assert cleric_cells[cleric_cells.index("1+1") - 1] == "3", row_1st


@pytest.mark.corpus
def test_phb_boxed_sidebars_corpus() -> None:
    """Regression for B10c-mand15's three sidebar defects (see
    `owlsperch.text.columns`'s module docstring, steps 2b/2c/4a), all in
    boxed sidebars that share a page with class text:

    A. PHB p.37 "THE DRUID'S ANIMAL COMPANION" -- the grid's "Improved
       evasion" cell arrives as a lone block 2.3pt wider than its own
       column, and used to be emitted after the whole table instead of on
       its printed 15th-17th row.
    B. PHB pp.53-54 "FAMILIARS" -- the 10-row `Familiar | Special` grid has
       exactly one gap per row, so it never became a table group and came
       out as a run-on sentence with the Snake row's label after its value.
    C. PHB p.46 "THE PALADIN'S MOUNT" -- a boxed sidebar spanning both body
       columns narrows the gutter enough that both columns merged into one
       cluster, so the sidebar opened with its right column's mid-sentence
       continuation.
    """
    import shutil
    import tempfile

    from owlsperch.manifest import default_manifest_path, default_pdf_dir

    pdf_dir = default_pdf_dir()
    if not pdf_dir.is_dir():
        pytest.skip(f"real PDF corpus not present at {pdf_dir}")
    if shutil.which("pdftotext") is None:
        pytest.skip("pdftotext (poppler) not installed")

    entries = load_manifest(default_manifest_path())
    book_id = "phb1" if any(e.book_id == "phb1" for e in entries) else "phb"

    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        assert run_text(book_id, pdf_dir=pdf_dir, data_dir=data_dir, page_range=(37, 37)) == 0
        assert run_text(book_id, pdf_dir=pdf_dir, data_dir=data_dir, page_range=(46, 46)) == 0
        assert run_text(book_id, pdf_dir=pdf_dir, data_dir=data_dir, page_range=(53, 54)) == 0

        def _lines(page: int) -> list[str]:
            return (data_dir / "text" / book_id / f"p{page:04d}.txt").read_text().splitlines()

        # (A) "Improved evasion" is a cell of the 15th-17th row, not a line
        # of its own, and the 18th-20th row does NOT claim it.
        p37 = _lines(37)
        row_15th = next((line for line in p37 if line.startswith("15th–17th\t")), None)
        assert row_15th is not None, p37
        assert row_15th.split("\t")[-1] == "Improved evasion", row_15th
        row_18th = next((line for line in p37 if line.startswith("18th–20th\t")), None)
        assert row_18th is not None, p37
        assert "Improved evasion" not in row_18th, row_18th
        assert not any(line.strip() == "Improved evasion" for line in p37), p37

        # (C) the druid and paladin sidebars open at their own heading and
        # first printed sentence, not at the right column's continuation.
        druid_sidebar = next(i for i, line in enumerate(p37) if "ANIMAL COMPANION" in line)
        assert p37[druid_sidebar + 2].startswith("A druid’s animal companion is different"), p37[
            druid_sidebar : druid_sidebar + 4
        ]

        p46 = _lines(46)
        mount_sidebar = next(i for i, line in enumerate(p46) if "PALADIN’S MOUNT" in line)
        assert p46[mount_sidebar + 2].startswith("The paladin’s mount is superior"), p46[
            mount_sidebar : mount_sidebar + 4
        ]
        # ...and the right column's continuation is no longer FIRST.
        mid_sentence = next(
            i for i, line in enumerate(p46) if line.startswith("mount must be within 5 feet")
        )
        assert mid_sentence > mount_sidebar, (mount_sidebar, mid_sentence)

        # (B) the FAMILIARS `Familiar | Special` grid is a tab-joined group
        # of 10 animal rows, each label with its own value.
        p53 = _lines(53)
        assert "Familiar\tSpecial" in p53, p53
        familiar_rows = {
            line.split("\t")[0]: line.split("\t")[1]
            for line in p53
            if len(line.split("\t")) == 2 and line.split("\t")[0] in _FAMILIAR_NAMES
        }
        assert sorted(familiar_rows) == sorted(_FAMILIAR_NAMES), sorted(familiar_rows)
        assert familiar_rows["Snake 2"] == "Master gains a +3 bonus on Bluff checks"
        assert familiar_rows["Raven 1"] == "Master gains a +3 bonus on Appraise checks"

        # ...and p53's FAMILIARS sidebar opens with its own intro, while its
        # p54 continuation still carries the master-class-level grid.
        familiars = next(i for i, line in enumerate(p53) if line.strip() == "FAMILIARS")
        assert p53[familiars + 2].startswith("Familiars are magically linked"), p53[
            familiars : familiars + 4
        ]
        p54 = _lines(54)
        assert any(line.startswith("19th–20th\t") for line in p54), p54


#: The FAMILIARS sidebar's own 10 familiar names, as PHB p.53 prints them
#: (the two footnoted ones carry their marker in the same cell).
_FAMILIAR_NAMES = [
    "Bat",
    "Cat",
    "Hawk",
    "Lizard",
    "Owl",
    "Rat",
    "Raven 1",
    "Snake 2",
    "Toad",
    "Weasel",
]
