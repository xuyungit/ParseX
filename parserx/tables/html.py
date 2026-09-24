"""HTML table parsing shared by the v1 OCR builder and TableGrid.

Moved verbatim from ``parserx/builders/ocr.py`` (Phase 0, P0-1); the v1
builder imports these helpers back, so its behaviour is unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
from typing import Iterable


_VOID_TAGS = {
    "br", "hr", "img", "meta", "link", "input", "source",
    "area", "base", "col", "embed", "param", "track", "wbr",
}
_SECTION_TAGS = {"thead", "tbody", "tfoot"}
_CELL_TAGS = {"td", "th"}


@dataclass
class _HTMLNode:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list["_HTMLNode"] = field(default_factory=list)
    text_parts: list[str] = field(default_factory=list)
    parent: "_HTMLNode | None" = None

    def append_text(self, data: str) -> None:
        if data:
            self.text_parts.append(data)

    @property
    def text(self) -> str:
        return _normalize_html_text(" ".join(part for part in self.text_parts if part))

    def descendants(self, tag: str | None = None) -> Iterable["_HTMLNode"]:
        for child in self.children:
            if tag is None or child.tag == tag:
                yield child
            yield from child.descendants(tag)


class _MiniHTMLTreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _HTMLNode("document")
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = _HTMLNode(
            tag=tag.lower(),
            attrs={k.lower(): v or "" for k, v in attrs},
            parent=self.stack[-1],
        )
        self.stack[-1].children.append(node)
        if node.tag not in _VOID_TAGS:
            self.stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                self.stack = self.stack[:index]
                return

    def handle_data(self, data: str) -> None:
        self.stack[-1].append_text(data)
        if data.strip():
            text_node = _HTMLNode(tag="#text", parent=self.stack[-1])
            text_node.text_parts.append(data)
            self.stack[-1].children.append(text_node)


@dataclass
class _TableCell:
    text: str
    is_header: bool
    rowspan: int
    colspan: int
    section: str
    row_index: int
    source_tag: str
    scope: str = ""


@dataclass
class _GridSlot:
    text: str
    is_header: bool
    row_from: int
    col_from: int
    row_to: int
    col_to: int
    row_span_cont: bool
    col_span_cont: bool
    section: str
    source_tag: str
    scope: str

    @property
    def is_origin(self) -> bool:
        return not self.row_span_cont and not self.col_span_cont


class _TableConversionError(ValueError):
    pass


def _safe_int(value: str | None, default: int) -> int:
    try:
        return max(int(value or default), 1)
    except (TypeError, ValueError):
        return default


def _normalize_html_text(text: str) -> str:
    return " ".join(unescape(text).split())


def _parse_tables(html: str) -> list[_HTMLNode]:
    parser = _MiniHTMLTreeBuilder()
    parser.feed(html)
    parser.close()
    return list(parser.root.descendants("table"))


def _get_table(html: str) -> _HTMLNode:
    tables = _parse_tables(html)
    if not tables:
        raise _TableConversionError("no <table> found")
    return tables[0]


def _extract_cell_text(node: _HTMLNode, line_join: str = " / ") -> str:
    """Cell text; lines (``<br>``, block elements) are joined with *line_join* (v1: " / "; TableGrid: a newline)."""
    parts: list[str] = []

    def walk(current: _HTMLNode) -> None:
        for child in current.children:
            if child.tag == "#text":
                text = _normalize_html_text(child.text)
                if text:
                    parts.append(text)
            elif child.tag == "img":
                src = child.attrs.get("src", "")
                alt = child.attrs.get("alt", "")
                parts.append(f"![{alt}]({src})")
            elif child.tag == "br":
                parts.append("\n")
            else:
                walk(child)
                if child.tag in {"p", "div", "li"}:
                    parts.append("\n")

    walk(node)
    text = " ".join(parts)
    text = text.replace(" \n ", "\n").replace("\n ", "\n").replace(" \n", "\n")
    lines = [_normalize_html_text(line) for line in text.split("\n")]
    lines = [line for line in lines if line]
    return line_join.join(lines) if lines else ""


def _infer_table_section(node: _HTMLNode) -> str:
    parent = node.parent
    while parent:
        if parent.tag in _SECTION_TAGS:
            return parent.tag
        parent = parent.parent
    return "tbody"


def _parse_row(
    tr: _HTMLNode,
    section: str,
    row_index: int,
    line_join: str = " / ",
) -> list[_TableCell]:
    cells: list[_TableCell] = []
    for child in tr.children:
        if child.tag not in _CELL_TAGS:
            continue
        cells.append(_TableCell(
            text=_extract_cell_text(child, line_join),
            is_header=child.tag == "th",
            rowspan=_safe_int(child.attrs.get("rowspan"), 1),
            colspan=_safe_int(child.attrs.get("colspan"), 1),
            section=section,
            row_index=row_index,
            source_tag=child.tag,
            scope=(child.attrs.get("scope", "") or "").strip().lower(),
        ))
    return cells


def _collect_rows(table: _HTMLNode, line_join: str = " / ") -> list[tuple[str, list[_TableCell]]]:
    rows: list[tuple[str, list[_TableCell]]] = []
    direct = [child for child in table.children if child.tag in _SECTION_TAGS or child.tag == "tr"]
    if not direct:
        direct = table.children
    for child in direct:
        if child.tag == "tr":
            rows.append(("tbody", _parse_row(child, "tbody", len(rows), line_join)))
        elif child.tag in _SECTION_TAGS:
            for tr in [grandchild for grandchild in child.children if grandchild.tag == "tr"]:
                rows.append((child.tag, _parse_row(tr, child.tag, len(rows), line_join)))
    if not rows:
        for tr in table.descendants("tr"):
            section = _infer_table_section(tr)
            rows.append((section, _parse_row(tr, section, len(rows), line_join)))
    return rows


def _build_table_grid(rows: list[tuple[str, list[_TableCell]]]) -> list[list[_GridSlot | None]]:
    grid: list[list[_GridSlot | None]] = []
    for row_idx, (section, cells) in enumerate(rows):
        while len(grid) <= row_idx:
            grid.append([])
        col = 0
        for cell in cells:
            row = grid[row_idx]
            while col < len(row) and row[col] is not None:
                col += 1
            for r in range(row_idx, row_idx + cell.rowspan):
                while len(grid) <= r:
                    grid.append([])
                if len(grid[r]) < col + cell.colspan:
                    grid[r].extend([None] * (col + cell.colspan - len(grid[r])))
                for c in range(col, col + cell.colspan):
                    if grid[r][c] is not None:
                        raise _TableConversionError(f"overlapping spans at row={r} col={c}")
                    grid[r][c] = _GridSlot(
                        text=cell.text,
                        is_header=cell.is_header,
                        row_from=row_idx,
                        col_from=col,
                        row_to=row_idx + cell.rowspan - 1,
                        col_to=col + cell.colspan - 1,
                        row_span_cont=r > row_idx,
                        col_span_cont=c > col,
                        section=section,
                        source_tag=cell.source_tag,
                        scope=cell.scope,
                    )
            col += cell.colspan
    width = max((len(row) for row in grid), default=0)
    for row in grid:
        if len(row) < width:
            row.extend([None] * (width - len(row)))
    return grid


def _detect_header_block(grid: list[list[_GridSlot | None]]) -> int:
    depth = 0
    for row_idx, row in enumerate(grid):
        substantive = [slot for slot in row if slot is not None]
        if not substantive:
            if depth == 0:
                continue
            break
        origins = [
            slot for col_idx, slot in enumerate(row)
            if slot is not None and slot.row_from == row_idx and slot.col_from == col_idx
        ]
        if not origins:
            if all(slot.source_tag == "th" and slot.row_from < row_idx for slot in substantive):
                depth = row_idx + 1
                continue
            break
        if any(slot.source_tag == "td" for slot in origins):
            break
        if all(slot.source_tag == "th" for slot in origins):
            depth = row_idx + 1
            continue
        break
    return depth
