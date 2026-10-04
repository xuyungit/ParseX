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
    engine, model, effort, adapter = "fake", "fake-model", "medium", "cli"

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
    """The agent reads the failed scan with a working engine and adopts the reading: the document becomes complete."""
    envelope, _ = call_tool("view_source", ws, {"looks": [{"page": 2, "as": "text"}]}, config=_config(),
                            context_factory=_context_class())
    assert envelope.ok and not envelope.failures
    evidence = envelope.result.results[0].evidence
    envelope, _ = call_tool("edit_draft", ws, {"ops": [{"op": "adopt", "page": 2, "evidence": evidence,
                                                        "reason": "扫描页"}]},
                            config=_config(), context_factory=_context_class())
    assert envelope.ok and envelope.result.outcomes[0].accepted, envelope.result
    titles = [u.target for u in envelope.result.issues_opened if u.kind == "structure_pending"]  # a new title's level
    envelope, _ = call_tool("edit_draft", ws, {"ops": [{"op": "set_role", "block": b, "role": "H2", "reason": "节标题"}
                                                       for b in titles]},
                            config=_config(), context_factory=_context_class())
    assert envelope.ok and all(o.accepted for o in envelope.result.outcomes)


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
    assert "{{" not in run["task"] and "read_draft" in run["task"] and "实验" not in run["task"]
    assert "SENTINEL-OCR" in (tmp_path / "out" / "doc.md").read_text(encoding="utf-8")
    processing = _summary(tmp_path)["processing"]
    assert processing["runtime"] == "hybrid:agent" and processing["runtime_note"] is None
    record = processing["agent"]
    assert record["model"] == "fake-model" and record["tool_calls"] == 3 and record["usd_at_list_price"] == 0.2
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
    from parserx.tools import edit

    real, calls = edit.unresolved_items, []

    def interrupt_after_the_commit(state):  # the call's second look at the issues: its change is committed
        calls.append(1)
        if len(calls) == 2:
            raise KeyboardInterrupt
        return real(state)

    monkeypatch.setattr(edit, "unresolved_items", interrupt_after_the_commit)
    block = json.loads((ws / "state.json").read_text())["blocks"][0]["id"]
    with pytest.raises(KeyboardInterrupt):
        call_tool("edit_draft", ws, {"ops": [{"op": "set_role", "block": block, "role": "H1", "reason": "test"}]},
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


def test_other_models_keys_stay_behind(tmp_path):
    # Q100: the agent's tools use the model their places name; the entries of the others (and their keys) stay out
    from parserx.config.schema import ParserXConfig

    config = ParserXConfig.model_validate({
        "models": {"used": {"endpoint": "https://u", "model": "u", "api_key": "sk-SECRET-USED"},
                   "other": {"endpoint": "https://o", "model": "o", "api_key": "sk-SECRET-OTHER"}},
        "services": {"vlm": {"use": "used"}}})
    text, secrets = agent_config(config, tmp_path)
    import yaml

    assert set(secrets.values()) == {"sk-SECRET-USED"} and "sk-SECRET-OTHER" not in text
    assert "models" not in yaml.safe_load(text)

    assert ParserXConfig.model_validate(yaml.safe_load(text)).services.vlm.model == "u"  # loads without the entries


def test_px_of_the_agent_directory_runs_the_tools(pdf, tmp_path):
    # the generated px starts this installation's tools with the directory's config (no services needed here)
    import subprocess

    config = _config()
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    prepare_agent_dir(agent_dir, config, tmp_path / "keys.env", input_name="doc.pdf", minutes=30)
    from parserx.tools import workspace_init

    workspace_init(pdf, agent_dir / "ws", config=config)
    proc = subprocess.run(["./px", "tool", "read_draft", "--ws", "ws", "--json"], cwd=agent_dir, capture_output=True,
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
        rec("view_source", {"looks": [{"page": 4, "as": "answer", "question": "是标题吗？"},
                                      {"block": "b-p004-0002", "as": "answer", "question": "?"}]}, {}),
        rec("edit_draft", {"ops": [{"op": "set_role", "block": "b-p004-0002", "role": "H2"},
                                   {"op": "set_role", "block": "b-p005-0001", "role": "H3"}]},
            {"outcomes": [{"index": 0, "accepted": True}, {"index": 1, "accepted": False}]}),
        rec("edit_draft", {"ops": [{"op": "dismiss", "issue": "w-1", "reason": "封面信息，不是节标题"},
                                   {"op": "insert_text", "page": 2, "text": "授权公告日 2020-01-01"},
                                   {"op": "replace_text", "block": "b-p003-0001", "find": "3", "replace": "8"}]},
            {"outcomes": [{"index": 0, "accepted": True, "target": "p1"}, {"index": 1, "accepted": True},
                          {"index": 2, "accepted": False}]}),
        rec("read_draft", {}, {}),
        {"type": "txn", "version": 3},
    ]
    got = [a for r in records for a in actions(r, texts.get)]
    assert [(a.action, a.target, a.page) for a in got] == [
        ("look", "p4", 4), ("look", "b-p004-0002", 4), ("set_title", "b-p004-0002", 4), ("rejected", None, None),
        ("close", "p1", 1), ("add", "p2", 2), ("rejected", None, None)]
    assert got[2].text == "技术领域" and got[2].level == 2 and got[4].detail == "封面信息，不是节标题"
    joins = rec("edit_draft", {"ops": [{"op": "join", "first": f"b-p001-000{i}", "second": f"b-p001-000{i + 1}"}
                                       for i in range(5)]},
                {"outcomes": [{"index": i, "accepted": True} for i in range(5)]})
    assert [(a.action, a.page, a.count) for a in actions(joins)] == [("join", 1, 5)]
    titles = rec("edit_draft", {"ops": [{"op": "set_role", "block": f"b-p00{i}-0001", "role": "H1"}
                                        for i in range(1, 6)]},
                 {"outcomes": [{"index": i, "accepted": True} for i in range(5)]})
    assert [a.action for a in actions(titles)] == ["set_title"] * 5  # each title is worth its own line
    docx = actions(rec("view_source", {"looks": [{"block": "b-d00093", "as": "answer", "question": "?"}]}, {}),
                   {"b-d00093": "5.2支座加工："}.get)
    assert (docx[0].page, docx[0].text) == (None, "5.2支座加工：")
    tally = AgentTally()
    for r in records:
        tally.add(r)
    assert (tally.tool_calls, tally.changes, tally.added, tally.closed) == (4, 1, 1, 1)


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



def test_codex_at_capacity_is_tried_again_and_continues_the_workspace(tmp_path):
    # round 1: "Selected model is at capacity" ended 3 of our 69 agent runs; the workspace holds what the agent did
    from parserx.runtimes.agent import CodexAgent

    fake = tmp_path / "codex"
    fake.write_text(
        "#!/bin/sh\nn=$(cat \"$FAKE_COUNT\" 2>/dev/null || echo 0); n=$((n+1)); echo $n > \"$FAKE_COUNT\"\n"
        "echo '{\"type\":\"thread.started\",\"thread_id\":\"t'$n'\"}'\n"
        "if [ $n -le ${FAKE_FAILS:-1} ]; then\n"
        "  echo '{\"type\":\"turn.failed\",\"error\":{\"message\":\"'\"$FAKE_ERROR\"'\"}}'; exit 1\nfi\n"
        "echo '{\"type\":\"turn.completed\",\"usage\":{\"input_tokens\":5}}'\nexit 0\n")
    fake.chmod(0o755)

    def run(name, fails, error):
        work = tmp_path / name
        work.mkdir()
        env = {"PATH": "/bin:/usr/bin", "FAKE_COUNT": str(tmp_path / f"{name}.count"), "FAKE_FAILS": str(fails),
               "FAKE_ERROR": error}
        agent = CodexAgent("m", "medium", env=env, executable=str(fake), capacity_retries=2, backoff_s=0.01)
        return agent.run(work, 60.0, tmp_path / f"{name}.log")

    once = run("once", 1, "Selected model is at capacity. Please try a different model.")
    assert once.ok and once.retries == 1 and once.usage.input_tokens == 5
    always = run("always", 9, "Selected model is at capacity. Please try a different model.")
    assert not always.ok and always.retries == 2  # a bounded number of tries
    other = run("other", 1, "tool call rejected")  # not a capacity failure: not tried again
    assert not other.ok and other.retries == 0

def test_px_reads_no_personal_config(tmp_path, monkeypatch):
    # Q107: the agent's config is complete; the personal file (every model's key) is not read under it
    from pathlib import Path

    import parserx.cli
    from parserx.runtimes import px

    seen = {}
    monkeypatch.setattr(parserx.cli, "main", lambda: seen.setdefault("dir", os.environ["PARSERX_CONFIG_DIR"]))
    (tmp_path / "parserx.yaml").write_text("{}\n")
    px.main(["--env-file", str(tmp_path / "none.env"), "--config", str(tmp_path / "parserx.yaml"), "--",
             "tool", "read_draft", "--ws", "ws", "--view", "summary"])
    assert Path(seen["dir"]).parent == tmp_path and not Path(seen["dir"]).exists()


def test_agent_when_always_hands_a_clean_document_to_the_agent(pdf, tmp_path):
    # Q135: what the program does not list, the agent may still see
    agent = FakeAgent()
    config = _config()
    config.runtime.agent_when = "always"
    outcome = parse_document(pdf, tmp_path / "out", config, agent=agent, reporter=lambda e: None,
                             context_class=_context_class(None))
    assert outcome.review_open == 0 and agent.runs and outcome.runtime == "hybrid:agent"


def test_the_readers_entries_go_with_their_keys_as_references(tmp_path):
    # F (2026-10-01): with every entry left behind, the readers the tools name (tools.recheck_readers) were not found
    # and the agent's corrections were read again by the service model instead; the named readers' entries go along,
    # their keys replaced by references as the others are
    import yaml

    from parserx.config.schema import ParserXConfig

    config = ParserXConfig.model_validate({
        "models": {"used": {"endpoint": "https://u", "model": "u", "api_key": "sk-SECRET-USED"},
                   "reader": {"endpoint": "https://r", "model": "r", "api_key": "sk-SECRET-READER"},
                   "other": {"endpoint": "https://o", "model": "o", "api_key": "sk-SECRET-OTHER"}},
        "services": {"vlm": {"use": "used"}},
        "tools": {"second_readers": [{"use": "reader"}], "recheck_readers": [{"use": "reader"}]}})
    text, secrets = agent_config(config, tmp_path)
    assert "sk-SECRET-READER" not in text and "sk-SECRET-OTHER" not in text
    assert set(secrets.values()) == {"sk-SECRET-USED", "sk-SECRET-READER"}
    loaded = ParserXConfig.model_validate(yaml.safe_load(text))
    assert set(loaded.models) == {"reader"} and loaded.models["reader"].api_key.startswith("${")


def test_agent_config_carries_the_formula_editors_entry(tmp_path):
    # the formula editor (tools.formula_editor) is looked up by entry as the readers are: its entry goes along
    import yaml

    from parserx.config.schema import ParserXConfig

    config = ParserXConfig.model_validate({
        "models": {"used": {"endpoint": "https://u", "model": "u", "api_key": "sk-SECRET-USED"},
                   "editor": {"endpoint": "https://e", "model": "e", "api_key": "sk-SECRET-EDITOR"},
                   "other": {"endpoint": "https://o", "model": "o", "api_key": "sk-SECRET-OTHER"}},
        "services": {"vlm": {"use": "used"}},
        "tools": {"second_readers": [], "recheck_readers": [], "formula_editor": {"use": "editor"}}})
    text, secrets = agent_config(config, tmp_path)
    assert "sk-SECRET-EDITOR" not in text and "sk-SECRET-OTHER" not in text
    loaded = ParserXConfig.model_validate(yaml.safe_load(text))
    assert set(loaded.models) == {"editor"}


def test_agent_config_carries_the_scan_engines_account_entry(tmp_path):
    # GLM-OCR takes its key from the entry builders.ocr.glm.account names: left behind, every region the agent asked
    # to be read again failed "scan engine not configured" and it typed a 26-row table by hand (speed plan, 2026-10-05)
    import yaml

    from parserx.config.schema import ParserXConfig
    from parserx.services.glm_ocr import glm_api_key

    config = ParserXConfig.model_validate({
        "builders": {"ocr": {"engine": "glm-ocr", "glm": {"account": "zhipu"}}},
        "models": {"used": {"endpoint": "https://u", "model": "u", "api_key": "sk-SECRET-USED"},
                   "zhipu": {"endpoint": "https://z", "model": "z", "api_key": "sk-SECRET-ZHIPU"},
                   "other": {"endpoint": "https://o", "model": "o", "api_key": "sk-SECRET-OTHER"}},
        "services": {"vlm": {"use": "used"}},
        "tools": {"second_readers": [], "recheck_readers": [], "formula_editor": None}})
    text, secrets = agent_config(config, tmp_path)
    assert "sk-SECRET-ZHIPU" not in text and "sk-SECRET-OTHER" not in text
    loaded = ParserXConfig.model_validate(yaml.safe_load(text))
    assert set(loaded.models) == {"zhipu"} and glm_api_key(loaded).startswith("${")
