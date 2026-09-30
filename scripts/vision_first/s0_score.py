"""Scanned-page probe measurements (docs/v2_vision_first_scanned.md §5.1), each on its own:

- **structured output**: first answer valid, valid after the retry, repaired, failed;
- **allocation of the engine's blocks**: blocks allocated exactly once in the first answer; where they went (copied,
  written over, table, aside) by blocks and characters;
- **the comparison** (plan §2): every write part against the engine blocks it replaces — numbers added and dropped;
  an added number is *supported* when one of the two independent readings has it: the engine's reading of the page
  (anywhere: the engine may have put a paragraph in another block) or the local reading within the replaced blocks'
  boxes (the written block's region for content no engine block holds).  Each change is judged on the page against
  the annotation: an added number is right when the page's output does not have it more often than the annotation,
  a dropped one wrong when the output has it less often.  The **conservative default** keeps the engine's reading
  where a write adds a number no reading supports (the rewrite would go to the agent as a candidate): its page is
  rendered and scored too;
- **page scores** against the annotation, paired on the same page with the engine-only draft (V's draft: the pipeline
  alone on a scanned page), the agent's result on it (C1), M, and Datalab;
- tokens, seconds and cost per page.

    uv run python scripts/vision_first/s0_score.py --run-dir eval_runs/<run>   # → scores.json, summary.md
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import p0_contract as contract  # noqa: E402
from p0_inputs import load  # noqa: E402
from p0_score import BENCH, Pages, letters, numbers, score  # noqa: E402
from s0_inputs import PAGES, page_id  # noqa: E402

from parserx.eval.pages import split_pages  # noqa: E402

CONFIGS = ("luna-medium-r1", "luna-medium-r2", "deepseek-medium", "deepseek-medium-r2")
# The agent's results on the same drafts (C1: ocr01 in the first round, the rest in the 24-document round).
AGENT_RUNS = Path.home() / "parserx-exp" / "vision-first-c1" / "c1b"
AGENT_RUN = {"ocr01": "_c1/ocr01.tool.medium.r1"}


def _overlaps(a: list, b: list) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def local_in(page: dict, boxes: list[list]) -> str:
    """The local reading's lines within *boxes* (the replaced blocks, or the written block's region)."""
    return "\n".join(line["text"] for line in page.get("local") or [] if any(_overlaps(line["box"], b) for b in boxes))


def writes(data: dict, page: dict, expected: str, output: str) -> list[dict]:
    units = {u["id"]: u for u in page["lines"]}
    truth, ours = numbers(expected), numbers(output)
    engine = numbers("\n".join(u["text"] for u in page["lines"]))
    out = []
    for block in data["blocks"]:
        for k, part in enumerate(block["parts"]):
            if part["kind"] != "write":
                continue
            names = [x for ref in part["lines"] for x in contract._names(ref, "K") if x in units]
            original = "\n".join(units[x]["text"] for x in names)
            boxes = [units[x]["box"] for x in names] or ([block["region"]] if block.get("region") else [])
            local = numbers(local_in(page, boxes))
            supported = lambda n: local[n] > 0 or engine[n] > 0  # noqa: E731
            added = numbers(part["text"]) - numbers(original)
            dropped = numbers(original) - numbers(part["text"])
            out.append({
                "block": block["id"], "part": k, "type": block["type"], "units": names,
                "chars": len("".join(part["text"].split())),
                "letters_added": sum((letters(part["text"]) - letters(original)).values()),
                "letters_dropped": sum((letters(original) - letters(part["text"])).values()),
                "added": [{"number": n, "count": c, "supported": supported(n), "right": ours[n] <= truth[n]}
                          for n, c in sorted(added.items())],
                "dropped": [{"number": n, "count": c, "right": ours[n] >= truth[n]} for n, c in sorted(dropped.items())],
            })
    return out


def conservative(data: dict, found: list[dict]) -> tuple[dict, int]:
    """The allocation with the conservative default: a write that adds a number no reading supports keeps the
    engine's reading (a copy of the blocks it replaces); returns (allocation, writes reverted)."""
    data = copy.deepcopy(data)
    reverted = 0
    for item in found:
        if item["units"] and any(not a["supported"] for a in item["added"]):
            part = data["blocks"][[b["id"] for b in data["blocks"]].index(item["block"])]["parts"][item["part"]]
            part.update(kind="copy", text="")
            reverted += 1
    return data, reverted


def first_allocation(text: str, page: dict) -> dict | None:
    checked = contract.check(text, page)
    if checked.level not in ("invariants", "valid"):
        return None
    total = len(page["lines"])
    counts = Counter(n for _w, _r, n in contract.allocations(checked.data, "K") if n is not None and 1 <= n <= total)
    return {"units": total, "once": sum(counts[n] == 1 for n in range(1, total + 1)),
            "missing": sum(counts[n] == 0 for n in range(1, total + 1)),
            "repeated": sum(counts[n] > 1 for n in range(1, total + 1))}


def destination_shares(result: dict, page: dict) -> dict:
    chars = {u["id"]: len("".join(u["text"].split())) for u in page["lines"]}
    by, by_chars = Counter(), Counter()
    for name, where in result["destinations"].items():
        kind = where.split(":")[0]
        kind = ("aside:excluded" if where.endswith(":excluded") else "aside:block") if kind == "aside" else kind
        by[kind] += 1
        by_chars[kind] += chars.get(name, 0)
    return {"units": dict(by), "chars": dict(by_chars)}


def comparisons(doc: str, n: int, info: dict) -> dict:
    """The other outputs' page *n*: the draft and the agent's result (C1), M and Datalab from the bench."""
    out = {}
    run = AGENT_RUNS / AGENT_RUN.get(doc, f"_c1b/{doc}.tool.medium.r1")
    for label, path in (("draft", run / "work" / "fixed" / f"{doc}.md"), ("agent", run / "out" / f"{doc}.md"),
                        ("M", BENCH / "parserx-fixed" / doc / "output.md"),
                        ("Datalab", BENCH / "datalab-accurate" / doc / "output.md")):
        if path.is_file():
            out[label] = split_pages(path.read_text(encoding="utf-8"), info["texts"])[n - 1]
    return out


def run_score(run_dir: Path) -> dict:
    pages = Pages()
    table: dict = {"pages": {}}
    for doc, n in PAGES:
        pid = page_id(doc, n)
        if not (run_dir / "inputs" / f"{pid}.json").exists():
            continue
        page = load(run_dir, pid)
        info = pages.of(doc)
        expected = info["expected"][n - 1]
        entry = {"others": {k: score(md, expected, f"{pid}:{k}") for k, md in comparisons(doc, n, info).items()},
                 "configs": {}}
        for name in CONFIGS:
            path = run_dir / "results" / name / f"{pid}.json"
            if not path.exists():
                continue
            result = json.loads(path.read_text(encoding="utf-8"))
            data = result["final"]["data"]
            found = writes(data, page, expected, result["markdown"])
            kept, reverted = conservative(data, found)
            entry["configs"][name] = {
                "status": result["final"]["status"], "levels": [r["level"] for r in result["rounds"]],
                "first_allocation": first_allocation(result["rounds"][0].get("text", ""), page),
                "repairs": result["final"]["repairs"], "destinations": destination_shares(result, page),
                "writes": found, "reverted": reverted,
                "unresolved": data.get("unresolved", []),
                "score": score(result["markdown"], expected, f"{pid}:{name}"),
                "score_conservative": score(contract.render(kept, page), expected, f"{pid}:{name}:conservative"),
                "tokens": result["tokens"], "seconds": result["seconds"], "usd": result["usd"],
            }
        table["pages"][pid] = entry
    (run_dir / "scores.json").write_text(json.dumps(table, ensure_ascii=False, indent=1), encoding="utf-8")
    (run_dir / "summary.md").write_text(summary(table), encoding="utf-8")
    return table


def summary(table: dict) -> str:
    out = ["# Scanned-page probe summary (generated by s0_score.py)", "",
           "Key errors of the page (char F1): the other outputs, then each configuration as answered and with the "
           "conservative default (writes adding a number no reading supports reverted to the engine's reading).", "",
           "| page | draft | agent | M | Datalab | configuration | status | answered | conservative (reverted) "
           "| writes | numbers added: supported right/wrong, unsupported right/wrong | dropped right/wrong | s | $ |",
           "|" + "---|" * 14]
    for pid, entry in table["pages"].items():
        others = [_k(entry["others"].get(k)) for k in ("draft", "agent", "M", "Datalab")]
        for name, c in entry["configs"].items():
            tally = Counter()
            for w in c["writes"]:
                for a in w["added"]:
                    tally[("s" if a["supported"] else "u") + ("r" if a["right"] else "w")] += a["count"]
                for d in w["dropped"]:
                    tally["d" + ("r" if d["right"] else "w")] += d["count"]
            out.append(f"| {pid} | {' | '.join(others)} | {name} | {c['status']} | {_k(c['score'])} | "
                       f"{_k(c['score_conservative'])} ({c['reverted']}) | {len(c['writes'])} | "
                       f"{tally['sr']}/{tally['sw']}, {tally['ur']}/{tally['uw']} | {tally['dr']}/{tally['dw']} | "
                       f"{c['seconds']} | {c['usd']} |")
    return "\n".join(out) + "\n"


def _k(s: dict | None) -> str:
    return "—" if not s else f"{s['key_errors']} ({s['char_f1']:.3f})"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    run_score(args.run_dir)
    print((args.run_dir / "summary.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
