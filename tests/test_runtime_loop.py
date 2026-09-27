"""Our own agent loop (Q86, Q88): the model calls the four tools as functions, in-process — here a scripted model —
and the adapters that turn the conversation into each API's request."""

import json
from pathlib import Path

import pymupdf

from parserx.config.schema import PriceConfig
from parserx.runtimes.loop import LoopAgent, _clear
from parserx.runtimes.models import (
    Answer,
    ChatModel,
    Note,
    Reply,
    ResponsesModel,
    ToolCall,
    ToolResult,
    Usage,
    inline_refs,
)
from parserx.tools import tool_schema, workspace_init
from tests.test_runtime_hybrid import _context_class, _parse, _summary
from tests.test_runtime_pipeline import pdf  # noqa: F401  (fixture: a native page and a scanned page)
from tests.test_tools_contract import _config


class ScriptedModel:
    """Reads the page the draft lacks, adopts the reading, sets the new titles' level, submits, reports."""

    name = "model-x"

    def __init__(self):
        self.turns = []

    def answer(self, system, tools, history, *, timeout):
        self.turns.append({"system": system, "tools": tools, "history": list(history)})
        results = [json.loads(e.text) for e in history if isinstance(e, ToolResult)]
        n = len(self.turns)
        if not results:
            calls = [ToolCall(f"c{n}", "view_source", json.dumps({"looks": [{"page": 2, "as": "text"}]}))]
        elif len(results) == 1:
            evidence = results[0]["result"]["results"][0]["evidence"]
            calls = [ToolCall(f"c{n}", "edit_draft", json.dumps(
                {"ops": [{"op": "adopt", "page": 2, "evidence": evidence, "reason": "扫描页"}]}))]
        elif len(results) == 2:
            titles = [i["target"] for i in results[1]["result"]["issues_opened"] if i["kind"] == "structure_pending"]
            calls = [ToolCall(f"c{n}", "edit_draft", json.dumps(
                         {"ops": [{"op": "set_role", "block": b, "role": "H2", "reason": "节标题"} for b in titles]})),
                     ToolCall(f"c{n}b", "submit_draft", "{}")]  # two calls in one turn, run in order
        else:
            return Answer(Reply("交稿被接受；没有未解决项。", []), Usage(1000, 600, 50))
        return Answer(Reply("", calls), Usage(1000, 600, 50))


def test_the_loop_works_through_the_tools_as_functions(pdf, tmp_path):
    model = ScriptedModel()
    agent = LoopAgent("model-x", "medium", config=_config(), context_class=_context_class(),
                      model_factory=lambda timeout: model)
    outcome = _parse(pdf, tmp_path, agent)
    assert outcome.runtime == "hybrid:agent" and outcome.status == "complete", outcome.runtime_detail
    first = model.turns[0]
    assert [t["name"] for t in first["tools"]] == ["read_draft", "view_source", "edit_draft", "submit_draft"]
    assert first["tools"][2]["parameters"]["properties"]["ops"]  # the request model is the function's parameters
    assert "函数调用" in first["system"] and "./px" not in first["system"] and "**`set_role`**" in first["system"]
    assert len(model.turns) == 4 and isinstance(first["history"][0], Note)
    record = _summary(tmp_path)["processing"]["agent"]
    assert record["tool_calls"] == 4 and record["model"] == "model-x"


def test_the_agent_is_told_to_submit_when_its_budget_is_reached(pdf, tmp_path):
    class Reader:
        name = "reader"
        notes: list = []

        def answer(self, system, tools, history, *, timeout):
            self.notes = [e.text for e in history if isinstance(e, Note)]
            if any("submit_draft" in n for n in self.notes):
                return Answer(Reply("交稿。", []), Usage(10, 0, 10))
            return Answer(Reply("", [ToolCall(f"c{len(history)}", "read_draft", "{}")]), Usage(1_000_000, 0, 0))

    reader = Reader()
    config = _config()
    config.runtime.agent.budget_usd = 1.0
    agent = LoopAgent("reader", "medium", config=config, price=PriceConfig(input=1.0, output=1.0),
                      context_class=_context_class(), model_factory=lambda timeout: reader)
    workspace_init(pdf, tmp_path / "agent" / "ws", config=_config())
    outcome = agent.run(tmp_path / "agent", deadline_s=600, log_dir=tmp_path / "log")
    assert outcome.ok and any("预算快用完了" in n for n in reader.notes)


def test_the_agent_sees_its_image_looks_when_it_looks_itself(tmp_path):
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 90), "SENTINEL page", fontsize=12)
    doc.save(tmp_path / "d.pdf")
    workspace_init(tmp_path / "d.pdf", tmp_path / "ws", config=_config())
    agent = LoopAgent("m", "medium", config=_config(), model_factory=lambda timeout: None)
    look = ToolCall("c", "view_source", json.dumps({"looks": [{"page": 1, "as": "image"}]}))
    images = agent._call(tmp_path / "ws", look, "agent").images
    assert len(images) == 1 and Path(images[0]).is_file()
    assert agent._call(tmp_path / "ws", look, "tool").images == []  # the service VLM answers instead
    assert json.loads(agent._call(tmp_path / "ws", ToolCall("c", "nope", "{}"), "tool").text)["ok"] is False


def test_a_cleared_context_gets_the_agents_notes_back(tmp_path):
    from parserx.runtimes.loop import _notes
    from parserx.tools import call_tool

    doc = pymupdf.open()
    doc.new_page().insert_text((72, 90), "SENTINEL page", fontsize=12)
    doc.save(tmp_path / "d.pdf")
    workspace_init(tmp_path / "d.pdf", tmp_path / "ws", config=_config())
    assert "还没有理解记录" in _notes(tmp_path / "ws")
    call_tool("edit_draft", tmp_path / "ws", {"ops": [{"op": "note", "scope": "全文", "text": "一页的说明"}]},
              config=_config())
    assert "- [n-001] 全文：一页的说明" in _notes(tmp_path / "ws")


def test_older_tool_results_are_cleared_at_once_past_the_threshold():
    history = [ToolResult(ToolCall(f"c{i}", "read_draft", json.dumps({"view": "text", "page": i})), "x" * 100)
               for i in range(7)]
    assert _clear(history, context_tokens=50_000, threshold=100_000) == 0
    assert _clear(history, context_tokens=120_000, threshold=100_000) == 3  # the latest four stay
    assert history[0].cleared.startswith("[已清理：read_draft") and '"page": 0' in history[0].cleared
    assert history[3].cleared is None
    assert _clear(history, context_tokens=120_000, threshold=100_000) == 0  # nothing older left


def test_images_are_cleared_down_to_half_the_limit_the_oldest_first(tmp_path):
    looks = [ToolResult(ToolCall(f"c{i}", "view_source", "{}"), "{}", [tmp_path / f"{i}.png", tmp_path / f"{i}b.png"])
             for i in range(4)]  # 8 images: over the limit of 6
    assert _clear(looks, context_tokens=0, threshold=100_000) == 3  # down to 2 images, the latest look's
    assert [e.cleared is None for e in looks] == [False, False, False, True]


def test_a_clearing_waits_for_the_context_to_grow_again(pdf, tmp_path):
    class Growing:
        """Each turn reads the draft again; the context grows by 30k tokens a turn and drops after a clearing."""

        name = "growing"
        contexts: list = []

        def answer(self, system, tools, history, *, timeout):
            live = sum(1 for e in history if isinstance(e, ToolResult) and e.cleared is None)
            context = 10_000 + 30_000 * live
            self.contexts.append(context)
            if len(self.contexts) >= 12:
                return Answer(Reply("完成。", []), Usage(context, 0, 10))
            return Answer(Reply("", [ToolCall(f"c{len(history)}", "read_draft", "{}")]), Usage(context, 0, 10))

    config = _config()
    config.runtime.agent.clear_at_tokens = 100_000
    agent = LoopAgent("growing", "medium", config=config, context_class=_context_class(),
                      model_factory=lambda timeout: Growing())
    workspace_init(pdf, tmp_path / "agent" / "ws", config=_config())
    assert agent.run(tmp_path / "agent", deadline_s=600, log_dir=tmp_path / "log").ok
    events = [e for line in (tmp_path / "log" / "trace.jsonl").read_text().splitlines()
              for e in json.loads(line)["events"]]
    assert 1 <= len(events) <= 3  # not every turn once past the threshold


# ── the adapters ────────────────────────────────────────────────────────


def _history(tmp_path):
    image = tmp_path / "p1.png"
    image.write_bytes(b"\x89PNG fake")
    look, read = ToolCall("c1", "view_source", '{"looks": [{"page": 1}]}'), ToolCall("c2", "read_draft", "{}")
    raw = ("responses", [{"type": "reasoning", "encrypted_content": "x"}, {"type": "function_call", "call_id": "c1"}])
    return [Note("开始"), Reply("", [look, read], raw=raw), ToolResult(look, '{"ok": true}', [image]),
            ToolResult(read, '{"ok": true, "result": {}}'), Note("上下文已清理")]


def test_the_responses_request_hands_back_the_providers_items_and_the_images(tmp_path):
    items = ResponsesModel(None, "m", "medium").input("任务", _history(tmp_path))
    assert items[0] == {"role": "developer", "content": "任务"} and items[1] == {"role": "user", "content": "开始"}
    assert items[2]["type"] == "reasoning" and items[3]["type"] == "function_call"  # verbatim, to the same API
    assert items[4]["call_id"] == "c1" and items[4]["output"][1]["image_url"].startswith("data:image/png;base64,")
    assert items[5]["output"] == '{"ok": true, "result": {}}' and items[6]["role"] == "user"


def test_the_chat_request_puts_a_turns_images_after_its_tool_results(tmp_path):
    messages = ChatModel(None, "m").messages("任务", _history(tmp_path))
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "tool", "tool", "user", "user"]
    assert [c["id"] for c in messages[2]["tool_calls"]] == ["c1", "c2"]  # rebuilt: another API's items stay out
    assert messages[5]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_a_cleared_result_goes_as_its_placeholder_without_images(tmp_path):
    history = _history(tmp_path)
    history[2].cleared = "[已清理]"
    assert ResponsesModel(None, "m", None).input("任务", history)[4]["output"] == "[已清理]"
    assert [m["role"] for m in ChatModel(None, "m").messages("任务", history)][-2:] == ["tool", "user"]


def test_references_are_written_out_for_apis_without_them():
    flat = inline_refs(tool_schema("edit_draft")["request"])
    text = json.dumps(flat)
    assert "$ref" not in text and "$defs" not in text and "discriminator" not in text
    assert any(o["properties"]["op"].get("const") == "set_role" for o in flat["properties"]["ops"]["items"]["oneOf"])


def test_the_chat_request_can_mark_cache_breakpoints(tmp_path):
    class Client:
        def __init__(self):
            self.request = None
            self.chat = type("C", (), {"completions": type("P", (), {"create": self.create})()})()

        def create(self, **request):
            self.request = request
            message = type("M", (), {"content": "好", "tool_calls": None})()
            return type("R", (), {"choices": [type("Ch", (), {"message": message})()], "usage": None})()

    client = Client()
    ChatModel(client, "m", cache_markers=True).answer("任务", [], _history(tmp_path), timeout=10)
    messages = client.request["messages"]
    assert messages[0]["content"][-1]["cache_control"] == {"type": "ephemeral"}  # the fixed prefix
    assert messages[-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}  # the history so far
    assert all("cache_control" not in str(m) for m in messages[1:-1])
