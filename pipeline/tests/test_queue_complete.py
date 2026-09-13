"""Tests for `owlsperch.queue.complete` (and `owlsperch queue complete`), per
B5 acceptance criterion 3: ingest the subagent's final JSON, covering the
`no_content`, `records` (-> `pending_records`), and malformed-JSON branches.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

from owlsperch.queue.complete import QueueError, complete_segment
from owlsperch.segment.runner import Segment


def _write_segment(data_dir: Path, book_id: str, seg_id: str, **overrides: object) -> Path:
    defaults: dict[str, Any] = dict(
        seg_id=seg_id,
        book_id=book_id,
        pages=[10],
        printed_pages=[10],
        kind_hint="spell",
        heading="Fireball",
        text="Fireball\n\nEvocation Level: Sor/Wiz 3.",
        status="in_progress",
        tier="haiku",
        created_at="2026-01-01T00:00:00+00:00",
        in_progress_since="2026-01-01T00:05:00+00:00",
    )
    defaults.update(overrides)
    segment = Segment(**defaults)
    seg_dir = data_dir / "segments" / book_id
    seg_dir.mkdir(parents=True, exist_ok=True)
    path = seg_dir / f"{seg_id}.json"
    path.write_text(segment.model_dump_json(indent=2))
    return path


def _read_segment(data_dir: Path, book_id: str, seg_id: str) -> dict[str, Any]:
    path = data_dir / "segments" / book_id / f"{seg_id}.json"
    raw: dict[str, Any] = json.loads(path.read_text())
    return raw


def _write_record_file(data_dir: Path, rel_path: str) -> None:
    """Create an (empty-content-wise) record file on disk at `rel_path`
    (relative to `data_dir`) -- `complete_segment` (B5 follow-up) requires a
    claimed record path to actually exist, not just be a well-formed
    string."""
    path = data_dir / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}")


# ---------------------------------------------------------------------------
# records -> pending_records
# ---------------------------------------------------------------------------


def test_records_result_sets_pending_records_and_status_pending(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0010-01")
    _write_record_file(data_dir, "records/book/spell/fireball.json")
    result = json.dumps(
        {
            "seg_id": "book-p0010-01",
            "records": ["records/book/spell/fireball.json"],
            "no_content": None,
            "notes": "found one spell",
        }
    )

    outcome = complete_segment("book-p0010-01", result, data_dir=data_dir)

    assert outcome.outcome == "pending_records"
    segment = _read_segment(data_dir, "book", "book-p0010-01")
    assert segment["status"] == "pending"
    assert segment["pending_records"] == ["records/book/spell/fireball.json"]
    assert segment["in_progress_since"] is None
    # A plain string `notes` value is normalized to a one-element list.
    assert segment["notes"] == ["found one spell"]


def test_records_result_merges_without_duplicating(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(
        data_dir,
        "book",
        "book-p0010-01",
        pending_records=["records/book/spell/fireball.json"],
    )
    _write_record_file(data_dir, "records/book/spell/fireball.json")
    _write_record_file(data_dir, "records/book/spell/icy-bolt.json")
    result = json.dumps(
        {
            "seg_id": "book-p0010-01",
            "records": ["records/book/spell/fireball.json", "records/book/spell/icy-bolt.json"],
            "no_content": None,
            "notes": "",
        }
    )

    complete_segment("book-p0010-01", result, data_dir=data_dir)

    segment = _read_segment(data_dir, "book", "book-p0010-01")
    assert segment["pending_records"] == [
        "records/book/spell/fireball.json",
        "records/book/spell/icy-bolt.json",
    ]


# ---------------------------------------------------------------------------
# no_content
# ---------------------------------------------------------------------------


def test_no_content_marks_segment_done_with_reason(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0010-01")
    result = json.dumps(
        {
            "seg_id": "book-p0010-01",
            "records": [],
            "no_content": {"reason": "table of contents entry, no rule text"},
            "notes": "",
        }
    )

    outcome = complete_segment("book-p0010-01", result, data_dir=data_dir)

    assert outcome.outcome == "no_content"
    segment = _read_segment(data_dir, "book", "book-p0010-01")
    assert segment["status"] == "done"
    assert segment["outcome"] == "no_content"
    assert segment["outcome_reason"] == "table of contents entry, no rule text"
    assert segment["in_progress_since"] is None


# ---------------------------------------------------------------------------
# malformed
# ---------------------------------------------------------------------------


def test_invalid_json_is_malformed_result(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0010-01")

    outcome = complete_segment("book-p0010-01", "{not json", data_dir=data_dir)

    assert outcome.outcome == "malformed"
    segment = _read_segment(data_dir, "book", "book-p0010-01")
    assert segment["status"] == "pending"
    # A malformed reply is "treated as a validation failure and escalated"
    # (spec 4.5, batch B8) -- one attempt advances the tier immediately.
    assert segment["tier"] == "sonnet"
    assert len(segment["attempts"]) == 1
    assert "malformed_result" in segment["attempts"][0]["errors"][0]
    assert segment["attempts"][0]["kind"] == "malformed"


def test_missing_required_keys_is_malformed_result(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0010-01")

    outcome = complete_segment("book-p0010-01", json.dumps({"notes": "oops"}), data_dir=data_dir)

    assert outcome.outcome == "malformed"
    segment = _read_segment(data_dir, "book", "book-p0010-01")
    assert segment["status"] == "pending"


def test_non_object_json_is_malformed_result(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0010-01")

    outcome = complete_segment("book-p0010-01", json.dumps([1, 2, 3]), data_dir=data_dir)

    assert outcome.outcome == "malformed"


def test_records_not_a_list_of_strings_is_malformed_result(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0010-01")
    result = json.dumps({"seg_id": "book-p0010-01", "records": [1, 2], "no_content": None})

    outcome = complete_segment("book-p0010-01", result, data_dir=data_dir)

    assert outcome.outcome == "malformed"


def test_no_content_without_reason_is_malformed_result(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0010-01")
    result = json.dumps({"seg_id": "book-p0010-01", "records": [], "no_content": {}})

    outcome = complete_segment("book-p0010-01", result, data_dir=data_dir)

    assert outcome.outcome == "malformed"


def test_repeated_malformed_replies_keep_escalating_not_stuck_at_same_tier(
    tmp_path: Path,
) -> None:
    """Unlike a `validate` rerun on an unchanged record (which has a fixed
    `extraction.tier` and is genuinely idempotent, see
    `owlsperch.queue.ladder`'s module docstring), two `queue complete` calls
    with identical malformed text are two distinct subagent attempts (one
    per `queue next` selection) -- each one escalates the segment, it never
    gets stuck re-recording the same attempt at the same tier."""
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0010-01")

    complete_segment("book-p0010-01", "{not json", data_dir=data_dir)
    complete_segment("book-p0010-01", "{not json", data_dir=data_dir)

    segment = _read_segment(data_dir, "book", "book-p0010-01")
    assert len(segment["attempts"]) == 2
    assert [a["tier"] for a in segment["attempts"]] == ["haiku", "sonnet"]
    assert segment["tier"] == "opus"
    assert segment["status"] == "pending"


def test_identical_validation_reply_at_the_same_fixed_tier_is_idempotent(
    tmp_path: Path,
) -> None:
    """When `record_failure` is given the *same* attempt tier explicitly
    (mirroring `owlsperch.validate.runner._write_back_fail`'s use of a
    record's own fixed `extraction.tier`), a repeat of the identical
    `(tier, kind, errors)` is a true no-op -- this is the guard
    `owlsperch.queue.ladder.record_failure`'s docstring describes, exercised
    directly here since `queue complete` itself has no such fixed tier to
    pass (see the test above). This guard applies only to `kind ==
    "validation"` (the only kind `_write_back_fail` ever passes an explicit
    `tier` for) -- `malformed` and `needs_context` are exercised elsewhere
    and must NOT get this no-op treatment, since every call for those kinds
    is a genuinely distinct subagent reply."""
    from owlsperch.queue.ladder import record_failure
    from owlsperch.segment.runner import Segment

    segment = Segment(
        seg_id="book-p0010-01",
        book_id="book",
        pages=[10],
        printed_pages=[10],
        kind_hint="spell",
        heading="Fireball",
        text="Fireball text.",
        created_at="2026-01-01T00:00:00+00:00",
    )

    first = record_failure(segment, ["boom"], kind="validation", tier="haiku")
    second = record_failure(segment, ["boom"], kind="validation", tier="haiku")

    assert first.escalated_to == "sonnet"
    assert second == type(second)()  # a true no-op: no further movement
    assert len(segment.attempts) == 1
    assert segment.tier == "sonnet"


def test_seg_id_mismatch_is_malformed_result(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0010-01")
    result = json.dumps({"seg_id": "book-p0099-01", "records": [], "no_content": {"reason": "art"}})

    outcome = complete_segment("book-p0010-01", result, data_dir=data_dir)

    assert outcome.outcome == "malformed"
    assert "seg_id mismatch" in outcome.detail
    segment = _read_segment(data_dir, "book", "book-p0010-01")
    assert segment["status"] == "pending"


def test_missing_seg_id_is_malformed_result(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0010-01")
    result = json.dumps({"records": [], "no_content": {"reason": "art"}})

    outcome = complete_segment("book-p0010-01", result, data_dir=data_dir)

    assert outcome.outcome == "malformed"


def test_notes_as_list_of_strings_is_stored_as_is(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0010-01")
    result = json.dumps(
        {
            "seg_id": "book-p0010-01",
            "records": [],
            "no_content": {"reason": "art"},
            "notes": ["unnamed_entity: Conjuration (Creation) [Acid] Level: Sor/Wiz 0"],
        }
    )

    complete_segment("book-p0010-01", result, data_dir=data_dir)

    segment = _read_segment(data_dir, "book", "book-p0010-01")
    assert segment["notes"] == ["unnamed_entity: Conjuration (Creation) [Acid] Level: Sor/Wiz 0"]


def test_notes_not_a_string_or_list_of_strings_is_malformed(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0010-01")
    result = json.dumps(
        {"seg_id": "book-p0010-01", "records": [], "no_content": {"reason": "art"}, "notes": 5}
    )

    outcome = complete_segment("book-p0010-01", result, data_dir=data_dir)

    assert outcome.outcome == "malformed"


def test_notes_merge_across_multiple_completes_without_duplicating(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0010-01")
    _write_record_file(data_dir, "records/book/spell/fireball.json")
    first = json.dumps(
        {
            "seg_id": "book-p0010-01",
            "records": ["records/book/spell/fireball.json"],
            "no_content": None,
            "notes": ["first note"],
        }
    )
    second = json.dumps(
        {
            "seg_id": "book-p0010-01",
            "records": [],
            "no_content": {"reason": "done"},
            "notes": ["first note", "second note"],
        }
    )

    complete_segment("book-p0010-01", first, data_dir=data_dir)
    complete_segment("book-p0010-01", second, data_dir=data_dir)

    segment = _read_segment(data_dir, "book", "book-p0010-01")
    assert segment["notes"] == ["first note", "second note"]


# ---------------------------------------------------------------------------
# Record path validation (each claimed path must resolve inside
# records/<book_id>/ under the data dir and exist on disk)
# ---------------------------------------------------------------------------


def test_record_path_outside_book_records_dir_is_rejected(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0010-01")
    _write_record_file(data_dir, "records/other-book/spell/fireball.json")
    result = json.dumps(
        {
            "seg_id": "book-p0010-01",
            "records": ["records/other-book/spell/fireball.json"],
            "no_content": None,
            "notes": "",
        }
    )

    outcome = complete_segment("book-p0010-01", result, data_dir=data_dir)

    assert outcome.outcome == "pending_records"
    segment = _read_segment(data_dir, "book", "book-p0010-01")
    assert segment["pending_records"] == []
    assert len(segment["attempts"]) == 1
    assert segment["attempts"][0]["errors"] == [
        "missing_record_path: records/other-book/spell/fireball.json"
    ]
    assert segment["status"] == "pending"


def test_record_path_with_traversal_outside_records_dir_is_rejected(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0010-01")
    result = json.dumps(
        {
            "seg_id": "book-p0010-01",
            "records": ["records/book/spell/../../../etc/passwd"],
            "no_content": None,
            "notes": "",
        }
    )

    outcome = complete_segment("book-p0010-01", result, data_dir=data_dir)

    assert outcome.outcome == "pending_records"
    segment = _read_segment(data_dir, "book", "book-p0010-01")
    assert segment["pending_records"] == []
    assert "missing_record_path" in segment["attempts"][0]["errors"][0]


def test_record_path_that_does_not_exist_on_disk_is_rejected(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0010-01")
    result = json.dumps(
        {
            "seg_id": "book-p0010-01",
            "records": ["records/book/spell/never-written.json"],
            "no_content": None,
            "notes": "",
        }
    )

    outcome = complete_segment("book-p0010-01", result, data_dir=data_dir)

    assert outcome.outcome == "pending_records"
    segment = _read_segment(data_dir, "book", "book-p0010-01")
    assert segment["pending_records"] == []
    assert segment["attempts"][0]["errors"] == [
        "missing_record_path: records/book/spell/never-written.json"
    ]


def test_mix_of_valid_and_invalid_record_paths(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0010-01")
    _write_record_file(data_dir, "records/book/spell/fireball.json")
    result = json.dumps(
        {
            "seg_id": "book-p0010-01",
            "records": [
                "records/book/spell/fireball.json",
                "records/book/spell/never-written.json",
            ],
            "no_content": None,
            "notes": "",
        }
    )

    outcome = complete_segment("book-p0010-01", result, data_dir=data_dir)

    assert outcome.outcome == "pending_records"
    segment = _read_segment(data_dir, "book", "book-p0010-01")
    assert segment["pending_records"] == ["records/book/spell/fireball.json"]
    assert segment["attempts"][0]["errors"] == [
        "missing_record_path: records/book/spell/never-written.json"
    ]


# ---------------------------------------------------------------------------
# Segment lookup (hyphenated book_ids, unknown seg_id)
# ---------------------------------------------------------------------------


def test_finds_segment_for_hyphenated_book_id(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "dmg1-building-a-city-we", "dmg1-building-a-city-we-p0001-01")
    result = json.dumps(
        {
            "seg_id": "dmg1-building-a-city-we-p0001-01",
            "records": [],
            "no_content": {"reason": "art"},
        }
    )

    outcome = complete_segment("dmg1-building-a-city-we-p0001-01", result, data_dir=data_dir)

    assert outcome.outcome == "no_content"


def test_unknown_seg_id_raises_queue_error(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)

    try:
        complete_segment("does-not-exist", "{}", data_dir=data_dir)
        raise AssertionError("expected QueueError")
    except QueueError:
        pass


# ---------------------------------------------------------------------------
# Tolerant result parsing (B5 follow-up 1): two of four real haiku replies in
# the trial came back wrapped in ```json fences and were wrongly rejected as
# malformed. `_parse_result` must accept a fenced reply, a reply with a
# leading sentence, and bare JSON alike.
# ---------------------------------------------------------------------------


def test_fenced_json_reply_is_parsed(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0010-01")
    payload = json.dumps(
        {"seg_id": "book-p0010-01", "records": [], "no_content": {"reason": "art"}, "notes": []}
    )
    result = f"```json\n{payload}\n```"

    outcome = complete_segment("book-p0010-01", result, data_dir=data_dir)

    assert outcome.outcome == "no_content"
    segment = _read_segment(data_dir, "book", "book-p0010-01")
    assert segment["outcome_reason"] == "art"


def test_reply_with_leading_sentence_before_json_is_parsed(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0010-01")
    payload = json.dumps(
        {"seg_id": "book-p0010-01", "records": [], "no_content": {"reason": "art"}, "notes": []}
    )
    result = f"Here is my final result:\n\n{payload}"

    outcome = complete_segment("book-p0010-01", result, data_dir=data_dir)

    assert outcome.outcome == "no_content"


def test_bare_json_reply_is_parsed(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0010-01")
    result = json.dumps(
        {"seg_id": "book-p0010-01", "records": [], "no_content": {"reason": "art"}, "notes": []}
    )

    outcome = complete_segment("book-p0010-01", result, data_dir=data_dir)

    assert outcome.outcome == "no_content"


def test_fenced_reply_with_records_still_validates_paths(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0010-01")
    _write_record_file(data_dir, "records/book/spell/fireball.json")
    payload = json.dumps(
        {
            "seg_id": "book-p0010-01",
            "records": ["records/book/spell/fireball.json"],
            "no_content": None,
            "notes": [],
        }
    )
    result = f"```json\n{payload}\n```"

    outcome = complete_segment("book-p0010-01", result, data_dir=data_dir)

    assert outcome.outcome == "pending_records"
    segment = _read_segment(data_dir, "book", "book-p0010-01")
    assert segment["pending_records"] == ["records/book/spell/fireball.json"]


# ---------------------------------------------------------------------------
# Authoritative extraction provenance (B5 follow-up 3): every accepted
# claimed record file's `extraction` is overwritten with the segment's own
# tier/model/segment_id and a fresh timestamp, regardless of whatever
# (possibly placeholder or bogus) values the subagent wrote.
# ---------------------------------------------------------------------------


def test_accepted_record_gets_authoritative_extraction_overwritten(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0010-01", tier="haiku", model="claude-haiku-4-5")
    record_path = data_dir / "records" / "book" / "spell" / "fireball.json"
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.write_text(
        json.dumps(
            {
                "name": "Fireball",
                "extraction": {
                    "tier": "bogus-tier",
                    "model": "bogus-model",
                    "segment_id": "bogus-segment",
                    "timestamp": "1999-01-01T00:00:00+00:00",
                },
            }
        )
    )
    result = json.dumps(
        {
            "seg_id": "book-p0010-01",
            "records": ["records/book/spell/fireball.json"],
            "no_content": None,
            "notes": [],
        }
    )

    complete_segment("book-p0010-01", result, data_dir=data_dir)

    record = json.loads(record_path.read_text())
    assert record["extraction"]["tier"] == "haiku"
    assert record["extraction"]["model"] == "claude-haiku-4-5"
    assert record["extraction"]["segment_id"] == "book-p0010-01"
    assert record["extraction"]["timestamp"] != "1999-01-01T00:00:00+00:00"
    # The rest of the record is untouched.
    assert record["name"] == "Fireball"


def test_accepted_record_extraction_falls_back_to_default_model_when_segment_has_none(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    # No `model` override -- simulates a segment that never went through
    # `queue next` (e.g. hand-crafted in a test or an old segment file).
    _write_segment(data_dir, "book", "book-p0010-01")
    _write_record_file(data_dir, "records/book/spell/fireball.json")
    result = json.dumps(
        {
            "seg_id": "book-p0010-01",
            "records": ["records/book/spell/fireball.json"],
            "no_content": None,
            "notes": [],
        }
    )

    complete_segment("book-p0010-01", result, data_dir=data_dir)

    record = json.loads((data_dir / "records" / "book" / "spell" / "fireball.json").read_text())
    assert record["extraction"]["model"] == "claude-haiku-4-5"


# ---------------------------------------------------------------------------
# Authoritative pages/book_id (B6 follow-up): a live trial found ~10% of
# real haiku extractions put the printed page number in `pages` instead of
# the PDF index -- `queue complete` must overwrite `pages`/`book_id` with
# the segment's own values, exactly like it already does for `extraction`,
# so a record like that stops failing validate's page-within-segment-span
# check.
# ---------------------------------------------------------------------------


def _repo_schemas_dir() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "schemas"


def _full_spell_record(*, pages: list[int], book_id: str = "book") -> dict[str, Any]:
    return {
        "id": f"spell:{book_id}:fireball",
        "type": "spell",
        "name": "Fireball",
        "slug": "fireball",
        "aliases": [],
        "book_id": book_id,
        "pages": pages,
        "citation": "Test Book p. 196",
        "text_md": "Deals fire damage in a burst.",
        "fields": {
            "school": "Evocation",
            "subschool": None,
            "descriptors": ["Fire"],
            "levels": [{"class": "Sorcerer", "level": 3}, {"class": "Wizard", "level": 3}],
            "components": ["V", "S", "M"],
            "casting_time": "1 standard action",
            "range": "Long",
            "target_effect_area": "20-ft.-radius burst",
            "duration": "Instantaneous",
            "saving_throw": "Reflex half",
            "spell_resistance": "Yes",
            "costs": {"material": None, "focus": None, "xp": None},
        },
        "tables": [],
        "canonical": False,
        "variant_of": None,
        "applied_overrides": [],
        "macro_eligible": False,
        "schema_version": 3,
        "extraction": {
            "tier": "bogus-tier",
            "model": "bogus-model",
            "segment_id": "bogus-segment",
            "timestamp": "1999-01-01T00:00:00+00:00",
        },
    }


def test_accepted_record_gets_authoritative_pages_and_book_id_overwritten(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    _write_segment(
        data_dir,
        "book",
        "book-p0010-01",
        pages=[197, 198],
        printed_pages=[196, 197],
    )
    record_path = data_dir / "records" / "book" / "spell" / "fireball.json"
    record_path.parent.mkdir(parents=True, exist_ok=True)
    # The model wrongly wrote the printed page number instead of the PDF
    # page indices.
    record_path.write_text(json.dumps(_full_spell_record(pages=[196])))
    result = json.dumps(
        {
            "seg_id": "book-p0010-01",
            "records": ["records/book/spell/fireball.json"],
            "no_content": None,
            "notes": [],
        }
    )

    complete_segment("book-p0010-01", result, data_dir=data_dir)

    record = json.loads(record_path.read_text())
    assert record["pages"] == [197, 198]
    assert record["book_id"] == "book"


def test_record_with_wrong_pages_is_corrected_after_complete_and_then_passes_validation(
    tmp_path: Path,
) -> None:
    from owlsperch.validate.runner import run_validate

    data_dir = tmp_path / "data"
    _write_segment(
        data_dir,
        "book",
        "book-p0010-01",
        pages=[197, 198],
        printed_pages=[196, 197],
    )
    record_path = data_dir / "records" / "book" / "spell" / "fireball.json"
    record_path.parent.mkdir(parents=True, exist_ok=True)
    # Wrong: the printed page number (196) instead of the PDF indices
    # ([197, 198]) -- would fail check_pages_within_segment.
    record_path.write_text(json.dumps(_full_spell_record(pages=[196])))
    result = json.dumps(
        {
            "seg_id": "book-p0010-01",
            "records": ["records/book/spell/fireball.json"],
            "no_content": None,
            "notes": [],
        }
    )

    complete_segment("book-p0010-01", result, data_dir=data_dir)

    out = io.StringIO()
    exit_code = run_validate("book", data_dir=data_dir, schemas_dir=_repo_schemas_dir(), out=out)

    assert exit_code == 0
    assert "PASS records/book/spell/fireball.json" in out.getvalue()
    record = json.loads(record_path.read_text())
    assert record["pages"] == [197, 198]


# ---------------------------------------------------------------------------
# missing_record_path escalates the tier too (batch B8 -- "treated as a
# validation failure and escalated").
# ---------------------------------------------------------------------------


def test_missing_record_path_advances_tier(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0010-01")
    result = json.dumps(
        {
            "seg_id": "book-p0010-01",
            "records": ["records/book/spell/never-written.json"],
            "no_content": None,
            "notes": "",
        }
    )

    complete_segment("book-p0010-01", result, data_dir=data_dir)

    segment = _read_segment(data_dir, "book", "book-p0010-01")
    assert segment["tier"] == "sonnet"
    assert segment["attempts"][0]["kind"] == "malformed"


def test_missing_record_path_at_opus_moves_to_human(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0010-01", tier="opus")
    result = json.dumps(
        {
            "seg_id": "book-p0010-01",
            "records": ["records/book/spell/never-written.json"],
            "no_content": None,
            "notes": "",
        }
    )

    outcome = complete_segment("book-p0010-01", result, data_dir=data_dir)

    # missing_record_path's outcome label stays "pending_records" (matching
    # the pre-B8 shape callers already branch on -- see e.g.
    # `test_mix_of_valid_and_invalid_record_paths`); the escalation happens
    # underneath regardless of the label.
    assert outcome.outcome == "pending_records"
    seg_path = data_dir / "segments" / "book" / "book-p0010-01.json"
    assert not seg_path.exists()
    human_path = data_dir / "human" / "book" / "book-p0010-01.json"
    assert json.loads(human_path.read_text())["outcome"] == "escalation_exhausted"


def test_malformed_at_opus_moves_to_human(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0010-01", tier="opus")

    outcome = complete_segment("book-p0010-01", "{not json", data_dir=data_dir)

    assert outcome.outcome == "malformed"
    seg_path = data_dir / "segments" / "book" / "book-p0010-01.json"
    assert not seg_path.exists()
    human_path = data_dir / "human" / "book" / "book-p0010-01.json"
    human_segment = json.loads(human_path.read_text())
    assert human_segment["status"] == "human"
    assert human_segment["outcome"] == "escalation_exhausted"
    assert len(human_segment["attempts"]) == 1


# ---------------------------------------------------------------------------
# needs_context (batch B8, acceptance criterion 3): first reply at a tier
# retries the same tier with merged context; a second escalates.
# ---------------------------------------------------------------------------


def test_needs_context_first_reply_retries_same_tier_and_merges_ids(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0010-01")
    _write_segment(data_dir, "book", "book-p0011-01")
    result = json.dumps(
        {
            "seg_id": "book-p0010-01",
            "records": [],
            "no_content": None,
            "needs_context": ["book-p0011-01"],
            "notes": [],
        }
    )

    outcome = complete_segment("book-p0010-01", result, data_dir=data_dir)

    assert outcome.outcome == "needs_context"
    segment = _read_segment(data_dir, "book", "book-p0010-01")
    assert segment["tier"] == "haiku"
    assert segment["status"] == "pending"
    assert segment["context_seg_ids"] == ["book-p0011-01"]
    assert segment["attempts"][0]["kind"] == "needs_context"
    assert segment["in_progress_since"] is None


def test_second_needs_context_at_same_tier_escalates_and_merges_ids(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0010-01")
    _write_segment(data_dir, "book", "book-p0011-01")
    first = json.dumps(
        {
            "seg_id": "book-p0010-01",
            "records": [],
            "no_content": None,
            "needs_context": ["book-p0011-01"],
            "notes": [],
        }
    )
    complete_segment("book-p0010-01", first, data_dir=data_dir)
    # A second reply naming the SAME adjacent segment id as the first -- a
    # genuinely new attempt (a distinct `queue complete` of a distinct
    # subagent reply), which must still escalate rather than be swallowed as
    # an idempotent no-op (see `owlsperch.queue.ladder`'s module docstring).
    second = json.dumps(
        {
            "seg_id": "book-p0010-01",
            "records": [],
            "no_content": None,
            "needs_context": ["book-p0011-01"],
            "notes": [],
        }
    )

    outcome = complete_segment("book-p0010-01", second, data_dir=data_dir)

    assert outcome.outcome == "needs_context"
    segment = _read_segment(data_dir, "book", "book-p0010-01")
    assert segment["tier"] == "sonnet"
    assert segment["context_seg_ids"] == ["book-p0011-01"]
    assert len(segment["attempts"]) == 2


def test_needs_context_unknown_id_is_malformed(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0010-01")
    result = json.dumps(
        {
            "seg_id": "book-p0010-01",
            "records": [],
            "no_content": None,
            "needs_context": ["book-p9999-01"],
            "notes": [],
        }
    )

    outcome = complete_segment("book-p0010-01", result, data_dir=data_dir)

    assert outcome.outcome == "malformed"
    segment = _read_segment(data_dir, "book", "book-p0010-01")
    assert segment["context_seg_ids"] == []
    assert segment["tier"] == "sonnet"  # a malformed reply still escalates


def test_needs_context_naming_itself_is_malformed(tmp_path: Path) -> None:
    """A segment can't be its own adjacent context -- naming its own
    `seg_id` in `needs_context` is invalid the same way an unknown id is."""
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0010-01")
    result = json.dumps(
        {
            "seg_id": "book-p0010-01",
            "records": [],
            "no_content": None,
            "needs_context": ["book-p0010-01"],
            "notes": [],
        }
    )

    outcome = complete_segment("book-p0010-01", result, data_dir=data_dir)

    assert outcome.outcome == "malformed"
    segment = _read_segment(data_dir, "book", "book-p0010-01")
    assert segment["context_seg_ids"] == []
    assert segment["tier"] == "sonnet"  # a malformed reply still escalates


def test_needs_context_empty_list_is_malformed(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0010-01")
    result = json.dumps(
        {"seg_id": "book-p0010-01", "records": [], "no_content": None, "needs_context": []}
    )

    outcome = complete_segment("book-p0010-01", result, data_dir=data_dir)

    assert outcome.outcome == "malformed"


def test_second_needs_context_at_opus_moves_to_human(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0010-01", tier="opus")
    _write_segment(data_dir, "book", "book-p0011-01", tier="opus")
    first = json.dumps(
        {
            "seg_id": "book-p0010-01",
            "records": [],
            "no_content": None,
            "needs_context": ["book-p0011-01"],
            "notes": [],
        }
    )
    complete_segment("book-p0010-01", first, data_dir=data_dir)
    # A second reply naming the SAME adjacent segment id as the first -- a
    # genuinely new attempt (a distinct `queue complete` of a distinct
    # subagent reply), which must still escalate (and exhaust at opus) rather
    # than be swallowed as an idempotent no-op (see `owlsperch.queue.ladder`'s
    # module docstring).
    second = json.dumps(
        {
            "seg_id": "book-p0010-01",
            "records": [],
            "no_content": None,
            "needs_context": ["book-p0011-01"],
            "notes": [],
        }
    )

    outcome = complete_segment("book-p0010-01", second, data_dir=data_dir)

    assert outcome.outcome == "needs_context"
    seg_path = data_dir / "segments" / "book" / "book-p0010-01.json"
    assert not seg_path.exists()
    human_path = data_dir / "human" / "book" / "book-p0010-01.json"
    assert json.loads(human_path.read_text())["outcome"] == "escalation_exhausted"


# ---------------------------------------------------------------------------
# proposed_type (batch B8, acceptance criterion 4): moves straight to
# human/ with the proposal.
# ---------------------------------------------------------------------------


def test_proposed_type_moves_segment_to_human_with_proposal(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0010-01")
    result = json.dumps(
        {
            "seg_id": "book-p0010-01",
            "records": [],
            "no_content": None,
            "proposed_type": {"name": "trap", "reason": "no schema for mechanical traps yet"},
            "notes": [],
        }
    )

    outcome = complete_segment("book-p0010-01", result, data_dir=data_dir)

    assert outcome.outcome == "proposed_type"
    seg_path = data_dir / "segments" / "book" / "book-p0010-01.json"
    assert not seg_path.exists()
    human_path = data_dir / "human" / "book" / "book-p0010-01.json"
    human_segment = json.loads(human_path.read_text())
    assert human_segment["status"] == "human"
    assert human_segment["outcome"] == "proposed_type"
    assert human_segment["proposal"] == {
        "name": "trap",
        "reason": "no schema for mechanical traps yet",
    }
    # tier is untouched -- proposed_type doesn't go through the ladder.
    assert human_segment["tier"] == "haiku"


def test_proposed_type_without_name_is_malformed(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0010-01")
    result = json.dumps(
        {
            "seg_id": "book-p0010-01",
            "records": [],
            "no_content": None,
            "proposed_type": {"reason": "why"},
            "notes": [],
        }
    )

    outcome = complete_segment("book-p0010-01", result, data_dir=data_dir)

    assert outcome.outcome == "malformed"


# ---------------------------------------------------------------------------
# Record path collisions: `queue complete` must never let one segment's
# claimed record path silently overwrite a file another segment already
# owns. Ownership here comes from the SEGMENT index (a segment's own
# `records`/`pending_records`) -- not from the record file's own
# `extraction.segment_id`, which is only a subagent-copied placeholder by
# the time `queue complete` runs (see `owlsperch.queue.prompt`) and would
# already name the new claimant on a just-clobbered file.
# ---------------------------------------------------------------------------


def test_claim_on_a_path_owned_by_another_segments_records_is_a_collision(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    _write_segment(
        data_dir,
        "book",
        "book-p0026-01",
        tier="opus",
        records=["records/book/rules_section/class-features.json"],
    )
    _write_segment(data_dir, "book", "book-p0148-01")
    record_path = data_dir / "records" / "book" / "rules_section" / "class-features.json"
    record_path.parent.mkdir(parents=True, exist_ok=True)
    # A real (already-validated) record body -- the byte-identity assertion
    # below is exactly what fails on main, since `_overwrite_authoritative_
    # fields` rewrites `extraction`/`pages`/`book_id` in place.
    original_body = json.dumps(
        {
            "extraction": {
                "tier": "haiku",
                "model": "claude-haiku-4-5",
                "segment_id": "book-p0026-01",
                "timestamp": "2026-01-01T00:00:00+00:00",
            },
            "pages": [26],
            "book_id": "book",
        },
        indent=2,
    )
    record_path.write_text(original_body)

    result = json.dumps(
        {
            "seg_id": "book-p0148-01",
            "records": ["records/book/rules_section/class-features.json"],
            "no_content": None,
        }
    )

    outcome = complete_segment("book-p0148-01", result, data_dir=data_dir)

    assert outcome.outcome == "pending_records"
    assert "collision" in outcome.detail
    # The file is left completely untouched, byte for byte.
    assert record_path.read_text() == original_body

    victim = _read_segment(data_dir, "book", "book-p0148-01")
    assert victim["pending_records"] == []
    assert victim["tier"] == "sonnet"
    assert len(victim["attempts"]) == 1
    assert victim["attempts"][0]["kind"] == "malformed"
    assert victim["attempts"][0]["errors"] == [
        "record_path_collision: records/book/rules_section/class-features.json "
        "is owned by segment book-p0026-01"
    ]

    # The original owner's own segment is unaffected.
    owner = _read_segment(data_dir, "book", "book-p0026-01")
    assert owner["records"] == ["records/book/rules_section/class-features.json"]


def test_claim_on_a_path_owned_by_another_segments_pending_records_is_a_collision(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    _write_segment(
        data_dir,
        "book",
        "book-p0010-01",
        pending_records=["records/book/spell/fireball.json"],
    )
    _write_segment(data_dir, "book", "book-p0011-01")
    _write_record_file(data_dir, "records/book/spell/fireball.json")

    result = json.dumps(
        {
            "seg_id": "book-p0011-01",
            "records": ["records/book/spell/fireball.json"],
            "no_content": None,
        }
    )

    outcome = complete_segment("book-p0011-01", result, data_dir=data_dir)

    assert outcome.outcome == "pending_records"
    thief = _read_segment(data_dir, "book", "book-p0011-01")
    assert thief["pending_records"] == []
    assert thief["attempts"][0]["errors"] == [
        "record_path_collision: records/book/spell/fireball.json is owned by segment book-p0010-01"
    ]


def test_segment_reclaiming_its_own_already_owned_path_is_not_a_collision(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    _write_segment(
        data_dir,
        "book",
        "book-p0010-01",
        pending_records=["records/book/spell/fireball.json"],
    )
    _write_record_file(data_dir, "records/book/spell/fireball.json")

    result = json.dumps(
        {
            "seg_id": "book-p0010-01",
            "records": ["records/book/spell/fireball.json"],
            "no_content": None,
        }
    )

    outcome = complete_segment("book-p0010-01", result, data_dir=data_dir)

    assert outcome.outcome == "pending_records"
    assert outcome.detail == "1 record(s) claimed"
    segment = _read_segment(data_dir, "book", "book-p0010-01")
    assert segment["pending_records"] == ["records/book/spell/fireball.json"]
    assert segment["attempts"] == []


def test_mix_of_valid_invalid_and_colliding_paths_is_one_attempt(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(
        data_dir,
        "book",
        "book-p0010-01",
        records=["records/book/spell/icy-bolt.json"],
    )
    _write_segment(data_dir, "book", "book-p0011-01")
    _write_record_file(data_dir, "records/book/spell/fireball.json")
    _write_record_file(data_dir, "records/book/spell/icy-bolt.json")

    result = json.dumps(
        {
            "seg_id": "book-p0011-01",
            "records": [
                "records/book/spell/fireball.json",
                "records/book/spell/icy-bolt.json",
                "records/book/spell/never-written.json",
            ],
            "no_content": None,
        }
    )

    outcome = complete_segment("book-p0011-01", result, data_dir=data_dir)

    assert outcome.outcome == "pending_records"
    segment = _read_segment(data_dir, "book", "book-p0011-01")
    assert segment["pending_records"] == ["records/book/spell/fireball.json"]
    # One reply -> one attempt, even though it carries two distinct kinds of
    # bad path.
    assert len(segment["attempts"]) == 1
    assert segment["attempts"][0]["errors"] == [
        "missing_record_path: records/book/spell/never-written.json",
        "record_path_collision: records/book/spell/icy-bolt.json is owned by segment book-p0010-01",
    ]
    assert "1 record(s) claimed" in outcome.detail
    assert "1 invalid path(s)" in outcome.detail
    assert "1 collision(s)" in outcome.detail


def test_ownership_index_scan_skips_a_file_that_is_not_valid_json(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0010-01")
    _write_record_file(data_dir, "records/book/spell/fireball.json")
    bogus = data_dir / "segments" / "book" / "book-p9999-01.json"
    bogus.write_text("{not json")

    result = json.dumps(
        {
            "seg_id": "book-p0010-01",
            "records": ["records/book/spell/fireball.json"],
            "no_content": None,
        }
    )

    outcome = complete_segment("book-p0010-01", result, data_dir=data_dir)

    assert outcome.outcome == "pending_records"
    segment = _read_segment(data_dir, "book", "book-p0010-01")
    assert segment["pending_records"] == ["records/book/spell/fireball.json"]


def test_collision_at_opus_moves_the_victim_to_human(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(
        data_dir,
        "book",
        "book-p0026-01",
        records=["records/book/rules_section/class-features.json"],
    )
    _write_segment(data_dir, "book", "book-p0148-01", tier="opus")
    _write_record_file(data_dir, "records/book/rules_section/class-features.json")

    result = json.dumps(
        {
            "seg_id": "book-p0148-01",
            "records": ["records/book/rules_section/class-features.json"],
            "no_content": None,
        }
    )

    outcome = complete_segment("book-p0148-01", result, data_dir=data_dir)

    assert outcome.outcome == "pending_records"
    assert "collision" in outcome.detail
    seg_path = data_dir / "segments" / "book" / "book-p0148-01.json"
    assert not seg_path.exists()
    human_path = data_dir / "human" / "book" / "book-p0148-01.json"
    human_segment = json.loads(human_path.read_text())
    assert human_segment["status"] == "human"
    assert human_segment["outcome"] == "escalation_exhausted"
    assert len(human_segment["attempts"]) == 1
    assert human_segment["attempts"][0]["kind"] == "malformed"
    assert human_segment["attempts"][0]["errors"] == [
        "record_path_collision: records/book/rules_section/class-features.json "
        "is owned by segment book-p0026-01"
    ]

    # The original owner's own segment is unaffected.
    owner = _read_segment(data_dir, "book", "book-p0026-01")
    assert owner["records"] == ["records/book/rules_section/class-features.json"]


def test_proposed_type_takes_precedence_over_needs_context(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_segment(data_dir, "book", "book-p0010-01")
    _write_segment(data_dir, "book", "book-p0011-01")
    result = json.dumps(
        {
            "seg_id": "book-p0010-01",
            "records": [],
            "no_content": None,
            "needs_context": ["book-p0011-01"],
            "proposed_type": {"name": "trap", "reason": "why"},
            "notes": [],
        }
    )

    outcome = complete_segment("book-p0010-01", result, data_dir=data_dir)

    assert outcome.outcome == "proposed_type"
