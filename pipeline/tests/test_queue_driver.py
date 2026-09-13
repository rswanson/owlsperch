"""Tests for `owlsperch.queue.driver` (`owlsperch queue run --dry-run
--fixtures <dir>`), per B8 acceptance criterion 7: the escalation state
machine exercised end to end against a `FixtureSubagent`, asserting on-disk
segment state after each scenario -- pass, fail-escalate x2 then human,
needs-context merge then pass, a second needs-context escalating, and a
stale `in_progress` segment being reset and then processed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from owlsperch.queue.driver import FixtureExhaustedError, drive_dry_run, run_queue_run
from owlsperch.segment.runner import Segment


def _repo_schemas_dir() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "schemas"


def _write_manifest(tmp_path: Path, book_id: str = "book") -> Path:
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text(
        f"""
entries:
  - book_id: {book_id}
    title: "Test Book"
    file: "{book_id}.pdf"
    edition: "3.5"
    kind: rulebook
"""
    )
    return manifest_path


def _write_segment(data_dir: Path, book_id: str, seg_id: str, **overrides: object) -> None:
    defaults: dict[str, Any] = dict(
        seg_id=seg_id,
        book_id=book_id,
        pages=[10],
        printed_pages=[10],
        kind_hint="spell",
        heading="Fireball",
        text="Fireball\n\nEvocation Level: Sor/Wiz 3.",
        status="pending",
        tier="haiku",
        created_at="2026-01-01T00:00:00+00:00",
    )
    defaults.update(overrides)
    segment = Segment(**defaults)
    seg_dir = data_dir / "segments" / book_id
    seg_dir.mkdir(parents=True, exist_ok=True)
    (seg_dir / f"{seg_id}.json").write_text(segment.model_dump_json(indent=2))


def _read_segment(data_dir: Path, book_id: str, seg_id: str) -> dict[str, Any]:
    path = data_dir / "segments" / book_id / f"{seg_id}.json"
    return json.loads(path.read_text())  # type: ignore[no-any-return]


def _read_human_segment(data_dir: Path, book_id: str, seg_id: str) -> dict[str, Any]:
    path = data_dir / "human" / book_id / f"{seg_id}.json"
    return json.loads(path.read_text())  # type: ignore[no-any-return]


def _spell_record(*, book_id: str, seg_id: str, slug: str, valid: bool = True) -> dict[str, Any]:
    return {
        "id": f"spell:{book_id}:{slug}",
        "type": "spell",
        "name": slug.replace("-", " ").title(),
        "slug": slug,
        "aliases": [],
        "book_id": book_id,
        "pages": [10],
        "citation": "Test Book p. 10",
        "text_md": "Deals fire damage in a burst.",
        "fields": {
            "school": "Evocation",
            "subschool": None,
            "descriptors": ["Fire"],
            "levels": [{"class": "Sorcerer", "level": 3}] if valid else [],
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
            "tier": "haiku",
            "model": "dry-run",
            "segment_id": seg_id,
            "timestamp": "2026-01-01T00:00:00+00:00",
        },
    }


def _write_fixture(fixtures_dir: Path, seg_id: str, n: int, payload: dict[str, Any]) -> None:
    seg_fixture_dir = fixtures_dir / seg_id
    seg_fixture_dir.mkdir(parents=True, exist_ok=True)
    (seg_fixture_dir / f"{n}.json").write_text(json.dumps(payload, indent=2))


# ---------------------------------------------------------------------------
# Scenario: pass
# ---------------------------------------------------------------------------


def test_dry_run_pass(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    fixtures_dir = tmp_path / "fixtures"
    _write_segment(data_dir, "book", "book-p0010-01")

    _write_fixture(
        fixtures_dir,
        "book-p0010-01",
        1,
        {
            "files": {
                "records/book/spell/fireball.json": _spell_record(
                    book_id="book", seg_id="book-p0010-01", slug="fireball"
                )
            },
            "reply": {
                "seg_id": "book-p0010-01",
                "records": ["records/book/spell/fireball.json"],
                "no_content": None,
                "notes": [],
            },
        },
    )

    result = drive_dry_run(
        "book",
        fixtures_dir=fixtures_dir,
        data_dir=data_dir,
        schemas_dir=_repo_schemas_dir(),
        manifest_path=manifest_path,
    )

    assert result.total_calls == 1
    assert result.served == {"book-p0010-01": ["1.json"]}
    segment = _read_segment(data_dir, "book", "book-p0010-01")
    assert segment["status"] == "done"
    assert segment["outcome"] == "validated"
    assert segment["tier"] == "haiku"
    assert result.summary.counts_by_status.get("done") == 1


# ---------------------------------------------------------------------------
# Scenario: fail-escalate x2, then human
# ---------------------------------------------------------------------------


def test_dry_run_fail_escalate_twice_then_human(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    fixtures_dir = tmp_path / "fixtures"
    _write_segment(data_dir, "book", "book-p0020-01")

    for n in (1, 2, 3):
        _write_fixture(
            fixtures_dir,
            "book-p0020-01",
            n,
            {
                "files": {
                    "records/book/spell/badspell.json": _spell_record(
                        book_id="book", seg_id="book-p0020-01", slug="badspell", valid=False
                    )
                },
                "reply": {
                    "seg_id": "book-p0020-01",
                    "records": ["records/book/spell/badspell.json"],
                    "no_content": None,
                    "notes": [],
                },
            },
        )

    result = drive_dry_run(
        "book",
        fixtures_dir=fixtures_dir,
        data_dir=data_dir,
        schemas_dir=_repo_schemas_dir(),
        manifest_path=manifest_path,
    )

    assert result.total_calls == 3
    assert result.served == {"book-p0020-01": ["1.json", "2.json", "3.json"]}
    seg_path = data_dir / "segments" / "book" / "book-p0020-01.json"
    assert not seg_path.exists()
    human_segment = _read_human_segment(data_dir, "book", "book-p0020-01")
    assert human_segment["status"] == "human"
    assert human_segment["outcome"] == "escalation_exhausted"
    assert human_segment["tier"] == "opus"
    assert [a["tier"] for a in human_segment["attempts"]] == ["haiku", "sonnet", "opus"]
    assert result.summary.human == 1


# ---------------------------------------------------------------------------
# Scenario: needs_context merge then pass
# ---------------------------------------------------------------------------


def test_dry_run_needs_context_merge_then_pass(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    fixtures_dir = tmp_path / "fixtures"
    _write_segment(data_dir, "book", "book-p0030-01")
    # Adjacent segment: already "done" so it's never itself selected by the
    # driver, but still resolvable as a `needs_context` target.
    _write_segment(data_dir, "book", "book-p0031-01", status="done", outcome="no_content")

    _write_fixture(
        fixtures_dir,
        "book-p0030-01",
        1,
        {
            "files": {},
            "reply": {
                "seg_id": "book-p0030-01",
                "records": [],
                "no_content": None,
                "needs_context": ["book-p0031-01"],
                "notes": [],
            },
        },
    )
    _write_fixture(
        fixtures_dir,
        "book-p0030-01",
        2,
        {
            "files": {
                "records/book/spell/mage-armor.json": _spell_record(
                    book_id="book", seg_id="book-p0030-01", slug="mage-armor"
                )
            },
            "reply": {
                "seg_id": "book-p0030-01",
                "records": ["records/book/spell/mage-armor.json"],
                "no_content": None,
                "notes": [],
            },
        },
    )

    result = drive_dry_run(
        "book",
        fixtures_dir=fixtures_dir,
        data_dir=data_dir,
        schemas_dir=_repo_schemas_dir(),
        manifest_path=manifest_path,
    )

    assert result.total_calls == 2
    assert result.served == {"book-p0030-01": ["1.json", "2.json"]}
    segment = _read_segment(data_dir, "book", "book-p0030-01")
    assert segment["status"] == "done"
    assert segment["outcome"] == "validated"
    assert segment["tier"] == "haiku"  # same-tier retry -- never escalated
    assert segment["context_seg_ids"] == ["book-p0031-01"]
    assert len(segment["attempts"]) == 1
    assert segment["attempts"][0]["kind"] == "needs_context"


# ---------------------------------------------------------------------------
# Scenario: a second needs_context escalates
# ---------------------------------------------------------------------------


def test_dry_run_second_needs_context_escalates(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    fixtures_dir = tmp_path / "fixtures"
    _write_segment(data_dir, "book", "book-p0040-01")
    _write_segment(data_dir, "book", "book-p0041-01", status="done", outcome="no_content")
    _write_segment(data_dir, "book", "book-p0042-01", status="done", outcome="no_content")

    _write_fixture(
        fixtures_dir,
        "book-p0040-01",
        1,
        {
            "files": {},
            "reply": {
                "seg_id": "book-p0040-01",
                "records": [],
                "no_content": None,
                "needs_context": ["book-p0041-01"],
                "notes": [],
            },
        },
    )
    _write_fixture(
        fixtures_dir,
        "book-p0040-01",
        2,
        {
            "files": {},
            "reply": {
                "seg_id": "book-p0040-01",
                "records": [],
                "no_content": None,
                "needs_context": ["book-p0042-01"],
                "notes": [],
            },
        },
    )
    _write_fixture(
        fixtures_dir,
        "book-p0040-01",
        3,
        {
            "files": {
                "records/book/spell/shield.json": _spell_record(
                    book_id="book", seg_id="book-p0040-01", slug="shield"
                )
            },
            "reply": {
                "seg_id": "book-p0040-01",
                "records": ["records/book/spell/shield.json"],
                "no_content": None,
                "notes": [],
            },
        },
    )

    result = drive_dry_run(
        "book",
        fixtures_dir=fixtures_dir,
        data_dir=data_dir,
        schemas_dir=_repo_schemas_dir(),
        manifest_path=manifest_path,
    )

    assert result.total_calls == 3
    segment = _read_segment(data_dir, "book", "book-p0040-01")
    assert segment["status"] == "done"
    assert segment["outcome"] == "validated"
    assert segment["tier"] == "sonnet"  # escalated once, by the 2nd needs_context
    assert segment["context_seg_ids"] == ["book-p0041-01", "book-p0042-01"]
    needs_context_attempts = [a for a in segment["attempts"] if a["kind"] == "needs_context"]
    assert len(needs_context_attempts) == 2
    assert result.summary.needs_context_retries == 2


# ---------------------------------------------------------------------------
# Scenario: a stale in_progress segment is reset and then processed
# ---------------------------------------------------------------------------


def test_dry_run_stale_in_progress_segment_is_reset_then_processed(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    fixtures_dir = tmp_path / "fixtures"
    _write_segment(
        data_dir,
        "book",
        "book-p0050-01",
        status="in_progress",
        in_progress_since="2020-01-01T00:00:00+00:00",  # ancient -- definitely stale
    )

    _write_fixture(
        fixtures_dir,
        "book-p0050-01",
        1,
        {
            "files": {
                "records/book/spell/light.json": _spell_record(
                    book_id="book", seg_id="book-p0050-01", slug="light"
                )
            },
            "reply": {
                "seg_id": "book-p0050-01",
                "records": ["records/book/spell/light.json"],
                "no_content": None,
                "notes": [],
            },
        },
    )

    result = drive_dry_run(
        "book",
        fixtures_dir=fixtures_dir,
        data_dir=data_dir,
        schemas_dir=_repo_schemas_dir(),
        manifest_path=manifest_path,
    )

    assert result.total_calls == 1
    assert result.served == {"book-p0050-01": ["1.json"]}
    segment = _read_segment(data_dir, "book", "book-p0050-01")
    assert segment["status"] == "done"
    assert segment["outcome"] == "validated"


# ---------------------------------------------------------------------------
# FixtureSubagent exhaustion and the non-dry-run CLI error
# ---------------------------------------------------------------------------


def test_fixture_exhausted_raises_when_no_more_fixtures_for_a_reselected_segment(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    fixtures_dir = tmp_path / "fixtures"
    _write_segment(data_dir, "book", "book-p0060-01")
    _write_fixture(
        fixtures_dir,
        "book-p0060-01",
        1,
        {
            "files": {
                "records/book/spell/badspell.json": _spell_record(
                    book_id="book", seg_id="book-p0060-01", slug="badspell", valid=False
                )
            },
            "reply": {
                "seg_id": "book-p0060-01",
                "records": ["records/book/spell/badspell.json"],
                "no_content": None,
                "notes": [],
            },
        },
    )
    # No fixture "2.json" -- the segment will be reselected at sonnet after
    # the first failure, and there's nothing scripted for that call.

    try:
        drive_dry_run(
            "book",
            fixtures_dir=fixtures_dir,
            data_dir=data_dir,
            schemas_dir=_repo_schemas_dir(),
            manifest_path=manifest_path,
        )
        raise AssertionError("expected FixtureExhaustedError")
    except FixtureExhaustedError:
        pass


def test_run_queue_run_without_dry_run_flag_errors(tmp_path: Path) -> None:
    import io

    exit_code = run_queue_run("book", dry_run=False, out=io.StringIO())

    assert exit_code == 1


def test_run_queue_run_without_fixtures_errors(tmp_path: Path) -> None:
    import io

    exit_code = run_queue_run("book", dry_run=True, fixtures_dir=None, out=io.StringIO())

    assert exit_code == 1


def test_run_queue_run_dry_run_prints_summary(tmp_path: Path) -> None:
    import io

    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    fixtures_dir = tmp_path / "fixtures"
    _write_segment(data_dir, "book", "book-p0070-01")
    _write_fixture(
        fixtures_dir,
        "book-p0070-01",
        1,
        {
            "files": {
                "records/book/spell/flare.json": _spell_record(
                    book_id="book", seg_id="book-p0070-01", slug="flare"
                )
            },
            "reply": {
                "seg_id": "book-p0070-01",
                "records": ["records/book/spell/flare.json"],
                "no_content": None,
                "notes": [],
            },
        },
    )

    out = io.StringIO()
    exit_code = run_queue_run(
        "book",
        dry_run=True,
        fixtures_dir=fixtures_dir,
        data_dir=data_dir,
        schemas_dir=_repo_schemas_dir(),
        manifest_path=manifest_path,
        out=out,
    )

    assert exit_code == 0
    assert "book:" in out.getvalue()
    assert "haiku" in out.getvalue()
