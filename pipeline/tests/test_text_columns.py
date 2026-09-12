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
