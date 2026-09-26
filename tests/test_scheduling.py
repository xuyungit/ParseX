"""Scheduling layer (guide §8.2): budgets, cost, ordered results, retry classification, OCR job resume."""

import json
import threading

import pymupdf
import httpx2
import openai
import pytest
import requests

import parserx.services.ocr as ocr_mod
from parserx.cache import CacheMiss, ResponseCache
from parserx.config.schema import OCRBuilderConfig
from parserx.scheduling import (
    Budget,
    BudgetExhausted,
    BudgetLimits,
    JobStore,
    MeteredService,
    PageCountMismatch,
    Price,
    PriceTable,
    RequestMeter,
    RetryPolicy,
    ServiceGateway,
    TransientError,
    UnparseableResponse,
    is_retryable,
    run_ordered,
)
from parserx.services.ocr import PaddleOCRService

NO_WAIT = RetryPolicy(max_attempts=3, backoff_s=0.0)


class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def _gateway(meter=None, **kw) -> ServiceGateway:
    return ServiceGateway(meter or RequestMeter(), kw.pop("cache", None), retry=kw.pop("retry", NO_WAIT), **kw)


# ── Budget ──────────────────────────────────────────────────────────────


def test_request_cap_skips_the_task_beyond_it():
    meter = RequestMeter()
    gw = _gateway(meter, budget=Budget(BudgetLimits(requests={"vlm": 1})))
    assert gw.call("vlm", {"q": 1}, lambda: "a") == "a"
    with pytest.raises(BudgetExhausted):
        gw.call("vlm", {"q": 2}, lambda: "b")
    snap = meter.snapshot()
    assert snap.requests == {"vlm": 1} and snap.skipped_budget == {"vlm": 1}


def test_cache_hits_do_not_consume_budget(tmp_path):
    cache = ResponseCache(tmp_path)
    gw = _gateway(cache=cache, budget=Budget(BudgetLimits(requests={"vlm": 1})))
    gw.call("vlm", {"q": 1}, lambda: "a")
    assert gw.call("vlm", {"q": 1}, lambda: "never") == "a"  # hit, still within budget


def test_deadline_ends_new_requests():
    clock = _Clock()
    budget = Budget(BudgetLimits(deadline_s=10), clock=clock)
    gw = _gateway(budget=budget)
    gw.call("ocr", {"p": 1}, lambda: {})
    clock.now = 11
    with pytest.raises(BudgetExhausted, match="deadline"):
        gw.call("ocr", {"p": 2}, lambda: {})
    assert budget.left()["seconds"] == 0


def test_cost_is_reserved_before_and_settled_after():
    budget = Budget(BudgetLimits(usd=0.015, reserve_usd={"vlm": 0.01}))
    first = budget.reserve("vlm")
    with pytest.raises(BudgetExhausted, match="usd"):
        budget.reserve("vlm")  # 0.01 in flight + 0.01 would exceed 0.015
    budget.settle(first, usd=0.001)
    second = budget.reserve("vlm")  # 0.001 spent + 0.01 reserved fits
    budget.settle(second, usd=0.002)
    assert budget.left()["usd"] == pytest.approx(0.012)


def test_reset_starts_a_new_document():
    clock = _Clock()
    budget = Budget(BudgetLimits(deadline_s=5, requests={"ocr": 1}), clock=clock)
    budget.settle(budget.reserve("ocr"), usd=None)
    clock.now = 6
    budget.reset()
    budget.settle(budget.reserve("ocr"), usd=None)


# ── Tokens and cost ─────────────────────────────────────────────────────


def test_price_table_counts_cached_input_at_its_own_rate():
    table = PriceTable({"m": Price(input=0.10, cached_input=0.01, output=0.50)})
    # 1M input of which 400k cached, 100k output
    assert table.cost("m", input_tokens=1_000_000, cached_input_tokens=400_000, output_tokens=100_000) == \
        pytest.approx(0.06 + 0.004 + 0.05)
    assert table.cost("unknown", input_tokens=1, cached_input_tokens=0, output_tokens=1) is None


class _FakeVLM:
    def __init__(self, model="m"):
        self.model = model
        self.attempt_hook = None
        self.usage_hook = None

    def describe_image(self, image_path, prompt, **kw):
        if self.attempt_hook:
            self.attempt_hook()
        self.usage_hook(self.model, 1000, 200, 100)
        return "text"


def test_usage_and_cost_are_metered_per_request():
    meter = RequestMeter()
    gw = _gateway(meter, prices=PriceTable({"m": Price(input=1.0, cached_input=0.1, output=10.0)}))
    vlm = MeteredService(_FakeVLM(), meter, "vlm", gateway=gw)
    vlm.describe_image("img", "p1")
    vlm.describe_image("img", "p2")
    snap = meter.snapshot()
    assert snap.tokens == {"vlm": {"input": 2000, "cached_input": 400, "output": 200}}
    assert snap.cost_usd == pytest.approx(2 * (800 * 1.0 + 200 * 0.1 + 100 * 10.0) / 1e6)
    assert snap.attempts == {"vlm": 2}


def test_unpriced_model_makes_cost_unknown():
    meter = RequestMeter()
    vlm = MeteredService(_FakeVLM("mystery"), meter, "vlm", gateway=_gateway(meter, prices=PriceTable({})))
    vlm.describe_image("img", "p")
    assert meter.snapshot().cost_usd is None


# ── run_ordered ─────────────────────────────────────────────────────────


def test_results_apply_in_task_order_whatever_the_completion_order():
    n = 6
    done = [threading.Event() for _ in range(n)]
    finished, applied = [], []

    def fetch(i):
        if i + 1 < n:
            done[i + 1].wait(5)  # task i finishes only after task i+1: completion order is reversed
        finished.append(i)
        done[i].set()
        return i * 10

    outcomes = run_ordered(list(range(n)), fetch, lambda task, value: applied.append((task, value)), max_workers=n)
    assert finished == list(reversed(range(n)))
    assert applied == [(i, i * 10) for i in range(n)]
    assert [o.status for o in outcomes] == ["ok"] * n


def test_failures_are_outcomes_not_applied():
    def fetch(task):
        if task == "budget":
            raise BudgetExhausted("vlm", "requests")
        if task == "offline":
            raise CacheMiss("vlm", "k" * 12)
        if task == "boom":
            raise TransientError("503")
        return task

    applied = []
    outcomes = run_ordered(["a", "budget", "offline", "boom", "b"], fetch,
                           lambda t, v: applied.append(v), max_workers=3)
    assert [o.status for o in outcomes] == ["ok", "skipped_budget", "cache_miss", "failed", "ok"]
    assert outcomes[3].retryable and "503" in outcomes[3].error
    assert applied == ["a", "b"]


# ── Retry classification ────────────────────────────────────────────────


def _status_error(cls, status):
    request = httpx2.Request("POST", "https://api.example/v1/responses")
    return cls("err", response=httpx2.Response(status, request=request), body=None)


def _http_error(status):
    response = requests.Response()
    response.status_code = status
    return requests.HTTPError(response=response)


@pytest.mark.parametrize("exc, retryable", [
    (openai.APIConnectionError(request=httpx2.Request("POST", "https://x")), True),
    (openai.APITimeoutError(request=httpx2.Request("POST", "https://x")), True),
    (_status_error(openai.RateLimitError, 429), True),
    (_status_error(openai.InternalServerError, 503), True),
    (_status_error(openai.BadRequestError, 400), False),
    (requests.ConnectionError(), True),
    (requests.Timeout(), True),
    (_http_error(502), True),
    (_http_error(404), False),
    (httpx2.ReadTimeout("stalled mid-stream"), True),  # raised while reading a stream: not wrapped by the SDK
    (httpx2.RemoteProtocolError("peer closed connection"), True),
    (TransientError("OCR queue full (10010)"), True),
    (UnparseableResponse("not json"), False),  # handled by the parse retry, not the transport retry
    (PageCountMismatch(3, 2), False),
    (ValueError("bad"), False),
])
def test_retry_classification(exc, retryable):
    assert is_retryable(exc) is retryable


def test_transport_errors_are_retried_then_raised():
    meter = RequestMeter()
    calls = []

    def flaky():
        calls.append(1)
        raise requests.ConnectionError("reset")

    with pytest.raises(requests.ConnectionError):
        _gateway(meter).call("ocr", {"p": 1}, flaky)
    assert len(calls) == 3
    assert meter.snapshot().requests == {"ocr": 1} and meter.snapshot().attempts == {"ocr": 3}


def test_non_retryable_errors_are_not_retried():
    calls = []

    def bad_request():
        calls.append(1)
        raise ValueError("400 invalid")

    with pytest.raises(ValueError):
        _gateway().call("llm", {"q": 1}, bad_request)
    assert calls == [1]


def test_unparseable_response_is_requested_again_and_replays_identically(tmp_path):
    answers = iter(["garbage", '{"ok": true}'])

    def parse(text):
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise UnparseableResponse(str(exc)) from exc

    record = _gateway(cache=ResponseCache(tmp_path, "read_write"))
    assert record.call("vlm", {"q": 1}, lambda: next(answers), parse=parse) == {"ok": True}

    replay_meter = RequestMeter()
    replay = _gateway(replay_meter, cache=ResponseCache(tmp_path, "read_only"))
    assert replay.call("vlm", {"q": 1}, lambda: pytest.fail("no request offline"), parse=parse) == {"ok": True}
    assert replay_meter.snapshot().cache_hits == {"vlm": 2}


def test_parsed_answer_is_never_requested_again():
    calls = []

    def fetch():
        calls.append(1)
        return "{}"

    assert _gateway().call("vlm", {"q": 1}, fetch, parse=json.loads) == {}  # no expected field, still final
    assert calls == [1]


# ── OCR jobs: resume and page count ─────────────────────────────────────


def _pdf(pages: int) -> bytes:
    doc = pymupdf.open()
    for _ in range(pages):
        doc.new_page()
    return doc.tobytes()


def _page(text):
    return {"prunedResult": {"width": 10, "height": 10, "parsing_res_list": [
        {"block_label": "text", "block_content": text, "block_bbox": [0, 0, 1, 1], "block_order": 1}]}}


class _Resp:
    def __init__(self, payload=None, text="", status_code=200):
        self._payload, self.text, self.status_code = payload, text, status_code
        self.ok = status_code < 400

    def raise_for_status(self):
        if not self.ok:
            raise _http_error(self.status_code)

    def json(self):
        return self._payload


def _ocr(monkeypatch, polls, pages, *, job_store=None):
    posts = []
    polls = iter(polls)

    def fake_post(url, **kw):
        posts.append(1)
        return _Resp({"code": 0, "data": {"jobId": f"job{len(posts)}"}})

    def fake_get(url, **kw):
        if "/jobs/" in url:
            step = next(polls)
            if isinstance(step, BaseException):
                raise step
            return _Resp({"data": step})
        line = {"errorCode": 0, "result": {"layoutParsingResults": [_page(f"p{i}") for i in range(pages)]}}
        return _Resp(text=json.dumps(line))

    monkeypatch.setattr(ocr_mod.requests, "post", fake_post)
    monkeypatch.setattr(ocr_mod.requests, "get", fake_get)
    monkeypatch.setattr(PaddleOCRService, "_POLL_INTERVAL", 0.0)
    svc = PaddleOCRService(OCRBuilderConfig(endpoint="https://x/api/v2/ocr/jobs", token="t"))
    svc.gateway = _gateway()
    if job_store is not None:
        svc.job_store = job_store
    return svc, posts


_DONE = {"state": "done", "resultUrl": {"jsonUrl": "https://x/result.jsonl"}}


def test_interrupted_poll_resumes_the_same_job(monkeypatch):
    svc, posts = _ocr(monkeypatch, [{"state": "running"}, requests.ConnectionError("reset"), _DONE], pages=2)
    results = svc.recognize_pdf(_pdf(2))
    assert [r.full_text for r in results] == ["p0", "p1"]
    assert len(posts) == 1  # resumed polling instead of resubmitting


def test_job_id_survives_a_process_restart(monkeypatch, tmp_path):
    store = JobStore(tmp_path / "jobs")
    pdf = _pdf(1)  # the same bytes in both processes (a fresh PDF gets a new document id)
    first, posts = _ocr(monkeypatch, [{"state": "running"}, KeyboardInterrupt()], pages=1, job_store=store)
    with pytest.raises(KeyboardInterrupt):  # the process dies mid-poll
        first.recognize_pdf(pdf)
    assert len(posts) == 1 and len(list((tmp_path / "jobs").iterdir())) == 1

    second, posts = _ocr(monkeypatch, [_DONE], pages=1, job_store=JobStore(tmp_path / "jobs"))
    assert [r.full_text for r in second.recognize_pdf(pdf)] == ["p0"]
    assert posts == []  # the new process polled the stored job
    assert list((tmp_path / "jobs").iterdir()) == []  # finished jobs are forgotten


def test_page_count_mismatch_fails_without_caching(monkeypatch, tmp_path):
    svc, posts = _ocr(monkeypatch, [_DONE], pages=2)
    cache = ResponseCache(tmp_path)
    svc.gateway = _gateway(cache=cache)
    with pytest.raises(PageCountMismatch):
        svc.recognize_pdf(_pdf(3))
    assert len(posts) == 1  # not retried
    assert not (tmp_path / "raw").exists()
