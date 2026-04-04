"""Tests for OCRBuilder (unit tests — no actual API calls)."""

from parserx.builders.ocr import OCRBuilder
from parserx.config.schema import OCRBuilderConfig
from parserx.models.elements import Document, Page, PageElement, PageType


def test_skip_native_pages():
    """OCRBuilder should skip native pages."""
    builder = OCRBuilder(OCRBuilderConfig(engine="none"))
    pages = [
        Page(number=1, width=595, height=842, page_type=PageType.NATIVE,
             elements=[PageElement(type="text", content="Text content" * 20)]),
    ]
    doc = Document(pages=pages)

    # Should not OCR — all pages native
    assert not builder._should_ocr_page(pages[0])


def test_ocr_scanned_page():
    """OCRBuilder should OCR scanned pages."""
    builder = OCRBuilder()
    page = Page(number=1, width=595, height=842, page_type=PageType.SCANNED)
    assert builder._should_ocr_page(page)


def test_ocr_mixed_page():
    """OCRBuilder should OCR mixed pages."""
    builder = OCRBuilder()
    page = Page(number=1, width=595, height=842, page_type=PageType.MIXED)
    assert builder._should_ocr_page(page)


def test_ocr_sparse_native():
    """OCRBuilder should OCR native pages with very little text (vector-rendered)."""
    builder = OCRBuilder()
    page = Page(
        number=1, width=595, height=842, page_type=PageType.NATIVE,
        elements=[PageElement(type="text", content="ab")],  # < 20 chars
    )
    assert builder._should_ocr_page(page)


def test_skip_rich_native():
    """OCRBuilder should skip native pages with sufficient text."""
    builder = OCRBuilder()
    page = Page(
        number=1, width=595, height=842, page_type=PageType.NATIVE,
        elements=[PageElement(type="text", content="Normal text content " * 10)],
    )
    assert not builder._should_ocr_page(page)
