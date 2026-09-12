"""Canonical class-abbreviation expansion table for spell `levels[].class`,
per B5 follow-up 4. Shared by `owlsperch.queue.prompt` (rendered verbatim
into the extraction prompt so a subagent expands e.g. `Sor/Wiz` itself) and
any future batch (B6+) that needs the same mapping (e.g. a data-repair pass
over already-extracted records).

D&D 3.5e stat blocks abbreviate class names in their `Level:` line (e.g.
`Sor/Wiz 3`, `Clr 5`). A cleric domain (`Air`, `Fire`, ...) or a prestige
class is never abbreviated this way and isn't in this table -- it's kept as
written, with only its first letter capitalized.
"""

from __future__ import annotations

#: Abbreviation -> canonical full class name, per the SRD's class list.
CLASS_ABBREVIATIONS: dict[str, str] = {
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


def _expand_one(token: str) -> str:
    """Expand a single (non-"/"-joined) class token: a known abbreviation
    expands to its canonical name; anything else (a cleric domain, a
    prestige class, or an already-full class name) is kept as written with
    only its first letter capitalized."""
    if token in CLASS_ABBREVIATIONS:
        return CLASS_ABBREVIATIONS[token]
    return token[:1].upper() + token[1:] if token else token


def expand_class(token: str) -> list[str]:
    """Expand one `levels[].class` token from a stat block's `Level:` line
    into the canonical class name(s) it stands for.

    A "/"-joined run of abbreviations (e.g. `"Sor/Wiz"`) expands to one
    entry per abbreviation, in order (`["Sorcerer", "Wizard"]`) -- so
    `"Sor/Wiz 3"` becomes two `levels` entries, `{"class": "Sorcerer",
    "level": 3}` and `{"class": "Wizard", "level": 3}`, never one combined
    entry. A single token (abbreviated or not) expands to a one-element
    list.
    """
    token = token.strip()
    return [_expand_one(part.strip()) for part in token.split("/")]
