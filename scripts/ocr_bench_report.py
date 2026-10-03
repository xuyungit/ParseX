"""OCR engine comparison report (docs/v2_ocr_engines.md).

    uv run --frozen python scripts/ocr_bench_report.py [--out eval_runs/ocr_bench] [--engines a,b,…]

Applies ``ocr_engines.tidy`` to every engine's output.md (idempotent: outputs made before it existed get it too),
scores everything with the benchmark's own scorer (``tool_eval.runner.score``: scores.json, page_scores.json), and
writes ``ocr_report.md``: per engine, the documents every listed engine read, split into our corpus (pages no engine
can have trained on) and OmniDocBench; per document; the worst pages; speed and cost.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from parserx.tool_eval import runner  # noqa: E402
from parserx.tool_eval.ocr_engines import tidy  # noqa: E402

GT_DIRS = [ROOT / "ground_truth", ROOT / "ground_truth_public", ROOT / "ground_truth_ocr"]
DOCS = ROOT / "configs" / "ocr_bench.txt"
GROUPS = (("本方语料（引擎不可能训练过）", lambda d: not d.startswith("omni")),
          ("OmniDocBench（公开评测集，引擎可能训练过）", lambda d: d.startswith("omni")))


def tidy_outputs(out: Path) -> int:
    changed = 0
    for path in out.glob("*/*/output.md"):
        text = path.read_text(encoding="utf-8")
        if tidy(text) != text:
            path.write_text(tidy(text), encoding="utf-8")
            changed += 1
    return changed


def page_seconds(out: Path, engine: str, doc: str, meta: dict) -> list[float]:
    pages = out / engine / doc / "raw" / "pages.json"
    if pages.exists():
        return json.loads(pages.read_text(encoding="utf-8"))["seconds"]
    n = meta.get("pages") or 1
    return [round((meta.get("wall_time_seconds") or 0) / n, 2)] * n  # a file engine: the job's time, per page


def _mean(values) -> float | None:
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def _f(value, digits=3) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def summary_rows(engines, docs, scores, metas, out) -> list[str]:
    rows = ["| 引擎 | 字 F1 | 字召回 | 字精确 | 编辑距离 | 表格 F1（篇） | 公式相似度（篇） | 顺序 τ | 关键内容错误 "
            "| 其中无公式的文档 | 漏掉的字 | 多出的字 |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for e in engines:
        s = [scores[e][d] for d in docs]
        tables = [x["table_f1"] for x in s if x["table_f1"] is not None]
        formulas = [x["formula_similarity"] for x in s if x["formula_similarity"] is not None]
        rows.append(
            f"| {label(e, metas)} | {_f(_mean(x['char_f1'] for x in s))} | {_f(_mean(x.get('char_recall') for x in s))} "
            f"| {_f(_mean(x.get('char_precision') for x in s))} | {_f(_mean(x['edit_distance'] for x in s))} "
            f"| {_f(_mean(tables))}（{len(tables)}） | {_f(_mean(formulas))}（{len(formulas)}） "
            f"| {_f(_mean(x['order_tau'] for x in s))} | {sum(x['key_errors'] for x in s)} "
            f"| {sum(x['key_errors'] for x in s if not x['formulas_expected'])} "
            f"| {sum(x['lost_chars'] for x in s)} | {sum(x['added_chars'] for x in s)} |")
    return rows


def label(engine: str, metas: dict) -> str:
    first = next(iter(metas[engine].values()), {})
    return f"{first.get('label') or engine}（`{engine}`）"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=ROOT / "eval_runs" / "ocr_bench")
    parser.add_argument("--engines", default="", help="comma-separated, in report order (default: all with results)")
    parser.add_argument("--no-score", action="store_true", help="use the scores.json on disk")
    args = parser.parse_args()
    out = args.out
    changed = tidy_outputs(out)
    if not args.no_score:
        runner.score(out, GT_DIRS)
    scores = json.loads((out / "scores.json").read_text(encoding="utf-8"))["scores"]
    page_scores = json.loads((out / "page_scores.json").read_text(encoding="utf-8"))["scores"] \
        if (out / "page_scores.json").exists() else {}
    metas = runner.load_results(out)
    wanted = runner.read_doc_names(DOCS)
    engines = [e.strip() for e in args.engines.split(",") if e.strip()] or sorted(scores)
    common = [d for d in wanted if all(d in scores.get(e, {}) for e in engines)]
    missing = {e: [d for d in wanted if d not in scores.get(e, {})] for e in engines}

    lines = [f"# OCR 引擎比较（{datetime.now().isoformat(timespec='seconds')}）", "",
             "每个引擎读的都是同一批页面图片（扫描页按原分辨率，其余页 200 dpi），不用文字层。指标与对标比较相同"
             "（`evaluate_markdown`）。平均只取所有列出的引擎都有结果的文档"
             f"（{len(common)} 篇）；表格 F1、公式相似度只取标注里有表格、公式的文档。",
             "关键内容错误（数字、单位、正负号、上下标）在公式多的文档里主要来自 LaTeX 写法不同（编号写在公式外、"
             "数字拆开），所以另列标注里没有公式的文档。",
             f"输出整理（`ocr_engines.tidy`）：表格单元格里写出来的「\\n」换成空格；引擎写进图片块里的图中文字不比较；HTML 写的图片改成 Markdown 图片、去掉 div 标签（{changed} 个文件这次被改）。", ""]
    for title, belongs in GROUPS:
        docs = [d for d in common if belongs(d)]
        if docs:
            pages = sum(next(iter(metas[e][d] for e in engines)).get("pages") or 1 for d in docs)
            lines += [f"## {title}：{len(docs)} 篇 {pages} 页", ""] + summary_rows(engines, docs, scores, metas, out) + [""]

    lines += ["## 速度与费用", "", "| 引擎 | 页数 | 每页用时中位数（秒） | 每页用时 90% 分位 | 合计用时（分钟） | 费用合计 | 每 100 页 | 失败 |",
              "|---|---|---|---|---|---|---|---|"]
    for e in engines:
        secs, cost, costed, failed = [], 0.0, False, 0
        for d in wanted:
            meta = metas[e].get(d)
            if meta is None:
                continue
            if meta.get("status") != "ok":
                failed += 1
                continue
            secs += page_seconds(out, e, d, meta)
            if meta.get("cost_usd") is not None:
                cost, costed = cost + meta["cost_usd"], True
        p90 = statistics.quantiles(secs, n=10)[-1] if len(secs) >= 10 else None
        lines.append(f"| {label(e, metas)} | {len(secs)} | {_f(statistics.median(secs) if secs else None, 1)} "
                     f"| {_f(p90, 1)} | {sum(secs) / 60:.0f} | {'$%.3f' % cost if costed else '—'} "
                     f"| {'$%.3f' % (cost / len(secs) * 100) if costed and secs else '—'} | {failed} |")
    lines += ["", "用时是同一台机器（M5 Pro）上几个引擎同时运行时测的，只看数量级；单独计时见文档。", ""]

    lines += ["## 逐篇", "", "每格：字 F1 / 表格 F1 / 关键内容错误。", "",
              "| 文档 | 页 | " + " | ".join(engines) + " |", "|---|---|" + "---|" * len(engines)]
    for d in wanted:
        cells = []
        for e in engines:
            x = scores.get(e, {}).get(d)
            cells.append("失败" if x is None and d in metas[e] else "—" if x is None else
                         f"{_f(x['char_f1'])} / {_f(x['table_f1'], 2)} / {x['key_errors']}")
        n = next((m.get("pages") for m in (metas[e].get(d) for e in engines) if m), "")
        lines.append(f"| {d} | {n} | " + " | ".join(cells) + " |")

    lines += ["", "## 每个引擎最差的 5 页（多页文档，按字 F1）", ""]
    for e in engines:
        pages = [(p["char_f1"], d, p["page"], p["key_errors"]) for d, ps in page_scores.get(e, {}).items()
                 if d in wanted for p in ps if p["char_f1"] is not None]
        worst = sorted(pages)[:5]
        lines.append(f"- {label(e, metas)}：" + "；".join(f"{d} 第 {n} 页 {f:.3f}（关键错误 {k}）"
                                                         for f, d, n, k in worst))
    gaps = {e: m for e, m in missing.items() if m}
    if gaps:
        lines += ["", "## 缺的结果", ""] + [f"- `{e}`：{', '.join(m)}" for e, m in gaps.items()]
    (out / "ocr_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{out / 'ocr_report.md'}  ({len(common)} documents in common, {changed} outputs tidied)")


if __name__ == "__main__":
    main()
