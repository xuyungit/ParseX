"""Response cache (guide §8.3) behind the service gateway."""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pymupdf
import pytest

from parserx.cache import CacheMiss, ResponseCache, open_cache, service_identity
from parserx.config.schema import CacheConfig, OCRBuilderConfig, ServiceConfig
from parserx.scheduling import MeteredService, RequestMeter, ServiceGateway
from parserx.services.ocr import PaddleOCRService


class _FakeVLM:
    def __init__(self):
        self.calls = 0

    def describe_image(self, image_path, prompt, *, context="", max_tokens=8192, json_schema=None):
        self.calls += 1
        return f"answer {self.calls}"


def _vlm(tmp_path, cache, meter=None, model="m1"):
    identity = service_identity(ServiceConfig(endpoint="https://api.example.com/v1", model=model, api_key="k"))
    inner = _FakeVLM()
    return inner, MeteredService(inner, meter or RequestMeter(), "vlm", cache=cache, identity=identity)


def _image(path: Path, payload: bytes) -> Path:
    path.write_bytes(payload)
    return path


def test_hit_replays_response_without_a_request(tmp_path):
    meter = RequestMeter()
    cache = ResponseCache(tmp_path / "c", "read_write")
    inner, vlm = _vlm(tmp_path, cache, meter)
    img = _image(tmp_path / "a.png", b"pixels")

    first = vlm.describe_image(img, "describe")
    second = vlm.describe_image(img, "describe")

    assert first == second == "answer 1"
    assert inner.calls == 1
    snap = meter.snapshot()
    assert snap.requests == {"vlm": 1} and snap.cache_hits == {"vlm": 1}


def test_key_follows_request_semantics_not_file_paths(tmp_path):
    cache = ResponseCache(tmp_path / "c", "read_write")
    inner, vlm = _vlm(tmp_path, cache)
    a = _image(tmp_path / "tmp_1.png", b"same bytes")
    b = _image(tmp_path / "tmp_2.png", b"same bytes")
    c = _image(tmp_path / "tmp_3.png", b"other bytes")

    vlm.describe_image(a, "describe")
    vlm.describe_image(b, "describe")  # temp path differs, bytes equal: hit
    assert inner.calls == 1
    vlm.describe_image(c, "describe")  # image bytes
    vlm.describe_image(a, "describe more")  # prompt
    vlm.describe_image(a, "describe", json_schema={"type": "object"})  # schema
    assert inner.calls == 4

    _, other_model = _vlm(tmp_path, cache, model="m2")
    other_model.describe_image(a, "describe")
    assert other_model._inner.calls == 1  # model is part of the key


def test_identity_ignores_credentials():
    base = ServiceConfig(endpoint="https://api.example.com/v1", model="m", api_key="k1")
    assert service_identity(base) == service_identity(base.model_copy(update={"api_key": "k2"}))
    assert service_identity(base) != service_identity(base.model_copy(update={"reasoning_effort": "low"}))


def test_read_only_miss_raises_and_is_recorded(tmp_path):
    meter = RequestMeter()
    inner, vlm = _vlm(tmp_path, ResponseCache(tmp_path / "c", "read_only"), meter)
    with pytest.raises(CacheMiss):
        vlm.describe_image(_image(tmp_path / "a.png", b"x"), "describe")
    assert inner.calls == 0
    assert meter.snapshot().cache_misses == {"vlm": 1}
    assert meter.snapshot().requests == {}


def test_refresh_mode_ignores_existing_entries_but_writes(tmp_path):
    root = tmp_path / "c"
    img = _image(tmp_path / "a.png", b"x")
    _, warm = _vlm(tmp_path, ResponseCache(root, "read_write"))
    warm.describe_image(img, "describe")

    inner, refresh = _vlm(tmp_path, ResponseCache(root, "refresh"))
    assert refresh.describe_image(img, "describe") == "answer 1"
    assert inner.calls == 1
    _, replay = _vlm(tmp_path, ResponseCache(root, "read_only"))
    assert replay.describe_image(img, "describe") == "answer 1"


def test_off_mode_has_no_cache(tmp_path):
    assert open_cache(CacheConfig(mode="off", dir=str(tmp_path))) is None


def test_concurrent_writes_leave_valid_entries(tmp_path):
    cache = ResponseCache(tmp_path / "c", "read_write")
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(lambda i: cache.put("llm", "k" * 64, f"value {i}", {"i": i}), range(50)))
    files = list((tmp_path / "c").rglob("*.json"))
    assert len(files) == 1
    assert json.loads(files[0].read_text())["response"].startswith("value ")


# ── OCR: raw response cached at the single transport exit ───────────────


def _pdf(pages: int) -> bytes:
    doc = pymupdf.open()
    for _ in range(pages):
        doc.new_page()
    return doc.tobytes(no_new_id=True)


def _ocr_page(text: str) -> dict:
    return {"prunedResult": {"width": 100, "height": 100, "parsing_res_list": [
        {"block_content": text, "block_label": "text", "block_bbox": [0, 0, 10, 10], "block_order": 1},
    ]}}


def test_ocr_caches_raw_result_and_counts_pages_once(tmp_path, monkeypatch):
    meter = RequestMeter()
    ocr = PaddleOCRService(OCRBuilderConfig(endpoint="https://ocr.example.com/jobs", token="t"))
    ocr.gateway = ServiceGateway(meter, ResponseCache(tmp_path / "c", "read_write"))
    jobs = []

    def fake_job(file_bytes, filename, mime, job_key=None):
        jobs.append(filename)
        return {"layoutParsingResults": [_ocr_page("一"), _ocr_page("二"), _ocr_page("三")]}

    monkeypatch.setattr(ocr, "_run_job", fake_job)
    pdf = _pdf(3)
    first = ocr.recognize_pdf(pdf)
    second = ocr.recognize_pdf(pdf)

    assert len(jobs) == 1
    assert [r.full_text for r in first] == [r.full_text for r in second] == ["一", "二", "三"]
    snap = meter.snapshot()
    assert snap.requests == {"ocr": 1} and snap.pages == {"ocr": 3} and snap.cache_hits == {"ocr": 1}
