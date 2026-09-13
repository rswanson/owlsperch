"""Endpoint tests for `owlsperch_server.app` (spec 4.9, batch B6): /search,
/records/{type}/{slug}, /schemas, /health, against the fixture DB built by
`built_data_dir` (see conftest.py)."""

from __future__ import annotations

from pathlib import Path

import pytest
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
# /search: FTS5 special-syntax queries never 500 -- a bare FTS5 boolean
# keyword (AND/OR/NOT) is a syntax error from the FTS5 query parser, and the
# stray punctuation queries below are sanitized by `_WORD_RE` before ever
# reaching FTS5; either way `_search`'s `except sqlite3.OperationalError`
# fallback (or a clean tokenized query) must still answer 200, never 500.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("q", ["AND", "OR", "NOT", 'fire"bolt', "fire*", "(fire"])
def test_search_special_syntax_queries_return_200(built_data_dir: Path, q: str) -> None:
    response = _client(built_data_dir).get("/search", params={"q": q})
    assert response.status_code == 200


def test_search_prefix_query_still_uses_fts_not_just_substring_fallback(
    built_data_dir: Path,
) -> None:
    # "fyre" only matches the "Fyre Ball" alias on `spell:book-a:fireball` --
    # the substring fallback only scans `records.name` (never `aliases`), so
    # this hit can only come from the FTS5 path over `names_fts` actually
    # running (and finding it), not from the fallback alone.
    response = _client(built_data_dir).get("/search", params={"q": "fyre"})
    assert response.status_code == 200
    hits = [h for g in response.json()["groups"] for h in g["hits"]]
    assert any(h["slug"] == "fireball" for h in hits)


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
# /records/{type}/{slug} `tables` resolution (B10 criterion 11)
# ---------------------------------------------------------------------------


def test_record_detail_resolves_owned_table(built_data_dir: Path) -> None:
    response = _client(built_data_dir).get("/records/rules_section/sable-rites")
    assert response.status_code == 200
    body = response.json()
    assert body["tables"] == [
        {
            "id": "table:book-a:table-1-1-sable-ranks",
            "pending": False,
            "name": "Table 1-1: Sable Ranks",
            "slug": "table-1-1-sable-ranks",
            "caption": "Table 1-1: Sable Ranks",
            "columns": ["Rank", "Title"],
            "rows": [["1", "Initiate"], ["2", "Adept"]],
            "citation": "book-a p. 1",
            "book_id": "book-a",
        }
    ]


def test_record_detail_unresolved_table_id_is_pending(built_data_dir: Path) -> None:
    response = _client(built_data_dir).get("/records/rules_section/pending-table-section")
    assert response.status_code == 200
    body = response.json()
    assert body["tables"] == [
        {
            "id": "table:book-a:does-not-exist-yet",
            "pending": True,
            "name": None,
            "slug": None,
            "caption": None,
            "columns": [],
            "rows": [],
            "citation": None,
            "book_id": None,
        }
    ]


def test_record_detail_with_no_tables_returns_empty_list(built_data_dir: Path) -> None:
    # Also covered by test_record_detail_returns_full_record's `tables ==
    # []` assertion -- an explicit test here names the "no tables at all"
    # case for criterion 11's three-case coverage.
    response = _client(built_data_dir).get("/records/spell/fireball")
    assert response.json()["tables"] == []


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
# /stats (batch B7, spec 4.10): canonical record counts by type, for the web
# UI home page's "N Spells" hint.
# ---------------------------------------------------------------------------


def test_stats_counts_canonical_records_by_type(built_data_dir: Path) -> None:
    response = _client(built_data_dir).get("/stats")
    assert response.status_code == 200
    # built_data_dir (conftest.py) writes 6 spell records: fireball,
    # acid-fog x2 (book-a and book-b), test-spell-{one,two,three} -- plus
    # (B10) 2 rules_section records and the 1 table record they reference.
    assert response.json() == {"counts": {"spell": 6, "rules_section": 2, "table": 1}}


def test_stats_503_when_db_missing(tmp_path: Path) -> None:
    response = _client(tmp_path / "empty-data").get("/stats")
    assert response.status_code == 503
    assert response.json() == {"detail": "database not built; run: uv run owlsperch build-db"}


def test_stats_503_when_db_corrupt(tmp_path: Path) -> None:
    response = _client(_corrupt_data_dir(tmp_path)).get("/stats")
    assert response.status_code == 503
    assert response.json() == {
        "detail": "database unreadable; rebuild with: uv run owlsperch build-db"
    }


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


# ---------------------------------------------------------------------------
# Corrupt (present but unreadable) DB file -> 503 with a distinct detail
# (except /health and /schemas, which never touch the database). Unlike the
# "missing" case above, `sqlite3.connect` on a garbage file succeeds --
# SQLite only validates the header lazily, on the first real read -- so this
# exercises the `_query_db` wrapper's `sqlite3.DatabaseError` handling, not
# `_require_db`'s file-existence check.
# ---------------------------------------------------------------------------


def _corrupt_data_dir(tmp_path: Path) -> Path:
    data_dir = tmp_path / "corrupt-data"
    db_path = data_dir / "db" / "owlsperch.sqlite"
    db_path.parent.mkdir(parents=True)
    db_path.write_bytes(b"not a real sqlite database, just garbage bytes")
    return data_dir


def test_search_503_when_db_corrupt(tmp_path: Path) -> None:
    response = _client(_corrupt_data_dir(tmp_path)).get("/search", params={"q": "fireball"})
    assert response.status_code == 503
    assert response.json() == {
        "detail": "database unreadable; rebuild with: uv run owlsperch build-db"
    }


def test_record_detail_503_when_db_corrupt(tmp_path: Path) -> None:
    response = _client(_corrupt_data_dir(tmp_path)).get("/records/spell/fireball")
    assert response.status_code == 503
    assert response.json() == {
        "detail": "database unreadable; rebuild with: uv run owlsperch build-db"
    }


def test_health_and_schemas_unaffected_by_corrupt_db(tmp_path: Path) -> None:
    client = _client(_corrupt_data_dir(tmp_path))
    assert client.get("/health").json() == {"status": "ok", "db": True}
    assert client.get("/schemas").status_code == 200
