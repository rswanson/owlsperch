"""Corpus-gated end-to-end test (see also pipeline/tests' `@pytest.mark.corpus`
tests): builds a real database from whatever spell records already exist
under the real corpus (batch B5's `/extract`, book_id `phb1`) and confirms
`/search` finds one of them by name prefix. Skipped (not failed) when no
`phb1` spell records have been extracted yet.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from owlsperch.build_db.runner import build_db
from owlsperch.manifest import default_manifest_path
from owlsperch.text.runner import default_data_dir
from owlsperch_server.app import create_app


@pytest.mark.corpus
def test_search_finds_a_real_extracted_phb1_spell(tmp_path: Path) -> None:
    real_data_dir = default_data_dir()
    phb1_spells_dir = real_data_dir / "records" / "phb1" / "spell"
    spell_files = sorted(phb1_spells_dir.glob("*.json")) if phb1_spells_dir.is_dir() else []
    if not spell_files:
        pytest.skip("no phb1 spell records extracted yet under $OWLSPERCH_DATA")

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
