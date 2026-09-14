"""Tests for `owlsperch.supersede.release_segment_claims` (batch
B10c-mand2): releasing the record claims a superseded segment still holds,
so the class segment superseding it can claim the same path (most often a
level table sharing the class's own printed title, and so the same
slug/id/path).

For ONE superseded segment, `release_segment_claims` walks its own
`records` + `pending_records` (deduplicated by resolved path, order
preserved), and for each claimed path:

- moves the record file from `records/<book_id>/<type>/<file>.json` to
  `$OWLSPERCH_DATA/superseded/<book_id>/<type>/<file>.json` (never
  deleting it), UNLESS the file's own `extraction.segment_id` names a
  DIFFERENT, still-live (not itself superseded) segment -- that claim is
  pruned but the file is left exactly where it is, since releasing must
  never steal a live segment's record;
- always clears `records`/`pending_records` on the passed-in `segment`
  object and appends one `ReleasedRecord` per claimed path onto
  `segment.released_records`;
- never writes the segment file itself -- only the record files it moves.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from owlsperch.segment.runner import Segment
from owlsperch.supersede import release_segment_claims


def _make_segment(**overrides: Any) -> Segment:
    defaults: dict[str, Any] = dict(
        seg_id="book-p0036-01",
        book_id="book",
        pages=[36],
        printed_pages=[36],
        kind_hint="table",
        heading="The Druid",
        text="The Druid table text.",
        status="done",
        tier="haiku",
        created_at="2026-01-01T00:00:00+00:00",
        superseded_by="book-class-p0034",
    )
    defaults.update(overrides)
    return Segment(**defaults)


def _write_record(data_dir: Path, rel_path: str, *, segment_id: str | None) -> Path:
    path = data_dir / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    body: dict[str, object] = {}
    if segment_id is not None:
        body["extraction"] = {
            "tier": "haiku",
            "model": "claude-haiku-4-5",
            "segment_id": segment_id,
            "timestamp": "2026-01-01T00:00:00+00:00",
        }
    path.write_text(json.dumps(body, indent=2))
    return path


def _write_other_segment(
    data_dir: Path, book_id: str, seg_id: str, *, location: str = "segments", **overrides: Any
) -> None:
    defaults: dict[str, Any] = dict(
        seg_id=seg_id,
        book_id=book_id,
        pages=[1],
        printed_pages=[1],
        kind_hint="rules_section",
        heading="Other",
        text="Other text.",
        status="pending",
        tier="haiku",
        created_at="2026-01-01T00:00:00+00:00",
    )
    defaults.update(overrides)
    segment = Segment(**defaults)
    seg_dir = data_dir / location / book_id
    seg_dir.mkdir(parents=True, exist_ok=True)
    (seg_dir / f"{seg_id}.json").write_text(segment.model_dump_json(indent=2))


def test_normal_move(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    record_path = _write_record(
        data_dir,
        "records/book/table/table-3-8-the-druid.json",
        segment_id="book-p0036-01",
    )
    segment = _make_segment(records=["records/book/table/table-3-8-the-druid.json"])

    released = release_segment_claims(segment, data_dir=data_dir)

    assert len(released) == 1
    assert released[0].path == "records/book/table/table-3-8-the-druid.json"
    assert released[0].moved_to == "superseded/book/table/table-3-8-the-druid.json"

    assert not record_path.is_file()
    dest = data_dir / "superseded" / "book" / "table" / "table-3-8-the-druid.json"
    assert dest.is_file()

    assert segment.records == []
    assert segment.pending_records == []
    assert segment.released_records == released


def test_deduplicates_a_path_claimed_in_both_lists(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_record(
        data_dir,
        "records/book/table/table-3-8-the-druid.json",
        segment_id="book-p0036-01",
    )
    segment = _make_segment(
        records=["records/book/table/table-3-8-the-druid.json"],
        pending_records=["records/book/table/table-3-8-the-druid.json"],
    )

    released = release_segment_claims(segment, data_dir=data_dir)

    assert len(released) == 1


def test_claimed_path_missing_on_disk_is_pruned_without_creating_anything(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    segment = _make_segment(records=["records/book/table/table-3-8-the-druid.json"])

    released = release_segment_claims(segment, data_dir=data_dir)

    assert len(released) == 1
    assert released[0].path == "records/book/table/table-3-8-the-druid.json"
    assert released[0].moved_to is None
    assert not (data_dir / "superseded").exists()
    assert segment.records == []


def test_claim_owned_by_a_different_live_segment_is_left_untouched(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    record_path = _write_record(
        data_dir,
        "records/book/table/table-3-8-the-druid.json",
        segment_id="book-p9999-01",
    )
    _write_other_segment(data_dir, "book", "book-p9999-01")  # live, not superseded
    original = record_path.read_text()
    segment = _make_segment(records=["records/book/table/table-3-8-the-druid.json"])

    released = release_segment_claims(segment, data_dir=data_dir)

    assert len(released) == 1
    assert released[0].moved_to is None
    assert record_path.is_file()
    assert record_path.read_text() == original
    assert not (data_dir / "superseded").exists()
    assert segment.records == []


def test_claim_owned_by_a_different_but_also_superseded_segment_is_moved(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    record_path = _write_record(
        data_dir,
        "records/book/table/table-3-8-the-druid.json",
        segment_id="book-p0037-01",
    )
    _write_other_segment(data_dir, "book", "book-p0037-01", superseded_by="book-class-p0034")
    segment = _make_segment(records=["records/book/table/table-3-8-the-druid.json"])

    released = release_segment_claims(segment, data_dir=data_dir)

    assert released[0].moved_to == "superseded/book/table/table-3-8-the-druid.json"
    assert not record_path.is_file()


def test_claim_with_no_extraction_segment_id_is_moved(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    record_path = _write_record(
        data_dir, "records/book/table/table-3-8-the-druid.json", segment_id=None
    )
    segment = _make_segment(records=["records/book/table/table-3-8-the-druid.json"])

    released = release_segment_claims(segment, data_dir=data_dir)

    assert released[0].moved_to == "superseded/book/table/table-3-8-the-druid.json"
    assert not record_path.is_file()


def test_destination_name_collision_is_never_overwritten(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_record(
        data_dir,
        "records/book/table/table-3-8-the-druid.json",
        segment_id="book-p0036-01",
    )
    first_taken = data_dir / "superseded" / "book" / "table" / "table-3-8-the-druid.json"
    first_taken.parent.mkdir(parents=True, exist_ok=True)
    first_taken.write_text('{"marker": "pre-existing-1"}')
    second_taken = (
        data_dir / "superseded" / "book" / "table" / "table-3-8-the-druid-book-p0036-01.json"
    )
    second_taken.write_text('{"marker": "pre-existing-2"}')

    segment = _make_segment(records=["records/book/table/table-3-8-the-druid.json"])
    released = release_segment_claims(segment, data_dir=data_dir)

    assert released[0].moved_to == "superseded/book/table/table-3-8-the-druid-book-p0036-01-2.json"
    assert json.loads(first_taken.read_text()) == {"marker": "pre-existing-1"}
    assert json.loads(second_taken.read_text()) == {"marker": "pre-existing-2"}
    moved_file = data_dir / released[0].moved_to
    assert moved_file.is_file()


def test_never_writes_the_segment_file_itself(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_record(
        data_dir,
        "records/book/table/table-3-8-the-druid.json",
        segment_id="book-p0036-01",
    )
    seg_dir = data_dir / "segments" / "book"
    seg_dir.mkdir(parents=True, exist_ok=True)
    segment = _make_segment(records=["records/book/table/table-3-8-the-druid.json"])
    seg_path = seg_dir / f"{segment.seg_id}.json"
    seg_path.write_text(segment.model_dump_json(indent=2))
    original = seg_path.read_text()

    release_segment_claims(segment, data_dir=data_dir)

    # The in-memory object changed, but nothing was written back to disk.
    assert seg_path.read_text() == original
