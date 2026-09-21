"""Which fragments a class span actually owns (batch B10c-mand11), and
releasing the record claims held by a superseded segment (batch
B10c-mand2).

`is_class_owned_fragment` is the shared, pure predicate that decides the
SCOPE of superseding, used by all three places that used to go by page
span alone: the class-span stamp pass (`owlsperch.segment.runner.
_supersede_segments_in_span`), `owlsperch build-db`'s record-level
superseding (`owlsperch.build_db.runner._apply_superseding`), and `owlsperch
queue audit`'s retroactive `wrongly_superseded` restore
(`owlsperch.queue.audit`). Page span alone swallowed printed SIDEBARS that
merely share a class's pages ("FAMILIARS", "THE PALADIN'S MOUNT",
"LEVEL ADVANCEMENT", ...), leaving them in no canonical record at all --
see its own docstring for the exact rule.

B10c's class-span pass (`owlsperch.segment.runner`) stamps `superseded_by`
on every fragment segment whose pages fall entirely inside a discovered
class/prestige_class span, but originally left that fragment's own
`records`/`pending_records` claims in place. When the class's own level
table shares the fragment's printed title -- and so the same slug, id, and
record file path -- `owlsperch.queue.complete`'s ownership guard refuses
the class segment's claim on that path as a collision, and the class can
never be extracted at all.

`release_segment_claims` is the fix, shared by two callers: the class-span
pass itself, at the moment a segment is NEWLY stamped `superseded_by` (see
`owlsperch.segment.runner._supersede_segments_in_span`), and `owlsperch
queue audit --fix` (`owlsperch.queue.audit.fix_book`), which applies the
same release retroactively to segments stamped before this release-at-
stamp-time behavior existed. For ONE superseded segment, it:

1. Walks the segment's own `records` + `pending_records`, deduplicated by
   RESOLVED path (order preserved, `records` first) -- a path can
   legitimately appear in both lists.
2. For each claimed path that resolves inside `records/<book_id>/` under
   `data_dir` (`owlsperch.queue.common.resolve_record_path_under_book`):
   moves the record file to `$OWLSPERCH_DATA/superseded/<book_id>/<type>/
   <file>.json` -- ALWAYS a move (`os.replace`), NEVER a delete -- unless
   the file's own `extraction.segment_id` names a DIFFERENT segment that is
   itself still live (not superseded): releasing must never steal a live
   segment's record, so that claim is pruned from the passed-in segment's
   lists but the file is left exactly where it is (`moved_to: None`). A
   claimed path missing on disk entirely is likewise pruned with
   `moved_to: None`, nothing created under `superseded/`. A destination
   name already taken under `superseded/` (two different segments'
   fragments both once owned a file with the same tail path -- unlikely,
   but never assumed away) is resolved by appending the releasing
   segment's own `seg_id`, then `-2`, `-3`, ... until a free name is found;
   an existing file under `superseded/` is never overwritten.
3. Clears the segment's `records`/`pending_records` to `[]` and appends one
   `ReleasedRecord` per claimed path onto `segment.released_records`.

It mutates the `segment` object passed to it but never writes it (or
anything else) to disk itself -- the caller persists it, in whatever atomic
write it was already about to do (e.g. the same one that sets
`superseded_by`).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from owlsperch.queue.common import resolve_record_path_under_book

if TYPE_CHECKING:
    from owlsperch.segment.runner import ReleasedRecord, Segment

#: A parenthetical qualifier, stripped before normalization so
#: `is_class_owned_fragment` reads a RECORD name the same way it reads a
#: printed segment heading: the extraction prompt's own name-qualification
#: rule (B10-mand1) makes a generic heading's record name "Class Features
#: (Barbarian)", never the bare "Class Features" a segment's own `heading`
#: carries.
_PARENTHETICAL_RE = re.compile(r"\([^()]*\)")

#: Everything that isn't a lowercase letter or digit -- normalization strips
#: punctuation/whitespace entirely rather than collapsing it (the same
#: choice `owlsperch.segment.runner._heading_matches_title` makes), so
#: "THE DRUID’S ANIMAL COMPANION" and "Ex-Barbarians" both normalize
#: cleanly.
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]")

#: The class-structural headings every class entry prints, whatever the
#: class -- they belong to the class record itself, so a fragment carrying
#: one of them inside a class's span is genuinely swallowed by that class.
CLASS_STRUCTURAL_HEADINGS: frozenset[str] = frozenset(
    {"gameruleinformation", "classskills", "classfeatures"}
)

#: Normalized tail of a "<Race> <Title> Starting Package" heading.
_STARTING_PACKAGE = "startingpackage"


def normalize_heading(text: str) -> str:
    """The normalized form every comparison here (and
    `owlsperch.build_db.runner._apply_superseding`'s own class-owned NAME
    set) is made in: parentheticals dropped, casefolded, every
    non-alphanumeric character deleted -- so "Wild Shape (Su)", "WILD
    SHAPE" and "Wild shape" are one and the same."""
    return _NON_ALNUM_RE.sub("", _PARENTHETICAL_RE.sub(" ", text).casefold())


#: Normalized flavor/section headings EVERY class prints (batch B10c-mand11),
#: so none of them is ever class-specific evidence that a given class owns a
#: heading. It lives here, beside `normalize_heading`, because two callers
#: need exactly the same set: `owlsperch.build_db.runner._class_owned_names`
#: (which headings may a class span supersede a fragment by?) and
#: `owlsperch.build_db.precedence._class_heading_names` (which headings may an
#: errata/update entry be matched to a class record by? -- B10c-mand17).
GENERIC_CLASS_SECTION_HEADINGS: frozenset[str] = frozenset(
    normalize_heading(h)
    for h in (
        "Adventures",
        "Characteristics",
        "Alignment",
        "Religion",
        "Background",
        "Races",
        "Other Classes",
        "Role",
        "Abilities",
        "Classes",
        "Game Rule Information",
        "Class Skills",
        "Class Features",
    )
)


def _plural_equal(a: str, b: str) -> bool:
    """Normalized equality tolerating a trailing plural "s" on either side
    -- PHB 3.5 prints "WIZARDS" for the toc's "Wizard" (mirrors
    `owlsperch.segment.runner._heading_matches_title`)."""
    if not a or not b:
        return False
    return a == b or a == b + "s" or b == a + "s"


def is_class_owned_fragment(heading: str, kind_hint: str, class_title: str) -> bool:
    """Batch B10c-mand11: whether a fragment inside a class's page span is
    part of THAT class's own printed entry (and so may be superseded by it)
    rather than a sidebar or unrelated chapter content that merely shares
    the pages.

    `heading` is the fragment's own printed heading (a segment's `heading`,
    or a record's `name` -- a parenthetical qualifier like "Class Features
    (Barbarian)" is stripped first, see `_PARENTHETICAL_RE`), `kind_hint`
    its kind (a segment's `kind_hint`, or a record's `type`), and
    `class_title` the class entry's own printed/toc title (a
    `_ClassSpan.heading`, or a class record's `name`).

    True for:

    - any `table` fragment in the span -- a class's own "tables belonging to
      this entity" convention has the class record claim those very record
      paths, so a table fragment's claim must stay released or
      `owlsperch.queue.complete`'s collision guard blocks the class itself;
    - a `rules_section` whose whole heading is the class title
      (plural-tolerant), one of `CLASS_STRUCTURAL_HEADINGS` ("Game Rule
      Information", "Class Skills", "Class Features"), "Ex-<Title>"
      (plural-tolerant), or ends with "<Title> Starting Package".

    False for everything else -- notably every sidebar the real PHB prints
    inside a class's own pages ("FAMILIARS", "ARCANE SPELLS AND ARMOR",
    "SCHOOL SPECIALIZATION", "LEVEL ADVANCEMENT", "THE PALADIN'S MOUNT",
    "SAMPLE PALADIN'S MOUNTS", "THE DRUID'S ANIMAL COMPANION",
    "ALTERNATIVE ANIMAL COMPANIONS") and any other kind (a spell or feat
    fragment stranded in a class's span is never class-owned). Note that
    "THE PALADIN'S MOUNT" NAMES the class and still isn't class-structural:
    every match here is against the WHOLE heading, never a substring of
    it."""
    if kind_hint == "table":
        return True
    if kind_hint != "rules_section":
        return False

    normalized = normalize_heading(heading)
    title = normalize_heading(class_title)
    if not normalized or not title:
        return False

    if normalized in CLASS_STRUCTURAL_HEADINGS:
        return True
    if _plural_equal(normalized, title):
        return True
    if normalized.startswith("ex") and _plural_equal(normalized[2:], title):
        return True
    if normalized.endswith(_STARTING_PACKAGE):
        prefix = normalized[: -len(_STARTING_PACKAGE)]
        if prefix and (prefix.endswith(title) or prefix.endswith(f"{title}s")):
            return True
    return False


def _record_owner_on_disk(path: Path) -> str | None:
    """The `extraction.segment_id` stamped on the record file at `path`, or
    `None` if it doesn't parse or doesn't carry one -- mirrors
    `owlsperch.queue.audit._record_owner_on_disk`."""
    try:
        record = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(record, dict):
        return None
    extraction = record.get("extraction")
    if not isinstance(extraction, dict):
        return None
    owner = extraction.get("segment_id")
    return owner if isinstance(owner, str) else None


def _segment_is_superseded(data_dir: Path, book_id: str, seg_id: str) -> bool:
    """Whether `seg_id` (searched under both `segments/<book_id>/` and
    `human/<book_id>/`) itself carries a non-empty `superseded_by`. A
    segment that can't be found at all is treated as NOT superseded (the
    conservative choice -- refusing to move a file whose real owner can't
    be confirmed frozen)."""
    for location in ("segments", "human"):
        path = data_dir / location / book_id / f"{seg_id}.json"
        if not path.is_file():
            continue
        try:
            raw: Any = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return False
        if not isinstance(raw, dict):
            return False
        superseded_by = raw.get("superseded_by")
        return isinstance(superseded_by, str) and bool(superseded_by)
    return False


def _unique_destination(dest: Path, seg_id: str) -> Path:
    """`dest` if free, otherwise `<stem>-<seg_id><suffix>`, otherwise
    `<stem>-<seg_id>-2<suffix>`, `-3`, ... -- never a name already taken
    under `superseded/`."""
    if not dest.exists():
        return dest
    stem, suffix = dest.stem, dest.suffix
    candidate = dest.with_name(f"{stem}-{seg_id}{suffix}")
    n = 2
    while candidate.exists():
        candidate = dest.with_name(f"{stem}-{seg_id}-{n}{suffix}")
        n += 1
    return candidate


def release_segment_claims(segment: Segment, *, data_dir: Path) -> list[ReleasedRecord]:
    """Release every record claim `segment` (already known to be
    superseded) holds -- see this module's docstring. Returns the list of
    `ReleasedRecord`s newly appended onto `segment.released_records`."""
    from owlsperch.segment.runner import ReleasedRecord  # lazy: see module docstring.

    resolved_data_dir = data_dir.resolve()
    records_root = (data_dir / "records" / segment.book_id).resolve()

    ordered_claims: list[str] = []
    seen: set[Path] = set()
    for rel_path in [*segment.records, *segment.pending_records]:
        resolved = resolve_record_path_under_book(data_dir, segment.book_id, rel_path)
        if resolved is None or resolved in seen:
            continue
        seen.add(resolved)
        ordered_claims.append(rel_path)

    newly_released: list[ReleasedRecord] = []
    for rel_path in ordered_claims:
        resolved = resolve_record_path_under_book(data_dir, segment.book_id, rel_path)
        assert resolved is not None  # already confirmed resolvable above
        moved_to: str | None = None

        if resolved.is_file():
            owner = _record_owner_on_disk(resolved)
            safe_to_move = (
                owner is None
                or owner == segment.seg_id
                or _segment_is_superseded(data_dir, segment.book_id, owner)
            )
            if safe_to_move:
                tail = resolved.relative_to(records_root)
                dest_dir = resolved_data_dir / "superseded" / segment.book_id / tail.parent
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest = _unique_destination(dest_dir / tail.name, segment.seg_id)
                os.replace(resolved, dest)
                moved_to = dest.relative_to(resolved_data_dir).as_posix()

        newly_released.append(ReleasedRecord(path=rel_path, moved_to=moved_to))

    segment.records = []
    segment.pending_records = []
    segment.released_records = [*segment.released_records, *newly_released]
    return newly_released
