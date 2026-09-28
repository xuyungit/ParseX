"""Tests for CLI config resolution visibility."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from parserx.cli import _cmd_compare, _cmd_eval
from parserx.eval.metrics import CostMetrics, EvalResult, HeadingMetrics, TableMetrics, TextMetrics


def _fake_result(name: str = "demo") -> EvalResult:
    return EvalResult(
        document_name=name,
        text=TextMetrics(edit_distance=0.1, char_precision=0.9, char_recall=0.9, char_f1=0.9),
        headings=HeadingMetrics(precision=1.0, recall=1.0, f1=1.0, detected_count=1, expected_count=1, correct_count=1),
        tables=TableMetrics(detected_count=1, expected_count=1, matched_count=1, cell_precision=1.0, cell_recall=1.0, cell_f1=1.0, column_accuracy=1.0),
        cost=CostMetrics(wall_time_seconds=0.5),
    )


def test_cmd_compare_warns_when_both_configs_omitted(
    tmp_path: Path,
    monkeypatch,
    caplog,
    capsys,
):
    from parserx.eval.runner import EvalRunner

    seen_configs = []

    def fake_evaluate_dir(self, ground_truth_dir: Path, *, include_docs=None):
        seen_configs.append(self._config)
        return [_fake_result()]

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(EvalRunner, "evaluate_dir", fake_evaluate_dir)

    args = argparse.Namespace(
        ground_truth=tmp_path,
        config_a=None,
        config_b=None,
        label_a="Base",
        label_b="Experiment",
        overrides_a=[],
        overrides_b=[],
        output=None,
        verbose=False,
    )

    with caplog.at_level(logging.WARNING):
        _cmd_compare(args)

    captured = capsys.readouterr()
    assert "# ParserX Compare Report" in captured.out
    assert "Compare Base config: no project parserx.yaml or personal config" in caplog.text
    assert "Compare Experiment config: no project parserx.yaml or personal config" in caplog.text
    assert len(seen_configs) == 2
    assert all(config.builders.ocr.engine == "paddleocr" for config in seen_configs)


def test_cmd_eval_logs_resolved_project_config_path(
    tmp_path: Path,
    monkeypatch,
    caplog,
    capsys,
):
    from parserx.eval.runner import EvalRunner

    config_file = tmp_path / "parserx.yaml"
    config_file.write_text("builders:\n  ocr:\n    engine: none\n", encoding="utf-8")

    def fake_evaluate_dir(self, ground_truth_dir: Path, *, include_docs=None):
        return [_fake_result()]

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(EvalRunner, "evaluate_dir", fake_evaluate_dir)

    args = argparse.Namespace(
        ground_truth=tmp_path,
        config=None,
        overrides=[],
        output=None,
        verbose=False,
    )

    with caplog.at_level(logging.INFO):
        _cmd_eval(args)

    captured = capsys.readouterr()
    assert "# ParserX Evaluation Report" in captured.out
    assert f"Eval config: {config_file.resolve()}" in caplog.text


def test_cmd_eval_supports_include_list(
    tmp_path: Path,
    monkeypatch,
    caplog,
    capsys,
):
    from parserx.eval.runner import EvalRunner

    seen_include_docs = []
    include_list = tmp_path / "subset.txt"
    include_list.write_text("# comment\nsample-a\nsample-b\n", encoding="utf-8")

    def fake_evaluate_dir(self, ground_truth_dir: Path, *, include_docs=None):
        seen_include_docs.append(include_docs)
        return [_fake_result("sample-a")]

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(EvalRunner, "evaluate_dir", fake_evaluate_dir)

    args = argparse.Namespace(
        ground_truth=tmp_path,
        config=None,
        overrides=[],
        include_docs=[],
        include_list=include_list,
        output=None,
        verbose=False,
    )

    with caplog.at_level(logging.INFO):
        _cmd_eval(args)

    captured = capsys.readouterr()
    assert "# ParserX Evaluation Report" in captured.out
    assert seen_include_docs == [{"sample-a", "sample-b"}]
    assert "Doc filter active: 2 document(s)" in caplog.text


def test_every_parse_option_is_read():
    # Phase 5 (Q75): `parserx parse --help` lists only options the command uses
    import re
    from pathlib import Path

    import parserx.cli as cli

    parser = cli.build_parser()
    parse = next(a for a in parser._subparsers._group_actions[0].choices.items() if a[0] == "parse")[1]
    options = {a.dest for a in parse._actions if a.dest not in ("help", "input")}
    code = "".join(Path(m.__file__).read_text(encoding="utf-8") for m in (cli, __import__("parserx.console.cli",
                                                                                                fromlist=["x"])))
    unread = sorted(d for d in options if not re.search(rf'args\.{d}\b|getattr\(args, "{d}"', code))
    assert unread == []


def test_parse_chooses_the_agent_and_the_service_model(tmp_path, monkeypatch, capsys):
    # R2: --agent codex | <model>, --no-agent, --vlm <model>; a name the config does not have is refused
    from parserx.cli import _cmd_parse, _collect_flag_overrides
    from parserx.config.schema import apply_overrides, load_config

    monkeypatch.chdir(tmp_path)
    base = load_config()

    def chosen(**flags):
        args = argparse.Namespace(no_vlm=False, no_ocr=False, vlm=None, agent=None, no_agent=False, runtime=None)
        vars(args).update(flags)
        return apply_overrides(base, _collect_flag_overrides(args))

    loop = chosen(agent="deepseek-flash", vlm="glm-5.3-flashx")
    assert (loop.runtime.agent.engine, loop.runtime.agent.model, loop.runtime.agent.api) == ("loop", "deepseek-flash", "chat")
    assert loop.services.vlm.model == "glm-5.3-flashx" and loop.runtime.mode == "hybrid"
    assert chosen(agent="codex").runtime.agent.engine == "codex"
    assert chosen(no_agent=True).runtime.mode == "fixed"
    args = argparse.Namespace(input=[tmp_path / "a.pdf"], config=None, overrides=[], no_vlm=False, no_ocr=False,
                              vlm="nope", agent=None, no_agent=False, runtime=None)
    assert _cmd_parse(args) == 2 and "no model 'nope'" in capsys.readouterr().err
