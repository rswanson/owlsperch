"""Endpoint tests for `GET /records/{type}` and `GET /facets/{type}` (spec
4.9, batch B9): filter/sort/paginate a type's records by its schema's
`x-ui` hints, and report distinct filter values with counts.

Fixture data (`browse_data_dir`) is a small, hand-picked set of six spells
across two books, chosen specifically to exercise:

- OR within a field (two `school` values).
- AND across fields (`school` + `class`, a `levels` sub-property).
- The nested-pair rule for array-of-object fields: "Mixed Spell" has BOTH
  `Cleric 3` and `Wizard 5` as separate `levels` items, so it must match
  `class=Cleric&level=3` but must NOT match `class=Cleric&level=5` (that
  combination isn't any single item on it) -- "Raise Dead" (`Cleric 5`,
  `Wizard 5` on one spell) is the genuine positive match for the latter.
- `source` (book_id) filtering.
- Sorting, pagination, and the `page_size` cap.
- Facet counts honoring every current filter except their own field.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from owlsperch.build_db.runner import build_db
from owlsperch.schemas import Registry, TypeInfo
from owlsperch_server.app import create_app
from owlsperch_server.browse import build_filter_clauses, load_type_browse_schema

_REPO_SCHEMAS_DIR = Path(__file__).resolve().parent.parent.parent / "schemas"

# (name, school, book_id, [(class, level), ...])
_SPELLS: list[tuple[str, str, str, list[tuple[str, int]]]] = [
    ("Fireball", "Evocation", "book-a", [("Sorcerer", 3), ("Wizard", 3)]),
    ("Alarm", "Abjuration", "book-a", [("Sorcerer", 1), ("Wizard", 1)]),
    ("Sanctuary", "Abjuration", "book-b", [("Cleric", 1)]),
    ("Prayer", "Conjuration", "book-b", [("Cleric", 3)]),
    ("Mixed Spell", "Illusion", "book-a", [("Cleric", 3), ("Wizard", 5)]),
    ("Raise Dead", "Necromancy", "book-b", [("Cleric", 5), ("Wizard", 5)]),
]


def _slugify(name: str) -> str:
    return name.lower().replace(" ", "-")


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


def _write_segment(data_dir: Path, book_id: str, seg_id: str) -> None:
    seg_dir = data_dir / "segments" / book_id
    seg_dir.mkdir(parents=True, exist_ok=True)
    segment = {
        "seg_id": seg_id,
        "book_id": book_id,
        "pages": [1],
        "printed_pages": [1],
        "kind_hint": "spell",
        "heading": "Test",
        "text": "Test spell text.",
        "status": "pending",
        "tier": "haiku",
        "attempts": [],
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    (seg_dir / f"{seg_id}.json").write_text(json.dumps(segment, indent=2))


def _record(
    *, book_id: str, name: str, seg_id: str, school: str, levels: list[tuple[str, int]]
) -> dict[str, Any]:
    slug = _slugify(name)
    return {
        "id": f"spell:{book_id}:{slug}",
        "type": "spell",
        "name": name,
        "slug": slug,
        "aliases": [],
        "book_id": book_id,
        "pages": [1],
        "citation": f"{book_id} p. 1",
        "text_md": f"{name} does something.",
        "fields": {
            "school": school,
            "levels": [{"class": cls, "level": lvl} for cls, lvl in levels],
        },
        "tables": [],
        "canonical": False,
        "variant_of": None,
        "applied_overrides": [],
        "macro_eligible": False,
        "schema_version": 3,
        "extraction": {
            "tier": "haiku",
            "model": "test",
            "segment_id": seg_id,
            "timestamp": "2026-01-01T00:00:00+00:00",
        },
    }


@pytest.fixture
def browse_data_dir(tmp_path: Path) -> Path:
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)

    _write_segment(data_dir, "book-a", "book-a-p0001-01")
    _write_segment(data_dir, "book-b", "book-b-p0001-01")

    for name, school, book_id, levels in _SPELLS:
        seg_id = f"{book_id}-p0001-01"
        out_dir = data_dir / "records" / book_id / "spell"
        out_dir.mkdir(parents=True, exist_ok=True)
        record = _record(book_id=book_id, name=name, seg_id=seg_id, school=school, levels=levels)
        (out_dir / f"{record['slug']}.json").write_text(json.dumps(record, indent=2))

    build_db(data_dir=data_dir, manifest_path=manifest_path, schemas_dir=_REPO_SCHEMAS_DIR)
    return data_dir


def _client(data_dir: Path) -> TestClient:
    return TestClient(create_app(data_dir=data_dir))


def _names(body: dict[str, Any]) -> set[str]:
    return {item["name"] for item in body["items"]}


# ---------------------------------------------------------------------------
# load_type_browse_schema / build_filter_clauses -- unit-level regressions
# ---------------------------------------------------------------------------


def test_load_type_browse_schema_handles_nullable_array_of_object_type(tmp_path: Path) -> None:
    """Regression: a filterable field typed `["array", "null"]` (how JSON
    Schema commonly expresses an optional array-of-object field) must still
    get sub-field/pair handling, not be treated as an opaque scalar with no
    sub-fields at all."""
    schema = {
        "properties": {
            "levels": {
                "type": ["array", "null"],
                "items": {
                    "type": "object",
                    "properties": {
                        "class": {"type": "string"},
                        "level": {"type": ["integer", "null"]},
                    },
                },
                "x-ui": {"label": "Levels", "filterable": True, "sortable": False},
            }
        }
    }
    (tmp_path / "widget.json").write_text(json.dumps(schema))
    registry = Registry(
        schemas_dir=tmp_path,
        types={
            "widget": TypeInfo(
                type_name="widget",
                schema_file="widget.json",
                label="Widget",
                plural_label="Widgets",
                version=1,
            )
        },
        envelope_schema={},
    )

    browse_schema = load_type_browse_schema(registry, "widget")
    field = browse_schema.filterable[0]
    assert field.name == "levels"
    assert [sf.name for sf in field.sub_fields] == ["class", "level"]
    assert {"class", "level"} <= browse_schema.allowed_params()

    # Both sub-properties given together must take the combined-row pair
    # path (one clause keyed by the parent field name), not the fallback
    # single-field ANDing path a missing `sub_fields` list would force.
    clauses, params = build_filter_clauses(browse_schema, {"class": ["Cleric"], "level": ["3"]})
    assert len(clauses) == 1
    assert params[0] == "levels"
    assert "cleric 3" in params


# ---------------------------------------------------------------------------
# GET /records/{type} -- filtering
# ---------------------------------------------------------------------------


def test_filter_by_school(browse_data_dir: Path) -> None:
    response = _client(browse_data_dir).get("/records/spell", params={"school": "Abjuration"})
    assert response.status_code == 200
    body = response.json()
    assert _names(body) == {"Alarm", "Sanctuary"}
    assert body["total"] == 2


def test_filter_or_within_field(browse_data_dir: Path) -> None:
    response = _client(browse_data_dir).get(
        "/records/spell", params=[("school", "Abjuration"), ("school", "Evocation")]
    )
    assert response.status_code == 200
    assert _names(response.json()) == {"Alarm", "Sanctuary", "Fireball"}


def test_filter_and_across_fields(browse_data_dir: Path) -> None:
    response = _client(browse_data_dir).get(
        "/records/spell", params={"school": "Abjuration", "class": "Cleric"}
    )
    assert response.status_code == 200
    # Alarm (Abjuration) has no Cleric level; only Sanctuary matches both.
    assert _names(response.json()) == {"Sanctuary"}


def test_filter_nested_pair_matches_same_item(browse_data_dir: Path) -> None:
    response = _client(browse_data_dir).get(
        "/records/spell", params={"class": "Cleric", "level": "3"}
    )
    assert response.status_code == 200
    # Prayer (Cleric 3) and Mixed Spell (which has a Cleric 3 item) match.
    assert _names(response.json()) == {"Prayer", "Mixed Spell"}


def test_filter_nested_pair_rejects_cross_item_match(browse_data_dir: Path) -> None:
    response = _client(browse_data_dir).get(
        "/records/spell", params={"class": "Cleric", "level": "5"}
    )
    assert response.status_code == 200
    # Mixed Spell has Cleric 3 *and* Wizard 5 as separate items -- it must
    # NOT match class=Cleric&level=5. Raise Dead (Cleric 5, Wizard 5) does.
    assert _names(response.json()) == {"Raise Dead"}


def test_filter_nested_pair_accepts_float_style_level(browse_data_dir: Path) -> None:
    response = _client(browse_data_dir).get(
        "/records/spell", params={"class": "Cleric", "level": "3.0"}
    )
    assert response.status_code == 200
    assert _names(response.json()) == {"Prayer", "Mixed Spell"}


def test_filter_single_subparam_uses_num_value(browse_data_dir: Path) -> None:
    response = _client(browse_data_dir).get("/records/spell", params={"level": "1"})
    assert response.status_code == 200
    assert _names(response.json()) == {"Alarm", "Sanctuary"}


def test_filter_matches_combined_row_regardless_of_json_key_order(tmp_path: Path) -> None:
    """Regression: `build_db.runner.flatten_fields`'s combined row must be
    built in sorted subkey order, not the record JSON's own key order, so a
    `levels` item written with `level` before `class` still matches
    `class=Cleric&level=3`. Uses its own isolated data dir rather than
    `browse_data_dir` so it doesn't perturb that fixture's exact counts."""
    data_dir = tmp_path / "data"
    manifest_path = _write_manifest(tmp_path)
    _write_segment(data_dir, "book-a", "book-a-p0001-01")

    record = _record(
        book_id="book-a",
        name="Reordered Spell",
        seg_id="book-a-p0001-01",
        school="Divination",
        levels=[],
    )
    # Deliberately reversed key order within the item, unlike `_record`'s
    # own `{"class": cls, "level": lvl}` construction.
    record["fields"]["levels"] = [{"level": 3, "class": "Cleric"}]
    out_dir = data_dir / "records" / "book-a" / "spell"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{record['slug']}.json").write_text(json.dumps(record, indent=2))

    build_db(data_dir=data_dir, manifest_path=manifest_path, schemas_dir=_REPO_SCHEMAS_DIR)

    response = _client(data_dir).get("/records/spell", params={"class": "Cleric", "level": "3"})
    assert response.status_code == 200
    assert _names(response.json()) == {"Reordered Spell"}


def test_filter_source(browse_data_dir: Path) -> None:
    response = _client(browse_data_dir).get("/records/spell", params={"source": "book-a"})
    assert response.status_code == 200
    assert _names(response.json()) == {"Fireball", "Alarm", "Mixed Spell"}


def test_filter_source_or(browse_data_dir: Path) -> None:
    response = _client(browse_data_dir).get(
        "/records/spell", params=[("source", "book-a"), ("source", "book-b")]
    )
    assert response.status_code == 200
    assert response.json()["total"] == 6


# ---------------------------------------------------------------------------
# GET /records/{type} -- sort, pagination
# ---------------------------------------------------------------------------


def test_sort_by_name_ascending_default(browse_data_dir: Path) -> None:
    response = _client(browse_data_dir).get("/records/spell", params={"sort": "name"})
    names = [item["name"] for item in response.json()["items"]]
    assert names == sorted(names)


def test_sort_by_name_descending(browse_data_dir: Path) -> None:
    response = _client(browse_data_dir).get("/records/spell", params={"sort": "-name"})
    names = [item["name"] for item in response.json()["items"]]
    assert names == sorted(names, reverse=True)


def test_pagination_with_total(browse_data_dir: Path) -> None:
    response = _client(browse_data_dir).get(
        "/records/spell", params={"sort": "name", "page": 2, "page_size": 2}
    )
    body = response.json()
    assert body["total"] == 6
    assert body["page"] == 2
    assert body["page_size"] == 2
    all_names_sorted = sorted(name for name, *_ in _SPELLS)
    assert [item["name"] for item in body["items"]] == all_names_sorted[2:4]


def test_page_size_default_is_50(browse_data_dir: Path) -> None:
    response = _client(browse_data_dir).get("/records/spell")
    assert response.json()["page_size"] == 50


def test_page_size_capped_at_200(browse_data_dir: Path) -> None:
    response = _client(browse_data_dir).get("/records/spell", params={"page_size": 5000})
    assert response.json()["page_size"] == 200


# ---------------------------------------------------------------------------
# GET /records/{type} -- errors
# ---------------------------------------------------------------------------


def test_unknown_query_param_400(browse_data_dir: Path) -> None:
    response = _client(browse_data_dir).get("/records/spell", params={"bogus": "x"})
    assert response.status_code == 400
    assert "bogus" in response.json()["detail"]


def test_unknown_type_404(browse_data_dir: Path) -> None:
    response = _client(browse_data_dir).get("/records/monster")
    assert response.status_code == 404


def test_records_list_503_when_db_missing(tmp_path: Path) -> None:
    response = _client(tmp_path / "empty-data").get("/records/spell")
    assert response.status_code == 503


def test_item_shape(browse_data_dir: Path) -> None:
    response = _client(browse_data_dir).get("/records/spell", params={"school": "Conjuration"})
    body = response.json()
    assert body["type"] == "spell"
    item = body["items"][0]
    assert item["name"] == "Prayer"
    assert item["slug"] == "prayer"
    assert item["book_id"] == "book-b"
    assert item["citation"] == "book-b p. 1"
    assert item["facets"]["school"] == ["Conjuration"]
    assert item["facets"]["levels"] == ["Cleric 3"]


# ---------------------------------------------------------------------------
# GET /facets/{type}
# ---------------------------------------------------------------------------


def test_facets_shape_and_own_field_excluded(browse_data_dir: Path) -> None:
    response = _client(browse_data_dir).get("/facets/spell", params={"school": "Abjuration"})
    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "spell"
    facets_by_field = {f["field"]: f for f in body["facets"]}

    # The "school" facet ignores its own filter -- it reports every school
    # across all 6 spells, not just Abjuration.
    school_values = {v["value"]: v["count"] for v in facets_by_field["school"]["values"]}
    assert school_values == {
        "Abjuration": 2,
        "Evocation": 1,
        "Conjuration": 1,
        "Illusion": 1,
        "Necromancy": 1,
    }

    # The "class" facet DOES honor the school=Abjuration filter: only Alarm
    # (Sorcerer, Wizard) and Sanctuary (Cleric) qualify.
    class_values = {v["value"]: v["count"] for v in facets_by_field["class"]["values"]}
    assert class_values == {"Sorcerer": 1, "Wizard": 1, "Cleric": 1}


def test_facets_includes_source_with_label(browse_data_dir: Path) -> None:
    response = _client(browse_data_dir).get("/facets/spell")
    body = response.json()
    facets_by_field = {f["field"]: f for f in body["facets"]}
    source_values = {v["value"]: v for v in facets_by_field["source"]["values"]}
    assert source_values["book-a"]["count"] == 3
    assert source_values["book-a"]["label"] == "BA"
    assert source_values["book-b"]["count"] == 3
    assert source_values["book-b"]["label"] == "BB"


def test_facets_level_values_are_not_float_formatted(browse_data_dir: Path) -> None:
    response = _client(browse_data_dir).get("/facets/spell")
    body = response.json()
    facets_by_field = {f["field"]: f for f in body["facets"]}
    level_values = {v["value"] for v in facets_by_field["level"]["values"]}
    assert "3.0" not in level_values
    assert "3" in level_values


def test_facets_unknown_type_404(browse_data_dir: Path) -> None:
    response = _client(browse_data_dir).get("/facets/monster")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# B10 pitfall: a newly-registered type (feat) whose only facet is the
# built-in `source` pseudo-field must not 500, even with zero records.
# ---------------------------------------------------------------------------


def test_records_list_for_feat_type_does_not_500(browse_data_dir: Path) -> None:
    response = _client(browse_data_dir).get("/records/feat")
    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "feat"
    assert body["total"] == 0
    assert body["items"] == []


def test_facets_for_feat_type_does_not_500(browse_data_dir: Path) -> None:
    response = _client(browse_data_dir).get("/facets/feat")
    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "feat"
    field_names = {f["field"] for f in body["facets"]}
    assert "source" in field_names
    assert "feat_type" in field_names


def test_facets_503_when_db_missing(tmp_path: Path) -> None:
    response = _client(tmp_path / "empty-data").get("/facets/spell")
    assert response.status_code == 503
