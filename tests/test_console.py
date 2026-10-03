"""Console of ``parserx parse`` (plan P4-2): event sequences rendered on a terminal and elsewhere, fallback and
interrupt texts in both languages, the command's JSON and exit codes."""

import io
import json
from pathlib import Path

import pytest

from parserx.console.reporter import ConsoleReporter, display_width, fit
from parserx.ir.state import Missing
from parserx.render.summary import AgentRecord
from parserx.runtimes.events import (
    AgentAction,
    DocEnd,
    DocStart,
    Notice,
    ReviewCount,
    StageEnd,
    StageStart,
    Step,
    Waiting,
)
from parserx.runtimes.hybrid import ParseOutcome


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def _outcome(tmp_path, **changes):
    values = dict(name="专利说明书", source="专利说明书.pdf", format="pdf", out_dir=str(tmp_path),
                  markdown=str(tmp_path / "专利说明书.md"), summary=str(tmp_path / "专利说明书.json"),
                  blocks=str(tmp_path / "专利说明书.blocks.json"), status="complete", runtime="hybrid:agent", pages=14,
                  tables=2, images=3, titles=9, review_open=0, review_by_kind={}, missing=[], wall_s=181.0,
                  service_usd=0.012, agent=AgentRecord(engine="codex", model="gpt-6-sol", effort="medium",
                                                       wall_s=138.0, usd_at_list_price=0.26, tool_calls=14,
                                                       changes=6, added=0, closed=3, review_open_before=9,
                                                       review_open_after=0))
    values.update(changes)
    return ParseOutcome(**values)


def _agent_run(reporter, clock, outcome):
    events = [
        (0.0, StageStart("read")),
        (0.8, DocStart(name="专利说明书", source="专利说明书.pdf", format="pdf", pages=14, scanned=5)),
        (0.8, StageEnd("read", 0.8)),
        (0.8, StageStart("process")),
        (0.8, Step("process", "recognize", total=5)),
        (12.8, Waiting("ocr", 12.0, "pending")),
        (30.8, Step("process", "reading", done=0, total=14)),
        (38.0, Step("process", "reading", done=14, total=14)),
        (38.0, Step("process", "describe", total=3)),
        (40.0, Step("process", "structure")),
        (41.0, Step("process", "check")),
        (41.8, StageEnd("process", 41.0)),
        (41.8, ReviewCount(9, {"title_candidate": 7, "text_unaccounted": 2})),
        (41.8, StageStart("agent", {"engine": "codex", "model": "gpt-6-sol"})),
        (60.0, AgentAction("look", target="p4", page=4, detail="这一行是节标题吗？")),
        (80.0, AgentAction("set_title", target="b-p004-0002", page=4, text="技术领域", level=2)),
        (90.0, AgentAction("close", target="p1", page=1, detail="封面信息，不是节标题")),
        (95.0, AgentAction("join", page=1, count=11)),
        (96.0, AgentAction("rejected", target="b-p001-0003", page=1, detail="correct")),
        (179.8, StageEnd("agent", 138.0, detail={"changes": 6, "added": 0, "closed": 3, "open": 0})),
        (179.8, StageStart("export")),
        (180.0, StageEnd("export", 0.2)),
        (181.0, DocEnd(outcome)),
    ]
    for at, event in events:
        clock.now = at
        reporter(event)
    reporter.close()


EXPECTED_ZH = """\
ParserX · 专利说明书.pdf（14 页，其中 5 页扫描）
[1/4] 读取文档                                  ✓ 0.8 s
[2/4] 标准处理
      识别扫描页 5 页                           ✓ 30 s
      本地读数 14/14 页                         ✓ 7.2 s
      图片描述 3 张                             ✓ 2.0 s
      标题与结构 · 检查                         ✓ 1.8 s
      待核对 9 项：标题候选 7 · 页面上有而输出里没有的文字 2
[3/4] Agent 复核（Codex · gpt-6-sol）
      看图  第 4 页 · 这一行是节标题吗？
      设为标题  技术领域 → 2 级
      关闭  第 1 页 · 封面信息，不是节标题
      接续  11 处 · 第 1 页
      未采用  第 1 页 · 修改未通过程序核对
      ✓ 2 分 18 秒 · 修改 6 处 · 补入 0 处 · 关闭 3 项 · 剩余 0 项
[4/4] 导出                                      ✓ 0.2 s
完成  {md}
      状态 complete · 14 页 · 表格 2 · 图片 3 · 标题 9 · 待核对 0 项
      用时 3 分 01 秒 · 费用约 $0.27（服务 $0.01 + Agent 按标价 $0.26）
      其余文件：images/ · 专利说明书.json · 专利说明书.blocks.json
"""


def test_a_hybrid_run_off_a_terminal_is_one_line_per_step(tmp_path):
    stream, clock = io.StringIO(), Clock()
    outcome = _outcome(tmp_path)
    _agent_run(ConsoleReporter(stream, lang="zh", tty=False, clock=clock), clock, outcome)
    assert stream.getvalue() == EXPECTED_ZH.format(md=outcome.markdown)


def test_on_a_terminal_the_step_in_progress_is_redrawn_and_then_cleared(tmp_path):
    stream, clock = io.StringIO(), Clock()
    outcome = _outcome(tmp_path)
    _agent_run(ConsoleReporter(stream, lang="zh", tty=True, tick_s=0, clock=clock), clock, outcome)
    raw = stream.getvalue()
    assert "\r\x1b[2K" in raw and "服务排队中 12 s" in raw  # the live line, with the service's queue
    # what stays on screen: the same lines as off a terminal
    screen = []
    for chunk in raw.split("\n")[:-1]:
        screen.append(chunk.split("\r\x1b[2K")[-1])
    assert "\n".join(screen) + "\n" == EXPECTED_ZH.format(md=outcome.markdown)


def test_english_interface(tmp_path):
    stream, clock = io.StringIO(), Clock()
    _agent_run(ConsoleReporter(stream, lang="en", tty=False, clock=clock), clock, _outcome(tmp_path))
    text = stream.getvalue()
    assert text.startswith("ParserX · 专利说明书.pdf (14 pages, 5 scanned)\n[1/4] Read document")
    assert "[3/4] Agent review (Codex · gpt-6-sol)" in text and "set title  技术领域 → level 2" in text
    assert "9 items to review: title candidates 7 · text on the page but not in the output 2" in text
    assert "time 3 min 01 s · cost about $0.27 (services $0.01 + agent at list price $0.26)" in text


@pytest.mark.parametrize("reason,lang,expected", [
    ("codex_not_logged_in", "zh", "⚠ Agent 未运行：Codex CLI 未登录（运行 `codex login` 后可用）"),
    ("codex_not_found", "zh", "⚠ Agent 未运行：未找到 Codex CLI"),
    ("codex_not_logged_in", "en", "⚠ Agent not run: Codex CLI is not logged in (run `codex login`)"),
])
def test_agent_not_available(tmp_path, reason, lang, expected):
    stream = io.StringIO()
    reporter = ConsoleReporter(stream, lang=lang, tty=False)
    reporter(DocStart(name="专利说明书", source="专利说明书.pdf", format="pdf", pages=14, scanned=0))
    reporter(ReviewCount(9, {"title_candidate": 9}))
    reporter(StageStart("agent", {"engine": "codex", "model": "gpt-6-sol"}))
    reporter(StageEnd("agent", 0.0, ok=False, skipped=reason))
    text = stream.getvalue()
    assert expected in text
    if lang == "zh":
        assert "已输出标准处理的结果，另有 9 项待核对，见 专利说明书.json 的 review" in text
        assert "再次运行同一命令会接着交给 Agent 处理" in text


def test_agent_failures_say_why_and_what_was_written(tmp_path):
    stream = io.StringIO()
    reporter = ConsoleReporter(stream, lang="zh", tty=False)
    reporter(ReviewCount(2, {"text_unaccounted": 2}))
    reporter(StageEnd("agent", 1800.0, ok=False, detail={"reason": "agent_timeout", "detail": "30 min"}))
    reporter(StageEnd("agent", 20.0, ok=False, detail={"reason": "workspace_tampered", "detail": "x"}))
    text = stream.getvalue()
    assert "⚠ Agent 超过截止时间（30 min），已停止" in text
    assert "⚠ Agent 绕过工具改动了工作区，它的修改全部不采用" in text
    assert text.count("再次运行同一命令会接着交给 Agent 处理") == 1  # not after a tampered workspace


def test_partial_result_lists_what_is_missing_and_service_errors(tmp_path):
    stream = io.StringIO()
    reporter = ConsoleReporter(stream, lang="zh", tty=False)
    reporter(Notice("tool_failures", "warning", {"failures": [
        {"code": "service_error", "message": "HTTP 503 upstream", "retryable": True, "targets": ["p2", "p3"]}]}))
    reporter(DocEnd(_outcome(tmp_path, status="partial", runtime="fixed", agent=None, review_open=2,
                             missing=[Missing(block="b-p002-0001", reason="scan engine failed")])))
    text = stream.getvalue()
    assert "⚠ p2,p3：服务错误（可重试）" in text and "HTTP 503" not in text  # details only with -v
    assert "部分完成" in text and "缺失 1 处：b-p002-0001（scan engine failed）" in text
    assert "费用约 $0.01（服务）" in text


def test_quiet_shows_only_the_result(tmp_path):
    stream, clock = io.StringIO(), Clock()
    _agent_run(ConsoleReporter(stream, lang="zh", tty=False, quiet=True, clock=clock), clock, _outcome(tmp_path))
    assert stream.getvalue().splitlines()[0].startswith("完成  ") and "[2/4]" not in stream.getvalue()


def test_live_lines_never_wrap():
    assert display_width("标题ab") == 6
    assert display_width(fit("专利说明书" * 20, 30)) <= 30


# ── the command ─────────────────────────────────────────────────────────


def _run_cli(monkeypatch, capsys, argv, fake):
    import parserx.runtimes.hybrid as hybrid
    from parserx.cli import main

    monkeypatch.setattr(hybrid, "parse_document", fake)
    monkeypatch.setattr("sys.argv", ["parserx", "parse", *argv])
    with pytest.raises(SystemExit) as exit_info:
        main()
    captured = capsys.readouterr()
    return exit_info.value.code, captured.out, captured.err


def test_json_goes_to_stdout_and_progress_to_stderr(tmp_path, monkeypatch, capsys):
    doc = tmp_path / "a.pdf"
    doc.write_bytes(b"%PDF")

    def fake(path, out_dir, config, *, reporter, keep_work):
        assert config.runtime.mode == "hybrid" and out_dir == tmp_path / "out"
        outcome = _outcome(tmp_path, name="a", source="a.pdf")
        reporter(DocEnd(outcome))
        return outcome

    code, out, err = _run_cli(monkeypatch, capsys, [str(doc), "-o", str(tmp_path / "out"), "--json"], fake)
    assert code == 0 and json.loads(out)["runtime"] == "hybrid:agent" and "完成" in err


def test_a_directory_is_one_line_per_document_and_a_total(tmp_path, monkeypatch, capsys):
    for name in ("b.docx", "a.pdf", "notes.txt"):
        (tmp_path / name).write_bytes(b"x")
    from parserx.runtimes.hybrid import ParseFailure

    seen = []

    def fake(path, out_dir, config, *, reporter, keep_work):
        seen.append((path.name, out_dir))
        if path.suffix == ".docx":
            raise ParseFailure("unreadable", "not a zip file")
        outcome = _outcome(tmp_path, name=path.stem, source=path.name, runtime="fixed", agent=None)
        reporter(DocEnd(outcome))
        return outcome

    code, out, err = _run_cli(monkeypatch, capsys, [str(tmp_path), "-o", str(tmp_path / "out"), "--runtime", "fixed",
                                                    "--json"], fake)
    assert [s[0] for s in seen] == ["a.pdf", "b.docx"] and seen[0][1] == tmp_path / "out" / "a"
    assert code == 1 and [r.get("error", {}).get("code") for r in json.loads(out)] == [None, "unreadable"]
    lines = [line for line in err.splitlines() if not line.startswith("⚠")]  # notices (no config here) come first
    assert lines[0].startswith("✓ a.pdf  complete · 14 页 · 待核对 0 项")
    assert "失败  b.docx  无法读取：not a zip file" in lines[1]
    assert lines[-1].startswith("合计 2 篇：完成 1 · 部分 0 · 失败 1")


def test_ctrl_c_says_where_the_work_is_kept(tmp_path, monkeypatch, capsys):
    doc = tmp_path / "a.pdf"
    doc.write_bytes(b"%PDF")

    def fake(path, out_dir, config, *, reporter, keep_work):
        raise KeyboardInterrupt

    code, out, err = _run_cli(monkeypatch, capsys, [str(doc), "-o", str(tmp_path / "out")], fake)
    assert code == 130 and out == ""
    assert "已中断，工作区保留在" in err and ".parserx-work" in err and "再次运行同一命令会从中断处继续" in err


def test_action_lines_name_pages_kinds_and_docx_blocks():
    stream = io.StringIO()
    reporter = ConsoleReporter(stream, lang="zh", tty=False)
    reporter(AgentAction("set_role", target=",".join(["p4", "p5", "p6"]), count=5))
    reporter(AgentAction("set_role", target="b-p001-0002", page=1, text="(12)发明专利", detail="other"))
    reporter(AgentAction("look", target="b-d00093", text="5.2支座加工：", detail="是独立小节标题吗？"))
    reporter(Step("process", "layout", total=0, detail={"figures": 13}))
    reporter(StageEnd("process", 1.0))
    assert stream.getvalue().splitlines() == [
        "      改类型  5 处 · 第 4、5、6 页",
        "      改类型  (12)发明专利 → 其他",
        "      看图  «5.2支座加工：» · 是独立小节标题吗？",
        "      图片分类 13 张                            ✓ 0.0 s",
    ]


def test_docx_result_has_no_page_count(tmp_path):
    stream = io.StringIO()
    ConsoleReporter(stream, lang="zh", tty=False)(DocEnd(_outcome(tmp_path, format="docx", pages=1)))
    assert "      状态 complete · 表格 2 · 图片 3 · 标题 9 · 待核对 0 项" in stream.getvalue().splitlines()


def test_what_cannot_work_is_said_before_the_first_document(tmp_path, monkeypatch):
    # R4: no token for the scan engine, no key for the service model or the loop's model; a role switched off is quiet
    from parserx.config.schema import apply_overrides, load_config
    from parserx.console.cli import preflight

    monkeypatch.chdir(tmp_path)
    bare = load_config()
    assert [n.code for n in preflight(bare)] == ["preflight_ocr", "preflight_vlm", "preflight_loop"]
    paddle = apply_overrides(bare, ["builders.ocr.engine=paddleocr", "builders.ocr.token=t"])
    assert "preflight_ocr" not in [n.code for n in preflight(paddle)]
    off = apply_overrides(bare, ["builders.ocr.engine=none", "services.vlm.endpoint=", "runtime.agent.engine=loop",
                                 "runtime.agent.use=deepseek-flash"])
    assert [n.code for n in preflight(off)] == ["preflight_loop"]
