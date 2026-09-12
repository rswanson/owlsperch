"""Parser for `pdftotext -bbox-layout` XHTML output.

`pdftotext -bbox-layout <pdf> <out.html>` (poppler) emits one XHTML document
for the whole PDF (or the page range requested with `-f`/`-l`) with this
element hierarchy, all in the `http://www.w3.org/1999/xhtml` namespace:

    <doc>
      <page width="..." height="...">
        <flow>
          <block xMin=".." yMin=".." xMax=".." yMax="..">
            <line xMin=".." yMin=".." xMax=".." yMax="..">
              <word xMin=".." yMin=".." xMax=".." yMax="..">text</word>
              ...
            </line>
            ...
          </block>
          ...
        </flow>
        ...
      </page>
      ...
    </doc>

`<page>` elements carry no page-number attribute -- the Nth `<page>` in
document order corresponds to PDF page N when the whole document was
converted, or to PDF page `first_page + N - 1` when `-f`/`-l` restricted the
range (see `owlsperch.text.runner.run_pdftotext`).

A page can have several `<flow>` siblings; poppler's flow grouping does not
reliably correspond to reading columns, so this module flattens every flow's
blocks into one list per page and leaves column ordering to
`owlsperch.text.columns`.

**Invalid XML control characters.** A handful of real-world PDFs (found
while working on batch B3, against the real Player's Handbook) have a font
glyph that `pdftotext` extracts as a raw C0 control character -- e.g.
`\x01` -- inside a `<word>`'s text. XML 1.0 forbids those characters
anywhere in a document, so `ET.parse` raises `xml.etree.ElementTree.
ParseError: not well-formed (invalid token)` on the raw file. Since these
characters are extraction noise, not real glyph content, they are stripped
(at the decoded-character level, so a multi-byte UTF-8 sequence is never
split) before parsing -- see `_strip_invalid_xml_chars`.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

_XHTML_NS = "{http://www.w3.org/1999/xhtml}"

#: XML 1.0's Char production excludes every C0 control character except
#: tab/newline/carriage-return; see the module docstring's "Invalid XML
#: control characters".
_INVALID_XML_CHAR_RE = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _strip_invalid_xml_chars(text: str) -> str:
    return _INVALID_XML_CHAR_RE.sub("", text)


def _tag(name: str) -> str:
    return f"{_XHTML_NS}{name}"


def _local_name(tag: str) -> str:
    """Strip a `{namespace}` prefix, if any, from an element tag."""
    return tag.rsplit("}", 1)[-1]


def _bbox_attrs(elem: ET.Element) -> tuple[float, float, float, float]:
    return (
        float(elem.attrib["xMin"]),
        float(elem.attrib["yMin"]),
        float(elem.attrib["xMax"]),
        float(elem.attrib["yMax"]),
    )


@dataclass(frozen=True)
class Word:
    x_min: float
    y_min: float
    x_max: float
    y_max: float
    text: str


@dataclass(frozen=True)
class Line:
    x_min: float
    y_min: float
    x_max: float
    y_max: float
    words: list[Word] = field(default_factory=list)

    @property
    def width(self) -> float:
        return self.x_max - self.x_min

    @property
    def height(self) -> float:
        return self.y_max - self.y_min

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words if w.text)


@dataclass(frozen=True)
class Block:
    x_min: float
    y_min: float
    x_max: float
    y_max: float
    lines: list[Line] = field(default_factory=list)

    @property
    def width(self) -> float:
        return self.x_max - self.x_min

    @property
    def x_center(self) -> float:
        return (self.x_min + self.x_max) / 2


@dataclass(frozen=True)
class Page:
    width: float
    height: float
    blocks: list[Block] = field(default_factory=list)


def _parse_word(elem: ET.Element) -> Word:
    x_min, y_min, x_max, y_max = _bbox_attrs(elem)
    return Word(x_min, y_min, x_max, y_max, text=elem.text or "")


def _parse_line(elem: ET.Element) -> Line:
    x_min, y_min, x_max, y_max = _bbox_attrs(elem)
    words = [_parse_word(w) for w in elem if _local_name(w.tag) == "word"]
    return Line(x_min, y_min, x_max, y_max, words)


def _parse_block(elem: ET.Element) -> Block:
    x_min, y_min, x_max, y_max = _bbox_attrs(elem)
    lines = [_parse_line(line_elem) for line_elem in elem if _local_name(line_elem.tag) == "line"]
    return Block(x_min, y_min, x_max, y_max, lines)


def _parse_page(elem: ET.Element) -> Page:
    width = float(elem.attrib["width"])
    height = float(elem.attrib["height"])
    blocks: list[Block] = []
    # Flatten every <flow>'s <block> children -- flow grouping is not a
    # reliable proxy for reading columns (see module docstring).
    for flow_elem in elem:
        if _local_name(flow_elem.tag) != "flow":
            continue
        for block_elem in flow_elem:
            if _local_name(block_elem.tag) == "block":
                blocks.append(_parse_block(block_elem))
    return Page(width, height, blocks)


def parse_bbox_xhtml(path: Path) -> list[Page]:
    """Parse a `pdftotext -bbox-layout` XHTML file into a list of `Page`s,
    one per `<page>` element in document order."""
    raw_bytes = path.read_bytes()
    text = raw_bytes.decode("utf-8", errors="replace")
    root = ET.fromstring(_strip_invalid_xml_chars(text))
    doc = root.find(_tag("body") + "/" + _tag("doc"))
    if doc is None:
        # Some poppler versions omit the wrapping <body>; fall back to
        # searching the whole tree for <doc>.
        doc = root.find(f".//{_tag('doc')}")
    if doc is None:
        return []
    return [_parse_page(page_elem) for page_elem in doc if _local_name(page_elem.tag) == "page"]
