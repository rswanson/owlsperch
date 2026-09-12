"""Acceptance tests for the B1 batch: manifest schema, in-scope derivation, and
`owlsperch manifest check` behavior against a directory of PDFs.

See docs/specs/2026-09-12-dnd-reference-site-spec.md (Scope boundaries, 4.1-4.4)
and the B1 batch description for the behavior these tests pin down.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from owlsperch.manifest import (
    ManifestError,
    check,
    in_scope,
    load_manifest,
)


def _write_manifest(tmp_path: Path, yaml_text: str) -> Path:
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text(yaml_text)
    return manifest_path


def _touch(dir_path: Path, *names: str) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    for name in names:
        (dir_path / name).write_text("stub")


# ---------------------------------------------------------------------------
# In-scope derivation (acceptance criterion 5, bullet 1)
# ---------------------------------------------------------------------------


def test_35_book_is_in_scope(tmp_path: Path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        """
entries:
  - book_id: phb1
    title: "Player's Handbook"
    file: "phb.pdf"
    edition: "3.5"
    kind: rulebook
    published: "2003-07"
    applies_to: null
    scanned: false
    preferred_over: null
    exclude_reason: null
""",
    )
    entries = load_manifest(manifest_path)
    assert in_scope(entries[0], entries) is True


def test_30_book_with_no_update_is_out_of_scope(tmp_path: Path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        """
entries:
  - book_id: sword-and-fist
    title: "Sword and Fist"
    file: "saf.pdf"
    edition: "3.0"
    kind: supplement
    published: "2000-08"
    applies_to: null
    scanned: false
    preferred_over: null
    exclude_reason: null
""",
    )
    entries = load_manifest(manifest_path)
    assert in_scope(entries[0], entries) is False


def test_30_book_with_update_pointing_at_it_is_in_scope(tmp_path: Path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        """
entries:
  - book_id: mm2
    title: "Monster Manual II"
    file: "mm2.pdf"
    edition: "3.0"
    kind: supplement
    published: "2000-09"
    applies_to: null
    scanned: false
    preferred_over: null
    exclude_reason: null
  - book_id: mm2-update
    title: "Monster Manual II 3.5 Update"
    file: "mm2-update.pdf"
    edition: "3.5"
    kind: update
    published: "2003-07"
    applies_to: mm2
    scanned: false
    preferred_over: null
    exclude_reason: null
""",
    )
    entries = load_manifest(manifest_path)
    by_id = {e.book_id: e for e in entries}
    assert in_scope(by_id["mm2"], entries) is True
    # The update/override entry itself is never "in scope" as a book.
    assert in_scope(by_id["mm2-update"], entries) is False


def test_excluded_entry_is_out_of_scope(tmp_path: Path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        """
entries:
  - book_id: personal-file
    title: "Personal File"
    file: "personal.pdf"
    edition: "3.5"
    kind: excluded
    published: null
    applies_to: null
    scanned: false
    preferred_over: null
    exclude_reason: "personal file"
""",
    )
    entries = load_manifest(manifest_path)
    assert in_scope(entries[0], entries) is False


def test_index_entry_is_out_of_scope(tmp_path: Path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        """
entries:
  - book_id: index-feats
    title: "Feats Index"
    file: "index-feats.pdf"
    edition: "3.5"
    kind: index
    published: null
    applies_to: null
    scanned: false
    preferred_over: null
    exclude_reason: null
""",
    )
    entries = load_manifest(manifest_path)
    assert in_scope(entries[0], entries) is False


# ---------------------------------------------------------------------------
# manifest check: mismatch directions (acceptance criterion 5, bullet 2)
# ---------------------------------------------------------------------------


def test_check_reports_file_present_but_not_in_manifest(tmp_path: Path) -> None:
    pdf_dir = tmp_path / "pdfs"
    _touch(pdf_dir, "known.pdf", "unknown.pdf")
    manifest_path = _write_manifest(
        tmp_path,
        """
entries:
  - book_id: known
    title: "Known Book"
    file: "known.pdf"
    edition: "3.5"
    kind: rulebook
    published: "2003-07"
    applies_to: null
    scanned: false
    preferred_over: null
    exclude_reason: null
""",
    )
    result = check(pdf_dir=pdf_dir, manifest_path=manifest_path)
    assert result.missing_from_manifest == ["unknown.pdf"]
    assert result.missing_from_dir == []
    assert result.exit_code == 1


def test_check_reports_entry_present_but_file_missing_from_dir(tmp_path: Path) -> None:
    pdf_dir = tmp_path / "pdfs"
    _touch(pdf_dir, "known.pdf")
    manifest_path = _write_manifest(
        tmp_path,
        """
entries:
  - book_id: known
    title: "Known Book"
    file: "known.pdf"
    edition: "3.5"
    kind: rulebook
    published: "2003-07"
    applies_to: null
    scanned: false
    preferred_over: null
    exclude_reason: null
  - book_id: ghost
    title: "Ghost Book"
    file: "ghost.pdf"
    edition: "3.5"
    kind: rulebook
    published: "2003-07"
    applies_to: null
    scanned: false
    preferred_over: null
    exclude_reason: null
""",
    )
    result = check(pdf_dir=pdf_dir, manifest_path=manifest_path)
    assert result.missing_from_manifest == []
    assert result.missing_from_dir == ["ghost.pdf"]
    assert result.exit_code == 1


def test_check_exits_zero_when_manifest_and_dir_match(tmp_path: Path) -> None:
    pdf_dir = tmp_path / "pdfs"
    _touch(pdf_dir, "known.pdf")
    manifest_path = _write_manifest(
        tmp_path,
        """
entries:
  - book_id: known
    title: "Known Book"
    file: "known.pdf"
    edition: "3.5"
    kind: rulebook
    published: "2003-07"
    applies_to: null
    scanned: false
    preferred_over: null
    exclude_reason: null
""",
    )
    result = check(pdf_dir=pdf_dir, manifest_path=manifest_path)
    assert result.missing_from_manifest == []
    assert result.missing_from_dir == []
    assert result.exit_code == 0


def test_check_ignores_ds_store(tmp_path: Path) -> None:
    pdf_dir = tmp_path / "pdfs"
    _touch(pdf_dir, "known.pdf", ".DS_Store")
    manifest_path = _write_manifest(
        tmp_path,
        """
entries:
  - book_id: known
    title: "Known Book"
    file: "known.pdf"
    edition: "3.5"
    kind: rulebook
    published: "2003-07"
    applies_to: null
    scanned: false
    preferred_over: null
    exclude_reason: null
""",
    )
    result = check(pdf_dir=pdf_dir, manifest_path=manifest_path)
    assert result.exit_code == 0


def test_check_counts_by_kind_and_status(tmp_path: Path) -> None:
    pdf_dir = tmp_path / "pdfs"
    _touch(pdf_dir, "phb.pdf", "mm2.pdf", "mm2-update.pdf", "excluded.pdf", "index.pdf")
    manifest_path = _write_manifest(
        tmp_path,
        """
entries:
  - book_id: phb1
    title: "Player's Handbook"
    file: "phb.pdf"
    edition: "3.5"
    kind: rulebook
    published: "2003-07"
    applies_to: null
    scanned: false
    preferred_over: null
    exclude_reason: null
  - book_id: mm2
    title: "Monster Manual II"
    file: "mm2.pdf"
    edition: "3.0"
    kind: supplement
    published: "2000-09"
    applies_to: null
    scanned: false
    preferred_over: null
    exclude_reason: null
  - book_id: mm2-update
    title: "Monster Manual II 3.5 Update"
    file: "mm2-update.pdf"
    edition: "3.5"
    kind: update
    published: "2003-07"
    applies_to: mm2
    scanned: false
    preferred_over: null
    exclude_reason: null
  - book_id: personal
    title: "Personal File"
    file: "excluded.pdf"
    edition: "3.5"
    kind: excluded
    published: null
    applies_to: null
    scanned: false
    preferred_over: null
    exclude_reason: "personal file"
  - book_id: index-feats
    title: "Feats Index"
    file: "index.pdf"
    edition: "3.5"
    kind: index
    published: null
    applies_to: null
    scanned: false
    preferred_over: null
    exclude_reason: null
""",
    )
    result = check(pdf_dir=pdf_dir, manifest_path=manifest_path)
    assert result.exit_code == 0
    assert result.counts_by_status["in_scope"] == 2  # phb1 and mm2 (via update)
    assert result.counts_by_status["override"] == 1  # mm2-update
    assert result.counts_by_status["excluded"] == 1
    assert result.counts_by_status["index"] == 1
    assert result.counts_by_kind["rulebook"] == 1
    assert result.counts_by_kind["supplement"] == 1
    assert result.counts_by_kind["update"] == 1


# ---------------------------------------------------------------------------
# Schema validation (acceptance criterion 4)
# ---------------------------------------------------------------------------


def test_malformed_entry_fails_with_clear_error_naming_book_id_and_field(
    tmp_path: Path,
) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        """
entries:
  - book_id: bad-kind-book
    title: "Bad Kind Book"
    file: "bad.pdf"
    edition: "3.5"
    kind: not-a-real-kind
    published: "2003-07"
    applies_to: null
    scanned: false
    preferred_over: null
    exclude_reason: null
""",
    )
    with pytest.raises(ManifestError) as exc_info:
        load_manifest(manifest_path)
    message = str(exc_info.value)
    assert "bad-kind-book" in message
    assert "kind" in message


def test_errata_entry_without_applies_to_fails(tmp_path: Path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        """
entries:
  - book_id: some-errata
    title: "Some Errata"
    file: "errata.pdf"
    edition: "3.5"
    kind: errata
    published: "2003-07"
    applies_to: null
    scanned: false
    preferred_over: null
    exclude_reason: null
""",
    )
    with pytest.raises(ManifestError) as exc_info:
        load_manifest(manifest_path)
    assert "some-errata" in str(exc_info.value)


def test_excluded_entry_without_exclude_reason_fails(tmp_path: Path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        """
entries:
  - book_id: some-excluded
    title: "Some Excluded File"
    file: "excl.pdf"
    edition: "3.5"
    kind: excluded
    published: null
    applies_to: null
    scanned: false
    preferred_over: null
    exclude_reason: null
""",
    )
    with pytest.raises(ManifestError) as exc_info:
        load_manifest(manifest_path)
    assert "some-excluded" in str(exc_info.value)


# ---------------------------------------------------------------------------
# The real corpus (acceptance criterion 5's corpus marker)
# ---------------------------------------------------------------------------


@pytest.mark.corpus
def test_manifest_check_passes_against_real_corpus() -> None:
    pdf_dir = Path(os.environ.get("OWLSPERCH_PDFS", str(Path.home() / "D_D")))
    if not pdf_dir.is_dir():
        pytest.skip(f"real PDF corpus not present at {pdf_dir}")
    repo_manifest = Path(__file__).resolve().parents[1] / "manifest.yaml"
    result = check(pdf_dir=pdf_dir, manifest_path=repo_manifest)
    assert result.exit_code == 0, (
        f"missing_from_manifest={result.missing_from_manifest} "
        f"missing_from_dir={result.missing_from_dir}"
    )
