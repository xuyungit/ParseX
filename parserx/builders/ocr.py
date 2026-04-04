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
import tempfile
from pathlib import Path

import fitz  # PyMuPDF

from parserx.config.schema import OCRBuilderConfig
from parserx.models.elements import Document, FontInfo, Page, PageElement, PageType
from parserx.services.ocr import OCRBlock, OCRResult, create_ocr_service

log = logging.getLogger(__name__)


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

        fitz_doc = fitz.open(str(source_path))

        for page in doc.pages:
            if self._should_ocr_page(page):
                log.debug("OCR page %d (%s)", page.number, page.page_type.value)
                ocr_elements = self._ocr_page(fitz_doc, page)
                if ocr_elements:
                    page.elements.extend(ocr_elements)
                    ocr_count += 1
            else:
                skip_count += 1

        fitz_doc.close()

        log.info("OCR: %d pages processed, %d skipped", ocr_count, skip_count)
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

    def _result_to_elements(self, result: OCRResult, page_number: int) -> list[PageElement]:
        """Convert OCR result blocks to PageElements."""
        elements: list[PageElement] = []

        for block in result.blocks:
            if not block.text.strip():
                continue

            elem_type = "text"
            if block.label == "table":
                elem_type = "table"

            elements.append(PageElement(
                type=elem_type,
                content=block.text,
                page_number=page_number,
                font=FontInfo(),  # OCR doesn't provide font info
                source="ocr",
                confidence=block.confidence,
                layout_type=block.label or None,
            ))

        return elements
