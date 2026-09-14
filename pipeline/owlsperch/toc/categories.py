"""The category rule table (batch B10b, design decision D9): maps a table-
of-contents entry's title to one of `schemas/categories.json`'s keys.

Resolution order (the "judgement" half of the batch's feedback -- a
chapter/section that mixes book-shelf placement with what a player actually
looks for at the table):

- Chapter (level 1): per-book override -> generic pattern -> `uncategorized`.
- Section (level >= 2): per-book override -> ITS CHAPTER'S RESOLVED
  CATEGORY -> generic pattern -> `uncategorized`.

Sections inherit their chapter's category before generic patterns are even
tried, on purpose: e.g. "Movement, Position, And Distance" is a Combat rule
(it lives in the PHB's Combat chapter) even though "Movement" alone is an
Adventuring pattern below -- a player looking it up during a fight expects
it under Combat, not Adventuring.

Generic patterns are matched case-insensitively against the title with any
leading "Chapter N:" prefix stripped, in the order below (most specific
first, first match wins). `BOOK_OVERRIDES` is the escape hatch for emergent,
book-specific groupings a player would actually want (see the docstring on
each entry) -- keep it small; don't add entries the generic table already
gets right.
"""

from __future__ import annotations

import re

#: Strips a leading "Chapter 8:" (any chapter number) before generic
#: patterns are tried, so "Chapter 8: Combat" matches on "Combat" rather
#: than needing the literal chapter number/colon in every pattern.
_CHAPTER_PREFIX_RE = re.compile(r"^Chapter\s+\d+\s*:\s*", re.IGNORECASE)

#: (pattern, category key), most-specific first; the first pattern whose
#: `search()` matches the (chapter-prefix-stripped) title wins. Verified on
#: phb1: every one of its 16 chapters resolves with zero fall-through (see
#: `pipeline/owlsperch/toc/runner.py`'s module docstring and the batch's
#: design decision D9).
GENERIC_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"Abilities|Description|Alignment|Ability Scores|"
            r"Character Creation Summary|Vital Statistics",
            re.IGNORECASE,
        ),
        "character-creation",
    ),
    (re.compile(r"Races?|Racial", re.IGNORECASE), "races"),
    # Prestige Class(es) must be tried BEFORE the generic "Classes" pattern
    # below, or a prestige-class chapter/section would resolve to "classes"
    # instead of the dedicated "prestige-classes" category (batch B10c,
    # design decision D10).
    (re.compile(r"Prestige Class(?:es)?", re.IGNORECASE), "prestige-classes"),
    (re.compile(r"Classes|Class Descriptions", re.IGNORECASE), "classes"),
    (re.compile(r"Skills?", re.IGNORECASE), "skills"),
    (re.compile(r"Feats?", re.IGNORECASE), "feats"),
    (
        re.compile(r"Equipment|Goods and Services|Weapons|Armor|Wealth and Money", re.IGNORECASE),
        "equipment",
    ),
    (
        re.compile(r"Combat|Initiative|Special Attacks|Injury and Death", re.IGNORECASE),
        "combat",
    ),
    (
        re.compile(r"Adventuring|Movement|Exploration|Carrying Capacity|Treasure", re.IGNORECASE),
        "adventuring",
    ),
    (re.compile(r"Magic|Spells|Spellcasting|Psionics", re.IGNORECASE), "magic"),
    (re.compile(r"Monsters|Creatures", re.IGNORECASE), "monsters"),
    (
        re.compile(r"Running the Game|Dungeon Master|Campaigns|Rewards|Traps", re.IGNORECASE),
        "running-the-game",
    ),
    (
        re.compile(
            r"Introduction|Prologue|Foreword|Appendix|Glossary|Index|Character Sheet",
            re.IGNORECASE,
        ),
        "basics",
    ),
)

#: `book_id` -> case-folded entry title -> category key. Keep this small and
#: justified per entry -- only for emergent groupings the generic table
#: above genuinely gets wrong.
BOOK_OVERRIDES: dict[str, dict[str, str]] = {
    "phb1": {
        # "Experience and Levels" is Chapter 3's leveling-up rules, not a
        # class write-up -- "Classes" would otherwise win here since the
        # section sits in the Classes chapter, but a player looking up how
        # XP/leveling works expects Character Creation, not a class page.
        "experience and levels": "character-creation",
    },
}

#: The category every entry falls back to when no override or pattern
#: matches -- always a valid `schemas/categories.json` key (see
#: `pipeline/tests/test_toc_categories.py`).
UNCATEGORIZED = "uncategorized"


def _strip_chapter_prefix(title: str) -> str:
    return _CHAPTER_PREFIX_RE.sub("", title)


def _generic_category(title: str) -> str | None:
    stripped = _strip_chapter_prefix(title)
    for pattern, key in GENERIC_PATTERNS:
        if pattern.search(stripped):
            return key
    return None


def resolve_chapter_category(book_id: str, title: str) -> str:
    """Category for a level-1 (chapter) entry: per-book override, then the
    generic pattern table, then `uncategorized`."""
    override = BOOK_OVERRIDES.get(book_id, {}).get(title.casefold())
    if override is not None:
        return override
    return _generic_category(title) or UNCATEGORIZED


def resolve_section_category(book_id: str, title: str, chapter_category: str | None) -> str:
    """Category for a level >= 2 (section) entry: per-book override, then
    its own chapter's already-resolved category (`chapter_category`, `None`
    for a section with no preceding chapter), then the generic pattern
    table, then `uncategorized`. See this module's docstring for why the
    chapter's category is checked BEFORE generic patterns."""
    override = BOOK_OVERRIDES.get(book_id, {}).get(title.casefold())
    if override is not None:
        return override
    if chapter_category is not None:
        return chapter_category
    return _generic_category(title) or UNCATEGORIZED
