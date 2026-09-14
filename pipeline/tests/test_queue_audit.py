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
