"""Real request counting at the service boundary (guide §9.2 item 5)."""

from concurrent.futures import ThreadPoolExecutor

import fitz

from parserx.config.schema import OCRBuilderConfig
from parserx.scheduling import MeteredService, RequestMeter, ServiceGateway
from parserx.services.ocr import PaddleOCRService


class _FakeLLM:
    model = "fake"

    def __init__(self):
        self.attempt_hook = None

    def complete(self, system, user, **kw):
        if self.attempt_hook:
            self.attempt_hook()
            self.attempt_hook()  # e.g. one parameter-downgrade retry
        return "ok"


def _pdf_bytes(pages: int) -> bytes:
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page()
    return doc.tobytes()


def test_counts_requests_and_attempts():
    meter = RequestMeter()
    llm = MeteredService(_FakeLLM(), meter, "llm")
    assert llm.complete("s", "u") == "ok"
    snap = meter.snapshot()
    assert snap.requests == {"llm": 1}
    assert snap.attempts == {"llm": 2}


def test_batch_ocr_is_one_request_with_many_pages(monkeypatch):
    meter = RequestMeter()
    ocr = PaddleOCRService(OCRBuilderConfig(endpoint="https://ocr.example.com/jobs", token="t"))
    ocr.gateway = ServiceGateway(meter)
    monkeypatch.setattr(ocr, "_run_job", lambda *a: {"layoutParsingResults": []})
    ocr.recognize_pdf(_pdf_bytes(3))
    snap = meter.snapshot()
    assert snap.requests == {"ocr": 1}
    assert snap.pages == {"ocr": 3}


def test_other_attributes_pass_through():
    llm = MeteredService(_FakeLLM(), RequestMeter(), "llm")
    assert llm.model == "fake"


def test_concurrent_requests_are_all_counted():
    meter = RequestMeter()
    llm = MeteredService(_FakeLLM(), meter, "llm")
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(lambda _: llm.complete("s", "u"), range(200)))
    assert meter.snapshot().requests == {"llm": 200}


def test_reset_clears_counts():
    meter = RequestMeter()
    MeteredService(_FakeLLM(), meter, "llm").complete("s", "u")
    meter.reset()
    assert meter.snapshot().requests == {}
