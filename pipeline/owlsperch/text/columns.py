"""Reading-order reconstruction for one page of `bbox.Block`s.

Algorithm (per page):

1. **Drop vertical/rotated blocks.** A block all of whose *judged* lines
   are taller than they are wide (`line.height > line.width`) is glyph text
   rotated 90 degrees -- in practice, a page-edge chapter/section tab
   printed sideways in the margin. These carry no reading-order position
   relative to the body columns, so they are excluded from the output
   entirely rather than assigned to a column. (The caller -- see
   `owlsperch.text.runner` -- separately counts how many blocks this drops,
   since the same rule also discards image credits like "Illus. by ...",
   and that loss is otherwise invisible.)

   Only lines of at least `VERTICAL_MIN_LINE_CHARS` (2) characters are
   judged, and a block with no line long enough to judge is never dropped:
   a *single* glyph is taller than it is wide in ordinary horizontal text
   too, so a one-character line carries no orientation evidence at all.
   Without that gate, a narrow class-table column whose every cell is one
   digit -- PHB p.56's Table 3-18: The Wizard "Spells per Day" 0 and 1st
   columns (`4`/`2`...), p.32's Table 3-6: The Cleric 0 column (`3`/`4`...)
   -- was read as rotated marginalia and dropped whole, silently losing the
   leading spells-per-day cell of every level row (and, on p.32, the `0`
   column header too). A sibling column holding an em dash, or two-glyph
   cells like `+1`, was wider than tall and so never affected, which is why
   only the leftmost spell columns went missing.

1a. **Exclude prose-like blocks from table grouping.** A three (or more)
    -column prose layout (e.g. the PHB spell chapter) looks, block by
    block, exactly like step 2's table-column shape: several blocks side
    by side, each spanning a similar y-range. To tell them apart, a block
    is **prose-like** -- and never eligible to join a table group -- when
    it has at least `PROSE_MIN_LINES` (4) non-blank lines, its median
    words-per-line is at least `PROSE_MIN_MEDIAN_WORDS_PER_LINE` (5), and
    at least `PROSE_SPANNING_LINE_FRACTION` (60%) of its lines each span
    at least `PROSE_LINE_SPAN_FRACTION` (75%) of the block's own width
    (justified body text runs edge to edge). A table cell block -- short,
    ragged, few-word lines -- fails this test and remains eligible.

1b. **Exclude stat-block-like blocks from table grouping.** Two (or more)
    spell or monster stat blocks that happen to sit side by side with a
    shared y-range (e.g. two adjacent spell descriptions' "Level:"/
    "Casting Time:"/"Saving Throw:" blocks in the PHB's 3-column spell
    chapter) satisfy step 2's pairwise table-column test just as well as
    real table columns do, and are not prose-like either (their lines are
    short, not justified running text) -- so they slip past 1a. To catch
    this instead: a block is **label:value-like** -- and, like a
    prose-like block, never eligible to join a table group -- when at
    least `LABEL_VALUE_LINE_FRACTION` (50%) of its (non-blank) lines match
    `^[A-Z][A-Za-z' /()]{1,30}:\\s` (e.g. "Level: Sor/Wiz 3", "Casting
    Time: 1 standard action", "Saving Throw: None"). A genuine table
    column's cells (a name, a cost, a die code) essentially never take
    this "Label: value" shape.

2. **Detect table groups.** `pdftotext` frequently emits each column of a
   multi-column table (e.g. a weapon table: name, cost, damage, critical,
   type) as its own narrow block rather than one block per row. Left to
   step 3's column clustering, these narrow blocks would each become their
   own "column" and get emitted one after another -- i.e. column-major
   (every name, then every cost, then every damage) instead of row-major.
   To detect this: among the remaining non-vertical, non-prose-like,
   non-label:value-like blocks with at least `TABLE_CANDIDATE_MIN_LINES`
   (2) non-blank lines -- a
   one-line block is a heading or caption, never a table column, however
   its y-range happens to sit -- find every pair whose vertical extents
   overlap by at least `TABLE_OVERLAP_FRACTION` (70%) of the *shorter*
   block's height *and* at least `TABLE_OVERLAP_TALLER_FRACTION` (50%) of
   the *taller* block's height, and whose x-extents do not overlap at all.
   Requiring both fractions (not just the shorter-block one) matters
   because true table columns span the same rows top to bottom, so the
   overlap is large relative to both blocks, not just the smaller one --
   otherwise a short block (e.g. a one-line heading) that merely happens to
   sit fully nested inside a much taller neighbor's y-range would trivially
   score 100% of its own (shorter) height while covering only a sliver of
   the taller block's. A **table group** is a *clique* of such pairs --
   every member overlaps every other member this way, not merely chained
   transitively (A-B and B-C does not imply A-C) -- of at least
   `TABLE_MIN_BLOCKS` (3) blocks. Where a candidate's blocks admit more than
   one maximal clique of qualifying size (rare), the largest wins and its
   blocks are removed from consideration by smaller, overlapping candidate
   cliques.

2a. **Detect single-block tables.** Sometimes `pdftotext` keeps a whole
    table as a *single* block instead -- e.g. a full-width table wide
    enough, or with cells close enough together, that poppler never splits
    it into the separate per-column blocks step 2 looks for. Left alone,
    such a block is just a block like any other: its lines get flattened
    into one run-on prose paragraph (see `owlsperch.text.runner`),
    scrambling the table into a wall of text (its cells are frequently
    still one `<line>` each, just all inside the same `<block>`).

    To detect this, for each remaining (non-multi-block-table) block:
    bucket its lines into rows the same way step 2's table groups are (see
    "Emitting a table group" below), since `pdftotext` often still emits
    each cell as its own `<line>` even within one block, at the same y
    position as its row-mates. Within each row, flatten its words (across
    however many `<line>`s contributed to it) into one x-sorted sequence
    and measure the horizontal gap between each consecutive pair. A gap is
    "large" if it exceeds `max(TABLE_GAP_MEDIAN_FACTOR *` the block's
    median inter-word gap`, TABLE_GAP_HEIGHT_FACTOR *` the block's median
    word height`)`. The median-gap baseline is measured only *within* each
    original `<line>` (not across the `<line>` boundaries a row groups
    together) -- a gap between two words `pdftotext` already put in
    separate `<line>`s is frequently itself a real column gap, so folding
    it into the baseline would inflate it and mask genuine column gaps;
    the height-based floor instead keeps a block with few or no small
    intra-line gaps to take a median of from treating every gap as "large"
    by default. A row with at least `TABLE_MIN_GAPS_PER_ROW`
    (2) large gaps is "gappy"; the block is a single-block table if at
    least `TABLE_MIN_GAPPY_ROWS` (3) of its rows are gappy *and* the gappy
    rows' large-gap midpoints cluster at consistent x-positions -- a
    cluster is a confirmed column split only if it is hit by at least
    `TABLE_GAP_CLUSTER_FRACTION` (60%) of the gappy rows.

    Before committing to a table, one more check guards against a
    different false positive: two adjacent prose columns (e.g. two spell
    entries side by side) that `pdftotext` happened to keep as one block.
    These have the same consistent-large-gap shape a table does -- the
    gap is the column gutter -- but each "cell" is a run of prose words,
    not a table cell. So the confirmed splits' cells (across the gappy
    rows) are measured for their median word count; a median of
    `TABLE_CELL_MAX_MEDIAN_WORDS` (4) or fewer confirms a table, but a
    higher median means these are sentences, not cells, and the block is
    instead split at the confirmed x-positions into that many ordinary
    `Block`s (one per column, each keeping every original line's words
    that fall on its side of the split) and handed back to be ordered as
    prose columns like any other block (step 3 onward) -- not as a table
    at all.

    A confirmed single-block table becomes a table group exactly like
    step 2's: each gappy row is split into cells at the confirmed
    x-splits; every other row (a caption, a category sub-heading, an
    unsplit footnote paragraph line) is kept as its own single-cell row.
    Superscript footnote markers poppler merges into a word (e.g. "1d23"
    for "1d2" with footnote marker 3) are not un-merged here -- out of
    scope.

2b. **Reassemble a table `pdftotext` fragmented into several groups plus
    orphan rows.** Sometimes step 2's clique detection does not produce one
    table group per printed table but several: a wide class-level table
    (e.g. PHB Table 3-6: The Cleric) can arrive from poppler as multiple
    per-column block clusters -- one clique of column blocks per short run
    of rows -- separated by rows that instead arrive as one block *per
    cell* (every column's cell for that row its own single-line block).
    Step 2's `TABLE_CANDIDATE_MIN_LINES` (>= 2 non-blank lines) correctly
    excludes those single-line fragments from clique detection -- a
    one-line block is ambiguous with a heading on its own -- but left
    there, a fragmented table like this yields several small `TableGroup`s
    plus a pile of loose one-cell blocks that reading order then scatters
    as prose.

    This step runs immediately after step 2's clique detection, before any
    of its leftover blocks reach step 2a's single-block-table detection --
    so a block this step absorbs is claimed once, never separately
    considered there and never emitted twice. It alternates two moves to a
    joint fixpoint:

    - **Merge.** Two step-2 table-group candidates merge when their
      x-extents overlap by at least `TABLE_MERGE_X_OVERLAP_FRACTION` (80%)
      of the narrower one's x span, and the vertical gap between them is
      at most `TABLE_MERGE_GAP_HEIGHT_FACTOR` (2.0) times the larger of
      their own median non-blank line heights -- i.e. they read as one
      table with an ordinary row pitch between them, not two unrelated
      tables that merely share a column layout.
    - **Absorb.** A leftover block -- not prose-like, not label:value-like
      (the same exclusions steps 1a/1b apply to real table columns), and
      *not* required to meet `TABLE_CANDIDATE_MIN_LINES` -- joins a group
      when its x-extent lies inside the group's own x span and
      its y-center falls within one of the group's own median non-blank
      line heights of the group's y span. A single-line orphan row
      fragment is exactly what this looks for. B10c-mand15: the x-extent
      test allows an overhang of up to
      `TABLE_ABSORB_X_OVERHANG_HEIGHT_FACTOR` (1.0) of the group's own
      median line height on either side, because a group's x span is only
      as wide as the cells it has already claimed and an orphan row's own
      cell is frequently the widest in its column -- PHB p.37's druid
      animal-companion sidebar grid prints "Improved evasion" 2.3pt wider
      than every other cell of its Special column, and rejecting it for
      those 2.3pt left it emitted as a standalone paragraph after the
      whole table, where the extractor had to guess which row it belonged
      to (and guessed the wrong one).

    Merging concatenates member BLOCKS, never already-built rows:
    rebuilding from blocks with `_build_table_group` (the same function
    step 2 already uses) is what keeps a merged group's rows correctly
    re-bucketed and column-sorted, rather than stitching two groups' row
    lists together with each one's cells numbered against its own,
    independent column layout. Alternation to a fixpoint matters: on PHB
    p.32, the gap between the 4th per-column-cluster group and the 5th is
    too wide to merge on its own, and only closes once the 15th- and
    16th-level orphan rows sitting in that gap are absorbed into the 4th
    group first -- a single merge-then-absorb pass would stop with two
    groups instead of one. Every individual merge or absorption is
    checked against a guard before being accepted: rebuilding the
    candidate with `_build_table_group` and counting rows with at least 2
    cells must not come out lower than the sum for the pieces going in; a
    step that would is rejected and the pass moves on to try a different
    pair or block, rather than aborting the whole reassembly.

    This step never removes the phantom ", Bonus Feat" / ", BF" errata
    tokens PHB class tables print in their Special column -- those are
    baked into the source PDF's own text (even `pdftotext -layout` prints
    them, and poppler's bbox output carries no font signal to tell them
    apart from a real feature reference), so distinguishing them stays the
    extraction prompt's job, not this step's.

2c. **Detect a two-column label/value grid** (B10c-mand15). A grid whose
    every row is a short label beside one long, sentence-like value -- the
    PHB FAMILIARS sidebar's `Familiar | Special` list (pp.53-54: "Bat |
    Master gains a +3 bonus on Listen checks"), or the monk's unarmed-damage
    grid (p.42) -- arrives as a single block with exactly ONE large gap per
    row, so step 2a's `TABLE_MIN_GAPS_PER_ROW` (2) can never see it, and its
    right-hand cells are sentences, so step 2a's cell-length check would
    call it two merged prose columns. Left undetected it flattens into one
    run-on paragraph in which the labels and values desynchronize (poppler
    emits the Snake row's label *after* its value).

    So a block step 2a declined is re-checked with a one-gap-per-row bar,
    and accepted as a table only when all of: at least
    `TABLE_MIN_GAPPY_ROWS` (3) rows carry a gap; those rows are at least
    `LABEL_GRID_MIN_GAPPY_ROW_FRACTION` (50%) of the block's own rows (every
    row of a real grid has the gap, whereas prose flowed around an
    illustration -- PHB p.28's bard column -- has a handful of accidentally
    aligned wide gaps among dozens of ordinary lines); the gaps confirm
    exactly ONE column split (step 2a's same
    `TABLE_GAP_CLUSTER_FRACTION` clustering); and the *label* side's cells
    have a median word count of at most
    `LABEL_GRID_MAX_MEDIAN_LABEL_WORDS` (3) -- a grid's left cells are a
    name or a code, two merged prose columns' are sentences. This path
    never falls through to a prose split, so it is strictly additive: it
    can only turn a block that used to flatten into prose into a table
    group.

3. **Split the page into runs at wide blocks and table groups.** The *text
   area* width is the span from the minimum `xMin` to the maximum `xMax`
   across all non-vertical blocks. Any remaining (non-table) block whose
   own width exceeds `WIDE_BLOCK_FRACTION` (60%) of that span -- a
   full-width table or a chapter heading spanning the columns -- is a
   "wide" block. Both wide blocks and table groups are column breaks:
   sorting all remaining blocks and table groups by `yMin`, each one splits
   the page into the run of blocks above it and the run below it. The
   final order is: run 1 in column order, break 1, run 2 in column order,
   break 2, ... (a page with no breaks is one run).

4. **Cluster each run's blocks into columns by x-position.** Within a run,
   blocks are sorted by `xMin` and merged left to right: a block joins the
   current column if its `xMin` is within `COLUMN_GAP_HEIGHT_FACTOR` (1.5)
   times the run's median word glyph height of that column's rightmost
   extent so far; otherwise a horizontal gap that wide is a real column
   gutter and it starts a new column. Splitting on an absolute,
   text-size-relative gap (rather than a fixed fraction of the text-area
   width, which implicitly assumed two columns) means this works the same
   whether the page actually has one, two, three, or four prose columns.

4a. **Repair an over-merged column** (B10c-mand15). The greedy chain of
    step 4 is order-dependent: one block reaching a little further right
    than its column-mates extends the running extent, and a gutter that is
    merely *narrow* is then bridged, merging two printed columns into one
    cluster that gets ordered purely by `yMin` -- i.e. interleaved. A
    full-width BOXED SIDEBAR is exactly where this bites: the box's own
    columns sit slightly wider than the body text around them, closing the
    gutter to ~10pt against a threshold of ~12pt (PHB pp.37, 46, 53-54,
    57-58), and the sidebar's right-column continuation -- which starts a
    few points higher than its own left-column intro, since the left column
    begins with the box's heading -- got emitted FIRST, so the sidebar
    opened mid-sentence ("mount must be within 5 feet at the time of
    casting to receive the benefit.").

    A cluster whose own x span exceeds the same `WIDE_BLOCK_FRACTION` (60%)
    of the text area that makes a *block* a column break cannot be one
    printed column, since no block that wide ever reaches column clustering
    (step 3 takes it out as a break first). Such a cluster is split at its
    widest internal **valley** -- an x-interval inside its span that none of
    its member blocks overlaps -- provided the valley is at least
    `COLUMN_VALLEY_GAP_HEIGHT_FACTOR` (1.0) times the run's median word
    glyph height, and each piece is then re-checked the same way. Being a
    global property of the cluster, a valley is order-independent, unlike
    the running extent step 4 chains. Splitting only while a piece is still
    implausibly wide is what keeps this from column-ordering a table's cell
    blocks that step 2 failed to group: those stay merged (and so ordered
    row-wise by y) as soon as their own piece is narrow enough.

5. **Emit columns left to right, each column's blocks top to bottom**
   (sorted by `yMin`), then continue with the next run after its wide
   block or table group. Known remaining limitation (B10c-mand15): a table
   group is a column break for the WHOLE page, even when it is only as wide
   as one column, so a grid printed inside a boxed sidebar's left column
   still cuts the page in two at its own `yMin` -- the sidebar's right
   column is emitted in the run above the grid rather than after the left
   column's own text below it. The sidebar now *opens* in printed order,
   which is what the extraction prompt needs; making a narrow table group
   participate in column clustering instead of breaking the page would be
   the fuller fix.

**Emitting a table group.** A table group's member blocks' lines (for a
step-2 multi-block table group) or one block's own lines (for a step-2a
single-block table) are collected into one flat list. Lines are bucketed
into rows by y-center: lines whose y-centers are within `TABLE_ROW_FRACTION`
(40%) of the median line height of each other belong to the same row. Rows
are ordered top to bottom. For a multi-block table group, within a row,
cells (one per contributing line) are ordered left to right by `xMin` and
joined with a single tab character. For a single-block table, a *gappy* row
is instead split into cells at its confirmed large-gap x-positions (see
step 2a) and a non-gappy row is kept as one cell -- both cases end up as the
same `TableRow`/`TableGroup` shape. The caller (see `owlsperch.text.runner`)
renders a table group's rows one per output line, surrounded by blank lines
-- like a wide block, a table group is a column break for the surrounding
prose.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from owlsperch.text.bbox import Block, Line, Page, Word

#: A block wider than this fraction of the text area is treated as a column
#: break (a full-width table or heading) rather than clustered into a column.
WIDE_BLOCK_FRACTION = 0.6

#: A line needs at least this many characters for its own width-vs-height
#: shape to be evidence of rotated text (see step 1 and
#: `is_vertical_block`): a single glyph -- a digit in a narrow numeric table
#: column, say -- is taller than it is wide whichever way the text runs.
VERTICAL_MIN_LINE_CHARS = 2

#: Column-clustering threshold, as a multiple of the run's median word
#: glyph height: a block starts a new column when the horizontal gap from
#: the current column's rightmost extent so far is at least this wide (see
#: step 4).
COLUMN_GAP_HEIGHT_FACTOR = 1.5

#: Two blocks are candidate table columns if their vertical extents overlap
#: by at least this fraction of the shorter block's height (see step 2).
TABLE_OVERLAP_FRACTION = 0.70

#: ...and by at least this fraction of the TALLER block's height (see step
#: 2) -- true table columns span the same rows, so the overlap must be
#: large relative to both blocks, not just the shorter one; this is what
#: rejects a short heading/caption block that happens to nest inside a much
#: taller neighbor's y-range.
TABLE_OVERLAP_TALLER_FRACTION = 0.50

#: A block needs at least this many non-blank lines to be a candidate table
#: column (see step 2) -- a single-line block is a heading or caption, and
#: is never a table column regardless of its overlap with its neighbors.
TABLE_CANDIDATE_MIN_LINES = 2

#: A group of blocks connected by the table-column relationship is only
#: treated as a table if it has at least this many members (see step 2) --
#: a two-block group is an ordinary two-column prose layout.
TABLE_MIN_BLOCKS = 3

#: A block needs at least this many non-blank lines to be considered
#: "prose-like" (see step 1a) -- fewer is too little evidence either way.
PROSE_MIN_LINES = 4

#: ...and a median words-per-line of at least this many (see step 1a) --
#: a table cell's lines are typically a name or a single number/die code.
PROSE_MIN_MEDIAN_WORDS_PER_LINE = 5.0

#: A line "spans" its block when its own width is at least this fraction of
#: the block's width (see step 1a) -- justified body text runs edge to
#: edge; a table cell's line does not.
PROSE_LINE_SPAN_FRACTION = 0.75

#: ...and at least this fraction of a block's (non-blank) lines must span
#: it this way for the block to count as prose-like (see step 1a).
PROSE_SPANNING_LINE_FRACTION = 0.60

#: A block's line matching this shape ("Label: value...", e.g. "Level:
#: Sor/Wiz 3", "Casting Time: 1 standard action") is evidence of a spell or
#: monster stat block rather than a table cell (see step 1b).
_LABEL_VALUE_LINE_RE = re.compile(r"^[A-Z][A-Za-z' /()]{1,30}:\s")

#: A block needs at least this fraction of its (non-blank) lines to match
#: `_LABEL_VALUE_LINE_RE` to count as label:value-like (see step 1b).
LABEL_VALUE_LINE_FRACTION = 0.50

#: Within a table group, lines whose y-centers are within this fraction of
#: the median line height of each other belong to the same row (see the
#: "Emitting a table group" section above).
TABLE_ROW_FRACTION = 0.40

#: A single-block table's word gap must exceed this multiple of the
#: block's own median inter-word gap to count as "large" (see step 2a).
TABLE_GAP_MEDIAN_FACTOR = 2.5

#: ...or this multiple of the block's median word height, whichever is
#: greater -- a floor for blocks with few small gaps to take a median of.
TABLE_GAP_HEIGHT_FACTOR = 1.2

#: A row needs at least this many large gaps to count as "gappy" (see step
#: 2a).
TABLE_MIN_GAPS_PER_ROW = 2

#: A single block needs at least this many gappy rows to be a table (see
#: step 2a) -- fewer is an ordinary two-cell layout, not a table.
TABLE_MIN_GAPPY_ROWS = 3

#: A single-block table's gappy-row cells (split at the confirmed gap
#: x-positions) must have a median word count no higher than this many
#: to still be a table (see step 2a) -- a higher median means the "cells"
#: are full sentences, i.e. two prose columns `pdftotext` merged into one
#: block, not table cells.
TABLE_CELL_MAX_MEDIAN_WORDS = 4

#: A cluster of gappy rows' large-gap midpoints is a confirmed column
#: split only if at least this fraction of the gappy rows land in it (see
#: step 2a).
TABLE_GAP_CLUSTER_FRACTION = 0.60

#: Two vertically adjacent table-group candidates merge (step 2b) when
#: their x-extents overlap by at least this fraction of the narrower one's
#: x span.
TABLE_MERGE_X_OVERLAP_FRACTION = 0.80

#: ...and the vertical gap between them is at most this multiple of the
#: larger of the two candidates' median non-blank line heights (see step
#: 2b).
TABLE_MERGE_GAP_HEIGHT_FACTOR = 2.0

#: An orphan row fragment may overhang the group's own x span by up to this
#: multiple of the group's median non-blank line height and still be
#: absorbed (see step 2b, B10c-mand15): a group's x span is only as wide as
#: the widest cell detected *so far*, and the orphan's own cell is
#: frequently the widest one in its own column.
TABLE_ABSORB_X_OVERHANG_HEIGHT_FACTOR = 1.0

#: In a two-column label/value grid (see step 2c, B10c-mand15), the LABEL
#: column's cells must have a median word count no higher than this -- what
#: tells such a grid apart from two prose columns `pdftotext` merged into
#: one block, whose left-hand "cells" are whole sentences too.
LABEL_GRID_MAX_MEDIAN_LABEL_WORDS = 3

#: ...and at least this fraction of the block's own rows must carry the
#: gap (see step 2c): every row of a real two-column grid has one, whereas
#: ragged prose flowed around an illustration (PHB p.28's bard column)
#: produces a handful of accidentally aligned wide gaps among dozens of
#: ordinary lines.
LABEL_GRID_MIN_GAPPY_ROW_FRACTION = 0.50

#: A column cluster wider than `WIDE_BLOCK_FRACTION` of the text area is two
#: or more columns the greedy left-to-right chain bridged across a narrow
#: gutter; it is split at its widest internal valley provided that valley is
#: at least this multiple of the run's median word glyph height (see step
#: 4a, B10c-mand15).
COLUMN_VALLEY_GAP_HEIGHT_FACTOR = 1.0


def is_vertical_block(block: Block) -> bool:
    """Whether `block` is rotated/vertical text (see module docstring, step 1).

    Only lines with at least `VERTICAL_MIN_LINE_CHARS` characters are judged:
    a one-character line (e.g. a table cell holding the single digit `4`) is
    taller than it is wide in ordinary horizontal text too, so it carries no
    orientation evidence at all (see step 1). A block with no line long
    enough to judge -- like a block with no lines -- is not considered
    vertical.
    """
    judged = [line for line in block.lines if len(line.text.strip()) >= VERTICAL_MIN_LINE_CHARS]
    if not judged:
        return False
    return all(line.height > line.width for line in judged)


def _is_prose_like_block(block: Block) -> bool:
    """Whether `block` looks like justified running prose -- several lines
    of several words each, most of which stretch across nearly the whole
    block width -- as opposed to a table cell's short, ragged lines (see
    module docstring, step 1a). Prose-like blocks are excluded from table
    grouping entirely (step 2)."""
    lines = [line for line in block.lines if line.words]
    if len(lines) < PROSE_MIN_LINES:
        return False

    words_per_line = sorted(len(line.words) for line in lines)
    median_words = words_per_line[len(words_per_line) // 2]
    if median_words < PROSE_MIN_MEDIAN_WORDS_PER_LINE:
        return False

    block_width = block.width
    if block_width <= 0:
        return False
    spanning = sum(1 for line in lines if line.width >= PROSE_LINE_SPAN_FRACTION * block_width)
    return spanning / len(lines) >= PROSE_SPANNING_LINE_FRACTION


def _is_label_value_block(block: Block) -> bool:
    """Whether `block` looks like a spell's or monster's own stat block --
    mostly "Label: value" lines -- as opposed to a genuine table column's
    cells (see module docstring, step 1b). Like a prose-like block,
    label:value-like blocks are excluded from table grouping entirely
    (step 2): two adjacent stat blocks sharing a y-range otherwise satisfy
    the pairwise table-column test just as well as real table columns do."""
    lines = [line for line in block.lines if line.text.strip()]
    if not lines:
        return False
    matching = sum(1 for line in lines if _LABEL_VALUE_LINE_RE.match(line.text))
    return matching / len(lines) >= LABEL_VALUE_LINE_FRACTION


@dataclass(frozen=True)
class TableRow:
    """One row of a detected `TableGroup`: its cell texts, already ordered
    left to right."""

    cells: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\t".join(self.cells)


@dataclass(frozen=True)
class TableGroup:
    """A cluster of `TABLE_MIN_BLOCKS` or more blocks detected as a table
    (see module docstring, step 2), to be emitted row-wise instead of
    column-wise."""

    rows: list[TableRow]
    y_min: float
    #: Median/max word glyph height (`yMax - yMin`) across every word that
    #: contributed to this table group, for `owlsperch.text.runner`'s
    #: `.meta.json` sidecar (see B3) -- computed here because `TableRow` only
    #: keeps cell text, not the source `Word` bboxes.
    median_word_height: float = 0.0
    max_word_height: float = 0.0


def _text_area_width(blocks: list[Block]) -> float:
    x_min = min(b.x_min for b in blocks)
    x_max = max(b.x_max for b in blocks)
    width = x_max - x_min
    return width if width > 0 else 1.0


def _vertical_overlap_fractions(a: Block, b: Block) -> tuple[float, float]:
    """The vertical overlap between `a` and `b`, as a fraction of each of
    (the shorter block's height, the taller block's height) -- see step 2's
    two-sided overlap test."""
    overlap = min(a.y_max, b.y_max) - max(a.y_min, b.y_min)
    if overlap <= 0:
        return 0.0, 0.0
    height_a = a.y_max - a.y_min
    height_b = b.y_max - b.y_min
    shorter, taller = min(height_a, height_b), max(height_a, height_b)
    if shorter <= 0 or taller <= 0:
        return 0.0, 0.0
    return overlap / shorter, overlap / taller


def _x_extents_overlap(a: Block, b: Block) -> bool:
    return min(a.x_max, b.x_max) - max(a.x_min, b.x_min) > 0


def _has_min_lines_for_table_candidacy(block: Block) -> bool:
    """Whether `block` has enough non-blank lines to be a table-column
    candidate at all (see `TABLE_CANDIDATE_MIN_LINES`, step 2) -- a
    one-line block is a heading or caption, never a table column."""
    non_blank = sum(1 for line in block.lines if line.text.strip())
    return non_blank >= TABLE_CANDIDATE_MIN_LINES


def _is_table_pair(a: Block, b: Block) -> bool:
    if _x_extents_overlap(a, b):
        return False
    shorter_fraction, taller_fraction = _vertical_overlap_fractions(a, b)
    return (
        shorter_fraction >= TABLE_OVERLAP_FRACTION
        and taller_fraction >= TABLE_OVERLAP_TALLER_FRACTION
    )


def _cluster_1d[T](items: list[tuple[float, T]], tolerance: float) -> list[list[T]]:
    """Single-pass 1-D clustering shared by `_cluster_lines_into_rows` and
    `_confirmed_gap_splits`: `items` (each a `(key, payload)` pair) are
    visited in ascending `key` order, and a payload joins the current
    cluster when its key is within `tolerance` of that cluster's
    running-average key so far -- otherwise it starts a new cluster. Every
    item before the current one in sorted order has a key no greater than
    it, so the running average is always <= the current key, making a
    plain (rather than absolute) difference equivalent here.

    This is *not* what `_cluster_columns` does for column clustering (a
    different, extent-based test: a block joins a column while its `xMin`
    is close to that column's *rightmost extent so far*, not the average
    key of its members) -- the two are only superficially similar."""
    ordered = sorted(items, key=lambda item: item[0])
    clusters: list[list[T]] = []
    key_sums: list[float] = []
    for key, payload in ordered:
        if clusters:
            running_avg = key_sums[-1] / len(clusters[-1])
            if key - running_avg <= tolerance:
                clusters[-1].append(payload)
                key_sums[-1] += key
                continue
        clusters.append([payload])
        key_sums.append(key)
    return clusters


def _cluster_lines_into_rows(lines: list[Line]) -> list[list[Line]]:
    """Group `lines` into rows by y-center, top to bottom: lines whose
    y-centers are within `TABLE_ROW_FRACTION` of the median line height of
    each other belong to the same row (see "Emitting a table group" in the
    module docstring). Shared by multi-block table groups (step 2) and
    single-block table detection (step 2a)."""
    heights = sorted(line.height for line in lines)
    median_height = heights[len(heights) // 2] if heights[len(heights) // 2] > 0 else 1.0
    row_threshold = median_height * TABLE_ROW_FRACTION

    items = [((ln.y_min + ln.y_max) / 2, ln) for ln in lines]
    return _cluster_1d(items, row_threshold)


def _build_table_group(member_blocks: list[Block]) -> TableGroup:
    lines: list[Line] = [
        line for block in member_blocks for line in block.lines if line.text.strip()
    ]
    y_min = min(b.y_min for b in member_blocks)
    if not lines:
        return TableGroup(rows=[], y_min=y_min)

    row_clusters = _cluster_lines_into_rows(lines)
    rows = [
        TableRow(cells=[ln.text for ln in sorted(cluster, key=lambda ln: ln.x_min)])
        for cluster in row_clusters
    ]
    all_words = [w for line in lines for w in line.words if w.text]
    word_heights = [w.y_max - w.y_min for w in all_words]
    median_word_height = _median(word_heights, default=0.0)
    max_word_height = max(word_heights) if word_heights else 0.0
    return TableGroup(
        rows=rows,
        y_min=y_min,
        median_word_height=median_word_height,
        max_word_height=max_word_height,
    )


def _row_words(cluster: list[Line]) -> list[Word]:
    """Flatten one row cluster's lines into a single x-sorted word list --
    the unit single-block table detection measures gaps across (see step
    2a), regardless of how many separate `<line>`s the row's cells arrived
    as."""
    words = [w for line in cluster for w in line.words if w.text]
    return sorted(words, key=lambda w: w.x_min)


def _word_gaps(words: list[Word]) -> list[tuple[float, float]]:
    """Each consecutive pair's (gap size, gap midpoint x) for `words`,
    already x-sorted. Overlapping/adjacent words contribute no gap."""
    gaps = []
    for a, b in zip(words, words[1:], strict=False):
        gap = b.x_min - a.x_max
        if gap > 0:
            gaps.append((gap, (a.x_max + b.x_min) / 2))
    return gaps


def _median(values: list[float], default: float) -> float:
    if not values:
        return default
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def _confirmed_gap_splits(
    row_gap_midpoints: list[list[float]],
    gappy_row_indices: list[int],
    cluster_tolerance: float,
) -> list[float]:
    """Cluster the gappy rows' large-gap midpoints with `_cluster_1d` (the
    same single-pass, running-average algorithm `_cluster_lines_into_rows`
    uses) and return the x-position of every cluster hit by at least
    `TABLE_GAP_CLUSTER_FRACTION` of the gappy rows -- the confirmed column
    splits (see step 2a)."""
    items = [
        (mid, (mid, row_index))
        for row_index in gappy_row_indices
        for mid in row_gap_midpoints[row_index]
    ]
    clusters = _cluster_1d(items, cluster_tolerance)

    n_gappy = len(gappy_row_indices)
    return sorted(
        sum(m for m, _ in cluster) / len(cluster)
        for cluster in clusters
        if len({row_index for _, row_index in cluster}) / n_gappy >= TABLE_GAP_CLUSTER_FRACTION
    )


def _bucket_words_at(words: list[Word], splits: list[float]) -> list[list[Word]]:
    """Bucket `words` (x-sorted) into `len(splits) + 1` groups at the given
    x-positions, left to right."""
    buckets: list[list[Word]] = [[] for _ in range(len(splits) + 1)]
    for word in words:
        index = 0
        while index < len(splits) and word.x_min > splits[index]:
            index += 1
        buckets[index].append(word)
    return buckets


def _split_words_at(words: list[Word], splits: list[float]) -> list[str]:
    """Bucket `words` (x-sorted) into `len(splits) + 1` cells at the given
    x-positions, space-joining each cell's words."""
    return [" ".join(w.text for w in bucket) for bucket in _bucket_words_at(words, splits)]


def _build_prose_sub_blocks(lines: list[Line], splits: list[float]) -> list[Block]:
    """Split a single block's own `lines` into `len(splits) + 1` ordinary
    `Block`s at the given x-positions -- used when a block that looked
    like a single-block table (see step 2a) turns out to be two or more
    prose columns `pdftotext` merged into one block instead. Each original
    line's words are bucketed by x-position into the new column they fall
    in; a line entirely on one side of every split contributes only to
    that column, just as if `pdftotext` had kept it separate to begin
    with. The result is meant to be treated as ordinary blocks by the
    existing column ordering (step 3 onward), not re-checked for
    tabularity."""
    n_buckets = len(splits) + 1
    bucket_lines: list[list[Line]] = [[] for _ in range(n_buckets)]
    for line in sorted(lines, key=lambda ln: ln.y_min):
        word_buckets = _bucket_words_at(sorted(line.words, key=lambda w: w.x_min), splits)
        for index, words in enumerate(word_buckets):
            if not words:
                continue
            bucket_lines[index].append(
                Line(
                    x_min=min(w.x_min for w in words),
                    y_min=min(w.y_min for w in words),
                    x_max=max(w.x_max for w in words),
                    y_max=max(w.y_max for w in words),
                    words=words,
                )
            )

    sub_blocks: list[Block] = []
    for column_lines in bucket_lines:
        if not column_lines:
            continue
        sub_blocks.append(
            Block(
                x_min=min(ln.x_min for ln in column_lines),
                y_min=min(ln.y_min for ln in column_lines),
                x_max=max(ln.x_max for ln in column_lines),
                y_max=max(ln.y_max for ln in column_lines),
                lines=column_lines,
            )
        )
    return sub_blocks


def _single_block_table_candidate(
    lines: list[Line],
) -> tuple[list[list[Word]], float, float] | None:
    """Step 1 of single-block table detection (see step 2a in the module
    docstring): bucket `lines` into rows the same way a table group's rows
    are (see "Emitting a table group"), then compute the block's "large
    gap" threshold from its own word spacing. Returns `(rows_words,
    threshold, median_height)`, or `None` if the block has no words to
    measure at all."""
    row_clusters = _cluster_lines_into_rows(lines)
    rows_words = [_row_words(cluster) for cluster in row_clusters]

    all_words = [word for row in rows_words for word in row]
    if not all_words:
        return None
    median_height = _median([w.y_max - w.y_min for w in all_words], default=1.0)

    # The block's "normal" word-spacing baseline is measured within each
    # original <line> (pdftotext's own line-breaking), not across the row
    # cluster's merged word list: two words `pdftotext` put in separate
    # `<line>`s are frequently separate table cells, so a gap between them
    # is exactly the kind of "large" gap this is trying to detect --
    # folding it into the baseline would only inflate the baseline and
    # mask real column gaps.
    intra_line_gaps = [
        gap for line in lines for gap, _ in _word_gaps(sorted(line.words, key=lambda w: w.x_min))
    ]
    median_gap = _median(intra_line_gaps, default=0.0)
    threshold = max(TABLE_GAP_MEDIAN_FACTOR * median_gap, TABLE_GAP_HEIGHT_FACTOR * median_height)
    return rows_words, threshold, median_height


def _gappy_rows(
    rows_words: list[list[Word]], threshold: float, min_gaps: int = TABLE_MIN_GAPS_PER_ROW
) -> tuple[list[list[float]], list[int]] | None:
    """Step 2: each row's large-gap midpoints, and which rows are "gappy"
    (see `TABLE_MIN_GAPS_PER_ROW`, step 2a). Returns `None` if fewer than
    `TABLE_MIN_GAPPY_ROWS` rows qualify -- too few to be a table at all.
    `min_gaps` is `TABLE_MIN_GAPS_PER_ROW` for an ordinary (3-or-more
    column) single-block table and 1 for step 2c's two-column label/value
    grid, whose rows have exactly one gap each."""
    row_gap_midpoints = [
        [mid for gap, mid in _word_gaps(row) if gap > threshold] for row in rows_words
    ]
    gappy_row_indices = [
        i for i, midpoints in enumerate(row_gap_midpoints) if len(midpoints) >= min_gaps
    ]
    if len(gappy_row_indices) < TABLE_MIN_GAPPY_ROWS:
        return None
    return row_gap_midpoints, gappy_row_indices


def _build_single_block_table_or_prose_split(
    block: Block,
    lines: list[Line],
    rows_words: list[list[Word]],
    confirmed_splits: list[float],
    gappy_row_indices: list[int],
    median_height: float,
) -> TableGroup | list[Block]:
    """Step 3: given confirmed column splits, decide whether the gappy
    rows' cells are table cells (build the `TableGroup`) or full sentences
    -- two prose columns `pdftotext` merged into one block (see step 2a's
    addendum in the module docstring) -- and split into separate column
    `Block`s instead."""
    # A table cell is short -- a name, a number, a die code. If the
    # confirmed splits' cells are, on the whole, full sentences instead,
    # this is two prose columns merged into one block, not a table.
    gappy_cell_word_counts = [
        float(len(cell_words))
        for i in gappy_row_indices
        for cell_words in _bucket_words_at(rows_words[i], confirmed_splits)
    ]
    median_words_per_cell = _median(gappy_cell_word_counts, default=0.0)
    if median_words_per_cell > TABLE_CELL_MAX_MEDIAN_WORDS:
        return _build_prose_sub_blocks(lines, confirmed_splits)

    gappy = set(gappy_row_indices)
    rows = [
        TableRow(cells=_split_words_at(words, confirmed_splits))
        if i in gappy
        else TableRow(cells=[" ".join(w.text for w in words)])
        for i, words in enumerate(rows_words)
    ]
    all_words = [word for row in rows_words for word in row]
    max_height = max(w.y_max - w.y_min for w in all_words)
    return TableGroup(
        rows=rows,
        y_min=block.y_min,
        median_word_height=median_height,
        max_word_height=max_height,
    )


def _detect_label_value_grid(
    block: Block,
    lines: list[Line],
    rows_words: list[list[Word]],
    threshold: float,
    median_height: float,
) -> TableGroup | None:
    """Step 2c (B10c-mand15): a *two*-column label/value grid `pdftotext`
    kept as one block -- e.g. the PHB's FAMILIARS sidebar's `Familiar |
    Special` list, whose every row is a one-word animal name beside a whole
    sentence. Every row has exactly ONE large gap, so step 2a's
    `TABLE_MIN_GAPS_PER_ROW` (2) can never see it, and its right-hand cells
    are sentences, so step 2a's cell-length check would call it two merged
    prose columns.

    Two things tell the grid apart from ordinary prose that happens to
    contain a few wide gaps. First, *how many* rows carry the gap: every row
    of a real grid does (at least `LABEL_GRID_MIN_GAPPY_ROW_FRACTION` of
    them), whereas prose flowed around an illustration -- PHB p.28's bard
    column, whose ragged right edge and one embedded caption produce three
    accidentally aligned gaps among 35 lines -- has only a handful. Second,
    the LABEL side: a grid's left-hand cells are a name or a code (a median
    of at most `LABEL_GRID_MAX_MEDIAN_LABEL_WORDS` words), a merged prose
    column's are sentences.

    This fallback runs only once ordinary detection has declined the block,
    and returns `None` -- leaving the block exactly as it was -- rather than
    ever falling through to a prose split, so the path is strictly additive:
    it can only turn a block that used to be flattened prose into a table
    group."""
    gappy = _gappy_rows(rows_words, threshold, min_gaps=1)
    if gappy is None:
        return None
    row_gap_midpoints, gappy_row_indices = gappy
    if len(gappy_row_indices) < LABEL_GRID_MIN_GAPPY_ROW_FRACTION * len(rows_words):
        return None

    confirmed_splits = _confirmed_gap_splits(row_gap_midpoints, gappy_row_indices, median_height)
    if len(confirmed_splits) != 1:
        return None

    label_word_counts = [
        float(len(_bucket_words_at(rows_words[i], confirmed_splits)[0])) for i in gappy_row_indices
    ]
    if _median(label_word_counts, default=0.0) > LABEL_GRID_MAX_MEDIAN_LABEL_WORDS:
        return None

    built = _build_single_block_table_or_prose_split(
        block, lines, rows_words, confirmed_splits, gappy_row_indices, median_height
    )
    return built if isinstance(built, TableGroup) else None


def _detect_single_block_table_group(block: Block) -> TableGroup | list[Block] | None:
    """If `block`'s own lines look like a table flattened into one block
    (see step 2a in the module docstring), return the `TableGroup` it
    represents. If they instead look like two (or more) prose columns
    merged into one block -- the same consistent-gap shape, but with
    sentence-like cells -- return the `Block`s to split it into. Otherwise
    `None`. Done in three steps: `_single_block_table_candidate` (row
    bucketing and gap threshold), `_gappy_rows` (which rows qualify), and
    `_build_single_block_table_or_prose_split` (the final table-vs-prose
    decision and result). A block ordinary detection declines gets one last
    chance as a two-column label/value grid (`_detect_label_value_grid`,
    step 2c)."""
    lines = [line for line in block.lines if line.words]
    if not lines:
        return None

    candidate = _single_block_table_candidate(lines)
    if candidate is None:
        return None
    rows_words, threshold, median_height = candidate

    gappy = _gappy_rows(rows_words, threshold)
    if gappy is None:
        return _detect_label_value_grid(block, lines, rows_words, threshold, median_height)
    row_gap_midpoints, gappy_row_indices = gappy

    confirmed_splits = _confirmed_gap_splits(row_gap_midpoints, gappy_row_indices, median_height)
    if not confirmed_splits:
        return _detect_label_value_grid(block, lines, rows_words, threshold, median_height)

    return _build_single_block_table_or_prose_split(
        block, lines, rows_words, confirmed_splits, gappy_row_indices, median_height
    )


def _maximal_cliques(nodes: set[int], adjacency: list[set[int]]) -> list[set[int]]:
    """Every maximal clique of the subgraph induced by `nodes` (Bron-Kerbosch,
    no pivoting) -- a page has few enough non-prose candidate blocks that the
    naive algorithm is fine. Used so a table group requires every member to
    mutually overlap every other member, not just be transitively connected
    (see module docstring, step 2)."""
    results: list[set[int]] = []

    def expand(r: set[int], p: set[int], x: set[int]) -> None:
        if not p and not x:
            results.append(r)
            return
        for v in list(p):
            neighbors = adjacency[v] & nodes
            expand(r | {v}, p & neighbors, x & neighbors)
            p = p - {v}
            x = x | {v}

    expand(set(), set(nodes), set())
    return results


def _connected_components(nodes: list[int], adjacency: list[set[int]]) -> list[set[int]]:
    visited: set[int] = set()
    components: list[set[int]] = []
    for start in nodes:
        if start in visited:
            continue
        component = {start}
        queue = [start]
        while queue:
            current = queue.pop()
            for neighbor in adjacency[current]:
                if neighbor not in component:
                    component.add(neighbor)
                    queue.append(neighbor)
        visited |= component
        components.append(component)
    return components


def _group_extent(blocks: list[Block]) -> tuple[float, float, float, float]:
    """(x_min, x_max, y_min, y_max) spanned by `blocks` together."""
    return (
        min(b.x_min for b in blocks),
        max(b.x_max for b in blocks),
        min(b.y_min for b in blocks),
        max(b.y_max for b in blocks),
    )


def _group_median_line_height(blocks: list[Block]) -> float:
    heights = [line.height for b in blocks for line in b.lines if line.text.strip()]
    return _median(heights, default=1.0)


def _qualifying_row_count(blocks: list[Block]) -> int:
    """How many of `_build_table_group(blocks)`'s rows have at least 2
    cells -- the measure step 2b's merge/absorb guard must not decrease
    (see module docstring, step 2b)."""
    return sum(1 for row in _build_table_group(blocks).rows if len(row.cells) >= 2)


def _groups_can_merge(a: list[Block], b: list[Block]) -> bool:
    """Whether table-group candidates `a` and `b` are vertically adjacent
    parts of the same printed table (see module docstring, step 2b): their
    x-extents overlap by at least `TABLE_MERGE_X_OVERLAP_FRACTION` of the
    narrower one's x span, and (with `a` above `b`) the vertical gap
    between them is at most `TABLE_MERGE_GAP_HEIGHT_FACTOR` times the
    larger of their own median non-blank line heights."""
    a_x_min, a_x_max, _, a_y_max = _group_extent(a)
    b_x_min, b_x_max, b_y_min, _ = _group_extent(b)
    overlap = min(a_x_max, b_x_max) - max(a_x_min, b_x_min)
    narrower = min(a_x_max - a_x_min, b_x_max - b_x_min)
    if narrower <= 0 or overlap / narrower < TABLE_MERGE_X_OVERLAP_FRACTION:
        return False
    gap = b_y_min - a_y_max
    if gap < 0:
        return False
    threshold = max(_group_median_line_height(a), _group_median_line_height(b))
    return gap <= TABLE_MERGE_GAP_HEIGHT_FACTOR * threshold


def _merge_adjacent_groups(groups: list[list[Block]]) -> list[list[Block]] | None:
    """Try to merge one vertically adjacent pair of `groups` (step 2b,
    part A); return the new list of groups with that pair concatenated, or
    `None` if no pair both qualifies (`_groups_can_merge`) and passes the
    row-count guard."""
    ordered = sorted(groups, key=lambda g: _group_extent(g)[2])
    for i in range(len(ordered) - 1):
        a, b = ordered[i], ordered[i + 1]
        if not _groups_can_merge(a, b):
            continue
        before = _qualifying_row_count(a) + _qualifying_row_count(b)
        merged = a + b
        if _qualifying_row_count(merged) < before:
            continue
        rest = [g for k, g in enumerate(ordered) if k != i and k != i + 1]
        return [merged, *rest]
    return None


def _block_fits_group(block: Block, group: list[Block]) -> bool:
    """Whether `block` is an orphan single-cell fragment belonging inside
    `group` (step 2b, part B): not prose-like or label:value-like (the
    same exclusions steps 1a/1b apply to real table columns), its
    x-extent inside the group's own x span -- allowing an overhang of up
    to `TABLE_ABSORB_X_OVERHANG_HEIGHT_FACTOR` of the group's own median
    non-blank line height on either side (B10c-mand15: the group's x span
    is only as wide as the cells it has already claimed, and an orphan
    row's own cell is frequently the widest one in its column) -- and its
    y-center within one of the group's own median non-blank line heights
    of the group's y span. Deliberately does not require
    `_has_min_lines_for_table_candidacy` -- a single-line orphan row
    fragment is exactly what this looks for."""
    if _is_prose_like_block(block) or _is_label_value_block(block):
        return False
    x_min, x_max, y_min, y_max = _group_extent(group)
    height = _group_median_line_height(group)
    overhang = TABLE_ABSORB_X_OVERHANG_HEIGHT_FACTOR * height
    if block.x_min < x_min - overhang or block.x_max > x_max + overhang:
        return False
    y_center = (block.y_min + block.y_max) / 2
    return y_min - height <= y_center <= y_max + height


def _absorb_orphan_blocks(
    groups: list[list[Block]], pool: list[Block]
) -> tuple[list[list[Block]], list[Block]] | None:
    """Try to absorb one leftover block from `pool` into one of `groups`
    (step 2b, part B); return the updated `(groups, pool)`, or `None` if
    no block both fits (`_block_fits_group`) and passes the row-count
    guard."""
    for gi, group in enumerate(groups):
        for bi, block in enumerate(pool):
            if not _block_fits_group(block, group):
                continue
            before = _qualifying_row_count(group) + _qualifying_row_count([block])
            if _qualifying_row_count([*group, block]) < before:
                continue
            new_groups = [*groups[:gi], [*group, block], *groups[gi + 1 :]]
            new_pool = [*pool[:bi], *pool[bi + 1 :]]
            return new_groups, new_pool
    return None


def _reassemble_table_groups(
    groups: list[list[Block]], pool: list[Block]
) -> tuple[list[list[Block]], list[Block]]:
    """Alternate merging vertically adjacent table-group candidates and
    absorbing orphan single-cell blocks into them to a joint fixpoint (see
    module docstring, step 2b) -- a single merge-then-absorb pass is not
    enough, since absorbing an orphan row can be exactly what brings a gap
    down under the merge threshold."""
    while True:
        merged = _merge_adjacent_groups(groups)
        if merged is not None:
            groups = merged
            continue
        absorbed = _absorb_orphan_blocks(groups, pool)
        if absorbed is not None:
            groups, pool = absorbed
            continue
        return groups, pool


def _extract_table_groups(blocks: list[Block]) -> tuple[list[TableGroup], list[Block]]:
    """Split `blocks` into (table groups, remaining non-table blocks) per
    steps 1a/2/2b of the module docstring."""
    n = len(blocks)
    candidate_indices = [
        i
        for i in range(n)
        if not _is_prose_like_block(blocks[i])
        and not _is_label_value_block(blocks[i])
        and _has_min_lines_for_table_candidacy(blocks[i])
    ]

    adjacency: list[set[int]] = [set() for _ in range(n)]
    for idx, i in enumerate(candidate_indices):
        for j in candidate_indices[idx + 1 :]:
            if _is_table_pair(blocks[i], blocks[j]):
                adjacency[i].add(j)
                adjacency[j].add(i)

    claimed: set[int] = set()
    groups: list[list[Block]] = []
    for component in _connected_components(candidate_indices, adjacency):
        if len(component) < TABLE_MIN_BLOCKS:
            continue
        cliques = _maximal_cliques(component, adjacency)
        for clique in sorted(cliques, key=len, reverse=True):
            available = clique - claimed
            if len(available) >= TABLE_MIN_BLOCKS:
                claimed |= available
                groups.append([blocks[k] for k in sorted(available)])

    pool = [blocks[i] for i in range(n) if i not in claimed]
    groups, pool = _reassemble_table_groups(groups, pool)
    table_groups = [_build_table_group(g) for g in groups]
    return table_groups, pool


def _run_median_word_height(blocks: list[Block]) -> float:
    heights = [
        word.y_max - word.y_min
        for block in blocks
        for line in block.lines
        for word in line.words
        if word.text
    ]
    return _median(heights, default=1.0)


def _widest_valley(blocks: list[Block]) -> tuple[float, float] | None:
    """The widest x-interval inside `blocks`' own x span that none of them
    overlaps (see step 4a), or `None` when their x-extents cover that span
    with no gap at all."""
    intervals = sorted((b.x_min, b.x_max) for b in blocks)
    widest: tuple[float, float] | None = None
    reach = intervals[0][1]
    for x_min, x_max in intervals[1:]:
        if x_min > reach and (widest is None or x_min - reach > widest[1] - widest[0]):
            widest = (reach, x_min)
        reach = max(reach, x_max)
    return widest


def _split_over_wide_cluster(
    cluster: list[Block], wide_threshold: float, min_valley: float
) -> list[list[Block]]:
    """Split a column cluster the greedy chain of step 4 over-merged across
    a narrow gutter (see step 4a), left to right, recursing on each piece.

    A cluster whose own x span is wider than `wide_threshold` -- the same
    `WIDE_BLOCK_FRACTION` of the text area that makes a *block* a column
    break -- cannot be one printed column, since no block that wide ever
    reaches column clustering in the first place (step 3 takes it out as a
    break). It is split at its widest internal valley, provided that valley
    is at least `min_valley` wide; a cluster with no qualifying valley is
    left as it is."""
    if len(cluster) < 2:
        return [cluster]
    span = max(b.x_max for b in cluster) - min(b.x_min for b in cluster)
    if span <= wide_threshold:
        return [cluster]
    valley = _widest_valley(cluster)
    if valley is None or valley[1] - valley[0] < min_valley:
        return [cluster]

    left = [b for b in cluster if b.x_max <= valley[0]]
    right = [b for b in cluster if b.x_min >= valley[1]]
    if not left or not right or len(left) + len(right) != len(cluster):
        return [cluster]
    return [
        *_split_over_wide_cluster(left, wide_threshold, min_valley),
        *_split_over_wide_cluster(right, wide_threshold, min_valley),
    ]


def _cluster_columns(blocks: list[Block], wide_threshold: float) -> list[Block]:
    """Cluster `blocks` into left-to-right columns by a gap-based split of
    their x-extents (see step 4), returning them concatenated column by
    column, top to bottom within each column. The split threshold is
    relative to the run's own text size, which is what lets this handle any
    number of columns (one, two, three, four, ...) rather than assuming
    two. Any cluster the greedy pass over-merged across a narrow gutter is
    then repaired by `_split_over_wide_cluster` (step 4a)."""
    if not blocks:
        return []

    median_word_height = _run_median_word_height(blocks)
    threshold = median_word_height * COLUMN_GAP_HEIGHT_FACTOR
    min_valley = median_word_height * COLUMN_VALLEY_GAP_HEIGHT_FACTOR

    ordered = sorted(blocks, key=lambda b: b.x_min)
    clusters: list[list[Block]] = [[ordered[0]]]
    cluster_max_x: list[float] = [ordered[0].x_max]
    for block in ordered[1:]:
        gap = block.x_min - cluster_max_x[-1]
        if gap >= threshold:
            clusters.append([block])
            cluster_max_x.append(block.x_max)
        else:
            clusters[-1].append(block)
            cluster_max_x[-1] = max(cluster_max_x[-1], block.x_max)

    result: list[Block] = []
    for cluster in clusters:
        for piece in _split_over_wide_cluster(cluster, wide_threshold, min_valley):
            result.extend(sorted(piece, key=lambda b: b.y_min))
    return result


def order_blocks(page: Page) -> list[Block | TableGroup]:
    """Return `page`'s blocks (and any detected table groups) in reading
    order (see module docstring)."""
    blocks = [b for b in page.blocks if not is_vertical_block(b)]
    if not blocks:
        return []

    text_area_width = _text_area_width(blocks)
    wide_threshold = text_area_width * WIDE_BLOCK_FRACTION

    table_groups, remaining_blocks = _extract_table_groups(blocks)

    single_block_tables: list[TableGroup] = []
    other_blocks: list[Block] = []
    for block in remaining_blocks:
        detected = _detect_single_block_table_group(block)
        if isinstance(detected, TableGroup):
            single_block_tables.append(detected)
        elif detected is not None:
            other_blocks.extend(detected)
        else:
            other_blocks.append(block)

    combined: list[tuple[float, Block | TableGroup]] = []
    combined.extend((b.y_min, b) for b in other_blocks)
    combined.extend((tg.y_min, tg) for tg in table_groups)
    combined.extend((tg.y_min, tg) for tg in single_block_tables)
    combined.sort(key=lambda item: item[0])

    result: list[Block | TableGroup] = []
    run: list[Block] = []
    for _, item in combined:
        if isinstance(item, TableGroup):
            result.extend(_cluster_columns(run, wide_threshold))
            result.append(item)
            run = []
        elif item.width > wide_threshold:
            result.extend(_cluster_columns(run, wide_threshold))
            result.append(item)
            run = []
        else:
            run.append(item)
    result.extend(_cluster_columns(run, wide_threshold))
    return result
