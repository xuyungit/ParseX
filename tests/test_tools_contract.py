"""Tool contracts (guide §5, interfaces §4–§5): envelope, DocText, failure codes, versions, legality, CLI."""

import io
import json
import sys
from pathlib import Path

import fitz
import pytest
from jsonschema import Draft202012Validator
from PIL import Image

import parserx.cli
from parserx.config.schema import CacheConfig, OCRBuilderConfig, ParserXConfig, PriceConfig
from parserx.ir.enums import BlockKind, PageStatus
from parserx.services.ocr import PaddleOCRService
from parserx.tools import TOOLS, ToolContext, call_tool, tool_schema, workspace_init
from parserx.workspace import Workspace, verify_workspace

NATIVE = "SENTINEL-NATIVE 采购金额为 100 万元"
OCR_TEXT = "SENTINEL-OCR 扫描文字 3 件"
VLM_TEXT = "SENTINEL-VLM 一座桥"
SENTINELS = ("SENTINEL-NATIVE", "SENTINEL-OCR", "SENTINEL-VLM")


def _png(w, h, color):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def pdf(tmp_path):
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 90), "SENTINEL-NATIVE 标题", fontsize=18, fontname="china-s")
    page.insert_text((72, 140), NATIVE, fontsize=11, fontname="china-s")
    page.insert_image(fitz.Rect(72, 300, 200, 400), stream=_png(128, 100, (200, 40, 40)))
    scanned = doc.new_page(width=595, height=842)
    scanned.insert_image(scanned.rect, stream=_png(300, 424, (230, 230, 230)))
    path = tmp_path / "doc.pdf"
    doc.save(path)
    return path


def _ocr_page():
    return {"prunedResult": {"width": 1000, "height": 1400, "parsing_res_list": [
        {"block_label": "paragraph_title", "block_content": "SENTINEL-OCR 标题", "block_bbox": [100, 100, 600, 140],
         "block_order": 1},
        {"block_label": "text", "block_content": OCR_TEXT, "block_bbox": [100, 160, 900, 220], "block_order": 2},
        {"block_label": "table", "block_content":
            "<table><tr><td>项目</td><td>数值</td></tr><tr><td>SENTINEL-OCR 甲</td><td>3</td></tr></table>",
         "block_bbox": [100, 300, 900, 500], "block_order": None},
        {"block_label": "image", "block_content": "", "block_bbox": [100, 600, 500, 900], "block_order": None},
        {"block_label": "number", "block_content": "2", "block_bbox": [480, 1350, 520, 1380], "block_order": None},
    ]}}


class FakeVLM:
    def __init__(self):
        self.attempt_hook = None
        self.usage_hook = None
        self.calls = []
        self.schemas = []

    def describe_image(self, image_path, prompt, *, context="", temperature=0.1, max_tokens=8192,
                       structured_output_mode="off", json_schema=None, json_schema_name="x"):
        self.calls.append(json_schema_name)
        self.schemas.append(json_schema)
        if self.usage_hook:
            self.usage_hook("gpt-6-luna", 1000, 0, 100)
        if json_schema_name == "parserx_review_table":
            return json.dumps({"table_html": "<table><tr><td>项目</td><td>数值</td></tr>"
                                             "<tr><td>SENTINEL-OCR 甲</td><td>8</td></tr></table>",
                               "undetermined": []})
        return json.dumps({"type": "photo", "summary": {"value": VLM_TEXT, "level": "inferred"},
                           "visible_text": [{"value": "SENTINEL-VLM 字", "level": "visible"}],
                           "chart": None, "diagram": None})


def _context(ocr_behaviour=None):
    vlm = FakeVLM()

    class Context(ToolContext):
        def _new_ocr(self):
            service = PaddleOCRService(OCRBuilderConfig(endpoint="https://x/api/v2/ocr/jobs", token="t"))

            def run_job(file_bytes, filename, mime, job_key=None):
                if ocr_behaviour is not None:
                    raise ocr_behaviour
                with fitz.open(stream=file_bytes, filetype="pdf") as sub:
                    return {"layoutParsingResults": [_ocr_page() for _ in sub]}

            service._run_job = run_job
            return service

        def _new_vlm(self, cfg):
            return vlm

    Context.fake_vlm = vlm
    return Context


def _config(**budget):
    config = ParserXConfig()
    config.services.vlm.model = "gpt-6-luna"
    config.scheduling.retry.backoff_s = 0.0
    config.scheduling.budget.requests = budget
    config.scheduling.prices = {"gpt-6-luna": PriceConfig(input=0.10, cached_input=0.01, output=0.50)}
    return config


@pytest.fixture
def ws(pdf, tmp_path):
    envelope, code = workspace_init(pdf, tmp_path / "ws", config=_config())
    assert code == 0 and envelope.ok
    return tmp_path / "ws"


def _call(name, ws, request=None, *, config=None, context=None, expect_version=None):
    return call_tool(name, ws, request or {}, config=config or _config(), context_factory=context or _context(),
                     expect_version=expect_version)


def _strings_outside_doc_text(value, inside=False):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _strings_outside_doc_text(item, inside or key == "doc_text")
    elif isinstance(value, list):
        for item in value:
            yield from _strings_outside_doc_text(item, inside)
    elif isinstance(value, str) and not inside:
        yield value


def _assert_contract(envelope, name):
    data = json.loads(envelope.model_dump_json(by_alias=True))
    Draft202012Validator(tool_schema(name)["envelope"]).validate(data)
    leaked = [s for s in _strings_outside_doc_text(data) if any(x in s for x in SENTINELS)]
    assert leaked == [], f"document text outside DocText in {name}: {leaked[:3]}"
    return data


# ── A full session through every tool ───────────────────────────────────


def test_session_through_every_tool(ws, tmp_path):
    context = _context()
    env, _ = _call("overview", ws, context=context)
    data = _assert_contract(env, "overview")
    assert [p["status"] for p in data["result"]["pages"]] == ["done", "pending"]
    assert data["result"]["unresolved"] == {"page_pending": 1}

    env, _ = _call("read", ws, {"page": 1, "image": "page", "observations": True}, context=context)
    data = _assert_contract(env, "read")
    assert data["result"]["image"]["path"].endswith(".png") and data["ws_version"] == 1  # reading changes nothing

    env, _ = _call("recognize", ws, {"pages": [2], "engine": "paddleocr"}, context=context)
    data = _assert_contract(env, "recognize")
    assert env.ok and data["result"]["selections"][0]["choice"] == "scan_engine"
    assert data["cost"]["requests"] == {"ocr": 1} and data["diff"][0]["after"] == "done"

    table = next(b for b in Workspace.open(ws).load().blocks if b.kind == BlockKind.TABLE)
    issues = [{"kind": "char", "cells": [[1, 1]], "note": "3 or 8?"}]
    env, _ = _call("review_table", ws, {"block": table.id, "issues": issues}, context=context)
    data = _assert_contract(env, "review_table")
    assert data["result"]["adopted"] is True and data["result"]["cell_diff"][0]["after"] == {"doc_text": "8"}
    env, code = _call("review_table", ws, {"block": table.id, "issues": issues}, context=context)
    assert not env.ok and env.failures[0].code == "invalid_request" and "no new evidence" in env.failures[0].message

    figure = next(b for b in Workspace.open(ws).load().blocks if b.kind == BlockKind.FIGURE)
    env, _ = _call("describe_figure", ws, {"block": figure.id}, context=context)
    data = _assert_contract(env, "describe_figure")
    assert data["result"]["type"] == "photo" and data["cost"]["requests"] == {"vlm": 1}
    env, _ = _call("describe_figure", ws, {"block": figure.id}, context=context)
    assert env.result.cached and env.cost.requests == {}

    title = next(b for b in Workspace.open(ws).load().blocks if b.kind == BlockKind.TITLE)
    env, _ = _call("apply_structure", ws, {"actor": "pipeline", "changes": [
        {"op": "set_level", "block": title.id, "level": 1, "reason": "engine title"},
        {"op": "set_level", "block": table.id, "level": 2, "reason": "illegal"}]}, context=context)
    data = _assert_contract(env, "apply_structure")
    assert data["result"]["accepted"] == [0] and data["result"]["rejected"][0]["rule"] == "level_on_non_title"

    env, _ = _call("check", ws, context=context)
    data = _assert_contract(env, "check")
    assert data["result"]["exportable"] and data["result"]["document_status"] == "complete"

    env, _ = _call("export", ws, {"out": str(tmp_path / "out")}, context=context)
    data = _assert_contract(env, "export")
    markdown = (tmp_path / "out" / "doc.md").read_text()
    assert "# SENTINEL-OCR 标题" in markdown and "> [图片语义] photo" in markdown and "| SENTINEL-OCR 甲 | 8 |" in markdown

    state = Workspace.open(ws).load()
    assert state.stats.requests == {"ocr": 1, "vlm": 2} and state.stats.cost_usd == pytest.approx(2 * (1000 * 0.10 + 100 * 0.50) / 1e6)
    calls = [json.loads(line) for line in (ws / "calls.jsonl").read_text().splitlines()]
    assert [c["tool"] for c in calls if c["type"] == "call"][:3] == ["workspace_init", "overview", "read"]
    assert verify_workspace(ws).ok  # every commit came from a tool call


def test_a_workspace_changed_outside_the_tools_is_refused(ws, tmp_path):
    raw = json.loads((ws / "state.json").read_text())
    raw["blocks"][0]["text"] = "SENTINEL-NATIVE 改写"
    (ws / "state.json").write_text(json.dumps(raw, ensure_ascii=False))
    for name, request in (("check", {}), ("export", {"out": str(tmp_path / "out")}), ("overview", {})):
        env, code = _call(name, ws, request)
        assert not env.ok and code == 0 and env.failures[0].code == "workspace_tampered", name
        assert not env.failures[0].retryable
    assert not (tmp_path / "out").exists()


def test_requests_carry_no_text_for_structure_and_schemas_exist():
    for name in TOOLS:
        schema = tool_schema(name)
        assert schema["request"]["type"] == "object" and "properties" in schema["envelope"]
    changes = tool_schema("apply_structure")["request"]["$defs"]
    assert not any("text" in d.get("properties", {}) for d in changes.values())


# ── Failure codes ───────────────────────────────────────────────────────


def test_invalid_request_exits_two(ws):
    env, code = _call("read", ws, {"page": 1, "block": "b-p001-0001"})
    assert code == 2 and not env.ok and env.failures[0].code == "invalid_request"


def test_not_found(ws):
    env, code = _call("read", ws, {"block": "b-nope"})
    assert code == 0 and not env.ok and env.failures[0].code == "not_found"


def test_version_conflict(ws):
    env, _ = _call("overview", ws, expect_version=99)
    assert not env.ok and env.failures[0].code == "version_conflict" and env.failures[0].retryable


def test_budget_exhausted_skips_the_page_but_exports_partial(ws, tmp_path):
    config = _config(ocr=0)
    env, _ = _call("recognize", ws, {"pages": [2], "engine": "paddleocr"}, config=config)
    assert env.ok and env.failures[0].code == "budget_exhausted"
    state = Workspace.open(ws).load()
    assert state.pages[1].status == PageStatus.SKIPPED
    env, _ = _call("export", ws, {"out": str(tmp_path / "out")}, config=config)
    assert env.ok and env.result.status == "partial" and env.result.missing


@pytest.mark.parametrize("exc, code, retryable", [
    (RuntimeError("OCR submit HTTP 400: bad file"), "service_error", False),
    (TimeoutError("job still running"), "timeout", True),
])
def test_service_failures_mark_the_page(ws, exc, code, retryable):
    env, _ = _call("recognize", ws, {"pages": [2], "engine": "paddleocr"}, context=_context(exc))
    assert env.ok and (env.failures[0].code, env.failures[0].retryable) == (code, retryable)
    assert Workspace.open(ws).load().pages[1].status == PageStatus.FAILED


def test_offline_cache_miss_leaves_the_page_pending_and_export_refused(ws, tmp_path):
    config = _config()
    config.cache = CacheConfig(mode="read_only", dir=str(tmp_path / "empty-cache"))
    env, _ = _call("recognize", ws, {"pages": [2], "engine": "paddleocr"}, config=config)
    assert env.failures[0].code == "cache_miss_offline"
    assert Workspace.open(ws).load().pages[1].status == PageStatus.PENDING
    env, _ = _call("export", ws, {"out": str(tmp_path / "out")}, config=config)
    assert not env.ok and env.failures[0].code == "check_failed" and not (tmp_path / "out").exists()


def test_internal_error_exits_one(ws, monkeypatch):
    spec = TOOLS["overview"]
    monkeypatch.setitem(TOOLS, "overview", spec.__class__(spec.request, spec.result,
                                                          lambda ctx, req: 1 / 0))
    env, code = _call("overview", ws)
    assert code == 1 and env.failures[0].code == "internal_error"


def test_native_pages_are_not_sent_to_the_scan_engine(ws):
    env, _ = _call("recognize", ws, {"pages": [1], "engine": "paddleocr"})
    assert env.ok and env.cost.requests == {} and "native text layer passed" in env.failures[0].message


# ── CLI ─────────────────────────────────────────────────────────────────


def test_cli_prints_only_the_envelope(ws, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["parserx", "tool", "overview", "--ws", str(ws), "--json"])
    with pytest.raises(SystemExit) as exit_info:
        parserx.cli.main()
    envelope = json.loads(capsys.readouterr().out)
    assert exit_info.value.code == 0 and envelope["tool"] == "overview" and envelope["ok"]


def test_cli_schema_and_invalid_request(ws, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["parserx", "tool", "schema", "read"])
    with pytest.raises(SystemExit):
        parserx.cli.main()
    assert "request" in json.loads(capsys.readouterr().out)
    monkeypatch.setattr(sys, "argv", ["parserx", "tool", "read", "--ws", str(ws), "--json"])
    with pytest.raises(SystemExit) as exit_info:
        parserx.cli.main()
    assert exit_info.value.code == 2 and json.loads(capsys.readouterr().out)["failures"][0]["code"] == \
        "invalid_request"


# ── Trial follow-ups (P1-7b) ────────────────────────────────────────────


def test_read_shows_adopted_content_unless_asked(ws):
    _call("recognize", ws, {"pages": [2], "engine": "paddleocr"})
    env, _ = _call("read", ws, {"page": 2})
    assert all(b.status in ("ok", "degraded") and b.anchors is None for b in env.result.blocks)
    env, _ = _call("read", ws, {"page": 2, "include_hidden": True, "geometry": True})
    assert any(b.status == "merged" for b in env.result.blocks) and env.result.blocks[0].anchors


def test_recognize_returns_views_only_when_asked(ws):
    env, _ = _call("recognize", ws, {"pages": [2], "engine": "paddleocr"})
    assert env.result.observations == [] and env.result.observations_total == 5


def test_answer_with_trailing_text_is_parsed():
    from parserx.tools.vlm_tasks import parse_review

    grid, undetermined, problem = parse_review(
        '{"table_html": "<table><tr><td>a</td></tr></table>", "undetermined": []}\n{"note": "extra"}')
    assert grid.slot(0, 0).content == "a" and problem is None


def test_rejected_review_leaves_an_unresolved_item(ws):
    class Rewriting(FakeVLM):
        def describe_image(self, *args, **kwargs):
            return json.dumps({"table_html": "<table><tr><td>项目</td></tr></table>", "undetermined": []})

    context = _context()
    rewriting = Rewriting()

    class Context(context):
        def _new_vlm(self, cfg):
            return rewriting

    _call("recognize", ws, {"pages": [2], "engine": "paddleocr"}, context=Context)
    table = next(b for b in Workspace.open(ws).load().blocks if b.kind == BlockKind.TABLE)
    env, _ = _call("review_table", ws, {"block": table.id, "issues": [{"kind": "structure", "note": "rows?"}]},
                   context=Context)
    assert env.ok and not env.result.adopted
    assert env.unresolved[0].kind == "table_uncertain" and "structure_valid" in env.unresolved[0].detail


def test_read_a_block_with_its_page_image(ws):
    block = next(b for b in Workspace.open(ws).load().blocks if b.kind == BlockKind.TEXT)
    env, code = _call("read", ws, {"block": block.id, "image": "page"})
    assert env.ok and code == 0 and env.failures == []
    by_page, _ = _call("read", ws, {"page": 1, "image": "page"})
    assert env.result.image.asset == by_page.result.image.asset  # the page the block is on


def test_a_named_figure_schema_is_enforced(ws):
    context = _context()
    figure = next(b for b in Workspace.open(ws).load().blocks if b.kind == BlockKind.FIGURE)
    _call("describe_figure", ws, {"block": figure.id, "schema": "diagram"}, context=context)
    assert context.fake_vlm.schemas[-1]["properties"]["type"]["enum"] == ["diagram"]


def test_empty_standard_input_is_named(ws, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["parserx", "tool", "review_table", "--ws", str(ws), "--request", "-", "--json"])
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    with pytest.raises(SystemExit) as exit_info:
        parserx.cli.main()
    failure = json.loads(capsys.readouterr().out)["failures"][0]
    assert exit_info.value.code == 2 and "standard input is empty" in failure["message"]


# ── P2-5: batches and the standard processing ───────────────────────────


def test_describe_figures_in_one_batch(ws):
    context = _context()
    _call("recognize", ws, {"pages": [2], "engine": "paddleocr"}, context=context)  # page 2 brings a second figure
    figures = [b.id for b in Workspace.open(ws).load().blocks if b.kind == BlockKind.FIGURE and b.status == "ok"]
    text = next(b.id for b in Workspace.open(ws).load().blocks if b.kind == BlockKind.TEXT)
    assert len(figures) == 2
    env, code = _call("describe_figure", ws, {"blocks": [*figures, text]}, context=context)
    data = _assert_contract(env, "describe_figure")
    assert env.ok and code == 0 and data["cost"]["requests"] == {"vlm": 2}
    assert [i["block"] for i in data["result"]["items"]] == figures and all(i["type"] == "photo" for i in data["result"]["items"])
    assert [f["targets"] for f in data["failures"]] == [[text]] and data["failures"][0]["code"] == "invalid_request"
    state = Workspace.open(ws).load()
    assert all(b.semantic is not None for b in state.blocks if b.id in figures)
    env, _ = _call("describe_figure", ws, {"blocks": figures}, context=context)
    assert env.cost.requests == {} and all(i.cached for i in env.result.items)


def test_batch_results_do_not_depend_on_completion_order(ws, monkeypatch):
    import threading
    import time as _time

    context = _context()
    _call("recognize", ws, {"pages": [2], "engine": "paddleocr"}, context=context)
    figures = [b for b in Workspace.open(ws).load().blocks if b.kind == BlockKind.FIGURE and b.status == "ok"]
    fake = context.fake_vlm
    original = fake.describe_image
    first = threading.Event()

    def slow_first(image_path, prompt, **kw):
        # the first figure answers last; each answer names its own image
        if str(image_path).endswith(figures[0].anchors[-1].asset + ".png") or not first.is_set():
            first.set()
            _time.sleep(0.2)
        answer = json.loads(original(image_path, prompt, **kw))
        answer["summary"]["value"] = f"SENTINEL-VLM {Path(image_path).stem}"
        return json.dumps(answer)

    monkeypatch.setattr(fake, "describe_image", slow_first)
    env, _ = _call("describe_figure", ws, {"blocks": [f.id for f in figures]}, context=context)
    state = Workspace.open(ws).load()
    for block in (b for b in state.blocks if b.id in {f.id for f in figures}):
        asset = next(a.asset for a in block.anchors if hasattr(a, "asset"))
        assert block.semantic.summary.value.endswith(asset)


def test_process_does_the_standard_steps_in_one_call(ws):
    config = _config()
    config.runtime.layout_shadow = False  # the layout step has its own tests (fake detector)
    context = _context()
    env, code = _call("process", ws, {}, config=config, context=context)
    data = _assert_contract(env, "process")
    result = data["result"]
    assert env.ok and code == 0 and data["cost"]["requests"] == {"ocr": 1, "vlm": 2}
    assert result["pages"] == {"done": 2} and result["figures"] == {"described": 2}
    assert result["check"]["exportable"] and result["check"]["document_status"] == "complete"
    assert [s["step"] for s in result["steps"]] == ["recognize", "describe_figure", "structure", "check"]
    assert all(set(w) == {"target", "kind", "detail"} for w in result["worklist"])
    calls = [json.loads(line) for line in (ws / "calls.jsonl").read_text().splitlines()]
    assert [c["tool"] for c in calls if c["type"] == "call"] == ["workspace_init", "process"]
    assert verify_workspace(ws).ok
    again, _ = _call("process", ws, {}, config=config, context=context)  # nothing left to do: no requests
    assert again.ok and again.cost.requests == {}
