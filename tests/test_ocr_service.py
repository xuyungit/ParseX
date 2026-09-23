"""Tests for the PaddleOCR jobs API client."""

import json

import pytest

from parserx.config.schema import OCRBuilderConfig
from parserx.services import ocr as ocr_mod
from parserx.services.ocr import PaddleOCRService, _extract_bbox, _merge_jsonl_results


def test_extract_bbox_prefers_block_bbox():
    assert _extract_bbox({"block_bbox": [1, 2, 3, 4]}) == (1.0, 2.0, 3.0, 4.0)


def test_extract_bbox_falls_back_to_polygon():
    block = {"block_polygon_points": [[1, 2], [3, 2], [3, 4], [1, 4]]}
    assert _extract_bbox(block) == (1.0, 2.0, 3.0, 4.0)


def test_extract_bbox_missing_is_zero():
    assert _extract_bbox({}) == (0.0, 0.0, 0.0, 0.0)


def _page(text: str) -> dict:
    return {"prunedResult": {"width": 100, "height": 200, "parsing_res_list": [
        {"block_label": "text", "block_content": text, "block_bbox": [0, 0, 10, 10], "block_order": 1},
    ]}}


def _line(*texts: str) -> str:
    return json.dumps({"errorCode": 0, "result": {"layoutParsingResults": [_page(t) for t in texts]}})


class _Resp:
    def __init__(self, payload=None, text="", status_code=200):
        self._payload, self.text, self.status_code = payload, text, status_code
        self.ok = status_code < 400

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_merge_jsonl_results_concatenates_lines_in_order():
    merged = _merge_jsonl_results(_line("a", "b") + "\n\n" + _line("c") + "\n")
    texts = [p["prunedResult"]["parsing_res_list"][0]["block_content"] for p in merged["layoutParsingResults"]]
    assert texts == ["a", "b", "c"]


_DONE = {"state": "done", "resultUrl": {"jsonUrl": "https://x/result.jsonl"}}


def _service(monkeypatch, states, jsonl="", submits=()):
    polls = iter(states)
    rejections = iter(submits)
    posts = []

    def fake_post(url, **kw):
        posts.append(kw)
        return next(rejections, None) or _Resp({"code": 0, "data": {"jobId": "42"}})

    def fake_get(url, **kw):
        if url.endswith("/jobs/42"):
            return _Resp({"data": next(polls)})
        return _Resp(text=jsonl)

    monkeypatch.setattr(ocr_mod.requests, "post", fake_post)
    monkeypatch.setattr(ocr_mod.requests, "get", fake_get)
    monkeypatch.setattr(ocr_mod.time, "sleep", lambda s: None)
    svc = PaddleOCRService(OCRBuilderConfig(endpoint="https://x/api/v2/ocr/jobs", token="t"))
    return svc, posts


def test_submits_polls_and_parses_pages(monkeypatch):
    svc, posts = _service(monkeypatch, [{"state": "pending"}, _DONE], _line("p1", "p2") + "\n" + _line("p3"))

    results = svc.recognize_pdf(b"%PDF")

    assert [r.full_text for r in results] == ["p1", "p2", "p3"]
    assert results[0].render_width == 100
    assert posts[0]["headers"]["Authorization"] == "bearer t"
    assert json.loads(posts[0]["data"]["optionalPayload"])["useLayoutDetection"] is True


def test_failed_job_is_retried(monkeypatch):
    failed = {"state": "failed", "errorMsg": "boom"}
    svc, posts = _service(monkeypatch, [failed, _DONE], _line("ok"))

    assert [r.full_text for r in svc.recognize_pdf(b"%PDF")] == ["ok"]
    assert len(posts) == 2


def test_raises_after_retries_exhausted(monkeypatch):
    failed = {"state": "failed", "errorMsg": "boom"}
    svc, _ = _service(monkeypatch, [failed] * 3)

    with pytest.raises(RuntimeError, match="boom"):
        svc.recognize_pdf(b"%PDF")


def test_waits_out_queue_full(monkeypatch):
    busy = _Resp({"code": 10010, "msg": "queue full"}, status_code=400)
    svc, posts = _service(monkeypatch, [_DONE], _line("ok"), submits=[busy, busy])

    assert [r.full_text for r in svc.recognize_pdf(b"%PDF")] == ["ok"]
    assert len(posts) == 3
