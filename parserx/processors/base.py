"""Base protocol for document processors."""

from __future__ import annotations

from typing import Protocol

from parserx.models.elements import Document


class Processor(Protocol):
    """A processor transforms a document in-place.

    Processors are run sequentially in a fixed order. Each processor
    handles exactly one concern. The order is:

    1. HeaderFooterProcessor - remove headers/footers
    2. ChapterProcessor - detect chapter structure
    3. TableProcessor - extract and structure tables
    4. ImageProcessor - classify and describe images
    5. VLMReviewProcessor - page-level VLM review for OCR correction / missing text
    6. FormulaProcessor - detect and convert formulas
    7. LineUnwrapProcessor - fix visual line breaks
    8. TextCleanProcessor - clean text artifacts
    9. ContentValueProcessor - suppress low-information shell/chrome while preserving evidence
    10. ReadingOrderProcessor - determine reading order
    """

    def process(self, doc: Document) -> Document:
        """Process the document and return the modified version."""
        ...
