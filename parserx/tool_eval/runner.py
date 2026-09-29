"""Run the tools over ground-truth documents, keep every result, score them all the same way.

Layout (``eval_runs/bench/`` by default)::

    <tool>/<doc>/output.md     the Markdown as the tool gave it
    <tool>/<doc>/meta.json     status, time, cost, settings, notes (or the error)
    <tool>/<doc>/images/ raw/  what the Markdown refers to; the raw response
    scores.json, report.md     automatic scores (``score``)
    page_scores.json, pages.md the same for every page of a PDF of several pages
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

from parserx.eval.metrics import METRIC_VERSION, evaluate_markdown, fmt_metric, mean_defined
from parserx.eval.outline import has_outline
from parserx.eval.pages import page_texts, split_pages
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


def score(out_root: Path = DEFAULT_OUT, gt_dirs=DEFAULT_GT_DIRS, *, pages: bool = True) -> dict:
    """Score every successful output that has an annotation; write ``scores.json`` and ``report.md`` — and, with
    *pages*, every page of a PDF of several pages (``page_scores.json``, ``pages.md``; ``eval/pages.py``)."""
    out_root = Path(out_root)
    docs = {d.name: d for d in find_documents(gt_dirs)}
    scores: dict[str, dict[str, dict]] = {}
    page_scores: dict[str, dict[str, list[dict]]] = {}
    page_texts_of: dict[str, list[str]] = {}
    for tool, by_doc in load_results(out_root).items():
        for name, meta in by_doc.items():
            doc = docs.get(name)
            if doc is None or meta.get("status") != "ok":
                continue
            output = (out_root / tool / name / "output.md").read_text(encoding="utf-8")
            expected = doc.expected.read_text(encoding="utf-8")
            result = evaluate_markdown(output, expected, name=name)
            scores.setdefault(tool, {})[name] = {
                "group": doc.group, **_scores_of(result), "outline": has_outline(doc.dir),
                "wall_time_seconds": meta.get("wall_time_seconds"), "cost_usd": meta.get("cost_usd"),
                **_costs(meta, out_root / tool / name),
            }
            if pages and doc.kind == "pdf" and (doc.pages or 0) > 1:
                if name not in page_texts_of:
                    page_texts_of[name] = page_texts(doc.input, _derived_cache())
                texts = page_texts_of[name]
                page_scores.setdefault(tool, {})[name] = [
                    {"page": n, **_scores_of(evaluate_markdown(o, e, name=f"{name}#{n}"))}
                    for n, (e, o) in enumerate(zip(split_pages(expected, texts), split_pages(output, texts)), 1)]
    record = {"generated": datetime.now().isoformat(timespec="seconds"), "metric_version": METRIC_VERSION,
              "scores": scores}
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "scores.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_root / "report.md").write_text(format_report(record, docs, load_results(out_root)), encoding="utf-8")
    if page_scores:
        page_record = {"generated": record["generated"], "metric_version": METRIC_VERSION, "scores": page_scores}
        (out_root / "page_scores.json").write_text(json.dumps(page_record, ensure_ascii=False, indent=2),
                                                   encoding="utf-8")
        (out_root / "pages.md").write_text(format_pages(page_record), encoding="utf-8")
    return record


def _scores_of(result) -> dict:
    return {
        "table_f1": result.tables.cell_f1,
        "header_association": result.tables.header_association,
        "merged_cells": result.tables.merged_cell_accuracy,
        "missing_tables": result.tables.missing_tables,
        "extra_tables": result.tables.extra_tables,
        "char_f1": result.text.char_f1,
        "edit_distance": result.text.edit_distance,
        "order_tau": result.order.tau,
        "heading_f1": result.headings.f1,
        "key_errors": result.key_content.total,
        "key_missing": dict(result.key_content.missing),
        "key_extra": dict(result.key_content.extra),
        "lost_blocks": result.omission.lost_blocks,
        "lost_runs": result.omission.lost_runs,
        "lost_chars": result.omission.lost_chars,
        "added_runs": result.omission.added_runs,
        "added_chars": result.omission.added_chars,
        "formulas_expected": result.formulas.expected,
        "formulas_paired": result.formulas.paired,
        "formula_similarity": result.formulas.similarity,
    }


def _derived_cache():
    from parserx.cache.store import ResponseCache

    return ResponseCache(Path("~/.cache/parserx").expanduser())  # local readings only (derived), never requests


_FROZEN: dict[str, dict] = {}


def _costs(meta: dict, result_dir: Path) -> dict:
    """Cost and time in layers (review R6, C0.6): what was paid, what the list price of a subscription use would
    be, what is unknown; live time apart from replay time.

    - a replayed frozen run: the frozen run's own record for paid service cost and live time; this replay's time is
      replay time; the scan engine's price is not recorded (unknown when it was called);
    - the hybrid runtime: service cost as recorded, the agent (Codex, a subscription) at list price;
    - an external tool: its recorded charge; none recorded is unknown, not free."""
    config = meta.get("config") or {}
    if config.get("frozen_run"):
        run = config["frozen_run"]
        if run not in _FROZEN:
            path = REPO_ROOT / "eval_runs" / run / "metrics.json"
            _FROZEN[run] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"documents": {}}
        frozen = _FROZEN[run]["documents"].get(meta.get("document"), {})
        return {"paid_usd": frozen.get("cost_usd"), "list_usd": None,
                "unknown": ["扫描引擎"] if (frozen.get("requests") or {}).get("ocr") else [],
                "live_s": frozen.get("wall_time_seconds"), "replay_s": meta.get("wall_time_seconds")}
    if config.get("runtime") == "hybrid":
        summary_path = result_dir / "raw" / "summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
        processing = summary.get("processing") or {}
        agent = processing.get("agent") or {}
        engines = processing.get("engines") or {}
        return {"paid_usd": meta.get("cost_usd"), "list_usd": agent.get("usd_at_list_price"),
                "unknown": ["扫描引擎"] if "paddleocr" in engines else [],
                "live_s": meta.get("wall_time_seconds"), "replay_s": None}
    cost = meta.get("cost_usd")
    return {"paid_usd": cost, "list_usd": None, "unknown": [] if cost is not None else ["费用"],
            "live_s": meta.get("wall_time_seconds"), "replay_s": None}


_SUMMARY = [
    ("表格 F1", "table_f1"), ("表头关联", "header_association"), ("合并单元格", "merged_cells"),
    ("char_f1", "char_f1"), ("编辑距离", "edit_distance"), ("顺序 τ", "order_tau"), ("标题 F1", "heading_f1"),
]
_OLD_KINDS = ("number", "unit", "negation", "date")
_NEW_KINDS = (("正负号", "sign"), ("上下标", "script"), ("归属", "attribution"), ("无映射", "unmapped"))


def _kind_total(row: dict, kinds) -> int:
    return sum(row["key_missing"].get(k, 0) + row["key_extra"].get(k, 0) for k in kinds)


def _money(values: list[float | None], n: int) -> str:
    known = [v for v in values if v is not None]
    if not known:
        return "—"
    each = sum(known) / n
    return f"${each:.4f}" if each < 0.01 else f"${each:.3f}"


def format_report(record: dict, docs: dict[str, Document], results: dict[str, dict[str, dict]]) -> str:
    scores = record["scores"]
    tools = sorted(scores)
    lines = [f"# 对标工具比较（{record['generated']}，指标 {record.get('metric_version', '?')}）", "",
             "指标与回归相同（`evaluate_markdown`）；标题 F1 只算有大纲的文档（Q79）。"
             "平均只取所有工具都有结果的文档，各工具缺的文档另列。", "",
             "质量指标是每篇的平均；关键内容错误、遗漏是合计；费用、用时是**每篇**（合计除以篇数）。"
             "费用分三层：实付（服务的计费）、标价估算（Codex 包月的 Agent 按标价）、未知（没有记录，不当作免费；"
             "列出有未知部分的篇数）。在线用时与回放用时分列，不相比较。", ""]
    for group, label in GROUP_LABELS.items():
        common = sorted(set.intersection(*(
            {n for n, s in scores[t].items() if s["group"] == group} for t in tools))) if tools else []
        if not common:
            continue
        n = len(common)
        lines += [f"## {label}（{n} 篇）", "",
                  "| 工具 | " + " | ".join(h for h, _ in _SUMMARY) + " | 公式配对 | 公式相似度 |",
                  "|---" * (len(_SUMMARY) + 3) + "|"]
        for tool in tools:
            rows = [scores[tool][d] for d in common]
            cells = [fmt_metric(mean_defined(r[key] for r in rows if key != "heading_f1" or r["outline"]))
                     for _, key in _SUMMARY]
            wanted = sum(r.get("formulas_expected", 0) for r in rows)
            paired = sum(r.get("formulas_paired", 0) for r in rows)
            lines.append(f"| {tool} | " + " | ".join(cells) + (f" | {paired}/{wanted}" if wanted else " | —")
                         + f" | {fmt_metric(mean_defined(r.get('formula_similarity') for r in rows))} |")
        lines += ["", "| 工具 | 关键内容错误 | 其中原四类 | " + " | ".join(h for h, _ in _NEW_KINDS)
                  + " | 整块遗漏 | 遗漏段（字） | 多出段（字） | 实付/篇 | 标价估算/篇 | 费用未知 | 在线用时/篇 | 回放用时/篇 |",
                  "|---" * 15 + "|"]
        for tool in tools:
            rows = [scores[tool][d] for d in common]
            new = " | ".join(str(sum(_kind_total(r, [k]) for r in rows)) for _, k in _NEW_KINDS)
            live = [r.get("live_s") for r in rows if r.get("live_s") is not None]
            replay = [r.get("replay_s") for r in rows if r.get("replay_s") is not None]
            unknown = sum(bool(r.get("unknown")) for r in rows)
            lines.append(
                f"| {tool} | {sum(r['key_errors'] for r in rows)} | {sum(_kind_total(r, _OLD_KINDS) for r in rows)}"
                f" | {new} | {sum(r['lost_blocks'] for r in rows)}"
                f" | {sum(r['lost_runs'] for r in rows)}（{sum(r['lost_chars'] for r in rows)}）"
                f" | {sum(r['added_runs'] for r in rows)}（{sum(r['added_chars'] for r in rows)}）"
                f" | {_money([r.get('paid_usd') for r in rows], n)} | {_money([r.get('list_usd') for r in rows], n)}"
                f" | {f'{unknown} 篇' if unknown else '—'}"
                f" | {f'{sum(live) / n:.0f}s' if len(live) == n else '—'}"
                f" | {f'{sum(replay) / n:.1f}s' if len(replay) == n else '—'} |")
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
    lines += ["", "每格：关键内容错误（其中正负号 + 上下标 + 归属 + 无映射）/ 遗漏段 / 公式相似度（有标注公式的文档）。", "",
              "| 文档 | " + " | ".join(tools) + " |", "|---" * (len(tools) + 1) + "|"]
    for n in names:
        cells = []
        for t in tools:
            s = scores[t].get(n)
            if s is None:
                cells.append("—")
                continue
            formula = f" / {fmt_metric(s['formula_similarity'], 2)}" if s.get("formulas_expected") else ""
            cells.append(f"{s['key_errors']}（{_kind_total(s, [k for _, k in _NEW_KINDS])}）/ {s['lost_runs']}{formula}")
        lines.append(f"| {n} | " + " | ".join(cells) + " |")

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


def format_pages(record: dict) -> str:
    """Per page: char_f1, key-content errors, lost content, formulas — one table per document."""
    scores = record["scores"]
    tools = sorted(scores)
    names = sorted({n for t in tools for n in scores[t]})
    lines = [f"# 逐页（{record['generated']}，指标 {record['metric_version']}）", "",
             "页由原件定（文字层加本地读数，`eval/pages.py`），标注与每个工具的输出按同一分页切开。"
             "每格：char_f1 / 关键内容错误 / 遗漏段；有公式的页另有公式相似度。", ""]
    for name in names:
        pages = max(len(scores[t].get(name, [])) for t in tools)
        lines += [f"## {name}", "", "| 页 | " + " | ".join(tools) + " |", "|---" * (len(tools) + 1) + "|"]
        for i in range(pages):
            cells = []
            for t in tools:
                rows = scores[t].get(name)
                if not rows:
                    cells.append("—")
                    continue
                r = rows[i]
                formula = f" / 式 {fmt_metric(r['formula_similarity'], 2)}" if r.get("formulas_expected") else ""
                cells.append(f"{fmt_metric(r['char_f1'], 3)} / {r['key_errors']} / {r['lost_runs']}{formula}")
            lines.append(f"| {i + 1} | " + " | ".join(cells) + " |")
        lines.append("")
    return "\n".join(lines) + "\n"
