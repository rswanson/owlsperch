"""Unit tests for `owlsperch.toc.categories` (batch B10b, design decision
D9): generic pattern resolution, the chapter-inherits-before-generic-
patterns rule for sections, per-book overrides, and the uncategorized
fallback."""

from __future__ import annotations

from pathlib import Path

from owlsperch.schemas import load_categories
from owlsperch.toc.categories import (
    BOOK_OVERRIDES,
    GENERIC_PATTERNS,
    resolve_chapter_category,
    resolve_section_category,
)


def _repo_schemas_dir() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "schemas"


def test_every_category_referenced_by_the_rule_table_exists_in_categories_json() -> None:
    categories = {c.key for c in load_categories(_repo_schemas_dir())}
    referenced = {key for _, key in GENERIC_PATTERNS}
    for book_overrides in BOOK_OVERRIDES.values():
        referenced.update(book_overrides.values())
    referenced.add("uncategorized")
    assert referenced <= categories


def test_resolve_chapter_category_generic_pattern() -> None:
    assert resolve_chapter_category("book", "Chapter 8: Combat") == "combat"
    assert resolve_chapter_category("book", "Chapter 3: Classes") == "classes"
    assert resolve_chapter_category("book", "Chapter 7: Equipment") == "equipment"


def test_resolve_chapter_category_falls_back_to_uncategorized() -> None:
    assert resolve_chapter_category("book", "Chapter 99: Zorbnaxxle") == "uncategorized"


def test_resolve_chapter_category_book_override_wins_over_generic_pattern() -> None:
    # "Experience and Levels" would generically fall through to
    # "uncategorized" (no pattern matches it) -- the phb1 override must win,
    # and win case-insensitively.
    assert resolve_chapter_category("phb1", "EXPERIENCE AND LEVELS") == "character-creation"


def test_resolve_section_category_generic_pattern_when_no_chapter() -> None:
    result = resolve_section_category("book", "Movement, Position, And Distance", None)
    assert result == "adventuring"


def test_resolve_section_category_inherits_chapter_before_generic_pattern() -> None:
    # "Movement, Position, And Distance" would generically match Adventuring
    # ("Movement"), but a section inside the Combat chapter must inherit
    # Combat instead -- the whole point of D9's ordering.
    assert (
        resolve_section_category("book", "Movement, Position, And Distance", "combat") == "combat"
    )


def test_resolve_section_category_book_override_wins_over_chapter_inheritance() -> None:
    result = resolve_section_category("phb1", "Experience and Levels", "classes")
    assert result == "character-creation"


def test_resolve_section_category_falls_back_to_uncategorized_with_no_chapter_or_pattern() -> None:
    assert resolve_section_category("book", "Zorbnaxxle Details", None) == "uncategorized"


def test_phb1_override_dict_is_small_and_documented() -> None:
    # Regression against override-dict creep (batch instructions: "keep the
    # phb1 override dict small and justified"). Not a hard cap -- just a
    # tripwire so a future PR notices before the dict grows unbounded.
    assert len(BOOK_OVERRIDES.get("phb1", {})) <= 5


# ---------------------------------------------------------------------------
# Batch B10c, design decision D10: "Prestige Class(es)" resolves to the
# dedicated "prestige-classes" category, tried BEFORE the generic "Classes"
# pattern.
# ---------------------------------------------------------------------------


def test_resolve_chapter_category_prestige_classes() -> None:
    assert resolve_chapter_category("book", "Chapter 5: Prestige Classes") == "prestige-classes"
    assert resolve_chapter_category("book", "Prestige Class") == "prestige-classes"


def test_resolve_section_category_prestige_classes_not_shadowed_by_generic_classes() -> None:
    # A section literally titled "Prestige Classes" must not fall through to
    # the generic "Classes" pattern just because "Classes" is a substring.
    assert resolve_section_category("book", "Prestige Classes", None) == "prestige-classes"


def test_resolve_section_category_prestige_class_section_inherits_chapter() -> None:
    assert resolve_section_category("book", "Iron Warden", "prestige-classes") == "prestige-classes"


# ---------------------------------------------------------------------------
# Batch B12: the Monster Manual's own chapter titles
# ---------------------------------------------------------------------------


def test_monster_book_chapters_resolve_to_monsters() -> None:
    for title in (
        "Chapter 1: Monsters A to Z",
        "Chapter 2: Animals",
        "Chapter 3: Vermin",
        "Chapter 4: Improving Monsters",
        "Chapter 5: Making Monsters",
    ):
        assert resolve_chapter_category("mm1", title) == "monsters"


def test_monster_skills_chapter_beats_the_generic_skills_pattern() -> None:
    """Tried before "Skills?"/"Feats?" on purpose, so the MM's own chapter 6
    groups with its other monster chapters rather than under skills."""
    assert resolve_chapter_category("mm1", "Chapter 6: Monster Skills and Feats") == "monsters"
    # ...without disturbing an ordinary skills chapter.
    assert resolve_chapter_category("phb1", "Chapter 4: Skills") == "skills"
