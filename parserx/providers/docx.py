"""DOCX provider using Docling for OOXML native parsing."""

from __future__ import annotations

import logging
import re
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

# A paragraph whose entire text is digits/whitespace is never a real heading.
# Docling emits such paragraphs as ``SectionHeaderItem`` when Word assigns
# them an outline level (e.g. because they live in a footer that contains a
# PAGE field, or other auto-generated section markers). Filter them out so
# they do not leak into the body flow as ``#### 1 2`` noise.
_NUMERIC_ONLY_RE = re.compile(r"^[\s\d]+$")


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

        # Pre-pass: collect group refs that are children of tables.
        # Docling emits table cell content both inside the TableItem (as
        # structured grid data) AND as standalone TextItems whose parent
        # is a group owned by the table.  We must suppress these ghosts.
        table_cell_groups = self._collect_table_cell_groups(docling_doc)

        # Phase 1: convert all items, tracking parent group refs
        raw_items: list[tuple[int, PageElement, str]] = []  # (page, element, parent_ref)
        current_page = 1

        for item, _level in docling_doc.iterate_items(with_groups=False):
            # Skip text items that are ghost duplicates of table cell content
            parent = getattr(item, "parent", None)
            if (
                table_cell_groups
                and parent is not None
                and hasattr(parent, "cref")
                and parent.cref in table_cell_groups
            ):
                continue
            item_page = current_page
            if hasattr(item, "prov") and item.prov:
                item_page = item.prov[0].page_no
            if item_page > current_page:
                current_page = item_page

            element = self._convert_item(item, item_page, docling_doc)
            if element is not None:
                parent_ref = ""
                parent = getattr(item, "parent", None)
                if parent is not None:
                    ref_str = str(parent)
                    # Group refs look like "cref='#/groups/N'"
                    if "#/groups/" in ref_str:
                        parent_ref = ref_str
                raw_items.append((item_page, element, parent_ref))

        # Phase 2: merge consecutive elements sharing the same group parent
        merged = self._merge_group_fragments(raw_items)

        # Phase 3: paginate
        pages: list[Page] = []
        current_page_num = 0
        page_elements: list[PageElement] = []

        for page_num, element in merged:
            if page_num != current_page_num:
                if page_elements or pages:
                    pages.append(self._make_page(current_page_num, page_elements))
                # Fill gaps
                for gap in range(current_page_num + 1, page_num):
                    pages.append(self._make_page(gap, []))
                page_elements = []
                current_page_num = page_num
            page_elements.append(element)

        if page_elements or not pages:
            pages.append(self._make_page(current_page_num or 1, page_elements))

        metadata = DocumentMetadata(
            page_count=len(pages),
            source_format="docx",
            source_path=str(path),
        )

        doc = Document(pages=pages, metadata=metadata)
        # Cache the Docling document for downstream image extraction
        # (avoids re-parsing the DOCX in ImageExtractor.extract_docx).
        doc._cache["docling_doc"] = docling_doc
        return doc

    @staticmethod
    def _collect_table_cell_groups(docling_doc) -> set[str]:
        """Return self_ref strings of groups that are direct children of tables.

        Docling duplicates table cell content as standalone TextItems parented
        by these groups.  Callers use this set to suppress those ghosts.
        """
        table_cell_groups: set[str] = set()
        for item, _level in docling_doc.iterate_items(with_groups=True):
            type_name = type(item).__name__
            if type_name in ("GroupItem", "InlineGroup"):
                parent = getattr(item, "parent", None)
                if (
                    parent is not None
                    and hasattr(parent, "cref")
                    and "#/tables/" in parent.cref
                ):
                    table_cell_groups.add(item.self_ref)
        return table_cell_groups

    def _merge_group_fragments(
        self,
        raw_items: list[tuple[int, PageElement, str]],
    ) -> list[tuple[int, PageElement]]:
        """Merge consecutive text elements that share the same Docling group parent.

        Docling splits a single DOCX paragraph into multiple TextItems at
        formatting boundaries (e.g. underline on/off).  We rejoin them into
        one PageElement so the renderer produces a single paragraph.

        Each merged element records per-span formatting in metadata so the
        renderer can reconstruct inline markup (bold, italic, underline).
        """
        result: list[tuple[int, PageElement]] = []
        i = 0
        while i < len(raw_items):
            page, elem, parent_ref = raw_items[i]
            # Only merge text elements with a group parent.
            # List items are separate paragraphs even within the same group.
            if not parent_ref or elem.type != "text" or elem.metadata.get("docling_list_item"):
                result.append((page, elem))
                i += 1
                continue

            # Collect consecutive items with the same group parent
            # Each span: (text, font, underline)
            ul0 = bool(elem.metadata.get("underline"))
            spans: list[tuple[str, FontInfo, bool]] = [(elem.content, elem.font, ul0)]
            j = i + 1
            while j < len(raw_items):
                p2, e2, pr2 = raw_items[j]
                if pr2 != parent_ref or e2.type != "text" or e2.metadata.get("docling_list_item"):
                    break
                ul2 = bool(e2.metadata.get("underline"))
                spans.append((e2.content, e2.font, ul2))
                j += 1

            if len(spans) == 1:
                result.append((page, elem))
                i += 1
                continue

            # Build merged content and per-span formatting records
            merged_text = "".join(text for text, _, _ in spans)
            span_records: list[dict] = []
            for text, font, ul in spans:
                if text:  # skip empty spans
                    span_records.append({
                        "text": text,
                        "bold": font.bold,
                        "italic": font.italic,
                        "underline": ul,
                    })

            # Use the first element's font as the "dominant" font
            merged_elem = PageElement(
                type="text",
                content=merged_text,
                bbox=elem.bbox,
                page_number=page,
                font=elem.font,
                source="native",
                metadata={**elem.metadata, "inline_spans": span_records},
            )
            result.append((page, merged_elem))
            i = j

        return result

    def _convert_item(self, item, page_number: int, docling_doc) -> PageElement | None:
        """Convert a Docling item to a PageElement."""
        from docling_core.types.doc.document import (
            ListItem,
            PictureItem,
            SectionHeaderItem,
            TableItem,
            TextItem,
        )

        bbox = self._extract_bbox(item)

        if isinstance(item, SectionHeaderItem):
            text = item.text or ""
            if not text.strip() or _NUMERIC_ONLY_RE.match(text):
                # Spurious heading emitted by Docling for empty/numeric-only
                # paragraphs (e.g. page-number artefacts). Never a real heading.
                return None
            font = FontInfo(
                name="",
                size=0.0,
                bold=True,
            )
            return PageElement(
                type="text",
                content=text,
                bbox=bbox,
                page_number=page_number,
                font=font,
                source="native",
                metadata={"heading_level": item.level},
            )

        # ListItem must be checked BEFORE TextItem because ListItem
        # inherits from TextItem — isinstance(list_item, TextItem) is True.
        if isinstance(item, ListItem):
            font = FontInfo()
            underline = False
            if hasattr(item, "formatting") and item.formatting:
                font = FontInfo(
                    bold=item.formatting.bold,
                    italic=item.formatting.italic,
                )
                underline = bool(getattr(item.formatting, "underline", False))
            # Note: Docling's ListItem.marker is internal list ordering,
            # NOT the document's actual section numbering.  Do not prepend
            # it — the original numbering (if any) is typically already
            # embedded in item.text or handled by DOCX auto-numbering
            # which Docling does not expose.
            meta: dict = {"docling_list_item": True}
            if underline:
                meta["underline"] = True
            return PageElement(
                type="text",
                content=item.text,
                bbox=bbox,
                page_number=page_number,
                font=font,
                source="native",
                metadata=meta,
            )

        if isinstance(item, TextItem):
            font = FontInfo()
            underline = False
            if hasattr(item, "formatting") and item.formatting:
                font = FontInfo(
                    bold=item.formatting.bold,
                    italic=item.formatting.italic,
                )
                underline = bool(getattr(item.formatting, "underline", False))
            meta: dict = {}
            if underline:
                meta["underline"] = True
            return PageElement(
                type="text",
                content=item.text,
                bbox=bbox,
                page_number=page_number,
                font=font,
                source="native",
                metadata=meta,
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
