"""Benchmark tooling (docs/v2_benchmark_plan.md): reading each tool's recorded response, the runner's reuse of
results, scoring, and the comparison page's server — all offline."""

import base64
import io
import json
import threading
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from parserx.tool_eval import adapters, runner
from parserx.tool_eval.adapters import (
    ToolAdapter,
    ToolRun,
    read_datalab_result,
    read_llamaparse_result,
    read_mineru_zip,
    read_paddle_jsonl,
)
from parserx.tool_eval.viewer import ManualScores, make_handler

EXPECTED = "# 标题\n\n正文一段。\n\n| 甲 | 乙 |\n|---|---|\n| 1 | 2 |\n"


def _gt(root: Path, name: str, *, meta: dict | None = None, expected: str | None = EXPECTED) -> Path:
    doc = root / name
    doc.mkdir(parents=True)
    (doc / "input.docx").write_bytes(b"not read")
    if expected is not None:
        (doc / "expected.md").write_text(expected, encoding="utf-8")
    if meta is not None:
        (doc / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return doc


def test_mineru_zip_keeps_markdown_images_and_json_but_not_the_input_copy(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("full.md", "# T\n\n![](images/a.jpg)\n")
        zf.writestr("images/a.jpg", b"jpg")
        zf.writestr("x_content_list.json", "[]")
        zf.writestr("x_origin.pdf", b"%PDF")
    md = read_mineru_zip(buf.getvalue(), tmp_path)
    assert md.startswith("# T")
    assert (tmp_path / "images" / "a.jpg").read_bytes() == b"jpg"
    assert (tmp_path / "raw" / "x_content_list.json").exists()
    assert not list(tmp_path.rglob("*origin.pdf"))


def test_datalab_result_writes_images_where_the_markdown_refers_and_reports_cost(tmp_path):
    result = {"success": True, "markdown": "![说明](p_img.jpg)\n", "images": {"p_img.jpg": base64.b64encode(b"x").decode()},
              "cost_breakdown": {"final_cost_cents": 3.0}, "metadata": {"failed_pages": []}}
    run = read_datalab_result(result, tmp_path, config={"mode": "accurate"})
    assert (tmp_path / "p_img.jpg").read_bytes() == b"x"
    assert run.cost_usd == 0.03
    assert "markdown" not in json.loads((tmp_path / "raw" / "result.json").read_text())
    with pytest.raises(RuntimeError, match="failed"):
        read_datalab_result({"success": False, "error": "bad"}, tmp_path, config={})


def test_paddle_pages_are_joined_in_order_and_empty_pages_skipped():
    def line(*texts):
        pages = [{"markdown": {"text": t, "images": {f"imgs/{i}.jpg": f"http://x/{i}"} if t.strip() else {}}}
                 for i, t in enumerate(texts)]
        return json.dumps({"result": {"layoutParsingResults": pages}})

    md, images = read_paddle_jsonl(line("第一页", " ") + "\n" + line("第四页") + "\n")
    assert md == "第一页\n\n第四页\n"
    assert set(images) == {"imgs/0.jpg"}
    with pytest.raises(RuntimeError):
        read_paddle_jsonl(json.dumps({"errorCode": 500, "errorMsg": "boom"}))


def test_llamaparse_images_are_downloaded_and_presigned_urls_not_kept(tmp_path, monkeypatch):
    class Resp:
        content = b"png"

    monkeypatch.setattr(adapters.requests, "get", lambda url, timeout: Resp())
    result = {"markdown_full": "# A\n", "job": {"usage": {"credits": 10.0}},
              "images_content_metadata": {"images": [{"filename": "img_p1_1.png", "presigned_url": "https://s3/secret"}]}}
    run = read_llamaparse_result(result, tmp_path, config={}, usd_per_credit=0.00125)
    assert run.markdown == "# A\n" and run.cost_usd == 0.0125 and run.cost_note == "10 credits"
    assert (tmp_path / "images" / "img_p1_1.png").read_bytes() == b"png"
    assert "secret" not in (tmp_path / "raw" / "result.json").read_text()


def test_annotation_groups_and_document_discovery(tmp_path):
    gt = tmp_path / "gt"
    _gt(gt, "omni", meta={"source": "OmniDocBench"})
    _gt(gt, "ours")
    _gt(gt, "ref", meta={"annotation_origin": "parserx_reference"})
    _gt(gt, "no_annotation", expected=None)
    docs = {d.name: d for d in runner.find_documents([gt])}
    assert set(docs) == {"omni", "ours", "ref"}
    assert (docs["omni"].group, docs["ours"].group, docs["ref"].group) == \
        ("independent", "llamaparse_draft", "parserx_reference")
    assert [d.name for d in runner.find_documents([gt], ["no_annotation"])] == ["no_annotation"]
    with pytest.raises(KeyError):
        runner.find_documents([gt], ["missing"])


class _Fake(ToolAdapter):
    name, label = "fake", "Fake"

    def __init__(self, fail: bool = False):
        self.calls, self.fail = 0, fail

    def parse(self, input_path, out_dir):
        self.calls += 1
        if self.fail:
            raise RuntimeError("service down")
        return ToolRun(markdown=EXPECTED, cost_usd=0.01)


def test_results_on_disk_are_reused_failures_are_recorded_and_everything_is_scored(tmp_path):
    gt, out = tmp_path / "gt", tmp_path / "out"
    _gt(gt, "doc")
    docs = runner.find_documents([gt])
    tool = _Fake()
    runner.run_tools([tool], docs, out, log=lambda *_: None)
    runner.run_tools([tool], docs, out, log=lambda *_: None)
    assert tool.calls == 1
    runner.run_tools([tool], docs, out, force=True, log=lambda *_: None)
    assert tool.calls == 2

    broken = _Fake(fail=True)
    broken.name = "broken"
    runner.run_tools([broken], docs, out, log=lambda *_: None)
    meta = json.loads((out / "broken" / "doc" / "meta.json").read_text())
    assert meta["status"] == "error" and "service down" in meta["error"]

    record = runner.score(out, [gt])
    assert record["scores"]["fake"]["doc"]["char_f1"] == 1.0
    assert "broken" not in record["scores"]
    report = (out / "report.md").read_text()
    assert "LlamaParse 初稿" in report and "service down" in report


def test_manual_scores_keep_valid_dimensions_and_an_empty_entry_removes_the_score(tmp_path):
    store = ManualScores(tmp_path / "manual.json")
    saved = store.save("doc", "fake", {"info": 4, "overall": 9, "bogus": 3, "note": " 标题少了一级 "})
    assert saved["info"] == 4 and "overall" not in saved and "bogus" not in saved and saved["note"] == "标题少了一级"
    store.save("doc", "fake", {"info": None, "note": ""})
    assert store.load() == {"doc": {}}


def test_comparison_server_lists_results_and_saves_manual_scores(tmp_path):
    gt, out = tmp_path / "gt", tmp_path / "out"
    _gt(gt, "doc")
    runner.run_tools([_Fake()], runner.find_documents([gt]), out, log=lambda *_: None)
    runner.score(out, [gt])
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(out, [gt]))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        index = json.loads(urllib.request.urlopen(f"{base}/api/index").read())
        assert [s["id"] for s in index["sources"]] == ["fake"]
        assert index["docs"][0]["results"]["fake"]["scores"]["char_f1"] == 1.0
        assert urllib.request.urlopen(f"{base}/src/fake/doc/output.md").read().decode() == EXPECTED
        assert urllib.request.urlopen(f"{base}/src/expected/doc/expected.md").read().decode() == EXPECTED
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(f"{base}/src/fake/doc/..%2F..%2Fgt%2Fdoc%2Fexpected.md")
        body = json.dumps({"document": "doc", "source": "fake", "scores": {"overall": 5}}).encode()
        req = urllib.request.Request(f"{base}/api/manual", data=body, headers={"Content-Type": "application/json"})
        assert json.loads(urllib.request.urlopen(req).read())["saved"]["overall"] == 5
        assert json.loads((out / "manual_scores.json").read_text())["doc"]["fake"]["overall"] == 5
    finally:
        server.shutdown()
