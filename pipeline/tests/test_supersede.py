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

Plus (batch B10c-mand11) `is_class_owned_fragment`, the shared, pure
predicate that decides the SCOPE of superseding for all three of its callers
(the segmenter's stamp pass, `build-db`'s record-level pass, and `queue
audit`'s retroactive restore) -- see the "is_class_owned_fragment" section
at the bottom of this file.
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


# ---------------------------------------------------------------------------
# is_class_owned_fragment (batch B10c-mand11): which fragments inside a
# class's page span the class actually OWNS. Every heading below is a real
# one taken from the PHB class chapter's own segments (the judgement's
# per-class listing), split into the set that must stay superseded and the
# printed SIDEBARS that must not be -- page span alone swallowed both, and
# the sidebars ended up in no canonical record at all.
# ---------------------------------------------------------------------------


#: (class title, heading) pairs the class does own -- all `rules_section`.
_OWNED_RULES_SECTIONS = [
    ("Barbarian", "BARBARIAN"),
    ("Barbarian", "GAME RULE INFORMATION"),
    ("Barbarian", "Class Skills"),
    ("Barbarian", "Class Features"),
    ("Barbarian", "Ex-Barbarians"),
    ("Barbarian", "Half-Orc Barbarian Starting Package"),
    # PHB 3.5 prints "WIZARDS" for the toc's "Wizard" -- plural-tolerant on
    # either side, same as the segmenter's own `_heading_matches_title`.
    ("Wizard", "WIZARDS"),
    ("Wizard", "Class Skills"),
    ("Wizard", "Class Features"),
    ("Wizard", "Elf Wizard Starting Package"),
    ("Paladin", "PALADIN"),
    ("Paladin", "GAME RULE INFORMATION"),
    ("Paladin", "Ex-Paladins"),
    ("Paladin", "Human Paladin Starting Package"),
    ("Druid", "DRUID"),
    ("Druid", "Half-Elf Druid Starting Package"),
    ("Cleric", "Human Cleric Starting Package"),
    ("Monk", "Human Monk Starting Package"),
    ("Monk", "Ex-Monks"),
    # A record NAME carries the prompt's own qualification (B10-mand1) --
    # the parenthetical is stripped before the match.
    ("Barbarian", "Class Features (Barbarian)"),
    ("Wizard", "Class Skills (Wizard)"),
]

#: (class title, heading) pairs that are NOT the class's -- every printed
#: sidebar the judgement found wrongly swallowed, plus the neighbouring
#: class's own fragments that a one-page span extension reaches into.
_NOT_OWNED_RULES_SECTIONS = [
    ("Sorcerer", "FAMILIARS"),
    ("Wizard", "ARCANE SPELLS AND ARMOR"),
    ("Wizard", "SCHOOL SPECIALIZATION"),
    ("Wizard", "LEVEL ADVANCEMENT"),
    # Names the class and still isn't class-structural -- the match is on
    # the WHOLE heading, never a substring of it.
    ("Paladin", "THE PALADIN’S MOUNT"),
    ("Paladin", "SAMPLE PALADIN’S MOUNTS"),
    ("Druid", "THE DRUID’S ANIMAL COMPANION"),
    ("Druid", "ALTERNATIVE ANIMAL COMPANIONS"),
    # A neighbouring class's own fragments, reached by the one-page span
    # extension: left for that class's own span to stamp.
    ("Fighter", "MONK"),
    ("Monk", "Human Fighter Starting Package"),
    ("Ranger", "ROGUE"),
    ("Sorcerer", "WIZARDS"),
    ("Wizard", "Ex-Sorcerers"),
]


def test_is_class_owned_fragment_accepts_every_class_structural_heading() -> None:
    from owlsperch.supersede import is_class_owned_fragment

    for title, heading in _OWNED_RULES_SECTIONS:
        assert is_class_owned_fragment(heading, "rules_section", title), (title, heading)


def test_is_class_owned_fragment_rejects_every_sidebar_and_neighbour() -> None:
    from owlsperch.supersede import is_class_owned_fragment

    for title, heading in _NOT_OWNED_RULES_SECTIONS:
        assert not is_class_owned_fragment(heading, "rules_section", title), (title, heading)


def test_is_class_owned_fragment_accepts_any_table_in_the_span() -> None:
    """A class's own "tables belonging to this entity" convention has the
    class record claim the same record paths its table fragments do, so
    every `table` fragment inside the span passes -- including one titled
    after a NEIGHBOURING class, which the one-page span extension reaches
    (PHB "Table 3–13: The Ranger" sits inside paladin's own span)."""
    from owlsperch.supersede import is_class_owned_fragment

    for title, heading in (
        ("Barbarian", "Table 3–3: The Barbarian"),
        ("Bard", "Table 3–5: Bard Spells Known"),
        ("Cleric", "Table 3–7: Deities"),
        ("Monk", "Table 3–11: Small or Large Monk Unarmed Damage"),
        ("Ranger", "Table 3–14: Ranger Favored Enemies"),
        ("Paladin", "Table 3–13: The Ranger"),
    ):
        assert is_class_owned_fragment(heading, "table", title), (title, heading)


def test_is_class_owned_fragment_rejects_every_other_kind() -> None:
    """A spell/feat/stat_block fragment stranded inside a class's span is
    never class-owned, whatever its heading."""
    from owlsperch.supersede import is_class_owned_fragment

    for kind in ("spell", "feat", "stat_block", "class", "prestige_class", ""):
        assert not is_class_owned_fragment("Class Features", kind, "Barbarian"), kind


def test_is_class_owned_fragment_needs_both_a_heading_and_a_title() -> None:
    from owlsperch.supersede import is_class_owned_fragment

    assert not is_class_owned_fragment("", "rules_section", "Barbarian")
    assert not is_class_owned_fragment("BARBARIAN", "rules_section", "")
    # A bare "Starting Package" with no class name in front of it isn't
    # attributable to this class either.
    assert not is_class_owned_fragment("Starting Package", "rules_section", "Barbarian")


# ---------------------------------------------------------------------------
# Batch B12: is_monster_owned_fragment
# ---------------------------------------------------------------------------


def test_monster_owns_its_own_heading_and_structural_sections() -> None:
    from owlsperch.supersede import is_monster_owned_fragment

    for heading in (
        "ALLIP",
        "ALLIPS",
        "COMBAT",
        "ALLIP SOCIETY",
        "ALLIP CHARACTERS",
        "ALLIPS AS CHARACTERS",
        "ALLIP LORE",
    ):
        assert is_monster_owned_fragment(heading, "rules_section", "Allip"), heading


def test_monster_owns_a_grouped_entrys_sub_block_headings() -> None:
    from owlsperch.supersede import is_monster_owned_fragment

    assert is_monster_owned_fragment("ANGEL, SOLAR", "rules_section", "ANGEL")
    assert is_monster_owned_fragment("LANTERN ARCHON", "rules_section", "ARCHON")


def test_monster_leaves_an_unrelated_sidebar_live() -> None:
    """The class-pass lesson: page span alone swallows a printed sidebar
    into no canonical record at all."""
    from owlsperch.supersede import is_monster_owned_fragment

    assert not is_monster_owned_fragment("FAMILIARS", "rules_section", "Allip")
    assert not is_monster_owned_fragment("DRAGONHIDE", "rules_section", "Black dragon")
    # Never a mid-word match: "BATTLE" is not the "BAT" entry's sub-block.
    assert not is_monster_owned_fragment("BATTLE", "rules_section", "BAT")
    # And never another kind entirely.
    assert not is_monster_owned_fragment("ALLIP", "spell", "Allip")


def test_monster_owns_only_a_table_that_names_it() -> None:
    from owlsperch.supersede import is_monster_owned_fragment

    assert is_monster_owned_fragment("Animated Object, Tiny", "table", "Animated object")
    assert not is_monster_owned_fragment("Table 1-1: Random Encounters", "table", "Allip")
