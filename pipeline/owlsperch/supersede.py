"""Releasing the record claims held by a superseded segment (batch
B10c-mand2).

B10c's class-span pass (`owlsperch.segment.runner`) stamps `superseded_by`
on every fragment segment whose pages fall entirely inside a discovered
class/prestige_class span, but originally left that fragment's own
`records`/`pending_records` claims in place. When the class's own level
table shares the fragment's printed title -- and so the same slug, id, and
record file path -- `owlsperch.queue.complete`'s ownership guard refuses
the class segment's claim on that path as a collision, and the class can
never be extracted at all.

`release_segment_claims` is the fix, shared by two callers: the class-span
pass itself, at the moment a segment is NEWLY stamped `superseded_by` (see
`owlsperch.segment.runner._supersede_segments_in_span`), and `owlsperch
queue audit --fix` (`owlsperch.queue.audit.fix_book`), which applies the
same release retroactively to segments stamped before this release-at-
stamp-time behavior existed. For ONE superseded segment, it:

1. Walks the segment's own `records` + `pending_records`, deduplicated by
   RESOLVED path (order preserved, `records` first) -- a path can
   legitimately appear in both lists.
2. For each claimed path that resolves inside `records/<book_id>/` under
   `data_dir` (`owlsperch.queue.common.resolve_record_path_under_book`):
   moves the record file to `$OWLSPERCH_DATA/superseded/<book_id>/<type>/
   <file>.json` -- ALWAYS a move (`os.replace`), NEVER a delete -- unless
   the file's own `extraction.segment_id` names a DIFFERENT segment that is
   itself still live (not superseded): releasing must never steal a live
   segment's record, so that claim is pruned from the passed-in segment's
   lists but the file is left exactly where it is (`moved_to: None`). A
   claimed path missing on disk entirely is likewise pruned with
   `moved_to: None`, nothing created under `superseded/`. A destination
   name already taken under `superseded/` (two different segments'
   fragments both once owned a file with the same tail path -- unlikely,
   but never assumed away) is resolved by appending the releasing
   segment's own `seg_id`, then `-2`, `-3`, ... until a free name is found;
   an existing file under `superseded/` is never overwritten.
3. Clears the segment's `records`/`pending_records` to `[]` and appends one
   `ReleasedRecord` per claimed path onto `segment.released_records`.

It mutates the `segment` object passed to it but never writes it (or
anything else) to disk itself -- the caller persists it, in whatever atomic
write it was already about to do (e.g. the same one that sets
`superseded_by`).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from owlsperch.queue.common import resolve_record_path_under_book

if TYPE_CHECKING:
    from owlsperch.segment.runner import ReleasedRecord, Segment


def _record_owner_on_disk(path: Path) -> str | None:
    """The `extraction.segment_id` stamped on the record file at `path`, or
    `None` if it doesn't parse or doesn't carry one -- mirrors
    `owlsperch.queue.audit._record_owner_on_disk`."""
    try:
        record = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(record, dict):
        return None
    extraction = record.get("extraction")
    if not isinstance(extraction, dict):
        return None
    owner = extraction.get("segment_id")
    return owner if isinstance(owner, str) else None


def _segment_is_superseded(data_dir: Path, book_id: str, seg_id: str) -> bool:
    """Whether `seg_id` (searched under both `segments/<book_id>/` and
    `human/<book_id>/`) itself carries a non-empty `superseded_by`. A
    segment that can't be found at all is treated as NOT superseded (the
    conservative choice -- refusing to move a file whose real owner can't
    be confirmed frozen)."""
    for location in ("segments", "human"):
        path = data_dir / location / book_id / f"{seg_id}.json"
        if not path.is_file():
            continue
        try:
            raw: Any = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return False
        if not isinstance(raw, dict):
            return False
        superseded_by = raw.get("superseded_by")
        return isinstance(superseded_by, str) and bool(superseded_by)
    return False


def _unique_destination(dest: Path, seg_id: str) -> Path:
    """`dest` if free, otherwise `<stem>-<seg_id><suffix>`, otherwise
    `<stem>-<seg_id>-2<suffix>`, `-3`, ... -- never a name already taken
    under `superseded/`."""
    if not dest.exists():
        return dest
    stem, suffix = dest.stem, dest.suffix
    candidate = dest.with_name(f"{stem}-{seg_id}{suffix}")
    n = 2
    while candidate.exists():
        candidate = dest.with_name(f"{stem}-{seg_id}-{n}{suffix}")
        n += 1
    return candidate


def release_segment_claims(segment: Segment, *, data_dir: Path) -> list[ReleasedRecord]:
    """Release every record claim `segment` (already known to be
    superseded) holds -- see this module's docstring. Returns the list of
    `ReleasedRecord`s newly appended onto `segment.released_records`."""
    from owlsperch.segment.runner import ReleasedRecord  # lazy: see module docstring.

    resolved_data_dir = data_dir.resolve()
    records_root = (data_dir / "records" / segment.book_id).resolve()

    ordered_claims: list[str] = []
    seen: set[Path] = set()
    for rel_path in [*segment.records, *segment.pending_records]:
        resolved = resolve_record_path_under_book(data_dir, segment.book_id, rel_path)
        if resolved is None or resolved in seen:
            continue
        seen.add(resolved)
        ordered_claims.append(rel_path)

    newly_released: list[ReleasedRecord] = []
    for rel_path in ordered_claims:
        resolved = resolve_record_path_under_book(data_dir, segment.book_id, rel_path)
        assert resolved is not None  # already confirmed resolvable above
        moved_to: str | None = None

        if resolved.is_file():
            owner = _record_owner_on_disk(resolved)
            safe_to_move = (
                owner is None
                or owner == segment.seg_id
                or _segment_is_superseded(data_dir, segment.book_id, owner)
            )
            if safe_to_move:
                tail = resolved.relative_to(records_root)
                dest_dir = resolved_data_dir / "superseded" / segment.book_id / tail.parent
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest = _unique_destination(dest_dir / tail.name, segment.seg_id)
                os.replace(resolved, dest)
                moved_to = dest.relative_to(resolved_data_dir).as_posix()

        newly_released.append(ReleasedRecord(path=rel_path, moved_to=moved_to))

    segment.records = []
    segment.pending_records = []
    segment.released_records = [*segment.released_records, *newly_released]
    return newly_released
