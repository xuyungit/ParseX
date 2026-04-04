"""Header/footer detection and removal processor.

Uses geometric position + cross-page repetition to detect headers and footers.
This is the deterministic approach (no LLM) that handles 90%+ of documents.

Migrated and adapted from legacy pipeline remove_headers_footers.py Phase 1.
The key insight: headers/footers appear at the same vertical position on
most pages. We detect them by finding text elements in the top/bottom
zones that repeat across >50% of pages.
"""

from __future__ import annotations

import logging
import re
from collections import Counter

from parserx.config.schema import MetadataBuilderConfig, ProcessorToggle
from parserx.models.elements import Document, Page, PageElement

log = logging.getLogger(__name__)

# Number of elements from each edge to inspect
_INSPECT_COUNT = 3
# Page number patterns
_PAGE_NUMBER_RE = re.compile(r"^[\s\-—]*\d{1,5}[\s\-—]*$")
_ROMAN_NUMBER_RE = re.compile(r"^[\s\-—]*[ivxlcdm]+[\s\-—]*$", re.IGNORECASE)


def _is_page_number(text: str) -> bool:
    """Check if text looks like a page number."""
    stripped = text.strip()
    if _PAGE_NUMBER_RE.match(stripped):
        return True
    if _ROMAN_NUMBER_RE.match(stripped):
        return True
    # Patterns like "- 3 -" or "第 3 页"
    if re.match(r"^[-–—]\s*\d+\s*[-–—]$", stripped):
        return True
    if re.match(r"^第\s*\d+\s*页$", stripped):
        return True
    return False


def _normalize_for_comparison(text: str) -> str:
    """Normalize text for cross-page comparison.

    Strips whitespace and removes page-specific numbers so that
    "Page 1" and "Page 2" are treated as the same header.
    """
    text = text.strip()
    # Replace sequences of digits with a placeholder
    text = re.sub(r"\d+", "#", text)
    return text


class HeaderFooterProcessor:
    """Remove headers, footers, and page numbers.

    Strategy: deterministic first (geometric + frequency), LLM fallback only
    when confidence is low and config allows it.

    Phase 1 (this implementation):
    - Identify top/bottom zone of each page using configurable ratios
    - Find text elements in these zones
    - Check cross-page repetition: if >50% of pages have the same
      (normalized) text at the same position → it's a header/footer
    - Also detect standalone page numbers
    """

    def __init__(
        self,
        config: ProcessorToggle | None = None,
        metadata_config: MetadataBuilderConfig | None = None,
    ):
        self._config = config or ProcessorToggle()
        self._meta_config = metadata_config or MetadataBuilderConfig()

    def process(self, doc: Document) -> Document:
        if not self._config.enabled:
            return doc

        header_zone = self._meta_config.header_zone_ratio
        footer_zone = self._meta_config.footer_zone_ratio
        threshold = self._meta_config.repetition_threshold

        # Step 1: Collect edge elements from each page
        page_count = len(doc.pages)
        if page_count < 2:
            return doc  # Can't detect repetition with < 2 pages

        # Count how many pages have each normalized text in top/bottom zones
        top_counter: Counter[str, int] = Counter()
        bottom_counter: Counter[str, int] = Counter()

        for page in doc.pages:
            if not page.elements:
                continue
            top_texts, bottom_texts = self._get_edge_texts(page, header_zone, footer_zone)
            # Count unique texts per page (not per occurrence)
            for text in set(top_texts):
                top_counter[text] += 1
            for text in set(bottom_texts):
                bottom_counter[text] += 1

        # Step 2: Find repeated patterns
        min_count = max(2, int(page_count * threshold))
        repeated_top = {text for text, count in top_counter.items() if count >= min_count}
        repeated_bottom = {text for text, count in bottom_counter.items() if count >= min_count}

        if not repeated_top and not repeated_bottom:
            log.debug("No repeated header/footer patterns found")
            return doc

        log.info(
            "Detected %d header pattern(s), %d footer pattern(s)",
            len(repeated_top), len(repeated_bottom),
        )

        # Step 3: Remove matching elements
        removed_count = 0
        for page in doc.pages:
            original_count = len(page.elements)
            page.elements = [
                elem for elem in page.elements
                if not self._should_remove(elem, page, repeated_top, repeated_bottom,
                                           header_zone, footer_zone)
            ]
            removed_count += original_count - len(page.elements)

        log.info("Removed %d header/footer elements", removed_count)
        return doc

    def _get_edge_texts(
        self, page: Page, header_ratio: float, footer_ratio: float
    ) -> tuple[list[str], list[str]]:
        """Get normalized text of elements in header/footer zones."""
        top_texts: list[str] = []
        bottom_texts: list[str] = []

        if page.height == 0:
            return top_texts, bottom_texts

        header_y = page.height * header_ratio
        footer_y = page.height * (1 - footer_ratio)

        for elem in page.elements:
            if elem.type not in ("text", "header", "footer"):
                continue
            # Element's vertical center
            elem_y_center = (elem.bbox[1] + elem.bbox[3]) / 2

            if elem_y_center < header_y:
                top_texts.append(_normalize_for_comparison(elem.content))
            elif elem_y_center > footer_y:
                bottom_texts.append(_normalize_for_comparison(elem.content))

        return top_texts, bottom_texts

    def _should_remove(
        self,
        elem: PageElement,
        page: Page,
        repeated_top: set[str],
        repeated_bottom: set[str],
        header_ratio: float,
        footer_ratio: float,
    ) -> bool:
        """Decide if an element should be removed as header/footer."""
        if elem.type not in ("text", "header", "footer"):
            return False
        if page.height == 0:
            return False

        header_y = page.height * header_ratio
        footer_y = page.height * (1 - footer_ratio)
        elem_y_center = (elem.bbox[1] + elem.bbox[3]) / 2
        normalized = _normalize_for_comparison(elem.content)

        # Check if in header zone and matches repeated pattern
        if elem_y_center < header_y and normalized in repeated_top:
            return True

        # Check if in footer zone and matches repeated pattern
        if elem_y_center > footer_y and normalized in repeated_bottom:
            return True

        # Also remove standalone page numbers in footer zone
        if elem_y_center > footer_y and _is_page_number(elem.content):
            return True

        return False
