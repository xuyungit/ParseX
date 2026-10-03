"""GLM-OCR as the scan engine (docs/v2_ocr_engines.md §8): its regions written as the PaddleOCR-VL engine's
results, the request, the key, and the choice of engine — offline, with a recorded-shape answer."""

import json

import pymupdf
import pytest

from parserx.config.schema import ModelProfile, OCRBuilderConfig, ParserXConfig
from parserx.content import scan
from parserx.scheduling import PageCountMismatch
from parserx.services import glm_ocr
from parserx.services.glm_ocr import GlmOcrService, glm_api_key, glm_pages, region_text
from parserx.services.ocr import PaddleOCRService, new_scan_engine


def _region(n, label, content, box):
    return {"index": n, "label": "text", "native_label": label, "bbox_2d": list(box), "content": content,
            "width": 1000, "height": 1400}


ANSWER = {"md_results": "…", "usage": {"total_tokens": 10}, "layout_details": [[
    _region(0, "doc_title", '<div align="center">\n\n# 2 标题\n\n</div>', (10, 20, 500, 60)),
    _region(1, "text", "正文 $2.5\\mathrm{mL}$，见 ![](page=0,bbox=[30, 40, 80, 90])", (10, 80, 500, 180)),
    _region(2, "image", "![](page=0,bbox=[100, 200, 300, 400])", (100, 200, 300, 400)),
    _region(3, "display_formula", "$$\n\\frac {1 2}{4 8} = x _ {1 1}\n$$", (10, 420, 500, 480)),
    _region(4, "table", "<table><tr><td>甲</td><td>1 000</td></tr></table>", (10, 500, 500, 600)),
]]}


def test_regions_become_the_scan_engines_entries():
    [page] = glm_pages(ANSWER)
    pruned = page["prunedResult"]
    assert (pruned["width"], pruned["height"]) == (1000, 1400)
    entries = pruned["parsing_res_list"]
    assert [e["block_label"] for e in entries] == ["doc_title", "text", "image", "display_formula", "table"]
    assert [e["block_order"] for e in entries] == [1, 2, None, 4, 5]  # a picture is placed by position
    assert entries[0]["block_content"] == "2 标题"
    assert entries[1]["block_content"] == "正文 $2.5\\mathrm{mL}$，见 ![](imgs/img_in_image_box_30_40_80_90.jpg)"
    assert entries[2]["block_content"] == ""  # GLM-OCR does not read inside pictures
    assert entries[3]["block_content"] == "$$ \\frac {12}{48} = x _ {11} $$"
    assert entries[4]["block_content"] == "<table><tr><td>甲</td><td>1 000</td></tr></table>"  # text: as given
    assert entries[2]["block_bbox"] == [100.0, 200.0, 300.0, 400.0]


def test_the_scan_reader_takes_the_converted_page():
    [page] = glm_pages(ANSWER)
    result = scan.page_blocks(scan.PageScan(page=1, raw=page, raw_ref="k", engine_version="glm-ocr"),
                              page_size=(500.0, 700.0), first_seq=1, first_item=1)
    kinds = [b.kind.value for b in result.blocks]
    assert kinds == ["title", "text", "figure", "formula", "table"]
    assert result.blocks[4].cells is not None
    assert result.blocks[0].observations[0].engine_version == "glm-ocr"
    assert result.blocks[0].anchors[0].bbox == (5.0, 10.0, 250.0, 30.0)  # 1000 px → 500 pt


def test_digits_are_joined_only_inside_math():
    assert region_text("text", "共 1 000 元，$1 2$ 与 $a b$") == "共 1 000 元，$12$ 与 $a b$"


def _pdf(pages: int) -> bytes:
    doc = pymupdf.open()
    for _ in range(pages):
        doc.new_page()
    return doc.tobytes()


class _Response:
    def __init__(self, status: int, body: dict):
        self.status_code, self._body, self.text = status, body, json.dumps(body)
        self.ok = status < 400

    def json(self):
        return self._body


def test_one_request_per_pdf_and_the_pages_must_all_be_there(monkeypatch):
    sent = []

    def post(url, json, headers, timeout):
        sent.append((url, json["model"], json["file"][:28], headers["Authorization"]))
        return _Response(200, ANSWER)

    monkeypatch.setattr(glm_ocr.requests, "post", post)
    service = GlmOcrService(OCRBuilderConfig().glm, "k")
    results = service.recognize_pdf(_pdf(1))
    assert len(results) == 1 and results[0].raw["layoutParsingResults"][0]["prunedResult"]["width"] == 1000
    assert sent == [("https://open.bigmodel.cn/api/paas/v4/layout_parsing", "glm-ocr",
                     "data:application/pdf;base64,", "Bearer k")]
    with pytest.raises(PageCountMismatch):
        service.recognize_pdf(_pdf(2))  # an answer with one page for two


def test_a_busy_service_is_a_transient_failure(monkeypatch):
    from parserx.scheduling import TransientError

    monkeypatch.setattr(glm_ocr.requests, "post", lambda *a, **k: _Response(500, {"error": {"code": "1234"}}))
    with pytest.raises(TransientError):
        GlmOcrService(OCRBuilderConfig().glm, "k")._ask(b"%PDF", "application/pdf", 0)


def test_the_key_comes_from_the_same_accounts_models_entry():
    config = ParserXConfig(models={"glm-5.3-flashx": ModelProfile(api_key="zhipu")})
    assert glm_api_key(config) == "zhipu"
    config.builders.ocr.glm.api_key = "own"
    assert glm_api_key(config) == "own"


def test_the_engine_named_in_the_configuration_is_made():
    config = ParserXConfig(models={"glm-5.3-flashx": ModelProfile(api_key="zhipu")})
    config.builders.ocr.endpoint, config.builders.ocr.token = "https://x/api/v2/ocr/jobs", "t"
    assert isinstance(new_scan_engine(config), PaddleOCRService)
    config.builders.ocr.engine = "glm-ocr"
    engine = new_scan_engine(config)
    assert isinstance(engine, GlmOcrService) and engine.model == "glm-ocr"
    config.builders.ocr.engine = "none"
    with pytest.raises(ValueError):
        new_scan_engine(config)
