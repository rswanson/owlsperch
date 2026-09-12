"""Unit tests for `owlsperch.text.columns` -- reading-order reconstruction,
built on hand-written `pdftotext -bbox-layout` XHTML fixtures.

Covers acceptance criterion 6(a): two-column ordering with a wide heading
block, plus single-column pages and rotated/vertical marginal blocks.
"""

from __future__ import annotations

from pathlib import Path

from owlsperch.text.bbox import Page, parse_bbox_xhtml
from owlsperch.text.columns import is_vertical_block, order_blocks

_NS = 'xmlns="http://www.w3.org/1999/xhtml"'


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
    return f"""
    <flow>
      <block xMin="{x_min}" yMin="{y_min}" xMax="{x_max}" yMax="{y_max}">
        <line xMin="{x_min}" yMin="{y_min}" xMax="{x_max}" yMax="{y_max + 1}">
          <word xMin="{x_min}" yMin="{y_min}" xMax="{x_max}" yMax="{y_max + 1}">{text}</word>
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

    texts = [b.lines[0].text for b in ordered]
    assert texts == ["HEADING", "LEFT", "RIGHT"]


def test_single_column_page_orders_top_to_bottom(tmp_path: Path) -> None:
    first = _block(34, 40, 580, 60, "FIRST")
    second = _block(34, 80, 580, 120, "SECOND")
    third = _block(34, 140, 580, 180, "THIRD")
    # Deliberately out of y-order in the fixture to prove sorting happens.
    page = _parse_page(tmp_path, "single_col.html", third + first + second)

    ordered = order_blocks(page)

    texts = [b.lines[0].text for b in ordered]
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

    texts = [b.lines[0].text for b in ordered]
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
    texts = [b.lines[0].text for b in ordered]
    assert texts == ["BODY"]
    assert "CHAPTER 7:" not in texts
