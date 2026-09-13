"""`owlsperch fixture-db <dir>` (batch B7, spec 4.10): builds a small,
synthetic, schema-valid SQLite database into a fresh `$OWLSPERCH_DATA`-shaped
directory, without touching the real PDF corpus or `~/owlsperch-data`.

This exists so the web frontend's Playwright smoke test (`web/e2e/`) and
anyone poking at the UI locally can stand up a real API server backed by
real data in one command, the same way `server/tests/conftest.py`'s
`built_data_dir` fixture does for the Python test suite -- but as a plain,
pytest-free function importable from a script (`web/e2e/serve-fixture.py`)
or runnable from the CLI.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import yaml

from owlsperch.build_db.runner import BuildResult, build_db

#: Kept in sync with server/tests/conftest.py's synthetic manifest/records
#: (batch B6) so the fixture DB exercises the same search/detail behaviors
#: (prefix match, alias match, grouping) -- but this module has no import
#: dependency on that test module, since pipeline/ must stand on its own.
_MANIFEST: dict[str, Any] = {
    "entries": [
        {
            "book_id": "fixture-book",
            "title": "Fixture Book",
            "short_title": "FB",
            "file": "fixture-book.pdf",
            "edition": "3.5",
            "kind": "rulebook",
            "published": "2001-01",
        }
    ]
}

_SEGMENT: dict[str, Any] = {
    "seg_id": "fixture-book-p0001-01",
    "book_id": "fixture-book",
    "pages": [1],
    "printed_pages": [1],
    "kind_hint": "spell",
    "heading": "Fireball",
    "text": "Fireball\n\nEvocation [Fire] Level: Sor/Wiz 3.",
    "status": "pending",
    "tier": "haiku",
    "attempts": [],
    "created_at": "2026-01-01T00:00:00+00:00",
}

#: A second segment (B10) so the feat fixture record below has its own
#: originating segment to cite pages within, distinct from the spell
#: segment above.
_SEGMENT_2: dict[str, Any] = {
    "seg_id": "fixture-book-p0002-01",
    "book_id": "fixture-book",
    "pages": [2],
    "printed_pages": [2],
    "kind_hint": "feat",
    "heading": "Power Strike [General]",
    "text": "Power Strike [General]\n\nYou hit harder at the cost of accuracy.",
    "status": "pending",
    "tier": "haiku",
    "attempts": [],
    "created_at": "2026-01-01T00:00:00+00:00",
}

#: A third segment (B10) for the rules_section + the table it owns.
_SEGMENT_3: dict[str, Any] = {
    "seg_id": "fixture-book-p0003-01",
    "book_id": "fixture-book",
    "pages": [3],
    "printed_pages": [3],
    "kind_hint": "rules_section",
    "heading": "Grapple Ranks",
    "text": (
        "Grapple Ranks\n\nA combatant's grapple rank reflects experience "
        "wrestling foes to the ground.\n\nRank\tBonus\n1\t+0\n2\t+2\n3\t+4"
    ),
    "status": "pending",
    "tier": "haiku",
    "attempts": [],
    "created_at": "2026-01-01T00:00:00+00:00",
}

_SPELLS: list[dict[str, Any]] = [
    {
        "id": "spell:fixture-book:fireball",
        "type": "spell",
        "name": "Fireball",
        "slug": "fireball",
        "aliases": [],
        "book_id": "fixture-book",
        "pages": [1],
        "citation": "FB p. 1",
        "text_md": (
            "A **fireball** spell is an explosion of flame that detonates "
            "with a low roar and deals 1d6 points of fire damage per "
            "caster level (maximum 10d6) to every creature within the "
            "area."
        ),
        "fields": {
            "school": "Evocation",
            "subschool": None,
            "descriptors": ["Fire"],
            "levels": [{"class": "Sorcerer", "level": 3}, {"class": "Wizard", "level": 3}],
            "components": ["V", "S", "M"],
            "casting_time": "1 standard action",
            "range": "Long (400 ft. + 40 ft./level)",
            "target_effect_area": "20-ft.-radius spread",
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
            "model": "fixture",
            "segment_id": "fixture-book-p0001-01",
            "timestamp": "2026-01-01T00:00:00+00:00",
        },
    },
    {
        # Batch B9 (browse/facets): a Cleric-3 Conjuration spell, distinct
        # from Fireball (Evocation) and Alarm (Abjuration, no Cleric level)
        # -- the fixture data flow B's Playwright spec filters on (class
        # Cleric, level 3, school Conjuration) needs a single spell that
        # matches all three and others that don't.
        "id": "spell:fixture-book:summon-monster-iii",
        "type": "spell",
        "name": "Summon Monster III",
        "slug": "summon-monster-iii",
        "aliases": [],
        "book_id": "fixture-book",
        "pages": [1],
        "citation": "FB p. 1",
        "text_md": (
            "A **summon monster III** spell calls a creature from the "
            "list of 3rd-level summoned creatures to fight for you."
        ),
        "fields": {
            "school": "Conjuration",
            "subschool": "Summoning",
            "descriptors": [],
            "levels": [
                {"class": "Cleric", "level": 3},
                {"class": "Sorcerer", "level": 3},
                {"class": "Wizard", "level": 3},
            ],
            "components": ["V", "S"],
            "casting_time": "1 round",
            "range": "Close (25 ft. + 5 ft./2 levels)",
            "target_effect_area": "One summoned creature",
            "duration": "1 round/level (D)",
            "saving_throw": "None",
            "spell_resistance": "No",
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
            "model": "fixture",
            "segment_id": "fixture-book-p0001-01",
            "timestamp": "2026-01-01T00:00:00+00:00",
        },
    },
    {
        "id": "spell:fixture-book:alarm",
        "type": "spell",
        "name": "Alarm",
        "slug": "alarm",
        "aliases": [],
        "book_id": "fixture-book",
        "pages": [1],
        "citation": "FB p. 1",
        "text_md": (
            "An **alarm** spell alerts you whenever a Small or larger "
            "creature enters the warded area."
        ),
        "fields": {
            "school": "Abjuration",
            "subschool": None,
            "descriptors": [],
            "levels": [{"class": "Sorcerer", "level": 1}, {"class": "Wizard", "level": 1}],
            "components": ["V", "S", "F"],
            "casting_time": "1 standard action",
            "range": "Close (25 ft. + 5 ft./2 levels)",
            "target_effect_area": "20-ft.-radius emanation centered on a point in space",
            "duration": "2 hours/level (D)",
            "saving_throw": "None",
            "spell_resistance": "No",
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
            "model": "fixture",
            "segment_id": "fixture-book-p0001-01",
            "timestamp": "2026-01-01T00:00:00+00:00",
        },
    },
]

#: (B10) An invented feat -- never copied from a real book (see this
#: module's docstring on why: no real book text may be committed to this
#: public repo).
_FEAT: dict[str, Any] = {
    "id": "feat:fixture-book:power-strike",
    "type": "feat",
    "name": "Power Strike",
    "slug": "power-strike",
    "aliases": [],
    "book_id": "fixture-book",
    "pages": [2],
    "citation": "FB p. 2",
    "text_md": "You hit harder at the cost of accuracy.",
    "fields": {
        "feat_type": "General",
        "prerequisites": ["Str 13", "Base attack bonus +1"],
        "benefit": (
            "You may take a -1 penalty on melee attack rolls to gain a +2 "
            "bonus on melee damage rolls."
        ),
    },
    "tables": [],
    "canonical": False,
    "variant_of": None,
    "applied_overrides": [],
    "macro_eligible": False,
    "schema_version": 1,
    "extraction": {
        "tier": "haiku",
        "model": "fixture",
        "segment_id": "fixture-book-p0002-01",
        "timestamp": "2026-01-01T00:00:00+00:00",
    },
}

#: (B10) An invented rules_section that owns a table via `tables` -- the
#: Playwright browse/detail spec (`web/e2e/smoke.spec.ts`) opens this record
#: and asserts a rendered `<table>` with its header cells shows up below the
#: record text.
_RULES_SECTION: dict[str, Any] = {
    "id": "rules_section:fixture-book:grapple-ranks",
    "type": "rules_section",
    "name": "Grapple Ranks",
    "slug": "grapple-ranks",
    "aliases": [],
    "book_id": "fixture-book",
    "pages": [3],
    "citation": "FB p. 3",
    "text_md": ("A combatant's grapple rank reflects experience wrestling foes to the ground."),
    "fields": {"topic": "Grapple Ranks"},
    "tables": ["table:fixture-book:table-1-grapple-ranks"],
    "canonical": False,
    "variant_of": None,
    "applied_overrides": [],
    "macro_eligible": False,
    "schema_version": 1,
    "extraction": {
        "tier": "haiku",
        "model": "fixture",
        "segment_id": "fixture-book-p0003-01",
        "timestamp": "2026-01-01T00:00:00+00:00",
    },
}

#: (B10) The table `_RULES_SECTION` above points at via `tables`.
_TABLE: dict[str, Any] = {
    "id": "table:fixture-book:table-1-grapple-ranks",
    "type": "table",
    "name": "Table 1: Grapple Ranks",
    "slug": "table-1-grapple-ranks",
    "aliases": [],
    "book_id": "fixture-book",
    "pages": [3],
    "citation": "FB p. 3",
    "text_md": "",
    "fields": {
        "caption": "Table 1: Grapple Ranks",
        "columns": ["Rank", "Bonus"],
        "rows": [["1", "+0"], ["2", "+2"], ["3", "+4"]],
        "parent_record": "rules_section:fixture-book:grapple-ranks",
    },
    "tables": [],
    "canonical": False,
    "variant_of": None,
    "applied_overrides": [],
    "macro_eligible": False,
    "schema_version": 1,
    "extraction": {
        "tier": "haiku",
        "model": "fixture",
        "segment_id": "fixture-book-p0003-01",
        "timestamp": "2026-01-01T00:00:00+00:00",
    },
}


def write_fixture_data(data_dir: Path) -> BuildResult:
    """Write the synthetic manifest/segment/record files under `data_dir`
    and build `data_dir/db/owlsperch.sqlite` from them. `data_dir` is
    created if it doesn't already exist; safe to call against a fresh temp
    directory, which is the intended use (see this module's docstring)."""
    data_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = data_dir / "manifest.yaml"
    manifest_path.write_text(yaml.safe_dump(_MANIFEST))

    seg_dir = data_dir / "segments" / "fixture-book"
    seg_dir.mkdir(parents=True, exist_ok=True)
    for segment in (_SEGMENT, _SEGMENT_2, _SEGMENT_3):
        (seg_dir / f"{segment['seg_id']}.json").write_text(json.dumps(segment, indent=2))

    records_dir = data_dir / "records" / "fixture-book" / "spell"
    records_dir.mkdir(parents=True, exist_ok=True)
    for spell in _SPELLS:
        (records_dir / f"{spell['slug']}.json").write_text(json.dumps(spell, indent=2))

    for record in (_FEAT, _RULES_SECTION, _TABLE):
        type_dir = data_dir / "records" / "fixture-book" / record["type"]
        type_dir.mkdir(parents=True, exist_ok=True)
        (type_dir / f"{record['slug']}.json").write_text(json.dumps(record, indent=2))

    return build_db(data_dir=data_dir, manifest_path=manifest_path)


def run_fixture_db(data_dir: Path, *, out: Any = None) -> int:
    """CLI entry point for `owlsperch fixture-db <dir>`: builds the fixture
    data described in this module's docstring and prints the same summary
    `owlsperch build-db` does. Always exits 0 -- the fixture data is
    schema-valid by construction, so a nonzero `skipped_invalid` would
    indicate a bug in this module, not expected in-progress extraction."""
    out = out if out is not None else sys.stdout
    result = write_fixture_data(data_dir)
    print(result.render(), file=out)
    return 0
