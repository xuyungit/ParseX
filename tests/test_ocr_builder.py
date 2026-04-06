"""Tests for OCRBuilder (unit tests — no actual API calls)."""

from parserx.builders.ocr import OCRBuilder, html_table_to_markdown
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


def test_fullpage_scan_image_marked_skipped():
    """Image is skipped when OCR text overlaps its bbox."""
    image = PageElement(
        type="image", page_number=1,
        bbox=(0, 0, 595, 842),
        metadata={"xref": 5, "width": 595, "height": 842},
    )
    ocr_text = PageElement(
        type="text", page_number=1, content="OCR提取的文字内容" * 10,
        source="ocr", bbox=(50, 50, 500, 200),
    )
    page = Page(
        number=1, width=595, height=842, page_type=PageType.SCANNED,
        elements=[image, ocr_text],
    )

    marked = OCRBuilder._mark_fullpage_scan_images(page)

    assert marked == 1
    assert image.metadata["skipped"] is True


def test_image_not_skipped_when_ocr_outside():
    """Image kept when OCR text is outside its bbox (OCR missed its content)."""
    image = PageElement(
        type="image", page_number=1,
        bbox=(50, 50, 300, 300),  # Image in upper-left
        metadata={"xref": 5, "width": 250, "height": 250},
    )
    ocr_text = PageElement(
        type="text", page_number=1, content="OCR提取的正文文字" * 10,
        source="ocr", bbox=(50, 400, 500, 700),  # OCR text is below the image
    )
    page = Page(
        number=1, width=595, height=842, page_type=PageType.SCANNED,
        elements=[image, ocr_text],
    )

    marked = OCRBuilder._mark_fullpage_scan_images(page)

    assert marked == 0
    assert image.metadata.get("skipped") is not True


def test_small_image_on_scanned_page_not_skipped():
    """Small image on a SCANNED page should NOT be skipped when no OCR overlap."""
    small_image = PageElement(
        type="image", page_number=1,
        bbox=(100, 100, 200, 200),  # Small area
        metadata={"xref": 5, "width": 100, "height": 100},
    )
    page = Page(
        number=1, width=595, height=842, page_type=PageType.SCANNED,
        elements=[small_image],
    )

    marked = OCRBuilder._mark_fullpage_scan_images(page)

    assert marked == 0
    assert small_image.metadata.get("skipped") is not True


def test_native_page_fullpage_image_not_affected():
    """_mark_fullpage_scan_images should skip images only on SCANNED pages.

    On NATIVE pages the method is never called from build(), but even
    if called directly it should still work (the logic only checks area).
    The guard is in build() — this test documents that large images on
    NATIVE pages are not touched by the OCR pipeline.
    """
    image = PageElement(
        type="image", page_number=1,
        bbox=(0, 0, 595, 842),
        metadata={"xref": 5, "width": 595, "height": 842},
    )
    page = Page(
        number=1, width=595, height=842, page_type=PageType.NATIVE,
        elements=[image, PageElement(type="text", content="Native text" * 20)],
    )

    builder = _make_builder()
    # build() won't call _mark_fullpage_scan_images on NATIVE pages.
    # Verify should_ocr returns False (so the whole branch is skipped).
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


def test_html_table_to_markdown_handles_rowspan_colspan_and_multirow_headers():
    html = """
    <table>
      <thead>
        <tr>
          <th rowspan="2">项目</th>
          <th colspan="2">2025</th>
        </tr>
        <tr>
          <th>Q1</th>
          <th>Q2</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td rowspan="2">收入</td>
          <td>10</td>
          <td>12</td>
        </tr>
        <tr>
          <td>11</td>
          <td>13</td>
        </tr>
      </tbody>
    </table>
    """

    markdown = html_table_to_markdown(html)

    assert markdown == "\n".join([
        "| 项目 | 2025 > Q1 | Q2 |",
        "| --- | --- | --- |",
        "| 收入 | 10 | 12 |",
        "|  | 11 | 13 |",
    ])


def test_html_table_to_markdown_uses_first_row_as_header_when_th_missing():
    html = """
    <table>
      <tr><td>型号</td><td>功率</td></tr>
      <tr><td>KFAW-1-80型</td><td>80kW</td></tr>
    </table>
    """

    markdown = html_table_to_markdown(html)

    assert markdown == "\n".join([
        "| 型号 | 功率 |",
        "| --- | --- |",
        "| KFAW-1-80型 | 80kW |",
    ])


def test_html_table_to_markdown_preserves_inline_content_order():
    html = """
    <table>
      <tr><th>说明</th></tr>
      <tr><td>主图<img src="chart.png" alt="图表"/><br><div>第二行</div></td></tr>
    </table>
    """

    markdown = html_table_to_markdown(html)

    assert markdown == "\n".join([
        "| 说明 |",
        "| --- |",
        "| 主图 ![图表](chart.png) / 第二行 |",
    ])


def test_result_to_elements_converts_complex_html_tables_to_markdown():
    builder = _make_builder()
    result = OCRResult(
        blocks=[
            OCRBlock(
                text="""
                <table>
                  <tr><td>型号</td><td>数量</td></tr>
                  <tr><td rowspan="2">A</td><td>1</td></tr>
                  <tr><td>2</td></tr>
                </table>
                """,
                label="table",
                bbox=(0, 0, 200, 100),
            )
        ]
    )

    elements = builder._result_to_elements(result, page_number=1)

    assert len(elements) == 1
    assert elements[0].type == "table"
    assert elements[0].content == "\n".join([
        "| 型号 | 数量 |",
        "| --- | --- |",
        "| A | 1 |",
        "|  | 2 |",
    ])
