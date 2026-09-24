"""Agent experiments (plan P2-1, §2.2–§2.5): experiment directories, the px wrapper, post-run verification."""

import json

import pytest
import yaml

from parserx.config.schema import ParserXConfig
from parserx.runtimes.experiment import (
    config_problems,
    doc_config,
    listing_problems,
    prepare_doc_dir,
    render_task,
    secret_names,
    verify_run,
)
from parserx.runtimes.px import PxRefused, px_argv, python_env
from parserx.tools import call_tool, workspace_init
from parserx.workspace import Workspace

RAW = {
    "services": {"vlm": {"endpoint": "${OPENAI_BASE_URL_B}", "api_key": "${OPENAI_API_KEY_B}",
                         "model": "${VLM_MODEL_B:gpt-6-luna}"}},
    "builders": {"ocr": {"token": "${PADDLE_OCR_TOKEN}"}},
    "cache": {"mode": "read_write", "dir": ".parserx_cache"},
}


def _prepare(tmp_path, repo):
    doc_dir = tmp_path / "exp" / "r1" / "doc"
    source = tmp_path / "input.docx"
    source.write_bytes(b"PK-synthetic")
    config = doc_config(RAW, doc_dir)
    prepare_doc_dir(doc_dir, input_path=source, config=config, px_text="#!/bin/sh\nexec /x/px-run \"$@\"\n",
                    agents_md="# Task\n", skills={"transcription": "t", "figure": "f", "structure": "s"})
    return doc_dir


def test_experiment_directory_holds_only_the_listed_files(tmp_path):
    repo = tmp_path / "repo"
    doc_dir = _prepare(tmp_path, repo)
    names = sorted(str(p.relative_to(doc_dir)) for p in doc_dir.rglob("*"))
    assert names == ["AGENTS.md", "input.docx", "parserx.yaml", "px", "skills", "skills/figure.md",
                     "skills/structure.md", "skills/transcription.md"]
    assert (doc_dir / "px").stat().st_mode & 0o111
    assert listing_problems(doc_dir) == []
    (doc_dir / "expected.md").write_text("answer")
    (doc_dir / "run").mkdir()
    assert listing_problems(doc_dir) == ["unexpected entry: expected.md", "unexpected entry: run"]


def test_config_keeps_secrets_as_placeholders_and_the_cache_inside(tmp_path):
    doc_dir = _prepare(tmp_path, tmp_path / "repo")
    text = (doc_dir / "parserx.yaml").read_text()
    config = yaml.safe_load(text)
    assert config["services"]["vlm"]["api_key"] == "${OPENAI_API_KEY_B}"
    assert config["cache"] == {"mode": "read_write", "dir": str(doc_dir / ".parserx_cache")}
    assert secret_names(RAW) == ["OPENAI_API_KEY_B", "OPENAI_BASE_URL_B", "PADDLE_OCR_TOKEN", "VLM_MODEL_B"]
    assert config_problems(text, forbidden=[tmp_path / "repo"], secret_values=["sk-live-123"]) == []
    leaked = text + f"\n# {tmp_path / 'repo'}/ground_truth\n# sk-live-123\n"
    assert len(config_problems(leaked, forbidden=[tmp_path / "repo"], secret_values=["sk-live-123"])) == 2


def test_px_fixes_the_config_and_allows_only_the_tools(tmp_path):
    cfg = tmp_path / "parserx.yaml"
    assert px_argv(["tool", "read", "--ws", "ws", "--page", "1", "--json"], cfg) == [
        "tool", "read", "--ws", "ws", "--page", "1", "--json", "--config", str(cfg)]
    assert px_argv(["workspace", "init", "input.pdf", "--ws", "ws"], cfg)[-2:] == ["--config", str(cfg)]
    assert px_argv(["tool", "schema", "read"], cfg) == ["tool", "schema", "read"]
    for refused in (["parse", "input.pdf"], ["eval", "gt"], ["tool", "read", "--config", "x.yaml"],
                    ["tool", "read", "-c", "x.yaml"], ["tool", "frobnicate"], ["workspace", "rm"]):
        with pytest.raises(PxRefused):
            px_argv(refused, cfg)


def test_px_python_runs_without_service_keys():
    env = python_env({"PATH": "/usr/bin", "OPENAI_API_KEY_B": "sk", "PADDLE_OCR_TOKEN": "t", "MY_SECRET": "s",
                      "OPENAI_BASE_URL_B": "https://x", "HOME": "/h"}, secret_names={"OPENAI_BASE_URL_B"})
    assert env == {"PATH": "/usr/bin", "HOME": "/h"}


def _docx(path):
    import docx

    document = docx.Document()
    document.add_heading("SENTINEL 标题", level=1)
    document.add_paragraph("SENTINEL 正文 100 万元")
    document.save(path)
    return path


def test_verify_run_checks_integrity_export_and_counts_tool_calls(tmp_path):
    doc_dir = tmp_path / "doc"
    doc_dir.mkdir()
    source = _docx(doc_dir / "input.docx")
    config = ParserXConfig()
    workspace_init(source, doc_dir / "ws", config=config)
    call_tool("overview", doc_dir / "ws", {}, config=config)
    call_tool("read", doc_dir / "ws", {"page": 99}, config=config)  # a failed call
    env, _ = call_tool("export", doc_dir / "ws", {"out": str(doc_dir / "out")}, config=config)
    assert env.ok

    report = verify_run(doc_dir)
    assert report.integrity.ok and report.export.exported and report.export.current
    assert report.export.markdown == str(doc_dir / "out" / "input.md")
    assert report.tools.calls == {"export": 1, "overview": 1, "read": 1, "workspace_init": 1}
    assert report.tools.failures == {"not_found": 1}
    assert report.check.exportable and report.check.document_status == "complete"

    # a change after the export makes the exported Markdown stale; a hand-written file is extra
    first = min(Workspace.open(doc_dir / "ws").load().blocks, key=lambda b: b.order)
    call_tool("apply_structure", doc_dir / "ws", {"changes": [
        {"op": "set_role", "block": first.id, "kind": "title", "reason": "test"},
        {"op": "set_level", "block": first.id, "level": 1, "reason": "test"}]}, config=config)
    (doc_dir / "out" / "mine.md").write_text("# hand made")
    report = verify_run(doc_dir)
    assert report.export.exported and not report.export.current
    assert report.export.extra_files == ["mine.md"]


def test_verify_run_reports_direct_changes(tmp_path):
    doc_dir = tmp_path / "doc"
    doc_dir.mkdir()
    source = _docx(doc_dir / "input.docx")
    workspace_init(source, doc_dir / "ws", config=ParserXConfig())
    raw = json.loads((doc_dir / "ws" / "state.json").read_text())
    raw["blocks"][1]["text"] = "改写"
    (doc_dir / "ws" / "state.json").write_text(json.dumps(raw, ensure_ascii=False))
    report = verify_run(doc_dir)
    assert not report.integrity.ok and not report.export.exported


def test_task_template_renders_one_round_and_every_value():
    template = "# {{input_name}}\n{{#r1}}r1 rule\n{{/r1}}{{#r2}}r2 rule\n{{/r2}}budget {{minutes}}\n"
    assert render_task(template, round_name="r1", values={"input_name": "input.pdf", "minutes": 30}) == \
        "# input.pdf\nr1 rule\nbudget 30\n"
    assert render_task(template, round_name="r2", values={"input_name": "input.pdf", "minutes": 30}) == \
        "# input.pdf\nr2 rule\nbudget 30\n"
    with pytest.raises(KeyError):
        render_task(template, round_name="r1", values={"input_name": "input.pdf"})
    with pytest.raises(KeyError):
        render_task(template, round_name="r3", values={"input_name": "input.pdf", "minutes": 30})


def test_shipped_task_differs_between_rounds_only_in_the_round_rules():
    from importlib.resources import files

    template = (files("parserx.runtimes") / "agent_task.md").read_text(encoding="utf-8")
    values = {"input_name": "input.pdf", "budget_minutes": 30}
    r1 = render_task(template, round_name="r1", values=values)
    assert r1 == render_task(template, round_name="r1", values=values)  # stable
    r2 = render_task(template, round_name="r2", values=values)
    section = "## 本轮规则"

    def outside_rules(text):
        head, rest = text.split(section, 1)
        return head + rest[rest.index("\n## "):]

    assert outside_rules(r1) == outside_rules(r2) and r1 != r2
    for text in (r1, r2):
        assert "{{" not in text and "input.pdf" in text and "30 分钟" in text
        for tool in ("process", "overview", "read", "recognize", "review_table", "correct", "describe_figure",
                     "apply_structure", "check", "export", "tool schema", "doc_text"):
            assert tool in text
    # the two ways of seeing images differ only where images are read
    agent = render_task(template, round_name="r1", values=values, options={"vision_agent"})
    tool = render_task(template, round_name="r1", values=values, options={"vision_tool"})
    assert "--image crop" in agent and "ask_image" not in agent
    assert "ask_image" in tool and "--image crop" not in tool


def test_control_runs_the_fixed_sequence_in_an_experiment_directory(tmp_path):
    from parserx.runtimes.experiment import run_control

    doc_dir = tmp_path / "control"
    doc_dir.mkdir()
    _docx(doc_dir / "input.docx")
    (doc_dir / "parserx.yaml").write_text(yaml.safe_dump(doc_config({"pipeline": "v2"}, doc_dir)))
    outcome = run_control(doc_dir)
    assert outcome["status"] == "complete" and outcome["error"] is None
    report = verify_run(doc_dir)
    assert report.integrity.ok and report.export.exported and report.export.current
    assert "# SENTINEL 标题" in (doc_dir / "out" / "input.md").read_text()


def test_task_template_keeps_the_blocks_of_the_chosen_options():
    template = "{{#r1}}r1\n{{/r1}}{{#vision_agent}}look yourself\n{{/vision_agent}}{{#vision_tool}}ask the tool\n{{/vision_tool}}"
    assert render_task(template, round_name="r1", values={}, options={"vision_tool"}) == "r1\nask the tool\n"
    assert render_task(template, round_name="r1", values={}, options={"vision_agent"}) == "r1\nlook yourself\n"
