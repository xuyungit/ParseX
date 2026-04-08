"""Tests for HeaderFooterProcessor."""

from parserx.models.elements import Document, FontInfo, Page, PageElement
from parserx.config.schema import HeaderFooterConfig
from parserx.processors.header_footer import (
    HeaderFooterProcessor,
    _is_page_number,
    _normalize_for_comparison,
)


def _text_elem(content: str, y0: float, y1: float, page: int = 1) -> PageElement:
    return PageElement(
        type="text",
        content=content,
        bbox=(50, y0, 500, y1),
        page_number=page,
        font=FontInfo(name="SimSun", size=10.0),
    )


def _make_doc_with_headers(page_count: int = 5) -> Document:
    """Create a document where each page has a repeated header and footer."""
    pages = []
    for i in range(1, page_count + 1):
        elements = [
            _text_elem("公司机密文件", 10, 25, i),           # Header (top zone)
            _text_elem(f"正文内容第{i}页" * 10, 100, 700, i),  # Body
            _text_elem(f"- {i} -", 770, 785, i),              # Page number (footer)
        ]
        pages.append(Page(number=i, width=595, height=842, elements=elements))
    return Document(pages=pages)


def test_is_page_number():
    assert _is_page_number("3") is True
    assert _is_page_number("- 3 -") is True
    assert _is_page_number("第 5 页") is True
    assert _is_page_number("iv") is True
    assert _is_page_number("这不是页码") is False


def test_normalize_for_comparison():
    # Numbers become placeholders
    assert _normalize_for_comparison("Page 1") == _normalize_for_comparison("Page 2")
    assert _normalize_for_comparison("第 3 页") == _normalize_for_comparison("第 7 页")


def test_remove_repeated_headers():
    doc = _make_doc_with_headers(5)
    processor = HeaderFooterProcessor()
    result = processor.process(doc)

    # Page 1: header retained as first-page identity
    page1_texts = [e.content for e in result.pages[0].elements if e.type == "text"]
    assert "公司机密文件" in page1_texts

    # Pages 2+: header removed
    for page in result.pages[1:]:
        texts = [e.content for e in page.elements if e.type == "text"]
        assert "公司机密文件" not in texts
        # Body should remain
        assert any("正文内容" in t for t in texts)


def test_remove_page_numbers():
    doc = _make_doc_with_headers(5)
    processor = HeaderFooterProcessor()
    result = processor.process(doc)

    for page in result.pages:
        texts = [e.content for e in page.elements if e.type == "text"]
        # Page numbers should be removed
        assert not any("- " in t and t.strip().replace("-", "").strip().isdigit() for t in texts)


def test_remove_bottom_edge_page_numbers_slightly_above_footer_zone():
    pages = []
    for i in range(1, 4):
        elements = [
            _text_elem("正文" * 20, 100, 400, i),
            _text_elem(str(i), 520, 536, i),
        ]
        pages.append(Page(number=i, width=595, height=595, elements=elements))
    doc = Document(pages=pages)

    result = HeaderFooterProcessor().process(doc)

    for page in result.pages:
        texts = [e.content for e in page.elements if e.type == "text"]
        assert not any(text.strip().isdigit() for text in texts)


def test_skip_with_few_pages():
    """Should not remove anything with < 2 pages (can't detect repetition)."""
    doc = Document(pages=[
        Page(number=1, width=595, height=842, elements=[
            _text_elem("Header", 10, 25),
            _text_elem("Body text", 100, 700),
        ])
    ])
    processor = HeaderFooterProcessor()
    result = processor.process(doc)
    assert len(result.pages[0].elements) == 2  # Nothing removed


def test_threshold_boundary_not_removed():
    """Text on exactly 50% of pages should NOT be removed (requires >50%)."""
    pages = []
    for i in range(1, 11):  # 10 pages
        elements = [_text_elem("正文" * 20, 100, 700, i)]
        # Put header on first 5 pages only (exactly 50%)
        if i <= 5:
            elements.insert(0, _text_elem("半数页眉", 10, 25, i))
        pages.append(Page(number=i, width=595, height=842, elements=elements))
    doc = Document(pages=pages)

    processor = HeaderFooterProcessor()
    result = processor.process(doc)

    # 50% is not >50%, so the header must be preserved
    for page in result.pages[:5]:
        texts = [e.content for e in page.elements]
        assert "半数页眉" in texts


def test_threshold_boundary_removed():
    """Text on >50% of pages SHOULD be removed."""
    pages = []
    for i in range(1, 11):  # 10 pages
        elements = [_text_elem("正文" * 20, 100, 700, i)]
        # Put header on first 6 pages (60%)
        if i <= 6:
            elements.insert(0, _text_elem("多数页眉", 10, 25, i))
        pages.append(Page(number=i, width=595, height=842, elements=elements))
    doc = Document(pages=pages)

    processor = HeaderFooterProcessor()
    result = processor.process(doc)

    # 60% > 50%, so the header should be removed on pages 2+
    # Page 1 retains it as first-page identity
    page1_texts = [e.content for e in result.pages[0].elements]
    assert "多数页眉" in page1_texts

    for page in result.pages[1:6]:
        texts = [e.content for e in page.elements]
        assert "多数页眉" not in texts


def test_preserve_non_repeated():
    """Truly different content in edge zones should be preserved."""
    unique_titles = ["项目概况", "技术要求", "评审方法", "合同条款", "附件清单"]
    pages = []
    for i in range(5):
        elements = [
            _text_elem(unique_titles[i], 10, 25, i + 1),  # Truly different each page
            _text_elem("正文" * 20, 100, 700, i + 1),
        ]
        pages.append(Page(number=i + 1, width=595, height=842, elements=elements))
    doc = Document(pages=pages)

    processor = HeaderFooterProcessor()
    result = processor.process(doc)

    # Non-repeated headers should be preserved
    for page in result.pages:
        assert len(page.elements) == 2


# ── First-page identity retention tests ──


def test_first_page_identity_retained():
    """Repeated header should be kept on page 1 and removed on pages 2+."""
    doc = _make_doc_with_headers(5)
    processor = HeaderFooterProcessor()
    result = processor.process(doc)

    # Page 1: repeated header "公司机密文件" should be KEPT
    page1_texts = [e.content for e in result.pages[0].elements if e.type == "text"]
    assert "公司机密文件" in page1_texts

    # Pages 2-5: repeated header should be REMOVED
    for page in result.pages[1:]:
        texts = [e.content for e in page.elements if e.type == "text"]
        assert "公司机密文件" not in texts


def test_first_page_page_numbers_still_removed():
    """Page numbers should be removed even on page 1."""
    doc = _make_doc_with_headers(5)
    processor = HeaderFooterProcessor()
    result = processor.process(doc)

    # Page 1 should NOT have page number
    for page in result.pages:
        texts = [e.content for e in page.elements if e.type == "text"]
        assert not any(
            t.strip().replace("-", "").replace(" ", "").isdigit()
            for t in texts
        )


def test_retained_elements_have_metadata_flag():
    """Retained first-page identity elements should have the metadata flag."""
    doc = _make_doc_with_headers(5)
    processor = HeaderFooterProcessor()
    result = processor.process(doc)

    # The retained header on page 1 should have the flag
    retained = [
        e for e in result.pages[0].elements
        if e.metadata.get("retained_page_identity")
    ]
    assert len(retained) == 1
    assert retained[0].content == "公司机密文件"


def test_first_page_unique_content_not_flagged():
    """Content only on page 1 (not repeated) should NOT get the retained flag.

    The retained_page_identity flag is only for elements that *would have been
    removed* but were kept because they're on page 1.
    """
    pages = []
    for i in range(1, 4):
        elements = [_text_elem("正文" * 20, 100, 700, i)]
        if i == 1:
            elements.insert(0, _text_elem("首页独有标题", 10, 25, i))
        pages.append(Page(number=i, width=595, height=842, elements=elements))
    doc = Document(pages=pages)

    processor = HeaderFooterProcessor()
    result = processor.process(doc)

    # The unique first-page element should be preserved (not removed)
    page1_texts = [e.content for e in result.pages[0].elements]
    assert "首页独有标题" in page1_texts

    # It should NOT have the retained_page_identity flag (it was never a removal candidate)
    for e in result.pages[0].elements:
        if e.content == "首页独有标题":
            assert not e.metadata.get("retained_page_identity")


# ── Max retained identity limit tests ──


def _make_doc_with_many_headers(page_count: int = 5) -> Document:
    """Create a doc with 4 repeated headers on each page."""
    pages = []
    for i in range(1, page_count + 1):
        elements = [
            _text_elem("公司机密文件", 5, 15, i),           # Header 1
            _text_elem("内部使用", 16, 26, i),               # Header 2
            _text_elem("XY科技有限公司研究报告", 27, 42, i),  # Header 3 (longest)
            _text_elem("2026年度", 43, 53, i),               # Header 4
            _text_elem(f"正文内容第{i}页" * 10, 100, 700, i),
            _text_elem(f"- {i} -", 770, 785, i),
        ]
        pages.append(Page(number=i, width=595, height=842, elements=elements))
    return Document(pages=pages)


def test_max_retained_identity_default_2():
    """With default max_retained_identity=2, only 2 elements retained on page 1."""
    doc = _make_doc_with_many_headers(5)
    processor = HeaderFooterProcessor()
    result = processor.process(doc)

    retained = [
        e for e in result.pages[0].elements
        if e.metadata.get("retained_page_identity")
    ]
    assert len(retained) == 2
    # Longest text should be retained first (information density ranking).
    assert retained[0].content == "XY科技有限公司研究报告"


def test_max_retained_identity_custom():
    """Custom max_retained_identity=1 should keep only 1 element."""
    doc = _make_doc_with_many_headers(5)
    config = HeaderFooterConfig(max_retained_identity=1)
    processor = HeaderFooterProcessor(config=config)
    result = processor.process(doc)

    retained = [
        e for e in result.pages[0].elements
        if e.metadata.get("retained_page_identity")
    ]
    assert len(retained) == 1


def test_retained_have_exclude_from_heading_detection():
    """Retained elements should have exclude_from_heading_detection metadata."""
    doc = _make_doc_with_headers(5)
    processor = HeaderFooterProcessor()
    result = processor.process(doc)

    retained = [
        e for e in result.pages[0].elements
        if e.metadata.get("retained_page_identity")
    ]
    assert len(retained) >= 1
    for e in retained:
        assert e.metadata.get("exclude_from_heading_detection") is True


def test_page_numbers_removed_even_with_high_limit():
    """Page numbers should always be removed, regardless of max_retained_identity."""
    doc = _make_doc_with_many_headers(5)
    config = HeaderFooterConfig(max_retained_identity=10)
    processor = HeaderFooterProcessor(config=config)
    result = processor.process(doc)

    for page in result.pages:
        texts = [e.content for e in page.elements if e.type == "text"]
        assert not any(
            t.strip().replace("-", "").replace(" ", "").isdigit()
            for t in texts
        )
