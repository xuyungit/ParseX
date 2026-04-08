"""Tests for the pipeline with real PDF files."""

import os
from pathlib import Path

import pytest

from parserx.config.schema import ParserXConfig
from parserx.pipeline import Pipeline


def _pipeline_no_ocr():
    """Create a Pipeline with OCR disabled (no credentials needed)."""
    cfg = ParserXConfig()
    cfg.builders.ocr.engine = "none"
    return Pipeline(cfg)

# Sample docs — set PARSERX_SAMPLE_DIR env var to point to test PDFs
SAMPLE_DIR = Path(os.environ.get("PARSERX_SAMPLE_DIR", "sample_docs"))
PDF_TEXT = SAMPLE_DIR / "pdf_text01.pdf"
DEEPSEEK = SAMPLE_DIR / "deepseek.pdf"


@pytest.mark.skipif(not PDF_TEXT.exists(), reason="Test PDF not available")
def test_parse_simple_pdf():
    pipeline = Pipeline()
    result = pipeline.parse(PDF_TEXT)
    assert len(result) > 0
    assert "<!-- PAGE 1 -->" in result


@pytest.mark.skipif(not DEEPSEEK.exists(), reason="Test PDF not available")
def test_parse_deepseek_pdf():
    pipeline = Pipeline()
    doc = pipeline.parse_to_document(DEEPSEEK)
    assert len(doc.pages) > 0
    assert doc.metadata.source_format == "pdf"
    # Should have some text elements
    text_elements = doc.elements_by_type("text")
    assert len(text_elements) > 0


def test_parse_nonexistent():
    pipeline = _pipeline_no_ocr()
    with pytest.raises(FileNotFoundError):
        pipeline.parse("/nonexistent/file.pdf")


def test_pipeline_init_without_ocr_credentials():
    Pipeline(ParserXConfig())


def test_parse_runs_image_extraction_without_output_dir(tmp_path: Path, monkeypatch):
    """parse() and parse_to_document() must still extract images + run VLM
    even though no output_dir is provided (using a temp dir internally)."""
    from parserx.models.elements import Document, Page, PageElement

    # Build a tiny document with one informational image
    img_elem = PageElement(
        type="image",
        bbox=(0, 0, 400, 300),
        metadata={"width": 400, "height": 300},
    )
    doc = Document(pages=[Page(number=1, width=595, height=842, elements=[img_elem])])

    pipeline = _pipeline_no_ocr()

    # Patch _extract to return our synthetic doc (avoid real PDF parsing)
    monkeypatch.setattr(pipeline, "_extract", lambda path: doc)

    # Track _extract_and_describe_images calls while stubbing
    # the actual image extractor (no real PDF to extract from)
    call_log: list[str] = []
    _real_method = pipeline._extract_and_describe_images

    def tracking_stub(d, source, images_dir):
        call_log.append(str(images_dir))
        # Simulate what the real extractor would do: set saved_path
        for elem in d.all_elements:
            if elem.type == "image" and not elem.metadata.get("skipped"):
                elem.metadata["saved_path"] = "images/fake.png"
                elem.metadata["saved_abs_path"] = str(images_dir / "images" / "fake.png")
        return d

    monkeypatch.setattr(pipeline, "_extract_and_describe_images", tracking_stub)

    # Create a dummy PDF so path validation passes
    dummy = tmp_path / "dummy.pdf"
    dummy.write_bytes(b"%PDF-1.4 fake")

    result_doc = pipeline.parse_to_document(dummy)

    # _extract_and_describe_images must have been called
    assert len(call_log) == 1
    # The temp dir should have been cleaned up — path no longer exists
    assert not Path(call_log[0]).exists()
    # Image element must NOT have stale saved_path / saved_abs_path
    img = result_doc.all_elements[0]
    assert "saved_path" not in img.metadata
    assert "saved_abs_path" not in img.metadata


def test_parse_unsupported_format(tmp_path: Path):
    fake = tmp_path / "test.xyz"
    fake.write_text("hello")
    pipeline = _pipeline_no_ocr()
    with pytest.raises(ValueError, match="Unsupported format"):
        pipeline.parse(fake)


def test_parse_result_collects_verification_warnings(tmp_path: Path, monkeypatch):
    from parserx.models.elements import Document, Page, PageElement

    doc = Document(
        pages=[
            Page(
                number=1,
                elements=[
                    PageElement(type="text", content="第一章 总则", metadata={"heading_level": 1}),
                    PageElement(type="text", content="三级标题", metadata={"heading_level": 3}),
                ],
            )
        ]
    )

    pipeline = _pipeline_no_ocr()
    monkeypatch.setattr(pipeline, "_extract", lambda path: doc)
    monkeypatch.setattr(pipeline, "_extract_and_describe_images", lambda d, source, images_dir: d)

    dummy = tmp_path / "dummy.pdf"
    dummy.write_bytes(b"%PDF-1.4 fake")

    result = pipeline.parse_result(dummy)

    assert result.page_count == 1
    assert result.element_count == 2
    assert any("jump from H1 to H3" in warning for warning in result.warnings)


def test_parse_result_counts_llm_fallback_calls(tmp_path: Path, monkeypatch):
    from parserx.models.elements import Document, Page, PageElement

    doc = Document(
        pages=[
            Page(
                number=1,
                elements=[
                    PageElement(
                        type="text",
                        content="项目概况",
                        metadata={"heading_level": 2, "llm_fallback_used": True},
                    ),
                    PageElement(type="text", content="正文内容"),
                ],
            )
        ]
    )

    pipeline = _pipeline_no_ocr()
    monkeypatch.setattr(pipeline, "_extract", lambda path: doc)
    monkeypatch.setattr(pipeline, "_extract_and_describe_images", lambda d, source, images_dir: d)

    dummy = tmp_path / "dummy.pdf"
    dummy.write_bytes(b"%PDF-1.4 fake")

    result = pipeline.parse_result(dummy)

    assert result.api_calls["llm"] == 1


def test_parse_result_separates_llm_api_calls_from_fallback_hits(tmp_path: Path, monkeypatch):
    from parserx.models.elements import Document, DocumentMetadata, Page, PageElement

    doc = Document(
        pages=[
            Page(
                number=1,
                elements=[
                    PageElement(
                        type="text",
                        content="项目概况",
                        metadata={"heading_level": 2, "llm_fallback_used": True},
                    ),
                    PageElement(
                        type="text",
                        content="适用范围",
                        metadata={"heading_level": 3, "llm_fallback_used": True},
                    ),
                ],
            )
        ],
        metadata=DocumentMetadata(processing_stats={"llm_calls": 1}),
    )

    pipeline = _pipeline_no_ocr()
    monkeypatch.setattr(pipeline, "_extract", lambda path: doc)
    monkeypatch.setattr(pipeline, "_extract_and_describe_images", lambda d, source, images_dir: d)

    dummy = tmp_path / "dummy.pdf"
    dummy.write_bytes(b"%PDF-1.4 fake")

    result = pipeline.parse_result(dummy)

    assert result.api_calls["llm"] == 1
    assert result.llm_fallback_hits == 2


# ── Quality check (formula fragmentation detection) ──────────────────


def test_quality_check_flags_formula_pages(monkeypatch):
    """Pages with formula fragments should be reclassified to SCANNED."""
    from parserx.models.elements import Document, Page, PageElement, PageType

    # Simulate a page with formula-like fragmented text (many short lines)
    formula_lines = "\n".join(
        ["EIδ11=(x", "′)", "3", "12+l3", "48-(x", "′+m)", "3", "12"]
        + ["正常文本行" * 5] * 3  # some normal lines to pass element count
    )
    elems = [
        PageElement(type="text", content=formula_lines, bbox=(0, 0, 300, 400)),
    ] + [
        PageElement(type="text", content=f"短{i}", bbox=(0, i * 10, 100, i * 10 + 10))
        for i in range(15)  # ensure > 10 text elements
    ]
    page = Page(number=2, width=595, height=842, elements=elems, page_type=PageType.NATIVE)
    doc = Document(pages=[page])

    # Mock LLM to return formula detection
    class MockLLM:
        def complete(self, system, user, *, temperature=0.0, max_tokens=64):
            return '{"has_formula_fragments": true}'

    pipeline = _pipeline_no_ocr()
    monkeypatch.setattr(pipeline, "_llm_service", MockLLM())

    pipeline._check_page_quality(doc)

    assert doc.pages[0].page_type == PageType.SCANNED


def test_quality_check_keeps_normal_pages(monkeypatch):
    """Normal text pages should stay NATIVE."""
    from parserx.models.elements import Document, Page, PageElement, PageType

    # Normal page with long text lines
    elems = [
        PageElement(
            type="text",
            content="这是一段正常的中文文本，没有公式碎片化的问题。" * 5,
            bbox=(0, i * 50, 500, i * 50 + 40),
        )
        for i in range(12)
    ]
    page = Page(number=1, width=595, height=842, elements=elems, page_type=PageType.NATIVE)
    doc = Document(pages=[page])

    class MockLLM:
        def complete(self, system, user, *, temperature=0.0, max_tokens=64):
            return '{"has_formula_fragments": false}'

    pipeline = _pipeline_no_ocr()
    monkeypatch.setattr(pipeline, "_llm_service", MockLLM())

    pipeline._check_page_quality(doc)

    assert doc.pages[0].page_type == PageType.NATIVE


def test_quality_check_skips_without_llm():
    """No crash when LLM service is not configured."""
    from parserx.models.elements import Document, Page, PageElement, PageType

    elems = [
        PageElement(type="text", content="x\n1\n2", bbox=(0, 0, 100, 100))
        for _ in range(12)
    ]
    page = Page(number=1, width=595, height=842, elements=elems, page_type=PageType.NATIVE)
    doc = Document(pages=[page])

    pipeline = _pipeline_no_ocr()
    # _llm_service is None by default (no credentials)
    pipeline._check_page_quality(doc)

    assert doc.pages[0].page_type == PageType.NATIVE
