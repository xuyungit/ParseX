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
from parserx.render import render_markdown
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
        image_path.read_bytes()  # like the real service: the image file is read (a str path would fail here)
        if self.usage_hook:
            self.usage_hook("gpt-6-luna", 1000, 0, 100)
        if json_schema_name == "parserx_ask_image":
            return "SENTINEL-VLM 图上写的是：扫描文字 8 件"
        if json_schema_name == "parserx_review_table":
            return json.dumps({"table_html": "<table><tr><td>项目</td><td>数值</td></tr>"
                                             "<tr><td>SENTINEL-OCR 甲</td><td>8</td></tr></table>",
                               "undetermined": []})
        return json.dumps({"type": "photo", "summary": {"value": VLM_TEXT, "level": "inferred"},
                           "visible_text": [{"value": "SENTINEL-VLM 字", "level": "visible"}],
                           "chart": None, "diagram": None})


class FakeReader:
    """The local page reader without the model: it sees nothing unless told (tests/test_page_reading.py)."""

    name = "reading"
    version = "fake-reader-1"

    def __init__(self, lines=None):
        self.lines, self.calls = lines or {}, 0

    def read(self, png):
        self.calls += 1
        return list(self.lines.get(self.calls, []))


def _context(ocr_behaviour=None, page=None):
    vlm = FakeVLM()

    class Context(ToolContext):
        def _new_reader(self):
            return FakeReader()

        def _new_ocr(self):
            service = PaddleOCRService(OCRBuilderConfig(endpoint="https://x/api/v2/ocr/jobs", token="t"))

            def run_job(file_bytes, filename, mime, job_key=None):
                if ocr_behaviour is not None:
                    raise ocr_behaviour
                with fitz.open(stream=file_bytes, filetype="pdf") as sub:
                    return {"layoutParsingResults": [(page or _ocr_page)() for _ in sub]}

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


def test_batch_inputs_do_not_depend_on_other_tasks(pdf, tmp_path, monkeypatch):
    # §11.5 (v1's image-processor race): each request's image, prompt and context are fixed before any task
    # runs, so a batch whose first figure answers last sends exactly what describing each figure alone sends
    import threading
    import time as _time

    def requests(root, batches, slow_first):
        assert workspace_init(pdf, root, config=_config())[1] == 0
        context = _context()
        _call("recognize", root, {"pages": [2], "engine": "paddleocr"}, context=context)
        figures = sorted(b.id for b in Workspace.open(root).load().blocks if b.kind == BlockKind.FIGURE)
        fake, original, sent, first = context.fake_vlm, context.fake_vlm.describe_image, {}, threading.Event()

        def record(image_path, prompt, **kw):
            if slow_first and not first.is_set():
                first.set()
                _time.sleep(0.2)
            sent[Path(image_path).name] = (Path(image_path).read_bytes(), prompt, kw["context"])
            return original(image_path, prompt, **kw)

        monkeypatch.setattr(fake, "describe_image", record)
        for batch in batches(figures):
            _call("describe_figure", root, {"blocks": batch}, context=context)
        return sent

    together = requests(tmp_path / "a", lambda figures: [figures], slow_first=True)
    alone = requests(tmp_path / "b", lambda figures: [[f] for f in reversed(figures)], slow_first=False)
    assert len(together) >= 2 and together == alone


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
    assert [s["step"] for s in result["steps"]] == ["recognize", "reading", "describe_figure", "structure", "check"]
    assert all(set(w) == {"target", "kind", "detail"} for w in result["worklist"])
    calls = [json.loads(line) for line in (ws / "calls.jsonl").read_text().splitlines()]
    assert [c["tool"] for c in calls if c["type"] == "call"] == ["workspace_init", "process"]
    assert verify_workspace(ws).ok
    again, _ = _call("process", ws, {}, config=config, context=context)  # nothing left to do: no requests
    assert again.ok and again.cost.requests == {}


def test_process_joins_a_paragraph_cut_by_the_page(tmp_path):
    doc = fitz.open()
    for text in ("供货方应在合同签订后分两批交货，第一批", "不少于总量的百分之六十。"):
        doc.new_page(width=595, height=842).insert_text((72, 400), text, fontsize=11, fontname="china-s")
    doc.save(tmp_path / "cut.pdf")
    config = _config()
    config.runtime.layout_shadow = False
    workspace_init(tmp_path / "cut.pdf", tmp_path / "cut", config=config)
    env, _ = _call("process", tmp_path / "cut", {}, config=config)
    state = Workspace.open(tmp_path / "cut").load()
    assert [(r.kind.value, r.src, r.dst) for r in state.relations] == [("continues", "b-p001-0001", "b-p002-0001")]
    assert "1 paragraph continuations" in env.result.steps[-2].detail
    assert "第一批不少于" in render_markdown(state)


# ── P2-5: the agent corrects what it read from the image (Q30) ──────────


def _looked_at(ws, block_id, context):
    env, _ = _call("read", ws, {"block": block_id, "image": "crop"}, context=context)
    return env.result.image.asset


def test_agent_corrects_ocr_text_it_has_seen(ws):
    context = _context()
    _call("recognize", ws, {"pages": [2], "engine": "paddleocr"}, context=context)
    block = next(b for b in Workspace.open(ws).load().blocks if b.text == OCR_TEXT)
    image = _looked_at(ws, block.id, context)
    env, code = _call("correct", ws, {"block": block.id, "image": image, "reason": "图上是 8 件",
                                      "edits": [{"find": "3 件", "replace": "8 件"}]}, context=context)
    data = _assert_contract(env, "correct")
    assert env.ok and code == 0 and data["result"]["adopted"] is True and data["cost"]["requests"] == {}
    after = next(b for b in Workspace.open(ws).load().blocks if b.id == block.id)
    assert after.text == OCR_TEXT.replace("3 件", "8 件") and after.chosen_observation.endswith("agent-1")
    assert after.decisions[-1].actor == "agent" and len(after.observations) == 2  # the OCR reading stays
    assert verify_workspace(ws).ok


def test_a_correction_needs_the_image_and_keeps_native_numbers(ws):
    context = _context()
    native = next(b for b in Workspace.open(ws).load().blocks if b.text == NATIVE)
    env, _ = _call("correct", ws, {"block": native.id, "image": "a-0000000000000000", "reason": "r",
                                   "edits": [{"find": "采购", "replace": "采买"}]}, context=context)
    assert env.ok and env.result.adopted is False and not env.result.gate[0].passed  # image never read
    image = _looked_at(ws, native.id, context)
    env, _ = _call("correct", ws, {"block": native.id, "image": image, "reason": "r",
                                   "edits": [{"find": "100 万元", "replace": "900 万元"}]}, context=context)
    assert env.result.adopted is False and [g.name for g in env.result.gate if not g.passed] == ["numeric_consistency"]
    env, _ = _call("correct", ws, {"block": native.id, "image": image, "reason": "r",
                                   "edits": [{"find": "采购", "replace": "采买"}]}, context=context)
    assert env.result.adopted is True
    assert any(u.kind == "evidence_conflict" for u in _call("check", ws, context=context)[0].unresolved) is False
    env, code = _call("correct", ws, {"block": native.id, "image": image, "reason": "r",
                                      "edits": [{"find": "不存在的字", "replace": "x"}]}, context=context)
    assert not env.ok and code == 2 and env.failures[0].code == "invalid_request"


def _give_reading(ws, n, lines):
    """A local reading of page *n* (what ``process`` stores; tests give it directly)."""
    from parserx.ir.state import PageReading, ReadLine

    with Workspace.open(ws).txn("test:reading") as state:
        state.readings = sorted([r for r in state.readings if r.n != n] + [PageReading(
            n=n, engine="test", dpi=150, lines=[ReadLine(bbox=b, text=t, score=0.99) for t, b in lines])],
            key=lambda r: r.n)


def test_the_agent_adds_text_the_page_shows_and_no_block_has(ws):
    context = _context()
    native = next(b for b in Workspace.open(ws).load().blocks if b.text == NATIVE)
    _give_reading(ws, 1, [("SENTINEL-NATIVE 标题", (72, 72, 300, 95)), (NATIVE, native.anchors[0].bbox),
                          ("专家评审组名单", (72, 200, 200, 214))])
    add = {"page": 1, "bbox": [72, 200, 200, 214], "text": "专家评审组名单"}
    env, _ = _call("correct", ws, {"image": "a-0000000000000000", "reason": "r", "add": add}, context=context)
    assert env.result.adopted is False  # no image of the place was read
    page_image = _call("read", ws, {"page": 1, "image": "page"}, context=context)[0].result.image.asset
    env, code = _call("correct", ws, {"image": page_image, "reason": "图上有这一行，文字层没有", "add": add},
                      context=context)
    data = _assert_contract(env, "correct")
    assert code == 0 and data["result"]["adopted"] is True, data["result"]["gate"]
    state = Workspace.open(ws).load()
    added = next(b for b in state.blocks if b.text == "专家评审组名单")
    assert added.kind == BlockKind.TEXT and added.anchors[0].bbox == (72, 200, 200, 214)
    order = [b.id for b in sorted(state.blocks, key=lambda b: b.order)]
    assert order.index(native.id) < order.index(added.id)  # placed by its position on the page
    entry = next(e for e in state.ledger if e.block == added.id)
    assert entry.unit == "agent_text" and entry.disposition == "output"
    checked = _call("check", ws, context=context)[0].result  # the reading was given outside a tool: check, not verify
    assert checked.accounting.unassigned == 0 and checked.accounting.output == checked.accounting.discovered
    for text, bbox in (("图上没有的一行", [72, 600, 200, 614]),  # the local reading does not show it
                       (NATIVE, list(native.anchors[0].bbox))):  # a block already has it
        env, _ = _call("correct", ws, {"image": page_image, "reason": "r",
                                       "add": {"page": 1, "bbox": bbox, "text": text}}, context=context)
        assert env.result.adopted is False
    assert sum(1 for b in Workspace.open(ws).load().blocks if b.text == NATIVE) == 1


def test_native_numbers_change_only_as_the_local_reading_shows(ws):
    context = _context()
    native = next(b for b in Workspace.open(ws).load().blocks if b.text == NATIVE)
    image = _looked_at(ws, native.id, context)
    edit = {"block": native.id, "image": image, "reason": "图上是 900", "edits": [{"find": "100 万元", "replace": "900 万元"}]}
    _give_reading(ws, 1, [("SENTINEL-NATIVE 采购金额为100万元", native.anchors[0].bbox)])  # the page shows 100
    assert _call("correct", ws, edit, context=context)[0].result.adopted is False
    _give_reading(ws, 1, [("SENTINEL-NATIVE 采购金额为900万元", native.anchors[0].bbox)])  # the page shows 900
    env, _ = _call("correct", ws, edit, context=context)
    assert env.result.adopted is True
    assert "local reading" in next(g.detail for g in env.result.gate if g.name == "numeric_consistency")



def test_an_item_checked_on_the_image_can_be_closed(ws):
    context = _context()
    native = next(b for b in Workspace.open(ws).load().blocks if b.text == NATIVE)
    _give_reading(ws, 1, [(NATIVE, native.anchors[0].bbox), ("扫描软件的标志", (400.0, 780.0, 520.0, 792.0))])
    open_items = [u for u in _call("check", ws, context=context)[0].unresolved if u.kind == "text_unaccounted"]
    assert [u.target for u in open_items] == ["p1"]
    request = {"target": "p1", "kind": "text_unaccounted", "image": "a-0000000000000000",
               "reason": "a logo the local reading took for text"}
    env, _ = _call("close", ws, request, context=context)
    assert env.ok and env.result.closed is False  # no image of the page was read
    request["image"] = _call("read", ws, {"page": 1, "image": "page"}, context=context)[0].result.image.asset
    env, code = _call("close", ws, request, context=context)
    data = _assert_contract(env, "close")
    assert code == 0 and data["result"]["closed"] is True
    assert not any(u.kind == "text_unaccounted" for u in _call("check", ws, context=context)[0].unresolved)
    env, code = _call("close", ws, {"target": "p2", "kind": "page_pending", "image": request["image"],
                                    "reason": "r"}, context=context)
    assert not env.ok and code == 2  # a failure is resolved by processing, not closed
    env, code = _call("close", ws, request, context=context)
    assert not env.ok and env.failures[0].code == "not_found"  # nothing open any more
    _give_reading(ws, 1, [(NATIVE, native.anchors[0].bbox), ("另一行漏掉的正文内容", (72.0, 600.0, 300.0, 612.0))])
    assert any(u.kind == "text_unaccounted" for u in _call("check", ws, context=context)[0].unresolved)  # new content



def test_text_covered_on_the_page_stays_and_the_summary_names_it(ws):
    """Q71: text the page draws under another element is content; the agent keeps it and marks it occluded."""
    context = _context()
    _call("recognize", ws, {"pages": [2], "engine": "paddleocr"}, context=context)
    native = next(b for b in Workspace.open(ws).load().blocks if b.text == NATIVE)
    _give_reading(ws, 1, [("给助手发送消息", (72, 700, 300, 712))])  # the page image does not show NATIVE
    item = next(u for u in _call("check", ws, context=context)[0].unresolved
                if u.kind == "text_not_seen" and u.target == native.id)
    image = _call("read", ws, {"page": 1, "image": "page"}, context=context)[0].result.image.asset
    request = {"target": item.target, "kind": "title_candidate", "image": image, "reason": "r", "occluded": True}
    env, code = _call("close", ws, request, context=context)
    assert not env.ok and code == 2  # only text the page does not show can be occluded
    request.update(kind="text_not_seen", reason="被悬浮的输入框盖住")
    env, _ = _call("close", ws, request, context=context)
    assert env.ok and env.result.closed is True
    env, _ = _call("export", ws, {"out": str(ws.parent / "out"), "name": "d"}, context=context)
    assert env.ok, env.failures
    assert NATIVE in (ws.parent / "out" / "d.md").read_text()
    summary = json.loads((ws.parent / "out" / "d.json").read_text())
    assert summary["review"]["occluded"] == [{"target": native.id, "page": 1, "quotes": [q.doc_text for q in item.quotes],
                                              "reason": "被悬浮的输入框盖住"}]


def test_agent_corrects_table_cells(ws):
    context = _context()
    _call("recognize", ws, {"pages": [2], "engine": "paddleocr"}, context=context)
    table = next(b for b in Workspace.open(ws).load().blocks if b.kind == BlockKind.TABLE)
    image = _looked_at(ws, table.id, context)
    env, _ = _call("correct", ws, {"block": table.id, "image": image, "reason": "图上是 8",
                                   "cells": [{"row": 1, "col": 1, "content": "8"}]}, context=context)
    assert env.result.adopted is True
    grid = next(b for b in Workspace.open(ws).load().blocks if b.id == table.id).cells
    assert grid.slot(1, 1).content == "8" and grid.slot(1, 0).content == "SENTINEL-OCR 甲"
    env, code = _call("correct", ws, {"block": table.id, "image": image, "reason": "r",
                                      "cells": [{"row": 9, "col": 0, "content": "x"}]}, context=context)
    assert not env.ok and code == 2


def test_a_table_continued_across_pages_is_corrected_as_one(ws):
    # after merge_tables the first block holds all rows and the anchors of every page; the continuation is merged
    from parserx.ir.anchor import PdfAnchor
    from parserx.ir.enums import BlockStatus
    from parserx.ir.relation import Relation

    context = _context()
    _call("recognize", ws, {"pages": [2], "engine": "paddleocr"}, context=context)
    workspace = Workspace.open(ws)
    with workspace.txn("test:merge") as state:
        second = next(b for b in state.blocks if b.kind == BlockKind.TABLE)
        first = second.model_copy(deep=True, update={
            "id": "t-first", "anchors": [PdfAnchor(page=1, bbox=(72, 500, 520, 700), coord_space="page_pt"),
                                         *second.anchors]})
        second.status = BlockStatus.MERGED
        state.blocks.append(first)
        state.relations.append(Relation(id="r-continues-t", kind="continues", src="t-first", dst=second.id))
    page2, _ = _call("ask_image", ws, {"page": 2, "question": "续表第 1 行的数值？"}, context=context)
    env, code = _call("correct", ws, {"block": second.id, "image": page2.result.image, "reason": "图上是 8",
                                      "cells": [{"row": 1, "col": 1, "content": "8"}]}, context=context)
    assert not env.ok and code == 2 and "t-first" in env.failures[0].message
    env, _ = _call("correct", ws, {"block": "t-first", "image": page2.result.image, "reason": "图上是 8",
                                   "cells": [{"row": 1, "col": 1, "content": "8"}]}, context=context)
    assert env.result.adopted is True  # page 2 is one of the table's pages
    part, _ = _call("ask_image", ws, {"block": second.id, "question": "续表第 1 行的数值？"}, context=context)
    env, _ = _call("correct", ws, {"block": "t-first", "image": part.result.image, "reason": "图上是 9",
                                   "cells": [{"row": 1, "col": 1, "content": "9"}]}, context=context)
    assert env.result.adopted is True  # the crop of a part merged into the table is evidence for the table


def test_ask_about_the_seam_between_two_pages(ws):
    # a table or sentence continued on the next page: the bottom of one page and the top of the next in one image
    context = _context()
    _call("recognize", ws, {"pages": [2], "engine": "paddleocr"}, context=context)
    env, _ = _call("ask_image", ws, {"seam": 1, "question": "续表的表头是否重复？"}, context=context)
    assert env.ok and env.result.answers[0].seam == 1 and env.result.image is not None
    seam = Path(next(p for p in (ws / "renders").iterdir() if p.stem == env.result.image))
    from PIL import Image as _Image
    with _Image.open(seam) as image:
        assert image.height > image.width  # two half pages, stacked
    table = next(b for b in Workspace.open(ws).load().blocks if b.kind == BlockKind.TABLE)  # on page 2
    env, _ = _call("correct", ws, {"block": table.id, "image": env.result.image, "reason": "图上是 8",
                                   "cells": [{"row": 1, "col": 1, "content": "8"}]}, context=context)
    assert env.result.adopted is True  # the seam image shows both pages
    env, code = _call("ask_image", ws, {"seam": 2, "question": "?"}, context=context)
    assert not env.ok and code == 2  # there is no page 3


def test_a_correction_may_fill_an_empty_position_of_the_grid():
    from parserx.tables.grid import Cell, TableGrid
    from parserx.tools.correct import CellEdit, _edited_grid

    grid = TableGrid(n_rows=2, n_cols=2, cells=[Cell(row=0, col=0, content="项目"), Cell(row=0, col=1, content="数值"),
                                                 Cell(row=1, col=0, content="甲")])  # (1, 1) was not recognized
    filled = _edited_grid(grid, [CellEdit(row=1, col=1, content="12")], "b")
    assert filled.slot(1, 1).content == "12" and filled.slot(1, 0).content == "甲"


# ── P2-5: the agent asks the service VLM about an image (vision through a tool) ──


def test_ask_image_answers_from_the_block_image(ws):
    context = _context()
    _call("recognize", ws, {"pages": [2], "engine": "paddleocr"}, context=context)
    block = next(b for b in Workspace.open(ws).load().blocks if b.text == OCR_TEXT)
    env, code = _call("ask_image", ws, {"block": block.id, "question": "这一行的数字是几？"}, context=context)
    data = _assert_contract(env, "ask_image")
    assert env.ok and code == 0 and data["cost"]["requests"] == {"vlm": 1}
    assert data["result"]["answer"]["doc_text"].startswith("SENTINEL-VLM") and data["result"]["image"]
    assert context.fake_vlm.calls[-1] == "parserx_ask_image"
    # the VLM's reading is image evidence for a correction: the agent never looked itself
    env, _ = _call("correct", ws, {"block": block.id, "image": data["result"]["image"], "reason": "VLM 读图为 8 件",
                                   "edits": [{"find": "3 件", "replace": "8 件"}]}, context=context)
    assert env.result.adopted is True
    env, code = _call("ask_image", ws, {"page": 1, "question": "页面上有几张表？"}, context=context)
    assert env.ok and env.result.image


def test_ask_image_takes_several_questions_in_one_call(ws):
    context = _context()
    _call("recognize", ws, {"pages": [2], "engine": "paddleocr"}, context=context)
    block = next(b for b in Workspace.open(ws).load().blocks if b.text == OCR_TEXT)
    questions = [{"block": block.id, "question": "数字是几？"}, {"page": 1, "question": "有几张表？"},
                 {"block": "b-missing", "question": "？"}]
    env, code = _call("ask_image", ws, {"questions": questions}, context=context)
    data = _assert_contract(env, "ask_image")
    assert env.ok and code == 0 and data["cost"]["requests"] == {"vlm": 2}
    answers = data["result"]["answers"]
    assert [a["block"] for a in answers] == [block.id, None] and [a["page"] for a in answers] == [None, 1]
    assert all(a["answer"]["doc_text"].startswith("SENTINEL-VLM") and a["image"] for a in answers)
    assert [f["targets"] for f in data["failures"]] == [["b-missing"]] and data["failures"][0]["code"] == "not_found"
    env, _ = _call("correct", ws, {"block": block.id, "image": answers[0]["image"], "reason": "r",
                                   "edits": [{"find": "3 件", "replace": "8 件"}]}, context=context)
    assert env.result.adopted is True


# ── Q42: text and tables inside embedded images become content after the image ──


def test_recognize_transcribes_an_embedded_image(ws, tmp_path):
    context = _context()
    figure = next(b for b in Workspace.open(ws).load().blocks if b.kind == BlockKind.FIGURE)
    env, code = _call("recognize", ws, {"blocks": [figure.id], "engine": "paddleocr"}, context=context)
    data = _assert_contract(env, "recognize")
    assert env.ok and code == 0 and data["cost"]["requests"] == {"ocr": 1}
    state = Workspace.open(ws).load()
    children = [r.dst for r in state.relations if r.kind == "contains" and r.src == figure.id]
    order = [b.id for b in sorted(state.blocks, key=lambda b: b.order)]
    at = order.index(figure.id)
    assert children and order[at + 1:at + 1 + len(children)] == children  # right after the image
    assert figure.id not in [b.id for b in state.blocks if b.status != "ok"]  # the image stays shown (Q42)
    assert verify_workspace(ws).ok
    env, _ = _call("check", ws, context=context)
    assert env.result.accounting.unassigned == 0 and env.result.mismatched == []
    _call("recognize", ws, {"pages": [2], "engine": "paddleocr"}, context=context)
    env, _ = _call("export", ws, {"out": str(tmp_path / "out")}, context=context)
    markdown = (tmp_path / "out" / "doc.md").read_text()
    image_line = next(line for line in markdown.splitlines() if line.startswith("![") and figure.anchors[-1].asset in line)
    after = markdown[markdown.index(image_line):]
    assert after.index("<!-- 以下转录自上图 -->") < after.index("SENTINEL-OCR 扫描文字")
    again, _ = _call("recognize", ws, {"blocks": [figure.id], "engine": "paddleocr"}, context=context)
    assert again.cost.requests == {} and again.failures and "already" in again.failures[0].message


def test_process_transcribes_scan_and_mixed_images_and_does_not_describe_scans(ws):
    from parserx.ir.enums import ImageRoute
    from parserx.ir.state import ImageRecord

    config = _config()
    config.runtime.layout_shadow = False
    context = _context()
    workspace = Workspace.open(ws)
    figure = next(b for b in workspace.load().blocks if b.kind == BlockKind.FIGURE)
    asset = figure.anchors[-1].asset
    with workspace.txn("test:route") as state:  # as the layout step would have routed it
        state.images = [ImageRecord(id=asset, route=ImageRoute.SCAN, shown=True, t=0.8, f=0.0, regions=3)]
    env, _ = _call("process", ws, {}, config=config, context=context)
    assert env.ok and "transcribe_images" in [s.step for s in env.result.steps]
    state = Workspace.open(ws).load()
    assert any(r.kind == "contains" and r.src == figure.id for r in state.relations)
    assert next(b for b in state.blocks if b.id == figure.id).semantic is None  # its content is the transcription
    assert next(r for r in state.images if r.id == asset).complete is True


def test_an_image_with_nothing_to_read_is_not_read_again(ws):
    # a photo: the engine finds only an image region, which folds back into the figure — no block follows it
    from parserx.ir.enums import ImageRoute
    from parserx.ir.state import ImageRecord

    def photo():
        return {"prunedResult": {"width": 1000, "height": 1400, "parsing_res_list": [
            {"block_label": "image", "block_content": "", "block_bbox": [0, 0, 1000, 1400], "block_order": None}]}}

    config = _config()
    config.runtime.layout_shadow = False
    context = _context(page=photo)
    workspace = Workspace.open(ws)
    figure = next(b for b in workspace.load().blocks if b.kind == BlockKind.FIGURE)
    with workspace.txn("test:route") as state:
        state.images = [ImageRecord(id=figure.anchors[-1].asset, route=ImageRoute.MIXED, shown=True, t=0.3, f=0.2,
                                    regions=2)]
    first, _ = _call("process", ws, {}, config=config, context=context)
    assert "transcribe_images" in [s.step for s in first.result.steps]
    again, _ = _call("process", ws, {}, config=config, context=context)
    assert "transcribe_images" not in [s.step for s in again.result.steps] and again.cost.requests == {}
    assert again.result.check.exportable
    env, _ = _call("recognize", ws, {"blocks": [figure.id], "engine": "paddleocr"}, context=context)
    assert env.failures[0].message.endswith("already transcribed") and env.cost.requests == {}


def test_text_read_inside_an_image_can_be_looked_at_and_corrected(ws):
    # a block read inside an embedded image is anchored in the image's pixels: its crop comes from the image, and a
    # reading of the whole image (the figure) is evidence for it, as a whole page is for a block on the page
    from parserx.ir.enums import ImageRoute
    from parserx.ir.state import ImageRecord

    config = _config()
    config.runtime.layout_shadow = False
    context = _context()
    workspace = Workspace.open(ws)
    figure = next(b for b in workspace.load().blocks if b.kind == BlockKind.FIGURE)
    with workspace.txn("test:route") as state:
        state.images = [ImageRecord(id=figure.anchors[-1].asset, route=ImageRoute.SCAN, shown=True, t=0.8, f=0.0,
                                    regions=3)]
    _call("process", ws, {}, config=config, context=context)
    state = Workspace.open(ws).load()
    inside = next(b for b in state.blocks if b.id.startswith(figure.id + "-") and b.text == OCR_TEXT)
    env, _ = _call("read", ws, {"block": inside.id, "image": "crop"}, context=context)
    assert env.ok and Path(env.result.image.path).is_file() and env.result.image.height < 100  # a line of the 128×100 image
    env, _ = _call("ask_image", ws, {"block": inside.id, "question": "这一行写的是什么？"}, context=context)
    assert env.ok and env.result.image is not None
    whole, _ = _call("ask_image", ws, {"block": figure.id, "question": "图中的件数是多少？"}, context=context)
    env, _ = _call("correct", ws, {"block": inside.id, "image": whole.result.image, "reason": "图上是 8 件",
                                   "edits": [{"find": "3 件", "replace": "8 件"}]}, context=context)
    assert env.ok and "8 件" in next(b for b in Workspace.open(ws).load().blocks if b.id == inside.id).text


def test_an_image_in_a_docx_is_transcribed_too(tmp_path):
    import docx

    document = docx.Document()
    document.add_paragraph("正文在图片之前")
    document.add_picture(io.BytesIO(_png(300, 200, (230, 230, 230))))
    document.add_paragraph("正文在图片之后")
    path = tmp_path / "scan.docx"
    document.save(path)
    workspace_init(path, tmp_path / "wsd", config=_config())
    context = _context()
    figure = next(b for b in Workspace.open(tmp_path / "wsd").load().blocks if b.kind == BlockKind.FIGURE)
    env, _ = _call("recognize", tmp_path / "wsd", {"blocks": [figure.id], "engine": "paddleocr"}, context=context)
    assert env.ok and env.cost.requests == {"ocr": 1}
    env, _ = _call("export", tmp_path / "wsd", {"out": str(tmp_path / "outd")}, context=context)
    markdown = (tmp_path / "outd" / "scan.md").read_text()
    assert markdown.index("正文在图片之前") < markdown.index("<!-- 以下转录自上图 -->") < \
        markdown.index("SENTINEL-OCR 扫描文字") < markdown.index("正文在图片之后")


def test_a_picture_in_a_table_cell_is_exported_with_the_table(ws):
    # the recognize tool renders the page for a picture the engine left inside a cell (not only for figures)
    def page():
        cell = '<img src="imgs/img_in_image_box_100_300_300_400.jpg" alt="Image" /> 跨中'
        return {"prunedResult": {"width": 1000, "height": 1400, "parsing_res_list": [
            {"block_label": "table", "block_content": f"<table><tr><td>图示</td></tr><tr><td>{cell}</td></tr></table>",
             "block_bbox": [100, 280, 900, 500], "block_order": 1}]}}

    context = _context(page=page)
    _call("recognize", ws, {"pages": [2], "engine": "paddleocr"}, context=context)
    env, _ = _call("export", ws, {"out": str(ws.parent / "outp")}, context=context)
    md = Path(env.result.markdown).read_text()
    assert "img_in_image_box" not in md and "〔图1〕" in md
    assert md.index("〔图1〕") < md.index("<!-- 以下是上方〔图 n〕处的图片 -->") < md.rindex("](images/")


def test_ask_about_some_rows_of_a_table_sees_a_sharper_strip(ws):
    # a whole-table crop of a long table leaves its digits too small for the VLM (round 4, real_doc03_pdf):
    # asking about rows crops the band of those rows (with a row of margin) at a higher resolution
    from PIL import Image as _Image

    context = _context()
    _call("recognize", ws, {"pages": [2], "engine": "paddleocr"}, context=context)
    table = next(b for b in Workspace.open(ws).load().blocks if b.kind == BlockKind.TABLE)
    whole, _ = _call("read", ws, {"block": table.id, "image": "crop"}, context=context)
    env, _ = _call("ask_image", ws, {"block": table.id, "rows": [1, 1], "question": "第 1 行的数值？"}, context=context)
    assert env.ok and env.result.image != whole.result.image
    strip = next(p for p in (ws / "renders").iterdir() if p.stem == env.result.image)
    with _Image.open(strip) as image:
        assert image.width >= 1.9 * whole.result.image.width  # 300 dpi against read's 150
    env2, _ = _call("correct", ws, {"block": table.id, "image": env.result.image, "reason": "图上是 8",
                                    "cells": [{"row": 1, "col": 1, "content": "8"}]}, context=context)
    assert env2.result.adopted is True  # the strip is an image of this block
    bad, code = _call("ask_image", ws, {"block": table.id, "rows": [5, 9], "question": "?"}, context=context)
    assert not bad.ok and code == 2  # the table has 2 rows


def test_structure_changes_return_the_items_they_open(ws):
    # a title without a level is open work: the call that made it says so (the agent sees what its change opened)
    block = next(b for b in json.loads((ws / "state.json").read_text())["blocks"] if b["kind"] == "text")["id"]
    env, _ = _call("apply_structure", ws, {"changes": [{"op": "set_role", "block": block, "kind": "title",
                                                        "reason": "test"}]})
    data = _assert_contract(env, "apply_structure")
    assert [(u["target"], u["kind"]) for u in data["unresolved"]] == [(block, "structure_pending")]


def test_split_through_the_tool_reports_the_new_block(tmp_path):
    # the diff of apply_structure knows blocks created by the call (a split's second part)
    doc = fitz.open()
    doc.new_page().insert_text((72, 90), "3 Results\nThe measured values follow.", fontsize=11)
    doc.save(tmp_path / "two.pdf")
    ws = tmp_path / "ws"
    workspace_init(tmp_path / "two.pdf", ws, config=_config())
    block = next(b for b in json.loads((ws / "state.json").read_text())["blocks"] if "\n" in (b["text"] or ""))
    env, _ = _call("apply_structure", ws, {"changes": [{"op": "split", "block": block["id"], "at_break": 1,
                                                        "reason": "title joined to the next line"}]})
    data = _assert_contract(env, "apply_structure")
    assert env.ok and data["result"]["accepted"] == [0]
    assert any(c["target"] == block["id"] + "-s1" for c in data["diff"])


def test_an_uncertain_image_is_transcribed_only_for_text_its_description_does_not_carry(tmp_path):
    # P4-6 (conservation): the local reading of the image is compared with its description
    from types import SimpleNamespace

    from parserx.ir.enums import EvidenceLevel
    from parserx.ir.semantic import Evidenced, GenericSemantic
    from parserx.tools.process import _text_not_carried

    class Reader:
        version = "fake"

        def __init__(self, lines):
            self.lines = lines

        def read(self, png):
            return [((0, 0, 10, 10), text, 0.9) for text in self.lines]

    (tmp_path / "img.png").write_bytes(b"png")
    asset = SimpleNamespace(path="img.png")
    described = SimpleNamespace(semantic=GenericSemantic(
        type="other", summary=Evidenced(value="表单截图", level=EvidenceLevel.INFERRED),
        visible_text=[Evidenced(value="ipmi_address", level=EvidenceLevel.VISIBLE)]))

    def ctx(lines):
        return SimpleNamespace(reader=lambda: Reader(lines), ws=SimpleNamespace(root=tmp_path), cache=None)

    assert not _text_not_carried(ctx(["ipmi_address"]), described, asset)
    assert _text_not_carried(ctx(["ipmi_address", "Kg key for IPMIv2 authentication."]), described, asset)
    assert not _text_not_carried(ctx(["|", "·"]), described, asset)  # no letters or digits: no evidence


def test_without_a_scan_engine_the_document_still_exports_as_partial(ws):
    # `parserx parse --no-ocr`: scanned pages stay unrecognised, listed as missing; the run does not fail
    config = _config()
    config.builders.ocr.engine = "none"
    config.runtime.layout_shadow = False

    class NoScanEngine(_context()):
        def _new_ocr(self):
            return ToolContext._new_ocr(self)  # the real factory: refuses an engine that is not configured

    env, code = _call("process", ws, {}, config=config, context=NoScanEngine)
    assert code == 0
    result = _assert_contract(env, "process")["result"]
    assert result["pages"] == {"done": 1, "failed": 1} and result["check"]["exportable"]
    assert result["check"]["document_status"] == "partial"
    assert any("scan engine not configured" in f.message for f in env.failures)
