"""Hybrid runtime of ``parserx parse`` (plan P4-1): routing, fallbacks, resume, summary records — with a fake agent
that works on the workspace through the tools, as a real one does through ``px``."""

import json
import os
import stat

import pymupdf
import pytest

from parserx.runtimes.agent import AgentOutcome, agent_env
from parserx.runtimes.events import AgentAction, Notice, StageEnd
from parserx.runtimes.hybrid import WORK_DIR, agent_config, parse_document, prepare_agent_dir
from parserx.tools import call_tool
from tests.test_layout_routing import FakeDetector
from tests.test_runtime_pipeline import pdf  # noqa: F401  (fixture: a native page and a scanned page)
from tests.test_tools_contract import _config, _context


def _context_class(ocr_behaviour=None):
    base = _context(ocr_behaviour=ocr_behaviour)

    class Context(base):
        def _new_detector(self):
            return FakeDetector()

    return Context


class FakeAgent:
    engine, model, effort = "fake", "fake-model", "medium"

    def __init__(self, act=None, usable=(True, None), outcome=None):
        self.act, self.usable, self.outcome = act, usable, outcome
        self.runs = []

    def available(self):
        return self.usable

    def run(self, work_dir, deadline_s, log_dir, on_event=None):
        self.runs.append({"files": sorted(p.name for p in work_dir.iterdir()), "deadline_s": deadline_s,
                          "task": (work_dir / "AGENTS.md").read_text(encoding="utf-8"),
                          "config": (work_dir / "parserx.yaml").read_text(encoding="utf-8")})
        if self.act is not None:
            self.act(work_dir / "ws")
        return self.outcome or AgentOutcome(ok=True, wall_s=12.0, usd_at_list_price=0.2)


def _rerecognize(ws):
    """The agent retries the failed scan with a working engine: the document becomes complete."""
    envelope, _ = call_tool("recognize", ws, {"pages": [2], "engine": "paddleocr", "force": True},
                            config=_config(), context_factory=_context_class())
    assert envelope.ok and not envelope.failures
    envelope, _ = call_tool("process", ws, {}, config=_config(), context_factory=_context_class())  # titles
    assert envelope.ok


def _parse(pdf, tmp_path, agent=None, *, mode="hybrid", ocr_down=True, events=None):
    config = _config()
    config.runtime.mode = mode
    reporter = events.append if events is not None else (lambda e: None)
    return parse_document(pdf, tmp_path / "out", config, agent=agent, reporter=reporter,
                          context_class=_context_class(RuntimeError("scan engine down") if ocr_down else None))


def _summary(tmp_path):
    return json.loads((tmp_path / "out" / "doc.json").read_text(encoding="utf-8"))


def test_clean_document_skips_the_agent_and_leaves_no_work_directory(pdf, tmp_path):
    agent = FakeAgent()
    events = []
    outcome = _parse(pdf, tmp_path, agent, ocr_down=False, events=events)
    assert outcome.runtime == "fixed" and outcome.runtime_note == "no_review_items" and not agent.runs
    assert outcome.status == "complete" and outcome.review_open == 0
    assert StageEnd("agent", 0.0, skipped="no_review_items") in events
    assert _summary(tmp_path)["processing"]["runtime"] == "fixed"
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == ["doc.blocks.json", "doc.json", "doc.md", "images"]


def test_open_items_go_to_the_agent_whose_result_is_exported(pdf, tmp_path):
    agent = FakeAgent(act=_rerecognize)
    events = []
    outcome = _parse(pdf, tmp_path, agent, events=events)
    assert len(agent.runs) == 1 and outcome.runtime == "hybrid:agent" and outcome.status == "complete"
    run = agent.runs[0]
    assert run["files"] == ["AGENTS.md", "parserx.yaml", "px", "skills", "ws"] and run["deadline_s"] == 30 * 60
    assert "{{" not in run["task"] and "不要再运行 `workspace init`" in run["task"] and "实验" not in run["task"]
    assert "SENTINEL-OCR" in (tmp_path / "out" / "doc.md").read_text(encoding="utf-8")
    processing = _summary(tmp_path)["processing"]
    assert processing["runtime"] == "hybrid:agent" and processing["runtime_note"] is None
    record = processing["agent"]
    assert record["model"] == "fake-model" and record["tool_calls"] == 2 and record["usd_at_list_price"] == 0.2
    assert record["review_open_before"] > 0 and record["review_open_after"] == 0
    assert any(isinstance(e, AgentAction) and e.action == "recognize" and e.page == 2 for e in events)
    assert not (tmp_path / "out" / WORK_DIR).exists()


@pytest.mark.parametrize("usable,outcome,reason", [
    ((False, "codex_not_logged_in"), None, "codex_not_logged_in"),
    ((True, None), AgentOutcome(ok=False, reason="agent_failed", detail="exit code 1", wall_s=3.0), "agent_failed"),
    ((True, None), AgentOutcome(ok=False, reason="agent_timeout", detail="30 min", wall_s=1800.0), "agent_timeout"),
])
def test_an_agent_that_cannot_run_or_finish_falls_back_to_the_fixed_result(pdf, tmp_path, usable, outcome, reason):
    fixed = _parse(pdf, tmp_path / "fixed", mode="fixed")
    agent = FakeAgent(act=_rerecognize if outcome else None, usable=usable, outcome=outcome)
    result = _parse(pdf, tmp_path, agent)
    assert result.runtime == "hybrid:fallback" and result.runtime_note == reason
    # the fixed result, even when the agent changed the workspace before it failed
    assert (tmp_path / "out" / "doc.md").read_bytes() == (tmp_path / "fixed" / "out" / "doc.md").read_bytes()
    assert result.status == fixed.status == "partial" and result.review_open == fixed.review_open > 0
    assert _summary(tmp_path)["processing"]["runtime_note"].startswith(reason)
    # kept, so that the same command later continues with the agent
    assert result.work_dir == str(tmp_path / "out" / WORK_DIR)


def test_a_later_run_continues_from_the_kept_workspace(pdf, tmp_path):
    first = _parse(pdf, tmp_path, FakeAgent(usable=(False, "codex_not_found")))
    assert first.work_dir is not None
    ws = tmp_path / "out" / WORK_DIR / "agent" / "ws"
    txns = sum(1 for line in (ws / "calls.jsonl").read_text().splitlines() if '"type": "txn"' in line)
    events = []
    agent = FakeAgent(act=_rerecognize)
    again = _parse(pdf, tmp_path, agent, events=events)
    assert again.runtime == "hybrid:agent" and again.status == "complete" and len(agent.runs) == 1
    assert next(e for e in events if type(e).__name__ == "DocStart").resumed


def test_a_workspace_changed_outside_the_tools_is_not_used(pdf, tmp_path):
    def tamper(ws):
        raw = json.loads((ws / "state.json").read_text(encoding="utf-8"))
        raw["blocks"][0]["text"] = "rewritten outside the tools"
        (ws / "state.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    outcome = _parse(pdf, tmp_path, FakeAgent(act=tamper))
    assert outcome.runtime == "hybrid:fallback" and outcome.runtime_note == "workspace_tampered"
    assert "rewritten outside" not in (tmp_path / "out" / "doc.md").read_text(encoding="utf-8")
    assert outcome.work_dir is None and not (tmp_path / "out" / WORK_DIR).exists()


def test_fixed_mode_never_asks_the_agent(pdf, tmp_path):
    agent = FakeAgent()
    outcome = _parse(pdf, tmp_path, agent, mode="fixed")
    assert not agent.runs and outcome.runtime == "fixed" and outcome.runtime_note == "mode_fixed"
    assert outcome.status == "partial" and outcome.work_dir is None


def test_interrupted_run_keeps_its_workspace_and_resumes(pdf, tmp_path):
    def interrupt(ws):
        _rerecognize(ws)  # some work done through the tools
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        _parse(pdf, tmp_path, FakeAgent(act=interrupt))
    work = tmp_path / "out" / WORK_DIR
    assert (work / "agent" / "ws" / "state.json").is_file()
    events = []
    outcome = _parse(pdf, tmp_path, FakeAgent(), events=events)
    assert next(e for e in events if type(e).__name__ == "DocStart").resumed
    assert outcome.status == "complete" and outcome.runtime == "fixed"  # the agent's earlier work was kept
    assert not work.exists()


def test_interrupted_tool_call_still_claims_its_changes(pdf, tmp_path, monkeypatch):
    from parserx.tools import structure
    from parserx.workspace import verify_workspace

    ws = tmp_path / "ws"
    from parserx.tools import workspace_init

    workspace_init(pdf, ws, config=_config())
    real = structure.run

    def run_then_interrupt(ctx, req):
        real(ctx, req)
        raise KeyboardInterrupt

    monkeypatch.setattr(structure, "run", run_then_interrupt)
    from parserx.tools import TOOLS, ToolSpec

    monkeypatch.setitem(TOOLS, "apply_structure", ToolSpec(structure.ApplyStructureRequest,
                                                          structure.ApplyStructureResult, run_then_interrupt))
    block = json.loads((ws / "state.json").read_text())["blocks"][0]["id"]
    with pytest.raises(KeyboardInterrupt):
        call_tool("apply_structure", ws, {"changes": [{"op": "set_role", "block": block, "kind": "title",
                                                       "level": 1, "reason": "test"}]},
                  config=_config(), context_factory=_context_class())
    assert verify_workspace(ws).ok
    last = json.loads((ws / "calls.jsonl").read_text().splitlines()[-1])
    assert last["envelope"]["failures"][0]["code"] == "interrupted" and last["txns"]


def test_service_keys_never_enter_the_agent_directory_or_environment(tmp_path):
    config = _config()
    config.services.vlm.api_key = "sk-SECRET-VLM"
    config.builders.ocr.token = "tok-SECRET-OCR"
    config.cache.mode = "read_write"
    agent_dir, keys = tmp_path / "agent", tmp_path / "keys" / "services.env"
    agent_dir.mkdir()
    prepare_agent_dir(agent_dir, config, keys, input_name="doc.pdf", minutes=30)
    for path in agent_dir.rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            assert "sk-SECRET-VLM" not in text and "tok-SECRET-OCR" not in text, path
    text, secrets = agent_config(config, agent_dir)
    assert set(secrets.values()) == {"sk-SECRET-VLM", "tok-SECRET-OCR"}
    assert f"dir: {agent_dir / '.parserx_cache'}" in text
    assert "SECRET" in keys.read_text() and stat.S_IMODE(os.stat(keys).st_mode) == 0o600
    assert str(keys) in (agent_dir / "px").read_text()
    env = agent_env({"PATH": "/bin", "OPENAI_API_KEY_B": "x", "SOME_TOKEN": "y", "ENDPOINT": "e", "OTHER": "sk-SECRET-VLM",
                     "HOME": "/h"}, {"ENDPOINT"}, secrets.values())
    assert env == {"PATH": "/bin", "HOME": "/h"}


def test_px_of_the_agent_directory_runs_the_tools(pdf, tmp_path):
    # the generated px starts this installation's tools with the directory's config (no services needed here)
    import subprocess

    config = _config()
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    prepare_agent_dir(agent_dir, config, tmp_path / "keys.env", input_name="doc.pdf", minutes=30)
    from parserx.tools import workspace_init

    workspace_init(pdf, agent_dir / "ws", config=config)
    proc = subprocess.run(["./px", "tool", "overview", "--ws", "ws", "--json"], cwd=agent_dir, capture_output=True,
                          text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["ok"]
    refused = subprocess.run(["./px", "parse", "doc.pdf"], cwd=agent_dir, capture_output=True, text=True,
                             timeout=60)
    assert refused.returncode == 2


def test_agent_actions_are_read_from_the_call_records():
    from parserx.runtimes.actions import AgentTally, actions

    def rec(tool, request, result, ok=True):
        return {"type": "call", "tool": tool, "request": request, "envelope": {"ok": ok}, "result": result}

    texts = {"b-p004-0002": "技术领域"}
    records = [
        rec("ask_image", {"questions": [{"page": 4, "question": "是标题吗？"}, {"block": "b-p004-0002", "question": "?"}]},
            {}),
        rec("apply_structure", {"changes": [{"op": "set_role", "block": "b-p004-0002", "kind": "title", "level": 2},
                                            {"op": "set_level", "block": "b-p005-0001", "level": 3}]},
            {"accepted": [0], "rejected": [{"index": 1}]}),
        rec("close", {"target": "p1", "kind": "title_candidate", "reason": "封面信息，不是节标题"}, {"closed": True}),
        rec("correct", {"image": "a", "reason": "r", "add": {"page": 2, "text": "授权公告日 2020-01-01"}},
            {"adopted": True}),
        rec("correct", {"block": "b-p003-0001", "image": "a", "reason": "r", "edits": [{"find": "3", "replace": "8"}]},
            {"adopted": False}),
        rec("overview", {}, {}),
        {"type": "txn", "version": 3},
    ]
    got = [a for r in records for a in actions(r, texts.get)]
    assert [(a.action, a.target, a.page) for a in got] == [
        ("look", "p4", 4), ("look", "b-p004-0002", 4), ("set_title", "b-p004-0002", 4), ("rejected", None, None),
        ("close", "p1", 1), ("add", "p2", 2), ("rejected", "b-p003-0001", 3)]
    assert got[2].text == "技术领域" and got[2].level == 2 and got[4].detail == "封面信息，不是节标题"
    joins = rec("apply_structure", {"changes": [{"op": "add_relation", "kind": "continues", "src": f"b-p001-000{i}",
                                                 "dst": f"b-p001-000{i + 1}"} for i in range(5)]},
                {"accepted": [0, 1, 2, 3, 4], "rejected": []})
    assert [(a.action, a.page, a.count) for a in actions(joins)] == [("join", 1, 5)]
    titles = rec("apply_structure", {"changes": [{"op": "set_role", "block": f"b-p00{i}-0001", "kind": "title",
                                                  "level": 1} for i in range(1, 6)]},
                 {"accepted": [0, 1, 2, 3, 4], "rejected": []})
    assert [a.action for a in actions(titles)] == ["set_title"] * 5  # each title is worth its own line
    docx = actions(rec("ask_image", {"block": "b-d00093", "question": "?"}, {}), {"b-d00093": "5.2支座加工："}.get)
    assert (docx[0].page, docx[0].text) == (None, "5.2支座加工：")
    tally = AgentTally()
    for r in records:
        tally.add(r)
    assert (tally.tool_calls, tally.changes, tally.added, tally.closed) == (6, 1, 1, 1)


def test_codex_availability_is_decided_by_return_codes(tmp_path):
    from parserx.runtimes.agent import CodexAgent

    fake = tmp_path / "codex"
    fake.write_text("#!/bin/sh\n[ \"$1\" = login ] && exit ${FAKE_LOGIN:-0}\nexit 0\n")
    fake.chmod(0o755)
    env = {"PATH": f"{tmp_path}:/bin:/usr/bin"}
    assert CodexAgent("m", "medium", env=env, executable=str(fake)).available() == (True, None)
    assert CodexAgent("m", "medium", env={**env, "FAKE_LOGIN": "1"}, executable=str(fake)).available() == \
        (False, "codex_not_logged_in")
    assert CodexAgent("m", "medium", env=env, executable=str(tmp_path / "missing")).available() == \
        (False, "codex_not_found")


def test_codex_runs_past_its_deadline_are_stopped(tmp_path):
    from parserx.runtimes.agent import CodexAgent

    fake = tmp_path / "codex"
    fake.write_text("#!/bin/sh\necho '{\"type\":\"thread.started\",\"thread_id\":\"t\"}'\nsleep 30\n")
    fake.chmod(0o755)
    work = tmp_path / "work"
    work.mkdir()
    outcome = CodexAgent("m", "medium", env={"PATH": "/bin:/usr/bin"}, executable=str(fake)).run(
        work, 1.0, tmp_path / "log")
    assert not outcome.ok and outcome.reason == "agent_timeout" and outcome.wall_s < 20
    assert outcome.usage.thread_id == "t"
