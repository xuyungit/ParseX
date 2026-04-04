"""Tests for TableProcessor cross-page merging."""

from parserx.models.elements import Document, DocumentMetadata, Page, PageElement
from parserx.processors.table import (
    TableProcessor,
    _build_md_table,
    _headers_match,
    _parse_md_table,
)


def _make_table(content: str, page_number: int, y0: float, y1: float) -> PageElement:
    return PageElement(
        type="table",
        content=content,
        bbox=(50.0, y0, 500.0, y1),
        page_number=page_number,
        source="native",
        metadata={"rows": 0, "cols": 0},
    )


def _make_doc(pages_data: list[list[PageElement]]) -> Document:
    pages = []
    for i, elements in enumerate(pages_data):
        pages.append(
            Page(number=i + 1, width=595.0, height=842.0, elements=elements)
        )
    return Document(pages=pages, metadata=DocumentMetadata(page_count=len(pages)))


TABLE_PAGE1 = """\
| Name | Age | City |
|---|---|---|
| Alice | 30 | NYC |
| Bob | 25 | LA |"""

TABLE_PAGE2_WITH_HEADER = """\
| Name | Age | City |
|---|---|---|
| Charlie | 35 | CHI |
| Dave | 28 | SF |"""

TABLE_PAGE2_NO_HEADER = """\
| Charlie | 35 | CHI |
|---|---|---|
| Dave | 28 | SF |"""


def test_parse_md_table():
    header, sep, rows = _parse_md_table(TABLE_PAGE1)
    assert header == ["Name", "Age", "City"]
    assert len(rows) == 2
    assert rows[0] == ["Alice", "30", "NYC"]


def test_build_md_table():
    md = _build_md_table(["A", "B"], ["---", "---"], [["1", "2"], ["3", "4"]])
    assert "| A | B |" in md
    assert "|---|---|" in md
    assert "| 1 | 2 |" in md


def test_headers_match():
    assert _headers_match(["Name", "Age"], ["Name", "Age"])
    assert not _headers_match(["Name", "Age"], ["Name", "City"])
    assert not _headers_match(["A"], ["A", "B"])


def test_cross_page_merge_with_repeated_header():
    """Tables split across pages where page 2 repeats the header."""
    t1 = _make_table(TABLE_PAGE1, 1, 700.0, 842.0)  # bottom of page
    t2 = _make_table(TABLE_PAGE2_WITH_HEADER, 2, 0.0, 150.0)  # top of page

    doc = _make_doc([[t1], [t2]])
    processor = TableProcessor()
    result = processor.process(doc)

    # Table on page 2 should be removed
    assert len(result.pages[1].elements) == 0

    # Page 1 table should have merged rows
    merged = result.pages[0].elements[0]
    assert "Alice" in merged.content
    assert "Charlie" in merged.content
    assert "Dave" in merged.content
    assert merged.metadata.get("merged_from_pages") == [1, 2]


def test_cross_page_merge_column_mismatch():
    """Tables with different column counts should NOT merge."""
    t1 = _make_table("| A | B |\n|---|---|\n| 1 | 2 |", 1, 700.0, 842.0)
    t2 = _make_table("| X | Y | Z |\n|---|---|---|\n| a | b | c |", 2, 0.0, 100.0)

    doc = _make_doc([[t1], [t2]])
    processor = TableProcessor()
    result = processor.process(doc)

    # Both tables should remain
    assert len(result.pages[0].elements) == 1
    assert len(result.pages[1].elements) == 1


def test_no_merge_when_table_not_at_boundary():
    """Tables not at page boundaries should not merge."""
    t1 = _make_table(TABLE_PAGE1, 1, 100.0, 200.0)  # middle of page
    t2 = _make_table(TABLE_PAGE2_WITH_HEADER, 2, 400.0, 500.0)  # middle of page

    doc = _make_doc([[t1], [t2]])
    processor = TableProcessor()
    result = processor.process(doc)

    assert len(result.pages[0].elements) == 1
    assert len(result.pages[1].elements) == 1


def test_disabled():
    """Disabled processor should pass through."""
    from parserx.config.schema import TableProcessorConfig

    t1 = _make_table(TABLE_PAGE1, 1, 700.0, 842.0)
    t2 = _make_table(TABLE_PAGE2_WITH_HEADER, 2, 0.0, 100.0)
    doc = _make_doc([[t1], [t2]])

    processor = TableProcessor(TableProcessorConfig(enabled=False))
    result = processor.process(doc)

    assert len(result.pages[0].elements) == 1
    assert len(result.pages[1].elements) == 1


def test_three_page_merge():
    """Table spanning 3 pages should merge sequentially."""
    t1 = _make_table("| A | B |\n|---|---|\n| 1 | 2 |", 1, 700.0, 842.0)
    t2 = _make_table("| A | B |\n|---|---|\n| 3 | 4 |", 2, 0.0, 842.0)  # full page table
    t3 = _make_table("| A | B |\n|---|---|\n| 5 | 6 |", 3, 0.0, 100.0)

    doc = _make_doc([[t1], [t2], [t3]])
    processor = TableProcessor()
    result = processor.process(doc)

    # After merge: page 1 has merged t1+t2, page 2 empty, t2(merged) at bottom merges with t3
    # Actually: first pass merges t1+t2 (removes t2 from page 2), then tries page 2→3 but page 2 has no table
    assert len(result.pages[1].elements) == 0
    merged = result.pages[0].elements[0]
    assert "1" in merged.content
    assert "3" in merged.content
