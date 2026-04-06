"""OCRBuilder — selective OCR for pages/regions that need it.

Key insight from LiteParse: don't OCR everything. Only OCR:
1. Scanned pages (no native text)
2. Mixed pages (some native text, some image regions)
3. Pages with vector-rendered text (native extraction blank but page has content)

This reduces OCR calls by 60-70% compared to legacy pipeline's approach of
OCRing every extracted image.
"""

from __future__ import annotations

import logging
import re
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable

import fitz  # PyMuPDF

from parserx.config.schema import OCRBuilderConfig
from parserx.models.elements import Document, FontInfo, Page, PageElement, PageType
from parserx.services.ocr import OCRBlock, OCRResult, create_ocr_service

log = logging.getLogger(__name__)

# ── Layout label → element type mapping ────────────────────────────────

# PaddleOCR layout labels that indicate headings
_HEADING_LABELS = {"doc_title", "paragraph_title", "title"}

# PaddleOCR layout labels to skip (noise in output)
_SKIP_LABELS = {"header", "footer", "number", "header_image", "aside_text"}
_SENTENCE_ENDING_RE = re.compile(r"[。！？!?.;；]$")
_ASCII_CHEMISTRY_RE = re.compile(r"^[A-Za-z0-9,\-\[\]()]+$")


def _normalize_dedup(text: str) -> str:
    """Collapse whitespace and strip for dedup comparison."""
    return re.sub(r"\s+", "", text)


def _looks_like_ocr_heading(text: str, label: str) -> bool:
    """Best-effort filter for OCR layout labels that over-predict headings."""
    first_line = text.splitlines()[0].strip()
    if not first_line:
        return False
    if len(first_line) > 120:
        return False
    if _SENTENCE_ENDING_RE.search(first_line):
        return False

    compact = re.sub(r"\s+", "", first_line)
    punctuation_hits = sum(compact.count(ch) for ch in ",-[]()")
    if (
        label == "paragraph_title"
        and len(compact) >= 24
        and " " not in first_line
        and any(ch.isdigit() for ch in compact)
        and punctuation_hits >= 6
        and _ASCII_CHEMISTRY_RE.fullmatch(compact)
    ):
        return False

    return True


def _char_overlap_ratio(ocr_text: str, native_bag: Counter) -> float:
    """Fraction of *ocr_text* characters that appear in *native_bag*.

    Uses character-frequency overlap: for each unique char in ocr_text,
    the matched count is min(ocr_count, native_count).  This handles
    repeated characters correctly without positional alignment.
    """
    if not ocr_text:
        return 0.0
    ocr_bag = Counter(ocr_text)
    matched = sum((ocr_bag & native_bag).values())
    return matched / sum(ocr_bag.values())


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


def _escape_md_table_text(text: str) -> str:
    return (text or "").replace("|", "\\|").replace("\n", "<br>")


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


def _extract_cell_text(node: _HTMLNode) -> str:
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
    return " / ".join(lines) if lines else ""


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
) -> list[_TableCell]:
    cells: list[_TableCell] = []
    for child in tr.children:
        if child.tag not in _CELL_TAGS:
            continue
        cells.append(_TableCell(
            text=_extract_cell_text(child),
            is_header=child.tag == "th",
            rowspan=_safe_int(child.attrs.get("rowspan"), 1),
            colspan=_safe_int(child.attrs.get("colspan"), 1),
            section=section,
            row_index=row_index,
            source_tag=child.tag,
            scope=(child.attrs.get("scope", "") or "").strip().lower(),
        ))
    return cells


def _collect_rows(table: _HTMLNode) -> list[tuple[str, list[_TableCell]]]:
    rows: list[tuple[str, list[_TableCell]]] = []
    direct = [child for child in table.children if child.tag in _SECTION_TAGS or child.tag == "tr"]
    if not direct:
        direct = table.children
    for child in direct:
        if child.tag == "tr":
            rows.append(("tbody", _parse_row(child, "tbody", len(rows))))
        elif child.tag in _SECTION_TAGS:
            for tr in [grandchild for grandchild in child.children if grandchild.tag == "tr"]:
                rows.append((child.tag, _parse_row(tr, child.tag, len(rows))))
    if not rows:
        for tr in table.descendants("tr"):
            section = _infer_table_section(tr)
            rows.append((section, _parse_row(tr, section, len(rows))))
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


def _flatten_header_paths(
    grid: list[list[_GridSlot | None]],
    header_rows: int,
) -> list[str]:
    width = max((len(row) for row in grid), default=0)
    if header_rows <= 0:
        return ["      " for _ in range(width)]
    paths: list[str] = []
    for col in range(width):
        parts: list[str] = []
        last_key = None
        for row in range(header_rows):
            slot = grid[row][col]
            if slot is None or not slot.text.strip():
                continue
            if slot.col_span_cont:
                continue
            key = (slot.row_from, slot.col_from, slot.text)
            if key == last_key:
                continue
            last_key = key
            if not parts or parts[-1] != slot.text:
                parts.append(slot.text)
        paths.append(" > ".join(parts) if parts else "      ")
    return paths


def _render_markdown_table(
    grid: list[list[_GridSlot | None]],
    header_paths: list[str],
    header_rows: int,
) -> str:
    width = len(header_paths)
    lines = [
        "| " + " | ".join(_escape_md_table_text(header) for header in header_paths) + " |",
        "| " + " | ".join(["---"] * width) + " |",
    ]
    for row_idx in range(header_rows, len(grid)):
        row = grid[row_idx]
        if not any(slot is not None for slot in row):
            continue
        values: list[str] = []
        for col in range(width):
            slot = row[col]
            if slot is None or not slot.is_origin:
                values.append("")
            else:
                values.append(slot.text)
        if all(not value for value in values):
            continue
        lines.append("| " + " | ".join(_escape_md_table_text(value) for value in values) + " |")
    return "\n".join(lines)


def html_table_to_markdown(html: str) -> str:
    """Convert an HTML <table> string to Markdown table format."""
    cleaned = re.sub(r"</?li>|</?i>", "", html)
    try:
        table = _get_table(cleaned)
        rows = _collect_rows(table)
        grid = _build_table_grid(rows)
        header_rows = _detect_header_block(grid)
        if header_rows == 0 and len(grid) >= 1:
            header_rows = 1
        header_paths = _flatten_header_paths(grid, header_rows)
        return _render_markdown_table(grid, header_paths, header_rows)
    except _TableConversionError:
        return html
    except Exception:
        return html


class OCRBuilder:
    """Selectively OCR pages that need it and merge results into the document.

    Decision logic per page:
    - NATIVE with sufficient text → skip OCR
    - SCANNED → full page OCR
    - MIXED → OCR image regions only
    - force_full_page config → OCR everything (debug)
    """

    def __init__(self, config: OCRBuilderConfig | None = None):
        self._config = config or OCRBuilderConfig()
        self._ocr = create_ocr_service(self._config)  # None when engine="none"

    def build(self, doc: Document, source_path: Path) -> Document:
        """Run selective OCR and add results to document."""
        if self._ocr is None:
            return doc
        if not self._config.selective and not self._config.force_full_page:
            return doc

        ocr_count = 0
        skip_count = 0
        dedup_count = 0

        fitz_doc = fitz.open(str(source_path))

        for page in doc.pages:
            if self._should_ocr_page(page):
                log.debug("OCR page %d (%s)", page.number, page.page_type.value)
                ocr_elements = self._ocr_page(fitz_doc, page)
                if ocr_elements:
                    if page.page_type == PageType.SCANNED:
                        # Scanned: no native text, use OCR directly
                        page.elements.extend(ocr_elements)
                        self._mark_fullpage_scan_images(page)
                    else:
                        # Mixed / sparse native: deduplicate against existing
                        new, dropped = self._deduplicate(
                            page.elements, ocr_elements,
                        )
                        page.elements.extend(new)
                        dedup_count += dropped
                    ocr_count += 1
            else:
                skip_count += 1

        fitz_doc.close()

        log.info(
            "OCR: %d pages processed, %d skipped, %d OCR blocks deduplicated",
            ocr_count, skip_count, dedup_count,
        )
        return doc

    @staticmethod
    def _mark_fullpage_scan_images(page: Page) -> int:
        """Mark scan-source images as skipped on SCANNED pages.

        After OCR extracts text from a scanned page, the original scan
        images are no longer independent content — they ARE the page
        that OCR already processed.

        On a SCANNED page, images may be the raw scan source or inset
        content that OCR's layout analysis classified as "figure".  We
        skip an image only when OCR text/table elements **within** its
        bbox already represent its content.  Images whose interior is
        not covered by OCR (e.g. inset diagrams, icon grids, tables
        that OCR classified as figures) are kept for VLM processing.
        """
        ocr_elems = [
            e for e in page.elements
            if e.type in {"text", "table"} and e.source == "ocr"
            and e.content.strip()
        ]
        if not ocr_elems:
            return 0

        image_elems = [
            e for e in page.elements
            if e.type == "image" and not e.metadata.get("skipped")
        ]
        if not image_elems:
            return 0

        page_area = max(page.width * page.height, 1.0)
        marked = 0
        for img in image_elems:
            ix0, iy0, ix1, iy1 = img.bbox
            img_area = max((ix1 - ix0) * (iy1 - iy0), 0.0)
            if img_area <= 0:
                continue

            # Sum the content length of OCR elements whose bbox
            # overlaps with this image.
            overlap_chars = 0
            for ocr in ocr_elems:
                ox0, oy0, ox1, oy1 = ocr.bbox
                inter_x0, inter_y0 = max(ix0, ox0), max(iy0, oy0)
                inter_x1, inter_y1 = min(ix1, ox1), min(iy1, oy1)
                if inter_x1 > inter_x0 and inter_y1 > inter_y0:
                    overlap_chars += len(ocr.content)

            # Image whose content is well-covered by OCR text is
            # redundant.  A full-page scan (> 50% page area) with
            # substantial overlapping OCR, or any image with heavy
            # OCR overlap relative to its size.
            if overlap_chars >= 40 or (
                img_area / page_area > 0.5 and overlap_chars >= 20
            ):
                img.metadata["skipped"] = True
                img.metadata["skip_reason"] = "scan_image_covered_by_ocr"
                marked += 1

        if marked:
            log.debug(
                "Marked %d scan-source image(s) as skipped on page %d",
                marked, page.number,
            )
        return marked

    def _should_ocr_page(self, page: Page) -> bool:
        """Decide if a page needs OCR."""
        if self._config.force_full_page:
            return True

        if page.page_type == PageType.SCANNED:
            return True

        if page.page_type == PageType.MIXED:
            return True

        # Native page — skip unless text is suspiciously sparse
        text_chars = sum(
            len(e.content) for e in page.elements if e.type == "text"
        )
        if text_chars < 20 and page.width > 0 and page.height > 0:
            # Almost no text on a non-empty page — likely vector-rendered text
            return True

        return False

    def _ocr_page(self, fitz_doc: fitz.Document, page: Page) -> list[PageElement]:
        """Render page to image and OCR it."""
        if page.number < 1 or page.number > len(fitz_doc):
            return []

        fitz_page = fitz_doc[page.number - 1]

        # Render page at 150 DPI for OCR
        mat = fitz.Matrix(150 / 72, 150 / 72)
        pix = fitz_page.get_pixmap(matrix=mat)

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            pix.save(tmp.name)
            tmp_path = Path(tmp.name)

        try:
            result = self._ocr.recognize(tmp_path)
        finally:
            tmp_path.unlink(missing_ok=True)

        return self._result_to_elements(result, page.number)

    # ── Deduplication ───────────────────────────────────────────────────

    _DEDUP_THRESHOLD = 0.6  # Drop OCR block if ≥60% of chars already in native

    def _deduplicate(
        self,
        native_elements: list[PageElement],
        ocr_elements: list[PageElement],
    ) -> tuple[list[PageElement], int]:
        """Filter OCR elements that duplicate existing native text.

        For each OCR block, check what fraction of its characters are
        already covered by native text on the same page.  If the overlap
        exceeds ``_DEDUP_THRESHOLD``, the block is redundant and dropped.

        Returns (kept_elements, dropped_count).
        """
        native_text = _normalize_dedup(
            " ".join(e.content for e in native_elements if e.content)
        )
        if not native_text:
            # No native text at all — keep everything from OCR
            return ocr_elements, 0

        native_bag = Counter(native_text)

        kept: list[PageElement] = []
        dropped = 0

        for elem in ocr_elements:
            ocr_norm = _normalize_dedup(elem.content)
            if not ocr_norm:
                continue

            overlap = _char_overlap_ratio(ocr_norm, native_bag)
            if overlap >= self._DEDUP_THRESHOLD:
                log.debug(
                    "Dedup drop (%.0f%% overlap): %.40s…",
                    overlap * 100, elem.content,
                )
                dropped += 1
            else:
                kept.append(elem)

        return kept, dropped

    def _result_to_elements(self, result: OCRResult, page_number: int) -> list[PageElement]:
        """Convert OCR result blocks to PageElements.

        Uses PaddleOCR layout labels to infer element types:
        - doc_title / paragraph_title / title → text with heading_level
        - table → table (HTML content converted to Markdown)
        - header / footer / number → skipped
        """
        elements: list[PageElement] = []

        for block in result.blocks:
            if not block.text.strip():
                continue

            label = block.label or ""

            # Skip noise elements
            if label in _SKIP_LABELS:
                continue

            metadata: dict = {}

            if label == "table":
                # Convert HTML table → Markdown table
                content = block.text
                if content.strip().startswith("<table"):
                    md_table = html_table_to_markdown(content)
                    if md_table:
                        content = md_table
                elements.append(PageElement(
                    type="table",
                    content=content,
                    bbox=block.bbox,
                    page_number=page_number,
                    font=FontInfo(),
                    source="ocr",
                    confidence=block.confidence,
                    layout_type=label,
                ))
            elif label in _HEADING_LABELS:
                # Map to heading — doc_title → H1, paragraph_title → H2
                level = 1 if label == "doc_title" else 2
                if _looks_like_ocr_heading(block.text, label):
                    metadata["heading_level"] = level
                elements.append(PageElement(
                    type="text",
                    content=block.text,
                    bbox=block.bbox,
                    page_number=page_number,
                    font=FontInfo(),
                    source="ocr",
                    confidence=block.confidence,
                    layout_type=label,
                    metadata=metadata,
                ))
            else:
                elements.append(PageElement(
                    type="text",
                    content=block.text,
                    bbox=block.bbox,
                    page_number=page_number,
                    font=FontInfo(),
                    source="ocr",
                    confidence=block.confidence,
                    layout_type=label or None,
                ))

        return elements
