"""Orchestration for `owlsperch validate <book_id|all> [--json] [--stale]`
(spec 4.4, 4.5, 4.14; batch B4).

Per record file under `records/<book_id>/<type>/*.json`:

1. JSON Schema conformance (draft 2020-12): the whole record against
   `schemas/envelope.json`, and `fields` against the type's own schema from
   `schemas/registry.json` (e.g. `schemas/spell.json`). An unrecognized
   `<type>` directory is itself a FAIL.
2. Envelope consistency: the record's `type` matches the directory it was
   found in, `slug` is the ASCII-folded kebab-case of `name`, `id` is
   `<type>:<book_id>:<slug>`, and `schema_version` matches the type's
   current registry version.
3. Type-specific field checks (`owlsperch.validate.checks`), e.g. a spell
   needs a non-empty `school` and at least one entry in `levels`.
4. Every page the record cites must fall within its originating segment's
   page span -- the segment is looked up by `extraction.segment_id` under
   `segments/<book_id>/`; a missing segment file is a FAIL.

`--stale` mode instead lists every record whose `schema_version` is behind
the type's current registry version and always exits 0 -- it does not run
the checks above or write back to segments.

Validation writes back to the originating segment (skipped for `--stale`,
and for a record whose segment can't be resolved at all): PASS sets the
segment `status` to `"done"` and `outcome` to `"validated"`, and appends the
record's path (relative to `$OWLSPERCH_DATA`) to the segment's `records`
list. FAIL appends `{tier, timestamp, errors}` to the segment's `attempts`
and leaves `status` as `"pending"`. Both are idempotent: rerunning on an
unchanged record does not add a duplicate record path or a duplicate
identical attempt.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from owlsperch.segment.runner import Segment
from owlsperch.text.runner import default_data_dir
from owlsperch.validate.checks import (
    TYPE_FIELD_CHECKS,
    check_envelope_consistency,
    check_pages_within_segment,
)
from owlsperch.validate.loader import (
    CompiledSchemas,
    LoadError,
    discover_books_with_records,
    discover_record_files,
    load_json,
    load_segment,
    segment_path,
)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class RecordResult:
    path: str
    status: str  # "PASS" | "FAIL"
    errors: list[str]
    segment_id: str | None
    type: str | None

    def render(self) -> str:
        if self.status == "PASS":
            return f"PASS {self.path}"
        return f"FAIL {self.path}: " + "; ".join(self.errors)

    def to_json(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "status": self.status,
            "errors": self.errors,
            "segment_id": self.segment_id,
            "type": self.type,
        }


@dataclass
class StaleResult:
    path: str
    type: str
    schema_version: int | None
    current_version: int

    def render(self) -> str:
        return (
            f"STALE {self.path}: schema_version {self.schema_version} "
            f"< current {self.current_version} ({self.type})"
        )


def _record_files_for(data_dir: Path, book_id: str) -> list[Path]:
    if book_id == "all":
        files: list[Path] = []
        for book in discover_books_with_records(data_dir):
            files.extend(discover_record_files(data_dir, book))
        return files
    return discover_record_files(data_dir, book_id)


def _write_back_pass(data_dir: Path, book_id: str, segment_id: str, record_rel_path: str) -> None:
    path = segment_path(data_dir, book_id, segment_id)
    segment_model = Segment.model_validate_json(path.read_text())
    segment_model.status = "done"
    segment_model.outcome = "validated"
    if record_rel_path not in segment_model.records:
        segment_model.records = [*segment_model.records, record_rel_path]
    path.write_text(segment_model.model_dump_json(indent=2) + "\n")


def _write_back_fail(
    data_dir: Path, book_id: str, segment_id: str, *, tier: str, errors: list[str]
) -> None:
    path = segment_path(data_dir, book_id, segment_id)
    segment_model = Segment.model_validate_json(path.read_text())
    attempts = segment_model.attempts
    last = attempts[-1] if attempts else None
    already_recorded = isinstance(last, dict) and last.get("errors") == errors
    if not already_recorded:
        attempts = [*attempts, {"tier": tier, "timestamp": _now_iso(), "errors": errors}]
    segment_model.attempts = attempts
    path.write_text(segment_model.model_dump_json(indent=2) + "\n")


def validate_record_file(path: Path, *, data_dir: Path, compiled: CompiledSchemas) -> RecordResult:
    rel_path = path.relative_to(data_dir).as_posix()
    type_dir = path.parent.name

    try:
        record = load_json(path)
    except LoadError as exc:
        return RecordResult(
            path=rel_path, status="FAIL", errors=[str(exc)], segment_id=None, type=None
        )

    errors: list[str] = []

    for err in sorted(
        compiled.envelope_validator().iter_errors(record), key=lambda e: [str(p) for p in e.path]
    ):
        location = ".".join(str(p) for p in err.path) or "<root>"
        errors.append(f"envelope.{location}: {err.message}")

    registry_version: int | None = None
    if type_dir in compiled.registry.types:
        registry_version = compiled.registry.types[type_dir].version
        type_validator = compiled.type_validator(type_dir)
        fields = record.get("fields", {})
        if type_validator is not None:
            for err in sorted(
                type_validator.iter_errors(fields), key=lambda e: [str(p) for p in e.path]
            ):
                location = ".".join(str(p) for p in err.path) or "<root>"
                errors.append(f"fields.{location}: {err.message}")
    else:
        errors.append(f"unknown type directory '{type_dir}'")

    errors.extend(
        check_envelope_consistency(record, type_dir=type_dir, registry_version=registry_version)
    )

    field_check = TYPE_FIELD_CHECKS.get(type_dir)
    if field_check is not None:
        errors.extend(field_check(record))

    book_id = record.get("book_id") if isinstance(record.get("book_id"), str) else None
    extraction_raw = record.get("extraction")
    extraction: dict[str, Any] = extraction_raw if isinstance(extraction_raw, dict) else {}
    segment_id_raw = extraction.get("segment_id")
    segment_id = segment_id_raw if isinstance(segment_id_raw, str) else None

    segment: dict[str, Any] | None = None
    if book_id is not None and segment_id is not None:
        segment = load_segment(data_dir, book_id, segment_id)
    errors.extend(check_pages_within_segment(record, segment))

    status = "FAIL" if errors else "PASS"

    if book_id is not None and segment_id is not None and segment is not None:
        if status == "PASS":
            _write_back_pass(data_dir, book_id, segment_id, rel_path)
        else:
            tier_raw = extraction.get("tier")
            tier = tier_raw if isinstance(tier_raw, str) else str(segment.get("tier", "haiku"))
            _write_back_fail(data_dir, book_id, segment_id, tier=tier, errors=errors)

    return RecordResult(
        path=rel_path,
        status=status,
        errors=errors,
        segment_id=segment_id,
        type=record.get("type") if isinstance(record.get("type"), str) else None,
    )


def find_stale_records(
    data_dir: Path, book_id: str, compiled: CompiledSchemas
) -> list[StaleResult]:
    stale: list[StaleResult] = []
    for path in _record_files_for(data_dir, book_id):
        type_dir = path.parent.name
        info = compiled.registry.types.get(type_dir)
        if info is None:
            continue
        try:
            record = load_json(path)
        except LoadError:
            continue
        schema_version = record.get("schema_version")
        version = schema_version if isinstance(schema_version, int) else None
        if version is None or version < info.version:
            stale.append(
                StaleResult(
                    path=path.relative_to(data_dir).as_posix(),
                    type=type_dir,
                    schema_version=version,
                    current_version=info.version,
                )
            )
    return stale


def run_validate(
    book_id: str,
    *,
    data_dir: Path | None = None,
    schemas_dir: Path | None = None,
    json_output: bool = False,
    stale: bool = False,
    out: Any = None,
) -> int:
    out = out if out is not None else sys.stdout
    data_dir = data_dir if data_dir is not None else default_data_dir()
    compiled = CompiledSchemas.load(schemas_dir)

    if stale:
        stale_records = find_stale_records(data_dir, book_id, compiled)
        for record in stale_records:
            print(record.render(), file=out)
        if not stale_records:
            print("no stale records", file=out)
        return 0

    files = _record_files_for(data_dir, book_id)
    results = [validate_record_file(path, data_dir=data_dir, compiled=compiled) for path in files]
    failed = sum(1 for r in results if r.status == "FAIL")

    if json_output:
        print(json.dumps([r.to_json() for r in results]), file=out)
        return 1 if failed else 0

    for result in results:
        print(result.render(), file=out)
    passed = len(results) - failed
    print(f"{passed} passed, {failed} failed, {len(results)} total", file=out)

    return 1 if failed else 0
