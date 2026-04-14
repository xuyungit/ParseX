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

# PaddleOCR layout labels that indicate figures/images
_FIGURE_LABELS = {"image", "figure"}
_SENTENCE_ENDING_RE = re.compile(r"[。！？!?.;；]$")
_ASCII_CHEMISTRY_RE = re.compile(r"^[A-Za-z0-9,\-\[\]()]+$")


def _normalize_dedup(text: str) -> str:
    """Collapse whitespace and strip for dedup comparison."""
    return re.sub(r"\s+", "", text)


def _count_table_columns(md_table: str) -> int:
    """Count columns in a Markdown table by inspecting the first data row."""
    for line in md_table.splitlines():
        stripped = line.strip()
        if stripped.startswith("|") and not re.fullmatch(r"[|\s\-:]+", stripped):
            return stripped.count("|") - 1
    return 1


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


def _pad_bbox(
    bbox: tuple[float, float, float, float],
    ratio: float = 0.10,
) -> tuple[float, float, float, float]:
    """Expand a bbox by *ratio* on each side."""
    x0, y0, x1, y1 = bbox
    pw = (x1 - x0) * ratio
    ph = (y1 - y0) * ratio
    return (x0 - pw, y0 - ph, x1 + pw, y1 + ph)


# Pattern matching figure/table captions like "Figure 1:", "Table 2:",
# "Fig. 3:", "图 4:" etc.  These should never be suppressed.
_CAPTION_RE = re.compile(
    r"^(?:Figure|Fig\.|Table|Tab\.|图|表)\s*\d+",
    re.IGNORECASE,
)


def _is_caption_text(content: str) -> bool:
    """Return True if *content* looks like a figure/table caption."""
    return bool(_CAPTION_RE.match(content.strip()))


def _suppress_text_inside_figures(elements: list[PageElement]) -> None:
    """Mark OCR text elements as skip_render if inside a figure bbox.

    When PaddleOCR detects a figure region, it often *also* returns text
    blocks for labels, axis ticks, and legends within that region.  These
    should not appear as body text in the final output.

    A 10% padding is added to figure bboxes because OCR layout detection
    often returns slightly tight bounding boxes that miss edge labels.
    """
    figures = [e for e in elements if e.metadata.get("vector_figure")]
    if not figures:
        return

    for elem in elements:
        if elem.type != "text" or elem.metadata.get("skip_render"):
            continue
        # Preserve figure/table captions (e.g. "Figure 1: ...")
        if _is_caption_text(elem.content):
            continue
        # Check if element center is inside any figure bbox (with padding)
        cx = (elem.bbox[0] + elem.bbox[2]) / 2
        cy = (elem.bbox[1] + elem.bbox[3]) / 2
        for fig in figures:
            fx0, fy0, fx1, fy1 = _pad_bbox(fig.bbox)
            if fx0 <= cx <= fx1 and fy0 <= cy <= fy1:
                elem.metadata["skip_render"] = True
                elem.metadata["suppressed_by_vector_figure"] = True
                break


def _attach_figure_captions(elements: list[PageElement]) -> None:
    """Attach ``figure_title`` OCR elements as captions on vector figures.

    PaddleOCR layout detection returns ``figure_title`` labels for
    captions like "Figure 5: Gradients computed for graph in Figure 2".
    We find the nearest vector figure element (by vertical distance)
    and store the caption text in its metadata.  The caption element
    itself is marked ``skip_render`` to avoid double output — the
    caption will be rendered by the crossref/renderer via the figure's
    ``caption`` metadata.
    """
    figures = [e for e in elements if e.metadata.get("vector_figure")]
    captions = [e for e in elements if e.layout_type == "figure_title"]
    if not figures or not captions:
        return

    for cap in captions:
        cap_top = cap.bbox[1]
        cap_cx = (cap.bbox[0] + cap.bbox[2]) / 2
        best_fig = None
        best_dist = float("inf")
        for fig in figures:
            fig_bottom = fig.bbox[3]
            fig_cx = (fig.bbox[0] + fig.bbox[2]) / 2
            # Caption typically appears below the figure.  Prefer
            # figures whose bottom edge is above the caption top.
            if fig_bottom <= cap_top + 20:  # small tolerance
                vert_dist = cap_top - fig_bottom
            else:
                # Figure below caption — penalise heavily
                vert_dist = abs(cap_top - fig_bottom) + 500
            dist = vert_dist + abs(cap_cx - fig_cx) * 0.3
            if dist < best_dist:
                best_dist = dist
                best_fig = fig
        if best_fig is not None:
            best_fig.metadata["ocr_caption"] = cap.content.strip()
            cap.metadata["skip_render"] = True
            cap.metadata["suppressed_by_vector_figure"] = True


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

    def __init__(
        self,
        config: OCRBuilderConfig | None = None,
        *,
        skip_scan_image_marking: bool = False,
    ):
        self._config = config or OCRBuilderConfig()
        self._ocr = create_ocr_service(self._config)  # None when engine="none"
        self._skip_scan_image_marking = skip_scan_image_marking

    def build(self, doc: Document, source_path: Path) -> Document:
        """Run selective OCR and add results to document.

        When multiple pages need OCR, uses batch mode: extracts the
        relevant pages from the original PDF into a temporary PDF and
        sends it in a single API call.  Falls back to per-page mode
        if batch fails.
        """
        if self._ocr is None:
            return doc
        if not self._config.selective and not self._config.force_full_page:
            return doc

        fitz_doc = fitz.open(str(source_path))

        # Pre-scan: detect NATIVE pages with vector drawings so they
        # get sent to OCR for layout-based figure detection.
        if self._config.vector_figure_extraction:
            self._detect_vector_drawing_pages(fitz_doc, doc)

        pages_to_ocr = [p for p in doc.pages if self._should_ocr_page(p)]
        total_ocr = len(pages_to_ocr)
        skip_count = len(doc.pages) - total_ocr

        if total_ocr == 0:
            # Even with no content-OCR pages, we may still need layout-only
            # OCR for multi-column native pages (Iter 23).
            if self._config.use_layout_reading_order:
                layout_pages = [p for p in doc.pages if self._is_layout_ambiguous(p)]
                if layout_pages:
                    log.info("Layout-only OCR: %d native pages", len(layout_pages))
                    self._apply_layout_reading_order_batch(fitz_doc, layout_pages)
            fitz_doc.close()
            return doc

        # Try batch mode for multiple pages.
        batch_results: dict[int, list[PageElement]] | None = None
        if total_ocr > 1 and self._config.batch and hasattr(self._ocr, "recognize_pdf"):
            batch_results = self._ocr_batch(fitz_doc, pages_to_ocr)

        # Apply results (batch or per-page fallback).
        ocr_count = 0
        dedup_count = 0

        for page in pages_to_ocr:
            log.info(
                "OCR page %d/%d (page %d, %s)",
                ocr_count + 1, total_ocr, page.number, page.page_type.value,
            )

            # Get OCR elements from batch or per-page.
            if batch_results is not None and page.number in batch_results:
                ocr_elements = batch_results[page.number]
            else:
                try:
                    ocr_elements = self._ocr_page(fitz_doc, page)
                except Exception as exc:
                    log.error(
                        "OCR failed on page %d, skipping: %s", page.number, exc,
                    )
                    ocr_count += 1
                    continue

            if ocr_elements:
                dedup_count += self._integrate_ocr_results(page, ocr_elements)
            ocr_count += 1

        # Layout-only OCR pass: run OCR on NATIVE pages with multi-column
        # layout to get reading-order regions from the layout model.
        # Content stays native (PyMuPDF font info preserved from Iter 21/22);
        # OCR's block order drives element sort.
        if self._config.use_layout_reading_order:
            already_ocrd = {p.number for p in pages_to_ocr}
            layout_pages = [
                p for p in doc.pages
                if p.number not in already_ocrd and self._is_layout_ambiguous(p)
            ]
            if layout_pages:
                log.info("Layout-only OCR: %d native pages", len(layout_pages))
                applied = self._apply_layout_reading_order_batch(fitz_doc, layout_pages)
                log.info("Layout reading-order applied to %d pages", applied)

        fitz_doc.close()

        log.info(
            "OCR: %d pages processed, %d skipped, %d OCR blocks deduplicated",
            ocr_count, skip_count, dedup_count,
        )
        return doc

    def _is_layout_ambiguous(self, page: Page) -> bool:
        """Detect NATIVE pages that likely have multi-column layout.

        Uses x-midpoint distribution of text elements: when at least 3
        elements cluster left-of-center and 3 right-of-center, we assume
        multi-column and worth calling the layout engine. Pure single-column
        pages, very sparse pages, and tiny pages are skipped.
        """
        from parserx.models.elements import PageType
        if page.page_type != PageType.NATIVE:
            return False
        if page.width <= 0 or page.height <= 0:
            return False
        text_elems = [
            e for e in page.elements
            if e.type == "text" and e.bbox != (0.0, 0.0, 0.0, 0.0)
        ]
        if len(text_elems) < 6:
            return False
        mid_page = page.width / 2
        left = right = 0
        for e in text_elems:
            w = e.bbox[2] - e.bbox[0]
            # Skip full-width elements (headlines, page banners)
            if w > page.width * 0.55:
                continue
            m = (e.bbox[0] + e.bbox[2]) / 2
            if m < mid_page:
                left += 1
            else:
                right += 1
        return left >= 3 and right >= 3

    def _apply_layout_reading_order_batch(
        self,
        fitz_doc: fitz.Document,
        pages: list[Page],
    ) -> int:
        """Run OCR on *pages* to get layout regions, apply reading order.

        Content remains native; we only consume OCR block (bbox, order)
        to assign each native element a reading_order and resort.
        Returns the number of pages where ordering was applied.
        """
        # Build temp PDF with just these pages
        temp_pdf = fitz.open()
        page_map: list[int] = []
        for page in pages:
            src_idx = page.number - 1
            if 0 <= src_idx < len(fitz_doc):
                temp_pdf.insert_pdf(fitz_doc, from_page=src_idx, to_page=src_idx)
                page_map.append(page.number)
        if not page_map:
            temp_pdf.close()
            return 0

        pdf_bytes = temp_pdf.tobytes()
        temp_pdf.close()

        try:
            results = self._ocr.recognize_pdf(pdf_bytes)
        except Exception as exc:
            log.warning("Layout-only OCR failed: %s", exc)
            return 0
        if len(results) != len(page_map):
            log.warning(
                "Layout OCR returned %d pages, expected %d", len(results), len(page_map)
            )
            return 0

        page_by_num = {p.number: p for p in pages}
        applied = 0
        for i, ocr_result in enumerate(results):
            page_number = page_map[i]
            page = page_by_num.get(page_number)
            if page is None:
                continue
            src_idx = page_number - 1
            fitz_page = fitz_doc[src_idx] if 0 <= src_idx < len(fitz_doc) else None
            if self._apply_layout_reading_order(page, ocr_result, fitz_page):
                applied += 1
        return applied

    def _apply_layout_reading_order(
        self,
        page: Page,
        ocr_result: OCRResult,
        fitz_page: "fitz.Page | None" = None,
    ) -> bool:
        """Rebuild page.elements per OCR region, using native PDF text.

        For each non-skipped OCR region, re-extract the PyMuPDF rawdict
        inside that region's bbox clip. This yields one PageElement per
        region, with font flags and per-span formatting preserved from
        Iter 21/22. Reading order follows the OCR layout engine's
        ``block_order`` — the whole point of this path.

        Non-text elements (images, tables, previously-extracted elements
        that weren't text) are preserved at the end; their reading_order
        is inherited from the nearest OCR region if possible.
        """
        if fitz_page is None:
            return False
        if (
            page.width <= 0 or page.height <= 0
            or ocr_result.render_width <= 0 or ocr_result.render_height <= 0
        ):
            return False
        scale_x = page.width / ocr_result.render_width
        scale_y = page.height / ocr_result.render_height

        regions: list[tuple[int, float, float, float, float, str]] = []
        for blk in ocr_result.blocks:
            if blk.label in _SKIP_LABELS:
                continue
            rx0 = blk.bbox[0] * scale_x
            ry0 = blk.bbox[1] * scale_y
            rx1 = blk.bbox[2] * scale_x
            ry1 = blk.bbox[3] * scale_y
            order = blk.order if blk.order is not None else 10_000
            regions.append((order, rx0, ry0, rx1, ry1, blk.label or ""))
        if len(regions) < 2:
            return False

        # Import lazily to avoid circular dependency on the provider side.
        from parserx.providers.pdf import (
            _merge_line_segments,
            _reconstruct_line_segments,
        )
        from parserx.models.elements import FontInfo
        import fitz

        # Keep non-text elements (images, tables, etc.) as-is. Text elements
        # get replaced from per-region clip extraction.
        preserved_non_text = [
            e for e in page.elements if e.type != "text"
        ]
        new_text_elements: list[PageElement] = []

        # Line-level regrouping: flatten all PyMuPDF lines once, assign
        # each line to an OCR region by center-in-region. This avoids the
        # clip-truncation that happens when a native line physically
        # extends past an OCR region boundary.
        try:
            full_dict = fitz_page.get_text(
                "rawdict", flags=fitz.TEXT_PRESERVE_WHITESPACE,
            )
        except Exception:
            return False

        # Group: region_order → list of (line_bbox, line_spans)
        region_lines: dict[int, list[tuple[tuple, list]]] = {}
        for blk in full_dict.get("blocks", []):
            if blk.get("type") != 0:
                continue
            for line in blk.get("lines", []):
                lbbox = line.get("bbox", (0, 0, 0, 0))
                if lbbox == (0, 0, 0, 0):
                    continue
                lcx = (lbbox[0] + lbbox[2]) / 2
                lcy = (lbbox[1] + lbbox[3]) / 2
                # Find region containing center; else nearest by center
                chosen_order = None
                for order, rx0, ry0, rx1, ry1, _lab in regions:
                    if rx0 <= lcx <= rx1 and ry0 <= lcy <= ry1:
                        chosen_order = order
                        break
                if chosen_order is None:
                    best_order = regions[0][0]
                    best_d2 = float("inf")
                    for order, rx0, ry0, rx1, ry1, _lab in regions:
                        rcx = (rx0 + rx1) / 2
                        rcy = (ry0 + ry1) / 2
                        d2 = (rcx - lcx) ** 2 + (rcy - lcy) ** 2
                        if d2 < best_d2:
                            best_d2 = d2
                            best_order = order
                    chosen_order = best_order
                region_lines.setdefault(chosen_order, []).append((lbbox, line.get("spans", [])))

        # Build one element per region from its assigned lines
        label_by_order = {order: lab for order, *_, lab in regions}
        region_bbox_by_order: dict[int, tuple[float, float, float, float]] = {}
        for order, rx0, ry0, rx1, ry1, _lab in regions:
            region_bbox_by_order[order] = (rx0, ry0, rx1, ry1)

        for order, lines in sorted(region_lines.items(), key=lambda kv: kv[0]):
            if False:  # placeholder to keep diff compact
                pass
            # Sort lines by y then x so intra-region reads top-down
            lines.sort(key=lambda t: (t[0][1], t[0][0]))
            line_entries: list[tuple[str, tuple]] = []
            line_segment_lists: list[list[dict]] = []
            dominant_font = FontInfo()
            max_font_chars = 0
            for lbbox, spans in lines:
                segments = _reconstruct_line_segments(spans)
                text = "".join(seg["text"] for seg in segments)
                if not text.strip():
                    continue
                line_entries.append((text, lbbox))
                line_segment_lists.append(segments)
                for span in spans:
                    char_count = len(span.get("chars", []))
                    if char_count > max_font_chars:
                        max_font_chars = char_count
                        flags = span.get("flags", 0)
                        dominant_font = FontInfo(
                            name=span.get("font", ""),
                            size=round(span.get("size", 0.0), 1),
                            bold=bool(flags & 2**4),
                            italic=bool(flags & 2**1),
                        )
            if not line_entries:
                continue
            content = "".join(
                (joiner + text)
                for i, (text, _) in enumerate(line_entries)
                for joiner in ([""] if i == 0 else ["\n"])
            )
            block_segments = _merge_line_segments(line_entries, line_segment_lists)
            metadata: dict = {"reading_order": order}
            lab = label_by_order.get(order, "")
            if lab:
                metadata["ocr_layout_label"] = lab
            has_mixed = len({
                (s["bold"], s["italic"], s.get("underline", False), s.get("sup", False))
                for s in block_segments
            }) > 1
            if has_mixed:
                metadata["inline_spans"] = block_segments
            new_text_elements.append(PageElement(
                type="text",
                content=content,
                bbox=region_bbox_by_order.get(order, (0.0, 0.0, 0.0, 0.0)),
                page_number=page.number,
                font=dominant_font,
                source="native",
                metadata=metadata,
            ))

        if not new_text_elements:
            return False

        # Assign reading_order to preserved non-text elements by nearest
        # region center. Tables/images after text at end of page.
        for elem in preserved_non_text:
            if elem.bbox == (0.0, 0.0, 0.0, 0.0):
                continue
            cx = (elem.bbox[0] + elem.bbox[2]) / 2
            cy = (elem.bbox[1] + elem.bbox[3]) / 2
            best = regions[0][0]
            best_d2 = float("inf")
            for order, x0, y0, x1, y1, _ in regions:
                rcx = (x0 + x1) / 2
                rcy = (y0 + y1) / 2
                d2 = (rcx - cx) ** 2 + (rcy - cy) ** 2
                if d2 < best_d2:
                    best_d2 = d2
                    best = order
            elem.metadata["reading_order"] = best

        merged = new_text_elements + preserved_non_text
        merged.sort(
            key=lambda e: (
                e.metadata.get("reading_order", 10_000),
                e.bbox[1],
                e.bbox[0],
            )
        )
        page.elements = merged
        return True

    # PaddleOCR sync API rejects PDFs with more than 100 pages.
    _BATCH_MAX_PAGES = 100

    def _ocr_batch(
        self,
        fitz_doc: fitz.Document,
        pages: list[Page],
    ) -> dict[int, list[PageElement]] | None:
        """Extract pages into temp PDF(s) and OCR in batch.

        If more than ``_BATCH_MAX_PAGES`` pages need OCR, they are
        split into chunks and each chunk is sent separately.

        Returns a dict mapping page.number → OCR elements, or None if
        batch OCR fails (caller will fall back to per-page).
        """
        result_map: dict[int, list[PageElement]] = {}

        for chunk_start in range(0, len(pages), self._BATCH_MAX_PAGES):
            chunk = pages[chunk_start : chunk_start + self._BATCH_MAX_PAGES]
            chunk_results = self._ocr_batch_chunk(fitz_doc, chunk)
            if chunk_results is None:
                # If any chunk fails, fall back to per-page for all
                # remaining pages.
                return result_map if result_map else None
            result_map.update(chunk_results)

        return result_map

    def _ocr_batch_chunk(
        self,
        fitz_doc: fitz.Document,
        pages: list[Page],
    ) -> dict[int, list[PageElement]] | None:
        """OCR a single chunk of pages (≤ _BATCH_MAX_PAGES).

        Returns a dict mapping page.number → OCR elements, or None on failure.
        """
        # Build a temporary PDF with only the pages that need OCR.
        temp_pdf = fitz.open()
        page_map: list[int] = []  # temp_pdf page index → original page number
        for page in pages:
            src_idx = page.number - 1  # fitz uses 0-based
            if 0 <= src_idx < len(fitz_doc):
                temp_pdf.insert_pdf(fitz_doc, from_page=src_idx, to_page=src_idx)
                page_map.append(page.number)

        if not page_map:
            temp_pdf.close()
            return None

        pdf_bytes = temp_pdf.tobytes()
        temp_pdf.close()
        log.info(
            "Batch OCR: assembled %d pages (%.1f KB)",
            len(page_map), len(pdf_bytes) / 1024,
        )

        try:
            results = self._ocr.recognize_pdf(pdf_bytes)
        except Exception as exc:
            log.warning("Batch OCR failed, falling back to per-page: %s", exc)
            return None

        if len(results) != len(page_map):
            log.warning(
                "Batch OCR returned %d pages, expected %d — falling back to per-page",
                len(results), len(page_map),
            )
            return None

        # Map results back to original page numbers.
        page_dims = {p.number: (p.width, p.height) for p in pages}
        chunk_map: dict[int, list[PageElement]] = {}
        for i, ocr_result in enumerate(results):
            page_number = page_map[i]
            pw, ph = page_dims.get(page_number, (0.0, 0.0))
            chunk_map[page_number] = self._result_to_elements(
                ocr_result, page_number,
                page_width=pw, page_height=ph,
            )

        return chunk_map

    def _integrate_ocr_results(
        self,
        page: Page,
        ocr_elements: list[PageElement],
    ) -> int:
        """Integrate OCR elements into a page. Returns dedup drop count."""
        if page.page_type == PageType.SCANNED:
            # Scanned page: replace any pre-existing text/table
            # elements with fresh OCR results.
            existing_text = [
                e for e in page.elements
                if e.type in {"text", "table"} and e.source == "native"
            ]
            if existing_text:
                page.elements = [
                    e for e in page.elements
                    if not (e.type in {"text", "table"} and e.source == "native")
                ]
                log.debug(
                    "Replaced %d native text/table elements with OCR on scanned page %d",
                    len(existing_text), page.number,
                )
            page.elements.extend(ocr_elements)
            if not self._skip_scan_image_marking:
                self._mark_fullpage_scan_images(page)
            return 0
        elif page.metadata.get("has_vector_drawings"):
            # NATIVE page with drawings: keep only figure image
            # elements from OCR — discard OCR text/table to avoid
            # introducing duplicates or garbled text.
            figure_elems = [
                e for e in ocr_elements
                if e.type == "image" and e.metadata.get("vector_figure")
            ]
            page.elements.extend(figure_elems)
            self._suppress_native_text_in_figures(page)
            return 0
        else:
            # Mixed / sparse native: deduplicate against existing.
            new, dropped = self._deduplicate(
                page.elements, ocr_elements,
            )
            page.elements.extend(new)

            # Suppress native text elements that fall inside vector
            # figure regions detected by OCR layout analysis.
            self._suppress_native_text_in_figures(page)

            return dropped

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

    @staticmethod
    def _suppress_native_text_in_figures(page: Page) -> int:
        """Suppress native text elements inside figure/image regions.

        Two cases handled:

        1. **Vector figures** (OCR-detected): axis labels, node labels,
           etc. that PDFProvider extracts as native text.
        2. **Embedded raster images**: some PDFs overlay native text
           (labels, annotations) on top of raster screenshots. Text
           whose center falls inside a large image bbox is suppressed.

        A 10% padding is added to figure bboxes to account for OCR
        layout detection returning slightly tight bounding boxes.
        """
        # Collect vector figures (padded).  Raster images are NOT
        # included: they often sit adjacent to body text or headings,
        # and a bbox-overlap test produces too many false suppressions.
        # Text inside raster screenshots is handled downstream by the
        # renderer's VLM correction skip for diagram/chart images.
        suppress_regions: list[tuple[float, float, float, float]] = []
        for e in page.elements:
            if e.metadata.get("vector_figure"):
                suppress_regions.append(_pad_bbox(e.bbox))

        if not suppress_regions:
            return 0

        suppressed = 0
        for elem in page.elements:
            if elem.type != "text":
                continue
            if elem.metadata.get("skip_render"):
                continue
            # Preserve figure/table captions (e.g. "Figure 7: ...")
            if _is_caption_text(elem.content):
                continue
            cx = (elem.bbox[0] + elem.bbox[2]) / 2
            cy = (elem.bbox[1] + elem.bbox[3]) / 2
            for fx0, fy0, fx1, fy1 in suppress_regions:
                if fx0 <= cx <= fx1 and fy0 <= cy <= fy1:
                    elem.metadata["skip_render"] = True
                    elem.metadata["suppressed_by_vector_figure"] = True
                    suppressed += 1
                    break

        if suppressed:
            log.debug(
                "Suppressed %d native text element(s) inside figure/image regions on page %d",
                suppressed, page.number,
            )
        return suppressed

    def _detect_vector_drawing_pages(
        self, fitz_doc: fitz.Document, doc: Document,
    ) -> None:
        """Flag NATIVE pages that contain vector drawings.

        Pages with a significant number of drawing commands AND at
        least one curve (cubic bezier) likely contain vector figures.
        The curve requirement filters out pages that only have table
        borders or decorative straight lines — those consist entirely
        of line segments (``l``) with no curves (``c``).
        """
        min_drawings = self._config.vector_figure_min_drawings
        flagged = 0
        for page in doc.pages:
            if page.page_type != PageType.NATIVE:
                continue
            idx = page.number - 1
            if idx < 0 or idx >= len(fitz_doc):
                continue
            drawings = fitz_doc[idx].get_drawings()
            if len(drawings) < min_drawings:
                continue
            # Require at least one curve — table borders and straight
            # decorative lines have zero curves.
            has_curves = any(
                item[0] == "c"
                for d in drawings
                for item in d["items"]
            )
            if not has_curves:
                continue
            page.metadata["has_vector_drawings"] = True
            page.metadata["drawing_count"] = len(drawings)
            flagged += 1
        if flagged:
            log.info(
                "Vector drawing pre-scan: %d NATIVE page(s) flagged for OCR",
                flagged,
            )

    def _should_ocr_page(self, page: Page) -> bool:
        """Decide if a page needs OCR."""
        if self._config.force_full_page:
            return True

        if page.page_type == PageType.SCANNED:
            return True

        if page.page_type == PageType.MIXED:
            return True

        # NATIVE page with vector drawings — needs OCR for figure detection
        if page.metadata.get("has_vector_drawings"):
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

        return self._result_to_elements(
            result, page.number,
            page_width=page.width, page_height=page.height,
        )

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

        # Pre-collect native tables for table-to-table dedup.
        native_tables = [
            e for e in native_elements
            if e.type == "table" and e.content.strip()
        ]

        kept: list[PageElement] = []
        dropped = 0

        for elem in ocr_elements:
            # Table dedup: only compare against native table elements
            # to avoid dropping a well-structured OCR table when native
            # extraction scattered the same content across text blocks.
            if elem.type == "table":
                if not native_tables:
                    kept.append(elem)
                    continue
                table_norm = _normalize_dedup(elem.content)
                if not table_norm:
                    continue
                ocr_cols = _count_table_columns(elem.content)
                is_dup = False
                for nt in native_tables:
                    nt_norm = _normalize_dedup(nt.content)
                    if not nt_norm:
                        continue
                    ratio = _char_overlap_ratio(table_norm, Counter(nt_norm))
                    if ratio < 0.85:
                        continue
                    # High character overlap — but only treat as duplicate
                    # if both tables have similar structure.  When the OCR
                    # table has significantly more columns, it is likely a
                    # better-structured version of a broken native table
                    # (single-column text dump).  In that case, replace the
                    # native table with the OCR version instead of dropping.
                    native_cols = _count_table_columns(nt.content)
                    if ocr_cols >= native_cols + 2:
                        log.debug(
                            "Dedup replace native table (%d cols) with OCR table (%d cols, %.0f%% overlap)",
                            native_cols, ocr_cols, ratio * 100,
                        )
                        nt.metadata["skip_render"] = True
                        is_dup = False
                        break
                    log.debug(
                        "Dedup drop table (%.0f%% overlap, %d vs %d cols): %.40s…",
                        ratio * 100, ocr_cols, native_cols, elem.content,
                    )
                    is_dup = True
                    break
                if is_dup:
                    dropped += 1
                else:
                    kept.append(elem)
                continue

            # Non-text elements (e.g. vector figure images) always kept
            if elem.type != "text":
                kept.append(elem)
                continue
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

    def _result_to_elements(
        self,
        result: OCRResult,
        page_number: int,
        page_width: float = 0.0,
        page_height: float = 0.0,
    ) -> list[PageElement]:
        """Convert OCR result blocks to PageElements.

        Uses PaddleOCR layout labels to infer element types:
        - doc_title / paragraph_title / title → text with heading_level
        - table → table (HTML content converted to Markdown)
        - image → image with vector_figure metadata
        - header / footer / number → skipped

        When *page_width*/*page_height* (PDF points) and
        ``result.render_width``/``render_height`` (OCR pixels) are both
        available, all bboxes are scaled from OCR pixel space to PDF
        point space so they are consistent with native elements.
        """
        # ── Coordinate scale factors ────────────────────────────────
        scale_x = scale_y = 1.0
        if (
            page_width > 0
            and page_height > 0
            and result.render_width > 0
            and result.render_height > 0
        ):
            scale_x = page_width / result.render_width
            scale_y = page_height / result.render_height

        elements: list[PageElement] = []

        for block in result.blocks:
            label = block.label or ""

            # Scale bbox to PDF points
            bbox = (
                block.bbox[0] * scale_x,
                block.bbox[1] * scale_y,
                block.bbox[2] * scale_x,
                block.bbox[3] * scale_y,
            )

            # Skip noise elements
            if label in _SKIP_LABELS:
                continue

            # ── Figure / image blocks ───────────────────────────────
            if label in _FIGURE_LABELS:
                fig_w = bbox[2] - bbox[0]
                fig_h = bbox[3] - bbox[1]
                if fig_w < 30 or fig_h < 30:
                    continue  # too small — likely noise
                elements.append(PageElement(
                    type="image",
                    content="",
                    bbox=bbox,
                    page_number=page_number,
                    font=FontInfo(),
                    source="ocr",
                    confidence=block.confidence,
                    layout_type=label,
                    metadata={
                        "vector_figure": True,
                        "width": fig_w,
                        "height": fig_h,
                    },
                ))
                continue

            if not block.text.strip():
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
                    bbox=bbox,
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
                    bbox=bbox,
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
                    bbox=bbox,
                    page_number=page_number,
                    font=FontInfo(),
                    source="ocr",
                    confidence=block.confidence,
                    layout_type=label or None,
                ))

        # ── Suppress OCR text blocks inside figure regions ──────────
        _suppress_text_inside_figures(elements)

        # ── Attach figure_title captions to nearest vector figures ──
        _attach_figure_captions(elements)

        return elements
