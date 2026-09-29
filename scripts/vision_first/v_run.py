"""V: whole documents (common plan §6.2, execution plan §7), the pipeline unchanged around the allocations.

Per document:

1. **M pass**: the fixed pipeline replayed offline from M's frozen cache (the frozen run's output), for its worklist;
2. **routing** (common plan §3.7, no new thresholds): a document with a formula on any page (the layout detector's
   formula regions) is sent whole; otherwise a native page is sent when the pipeline's own review signals point at
   it — unreadable characters (``text_suspicious``), text the page reading sees that no block holds
   (``text_unaccounted``), output text the page does not show (``text_not_seen``), a formula reading not adopted
   (``formula_candidate``) — or when the detector sees a figure where no image is placed (a drawn figure).  Scanned
   pages stay with the scan engine (this round is native PDF only, plan §3.8).  ``--all`` sends every native page
   (V-all);
3. **V pass**: ``workspace init``, the routed pages' allocations (``p0_run.run_page``: the same contract, checks,
   retry and cache as P0) put into the workspace (``v_adapter``), then the unchanged ``run_pipeline`` (its formula
   step off: formula documents are sent whole) and ``export``.

Results in ``<run-dir>/docs/<configuration>/<document>/`` (Markdown, sidecar, routing, counts); scores in
``<run-dir>/v_scores.json`` and ``v_summary.md``.

    uv run python scripts/vision_first/v_run.py --run-dir eval_runs/<run> --configs luna-medium-r1 [--all]
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pymupdf  # noqa: E402

import p0_inputs  # noqa: E402
import v_adapter  # noqa: E402
from p0_run import callers, run_page  # noqa: E402

from parserx.config.schema import apply_overrides, load_config  # noqa: E402
from parserx.eval.metrics import evaluate_markdown  # noqa: E402
from parserx.ir.enums import PageStatus  # noqa: E402
from parserx.runtimes.pipeline import _Session, run as pipeline_run  # noqa: E402
from parserx.tools import call_tool, workspace_init  # noqa: E402
from parserx.tools.views import unresolved_items  # noqa: E402
from parserx.tool_eval.runner import _scores_of  # noqa: E402
from parserx.workspace import Workspace  # noqa: E402

DOCS = ("paper_chn01", "paper_chn02", "ocr01", "receipt")  # the tuning set (common plan §6.2)
M_RUN = REPO_ROOT / "eval_runs" / "2026-09-29_bench2f_fixed_full"
ROUTING_KINDS = frozenset({"text_suspicious", "text_unaccounted", "text_not_seen", "formula_candidate"})
FORMULA_LABELS = frozenset({"display_formula", "inline_formula", "formula"})
FIGURE_LABELS = frozenset({"image", "figure", "chart"})


def typography_from_text_layer() -> None:
    """The title steps read a block's typography only from its chosen reading when that is the text layer's; a block
    the service model rewrote keeps the text layer's reading of its lines as another observation.  For V the two
    readers also take that one (the typography is a fact about the lines, whoever rewrote their text) — a candidate
    for the minimal change the workspace needs (execution plan §2, audit §4), made here only, in the experiment."""
    from parserx.hierarchy import layout_titles, typography_titles

    def native_style(block):
        chosen = next((o for o in block.observations if o.id == block.chosen_observation), None)
        if chosen is not None and chosen.engine in typography_titles.ENGINES:
            return chosen.style
        return next((o.style for o in block.observations if o.engine in typography_titles.ENGINES
                     and o.task.value == "extract" and o.style is not None), None)

    typography_titles.native_style = native_style
    layout_titles._native_style = lambda block: (native_style(block) if any(
        o.engine == "native_pdf" for o in block.observations) else None)


def pipeline_config(cache_dir: Path, mode: str, *, formulas: bool = True):
    config = load_config(REPO_ROOT / "configs" / "regression.yaml")
    overrides = [f"cache.dir={cache_dir}", f"cache.mode={mode}"]
    if not formulas:
        overrides.append("runtime.formulas=false")
    return apply_overrides(config, overrides)


def m_pass(doc: str, work: Path) -> dict:
    """The fixed pipeline replayed from M's cache: its worklist by page, and which pages are native."""
    ws_dir, out = work / f"m-{doc}", work / f"m-{doc}-out"
    shutil.rmtree(ws_dir, ignore_errors=True)
    outcome = pipeline_run(p0_inputs.document(doc), ws_dir, out, pipeline_config(M_RUN / "cache", "read_only"),
                           name=doc)
    state = Workspace.open(ws_dir).load()
    by_page: dict[int, list[str]] = {}
    blocks = {b.id: b for b in state.blocks}
    for item in unresolved_items(state):
        target = blocks.get(item.target)
        page = target.anchors[0].page if target is not None and hasattr(target.anchors[0], "page") else None
        if page is None and item.target.startswith("p") and item.target[1:].isdigit():
            page = int(item.target[1:])
        if page is not None:
            by_page.setdefault(page, []).append(item.kind.value)
    # native pages: those whose text layer the extraction accepts (status right after init; a page the scan engine
    # reads for its formulas is still native)
    init_dir = work / f"init-{doc}"
    shutil.rmtree(init_dir, ignore_errors=True)
    workspace_init(p0_inputs.document(doc), init_dir, config=pipeline_config(M_RUN / "cache", "read_only"))
    native = [p.n for p in Workspace.open(init_dir).load().pages if p.status == PageStatus.DONE]
    return {"worklist": by_page, "native": native, "markdown": outcome.markdown}


def route(doc: str, m: dict, all_pages: bool) -> dict[int, list[str]]:
    """Page → why it is sent."""
    config = load_config(REPO_ROOT / "configs" / "regression.yaml")
    from parserx.cache import ResponseCache

    derived = ResponseCache(REPO_ROOT / config.cache.dir, "read_write")
    reasons: dict[int, list[str]] = {}
    with pymupdf.open(p0_inputs.document(doc)) as src:
        regions = {n: p0_inputs.detector_regions(src[n - 1], config, derived) for n in m["native"]}
        images = {n: [info["bbox"] for info in src[n - 1].get_image_info() if info.get("bbox")] for n in m["native"]}
    formula_doc = any(r["label"] in FORMULA_LABELS for rs in regions.values() for r in rs)
    k = 72.0 / p0_inputs.DPI
    for n in m["native"]:
        why = []
        if all_pages:
            why.append("all pages (V-all)")
        if formula_doc:
            why.append("formula document")
        kinds = sorted(set(m["worklist"].get(n, [])) & ROUTING_KINDS)
        why += kinds
        for r in regions[n]:
            if r["label"] in FIGURE_LABELS:
                box = [v * k for v in r["box"]]
                if not any(v_adapter._overlaps(box, img) for img in images[n]):
                    why.append("drawn figure")
                    break
        if why:
            reasons[n] = why
    return reasons


def v_pass(doc: str, config_name: str, run_dir: Path, pages: dict[int, list[str]]) -> dict:
    out_dir = run_dir / "docs" / config_name / doc
    ws_dir = run_dir / "work" / config_name / doc
    shutil.rmtree(ws_dir, ignore_errors=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    cache = run_dir / "pipeline_cache"
    if not cache.exists():  # the pipeline's own requests start from M's recorded ones
        shutil.copytree(M_RUN / "cache", cache)
    config = pipeline_config(cache, "read_write", formulas=False)
    started = time.monotonic()
    envelope, _ = workspace_init(p0_inputs.document(doc), ws_dir, config=config)
    if not envelope.ok:
        raise RuntimeError(f"{doc}: workspace init failed")
    ws = Workspace.open(ws_dir)
    missing = [(doc, n) for n in pages if not (run_dir / "inputs" / f"{p0_inputs.page_id(doc, n)}.json").exists()]
    if missing:
        p0_inputs.build(run_dir, pages=missing)
    caller, run = callers(run_dir, [config_name], offline=False)[config_name]
    allocations, applied = {}, {}
    for n in sorted(pages):
        pid = p0_inputs.page_id(doc, n)
        result = run_page(caller, run, run_dir, pid)
        page = p0_inputs.load(run_dir, pid)
        applied[n] = v_adapter.apply(ws, p0_inputs.document(doc), n, page, result["final"]["data"],
                                     model=caller.config.model, dpi=p0_inputs.DPI)
        allocations[n] = {"status": result["final"]["status"], "usd": result["usd"], "seconds": result["seconds"]}
    typography_from_text_layer()
    session = _Session()
    pipeline_envelope, code = call_tool("run_pipeline", ws_dir, {}, config=config, context_factory=session)
    if code == 1 or not pipeline_envelope.ok:
        raise RuntimeError(f"{doc}: run_pipeline failed: {pipeline_envelope.failures[:1]}")
    export, _ = call_tool("export", ws_dir, {"out": str(out_dir), "name": doc}, config=config, context_factory=session)
    if not export.ok or not export.result.accepted:
        raise RuntimeError(f"{doc}: export refused: {export.failures[:1] or export.result.blockers}")
    markdown = Path(export.result.markdown).read_text(encoding="utf-8")
    cost = session.context.cost(time.monotonic() - started) if session.context else None
    record = {"document": doc, "configuration": config_name, "routed": {str(n): why for n, why in pages.items()},
              "allocations": {str(n): a for n, a in allocations.items()}, "applied": {str(n): a for n, a in applied.items()},
              "allocation_usd": _sum(a["usd"] for a in allocations.values()),
              "pipeline_usd": cost.usd if cost else None, "pipeline_requests": cost.requests if cost else None,
              "seconds": round(time.monotonic() - started, 1), "status": export.result.status.value}
    (out_dir / "v_record.json").write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    return {**record, "markdown": markdown}


def _sum(values):
    values = list(values)
    return None if any(v is None for v in values) else round(sum(values), 6)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--configs", required=True)
    parser.add_argument("--docs", default=",".join(DOCS))
    parser.add_argument("--all", action="store_true", help="send every native page (V-all)")
    parser.add_argument("--allocation-cache", type=Path, help="a P0 run directory whose cache the allocations reuse")
    args = parser.parse_args()
    run_dir = args.run_dir
    run_dir.mkdir(parents=True, exist_ok=True)
    if args.allocation_cache and not (run_dir / "cache").exists():
        shutil.copytree(args.allocation_cache / "cache", run_dir / "cache")
    routing_path = run_dir / "routing.json"
    routing = json.loads(routing_path.read_text(encoding="utf-8")) if routing_path.exists() else {}
    for doc in args.docs.split(","):
        if doc not in routing:
            m = m_pass(doc, run_dir / "work")
            routing[doc] = {"native": m["native"], "worklist": {str(k): v for k, v in m["worklist"].items()},
                            "sent": {str(n): why for n, why in route(doc, m, args.all).items()}}
            routing_path.write_text(json.dumps(routing, ensure_ascii=False, indent=1), encoding="utf-8")
        pages = {int(n): why for n, why in routing[doc]["sent"].items()}
        print(f"{doc}: {len(routing[doc]['native'])} native pages, sent {sorted(pages)}", flush=True)
        for name in args.configs.split(","):
            record = v_pass(doc, name, run_dir, pages)
            expected = (p0_inputs.document(doc).parent / "expected.md").read_text(encoding="utf-8")
            scores = _scores_of(evaluate_markdown(record["markdown"], expected, name=doc))
            print(f"  {name}: {record['status']}, key errors {scores['key_errors']}, char_f1 {scores['char_f1']:.3f}, "
                  f"${record['allocation_usd']} + pipeline ${record['pipeline_usd']}, {record['seconds']} s", flush=True)


if __name__ == "__main__":
    main()
