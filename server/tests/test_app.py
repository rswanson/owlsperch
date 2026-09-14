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


def test_search_returns_canonical_only_after_precedence(built_data_dir: Path) -> None:
    """Batch B11 criterion 6/acceptance criterion 5: precedence's
    latest-wins pass demotes book-a's "Acid Fog" to canonical = 0 at build
    time, and `/search` (like `/records/{type}` and `/facets/{type}`) must
    never surface a non-canonical row."""
    response = _client(built_data_dir).get("/search", params={"q": "acid"})
    hits = [h for g in response.json()["groups"] for h in g["hits"]]
    assert [h["id"] for h in hits] == ["spell:book-b:acid-fog"]

    list_response = _client(built_data_dir).get("/records/spell", params={"page_size": 50})
    ids = [item["id"] for item in list_response.json()["items"]]
    assert "spell:book-b:acid-fog" in ids
    assert "spell:book-a:acid-fog" not in ids


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
    # Batch B11: precedence's latest-wins pass demotes book-a's printing at
    # build time (canonical = 0, variant_of = book-b's id), and `variants`
    # is now a list of objects, not bare id strings (design decision D19).
    response = _client(built_data_dir).get("/records/spell/acid-fog")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "spell:book-b:acid-fog"  # book-b published later
    assert body["variant_of"] is None
    assert len(body["variants"]) == 1
    variant = body["variants"][0]
    assert variant["id"] == "spell:book-a:acid-fog"
    assert variant["book_id"] == "book-a"
    assert "book_title" in variant
    assert "citation" in variant


def test_record_detail_resolves_applied_overrides_to_objects(built_data_dir: Path) -> None:
    response = _client(built_data_dir).get("/records/spell/ember-spark")
    assert response.status_code == 200
    body = response.json()
    assert len(body["applied_overrides"]) == 1
    override = body["applied_overrides"][0]
    assert override["id"] == "errata_entry:book-a-errata:ember-spark"
    assert override["type"] == "errata_entry"
    assert override["replacement_text"] == "Deals fire damage in a slightly bigger burst."
    assert override["book_id"] == "book-a-errata"


def test_record_detail_variant_with_different_slug_resolves_by_direct_url(
    built_data_dir: Path,
) -> None:
    """Design decision D19's RC-shaped edge case: a variant whose own slug
    differs from its winner's must still resolve by direct URL (the
    `canonical = 0` fallback branch) and report `variant_of` -- not the
    pre-B11 empty shape. Simulates precedence's output directly on the
    built database (the matching/grouping logic itself is covered by
    `test_build_db_precedence.py`) using two already-fixtured, differently
    -slugged rules_section records."""
    import sqlite3

    conn = sqlite3.connect(built_data_dir / "db" / "owlsperch.sqlite")
    conn.execute(
        "UPDATE records SET canonical = 0, variant_of = ? WHERE id = ?",
        (
            "rules_section:book-a:sable-rites",
            "rules_section:book-a:pending-table-section",
        ),
    )
    conn.commit()
    conn.close()

    response = _client(built_data_dir).get("/records/rules_section/pending-table-section")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "rules_section:book-a:pending-table-section"
    assert body["variant_of"] == "rules_section:book-a:sable-rites"
    assert body["variants"] == []


def test_record_detail_variants_resolve_a_two_hop_chain(built_data_dir: Path) -> None:
    """B11 fix: `_resolve_variants` must walk the transitive closure of
    `variant_of`, not just one hop -- defence in depth against a chain the
    pipeline itself no longer produces (`build_db.precedence
    ._flatten_variant_chains` keeps every stored `variant_of` at most one
    hop from its root). Builds a second hop on top of the fixture's real
    build-time chain (`spell:book-a:acid-fog` -> `spell:book-b:acid-fog`,
    book-b winning as the later-published printing) by repointing an
    already-fixtured, otherwise-unrelated spell at `spell:book-a:acid-fog`."""
    import sqlite3

    conn = sqlite3.connect(built_data_dir / "db" / "owlsperch.sqlite")
    conn.execute(
        "UPDATE records SET canonical = 0, variant_of = ? WHERE id = ?",
        ("spell:book-a:acid-fog", "spell:book-a:test-spell-one"),
    )
    conn.commit()
    conn.close()

    response = _client(built_data_dir).get("/records/spell/acid-fog")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "spell:book-b:acid-fog"

    variant_ids = [v["id"] for v in body["variants"]]
    assert "spell:book-a:acid-fog" in variant_ids
    assert "spell:book-a:test-spell-one" in variant_ids

    for variant in body["variants"]:
        assert variant.keys() >= {"id", "book_id", "book_title", "citation", "published"}


# ---------------------------------------------------------------------------
# /records/{type}/{slug} `toc`/`book_title` (batch B10b, design decision D11)
# ---------------------------------------------------------------------------


def test_record_detail_includes_toc_and_book_title_when_book_has_a_toc(
    built_data_dir: Path,
) -> None:
    # `book-a` has a synthetic toc (conftest.py's `_write_toc`): one chapter,
    # "Chapter 1: Magic", category "magic", covering every page book-a's
    # fixture records use.
    response = _client(built_data_dir).get("/records/spell/fireball")
    body = response.json()
    assert body["book_title"] == "BA"
    assert body["toc"] == {
        "category": "magic",
        "category_label": "Magic",
        "chapter": "Chapter 1: Magic",
        "section": None,
        "path": ["Chapter 1: Magic"],
    }


def test_record_detail_toc_is_uncategorized_when_book_has_no_toc(built_data_dir: Path) -> None:
    # `book-b` has no toc file at all -- the winning "acid-fog" record
    # (book-b, published later) must resolve to uncategorized/null, never a
    # 500 or a missing key.
    response = _client(built_data_dir).get("/records/spell/acid-fog")
    body = response.json()
    assert body["id"] == "spell:book-b:acid-fog"
    assert body["book_title"] == "BB"
    assert body["toc"] == {
        "category": "uncategorized",
        "category_label": "Uncategorized",
        "chapter": None,
        "section": None,
        "path": [],
    }


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
    # built_data_dir (conftest.py) writes 7 spell records: fireball,
    # acid-fog x2 (book-a and book-b), test-spell-{one,two,three},
    # ember-spark -- plus (B10) 2 rules_section records and the 1 table
    # record they reference, plus (B11) 1 errata_entry record.
    # Precedence's latest-wins pass demotes book-a's acid-fog printing to
    # canonical = 0 (book-b's is the later printing), so only 6 of the 7
    # spell records are canonical now.
    assert response.json() == {
        "counts": {"spell": 6, "rules_section": 2, "table": 1, "errata_entry": 1}
    }


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


# ---------------------------------------------------------------------------
# Batch B10c, design decision D12: /records/{type}/{slug} still resolves a
# record a class span superseded (canonical = 0), and reports
# `superseded_by`; /search, /records/{type}, /facets/{type} stay
# canonical-only (already true via existing `canonical = 1` clauses --
# these just prove it, per D12's "verify, don't re-plumb").
# ---------------------------------------------------------------------------


def _write_superseded_fixture(tmp_path: Path) -> Path:
    """A minimal, self-built data dir (not `built_data_dir`, which has no
    class records): one class record with a level table it owns, and one
    rules_section fragment fully inside its page span."""
    import json as json_mod

    import yaml

    from owlsperch.build_db.runner import build_db

    data_dir = tmp_path / "data"
    manifest_path = data_dir / "manifest.yaml"
    data_dir.mkdir(parents=True)
    manifest_path.write_text(
        yaml.safe_dump(
            {
                "entries": [
                    {
                        "book_id": "book",
                        "title": "Test Book",
                        "short_title": "TB",
                        "file": "book.pdf",
                        "edition": "3.5",
                        "kind": "rulebook",
                    }
                ]
            }
        )
    )

    seg_dir = data_dir / "segments" / "book"
    seg_dir.mkdir(parents=True)
    for seg_id, pages in (("book-class-p0002", [2, 3]), ("book-p0003-01", [3])):
        segment = {
            "seg_id": seg_id,
            "book_id": "book",
            "pages": pages,
            "printed_pages": pages,
            "kind_hint": "spell",
            "heading": "x",
            "text": "x",
            "status": "pending",
            "tier": "haiku",
            "attempts": [],
            "created_at": "2026-01-01T00:00:00+00:00",
        }
        (seg_dir / f"{seg_id}.json").write_text(json_mod.dumps(segment))

    class_record = {
        "id": "class:book:testclass",
        "type": "class",
        "name": "Testclass",
        "slug": "testclass",
        "aliases": [],
        "book_id": "book",
        "pages": [2, 3],
        "citation": "Test Book pp. 2-3",
        "text_md": "Testclass overview.",
        "fields": {
            "hit_die": "d12",
            "class_type": "base",
            "max_level": 1,
            "alignment": "Any",
            "bab_progression": "good",
            "save_progressions": {"fort": "good", "ref": "poor", "will": "poor"},
            "class_skills": [{"skill": "Climb", "key_ability": "Str"}],
            "skill_points": {"base": 4, "ability": "Int"},
            "level_table": "table:book:table-x",
            "class_features": [{"name": "Rage", "level": 1, "text_md": "You rage."}],
            "description_sections": [{"heading": "Adventures", "text_md": "..."}],
            "weapon_and_armor_proficiency": "Simple weapons only.",
            "source_pages": {"start": 2, "end": 3},
        },
        "tables": ["table:book:table-x"],
        "canonical": False,
        "variant_of": None,
        "applied_overrides": [],
        "macro_eligible": False,
        "schema_version": 3,
        "extraction": {
            "tier": "sonnet",
            "model": "m",
            "segment_id": "book-class-p0002",
            "timestamp": "2026-01-01T00:00:00+00:00",
        },
    }
    table_record = {
        "id": "table:book:table-x",
        "type": "table",
        "name": "Table X",
        "slug": "table-x",
        "aliases": [],
        "book_id": "book",
        "pages": [2],
        "citation": "Test Book p. 2",
        "text_md": "",
        "fields": {
            "columns": ["Level", "Base Attack Bonus", "Fort Save", "Ref Save", "Will Save"],
            "rows": [["1st", "+1", "+2", "+0", "+0"]],
            "parent_record": "class:book:testclass",
        },
        "tables": [],
        "canonical": False,
        "variant_of": None,
        "applied_overrides": [],
        "macro_eligible": False,
        "schema_version": 1,
        "extraction": {
            "tier": "sonnet",
            "model": "m",
            "segment_id": "book-class-p0002",
            "timestamp": "2026-01-01T00:00:00+00:00",
        },
    }
    fragment_record = {
        "id": "rules_section:book:barbarian-fluff",
        "type": "rules_section",
        "name": "Barbarian Fluff",
        "slug": "barbarian-fluff",
        "aliases": [],
        "book_id": "book",
        "pages": [3],
        "citation": "Test Book p. 3",
        "text_md": "Fragment text.",
        "fields": {"topic": "Barbarian Fluff"},
        "tables": [],
        "canonical": False,
        "variant_of": None,
        "applied_overrides": [],
        "macro_eligible": False,
        "schema_version": 1,
        "extraction": {
            "tier": "haiku",
            "model": "m",
            "segment_id": "book-p0003-01",
            "timestamp": "2026-01-01T00:00:00+00:00",
        },
    }
    for record in (class_record, table_record, fragment_record):
        type_dir = data_dir / "records" / "book" / str(record["type"])
        type_dir.mkdir(parents=True, exist_ok=True)
        (type_dir / f"{record['slug']}.json").write_text(json_mod.dumps(record))

    result = build_db(data_dir=data_dir, manifest_path=manifest_path)
    assert result.skipped_invalid == 0, result.skipped
    assert result.superseded == 1
    return data_dir


def test_record_detail_still_resolves_a_superseded_record(tmp_path: Path) -> None:
    data_dir = _write_superseded_fixture(tmp_path)

    response = _client(data_dir).get("/records/rules_section/barbarian-fluff")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "rules_section:book:barbarian-fluff"
    assert body["superseded_by"] == "class:book:testclass"


def test_record_detail_canonical_record_reports_null_superseded_by(tmp_path: Path) -> None:
    data_dir = _write_superseded_fixture(tmp_path)

    response = _client(data_dir).get("/records/class/testclass")

    assert response.status_code == 200
    assert response.json()["superseded_by"] is None


def test_search_never_returns_a_superseded_record(tmp_path: Path) -> None:
    data_dir = _write_superseded_fixture(tmp_path)

    response = _client(data_dir).get("/search", params={"q": "Barbarian Fluff"})

    hits = [h for g in response.json()["groups"] for h in g["hits"]]
    assert not any(h["slug"] == "barbarian-fluff" for h in hits)


def test_records_list_never_returns_a_superseded_record(tmp_path: Path) -> None:
    data_dir = _write_superseded_fixture(tmp_path)

    response = _client(data_dir).get("/records/rules_section")

    assert response.status_code == 200
    slugs = {item["slug"] for item in response.json()["items"]}
    assert "barbarian-fluff" not in slugs
