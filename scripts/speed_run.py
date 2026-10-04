"""Run the speed regression set and tabulate where the time and the money go (speed plan §1–2).

    uv run python scripts/speed_run.py LABEL [--no-agent] [--cache DIR] [--docs FILE] [--set key=value …]
                                             [--agent codex|<model>] [--parserx PATH] [--keep-work]

Each document runs through ``parserx parse --report`` one after another, as a user would, into
``~/parserx-exp/speed/<date>_<LABEL>/<doc>/``.  Without ``--cache`` the run is cold: a new, empty cache in the run's
directory, so every request and every local reading is made again (the cache is kept for warm reruns).  Quality:
``--parserx`` runs another installation's command — a frozen snapshot of the code
(``scripts/agent_explore.py snapshot``), so the code can change while an agent run goes on.  Quality: documents
with an annotation are scored (key content errors, characters, headings, tables), documents with
``checks.json`` run their must-read checks (Q154).  ``speed.json`` holds every figure, ``speed.md`` the tables.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from parserx.eval.checks import load as load_checks, run_checks  # noqa: E402
from parserx.eval.metrics import evaluate_markdown  # noqa: E402

ROOT = Path("~/parserx-exp/speed").expanduser()


def source(doc: str) -> Path:
    for gt in ("ground_truth", "ground_truth_public"):
        found = sorted((REPO / gt / doc).glob("input.*"))
        found = [f for f in found if f.suffix in (".pdf", ".docx", ".doc", ".png", ".jpg", ".jpeg")]
        if found:
            return found[0]
    raise SystemExit(f"no input for {doc}")


def quality(doc: str, markdown: str) -> dict:
    src = source(doc).parent
    out: dict = {}
    if (src / "expected.md").is_file():
        r = evaluate_markdown(markdown, (src / "expected.md").read_text(encoding="utf-8"), name=doc)
        out.update(key_errors=r.key_content.total, char_f1=r.text.char_f1, heading_f1=r.headings.f1,
                   table_f1=r.tables.cell_f1)
    if (src / "checks.json").is_file():
        results = run_checks(markdown, load_checks(src / "checks.json"))
        out["checks"] = {"passed": sum(r.passed for r in results), "total": len(results),
                         "failed": [r.id for r in results if not r.passed]}
    return out


def run_one(doc: str, out: Path, cache: Path, args) -> dict:
    target = out / doc
    command = [str(args.parserx)] if args.parserx else ["uv", "run", "--frozen", "parserx"]
    cmd = [*command, "parse", str(source(doc)), "--report", "-o", str(target),
           "--set", f"cache.dir={cache}", "--set", "cache.mode=read_write"]
    if args.no_agent:
        cmd.append("--no-agent")
    if args.agent:
        cmd += ["--agent", args.agent]
    if args.keep_work:
        cmd.append("--keep-work")
    for item in args.set or []:
        cmd += ["--set", item]
    t = time.monotonic()
    proc = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True)
    wall = round(time.monotonic() - t, 1)
    (target).mkdir(parents=True, exist_ok=True)
    (target / "console.log").write_text(proc.stdout + proc.stderr, encoding="utf-8")
    record: dict = {"doc": doc, "exit": proc.returncode, "wall_s": wall}
    summaries = [p for p in target.glob("*.json")]
    if proc.returncode != 0 or not summaries:
        record["error"] = (proc.stderr or proc.stdout)[-800:]
        return record
    summary = json.loads(summaries[0].read_text(encoding="utf-8"))
    markdown = next(target.glob("*.md")).read_text(encoding="utf-8")
    p = summary["processing"]
    record.update(parse_wall_s=p.get("wall_s"), stages=p.get("stages"), steps=p.get("steps"), models=p.get("models"),
                  requests=p.get("requests"), cost_usd=p.get("cost_usd"), runtime=p.get("runtime"),
                  agent=p.get("agent"), review_open=summary["review"]["open"],
                  review_by_kind=summary["review"].get("by_kind"), quality=quality(doc, markdown))
    return record


def tables(records: list[dict]) -> str:
    lines = ["| 文档 | 总用时 | 读取 | 标准处理 | Agent | 导出 | 请求 ocr / vlm | 费用 $ | 待核对 | 质量 |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for r in records:
        if "error" in r:
            lines.append(f"| {r['doc']} | 失败 | | | | | | | | {r['error'][-80:]!r} |")
            continue
        s, q = r.get("stages") or {}, r.get("quality") or {}
        quality_text = ", ".join(filter(None, [
            f"关键 {q['key_errors']}" if "key_errors" in q else "",
            f"字 {q['char_f1']:.3f}" if q.get("char_f1") is not None else "",
            f"标题 {q['heading_f1']:.3f}" if q.get("heading_f1") is not None else "",
            f"表 {q['table_f1']:.3f}" if q.get("table_f1") is not None else "",
            f"清单 {q['checks']['passed']}/{q['checks']['total']}" if "checks" in q else ""]))
        req = r.get("requests") or {}
        lines.append(f"| {r['doc']} | {r['wall_s']} | {s.get('read', '')} | {s.get('process', '')} | "
                     f"{s.get('agent', '')} | {s.get('export', '')} | {req.get('ocr', 0)} / {req.get('vlm', 0)} | "
                     f"{r.get('cost_usd') or 0:.3f} | {r.get('review_open')} | {quality_text} |")
    lines += ["", "**各步用时（秒）**", "", "| 文档 | 步骤 | 秒 | 请求 | 本机 | 费用 $ |", "|---|---|---|---|---|---|"]
    for r in records:
        for st in sorted(r.get("steps") or [], key=lambda x: -x["s"]):
            if st["s"] < 0.5:
                continue
            local = ", ".join(f"{k} {v['calls']}×/{v['s']}s" for k, v in st.get("local", {}).items())
            req = ", ".join(f"{k} {v}" for k, v in st.get("requests", {}).items())
            lines.append(f"| {r['doc']} | {st['step']} | {st['s']} | {req} | {local} | {st.get('usd', 0):.4f} |")
    agents = [r for r in records if r.get("agent")]
    if agents:
        lines += ["", "**Agent**", "", "| 文档 | 秒 | 轮 | 思考 s | 工具 s | 工具调用 | 待核对 前→后 |", "|---|---|---|---|---|---|---|"]
        for r in agents:
            a = r["agent"]
            tools = ", ".join(f"{k} {int(v['calls'])}×/{v['s']}s" for k, v in (a.get("tools") or {}).items())
            lines.append(f"| {r['doc']} | {a['wall_s']} | {a.get('turns')} | {a.get('model_s')} | {a.get('tool_s')} | "
                         f"{tools} | {a['review_open_before']}→{a['review_open_after']} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("label")
    ap.add_argument("--docs", type=Path, default=REPO / "configs" / "speed_set.txt")
    ap.add_argument("--cache", type=Path, help="a cache to use (read and write); default: a new, empty one (cold)")
    ap.add_argument("--no-agent", action="store_true")
    ap.add_argument("--set", action="append", help="a config override passed to parserx parse")
    ap.add_argument("--agent", help="codex or a model entry (our own loop), passed to parserx parse")
    ap.add_argument("--parserx", type=Path, help="the parserx command to run (a snapshot's), default: this checkout")
    ap.add_argument("--keep-work", action="store_true", help="keep each document's work directory (agent events)")
    args = ap.parse_args()
    docs = [d for d in (l.split("#")[0].strip() for l in args.docs.read_text().splitlines()) if d]
    out = ROOT / f"{dt.date.today().isoformat()}_{args.label}"
    if out.exists():
        raise SystemExit(f"{out} exists: choose another label")
    out.mkdir(parents=True)
    cache = (args.cache or out / "cache").resolve()
    records = []
    for doc in docs:
        record = run_one(doc, out, cache, args)
        records.append(record)
        print(doc, record.get("wall_s"), record.get("stages"), record.get("quality"), flush=True)
    meta = {"label": args.label, "docs": docs, "cache": str(cache), "cold": args.cache is None,
            "no_agent": args.no_agent, "set": args.set or [], "agent": args.agent,
            "parserx": str(args.parserx) if args.parserx else None,
            "commit": subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO, capture_output=True,
                                     text=True).stdout.strip()}
    (out / "speed.json").write_text(json.dumps({"meta": meta, "records": records}, ensure_ascii=False, indent=1))
    (out / "speed.md").write_text(f"# 速度回归 {args.label}\n\n{json.dumps(meta, ensure_ascii=False)}\n\n" + tables(records),
                                  encoding="utf-8")
    print(out / "speed.md")


if __name__ == "__main__":
    main()
