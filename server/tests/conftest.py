"""Shared fixtures for `owlsperch_server` tests: builds a real SQLite
database (via `owlsperch.build_db.runner.build_db`) from synthetic spell
records in a temp `$OWLSPERCH_DATA`-shaped directory, validated against the
real committed `schemas/` -- matching the pipeline's fixture convention (no
real book text involved; see pipeline/tests/test_build_db.py and
test_validate_runner.py).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from owlsperch.build_db.runner import build_db

_REPO_SCHEMAS_DIR = Path(__file__).resolve().parent.parent.parent / "schemas"


def _write_manifest(tmp_path: Path) -> Path:
    manifest = {
        "entries": [
            {
                "book_id": "book-a",
                "title": "Book A",
                "short_title": "BA",
                "file": "book-a.pdf",
                "edition": "3.5",
                "kind": "rulebook",
                "published": "2001-01",
            },
            {
                "book_id": "book-b",
                "title": "Book B",
                "short_title": "BB",
                "file": "book-b.pdf",
                "edition": "3.5",
                "kind": "rulebook",
                "published": "2010-05",
            },
        ]
    }
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(manifest))
    return path


def _write_segment(data_dir: Path, book_id: str, seg_id: str, pages: list[int]) -> None:
    seg_dir = data_dir / "segments" / book_id
    seg_dir.mkdir(parents=True, exist_ok=True)
    segment = {
        "seg_id": seg_id,
        "book_id": book_id,
        "pages": pages,
        "printed_pages": pages,
        "kind_hint": "spell",
        "heading": "Test Spell",
        "text": "Test Spell\n\nEvocation Level: Sor/Wiz 3.",
        "status": "pending",
        "tier": "haiku",
        "attempts": [],
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    (seg_dir / f"{seg_id}.json").write_text(json.dumps(segment, indent=2))


def _spell_record(
    *,
    book_id: str,
    slug: str,
    name: str,
    seg_id: str,
    pages: list[int],
    aliases: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": f"spell:{book_id}:{slug}",
        "type": "spell",
        "name": name,
        "slug": slug,
        "aliases": aliases or [],
        "book_id": book_id,
        "pages": pages,
        "citation": f"{book_id} p. {pages[0]}",
        "text_md": f"{name} does something.",
        "fields": {
            "school": "Evocation",
            "subschool": None,
            "descriptors": ["Fire"],
            "levels": [{"class": "Sorcerer", "level": 3}],
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
            "model": "claude-haiku-test",
            "segment_id": seg_id,
            "timestamp": "2026-01-01T00:00:00+00:00",
        },
    }


def _write_record(data_dir: Path, book_id: str, slug: str, record: dict[str, Any]) -> None:
    out_dir = data_dir / "records" / book_id / "spell"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{slug}.json").write_text(json.dumps(record, indent=2))


@pytest.fixture
def built_data_dir(tmp_path: Path) -> Path:
    """A temp `$OWLSPERCH_DATA`-shaped directory with `db/owlsperch.sqlite`
    already built from a handful of synthetic spell records:

    - `spell:book-a:fireball` ("Fireball", alias "Fyre Ball") -- prefix
      search, alias search, and substring-fallback search (query "reba",
      a mid-word substring of "Fireball" that no FTS5 prefix query matches).
    - `spell:book-a:acid-fog` (book-a, published 2001-01) and
      `spell:book-b:acid-fog` (book-b, published 2010-05) -- same
      type+slug in two books, for the duplicate-slug/variants case
      (book-b, the later printing, wins; book-a's id is listed as a variant).
    - `spell:book-a:test-spell-{one,two,three}` -- three same-prefix names,
      for the `limit` and grouping tests.
    """
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)

    _write_segment(data_dir, "book-a", "book-a-p0001-01", [1])
    _write_segment(data_dir, "book-b", "book-b-p0001-01", [1])

    _write_record(
        data_dir,
        "book-a",
        "fireball",
        _spell_record(
            book_id="book-a",
            slug="fireball",
            name="Fireball",
            seg_id="book-a-p0001-01",
            pages=[1],
            aliases=["Fyre Ball"],
        ),
    )
    _write_record(
        data_dir,
        "book-a",
        "acid-fog",
        _spell_record(
            book_id="book-a",
            slug="acid-fog",
            name="Acid Fog",
            seg_id="book-a-p0001-01",
            pages=[1],
        ),
    )
    _write_record(
        data_dir,
        "book-b",
        "acid-fog",
        _spell_record(
            book_id="book-b",
            slug="acid-fog",
            name="Acid Fog",
            seg_id="book-b-p0001-01",
            pages=[1],
        ),
    )
    for ordinal in ("one", "two", "three"):
        _write_record(
            data_dir,
            "book-a",
            f"test-spell-{ordinal}",
            _spell_record(
                book_id="book-a",
                slug=f"test-spell-{ordinal}",
                name=f"Test Spell {ordinal.title()}",
                seg_id="book-a-p0001-01",
                pages=[1],
            ),
        )

    build_db(data_dir=data_dir, manifest_path=manifest_path, schemas_dir=_REPO_SCHEMAS_DIR)
    return data_dir
