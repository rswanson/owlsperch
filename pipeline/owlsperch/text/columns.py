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

2. **Detect table groups.** `pdftotext` frequently emits each column of a
   multi-column table (e.g. a weapon table: name, cost, damage, critical,
   type) as its own narrow block rather than one block per row. Left to
   step 3's column clustering, these narrow blocks would each become their
   own "column" and get emitted one after another -- i.e. column-major
   (every name, then every cost, then every damage) instead of row-major.
   To detect this: among the remaining (non-vertical) blocks, find every
   pair whose vertical extents overlap by at least `TABLE_OVERLAP_FRACTION`
   (70%) of the shorter block's height *and* whose x-extents do not
   overlap at all. Blocks connected (transitively) by such pairs form a
   group; a group of at least `TABLE_MIN_BLOCKS` (3) blocks is a **table
   group** and is pulled out of the normal column-clustering blocks
   entirely.

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
   blocks are sorted by `xMin` and assigned to columns greedily: each
   block joins the existing column whose running x-center average is
   closest, if within `COLUMN_GAP_FRACTION` (25%) of the text-area width;
   otherwise it starts a new column. This is a simple single-pass
   clustering, not k-means -- sufficient for the typical one- or
   two-column D&D 3.5e rulebook layouts this pipeline targets.

5. **Emit columns left to right, each column's blocks top to bottom**
   (sorted by `yMin`), then continue with the next run after its wide
   block or table group.

**Emitting a table group.** A table group's member blocks' lines are
collected into one flat list. Lines are bucketed into rows by y-center:
lines whose y-centers are within `TABLE_ROW_FRACTION` (40%) of the median
line height of each other belong to the same row. Rows are ordered top to
bottom; within a row, cells (one per contributing line) are ordered left to
right by `xMin` and joined with a single tab character. The caller (see
`owlsperch.text.runner`) renders a table group's rows one per output line,
surrounded by blank lines -- like a wide block, a table group is a column
break for the surrounding prose.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from owlsperch.text.bbox import Block, Line, Page

#: A block wider than this fraction of the text area is treated as a column
#: break (a full-width table or heading) rather than clustered into a column.
WIDE_BLOCK_FRACTION = 0.6

#: Greedy column-clustering threshold, as a fraction of the text-area width:
#: a block joins the nearest existing column if its x-center is within this
#: distance of that column's running average center.
COLUMN_GAP_FRACTION = 0.25

#: Two blocks are candidate table columns if their vertical extents overlap
#: by at least this fraction of the shorter block's height (see step 2).
TABLE_OVERLAP_FRACTION = 0.70

#: A group of blocks connected by the table-column relationship is only
#: treated as a table if it has at least this many members (see step 2) --
#: a two-block group is an ordinary two-column prose layout.
TABLE_MIN_BLOCKS = 3

#: Within a table group, lines whose y-centers are within this fraction of
#: the median line height of each other belong to the same row (see the
#: "Emitting a table group" section above).
TABLE_ROW_FRACTION = 0.40


def is_vertical_block(block: Block) -> bool:
    """Whether `block` is rotated/vertical text (see module docstring, step 1).

    A block with no lines is not considered vertical (nothing to judge).
    """
    if not block.lines:
        return False
    return all(line.height > line.width for line in block.lines)


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


def _build_table_group(member_blocks: list[Block]) -> TableGroup:
    lines: list[Line] = [
        line for block in member_blocks for line in block.lines if line.text.strip()
    ]
    y_min = min(b.y_min for b in member_blocks)
    if not lines:
        return TableGroup(rows=[], y_min=y_min)

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

    rows = [
        TableRow(cells=[ln.text for ln in sorted(cluster, key=lambda ln: ln.x_min)])
        for cluster in row_clusters
    ]
    return TableGroup(rows=rows, y_min=y_min)


def _extract_table_groups(blocks: list[Block]) -> tuple[list[TableGroup], list[Block]]:
    """Split `blocks` into (table groups, remaining non-table blocks) per
    step 2 of the module docstring."""
    n = len(blocks)
    adjacency: list[set[int]] = [set() for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            if _is_table_pair(blocks[i], blocks[j]):
                adjacency[i].add(j)
                adjacency[j].add(i)

    visited: set[int] = set()
    table_groups: list[TableGroup] = []
    remaining: list[Block] = []
    for i in range(n):
        if i in visited:
            continue
        component = {i}
        queue = [i]
        while queue:
            current = queue.pop()
            for neighbor in adjacency[current]:
                if neighbor not in component:
                    component.add(neighbor)
                    queue.append(neighbor)
        visited |= component
        if len(component) >= TABLE_MIN_BLOCKS:
            table_groups.append(_build_table_group([blocks[k] for k in sorted(component)]))
        else:
            remaining.extend(blocks[k] for k in sorted(component))
    return table_groups, remaining


def _cluster_columns(blocks: list[Block], text_area_width: float) -> list[Block]:
    """Greedily cluster `blocks` into left-to-right columns (see step 4),
    returning them concatenated column by column, top to bottom within
    each column."""
    if not blocks:
        return []

    threshold = text_area_width * COLUMN_GAP_FRACTION
    # Each cluster: running (sum_of_centers, count, [blocks]).
    clusters: list[list[Block]] = []
    cluster_center_sums: list[float] = []

    for block in sorted(blocks, key=lambda b: b.x_min):
        center = block.x_center
        best_index = None
        best_distance = threshold
        for i, blocks_in_cluster in enumerate(clusters):
            cluster_center = cluster_center_sums[i] / len(blocks_in_cluster)
            distance = abs(center - cluster_center)
            if distance <= best_distance:
                best_distance = distance
                best_index = i
        if best_index is None:
            clusters.append([block])
            cluster_center_sums.append(center)
        else:
            clusters[best_index].append(block)
            cluster_center_sums[best_index] += center

    ordered_clusters = sorted(
        range(len(clusters)),
        key=lambda i: cluster_center_sums[i] / len(clusters[i]),
    )
    result: list[Block] = []
    for i in ordered_clusters:
        result.extend(sorted(clusters[i], key=lambda b: b.y_min))
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

    combined: list[tuple[float, Block | TableGroup]] = [(b.y_min, b) for b in remaining_blocks] + [
        (tg.y_min, tg) for tg in table_groups
    ]
    combined.sort(key=lambda item: item[0])

    result: list[Block | TableGroup] = []
    run: list[Block] = []
    for _, item in combined:
        if isinstance(item, TableGroup):
            result.extend(_cluster_columns(run, text_area_width))
            result.append(item)
            run = []
        elif item.width > wide_threshold:
            result.extend(_cluster_columns(run, text_area_width))
            result.append(item)
            run = []
        else:
            run.append(item)
    result.extend(_cluster_columns(run, text_area_width))
    return result
