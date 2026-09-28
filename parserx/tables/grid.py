"""TableGrid: the primary representation of a table (guide §4.3).

GFM and HTML are both parsed into a grid of origin cells with spans; metrics,
cross-page merging and rendering all work on the grid, never on Markdown text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import escape as html_escape

from pydantic import Field, model_validator

from parserx.content.text import escape_strikethrough
from parserx.ir.anchor import SourceAnchor
from parserx.ir.base import IRModel
from parserx.tables.html import (
    _TableConversionError,
    _build_table_grid,
    _collect_rows,
    _detect_header_block,
    _get_table,
)


class Cell(IRModel):
    row: int = Field(ge=0)
    col: int = Field(ge=0)
    rowspan: int = Field(1, ge=1)
    colspan: int = Field(1, ge=1)
    content: str
    is_header: bool = False
    anchors: list[SourceAnchor] = []
    rec_confidence: float | None = None


class TableGrid(IRModel):
    """Origin cells only; positions covered by a span are not repeated."""

    n_rows: int = Field(ge=0)
    n_cols: int = Field(ge=0)
    cells: list[Cell] = []
    header_rows: int = Field(0, ge=0)

    @model_validator(mode="after")
    def _check_layout(self) -> "TableGrid":
        if self.header_rows > self.n_rows:
            raise ValueError(f"header_rows={self.header_rows} exceeds n_rows={self.n_rows}")
        occupied: set[tuple[int, int]] = set()
        for cell in self.cells:
            if cell.row + cell.rowspan > self.n_rows or cell.col + cell.colspan > self.n_cols:
                raise ValueError(f"cell at ({cell.row}, {cell.col}) extends outside the grid")
            for r in range(cell.row, cell.row + cell.rowspan):
                for c in range(cell.col, cell.col + cell.colspan):
                    if (r, c) in occupied:
                        raise ValueError(f"overlapping cells at ({r}, {c})")
                    occupied.add((r, c))
        return self

    @property
    def has_spans(self) -> bool:
        return any(cell.rowspan > 1 or cell.colspan > 1 for cell in self.cells)

    @property
    def needs_html(self) -> bool:
        """GFM has no spans and exactly one header row; anything else renders as HTML (guide §4.5)."""
        return self.has_spans or self.header_rows > 1

    def to_gfm(self) -> str:
        """GFM pipe table. GFM requires a header row, so a grid without one uses its first row."""
        if self.needs_html:
            raise ValueError("table has spans or several header rows; render it with to_html()")
        if self.n_rows == 0 or self.n_cols == 0:
            return ""
        rows = [
            "| " + " | ".join(_escape_gfm(cell.content if cell else "") for cell in row) + " |"
            for row in self.slot_matrix()
        ]
        delimiter = "| " + " | ".join(["---"] * self.n_cols) + " |"
        return "\n".join([rows[0], delimiter, *rows[1:]])

    def to_html(self) -> str:
        """HTML table keeping spans; header rows use ``<th>``, uncovered positions an empty ``<td>``.

        Like ``to_gfm``, a grid without marked header rows renders its first row as the header.
        """
        matrix = self.slot_matrix()
        header_rows = self.header_rows or (1 if self.n_rows > 1 else 0)
        lines = ["<table>"]
        for r, row in enumerate(matrix):
            parts: list[str] = []
            for c, cell in enumerate(row):
                if cell is None:
                    parts.append("<td></td>")
                    continue
                if (cell.row, cell.col) != (r, c):
                    continue  # covered by a span
                tag = "th" if cell.is_header or r < header_rows else "td"
                attrs = (f' rowspan="{cell.rowspan}"' if cell.rowspan > 1 else "") + (
                    f' colspan="{cell.colspan}"' if cell.colspan > 1 else ""
                )
                parts.append(f"<{tag}{attrs}>{_escape_html(cell.content)}</{tag}>")
            lines.append("<tr>" + "".join(parts) + "</tr>")
        lines.append("</table>")
        return "\n".join(lines)

    def slot(self, row: int, col: int) -> Cell | None:
        """The cell covering position (row, col), or None if nothing covers it."""
        for cell in self.cells:
            if cell.row <= row < cell.row + cell.rowspan and cell.col <= col < cell.col + cell.colspan:
                return cell
        return None

    def slot_matrix(self) -> list[list[Cell | None]]:
        """n_rows × n_cols matrix of covering cells (spans repeat the same Cell)."""
        matrix: list[list[Cell | None]] = [[None] * self.n_cols for _ in range(self.n_rows)]
        for cell in self.cells:
            for r in range(cell.row, cell.row + cell.rowspan):
                for c in range(cell.col, cell.col + cell.colspan):
                    matrix[r][c] = cell
        return matrix

    @classmethod
    def from_html(cls, html: str) -> "TableGrid":
        """Parse the first ``<table>`` in *html*. Raises ValueError on malformed spans."""
        try:
            grid = _build_table_grid(_collect_rows(_get_table(html), line_join="\n"))  # in-cell line breaks kept
        except _TableConversionError as exc:
            raise ValueError(str(exc)) from exc
        cells = [
            Cell(
                row=r,
                col=c,
                rowspan=slot.row_to - slot.row_from + 1,
                colspan=slot.col_to - slot.col_from + 1,
                content=slot.text,
                is_header=slot.is_header,
            )
            for r, row in enumerate(grid)
            for c, slot in enumerate(row)
            if slot is not None and slot.is_origin
        ]
        n_cols = max((len(row) for row in grid), default=0)
        return cls(n_rows=len(grid), n_cols=n_cols, cells=cells, header_rows=_detect_header_block(grid))

    @classmethod
    def from_gfm(cls, lines: list[str]) -> "TableGrid":
        """Parse GFM pipe-table lines; a delimiter row after the first line marks it as header."""
        header_rows = 1 if len(lines) >= 2 and _is_delimiter_row(lines[1]) else 0
        rows = [_split_gfm_row(line) for line in lines if not _is_delimiter_row(line)]
        n_cols = max((len(row) for row in rows), default=0)
        cells = [
            Cell(row=r, col=c, content=row[c] if c < len(row) else "", is_header=r < header_rows)
            for r, row in enumerate(rows)
            for c in range(n_cols)
        ]
        return cls(n_rows=len(rows), n_cols=n_cols, cells=cells, header_rows=header_rows)


# ── Locating tables in Markdown ─────────────────────────────────────────


@dataclass(frozen=True)
class TableSpan:
    start: int  # character offsets into the Markdown text
    end: int
    fmt: str  # "gfm" | "html"
    grid: TableGrid | None  # None when the HTML could not be converted (malformed spans)


_DELIMITER_CELL_RE = re.compile(r"^\s*:?-+:?\s*$")
_HTML_TABLE_TAG_RE = re.compile(r"<table\b[^>]*>|</table\s*>", re.IGNORECASE)
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)


def _escape_gfm(text: str) -> str:
    return escape_strikethrough(text).replace("|", "\\|").replace("\n", "<br>")


def _escape_html(text: str) -> str:
    return html_escape(text, quote=False).replace("\n", "<br>")


def _split_gfm_row(line: str) -> list[str]:
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|") and not body.endswith("\\|"):
        body = body[:-1]
    cells = re.split(r"(?<!\\)\|", body)
    return [_BR_RE.sub("\n", cell.replace("\\|", "|").replace("\\~", "~")).strip() for cell in cells]


def _is_delimiter_row(line: str) -> bool:
    cells = _split_gfm_row(line)
    return bool(cells) and all(_DELIMITER_CELL_RE.match(cell) for cell in cells)


def _is_pipe_row(line: str) -> bool:
    stripped = line.strip()
    return len(stripped) >= 2 and stripped.startswith("|") and stripped.endswith("|")


def find_tables(markdown: str) -> list[TableSpan]:
    """Locate GFM pipe tables (two or more consecutive pipe rows) and HTML tables, in order."""
    spans = _find_html_tables(markdown)
    html_ranges = [(s.start, s.end) for s in spans]

    offset = 0
    in_fence = False
    block: list[tuple[int, str]] = []

    def flush() -> None:
        if len(block) >= 2:
            start = block[0][0]
            end = block[-1][0] + len(block[-1][1].rstrip("\n"))
            spans.append(TableSpan(start, end, "gfm", TableGrid.from_gfm([line for _, line in block])))
        block.clear()

    for line in markdown.splitlines(keepends=True):
        line_start = offset
        offset += len(line)
        if _FENCE_RE.match(line):
            flush()
            in_fence = not in_fence
            continue
        inside_html = any(start <= line_start < end for start, end in html_ranges)
        if not in_fence and not inside_html and _is_pipe_row(line):
            block.append((line_start, line))
        else:
            flush()
    flush()

    return sorted(spans, key=lambda s: s.start)


def _find_html_tables(markdown: str) -> list[TableSpan]:
    spans: list[TableSpan] = []
    depth = 0
    start = 0
    for match in _HTML_TABLE_TAG_RE.finditer(markdown):
        if not match.group(0).startswith("</"):
            if depth == 0:
                start = match.start()
            depth += 1
        elif depth > 0:
            depth -= 1
            if depth == 0:
                html = markdown[start:match.end()]
                try:
                    grid: TableGrid | None = TableGrid.from_html(html)
                except ValueError:
                    grid = None
                spans.append(TableSpan(start, match.end(), "html", grid))
    return spans
