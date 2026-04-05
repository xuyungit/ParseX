"""Tests for OCRBuilder (unit tests — no actual API calls)."""

from parserx.builders.ocr import OCRBuilder
from parserx.config.schema import OCRBuilderConfig
from parserx.models.elements import Document, Page, PageElement, PageType
from parserx.services.ocr import OCRBlock, OCRResult


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


def _make_builder():
    """Create an OCRBuilder with engine=none for unit tests (no API needed)."""
    return OCRBuilder(OCRBuilderConfig(engine="none"))


def test_ocr_scanned_page():
    """OCRBuilder should OCR scanned pages."""
    builder = _make_builder()
    page = Page(number=1, width=595, height=842, page_type=PageType.SCANNED)
    assert builder._should_ocr_page(page)


def test_ocr_mixed_page():
    """OCRBuilder should OCR mixed pages."""
    builder = _make_builder()
    page = Page(number=1, width=595, height=842, page_type=PageType.MIXED)
    assert builder._should_ocr_page(page)


def test_ocr_sparse_native():
    """OCRBuilder should OCR native pages with very little text (vector-rendered)."""
    builder = _make_builder()
    page = Page(
        number=1, width=595, height=842, page_type=PageType.NATIVE,
        elements=[PageElement(type="text", content="ab")],  # < 20 chars
    )
    assert builder._should_ocr_page(page)


def test_skip_rich_native():
    """OCRBuilder should skip native pages with sufficient text."""
    builder = _make_builder()
    page = Page(
        number=1, width=595, height=842, page_type=PageType.NATIVE,
        elements=[PageElement(type="text", content="Normal text content " * 10)],
    )
    assert not builder._should_ocr_page(page)


def test_result_to_elements_keeps_normal_paragraph_title_as_heading():
    builder = _make_builder()
    result = OCRResult(
        blocks=[
            OCRBlock(
                text="7.4 乙型肝炎表面抗原破坏试验",
                label="paragraph_title",
                bbox=(0, 0, 100, 20),
            )
        ]
    )

    elements = builder._result_to_elements(result, page_number=1)

    assert len(elements) == 1
    assert elements[0].metadata["heading_level"] == 2


def test_result_to_elements_filters_chemical_name_false_heading():
    builder = _make_builder()
    result = OCRResult(
        blocks=[
            OCRBlock(
                text="1,3,5-Tris[(5-isopropyl-3-methoxycarbonyl-1-azulenyl)ethynyl]benzene",
                label="paragraph_title",
                bbox=(0, 0, 100, 20),
            )
        ]
    )

    elements = builder._result_to_elements(result, page_number=1)

    assert len(elements) == 1
    assert "heading_level" not in elements[0].metadata
