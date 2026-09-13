"""Orchestration for `owlsperch validate <book_id|all> [--json] [--stale]
[--bump-compatible]` (spec 4.4, 4.5, 4.14; batch B4).

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

`--bump-compatible` mode (B6 follow-up, for a backward-compatible schema
change like widening a field's type to also accept `null`) instead visits
every stale record and re-checks it as if its `schema_version` already were
the type's current version: a record with no other errors under the
current schema differs from the current schema only by the version number,
so its `schema_version` is rewritten in place (atomic write) to the current
version. A record that's stale *and* still has a real error under the
current schema (or whose type isn't registered at all) is left untouched
and reported as not bumped, with those errors. Exits 1 if anything was left
un-bumped, 0 otherwise. Like `--stale`, this never runs the checks above,
writes back to segments, or touches a record that isn't stale to begin
with.

Validation writes back to the originating segment (skipped for `--stale`,
and for a record whose segment can't be resolved at all): PASS sets the
segment `status` to `"done"` and `outcome` to `"validated"`, and appends the
record's path (relative to `$OWLSPERCH_DATA`) to the segment's `records`
list. FAIL appends `{tier, timestamp, errors, kind: "validation"}` to the
segment's `attempts` and advances it along the escalation ladder (batch B8,
`owlsperch.queue.ladder`): `tier` haiku -> sonnet -> opus, one attempt per
tier; a FAIL recorded while already on opus moves the segment to
`human/<book_id>/` instead (`status: "human"`, `outcome:
"escalation_exhausted"`), keeping every attempt. Both PASS and FAIL are
idempotent: rerunning on an unchanged record does not add a duplicate
record path, a duplicate identical attempt, or advance the ladder twice for
the same failure -- see `_write_back_fail` and `owlsperch.queue.ladder`'s
module docstring for the stale-re-validation case this guards against.

Batch B5's extract skill (`owlsperch queue complete`) records a subagent's
claimed record paths under the segment's `pending_records`, not `records`,
until they're proven to conform: PASS here moves a path from
`pending_records` into `records` (on top of the above); FAIL just drops it
from `pending_records` without ever adding it to `records`.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from owlsperch.fsutil import atomic_write_text
from owlsperch.queue.common import finish_after_failure
from owlsperch.queue.ladder import record_failure
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


def _schema_errors(validator: Any, instance: Any, *, prefix: str) -> list[str]:
    """Render a validator's errors as `"<prefix>.<path>: <message>"`
    strings, in a stable (path-sorted) order."""
    errors = sorted(validator.iter_errors(instance), key=lambda e: [str(p) for p in e.path])
    return [
        f"{prefix}.{'.'.join(str(p) for p in err.path) or '<root>'}: {err.message}"
        for err in errors
    ]


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

    def to_json(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "type": self.type,
            "schema_version": self.schema_version,
            "current_version": self.current_version,
        }


@dataclass
class BumpResult:
    path: str
    type: str
    bumped: bool
    from_version: int | None
    to_version: int
    #: Errors that kept a stale record from being bumped (empty when
    #: `bumped` is True).
    errors: list[str]

    def render(self) -> str:
        if self.bumped:
            return f"BUMPED {self.path}: schema_version {self.from_version} -> {self.to_version}"
        return f"NOT BUMPED {self.path}: " + "; ".join(self.errors)

    def to_json(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "type": self.type,
            "bumped": self.bumped,
            "from_version": self.from_version,
            "to_version": self.to_version,
            "errors": self.errors,
        }


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
    # B5: a record claimed via `owlsperch queue complete` lives in
    # pending_records until validated -- promote it out on PASS.
    if record_rel_path in segment_model.pending_records:
        segment_model.pending_records = [
            p for p in segment_model.pending_records if p != record_rel_path
        ]
    atomic_write_text(path, segment_model.model_dump_json(indent=2) + "\n")


def _write_back_fail(
    data_dir: Path,
    book_id: str,
    segment_id: str,
    *,
    tier: str,
    errors: list[str],
    record_rel_path: str | None = None,
) -> None:
    """Record a validation FAIL onto its originating segment and advance the
    escalation ladder (batch B8, `owlsperch.queue.ladder.record_failure`):
    `tier` haiku -> sonnet -> opus, with the failing record's own
    `extraction.tier` as the attempt's tier (so a stale re-validation of an
    old-tier record -- one whose `extraction.tier` is behind the segment's
    current tier -- never double-advances the ladder; see the ladder module
    docstring). A FAIL recorded while already on opus moves the segment to
    `human/<book_id>/` instead (`outcome: "escalation_exhausted"`), keeping
    every attempt.

    `record_rel_path`, if given and still listed in the segment's
    `pending_records` (i.e. claimed by `owlsperch queue complete` but never
    promoted to `records` by a prior PASS), is both dropped from
    `pending_records` *and* deleted from disk -- a wrong-slug or otherwise
    bogus file from a failed attempt must not linger forever to be picked
    up (or silently skipped) by a later `owlsperch build-db`. A path already
    in `records` (a previously-validated record failing only now, e.g. after
    a schema change) is never deleted -- only dropped from `pending_records`
    if for some reason it was listed in both.

    The segment write-back happens *before* the file is unlinked: if a
    crash lands between the two, the orphaned record file on disk (already
    dropped from `pending_records`, so nothing will ever look at it again)
    is a harmless leak, unlike the reverse order, which could leave
    `pending_records` naming a file that no longer exists.
    """
    path = segment_path(data_dir, book_id, segment_id)
    segment_model = Segment.model_validate_json(path.read_text())

    should_delete_record_file = False
    if record_rel_path is not None and record_rel_path in segment_model.pending_records:
        segment_model.pending_records = [
            p for p in segment_model.pending_records if p != record_rel_path
        ]
        should_delete_record_file = record_rel_path not in segment_model.records

    result = record_failure(segment_model, errors, kind="validation", tier=tier)
    finish_after_failure(data_dir, path, segment_model, result)

    if should_delete_record_file and record_rel_path is not None:
        record_file = data_dir / record_rel_path
        if record_file.is_file():
            record_file.unlink()


def validate_record(
    record: dict[str, Any],
    *,
    type_dir: str,
    compiled: CompiledSchemas,
    segment: dict[str, Any] | None,
) -> list[str]:
    """Pure validation of one already-loaded record: JSON Schema conformance
    (envelope + type fields), envelope/type-specific consistency checks, and
    the page-within-segment check -- everything `validate_record_file` does
    *except* loading the record/segment from disk and writing back to the
    segment. No I/O, no side effects: reused by `owlsperch build-db` (batch
    B6), which must decide PASS/FAIL for every record without mutating
    segment files as a side effect of a read-only build.

    `segment` is the already-resolved originating segment (or `None` if it
    couldn't be found/resolved), matching what `check_pages_within_segment`
    expects.
    """
    errors = _schema_errors(compiled.envelope_validator(), record, prefix="envelope")

    registry_version: int | None = None
    if type_dir in compiled.registry.types:
        registry_version = compiled.registry.types[type_dir].version
        type_validator = compiled.type_validator(type_dir)
        if type_validator is not None:
            errors.extend(_schema_errors(type_validator, record.get("fields", {}), prefix="fields"))
    else:
        errors.append(f"unknown type directory '{type_dir}'")

    errors.extend(
        check_envelope_consistency(record, type_dir=type_dir, registry_version=registry_version)
    )

    field_check = TYPE_FIELD_CHECKS.get(type_dir)
    if field_check is not None:
        errors.extend(field_check(record))

    errors.extend(check_pages_within_segment(record, segment))
    return errors


def validate_record_file(path: Path, *, data_dir: Path, compiled: CompiledSchemas) -> RecordResult:
    rel_path = path.relative_to(data_dir).as_posix()
    type_dir = path.parent.name

    try:
        record = load_json(path)
    except LoadError as exc:
        return RecordResult(
            path=rel_path, status="FAIL", errors=[str(exc)], segment_id=None, type=None
        )

    book_id = record.get("book_id") if isinstance(record.get("book_id"), str) else None
    extraction_raw = record.get("extraction")
    extraction: dict[str, Any] = extraction_raw if isinstance(extraction_raw, dict) else {}
    segment_id_raw = extraction.get("segment_id")
    segment_id = segment_id_raw if isinstance(segment_id_raw, str) else None

    segment: dict[str, Any] | None = None
    if book_id is not None and segment_id is not None:
        segment = load_segment(data_dir, book_id, segment_id)

    errors = validate_record(record, type_dir=type_dir, compiled=compiled, segment=segment)
    status = "FAIL" if errors else "PASS"

    if book_id is not None and segment_id is not None and segment is not None:
        if status == "PASS":
            _write_back_pass(data_dir, book_id, segment_id, rel_path)
        else:
            tier_raw = extraction.get("tier")
            tier = tier_raw if isinstance(tier_raw, str) else str(segment.get("tier", "haiku"))
            _write_back_fail(
                data_dir, book_id, segment_id, tier=tier, errors=errors, record_rel_path=rel_path
            )

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


def _resolve_segment_for_record(data_dir: Path, record: dict[str, Any]) -> dict[str, Any] | None:
    book_id = record.get("book_id") if isinstance(record.get("book_id"), str) else None
    extraction_raw = record.get("extraction")
    extraction: dict[str, Any] = extraction_raw if isinstance(extraction_raw, dict) else {}
    segment_id_raw = extraction.get("segment_id")
    segment_id = segment_id_raw if isinstance(segment_id_raw, str) else None
    if book_id is None or segment_id is None:
        return None
    return load_segment(data_dir, book_id, segment_id)


def find_bump_candidates(
    data_dir: Path, book_id: str, compiled: CompiledSchemas
) -> list[BumpResult]:
    """Every stale record (per `find_stale_records`'s definition), each
    re-checked as though its `schema_version` were already the type's
    current registry version -- `bumped=True` iff that leaves zero errors,
    meaning the only thing standing between this record and the current
    schema is the version number itself. Read-only: doesn't write anything;
    `run_validate` applies the actual bump for every `bumped=True` result.
    """
    results: list[BumpResult] = []
    for path in _record_files_for(data_dir, book_id):
        type_dir = path.parent.name
        try:
            record = load_json(path)
        except LoadError:
            continue
        schema_version = record.get("schema_version")
        version = schema_version if isinstance(schema_version, int) else None

        info = compiled.registry.types.get(type_dir)
        if info is None:
            # Matches this module's docstring: an unregistered type is
            # reported as not bumped (with an explanatory error), not
            # silently dropped from the results.
            results.append(
                BumpResult(
                    path=path.relative_to(data_dir).as_posix(),
                    type=type_dir,
                    bumped=False,
                    from_version=version,
                    to_version=version if version is not None else 0,
                    errors=[f"unregistered type: {type_dir}"],
                )
            )
            continue

        if version is not None and version >= info.version:
            continue  # not stale -- nothing to bump

        segment = _resolve_segment_for_record(data_dir, record)
        candidate = dict(record)
        candidate["schema_version"] = info.version
        errors = validate_record(candidate, type_dir=type_dir, compiled=compiled, segment=segment)

        results.append(
            BumpResult(
                path=path.relative_to(data_dir).as_posix(),
                type=type_dir,
                bumped=not errors,
                from_version=version,
                to_version=info.version,
                errors=errors,
            )
        )
    return results


def _apply_bump(data_dir: Path, result: BumpResult) -> None:
    path = data_dir / result.path
    record = load_json(path)
    record["schema_version"] = result.to_version
    atomic_write_text(path, json.dumps(record, indent=2) + "\n")


def run_validate(
    book_id: str,
    *,
    data_dir: Path | None = None,
    schemas_dir: Path | None = None,
    json_output: bool = False,
    stale: bool = False,
    bump_compatible: bool = False,
    out: Any = None,
) -> int:
    out = out if out is not None else sys.stdout
    data_dir = data_dir if data_dir is not None else default_data_dir()
    compiled = CompiledSchemas.load(schemas_dir)

    if bump_compatible:
        bump_results = find_bump_candidates(data_dir, book_id, compiled)
        for bump_result in bump_results:
            if bump_result.bumped:
                _apply_bump(data_dir, bump_result)

        if json_output:
            print(json.dumps([r.to_json() for r in bump_results]), file=out)
        else:
            for bump_result in bump_results:
                print(bump_result.render(), file=out)
            bumped_count = sum(1 for r in bump_results if r.bumped)
            not_bumped_count = len(bump_results) - bumped_count
            print(
                f"{bumped_count} bumped, {not_bumped_count} not bumped, "
                f"{len(bump_results)} stale total",
                file=out,
            )
        return 1 if any(not r.bumped for r in bump_results) else 0

    if stale:
        stale_records = find_stale_records(data_dir, book_id, compiled)
        if json_output:
            print(json.dumps([r.to_json() for r in stale_records]), file=out)
            return 0
        for record in stale_records:
            print(record.render(), file=out)
        if not stale_records:
            print("no stale records", file=out)
        return 0

    files = _record_files_for(data_dir, book_id)
    record_results = [
        validate_record_file(path, data_dir=data_dir, compiled=compiled) for path in files
    ]
    failed = sum(1 for r in record_results if r.status == "FAIL")

    if json_output:
        print(json.dumps([r.to_json() for r in record_results]), file=out)
        return 1 if failed else 0

    for result in record_results:
        print(result.render(), file=out)
    passed = len(record_results) - failed
    print(f"{passed} passed, {failed} failed, {len(record_results)} total", file=out)

    return 1 if failed else 0
