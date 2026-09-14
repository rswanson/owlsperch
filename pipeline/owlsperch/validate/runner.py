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
and for a record whose segment can't be resolved at all) -- batch
B10c-mand4's "atomic per-segment validation write-back": every record file
discovered in one `validate` run is first checked in isolation
(`_check_record_file`, no I/O beyond loading), then the results are grouped
by `(book_id, segment_id)` and each segment is written back exactly ONCE
(`validate_record_files`), atomically, for the whole group. This matters
because a segment often owns more than one record from the same extraction
attempt (an entity plus a `table` record it cross-links, see
`owlsperch.queue.prompt`'s "Tables belonging to this entity" convention):
previously, PASS and FAIL were written back independently, per record, in
file-discovery order -- so a passing sibling record validated *after* a
failing one silently overwrote the FAIL's escalation with `status: "done"`,
burying the whole segment (and its real failure) forever. Now a segment's
write-back looks at every one of its records from this run together:

- `failed`: every record in the group that failed conformance this run.
- `missing`: every path still listed in the segment's own `pending_records`
  that this run's file discovery never found at all (deleted, moved, or
  never actually written) -- each contributes a `missing_record_path: <p>`
  error, exactly like a FAIL, and is left in `pending_records` (there's
  nothing on disk to drop).
- If either is non-empty, the WHOLE group is treated as a failure: `errors`
  (every failed record's own errors, then every `missing_record_path`) is
  recorded as one `{tier, timestamp, errors, kind: "validation"}` attempt
  and the segment is advanced along the escalation ladder (batch B8,
  `owlsperch.queue.ladder`): `tier` haiku -> sonnet -> opus, one attempt per
  tier; a FAIL recorded while already on opus moves the segment to
  `human/<book_id>/` instead (`status: "human"`, `outcome:
  "escalation_exhausted"`), keeping every attempt. The attempt's own `tier`
  is, in order: the first failing record's own `extraction.tier`; else the
  segment's `claim_tier` (batch B10c-mand4 -- the tier stamped by
  `owlsperch queue complete` when the current `pending_records` claim was
  registered, stable across repeated validate runs); else the segment's
  current `tier`. The middle rung exists for the pure `missing_record_path`
  case: there's no file on disk to read an `extraction.tier` from, and
  falling back straight to the segment's current `tier` would pick up
  whatever tier the *previous* run just advanced it to, so re-validating an
  otherwise-unchanged segment would walk the ladder once per run instead of
  recording one idempotent repeat (see the ladder module's docstring for
  the stale-attempt guard this relies on). Every path
  in the group is dropped from `pending_records`, and any of them that was
  actually a live CLAIM there (written via `owlsperch queue complete`, not
  merely sitting under `records/<book_id>/<type>/` for some other reason)
  and never made it into `records` is deleted from disk -- after the
  segment write-back, never before (so a crash between the two leaves only
  a harmless orphaned file, never a `pending_records` entry naming a file
  that no longer exists). A path already sitting in `records` (a
  previously-validated record failing only now, e.g. after a schema
  change) is never touched.
- Otherwise (nothing failed and nothing is missing), every PASSing path in
  the group is appended to `records` (if not already there) and dropped
  from `pending_records`; the segment is marked `status: "done"`/
  `outcome: "validated"` only once `pending_records` is left empty by this
  -- a segment with some other still-outstanding claim isn't "done" yet.

Both branches are idempotent the same way the old per-record write-back
was: rerunning on an unchanged set of records does not add a duplicate
record path, a duplicate identical attempt, or advance the ladder twice for
the same failure -- see `owlsperch.queue.ladder`'s module docstring for the
stale-re-validation case this guards against.

Batch B5's extract skill (`owlsperch queue complete`) records a subagent's
claimed record paths under the segment's `pending_records`, not `records`,
until they're proven to conform.

A `pending_records` claim can vanish ENTIRELY -- every path in it deleted,
moved, or never actually written -- leaving the owning segment with zero
`_CheckedRecord`s of its own this run, since the normal per-record grouping
above only ever groups records `_check_record_file` actually discovered.
Left alone, such a segment's `(book_id, segment_id)` key would never even
enter `groups`, so it would sit `pending` forever with no new `attempts`
entry -- a follow-up to B10c-mand4 closes this: `run_validate` and
`drive_dry_run` both pass `validate_record_files` the `book_ids` they're
scoped to, and `_orphaned_pending_segments` walks every segment file under
`segments/<book_id>/` for those books, adding an empty group for any
segment with an unvalidated `pending_records` entry that the normal
grouping missed -- `_write_back_group` then fails it via the same
`missing_record_path` path a partially-vanished claim already used.
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
    NULL_CONTEXT,
    TYPE_CONTEXT_CHECKS,
    TYPE_FIELD_CHECKS,
    ValidationContext,
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
    segments_dir,
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


@dataclass
class _CheckedRecord:
    """The result of checking one record file in isolation (Phase 1 of
    `validate_record_files`), plus everything Phase 2 needs to group it with
    its siblings and write its segment back -- no I/O happens here beyond
    what `_check_record_file` already did to produce `result`."""

    result: RecordResult
    book_id: str | None
    segment_id: str | None
    #: This record's own `extraction.tier`, if it's a string -- used (only
    #: for the first failing record in a group) as the ladder attempt's
    #: tier, falling back to the segment's own current tier.
    own_tier: str | None
    #: Whether `load_segment` actually found `segment_id`'s file under
    #: `segments/<book_id>/` -- a record whose segment can't be resolved at
    #: all is never grouped for write-back, exactly as before this batch.
    segment_found: bool


def _check_record_file(
    path: Path, *, data_dir: Path, compiled: CompiledSchemas, context: ValidationContext
) -> _CheckedRecord:
    """Phase 1: load and validate one record file in isolation. Pure aside
    from the read -- no write-back. Factored out of `validate_record_file`
    so `validate_record_files` can check every record in a run before any of
    them writes back to a segment (see module docstring)."""
    rel_path = path.relative_to(data_dir).as_posix()
    type_dir = path.parent.name

    try:
        record = load_json(path)
    except LoadError as exc:
        return _CheckedRecord(
            result=RecordResult(
                path=rel_path, status="FAIL", errors=[str(exc)], segment_id=None, type=None
            ),
            book_id=None,
            segment_id=None,
            own_tier=None,
            segment_found=False,
        )

    book_id = record.get("book_id") if isinstance(record.get("book_id"), str) else None
    extraction_raw = record.get("extraction")
    extraction: dict[str, Any] = extraction_raw if isinstance(extraction_raw, dict) else {}
    segment_id_raw = extraction.get("segment_id")
    segment_id = segment_id_raw if isinstance(segment_id_raw, str) else None
    tier_raw = extraction.get("tier")
    own_tier = tier_raw if isinstance(tier_raw, str) else None

    segment: dict[str, Any] | None = None
    if book_id is not None and segment_id is not None:
        segment = load_segment(data_dir, book_id, segment_id)

    errors = validate_record(
        record, type_dir=type_dir, compiled=compiled, segment=segment, context=context
    )
    status = "FAIL" if errors else "PASS"

    return _CheckedRecord(
        result=RecordResult(
            path=rel_path,
            status=status,
            errors=errors,
            segment_id=segment_id,
            type=record.get("type") if isinstance(record.get("type"), str) else None,
        ),
        book_id=book_id,
        segment_id=segment_id,
        own_tier=own_tier,
        segment_found=segment is not None,
    )


def _write_back_group(
    data_dir: Path,
    book_id: str,
    segment_id: str,
    members: list[_CheckedRecord],
    validated_paths_this_run: set[str],
) -> None:
    """Phase 2 for one `(book_id, segment_id)` group (see module
    docstring): write the segment back exactly once for every record it owns
    from this run, atomically -- either the whole group is a failure
    (advance the ladder, drop every group path from `pending_records`, and
    delete whichever of them was a live claim there that never made it into
    `records`), or the whole group passes (promote every path, and only
    then consider the segment `done`)."""
    path = segment_path(data_dir, book_id, segment_id)
    segment = Segment.model_validate_json(path.read_text())
    members = sorted(members, key=lambda m: m.result.path)

    failed = [m for m in members if m.result.status == "FAIL"]
    missing = [p for p in segment.pending_records if p not in validated_paths_this_run]

    if failed or missing:
        errors = [f"{m.result.path}: {e}" for m in failed for e in m.result.errors] + [
            f"missing_record_path: {p}" for p in missing
        ]
        tier = next(
            t
            for t in (failed[0].own_tier if failed else None, segment.claim_tier, segment.tier)
            if t is not None
        )

        group_paths = [m.result.path for m in members]
        # Only a path this segment actually had a live CLAIM on (still
        # listed in `pending_records`, i.e. written via `owlsperch queue
        # complete`, not merely sitting under `records/<book_id>/<type>/`
        # for some other reason) is ever deleted here -- mirrors the old
        # single-record write-back's own gate, generalized to the group.
        originally_pending = set(segment.pending_records)
        to_delete = [p for p in group_paths if p in originally_pending and p not in segment.records]
        segment.pending_records = [p for p in segment.pending_records if p not in group_paths]

        result = record_failure(segment, errors, kind="validation", tier=tier)
        finish_after_failure(data_dir, path, segment, result)

        for rel in to_delete:
            record_file = data_dir / rel
            if record_file.is_file():
                record_file.unlink()
        return

    for m in members:
        rel = m.result.path
        if rel not in segment.records:
            segment.records = [*segment.records, rel]
        if rel in segment.pending_records:
            segment.pending_records = [p for p in segment.pending_records if p != rel]

    if not segment.pending_records:
        segment.status = "done"
        segment.outcome = "validated"

    atomic_write_text(path, segment.model_dump_json(indent=2) + "\n")


def _orphaned_pending_segments(
    data_dir: Path,
    book_ids: set[str],
    validated_paths_this_run: set[str],
    already_grouped: set[tuple[str, str]],
) -> list[tuple[str, str]]:
    """Every `(book_id, segment_id)` under `segments/<book_id>/` (for
    `book_ids`) that isn't already in `already_grouped` but has at least one
    `pending_records` entry this run's file discovery never found -- i.e. a
    segment whose entire claim vanished, so it never produced a single
    `_CheckedRecord` for `validate_record_files`'s normal grouping to pick
    up. Without this, such a segment is left `pending` forever (batch
    B10c-mand4 follow-up): criterion 3 requires it FAIL and escalate too."""
    orphans: list[tuple[str, str]] = []
    for book_id in sorted(book_ids):
        book_segments_dir = segments_dir(data_dir) / book_id
        if not book_segments_dir.is_dir():
            continue
        for path in sorted(book_segments_dir.glob("*.json")):
            segment_id = path.stem
            if (book_id, segment_id) in already_grouped:
                continue
            try:
                segment = load_json(path)
            except LoadError:
                continue
            pending = segment.get("pending_records")
            if not isinstance(pending, list):
                continue
            if any(p not in validated_paths_this_run for p in pending if isinstance(p, str)):
                orphans.append((book_id, segment_id))
    return orphans


def validate_record_files(
    paths: list[Path],
    *,
    data_dir: Path,
    compiled: CompiledSchemas,
    context: ValidationContext,
    book_ids: set[str] | None = None,
) -> list[RecordResult]:
    """Check every record file in `paths` (Phase 1, no writes), then write
    each affected segment back exactly once (Phase 2, grouped by
    `(book_id, segment_id)`, in sorted path order within each group for
    determinism) -- see the module docstring for the atomicity this buys.
    Returns one `RecordResult` per path, in the same order as `paths`. A
    record whose `book_id`/`segment_id` can't both be resolved to strings,
    or whose segment file isn't found on disk at all, is still checked and
    returned but never grouped for write-back, exactly as before this
    batch.

    `book_ids` (batch B10c-mand4 follow-up) names the book(s) this run is
    scoped to -- `run_validate`/`drive_dry_run` both know this already and
    pass it explicitly. It's used only to catch a segment whose ENTIRE
    `pending_records` claim vanished (every path deleted, moved, or never
    written) and so produced zero `_CheckedRecord`s of its own for the
    normal per-record grouping below to find: such a segment is still
    walked (via `segments/<book_id>/*.json`) and, if it has any
    unvalidated `pending_records` entry, is failed via the same
    `missing_record_path` path criterion 3 describes for a partially
    vanished claim. Omitted (or left `None`) by a caller that only has a
    single path in hand and no book context (e.g. `validate_record_file`),
    in which case this check is skipped -- matching this function's
    pre-existing behavior for such callers."""
    checked = [
        _check_record_file(p, data_dir=data_dir, compiled=compiled, context=context) for p in paths
    ]
    validated_paths_this_run = {c.result.path for c in checked}

    groups: dict[tuple[str, str], list[_CheckedRecord]] = {}
    for c in checked:
        if c.book_id is not None and c.segment_id is not None and c.segment_found:
            groups.setdefault((c.book_id, c.segment_id), []).append(c)

    if book_ids:
        for book_id, segment_id in _orphaned_pending_segments(
            data_dir, book_ids, validated_paths_this_run, set(groups)
        ):
            groups.setdefault((book_id, segment_id), [])

    for (book_id, seg_id), members in groups.items():
        _write_back_group(data_dir, book_id, seg_id, members, validated_paths_this_run)

    return [c.result for c in checked]


def build_validation_context(data_dir: Path) -> ValidationContext:
    """The disk-backed `ValidationContext` (design decision D9):
    `record_by_id` parses `"<type>:<book_id>:<slug>"` into
    `data_dir/records/<book_id>/<type>/<slug>.json` and loads it if present;
    `spell_list_classes` scans `records/<book_id>/spell/*.json` once per
    `book_id` and memoizes every distinct `levels[].class` value found.
    Built once per run by `run_validate` and by `owlsperch.build_db.runner`
    (both know `data_dir`) and threaded through `validate_record` from
    there -- `checks.py` itself stays I/O-free."""
    spell_classes_cache: dict[str, set[str]] = {}

    def record_by_id(record_id: str) -> dict[str, Any] | None:
        parts = record_id.split(":", 2)
        if len(parts) != 3:
            return None
        type_name, book_id, slug = parts
        path = data_dir / "records" / book_id / type_name / f"{slug}.json"
        if not path.is_file():
            return None
        try:
            return load_json(path)
        except LoadError:
            return None

    def spell_list_classes(book_id: str) -> set[str]:
        if book_id not in spell_classes_cache:
            classes: set[str] = set()
            spell_dir = data_dir / "records" / book_id / "spell"
            if spell_dir.is_dir():
                for path in spell_dir.glob("*.json"):
                    try:
                        record = load_json(path)
                    except LoadError:
                        continue
                    fields = record.get("fields")
                    levels = fields.get("levels") if isinstance(fields, dict) else None
                    if isinstance(levels, list):
                        for item in levels:
                            if isinstance(item, dict) and isinstance(item.get("class"), str):
                                classes.add(item["class"])
            spell_classes_cache[book_id] = classes
        return spell_classes_cache[book_id]

    return ValidationContext(record_by_id=record_by_id, spell_list_classes=spell_list_classes)


def validate_record(
    record: dict[str, Any],
    *,
    type_dir: str,
    compiled: CompiledSchemas,
    segment: dict[str, Any] | None,
    context: ValidationContext = NULL_CONTEXT,
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
    expects. `context` (batch B10c, design decision D9) is `NULL_CONTEXT` by
    default -- every cross-record class/prestige_class check then degrades
    to "can't resolve" rather than crashing; a real caller passes one built
    by `build_validation_context`.
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

    context_check = TYPE_CONTEXT_CHECKS.get(type_dir)
    if context_check is not None:
        errors.extend(context_check(record, context))

    errors.extend(check_pages_within_segment(record, segment))
    return errors


def validate_record_file(
    path: Path,
    *,
    data_dir: Path,
    compiled: CompiledSchemas,
    context: ValidationContext | None = None,
) -> RecordResult:
    """Check and write back exactly one record file, as its own
    single-record group -- a thin wrapper around `validate_record_files`
    kept for callers that only ever have one path in hand. A caller
    validating several record files that may share a segment (e.g. a whole
    book) should call `validate_record_files` directly with the full list
    instead of looping this, or it loses the atomic per-segment write-back
    the module docstring describes."""
    resolved_context = context if context is not None else build_validation_context(data_dir)
    return validate_record_files(
        [path], data_dir=data_dir, compiled=compiled, context=resolved_context
    )[0]


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
    context = build_validation_context(data_dir)
    if book_id == "all":
        # Union with books that only have a `segments/<book_id>/` dir (no
        # `records/<book_id>/` at all yet) too, so a book whose every
        # segment's entire claim vanished before a single record file ever
        # landed on disk still gets its orphaned segments checked.
        book_ids = set(discover_books_with_records(data_dir))
        if segments_dir(data_dir).is_dir():
            book_ids |= {p.name for p in segments_dir(data_dir).iterdir() if p.is_dir()}
    else:
        book_ids = {book_id}
    record_results = validate_record_files(
        files, data_dir=data_dir, compiled=compiled, context=context, book_ids=book_ids
    )
    failed = sum(1 for r in record_results if r.status == "FAIL")

    if json_output:
        print(json.dumps([r.to_json() for r in record_results]), file=out)
        return 1 if failed else 0

    for result in record_results:
        print(result.render(), file=out)
    passed = len(record_results) - failed
    print(f"{passed} passed, {failed} failed, {len(record_results)} total", file=out)

    return 1 if failed else 0
