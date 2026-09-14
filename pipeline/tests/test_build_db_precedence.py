"""Tests for `owlsperch.build_db.precedence` (spec 4.7, batch B11): errata/
update override matching, Rules Compendium override, latest-wins, and their
interaction with `superseded_by`.

Exercises `apply_precedence` directly against a small in-memory SQLite
connection built from the real `records`/`books` table schema
(`owlsperch.build_db.runner._SCHEMA_SQL`) with hand-inserted rows -- this
targets the precedence pass itself without needing a full manifest/segment/
record-file fixture tree (that end-to-end wiring, including the toc-warning
exemption and report/human/ file writes, is covered by
`test_build_db.py`).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from owlsperch.build_db.precedence import (
    PrecedenceResult,
    apply_precedence,
    write_precedence_report,
    write_unmatched_overrides,
)
from owlsperch.build_db.runner import _SCHEMA_SQL


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA_SQL)
    return conn


def _insert_book(conn: sqlite3.Connection, book_id: str, *, published: str | None = None) -> None:
    conn.execute(
        "INSERT INTO books (book_id, title, short_title, kind, edition, published, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (book_id, book_id, book_id.upper(), "rulebook", "3.5", published, "in_scope"),
    )


def _insert_record(
    conn: sqlite3.Connection,
    *,
    record_id: str,
    type_name: str,
    slug: str,
    name: str,
    book_id: str,
    fields: dict[str, Any] | None = None,
    pages: list[int] | None = None,
    superseded_by: str | None = None,
) -> None:
    record = {
        "id": record_id,
        "type": type_name,
        "name": name,
        "slug": slug,
        "book_id": book_id,
        "pages": pages or [1],
        "fields": fields or {},
    }
    conn.execute(
        "INSERT INTO records "
        "(id, type, name, slug, book_id, aliases, record_id, canonical, macro_eligible, json, "
        "toc_category, toc_chapter, toc_section, toc_path, superseded_by) "
        "VALUES (?, ?, ?, ?, ?, '', ?, 1, 0, ?, 'uncategorized', NULL, NULL, NULL, ?)",
        (record_id, type_name, name, slug, book_id, record_id, json.dumps(record), superseded_by),
    )


def _record_row(conn: sqlite3.Connection, record_id: str) -> Any:
    row = conn.execute(
        "SELECT canonical, variant_of, applied_overrides FROM records WHERE id = ?", (record_id,)
    ).fetchone()
    assert row is not None, f"no such record: {record_id}"
    return row


# ---------------------------------------------------------------------------
# Errata override: NAME match
# ---------------------------------------------------------------------------


def test_errata_override_applied_by_name_match() -> None:
    conn = _make_conn()
    _insert_book(conn, "book")
    _insert_book(conn, "book-errata")
    _insert_record(
        conn,
        record_id="spell:book:glibness",
        type_name="spell",
        slug="glibness",
        name="Glibness",
        book_id="book",
        pages=[236, 237],
    )
    _insert_record(
        conn,
        record_id="errata_entry:book-errata:glibness-p-236",
        type_name="errata_entry",
        slug="glibness-p-236",
        name="Glibness (p. 236)",
        book_id="book-errata",
        fields={
            "target_book": "book",
            "target_page": 236,
            "target_name": "Glibness",
            "replacement_text": "New wording.",
        },
    )

    result = apply_precedence(conn)

    assert result.overrides_applied == 1
    assert result.records_with_overrides == 1
    assert result.unmatched_overrides == []

    row = _record_row(conn, "spell:book:glibness")
    assert json.loads(row["applied_overrides"]) == ["errata_entry:book-errata:glibness-p-236"]
    assert row["canonical"] == 1
    assert row["variant_of"] is None


# ---------------------------------------------------------------------------
# Update override: PAGE match (target name absent from the book, one record
# on that page)
# ---------------------------------------------------------------------------


def test_update_override_applied_by_page_match_when_name_absent() -> None:
    conn = _make_conn()
    _insert_book(conn, "otherbook")
    _insert_book(conn, "otherbook-update")
    _insert_record(
        conn,
        record_id="spell:otherbook:overrun",
        type_name="spell",
        slug="overrun",
        name="Overrun",
        book_id="otherbook",
        pages=[148],
    )
    _insert_record(
        conn,
        record_id="update_entry:otherbook-update:overrun",
        type_name="update_entry",
        slug="overrun",
        name="Overrun",
        book_id="otherbook-update",
        fields={
            "target_book": "otherbook",
            "target_page": 148,
            # Deliberately not matching the target's own name, so the
            # name-match step must fall through to the page match.
            "target_name": "Some Different Wording",
            "replacement_text": "Change -1 to +1.",
        },
    )

    result = apply_precedence(conn)

    assert result.overrides_applied == 1
    row = _record_row(conn, "spell:otherbook:overrun")
    assert json.loads(row["applied_overrides"]) == ["update_entry:otherbook-update:overrun"]


# ---------------------------------------------------------------------------
# Update override: page match, but the target name overlaps TWO same-page
# records (e.g. two records that both start with a shared short word) ->
# ambiguous, applied to NEITHER (reviewer finding on _apply_overrides).
# ---------------------------------------------------------------------------


def test_update_override_ambiguous_name_overlap_on_same_page_is_unmatched() -> None:
    conn = _make_conn()
    _insert_book(conn, "book")
    _insert_book(conn, "book-update")
    _insert_record(
        conn,
        record_id="spell:book:mage-armor",
        type_name="spell",
        slug="mage-armor",
        name="Mage Armor",
        book_id="book",
        pages=[150],
    )
    _insert_record(
        conn,
        record_id="spell:book:mage-hand",
        type_name="spell",
        slug="mage-hand",
        name="Mage Hand",
        book_id="book",
        pages=[150],
    )
    _insert_record(
        conn,
        record_id="update_entry:book-update:mage-p-150",
        type_name="update_entry",
        slug="mage-p-150",
        name="Mage (p. 150)",
        book_id="book-update",
        fields={
            "target_book": "book",
            "target_page": 150,
            # A short prefix shared by both same-page spells' names -- must
            # not be applied to both just because each slug contains it.
            "target_name": "Mage",
            "replacement_text": "Some replacement.",
        },
    )

    result = apply_precedence(conn)

    assert result.overrides_applied == 0
    assert result.records_with_overrides == 0
    assert len(result.unmatched_overrides) == 1
    unmatched = result.unmatched_overrides[0]
    assert unmatched.entry_id == "update_entry:book-update:mage-p-150"
    candidate_ids = {c["id"] for c in unmatched.candidates}
    assert candidate_ids == {"spell:book:mage-armor", "spell:book:mage-hand"}

    for record_id in ("spell:book:mage-armor", "spell:book:mage-hand"):
        row = _record_row(conn, record_id)
        assert json.loads(row["applied_overrides"]) == []


# ---------------------------------------------------------------------------
# An entry matching neither name nor page -> human/ + report
# ---------------------------------------------------------------------------


def test_unmatched_override_goes_to_human_and_report(tmp_path: Path) -> None:
    conn = _make_conn()
    _insert_book(conn, "book")
    _insert_book(conn, "book-errata")
    _insert_record(
        conn,
        record_id="spell:book:fireball",
        type_name="spell",
        slug="fireball",
        name="Fireball",
        book_id="book",
        pages=[100],
    )
    _insert_record(
        conn,
        record_id="errata_entry:book-errata:nonexistent-p-999",
        type_name="errata_entry",
        slug="nonexistent-p-999",
        name="Nonexistent (p. 999)",
        book_id="book-errata",
        fields={
            "target_book": "book",
            "target_page": 999,
            "target_name": "Nonexistent",
            "replacement_text": "Some replacement.",
        },
    )

    result = apply_precedence(conn)

    assert result.overrides_applied == 0
    assert len(result.unmatched_overrides) == 1
    unmatched = result.unmatched_overrides[0]
    assert unmatched.entry_id == "errata_entry:book-errata:nonexistent-p-999"
    assert unmatched.target_name == "Nonexistent"
    assert unmatched.target_page == 999

    data_dir = tmp_path / "data"
    write_unmatched_overrides(data_dir, result)
    human_path = data_dir / "human" / "book-errata" / "overrides" / "nonexistent-p-999.json"
    assert human_path.is_file()
    payload = json.loads(human_path.read_text())
    assert payload["entry_id"] == "errata_entry:book-errata:nonexistent-p-999"
    assert payload["target_name"] == "Nonexistent"

    report_path = write_precedence_report(data_dir, result)
    report_text = report_path.read_text()
    assert "# Precedence report" in report_text
    assert "## Unmatched errata and update entries" in report_text
    assert "errata_entry:book-errata:nonexistent-p-999" in report_text


def test_unmatched_overrides_directory_cleared_before_each_build(tmp_path: Path) -> None:
    """A since-fixed entry's stale human/ file must not linger (D14)."""
    data_dir = tmp_path / "data"
    overrides_dir = data_dir / "human" / "book-errata" / "overrides"
    overrides_dir.mkdir(parents=True)
    stale = overrides_dir / "stale-entry.json"
    stale.write_text("{}")

    result = PrecedenceResult(override_source_books={"book-errata"})
    write_unmatched_overrides(data_dir, result)

    assert not stale.exists()


# ---------------------------------------------------------------------------
# Rules Compendium override
# ---------------------------------------------------------------------------


def test_rules_compendium_overrides_matching_topic() -> None:
    conn = _make_conn()
    _insert_book(conn, "rules-compendium")
    _insert_book(conn, "book")
    _insert_record(
        conn,
        record_id="rules_section:rules-compendium:grappling",
        type_name="rules_section",
        slug="grappling",
        name="Grappling",
        book_id="rules-compendium",
        fields={"topic": "Grappling"},
    )
    _insert_record(
        conn,
        record_id="rules_section:book:grappling",
        type_name="rules_section",
        slug="grappling",
        name="Grappling",
        book_id="book",
        fields={"topic": "Grappling"},
    )

    result = apply_precedence(conn)

    assert result.rc_overrides == 1
    assert result.unmatched_rc == []

    winner = _record_row(conn, "rules_section:rules-compendium:grappling")
    assert winner["canonical"] == 1
    assert winner["variant_of"] is None

    loser = _record_row(conn, "rules_section:book:grappling")
    assert loser["canonical"] == 0
    assert loser["variant_of"] == "rules_section:rules-compendium:grappling"


def test_rules_compendium_section_matching_nothing_stays_canonical_and_reported() -> None:
    conn = _make_conn()
    _insert_book(conn, "rules-compendium")
    _insert_record(
        conn,
        record_id="rules_section:rules-compendium:flanking",
        type_name="rules_section",
        slug="flanking",
        name="Flanking",
        book_id="rules-compendium",
        fields={"topic": "Flanking"},
    )

    result = apply_precedence(conn)

    assert result.rc_overrides == 0
    assert len(result.unmatched_rc) == 1
    assert result.unmatched_rc[0].record_id == "rules_section:rules-compendium:flanking"
    assert result.unmatched_rc[0].topic == "Flanking"

    row = _record_row(conn, "rules_section:rules-compendium:flanking")
    assert row["canonical"] == 1
    assert row["variant_of"] is None


# ---------------------------------------------------------------------------
# Latest-wins
# ---------------------------------------------------------------------------


def test_latest_wins_demotes_older_printing() -> None:
    conn = _make_conn()
    _insert_book(conn, "old-book", published="2001-01")
    _insert_book(conn, "new-book", published="2005-01")
    _insert_record(
        conn,
        record_id="spell:old-book:acid-fog",
        type_name="spell",
        slug="acid-fog",
        name="Acid Fog",
        book_id="old-book",
    )
    _insert_record(
        conn,
        record_id="spell:new-book:acid-fog",
        type_name="spell",
        slug="acid-fog",
        name="Acid Fog",
        book_id="new-book",
    )

    result = apply_precedence(conn)

    assert result.variants == 1
    winner = _record_row(conn, "spell:new-book:acid-fog")
    assert winner["canonical"] == 1
    assert winner["variant_of"] is None

    loser = _record_row(conn, "spell:old-book:acid-fog")
    assert loser["canonical"] == 0
    assert loser["variant_of"] == "spell:new-book:acid-fog"


def test_latest_wins_is_deterministic_on_equal_published() -> None:
    conn = _make_conn()
    _insert_book(conn, "book-a", published="2005-01")
    _insert_book(conn, "book-b", published="2005-01")
    _insert_record(
        conn,
        record_id="spell:book-b:acid-fog",
        type_name="spell",
        slug="acid-fog",
        name="Acid Fog",
        book_id="book-b",
    )
    _insert_record(
        conn,
        record_id="spell:book-a:acid-fog",
        type_name="spell",
        slug="acid-fog",
        name="Acid Fog",
        book_id="book-a",
    )

    winners = set()
    for _ in range(5):
        conn2 = _make_conn()
        _insert_book(conn2, "book-a", published="2005-01")
        _insert_book(conn2, "book-b", published="2005-01")
        _insert_record(
            conn2,
            record_id="spell:book-b:acid-fog",
            type_name="spell",
            slug="acid-fog",
            name="Acid Fog",
            book_id="book-b",
        )
        _insert_record(
            conn2,
            record_id="spell:book-a:acid-fog",
            type_name="spell",
            slug="acid-fog",
            name="Acid Fog",
            book_id="book-a",
        )
        apply_precedence(conn2)
        row = _record_row(conn2, "spell:book-a:acid-fog")
        winners.add("book-a" if row["canonical"] == 1 else "book-b")
        conn2.close()

    # Same book_id (the lexicographically smaller one, "book-a") wins every
    # time -- the tie-break is a real total order, not dict/set iteration.
    assert winners == {"book-a"}


def test_override_on_row_demoted_by_latest_wins_reaches_the_winner() -> None:
    """Regression: an errata entry matched against a specific printing
    (phb1's Fireball, by page) must still be reachable once that exact row
    is demoted to a variant by latest-wins, because a later-published book
    reprints the same spell under the same slug. Before the fix, the
    override stayed stuck on the now-noncanonical row and the canonical
    winner's own `applied_overrides` came back empty."""
    conn = _make_conn()
    _insert_book(conn, "phb1", published="2003-01")
    _insert_book(conn, "later-book", published="2005-01")
    _insert_book(conn, "phb1-errata")
    _insert_record(
        conn,
        record_id="spell:phb1:fireball",
        type_name="spell",
        slug="fireball",
        name="Fireball",
        book_id="phb1",
        pages=[241],
    )
    _insert_record(
        conn,
        record_id="spell:later-book:fireball",
        type_name="spell",
        slug="fireball",
        name="Fireball",
        book_id="later-book",
        pages=[300],
    )
    _insert_record(
        conn,
        record_id="errata_entry:phb1-errata:fireball-p-241",
        type_name="errata_entry",
        slug="fireball-p-241",
        name="Fireball (p. 241)",
        book_id="phb1-errata",
        fields={
            "target_book": "phb1",
            "target_page": 241,
            "target_name": "Fireball",
            "replacement_text": "Corrected wording.",
        },
    )

    result = apply_precedence(conn)

    assert result.overrides_applied == 1

    # The specific row the errata matched is demoted by latest-wins...
    loser = _record_row(conn, "spell:phb1:fireball")
    assert loser["canonical"] == 0
    assert loser["variant_of"] == "spell:later-book:fireball"
    assert json.loads(loser["applied_overrides"]) == ["errata_entry:phb1-errata:fireball-p-241"]

    # ...but the override must still be reachable on the canonical winner,
    # since that's the record `/records/{type}/{slug}` actually serves.
    winner = _record_row(conn, "spell:later-book:fireball")
    assert winner["canonical"] == 1
    assert json.loads(winner["applied_overrides"]) == ["errata_entry:phb1-errata:fireball-p-241"]


def test_override_on_row_demoted_by_rules_compendium_reaches_the_winner() -> None:
    """Same interaction, one hop earlier: an override matched against a
    non-RC rules_section row that the Rules Compendium pass then demotes."""
    conn = _make_conn()
    _insert_book(conn, "book")
    _insert_book(conn, "book-update")
    _insert_book(conn, "rules-compendium")
    _insert_record(
        conn,
        record_id="rules_section:book:grappling",
        type_name="rules_section",
        slug="grappling",
        name="Grappling",
        book_id="book",
        fields={"topic": "Grappling"},
        pages=[50],
    )
    _insert_record(
        conn,
        record_id="rules_section:rules-compendium:grappling",
        type_name="rules_section",
        slug="grappling",
        name="Grappling",
        book_id="rules-compendium",
        fields={"topic": "Grappling"},
    )
    _insert_record(
        conn,
        record_id="update_entry:book-update:grappling-p-50",
        type_name="update_entry",
        slug="grappling-p-50",
        name="Grappling (p. 50)",
        book_id="book-update",
        fields={
            "target_book": "book",
            "target_page": 50,
            "target_name": "Grappling",
            "replacement_text": "Clarified wording.",
        },
    )

    result = apply_precedence(conn)

    assert result.overrides_applied == 1
    assert result.rc_overrides == 1

    winner = _record_row(conn, "rules_section:rules-compendium:grappling")
    assert winner["canonical"] == 1
    assert json.loads(winner["applied_overrides"]) == ["update_entry:book-update:grappling-p-50"]


def test_variant_of_chain_is_flattened_to_the_true_root() -> None:
    """The reviewer finding: a record can be demoted twice -- first by the
    Rules Compendium pass (D13c) to an RC winner, then that same RC winner
    demoted again by latest-wins (D13d) to a later-published book that
    shares its `(type, slug)` but not its normalized topic, so it was
    never grouped with the RC record in the first place. Left unflattened
    this produces a 2-hop `variant_of` chain (book -> rules-compendium ->
    book-later); `_flatten_variant_chains` must rewrite the first hop to
    point straight at the true root."""
    conn = _make_conn()
    _insert_book(conn, "book", published="2000-01")
    _insert_book(conn, "rules-compendium", published="2010-01")
    _insert_book(conn, "book-later", published="2020-01")
    _insert_book(conn, "book-update")
    _insert_record(
        conn,
        record_id="rules_section:book:grappling",
        type_name="rules_section",
        slug="grappling",
        name="Grappling",
        book_id="book",
        fields={"topic": "Grappling"},
        pages=[50],
    )
    _insert_record(
        conn,
        record_id="rules_section:rules-compendium:grappling",
        type_name="rules_section",
        slug="grappling",
        name="Grappling",
        book_id="rules-compendium",
        fields={"topic": "Grappling"},
    )
    # Same (type, slug) as the two above, but a DIFFERENT normalized topic
    # -- the Rules Compendium pass never groups this one, but latest-wins
    # does (it only looks at (type, slug)), and this is the latest-
    # published book so it wins.
    _insert_record(
        conn,
        record_id="rules_section:book-later:grappling",
        type_name="rules_section",
        slug="grappling",
        name="Grappling",
        book_id="book-later",
        fields={"topic": "Grappling Rules"},
    )
    _insert_record(
        conn,
        record_id="update_entry:book-update:grappling-p-50",
        type_name="update_entry",
        slug="grappling-p-50",
        name="Grappling (p. 50)",
        book_id="book-update",
        fields={
            "target_book": "book",
            "target_page": 50,
            "target_name": "Grappling",
            "replacement_text": "Clarified wording.",
        },
    )

    result = apply_precedence(conn)

    assert result.overrides_applied == 1
    assert result.rc_overrides == 1
    assert result.variants == 1

    winner = _record_row(conn, "rules_section:book-later:grappling")
    assert winner["canonical"] == 1
    assert winner["variant_of"] is None

    rc_row = _record_row(conn, "rules_section:rules-compendium:grappling")
    assert rc_row["variant_of"] == "rules_section:book-later:grappling"

    # This is the line that fails before the fix: pre-fix, book's
    # variant_of is left at the RC id (one hop, not the true root).
    book_row = _record_row(conn, "rules_section:book:grappling")
    assert book_row["variant_of"] == "rules_section:book-later:grappling"

    # The override was matched against `book`'s row, then merged all the
    # way to the true (now-flattened) root, not left stranded on the
    # intermediate RC row.
    assert json.loads(winner["applied_overrides"]) == ["update_entry:book-update:grappling-p-50"]

    # The intermediate RC row still keeps its own (here: empty, since the
    # override never targeted it directly) applied_overrides list -- the
    # merge copies onto the root, it never clears the row it copied from.
    assert json.loads(rc_row["applied_overrides"]) == []


# ---------------------------------------------------------------------------
# superseded_by interaction
# ---------------------------------------------------------------------------


def test_superseded_record_never_a_target_winner_or_variant() -> None:
    conn = _make_conn()
    _insert_book(conn, "book")
    _insert_book(conn, "book-errata")
    # A rules_section fragment already superseded by a class span (B10c) --
    # must never be picked as an errata target...
    _insert_record(
        conn,
        record_id="rules_section:book:absorbed",
        type_name="rules_section",
        slug="absorbed",
        name="Absorbed",
        book_id="book",
        pages=[50],
        superseded_by="class:book:someclass",
    )
    _insert_record(
        conn,
        record_id="errata_entry:book-errata:absorbed-p-50",
        type_name="errata_entry",
        slug="absorbed-p-50",
        name="Absorbed (p. 50)",
        book_id="book-errata",
        fields={
            "target_book": "book",
            "target_page": 50,
            "target_name": "Absorbed",
            "replacement_text": "New text.",
        },
    )
    # ...nor a latest-wins winner/variant: a duplicate-slug pair where one
    # side is superseded must not interact with the other at all.
    _insert_book(conn, "old-book", published="2001-01")
    _insert_book(conn, "new-book", published="2005-01")
    _insert_record(
        conn,
        record_id="spell:old-book:acid-fog",
        type_name="spell",
        slug="acid-fog",
        name="Acid Fog",
        book_id="old-book",
        superseded_by="class:old-book:someclass",
    )
    _insert_record(
        conn,
        record_id="spell:new-book:acid-fog",
        type_name="spell",
        slug="acid-fog",
        name="Acid Fog",
        book_id="new-book",
    )

    result = apply_precedence(conn)

    # The errata entry found no eligible (non-superseded) target at all.
    assert result.overrides_applied == 0
    assert len(result.unmatched_overrides) == 1

    # The lone remaining non-superseded "Acid Fog" is untouched -- there is
    # no live duplicate to demote it against.
    assert result.variants == 0
    winner = _record_row(conn, "spell:new-book:acid-fog")
    assert winner["canonical"] == 1
    assert winner["variant_of"] is None

    # The superseded row itself is never rewritten by precedence.
    superseded_row = conn.execute(
        "SELECT canonical, variant_of, superseded_by FROM records WHERE id = ?",
        ("spell:old-book:acid-fog",),
    ).fetchone()
    assert superseded_row["superseded_by"] == "class:old-book:someclass"


@pytest.mark.parametrize("noop", [None])
def test_report_shows_none_when_nothing_unmatched(tmp_path: Path, noop: None) -> None:
    result = PrecedenceResult()
    path = write_precedence_report(tmp_path / "data", result)
    text = path.read_text()
    assert "## Unmatched errata and update entries" in text
    assert "## Unmatched Rules Compendium sections" in text
    assert text.count("None.") == 2


# ---------------------------------------------------------------------------
# errata_entry/update_entry are ordinary, searchable records -- they are
# excluded only from being override TARGETS, not from step (3)'s
# latest-wins grouping (CLAUDE.md's "The two override entry types" section).
# ---------------------------------------------------------------------------


def test_two_errata_books_with_the_same_entry_slug_collapse_under_latest_wins() -> None:
    conn = _make_conn()
    _insert_book(conn, "errata-v1", published="2003-01-01")
    _insert_book(conn, "errata-v2", published="2007-01-01")
    _insert_book(conn, "phb1", published="2003-07-01")
    _insert_record(
        conn,
        record_id="spell:phb1:fireball",
        type_name="spell",
        slug="fireball",
        name="Fireball",
        book_id="phb1",
        pages=[231],
    )
    fields = {
        "target_book": "phb1",
        "target_page": 231,
        "target_name": "Fireball",
        "replacement_text": "...",
    }
    _insert_record(
        conn,
        record_id="errata_entry:errata-v1:fireball-p-231",
        type_name="errata_entry",
        slug="fireball-p-231",
        name="Fireball (p. 231)",
        book_id="errata-v1",
        fields=fields,
    )
    _insert_record(
        conn,
        record_id="errata_entry:errata-v2:fireball-p-231",
        type_name="errata_entry",
        slug="fireball-p-231",
        name="Fireball (p. 231)",
        book_id="errata-v2",
        fields=fields,
    )

    result = apply_precedence(conn)

    assert result.overrides_applied == 2

    spell_row = _record_row(conn, "spell:phb1:fireball")
    applied = json.loads(spell_row["applied_overrides"])
    assert "errata_entry:errata-v1:fireball-p-231" in applied
    assert "errata_entry:errata-v2:fireball-p-231" in applied

    older = _record_row(conn, "errata_entry:errata-v1:fireball-p-231")
    assert older["canonical"] == 0
    assert older["variant_of"] == "errata_entry:errata-v2:fireball-p-231"

    newer = _record_row(conn, "errata_entry:errata-v2:fireball-p-231")
    assert newer["canonical"] == 1
    assert newer["variant_of"] is None
