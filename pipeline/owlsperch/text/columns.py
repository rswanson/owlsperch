"""Reading-order reconstruction for one page of `bbox.Block`s.

Algorithm (per page):

1. **Drop vertical/rotated blocks.** A block all of whose lines are taller
   than they are wide (`line.height > line.width` for every line) is glyph
   text rotated 90 degrees -- in practice, a page-edge chapter/section tab
   printed sideways in the margin. These carry no reading-order position
   relative to the body columns, so they are excluded from the output
   entirely rather than assigned to a column. (The caller -- see
   `owlsperch.text.runner` -- separately counts how many blocks this drops,
   since the same rule also discards image credits like "Illus. by ...",
   and that loss is otherwise invisible.)

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

2. **Detect table groups.** `pdftotext` frequently emits each column of a
   multi-column table (e.g. a weapon table: name, cost, damage, critical,
   type) as its own narrow block rather than one block per row. Left to
   step 3's column clustering, these narrow blocks would each become their
   own "column" and get emitted one after another -- i.e. column-major
   (every name, then every cost, then every damage) instead of row-major.
   To detect this: among the remaining non-vertical, non-prose-like blocks,
   find every pair whose vertical extents overlap by at least
   `TABLE_OVERLAP_FRACTION` (70%) of the shorter block's height *and* whose
   x-extents do not overlap at all. A **table group** is a *clique* of such
   pairs -- every member overlaps every other member this way, not merely
   chained transitively (A-B and B-C does not imply A-C) -- of at least
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

5. **Emit columns left to right, each column's blocks top to bottom**
   (sorted by `yMin`), then continue with the next run after its wide
   block or table group.

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

from dataclasses import dataclass, field

from owlsperch.text.bbox import Block, Line, Page, Word

#: A block wider than this fraction of the text area is treated as a column
#: break (a full-width table or heading) rather than clustered into a column.
WIDE_BLOCK_FRACTION = 0.6

#: Column-clustering threshold, as a multiple of the run's median word
#: glyph height: a block starts a new column when the horizontal gap from
#: the current column's rightmost extent so far is at least this wide (see
#: step 4).
COLUMN_GAP_HEIGHT_FACTOR = 1.5

#: Two blocks are candidate table columns if their vertical extents overlap
#: by at least this fraction of the shorter block's height (see step 2).
TABLE_OVERLAP_FRACTION = 0.70

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


def is_vertical_block(block: Block) -> bool:
    """Whether `block` is rotated/vertical text (see module docstring, step 1).

    A block with no lines is not considered vertical (nothing to judge).
    """
    if not block.lines:
        return False
    return all(line.height > line.width for line in block.lines)


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


def _vertical_overlap_fraction(a: Block, b: Block) -> float:
    overlap = min(a.y_max, b.y_max) - max(a.y_min, b.y_min)
    if overlap <= 0:
        return 0.0
    shorter = min(a.y_max - a.y_min, b.y_max - b.y_min)
    if shorter <= 0:
        return 0.0
    return overlap / shorter


def _x_extents_overlap(a: Block, b: Block) -> bool:
    return min(a.x_max, b.x_max) - max(a.x_min, b.x_min) > 0


def _is_table_pair(a: Block, b: Block) -> bool:
    return _vertical_overlap_fraction(a, b) >= TABLE_OVERLAP_FRACTION and not _x_extents_overlap(
        a, b
    )


def _cluster_lines_into_rows(lines: list[Line]) -> list[list[Line]]:
    """Group `lines` into rows by y-center, top to bottom: lines whose
    y-centers are within `TABLE_ROW_FRACTION` of the median line height of
    each other belong to the same row (see "Emitting a table group" in the
    module docstring). Shared by multi-block table groups (step 2) and
    single-block table detection (step 2a)."""
    heights = sorted(line.height for line in lines)
    median_height = heights[len(heights) // 2] if heights[len(heights) // 2] > 0 else 1.0
    row_threshold = median_height * TABLE_ROW_FRACTION

    sorted_lines = sorted(lines, key=lambda ln: (ln.y_min + ln.y_max) / 2)
    row_clusters: list[list[Line]] = []
    row_center_sums: list[float] = []
    for line in sorted_lines:
        center = (line.y_min + line.y_max) / 2
        if row_clusters:
            last_avg = row_center_sums[-1] / len(row_clusters[-1])
            if abs(center - last_avg) <= row_threshold:
                row_clusters[-1].append(line)
                row_center_sums[-1] += center
                continue
        row_clusters.append([line])
        row_center_sums.append(center)
    return row_clusters


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
    """Cluster the gappy rows' large-gap midpoints (single-pass, by
    running average, like `_cluster_columns`) and return the x-position of
    every cluster hit by at least `TABLE_GAP_CLUSTER_FRACTION` of the
    gappy rows -- the confirmed column splits (see step 2a)."""
    entries = sorted((mid, i) for i in gappy_row_indices for mid in row_gap_midpoints[i])
    clusters: list[list[tuple[float, int]]] = []
    cluster_sums: list[float] = []
    for mid, row_index in entries:
        if clusters:
            running_avg = cluster_sums[-1] / len(clusters[-1])
            if mid - running_avg <= cluster_tolerance:
                clusters[-1].append((mid, row_index))
                cluster_sums[-1] += mid
                continue
        clusters.append([(mid, row_index)])
        cluster_sums.append(mid)

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


def _detect_single_block_table_group(block: Block) -> TableGroup | list[Block] | None:
    """If `block`'s own lines look like a table flattened into one block
    (see step 2a in the module docstring), return the `TableGroup` it
    represents. If they instead look like two (or more) prose columns
    merged into one block -- the same consistent-gap shape, but with
    sentence-like cells -- return the `Block`s to split it into. Otherwise
    `None`."""
    lines = [line for line in block.lines if line.words]
    if not lines:
        return None

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

    row_gap_midpoints = [
        [mid for gap, mid in _word_gaps(row) if gap > threshold] for row in rows_words
    ]
    gappy_row_indices = [
        i
        for i, midpoints in enumerate(row_gap_midpoints)
        if len(midpoints) >= TABLE_MIN_GAPS_PER_ROW
    ]
    if len(gappy_row_indices) < TABLE_MIN_GAPPY_ROWS:
        return None

    confirmed_splits = _confirmed_gap_splits(row_gap_midpoints, gappy_row_indices, median_height)
    if not confirmed_splits:
        return None

    # A table cell is short -- a name, a number, a die code. If the
    # confirmed splits' cells are, on the whole, full sentences instead,
    # this is two prose columns `pdftotext` merged into one block, not a
    # table (see step 2a's addendum in the module docstring): split the
    # block into separate column blocks instead of reading it row-wise.
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
    max_height = max(w.y_max - w.y_min for w in all_words)
    return TableGroup(
        rows=rows,
        y_min=block.y_min,
        median_word_height=median_height,
        max_word_height=max_height,
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


def _extract_table_groups(blocks: list[Block]) -> tuple[list[TableGroup], list[Block]]:
    """Split `blocks` into (table groups, remaining non-table blocks) per
    steps 1a/2 of the module docstring."""
    n = len(blocks)
    candidate_indices = [i for i in range(n) if not _is_prose_like_block(blocks[i])]

    adjacency: list[set[int]] = [set() for _ in range(n)]
    for idx, i in enumerate(candidate_indices):
        for j in candidate_indices[idx + 1 :]:
            if _is_table_pair(blocks[i], blocks[j]):
                adjacency[i].add(j)
                adjacency[j].add(i)

    claimed: set[int] = set()
    table_groups: list[TableGroup] = []
    for component in _connected_components(candidate_indices, adjacency):
        if len(component) < TABLE_MIN_BLOCKS:
            continue
        cliques = _maximal_cliques(component, adjacency)
        for clique in sorted(cliques, key=len, reverse=True):
            available = clique - claimed
            if len(available) >= TABLE_MIN_BLOCKS:
                claimed |= available
                table_groups.append(_build_table_group([blocks[k] for k in sorted(available)]))

    remaining = [blocks[i] for i in range(n) if i not in claimed]
    return table_groups, remaining


def _run_median_word_height(blocks: list[Block]) -> float:
    heights = [
        word.y_max - word.y_min
        for block in blocks
        for line in block.lines
        for word in line.words
        if word.text
    ]
    return _median(heights, default=1.0)


def _cluster_columns(blocks: list[Block]) -> list[Block]:
    """Cluster `blocks` into left-to-right columns by a gap-based split of
    their x-extents (see step 4), returning them concatenated column by
    column, top to bottom within each column. The split threshold is
    relative to the run's own text size, which is what lets this handle any
    number of columns (one, two, three, four, ...) rather than assuming
    two."""
    if not blocks:
        return []

    threshold = _run_median_word_height(blocks) * COLUMN_GAP_HEIGHT_FACTOR

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
        result.extend(sorted(cluster, key=lambda b: b.y_min))
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
            result.extend(_cluster_columns(run))
            result.append(item)
            run = []
        elif item.width > wide_threshold:
            result.extend(_cluster_columns(run))
            result.append(item)
            run = []
        else:
            run.append(item)
    result.extend(_cluster_columns(run))
    return result
