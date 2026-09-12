"""Reading-order reconstruction for one page of `bbox.Block`s.

Algorithm (per page):

1. **Drop vertical/rotated blocks.** A block all of whose lines are taller
   than they are wide (`line.height > line.width` for every line) is glyph
   text rotated 90 degrees -- in practice, a page-edge chapter/section tab
   printed sideways in the margin. These carry no reading-order position
   relative to the body columns, so they are excluded from the output
   entirely rather than assigned to a column.

2. **Split the page into runs at wide blocks.** The *text area* width is the
   span from the minimum `xMin` to the maximum `xMax` across all remaining
   (non-vertical) blocks. Any block whose own width exceeds
   `WIDE_BLOCK_FRACTION` (60%) of that span -- a full-width table or a
   chapter heading spanning the columns -- is a "wide" block. Wide blocks
   are column breaks: sorting all remaining blocks by `yMin`, a wide block
   splits the page into the run of blocks above it and the run below it.
   The final order is: run 1 in column order, wide block 1, run 2 in column
   order, wide block 2, ... (a page with no wide blocks is one run).

3. **Cluster each run's blocks into columns by x-position.** Within a run,
   blocks are sorted by `xMin` and assigned to columns greedily: each
   block joins the existing column whose running x-center average is
   closest, if within `COLUMN_GAP_FRACTION` (25%) of the text-area width;
   otherwise it starts a new column. This is a simple single-pass
   clustering, not k-means -- sufficient for the typical one- or
   two-column D&D 3.5e rulebook layouts this pipeline targets.

4. **Emit columns left to right, each column's blocks top to bottom**
   (sorted by `yMin`), then continue with the next run after its wide
   block.
"""

from __future__ import annotations

from owlsperch.text.bbox import Block, Page

#: A block wider than this fraction of the text area is treated as a column
#: break (a full-width table or heading) rather than clustered into a column.
WIDE_BLOCK_FRACTION = 0.6

#: Greedy column-clustering threshold, as a fraction of the text-area width:
#: a block joins the nearest existing column if its x-center is within this
#: distance of that column's running average center.
COLUMN_GAP_FRACTION = 0.25


def is_vertical_block(block: Block) -> bool:
    """Whether `block` is rotated/vertical text (see module docstring, step 1).

    A block with no lines is not considered vertical (nothing to judge).
    """
    if not block.lines:
        return False
    return all(line.height > line.width for line in block.lines)


def _text_area_width(blocks: list[Block]) -> float:
    x_min = min(b.x_min for b in blocks)
    x_max = max(b.x_max for b in blocks)
    width = x_max - x_min
    return width if width > 0 else 1.0


def _cluster_columns(blocks: list[Block], text_area_width: float) -> list[Block]:
    """Greedily cluster `blocks` into left-to-right columns (see step 3),
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


def order_blocks(page: Page) -> list[Block]:
    """Return `page`'s blocks in reading order (see module docstring)."""
    blocks = [b for b in page.blocks if not is_vertical_block(b)]
    if not blocks:
        return []

    text_area_width = _text_area_width(blocks)
    wide_threshold = text_area_width * WIDE_BLOCK_FRACTION

    by_y = sorted(blocks, key=lambda b: b.y_min)

    result: list[Block] = []
    run: list[Block] = []
    for block in by_y:
        if block.width > wide_threshold:
            result.extend(_cluster_columns(run, text_area_width))
            result.append(block)
            run = []
        else:
            run.append(block)
    result.extend(_cluster_columns(run, text_area_width))
    return result
