"""CLI operations for isolated reference captures, evaluations, and browsing."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any, NoReturn, cast

from owlsperch.manifest import default_manifest_path, default_pdf_dir, load_manifest, status_for
from owlsperch.reference.capture import capture_source
from owlsperch.reference.evaluate import DocumentError, evaluate, resolve_pointer, typed_equal
from owlsperch.reference.store import ReferenceStore


def _poppler_info(pdf_path: Path) -> tuple[int, str, str]:
    try:
        info = subprocess.run(
            ["pdfinfo", str(pdf_path)], capture_output=True, text=True, check=False, timeout=30
        )
        version = subprocess.run(
            ["pdftotext", "-v"], capture_output=True, text=True, check=False, timeout=10
        )
    except FileNotFoundError as exc:
        raise DocumentError(f"Poppler tool not installed: {exc.filename}") from exc
    except subprocess.TimeoutExpired as exc:
        raise DocumentError(f"Poppler timed out reading {pdf_path}") from exc
    if info.returncode:
        raise DocumentError(f"pdfinfo failed for {pdf_path}: {info.stderr.strip()}")
    if version.returncode:
        raise DocumentError(f"pdftotext -v failed: {version.stderr.strip()}")
    match = re.search(r"^Pages:\s+(\d+)\s*$", info.stdout, re.MULTILINE)
    if match is None:
        raise DocumentError(f"pdfinfo did not report page count for {pdf_path}")
    version_line = (version.stderr or version.stdout).splitlines()[0].strip()
    return int(match.group(1)), version_line, info.stderr.strip()


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    document: dict[str, Any] = {}
    for key, value in pairs:
        if key in document:
            raise DocumentError(f"duplicate JSON object key: {key!r}")
        document[key] = value
    return document


def _reject_constant(value: str) -> NoReturn:
    raise DocumentError(f"non-finite JSON number is invalid: {value}")


def strict_json_loads(value: str) -> Any:
    """Decode JSON without silently collapsing keys or accepting NaN/Infinity."""
    return json.loads(value, object_pairs_hook=_unique_pairs, parse_constant=_reject_constant)


def _load_json(path: Path) -> dict[str, Any]:
    try:
        document = strict_json_loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DocumentError(f"cannot read JSON {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise DocumentError(f"JSON document must be an object: {path}")
    return document


def capture(book_id: str, page_range: tuple[int, int], db_path: Path) -> dict[str, Any]:
    entries = load_manifest(default_manifest_path())
    entry = next((item for item in entries if item.book_id == book_id), None)
    if entry is None or status_for(entry, entries) not in {"in_scope", "override"}:
        raise DocumentError(f"book {book_id!r} is not an eligible manifest source")
    pdf_root = default_pdf_dir().resolve()
    pdf_path = (pdf_root / entry.file).resolve()
    if not pdf_path.is_relative_to(pdf_root):
        raise DocumentError(f"book {book_id!r} PDF path escapes configured directory")
    if not pdf_path.is_file():
        raise DocumentError(f"PDF not found: {pdf_path}")
    page_count, extractor, info_warnings = _poppler_info(pdf_path)
    snapshot, artifact = capture_source(
        book_id, pdf_path, page_range, extractor=extractor, source_page_count=page_count
    )
    snapshot["source_info_warnings"] = info_warnings
    with ReferenceStore(db_path) as store:
        store.save_snapshot(snapshot, artifact)
    return snapshot


def source(db_path: Path, snapshot_id: str) -> dict[str, Any]:
    with ReferenceStore(db_path, readonly=True) as store:
        return store.load_snapshot(snapshot_id)


def evaluate_run(
    inventory_path: Path, candidates_path: Path, db_path: Path, run_id: str
) -> dict[str, Any]:
    inventory = _load_json(inventory_path)
    submission = _load_json(candidates_path)
    raw_cases = inventory.get("cases")
    if not isinstance(raw_cases, list):
        raise DocumentError("inventory cases must be a list")
    snapshot_ids = {case.get("snapshot_id") for case in raw_cases if isinstance(case, dict)}
    with ReferenceStore(db_path, readonly=True) as store:
        snapshots = {
            snapshot_id: store.load_snapshot(snapshot_id)
            for snapshot_id in snapshot_ids
            if isinstance(snapshot_id, str)
        }
    report = evaluate(inventory, submission, snapshots)
    with ReferenceStore(db_path) as store:
        store.save_run(run_id, inventory, submission, report)
        return cast(dict[str, Any], store.load_run(run_id)["report"])


def browse(
    db_path: Path,
    run_id: str,
    *,
    kind: str | None = None,
    field: str | None = None,
    equals: Any = None,
    has_filter: bool = False,
) -> dict[str, Any]:
    if (field is None) != (not has_filter):
        raise DocumentError("--field and --equals must be supplied together")
    if field is not None:
        # Validate pointer syntax even if there are no passing candidates.
        try:
            resolve_pointer({"_": 1}, field)
        except DocumentError as exc:
            if "does not resolve" not in str(exc):
                raise
    with ReferenceStore(db_path, readonly=True) as store:
        run = store.load_run(run_id)
    report = run["report"]
    results = []
    for case in report["cases"]:
        if not case["passed"]:
            continue
        candidate = case["candidate"]
        if kind is not None and candidate["type"] != kind:
            continue
        if field is not None:
            try:
                value = resolve_pointer(candidate, field)
            except DocumentError:
                continue
            if not typed_equal(value, equals):
                continue
        results.append(candidate)
    return {
        "run_id": run_id,
        "dataset_id": report["dataset_id"],
        "review_status": report["review_status"],
        "release_ready": report["release_ready"],
        "coverage": report["counts"],
        "results": results,
    }
