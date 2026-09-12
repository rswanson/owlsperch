"""Endpoint tests for `owlsperch_server.app` (spec 4.9, batch B6): /search,
/records/{type}/{slug}, /schemas, /health, against the fixture DB built by
`built_data_dir` (see conftest.py)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from owlsperch_server.app import create_app


def _client(data_dir: Path) -> TestClient:
    return TestClient(create_app(data_dir=data_dir))


# ---------------------------------------------------------------------------
# /search
# ---------------------------------------------------------------------------


def test_search_prefix_match_on_name(built_data_dir: Path) -> None:
    response = _client(built_data_dir).get("/search", params={"q": "fireb"})
    assert response.status_code == 200
    hits = [h for g in response.json()["groups"] for h in g["hits"]]
    assert any(h["slug"] == "fireball" and h["type"] == "spell" for h in hits)


def test_search_matches_alias(built_data_dir: Path) -> None:
    response = _client(built_data_dir).get("/search", params={"q": "fyre"})
    hits = [h for g in response.json()["groups"] for h in g["hits"]]
    assert any(h["slug"] == "fireball" for h in hits)


def test_search_substring_fallback_for_mid_word_match(built_data_dir: Path) -> None:
    # "reba" is a substring of "Fireball" but not a token prefix -- FTS5
    # prefix search alone won't find it; the substring fallback should.
    response = _client(built_data_dir).get("/search", params={"q": "reba"})
    hits = [h for g in response.json()["groups"] for h in g["hits"]]
    assert any(h["slug"] == "fireball" for h in hits)


def test_search_type_filter(built_data_dir: Path) -> None:
    response = _client(built_data_dir).get("/search", params={"q": "test", "types": "spell"})
    hits = [h for g in response.json()["groups"] for h in g["hits"]]
    assert len(hits) == 3

    none_response = _client(built_data_dir).get("/search", params={"q": "test", "types": "monster"})
    assert none_response.json()["groups"] == []


def test_search_limit(built_data_dir: Path) -> None:
    response = _client(built_data_dir).get("/search", params={"q": "test", "limit": 2})
    hits = [h for g in response.json()["groups"] for h in g["hits"]]
    assert len(hits) == 2


def test_search_groups_by_type_with_label(built_data_dir: Path) -> None:
    response = _client(built_data_dir).get("/search", params={"q": "fireball"})
    groups = response.json()["groups"]
    assert groups == [
        {
            "type": "spell",
            "label": "Spells",
            "hits": [
                {
                    "id": "spell:book-a:fireball",
                    "type": "spell",
                    "name": "Fireball",
                    "slug": "fireball",
                    "citation": "book-a p. 1",
                    "book_id": "book-a",
                }
            ],
        }
    ]


def test_search_rejects_short_query(built_data_dir: Path) -> None:
    response = _client(built_data_dir).get("/search", params={"q": "f"})
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# /records/{type}/{slug}
# ---------------------------------------------------------------------------


def test_record_detail_returns_full_record(built_data_dir: Path) -> None:
    response = _client(built_data_dir).get("/records/spell/fireball")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "spell:book-a:fireball"
    assert body["name"] == "Fireball"
    assert body["citation"] == "book-a p. 1"
    assert body["variants"] == []
    assert body["links"] == []
    assert body["referenced_by"] == []
    assert body["tables"] == []


def test_record_detail_unknown_type_404(built_data_dir: Path) -> None:
    response = _client(built_data_dir).get("/records/monster/fireball")
    assert response.status_code == 404
    assert response.json() == {"detail": "unknown type"}


def test_record_detail_unknown_slug_404(built_data_dir: Path) -> None:
    response = _client(built_data_dir).get("/records/spell/does-not-exist")
    assert response.status_code == 404
    assert response.json()["detail"] == "unknown slug"


def test_record_detail_duplicate_slug_returns_latest_and_lists_variant(
    built_data_dir: Path,
) -> None:
    response = _client(built_data_dir).get("/records/spell/acid-fog")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "spell:book-b:acid-fog"  # book-b published later
    assert body["variants"] == ["spell:book-a:acid-fog"]


# ---------------------------------------------------------------------------
# /schemas, /health
# ---------------------------------------------------------------------------


def test_schemas_shape(built_data_dir: Path) -> None:
    response = _client(built_data_dir).get("/schemas")
    assert response.status_code == 200
    body = response.json()
    spell = body["types"]["spell"]
    assert spell["label"] == "Spell"
    assert spell["plural_label"] == "Spells"
    field_names = {f["name"] for f in spell["fields"]}
    assert "school" in field_names
    school_field = next(f for f in spell["fields"] if f["name"] == "school")
    assert school_field["x-ui"]["label"] == "School"


def test_health_reports_db_true_when_built(built_data_dir: Path) -> None:
    response = _client(built_data_dir).get("/health")
    assert response.json() == {"status": "ok", "db": True}


# ---------------------------------------------------------------------------
# Missing DB -> 503 (except /health and /schemas)
# ---------------------------------------------------------------------------


def test_search_503_when_db_missing(tmp_path: Path) -> None:
    response = _client(tmp_path / "empty-data").get("/search", params={"q": "fireball"})
    assert response.status_code == 503
    assert response.json() == {"detail": "database not built; run: uv run owlsperch build-db"}


def test_record_detail_503_when_db_missing(tmp_path: Path) -> None:
    response = _client(tmp_path / "empty-data").get("/records/spell/fireball")
    assert response.status_code == 503


def test_health_reports_db_false_when_missing(tmp_path: Path) -> None:
    response = _client(tmp_path / "empty-data").get("/health")
    assert response.json() == {"status": "ok", "db": False}


def test_schemas_available_even_when_db_missing(tmp_path: Path) -> None:
    response = _client(tmp_path / "empty-data").get("/schemas")
    assert response.status_code == 200
