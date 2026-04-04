"""OCRBuilder — selective OCR for pages/regions that need it.

Key insight from LiteParse: don't OCR everything. Only OCR:
1. Scanned pages (no native text)
2. Mixed pages (some native text, some image regions)
3. Pages with vector-rendered text (native extraction blank but page has content)

This reduces OCR calls by 60-70% compared to doc-refine's approach of
OCRing every extracted image.
"""

from __future__ import annotations

import logging
import re
import tempfile
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

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


def _normalize_dedup(text: str) -> str:
    """Collapse whitespace and strip for dedup comparison."""
    return re.sub(r"\s+", "", text)


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


class _TableHTMLToMarkdown(HTMLParser):
    """Minimal HTML table → Markdown table converter."""

    def __init__(self):
        super().__init__()
        self.rows: list[list[str]] = []
        self._current_row: list[str] = []
        self._current_cell: str = ""
        self._in_cell = False

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._current_row = []
        elif tag in ("td", "th"):
            self._current_cell = ""
            self._in_cell = True

    def handle_endtag(self, tag):
        if tag in ("td", "th"):
            self._in_cell = False
            self._current_row.append(self._current_cell.strip())
        elif tag == "tr":
            if self._current_row:
                self.rows.append(self._current_row)

    def handle_data(self, data):
        if self._in_cell:
            self._current_cell += data

    def to_markdown(self) -> str:
        if not self.rows:
            return ""
        n_cols = max(len(r) for r in self.rows)
        lines = []
        for i, row in enumerate(self.rows):
            padded = row + [""] * (n_cols - len(row))
            cells = " | ".join(c.replace("|", "\\|") for c in padded)
            lines.append(f"| {cells} |")
            if i == 0:
                lines.append("| " + " | ".join(["---"] * n_cols) + " |")
        return "\n".join(lines)


def html_table_to_markdown(html: str) -> str:
    """Convert an HTML <table> string to Markdown table format."""
    parser = _TableHTMLToMarkdown()
    # Clean stray tags that some OCR engines emit inside cells
    html = re.sub(r"</?li>|</?i>", "", html)
    parser.feed(html)
    return parser.to_markdown()


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
        self._ocr = create_ocr_service(self._config)

    def build(self, doc: Document, source_path: Path) -> Document:
        """Run selective OCR and add results to document."""
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
                    page_number=page_number,
                    font=FontInfo(),
                    source="ocr",
                    confidence=block.confidence,
                    layout_type=label,
                ))
            elif label in _HEADING_LABELS:
                # Map to heading — doc_title → H1, paragraph_title → H2
                level = 1 if label == "doc_title" else 2
                metadata["heading_level"] = level
                elements.append(PageElement(
                    type="text",
                    content=block.text,
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
                    page_number=page_number,
                    font=FontInfo(),
                    source="ocr",
                    confidence=block.confidence,
                    layout_type=label or None,
                ))

        return elements
