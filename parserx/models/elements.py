"""Core data models for ParserX pipeline."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


class PageType(str, Enum):
    """Classification of a page's content origin."""

    NATIVE = "native"  # Text extractable from PDF structure
    SCANNED = "scanned"  # Image-only, needs OCR
    MIXED = "mixed"  # Some native text, some image regions


class FontInfo(BaseModel):
    """Font characteristics for a text span."""

    name: str = ""
    size: float = 0.0
    bold: bool = False
    italic: bool = False


class PageElement(BaseModel):
    """A single element extracted from a document page.

    This is the universal intermediate representation that flows through
    the entire pipeline. Providers create these; Builders annotate them;
    Processors transform them; Assembly renders them.
    """

    type: Literal["text", "table", "image", "formula", "header", "footer"] = "text"
    content: str = ""
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)  # (x0, y0, x1, y1)
    page_number: int = 1
    font: FontInfo = Field(default_factory=FontInfo)
    metadata: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 1.0
    source: Literal["native", "ocr", "vlm"] = "native"
    layout_type: str | None = None  # Set by LayoutBuilder
    children: list[PageElement] = Field(default_factory=list)  # For tables: cell elements


class Page(BaseModel):
    """A single page in the document."""

    number: int
    width: float = 0.0
    height: float = 0.0
    page_type: PageType = PageType.NATIVE
    elements: list[PageElement] = Field(default_factory=list)
    image_path: Path | None = None  # Rendered page image (for OCR/VLM)


class NumberingPattern(BaseModel):
    """A detected numbering pattern in the document."""

    signal: str  # e.g. "chapter_cn", "section_arabic_nested"
    level: str  # "H1", "H2", "H3"
    count: int = 0  # How many headings match this pattern
    regex: str = ""  # The regex that matched


class FontStatistics(BaseModel):
    """Document-level font statistics for rule-based heading detection."""

    body_font: FontInfo = Field(default_factory=FontInfo)
    heading_candidates: list[FontInfo] = Field(default_factory=list)
    font_counts: dict[str, int] = Field(default_factory=dict)  # "fontname_size_bold" → count


class DocumentMetadata(BaseModel):
    """Document-level metadata extracted by MetadataBuilder."""

    title: str = ""
    page_count: int = 0
    font_stats: FontStatistics = Field(default_factory=FontStatistics)
    numbering_patterns: list[NumberingPattern] = Field(default_factory=list)
    page_types: dict[int, PageType] = Field(default_factory=dict)
    source_format: str = ""  # "pdf", "docx", "image"
    source_path: str = ""


class Document(BaseModel):
    """The complete document representation flowing through the pipeline."""

    pages: list[Page] = Field(default_factory=list)
    metadata: DocumentMetadata = Field(default_factory=DocumentMetadata)

    @property
    def all_elements(self) -> list[PageElement]:
        """Iterate all elements across all pages."""
        result = []
        for page in self.pages:
            result.extend(page.elements)
        return result

    def elements_by_type(self, element_type: str) -> list[PageElement]:
        """Get all elements of a specific type."""
        return [e for e in self.all_elements if e.type == element_type]
