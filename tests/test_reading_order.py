"""Tests for ReadingOrderBuilder and column detection."""

from parserx.builders.reading_order import (
    ColumnLayout,
    ReadingOrderBuilder,
    classify_element,
    detect_columns,
    reorder_elements,
)
from parserx.models.elements import Document, FontInfo, Page, PageElement

_BODY_FONT = FontInfo(name="NimbusRomNo9L-Regu", size=10.0)
_PAGE_WIDTH = 612.0  # US Letter
_PAGE_HEIGHT = 792.0


def _elem(
    content: str,
    x0: float, y0: float, x1: float, y1: float,
) -> PageElement:
    return PageElement(
        type="text",
        content=content,
        bbox=(x0, y0, x1, y1),
        font=_BODY_FONT,
        page_number=1,
    )


def _make_doc(elements: list[PageElement]) -> Document:
    return Document(
        pages=[Page(number=1, width=_PAGE_WIDTH, height=_PAGE_HEIGHT, elements=elements)],
    )


# ── detect_columns ────────────────────────────────────────────────────


def test_two_column_detected():
    """Standard academic two-column layout should be detected."""
    elems = [
        # Left column (x: 72–297)
        _elem("Left paragraph 1", 72, 74, 297, 120),
        _elem("Left paragraph 2", 72, 130, 297, 180),
        _elem("Left paragraph 3", 72, 190, 297, 240),
        _elem("Left paragraph 4", 72, 250, 297, 300),
        # Right column (x: 315–540)
        _elem("Right paragraph 1", 315, 74, 540, 120),
        _elem("Right paragraph 2", 315, 130, 540, 180),
        _elem("Right paragraph 3", 315, 190, 540, 240),
        _elem("Right paragraph 4", 315, 250, 540, 300),
    ]
    layout = detect_columns(elems, _PAGE_WIDTH)
    assert layout is not None
    assert layout.column_count == 2
    assert 280 < layout.gutter_x < 330  # Somewhere between columns
    assert layout.confidence >= 0.6


def test_single_column_returns_none():
    """Full-width elements should not trigger column detection."""
    elems = [
        _elem("Paragraph 1", 72, 74, 540, 120),
        _elem("Paragraph 2", 72, 130, 540, 180),
        _elem("Paragraph 3", 72, 190, 540, 240),
        _elem("Paragraph 4", 72, 250, 540, 300),
        _elem("Paragraph 5", 72, 310, 540, 360),
        _elem("Paragraph 6", 72, 370, 540, 420),
    ]
    layout = detect_columns(elems, _PAGE_WIDTH)
    assert layout is None


def test_too_few_elements_returns_none():
    elems = [
        _elem("Left", 72, 74, 297, 120),
        _elem("Right", 315, 74, 540, 120),
    ]
    layout = detect_columns(elems, _PAGE_WIDTH)
    assert layout is None


def test_unbalanced_columns_low_confidence():
    """Very unbalanced sides should reduce confidence."""
    elems = [
        # 1 left, 7 right — very unbalanced
        _elem("Left only", 72, 74, 297, 120),
        _elem("Right 1", 315, 74, 540, 120),
        _elem("Right 2", 315, 130, 540, 180),
        _elem("Right 3", 315, 190, 540, 240),
        _elem("Right 4", 315, 250, 540, 300),
        _elem("Right 5", 315, 310, 540, 360),
        _elem("Right 6", 315, 370, 540, 420),
        _elem("Right 7", 315, 430, 540, 480),
    ]
    layout = detect_columns(elems, _PAGE_WIDTH)
    # Should fail: left side has only 1 element (< MIN_SIDE_ELEMENTS=3)
    assert layout is None


# ── classify_element ──────────────────────────────────────────────────


def test_classify_element_left_right_fullwidth():
    layout = ColumnLayout(
        column_count=2, gutter_x=306, gutter_width=18,
        left_col_right=297, right_col_right=540, confidence=0.9,
    )
    assert classify_element(
        _elem("Left", 72, 74, 297, 120), layout, _PAGE_WIDTH,
    ) == "left"
    assert classify_element(
        _elem("Right", 315, 74, 540, 120), layout, _PAGE_WIDTH,
    ) == "right"
    assert classify_element(
        _elem("Title", 72, 30, 540, 60), layout, _PAGE_WIDTH,
    ) == "full_width"


# ── reorder_elements ──────────────────────────────────────────────────


def test_reorder_two_columns():
    """Left column should come before right column after reorder."""
    layout = ColumnLayout(
        column_count=2, gutter_x=306, gutter_width=18,
        left_col_right=297, right_col_right=540, confidence=0.9,
    )
    elems = [
        # Interleaved by y-coordinate (current broken order)
        _elem("L1", 72, 74, 297, 120),
        _elem("R1", 315, 74, 540, 120),
        _elem("L2", 72, 130, 297, 180),
        _elem("R2", 315, 130, 540, 180),
    ]
    result = reorder_elements(elems, layout, _PAGE_WIDTH)
    contents = [e.content for e in result]
    assert contents == ["L1", "L2", "R1", "R2"]


def test_reorder_fullwidth_boundary():
    """Full-width element should split column zones."""
    layout = ColumnLayout(
        column_count=2, gutter_x=306, gutter_width=18,
        left_col_right=297, right_col_right=540, confidence=0.9,
    )
    elems = [
        _elem("L1", 72, 74, 297, 120),
        _elem("R1", 315, 74, 540, 120),
        _elem("TITLE", 72, 200, 540, 230),  # Full-width boundary
        _elem("L2", 72, 250, 297, 300),
        _elem("R2", 315, 250, 540, 300),
    ]
    result = reorder_elements(elems, layout, _PAGE_WIDTH)
    contents = [e.content for e in result]
    assert contents == ["L1", "R1", "TITLE", "L2", "R2"]


def test_reorder_stores_column_metadata():
    layout = ColumnLayout(
        column_count=2, gutter_x=306, gutter_width=18,
        left_col_right=297, right_col_right=540, confidence=0.9,
    )
    elems = [
        _elem("Left", 72, 74, 297, 120),
        _elem("Right", 315, 74, 540, 120),
    ]
    reorder_elements(elems, layout, _PAGE_WIDTH)
    assert elems[0].metadata["column"] == "left"
    assert elems[0].metadata["column_right_margin"] == 297
    assert elems[1].metadata["column"] == "right"
    assert elems[1].metadata["column_right_margin"] == 540


# ── ReadingOrderBuilder integration ──────────────────────────────────


def test_builder_single_column_unchanged():
    """Single-column documents should not be modified."""
    elems = [
        _elem("P1", 72, 74, 540, 120),
        _elem("P2", 72, 130, 540, 180),
        _elem("P3", 72, 190, 540, 240),
        _elem("P4", 72, 250, 540, 300),
        _elem("P5", 72, 310, 540, 360),
        _elem("P6", 72, 370, 540, 420),
    ]
    doc = _make_doc(list(elems))
    ReadingOrderBuilder().build(doc)
    # Order unchanged.
    assert [e.content for e in doc.pages[0].elements] == [
        "P1", "P2", "P3", "P4", "P5", "P6",
    ]
    # No column metadata set.
    assert "column" not in doc.pages[0].elements[0].metadata


def test_builder_two_column_reordered():
    elems = [
        _elem("L1", 72, 74, 297, 120),
        _elem("R1", 315, 74, 540, 120),
        _elem("L2", 72, 130, 297, 180),
        _elem("R2", 315, 130, 540, 180),
        _elem("L3", 72, 190, 297, 240),
        _elem("R3", 315, 190, 540, 240),
    ]
    doc = _make_doc(list(elems))
    ReadingOrderBuilder().build(doc)
    contents = [e.content for e in doc.pages[0].elements]
    assert contents == ["L1", "L2", "L3", "R1", "R2", "R3"]
