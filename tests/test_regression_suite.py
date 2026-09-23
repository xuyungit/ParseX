"""Regression tiers (guide §9.1): document lists, multiple ground-truth dirs, repeat checks."""

import pytest

from parserx.config.schema import ParserXConfig
from parserx.eval.gate import evaluate_gate, run_record
from parserx.eval.metrics import EvalResult
from parserx.eval.runner import EvalRunner, NotReplayable
from parserx.eval.suite import read_doc_list, repeat_mismatches, run_suite


def _gt(root, *names):
    for name in names:
        d = root / name
        d.mkdir(parents=True)
        (d / "expected.md").write_text("# x\n", encoding="utf-8")
        (d / "input.pdf").write_bytes(b"%PDF-1.4 fake")
    return root


def _runner(monkeypatch, outputs=None, raise_for=()):
    runner = EvalRunner(ParserXConfig())

    def fake(input_path, expected_md_path, name=""):
        if name in raise_for:
            raise NotReplayable("cache miss (vlm 1); rerun with calls allowed")
        runner.outputs[name] = (outputs or {}).get(name, f"output of {name}")
        return EvalResult(document_name=name)

    monkeypatch.setattr(runner, "evaluate_single", fake)
    return runner


def test_doc_list_ignores_comments_and_blank_lines(tmp_path):
    path = tmp_path / "core.txt"
    path.write_text("# header\ndeepseek\n\nreceipt  # scanned\n", encoding="utf-8")
    assert read_doc_list(path) == ["deepseek", "receipt"]


def test_suite_spans_directories_and_reports_unknown_names_once(tmp_path, monkeypatch):
    private = _gt(tmp_path / "private", "a", "b")
    public = _gt(tmp_path / "public", "c")
    run = run_suite(_runner(monkeypatch), [private, public], {"a", "c", "missing"})
    assert sorted(r.document_name for r in run.results) == ["a", "c"]
    assert [name for name, _ in run.not_executed] == ["missing"]
    assert set(run.outputs) == {"a", "c"}


def test_duplicate_names_across_directories_are_rejected(tmp_path, monkeypatch):
    first = _gt(tmp_path / "one", "same")
    second = _gt(tmp_path / "two", "same")
    with pytest.raises(ValueError, match="same"):
        run_suite(_runner(monkeypatch), [first, second], None)


def test_offline_miss_is_not_executed(tmp_path, monkeypatch):
    root = _gt(tmp_path / "gt", "a", "b")
    run = run_suite(_runner(monkeypatch, raise_for={"b"}), [root], None)
    assert [r.document_name for r in run.results] == ["a"]
    assert run.not_executed[0][0] == "b" and "cache miss" in run.not_executed[0][1]


def test_repeat_mismatch_is_a_hard_failure(tmp_path, monkeypatch):
    root = _gt(tmp_path / "gt", "a", "b")
    first = run_suite(_runner(monkeypatch), [root], None)
    again = run_suite(_runner(monkeypatch, outputs={"b": "different"}), [root], None)
    mismatches = repeat_mismatches(first.outputs, again.outputs)
    assert mismatches == ["b"]

    record = run_record(first.results, failed=[], not_executed=[])
    record["not_reproducible"] = mismatches
    assert evaluate_gate(record, baseline=None).exit_code == 2
