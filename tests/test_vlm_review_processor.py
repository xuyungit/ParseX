"""Tests for VLMReviewProcessor."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from parserx.config.schema import VLMReviewConfig
from parserx.models.elements import Document, Page, PageElement, PageType
from parserx.processors.vlm_review import (
    Correction,
    VLMReviewProcessor,
    _extract_json,
)


# ── Fake VLM service ──────────────────────────────────────────────────────


class FakeVLMService:
    def __init__(self, responses: list[str] | None = None):
        self._responses = list(responses or [])
        self.calls: list[tuple[Path, str, str]] = []

    def describe_image(
        self,
        image_path: Path,
        prompt: str,
        *,
        context: str = "",
        temperature: float = 0.0,
        max_tokens: int = 4096,
        structured_output_mode: str = "off",
        json_schema: dict | None = None,
        json_schema_name: str = "parserx_vlm_review",
    ) -> str:
        self.calls.append((image_path, prompt, context))
        if not self._responses:
            return json.dumps({"corrections": [], "page_quality": "ok"})
        return self._responses.pop(0)


# ── Helpers ───────────────────────────────────────────────────────────────


def _text_elem(content: str, source: str = "ocr", **kwargs: Any) -> PageElement:
    return PageElement(type="text", content=content, source=source, **kwargs)


def _make_scanned_page(page_num: int = 1, texts: list[str] | None = None) -> Page:
    elements = [_text_elem(t) for t in (texts or ["混疑土强度"])]
    return Page(
        number=page_num,
        width=595,
        height=842,
        page_type=PageType.SCANNED,
        elements=elements,
    )


def _make_native_page(page_num: int = 1, texts: list[str] | None = None) -> Page:
    elements = [_text_elem(t, source="native") for t in (texts or ["正常文本内容" * 200])]
    return Page(
        number=page_num,
        width=595,
        height=842,
        page_type=PageType.NATIVE,
        elements=elements,
    )


def _ok_response() -> str:
    return json.dumps({"corrections": [], "page_quality": "ok"})


def _fix_response(index: int, original: str, corrected: str) -> str:
    return json.dumps({
        "corrections": [
            {
                "type": "fix_text",
                "element_index": index,
                "original": original,
                "corrected": corrected,
            }
        ],
        "page_quality": "needs_correction",
    })


def _add_missing_response(content: str, content_type: str = "text",
                          heading_level: int | None = None,
                          insert_after: int = -1) -> str:
    corr: dict[str, Any] = {
        "type": "add_missing",
        "content": content,
        "content_type": content_type,
        "insert_after_index": insert_after,
    }
    if heading_level is not None:
        corr["heading_level"] = heading_level
    return json.dumps({
        "corrections": [corr],
        "page_quality": "needs_correction",
    })


# ── Page selection tests ──────────────────────────────────────────────────


def test_select_scanned_pages():
    proc = VLMReviewProcessor(config=VLMReviewConfig())
    doc = Document(pages=[
        _make_scanned_page(1),
        _make_native_page(2),
        _make_scanned_page(3),
    ])
    selected = proc._select_pages(doc)
    assert [p.number for p in selected] == [1, 3]


def test_select_mixed_pages():
    proc = VLMReviewProcessor(config=VLMReviewConfig())
    page = Page(number=1, width=595, height=842, page_type=PageType.MIXED,
                elements=[_text_elem("some text")])
    doc = Document(pages=[page])
    selected = proc._select_pages(doc)
    assert len(selected) == 1


def test_skip_native_pages():
    """NATIVE pages should not be selected — only SCANNED/MIXED are reviewed."""
    proc = VLMReviewProcessor(config=VLMReviewConfig())
    page = _make_native_page(1, texts=["短"])  # Sparse text, but still NATIVE
    doc = Document(pages=[page])
    selected = proc._select_pages(doc)
    assert len(selected) == 0



def test_review_all_pages_override():
    proc = VLMReviewProcessor(config=VLMReviewConfig(review_all_pages=True))
    doc = Document(pages=[_make_native_page(1), _make_native_page(2)])
    selected = proc._select_pages(doc)
    assert len(selected) == 2


def test_max_pages_cap():
    proc = VLMReviewProcessor(config=VLMReviewConfig(
        review_all_pages=True, max_pages_per_doc=3,
    ))
    doc = Document(pages=[_make_scanned_page(i) for i in range(1, 8)])
    selected = proc._select_pages(doc)
    assert len(selected) == 3


# ── Extraction summary tests ─────────────────────────────────────────────


def test_build_extraction_summary():
    proc = VLMReviewProcessor()
    page = _make_scanned_page(1, texts=["混疑土强度等级", "C30 混凝土"])
    summary = proc._build_extraction_summary(page)
    data = json.loads(summary)
    assert len(data) == 2
    assert data[0]["text"] == "混疑土强度等级"
    assert data[1]["text"] == "C30 混凝土"


def test_summary_truncates_long_text():
    proc = VLMReviewProcessor()
    page = _make_scanned_page(1, texts=["x" * 500])
    summary = proc._build_extraction_summary(page)
    data = json.loads(summary)
    assert data[0]["text"].endswith("...")
    assert data[0]["text_length"] == 500


def test_summary_skips_skip_render():
    proc = VLMReviewProcessor()
    elem = _text_elem("hidden")
    elem.metadata["skip_render"] = True
    page = Page(number=1, width=595, height=842, page_type=PageType.SCANNED,
                elements=[elem, _text_elem("visible")])
    summary = proc._build_extraction_summary(page)
    data = json.loads(summary)
    assert len(data) == 1
    assert data[0]["text"] == "visible"


# ── JSON parsing tests ────────────────────────────────────────────────────


def test_extract_json_plain():
    data = _extract_json('{"corrections": [], "page_quality": "ok"}')
    assert data["page_quality"] == "ok"


def test_extract_json_with_fences():
    raw = '```json\n{"corrections": [], "page_quality": "ok"}\n```'
    data = _extract_json(raw)
    assert data["page_quality"] == "ok"


def test_parse_corrections_fix_text():
    proc = VLMReviewProcessor()
    response = _fix_response(0, "混疑土", "混凝土")
    corrections = proc._parse_corrections(response)
    assert len(corrections) == 1
    assert corrections[0].type == "fix_text"
    assert corrections[0].element_index == 0
    assert corrections[0].original == "混疑土"
    assert corrections[0].corrected == "混凝土"


def test_parse_corrections_add_missing():
    proc = VLMReviewProcessor()
    response = _add_missing_response("专家评审组名单", "heading", heading_level=2)
    corrections = proc._parse_corrections(response)
    assert len(corrections) == 1
    c = corrections[0]
    assert c.type == "add_missing"
    assert c.content == "专家评审组名单"
    assert c.content_type == "heading"
    assert c.heading_level == 2


def test_parse_corrections_empty():
    proc = VLMReviewProcessor()
    corrections = proc._parse_corrections(_ok_response())
    assert corrections == []


def test_parse_corrections_invalid_json():
    proc = VLMReviewProcessor()
    corrections = proc._parse_corrections("not json at all")
    assert corrections == []


def test_parse_corrections_unknown_type_skipped():
    proc = VLMReviewProcessor()
    response = json.dumps({
        "corrections": [{"type": "unknown_type"}],
        "page_quality": "ok",
    })
    corrections = proc._parse_corrections(response)
    assert corrections == []


# ── Correction application tests ──────────────────────────────────────────


def test_apply_fix_text():
    proc = VLMReviewProcessor()
    page = _make_scanned_page(1, texts=["混疑土强度等级"])
    corr = Correction(type="fix_text", element_index=0,
                      original="混疑土", corrected="混凝土")
    applied = proc._apply_corrections(page, [corr])
    assert applied == 1
    assert page.elements[0].content == "混凝土强度等级"
    assert page.elements[0].source == "vlm"
    assert page.elements[0].metadata["vlm_review_original"] == "混疑土强度等级"
    assert page.elements[0].metadata["vlm_review_applied"] == "fix_text"


def test_apply_fix_text_full_replacement():
    """When original is not provided, corrected replaces entire content."""
    proc = VLMReviewProcessor()
    page = _make_scanned_page(1, texts=["garbled text"])
    corr = Correction(type="fix_text", element_index=0,
                      corrected="correct text")
    applied = proc._apply_corrections(page, [corr])
    assert applied == 1
    assert page.elements[0].content == "correct text"


def test_apply_fix_skips_when_original_mismatches():
    """When VLM provides an original that doesn't match, skip the correction."""
    proc = VLMReviewProcessor()
    page = _make_scanned_page(1, texts=["钢盤混疑土强度等级"])
    corr = Correction(type="fix_text", element_index=0,
                      original="完全不同的文本", corrected="钢筋混凝土")
    applied = proc._apply_corrections(page, [corr])
    assert applied == 0
    assert page.elements[0].content == "钢盤混疑土强度等级"  # Unchanged


def test_apply_fix_out_of_range():
    proc = VLMReviewProcessor()
    page = _make_scanned_page(1, texts=["only one"])
    corr = Correction(type="fix_text", element_index=5, corrected="nope")
    applied = proc._apply_corrections(page, [corr])
    assert applied == 0


def test_apply_add_missing():
    proc = VLMReviewProcessor()
    page = _make_scanned_page(1, texts=["existing text"])
    corr = Correction(
        type="add_missing",
        content="专家评审组名单",
        content_type="heading",
        heading_level=2,
        insert_after_index=0,
    )
    applied = proc._apply_corrections(page, [corr])
    assert applied == 1
    assert len(page.elements) == 2
    new_elem = page.elements[1]
    assert new_elem.content == "专家评审组名单"
    assert new_elem.source == "vlm"
    assert new_elem.metadata["heading_level"] == 2
    assert new_elem.metadata["vlm_review_applied"] == "add_missing"


def test_apply_add_missing_at_beginning():
    proc = VLMReviewProcessor()
    page = _make_scanned_page(1, texts=["existing text"])
    corr = Correction(
        type="add_missing",
        content="标题",
        content_type="heading",
        heading_level=1,
        insert_after_index=-1,
    )
    applied = proc._apply_corrections(page, [corr])
    assert applied == 1
    assert page.elements[0].content == "标题"
    assert page.elements[1].content == "existing text"


def test_apply_add_missing_table():
    proc = VLMReviewProcessor()
    page = _make_scanned_page(1, texts=["before table"])
    corr = Correction(
        type="add_missing",
        content="| A | B |\n|---|---|\n| 1 | 2 |",
        content_type="table",
        insert_after_index=0,
    )
    applied = proc._apply_corrections(page, [corr])
    assert applied == 1
    assert page.elements[1].type == "table"


# ── Config disabled tests ─────────────────────────────────────────────────


def test_disabled_skips_processing():
    vlm = FakeVLMService()
    proc = VLMReviewProcessor(
        config=VLMReviewConfig(enabled=False),
        vlm_service=vlm,
        source_path=Path("/tmp/fake.pdf"),
    )
    doc = Document(pages=[_make_scanned_page(1)])
    result = proc.process(doc)
    assert vlm.calls == []
    assert result is doc


def test_no_vlm_service_skips():
    proc = VLMReviewProcessor(config=VLMReviewConfig(), vlm_service=None)
    doc = Document(pages=[_make_scanned_page(1)])
    result = proc.process(doc)
    assert result is doc


# ── End-to-end with mock VLM ─────────────────────────────────────────────


def test_e2e_fix_text_on_scanned_page(tmp_path: Path):
    """End-to-end test: VLM corrects OCR error on scanned page.

    Since we can't create a real PDF for rendering, we test by providing
    a source_path that doesn't exist — the processor should handle gracefully.
    """
    vlm = FakeVLMService([_fix_response(0, "混疑土", "混凝土")])
    proc = VLMReviewProcessor(
        config=VLMReviewConfig(),
        vlm_service=vlm,
        source_path=tmp_path / "nonexistent.pdf",  # Will fail to render
    )
    doc = Document(pages=[_make_scanned_page(1, texts=["混疑土强度"])])
    result = proc.process(doc)
    # Source path doesn't exist, so rendering fails gracefully.
    # No VLM calls should be made.
    assert vlm.calls == []
    assert result.pages[0].elements[0].content == "混疑土强度"  # Unchanged
