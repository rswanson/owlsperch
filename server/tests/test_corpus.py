"""Corpus-gated end-to-end test (see also pipeline/tests' `@pytest.mark.corpus`
tests): builds a real database from whatever spell records already exist
under the real corpus (batch B5's `/extract`, book_id `phb1`) and confirms
`/search` finds one of them by name prefix. Skipped (not failed) when no
`phb1` spell records have been extracted yet.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from owlsperch.build_db.runner import build_db
from owlsperch.manifest import default_manifest_path
from owlsperch.schemas import load_registry
from owlsperch.text.runner import default_data_dir
from owlsperch_server.app import create_app


@pytest.mark.corpus
def test_search_finds_a_real_extracted_phb1_spell(tmp_path: Path) -> None:
    real_data_dir = default_data_dir()
    phb1_spells_dir = real_data_dir / "records" / "phb1" / "spell"
    spell_files = sorted(phb1_spells_dir.glob("*.json")) if phb1_spells_dir.is_dir() else []
    if not spell_files:
        pytest.skip("no phb1 spell records extracted yet under $OWLSPERCH_DATA")

    # If any of these records predate the spell schema's current version,
    # this isn't a real regression -- it's a schema bump that hasn't been
    # migrated onto the corpus's existing records yet (see CLAUDE.md's
    # `validate` paragraph). Skip, naming the fix, instead of risking a
    # build-db skip (for a reason that may or may not mention
    # `schema_version`) masking the search assertion below.
    current_spell_version = load_registry().types["spell"].version
    if any(
        json.loads(f.read_text())["schema_version"] != current_spell_version for f in spell_files
    ):
        pytest.skip(
            "at least one phb1 spell record predates the current spell schema "
            "version -- run `uv run owlsperch validate phb1 --bump-compatible` "
            "against $OWLSPERCH_DATA to migrate them, then rerun this test"
        )

    record = json.loads(spell_files[0].read_text())
    name = record["name"]
    prefix = name[: max(2, min(4, len(name)))]

    # "build-db into a temp copy": copy just the records/segments (small
    # JSON) this needs, not the whole data dir (text/pages/etc. can be huge
    # and build-db never reads them).
    tmp_data_dir = tmp_path / "data"
    shutil.copytree(real_data_dir / "records", tmp_data_dir / "records")
    if (real_data_dir / "segments").is_dir():
        shutil.copytree(real_data_dir / "segments", tmp_data_dir / "segments")

    build_db(data_dir=tmp_data_dir, manifest_path=default_manifest_path())

    client = TestClient(create_app(data_dir=tmp_data_dir))
    response = client.get("/search", params={"q": prefix})
    assert response.status_code == 200
    hits = [hit for group in response.json()["groups"] for hit in group["hits"]]
    assert any(hit["name"] == name for hit in hits)


@pytest.mark.corpus
def test_search_finds_the_real_extracted_power_attack_feat(tmp_path: Path) -> None:
    """B10 criterion 5 (manual gate, not CI): once `/extract phb1 --kind
    feat` has produced `records/phb1/feat/power-attack.json`, `/search`
    finds it as a feat named "Power Attack" with a non-empty benefit.
    Skipped (not failed) until that extraction has actually been run."""
    real_data_dir = default_data_dir()
    power_attack_path = real_data_dir / "records" / "phb1" / "feat" / "power-attack.json"
    if not power_attack_path.is_file():
        pytest.skip(
            "records/phb1/feat/power-attack.json does not exist yet -- run "
            "`/extract phb1 --kind feat` (or `uv run owlsperch queue next phb1 "
            "--kind feat --limit N` + the usual complete/validate loop) to "
            "produce it, then rerun this test"
        )

    current_feat_version = load_registry().types["feat"].version
    record = json.loads(power_attack_path.read_text())
    if record["schema_version"] != current_feat_version:
        pytest.skip(
            "records/phb1/feat/power-attack.json predates the current feat "
            "schema version -- run `uv run owlsperch validate phb1 "
            "--bump-compatible` against $OWLSPERCH_DATA to migrate it, then "
            "rerun this test"
        )

    tmp_data_dir = tmp_path / "data"
    shutil.copytree(real_data_dir / "records", tmp_data_dir / "records")
    if (real_data_dir / "segments").is_dir():
        shutil.copytree(real_data_dir / "segments", tmp_data_dir / "segments")

    build_db(data_dir=tmp_data_dir, manifest_path=default_manifest_path())

    client = TestClient(create_app(data_dir=tmp_data_dir))
    response = client.get("/search", params={"q": "Power"})
    assert response.status_code == 200
    hits = [hit for group in response.json()["groups"] for hit in group["hits"]]
    feat_hits = [hit for hit in hits if hit["type"] == "feat" and hit["name"] == "Power Attack"]
    assert feat_hits, f"expected a feat hit named 'Power Attack', got: {hits}"

    assert isinstance(record["fields"].get("benefit"), str)
    assert record["fields"]["benefit"].strip()


@pytest.mark.corpus
def test_attacks_of_opportunity_detail_resolves_to_combat_category(tmp_path: Path) -> None:
    """Batch B10b criterion 5: once `uv run owlsperch toc phb1` has run,
    `/records/rules_section/attacks-of-opportunity` (if the record has been
    extracted) resolves to `toc.category == "combat"`. Skipped (not failed)
    until both the toc file and the record exist."""
    real_data_dir = default_data_dir()
    toc_path = real_data_dir / "toc" / "phb1.json"
    if not toc_path.is_file():
        pytest.skip("toc/phb1.json not present -- run `uv run owlsperch toc phb1` first")

    record_path = (
        real_data_dir / "records" / "phb1" / "rules_section" / "attacks-of-opportunity.json"
    )
    if not record_path.is_file():
        pytest.skip("records/phb1/rules_section/attacks-of-opportunity.json not extracted yet")

    tmp_data_dir = tmp_path / "data"
    shutil.copytree(real_data_dir / "records", tmp_data_dir / "records")
    if (real_data_dir / "segments").is_dir():
        shutil.copytree(real_data_dir / "segments", tmp_data_dir / "segments")
    shutil.copytree(real_data_dir / "toc", tmp_data_dir / "toc")

    build_db(data_dir=tmp_data_dir, manifest_path=default_manifest_path())

    client = TestClient(create_app(data_dir=tmp_data_dir))
    response = client.get("/records/rules_section/attacks-of-opportunity")
    assert response.status_code == 200
    assert response.json()["toc"]["category"] == "combat"


#: Same column-header check `check_class_fields` (Part 3b) runs, kept as a
#: literal copy rather than an import so this standing gate doesn't
#: silently stop meaning anything if that check is ever loosened.
_SPELL_COLUMN_RE = re.compile(r"per day|known|points", re.IGNORECASE)


@pytest.mark.corpus
def test_all_eleven_phb1_classes_are_extracted_and_validated() -> None:
    """B10c-mand3 Part 7 (criterion 5's standing gate): once all 11 PHB
    base classes have been re-extracted under the new heading-anchored
    segmenter, the amended prompt rules, and the stricter validators, each
    class record is at the current class schema version, owns a
    `level_table` that resolves to a real `table` record with exactly 20
    rows, and (when it casts spells) that table has a spells-per-day/
    known/points column. Skipped -- naming batch B10c-mand3 and `/extract
    phb1 --kind class` -- until all 11 exist and none is stale. Today's
    corpus has 6 (post B10c's initial judge spot-check), so this SKIPS,
    not fails."""
    real_data_dir = default_data_dir()
    class_dir = real_data_dir / "records" / "phb1" / "class"
    class_files = sorted(class_dir.glob("*.json")) if class_dir.is_dir() else []
    records = [json.loads(f.read_text()) for f in class_files]

    current_class_version = load_registry().types["class"].version
    stale = [r.get("name") for r in records if r.get("schema_version") != current_class_version]

    if len(records) < 11 or stale:
        pytest.skip(
            f"{len(records)}/11 phb1 class records extracted"
            + (f" (stale: {stale})" if stale else "")
            + " -- run `/extract phb1 --kind class` (batch B10c-mand3's "
            "re-extraction under the new segmenter/prompt/validator rules) "
            "to produce all 11 at the current schema version, then rerun "
            "this test"
        )

    assert len(records) == 11

    table_dir = real_data_dir / "records" / "phb1" / "table"
    for record in records:
        name = record["name"]
        level_table_id = record["fields"]["level_table"]
        slug = level_table_id.split(":")[-1]
        table_path = table_dir / f"{slug}.json"
        assert table_path.is_file(), (
            f"{name}: level_table {level_table_id!r} not found at {table_path}"
        )

        table = json.loads(table_path.read_text())
        rows = table["fields"]["rows"]
        assert len(rows) == 20, (
            f"{name}: level_table {level_table_id!r} has {len(rows)} row(s), expected 20"
        )

        spellcasting = record["fields"].get("spellcasting")
        if spellcasting is not None:
            columns = table["fields"]["columns"]
            assert any(
                isinstance(c, str) and _SPELL_COLUMN_RE.search(c) for c in columns
            ), (
                f"{name}: spellcasting is set but level_table {level_table_id!r} has no "
                f"spells-per-day/known column (columns: {columns!r})"
            )
