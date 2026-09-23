"""Frozen runs (guide §8.4, §9.2 item 6): self-contained, replayable baselines."""

import json

from parserx.config.schema import ParserXConfig
from parserx.eval.freeze import (
    document_inventory,
    git_state,
    replay_differences,
    run_id_for,
    write_frozen_run,
)


def _gt(root, name, suffix=".pdf"):
    d = root / name
    d.mkdir(parents=True)
    (d / "expected.md").write_text(f"# {name}\n", encoding="utf-8")
    (d / f"input{suffix}").write_bytes(b"%PDF-1.4 " + name.encode())
    return d


def _record(**docs):
    return {
        "metric_version": "2.0",
        "config_fingerprint": "abc",
        "documents": {
            name: {"char_f1": score, "output_sha256": f"h-{name}", "wall_time_seconds": 3.0,
                   "requests": {"ocr": 1, "vlm": 2, "llm": 0}, "cache_hits": {}, "attempts": {"ocr": 1},
                   "ocr_pages": 4, "cost_usd": None}
            for name, score in docs.items()
        },
        "failed": [],
        "not_executed": [],
    }


def test_run_id_carries_date_and_label():
    assert run_id_for("p0_v1_gpt-6-luna", today="2026-09-23") == "2026-09-23_p0_v1_gpt-6-luna"


def test_inventory_hashes_inputs_and_marks_isolation(tmp_path):
    private = tmp_path / "ground_truth"
    _gt(private, "a")
    _gt(private, "held_out")
    inventory = document_inventory({"a": private, "held_out": private}, isolation={"held_out"})
    assert inventory["held_out"]["isolation"] is True and inventory["a"]["isolation"] is False
    assert inventory["a"]["gt_dir"] == "ground_truth"
    assert len(inventory["a"]["input_sha256"]) == 64 and len(inventory["a"]["expected_sha256"]) == 64
    assert inventory["a"]["input_sha256"] != inventory["held_out"]["input_sha256"]


def test_write_frozen_run_is_self_describing(tmp_path):
    run_dir = tmp_path / "eval_runs" / "2026-09-23_x"
    record = _record(a=0.9)
    manifest = {"run_id": "2026-09-23_x", "documents": {"a": {"isolation": False}}}
    write_frozen_run(run_dir, record=record, outputs={"a": "# out\n"}, manifest=manifest)
    assert json.loads((run_dir / "metrics.json").read_text()) == record
    assert json.loads((run_dir / "manifest.json").read_text())["run_id"] == "2026-09-23_x"
    assert (run_dir / "outputs" / "a.md").read_text() == "# out\n"


def test_replay_ignores_volatile_fields_but_not_scores_or_outputs():
    frozen = _record(a=0.9, b=0.8)
    replay = _record(a=0.9, b=0.8)
    for doc in replay["documents"].values():  # a replay makes no requests and runs faster
        doc.update(wall_time_seconds=0.1, requests={"ocr": 0, "vlm": 0, "llm": 0},
                   cache_hits={"ocr": 1, "vlm": 2}, attempts={}, ocr_pages=0)
    assert replay_differences(replay, frozen) == []

    replay["documents"]["b"]["char_f1"] = 0.7
    replay["documents"]["a"]["output_sha256"] = "changed"
    diffs = replay_differences(replay, frozen)
    assert any("b" in d and "char_f1" in d for d in diffs)
    assert any("a" in d and "output_sha256" in d for d in diffs)


def test_replay_reports_missing_documents_and_config_changes():
    frozen = _record(a=0.9, b=0.8)
    replay = _record(a=0.9)
    replay["config_fingerprint"] = "other"
    diffs = replay_differences(replay, frozen)
    assert any("b" in d and "missing" in d for d in diffs)
    assert any("config" in d for d in diffs)


def test_git_state_outside_a_repository(tmp_path):
    state = git_state(tmp_path)
    assert state["commit"] is None
