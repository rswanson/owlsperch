"""Unit tests for `owlsperch.text.columns` -- reading-order reconstruction,
built on hand-written `pdftotext -bbox-layout` XHTML fixtures.

Covers acceptance criterion 6(a): two-column ordering with a wide heading
block, plus single-column pages and rotated/vertical marginal blocks.
"""

from __future__ import annotations

from pathlib import Path

from owlsperch.text.bbox import Block, Line, Page, Word, parse_bbox_xhtml
from owlsperch.text.columns import (
    TableGroup,
    _absorb_orphan_blocks,
    _groups_can_merge,
    _merge_adjacent_groups,
    _qualifying_row_count,
    _TableCandidate,
    is_vertical_block,
    order_blocks,
)

_NS = 'xmlns="http://www.w3.org/1999/xhtml"'


def _block_text(item: Block | TableGroup) -> str:
    assert isinstance(item, Block)
    return item.lines[0].text


def _parse_page(tmp_path: Path, name: str, body: str) -> Page:
    html_path = tmp_path / name
    html_path.write_text(
        f"""<html {_NS}><body><doc>
  <page width="612.000000" height="792.000000">
    {body}
  </page>
</doc></body></html>"""
    )
    pages = parse_bbox_xhtml(html_path)
    return pages[0]


def _block(x_min: float, y_min: float, x_max: float, y_max: float, text: str) -> str:
    # The block spans the full (x_min, y_min, x_max, y_max) box (this is what
    # column clustering and the wide-block check look at), but its one line
    # of text has a realistic (short) height -- a block this tall with a
    # single line spanning its whole height would itself look "vertical"
    # (line height > line width), which is not what these fixtures mean to
    # represent.
    line_y_max = min(y_min + 12.0, y_max)
    return f"""
    <flow>
      <block xMin="{x_min}" yMin="{y_min}" xMax="{x_max}" yMax="{y_max}">
        <line xMin="{x_min}" yMin="{y_min}" xMax="{x_max}" yMax="{line_y_max}">
          <word xMin="{x_min}" yMin="{y_min}" xMax="{x_max}" yMax="{line_y_max}">{text}</word>
        </line>
      </block>
    </flow>
    """


def test_two_column_page_with_wide_heading_block(tmp_path: Path) -> None:
    # A full-width heading above two body columns.
    heading = _block(34, 40, 580, 60, "HEADING")
    left = _block(34, 80, 290, 400, "LEFT")
    right = _block(300, 80, 580, 400, "RIGHT")
    page = _parse_page(tmp_path, "two_col.html", heading + left + right)

    ordered = order_blocks(page)

    texts = [_block_text(b) for b in ordered]
    assert texts == ["HEADING", "LEFT", "RIGHT"]


def test_single_column_page_orders_top_to_bottom(tmp_path: Path) -> None:
    first = _block(34, 40, 580, 60, "FIRST")
    second = _block(34, 80, 580, 120, "SECOND")
    third = _block(34, 140, 580, 180, "THIRD")
    # Deliberately out of y-order in the fixture to prove sorting happens.
    page = _parse_page(tmp_path, "single_col.html", third + first + second)

    ordered = order_blocks(page)

    texts = [_block_text(b) for b in ordered]
    assert texts == ["FIRST", "SECOND", "THIRD"]


def test_multiple_wide_blocks_act_as_column_breaks(tmp_path: Path) -> None:
    heading1 = _block(34, 10, 580, 20, "HEAD1")
    left1 = _block(34, 30, 290, 100, "LEFT1")
    right1 = _block(300, 30, 580, 100, "RIGHT1")
    table = _block(34, 110, 580, 150, "TABLE")
    left2 = _block(34, 160, 290, 220, "LEFT2")
    right2 = _block(300, 160, 580, 220, "RIGHT2")
    page = _parse_page(
        tmp_path,
        "two_breaks.html",
        heading1 + left1 + right1 + table + left2 + right2,
    )

    ordered = order_blocks(page)

    texts = [_block_text(b) for b in ordered]
    assert texts == ["HEAD1", "LEFT1", "RIGHT1", "TABLE", "LEFT2", "RIGHT2"]


def test_vertical_block_is_excluded_from_reading_order(tmp_path: Path) -> None:
    body_block = _block(34, 40, 580, 400, "BODY")
    # A narrow, tall single-line block: a rotated page-edge chapter tab.
    tab = """
    <flow>
      <block xMin="587.7" yMin="145.4" xMax="599.5" yMax="210.8">
        <line xMin="587.7" yMin="145.4" xMax="599.5" yMax="210.8">
          <word xMin="587.7" yMin="145.4" xMax="599.5" yMax="210.8">CHAPTER 7:</word>
        </line>
      </block>
    </flow>
    """
    page = _parse_page(tmp_path, "vertical.html", body_block + tab)

    assert is_vertical_block(page.blocks[1]) is True
    ordered = order_blocks(page)
    texts = [_block_text(b) for b in ordered]
    assert texts == ["BODY"]
    assert "CHAPTER 7:" not in texts


def _table_column_block(x_min: float, x_max: float, y_min: float, cell_texts: list[str]) -> str:
    # One narrow block containing one line per row, at the same y positions
    # across every column of a table -- the shape `pdftotext` emits for a
    # multi-column table like the PHB weapon table.
    row_height = 20.0
    lines = []
    y = y_min
    for text in cell_texts:
        line_y_max = y + 12.0
        lines.append(
            f"""
            <line xMin="{x_min}" yMin="{y}" xMax="{x_max}" yMax="{line_y_max}">
              <word xMin="{x_min}" yMin="{y}" xMax="{x_max}" yMax="{line_y_max}">{text}</word>
            </line>
            """
        )
        y += row_height
    y_max = y - row_height + 12.0
    return f"""
    <flow>
      <block xMin="{x_min}" yMin="{y_min}" xMax="{x_max}" yMax="{y_max}">
        {"".join(lines)}
      </block>
    </flow>
    """


def test_table_group_of_five_columns_is_emitted_row_wise(tmp_path: Path) -> None:
    # 5 narrow blocks side by side, each one column of a 4-row table --
    # `pdftotext`'s typical column-per-block table shape.
    columns = [
        (34.0, 100.0, ["Name", "Falchion", "Longsword", "Dagger"]),
        (110.0, 180.0, ["Cost", "75 gp", "15 gp", "2 gp"]),
        (190.0, 260.0, ["Dmg", "2d4", "1d8", "1d4"]),
        (270.0, 340.0, ["Crit", "18-20/x2", "19-20/x2", "19-20/x2"]),
        (350.0, 420.0, ["Type", "Slashing", "Slashing", "Piercing"]),
    ]
    blocks_xml = "".join(
        _table_column_block(x_min, x_max, 100.0, texts) for x_min, x_max, texts in columns
    )
    page = _parse_page(tmp_path, "table.html", blocks_xml)

    ordered = order_blocks(page)

    assert len(ordered) == 1
    group = ordered[0]
    assert isinstance(group, TableGroup)
    assert len(group.rows) == 4
    assert group.rows[0].text == "Name\tCost\tDmg\tCrit\tType"
    assert group.rows[1].text == "Falchion\t75 gp\t2d4\t18-20/x2\tSlashing"
    assert group.rows[2].text == "Longsword\t15 gp\t1d8\t19-20/x2\tSlashing"
    assert group.rows[3].text == "Dagger\t2 gp\t1d4\t19-20/x2\tPiercing"


def test_two_column_prose_page_is_not_a_table_group(tmp_path: Path) -> None:
    # Only 2 blocks, tall, not overlapping in x -- an ordinary two-column
    # prose layout, not a table (a table group requires >= 3 blocks).
    left = _block(34, 80, 290, 400, "LEFT")
    right = _block(300, 80, 580, 400, "RIGHT")
    page = _parse_page(tmp_path, "two_col_prose.html", left + right)

    ordered = order_blocks(page)

    assert len(ordered) == 2
    assert all(isinstance(b, Block) for b in ordered)
    assert not any(isinstance(b, TableGroup) for b in ordered)


# ---------------------------------------------------------------------------
# Single-block tables (step 2a): a whole table flattened by `pdftotext` into
# one wide block, e.g. the PHB p.118 "Exotic Weapons" table.
# ---------------------------------------------------------------------------


def _word(x_min: float, x_max: float, y_min: float, y_max: float, text: str) -> str:
    return f'<word xMin="{x_min}" yMin="{y_min}" xMax="{x_max}" yMax="{y_max}">{text}</word>'


def _line_of_words(y_min: float, y_max: float, words: list[tuple[float, float, str]]) -> str:
    # words: (x_min, x_max, text) triples, already left-to-right.
    x_min = words[0][0]
    x_max = words[-1][1]
    words_xml = "".join(_word(x0, x1, y_min, y_max, text) for x0, x1, text in words)
    return f'<line xMin="{x_min}" yMin="{y_min}" xMax="{x_max}" yMax="{y_max}">{words_xml}</line>'


def _wide_block_of_rows(
    rows: list[list[tuple[float, float, str]]], y_start: float, row_height: float = 20.0
) -> str:
    # One flow/block containing one <line> per row, each row a list of
    # (x_min, x_max, text) words -- the shape a single-block table (or an
    # ordinary wrapped prose paragraph) takes in `pdftotext -bbox-layout`.
    lines = []
    y = y_start
    for row_words in rows:
        lines.append(_line_of_words(y, y + 12.0, row_words))
        y += row_height
    x_min = min(w[0] for row in rows for w in row)
    x_max = max(w[1] for row in rows for w in row)
    y_max = y - row_height + 12.0
    return (
        f'<flow><block xMin="{x_min}" yMin="{y_start}" xMax="{x_max}" '
        f'yMax="{y_max}">{"".join(lines)}</block></flow>'
    )


def _table_row(
    labels: list[tuple[str, str]], cols_x: list[float]
) -> list[tuple[float, float, str]]:
    # A row of `len(cols_x)` two-word cells (a small intra-cell gap between
    # each cell's two words), each cell starting at its column's x -- big,
    # consistent gaps between cells; small, consistent gaps within a cell.
    words: list[tuple[float, float, str]] = []
    for x0, (first, second) in zip(cols_x, labels, strict=True):
        words.append((x0, x0 + 20.0, first))
        words.append((x0 + 24.0, x0 + 40.0, second))
    return words


def test_single_block_table_with_consistent_gaps_becomes_a_table_group(tmp_path: Path) -> None:
    # 5 rows x 4 columns, one wide block (pdftotext kept the whole table as
    # a single block, one row per <line>) -- should become one TableGroup
    # of 5 tab-separated rows.
    cols_x = [34.0, 150.0, 250.0, 350.0]
    rows_labels = [
        [("Name", "One"), ("Cost", "A"), ("Dmg", "X"), ("Type", "Q")],
        [("Name", "Two"), ("Cost", "B"), ("Dmg", "Y"), ("Type", "R")],
        [("Name", "Three"), ("Cost", "C"), ("Dmg", "Z"), ("Type", "S")],
        [("Name", "Four"), ("Cost", "D"), ("Dmg", "W"), ("Type", "T")],
        [("Name", "Five"), ("Cost", "E"), ("Dmg", "V"), ("Type", "U")],
    ]
    rows = [_table_row(labels, cols_x) for labels in rows_labels]
    block_xml = _wide_block_of_rows(rows, y_start=100.0)
    page = _parse_page(tmp_path, "single_block_table.html", block_xml)

    ordered = order_blocks(page)

    assert len(ordered) == 1
    group = ordered[0]
    assert isinstance(group, TableGroup)
    assert len(group.rows) == 5
    assert group.rows[0].text == "Name One\tCost A\tDmg X\tType Q"
    assert group.rows[2].text == "Name Three\tCost C\tDmg Z\tType S"
    assert group.rows[4].text == "Name Five\tCost E\tDmg V\tType U"


def test_ordinary_wide_prose_block_is_not_treated_as_tabular(tmp_path: Path) -> None:
    # A justified paragraph: several lines of normal running text, each
    # word only a few points from the next -- variable, but never a "large"
    # gap. Must stay a normal prose Block, not be split into a table.
    rows = [
        [(34.0, 70.0, "This"), (74.0, 110.0, "sword"), (115.0, 150.0, "has"), (155.0, 200.0, "a")],
        [
            (34.0, 90.0, "curve"),
            (96.0, 130.0, "that"),
            (134.0, 190.0, "gives"),
            (196.0, 220.0, "it"),
        ],
        [
            (34.0, 80.0, "the"),
            (85.0, 140.0, "effect"),
            (147.0, 170.0, "of"),
            (177.0, 230.0, "a"),
        ],
        [(34.0, 90.0, "keener"), (95.0, 140.0, "edge."), (146.0, 200.0, "Done.")],
    ]
    block_xml = _wide_block_of_rows(rows, y_start=100.0)
    page = _parse_page(tmp_path, "prose_block.html", block_xml)

    ordered = order_blocks(page)

    assert len(ordered) == 1
    assert isinstance(ordered[0], Block)
    assert not any(isinstance(item, TableGroup) for item in ordered)


def test_single_block_with_only_two_gappy_lines_is_not_tabular(tmp_path: Path) -> None:
    # Only 2 rows have big, consistent gaps -- below TABLE_MIN_GAPPY_ROWS
    # (3), so the block is not a table even though those 2 rows look
    # table-like.
    cols_x = [34.0, 150.0, 250.0, 350.0]
    rows_labels = [
        [("Name", "One"), ("Cost", "A"), ("Dmg", "X"), ("Type", "Q")],
        [("Name", "Two"), ("Cost", "B"), ("Dmg", "Y"), ("Type", "R")],
    ]
    rows = [_table_row(labels, cols_x) for labels in rows_labels]
    block_xml = _wide_block_of_rows(rows, y_start=100.0)
    page = _parse_page(tmp_path, "two_gappy_lines.html", block_xml)

    ordered = order_blocks(page)

    assert len(ordered) == 1
    assert isinstance(ordered[0], Block)
    assert not any(isinstance(item, TableGroup) for item in ordered)


# ---------------------------------------------------------------------------
# Prose-column exclusion from table grouping, and mutual- (not merely
# transitive-) overlap for a table group (B3 fix, real PHB corpus).
# ---------------------------------------------------------------------------


def _justified_column(x_left: float, width: float, y_start: float, label: str) -> str:
    """One block of justified prose: 5 lines of 6 words each, every line
    spanning nearly the whole `width` -- easily clears the prose-like
    thresholds (>= 4 lines, median >= 5 words/line, >= 60% of lines >= 75%
    of the block's own width). `label` is the very first word, so the
    block can be told apart from others by its first line's first word."""
    words_per_line = 6
    word_width = width / words_per_line - 4.0
    rows: list[list[tuple[float, float, str]]] = []
    for line_idx in range(5):
        words: list[tuple[float, float, str]] = []
        x = x_left
        for word_idx in range(words_per_line):
            text = label if line_idx == 0 and word_idx == 0 else f"w{line_idx}_{word_idx}"
            words.append((x, x + word_width, text))
            x += word_width + 4.0
        rows.append(words)
    return _wide_block_of_rows(rows, y_start=y_start)


def test_three_column_prose_page_is_not_a_table_and_orders_correctly(tmp_path: Path) -> None:
    # Three genuine prose columns sharing the same y-range -- structurally
    # identical to a table's shape (>= 3 blocks, mutual vertical overlap, no
    # x-overlap), but each block is justified running prose, so none should
    # be pulled into a table group; each stays its own column, ordered left
    # to right, and no tab character appears anywhere in the output.
    col1 = _justified_column(34.0, 160.0, 100.0, "ALPHA")
    col2 = _justified_column(234.0, 160.0, 100.0, "BETA")
    col3 = _justified_column(434.0, 160.0, 100.0, "GAMMA")
    page = _parse_page(tmp_path, "three_col_prose.html", col1 + col2 + col3)

    ordered = order_blocks(page)

    assert len(ordered) == 3
    assert not any(isinstance(item, TableGroup) for item in ordered)

    first_words: list[str] = []
    all_lines_text: list[str] = []
    for item in ordered:
        assert isinstance(item, Block)
        first_words.append(item.lines[0].words[0].text)
        all_lines_text.extend(line.text for line in item.lines)

    assert first_words == ["ALPHA", "BETA", "GAMMA"]
    assert "\t" not in "\n".join(all_lines_text)


def test_table_beside_prose_column_only_table_becomes_a_group(tmp_path: Path) -> None:
    # A genuine 5-column table next to a justified prose column, both
    # sharing the same y-range -- so the prose block would satisfy the same
    # "mutual vertical overlap, no x-overlap" pairwise test the table's own
    # columns do. The prose column must stay its own untouched Block while
    # the table's 5 columns still become one row-wise TableGroup.
    columns = [
        (34.0, 100.0, ["Name", "Falchion", "Longsword", "Dagger"]),
        (110.0, 180.0, ["Cost", "75 gp", "15 gp", "2 gp"]),
        (190.0, 260.0, ["Dmg", "2d4", "1d8", "1d4"]),
        (270.0, 340.0, ["Crit", "18-20/x2", "19-20/x2", "19-20/x2"]),
        (350.0, 420.0, ["Type", "Slashing", "Slashing", "Piercing"]),
    ]
    table_xml = "".join(
        _table_column_block(x_min, x_max, 100.0, texts) for x_min, x_max, texts in columns
    )
    prose_xml = _justified_column(450.0, 130.0, 100.0, "PROSE")
    page = _parse_page(tmp_path, "table_beside_prose.html", table_xml + prose_xml)

    ordered = order_blocks(page)

    table_groups = [item for item in ordered if isinstance(item, TableGroup)]
    prose_blocks = [item for item in ordered if isinstance(item, Block)]

    assert len(table_groups) == 1
    group = table_groups[0]
    assert len(group.rows) == 4
    assert group.rows[0].text == "Name\tCost\tDmg\tCrit\tType"
    assert group.rows[1].text == "Falchion\t75 gp\t2d4\t18-20/x2\tSlashing"

    assert len(prose_blocks) == 1
    assert prose_blocks[0].lines[0].words[0].text == "PROSE"
    assert not any("\t" in line.text for line in prose_blocks[0].lines)


def test_single_block_merged_prose_columns_are_split_not_tabular(tmp_path: Path) -> None:
    # Prose columns (like the PHB's own 3-column spell-chapter layout the
    # module docstring's step 1a describes) that `pdftotext` merged into
    # ONE block instead of separate ones -- the B3 follow-up real-corpus
    # failure mode (PHB pp. 197, 204: two adjacent spell entries' stat
    # blocks read as one tab-joined table row). Each line has 3 runs of 6
    # words each (sentence-like "cells", not table cells) separated by
    # large, consistent gaps -- the same shape a genuine single-block
    # table has (TABLE_MIN_GAPS_PER_ROW needs >= 2 large gaps per row, so
    # a bare two-run/one-gap merge can never even reach detection; three
    # runs is the minimal faithful reproduction) -- but each "cell" is a
    # run of prose words, not a table cell, so the block must be split at
    # the gaps into separate prose columns, not read row-wise.
    word_width = 18.0
    small_gap = 4.0
    big_gap = 90.0
    words_per_run = 6

    def _run(x0: float, row_idx: int, label: str) -> list[tuple[float, float, str]]:
        words: list[tuple[float, float, str]] = []
        x = x0
        for word_idx in range(words_per_run):
            text = label if row_idx == 0 and word_idx == 0 else f"w{row_idx}_{word_idx}"
            words.append((x, x + word_width, text))
            x += word_width + small_gap
        return words

    rows: list[list[tuple[float, float, str]]] = []
    for row_idx in range(5):
        left = _run(34.0, row_idx, "LEFT")
        mid = _run(left[-1][1] + big_gap, row_idx, "MID")
        right = _run(mid[-1][1] + big_gap, row_idx, "RIGHT")
        rows.append(left + mid + right)

    block_xml = _wide_block_of_rows(rows, y_start=100.0)
    page = _parse_page(tmp_path, "single_block_merged_prose_cols.html", block_xml)

    ordered = order_blocks(page)

    assert len(ordered) == 3
    for item in ordered:
        assert isinstance(item, Block)

    all_lines_text: list[str] = []
    first_words: list[str] = []
    for item in ordered:
        assert isinstance(item, Block)
        all_lines_text.extend(line.text for line in item.lines)
        first_words.append(item.lines[0].words[0].text)

    assert "\t" not in "\n".join(all_lines_text)
    assert first_words == ["LEFT", "MID", "RIGHT"]


def _stat_block_rows(
    x_left: float, n_lines: int, words_per_line: int = 3
) -> list[list[tuple[float, float, str]]]:
    """`n_lines` rows of `words_per_line` short words each, starting at
    `x_left` -- a stat-block-like block's shape (short, ragged cells, not
    justified prose): used to reproduce the real-corpus false-positive
    pattern below (a short heading block nested, in y-range, inside two
    such stat blocks in adjacent columns)."""
    word_width = 18.0
    gap = 3.0
    rows: list[list[tuple[float, float, str]]] = []
    for line_idx in range(n_lines):
        words: list[tuple[float, float, str]] = []
        x = x_left
        for word_idx in range(words_per_line):
            words.append((x, x + word_width, f"w{line_idx}_{word_idx}"))
            x += word_width + gap
        rows.append(words)
    return rows


def test_short_heading_nested_between_two_tall_blocks_is_not_a_table(tmp_path: Path) -> None:
    # Real-corpus false positive (B3 follow-up, PHB pp. 197, 204): a
    # one-line heading ("Acid Fog") sits, in y-range, entirely inside the
    # overlap of two unrelated tall stat-block-like blocks ("Acid Splash",
    # "Air Walk") in adjacent columns. The heading's own (short) height is
    # 100% covered by each neighbor -- satisfying the old, shorter-block-only
    # overlap test -- but covers only a sliver of either neighbor's own
    # height. With the two-sided test (>= 70% of the shorter block's height
    # AND >= 50% of the taller block's height) the heading must not pair
    # with either stat block; and even though the two stat blocks *do*
    # mutually qualify as a pair (they share nearly the same y-range), a
    # 2-block group is below `TABLE_MIN_BLOCKS`, so no `TableGroup` forms
    # at all.
    left = _wide_block_of_rows(_stat_block_rows(34.0, 8), y_start=406.0, row_height=11.0)
    right = _wide_block_of_rows(_stat_block_rows(300.0, 8), y_start=406.0, row_height=11.0)
    heading = _block(160.0, 457.0, 280.0, 469.0, "ACID FOG")
    page = _parse_page(tmp_path, "nested_heading.html", left + right + heading)

    ordered = order_blocks(page)

    assert not any(isinstance(item, TableGroup) for item in ordered)
    assert len(ordered) == 3


def test_short_block_covering_only_20_percent_of_neighbors_height_is_not_a_table(
    tmp_path: Path,
) -> None:
    # A variant of the above where the nested block has 2 lines (clearing
    # `TABLE_CANDIDATE_MIN_LINES`) but still only covers ~20% of its tall
    # neighbors' height -- well short of `TABLE_OVERLAP_TALLER_FRACTION`
    # (50%), so this isolates the taller-block overlap fraction itself
    # (independent of the line-count exclusion) as the reason no table
    # group forms.
    left = _wide_block_of_rows(_stat_block_rows(34.0, 8), y_start=406.0, row_height=11.0)
    right = _wide_block_of_rows(_stat_block_rows(300.0, 8), y_start=406.0, row_height=11.0)
    # left/right span y406-495 (89pt tall); this block spans y406-424 (18pt,
    # ~20% of 89) -- fully nested at the top of their shared range.
    short = _wide_block_of_rows(_stat_block_rows(160.0, 2), y_start=406.0, row_height=6.0)
    page = _parse_page(tmp_path, "short_20_percent.html", left + right + short)

    ordered = order_blocks(page)

    assert not any(isinstance(item, TableGroup) for item in ordered)
    assert len(ordered) == 3


def _label_value_block(x_min: float, x_max: float, y_min: float, labels: list[str]) -> str:
    """One block whose lines each read a whole "Label: value" string (e.g.
    "Level: Sor/Wiz 3") -- shaped like a spell's or monster's own stat
    block, not a table column's cells."""
    row_height = 14.0
    lines = []
    y = y_min
    for text in labels:
        line_y_max = y + 11.0
        lines.append(
            f'<line xMin="{x_min}" yMin="{y}" xMax="{x_max}" yMax="{line_y_max}">'
            f'<word xMin="{x_min}" yMin="{y}" xMax="{x_max}" yMax="{line_y_max}">{text}</word>'
            f"</line>"
        )
        y += row_height
    y_max = y - row_height + 11.0
    return (
        f'<flow><block xMin="{x_min}" yMin="{y_min}" xMax="{x_max}" '
        f'yMax="{y_max}">{"".join(lines)}</block></flow>'
    )


def test_three_adjacent_stat_blocks_do_not_merge_into_a_table_group(tmp_path: Path) -> None:
    # Real-corpus false positive (B3 follow-up, PHB pp. 254/272 "Mind Fog",
    # "Repel Wood"): three adjacent spell stat blocks of equal height,
    # side by side, mutually satisfy the same "no x-overlap, shared
    # y-range" pairwise test real table columns do -- but their lines are
    # "Label: value" shaped, not table cells, so they must never merge
    # into one TableGroup.
    labels = [
        "Level: Sor/Wiz 5",
        "Components: V, S",
        "Casting Time: 1 standard action",
        "Range: Long (400 ft. + 40 ft./level)",
        "Duration: Instantaneous",
        "Saving Throw: Will negates",
    ]
    col1 = _label_value_block(34.0, 140.0, 100.0, labels)
    col2 = _label_value_block(160.0, 266.0, 100.0, labels)
    col3 = _label_value_block(286.0, 392.0, 100.0, labels)
    page = _parse_page(tmp_path, "three_stat_blocks.html", col1 + col2 + col3)

    ordered = order_blocks(page)

    assert not any(isinstance(item, TableGroup) for item in ordered)
    assert len(ordered) == 3
    all_lines_text = [line.text for item in ordered for line in item.lines]  # type: ignore[union-attr]
    assert "\t" not in "\n".join(all_lines_text)


def test_transitive_but_not_mutual_overlap_is_not_one_table_group(tmp_path: Path) -> None:
    # A pairwise-overlaps-B and B pairwise-overlaps-C (each qualifying on
    # its own as a table-column pair: >= 70% vertical overlap, no
    # x-overlap), but A does NOT pairwise-overlap C -- a chain, not a
    # clique. Grouping must require every pair in a group to mutually
    # qualify, so this must not become one 3-block table group.
    a = _block(34, 100, 100, 160, "A")
    b = _block(120, 110, 186, 170, "B")
    c = _block(206, 120, 272, 180, "C")
    page = _parse_page(tmp_path, "chain_not_clique.html", a + b + c)

    ordered = order_blocks(page)

    assert len(ordered) == 3
    assert not any(isinstance(item, TableGroup) for item in ordered)
    assert all(isinstance(item, Block) for item in ordered)


# ---------------------------------------------------------------------------
# Fragmented class-level tables (step 2b, B10c-mand7): `pdftotext` splits a
# printed class-level table (e.g. PHB Table 3-6: The Cleric) into several
# per-column table groups plus orphan single-cell blocks for the rows in
# between, and step 2b must reassemble all of it into one `TableGroup`.
# ---------------------------------------------------------------------------

#: (x_min, x_max, short column code) for the class-table fixture's columns,
#: modeled on PHB p.32's Level/BAB/Fort/Ref/Will/Special columns.
_CLASS_TABLE_COLUMNS = [
    (35.0, 50.0, "LV"),
    (62.0, 98.0, "BA"),
    (120.0, 133.0, "FO"),
    (148.0, 158.0, "RE"),
    (175.0, 187.0, "WI"),
    (206.0, 284.0, "SP"),
]

#: Row 0 is the header; rows 1-20 are class levels 1st-20th. Row pitch 10pt,
#: line height 8pt -- close to PHB p.32's real ~10pt pitch/~8pt line height.
_CLASS_TABLE_ROW_PITCH = 10.0
_CLASS_TABLE_Y_START = 489.0
_CLASS_TABLE_LINE_HEIGHT = 8.0

#: Rows `pdftotext` emits as one block per CELL rather than as part of a
#: per-column block -- the 4th/8th/12th/15th/16th/20th level rows, per the
#: real PHB p.32 probe this fixture is modeled on.
_CLASS_TABLE_ORPHAN_ROWS = {4, 8, 12, 15, 16, 20}

_CLASS_TABLE_HEADER_BY_CODE = {
    "LV": "LVL",
    "BA": "BAB",
    "FO": "FORT",
    "RE": "REF",
    "WI": "WILL",
    "SP": "SPECIAL",
}


def _class_table_row_y(row: int) -> tuple[float, float]:
    y_min = _CLASS_TABLE_Y_START + row * _CLASS_TABLE_ROW_PITCH
    return y_min, y_min + _CLASS_TABLE_LINE_HEIGHT


def _class_table_cell_text(row: int, header: str, code: str) -> str:
    return header if row == 0 else f"{code}{row}"


def _class_table_column_block(x_min: float, x_max: float, code: str, rows: list[int]) -> str:
    """One per-column block (the shape `pdftotext` emits for a class-level
    table's columns) containing exactly `rows`' cells for this column --
    possibly a non-contiguous subset, modeling one of the several
    per-column clusters poppler splits a fragmented table into."""
    lines = []
    for row in rows:
        y_min, y_max = _class_table_row_y(row)
        text = _class_table_cell_text(row, _CLASS_TABLE_HEADER_BY_CODE[code], code)
        lines.append(
            f'<line xMin="{x_min}" yMin="{y_min}" xMax="{x_max}" yMax="{y_max}">'
            f'<word xMin="{x_min}" yMin="{y_min}" xMax="{x_max}" yMax="{y_max}">{text}</word>'
            f"</line>"
        )
    block_y_min, _ = _class_table_row_y(rows[0])
    _, block_y_max = _class_table_row_y(rows[-1])
    return (
        f'<flow><block xMin="{x_min}" yMin="{block_y_min}" xMax="{x_max}" '
        f'yMax="{block_y_max}">{"".join(lines)}</block></flow>'
    )


def _class_table_orphan_cell_blocks(row: int) -> str:
    """The single-word, single-cell blocks `pdftotext` emits for one
    "orphan" row of a fragmented class-level table -- one block per
    column, all at that row's y position (see step 2b's module docstring:
    these rows arrive as one block per CELL, not as part of any
    per-column block)."""
    y_min, y_max = _class_table_row_y(row)
    out = []
    for x_min, x_max, code in _CLASS_TABLE_COLUMNS:
        text = _class_table_cell_text(row, _CLASS_TABLE_HEADER_BY_CODE[code], code)
        out.append(_block(x_min, y_min, x_max, y_max, text))
    return "".join(out)


def _fragmented_class_table_body() -> str:
    # Non-orphan rows are grouped into 5 runs (5 per-column block clusters
    # per column), separated by the orphan rows -- the exact PHB p.32
    # shape (5 table groups + ~90 loose orphan blocks) this step must
    # reassemble into one.
    row_groups = [
        [0, 1, 2, 3],
        [5, 6, 7],
        [9, 10, 11],
        [13, 14],
        [17, 18, 19],
    ]
    body = ""
    for x_min, x_max, code in _CLASS_TABLE_COLUMNS:
        for rows in row_groups:
            body += _class_table_column_block(x_min, x_max, code, rows)
    for row in sorted(_CLASS_TABLE_ORPHAN_ROWS):
        body += _class_table_orphan_cell_blocks(row)

    # A caption to the left of the table's own x span (x_min 26 < the
    # table's 35), a multi-line footnote below it, and a page number far to
    # the right -- none of these belong in the reassembled table group.
    body += _block(26.0, 470.0, 200.0, 482.0, "Table 3-6: The Cleric")
    footnote_lines = [
        [
            (35.0, 90.0, "1"),
            (95.0, 538.0, "In addition to the stated number of spells per day for"),
        ],
        [(35.0, 538.0, "the cleric's level, the cleric gets a number of bonus spells")],
        [(35.0, 538.0, "per day if the cleric has a high Wisdom score.")],
    ]
    body += _wide_block_of_rows(footnote_lines, y_start=721.6, row_height=11.0)
    body += _block(562.9, 750.0, 578.0, 762.0, "31")
    return body


def test_fragmented_class_level_table_reassembles_into_one_group(tmp_path: Path) -> None:
    # Real-corpus regression (B10c-mand7, PHB p.32 "Table 3-6: The
    # Cleric"): `pdftotext` fragments a class-level table into several
    # per-column table groups plus orphan single-cell blocks for the rows
    # in between. Step 2b must reassemble all of it into ONE `TableGroup`
    # with every level row complete and in column order, and must NOT pull
    # in the caption, footnote, or page number.
    page = _parse_page(tmp_path, "fragmented_class_table.html", _fragmented_class_table_body())

    ordered = order_blocks(page)

    table_groups = [item for item in ordered if isinstance(item, TableGroup)]
    assert len(table_groups) == 1, [
        (len(tg.rows), tg.rows[0].cells if tg.rows else None) for tg in table_groups
    ]
    group = table_groups[0]

    # Header row + 20 level rows, all with every column present.
    assert len(group.rows) == 21
    assert group.rows[0].cells == ["LVL", "BAB", "FORT", "REF", "WILL", "SPECIAL"]
    for row in range(1, 21):
        expected = [f"LV{row}", f"BA{row}", f"FO{row}", f"RE{row}", f"WI{row}", f"SP{row}"]
        assert group.rows[row].cells == expected, (row, group.rows[row].cells)

    # The caption, footnote, and page number stay out of the table group.
    non_table_texts = [
        line.text for item in ordered if isinstance(item, Block) for line in item.lines
    ]
    assert any("Table 3-6" in text for text in non_table_texts)
    assert any("bonus spells" in text for text in non_table_texts)
    assert any(text.strip() == "31" for text in non_table_texts)


def _line(x_min: float, y_min: float, x_max: float, y_max: float, text: str) -> Line:
    return Line(x_min, y_min, x_max, y_max, [Word(x_min, y_min, x_max, y_max, text)])


def test_two_stacked_tables_with_incompatible_x_grids_stay_separate(tmp_path: Path) -> None:
    # Acceptance criterion 8 regression: two real, independently-detected
    # table groups sit almost directly on top of each other (a small
    # vertical gap, well within the step-2b merge threshold) but their
    # x-grids barely overlap -- well below `TABLE_MERGE_X_OVERLAP_FRACTION`
    # (80% of the narrower one's span). Step 2b's merge pass must leave
    # them as two separate `TableGroup`s.
    top_columns = [
        (34.0, 100.0, ["Name", "Falchion", "Longsword"]),
        (110.0, 180.0, ["Cost", "75 gp", "15 gp"]),
        (190.0, 260.0, ["Dmg", "2d4", "1d8"]),
    ]
    top_blocks = "".join(
        _table_column_block(x_min, x_max, 100.0, texts) for x_min, x_max, texts in top_columns
    )

    # Shifted far enough right that the two tables' x-extents (34-260 vs.
    # 254-480, both 226 wide) overlap by only 6 units -- ~2.7% of the
    # narrower span, far under the 80% merge threshold -- even though the
    # vertical gap between them (a few points) would easily pass the merge
    # pass's own gap check on its own.
    bottom_columns = [
        (254.0, 320.0, ["Name", "Dagger", "Rapier"]),
        (330.0, 400.0, ["Cost", "2 gp", "20 gp"]),
        (410.0, 480.0, ["Dmg", "1d4", "1d6"]),
    ]
    bottom_blocks = "".join(
        _table_column_block(x_min, x_max, 155.0, texts) for x_min, x_max, texts in bottom_columns
    )

    page = _parse_page(tmp_path, "stacked_tables.html", top_blocks + bottom_blocks)

    ordered = order_blocks(page)

    table_groups = [item for item in ordered if isinstance(item, TableGroup)]
    assert len(table_groups) == 2, [tg.rows for tg in table_groups]
    row_counts = sorted(len(tg.rows) for tg in table_groups)
    assert row_counts == [3, 3]


def test_group_merge_is_rejected_when_it_would_reduce_qualifying_rows() -> None:
    # Acceptance criterion 5 regression, merge guard (step 2b, part A):
    # hand-built table-group candidates that satisfy `_groups_can_merge`'s
    # geometry (x-overlap, vertical gap) on their own, but whose combined
    # line-height distribution shifts `_build_table_group`'s shared row
    # threshold enough to fuse what were 2 clean rows in each group into a
    # single garbled row -- reducing the qualifying (>= 2 cell) row count
    # from 2+2=4 to 3. The guard must discard this merge and leave both
    # groups untouched.
    group_a = [
        Block(0.0, 0.0, 10.0, 20.0, [_line(0, 0, 10, 10, "a1"), _line(0, 10, 10, 20, "a2")]),
        Block(20.0, 0.0, 30.0, 20.0, [_line(20, 0, 30, 10, "b1"), _line(20, 10, 30, 20, "b2")]),
    ]
    group_b = [
        Block(0.0, 40.0, 10.0, 120.0, [_line(0, 40, 10, 80, "c1"), _line(0, 80, 10, 120, "c2")]),
        Block(20.0, 40.0, 30.0, 120.0, [_line(20, 40, 30, 80, "d1"), _line(20, 80, 30, 120, "d2")]),
    ]

    # The geometry alone qualifies for a merge (full x-overlap, small gap
    # relative to group b's own -- much taller -- median line height).
    assert _groups_can_merge(group_a, group_b) is True

    before = _qualifying_row_count(group_a) + _qualifying_row_count(group_b)
    assert before == 4
    assert _qualifying_row_count(group_a + group_b) == 3

    result = _merge_adjacent_groups([_TableCandidate.of(group_a), _TableCandidate.of(group_b)])
    assert result is None


def test_orphan_absorption_is_rejected_when_it_would_reduce_qualifying_rows() -> None:
    # Acceptance criterion 5 regression, absorb guard (step 2b, part B): a
    # leftover block that satisfies `_block_fits_group` (not prose-like or
    # label:value-like, x-extent inside the group's, y-center inside the
    # group's y-span expanded by one median line height) but whose own
    # much-taller lines shift the combined row threshold enough that every
    # row -- the group's own 2 and the leftover's own, otherwise-qualifying
    # row -- fuses into a single row. Absorbing it would reduce the
    # qualifying row count from 2+1=3 to 1, so the guard must discard it.
    group = [
        Block(0.0, 0.0, 10.0, 40.0, [_line(0, 0, 10, 20, "a1"), _line(0, 20, 10, 40, "a2")]),
        Block(20.0, 0.0, 30.0, 40.0, [_line(20, 0, 30, 20, "b1"), _line(20, 20, 30, 40, "b2")]),
    ]
    leftover = Block(
        0.0,
        -10.0,
        30.0,
        55.0,
        [
            _line(0, -10, 10, 50, "c1"),
            _line(20, -10, 30, 50, "d1"),
            _line(0, -5, 10, 55, "c2"),
            _line(20, -5, 30, 55, "d2"),
        ],
    )

    before = _qualifying_row_count(group) + _qualifying_row_count([leftover])
    assert before == 3
    assert _qualifying_row_count([*group, leftover]) == 1

    result = _absorb_orphan_blocks([_TableCandidate.of(group)], [leftover])
    assert result is None


def test_orphan_absorption_guard_uses_true_total_not_group_only_baseline() -> None:
    # Acceptance criterion 5 regression, absorb guard (step 2b, part B):
    # distinguishes the correct guard baseline (the group's own qualifying
    # rows PLUS the leftover block's own standalone qualifying row count)
    # from a buggy one that omits the leftover's own count. Here the group
    # alone has 1 qualifying row and the leftover alone would also form 1
    # qualifying row (true total 2), but absorbing it fuses everything into
    # a single combined row (1 qualifying row) -- fewer than the true
    # total of 2, so the guard must discard it. A guard whose baseline is
    # only the group's own row count (1) would wrongly accept this, since
    # 1 is not less than 1 -- this is exactly the scenario the earlier
    # `test_orphan_absorption_is_rejected_when_it_would_reduce_qualifying_rows`
    # cannot catch, because there both baselines already agree on
    # rejection.
    group = [
        Block(0.0, 0.0, 10.0, 20.0, [_line(0, 0, 10, 20, "a1")]),
        Block(20.0, 0.0, 30.0, 20.0, [_line(20, 0, 30, 20, "b1")]),
    ]
    leftover = Block(
        12.0,
        0.0,
        18.0,
        20.0,
        [_line(12, 0, 15, 20, "c1"), _line(16, 0, 18, 20, "c2")],
    )

    group_only_baseline = _qualifying_row_count(group)
    true_total_baseline = _qualifying_row_count(group) + _qualifying_row_count([leftover])
    assert group_only_baseline == 1
    assert true_total_baseline == 2
    assert _qualifying_row_count([*group, leftover]) == 1

    result = _absorb_orphan_blocks([_TableCandidate.of(group)], [leftover])
    assert result is None


# ---------------------------------------------------------------------------
# Narrow single-glyph table columns (step 1): a numeric column whose every
# cell is one digit, e.g. the PHB p.56 wizard table's "Spells per Day" 0 and
# 1st columns.
# ---------------------------------------------------------------------------


def _wizard_table_column_block(
    x_min: float, x_max: float, cells: list[str], *, y_start: float = 547.3
) -> str:
    # The real PHB p.56 geometry of one class-table column block: one line
    # (one word) per level row, glyph height 6.4 at a 9.8 row pitch, with
    # the column's own -- frequently very narrow -- x extent.
    glyph_height = 6.4
    row_pitch = 9.8
    lines = []
    for index, text in enumerate(cells):
        y = y_start + index * row_pitch
        lines.append(
            f"""
            <line xMin="{x_min}" yMin="{y}" xMax="{x_max}" yMax="{y + glyph_height}">
              <word xMin="{x_min}" yMin="{y}" xMax="{x_max}" yMax="{y + glyph_height}">{text}</word>
            </line>
            """
        )
    y_max = y_start + (len(cells) - 1) * row_pitch + glyph_height
    return f"""
    <flow>
      <block xMin="{x_min}" yMin="{y_start}" xMax="{x_max}" yMax="{y_max}">
        {"".join(lines)}
      </block>
    </flow>
    """


#: The real x extents and cell values of six of PHB p.56 (Table 3-18: The
#: Wizard)'s column blocks, levels 2nd-6th: Level, Base Attack Bonus, Will
#: Save, then the "Spells per Day" 0, 1st, and 2nd columns. The 0 and 1st
#: columns' cells are each a single digit, ~5 units wide against a 6.4-unit
#: glyph height.
_WIZARD_TABLE_COLUMNS = [
    (34.2, 49.2, ["2nd", "3rd", "4th", "5th", "6th"]),
    (70.5, 94.7, ["+1", "+1", "+2", "+2", "+3"]),
    (181.8, 194.3, ["+3", "+3", "+4", "+4", "+5"]),
    (301.3, 306.5, ["4", "4", "4", "4", "4"]),
    (330.0, 334.8, ["2", "2", "3", "3", "3"]),
    (356.3, 364.3, ["—", "1", "2", "2", "3"]),
]


def test_single_glyph_numeric_column_is_not_dropped_as_vertical(tmp_path: Path) -> None:
    # Real-corpus regression (B10c-mand9, PHB p.56 "Table 3-18: The
    # Wizard"): the "Spells per Day" 0 and 1st column blocks hold nothing
    # but single digits, each of which is taller than it is wide -- so the
    # old `all(line.height > line.width)` test read the whole block as
    # rotated marginalia and dropped it from the page, losing the leading
    # spells-per-day cells of every level row (see step 1).
    blocks_xml = "".join(
        _wizard_table_column_block(x_min, x_max, cells)
        for x_min, x_max, cells in _WIZARD_TABLE_COLUMNS
    )
    page = _parse_page(tmp_path, "wizard_table.html", blocks_xml)

    # The two digit-only columns' every line really is taller than it is
    # wide (this is the geometry that used to trigger the drop) -- they are
    # still not vertical text.
    digit_columns = [page.blocks[3], page.blocks[4]]
    for block in digit_columns:
        assert all(line.height > line.width for line in block.lines)
        assert is_vertical_block(block) is False

    ordered = order_blocks(page)

    table_groups = [item for item in ordered if isinstance(item, TableGroup)]
    assert len(table_groups) == 1, [tg.rows for tg in table_groups]
    group = table_groups[0]
    assert [row.cells for row in group.rows] == [
        ["2nd", "+1", "+3", "4", "2", "—"],
        ["3rd", "+1", "+3", "4", "2", "1"],
        ["4th", "+2", "+4", "4", "3", "2"],
        ["5th", "+2", "+4", "4", "3", "2"],
        ["6th", "+3", "+5", "4", "3", "3"],
    ]


def test_multi_character_rotated_line_is_still_vertical(tmp_path: Path) -> None:
    # The other side of the same rule: a block whose lines have real
    # multi-character text that is nonetheless taller than it is wide is a
    # rotated page-edge tab, and still excluded (step 1). A single-glyph
    # line mixed in with it (poppler occasionally splits one off) does not
    # rescue it.
    tab = """
    <flow>
      <block xMin="587.7" yMin="145.4" xMax="599.5" yMax="215.0">
        <line xMin="587.7" yMin="145.4" xMax="599.5" yMax="210.8">
          <word xMin="587.7" yMin="145.4" xMax="599.5" yMax="210.8">CHAPTER 3:</word>
        </line>
        <line xMin="587.7" yMin="211.0" xMax="599.5" yMax="215.0">
          <word xMin="587.7" yMin="211.0" xMax="599.5" yMax="215.0">3</word>
        </line>
      </block>
    </flow>
    """
    page = _parse_page(tmp_path, "rotated_tab.html", _block(34, 40, 580, 400, "BODY") + tab)

    assert is_vertical_block(page.blocks[1]) is True
    assert [_block_text(item) for item in order_blocks(page)] == ["BODY"]


# ---------------------------------------------------------------------------
# B10c-mand15: boxed sidebars sharing a page with class text
# ---------------------------------------------------------------------------

#: PHB p.37's own geometry for the druid's animal-companion sidebar grid:
#: 9 rows (a 2-line header plus 7 level bands) at a ~9.9pt pitch, ~8pt
#: lines.
_SIDEBAR_GRID_Y_START = 220.9
_SIDEBAR_GRID_ROW_PITCH = 9.9
_SIDEBAR_GRID_LINE_HEIGHT = 8.0


def _sidebar_grid_row_y(row: int) -> tuple[float, float]:
    y_min = _SIDEBAR_GRID_Y_START + row * _SIDEBAR_GRID_ROW_PITCH
    return y_min, y_min + _SIDEBAR_GRID_LINE_HEIGHT


def _sidebar_grid_column_block(x_min: float, x_max: float, cells: dict[int, str]) -> str:
    """One per-column block of a sidebar progression grid, holding only the
    rows in `cells` (a column printed for a subset of rows, as PHB p.37's
    Special column is)."""
    rows = sorted(cells)
    lines = []
    for row in rows:
        y_min, y_max = _sidebar_grid_row_y(row)
        lines.append(
            f'<line xMin="{x_min}" yMin="{y_min}" xMax="{x_max}" yMax="{y_max}">'
            f'<word xMin="{x_min}" yMin="{y_min}" xMax="{x_max}" yMax="{y_max}">{cells[row]}</word>'
            f"</line>"
        )
    block_y_min, _ = _sidebar_grid_row_y(rows[0])
    _, block_y_max = _sidebar_grid_row_y(rows[-1])
    return (
        f'<flow><block xMin="{x_min}" yMin="{block_y_min}" xMax="{x_max}" '
        f'yMax="{block_y_max}">{"".join(lines)}</block></flow>'
    )


def test_orphan_cell_wider_than_its_column_lands_in_its_own_row(tmp_path: Path) -> None:
    # Real-corpus regression (B10c-mand15 finding A, PHB p.37 "THE DRUID'S
    # ANIMAL COMPANION"): the grid's last Special cell ("Improved evasion",
    # printed on the 15th-17th band) arrives from poppler as a lone
    # single-cell block, and it is 2.3pt WIDER than every cell the Special
    # column block itself holds. Step 2b used to reject it for overhanging
    # the group's x span by those 2.3pt, so it was emitted as a standalone
    # paragraph after the whole table and the extractor had to guess a row.
    levels = ["Class/Level", "1st-2nd", "3rd-5th", "6th-8th", "9th-11th"]
    levels += ["12th-14th", "15th-17th", "18th-20th"]
    body = _sidebar_grid_column_block(76.6, 110.2, dict(enumerate(levels)))
    body += _sidebar_grid_column_block(
        120.4, 216.3, {row: ("Bonus HD" if row == 0 else f"+{2 * row - 2}") for row in range(8)}
    )
    body += _sidebar_grid_column_block(
        228.8, 250.2, {row: ("Tricks" if row == 0 else str(row)) for row in range(8)}
    )
    # The Special column block, printed only for the first four level bands.
    body += _sidebar_grid_column_block(
        257.5,
        315.0,
        {0: "Special", 1: "Link, share spells", 2: "Evasion", 3: "Devotion", 4: "Multiattack"},
    )
    # ...and the orphan cell, on the 15th-17th row, wider than the column.
    orphan_y_min, orphan_y_max = _sidebar_grid_row_y(6)
    orphan_box = f'xMin="257.5" yMin="{orphan_y_min}" xMax="317.3" yMax="{orphan_y_max}"'
    body += f"""
    <flow>
      <block {orphan_box}>
        <line {orphan_box}>
          <word {orphan_box}>Improved evasion</word>
        </line>
      </block>
    </flow>
    """
    page = _parse_page(tmp_path, "sidebar_grid_orphan_cell.html", body)

    ordered = order_blocks(page)

    table_groups = [item for item in ordered if isinstance(item, TableGroup)]
    assert len(table_groups) == 1
    rows = table_groups[0].rows
    assert len(rows) == 8
    assert rows[6].cells == ["15th-17th", "+10", "6", "Improved evasion"], rows[6].cells
    assert rows[7].cells == ["18th-20th", "+12", "7"], rows[7].cells

    # ...and it is no longer a loose block of its own anywhere on the page.
    assert not any(
        isinstance(item, Block) and "Improved evasion" in item.lines[0].text for item in ordered
    )


#: PHB p.53's own geometry for the FAMILIARS sidebar's `Familiar | Special`
#: grid: a label column at x 76.6 and a value column at x 121.8, ~9.9pt row
#: pitch, ~8pt lines.
_FAMILIAR_ROWS = [
    ("Familiar", 103.9, "Special"),
    ("Bat", 87.7, "Master gains a +3 bonus on Listen checks"),
    ("Cat", 87.8, "Master gains a +3 bonus on Move Silently checks"),
    ("Hawk", 95.7, "Master gains a +3 bonus on Spot checks in bright light"),
    ("Lizard", 97.3, "Master gains a +3 bonus on Climb checks"),
    ("Owl", 90.1, "Master gains a +3 bonus on Spot checks in shadows"),
    ("Rat", 87.6, "Master gains a +2 bonus on Fortitude saves"),
    ("Raven 1", 99.4, "Master gains a +3 bonus on Appraise checks"),
    ("Snake 2", 99.0, "Master gains a +3 bonus on Bluff checks"),
    ("Toad", 93.4, "Master gains +3 hit points"),
    ("Weasel", 100.4, "Master gains a +2 bonus on Reflex saves"),
]


def _label_value_grid_block(rows: list[tuple[str, float, str]] | None = None) -> str:
    """The shape `pdftotext` emits for the PHB's FAMILIARS `Familiar |
    Special` grid: ONE block, with the label and the value of each row as
    two separate `<line>`s at the same y, plus a two-line footnote."""
    lines = []
    y = 565.1
    for label, label_x_max, value in _FAMILIAR_ROWS if rows is None else rows:
        y_max = y + _SIDEBAR_GRID_LINE_HEIGHT
        lines.append(_line_of_words(y, y_max, [(76.6, label_x_max, label)]))
        value_words: list[tuple[float, float, str]] = []
        x = 121.8
        for token in value.split(" "):
            x_max = x + max(len(token) * 5.5, 8.0)
            value_words.append((x, x_max, token))
            x = x_max + 3.0
        lines.append(_line_of_words(y, y_max, value_words))
        y += _SIDEBAR_GRID_ROW_PITCH
    for footnote in ["1 A raven familiar can speak one language", "2 Tiny viper."]:
        y_max = y + _SIDEBAR_GRID_LINE_HEIGHT
        words: list[tuple[float, float, str]] = []
        x = 76.6
        for token in footnote.split(" "):
            x_max = x + max(len(token) * 5.5, 8.0)
            words.append((x, x_max, token))
            x = x_max + 3.0
        lines.append(_line_of_words(y, y_max, words))
        y += _SIDEBAR_GRID_ROW_PITCH
    return (
        f'<flow><block xMin="76.6" yMin="565.1" xMax="306.1" '
        f'yMax="{y - _SIDEBAR_GRID_ROW_PITCH + _SIDEBAR_GRID_LINE_HEIGHT}">'
        f"{''.join(lines)}</block></flow>"
    )


def test_two_column_label_value_grid_becomes_a_table_group(tmp_path: Path) -> None:
    # Real-corpus regression (B10c-mand15 finding B, PHB pp.53-54's
    # FAMILIARS sidebar): a two-column grid whose every row is a one-word
    # animal name beside a whole sentence. Every row has exactly ONE large
    # gap, so step 2a's `TABLE_MIN_GAPS_PER_ROW` (2) could never see it and
    # the grid came out as one run-on sentence with the Snake row's label
    # after its value. Step 2c must emit it as a table group with all 10
    # animal rows intact.
    page = _parse_page(tmp_path, "familiar_label_value_grid.html", _label_value_grid_block())

    ordered = order_blocks(page)

    table_groups = [item for item in ordered if isinstance(item, TableGroup)]
    assert len(table_groups) == 1, [item for item in ordered]
    rows = table_groups[0].rows
    assert rows[0].text == "Familiar\tSpecial"
    animal_rows = [row for row in rows if len(row.cells) == 2][1:]
    assert len(animal_rows) == 10, [row.cells for row in rows]
    assert animal_rows[0].text == "Bat\tMaster gains a +3 bonus on Listen checks"
    assert animal_rows[7].text == "Snake 2\tMaster gains a +3 bonus on Bluff checks"
    assert animal_rows[9].text == "Weasel\tMaster gains a +2 bonus on Reflex saves"
    # The footnotes below the grid stay single-cell rows, not tab-joined.
    footnotes = [row for row in rows if len(row.cells) == 1]
    assert [row.cells[0] for row in footnotes] == [
        "1 A raven familiar can speak one language",
        "2 Tiny viper.",
    ]


def test_ragged_prose_with_a_few_aligned_gaps_is_not_a_label_value_grid(tmp_path: Path) -> None:
    # The false positive step 2c's gappy-row-fraction guard exists for
    # (PHB p.28's bard column): prose flowed around an illustration, whose
    # ragged right edge and one embedded caption produce a handful of
    # accidentally aligned wide gaps among dozens of ordinary lines. Only
    # 3 of 12 rows carry a gap, so this must stay one prose block.
    rows: list[list[tuple[float, float, str]]] = []
    for row_idx in range(12):
        words: list[tuple[float, float, str]] = []
        x = 310.0
        for word_idx in range(6):
            x_max = x + 30.0
            words.append((x, x_max, f"w{row_idx}_{word_idx}"))
            x = x_max + 4.0
        if row_idx in {3, 7, 11}:
            # One extra word after a wide gap, at a consistent x position.
            words.append((x + 40.0, x + 70.0, f"tail{row_idx}"))
        rows.append(words)
    page = _parse_page(
        tmp_path, "ragged_prose_gaps.html", _wide_block_of_rows(rows, y_start=51.5, row_height=9.9)
    )

    ordered = order_blocks(page)

    assert len(ordered) == 1
    assert isinstance(ordered[0], Block)
    assert not any("\t" in line.text for line in ordered[0].lines)


def _narrow_gutter_block(x_min: float, y_min: float, x_max: float, y_max: float, text: str) -> str:
    """Like `_block`, but with a realistic ~8pt word height (rather than
    12pt) -- the column-gutter thresholds of steps 4/4a are multiples of the
    run's median word height, so a fixture reproducing a real narrow gutter
    needs the real text size too."""
    line_y_max = min(y_min + 8.0, y_max)
    return f"""
    <flow>
      <block xMin="{x_min}" yMin="{y_min}" xMax="{x_max}" yMax="{y_max}">
        <line xMin="{x_min}" yMin="{y_min}" xMax="{x_max}" yMax="{line_y_max}">
          <word xMin="{x_min}" yMin="{y_min}" xMax="{x_max}" yMax="{line_y_max}">{text}</word>
        </line>
      </block>
    </flow>
    """


def _paladin_mount_page_body() -> str:
    """PHB p.46's own geometry: two bands of ordinary body text (y 51-155)
    above a full-width boxed sidebar (y 193-471) whose own columns sit
    slightly wider than the body's, narrowing the gutter between them to
    ~9.6pt."""
    body = _narrow_gutter_block(34.0, 51.5, 274.2, 155.3, "LEFT_BODY")
    body += _narrow_gutter_block(292.1, 53.7, 454.3, 64.5, "RIGHT_HEADING")
    body += _narrow_gutter_block(301.1, 66.5, 541.3, 138.8, "RIGHT_BODY")
    body += _narrow_gutter_block(39.6, 193.1, 164.0, 203.9, "SIDEBAR_HEADING")
    body += _narrow_gutter_block(39.6, 206.3, 282.4, 293.3, "SIDEBAR_INTRO")
    body += _narrow_gutter_block(293.1, 196.4, 535.9, 471.4, "SIDEBAR_RIGHT")
    return body


def test_boxed_sidebar_columns_read_left_then_right(tmp_path: Path) -> None:
    # Real-corpus regression (B10c-mand15 finding C, PHB p.46 "THE
    # PALADIN'S MOUNT"): a boxed sidebar spanning both body columns sits
    # slightly wider than the body text, narrowing the gutter between the
    # two columns to ~9.6pt -- under step 4's
    # `COLUMN_GAP_HEIGHT_FACTOR` * word height (12pt). The greedy chain then
    # merged both columns into ONE cluster, which orders purely by y, so the
    # sidebar's right-column continuation (y 196.4, just below the box's
    # top) was emitted BEFORE its own left-column intro (y 206.3) and the
    # sidebar opened mid-sentence. Step 4a must split that cluster at the
    # gutter.
    #
    # B10c-mand22: and step 3a must band-split the run first, so the body
    # text above the box is emitted complete (both its columns) BEFORE the
    # sidebar -- see `test_body_text_is_never_emitted_inside_a_boxed_sidebar`.
    page = _parse_page(tmp_path, "boxed_sidebar_narrow_gutter.html", _paladin_mount_page_body())

    texts = [_block_text(item) for item in order_blocks(page)]

    assert texts == [
        "LEFT_BODY",
        "RIGHT_HEADING",
        "RIGHT_BODY",
        "SIDEBAR_HEADING",
        "SIDEBAR_INTRO",
        "SIDEBAR_RIGHT",
    ]


def test_body_text_is_never_emitted_inside_a_boxed_sidebar(tmp_path: Path) -> None:
    # Real-corpus regression (B10c-mand22 defect 2, PHB p.46 "THE PALADIN'S
    # MOUNT"): step 4a's cluster split is correct per column but the run it
    # splits stacks TWO layouts -- body text above, boxed sidebar below --
    # so the body's left column and the sidebar's left column ended up in
    # one cluster and the body's right column and the sidebar's right
    # column in another. Emitting left cluster then right cluster then put
    # the body's "Human Paladin Starting Package" heading and its
    # Armor/Weapons body INSIDE the sidebar, between its heading and the
    # rest of it. Step 3a cuts the run at the 37.8pt (4.8 line height)
    # whitespace band between the two regions first.
    page = _parse_page(tmp_path, "boxed_sidebar_body_band.html", _paladin_mount_page_body())

    texts = [_block_text(item) for item in order_blocks(page)]

    sidebar_start = texts.index("SIDEBAR_HEADING")
    assert texts[:sidebar_start] == ["LEFT_BODY", "RIGHT_HEADING", "RIGHT_BODY"], texts
    assert not any(
        text.endswith("_BODY") or text == "RIGHT_HEADING" for text in texts[sidebar_start:]
    )


def test_a_run_with_no_full_width_whitespace_band_is_not_split(tmp_path: Path) -> None:
    # Step 3a must not fire on an ordinary two-column page just because ONE
    # column has a tall gap in it: the band has to run the run's full width.
    # Here the left column breaks between y 60 and y 300 (30 line heights)
    # while the right column runs straight through, so the page stays one
    # band and reads left column then right column.
    body = _narrow_gutter_block(34.0, 51.5, 274.2, 60.0, "LEFT_TOP")
    body += _narrow_gutter_block(34.0, 300.0, 274.2, 400.0, "LEFT_BOTTOM")
    body += _narrow_gutter_block(300.0, 51.5, 540.0, 400.0, "RIGHT")
    page = _parse_page(tmp_path, "one_column_gap_only.html", body)

    assert [_block_text(item) for item in order_blocks(page)] == [
        "LEFT_TOP",
        "LEFT_BOTTOM",
        "RIGHT",
    ]


#: PHB p.41's own geometry for Table 3-10: The Monk -- ~9.894pt row pitch,
#: ~7.94pt lines, the grid's own columns ending at x 516.28 and the detached
#: "Unarmored Speed Bonus" column standing at x 540.08-561.68, 23.8pt (3.0
#: line heights) clear of it.
_MONK_ROW_FIRST_CENTER = 515.03
_MONK_ROW_PITCH = 9.894
_MONK_LINE_HEIGHT = 7.94

#: Local row index -> the level that row prints. Rows 15 and 17 are wrapped
#: continuations of the row above them ("slow fall 80 ft.", "tongue of the
#: sun and moon") and print no level or speed bonus of their own.
_MONK_LEVELS = {
    0: "2nd",
    1: "3rd",
    2: "4th",
    3: "5th",
    4: "6th",
    5: "7th",
    6: "8th",
    7: "9th",
    8: "10th",
    9: "11th",
    10: "12th",
    11: "13th",
    12: "14th",
    13: "15th",
    14: "16th",
    16: "17th",
    18: "18th",
    19: "19th",
    20: "20th",
}

#: The printed Unarmored Speed Bonus cells, by local row index: +0 ft. for
#: 1st-2nd (only 2nd is on this fixture's rows), then +10/+20/... every three
#: levels. Poppler emits them as THREE blocks -- rows 0-14, row 16, rows
#: 18-20 -- since the two continuation rows break the run.
_MONK_SPEED_CELLS = {
    0: "+0 ft.",
    **{row: "+10 ft." for row in (1, 2, 3)},
    **{row: "+20 ft." for row in (4, 5, 6)},
    **{row: "+30 ft." for row in (7, 8, 9)},
    **{row: "+40 ft." for row in (10, 11, 12)},
    **{row: "+50 ft." for row in (13, 14, 16)},
    **{row: "+60 ft." for row in (18, 19, 20)},
}


def _monk_row_y(row: int) -> tuple[float, float]:
    center = _MONK_ROW_FIRST_CENTER + row * _MONK_ROW_PITCH
    return center - _MONK_LINE_HEIGHT / 2, center + _MONK_LINE_HEIGHT / 2


def _monk_column_block(x_min: float, x_max: float, cells: dict[int, str]) -> str:
    """One detached-column block: a run of consecutive rows' cells, each its
    own `<line>`, in a block standing to the right of the grid."""
    rows = sorted(cells)
    lines = []
    for row in rows:
        y_min, y_max = _monk_row_y(row)
        box = f'xMin="{x_min}" yMin="{y_min}" xMax="{x_max}" yMax="{y_max}"'
        lines.append(f"<line {box}><word {box}>{cells[row]}</word></line>")
    block_y_min, _ = _monk_row_y(rows[0])
    _, block_y_max = _monk_row_y(rows[-1])
    return (
        f'<flow><block xMin="{x_min}" yMin="{block_y_min}" xMax="{x_max}" '
        f'yMax="{block_y_max}">{"".join(lines)}</block></flow>'
    )


#: The grid's own three columns, as x extents: Level, Base Attack Bonus and
#: Special, the last ending at x 516.28.
_MONK_GRID_COLUMNS = [(71.20, 88.34), (107.44, 151.55), (260.00, 350.00)]


def _monk_grid_block() -> str:
    """The grid itself, as PHB p.41 emits it: ONE block holding every cell as
    its own `<line>`, which step 2a picks up as a single-block table. That
    the grid is a single block (rather than a clique of per-column blocks) is
    exactly why step 2b's absorb never sees the detached column: there is no
    clique whose base span to measure it against."""
    lines = []
    for row in range(21):
        y_min, y_max = _monk_row_y(row)
        level = _MONK_LEVELS.get(row)
        cells = (
            [(_MONK_GRID_COLUMNS[2], "slow fall 80 ft." if row == 15 else "tongue of the sun")]
            if level is None
            else [
                (_MONK_GRID_COLUMNS[0], level),
                (_MONK_GRID_COLUMNS[1], f"+{row + 1}"),
                (_MONK_GRID_COLUMNS[2], "Bonus feat"),
            ]
        )
        for (x_min, x_max), text in cells:
            box = f'xMin="{x_min}" yMin="{y_min}" xMax="{x_max}" yMax="{y_max}"'
            lines.append(f"<line {box}><word {box}>{text}</word></line>")
    y_min, _ = _monk_row_y(0)
    _, y_max = _monk_row_y(20)
    return (
        f'<flow><block xMin="71.20" yMin="{y_min}" xMax="516.28" '
        f'yMax="{y_max}">{"".join(lines)}</block></flow>'
    )


def test_detached_column_is_folded_into_its_own_rows(tmp_path: Path) -> None:
    # Real-corpus regression (B10c-mand22 defect 1, PHB p.41 Table 3-10: The
    # Monk): the whole "Unarmored Speed Bonus" column arrives from poppler as
    # three blocks standing 23.8pt clear of the grid's own last column, so
    # step 2b's absorb (which requires a block's x-extent INSIDE the clique's
    # base span, bar one line height) could never take them, and the column
    # was emitted as loose paragraphs after the table -- the extractor then
    # reattached it at the wrong offset, 18 of 20 cells wrong.
    body = _monk_grid_block()
    body += _monk_column_block(540.08, 561.68, {r: _MONK_SPEED_CELLS[r] for r in range(15)})
    body += _monk_column_block(540.25, 561.69, {16: _MONK_SPEED_CELLS[16]})
    body += _monk_column_block(540.10, 561.66, {r: _MONK_SPEED_CELLS[r] for r in (18, 19, 20)})
    page = _parse_page(tmp_path, "monk_detached_column.html", body)

    ordered = order_blocks(page)

    table_groups = [item for item in ordered if isinstance(item, TableGroup)]
    assert len(table_groups) == 1
    rows = table_groups[0].rows
    assert len(rows) == 21
    by_level = {row.cells[0]: row.cells for row in rows}
    for level in ("3rd", "4th", "5th"):
        assert by_level[level][-1] == "+10 ft.", by_level[level]
    for level in ("18th", "19th", "20th"):
        assert by_level[level][-1] == "+60 ft.", by_level[level]
    # A continuation row inside the column's own span gets an empty cell, so
    # the column stays aligned rather than shifting up a row.
    assert rows[15].cells == ["slow fall 80 ft.", ""], rows[15].cells

    # ...and none of the three blocks survives as a loose paragraph.
    assert not any(isinstance(item, Block) for item in ordered), ordered


def test_a_block_beside_a_table_covering_too_few_rows_stays_out(tmp_path: Path) -> None:
    # Step 2d's row-coverage guard: a two-line note printed beside the grid
    # (aligned with two of its rows by coincidence) is not a column, and
    # must stay an ordinary block.
    body = _monk_grid_block()
    body += _monk_column_block(540.08, 561.68, {3: "see", 4: "below"})
    page = _parse_page(tmp_path, "note_beside_table.html", body)

    ordered = order_blocks(page)

    table_groups = [item for item in ordered if isinstance(item, TableGroup)]
    assert len(table_groups) == 1
    assert all(len(row.cells) <= 3 for row in table_groups[0].rows)
    assert any(isinstance(item, Block) and "see" in item.lines[0].text for item in ordered)


#: The FAMILIARS grid with a long (6-word) header value instead of the
#: printed one-word "Special". Averaged across BOTH cells, a label/value
#: grid's median cell length depends on how many words the header happens to
#: print -- which is not a fact about the grid -- so step 2c must not apply
#: step 2a's `TABLE_CELL_MAX_MEDIAN_WORDS` check at all.
_FAMILIAR_ROWS_LONG_HEADER = [
    ("Familiar", 103.9, "Special ability granted to the master"),
    *_FAMILIAR_ROWS[1:],
]


def test_label_value_grid_survives_a_long_header_row(tmp_path: Path) -> None:
    # Review regression (B10c-mand15, finding (1)): step 2c used to delegate
    # to `_build_single_block_table_or_prose_split` with its both-cells
    # `TABLE_CELL_MAX_MEDIAN_WORDS` (4) check still armed, so the FAMILIARS
    # grid only passed because its own one-word "Special" header happened to
    # contribute a second short cell. Widen that header to 6 words and the
    # median crosses 4, rejecting all 10 otherwise-identical animal rows.
    page = _parse_page(
        tmp_path,
        "familiar_grid_long_header.html",
        _label_value_grid_block(_FAMILIAR_ROWS_LONG_HEADER),
    )

    ordered = order_blocks(page)

    table_groups = [item for item in ordered if isinstance(item, TableGroup)]
    assert len(table_groups) == 1, ordered
    rows = table_groups[0].rows
    assert rows[0].text == "Familiar\tSpecial ability granted to the master"
    animal_rows = [row for row in rows if len(row.cells) == 2][1:]
    assert len(animal_rows) == 10, [row.cells for row in rows]
    assert animal_rows[7].text == "Snake 2\tMaster gains a +3 bonus on Bluff checks"


def test_label_value_grid_with_no_header_row_is_still_detected(tmp_path: Path) -> None:
    # The same point from the other side: a grid printed with NO header row
    # at all has nothing but sentence-like values on its right, so the
    # both-cells median is above 4 for every row. Its label side is still a
    # column of one-word names, which is the discriminator step 2c uses.
    page = _parse_page(
        tmp_path,
        "familiar_grid_no_header.html",
        _label_value_grid_block(list(_FAMILIAR_ROWS[1:])),
    )

    ordered = order_blocks(page)

    table_groups = [item for item in ordered if isinstance(item, TableGroup)]
    assert len(table_groups) == 1, ordered
    animal_rows = [row for row in table_groups[0].rows if len(row.cells) == 2]
    assert len(animal_rows) == 10, [row.cells for row in table_groups[0].rows]
    assert animal_rows[0].text == "Bat\tMaster gains a +3 bonus on Listen checks"
    assert animal_rows[7].text == "Snake 2\tMaster gains a +3 bonus on Bluff checks"


def _sidebar_grid_base_body() -> str:
    """The PHB p.37 sidebar grid's four per-column blocks, with NO orphan
    cell -- the base the overhang tests below add one to. The Special
    column's own widest cell reaches x 315.0, and every line is 8pt tall, so
    step 2b's absorb tolerance is 8.0pt either side of x[76.6, 315.0]."""
    levels = ["Class/Level", "1st-2nd", "3rd-5th", "6th-8th", "9th-11th"]
    levels += ["12th-14th", "15th-17th", "18th-20th"]
    body = _sidebar_grid_column_block(76.6, 110.2, dict(enumerate(levels)))
    body += _sidebar_grid_column_block(
        120.4, 216.3, {row: ("Bonus HD" if row == 0 else f"+{2 * row - 2}") for row in range(8)}
    )
    body += _sidebar_grid_column_block(
        228.8, 250.2, {row: ("Tricks" if row == 0 else str(row)) for row in range(8)}
    )
    return body + _sidebar_grid_column_block(
        257.5,
        315.0,
        {0: "Special", 1: "Link, share spells", 2: "Evasion", 3: "Devotion", 4: "Multiattack"},
    )


def _sidebar_grid_orphan(row: int, x_min: float, x_max: float, text: str) -> str:
    y_min, y_max = _sidebar_grid_row_y(row)
    box = f'xMin="{x_min}" yMin="{y_min}" xMax="{x_max}" yMax="{y_max}"'
    return f"<flow><block {box}><line {box}><word {box}>{text}</word></line></block></flow>"


def test_orphan_overhanging_beyond_the_tolerance_stays_out(tmp_path: Path) -> None:
    # The absorb tolerance is one median line height (8pt) either side of
    # the group's base x span, which ends at 315.0. A cell reaching 330.0
    # overhangs by 15pt and must NOT be absorbed; neither must a cell in the
    # neighbouring column, even at the exact y of one of the grid's rows.
    body = _sidebar_grid_base_body()
    body += _sidebar_grid_orphan(6, 257.5, 330.0, "TOO_WIDE")
    body += _sidebar_grid_orphan(3, 340.0, 420.0, "NEIGHBOUR_COLUMN")
    page = _parse_page(tmp_path, "sidebar_grid_overhang_rejected.html", body)

    ordered = order_blocks(page)

    table_groups = [item for item in ordered if isinstance(item, TableGroup)]
    assert len(table_groups) == 1
    cell_texts = [cell for row in table_groups[0].rows for cell in row.cells]
    assert "TOO_WIDE" not in cell_texts
    assert "NEIGHBOUR_COLUMN" not in cell_texts
    loose = [item.lines[0].text for item in ordered if isinstance(item, Block)]
    assert "TOO_WIDE" in loose and "NEIGHBOUR_COLUMN" in loose, loose


def test_absorbing_an_overhanging_orphan_does_not_widen_the_tolerance(tmp_path: Path) -> None:
    # Review regression (B10c-mand15, finding (2)): the tolerance used to be
    # measured against the group's CURRENT extent, which
    # `_reassemble_table_groups` re-derives after every absorb -- so an
    # absorbed cell reaching 322.0 (7pt overhang, accepted) moved the goal
    # posts, and a second cell reaching 329.0 then looked like a 7pt
    # overhang too and ratcheted the group outward one tolerance at a time.
    # Measured against the clique's own base span (x_max 315.0) the second
    # cell overhangs by 14pt and must stay out.
    body = _sidebar_grid_base_body()
    body += _sidebar_grid_orphan(5, 257.5, 322.0, "WITHIN_TOLERANCE")
    body += _sidebar_grid_orphan(7, 257.5, 329.0, "RATCHET")
    page = _parse_page(tmp_path, "sidebar_grid_no_ratchet.html", body)

    ordered = order_blocks(page)

    table_groups = [item for item in ordered if isinstance(item, TableGroup)]
    assert len(table_groups) == 1
    cell_texts = [cell for row in table_groups[0].rows for cell in row.cells]
    assert "WITHIN_TOLERANCE" in cell_texts, cell_texts
    assert "RATCHET" not in cell_texts, cell_texts
    loose = [item.lines[0].text for item in ordered if isinstance(item, Block)]
    assert loose == ["RATCHET"], loose


def test_over_wide_cluster_with_no_qualifying_valley_stays_merged(tmp_path: Path) -> None:
    # Step 4a's safety is the VALLEY, not the width: an over-wide cluster
    # whose only internal gap is 4pt -- under
    # `COLUMN_VALLEY_GAP_HEIGHT_FACTOR` (1.0) * the 8pt median word height
    # -- is left exactly as the greedy pass built it, i.e. ordered by y.
    body = _narrow_gutter_block(34.0, 51.5, 274.2, 155.3, "LEFT_TOP")
    body += _narrow_gutter_block(278.2, 53.7, 541.3, 138.8, "RIGHT_TOP")
    body += _narrow_gutter_block(34.0, 200.0, 274.2, 293.3, "LEFT_LOW")
    body += _narrow_gutter_block(278.2, 196.0, 541.3, 280.0, "RIGHT_LOW")
    page = _parse_page(tmp_path, "over_wide_no_valley.html", body)

    texts = [_block_text(item) for item in order_blocks(page)]

    assert texts == ["LEFT_TOP", "RIGHT_TOP", "RIGHT_LOW", "LEFT_LOW"]


def test_wide_single_column_run_with_no_valley_is_not_split(tmp_path: Path) -> None:
    # A run whose blocks overlap each other's x-extents in a chain covers
    # its whole span with no valley at all (`_widest_valley` returns None),
    # so however wide the cluster is it stays one column, read top to
    # bottom. The fixture's y order deliberately disagrees with its x order,
    # so a split would be visible.
    body = _narrow_gutter_block(34.0, 300.0, 300.0, 340.0, "LOWEST_LEFTMOST")
    body += _narrow_gutter_block(200.0, 100.0, 450.0, 140.0, "TOP_MIDDLE")
    body += _narrow_gutter_block(380.0, 200.0, 541.0, 240.0, "MIDDLE_RIGHTMOST")
    page = _parse_page(tmp_path, "wide_single_column.html", body)

    texts = [_block_text(item) for item in order_blocks(page)]

    assert texts == ["TOP_MIDDLE", "MIDDLE_RIGHTMOST", "LOWEST_LEFTMOST"]
