"""The toc-driven `monster` span discovery for `owlsperch segment` (batch
B12) -- the pure half, with no filesystem access of its own, so it can be
unit tested against a synthetic paragraph stream.

`owlsperch.segment.runner._run_monster_pass` is the I/O half (writing the
segment files and stamping `superseded_by`); this module decides WHICH
monster entries a book has, WHERE each one's text starts and stops, and
which of its toc entries collapse into the same segment.

Modelled on B10c's class pass, with the differences the Monster Manual's own
layout forces:

1. **Candidates.** Every level >= 2 toc entry whose resolved `category` is
   `monsters` and whose own pdf page range contains a `Hit Dice` marker
   (`HIT_DICE_RE`), searched over the SAME window the span itself will use
   (its own pages plus the one-page spill-over of point 5, since the index
   routinely gives a monster one page while its stat block sits at the top
   of the next). A monster's stat block prints the marker as "Hit Dice:",
   but a GROUPED entry's shared stat table prints it as a bare row label
   ("Hit Dice (hp)" on the true-dragon pages), so the colon is deliberately
   NOT required -- requiring it dropped all ten colour dragons, and
   restricting the window to the entry's own pages dropped 22 more
   including Elf, Gnome, Hydra, Ogre, Salamander and Wraith. It is the same
   kind of discriminator `_HIT_DIE_RE` is for classes: a `monsters`-category
   toc entry with no stat block anywhere near it is not an extractable
   monster entry.

   Known real-corpus limitation: the MM's own aboleth stat block (p0008) is
   simply ABSENT from the PDF's text layer -- `pdftotext` emits its title
   line and nothing else -- so the aboleth entry qualifies here on its
   NEIGHBOUR's marker and its segment carries no stat block of its own.
   That is an extraction-time failure (the entry escalates and lands in
   `human/`), deliberately preferred over silently dropping the entry.

2. **Grouping.** The MM's alphabetical index lists a grouped entry's
   sub-blocks as their own entries, with the group in a parenthetical:
   "Angel", then "Astral deva (angel)", "Planetar (angel)", "Solar
   (angel)". A sub-entry whose parenthetical names ANOTHER candidate entry
   is folded into that entry -- the group's own shared traits ("Angel
   Traits") are printed once, under the group heading, so the group must be
   ONE segment from which the extractor writes several records. A
   parenthetical that names no candidate entry is an ALIAS, not a group
   ("Barbed devil (hamatula)"), and is left as its own entry.

3. **Anchoring.** A span's text starts at the entry's own printed heading
   paragraph, matched with `title_matches`: normalized, plural-tolerant,
   parenthetical-tolerant, and comma-permuting in BOTH directions, because
   the index and the printed heading routinely disagree on word order
   ("Astral deva (angel)" vs. the printed "ANGEL, ASTRAL DEVA"; "Ape, dire"
   vs. "DIRE APE"; "Barbed devil (hamatula)" vs. "DEVIL, BARBED").
   A match on the entry's own toc start page wins over a later one, exactly
   as `_class_start_index` prefers.

4. **Absorbing an unmatched entry.** An entry whose heading is never found
   is, in every real mm1 case, a sub-block the book prints in body-size
   title case rather than as a heading ("Mountain dwarf", "Gray elf",
   "Orca", "Criosphinx") -- so it is folded into the nearest PRECEDING
   anchor's span, which already covers its pages, rather than becoming a
   second, overlapping segment. Only an entry with no preceding anchor at
   all is reported as unmatched.

5. **The end cut, and its stat-block fallback.** A span normally runs to
   the next anchor, capped at its own page range (its own `pdf_page_end`,
   widened by every entry folded into or absorbed by it, plus one page --
   a monster's last column routinely spills onto the next entry's page).
   But poppler's reading order frequently emits a monster's heading
   paragraph AWAY from its body (mm1 p0201 emits "OOZE", "BLACK PUDDING"
   and "ELDER BLACK PUDDING" as three adjacent paragraphs, with all three
   bodies elsewhere on the page), which cuts the span down to a heading
   with no stat block in it at all. So a span whose resolved text contains
   no `Hit Dice` marker falls back to its whole page range -- complete but
   overlapping, the same trade the class pass makes for a shared page. 22
   of mm1's 336 spans take this fallback; all 336 end up containing a
   marker.

6. **Back-extension.** As in B10c-mand6, a span's text start is
   back-extended to the top of its heading's own page (bounded so it never
   reaches past the previous span's own anchor), since the column
   reconstructor routinely prints a monster's own pre-heading tail (its
   flavor "read-aloud" paragraph, or its stat block) before the heading
   itself -- mm1 p0012 emits the solar's flavor text and Combat section
   BEFORE the "ANGEL, SOLAR" heading.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from owlsperch.segment.headings import Paragraph, is_heading
from owlsperch.segment.splitter import KindHint
from owlsperch.toc.parser import Toc, TocEntry

#: The `kind_hint` this pass produces.
MONSTER_KIND: KindHint = "monster"

#: The resolved toc `category` (`owlsperch.toc.categories`) that makes a
#: level >= 2 entry a monster-segment candidate. In mm1 this is chapters 1-6
#: ("Monsters A to Z", "Animals", "Vermin", "Improving Monsters", "Making
#: Monsters", "Monster Skills and Feats"); only the first three actually
#: carry index entries, and `HIT_DICE_RE` filters the rest either way.
MONSTER_CATEGORY = "monsters"

#: The stat-block discriminator (see this module's docstring, point 1). No
#: colon on purpose: a grouped entry's shared stat table prints "Hit Dice"
#: as a tab-separated row label, not "Hit Dice:".
HIT_DICE_RE = re.compile(r"\bHit Dice\b")

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]")
_PARENTHETICAL_RE = re.compile(r"\([^()]*\)")
_PARENTHETICAL_CONTENT_RE = re.compile(r"\(([^()]*)\)")


def _norm(text: str) -> str:
    return _NON_ALNUM_RE.sub("", text.casefold())


def title_variants(text: str) -> frozenset[str]:
    """Every normalized form a monster name may be printed in: with its
    parenthetical dropped and with it inlined, each of those also with a
    comma-separated title's two halves swapped. So "Astral deva (angel)"
    yields `astraldeva` and `astraldevaangel`, and the printed "ANGEL,
    ASTRAL DEVA" yields `angelastraldeva` and `astraldevaangel` -- the two
    meet on the swapped form."""
    forms: set[str] = set()
    for base in (_PARENTHETICAL_RE.sub(" ", text), text.replace("(", " ").replace(")", " ")):
        forms.add(base)
        if "," in base:
            head, tail = base.split(",", 1)
            forms.add(f"{tail} {head}")
    return frozenset(n for n in (_norm(f) for f in forms) if n)


def _plural_equal(a: str, b: str) -> bool:
    return a == b or a == b + "s" or b == a + "s"


def title_matches(heading: str, title: str) -> bool:
    """Whether a printed heading paragraph is the printed form of `title`
    (a toc entry's own title) -- any `title_variants` form of one equal,
    plural-tolerantly, to any form of the other."""
    return any(_plural_equal(h, t) for h in title_variants(heading) for t in title_variants(title))


def group_name(title: str) -> str | None:
    """The normalized content of `title`'s parenthetical, if it has one --
    the candidate GROUP a sub-entry may fold into ("Solar (angel)" ->
    `angel`)."""
    match = _PARENTHETICAL_CONTENT_RE.search(title)
    if match is None:
        return None
    return _norm(match.group(1)) or None


@dataclass(frozen=True)
class MonsterSpan:
    """One discovered monster segment: the paragraph range to write, the
    pages it spans, and every toc title it covers."""

    seg_id: str
    #: The monster entry's own PRINTED heading (the anchor paragraph's text)
    #: -- what `owlsperch.supersede.is_monster_owned_fragment` compares a
    #: fragment's heading against, and the segment's own `heading`.
    heading: str
    anchor_index: int
    start_index: int
    end_index: int
    page_start: int
    #: Last page this span's own toc entries claim, plus the one-page
    #: spill-over -- the cap on `end_index`. NOT the supersede window: that
    #: is `text_pages` below, since this can reach a page the resolved text
    #: never covers.
    page_end: int
    #: Every page the span's own resolved TEXT actually covers (batch B12
    #: review finding 1) -- the window the supersede pass stamps in, so a
    #: monster can never supersede a fragment on a page its own segment
    #: doesn't contain (on the real mm1 that was 122 fragments, e.g. the
    #: NEXT monster's own "COMBAT" section on a shared page).
    text_pages: tuple[int, ...]
    #: Every toc entry title this segment covers: its own, plus the group
    #: sub-entries folded into it and the unmatched entries absorbed by it.
    titles: tuple[str, ...]
    #: Whether the stat-block fallback (docstring point 5) was applied.
    page_fallback: bool = False


@dataclass
class MonsterDiscovery:
    """What `discover_monster_spans` found, for the summary line."""

    spans: list[MonsterSpan] = field(default_factory=list)
    #: Level >= 2 `monsters` toc entries with a `Hit Dice` marker.
    candidates: int = 0
    #: Candidates with no marker in their own pages (docstring point 1).
    without_stat_block: list[str] = field(default_factory=list)
    #: Sub-entries folded into a group entry (docstring point 2).
    grouped: int = 0
    #: Unmatched entries absorbed by an enclosing span (docstring point 4).
    absorbed: int = 0
    #: Entries with no heading match AND no preceding anchor to absorb them.
    unmatched: list[str] = field(default_factory=list)


def _page_start_index(paragraphs: list[Paragraph], index: int) -> int:
    """The index of the first paragraph on the same page as `index`."""
    page = paragraphs[index].page
    start = index
    while start > 0 and paragraphs[start - 1].page == page:
        start -= 1
    return start


def _first_index_after_page(paragraphs: list[Paragraph], page: int) -> int:
    for i, paragraph in enumerate(paragraphs):
        if paragraph.page > page:
            return i
    return len(paragraphs)


def _indices_in_pages(paragraphs: list[Paragraph], start: int, end: int) -> list[int]:
    return [i for i, p in enumerate(paragraphs) if start <= p.page <= end]


def discover_monster_spans(
    toc: Toc,
    *,
    paragraphs: list[Paragraph],
    body_median: float,
    page_text: Callable[[int], str],
    book_id: str,
    last_page: int,
) -> MonsterDiscovery:
    """Every monster segment `toc` implies, per this module's docstring.
    `page_text(pdf_page)` returns that page's raw text (the caller owns the
    filesystem); `last_page` caps every page span."""
    result = MonsterDiscovery()

    candidates: list[TocEntry] = []
    for entry in toc.entries:
        if entry.level < 2 or entry.category != MONSTER_CATEGORY:
            continue
        if entry.pdf_page_start is None or entry.pdf_page_end is None:
            continue
        span_text = "".join(
            page_text(p)
            for p in range(entry.pdf_page_start, min(entry.pdf_page_end + 1, last_page) + 1)
        )
        if not HIT_DICE_RE.search(span_text):
            result.without_stat_block.append(entry.title)
            continue
        candidates.append(entry)
    result.candidates = len(candidates)
    if not candidates:
        return result

    # Point 2: fold a sub-entry whose parenthetical names another candidate.
    by_variant: dict[str, TocEntry] = {}
    for entry in candidates:
        for variant in title_variants(entry.title):
            by_variant.setdefault(variant, entry)

    folded_into: dict[str, TocEntry] = {}
    own_entries: list[TocEntry] = []
    for entry in candidates:
        group = group_name(entry.title)
        owner = None
        if group is not None:
            owner = (
                by_variant.get(group)
                or by_variant.get(group + "s")
                or by_variant.get(group.removesuffix("s"))
            )
            if owner is entry:
                owner = None
        if owner is None:
            own_entries.append(entry)
        else:
            folded_into[entry.title] = owner
    result.grouped = len(folded_into)

    headings = [
        (i, p.page, p.text.strip()) for i, p in enumerate(paragraphs) if is_heading(p, body_median)
    ]

    # Point 3: anchor each remaining entry at its own printed heading.
    anchored: dict[int, list[TocEntry]] = {}
    unanchored: list[TocEntry] = []
    for entry in own_entries:
        first = entry.pdf_page_start
        page_end = entry.pdf_page_end
        # Every candidate resolved both above; re-stated for the type checker.
        if first is None or page_end is None:
            continue
        last = min(page_end + 1, last_page)
        exact: int | None = None
        later: int | None = None
        for heading_index, page, text in headings:
            if not (first <= page <= last) or not title_matches(text, entry.title):
                continue
            if page == first:
                exact = heading_index
                break
            if later is None:
                later = heading_index
        resolved = exact if exact is not None else later
        if resolved is None:
            unanchored.append(entry)
        else:
            anchored.setdefault(resolved, []).append(entry)

    anchor_order = sorted(anchored)

    # Point 4: absorb an unmatched entry into the nearest preceding anchor.
    absorbed: dict[int, list[TocEntry]] = {}
    for entry in unanchored:
        entry_page = entry.pdf_page_start
        preceding = [
            i for i in anchor_order if entry_page is not None and paragraphs[i].page <= entry_page
        ]
        if not preceding:
            result.unmatched.append(entry.title)
            continue
        absorbed.setdefault(max(preceding), []).append(entry)
        result.absorbed += 1

    seg_ordinals: dict[int, int] = {}
    for position, anchor in enumerate(anchor_order):
        owners = anchored[anchor] + absorbed.get(anchor, [])
        # Every sub-entry folded into one of this anchor's own entries is
        # covered by this segment too: its pages widen the span, and its
        # title belongs in `titles` (which is what tells the supersede pass
        # that e.g. the "SUCCUBUS" fragment inside the DEMON span is the
        # demon entry's own sub-block).
        subs = [
            sub
            for sub in candidates
            if any(folded_into.get(sub.title) is owner for owner in anchored[anchor])
        ]
        page_ends = [e.pdf_page_end for e in (*owners, *subs) if e.pdf_page_end is not None]
        anchor_page = paragraphs[anchor].page
        page_end = min(max(page_ends, default=anchor_page) + 1, last_page)
        page_end = max(page_end, anchor_page)

        # Point 5: the end cut -- next anchor, capped at the page range.
        page_cap_index = _first_index_after_page(paragraphs, page_end)
        next_anchor = (
            anchor_order[position + 1] if position + 1 < len(anchor_order) else len(paragraphs)
        )
        end_index = min(next_anchor, page_cap_index)

        # Point 6: back-extend to the top of the heading's own page.
        start_index = _page_start_index(paragraphs, anchor)
        if position > 0:
            start_index = max(start_index, anchor_order[position - 1] + 1)
        if start_index >= end_index:
            start_index = anchor
        end_index = max(end_index, anchor + 1)

        text = "\n\n".join(paragraphs[i].text for i in range(start_index, end_index))
        page_fallback = False
        if not HIT_DICE_RE.search(text):
            indices = _indices_in_pages(paragraphs, anchor_page, page_end)
            if indices:
                start_index, end_index = indices[0], indices[-1] + 1
                page_fallback = True

        text_pages = tuple(sorted({paragraphs[i].page for i in range(start_index, end_index)}))
        ordinal = seg_ordinals.get(anchor_page, 0) + 1
        seg_ordinals[anchor_page] = ordinal
        result.spans.append(
            MonsterSpan(
                seg_id=f"{book_id}-{MONSTER_KIND}-p{anchor_page:04d}-{ordinal:02d}",
                heading=paragraphs[anchor].text.strip(),
                anchor_index=anchor,
                start_index=start_index,
                end_index=end_index,
                page_start=anchor_page,
                page_end=page_end,
                text_pages=text_pages,
                titles=tuple(e.title for e in (*owners, *subs)),
                page_fallback=page_fallback,
            )
        )
    return result
