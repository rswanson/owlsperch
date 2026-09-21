"""Tests for `owlsperch.queue.audit` (and `owlsperch queue audit`): the
recovery tool for segments that lost a record to the collision the
`owlsperch.queue.complete` guard now prevents going forward (see
`test_queue_complete.py`'s "Record path collisions" section).

`audit_book` is read-only and reports two related but distinct things:

- `collisions`: record paths currently claimed (via `records` +
  `pending_records`) by more than one segment.
- `stale_claims`: segments that claim a path they do not actually own any
  more, decided from the record FILE's own `extraction.segment_id` (the
  last writer's stamp) -- "owned_by_other" when another segment's stamp is
  on the surviving file, "missing" when the file isn't on disk at all.

- `superseded_claims` (batch B10c-mand2): segments frozen by a class span
  that still hold a claim, released by `--fix`.
- `wrongly_superseded` (batch B10c-mand11): segments a class span froze
  without owning them (`owlsperch.supersede.is_class_owned_fragment` fails
  for the stamping class's heading) -- a printed sidebar, or the next
  class's own fragment on a shared page. `--fix` restores them: clear
  `superseded_by`, move every released record file back out of
  `superseded/`, and put the claims back.

`fix_book` performs the recovery: prune stale paths from a segment's own
lists, and soft-reset a `done` victim back to `pending` so it re-extracts.
A segment sitting in `human/` is reported but left untouched.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from owlsperch.queue.audit import audit_book, fix_book
from owlsperch.segment.runner import Segment


def _write_segment(
    data_dir: Path, book_id: str, seg_id: str, *, location: str = "segments", **overrides: object
) -> Path:
    defaults: dict[str, Any] = dict(
        seg_id=seg_id,
        book_id=book_id,
        pages=[10],
        printed_pages=[10],
        kind_hint="rules_section",
        heading="Class Features",
        text="Class features text.",
        status="pending",
        tier="haiku",
        created_at="2026-01-01T00:00:00+00:00",
    )
    defaults.update(overrides)
    segment = Segment(**defaults)
    seg_dir = data_dir / location / book_id
    seg_dir.mkdir(parents=True, exist_ok=True)
    path = seg_dir / f"{seg_id}.json"
    path.write_text(segment.model_dump_json(indent=2))
    return path


def _read_segment(data_dir: Path, book_id: str, seg_id: str, *, location: str = "segments") -> Any:
    path = data_dir / location / book_id / f"{seg_id}.json"
    return json.loads(path.read_text())


def _write_record(data_dir: Path, rel_path: str, *, segment_id: str) -> Path:
    path = data_dir / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "extraction": {
                    "tier": "haiku",
                    "model": "claude-haiku-4-5",
                    "segment_id": segment_id,
                    "timestamp": "2026-01-01T00:00:00+00:00",
                },
            }
        )
    )
    return path


# ---------------------------------------------------------------------------
# collisions
# ---------------------------------------------------------------------------


def test_collisions_reports_claimants_in_scan_order_and_the_on_disk_owner(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    _write_segment(
        data_dir,
        "book",
        "book-p0010-01",
        status="done",
        outcome="validated",
        tier="haiku",
        records=["records/book/rules_section/class-features.json"],
    )
    _write_segment(
        data_dir,
        "book",
        "book-p0011-01",
        status="in_progress",
        tier="sonnet",
        pending_records=["records/book/rules_section/class-features.json"],
    )
    _write_record(
        data_dir,
        "records/book/rules_section/class-features.json",
        segment_id="book-p0011-01",
    )

    report = audit_book("book", data_dir=data_dir)

    assert len(report.collisions) == 1
    collision = report.collisions[0]
    assert collision.path == "records/book/rules_section/class-features.json"
    assert collision.claimants == ["book-p0010-01", "book-p0011-01"]
    assert collision.on_disk_owner == "book-p0011-01"


def test_no_collision_when_a_path_has_only_one_claimant(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(
        data_dir,
        "book",
        "book-p0010-01",
        status="done",
        outcome="validated",
        records=["records/book/rules_section/class-features.json"],
    )
    _write_record(
        data_dir,
        "records/book/rules_section/class-features.json",
        segment_id="book-p0010-01",
    )

    report = audit_book("book", data_dir=data_dir)

    assert report.collisions == []
    assert report.stale_claims == []


# ---------------------------------------------------------------------------
# stale_claims
# ---------------------------------------------------------------------------


def test_stale_claim_owned_by_other_is_reported_for_the_losing_claimant_only(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    _write_segment(
        data_dir,
        "book",
        "book-p0010-01",
        status="done",
        outcome="validated",
        tier="haiku",
        records=["records/book/rules_section/class-features.json"],
    )
    _write_segment(
        data_dir,
        "book",
        "book-p0011-01",
        status="in_progress",
        tier="sonnet",
        pending_records=["records/book/rules_section/class-features.json"],
    )
    _write_record(
        data_dir,
        "records/book/rules_section/class-features.json",
        segment_id="book-p0011-01",
    )

    report = audit_book("book", data_dir=data_dir)

    stale_seg_ids = {sc.seg_id for sc in report.stale_claims}
    assert stale_seg_ids == {"book-p0010-01"}

    victim = next(sc for sc in report.stale_claims if sc.seg_id == "book-p0010-01")
    assert victim.status == "done"
    assert victim.tier == "haiku"
    assert victim.location == "segments"
    assert len(victim.paths) == 1
    assert victim.paths[0].path == "records/book/rules_section/class-features.json"
    assert victim.paths[0].reason == "owned_by_other"
    assert victim.paths[0].owner == "book-p0011-01"


def test_stale_claim_missing_from_disk_is_reported_as_missing(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(
        data_dir,
        "book",
        "book-p0010-01",
        status="done",
        outcome="validated",
        records=["records/book/rules_section/tactical-movement.json"],
    )

    report = audit_book("book", data_dir=data_dir)

    assert report.collisions == []
    assert len(report.stale_claims) == 1
    stale = report.stale_claims[0]
    assert stale.seg_id == "book-p0010-01"
    assert len(stale.paths) == 1
    assert stale.paths[0].reason == "missing"
    assert stale.paths[0].owner is None


def test_stale_claim_in_human_is_reported_with_human_location(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(
        data_dir,
        "book",
        "book-p0026-01",
        location="human",
        status="human",
        outcome="escalation_exhausted",
        tier="opus",
        pending_records=["records/book/rules_section/class-features.json"],
    )
    _write_record(
        data_dir,
        "records/book/rules_section/class-features.json",
        segment_id="book-p0148-01",
    )

    report = audit_book("book", data_dir=data_dir)

    assert len(report.stale_claims) == 1
    stale = report.stale_claims[0]
    assert stale.seg_id == "book-p0026-01"
    assert stale.location == "human"


def test_a_segment_owning_its_claim_has_no_stale_entry(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(
        data_dir,
        "book",
        "book-p0010-01",
        status="done",
        outcome="validated",
        records=["records/book/rules_section/class-features.json"],
    )
    _write_record(
        data_dir,
        "records/book/rules_section/class-features.json",
        segment_id="book-p0010-01",
    )

    report = audit_book("book", data_dir=data_dir)

    assert report.stale_claims == []


# ---------------------------------------------------------------------------
# render / to_json
# ---------------------------------------------------------------------------


def test_report_render_and_to_json_smoke(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0010-01")

    report = audit_book("book", data_dir=data_dir)

    assert "book:" in report.render()
    payload = report.to_json()
    assert payload["book_id"] == "book"
    assert payload["collisions"] == []
    assert payload["stale_claims"] == []


def test_render_names_the_collision_claimants_and_on_disk_owner(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(
        data_dir,
        "book",
        "book-p0010-01",
        status="done",
        outcome="validated",
        tier="haiku",
        records=["records/book/rules_section/class-features.json"],
    )
    _write_segment(
        data_dir,
        "book",
        "book-p0011-01",
        status="in_progress",
        tier="sonnet",
        pending_records=["records/book/rules_section/class-features.json"],
    )
    _write_record(
        data_dir,
        "records/book/rules_section/class-features.json",
        segment_id="book-p0011-01",
    )

    text = audit_book("book", data_dir=data_dir).render()

    # (a) collision line names the path, both claimants, and the on-disk owner.
    assert "records/book/rules_section/class-features.json" in text
    assert "book-p0010-01" in text
    assert "book-p0011-01" in text
    assert "book-p0011-01" in text.split("record path(s) claimed")[1].split("stale claim")[0]

    # (b) stale-claim section names the specific segment, its status/tier, and
    # the stale path -- not just an aggregate count.
    assert "book-p0010-01" in text.split("stale claim")[1]
    assert "status=done" in text
    assert "tier=haiku" in text
    assert "records/book/rules_section/class-features.json" in text.split("stale claim")[1]


# ---------------------------------------------------------------------------
# fix_book
# ---------------------------------------------------------------------------


def test_fix_book_soft_resets_a_done_victim_and_prunes_the_stale_path(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    prior_attempts = [
        {
            "tier": "haiku",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "errors": ["needs_context: which class?"],
            "kind": "needs_context",
        }
    ]
    _write_segment(
        data_dir,
        "book",
        "book-p0010-01",
        status="done",
        outcome="validated",
        tier="haiku",
        attempts=prior_attempts,
        records=[
            "records/book/rules_section/class-features.json",
            "records/book/rules_section/class-skills.json",
        ],
    )
    _write_record(
        data_dir,
        "records/book/rules_section/class-features.json",
        segment_id="book-p0011-01",
    )
    _write_record(
        data_dir,
        "records/book/rules_section/class-skills.json",
        segment_id="book-p0010-01",
    )

    results = fix_book("book", data_dir=data_dir)

    assert len(results) == 1
    assert results[0]["seg_id"] == "book-p0010-01"
    assert results[0]["action"] == "reset"

    segment = _read_segment(data_dir, "book", "book-p0010-01")
    assert segment["status"] == "pending"
    assert segment["outcome"] is None
    assert segment["outcome_reason"] is None
    assert segment["in_progress_since"] is None
    # The path it still owns survives; only the stolen one is pruned.
    assert segment["records"] == ["records/book/rules_section/class-skills.json"]
    # Tier/attempts are left alone -- it re-extracts at the tier it reached.
    assert segment["tier"] == "haiku"
    assert segment["attempts"] == prior_attempts


def test_fix_book_only_prunes_a_pending_segments_stale_claim(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(
        data_dir,
        "book",
        "book-p0011-01",
        status="pending",
        tier="sonnet",
        pending_records=["records/book/rules_section/class-features.json"],
    )
    _write_record(
        data_dir,
        "records/book/rules_section/class-features.json",
        segment_id="book-p0026-01",
    )

    results = fix_book("book", data_dir=data_dir)

    assert results[0]["action"] == "pruned"
    segment = _read_segment(data_dir, "book", "book-p0011-01")
    assert segment["status"] == "pending"
    assert segment["pending_records"] == []


def test_fix_book_leaves_a_human_segment_untouched(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    human_path = _write_segment(
        data_dir,
        "book",
        "book-p0026-01",
        location="human",
        status="human",
        outcome="escalation_exhausted",
        tier="opus",
        pending_records=["records/book/rules_section/class-features.json"],
    )
    _write_record(
        data_dir,
        "records/book/rules_section/class-features.json",
        segment_id="book-p0148-01",
    )
    original = human_path.read_text()

    results = fix_book("book", data_dir=data_dir)

    assert len(results) == 1
    assert results[0]["seg_id"] == "book-p0026-01"
    assert results[0]["action"] == "left_in_human"
    assert human_path.read_text() == original


def test_fix_book_never_touches_record_files(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(
        data_dir,
        "book",
        "book-p0010-01",
        status="done",
        outcome="validated",
        records=["records/book/rules_section/class-features.json"],
    )
    record_path = _write_record(
        data_dir,
        "records/book/rules_section/class-features.json",
        segment_id="book-p0011-01",
    )
    original = record_path.read_text()

    fix_book("book", data_dir=data_dir)

    assert record_path.is_file()
    assert record_path.read_text() == original


def test_fix_book_is_a_noop_when_nothing_is_stale(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(
        data_dir,
        "book",
        "book-p0010-01",
        status="pending",
    )

    results = fix_book("book", data_dir=data_dir)

    assert results == []


def test_fix_book_running_twice_is_a_noop_the_second_time(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(
        data_dir,
        "book",
        "book-p0010-01",
        status="done",
        outcome="validated",
        tier="haiku",
        attempts=[
            {
                "tier": "haiku",
                "timestamp": "2026-01-01T00:00:00+00:00",
                "errors": ["needs_context: which class?"],
                "kind": "needs_context",
            }
        ],
        records=[
            "records/book/rules_section/class-features.json",
            "records/book/rules_section/class-skills.json",
        ],
    )
    _write_record(
        data_dir,
        "records/book/rules_section/class-features.json",
        segment_id="book-p0011-01",
    )
    _write_record(
        data_dir,
        "records/book/rules_section/class-skills.json",
        segment_id="book-p0010-01",
    )

    first_results = fix_book("book", data_dir=data_dir)
    assert len(first_results) == 1
    after_first = _read_segment(data_dir, "book", "book-p0010-01")

    # Re-running immediately, with nothing else changed, must find no more
    # stale claims -- the pruned path is gone and the segment's own PASS
    # left `extraction.segment_id` matching, so it's no longer a victim.
    second_results = fix_book("book", data_dir=data_dir)
    assert second_results == []

    after_second = _read_segment(data_dir, "book", "book-p0010-01")
    assert after_second == after_first


# ---------------------------------------------------------------------------
# superseded_claims (batch B10c-mand2): a segment whose own `superseded_by`
# is set is frozen -- it must never be soft-reset via `stale_claims`, and
# any claim it still holds is reported separately so `--fix` can release
# it (see `owlsperch.supersede.release_segment_claims`) instead of pruning
# it like an ordinary stale claim.
# ---------------------------------------------------------------------------


def test_superseded_segment_holding_a_claim_is_reported_separately(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(
        data_dir,
        "book",
        "book-p0036-01",
        kind_hint="table",
        status="done",
        outcome="validated",
        superseded_by="book-class-p0034",
        records=["records/book/table/table-3-8-the-druid.json"],
    )
    _write_record(
        data_dir,
        "records/book/table/table-3-8-the-druid.json",
        segment_id="book-p0036-01",
    )

    report = audit_book("book", data_dir=data_dir)

    assert len(report.superseded_claims) == 1
    claim = report.superseded_claims[0]
    assert claim.seg_id == "book-p0036-01"
    assert claim.superseded_by == "book-class-p0034"
    assert claim.status == "done"
    assert claim.location == "segments"
    assert claim.paths == ["records/book/table/table-3-8-the-druid.json"]

    payload = report.to_json()
    assert payload["superseded_claims"][0]["seg_id"] == "book-p0036-01"
    assert "book-p0036-01" in report.render()


def test_superseded_segment_is_excluded_from_stale_claims_even_if_it_looks_stale(
    tmp_path: Path,
) -> None:
    """A superseded segment's own-disk-owner mismatch must never land it in
    `stale_claims` -- that pass can soft-reset a `done` segment back to
    `pending`, which would un-freeze a segment this batch deliberately
    freezes."""
    data_dir = tmp_path / "data"
    _write_segment(
        data_dir,
        "book",
        "book-p0036-01",
        kind_hint="table",
        status="done",
        outcome="validated",
        superseded_by="book-class-p0034",
        records=["records/book/table/table-3-8-the-druid.json"],
    )
    # The file's on-disk owner is a DIFFERENT segment -- would ordinarily
    # be "owned_by_other" stale, but must be reported ONLY as a superseded
    # claim.
    _write_record(
        data_dir,
        "records/book/table/table-3-8-the-druid.json",
        segment_id="book-class-p0034",
    )

    report = audit_book("book", data_dir=data_dir)

    assert report.stale_claims == []
    assert len(report.superseded_claims) == 1


def test_superseded_segment_with_no_claim_is_not_reported(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(
        data_dir,
        "book",
        "book-p0036-01",
        status="done",
        outcome="validated",
        superseded_by="book-class-p0034",
    )

    report = audit_book("book", data_dir=data_dir)

    assert report.superseded_claims == []
    assert report.stale_claims == []


def test_fix_book_releases_a_superseded_segments_claim(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(
        data_dir,
        "book",
        "book-p0036-01",
        kind_hint="table",
        status="done",
        outcome="validated",
        superseded_by="book-class-p0034",
        records=["records/book/table/table-3-8-the-druid.json"],
    )
    record_path = _write_record(
        data_dir,
        "records/book/table/table-3-8-the-druid.json",
        segment_id="book-p0036-01",
    )

    results = fix_book("book", data_dir=data_dir)

    assert len(results) == 1
    assert results[0]["seg_id"] == "book-p0036-01"
    assert results[0]["location"] == "segments"
    assert results[0]["action"] == "released"
    assert results[0]["pruned_paths"] == ["records/book/table/table-3-8-the-druid.json"]
    assert results[0]["moved"] == [
        {
            "from": "records/book/table/table-3-8-the-druid.json",
            "to": "superseded/book/table/table-3-8-the-druid.json",
        }
    ]

    assert not record_path.is_file()
    moved = data_dir / "superseded" / "book" / "table" / "table-3-8-the-druid.json"
    assert moved.is_file()

    segment = _read_segment(data_dir, "book", "book-p0036-01")
    assert segment["records"] == []
    assert segment["pending_records"] == []
    assert segment["superseded_by"] == "book-class-p0034"
    # Frozen -- not soft-reset like an ordinary stale-claim victim.
    assert segment["status"] == "done"
    assert len(segment["released_records"]) == 1


def test_fix_book_leaves_a_superseded_segment_in_human_untouched(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    human_path = _write_segment(
        data_dir,
        "book",
        "book-p0148-01",
        location="human",
        status="human",
        outcome="escalation_exhausted",
        superseded_by="book-class-p0034",
        pending_records=["records/book/table/table-3-8-the-druid.json"],
    )
    _write_record(
        data_dir,
        "records/book/table/table-3-8-the-druid.json",
        segment_id="book-p0148-01",
    )
    original = human_path.read_text()

    results = fix_book("book", data_dir=data_dir)

    assert len(results) == 1
    assert results[0]["seg_id"] == "book-p0148-01"
    assert results[0]["action"] == "left_in_human"
    assert human_path.read_text() == original
    assert (data_dir / "records" / "book" / "table" / "table-3-8-the-druid.json").is_file()
    assert not (data_dir / "superseded").exists()


def test_fix_book_release_is_idempotent(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(
        data_dir,
        "book",
        "book-p0036-01",
        kind_hint="table",
        status="done",
        outcome="validated",
        superseded_by="book-class-p0034",
        records=["records/book/table/table-3-8-the-druid.json"],
    )
    _write_record(
        data_dir,
        "records/book/table/table-3-8-the-druid.json",
        segment_id="book-p0036-01",
    )

    first = fix_book("book", data_dir=data_dir)
    assert len(first) == 1

    second = fix_book("book", data_dir=data_dir)
    assert second == []

    moved = data_dir / "superseded" / "book" / "table" / "table-3-8-the-druid.json"
    assert moved.is_file()
    # No new/renamed file was created the second time around.
    assert list((data_dir / "superseded" / "book" / "table").iterdir()) == [moved]


# ---------------------------------------------------------------------------
# wrongly_superseded (batch B10c-mand11): a segment stamped `superseded_by`
# by a class span that does not actually OWN it -- a printed sidebar, or the
# next class's own heading fragment on a shared page. Page span alone used
# to swallow both, moving their record files into `superseded/` and leaving
# that content in no canonical record at all. `--fix` restores them.
# ---------------------------------------------------------------------------


def _write_class_segment(data_dir: Path, book_id: str, seg_id: str, heading: str) -> Path:
    return _write_segment(
        data_dir,
        book_id,
        seg_id,
        pages=[52, 53],
        printed_pages=[52, 53],
        kind_hint="class",
        heading=heading,
        text=f"{heading} class text.",
        tier="sonnet",
    )


def _write_released_record(data_dir: Path, rel_path: str, *, segment_id: str) -> Path:
    """A record file already MOVED under `superseded/` by a (wrong) release."""
    return _write_record(data_dir, rel_path, segment_id=segment_id)


def test_wrongly_superseded_reports_a_sidebar_stamped_by_a_class(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_class_segment(data_dir, "book", "book-class-p0052", "Sorcerer")
    _write_segment(
        data_dir,
        "book",
        "book-p0053-03",
        status="done",
        outcome="validated",
        heading="FAMILIARS",
        pages=[53],
        printed_pages=[53],
        superseded_by="book-class-p0052",
        released_records=[
            {
                "path": "records/book/rules_section/familiars.json",
                "moved_to": "superseded/book/rules_section/familiars.json",
            }
        ],
    )

    report = audit_book("book", data_dir=data_dir)

    assert len(report.wrongly_superseded) == 1
    wrong = report.wrongly_superseded[0]
    assert wrong.seg_id == "book-p0053-03"
    assert wrong.superseded_by == "book-class-p0052"
    assert wrong.class_heading == "Sorcerer"
    assert wrong.heading == "FAMILIARS"
    assert wrong.kind_hint == "rules_section"
    assert wrong.status == "done"
    assert wrong.location == "segments"
    assert wrong.restorable_paths == ["records/book/rules_section/familiars.json"]

    payload = report.to_json()
    assert payload["wrongly_superseded"][0]["seg_id"] == "book-p0053-03"
    assert "FAMILIARS" in report.render()


def test_a_class_structural_fragment_is_not_wrongly_superseded(tmp_path: Path) -> None:
    """The keep-stamped half: a fragment whose heading IS class-structural
    for the stamping class is never reported (and, when it still holds a
    claim, still belongs to `superseded_claims` so `--fix` releases it)."""
    data_dir = tmp_path / "data"
    _write_class_segment(data_dir, "book", "book-class-p0052", "Sorcerer")
    for seg_id, heading in (
        ("book-p0053-01", "SORCERER"),
        ("book-p0053-02", "GAME RULE INFORMATION"),
        ("book-p0053-04", "Human Sorcerer Starting Package"),
    ):
        _write_segment(
            data_dir,
            "book",
            seg_id,
            heading=heading,
            pages=[53],
            printed_pages=[53],
            superseded_by="book-class-p0052",
        )
    _write_segment(
        data_dir,
        "book",
        "book-p0053-05",
        kind_hint="table",
        heading="Table 3–16: The Sorcerer",
        pages=[53],
        printed_pages=[53],
        superseded_by="book-class-p0052",
        records=["records/book/table/table-3-16-the-sorcerer.json"],
    )
    _write_record(
        data_dir, "records/book/table/table-3-16-the-sorcerer.json", segment_id="book-p0053-05"
    )

    report = audit_book("book", data_dir=data_dir)

    assert report.wrongly_superseded == []
    assert [c.seg_id for c in report.superseded_claims] == ["book-p0053-05"]


def test_superseded_by_naming_a_non_class_segment_is_left_alone(tmp_path: Path) -> None:
    """Only the toc-driven class pass ever stamps, so a `superseded_by`
    naming something that isn't a class/prestige_class segment (or naming a
    segment this book has no file for) is hand-made -- reported the old
    way, never undone."""
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0053-01", kind_hint="spell", heading="Fireball")
    _write_segment(
        data_dir,
        "book",
        "book-p0053-03",
        heading="FAMILIARS",
        superseded_by="book-p0053-01",
        records=["records/book/rules_section/familiars.json"],
    )
    _write_segment(
        data_dir,
        "book",
        "book-p0053-04",
        heading="FAMILIARS",
        superseded_by="book-class-p9999",
    )
    _write_record(data_dir, "records/book/rules_section/familiars.json", segment_id="book-p0053-03")

    report = audit_book("book", data_dir=data_dir)

    assert report.wrongly_superseded == []
    assert [c.seg_id for c in report.superseded_claims] == ["book-p0053-03"]


def test_fix_book_restores_a_wrongly_superseded_segment(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_class_segment(data_dir, "book", "book-class-p0052", "Sorcerer")
    seg_path = _write_segment(
        data_dir,
        "book",
        "book-p0053-03",
        status="done",
        outcome="validated",
        tier="sonnet",
        heading="FAMILIARS",
        pages=[53],
        printed_pages=[53],
        superseded_by="book-class-p0052",
        released_records=[
            {
                "path": "records/book/rules_section/familiars.json",
                "moved_to": "superseded/book/rules_section/familiars.json",
            }
        ],
    )
    moved_file = _write_released_record(
        data_dir, "superseded/book/rules_section/familiars.json", segment_id="book-p0053-03"
    )

    results = fix_book("book", data_dir=data_dir)

    assert results == [
        {
            "seg_id": "book-p0053-03",
            "location": "segments",
            "action": "restored",
            "pruned_paths": [],
            "restored_paths": ["records/book/rules_section/familiars.json"],
            "moved": [
                {
                    "from": "superseded/book/rules_section/familiars.json",
                    "to": "records/book/rules_section/familiars.json",
                }
            ],
            "blocked": [],
        }
    ]

    # The record file is back where the segment claims it ...
    assert not moved_file.exists()
    assert (data_dir / "records" / "book" / "rules_section" / "familiars.json").is_file()

    # ... the claim is back in `records`, `superseded_by`/`released_records`
    # are cleared, and the extraction bookkeeping is untouched (the record
    # had already been validated before the wrong stamp).
    restored = _read_segment(data_dir, "book", "book-p0053-03")
    assert restored["superseded_by"] is None
    assert restored["records"] == ["records/book/rules_section/familiars.json"]
    assert restored["released_records"] == []
    assert restored["status"] == "done"
    assert restored["outcome"] == "validated"
    assert restored["tier"] == "sonnet"
    assert seg_path.is_file()


def test_fix_book_restore_is_a_noop_the_second_time(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_class_segment(data_dir, "book", "book-class-p0052", "Sorcerer")
    _write_segment(
        data_dir,
        "book",
        "book-p0053-03",
        status="done",
        heading="FAMILIARS",
        superseded_by="book-class-p0052",
        released_records=[
            {
                "path": "records/book/rules_section/familiars.json",
                "moved_to": "superseded/book/rules_section/familiars.json",
            }
        ],
    )
    _write_released_record(
        data_dir, "superseded/book/rules_section/familiars.json", segment_id="book-p0053-03"
    )

    assert len(fix_book("book", data_dir=data_dir)) == 1
    after_first = _read_segment(data_dir, "book", "book-p0053-03")
    record_file = data_dir / "records" / "book" / "rules_section" / "familiars.json"
    record_body = record_file.read_text()

    assert fix_book("book", data_dir=data_dir) == []
    assert _read_segment(data_dir, "book", "book-p0053-03") == after_first
    assert record_file.read_text() == record_body
    assert audit_book("book", data_dir=data_dir).wrongly_superseded == []


def test_fix_book_never_overwrites_an_existing_destination(tmp_path: Path) -> None:
    """A destination that already exists (a live segment re-extracted the
    same path since) is never clobbered: the file stays under
    `superseded/`, the entry is reported blocked, and its
    `released_records` entry is kept so the pointer isn't lost."""
    data_dir = tmp_path / "data"
    _write_class_segment(data_dir, "book", "book-class-p0052", "Sorcerer")
    _write_segment(
        data_dir,
        "book",
        "book-p0053-03",
        status="done",
        heading="FAMILIARS",
        superseded_by="book-class-p0052",
        released_records=[
            {
                "path": "records/book/rules_section/familiars.json",
                "moved_to": "superseded/book/rules_section/familiars.json",
            }
        ],
    )
    _write_released_record(
        data_dir, "superseded/book/rules_section/familiars.json", segment_id="book-p0053-03"
    )
    live = _write_record(
        data_dir, "records/book/rules_section/familiars.json", segment_id="book-p0060-01"
    )
    live_body = live.read_text()

    results = fix_book("book", data_dir=data_dir)

    assert results[0]["restored_paths"] == []
    assert results[0]["moved"] == []
    assert results[0]["blocked"] == [
        {
            "path": "records/book/rules_section/familiars.json",
            "moved_to": "superseded/book/rules_section/familiars.json",
            "reason": "destination_exists",
        }
    ]
    assert live.read_text() == live_body
    assert (data_dir / "superseded" / "book" / "rules_section" / "familiars.json").is_file()

    restored = _read_segment(data_dir, "book", "book-p0053-03")
    # Still stamped: a blocked entry must be retried by a later --fix, so
    # the segment stays in `wrongly_superseded` rather than silently
    # dropping out with its file stranded under superseded/.
    assert restored["superseded_by"] == "book-class-p0052"
    assert restored["records"] == []
    assert restored["released_records"] == [
        {
            "path": "records/book/rules_section/familiars.json",
            "moved_to": "superseded/book/rules_section/familiars.json",
        }
    ]
    # Once the collision is cleared, the next --fix restores it.
    live.unlink()
    results = fix_book("book", data_dir=data_dir)
    assert results[0]["blocked"] == []
    assert results[0]["restored_paths"] == ["records/book/rules_section/familiars.json"]
    assert _read_segment(data_dir, "book", "book-p0053-03")["superseded_by"] is None


def test_fix_book_leaves_a_wrongly_superseded_segment_in_human_untouched(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_class_segment(data_dir, "book", "book-class-p0052", "Sorcerer")
    _write_segment(
        data_dir,
        "book",
        "book-p0053-03",
        location="human",
        status="done",
        heading="FAMILIARS",
        superseded_by="book-class-p0052",
        released_records=[
            {
                "path": "records/book/rules_section/familiars.json",
                "moved_to": "superseded/book/rules_section/familiars.json",
            }
        ],
    )
    moved_file = _write_released_record(
        data_dir, "superseded/book/rules_section/familiars.json", segment_id="book-p0053-03"
    )
    before = _read_segment(data_dir, "book", "book-p0053-03", location="human")

    results = fix_book("book", data_dir=data_dir)

    assert results == [
        {
            "seg_id": "book-p0053-03",
            "location": "human",
            "action": "left_in_human",
            "pruned_paths": ["records/book/rules_section/familiars.json"],
        }
    ]
    assert _read_segment(data_dir, "book", "book-p0053-03", location="human") == before
    assert moved_file.is_file()
