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

from parserx.config.schema import HeaderFooterConfig, MetadataBuilderConfig
from parserx.models.elements import Document, Page, PageElement

log = logging.getLogger(__name__)

# Number of elements from each edge to inspect
_INSPECT_COUNT = 3
# Page number patterns
_PAGE_NUMBER_RE = re.compile(r"^[\s\-—]*\d{1,5}[\s\-—]*$")
_ROMAN_NUMBER_RE = re.compile(r"^[\s\-—]*[ivxlcdm]+[\s\-—]*$", re.IGNORECASE)

# Section-opener page header: "<section name> N/M 页" — a widespread CN
# convention across patents, legal docs, standards, technical specs. The
# name preceding the page counter is the section title; we capture it
# generically instead of maintaining a keyword whitelist.
_SECTION_PAGE_HEADER_RE = re.compile(
    r"^\s*(?P<name>.+?)\s+\d{1,4}\s*/\s*\d{1,4}\s*页\s*$"
)


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


def _is_bottom_edge_text(elem: PageElement, page: Page, count: int = _INSPECT_COUNT) -> bool:
    """Check whether *elem* is one of the bottom-most text-like elements on the page."""
    text_elements = [
        candidate for candidate in page.elements
        if candidate.type in ("text", "header", "footer")
    ]
    if not text_elements:
        return False
    ordered = sorted(
        text_elements,
        key=lambda candidate: ((candidate.bbox[1] + candidate.bbox[3]) / 2, candidate.bbox[3]),
        reverse=True,
    )
    return elem in ordered[:count]


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
        config: HeaderFooterConfig | None = None,
        metadata_config: MetadataBuilderConfig | None = None,
    ):
        self._config = config or HeaderFooterConfig()
        self._meta_config = metadata_config or MetadataBuilderConfig()

    def process(self, doc: Document) -> Document:
        if not self._config.enabled:
            return doc

        header_zone = self._meta_config.header_zone_ratio
        footer_zone = self._meta_config.footer_zone_ratio
        threshold = self._meta_config.repetition_threshold

        # Step 0: Promote section-opener page headers (e.g. "说明书 1/6 页")
        # before the frequency loop — these span only part of the doc and
        # would fall below the repetition threshold otherwise.
        self._promote_section_page_headers(doc, header_zone)

        # Step 1: Collect edge elements from each page
        page_count = len(doc.pages)
        if page_count < 2:
            return doc  # Can't detect repetition with < 2 pages

        # Count how many pages have each normalized text in top/bottom zones
        top_counter: Counter[str] = Counter()
        bottom_counter: Counter[str] = Counter()

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
        min_count = max(2, int(page_count * threshold) + 1)
        repeated_top = {text for text, count in top_counter.items() if count >= min_count}
        repeated_bottom = {text for text, count in bottom_counter.items() if count >= min_count}

        if not repeated_top and not repeated_bottom:
            log.debug("No repeated header/footer patterns found; checking standalone page numbers only")

        if repeated_top or repeated_bottom:
            log.info(
                "Detected %d header pattern(s), %d footer pattern(s)",
                len(repeated_top), len(repeated_bottom),
            )

        # Step 3: Identify the first page number for first-page identity retention
        first_page_num = doc.pages[0].number if doc.pages else 1

        # Step 4: Remove matching elements (retain first-page identity)
        max_retain = getattr(self._config, "max_retained_identity", 2)
        removed_count = 0
        retained_count = 0
        for page in doc.pages:
            kept: list[PageElement] = []
            if page.number == first_page_num:
                # Collect first-page candidates, then rank and limit.
                candidates: list[PageElement] = []
                for elem in page.elements:
                    if self._should_remove(elem, page, repeated_top, repeated_bottom,
                                           header_zone, footer_zone):
                        if _is_page_number(elem.content):
                            removed_count += 1
                        else:
                            candidates.append(elem)
                    else:
                        kept.append(elem)

                # Rank candidates by information density (longer text first).
                candidates.sort(key=lambda e: len(e.content.strip()), reverse=True)
                for idx, elem in enumerate(candidates):
                    if idx < max_retain:
                        elem.metadata["retained_page_identity"] = True
                        elem.metadata["exclude_from_heading_detection"] = True
                        kept.append(elem)
                        retained_count += 1
                    else:
                        removed_count += 1
            else:
                for elem in page.elements:
                    if self._should_remove(elem, page, repeated_top, repeated_bottom,
                                           header_zone, footer_zone):
                        removed_count += 1
                    else:
                        kept.append(elem)
            page.elements = kept

        log.info(
            "Removed %d header/footer elements, retained %d on first page",
            removed_count, retained_count,
        )
        return doc

    def _promote_section_page_headers(self, doc: Document, header_ratio: float) -> None:
        """Promote section-opener page headers to `# heading`, strip the rest.

        Elements matching `<name> N/M 页` in the top-of-page zone mark the
        running header for a section (e.g. 权利要求书 / 说明书 / 附图 in
        patents, 第X章 in standards). The first occurrence of each distinct
        name becomes an H1 heading containing only the name; later
        occurrences are removed as noise.

        Purely structural — no keyword list. Generalizes to any document
        using the `N/M 页` page-counter convention.
        """
        seen_names: set[str] = set()
        page_counter_re = re.compile(r"^\d+\s*/\s*\d+\s*页$")
        for page in doc.pages:
            if page.height <= 0:
                continue
            header_y = page.height * header_ratio
            new_elements: list[PageElement] = []
            for elem in page.elements:
                if elem.type != "text":
                    new_elements.append(elem)
                    continue
                # Only consider elements near the top of the page. Use a
                # relaxed zone (2× header ratio) because Layout-only OCR can
                # merge the top band with adjacent body text and report a
                # bbox lower than the native line origin.
                if elem.bbox[1] >= page.height * max(header_ratio * 2, 0.15):
                    new_elements.append(elem)
                    continue

                raw_lines = elem.content.split("\n")
                norm_lines = [
                    re.sub(r"\s+", " ", line.replace("\u3000", " ")).strip()
                    for line in raw_lines
                ]
                name: str | None = None
                body_start = 0
                # Case A: first line already contains "<name> N/M 页".
                if norm_lines:
                    m = _SECTION_PAGE_HEADER_RE.match(norm_lines[0])
                    if m:
                        name = m.group("name").strip()
                        body_start = 1
                # Case B: first line is the bare name, second line is "N/M 页"
                # (OCR rejoined sibling elements with a newline between them).
                if name is None and len(norm_lines) >= 2:
                    if (
                        norm_lines[0]
                        and page_counter_re.match(norm_lines[1])
                        and len(norm_lines[0]) <= 20
                    ):
                        name = norm_lines[0]
                        body_start = 2
                if name is None or not name or len(name) > 40:
                    new_elements.append(elem)
                    continue

                body_text = "\n".join(raw_lines[body_start:])
                first_occurrence = name not in seen_names
                if first_occurrence:
                    seen_names.add(name)
                    heading_elem = PageElement(
                        type="text",
                        content=name,
                        bbox=(
                            elem.bbox[0], elem.bbox[1],
                            elem.bbox[2], elem.bbox[1],
                        ),
                        page_number=elem.page_number,
                        font=elem.font,
                        source=elem.source,
                        metadata={
                            "heading_level": 1,
                            "section_page_header_origin": True,
                        },
                    )
                    new_elements.append(heading_elem)
                if body_text.strip():
                    elem.content = body_text
                    new_elements.append(elem)
            page.elements = new_elements

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

        # PDFs often place page numbers slightly above the configured footer zone.
        # If a standalone page number is one of the bottom-most text elements,
        # treat it as footer noise even when geometry is imperfect.
        if _is_page_number(elem.content) and _is_bottom_edge_text(elem, page):
            return True

        return False
