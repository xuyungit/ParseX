"""PDF provider using PyMuPDF for character-level text extraction."""

from __future__ import annotations

import logging
from pathlib import Path

import fitz  # PyMuPDF

from parserx.models.elements import (
    Document,
    DocumentMetadata,
    FontInfo,
    Page,
    PageElement,
    PageType,
)
from parserx.processors.text_clean import normalize_fullwidth_ascii

log = logging.getLogger(__name__)


class PDFProvider:
    """Extract text, tables, and images from PDF using PyMuPDF.

    Unlike the old legacy pipeline approach (PyMuPDF4LLM → Markdown string),
    this provider extracts character-level metadata (font name, size, bold)
    which enables rule-based heading detection in MetadataBuilder.
    """

    def extract(self, path: Path) -> Document:
        doc = fitz.open(str(path))
        pages: list[Page] = []

        for page_idx in range(len(doc)):
            fitz_page = doc[page_idx]
            page = self._extract_page(fitz_page, page_idx + 1)
            pages.append(page)

        doc.close()

        metadata = DocumentMetadata(
            page_count=len(pages),
            source_format="pdf",
            source_path=str(path),
        )

        return Document(pages=pages, metadata=metadata)

    def _extract_page(self, fitz_page: fitz.Page, page_number: int) -> Page:
        """Extract all elements from a single PDF page."""
        rect = fitz_page.rect
        page = Page(
            number=page_number,
            width=rect.width,
            height=rect.height,
        )

        # Extract text blocks with font metadata
        text_elements = self._extract_text_elements(fitz_page, page_number)

        # Extract tables
        table_elements = self._extract_tables(fitz_page, page_number)

        # Remove text blocks that overlap with table regions to avoid
        # duplicating table cell text as both prose and markdown table.
        text_elements = self._remove_table_overlapping_text(
            text_elements, table_elements
        )

        # Extract images
        image_elements = self._extract_images(fitz_page, page_number)

        # Merge all elements and sort by visual position (top-to-bottom,
        # left-to-right) so mid-page tables/images stay in reading order.
        all_elements = text_elements + table_elements + image_elements
        all_elements.sort(key=lambda e: (e.bbox[1], e.bbox[0]))
        page.elements.extend(all_elements)

        # Classify page type
        page.page_type = self._classify_page(fitz_page, text_elements, image_elements)

        return page

    def _extract_text_elements(
        self, fitz_page: fitz.Page, page_number: int
    ) -> list[PageElement]:
        """Extract text with character-level font metadata.

        Uses page.get_text("dict") to get font name, size, and flags
        for each text span. This is the key improvement over PyMuPDF4LLM
        which only returns a Markdown string without font metadata.
        """
        elements: list[PageElement] = []
        page_dict = fitz_page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)

        for block in page_dict.get("blocks", []):
            if block.get("type") != 0:  # type 0 = text block
                continue

            block_bbox = (
                block["bbox"][0],
                block["bbox"][1],
                block["bbox"][2],
                block["bbox"][3],
            )

            # Collect all lines in this block
            lines_text: list[str] = []
            dominant_font = FontInfo()
            max_font_chars = 0

            for line in block.get("lines", []):
                line_parts: list[str] = []
                for span in line.get("spans", []):
                    text = span.get("text", "")
                    if text:
                        line_parts.append(text)

                    # Track dominant font (by character count)
                    char_count = len(text)
                    if char_count > max_font_chars:
                        max_font_chars = char_count
                        flags = span.get("flags", 0)
                        dominant_font = FontInfo(
                            name=span.get("font", ""),
                            size=round(span.get("size", 0.0), 1),
                            bold=bool(flags & 2**4),  # bit 4 = bold
                            italic=bool(flags & 2**1),  # bit 1 = italic
                        )

                line_text = normalize_fullwidth_ascii("".join(line_parts))
                if line_text.strip():
                    lines_text.append(line_text)

            content = "\n".join(lines_text)
            if not content.strip():
                continue

            elements.append(
                PageElement(
                    type="text",
                    content=content,
                    bbox=block_bbox,
                    page_number=page_number,
                    font=dominant_font,
                    source="native",
                )
            )

        return elements

    def _extract_tables(
        self, fitz_page: fitz.Page, page_number: int
    ) -> list[PageElement]:
        """Extract tables using PyMuPDF's built-in table finder."""
        elements: list[PageElement] = []

        try:
            tables = fitz_page.find_tables()
        except Exception:
            return elements

        for table in tables:
            bbox = table.bbox
            # Convert table to markdown
            md_lines: list[str] = []
            extracted = table.extract()

            if not extracted:
                continue

            # Build markdown table
            for row_idx, row in enumerate(extracted):
                cells = [
                    normalize_fullwidth_ascii(str(cell).replace("\n", " ").strip())
                    if cell else ""
                    for cell in row
                ]
                md_lines.append("| " + " | ".join(cells) + " |")
                if row_idx == 0:
                    md_lines.append("|" + "|".join(["---"] * len(cells)) + "|")

            if md_lines:
                elements.append(
                    PageElement(
                        type="table",
                        content="\n".join(md_lines),
                        bbox=(bbox[0], bbox[1], bbox[2], bbox[3]),
                        page_number=page_number,
                        source="native",
                        metadata={"rows": len(extracted), "cols": len(extracted[0]) if extracted else 0},
                    )
                )

        return elements

    def _extract_images(
        self, fitz_page: fitz.Page, page_number: int
    ) -> list[PageElement]:
        """Extract image references from the page."""
        elements: list[PageElement] = []

        for img_info in fitz_page.get_image_info(xrefs=True):
            bbox = img_info.get("bbox")
            if not bbox:
                continue

            width = img_info.get("width", 0)
            height = img_info.get("height", 0)

            # Skip tiny images (likely decorative)
            if width < 10 or height < 10:
                continue

            elements.append(
                PageElement(
                    type="image",
                    content="",  # Image content extracted later if needed
                    bbox=(bbox[0], bbox[1], bbox[2], bbox[3]),
                    page_number=page_number,
                    source="native",
                    metadata={
                        "width": width,
                        "height": height,
                        "xref": img_info.get("xref", 0),
                    },
                )
            )

        return elements

    def _classify_page(
        self,
        fitz_page: fitz.Page,
        text_elements: list[PageElement],
        image_elements: list[PageElement],
    ) -> PageType:
        """Classify page as native, scanned, or mixed.

        Per-page classification (not per-document) addresses P15.

        Detects OCR-layered scanned PDFs by checking spatial
        relationships: if a dominant image covers most of the page
        and most text characters are located *inside* that image's
        bounding box, the page is a scan with an overlaid OCR text
        layer — not a genuine native PDF.
        """
        total_text_chars = sum(len(e.content) for e in text_elements)
        page_area = fitz_page.rect.width * fitz_page.rect.height

        if page_area == 0:
            return PageType.NATIVE

        # Check if large images cover most of the page (likely scanned)
        total_image_area = 0.0
        dominant_image: PageElement | None = None
        dominant_image_area = 0.0
        for img in image_elements:
            img_w = img.bbox[2] - img.bbox[0]
            img_h = img.bbox[3] - img.bbox[1]
            area = img_w * img_h
            total_image_area += area
            if area > dominant_image_area:
                dominant_image_area = area
                dominant_image = img

        image_coverage = total_image_area / page_area

        if total_text_chars < 50 and image_coverage > 0.5:
            return PageType.SCANNED

        # Detect OCR text layer on scanned pages: a dominant image
        # covers >50% of the page and >70% of text chars sit inside it.
        if (
            dominant_image is not None
            and dominant_image_area / page_area > 0.5
            and total_text_chars > 0
        ):
            chars_inside = self._count_chars_inside_bbox(
                text_elements, dominant_image.bbox,
            )
            if chars_inside / total_text_chars > 0.7:
                return PageType.SCANNED

        if total_text_chars < 200 and image_coverage > 0.3:
            return PageType.MIXED

        # Detect garbled text from fonts with missing encoding tables
        # (e.g. CFF Type1 fonts without ToUnicode CMap).  PyMuPDF returns
        # U+FFFD for unmappable characters.  A high ratio means the page
        # needs OCR to recover the lost text.
        #
        # Use SCANNED (not MIXED) so OCR fully replaces native text.
        # MIXED would only add missing text via dedup, keeping the garbled
        # native elements.  Since the page is vector-rendered, OCR on
        # the rasterized image should produce good results for all text.
        if total_text_chars > 0:
            replacement_chars = sum(
                e.content.count("\ufffd") for e in text_elements
            )
            if replacement_chars / total_text_chars > 0.05:
                return PageType.SCANNED

        return PageType.NATIVE

    @staticmethod
    def _count_chars_inside_bbox(
        text_elements: list[PageElement],
        bbox: tuple[float, float, float, float],
    ) -> int:
        """Count how many text characters are spatially inside a bbox."""
        bx0, by0, bx1, by1 = bbox
        inside = 0
        for elem in text_elements:
            # Use element bbox center to determine containment.
            ex0, ey0, ex1, ey1 = elem.bbox
            cx = (ex0 + ex1) / 2
            cy = (ey0 + ey1) / 2
            if bx0 <= cx <= bx1 and by0 <= cy <= by1:
                inside += len(elem.content)
        return inside

    # ------------------------------------------------------------------
    # Deduplication helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _bbox_overlap_ratio(
        inner: tuple[float, float, float, float],
        outer: tuple[float, float, float, float],
    ) -> float:
        """Return fraction of *inner* area that overlaps with *outer*."""
        x0 = max(inner[0], outer[0])
        y0 = max(inner[1], outer[1])
        x1 = min(inner[2], outer[2])
        y1 = min(inner[3], outer[3])

        if x1 <= x0 or y1 <= y0:
            return 0.0

        intersection = (x1 - x0) * (y1 - y0)
        inner_area = (inner[2] - inner[0]) * (inner[3] - inner[1])
        if inner_area <= 0:
            return 0.0
        return intersection / inner_area

    def _remove_table_overlapping_text(
        self,
        text_elements: list[PageElement],
        table_elements: list[PageElement],
    ) -> list[PageElement]:
        """Drop text blocks whose bbox is mostly inside a table region."""
        if not table_elements:
            return text_elements

        _OVERLAP_THRESHOLD = 0.5
        table_bboxes = [t.bbox for t in table_elements]
        kept: list[PageElement] = []

        for te in text_elements:
            overlaps = any(
                self._bbox_overlap_ratio(te.bbox, tb) >= _OVERLAP_THRESHOLD
                for tb in table_bboxes
            )
            if not overlaps:
                kept.append(te)

        dropped = len(text_elements) - len(kept)
        if dropped:
            log.debug("Dropped %d text blocks overlapping with tables", dropped)

        return kept
