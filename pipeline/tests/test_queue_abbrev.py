"""Tests for `owlsperch.queue.abbrev` -- the canonical class-abbreviation
table for spell `levels[].class` (B5 follow-up 4)."""

from __future__ import annotations

from owlsperch.queue.abbrev import CLASS_ABBREVIATIONS, expand_class


def test_table_maps_every_documented_abbreviation() -> None:
    assert CLASS_ABBREVIATIONS == {
        "Adp": "Adept",
        "Asn": "Assassin",
        "Brd": "Bard",
        "Blk": "Blackguard",
        "Clr": "Cleric",
        "Drd": "Druid",
        "Ftr": "Fighter",
        "Mnk": "Monk",
        "Pal": "Paladin",
        "Rgr": "Ranger",
        "Rog": "Rogue",
        "Sor": "Sorcerer",
        "Wiz": "Wizard",
    }


def test_expand_class_sor_wiz_becomes_two_entries() -> None:
    assert expand_class("Sor/Wiz") == ["Sorcerer", "Wizard"]


def test_expand_class_single_abbreviation() -> None:
    assert expand_class("Clr") == ["Cleric"]
    assert expand_class("Wiz") == ["Wizard"]


def test_expand_class_keeps_cleric_domain_capitalized_as_written() -> None:
    assert expand_class("Air") == ["Air"]
    assert expand_class("air") == ["Air"]
    assert expand_class("Fire") == ["Fire"]


def test_expand_class_keeps_prestige_class_as_written() -> None:
    assert expand_class("Arcane Archer") == ["Arcane Archer"]


def test_expand_class_strips_surrounding_whitespace() -> None:
    assert expand_class(" Sor/Wiz ") == ["Sorcerer", "Wizard"]
