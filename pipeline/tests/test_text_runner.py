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
