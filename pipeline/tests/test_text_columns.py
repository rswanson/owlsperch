"""Unit tests for `owlsperch.text.columns` -- reading-order reconstruction,
built on hand-written `pdftotext -bbox-layout` XHTML fixtures.

Covers acceptance criterion 6(a): two-column ordering with a wide heading
block, plus single-column pages and rotated/vertical marginal blocks.
"""

from __future__ import annotations

from pathlib import Path

from owlsperch.text.bbox import Block, Page, parse_bbox_xhtml
from owlsperch.text.columns import TableGroup, is_vertical_block, order_blocks

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
