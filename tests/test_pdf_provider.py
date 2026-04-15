"""Tests for PdfProvider page classification."""

from parserx.models.elements import PageElement, PageType
from parserx.providers.pdf import PDFProvider


def _make_provider():
    return PDFProvider.__new__(PDFProvider)


# ── Page classification ─────────────────────────────────────────────


def _make_text_elem(content: str, bbox: tuple) -> PageElement:
    return PageElement(type="text", content=content, bbox=bbox, source="native")


def _make_image_elem(bbox: tuple) -> PageElement:
    return PageElement(
        type="image", bbox=bbox, source="native",
        metadata={"xref": 1, "width": bbox[2] - bbox[0], "height": bbox[3] - bbox[1]},
    )


class _FakePage:
    """Minimal stand-in for fitz.Page with only the fields _classify_page reads."""
    def __init__(self, w: float = 595, h: float = 842, drawings: int = 0):
        self.rect = type("R", (), {"width": w, "height": h})()
        self._drawings = [{}] * drawings

    def get_drawings(self):
        return self._drawings


def test_classify_native_page():
    """Page with plenty of text and no images → NATIVE."""
    provider = _make_provider()
    text = [_make_text_elem("正文" * 200, (50, 50, 500, 700))]
    result = provider._classify_page(_FakePage(), text, [])
    assert result == PageType.NATIVE


def test_classify_scanned_no_text():
    """Page with big image but no text → SCANNED."""
    provider = _make_provider()
    img = [_make_image_elem((0, 0, 595, 842))]
    result = provider._classify_page(_FakePage(), [], img)
    assert result == PageType.SCANNED


def test_classify_ocr_layered_scan():
    """Page with a large scan image and text INSIDE it → SCANNED.

    This is the key case: a searchable-scan PDF has visible OCR text
    overlaid on the scan image.  Even though text_chars is high, the
    spatial relationship (text inside image) reveals it as a scan.
    """
    provider = _make_provider()
    # Scan image covering most of the page
    img = [_make_image_elem((50, 50, 550, 800))]
    # OCR text layer — all text inside the scan image bbox
    text = [
        _make_text_elem("中华人民共和国行业标准" * 10, (80, 100, 500, 150)),
        _make_text_elem("公路钢筋混凝土桥涵设计" * 10, (80, 200, 500, 250)),
        _make_text_elem("规范条文说明及解释内容" * 10, (80, 300, 500, 350)),
    ]
    result = provider._classify_page(_FakePage(), text, img)
    assert result == PageType.SCANNED


def test_classify_native_with_embedded_photo():
    """Page with text around an image (not inside it) → NATIVE.

    Normal native PDF: text is above/below the image, not spatially
    contained within it.
    """
    provider = _make_provider()
    # Image in the middle of the page
    img = [_make_image_elem((100, 300, 500, 600))]
    # Text above and below the image — outside image bbox
    text = [
        _make_text_elem("标题文字内容很长" * 20, (50, 50, 500, 100)),
        _make_text_elem("图片下方的正文段落" * 20, (50, 650, 500, 800)),
    ]
    result = provider._classify_page(_FakePage(), text, img)
    assert result == PageType.NATIVE


def test_classify_ocr_scan_borderline_coverage():
    """Image covering exactly 50% with all text inside → SCANNED."""
    provider = _make_provider()
    pw, ph = 600, 800
    # Image covering 50.1% of page area
    img_h = int((pw * ph * 0.51) / pw)
    img = [_make_image_elem((0, 0, pw, img_h))]
    text = [_make_text_elem("文字" * 100, (50, 50, pw - 50, img_h - 50))]
    result = provider._classify_page(_FakePage(pw, ph), text, img)
    assert result == PageType.SCANNED


def test_classify_mixed_page():
    """Page with little text and moderate image coverage → MIXED."""
    provider = _make_provider()
    img = [_make_image_elem((0, 0, 400, 400))]  # ~32% of page
    text = [_make_text_elem("短文" * 30, (50, 500, 400, 550))]  # 60 chars < 200
    result = provider._classify_page(_FakePage(), text, img)
    assert result == PageType.MIXED


def test_classify_vector_drawn_page():
    """Page with no images, ~no text, but dense vector drawings → SCANNED.

    Vector-rendered PDFs (print-to-PDF from web, SVG-based reports) emit
    text as path strokes. Without routing to OCR they produce empty output.
    """
    provider = _make_provider()
    # 224 chars of chrome (URLs, footer) — below the 500 threshold
    text = [_make_text_elem("https://example.com/foo " * 10, (50, 800, 500, 820))]
    result = provider._classify_page(_FakePage(drawings=3000), text, [])
    assert result == PageType.SCANNED


def test_classify_native_page_with_drawings():
    """Plenty of text + drawings (e.g. native PDF with charts) → NATIVE."""
    provider = _make_provider()
    text = [_make_text_elem("正文" * 400, (50, 50, 500, 700))]  # 800 chars
    result = provider._classify_page(_FakePage(drawings=3000), text, [])
    assert result == PageType.NATIVE


# ── _count_chars_inside_bbox ─────────────────────────────────────────


def test_count_chars_inside_bbox():
    bbox = (100, 100, 500, 500)
    inside = _make_text_elem("内部文本", (150, 150, 400, 200))   # center inside
    outside = _make_text_elem("外部文本", (10, 10, 90, 50))      # center outside
    partial = _make_text_elem("边界文本", (80, 300, 200, 350))   # center at (140, 325) → inside

    count = PDFProvider._count_chars_inside_bbox([inside, outside, partial], bbox)
    assert count == len("内部文本") + len("边界文本")
