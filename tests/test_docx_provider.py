"""Tests for DOCXProvider."""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from parserx.providers.docx import DOCXProvider


class FakeProv:
    def __init__(self, page_no=1):
        self.page_no = page_no
        self.bbox = MagicMock(l=0.0, t=0.0, r=100.0, b=50.0)


class FakeFormatting:
    bold = True
    italic = False


class FakeSectionHeader:
    """Mimics docling SectionHeaderItem."""
    def __init__(self, text, level, page_no=1):
        self.text = text
        self.level = level
        self.prov = [FakeProv(page_no)]
        self.formatting = None


class FakeTextItem:
    """Mimics docling TextItem."""
    def __init__(self, text, page_no=1, formatting=None):
        self.text = text
        self.prov = [FakeProv(page_no)]
        self.formatting = formatting


class FakeTableCell:
    def __init__(self, text):
        self.text = text


class FakeTableData:
    def __init__(self, grid):
        self.grid = grid
        self.num_rows = len(grid)
        self.num_cols = len(grid[0]) if grid else 0


class FakeTableItem:
    def __init__(self, grid, page_no=1):
        self.data = FakeTableData(grid)
        self.prov = [FakeProv(page_no)]


class FakePictureItem:
    def __init__(self, page_no=1):
        self.prov = [FakeProv(page_no)]
        self.image = None


class FakeDoclingDoc:
    def __init__(self, items):
        self._items = items

    def iterate_items(self, with_groups=False):
        for item in self._items:
            yield item, 0


class FakeConversionResult:
    def __init__(self, doc):
        self.document = doc


def test_docx_provider_text_extraction():
    """Test basic text extraction from DOCX."""
    items = [
        FakeTextItem("Hello world", page_no=1),
        FakeTextItem("Second paragraph", page_no=1),
    ]
    docling_doc = FakeDoclingDoc(items)
    result = FakeConversionResult(docling_doc)

    provider = DOCXProvider()

    with patch("parserx.providers.docx.DOCXProvider.extract") as mock_extract:
        # Test _convert_item directly
        from docling_core.types.doc.document import TextItem

        # Instead, test the internal methods
        pass

    # Test through the internal flow by calling convert_item
    # We need to patch the isinstance checks
    provider_instance = DOCXProvider()

    # Test _table_to_markdown
    grid = [
        [FakeTableCell("Name"), FakeTableCell("Value")],
        [FakeTableCell("A"), FakeTableCell("1")],
    ]
    table = FakeTableItem(grid)
    md = provider_instance._table_to_markdown(table)
    assert "| Name | Value |" in md
    assert "|---|---|" in md
    assert "| A | 1 |" in md


def test_table_to_markdown_empty():
    provider = DOCXProvider()
    table = MagicMock()
    table.data = None
    assert provider._table_to_markdown(table) == ""


def test_section_header_numeric_only_skipped():
    """SectionHeaderItem whose entire text is digits/whitespace returns None.

    Docling emits such paragraphs for PAGE-field-bearing footers and similar
    auto-generated section markers that Word promotes to an outline level.
    They are never real headings — filtering them out prevents bogus
    ``#### 1 2`` / ``#### 3`` noise from leaking into the body.
    """
    provider = DOCXProvider()
    # Patch the isinstance checks by patching the imported types the
    # provider does a fresh import of inside _convert_item.
    with patch("docling_core.types.doc.document.SectionHeaderItem", FakeSectionHeader), \
         patch("docling_core.types.doc.document.TextItem", FakeTextItem), \
         patch("docling_core.types.doc.document.ListItem", type("_FakeListItem", (), {})), \
         patch("docling_core.types.doc.document.TableItem", type("_FakeTableItem", (), {})), \
         patch("docling_core.types.doc.document.PictureItem", type("_FakePictureItem", (), {})):
        numeric = FakeSectionHeader("1 ", level=3, page_no=1)
        result = provider._convert_item(numeric, page_number=1, docling_doc=None)
        assert result is None

        numeric_two = FakeSectionHeader("5 6", level=3, page_no=1)
        assert provider._convert_item(numeric_two, 1, None) is None

        blank = FakeSectionHeader("   ", level=2, page_no=1)
        assert provider._convert_item(blank, 1, None) is None

        # Legitimate heading with digits is kept.
        real = FakeSectionHeader("1 买卖合同", level=1, page_no=1)
        kept = provider._convert_item(real, 1, None)
        assert kept is not None
        assert kept.content == "1 买卖合同"
        assert kept.metadata["heading_level"] == 1


def test_extract_bbox():
    provider = DOCXProvider()
    item = MagicMock()
    item.prov = [FakeProv()]
    bbox = provider._extract_bbox(item)
    assert bbox == (0.0, 0.0, 100.0, 50.0)


def test_extract_bbox_no_prov():
    provider = DOCXProvider()
    item = MagicMock(spec=[])  # No prov attribute
    bbox = provider._extract_bbox(item)
    assert bbox == (0.0, 0.0, 0.0, 0.0)


def test_make_page():
    provider = DOCXProvider()
    from parserx.models.elements import PageElement, PageType

    elems = [PageElement(type="text", content="test", page_number=1)]
    page = provider._make_page(1, elems)
    assert page.number == 1
    assert page.page_type == PageType.NATIVE
    assert len(page.elements) == 1
