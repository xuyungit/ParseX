"""Tests for ImageProcessor."""

from pathlib import Path

from PIL import Image, ImageDraw

from parserx.config.schema import ImageProcessorConfig
from parserx.models.elements import Document, Page, PageElement
from parserx.processors.image import (
    ImageClassification,
    ImageProcessor,
    _build_route_hint,
    _build_vlm_system_prompt,
    _detect_prompt_language,
    _extract_json_object,
    _looks_like_truncated_json,
    classify_image_element,
)


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


class FakeVLMService:
    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls: list[tuple[Path, str, str]] = []

    def describe_image(
        self,
        image_path: Path,
        prompt: str,
        *,
        context: str = "",
        temperature: float = 0.1,
        max_tokens: int = 8192,
        structured_output_mode: str = "off",
        json_schema: dict | None = None,
        json_schema_name: str = "parserx_image_description",
    ) -> str:
        self.calls.append((image_path, prompt, context))
        if not self._responses:
            return ""
        return self._responses.pop(0)


def _write_test_image(path: Path) -> Path:
    image = Image.new("RGB", (320, 240), color="white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((40, 40, 280, 180), outline="black", width=4)
    draw.line((40, 180, 280, 40), fill="black", width=3)
    image.save(path, format="PNG")
    return path


def test_vlm_json_output_prefers_summary_for_informational(tmp_path: Path):
    image_path = _write_test_image(tmp_path / "diagram.png")
    elem = _img_elem(400, 300)
    elem.metadata["saved_abs_path"] = str(image_path)
    doc = Document(pages=[Page(number=1, elements=[elem])])
    vlm = FakeVLMService([
        '{"image_type":"diagram","summary":"A flow diagram with two connected boxes.","visible_text":"Input\\nOutput","markdown":""}'
    ])
    config = ImageProcessorConfig(
        vlm_description=True,
        vlm_response_format="json",
        vlm_prompt_style="strict_bilingual",
    )

    ImageProcessor(config=config, vlm_service=vlm).process(doc)

    assert elem.metadata["description"] == "A flow diagram with two connected boxes."
    assert elem.metadata["vlm_image_type"] == "diagram"
    assert len(vlm.calls) == 1


def test_vlm_json_corrects_ocr_and_keeps_independent_summary(tmp_path: Path):
    """VLM correction path: visible_text replaces OCR, independent summary kept."""
    image_path = _write_test_image(tmp_path / "text-heavy.png")
    elem = _img_elem(400, 300)
    elem.page_number = 1
    elem.metadata["saved_abs_path"] = str(image_path)
    ocr_text = PageElement(
        type="text",
        page_number=1,
        bbox=(10.0, 10.0, 390.0, 290.0),
        content="项目名称\n采购金额 100 万元\n联系人 张三",
        source="ocr",
    )
    doc = Document(pages=[Page(number=1, elements=[elem, ocr_text])])
    vlm = FakeVLMService([
        '{"image_type":"diagram","summary":"A procurement notice card with key facts.","visible_text":"项目名称\\n采购金额 100 万元\\n联系人 张三","markdown":""}'
    ])
    config = ImageProcessorConfig(vlm_description=True, vlm_response_format="json")

    ImageProcessor(config=config, vlm_service=vlm).process(doc)

    # VLM visible_text stored as corrected text
    assert elem.metadata.get("vlm_corrected_text")
    # OCR element suppressed
    assert ocr_text.metadata.get("skip_render") is True
    # Summary is independent (English vs Chinese) → kept as description
    assert "procurement" in str(elem.metadata.get("description", ""))


def test_vlm_json_falls_back_to_overlap_evidence_on_number_mismatch(tmp_path: Path):
    image_path = _write_test_image(tmp_path / "number-mismatch.png")
    elem = _img_elem(400, 300)
    elem.page_number = 1
    elem.metadata["saved_abs_path"] = str(image_path)
    ocr_text = PageElement(
        type="text",
        page_number=1,
        bbox=(20.0, 20.0, 380.0, 280.0),
        content="采购金额为 100 万元。",
        source="ocr",
    )
    doc = Document(pages=[Page(number=1, elements=[elem, ocr_text])])
    vlm = FakeVLMService([
        '{"image_type":"diagram","summary":"采购金额为 999 万元。","visible_text":"","markdown":""}'
    ])
    config = ImageProcessorConfig(vlm_description=True, vlm_response_format="json")

    ImageProcessor(config=config, vlm_service=vlm).process(doc)

    assert elem.metadata["description"] == "采购金额为 100 万元。"
    assert elem.metadata["description_source"] == "ocr_overlap_evidence"
    assert elem.metadata["vlm_number_mismatch"] is True
    assert elem.metadata["vlm_summary_suppressed"] is True


def test_vlm_json_output_prefers_markdown_table(tmp_path: Path):
    """VLM markdown table goes to vlm_corrected_content for body rendering."""
    image_path = _write_test_image(tmp_path / "table.png")
    elem = _img_elem(400, 300)
    elem.metadata["saved_abs_path"] = str(image_path)
    elem.layout_type = "table"
    doc = Document(pages=[Page(number=1, elements=[elem])])
    vlm = FakeVLMService([
        '{"image_type":"table","summary":"Table content","visible_text":"A B","markdown":"| A | B |\\n| --- | --- |\\n| 1 | 2 |"}'
    ])
    config = ImageProcessorConfig(vlm_description=True, vlm_response_format="json")

    ImageProcessor(config=config, vlm_service=vlm).process(doc)

    # Markdown table goes to corrected_content (rendered as body text)
    assert elem.metadata.get("vlm_corrected_table", "").startswith("| A | B |")


def test_vlm_retries_when_output_is_not_json(tmp_path: Path):
    image_path = _write_test_image(tmp_path / "retry.png")
    elem = _img_elem(400, 300)
    elem.metadata["saved_abs_path"] = str(image_path)
    doc = Document(pages=[Page(number=1, elements=[elem])])
    vlm = FakeVLMService([
        "not-json",
        '{"image_type":"diagram","summary":"Recovered structured description.","visible_text":"","markdown":""}',
    ])
    config = ImageProcessorConfig(
        vlm_description=True,
        vlm_response_format="json",
        vlm_retry_attempts=1,
    )

    ImageProcessor(config=config, vlm_service=vlm).process(doc)

    assert elem.metadata["description"] == "Recovered structured description."
    assert elem.metadata["vlm_retry_used"] is True
    assert len(vlm.calls) == 2


def test_vlm_skips_long_text_overlap_before_call(tmp_path: Path):
    image_path = _write_test_image(tmp_path / "skip-long-text.png")
    elem = _img_elem(400, 300)
    elem.page_number = 1
    elem.metadata["saved_abs_path"] = str(image_path)
    long_text = "科研结果 " * 400
    ocr_text = PageElement(
        type="text",
        page_number=1,
        bbox=(10.0, 10.0, 390.0, 290.0),
        content=long_text,
        source="ocr",
    )
    doc = Document(pages=[Page(number=1, elements=[elem, ocr_text])])
    vlm = FakeVLMService([])
    config = ImageProcessorConfig(
        vlm_description=True,
        vlm_response_format="json",
        vlm_skip_large_text_overlap_chars=200,
    )

    ImageProcessor(config=config, vlm_service=vlm).process(doc)

    assert elem.metadata["description"] == long_text[:1199].rstrip() + "…"
    assert elem.metadata["description_source"] == "ocr_overlap_evidence"
    assert elem.metadata["vlm_skipped_due_to_large_text_overlap"] is True
    assert len(vlm.calls) == 0


def test_vlm_truncated_json_falls_back_to_overlap_evidence(tmp_path: Path):
    image_path = _write_test_image(tmp_path / "truncated-json.png")
    elem = _img_elem(400, 300)
    elem.page_number = 1
    elem.metadata["saved_abs_path"] = str(image_path)
    ocr_text = PageElement(
        type="text",
        page_number=1,
        bbox=(10.0, 10.0, 390.0, 290.0),
        content="项目名称\n采购金额 100 万元\n联系人 张三",
        source="ocr",
    )
    doc = Document(pages=[Page(number=1, elements=[elem, ocr_text])])
    vlm = FakeVLMService([
        '{"image_type":"text","summary":"","visible_text":"项目名称\\n采购金额 100 万元\\n联系人 张三"'
    ])
    config = ImageProcessorConfig(
        vlm_description=True,
        vlm_response_format="json",
        vlm_retry_attempts=0,
    )

    ImageProcessor(config=config, vlm_service=vlm).process(doc)

    assert elem.metadata["description"] == "项目名称\n采购金额 100 万元\n联系人 张三"
    assert elem.metadata["description_source"] == "ocr_overlap_evidence"
    assert elem.metadata["vlm_unstructured_output"] is True
    assert elem.metadata["vlm_unstructured_reason"] == "truncated_json"
    assert "项目名称" in elem.metadata["vlm_raw_excerpt"]


def test_extract_json_object_avoids_greedy_brace_capture():
    raw = 'prefix {"summary":"first"} middle {"summary":"second"} suffix'

    parsed = _extract_json_object(raw)

    assert parsed == {"summary": "first"}


def test_looks_like_truncated_json_detects_missing_closing_brace():
    raw = '{"image_type":"text","summary":"","visible_text":"abc"'

    assert _looks_like_truncated_json(raw) is True


def test_detect_prompt_language_prefers_chinese_when_context_is_cjk_heavy():
    detected = _detect_prompt_language("采购金额", "项目名称\n联系人\n采购金额 100 万元")

    assert detected == "zh"


def test_build_vlm_system_prompt_auto_selects_english_policy():
    prompt = _build_vlm_system_prompt(
        "strict_auto",
        preferred_language="en",
    )

    assert "Only describe content that is clearly visible" in prompt
    assert "visible_text: transcribe ALL readable text" in prompt


def test_build_route_hint_prefers_text_heavy_mode():
    elem = _img_elem(400, 300)
    elem.metadata["image_class"] = ImageClassification.INFORMATIONAL

    route_hint = _build_route_hint(
        elem=elem,
        evidence={"text": "Project Name\nBudget 100 USD\nOwner Alice", "table_text": "", "best_overlap": 0.7},
        visible_text_hint="Project Name\nBudget 100 USD\nOwner Alice",
    )

    assert route_hint.startswith("text-heavy:")


# ── VLM correction routing tests ──────────────────────────────────────


def test_vlm_corrects_ocr_text(tmp_path: Path):
    """VLM visible_text supersedes overlapping OCR text (suppressed, not mutated)."""
    image_path = _write_test_image(tmp_path / "scan.png")
    elem = _img_elem(400, 300)
    elem.page_number = 1
    elem.metadata["saved_abs_path"] = str(image_path)

    ocr_text = PageElement(
        type="text", page_number=1,
        bbox=(10.0, 10.0, 390.0, 290.0),
        content="采购金额 1OO 万元，联系人 张三",  # OCR error: 1OO
        source="ocr",
    )
    doc = Document(pages=[Page(number=1, elements=[elem, ocr_text])])
    vlm = FakeVLMService([
        '{"image_type":"text","summary":"","visible_text":"采购金额 100 万元，联系人 张三","markdown":""}'
    ])
    config = ImageProcessorConfig(
        vlm_description=True, vlm_response_format="json",
        vlm_correction_mode=True,
    )

    ImageProcessor(config=config, vlm_service=vlm).process(doc)

    # OCR element suppressed (not mutated)
    assert ocr_text.metadata.get("skip_render") is True
    # VLM content stored as corrected content on the image
    assert "100 万元" in elem.metadata.get("vlm_corrected_text", "")


def test_vlm_corrects_ocr_table(tmp_path: Path):
    """VLM markdown supersedes overlapping OCR table."""
    image_path = _write_test_image(tmp_path / "table.png")
    elem = _img_elem(400, 300)
    elem.page_number = 1
    elem.metadata["saved_abs_path"] = str(image_path)

    ocr_table = PageElement(
        type="table", page_number=1,
        bbox=(10.0, 10.0, 390.0, 290.0),
        content="| Itern | Qty |\n|---|---|\n| Bolt | 100 |",  # OCR error: Itern
        source="ocr",
    )
    doc = Document(pages=[Page(number=1, elements=[elem, ocr_table])])
    vlm = FakeVLMService([
        '{"image_type":"table","summary":"","visible_text":"","markdown":"| Item | Qty |\\n|---|---|\\n| Bolt | 100 |"}'
    ])
    config = ImageProcessorConfig(
        vlm_description=True, vlm_response_format="json",
        vlm_correction_mode=True,
    )

    ImageProcessor(config=config, vlm_service=vlm).process(doc)

    # OCR table suppressed
    assert ocr_table.metadata.get("skip_render") is True
    # VLM table stored as corrected content
    assert "Item" in elem.metadata.get("vlm_corrected_table", "")


def test_vlm_chart_summary_kept_as_description(tmp_path: Path):
    """Chart/diagram images should keep summary as description."""
    image_path = _write_test_image(tmp_path / "chart.png")
    elem = _img_elem(400, 300)
    elem.page_number = 1
    elem.metadata["saved_abs_path"] = str(image_path)

    doc = Document(pages=[Page(number=1, elements=[elem])])
    vlm = FakeVLMService([
        '{"image_type":"chart","summary":"A bar chart showing quarterly revenue growth.","visible_text":"Q1 Q2 Q3 Q4","markdown":""}'
    ])
    config = ImageProcessorConfig(
        vlm_description=True, vlm_response_format="json",
        vlm_correction_mode=True,
    )

    ImageProcessor(config=config, vlm_service=vlm).process(doc)

    # No OCR overlap → correction doesn't fire → falls through to description
    assert elem.metadata.get("description")
    assert elem.metadata.get("skipped") is not True


def test_vlm_text_correction_plus_independent_summary(tmp_path: Path):
    """When VLM corrects text but summary has independent info, both apply."""
    image_path = _write_test_image(tmp_path / "mixed.png")
    elem = _img_elem(400, 300)
    elem.page_number = 1
    elem.metadata["saved_abs_path"] = str(image_path)

    ocr_text = PageElement(
        type="text", page_number=1,
        bbox=(10.0, 10.0, 390.0, 290.0),
        content="采购项目一期 总预算 5OO 万",  # OCR error
        source="ocr",
    )
    doc = Document(pages=[Page(number=1, elements=[elem, ocr_text])])
    vlm = FakeVLMService([
        '{"image_type":"text","summary":"The image also contains a workflow diagram showing the procurement approval process.","visible_text":"采购项目一期 总预算 500 万","markdown":""}'
    ])
    config = ImageProcessorConfig(
        vlm_description=True, vlm_response_format="json",
        vlm_correction_mode=True,
    )

    ImageProcessor(config=config, vlm_service=vlm).process(doc)

    # OCR suppressed, VLM content stored
    assert ocr_text.metadata.get("skip_render") is True
    assert "500 万" in elem.metadata.get("vlm_corrected_text", "")
    # Summary is independent (English description of visual layout) → kept
    assert "workflow diagram" in str(elem.metadata.get("description", ""))


def test_vlm_correction_trusts_vlm_numbers(tmp_path: Path):
    """VLM is authoritative — even different numbers from OCR are accepted."""
    image_path = _write_test_image(tmp_path / "table.png")
    elem = _img_elem(400, 300)
    elem.page_number = 1
    elem.metadata["saved_abs_path"] = str(image_path)

    ocr_table = PageElement(
        type="table", page_number=1,
        bbox=(10.0, 10.0, 390.0, 290.0),
        content="| Item | Price |\n|---|---|\n| Widget | 99.5 |",
        source="ocr",
    )
    doc = Document(pages=[Page(number=1, elements=[elem, ocr_table])])
    # VLM sees the image and determines the correct price is 88.3
    vlm = FakeVLMService([
        '{"image_type":"table","summary":"","visible_text":"","markdown":"| Item | Price |\\n|---|---|\\n| Widget | 88.3 |"}'
    ])
    config = ImageProcessorConfig(
        vlm_description=True, vlm_response_format="json",
        vlm_correction_mode=True,
    )

    ImageProcessor(config=config, vlm_service=vlm).process(doc)

    # VLM is authoritative — its content is used, OCR suppressed
    assert ocr_table.metadata.get("skip_render") is True
    assert "88.3" in elem.metadata.get("vlm_corrected_table", "")


def test_correction_disabled_by_config(tmp_path: Path):
    """When vlm_correction_mode is off, fall through to description path."""
    image_path = _write_test_image(tmp_path / "text.png")
    elem = _img_elem(400, 300)
    elem.page_number = 1
    elem.metadata["saved_abs_path"] = str(image_path)

    ocr_text = PageElement(
        type="text", page_number=1,
        bbox=(10.0, 10.0, 390.0, 290.0),
        content="采购金额 1OO 万元",
        source="ocr",
    )
    doc = Document(pages=[Page(number=1, elements=[elem, ocr_text])])
    vlm = FakeVLMService([
        '{"image_type":"text","summary":"","visible_text":"采购金额 100 万元","markdown":""}'
    ])
    config = ImageProcessorConfig(
        vlm_description=True, vlm_response_format="json",
        vlm_correction_mode=False,  # Disabled
    )

    ImageProcessor(config=config, vlm_service=vlm).process(doc)

    # OCR should NOT be suppressed
    assert ocr_text.metadata.get("skip_render") is not True
    # Image should have a description via the fallback path
    assert elem.metadata.get("description")
