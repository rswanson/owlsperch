"""The precedence pass for `owlsperch build-db` (spec 4.7, batch B11).

Run once per `build_db()` call, right after `_apply_superseding` and before
the `names_fts` insert (see `build_db.runner`'s pass order, design decision
D12): errata and update entries are applied to their targets, a Rules
Compendium `rules_section` becomes canonical over any other book's matching
topic, and among what remains the latest `published` book wins. All three
steps only UPDATE the `records` table's own `canonical`/`variant_of`/
`applied_overrides` columns in the connection they are handed -- this
module never reads or writes a segment or record FILE on disk, exactly like
`_apply_superseding` before it (build-db is a read-only build step with
respect to the pipeline's own on-disk records). `write_precedence_report`
and `write_unmatched_overrides` are separate, disk-writing steps the caller
runs AFTER the database file has been atomically renamed into place (D12),
using the `PrecedenceResult` this module returns.

See the batch brief's design decisions D13-D15 for the exact matching and
grouping rules this implements.

Batch B10c-mand17 adds two FALLBACKS to step (1)'s target matching, for the
B10c x B11 interaction the 2026-09-21 class-quality judgement's finding E
surfaced: a class record absorbs its own feature-level `rules_section`
fragments (`_apply_superseding` sets their `superseded_by`), and every
candidate pool here is `records WHERE superseded_by IS NULL` (D13a), so an
erratum targeting the druid's "Wild Shape" or the paladin's "Special Mount"
matched nothing at all and its correction never reached the class page. See
`_apply_overrides` for the exact order and the (a)/(b) precedence.
"""

from __future__ import annotations

import functools
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from sqlite3 import Connection
from typing import Any

from owlsperch.fsutil import atomic_write_text
from owlsperch.supersede import GENERIC_CLASS_SECTION_HEADINGS, normalize_heading
from owlsperch.validate.checks import slugify

#: The book_id of the Rules Compendium in `pipeline/manifest.yaml` -- a
#: module constant AND an injectable argument (`apply_precedence`'s
#: `rules_compendium_book_id`) so tests can use their own fixture book id.
RULES_COMPENDIUM_BOOK_ID = "rules-compendium"

#: The two override entry types (never override TARGETS -- design decision
#: D13b -- but themselves ordinary, searchable records that still take
#: part in latest-wins grouping like any other record: D13d).
_OVERRIDE_TYPES = ("errata_entry", "update_entry")

#: How many "candidates considered" to keep per unmatched override, for the
#: report and the human/ file (design decision D13b.3).
_MAX_CANDIDATES = 5


@dataclass(frozen=True)
class UnmatchedOverride:
    """One errata/update entry whose target could not be matched (design
    decisions D13b.3, D14, D15)."""

    entry_id: str
    entry_name: str
    entry_book_id: str
    entry_slug: str
    target_book: str
    target_page: int | None
    target_name: str
    candidates: list[dict[str, Any]]
    reason: str


@dataclass(frozen=True)
class UnmatchedRcSection:
    """One Rules Compendium `rules_section` record whose topic matched no
    other book's section (design decisions D13c, D15) -- it stays
    canonical; nothing is overridden."""

    record_id: str
    topic: str


@dataclass
class PrecedenceResult:
    overrides_applied: int = 0
    records_with_overrides: int = 0
    unmatched_overrides: list[UnmatchedOverride] = field(default_factory=list)
    rc_overrides: int = 0
    unmatched_rc: list[UnmatchedRcSection] = field(default_factory=list)
    variants: int = 0
    #: Every book_id an errata_entry/update_entry record in this build came
    #: from, matched or not -- `write_unmatched_overrides` clears each of
    #: their `human/<book_id>/overrides/*.json` directories first (D14), so
    #: a since-fixed entry doesn't linger as a stale human/ file.
    override_source_books: set[str] = field(default_factory=set)


@dataclass
class _PoolRecord:
    id: str
    type: str
    slug: str
    name: str
    book_id: str
    fields: dict[str, Any]
    pages: list[int]


def _load_pool(conn: Connection) -> list[_PoolRecord]:
    """Design decision D13a: every candidate for every precedence step --
    `records WHERE superseded_by IS NULL`. A record a class span already
    absorbed is not a printing of anything and takes no further part."""
    rows = conn.execute(
        "SELECT id, type, slug, name, book_id, json FROM records WHERE superseded_by IS NULL"
    ).fetchall()
    pool = []
    for record_id, type_name, slug, name, book_id, raw_json in rows:
        record = json.loads(raw_json)
        fields = record.get("fields")
        pages = record.get("pages")
        pool.append(
            _PoolRecord(
                id=record_id,
                type=type_name,
                slug=slug,
                name=name,
                book_id=book_id,
                fields=fields if isinstance(fields, dict) else {},
                pages=[p for p in pages if isinstance(p, int)] if isinstance(pages, list) else [],
            )
        )
    return pool


@dataclass
class _SupersededRecord:
    """One `records` row a class span already absorbed (`superseded_by` set)
    -- deliberately NOT part of any precedence pool (D13a), but kept
    alongside it so step (1)'s fallback (a) can follow a matched fragment to
    the record that superseded it (batch B10c-mand17)."""

    id: str
    type: str
    slug: str
    name: str
    book_id: str
    pages: list[int]
    superseded_by: str


def _load_superseded(conn: Connection) -> list[_SupersededRecord]:
    rows = conn.execute(
        "SELECT id, type, slug, name, book_id, json, superseded_by FROM records "
        "WHERE superseded_by IS NOT NULL"
    ).fetchall()
    superseded = []
    for record_id, type_name, slug, name, book_id, raw_json, superseded_by in rows:
        record = json.loads(raw_json)
        pages = record.get("pages")
        superseded.append(
            _SupersededRecord(
                id=record_id,
                type=type_name,
                slug=slug,
                name=name,
                book_id=book_id,
                pages=[p for p in pages if isinstance(p, int)] if isinstance(pages, list) else [],
                superseded_by=superseded_by,
            )
        )
    return superseded


def _superseding_root(record_id: str, superseded_by: dict[str, str]) -> str:
    """Follow `superseded_by` pointers from `record_id` to the end of the
    chain -- normally exactly one hop (a fragment -> its class), but walked
    and cycle-guarded exactly like `_root_of` does for `variant_of`, so a
    hand-edited or chained database can't loop or `KeyError`."""
    seen: set[str] = set()
    current = record_id
    while True:
        nxt = superseded_by.get(current)
        if nxt is None or nxt == current or current in seen:
            return current
        seen.add(current)
        current = nxt


def _class_heading_names(record: _PoolRecord) -> set[str]:
    """The normalized printed headings a `class`/`prestige_class` record owns:
    every `fields.class_features[].name` and every
    `fields.description_sections[].heading` (batch B10c-mand17), normalized
    with `owlsperch.supersede.normalize_heading` -- so "Wild Shape (Su)" and
    an erratum's bare "Wild Shape" are one name -- and with the shared
    `GENERIC_CLASS_SECTION_HEADINGS` set subtracted, exactly as
    `build_db.runner._class_owned_names` does for the superseding pass (a
    separate function computing the same heading set for its own purpose, not
    the same one): "Alignment"/"Races"/"Class Features" are printed by every
    class and so are never evidence that THIS class is an erratum's target."""
    names: set[str] = set()
    for feature in record.fields.get("class_features") or []:
        if isinstance(feature, dict):
            names.add(normalize_heading(str(feature.get("name", ""))))
    for section in record.fields.get("description_sections") or []:
        if isinstance(section, dict):
            names.add(normalize_heading(str(section.get("heading", ""))))
    names.discard("")
    return names - GENERIC_CLASS_SECTION_HEADINGS


def _first_word(name: str) -> str:
    words = name.strip().split()
    return words[0].casefold() if words else ""


def _names_overlap(a: str, b: str) -> bool:
    """Whether normalized `a` contains, or is contained by, normalized `b`
    (design decision D13b.2)."""
    na, nb = slugify(a), slugify(b)
    if not na or not nb:
        return False
    return na in nb or nb in na


def _candidate_dict(record: _PoolRecord) -> dict[str, Any]:
    return {"id": record.id, "type": record.type, "name": record.name, "pages": record.pages}


def _superseded_fallback_targets(
    candidates: list[_SupersededRecord],
    target_name: str,
    target_page: int | None,
    superseded_by: dict[str, str],
    live_target_ids: set[str],
) -> list[str]:
    """Batch B10c-mand17, step (1) fallback (a): the same NAME-then-PAGE
    match the live pool got, run over the SUPERSEDED records of the target
    book, with every hit resolved to the record that superseded it
    (`_superseding_root`). A resolved root only counts when it is itself a
    live, non-override target (in `live_target_ids`), so a chain ending
    outside the pool -- or a fragment whose superseding record was skipped
    or is itself an errata entry -- yields no match at all rather than a
    dangling `applied_overrides` id. The narrowing discipline is the live
    match's: a page hit needs either exactly one name-overlapping record or
    exactly one record on the page, never a silent apply-to-all."""
    matches = [s for s in candidates if s.slug == slugify(target_name)]
    if not matches and isinstance(target_page, int):
        on_page = [s for s in candidates if target_page in s.pages]
        narrower = [s for s in on_page if _names_overlap(s.name, target_name)]
        if len(narrower) == 1:
            matches = narrower
        elif not narrower and len(on_page) == 1:
            matches = on_page

    resolved: list[str] = []
    for fragment in matches:
        root = _superseding_root(fragment.id, superseded_by)
        if root != fragment.id and root in live_target_ids and root not in resolved:
            resolved.append(root)
    return resolved


def _class_heading_fallback_targets(
    targets: list[_PoolRecord], target_name: str, target_page: int | None
) -> list[str]:
    """Batch B10c-mand17, step (1) fallback (b): an entry whose normalized
    `target_name` IS one of a same-book `class`/`prestige_class` record's own
    printed feature/section headings is applied to that class record -- the
    case where no `rules_section` fragment for that feature exists at all
    (the paladin's "Special Mount", the druid's "Animal Companion"), so
    fallback (a) has nothing to follow.

    A given `target_page` must fall inside the class record's own pages;
    with no page, every class in the book is eligible. Either way the match
    must be UNIQUE -- a generic heading several classes print ("Spells",
    "Alignment") resolves to nothing and the entry stays unmatched, rather
    than being applied to an arbitrary one of them."""
    normalized = normalize_heading(target_name)
    if not normalized:
        return []
    classes = [
        t
        for t in targets
        if t.type in ("class", "prestige_class") and normalized in _class_heading_names(t)
    ]
    if isinstance(target_page, int):
        classes = [t for t in classes if target_page in t.pages]
    return [classes[0].id] if len(classes) == 1 else []


def _apply_overrides(
    pool: list[_PoolRecord],
    state: dict[str, dict[str, Any]],
    applied_overrides: dict[str, list[str]],
    superseded: list[_SupersededRecord],
) -> tuple[int, list[UnmatchedOverride], set[str]]:
    """Design decision D13b: apply every errata_entry/update_entry to its
    target(s). Returns (overrides_applied, unmatched, source_books).

    Match order per entry, first hit winning:

    1. the live pool's NAME match (`slug == slugify(target_name)`),
    2. the live pool's PAGE match (unambiguous only),
    3. batch B10c-mand17 fallback (a): the same name/page match over the
       book's SUPERSEDED records, resolved to whatever superseded them
       (`_superseded_fallback_targets`),
    4. batch B10c-mand17 fallback (b): a unique same-book class/
       prestige_class record whose own `class_features[].name`/
       `description_sections[].heading` normalizes to `target_name`
       (`_class_heading_fallback_targets`).

    Steps 3 and 4 run only when no live record matched at ALL -- an
    AMBIGUOUS live page match (two same-page candidates whose names overlap
    the target) keeps its existing "unmatched, reported with its candidates"
    outcome rather than falling through to a class-level apply.

    (a) is tried before (b) deliberately: when both hit they name the same
    class record (the fragment was superseded BY that class precisely
    because the class owns a feature of that name -- see
    `build_db.runner._class_owned_names`), and where they could disagree the
    fragment's own stored `superseded_by` pointer is the harder evidence of
    which record actually absorbed that printed text. An entry matching none
    of the four is unmatched exactly as before."""
    entries = sorted(
        (r for r in pool if r.type in _OVERRIDE_TYPES), key=lambda r: (r.book_id, r.id)
    )
    targets_by_book: dict[str, list[_PoolRecord]] = {}
    for r in pool:
        if r.type in _OVERRIDE_TYPES:
            continue
        targets_by_book.setdefault(r.book_id, []).append(r)

    superseded_by_id = {s.id: s.superseded_by for s in superseded}
    superseded_by_book: dict[str, list[_SupersededRecord]] = {}
    for s in superseded:
        superseded_by_book.setdefault(s.book_id, []).append(s)
    live_target_ids = {r.id for r in pool if r.type not in _OVERRIDE_TYPES}

    overrides_applied = 0
    unmatched: list[UnmatchedOverride] = []
    source_books: set[str] = set()

    for entry in entries:
        source_books.add(entry.book_id)
        target_book = entry.fields.get("target_book")
        target_name = entry.fields.get("target_name")
        target_page = entry.fields.get("target_page")
        if not isinstance(target_book, str) or not isinstance(target_name, str):
            unmatched.append(
                UnmatchedOverride(
                    entry_id=entry.id,
                    entry_name=entry.name,
                    entry_book_id=entry.book_id,
                    entry_slug=entry.slug,
                    target_book=target_book if isinstance(target_book, str) else "",
                    target_page=target_page if isinstance(target_page, int) else None,
                    target_name=target_name if isinstance(target_name, str) else "",
                    candidates=[],
                    reason="missing_target_fields",
                )
            )
            continue

        targets = targets_by_book.get(target_book, [])
        name_matches = [t for t in targets if t.slug == slugify(target_name)]
        if name_matches:
            for t in name_matches:
                applied_overrides[t.id].append(entry.id)
            overrides_applied += len(name_matches)
            continue

        on_page = (
            [t for t in targets if target_page in t.pages] if isinstance(target_page, int) else []
        )
        chosen: list[_PoolRecord] = []
        ambiguous: list[_PoolRecord] = []
        if isinstance(target_page, int):
            narrower = [t for t in on_page if _names_overlap(t.name, target_name)]
            if len(narrower) == 1:
                chosen = narrower
            elif len(narrower) > 1:
                # Several same-page records' names overlap the target name
                # (e.g. two records that both start with a shared short
                # word) -- ambiguous, never silently applied to all of
                # them (see module docstring / the reviewer finding this
                # guards against).
                ambiguous = narrower
            elif len(on_page) == 1:
                chosen = on_page

        if chosen:
            for t in chosen:
                applied_overrides[t.id].append(entry.id)
            overrides_applied += len(chosen)
            continue

        # Batch B10c-mand17: no LIVE record matched at all -- try the two
        # superseded/class-feature fallbacks, (a) then (b). Deliberately NOT
        # when the live page match was AMBIGUOUS: two live candidates on the
        # target's own page is a "needs a human" report, and quietly applying
        # the entry to a class record instead would bury that ambiguity.
        fallback_ids: list[str] = []
        if not ambiguous:
            fallback_ids = _superseded_fallback_targets(
                superseded_by_book.get(target_book, []),
                target_name,
                target_page if isinstance(target_page, int) else None,
                superseded_by_id,
                live_target_ids,
            ) or _class_heading_fallback_targets(
                targets, target_name, target_page if isinstance(target_page, int) else None
            )
        if fallback_ids:
            for target_id in fallback_ids:
                # Every fallback id is a live pool record by construction
                # (`live_target_ids` / `targets`), so plain indexing is right:
                # a KeyError here means that invariant broke and must not be
                # papered over with a fresh list nobody writes to the DB.
                target_overrides = applied_overrides[target_id]
                if entry.id not in target_overrides:
                    target_overrides.append(entry.id)
            overrides_applied += len(fallback_ids)
            continue

        if ambiguous:
            candidates = [_candidate_dict(t) for t in ambiguous[:_MAX_CANDIDATES]]
        elif isinstance(target_page, int):
            candidates = [_candidate_dict(t) for t in on_page[:_MAX_CANDIDATES]]
        else:
            first_word = _first_word(target_name)
            word_matches = (
                [t for t in targets if _first_word(t.name) == first_word] if first_word else []
            )
            candidates = [_candidate_dict(t) for t in word_matches[:_MAX_CANDIDATES]]

        unmatched.append(
            UnmatchedOverride(
                entry_id=entry.id,
                entry_name=entry.name,
                entry_book_id=entry.book_id,
                entry_slug=entry.slug,
                target_book=target_book,
                target_page=target_page if isinstance(target_page, int) else None,
                target_name=target_name,
                candidates=candidates,
                reason="no_unambiguous_target_match",
            )
        )

    return overrides_applied, unmatched, source_books


def _apply_rules_compendium(
    pool: list[_PoolRecord], state: dict[str, dict[str, Any]], rules_compendium_book_id: str
) -> tuple[int, list[UnmatchedRcSection]]:
    """Design decision D13c: group pool `rules_section` records by
    `slugify(fields.topic)`; a group containing an RC record makes that
    record (lowest id) canonical over every other-book record in the
    group."""
    groups: dict[str, list[_PoolRecord]] = {}
    topic_text: dict[str, str] = {}
    for r in pool:
        if r.type != "rules_section":
            continue
        topic = r.fields.get("topic")
        if not isinstance(topic, str) or not topic.strip():
            continue
        key = slugify(topic)
        if not key:
            continue
        groups.setdefault(key, []).append(r)
        topic_text.setdefault(key, topic)

    demoted = 0
    unmatched: list[UnmatchedRcSection] = []
    for key in sorted(groups):
        group = groups[key]
        rc_in_group = sorted(
            (r for r in group if r.book_id == rules_compendium_book_id), key=lambda r: r.id
        )
        if not rc_in_group:
            continue
        winner = rc_in_group[0]
        others = [r for r in group if r.book_id != rules_compendium_book_id]
        if others:
            for o in others:
                state[o.id]["canonical"] = 0
                state[o.id]["variant_of"] = winner.id
                demoted += 1
        else:
            for rc_record in rc_in_group:
                unmatched.append(UnmatchedRcSection(record_id=rc_record.id, topic=topic_text[key]))

    return demoted, unmatched


def _latest_wins_compare(a: _PoolRecord, b: _PoolRecord, published: dict[str, str | None]) -> int:
    """Design decision D13d's total order: `published` DESC (missing sorts
    last), then `book_id` ASC, then `id` ASC -- deterministic regardless of
    SQLite row order or dict iteration order."""
    pa, pb = published.get(a.book_id), published.get(b.book_id)
    if pa != pb:
        if pa is None:
            return 1
        if pb is None:
            return -1
        return -1 if pa > pb else 1
    if a.book_id != b.book_id:
        return -1 if a.book_id < b.book_id else 1
    if a.id != b.id:
        return -1 if a.id < b.id else 1
    return 0


def _apply_latest_wins(
    pool: list[_PoolRecord], state: dict[str, dict[str, Any]], published: dict[str, str | None]
) -> int:
    """Design decision D13d: among records still `variant_of IS NULL AND
    canonical = 1`, group by `(type, slug)`; the latest `published` wins,
    others become variants of it."""
    groups: dict[tuple[str, str], list[_PoolRecord]] = {}
    for r in pool:
        st = state[r.id]
        if st["variant_of"] is not None or st["canonical"] != 1:
            continue
        groups.setdefault((r.type, r.slug), []).append(r)

    def _compare(a: _PoolRecord, b: _PoolRecord) -> int:
        return _latest_wins_compare(a, b, published)

    variants = 0
    for key in sorted(groups):
        group = groups[key]
        if len(group) <= 1:
            continue
        ordered = sorted(group, key=functools.cmp_to_key(_compare))
        winner = ordered[0]
        for loser in ordered[1:]:
            state[loser.id]["canonical"] = 0
            state[loser.id]["variant_of"] = winner.id
            variants += 1
    return variants


def _root_of(state: dict[str, dict[str, Any]], record_id: str) -> str:
    """Follow `variant_of` pointers from `record_id` to the end of the
    chain, guarding against a cycle (`seen`) and against a `variant_of`
    that names an id not present in `state` at all (treated as terminal,
    so a dangling pointer can't `KeyError`)."""
    seen: set[str] = set()
    current = record_id
    while True:
        entry = state.get(current)
        if entry is None:
            return current
        variant_of = entry["variant_of"]
        if variant_of is None or current in seen:
            return current
        seen.add(current)
        current = variant_of


def _flatten_variant_chains(pool: list[_PoolRecord], state: dict[str, dict[str, Any]]) -> None:
    """B11 fix: the Rules Compendium pass (D13c) can demote a record to an
    RC winner, and latest-wins (D13d) -- run afterward, over records still
    `variant_of IS NULL` -- can then separately demote that same RC winner
    to a later-published record sharing its `(type, slug)` but not its
    normalized topic (so it was never grouped with the RC record and
    remained eligible there). Left alone this produces a 2-hop
    `variant_of` chain (book-a -> rules-compendium -> book-c), but
    `server.owlsperch_server.app._resolve_variants` (and any other reader
    of the stored graph) is designed around a single hop (D19), so book-a's
    printing would never surface under book-c's "Other printings" and, via
    `/records/{type}/{slug}`'s `canonical = 1` lookup resolving the RC row
    first, would be unreachable by URL at all. This rewrites every
    non-root `variant_of` pointer to the ROOT of its own chain (via
    `_root_of`), so the stored graph is always exactly one hop deep and no
    reader has to walk it."""
    for r in pool:
        variant_of = state[r.id]["variant_of"]
        if variant_of is None:
            continue
        root = _root_of(state, r.id)
        if root != r.id and root != variant_of:
            state[r.id]["variant_of"] = root


def _merge_overrides_to_winners(
    pool: list[_PoolRecord],
    state: dict[str, dict[str, Any]],
    applied_overrides: dict[str, list[str]],
) -> None:
    """B11 fix: an errata/update override is matched against one specific
    record row (D13b), but that exact row can later be demoted to a
    variant by the Rules Compendium pass (D13c) or latest-wins (D13d) --
    the two passes this module runs AFTER the override match. Left alone,
    the override stays recorded only on the now-demoted row: the canonical
    winner sharing the same (type, slug) is what `/records/{type}/{slug}`
    always resolves (its `canonical = 1` query succeeds before the
    fallback to `canonical = 0` ever runs), so the override becomes
    permanently unreachable even though `overrides_applied` reports it as
    applied. This copies every demoted record's own overrides onto its
    ultimate canonical winner -- following `variant_of` to the end via the
    shared `_root_of` helper (by the time this runs, `_flatten_variant_chains`
    has already made that at most one hop for every record, but the walk
    is harmless either way) -- de-duplicated but keeping first-seen order.
    The demoted record keeps its own list too, so a direct look at a
    superseded/variant row (the D12 direct-URL case) still shows exactly
    what was applied to it."""
    for r in pool:
        own = applied_overrides.get(r.id) or []
        if not own:
            continue
        root = _root_of(state, r.id)
        if root == r.id:
            continue
        target_list = applied_overrides.setdefault(root, [])
        for entry_id in own:
            if entry_id not in target_list:
                target_list.append(entry_id)


def apply_precedence(
    conn: Connection, *, rules_compendium_book_id: str = RULES_COMPENDIUM_BOOK_ID
) -> PrecedenceResult:
    """Run the full precedence pass (design decisions D13a-e) against
    `conn`, updating `records.canonical`/`variant_of`/`applied_overrides`
    in place, and return a `PrecedenceResult` summarizing what happened for
    `write_precedence_report`/`write_unmatched_overrides` to use afterward."""
    pool = _load_pool(conn)
    superseded = _load_superseded(conn)
    state: dict[str, dict[str, Any]] = {r.id: {"canonical": 1, "variant_of": None} for r in pool}
    applied_overrides: dict[str, list[str]] = {r.id: [] for r in pool}

    overrides_applied, unmatched_overrides, source_books = _apply_overrides(
        pool, state, applied_overrides, superseded
    )
    records_with_overrides = sum(1 for ids in applied_overrides.values() if ids)

    published: dict[str, str | None] = {r.book_id: None for r in pool}
    rows = conn.execute("SELECT book_id, published FROM books").fetchall()
    for book_id, pub in rows:
        published[book_id] = pub

    rc_overrides, unmatched_rc = _apply_rules_compendium(pool, state, rules_compendium_book_id)
    variants = _apply_latest_wins(pool, state, published)
    _flatten_variant_chains(pool, state)
    _merge_overrides_to_winners(pool, state, applied_overrides)

    for r in pool:
        st = state[r.id]
        overrides = applied_overrides.get(r.id) or []
        conn.execute(
            "UPDATE records SET canonical = ?, variant_of = ?, applied_overrides = ? WHERE id = ?",
            (st["canonical"], st["variant_of"], json.dumps(overrides), r.id),
        )

    return PrecedenceResult(
        overrides_applied=overrides_applied,
        records_with_overrides=records_with_overrides,
        unmatched_overrides=unmatched_overrides,
        rc_overrides=rc_overrides,
        unmatched_rc=unmatched_rc,
        variants=variants,
        override_source_books=source_books,
    )


def _human_overrides_dir(data_dir: Path, book_id: str) -> Path:
    return data_dir / "human" / book_id / "overrides"


def write_unmatched_overrides(data_dir: Path, result: PrecedenceResult) -> list[Path]:
    """Design decision D14: write one file per unmatched override to
    `human/<book_id>/overrides/<entry-slug>.json` -- a SUBDIRECTORY,
    deliberately, so the existing non-recursive `human/<book_id>/*.json`
    globs in `queue/summary.py`, `queue/audit.py`, and
    `queue/complete.py::_record_path_owners` (which treat every `*.json`
    file directly in `human/<book_id>/` as a pydantic `Segment`) never see
    it. Every errata/update source book's `overrides/` directory is cleared
    first, so a since-fixed entry doesn't linger as a stale file. Never
    touches a segment or record file -- only this dedicated subdirectory."""
    for book_id in sorted(result.override_source_books):
        overrides_dir = _human_overrides_dir(data_dir, book_id)
        if overrides_dir.is_dir():
            for path in overrides_dir.glob("*.json"):
                path.unlink()

    written: list[Path] = []
    generated_at = datetime.now(UTC).isoformat()
    for unmatched in result.unmatched_overrides:
        overrides_dir = _human_overrides_dir(data_dir, unmatched.entry_book_id)
        overrides_dir.mkdir(parents=True, exist_ok=True)
        path = overrides_dir / f"{unmatched.entry_slug}.json"
        payload = {
            "entry_id": unmatched.entry_id,
            "entry_name": unmatched.entry_name,
            "entry_book_id": unmatched.entry_book_id,
            "entry_record_path": (
                f"records/{unmatched.entry_book_id}/"
                f"{unmatched.entry_id.split(':', 1)[0]}/{unmatched.entry_slug}.json"
            ),
            "target_book": unmatched.target_book,
            "target_page": unmatched.target_page,
            "target_name": unmatched.target_name,
            "candidates": unmatched.candidates,
            "reason": unmatched.reason,
            "generated_at": generated_at,
        }
        atomic_write_text(path, json.dumps(payload, indent=2) + "\n")
        written.append(path)
    return written


def write_precedence_report(data_dir: Path, result: PrecedenceResult) -> Path:
    """Design decision D15: `<data_dir>/reports/precedence.md`."""
    path = data_dir / "reports" / "precedence.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(UTC).isoformat()

    lines = [
        "# Precedence report",
        "",
        f"Generated at: {generated_at}",
        "",
        "## Summary",
        "",
        f"- Overrides applied: {result.overrides_applied}",
        f"- Records with overrides: {result.records_with_overrides}",
        f"- Unmatched errata/update entries: {len(result.unmatched_overrides)}",
        f"- Rules Compendium overrides: {result.rc_overrides}",
        f"- Unmatched Rules Compendium sections: {len(result.unmatched_rc)}",
        f"- Variants created: {result.variants}",
        "",
        "## Unmatched errata and update entries",
        "",
    ]

    if result.unmatched_overrides:
        lines.append(
            "| entry id | target book | target page | target name | candidates considered |"
        )
        lines.append("| --- | --- | --- | --- | --- |")
        for unmatched in result.unmatched_overrides:
            candidates = (
                "; ".join(f"{c['id']} ({c['name']})" for c in unmatched.candidates)
                if unmatched.candidates
                else "(none)"
            )
            page = unmatched.target_page if unmatched.target_page is not None else ""
            lines.append(
                f"| {unmatched.entry_id} | {unmatched.target_book} | {page} | "
                f"{unmatched.target_name} | {candidates} |"
            )
    else:
        lines.append("None.")

    lines += ["", "## Unmatched Rules Compendium sections", ""]
    if result.unmatched_rc:
        lines.append("| record id | topic |")
        lines.append("| --- | --- |")
        for entry in result.unmatched_rc:
            lines.append(f"| {entry.record_id} | {entry.topic} |")
    else:
        lines.append("None.")

    lines.append("")
    atomic_write_text(path, "\n".join(lines))
    return path
