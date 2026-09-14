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
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from owlsperch.fsutil import atomic_write_text
from owlsperch.queue.common import resolve_record_path_under_book
from owlsperch.segment.runner import Segment
from owlsperch.supersede import release_segment_claims


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
class AuditReport:
    book_id: str
    collisions: list[Collision] = field(default_factory=list)
    stale_claims: list[StaleClaim] = field(default_factory=list)
    superseded_claims: list[SupersededClaim] = field(default_factory=list)

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
        return "\n".join(lines)

    def to_json(self) -> dict[str, Any]:
        return {
            "book_id": self.book_id,
            "collisions": [c.to_json() for c in self.collisions],
            "stale_claims": [sc.to_json() for sc in self.stale_claims],
            "superseded_claims": [sc.to_json() for sc in self.superseded_claims],
        }


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

    stale_claims: list[StaleClaim] = []
    superseded_claims: list[SupersededClaim] = []
    for seg_id, location, raw in parsed:
        superseded_by = raw.get("superseded_by")
        if isinstance(superseded_by, str) and superseded_by:
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
    )


def fix_book(book_id: str, *, data_dir: Path) -> list[dict[str, Any]]:
    """Apply the recovery `audit_book` describes. Returns one summary dict
    per affected segment: `{"seg_id", "location", "action", "pruned_paths"}`
    (plus, for a `"released"` action, `"moved"`) where `action` is
    `"reset"` (soft reset, was `done`), `"pruned"` (paths dropped, status
    left alone), `"released"` (a superseded segment's claims released via
    `owlsperch.supersede.release_segment_claims`), or `"left_in_human"`
    (reported only, nothing written)."""
    report = audit_book(book_id, data_dir=data_dir)
    results: list[dict[str, Any]] = []

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
