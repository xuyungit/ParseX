"""DOCX provider using Docling for OOXML native parsing."""

from __future__ import annotations

import logging
from pathlib import Path

from parserx.models.elements import (
    Document,
    DocumentMetadata,
    FontInfo,
    Page,
    PageElement,
    PageType,
)

log = logging.getLogger(__name__)


class DOCXProvider:
    """Extract text, tables, and images from DOCX using Docling.

    Docling parses OOXML natively, preserving heading styles, table structure
    (including merged cells), and embedded images. OOXML heading styles are
    deterministic — no font-based heuristics needed.
    """

    def extract(self, path: Path) -> Document:
        from docling.document_converter import DocumentConverter

        converter = DocumentConverter()
        result = converter.convert(str(path))
        docling_doc = result.document

        pages: list[Page] = []
        current_page = 1
        page_elements: list[PageElement] = []

        for item, _level in docling_doc.iterate_items(with_groups=False):
            # Determine page number from provenance
            item_page = current_page
            if hasattr(item, "prov") and item.prov:
                item_page = item.prov[0].page_no

            # Start a new page if needed
            if item_page > current_page:
                pages.append(self._make_page(current_page, page_elements))
                # Fill gaps (empty pages between)
                for gap_page in range(current_page + 1, item_page):
                    pages.append(self._make_page(gap_page, []))
                page_elements = []
                current_page = item_page

            element = self._convert_item(item, item_page, docling_doc)
            if element is not None:
                page_elements.append(element)

        # Flush last page
        if page_elements or not pages:
            pages.append(self._make_page(current_page, page_elements))

        metadata = DocumentMetadata(
            page_count=len(pages),
            source_format="docx",
            source_path=str(path),
        )

        return Document(pages=pages, metadata=metadata)

    def _convert_item(self, item, page_number: int, docling_doc) -> PageElement | None:
        """Convert a Docling item to a PageElement."""
        from docling_core.types.doc.document import (
            PictureItem,
            SectionHeaderItem,
            TableItem,
            TextItem,
        )

        bbox = self._extract_bbox(item)

        if isinstance(item, SectionHeaderItem):
            font = FontInfo(
                name="",
                size=0.0,
                bold=True,
            )
            return PageElement(
                type="text",
                content=item.text,
                bbox=bbox,
                page_number=page_number,
                font=font,
                source="native",
                metadata={"heading_level": item.level},
            )

        if isinstance(item, TextItem):
            font = FontInfo()
            if hasattr(item, "formatting") and item.formatting:
                font = FontInfo(
                    bold=item.formatting.bold,
                    italic=item.formatting.italic,
                )
            return PageElement(
                type="text",
                content=item.text,
                bbox=bbox,
                page_number=page_number,
                font=font,
                source="native",
            )

        if isinstance(item, TableItem):
            md = self._table_to_markdown(item)
            if md:
                rows = item.data.num_rows if item.data else 0
                cols = item.data.num_cols if item.data else 0
                return PageElement(
                    type="table",
                    content=md,
                    bbox=bbox,
                    page_number=page_number,
                    source="native",
                    metadata={"rows": rows, "cols": cols},
                )

        if isinstance(item, PictureItem):
            width = 0
            height = 0
            if item.image and item.image.size:
                width = int(item.image.size.width)
                height = int(item.image.size.height)
            return PageElement(
                type="image",
                content="",
                bbox=bbox,
                page_number=page_number,
                source="native",
                metadata={
                    "docling_picture": True,
                    "width": width,
                    "height": height,
                    "docling_self_ref": item.self_ref,
                },
            )

        return None

    def _table_to_markdown(self, table_item) -> str:
        """Convert a Docling TableItem to Markdown table format."""
        if not table_item.data:
            return ""

        grid = table_item.data.grid
        if not grid:
            return ""

        lines: list[str] = []
        for row_idx, row in enumerate(grid):
            cells = [
                (cell.text if cell else "").replace("\n", " ").strip()
                for cell in row
            ]
            lines.append("| " + " | ".join(cells) + " |")
            if row_idx == 0:
                lines.append("|" + "|".join(["---"] * len(cells)) + "|")

        return "\n".join(lines)

    def _extract_bbox(self, item) -> tuple[float, float, float, float]:
        """Extract bounding box from item provenance."""
        if hasattr(item, "prov") and item.prov:
            prov = item.prov[0]
            if hasattr(prov, "bbox") and prov.bbox:
                bb = prov.bbox
                return (bb.l, bb.t, bb.r, bb.b)
        return (0.0, 0.0, 0.0, 0.0)

    def _make_page(self, number: int, elements: list[PageElement]) -> Page:
        return Page(
            number=number,
            page_type=PageType.NATIVE,
            elements=elements,
        )
