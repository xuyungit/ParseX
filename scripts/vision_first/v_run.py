"""V: whole documents (common plan §6.2, execution plan §7), the pipeline unchanged around the allocations.

Per document:

1. **M pass**: the fixed pipeline replayed offline from M's frozen cache (the frozen run's output), for its worklist;
2. **routing** (common plan §3.7, no new thresholds): a document with a formula on any page (the layout detector's
   formula regions) is sent whole; otherwise a native page is sent when the pipeline's own review signals point at
   it — unreadable characters (``text_suspicious``), text the page reading sees that no block holds
   (``text_unaccounted``), output text the page does not show (``text_not_seen``), a formula reading not adopted
   (``formula_candidate``) — or when the detector sees a figure where no image is placed (a drawn figure).  Scanned
   pages stay with the scan engine (this round is native PDF only, plan §3.8).  ``--all`` sends every native page
   (V-all).  With ``--scanned`` (docs/v2_vision_first_scanned.md §7) a scanned page is sent on the same review signals
   (and ``text_added``: text the pipeline put in from the local reading), or when an engine block's reading holds
   less than half of what the local reading sees within its box (``engine_short``: a paragraph the engine left
   empty or read elsewhere);
3. **V pass**: ``workspace init``, the routed pages' allocations (``p0_run.run_page``: the same contract, checks,
   retry and cache as P0) put into the workspace (``v_adapter``), then the unchanged ``run_pipeline`` (its formula
   step off: formula documents are sent whole) and ``export``.  Routed scanned pages are read by the scan engine
   first (``recognize``, as ``run_pipeline`` would), their engine blocks allocated (``s0_contract``) and put into the
   workspace (``s_adapter``); ``run_pipeline`` then skips the reading it already has.

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

import p0_contract as contract  # noqa: E402
import p0_inputs  # noqa: E402
import s0_inputs  # noqa: E402
import s_adapter  # noqa: E402
import v_adapter  # noqa: E402
import va_free  # noqa: E402
from p0_run import MAX_TOKENS, callers, run_page  # noqa: E402

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
DEFAULTS = True  # the conservative defaults without the agent (common plan §3.4); --no-defaults turns them off
ROUTING_KINDS = frozenset({"text_suspicious", "text_unaccounted", "text_not_seen", "formula_candidate"})
SCAN_ROUTING_KINDS = ROUTING_KINDS | {"text_added"}
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


def titles_from_vision() -> None:
    """The scan engine's title a scanned-page allocation rewrote keeps its label on the rewritten reading, but the
    title step ranks a label by the chosen reading's engine; the service model's reading ranks as the engine's —
    the other candidate for the workspace's minimal change (made here only; native allocations write titles as
    TEXT, so only rewritten scan titles are affected)."""
    from parserx.layout import labels

    labels.TITLE_RANK.setdefault("vlm", dict(labels.TITLE_RANK["paddleocr"]))


def engine_short(state, n: int) -> list[str]:
    """Engine blocks of scanned page *n* whose reading holds less than half of the characters the local reading
    sees within their box (at least ten)."""
    import unicodedata

    from parserx.ir.anchor import PdfAnchor
    from parserx.ir.enums import BlockKind, BlockStatus, TaskKind
    from parserx.workspace.queries import block_unit

    def chars(text: str) -> int:
        return len("".join(unicodedata.normalize("NFKC", text or "").split()))

    def inside(a, b) -> bool:  # most of line *a* within box *b*
        w, h = min(a[2], b[2]) - max(a[0], b[0]), min(a[3], b[3]) - max(a[1], b[1])
        return w > 0 and h > 0 and w * h >= 0.6 * (a[2] - a[0]) * (a[3] - a[1])

    reading = next((r for r in state.readings if r.n == n), None)
    out = []
    for block in state.blocks:
        if (reading is None or block_unit(state, block) != n or block.status == BlockStatus.MERGED
                or not isinstance(block.anchors[0], PdfAnchor)  # read inside an embedded image: its own pixels
                or block.kind == BlockKind.FIGURE or not any(o.engine == "paddleocr" and o.task == TaskKind.RECOGNIZE
                                                             for o in block.observations)):
            continue
        box = block.anchors[0].bbox
        local = sum(chars(line.text) for line in reading.lines if inside(line.bbox, box))
        held = sum(chars(c.content) for c in block.cells.cells) if block.cells is not None else chars(block.text)
        if local >= 10 and held < local / 2:
            out.append(block.id)
    return out


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
    short = {p.n: engine_short(state, p.n) for p in state.pages if p.n not in native}
    return {"worklist": by_page, "native": native, "markdown": outcome.markdown,
            "pages": [p.n for p in state.pages], "short": {n: b for n, b in short.items() if b}}


def route(doc: str, m: dict, all_pages: bool, scanned: bool = False, scanned_only: bool = False) -> dict[int, list[str]]:
    """Page → why it is sent (a Word document: none, it has no pages to show); *scanned_only*: native pages stay
    with the pipeline (the scanned pages' effect alone)."""
    if p0_inputs.document(doc).suffix.lower() != ".pdf":
        return {}
    config = load_config(REPO_ROOT / "configs" / "regression.yaml")
    from parserx.cache import ResponseCache

    derived = ResponseCache(REPO_ROOT / config.cache.dir, "read_write")
    reasons: dict[int, list[str]] = {}
    with pymupdf.open(p0_inputs.document(doc)) as src:
        regions = {n: p0_inputs.detector_regions(src[n - 1], config, derived) for n in m["native"]}
        images = {n: [info["bbox"] for info in src[n - 1].get_image_info() if info.get("bbox")] for n in m["native"]}
    formula_doc = any(r["label"] in FORMULA_LABELS for rs in regions.values() for r in rs)
    k = 72.0 / p0_inputs.DPI
    for n in ([] if scanned_only else m["native"]):
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
    for n in (p for p in m.get("pages", []) if scanned and p not in m["native"]):
        why = sorted(set(m["worklist"].get(n, [])) & SCAN_ROUTING_KINDS)
        if m["short"].get(n):
            why.append(f"engine_short ({len(m['short'][n])} blocks)")
        if all_pages:
            why.append("all pages (V-all)")
        if why:
            reasons[n] = why
    return reasons


def free_page(caller, run: int, run_dir: Path, pid: str) -> dict:
    """V-a: the page written freely (``va_free``), one retry with the problems; stored beside the allocations."""
    page = p0_inputs.load(run_dir, pid)
    image = run_dir / "inputs" / page["image"]["file"]
    prompt, context = va_free.prompt(page), contract.context(page)
    rounds, feedback, final = [], "", None
    for round_ in (1, 2):
        record = caller.ask(image, prompt, context, schema=va_free.SCHEMA, max_tokens=MAX_TOKENS, run=run,
                            round_=round_, feedback=feedback)
        checked = va_free.check(record.get("text", ""))
        rounds.append({"round": round_, **record, "level": checked.level, "problems": checked.problems})
        if checked.level == "valid" or (round_ == 2 and checked.data):
            final = {"status": "valid" if checked.level == "valid" else checked.level,
                     "markdown": checked.data["markdown"]}
            break
        feedback = contract.feedback(checked.problems, record.get("text", ""))
    if final is None:
        final = {"status": "failed", "markdown": ""}
    result = {"configuration": caller.name + "-free", "page_id": pid, "rounds": rounds, "final": final,
              "usd": sum(r.get("usd") or 0 for r in rounds), "seconds": round(sum(r.get("seconds", 0) for r in rounds), 2)}
    out = run_dir / "results" / f"{caller.name}-free"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{pid}.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


def v_pass(doc: str, config_name: str, run_dir: Path, pages: dict[int, list[str]], *, free: bool = False,
           native: list[int] | None = None) -> dict:
    label = config_name + ("-free" if free else "")
    out_dir = run_dir / "docs" / label / doc
    ws_dir = run_dir / "work" / label / doc
    shutil.rmtree(ws_dir, ignore_errors=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    cache = run_dir / "pipeline_cache"
    if not cache.exists():  # the pipeline's own requests start from M's recorded ones
        shutil.copytree(M_RUN / "cache", cache)
    config = pipeline_config(cache, "read_write")  # its formula step takes only pages left to it (a failed allocation)
    started = time.monotonic()
    envelope, _ = workspace_init(p0_inputs.document(doc), ws_dir, config=config)
    if not envelope.ok:
        raise RuntimeError(f"{doc}: workspace init failed")
    ws = Workspace.open(ws_dir)
    scanned = {n for n in pages if native is not None and n not in native}
    missing = [(doc, n) for n in pages if n not in scanned
               and not (run_dir / "inputs" / f"{p0_inputs.page_id(doc, n)}.json").exists()]
    if missing:
        p0_inputs.build(run_dir, pages=missing)
    caller, run = callers(run_dir, [config_name], offline=False)[config_name]
    from p0_score import furniture_keys

    keys = furniture_keys(p0_inputs.document(doc))
    allocations, applied = {}, {}
    session = _Session()
    for n in sorted(pages):
        if n in scanned:
            continue
        pid = p0_inputs.page_id(doc, n)
        page = p0_inputs.load(run_dir, pid)
        if free:
            result = free_page(caller, run, run_dir, pid)
            if result["final"]["status"] == "failed":
                applied[n] = {"left_to_pipeline": 1}
            else:
                applied[n] = va_free.apply(ws, p0_inputs.document(doc), n, page, result["final"]["markdown"],
                                           model=caller.config.model)
        else:
            result = run_page(caller, run, run_dir, pid)
            if result["final"]["status"] == "failed":  # no allocation: the page stays with the pipeline, as in M
                applied[n] = {"left_to_pipeline": 1}
            else:
                applied[n] = v_adapter.apply(ws, p0_inputs.document(doc), n, page, result["final"]["data"],
                                             model=caller.config.model, dpi=p0_inputs.DPI, defaults=DEFAULTS,
                                             repeated=set().union(*(k for i, k in enumerate(keys, 1) if i != n)))
        allocations[n] = {"status": result["final"]["status"], "usd": result["usd"], "seconds": result["seconds"]}
    if scanned:  # the scan engine reads the pages first (as run_pipeline would), then their blocks are allocated
        pending = [p.n for p in ws.load().pages if p.status == PageStatus.PENDING]
        envelope, code = call_tool("recognize", ws_dir, {"pages": pending, "engine": "paddleocr"}, config=config,
                                   context_factory=session)
        if code == 1:
            raise RuntimeError(f"{doc}: recognize failed: {envelope.failures[:1]}")
        from parserx.cache import ResponseCache

        base_config = load_config(REPO_ROOT / "configs" / "regression.yaml")
        derived = ResponseCache(REPO_ROOT / base_config.cache.dir, "read_write")
        for n in sorted(scanned):
            page = s0_inputs.from_workspace(run_dir, doc, n, ws.load(), base_config, derived)
            result = run_page(caller, run, run_dir, page["page_id"])
            if result["final"]["status"] == "failed":
                applied[n] = {"left_to_pipeline": 1}
            else:
                applied[n] = s_adapter.apply(ws, p0_inputs.document(doc), n, page, result["final"]["data"],
                                             model=caller.config.model, dpi=p0_inputs.DPI)
            allocations[n] = {"status": result["final"]["status"], "usd": result["usd"], "seconds": result["seconds"],
                              "scanned": True}
        titles_from_vision()
    typography_from_text_layer()
    pipeline_envelope, code = call_tool("run_pipeline", ws_dir, {}, config=config, context_factory=session)
    if code == 1 or not pipeline_envelope.ok:
        raise RuntimeError(f"{doc}: run_pipeline failed: {pipeline_envelope.failures[:1]}")
    export, _ = call_tool("export", ws_dir, {"out": str(out_dir), "name": doc}, config=config, context_factory=session)
    if not export.ok or not export.result.accepted:
        raise RuntimeError(f"{doc}: export refused: {export.failures[:1] or export.result.blockers}")
    markdown = Path(export.result.markdown).read_text(encoding="utf-8")
    cost = session.context.cost(time.monotonic() - started) if session.context else None
    record = {"document": doc, "configuration": label, "routed": {str(n): why for n, why in pages.items()},
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
    parser.add_argument("--free", action="store_true", help="V-a: the service model writes the pages freely")
    parser.add_argument("--no-defaults", action="store_true",
                        help="put written parts in as they are (without the conservative defaults of plan §3.4)")
    parser.add_argument("--prepare", action="store_true",
                        help="routing, page inputs and the pipeline cache only (before configurations run in parallel)")
    parser.add_argument("--allocation-cache", type=Path, help="a P0 run directory whose cache the allocations reuse")
    parser.add_argument("--scanned", action="store_true",
                        help="send scanned pages too, by the review signals and engine_short (scanned-page plan §7)")
    parser.add_argument("--scanned-only", action="store_true", help="send scanned pages only; native pages stay "
                        "with the pipeline (implies --scanned)")
    parser.add_argument("--pipeline-cache", type=Path, help="a V run directory whose pipeline cache this run starts from")
    args = parser.parse_args()
    global DEFAULTS
    DEFAULTS = not args.no_defaults
    run_dir = args.run_dir
    run_dir.mkdir(parents=True, exist_ok=True)
    if args.allocation_cache and not (run_dir / "cache").exists():
        shutil.copytree(args.allocation_cache / "cache", run_dir / "cache")
    if args.pipeline_cache and not (run_dir / "pipeline_cache").exists():
        shutil.copytree(args.pipeline_cache / "pipeline_cache", run_dir / "pipeline_cache")
    routing_path = run_dir / "routing.json"
    routing = json.loads(routing_path.read_text(encoding="utf-8")) if routing_path.exists() else {}
    for doc in args.docs.split(","):
        if doc not in routing:
            m = m_pass(doc, run_dir / "work")
            routing[doc] = {"native": m["native"], "worklist": {str(k): v for k, v in m["worklist"].items()},
                            "short": {str(k): v for k, v in m.get("short", {}).items()},
                            "sent": {str(n): why for n, why in route(doc, m, args.all, args.scanned or args.scanned_only,
                                                                     args.scanned_only).items()},
                            "mode": "scanned only" if args.scanned_only else "scanned" if args.scanned else "native"}
            routing_path.write_text(json.dumps(routing, ensure_ascii=False, indent=1), encoding="utf-8")
        pages = {int(n): why for n, why in routing[doc]["sent"].items()}
        print(f"{doc}: {len(routing[doc]['native'])} native pages, sent {sorted(pages)}", flush=True)
        native = routing[doc]["native"]
        if args.prepare:
            missing = [(doc, n) for n in pages if n in native
                       and not (run_dir / "inputs" / f"{p0_inputs.page_id(doc, n)}.json").exists()]
            if missing:
                p0_inputs.build(run_dir, pages=missing)
            if not (run_dir / "pipeline_cache").exists():
                shutil.copytree(M_RUN / "cache", run_dir / "pipeline_cache")
            continue
        for name in args.configs.split(","):
            record = v_pass(doc, name, run_dir, pages, free=args.free, native=native)
            expected = (p0_inputs.document(doc).parent / "expected.md").read_text(encoding="utf-8")
            scores = _scores_of(evaluate_markdown(record["markdown"], expected, name=doc))
            print(f"  {name}: {record['status']}, key errors {scores['key_errors']}, char_f1 {scores['char_f1']:.3f}, "
                  f"${record['allocation_usd']} + pipeline ${record['pipeline_usd']}, {record['seconds']} s", flush=True)


if __name__ == "__main__":
    main()
