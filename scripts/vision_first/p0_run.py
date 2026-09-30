"""P0 probe runs (execution plan §3.5, §7): ten pages × ten model configurations, each page one request.

Per page and configuration: ask (round 1); an answer that is empty, cut off, not JSON, off the schema, or breaks an
invariant is asked once more with the problems and the answer (round 2, Q139); what still breaks an invariant is
repaired mechanically, an answer that never parsed falls back to copying every line (``p0_contract``).  Every round
is recorded (answer, finish reason, tokens, seconds, cost, failed attempts, cache hit) in
``<run-dir>/results/<configuration>/<page>.json`` with the page's Markdown beside it; the run's own response cache
(``<run-dir>/cache``) replays it offline.

    uv run python scripts/vision_first/p0_run.py estimate --run-dir eval_runs/<run>
    uv run python scripts/vision_first/p0_run.py run --run-dir eval_runs/<run> [--configs a,b] [--pages p,q] [--offline]
    uv run python scripts/vision_first/p0_run.py render --run-dir eval_runs/<run>   # the Markdown again, no requests
    uv run python scripts/vision_first/p0_run.py manifest --run-dir eval_runs/<run>   # <run-dir>/manifest.json (C0.7)
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import p0_contract as contract  # noqa: E402
from p0_client import Caller  # noqa: E402
import v_formulas  # noqa: E402
from p0_inputs import PAGES, document, load, page_id  # noqa: E402

from parserx.cache import ResponseCache  # noqa: E402
from parserx.config.schema import apply_overrides, load_config  # noqa: E402
from parserx.scheduling.usage import PriceTable  # noqa: E402

# (name, model entry, effort, run): luna twice per effort; sol as the reference, not an upper bound (§1).
CONFIGS: tuple[tuple[str, str, str, int], ...] = (
    ("luna-low-r1", "gpt-6-luna", "low", 1), ("luna-low-r2", "gpt-6-luna", "low", 2),
    ("luna-low-r3", "gpt-6-luna", "low", 3),  # C0.3: a severe error in only one of the two runs (r2, paper_chn01 p3)
    ("luna-medium-r1", "gpt-6-luna", "medium", 1), ("luna-medium-r2", "gpt-6-luna", "medium", 2),
    ("sol-low", "gpt-6-sol", "low", 1), ("sol-medium", "gpt-6-sol", "medium", 1),
    ("deepseek-low", "deepseek-flash", "low", 1), ("deepseek-medium", "deepseek-flash", "medium", 1),
    ("deepseek-medium-r2", "deepseek-flash", "medium", 2),  # the second candidate runs twice from contract v2 on
    ("glm-low", "glm-5.3-flashx", "low", 1), ("glm-high", "glm-5.3-flashx", "high", 1),
)
# The answer's budget, reasoning included (an output cap, not the context window).  P0 used 32 768; DeepSeek at medium
# spent all of it thinking on formula pages, twice (paper_chn01 p5), and the page failed.  From V on (2026-09-29, user):
# 131 072 — the largest glm-5.3-flashx accepts ("max_tokens … [1,131072]"); luna, sol and deepseek-flash accept at least
# 393 216 (probed); about twice the most an answer has needed (65 536 of thinking).  Billed per token generated.
MAX_TOKENS = 131072
TIMEOUT_S = 1800  # Chat answers (DeepSeek, GLM) are not streamed: a long thinking run takes many minutes
STREAM_IDLE_S = 1200  # a Responses model sends no events while it reasons
PER_MODEL = 4  # concurrent requests per model


def service_config(model: str, effort: str):
    config = load_config(REPO_ROOT / "configs" / "regression.yaml")
    config = apply_overrides(config, [f"services.vlm.use={model}", f"services.vlm.reasoning_effort={effort}",
                                      f"services.vlm.timeout={TIMEOUT_S}",
                                      f"services.vlm.stream_idle_timeout={STREAM_IDLE_S}"])
    return config.services.vlm, PriceTable.from_config(config.scheduling.prices)


def callers(run_dir: Path, names: list[str], offline: bool) -> dict[str, tuple[Caller, int]]:
    cache = ResponseCache(run_dir / "cache", "read_only" if offline else "read_write")
    out = {}
    for name, model, effort, run in CONFIGS:
        if name in names:
            cfg, prices = service_config(model, effort)
            out[name] = (Caller(name, cfg, cache, prices), run)
    return out


def _round_feedback(checked: contract.Checked, record: dict) -> str:
    finish = record.get("finish", "")
    if finish.startswith("incomplete") or finish == "length":
        return ("\n\n【上一次的回答被截断了（超过输出上限）】请更简洁：能复制的行一律用 copy 引用行号或区间，"
                "不要把能复制的正文写进 write；重新输出完整的 JSON。")
    if checked.level == "empty":
        return "\n\n【上一次的回答是空的】请按要求输出完整的 JSON。"
    return contract.feedback(checked.problems, record.get("text", ""))


def run_page(caller: Caller, run: int, run_dir: Path, pid: str) -> dict:
    page = load(run_dir, pid)
    image = run_dir / "inputs" / page["image"]["file"]
    prompt, context = contract.prompt(page), contract.context(page)
    rounds = []
    feedback = ""
    final = None
    for round_ in (1, 2):
        record = caller.ask(image, prompt, context, schema=contract.SCHEMA, max_tokens=MAX_TOKENS, run=run,
                            round_=round_, feedback=feedback)
        checked = contract.check(record.get("text", ""), page)
        rounds.append({"round": round_, **record, "level": checked.level, "problems": checked.problems})
        if checked.level == "valid":
            final = {"status": "first_valid" if round_ == 1 else "valid_after_retry", "data": checked.data,
                     "repairs": [], "from_round": round_}
            break
        feedback = _round_feedback(checked, record)
    if final is None:
        usable = [(r["round"], contract.check(r.get("text", ""), page)) for r in reversed(rounds)]
        usable = [(n, c) for n, c in usable if c.level == "invariants"]
        if usable:
            n, checked = usable[0]
            data, repairs = contract.repair(checked.data, page)
            final = {"status": "repaired", "data": data, "repairs": repairs, "from_round": n}
        else:
            final = {"status": "failed", "data": contract.fallback(page), "repairs": ["整页按文字层复制（回答不能用）"],
                     "from_round": None}
    formulas = None  # contract v5: the display formulas' LaTeX, asked apart (Q143)
    if contract.CONTRACT_VERSION >= 5 and contract.unit_of(page) == "L" and v_formulas.wanted(final["data"]):
        formulas = v_formulas.run(caller, run, run_dir, page, final["data"], max_tokens=MAX_TOKENS,
                                  source=document(page["document"]))
        final["data"] = v_formulas.fill(final["data"], formulas["latex"])
    asked = rounds + (formulas["rounds"] if formulas else [])
    markdown = contract.render(final["data"], page)
    result = {"configuration": caller.name, "contract": contract.CONTRACT_VERSION, "page_id": pid,
              "model": caller.config.model,
              "identity": caller.identity(), "run": run, "rounds": rounds, "final": final,
              "destinations": contract.destinations(final["data"], page), "markdown": markdown, "formulas": formulas,
              "usd": _sum(r.get("usd") for r in asked),
              "seconds": round(sum(r.get("seconds", 0) for r in asked), 2),
              "tokens": {k: sum(r.get("usage", {}).get(k, 0) for r in asked)
                         for k in ("input", "cached", "output", "reasoning")}}
    out = run_dir / "results" / caller.name
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{pid}.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / f"{pid}.md").write_text(markdown, encoding="utf-8")
    return result


def _sum(values):
    values = list(values)
    return None if any(v is None for v in values) else round(sum(values), 6)


def run(run_dir: Path, names: list[str], pids: list[str], offline: bool) -> None:
    chosen = callers(run_dir, names, offline)
    lock = threading.Lock()

    def one(task):
        name, pid = task
        caller, n = chosen[name]
        result = run_page(caller, n, run_dir, pid)
        with lock:
            levels = "/".join(r["level"] for r in result["rounds"])
            print(f"{name:16s} {pid:22s} {result['final']['status']:18s} {levels:24s} "
                  f"{result['seconds']:7.1f}s ${result['usd'] if result['usd'] is not None else float('nan'):.4f} "
                  f"in {result['tokens']['input']} out {result['tokens']['output']} "
                  f"(reasoning {result['tokens']['reasoning']}) {result['rounds'][-1].get('finish')}", flush=True)
        return result

    by_model: dict[str, list] = {}
    for name in names:
        by_model.setdefault(chosen[name][0].config.model, []).extend((name, pid) for pid in pids)
    with ThreadPoolExecutor(max_workers=len(by_model)) as outer:
        list(outer.map(lambda tasks: list(ThreadPoolExecutor(PER_MODEL).map(one, tasks)), by_model.values()))


def rerender(run_dir: Path) -> None:
    """Every stored answer's Markdown rendered again from its final allocation (after a renderer change)."""
    for path in sorted((run_dir / "results").glob("*/*.json")):
        result = json.loads(path.read_text(encoding="utf-8"))
        markdown = contract.render(result["final"]["data"], load(run_dir, result["page_id"]))
        if markdown != result["markdown"]:
            print(f"re-rendered {path.parent.name}/{path.stem}")
        result["markdown"] = markdown
        path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        path.with_suffix(".md").write_text(markdown, encoding="utf-8")


def manifest(run_dir: Path) -> Path:
    """The run's manifest (``manifest.run_manifest``): what every result depends on, named and hashed."""
    from manifest import run_manifest, sha256

    models = {}
    for name, model, effort, run in CONFIGS:
        cfg, _prices = service_config(model, effort)
        caller_identity = Caller(name, cfg, ResponseCache(run_dir / "cache", "read_only"), _prices).identity()
        models[name] = {"entry": model, "run": run, "effort_requested": effort, **caller_identity,
                        "max_tokens": MAX_TOKENS, "timeout_s": TIMEOUT_S, "stream_idle_s": STREAM_IDLE_S}
    pids = sorted(p.stem for p in (run_dir / "inputs").glob("*.json"))  # the run's pages (P0: ``PAGES``)
    prompts = {f"{pid}:prompt": contract.prompt(load(run_dir, pid)) for pid in pids}
    prompts |= {f"{pid}:context": contract.context(load(run_dir, pid)) for pid in pids}
    prompts["schema"] = json.dumps(contract.SCHEMA, sort_keys=True)
    outputs = {f"{path.parent.name}/{path.stem}": path.read_text(encoding="utf-8")
               for path in sorted((run_dir / "results").glob("*/*.md"))}
    scanned = any(contract.unit_of(load(run_dir, pid)) == "K" for pid in pids)
    inputs = ("inputs: <run-dir>/inputs (page render 150 dpi, the scan engine's blocks rebuilt from the V run's cached "
              "engine response, the pipeline's local reading, detector regions; s0_inputs.py)" if scanned else
              "inputs: <run-dir>/inputs (page render 150 dpi, text-layer lines, script candidates, engine entries of "
              "the frozen run 2026-09-29_contentA_fixed_full's cache, detector regions)")
    record = run_manifest(
        run_id=run_dir.name,
        purpose=("vision-first scanned-page probe (docs/v2_vision_first_scanned.md §5.1): complete allocation of the "
                 "scan engine's blocks" if scanned else
                 "vision-first P0 probe (execution plan §3, §7): complete allocation, ten pages"),
        gt_dirs=[REPO_ROOT / "ground_truth", REPO_ROOT / "ground_truth_public"],
        docs=sorted({load(run_dir, pid)["document"] for pid in pids}),
        models={"service": models, "agent": None}, tools=[], cache={"mode": "read_write", "dir": str(run_dir / "cache")},
        prompts=prompts, outputs=outputs,
        notes=[f"contract version {contract.CONTRACT_VERSION} (p0_contract.CONTRACT_VERSION)", "pages: " + ", ".join(pids),
               inputs,
               "outputs are keyed <configuration>/<page>; results/<configuration>/<page>.json holds every round"])
    record["inputs_by_page"] = {pid: sha256(run_dir / "inputs" / f"{pid}.json") for pid in pids}
    path = run_dir / "manifest.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return path


def estimate(run_dir: Path, pids: list[str]) -> None:
    """Rough input size per page (characters → tokens: a CJK character about one token, other text about four
    characters a token), before any request."""
    total = 0
    for pid in pids:
        page = load(run_dir, pid)
        text = contract.prompt(page) + contract.context(page)
        wide = sum(1 for ch in text if ord(ch) > 0x2E80)
        tokens = wide + (len(text) - wide) / 4 + 1500  # the image: about 1.5k tokens
        total += tokens
        print(f"{pid:22s} chars {len(text):7d}  ≈ {tokens / 1000:5.1f}k input tokens")
    print(f"ten pages ≈ {total / 1000:.0f}k input tokens per configuration")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["estimate", "run", "render", "manifest"])
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--configs", default=",".join(c[0] for c in CONFIGS))
    parser.add_argument("--pages", default=",".join(page_id(d, n) for d, n in PAGES))
    parser.add_argument("--offline", action="store_true", help="replay from the run's cache; a miss is an error")
    args = parser.parse_args()
    pids = args.pages.split(",")
    if args.command == "estimate":
        estimate(args.run_dir, pids)
    elif args.command == "render":
        rerender(args.run_dir)
    elif args.command == "manifest":
        print(manifest(args.run_dir))
    else:
        run(args.run_dir, args.configs.split(","), pids, args.offline)


if __name__ == "__main__":
    main()
