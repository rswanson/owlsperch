"""Unit tests for `owlsperch.text.bbox` -- parsing `pdftotext -bbox-layout`
XHTML into Page/Block/Line/Word."""

from __future__ import annotations

from pathlib import Path

from owlsperch.text.bbox import parse_bbox_xhtml

_SIMPLE = """<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>Fixture</title></head>
<body>
<doc>
  <page width="612.000000" height="792.000000">
    <flow>
      <block xMin="34.0" yMin="40.0" xMax="200.0" yMax="60.0">
        <line xMin="34.0" yMin="40.0" xMax="200.0" yMax="60.0">
          <word xMin="34.0" yMin="40.0" xMax="80.0" yMax="60.0">Hello,</word>
          <word xMin="85.0" yMin="40.0" xMax="200.0" yMax="60.0">world</word>
        </line>
      </block>
    </flow>
  </page>
  <page width="612.000000" height="792.000000">
    <flow>
      <block xMin="34.0" yMin="40.0" xMax="200.0" yMax="60.0">
        <line xMin="34.0" yMin="40.0" xMax="200.0" yMax="60.0">
          <word xMin="34.0" yMin="40.0" xMax="80.0" yMax="60.0">Page</word>
          <word xMin="85.0" yMin="40.0" xMax="200.0" yMax="60.0">two</word>
        </line>
      </block>
    </flow>
  </page>
</doc>
</body>
</html>
"""


def test_parses_pages_blocks_lines_words_in_order(tmp_path: Path) -> None:
    html_path = tmp_path / "fixture.html"
    html_path.write_text(_SIMPLE)

    pages = parse_bbox_xhtml(html_path)

    assert len(pages) == 2
    assert pages[0].width == 612.0
    assert pages[0].height == 792.0
    assert len(pages[0].blocks) == 1
    block = pages[0].blocks[0]
    assert (block.x_min, block.y_min, block.x_max, block.y_max) == (34.0, 40.0, 200.0, 60.0)
    assert len(block.lines) == 1
    line = block.lines[0]
    assert [w.text for w in line.words] == ["Hello,", "world"]
    assert line.text == "Hello, world"

    assert pages[1].blocks[0].lines[0].text == "Page two"


def test_empty_doc_returns_no_pages(tmp_path: Path) -> None:
    html_path = tmp_path / "empty.html"
    html_path.write_text(
        '<html xmlns="http://www.w3.org/1999/xhtml"><body><doc></doc></body></html>'
    )
    assert parse_bbox_xhtml(html_path) == []


def test_invalid_xml_control_character_in_word_text_is_stripped(tmp_path: Path) -> None:
    """Regression test: a real-world PDF's `pdftotext -bbox-layout` output can
    contain a raw C0 control character (observed: `\\x01`) inside a <word>'s
    text -- invalid in XML 1.0, which `ET.parse` used to reject outright with
    `not well-formed (invalid token)`. It should be stripped, not crash."""
    html_path = tmp_path / "bad_char.html"
    html_bytes = (
        b'<html xmlns="http://www.w3.org/1999/xhtml"><body><doc>'
        b'<page width="612.000000" height="792.000000"><flow>'
        b'<block xMin="34.0" yMin="40.0" xMax="200.0" yMax="60.0">'
        b'<line xMin="34.0" yMin="40.0" xMax="200.0" yMax="60.0">'
        b'<word xMin="34.0" yMin="40.0" xMax="80.0" yMax="60.0">Real'
        b"\x01"
        b"text</word>"
        b"</line></block></flow></page></doc></body></html>"
    )
    html_path.write_bytes(html_bytes)

    pages = parse_bbox_xhtml(html_path)

    assert len(pages) == 1
    assert pages[0].blocks[0].lines[0].words[0].text == "Realtext"
