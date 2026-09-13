"""SQL builder and endpoint helpers for `GET /records/{type}` and `GET
/facets/{type}` (spec 4.9, batch B9): filter/sort/paginate a type's records
by its schema's `x-ui` hints, and report distinct filter values with counts.
`app.py` stays thin -- it only wires these into FastAPI routes and runs them
through `_query_db`.

Filterable/sortable fields come ONLY from the type schema's `x-ui` hints
(`owlsperch.schemas.load_registry`): a field is filterable/sortable iff its
`x-ui.filterable`/`x-ui.sortable` is `true`. `name` is always an allowed
sort key (and the default) even though it isn't part of the type schema (it
lives on the envelope, not `fields`) -- no type schema currently marks
anything sortable, so without this there'd be no way to sort at all.
`source` is a built-in filterable/facetable pseudo-field backed by
`records.book_id`, not by anything in `record_fields`.

An array-of-object filterable field (spell `levels`, whose `items.properties`
are `class`/`level`) doesn't accept its own name as a query param. Instead
each item sub-property becomes its own top-level param (`class`, `level`),
derived generically from `items.properties` -- never hard-coded to spells.
Given only one of them, it matches that sub-property's own `record_fields`
row (`levels.class`/`levels.level`). Given ALL of an item's sub-properties at
once, they must describe the SAME item (`class=Cleric&level=3` must not
match a spell whose only Cleric level is 3 in one `levels` entry and a
different class at level 3 in another) -- so that case matches the parent
field's *combined* `record_fields` row instead (`levels` -> `"Cleric 3"`,
built by `build_db.runner.flatten_fields` in SORTED sub-property name order
-- a canonical order both modules agree on independently of the schema's
declared property order, since JSON Schema doesn't guarantee one; see that
module's docstring), expanding to the cross product of combined values when
any sub-param has repeated (OR) values. This module builds its own combos in
that same sorted order (`FilterableField.sub_fields` below is sorted by
sub-property name, not schema declaration order). A partial subset (2 of 3+
sub-properties, not currently reachable -- `levels` only has two) falls back
to ANDing each given sub-property's own single-field match; that's an
approximation for a case no current schema exercises.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from owlsperch.schemas import Registry

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200

#: Query params that never name a filter field.
_RESERVED_PARAMS = {"sort", "page", "page_size"}


class BrowseError(ValueError):
    """A bad request (unknown query param, bad sort/page value): `app.py`
    turns this into a 400 with `str(exc)` as the detail."""


@dataclass(frozen=True)
class SubField:
    """One item sub-property of an array-of-object filterable field, e.g.
    `class`/`level` under spell `levels`."""

    name: str
    parent: str
    numeric: bool


@dataclass(frozen=True)
class FilterableField:
    name: str
    label: str
    #: Non-empty only for an array-of-object field; its item's sub-properties,
    #: in SORTED sub-property-name order (the same canonical order
    #: `flatten_fields` joins them in for the combined row -- see that
    #: function's docstring).
    sub_fields: list[SubField]


@dataclass(frozen=True)
class TypeBrowseSchema:
    """Everything `browse.py` needs from one type's JSON Schema, computed
    once per request."""

    type_name: str
    filterable: list[FilterableField]
    #: Always includes "name" (see module docstring), regardless of what the
    #: type schema itself marks sortable.
    sortable: list[str]

    def allowed_params(self) -> set[str]:
        names: set[str] = {"source", *_RESERVED_PARAMS}
        for f in self.filterable:
            if f.sub_fields:
                names.update(sf.name for sf in f.sub_fields)
            else:
                names.add(f.name)
        return names

    def filterable_field_names(self) -> list[str]:
        """Every filterable field's own name -- for a plain field this is
        its `record_fields` key directly; for an array-of-object field
        (`levels`) it's the *combined* row's key (`flatten_fields` inserts
        one keyed by the parent name too, e.g. `"levels" -> "Cleric 3"`).
        Either way this is exactly the set of keys an item's display
        `facets` (batch B9 acceptance criterion 2) can read straight off
        `record_fields` without loading the full record JSON."""
        return [f.name for f in self.filterable]


def load_type_browse_schema(registry: Registry, type_name: str) -> TypeBrowseSchema:
    schema = registry.load_type_schema(type_name)
    filterable: list[FilterableField] = []
    sortable: list[str] = ["name"]

    for field_name, prop in schema.get("properties", {}).items():
        hint = prop.get("x-ui") or {}
        if hint.get("sortable"):
            sortable.append(field_name)
        if not hint.get("filterable"):
            continue

        sub_fields: list[SubField] = []
        items = prop.get("items")
        prop_type = prop.get("type")
        prop_types = prop_type if isinstance(prop_type, list) else [prop_type]
        if "array" in prop_types and isinstance(items, dict) and items.get("type") == "object":
            # Sorted by sub-property name (not schema declaration order): the
            # canonical order this module and `build_db.runner.flatten_fields`
            # both independently agree on for the combined row (see module
            # docstring and `flatten_fields`'s docstring).
            for subname in sorted(items.get("properties") or {}):
                subschema = (items.get("properties") or {})[subname]
                subtype = subschema.get("type")
                types = subtype if isinstance(subtype, list) else [subtype]
                numeric = any(t in ("integer", "number") for t in types)
                sub_fields.append(SubField(name=subname, parent=field_name, numeric=numeric))

        filterable.append(
            FilterableField(
                name=field_name, label=hint.get("label", field_name), sub_fields=sub_fields
            )
        )

    return TypeBrowseSchema(type_name=type_name, filterable=filterable, sortable=sortable)


@dataclass(frozen=True)
class ParsedQuery:
    #: Raw query param name (a simple filterable field, a sub-param, or
    #: "source") -> its (possibly repeated) values, in the order given.
    filters: dict[str, list[str]]
    sort_field: str
    sort_desc: bool
    page: int
    page_size: int


def parse_query(schema: TypeBrowseSchema, multi_params: list[tuple[str, str]]) -> ParsedQuery:
    """Parse a request's raw (possibly-repeated) query params against
    `schema`, raising `BrowseError` for anything not filterable/sortable/
    `source`/`sort`/`page`/`page_size` -- naming the bad key -- or for a
    non-integer/out-of-range `page`/`page_size` or unknown `sort` field."""
    allowed = schema.allowed_params()
    filters: dict[str, list[str]] = {}
    sort_raw = "name"
    page = 1
    page_size = DEFAULT_PAGE_SIZE

    for key, value in multi_params:
        if key not in allowed:
            raise BrowseError(f"unknown query parameter: {key!r}")
        if key == "sort":
            sort_raw = value
        elif key == "page":
            try:
                page = int(value)
            except ValueError as exc:
                raise BrowseError("page must be an integer") from exc
        elif key == "page_size":
            try:
                page_size = int(value)
            except ValueError as exc:
                raise BrowseError("page_size must be an integer") from exc
        else:
            filters.setdefault(key, []).append(value)

    sort_desc = sort_raw.startswith("-")
    sort_field = sort_raw[1:] if sort_desc else sort_raw
    if sort_field not in schema.sortable:
        raise BrowseError(f"unknown sort field: {sort_field!r}")

    if page < 1:
        raise BrowseError("page must be >= 1")
    if page_size < 1:
        raise BrowseError("page_size must be >= 1")
    page_size = min(page_size, MAX_PAGE_SIZE)

    return ParsedQuery(
        filters=filters, sort_field=sort_field, sort_desc=sort_desc, page=page, page_size=page_size
    )


def _normalize_numeric_str(value: str) -> str:
    """`"3"` and `"3.0"` must both match a stored combined value built from
    the integer `3` (`flatten_fields` stringifies with plain `str()`, so an
    integer level is `"3"`, never `"3.0"`)."""
    try:
        parsed = float(value)
    except ValueError:
        return value
    if parsed.is_integer():
        return str(int(parsed))
    return str(parsed)


def _num_variants(value: str) -> list[float]:
    try:
        return [float(value)]
    except ValueError:
        return []


def _cross_product(value_lists: list[list[str]]) -> list[str]:
    combos = [""]
    for values in value_lists:
        combos = [f"{combo} {value}".strip() for combo in combos for value in values]
    return combos


def build_filter_clauses(
    schema: TypeBrowseSchema, filters: dict[str, list[str]], *, exclude: str | None = None
) -> tuple[list[str], list[Any]]:
    """SQL fragments (each an `r.id IN (...)`/`r.book_id IN (...)` clause,
    meant to be ANDed together by the caller) plus their bound params, for
    every filter in `filters` except `exclude` (used by facet counting to
    drop a field's own filter while keeping every other one -- see module
    docstring and `compute_facets`)."""
    clauses: list[str] = []
    params: list[Any] = []

    if "source" in filters and exclude != "source":
        values = filters["source"]
        placeholders = ", ".join("?" for _ in values)
        clauses.append(f"r.book_id IN ({placeholders})")
        params.extend(values)

    for f in schema.filterable:
        if f.sub_fields:
            continue
        if f.name not in filters or f.name == exclude:
            continue
        values = filters[f.name]
        placeholders = ", ".join("?" for _ in values)
        clauses.append(
            "r.id IN (SELECT record_id FROM record_fields "
            f"WHERE key = ? AND LOWER(text_value) IN ({placeholders}))"
        )
        params.append(f.name)
        params.extend(v.lower() for v in values)

    for parent_field in schema.filterable:
        if not parent_field.sub_fields:
            continue
        given = [sf for sf in parent_field.sub_fields if sf.name in filters and sf.name != exclude]
        if not given:
            continue

        if len(given) >= 2 and len(given) == len(parent_field.sub_fields):
            value_lists: list[list[str]] = []
            for sf in parent_field.sub_fields:
                values = filters[sf.name]
                value_lists.append(
                    [_normalize_numeric_str(v) for v in values] if sf.numeric else values
                )
            combos = _cross_product(value_lists)
            placeholders = ", ".join("?" for _ in combos)
            clauses.append(
                "r.id IN (SELECT record_id FROM record_fields "
                f"WHERE key = ? AND LOWER(text_value) IN ({placeholders}))"
            )
            params.append(parent_field.name)
            params.extend(c.lower() for c in combos)
        else:
            for sf in given:
                values = filters[sf.name]
                key = f"{sf.parent}.{sf.name}"
                if sf.numeric:
                    nums = [n for v in values for n in _num_variants(v)]
                    placeholders = ", ".join("?" for _ in nums)
                    clauses.append(
                        "r.id IN (SELECT record_id FROM record_fields "
                        f"WHERE key = ? AND num_value IN ({placeholders}))"
                    )
                    params.append(key)
                    params.extend(nums)
                else:
                    placeholders = ", ".join("?" for _ in values)
                    clauses.append(
                        "r.id IN (SELECT record_id FROM record_fields "
                        f"WHERE key = ? AND LOWER(text_value) IN ({placeholders}))"
                    )
                    params.append(key)
                    params.extend(v.lower() for v in values)

    return clauses, params


def _order_by_sql(parsed: ParsedQuery) -> tuple[str, list[Any]]:
    direction = "DESC" if parsed.sort_desc else "ASC"
    if parsed.sort_field == "name":
        return f"ORDER BY r.name COLLATE NOCASE {direction}, r.id {direction}", []
    # No current type schema marks a field sortable, but a correlated
    # subquery keeps this generic rather than hard-coded to "name" alone.
    sort_expr = (
        "(SELECT COALESCE(text_value, num_value) FROM record_fields "
        "WHERE record_id = r.id AND key = ?)"
    )
    return f"ORDER BY {sort_expr} {direction}, r.id {direction}", [parsed.sort_field]


def build_list_sql(
    schema: TypeBrowseSchema, parsed: ParsedQuery
) -> tuple[str, list[Any], str, list[Any]]:
    """Returns `(count_sql, count_params, page_sql, page_params)`."""
    clauses, filter_params = build_filter_clauses(schema, parsed.filters)
    where_sql = " AND ".join(["r.type = ?", "r.canonical = 1", *clauses])
    base_params: list[Any] = [schema.type_name, *filter_params]

    count_sql = f"SELECT COUNT(*) AS n FROM records r WHERE {where_sql}"

    order_sql, order_params = _order_by_sql(parsed)
    offset = (parsed.page - 1) * parsed.page_size
    page_sql = (
        "SELECT r.id, r.type, r.name, r.slug, r.book_id, r.json FROM records r "
        f"WHERE {where_sql} {order_sql} LIMIT ? OFFSET ?"
    )
    page_params = [*base_params, *order_params, parsed.page_size, offset]

    return count_sql, base_params, page_sql, page_params


def _item_facets(
    conn: sqlite3.Connection, ids: list[str], field_names: list[str]
) -> dict[str, dict[str, list[str]]]:
    if not ids or not field_names:
        return {}
    id_placeholders = ", ".join("?" for _ in ids)
    key_placeholders = ", ".join("?" for _ in field_names)
    rows = conn.execute(
        "SELECT id, record_id, key, text_value FROM record_fields "
        f"WHERE record_id IN ({id_placeholders}) AND key IN ({key_placeholders}) "
        "ORDER BY id",
        [*ids, *field_names],
    )
    out: dict[str, dict[str, list[str]]] = {}
    for row in rows:
        per_record = out.setdefault(row["record_id"], {})
        per_record.setdefault(row["key"], []).append(row["text_value"])
    return out


def list_records(
    conn: sqlite3.Connection, type_name: str, schema: TypeBrowseSchema, parsed: ParsedQuery
) -> dict[str, Any]:
    count_sql, count_params, page_sql, page_params = build_list_sql(schema, parsed)
    total = conn.execute(count_sql, count_params).fetchone()[0]
    rows = list(conn.execute(page_sql, page_params))

    ids = [row["id"] for row in rows]
    facets_by_id = _item_facets(conn, ids, schema.filterable_field_names())

    items = []
    for row in rows:
        record = json.loads(row["json"])
        items.append(
            {
                "id": row["id"],
                "type": row["type"],
                "name": row["name"],
                "slug": row["slug"],
                "book_id": row["book_id"],
                "citation": record.get("citation"),
                "facets": facets_by_id.get(row["id"], {}),
            }
        )

    return {
        "type": type_name,
        "total": total,
        "page": parsed.page,
        "page_size": parsed.page_size,
        "items": items,
    }


def _facet_values_simple(
    conn: sqlite3.Connection,
    schema: TypeBrowseSchema,
    filters: dict[str, list[str]],
    field_name: str,
) -> list[dict[str, Any]]:
    clauses, params = build_filter_clauses(schema, filters, exclude=field_name)
    where_sql = " AND ".join(["r.type = ?", "r.canonical = 1", *clauses])
    sql = (
        "SELECT rf.text_value AS value, COUNT(DISTINCT rf.record_id) AS cnt "
        "FROM record_fields rf JOIN records r ON r.id = rf.record_id "
        f"WHERE rf.key = ? AND {where_sql} "
        "GROUP BY rf.text_value ORDER BY cnt DESC, value ASC"
    )
    rows = conn.execute(sql, [field_name, schema.type_name, *params])
    return [{"value": row["value"], "count": row["cnt"]} for row in rows]


def _facet_values_subfield(
    conn: sqlite3.Connection, schema: TypeBrowseSchema, filters: dict[str, list[str]], sf: SubField
) -> list[dict[str, Any]]:
    clauses, params = build_filter_clauses(schema, filters, exclude=sf.name)
    where_sql = " AND ".join(["r.type = ?", "r.canonical = 1", *clauses])
    key = f"{sf.parent}.{sf.name}"
    value_col = "rf.num_value" if sf.numeric else "rf.text_value"
    sql = (
        f"SELECT {value_col} AS value, COUNT(DISTINCT rf.record_id) AS cnt "
        "FROM record_fields rf JOIN records r ON r.id = rf.record_id "
        f"WHERE rf.key = ? AND {where_sql} "
        f"GROUP BY {value_col} ORDER BY cnt DESC, value ASC"
    )
    rows = conn.execute(sql, [key, schema.type_name, *params])
    results = []
    for row in rows:
        value = row["value"]
        if sf.numeric and value is not None:
            value = _normalize_numeric_str(str(value))
        results.append({"value": value, "count": row["cnt"]})
    return results


def _facet_values_source(
    conn: sqlite3.Connection, schema: TypeBrowseSchema, filters: dict[str, list[str]]
) -> list[dict[str, Any]]:
    clauses, params = build_filter_clauses(schema, filters, exclude="source")
    where_sql = " AND ".join(["r.type = ?", "r.canonical = 1", *clauses])
    sql = (
        "SELECT r.book_id AS value, COUNT(*) AS cnt, "
        "COALESCE(b.short_title, b.title) AS label "
        "FROM records r LEFT JOIN books b ON b.book_id = r.book_id "
        f"WHERE {where_sql} "
        "GROUP BY r.book_id ORDER BY cnt DESC, value ASC"
    )
    rows = conn.execute(sql, [schema.type_name, *params])
    return [{"value": row["value"], "count": row["cnt"], "label": row["label"]} for row in rows]


def compute_facets(
    conn: sqlite3.Connection,
    type_name: str,
    schema: TypeBrowseSchema,
    filters: dict[str, list[str]],
) -> dict[str, Any]:
    """Every filterable field's distinct values + counts (one facet per
    sub-property for an array-of-object field, never the combined row),
    plus `source`. Each facet's counts honor every current filter EXCEPT its
    own field (see module docstring)."""
    facets: list[dict[str, Any]] = []

    for f in schema.filterable:
        if not f.sub_fields:
            facets.append(
                {
                    "field": f.name,
                    "label": f.label,
                    "values": _facet_values_simple(conn, schema, filters, f.name),
                }
            )
        else:
            for sf in f.sub_fields:
                facets.append(
                    {
                        "field": sf.name,
                        "label": sf.name.replace("_", " ").title(),
                        "values": _facet_values_subfield(conn, schema, filters, sf),
                    }
                )

    facets.append(
        {
            "field": "source",
            "label": "Source",
            "values": _facet_values_source(conn, schema, filters),
        }
    )

    return {"type": type_name, "facets": facets}
