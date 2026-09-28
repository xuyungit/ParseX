"""Run the tools over ground-truth documents, keep every result, score them all the same way.

Layout (``eval_runs/bench/`` by default)::

    <tool>/<doc>/output.md     the Markdown as the tool gave it
    <tool>/<doc>/meta.json     status, time, cost, settings, notes (or the error)
    <tool>/<doc>/images/ raw/  what the Markdown refers to; the raw response
    scores.json, report.md     automatic scores (``score``)
    manual_scores.json         scores given on the comparison page (``viewer``)

A document a tool already produced is not requested again unless forced: reruns cost nothing.
"""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from parserx.eval.metrics import evaluate_markdown, fmt_metric, mean_defined
from parserx.eval.outline import has_outline
from parserx.tool_eval.adapters import REPO_ROOT, ToolAdapter, page_count

DEFAULT_OUT = REPO_ROOT / "eval_runs" / "bench"
DEFAULT_GT_DIRS = (REPO_ROOT / "ground_truth", REPO_ROOT / "ground_truth_public")
INPUT_SUFFIXES = (".pdf", ".docx", ".doc")

# Where an annotation came from decides which tool it favours (plan §3).
GROUP_LABELS = {
    "independent": "独立标注",
    "llamaparse_draft": "LlamaParse 初稿改出的标注",
    "parserx_reference": "参考过 ParserX 结果的标注",
}
_INDEPENDENT_SOURCES = {"OmniDocBench", "synthetic_public_smoke"}


@dataclass
class Document:
    name: str
    dir: Path
    input: Path
    expected: Path | None
    group: str
    pages: int | None

    @property
    def kind(self) -> str:
        return "word" if self.input.suffix.lower() in (".docx", ".doc") else "pdf"


def annotation_group(doc_dir: Path) -> str:
    """``meta.json`` ``annotation_origin`` when given; public sources are independent; the rest began as a
    LlamaParse draft (docs/evaluation_workflow.md §2)."""
    meta_path = doc_dir / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
    if meta.get("annotation_origin") in GROUP_LABELS:
        return meta["annotation_origin"]
    return "independent" if meta.get("source") in _INDEPENDENT_SOURCES else "llamaparse_draft"


def find_documents(gt_dirs=DEFAULT_GT_DIRS, names: list[str] | None = None) -> list[Document]:
    """Documents with an input file, in the order of *names* when given (unknown names raise)."""
    found: dict[str, Document] = {}
    for gt_dir in map(Path, gt_dirs):
        if not gt_dir.is_dir():
            continue
        for doc_dir in sorted(p for p in gt_dir.iterdir() if p.is_dir()):
            # .docx before .doc: a converted legacy file sits next to its original.
            inputs = [doc_dir / f"input{s}" for s in INPUT_SUFFIXES if (doc_dir / f"input{s}").exists()]
            if not inputs or doc_dir.name in found:
                continue
            expected = doc_dir / "expected.md"
            found[doc_dir.name] = Document(doc_dir.name, doc_dir, inputs[0], expected if expected.exists() else None,
                                           annotation_group(doc_dir), page_count(inputs[0]))
    if names is None:
        return [d for d in found.values() if d.expected is not None]
    unknown = [n for n in names if n not in found]
    if unknown:
        raise KeyError(f"unknown documents: {', '.join(unknown)}")
    return [found[n] for n in names]


def read_doc_names(path: Path) -> list[str]:
    lines = (line.split("#", 1)[0].strip() for line in Path(path).read_text(encoding="utf-8").splitlines())
    return [line for line in lines if line]


def run_tools(tools: list[ToolAdapter], docs: list[Document], out_root: Path = DEFAULT_OUT, *,
              force: bool = False, log=print) -> list[dict]:
    """Every tool on every document; results already on disk are kept unless *force*."""
    metas = []
    for doc in docs:
        for tool in tools:
            doc_dir = out_root / tool.name / doc.name
            meta_path = doc_dir / "meta.json"
            if not force and meta_path.exists():
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if meta.get("status") == "ok":
                    metas.append(meta)
                    continue
            if doc_dir.exists():
                shutil.rmtree(doc_dir)
            doc_dir.mkdir(parents=True)
            meta = {"tool": tool.name, "label": tool.label, "document": doc.name, "input": str(doc.input),
                    "pages": doc.pages, "started": datetime.now().isoformat(timespec="seconds")}
            log(f"{tool.name:18s} {doc.name} …")
            start = time.monotonic()
            try:
                run = tool.parse(doc.input, doc_dir)
            except Exception as exc:  # one failure must not stop the others; it is recorded where it happened
                meta.update(status="error", error=f"{type(exc).__name__}: {exc}",
                            wall_time_seconds=round(time.monotonic() - start, 1))
                log(f"{'':18s} error: {meta['error'][:200]}")
            else:
                (doc_dir / "output.md").write_text(run.markdown, encoding="utf-8")
                meta.update(status="ok", wall_time_seconds=round(time.monotonic() - start, 1), config=run.config,
                            cost_usd=run.cost_usd, cost_note=run.cost_note, notes=run.notes)
                log(f"{'':18s} ok, {meta['wall_time_seconds']}s"
                    + (f", ${run.cost_usd}" if run.cost_usd else "") + (f" ({run.cost_note})" if run.cost_note else ""))
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            metas.append(meta)
    return metas


def load_results(out_root: Path = DEFAULT_OUT) -> dict[str, dict[str, dict]]:
    """tool → document → meta.json, for every result on disk."""
    results: dict[str, dict[str, dict]] = {}
    for meta_path in sorted(Path(out_root).glob("*/*/meta.json")):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        results.setdefault(meta_path.parent.parent.name, {})[meta_path.parent.name] = meta
    return results


def score(out_root: Path = DEFAULT_OUT, gt_dirs=DEFAULT_GT_DIRS) -> dict:
    """Score every successful output that has an annotation; write ``scores.json`` and ``report.md``."""
    out_root = Path(out_root)
    docs = {d.name: d for d in find_documents(gt_dirs)}
    scores: dict[str, dict[str, dict]] = {}
    for tool, by_doc in load_results(out_root).items():
        for name, meta in by_doc.items():
            doc = docs.get(name)
            if doc is None or meta.get("status") != "ok":
                continue
            output = (out_root / tool / name / "output.md").read_text(encoding="utf-8")
            result = evaluate_markdown(output, doc.expected.read_text(encoding="utf-8"), name=name)
            outline = has_outline(doc.dir)
            scores.setdefault(tool, {})[name] = {
                "group": doc.group,
                "table_f1": result.tables.cell_f1,
                "header_association": result.tables.header_association,
                "merged_cells": result.tables.merged_cell_accuracy,
                "missing_tables": result.tables.missing_tables,
                "extra_tables": result.tables.extra_tables,
                "char_f1": result.text.char_f1,
                "edit_distance": result.text.edit_distance,
                "order_tau": result.order.tau,
                "heading_f1": result.headings.f1,
                "outline": outline,
                "key_errors": result.key_content.total,
                "wall_time_seconds": meta.get("wall_time_seconds"),
                "cost_usd": meta.get("cost_usd"),
            }
    record = {"generated": datetime.now().isoformat(timespec="seconds"), "scores": scores}
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "scores.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_root / "report.md").write_text(format_report(record, docs, load_results(out_root)), encoding="utf-8")
    return record


_SUMMARY = [
    ("表格 F1", "table_f1"), ("表头关联", "header_association"), ("合并单元格", "merged_cells"),
    ("char_f1", "char_f1"), ("编辑距离", "edit_distance"), ("顺序 τ", "order_tau"), ("标题 F1", "heading_f1"),
]


def format_report(record: dict, docs: dict[str, Document], results: dict[str, dict[str, dict]]) -> str:
    scores = record["scores"]
    tools = sorted(scores)
    lines = [f"# 对标工具比较（{record['generated']}）", "",
             "指标与回归相同（`evaluate_markdown`）；标题 F1 只算有大纲的文档（Q79）。"
             "平均只取所有工具都有结果的文档，各工具缺的文档另列。", ""]
    for group, label in GROUP_LABELS.items():
        common = sorted(set.intersection(*(
            {n for n, s in scores[t].items() if s["group"] == group} for t in tools))) if tools else []
        if not common:
            continue
        lines += [f"## {label}（{len(common)} 篇）", "",
                  "| 工具 | " + " | ".join(h for h, _ in _SUMMARY) + " | 关键内容错误 | 用时 | 费用 |",
                  "|---" * (len(_SUMMARY) + 4) + "|"]
        for tool in tools:
            rows = [scores[tool][n] for n in common]
            cells = []
            for _, key in _SUMMARY:
                values = [r[key] for r in rows if key != "heading_f1" or r["outline"]]
                cells.append(fmt_metric(mean_defined(values)))
            time_s = sum(r["wall_time_seconds"] or 0 for r in rows)
            cost = sum(r["cost_usd"] or 0 for r in rows)
            lines.append(f"| {tool} | " + " | ".join(cells)
                         + f" | {sum(r['key_errors'] for r in rows)} | {time_s:.0f}s | ${cost:.2f} |")
        lines.append("")

    names = sorted({n for t in tools for n in scores[t]}, key=lambda n: (docs[n].pages or 0, n))
    lines += ["## 逐篇", "", "每格：表格 F1 / char_f1 / 标题 F1。", "",
              "| 文档 | 页数 | 标注 | " + " | ".join(tools) + " |", "|---" * (len(tools) + 3) + "|"]
    for n in names:
        cells = []
        for t in tools:
            s = scores[t].get(n)
            cells.append("—" if s is None else
                         f"{fmt_metric(s['table_f1'], 2)} / {fmt_metric(s['char_f1'], 3)} / {fmt_metric(s['heading_f1'], 2)}")
        doc = docs[n]
        pages = doc.pages if doc.pages is not None else doc.kind
        lines.append(f"| {n} | {pages} | {GROUP_LABELS[doc.group][:6]} | " + " | ".join(cells) + " |")

    problems = [(t, n, m.get("error") or "") for t, by in sorted(results.items()) for n, m in sorted(by.items())
                if m.get("status") != "ok"]
    notes = []
    for t, by in sorted(results.items()):
        # A note every document of the tool carries is said once.
        everywhere = set.intersection(*(set(m.get("notes") or []) for m in by.values()))
        notes += [f"- {t}（全部 {len(by)} 篇）：{x}" for x in sorted(everywhere)]
        notes += [f"- {t} / {n}：{'；'.join(x for x in m['notes'] if x not in everywhere)}"
                  for n, m in sorted(by.items()) if set(m.get("notes") or []) - everywhere]
    if problems:
        lines += ["", "## 失败", ""] + [f"- {t} / {n}：{e}" for t, n, e in problems]
    if notes:
        lines += ["", "## 说明", ""] + notes
    return "\n".join(lines) + "\n"
