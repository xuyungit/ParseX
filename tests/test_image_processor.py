"""Tests for ImageProcessor."""

from parserx.models.elements import Document, Page, PageElement
from parserx.processors.image import ImageClassification, ImageProcessor, classify_image_element


def _img_elem(width: int, height: int) -> PageElement:
    return PageElement(
        type="image",
        bbox=(0, 0, float(width), float(height)),
        metadata={"width": width, "height": height},
    )


def test_classify_blank():
    elem = _img_elem(0, 0)
    assert classify_image_element(elem) == ImageClassification.BLANK


def test_classify_tiny_decorative():
    elem = _img_elem(3, 3)
    assert classify_image_element(elem) == ImageClassification.DECORATIVE


def test_classify_thin_strip():
    elem = _img_elem(2, 500)  # Thin horizontal line
    assert classify_image_element(elem) == ImageClassification.DECORATIVE


def test_classify_small_icon():
    elem = _img_elem(50, 50)  # 2500 area, < 12000
    assert classify_image_element(elem) == ImageClassification.DECORATIVE


def test_classify_informational():
    elem = _img_elem(400, 300)
    assert classify_image_element(elem) == ImageClassification.INFORMATIONAL


def test_classify_table_layout():
    elem = _img_elem(400, 300)
    elem.layout_type = "table"
    assert classify_image_element(elem) == ImageClassification.TABLE_IMAGE


def test_processor_stats():
    """ImageProcessor should classify and count images."""
    elements = [
        _img_elem(3, 3),      # decorative
        _img_elem(400, 300),   # informational
        _img_elem(50, 50),     # decorative (small)
        _img_elem(600, 400),   # informational
    ]
    doc = Document(pages=[Page(number=1, elements=elements)])

    processor = ImageProcessor()
    processor.process(doc)

    classifications = [e.metadata.get("image_class") for e in doc.all_elements]
    assert classifications.count(ImageClassification.DECORATIVE) == 2
    assert classifications.count(ImageClassification.INFORMATIONAL) == 2


def test_skip_decorative():
    """Decorative images should be marked as skipped."""
    doc = Document(pages=[Page(number=1, elements=[_img_elem(3, 3)])])
    processor = ImageProcessor()
    processor.process(doc)

    elem = doc.all_elements[0]
    assert elem.metadata.get("skipped") is True


def test_informational_needs_vlm():
    """Informational images should be marked for VLM processing."""
    doc = Document(pages=[Page(number=1, elements=[_img_elem(400, 300)])])
    processor = ImageProcessor()
    processor.process(doc)

    elem = doc.all_elements[0]
    assert elem.metadata.get("needs_vlm") is True
