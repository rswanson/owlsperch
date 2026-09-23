"""Synthetic, independently authored evidence for the bounded reference pilot."""

from __future__ import annotations

import copy
import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from owlsperch.reference.capture import capture_source
from owlsperch.reference.evaluate import DocumentError, evaluate, resolve_pointer
from owlsperch.reference.store import ReferenceStore


def snapshot() -> dict[str, Any]:
    return {
        "snapshot_id": "snapshot-a",
        "book_id": "test",
        "source_sha256": "a" * 64,
        "extractor": "pdftotext 1",
        "page_range": [10, 10],
        "pages": [
            {
                "pdf_page_index": 10,
                "width": 612.0,
                "height": 792.0,
                "text_empty": False,
                "blocks": [
                    {
                        "block_id": "block-a",
                        "text": "Éclair Spell Evocation Wizard 3",
                        "bbox": [1, 2, 100, 20],
                        "words": [{"text": "Éclair", "bbox": [1, 2, 20, 10]}],
                    }
                ],
            }
        ],
    }


def inventory(*, reviewed: bool = False, text_reference: bool = False) -> dict[str, Any]:
    expected = {"/fields/school": "Evocation", "/fields/levels": [{"class": "Wizard", "level": 3}]}
    required = ["/name", "/fields/school", "/fields/levels"]
    if text_reference:
        expected["/text_md"] = "Éclair Spell is a bright spell."
        required.append("/text_md")
    return {
        "version": 1,
        "dataset_id": "synthetic-v1",
        "review_status": "reviewed" if reviewed else "provisional",
        "cases": [
            {
                "case_id": "same-name-01",
                "snapshot_id": "snapshot-a",
                "name": "Éclair Spell",
                "type": "spell",
                "block_ids": ["block-a"],
                "expected": expected,
                "required_evidence": required,
            }
        ],
    }


def candidate(*, text_reference: bool = False) -> dict[str, Any]:
    evidence = {
        "/name": [{"block_id": "block-a", "start": 0, "end": 12, "quote": "Éclair Spell"}],
        "/fields/school": [{"block_id": "block-a", "start": 13, "end": 22, "quote": "Evocation"}],
        "/fields/levels": [{"block_id": "block-a", "start": 23, "end": 31, "quote": "Wizard 3"}],
    }
    if text_reference:
        evidence["/text_md"] = [
            {"block_id": "block-a", "start": 0, "end": 12, "quote": "Éclair Spell"}
        ]
    return {
        "case_id": "same-name-01",
        "name": "Éclair Spell",
        "type": "spell",
        "text_md": "Éclair Spell is a bright spell.",
        "fields": {"school": "Evocation", "levels": [{"class": "Wizard", "level": 3}]},
        "evidence": evidence,
    }


def submission(*entries: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": 1,
        "model": "synthetic-model",
        "prompt_version": "v1",
        "candidates": list(entries),
    }


def test_evaluator_rejects_fabricated_field_and_keeps_provenance_distinct() -> None:
    original = candidate()
    altered = copy.deepcopy(original)
    altered["fields"]["school"] = "Necromancy"
    baseline = evaluate(inventory(), submission(original), {"snapshot-a": snapshot()})
    corrupted = evaluate(inventory(), submission(altered), {"snapshot-a": snapshot()})
    assert baseline["counts"]["passed"] == 1
    assert baseline["counts"]["unscored_text"] == 1
    assert baseline["counts"]["correct_text"] == 0
    assert baseline["counts"]["expected_field_assertions"] == 2
    assert baseline["counts"]["correct_field_assertions"] == 2
    assert corrupted["counts"]["passed"] == 0
    assert corrupted["counts"]["valid_evidence"] == 1
    assert corrupted["counts"]["correct_fields"] == 0
    assert corrupted["counts"]["correct_field_assertions"] == 1


def test_missing_extra_duplicate_and_same_name_cases_affect_denominator() -> None:
    inv = inventory()
    second = copy.deepcopy(inv["cases"][0])
    second["case_id"] = "same-name-02"
    inv["cases"].append(second)
    one = evaluate(inv, submission(candidate()), {"snapshot-a": snapshot()})
    assert one["counts"]["expected"] == 2
    assert one["counts"]["missing"] == 1
    assert one["counts"]["passed"] == 1
    extra = copy.deepcopy(candidate())
    extra["case_id"] = "invented"
    duplicate = evaluate(
        inv, submission(candidate(), candidate(), extra), {"snapshot-a": snapshot()}
    )
    assert duplicate["counts"]["duplicates"] == 1
    assert duplicate["counts"]["extras"] == 1
    assert not duplicate["release_ready"]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda c: c["fields"]["levels"][0].update(level=True),
        lambda c: c["fields"].update(levels=[{"class": "Wizard", "level": 4}]),
        lambda c: c["evidence"]["/name"][0].update(quote="Éclair Spells"),
        lambda c: c["evidence"]["/name"][0].update(end=99),
        lambda c: c["evidence"]["/name"][0].update(block_id="other-source-block"),
    ],
)
def test_type_cell_and_span_corruptions_fail(
    mutation: Callable[[dict[str, Any]], None],
) -> None:
    altered = candidate()
    mutation(altered)
    result = evaluate(inventory(), submission(altered), {"snapshot-a": snapshot()})
    assert result["counts"]["passed"] == 0


def test_cross_snapshot_quote_fails_even_when_text_matches() -> None:
    snap = snapshot()
    other = copy.deepcopy(snap)
    other["snapshot_id"] = "snapshot-b"
    other["pages"][0]["blocks"][0]["block_id"] = "block-b"
    altered = candidate()
    altered["evidence"]["/name"][0]["block_id"] = "block-b"
    result = evaluate(inventory(), submission(altered), {"snapshot-a": snap, "snapshot-b": other})
    assert result["counts"]["valid_evidence"] == 0


def test_release_requires_reviewed_text_reference() -> None:
    provisional = evaluate(
        inventory(text_reference=True),
        submission(candidate(text_reference=True)),
        {"snapshot-a": snapshot()},
    )
    reviewed = evaluate(
        inventory(reviewed=True, text_reference=True),
        submission(candidate(text_reference=True)),
        {"snapshot-a": snapshot()},
    )
    assert not provisional["release_ready"]
    assert reviewed["release_ready"]
    assert not evaluate(
        inventory(reviewed=True), submission(candidate()), {"snapshot-a": snapshot()}
    )["release_ready"]


def test_missing_readable_text_fails_reference_and_counted() -> None:
    altered = candidate(text_reference=True)
    altered["text_md"] = ""
    result = evaluate(
        inventory(reviewed=True, text_reference=True),
        submission(altered),
        {"snapshot-a": snapshot()},
    )
    assert result["counts"]["correct_text"] == 0
    assert result["counts"]["passed"] == 0


def test_invalid_documents_and_pointer_fail_before_persistence() -> None:
    assert resolve_pointer({"a/b": [False, 0]}, "/a~1b/0") is False
    with pytest.raises(DocumentError, match="pointer"):
        resolve_pointer({}, "fields/school")
    bad = inventory()
    bad["cases"][0]["expected"] = {"/fields/~2": 1}
    with pytest.raises(DocumentError, match="pointer"):
        evaluate(bad, submission(candidate()), {"snapshot-a": snapshot()})
    with pytest.raises(DocumentError, match="cases"):
        evaluate({**inventory(), "cases": []}, submission(), {"snapshot-a": snapshot()})
    with pytest.raises(DocumentError, match="snapshot"):
        evaluate(inventory(), submission(candidate()), {})
    without_structured_assertion = inventory(reviewed=True, text_reference=True)
    without_structured_assertion["cases"][0]["expected"] = {"/text_md": "anything"}
    with pytest.raises(DocumentError, match="/fields/"):
        evaluate(without_structured_assertion, submission(candidate()), {"snapshot-a": snapshot()})
    without_field_evidence = inventory()
    without_field_evidence["cases"][0]["required_evidence"] = ["/name"]
    with pytest.raises(DocumentError, match="required_evidence"):
        evaluate(without_field_evidence, submission(candidate()), {"snapshot-a": snapshot()})
    malformed_snapshot = snapshot()
    malformed_snapshot["pages"][0]["blocks"][0]["text"] = 123
    with pytest.raises(DocumentError, match="snapshot"):
        evaluate(inventory(), submission(candidate()), {"snapshot-a": malformed_snapshot})
    malformed_span = candidate()
    malformed_span["evidence"]["/name"][0]["block_id"] = []
    with pytest.raises(DocumentError, match="evidence"):
        evaluate(inventory(), submission(malformed_span), {"snapshot-a": snapshot()})
    empty_text_reference = inventory(reviewed=True, text_reference=True)
    empty_text_reference["cases"][0]["expected"]["/text_md"] = ""
    empty_text_candidate = candidate(text_reference=True)
    empty_text_candidate["text_md"] = ""
    with pytest.raises(DocumentError, match="text_md"):
        evaluate(
            empty_text_reference,
            submission(empty_text_candidate),
            {"snapshot-a": snapshot()},
        )


def test_store_idempotency_conflict_and_run_rollback(tmp_path: Path) -> None:
    db = tmp_path / "reference.sqlite"
    snap = snapshot()
    inv = inventory(reviewed=True, text_reference=True)
    sub = submission(candidate(text_reference=True))
    report = evaluate(inv, sub, {"snapshot-a": snap})
    with ReferenceStore(db) as store:
        store.save_snapshot(snap, b"<html>raw</html>")
        store.save_run("run-1", inv, sub, report)
        store.save_run("run-1", inv, sub, report)
        store.save_run("run-1", inv, sub, {"new_evaluator": "must not replace immutable report"})
        assert store.load_run("run-1")["report"] == report
        assert store.load_snapshot("snapshot-a")["pages"] == snap["pages"]
        assert store.load_artifact("snapshot-a") == b"<html>raw</html>"
        changed = copy.deepcopy(sub)
        changed["model"] = "other-model"
        with pytest.raises(DocumentError, match="conflict"):
            store.save_run("run-1", inv, changed, report)
        with pytest.raises(DocumentError):
            store.save_run("run-2", inv, changed, {"not": object()})
        with pytest.raises(DocumentError, match="not found"):
            store.load_run("run-2")


def test_store_refuses_unrelated_database_and_read_only_lookup_does_not_create(
    tmp_path: Path,
) -> None:
    foreign = tmp_path / "legacy.sqlite"
    with sqlite3.connect(foreign) as db:
        db.execute("CREATE TABLE legacy_records (id TEXT)")
    with pytest.raises(DocumentError, match="unrelated"):
        with ReferenceStore(foreign):
            pass
    with sqlite3.connect(foreign) as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [
            ("legacy_records",)
        ]
    missing = tmp_path / "missing.sqlite"
    with pytest.raises(DocumentError, match="not found"):
        with ReferenceStore(missing, readonly=True):
            pass
    assert not missing.exists()


def test_capture_keeps_geometry_unicode_raw_and_empty_pages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from owlsperch.reference import capture

    pdf = tmp_path / "fake.pdf"
    pdf.write_bytes(b"fake pdf bytes")
    raw = (
        '<html xmlns="http://www.w3.org/1999/xhtml"><body><doc>'
        '<page width="612" height="792"><flow><block xMin="1" yMin="2" xMax="100" yMax="20">'
        '<line xMin="1" yMin="2" xMax="100" yMax="20">'
        '<word xMin="1" yMin="2" xMax="20" yMax="10">Éclair</word>'
        "</line></block></flow></page>"
        '<page width="612" height="792"/></doc></body></html>'
    )

    def fake_poppler(source: Path, output: Path, first: int, last: int) -> None:
        assert (source, first) == (pdf, 10)
        output.write_text(raw)

    monkeypatch.setattr(capture, "run_pdftotext", fake_poppler)
    snap, artifact = capture_source("test", pdf, (10, 11), extractor="poppler-test")
    assert artifact == raw.encode()
    assert snap["pages"][0]["pdf_page_index"] == 10
    assert snap["pages"][0]["blocks"][0]["text"] == "Éclair"
    assert snap["pages"][0]["blocks"][0]["words"][0]["bbox"] == [1.0, 2.0, 20.0, 10.0]
    assert snap["pages"][1]["text_empty"] is True
    assert snap["capture_format_version"] == 1
    monkeypatch.setattr(capture, "CAPTURE_FORMAT_VERSION", 2)
    newer, _ = capture_source("test", pdf, (10, 11), extractor="poppler-test")
    assert newer["snapshot_id"] != snap["snapshot_id"]
    with pytest.raises(DocumentError, match="pages"):
        capture_source("test", pdf, (10, 12), extractor="poppler-test")


def test_capture_rejects_pdf_replaced_during_poppler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from owlsperch.reference import capture

    pdf = tmp_path / "changing.pdf"
    pdf.write_bytes(b"source at hash time")

    def replaced_pdf(_source: Path, output: Path, _first: int, _last: int) -> str:
        output.write_text(
            '<html xmlns="http://www.w3.org/1999/xhtml"><body><doc>'
            '<page width="612" height="792"/></doc></body></html>'
        )
        pdf.write_bytes(b"source used by extractor")
        return ""

    monkeypatch.setattr(capture, "run_pdftotext", replaced_pdf)
    with pytest.raises(DocumentError, match="changed during capture"):
        capture_source("test", pdf, (1, 1), extractor="poppler-test")


def test_malformed_poppler_output_is_actionable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from owlsperch.reference import capture

    pdf = tmp_path / "fake.pdf"
    pdf.write_bytes(b"synthetic PDF")

    def broken_poppler(_source: Path, output: Path, _first: int, _last: int) -> str:
        output.write_text("<broken>")
        return ""

    monkeypatch.setattr(capture, "run_pdftotext", broken_poppler)
    with pytest.raises(DocumentError, match="bbox"):
        capture_source("test", pdf, (1, 1), extractor="poppler-test")


def test_override_manifest_book_is_eligible_for_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from owlsperch.manifest import default_manifest_path, load_manifest
    from owlsperch.reference import runner

    entry = next(
        item for item in load_manifest(default_manifest_path()) if item.book_id == "phb1-errata"
    )
    pdf = tmp_path / entry.file
    pdf.write_bytes(b"synthetic override PDF")
    monkeypatch.setattr(runner, "default_pdf_dir", lambda: tmp_path)
    monkeypatch.setattr(runner, "_poppler_info", lambda _path: (3, "poppler-test", ""))

    def fake_capture(
        book_id: str,
        pdf_path: Path,
        page_range: tuple[int, int],
        *,
        extractor: str,
        source_page_count: int,
    ) -> tuple[dict[str, Any], bytes]:
        assert (book_id, pdf_path, page_range, source_page_count) == ("phb1-errata", pdf, (1, 3), 3)
        return snapshot(), b"raw"

    monkeypatch.setattr(runner, "capture_source", fake_capture)
    result = runner.capture("phb1-errata", (1, 3), tmp_path / "pilot.sqlite")
    assert result["snapshot_id"] == "snapshot-a"


def test_cli_evaluate_failure_browse_typed_filter_and_source(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from owlsperch.cli import main

    db = tmp_path / "reference.sqlite"
    inventory_path = tmp_path / "inventory.json"
    candidates_path = tmp_path / "candidates.json"
    with ReferenceStore(db) as store:
        store.save_snapshot(snapshot(), b"raw")
    inv = inventory()
    inv["cases"][0]["expected"]["/fields/active"] = False
    inv["cases"][0]["required_evidence"].append("/fields/active")
    entry = candidate()
    entry["fields"]["active"] = False
    entry["evidence"]["/fields/active"] = [
        {"block_id": "block-a", "start": 23, "end": 31, "quote": "Wizard 3"}
    ]
    inventory_path.write_text(json.dumps(inv))
    candidates_path.write_text(json.dumps(submission(entry)))
    assert (
        main(
            [
                "reference",
                "evaluate",
                str(inventory_path),
                str(candidates_path),
                "--db",
                str(db),
                "--run-id",
                "run-1",
            ]
        )
        == 1
    )
    result = json.loads(capsys.readouterr().out)
    assert result["counts"]["passed"] == 1
    assert (
        main(
            [
                "reference",
                "browse",
                "--db",
                str(db),
                "--run-id",
                "run-1",
                "--field",
                "/fields/active",
                "--equals",
                "false",
            ]
        )
        == 0
    )
    browse = json.loads(capsys.readouterr().out)
    assert len(browse["results"]) == 1
    assert browse["coverage"]["expected"] == 1
    assert (
        main(
            [
                "reference",
                "browse",
                "--db",
                str(db),
                "--run-id",
                "run-1",
                "--field",
                "/fields/active",
                "--equals",
                "0",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["results"] == []
    assert (
        main(
            [
                "reference",
                "browse",
                "--db",
                str(db),
                "--run-id",
                "run-1",
                "--field",
                "/fields/active",
                "--equals",
                "NaN",
            ]
        )
        == 1
    )
    assert "non-finite" in capsys.readouterr().err
    assert main(["reference", "source", "--db", str(db), "--snapshot-id", "snapshot-a"]) == 0
    assert json.loads(capsys.readouterr().out)["pages"][0]["blocks"][0]["block_id"] == "block-a"


def test_cli_reports_invalid_database_without_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from owlsperch.cli import main

    db = tmp_path / "not-sqlite.db"
    db.write_bytes(b"not a SQLite database")
    assert main(["reference", "source", "--db", str(db), "--snapshot-id", "anything"]) == 1
    assert "error:" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("file", "old", "replacement"),
    [
        (
            "inventory",
            '"/fields/school": "Evocation"',
            '"/fields/school": "Necromancy", "/fields/school": "Evocation"',
        ),
        ("candidates", '"school": "Evocation"', '"school": "Necromancy", "school": "Evocation"'),
        ("candidates", '"level": 3', '"level": NaN'),
        ("candidates", '"level": 3', '"level": Infinity'),
        ("candidates", '"level": 3', '"level": -Infinity'),
    ],
)
def test_cli_rejects_ambiguous_or_nonfinite_json_without_saving_run(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    file: str,
    old: str,
    replacement: str,
) -> None:
    from owlsperch.cli import main

    db = tmp_path / "reference.sqlite"
    with ReferenceStore(db) as store:
        store.save_snapshot(snapshot(), b"raw")
    documents = {
        "inventory": tmp_path / "inventory.json",
        "candidates": tmp_path / "candidates.json",
    }
    documents["inventory"].write_text(json.dumps(inventory(reviewed=True, text_reference=True)))
    documents["candidates"].write_text(json.dumps(submission(candidate(text_reference=True))))
    text = documents[file].read_text()
    assert old in text
    documents[file].write_text(text.replace(old, replacement, 1))
    assert (
        main(
            [
                "reference",
                "evaluate",
                str(documents["inventory"]),
                str(documents["candidates"]),
                "--db",
                str(db),
                "--run-id",
                "ambiguous",
            ]
        )
        == 1
    )
    assert "error:" in capsys.readouterr().err
    with ReferenceStore(db, readonly=True) as store:
        with pytest.raises(DocumentError, match="not found"):
            store.load_run("ambiguous")
