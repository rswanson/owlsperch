"""`owlsperch queue audit <book_id> [--fix]` -- a recovery tool for the
defect the guard in `owlsperch.queue.complete` now prevents going forward
(see that module's docstring): a subagent reply claiming a record path
another segment already owned used to silently overwrite the earlier
segment's extracted content, and both `queue complete` and `owlsperch
validate` reported success while that content was gone.

`audit_book` is read-only and reports two related but distinct things, from
two distinct sources of truth:

- `collisions`: which record paths are (or were) claimed by more than one
  segment right now, per the SEGMENT index (a segment's own `records` +
  `pending_records`, scanned across every `segments/<book_id>/*.json` and
  `human/<book_id>/*.json` file) -- the same index
  `owlsperch.queue.complete`'s guard consults to decide ownership going
  forward.
- `stale_claims`: which segments currently claim a path they do not
  actually own on disk any more. Unlike the guard (and unlike
  `collisions`), this IS decided from the record FILE's own
  `extraction.segment_id` -- for a surviving file, that's the last writer's
  stamp and so the legitimate owner; a path claimed by a segment but not
  present on disk at all (a dangling claim, e.g. after a hand reset or a
  record deleted out from under a segment) has no stamp to consult and is
  reported with reason `"missing"` instead.

`fix_book` performs the actual recovery, and is the only function here that
writes anything: for each segment with a stale claim, it prunes exactly the
stale paths from that segment's own `records`/`pending_records` (order
preserved) and, for a `done` segment not sitting in `human/`, soft-resets it
back to `pending` (clearing `outcome`/`outcome_reason`/`in_progress_since`,
leaving `tier`/`attempts`/`notes`/`context_seg_ids` alone) so it re-extracts
under the new guard and under B10-mand1's name-qualification prompt rule. A
segment already `pending`/`in_progress` just gets the pruning. A segment
sitting in `human/` is reported but left completely untouched -- a human is
meant to look at those. `fix_book` never deletes, moves, or rewrites a
record file for a stale (non-superseded) claim: pruning it from a segment's
own lists is index hygiene, not deletion, and it's what keeps the ownership
index unambiguous for the Part (a) guard afterwards (a victim that kept
claiming a path it no longer owns would give that path two claimants
again).

Batch B10c-mand2 adds a third, related report and recovery: `audit_book`
also reports `superseded_claims` -- every segment whose own `superseded_by`
is set that STILL holds a claim (`records`/`pending_records` non-empty). A
superseded segment is EXCLUDED from `stale_claims` entirely (even if its
claimed file's on-disk owner doesn't match it): that pass can soft-reset a
`done` victim back to `pending`, which must never happen to a segment this
batch deliberately freezes. `fix_book` releases every `superseded_claims`
entry through the shared `owlsperch.supersede.release_segment_claims`
helper -- exactly the retroactive counterpart to the class-span pass's
release-at-stamp-time behavior (`owlsperch.segment.runner`), for the
segments that were stamped `superseded_by` before that behavior existed
and so never had their claims released. A superseded segment sitting in
`human/` is, like a `stale_claims` victim there, reported but left
completely untouched.

Batch B10c-mand11 adds a fourth pass, `wrongly_superseded`: every segment
whose `superseded_by` names a class/prestige_class segment of the same book
whose own `(heading, kind_hint)` FAILS `owlsperch.supersede.
is_class_owned_fragment` for that class's heading. Before that predicate
existed the class-span stamp pass went by page span alone, so printed
SIDEBARS sharing a class's pages (the real PHB's "FAMILIARS", "THE
PALADIN'S MOUNT", "SCHOOL SPECIALIZATION", ...) were frozen and their
record files moved into `superseded/`, leaving that content in no canonical
record at all. This is the retroactive restore for exactly that data:
`fix_book` clears `superseded_by`, moves every `released_records` entry's
file back out of `superseded/` to its original `records/<book_id>/` path
(`os.replace`, never overwriting a destination that already exists -- such
an entry is reported as `blocked` and its file left where it is, with its
`released_records` entry KEPT so the pointer isn't lost), puts every
restored path back into the segment's own `records`, and leaves
`status`/`outcome`/`tier`/`attempts` exactly as they are (the records had
already been validated before the stamp). A wrongly superseded segment is
EXCLUDED from `superseded_claims` -- that pass would release the very
claims this one restores -- and one sitting in `human/` is, as everywhere
else here, reported but left completely untouched. Running `--fix` twice is
a no-op the second time: the restored segment no longer carries
`superseded_by`, so it isn't reported at all.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from owlsperch.fsutil import atomic_write_text
from owlsperch.queue.common import resolve_record_path_under_book
from owlsperch.segment.runner import ReleasedRecord, Segment
from owlsperch.supersede import is_class_owned_fragment, release_segment_claims

#: The only segment kinds a `superseded_by` value can legitimately name
#: (batch B10c-mand11) -- only the toc-driven class pass ever stamps one.
_CLASS_KINDS: frozenset[str] = frozenset({"class", "prestige_class"})


def _segment_files(data_dir: Path, book_id: str) -> list[tuple[Path, str]]:
    """Every segment file for `book_id`, paired with its location
    (`"segments"` or `"human"`), in the same scan order
    `owlsperch.queue.complete._record_path_owners` uses: `segments/` first,
    then `human/`, each sorted."""
    return [
        *((p, "segments") for p in sorted(data_dir.glob(f"segments/{book_id}/*.json"))),
        *((p, "human") for p in sorted(data_dir.glob(f"human/{book_id}/*.json"))),
    ]


def _load_raw(path: Path) -> dict[str, Any] | None:
    """Best-effort raw JSON load of a segment file -- tolerant the same way
    `owlsperch.queue.complete._record_path_owners` is, since one odd legacy
    file must never make the audit blow up."""
    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    return raw if isinstance(raw, dict) else None


def _claimed_paths(raw: dict[str, Any]) -> list[str]:
    claimed: list[str] = []
    for key in ("records", "pending_records"):
        values = raw.get(key)
        if isinstance(values, list):
            claimed.extend(v for v in values if isinstance(v, str))
    return claimed


def _record_owner_on_disk(resolved: Path) -> str | None:
    """The `extraction.segment_id` stamped on the record file at `resolved`,
    or `None` if it doesn't exist, isn't valid JSON, or doesn't carry one."""
    if not resolved.is_file():
        return None
    try:
        record = json.loads(resolved.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(record, dict):
        return None
    extraction = record.get("extraction")
    if not isinstance(extraction, dict):
        return None
    owner = extraction.get("segment_id")
    return owner if isinstance(owner, str) else None


@dataclass
class Collision:
    #: Relative to `$OWLSPERCH_DATA`, as claimed (its canonical, resolved
    #: spelling -- not necessarily byte-identical to any one claimant's own
    #: string).
    path: str
    #: Every segment id claiming `path`, in scan order.
    claimants: list[str]
    #: The `extraction.segment_id` currently stamped on the surviving file,
    #: or `None` if the file doesn't exist or doesn't carry one.
    on_disk_owner: str | None

    def to_json(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "claimants": self.claimants,
            "on_disk_owner": self.on_disk_owner,
        }


@dataclass
class StalePathEntry:
    #: Exactly as the owning segment spelled it in its own
    #: records/pending_records list.
    path: str
    #: "owned_by_other" | "missing"
    reason: str
    #: The current on-disk owner, or `None` for "missing".
    owner: str | None

    def to_json(self) -> dict[str, Any]:
        return {"path": self.path, "reason": self.reason, "owner": self.owner}


@dataclass
class StaleClaim:
    seg_id: str
    status: str
    tier: str
    #: "segments" | "human"
    location: str
    paths: list[StalePathEntry] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "seg_id": self.seg_id,
            "status": self.status,
            "tier": self.tier,
            "location": self.location,
            "paths": [p.to_json() for p in self.paths],
        }


@dataclass
class SupersededClaim:
    """A segment whose own `superseded_by` is set but that still holds a
    claim (batch B10c-mand2) -- reported separately from, and excluded
    from, `stale_claims` (see this module's docstring)."""

    seg_id: str
    superseded_by: str
    status: str
    #: "segments" | "human"
    location: str
    #: Every path currently in the segment's own `records` +
    #: `pending_records`, deduplicated (order preserved), exactly as the
    #: segment spelled them.
    paths: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "seg_id": self.seg_id,
            "superseded_by": self.superseded_by,
            "status": self.status,
            "location": self.location,
            "paths": self.paths,
        }


@dataclass
class WronglySuperseded:
    """A segment stamped `superseded_by` by a class span that does not
    actually own it (batch B10c-mand11): its `superseded_by` names a
    class/prestige_class segment of the same book, but its own
    `(heading, kind_hint)` fails `owlsperch.supersede.
    is_class_owned_fragment` for that class's title -- a printed sidebar,
    or the NEXT class's own heading fragment on a shared page, swallowed by
    the page-span-only stamp pass this batch replaced."""

    seg_id: str
    superseded_by: str
    #: The stamping class segment's own `heading` (its toc entry title).
    class_heading: str
    heading: str
    kind_hint: str
    status: str
    #: "segments" | "human"
    location: str
    #: Every `released_records` path whose file was actually MOVED under
    #: `superseded/` and so can be moved back (in `released_records`
    #: order); a path released with `moved_to: None` has no file to
    #: restore and is not listed.
    restorable_paths: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "seg_id": self.seg_id,
            "superseded_by": self.superseded_by,
            "class_heading": self.class_heading,
            "heading": self.heading,
            "kind_hint": self.kind_hint,
            "status": self.status,
            "location": self.location,
            "restorable_paths": self.restorable_paths,
        }


@dataclass
class AuditReport:
    book_id: str
    collisions: list[Collision] = field(default_factory=list)
    stale_claims: list[StaleClaim] = field(default_factory=list)
    superseded_claims: list[SupersededClaim] = field(default_factory=list)
    wrongly_superseded: list[WronglySuperseded] = field(default_factory=list)

    def render(self) -> str:
        lines = [f"{self.book_id}:"]
        if self.collisions:
            lines.append(
                f"  {len(self.collisions)} record path(s) claimed by more than one segment:"
            )
            for collision in self.collisions:
                owner = (
                    collision.on_disk_owner if collision.on_disk_owner is not None else "missing"
                )
                lines.append(
                    f"    {collision.path}: claimed by {', '.join(collision.claimants)}"
                    f" (on-disk owner: {owner})"
                )
        else:
            lines.append("  no record paths claimed by more than one segment")

        if self.stale_claims:
            lines.append(f"  {len(self.stale_claims)} segment(s) carry a stale claim:")
            for claim in self.stale_claims:
                lines.append(
                    f"    {claim.seg_id} (status={claim.status}, tier={claim.tier},"
                    f" location={claim.location}):"
                )
                for entry in claim.paths:
                    owner = entry.owner if entry.owner is not None else "missing"
                    lines.append(f"      {entry.path}: {entry.reason} (owner: {owner})")
        else:
            lines.append("  no segments carry a stale claim")

        if self.superseded_claims:
            lines.append(
                f"  {len(self.superseded_claims)} superseded segment(s) still hold a claim:"
            )
            for superseded_claim in self.superseded_claims:
                lines.append(
                    f"    {superseded_claim.seg_id}"
                    f" (superseded_by={superseded_claim.superseded_by},"
                    f" status={superseded_claim.status}, location={superseded_claim.location}):"
                    f" {', '.join(superseded_claim.paths)}"
                )
        else:
            lines.append("  no superseded segments hold a claim")

        if self.wrongly_superseded:
            lines.append(
                f"  {len(self.wrongly_superseded)} segment(s) superseded by a class that"
                " does not own them:"
            )
            for wrong in self.wrongly_superseded:
                restorable = ", ".join(wrong.restorable_paths) or "(no record files to restore)"
                lines.append(
                    f"    {wrong.seg_id} ({wrong.kind_hint} '{wrong.heading}')"
                    f" superseded_by={wrong.superseded_by} ('{wrong.class_heading}'),"
                    f" status={wrong.status}, location={wrong.location}: {restorable}"
                )
        else:
            lines.append("  no segments superseded by a class that does not own them")
        return "\n".join(lines)

    def to_json(self) -> dict[str, Any]:
        return {
            "book_id": self.book_id,
            "collisions": [c.to_json() for c in self.collisions],
            "stale_claims": [sc.to_json() for sc in self.stale_claims],
            "superseded_claims": [sc.to_json() for sc in self.superseded_claims],
            "wrongly_superseded": [ws.to_json() for ws in self.wrongly_superseded],
        }


def _moved_released_paths(raw: dict[str, Any]) -> list[tuple[str, str]]:
    """Every `released_records` entry of `raw` that actually MOVED a file --
    `(original path, moved_to)`, in stored order. An entry with a null
    `moved_to` (the file was already gone, or still owned by a live
    segment) has nothing to restore and is skipped."""
    released = raw.get("released_records")
    if not isinstance(released, list):
        return []
    pairs: list[tuple[str, str]] = []
    for entry in released:
        if not isinstance(entry, dict):
            continue
        path, moved_to = entry.get("path"), entry.get("moved_to")
        if isinstance(path, str) and isinstance(moved_to, str) and moved_to:
            pairs.append((path, moved_to))
    return pairs


def _wrongly_superseded_entry(
    *,
    seg_id: str,
    location: str,
    raw: dict[str, Any],
    superseded_by: str,
    by_seg_id: dict[str, dict[str, Any]],
) -> WronglySuperseded | None:
    """Batch B10c-mand11: the `WronglySuperseded` entry for `raw` (already
    known to carry `superseded_by`), or `None` when the stamp is legitimate
    -- which includes every case the audit can't confidently call wrong: a
    `superseded_by` naming a segment this book has no file for, or one that
    isn't a class/prestige_class segment at all (nothing else ever stamps,
    so such a value is hand-made and left alone rather than undone)."""
    class_raw = by_seg_id.get(superseded_by)
    if class_raw is None:
        return None
    if class_raw.get("kind_hint") not in _CLASS_KINDS:
        return None

    class_heading = str(class_raw.get("heading", ""))
    heading = str(raw.get("heading", ""))
    kind_hint = str(raw.get("kind_hint", ""))
    if is_class_owned_fragment(heading, kind_hint, class_heading):
        return None

    return WronglySuperseded(
        seg_id=seg_id,
        superseded_by=superseded_by,
        class_heading=class_heading,
        heading=heading,
        kind_hint=kind_hint,
        status=str(raw.get("status", "")),
        location=location,
        restorable_paths=[path for path, _moved_to in _moved_released_paths(raw)],
    )


def audit_book(book_id: str, *, data_dir: Path) -> AuditReport:
    segment_files = _segment_files(data_dir, book_id)

    parsed: list[tuple[str, str, dict[str, Any]]] = []  # (seg_id, location, raw)
    claims: dict[Path, list[str]] = {}
    for seg_path, location in segment_files:
        raw = _load_raw(seg_path)
        if raw is None:
            continue
        seg_id = raw.get("seg_id")
        if not isinstance(seg_id, str):
            continue
        parsed.append((seg_id, location, raw))
        for rel_path in _claimed_paths(raw):
            resolved = resolve_record_path_under_book(data_dir, book_id, rel_path)
            if resolved is not None:
                claims.setdefault(resolved, []).append(seg_id)

    resolved_data_dir = data_dir.resolve()
    collisions: list[Collision] = []
    for resolved, claimants in sorted(claims.items(), key=lambda kv: kv[0].as_posix()):
        if len(claimants) < 2:
            continue
        collisions.append(
            Collision(
                path=resolved.relative_to(resolved_data_dir).as_posix(),
                claimants=claimants,
                on_disk_owner=_record_owner_on_disk(resolved),
            )
        )

    by_seg_id = {seg_id: raw for seg_id, _location, raw in parsed}

    stale_claims: list[StaleClaim] = []
    superseded_claims: list[SupersededClaim] = []
    wrongly_superseded: list[WronglySuperseded] = []
    for seg_id, location, raw in parsed:
        superseded_by = raw.get("superseded_by")
        if isinstance(superseded_by, str) and superseded_by:
            # Batch B10c-mand11: first decide whether the stamping class
            # actually OWNS this segment. If it doesn't, the segment belongs
            # in `wrongly_superseded` (to be un-superseded, its released
            # record files moved back) and NOT in `superseded_claims`, whose
            # `--fix` would release the very claims this one restores.
            wrong = _wrongly_superseded_entry(
                seg_id=seg_id,
                location=location,
                raw=raw,
                superseded_by=superseded_by,
                by_seg_id=by_seg_id,
            )
            if wrong is not None:
                wrongly_superseded.append(wrong)
                continue

            # A superseded segment is frozen (batch B10c-mand2): it is
            # EXCLUDED from stale_claims entirely (that pass can soft-reset
            # a `done` segment back to `pending`, which must never happen
            # here), and any claim it still holds is reported separately so
            # `--fix` can release it instead.
            claimed_paths = list(dict.fromkeys(_claimed_paths(raw)))
            if claimed_paths:
                superseded_claims.append(
                    SupersededClaim(
                        seg_id=seg_id,
                        superseded_by=superseded_by,
                        status=str(raw.get("status", "")),
                        location=location,
                        paths=claimed_paths,
                    )
                )
            continue

        seen: set[Path] = set()
        stale_paths: list[StalePathEntry] = []
        for rel_path in _claimed_paths(raw):
            resolved = resolve_record_path_under_book(data_dir, book_id, rel_path)
            if resolved is None or resolved in seen:
                continue
            seen.add(resolved)
            if not resolved.is_file():
                stale_paths.append(StalePathEntry(path=rel_path, reason="missing", owner=None))
                continue
            owner = _record_owner_on_disk(resolved)
            if owner is not None and owner != seg_id:
                stale_paths.append(
                    StalePathEntry(path=rel_path, reason="owned_by_other", owner=owner)
                )

        if stale_paths:
            stale_claims.append(
                StaleClaim(
                    seg_id=seg_id,
                    status=str(raw.get("status", "")),
                    tier=str(raw.get("tier", "")),
                    location=location,
                    paths=stale_paths,
                )
            )

    return AuditReport(
        book_id=book_id,
        collisions=collisions,
        stale_claims=stale_claims,
        superseded_claims=superseded_claims,
        wrongly_superseded=wrongly_superseded,
    )


def _restore_segment(
    segment: Segment, *, data_dir: Path
) -> tuple[list[str], list[dict[str, str]], list[dict[str, str]]]:
    """Batch B10c-mand11: undo a wrong supersede on ONE segment, in memory
    (the caller persists it). Clears `superseded_by`; for every
    `released_records` entry whose file was MOVED under `superseded/`, moves
    it back (`os.replace`) to its original `records/<book_id>/` path and
    puts that path back in the segment's own `records` (the record had
    already been validated before the stamp, so `status`/`outcome` are left
    exactly as they are). A destination that already exists is NOT
    overwritten: the file stays under `superseded/`, the entry is reported
    as blocked, and its `released_records` entry is KEPT so the pointer to
    where the file actually lives isn't lost. Returns `(restored paths,
    moved [{"from", "to"}], blocked [{"path", "moved_to", "reason"}])`."""
    restored: list[str] = []
    moved: list[dict[str, str]] = []
    blocked: list[dict[str, str]] = []
    kept: list[ReleasedRecord] = []

    for entry in segment.released_records:
        if entry.moved_to is None:
            # Nothing was moved for this claim (the file was already gone,
            # or still owned by a live segment) -- nothing to restore.
            continue
        src = data_dir / entry.moved_to
        dest = resolve_record_path_under_book(data_dir, segment.book_id, entry.path)
        reason: str | None = None
        if dest is None:
            reason = "unresolvable_path"
        elif not src.is_file():
            reason = "missing_in_superseded"
        elif dest.exists():
            reason = "destination_exists"
        if reason is not None or dest is None:
            blocked.append(
                {"path": entry.path, "moved_to": entry.moved_to, "reason": reason or "unknown"}
            )
            kept.append(entry)
            continue

        dest.parent.mkdir(parents=True, exist_ok=True)
        os.replace(src, dest)
        restored.append(entry.path)
        moved.append({"from": entry.moved_to, "to": entry.path})
        if entry.path not in segment.records:
            segment.records = [*segment.records, entry.path]

    # A blocked entry keeps the segment stamped: clearing `superseded_by`
    # here would drop it out of `wrongly_superseded` on the next run and
    # leave the stranded file under `superseded/` unreported forever.
    # Leaving the stamp in place makes the next `--fix` retry it once the
    # collision at the destination is cleared.
    if not blocked:
        segment.superseded_by = None
    segment.released_records = kept
    return restored, moved, blocked


def fix_book(book_id: str, *, data_dir: Path) -> list[dict[str, Any]]:
    """Apply the recovery `audit_book` describes. Returns one summary dict
    per affected segment: `{"seg_id", "location", "action", "pruned_paths"}`
    (plus, for a `"released"` action, `"moved"`; plus, for a `"restored"`
    one, `"restored_paths"`/`"moved"`/`"blocked"`) where `action` is
    `"reset"` (soft reset, was `done`), `"pruned"` (paths dropped, status
    left alone), `"released"` (a superseded segment's claims released via
    `owlsperch.supersede.release_segment_claims`), `"restored"` (batch
    B10c-mand11 -- a wrongly superseded segment un-superseded and its
    released record files moved back out of `superseded/`), or
    `"left_in_human"` (reported only, nothing written)."""
    report = audit_book(book_id, data_dir=data_dir)
    results: list[dict[str, Any]] = []

    for wrong in report.wrongly_superseded:
        if wrong.location == "human":
            results.append(
                {
                    "seg_id": wrong.seg_id,
                    "location": "human",
                    "action": "left_in_human",
                    "pruned_paths": list(wrong.restorable_paths),
                }
            )
            continue

        seg_path = data_dir / "segments" / book_id / f"{wrong.seg_id}.json"
        segment = Segment.model_validate_json(seg_path.read_text())
        restored, moved, blocked = _restore_segment(segment, data_dir=data_dir)
        atomic_write_text(seg_path, segment.model_dump_json(indent=2) + "\n")

        results.append(
            {
                "seg_id": wrong.seg_id,
                "location": "segments",
                "action": "restored",
                "pruned_paths": [],
                "restored_paths": restored,
                "moved": moved,
                "blocked": blocked,
            }
        )

    for superseded_claim in report.superseded_claims:
        if superseded_claim.location == "human":
            results.append(
                {
                    "seg_id": superseded_claim.seg_id,
                    "location": "human",
                    "action": "left_in_human",
                    "pruned_paths": list(superseded_claim.paths),
                }
            )
            continue

        seg_path = data_dir / "segments" / book_id / f"{superseded_claim.seg_id}.json"
        segment = Segment.model_validate_json(seg_path.read_text())
        newly_released = release_segment_claims(segment, data_dir=data_dir)
        atomic_write_text(seg_path, segment.model_dump_json(indent=2) + "\n")

        results.append(
            {
                "seg_id": superseded_claim.seg_id,
                "location": "segments",
                "action": "released",
                "pruned_paths": [r.path for r in newly_released],
                "moved": [
                    {"from": r.path, "to": r.moved_to}
                    for r in newly_released
                    if r.moved_to is not None
                ],
            }
        )

    for claim in report.stale_claims:
        stale_paths = {p.path for p in claim.paths}

        if claim.location == "human":
            results.append(
                {
                    "seg_id": claim.seg_id,
                    "location": "human",
                    "action": "left_in_human",
                    "pruned_paths": sorted(stale_paths),
                }
            )
            continue

        seg_path = data_dir / "segments" / book_id / f"{claim.seg_id}.json"
        segment = Segment.model_validate_json(seg_path.read_text())
        segment.records = [p for p in segment.records if p not in stale_paths]
        segment.pending_records = [p for p in segment.pending_records if p not in stale_paths]

        action = "pruned"
        if segment.status == "done":
            segment.status = "pending"
            segment.outcome = None
            segment.outcome_reason = None
            segment.in_progress_since = None
            action = "reset"

        atomic_write_text(seg_path, segment.model_dump_json(indent=2) + "\n")
        results.append(
            {
                "seg_id": claim.seg_id,
                "location": "segments",
                "action": action,
                "pruned_paths": sorted(stale_paths),
            }
        )

    return results
