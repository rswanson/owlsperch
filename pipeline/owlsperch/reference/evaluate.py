"""Pure validation and scoring of independently inventoried occurrences."""

from __future__ import annotations

from collections import Counter
from typing import Any, cast


class DocumentError(ValueError):
    """Malformed pilot input or conflicting immutable identity."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise DocumentError(message)


def resolve_pointer(document: Any, pointer: str) -> Any:
    """Resolve an RFC 6901 pointer; missing paths raise DocumentError."""
    _require(
        isinstance(pointer, str) and pointer.startswith("/"), f"invalid JSON pointer: {pointer!r}"
    )
    value = document
    for token in pointer[1:].split("/"):
        _require(
            not any(
                token[i] == "~" and (i + 1 == len(token) or token[i + 1] not in "01")
                for i in range(len(token))
            ),
            f"invalid JSON pointer escape: {pointer!r}",
        )
        key = token.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict) and key in value:
            value = value[key]
        elif (
            isinstance(value, list)
            and key.isascii()
            and key.isdecimal()
            and (key == "0" or not key.startswith("0"))
            and int(key) < len(value)
        ):
            value = value[int(key)]
        else:
            raise DocumentError(f"JSON pointer {pointer!r} does not resolve")
    return value


def _valid_pointer(pointer: Any) -> None:
    _require(
        isinstance(pointer, str) and pointer.startswith("/"), f"invalid JSON pointer: {pointer!r}"
    )
    for token in pointer[1:].split("/"):
        _require(
            not any(
                token[i] == "~" and (i + 1 == len(token) or token[i + 1] not in "01")
                for i in range(len(token))
            ),
            f"invalid JSON pointer escape: {pointer!r}",
        )


def typed_equal(left: Any, right: Any) -> bool:
    """JSON structural equality without Python's bool/int equivalence."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(typed_equal(left[k], right[k]) for k in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(
            typed_equal(a, b) for a, b in zip(left, right, strict=True)
        )
    return bool(left == right)


def _string(value: Any, label: str) -> None:
    _require(isinstance(value, str) and bool(value.strip()), f"{label} must be a nonempty string")


def _validate_snapshot(snapshot: Any, snapshot_id: str) -> set[str]:
    _require(
        isinstance(snapshot, dict) and snapshot.get("snapshot_id") == snapshot_id,
        f"stale snapshot identity: {snapshot_id}",
    )
    pages = snapshot.get("pages")
    _require(isinstance(pages, list) and bool(pages), f"snapshot {snapshot_id} has invalid pages")
    ids: set[str] = set()
    for page in pages:
        _require(isinstance(page, dict), f"snapshot {snapshot_id} has invalid page")
        blocks = page.get("blocks")
        _require(isinstance(blocks, list), f"snapshot {snapshot_id} has invalid blocks")
        for block in blocks:
            _require(isinstance(block, dict), f"snapshot {snapshot_id} has invalid block")
            block_id, text = block.get("block_id"), block.get("text")
            _require(
                isinstance(block_id, str) and bool(block_id) and isinstance(text, str),
                f"snapshot {snapshot_id} has invalid block ID or text",
            )
            _require(block_id not in ids, f"snapshot {snapshot_id} has duplicate block ID")
            ids.add(block_id)
    return ids


def _validate_inventory(inventory: Any, snapshots: dict[str, Any]) -> list[dict[str, Any]]:
    _require(
        isinstance(inventory, dict)
        and type(inventory.get("version")) is int
        and inventory["version"] == 1,
        "inventory version must be 1",
    )
    _string(inventory.get("dataset_id"), "dataset_id")
    _require(inventory.get("review_status") in ("provisional", "reviewed"), "invalid review_status")
    cases = inventory.get("cases")
    _require(isinstance(cases, list) and bool(cases), "inventory cases must be a nonempty list")
    seen: set[str] = set()
    for index, case in enumerate(cases):
        label = f"case {index}"
        _require(isinstance(case, dict), f"{label} must be an object")
        for key in ("case_id", "snapshot_id", "name", "type"):
            _string(case.get(key), f"{label} {key}")
        case_id = case["case_id"]
        _require(case_id not in seen, f"duplicate inventory case_id: {case_id}")
        seen.add(case_id)
        snapshot_id = case["snapshot_id"]
        _require(snapshot_id in snapshots, f"unresolved snapshot: {snapshot_id}")
        available = _validate_snapshot(snapshots[snapshot_id], snapshot_id)
        blocks = case.get("block_ids")
        _require(
            isinstance(blocks, list) and bool(blocks) and all(isinstance(b, str) for b in blocks),
            f"{label} block_ids must be a nonempty string list",
        )
        _require(set(blocks) <= available, f"{label} references block outside snapshot")
        expected = case.get("expected")
        required = case.get("required_evidence")
        _require(isinstance(expected, dict), f"{label} expected must be an object")
        _require(
            isinstance(required, list) and all(isinstance(p, str) for p in required),
            f"{label} required_evidence must be a pointer list",
        )
        for pointer in [*expected, *required]:
            _valid_pointer(pointer)
        if "/text_md" in expected:
            _string(expected["/text_md"], f"{label} expected /text_md")
        field_pointers = {pointer for pointer in expected if pointer.startswith("/fields/")}
        _require(bool(field_pointers), f"{label} requires at least one /fields/ assertion")
        _require(
            {"/name", *field_pointers} <= set(required),
            f"{label} required_evidence must include /name and all expected field pointers",
        )
    return cast(list[dict[str, Any]], cases)


def _validate_submission(submission: Any) -> list[dict[str, Any]]:
    _require(
        isinstance(submission, dict)
        and type(submission.get("version")) is int
        and submission["version"] == 1,
        "candidate document version must be 1",
    )
    _string(submission.get("model"), "model")
    _string(submission.get("prompt_version"), "prompt_version")
    candidates = submission.get("candidates")
    _require(isinstance(candidates, list), "candidates must be a list")
    for index, entry in enumerate(candidates):
        label = f"candidate {index}"
        _require(isinstance(entry, dict), f"{label} must be an object")
        for key in ("case_id", "name", "type"):
            _string(entry.get(key), f"{label} {key}")
        _require(isinstance(entry.get("text_md"), str), f"{label} text_md must be a string")
        _require(isinstance(entry.get("fields"), dict), f"{label} fields must be an object")
        evidence = entry.get("evidence")
        _require(isinstance(evidence, dict), f"{label} evidence must be an object")
        for pointer, spans in evidence.items():
            _valid_pointer(pointer)
            _require(isinstance(spans, list), f"{label} evidence {pointer} must be a span list")
            for span in spans:
                _require(isinstance(span, dict), f"{label} evidence span must be an object")
                _require(
                    isinstance(span.get("block_id"), str),
                    f"{label} evidence block_id must be a string",
                )
    return cast(list[dict[str, Any]], candidates)


def _evidence_diagnostics(
    case: dict[str, Any], entry: dict[str, Any], snap: dict[str, Any]
) -> list[str]:
    diagnostics: list[str] = []
    blocks = {
        b["block_id"]: b["text"]
        for p in snap["pages"]
        for b in p["blocks"]
        if b.get("block_id") in case["block_ids"]
    }
    evidence = entry["evidence"]
    for pointer in case["required_evidence"]:
        if not evidence.get(pointer):
            diagnostics.append(f"missing required evidence {pointer}")
        try:
            resolve_pointer(entry, pointer)
        except DocumentError:
            diagnostics.append(f"required evidence pointer does not resolve: {pointer}")
    for pointer, spans in evidence.items():
        try:
            resolve_pointer(entry, pointer)
        except DocumentError:
            diagnostics.append(f"evidence pointer does not resolve: {pointer}")
        if not spans:
            diagnostics.append(f"empty evidence {pointer}")
        for span in spans:
            block_id = span.get("block_id")
            start, end, quote = span.get("start"), span.get("end"), span.get("quote")
            if block_id not in blocks:
                diagnostics.append(f"evidence block outside case snapshot: {block_id}")
            elif (
                type(start) is not int
                or type(end) is not int
                or not isinstance(quote, str)
                or not quote
                or start < 0
                or end <= start
                or end > len(blocks[block_id])
                or blocks[block_id][start:end] != quote
            ):
                diagnostics.append(f"invalid evidence span or quote for {pointer}")
    return diagnostics


def evaluate(
    inventory: dict[str, Any], submission: dict[str, Any], snapshots: dict[str, Any]
) -> dict[str, Any]:
    """Score all inventory cases, preserving missing cases in the denominator."""
    _require(isinstance(snapshots, dict), "snapshots must be an object")
    cases = _validate_inventory(inventory, snapshots)
    candidates = _validate_submission(submission)
    ids = Counter(entry["case_id"] for entry in candidates)
    known = {case["case_id"] for case in cases}
    extras = sum(count for case_id, count in ids.items() if case_id not in known)
    duplicates = sum(count - 1 for count in ids.values() if count > 1)
    by_id = {entry["case_id"]: entry for entry in candidates if ids[entry["case_id"]] == 1}
    counts = {
        "expected": len(cases),
        "submitted": len(candidates),
        "missing": 0,
        "extras": extras,
        "duplicates": duplicates,
        "passed": 0,
        "correct_fields": 0,
        "fields_passed_cases": 0,
        "expected_field_assertions": 0,
        "correct_field_assertions": 0,
        "valid_evidence": 0,
        "text_present": 0,
        "correct_text": 0,
        "unscored_text": 0,
        "unreviewed": len(cases) if inventory["review_status"] == "provisional" else 0,
    }
    results: list[dict[str, Any]] = []
    all_text_referenced = True
    for case in cases:
        field_pointers = [pointer for pointer in case["expected"] if pointer.startswith("/fields/")]
        counts["expected_field_assertions"] += len(field_pointers)
        case_id = case["case_id"]
        entry = by_id.get(case_id)
        if entry is None:
            counts["missing"] += 1
            reason = "duplicate candidate" if ids[case_id] > 1 else "missing candidate"
            results.append({"case_id": case_id, "passed": False, "diagnostics": [reason]})
            all_text_referenced = all_text_referenced and "/text_md" in case["expected"]
            continue
        diagnostics: list[str] = []
        if entry["name"] != case["name"] or entry["type"] != case["type"]:
            diagnostics.append("name or type differs from inventory")
        fields_ok = True
        text_present = bool(entry["text_md"].strip())
        if text_present:
            counts["text_present"] += 1
        if "/text_md" not in case["expected"]:
            counts["unscored_text"] += 1
        for pointer, expected in case["expected"].items():
            if pointer == "/text_md":
                continue
            try:
                actual = resolve_pointer(entry, pointer)
            except DocumentError:
                actual = None
                diagnostics.append(f"missing expected {pointer}")
                fields_ok = False
                continue
            if not typed_equal(actual, expected):
                diagnostics.append(f"incorrect expected {pointer}")
                fields_ok = False
            elif pointer in field_pointers:
                counts["correct_field_assertions"] += 1
        if fields_ok:
            counts["correct_fields"] += 1
            counts["fields_passed_cases"] += 1
        all_text_referenced = all_text_referenced and "/text_md" in case["expected"]
        if "/text_md" in case["expected"] and typed_equal(
            entry["text_md"], case["expected"]["/text_md"]
        ):
            counts["correct_text"] += 1
        elif "/text_md" in case["expected"] or not text_present:
            diagnostics.append("readable text missing or incorrect")
        evidence_errors = _evidence_diagnostics(case, entry, snapshots[case["snapshot_id"]])
        if not evidence_errors:
            counts["valid_evidence"] += 1
        diagnostics.extend(evidence_errors)
        passed = not diagnostics
        if passed:
            counts["passed"] += 1
        results.append(
            {"case_id": case_id, "passed": passed, "diagnostics": diagnostics, "candidate": entry}
        )
    release_ready = (
        inventory["review_status"] == "reviewed"
        and all_text_referenced
        and counts["passed"] == counts["expected"]
        and not extras
        and not duplicates
    )
    return {
        "dataset_id": inventory["dataset_id"],
        "model": submission["model"],
        "prompt_version": submission["prompt_version"],
        "review_status": inventory["review_status"],
        "release_ready": release_ready,
        "counts": counts,
        "cases": results,
        "diagnostics": ([f"{extras} extra candidate(s)"] if extras else [])
        + ([f"{duplicates} duplicate candidate ID(s)"] if duplicates else []),
    }
