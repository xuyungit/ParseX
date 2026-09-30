"""P0 measurements (execution plan §3.3), each reported on its own:

- **structured output**: first answer valid, schema-valid (but an invariant broken), valid after the retry, repaired
  mechanically, failed; cut off, empty, timed out and failed requests apart;
- **allocation of the text layer**: lines allocated exactly once in the first answer, lines the program put back;
- **where the text layer went**: copied, written over, table, aside (excluded, or into a block);
- **comparison of written text** with the lines it replaces (the program's evidence, plan §3.4): numbers, letters
  and digits the written text lacks or adds; written text with no line behind it checked against the page's
  local reading;
- **page scores** against the annotation (evaluator 2.5), paired on the same page with the arms M, R and L: the
  annotation and each arm's output cut into pages the same way (``eval/pages.split_pages``); a P0 answer is the page;
- tokens, seconds and cost per page.

    uv run python scripts/vision_first/p0_score.py --run-dir eval_runs/<run>
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import p0_contract as contract  # noqa: E402
from p0_inputs import PAGES, document, load, page_id  # noqa: E402
from p0_run import CONFIGS  # noqa: E402

from parserx.content.latex import characters  # noqa: E402
from parserx.eval.key_content import extract_key_tokens  # noqa: E402
from parserx.eval.metrics import evaluate_markdown  # noqa: E402
from parserx.eval.pages import page_texts, split_pages  # noqa: E402
from parserx.reading.compare import normalize  # noqa: E402
from parserx.tool_eval.runner import _derived_cache, _scores_of  # noqa: E402

BENCH = REPO_ROOT / "eval_runs" / "bench"
ARMS = {"M": "parserx-fixed", "R": "parserx-fixed-R", "L": "llamaparse-agentic"}
_NUM = re.compile(r"\d+(?:\.\d+)?")
_TAG = re.compile(r"<[^<>]+>")
_SPACED = re.compile(r"(\\(?!begin|end)[a-zA-Z]+)(?![a-zA-Z])")  # a command, other than an environment's


def _shown(text: str) -> str:
    """The characters a LaTeX text shows (``content/latex.characters``), with a space where a command stood, so
    ``3.886\\times10`` stays two numbers; a written HTML table's tags and attributes (``colspan="2"``) are markup."""
    return characters(_SPACED.sub(r"\1 ", _TAG.sub(" ", text)))


def numbers(text: str) -> Counter:
    """Digit runs as the characters show them (NFKC: a superscript 2 is a 2)."""
    return Counter(_NUM.findall(unicodedata.normalize("NFKC", _shown(text))))


def letters(text: str) -> Counter:
    return Counter(normalize(_shown(text)))


def expected_of(doc: str) -> str:
    return (document(doc).parent / "expected.md").read_text(encoding="utf-8")


class Pages:
    """Per document: the page texts of the source, the annotation's pages and each arm's pages."""

    def __init__(self) -> None:
        self._docs: dict[str, dict] = {}

    def of(self, doc: str) -> dict:
        if doc not in self._docs:
            texts = page_texts(document(doc), _derived_cache())
            arms = {}
            for arm, tool in ARMS.items():
                out = BENCH / tool / doc / "output.md"
                if out.exists():
                    arms[arm] = split_pages(out.read_text(encoding="utf-8"), texts)
            self._docs[doc] = {"texts": texts, "expected": split_pages(expected_of(doc), texts), "arms": arms,
                               "local": local_texts(document(doc)), "keys": furniture_keys(document(doc))}
        return self._docs[doc]


def local_texts(source: Path) -> list[str]:
    """Each page's local reading alone (RapidOCR at the evaluation's resolution; derived cache)."""
    import pymupdf

    from parserx.content.scan import render_page_at
    from parserx.eval.pages import READING_DPI
    from parserx.reading.local import LocalReader, read_cached

    reader, out = LocalReader(), []
    with pymupdf.open(source) as doc:
        for n in range(1, doc.page_count + 1):
            png, _, _ = render_page_at(doc, n, READING_DPI)
            out.append("\n".join(text for _, text, _ in read_cached(reader, png, _derived_cache())))
    return out


def furniture_key(text: str) -> str:
    """A line as it repeats from page to page: digits (page numbers, dates), spaces and unmapped glyphs dropped."""
    return "".join(ch for ch in unicodedata.normalize("NFKC", text) if not (ch.isdigit() or ch.isspace() or contract.unmapped(ch)))


def furniture_keys(source: Path) -> list[set[str]]:
    """Per page, the keys of its text-layer lines."""
    import pymupdf

    from parserx.content.pdf_native import _lines

    with pymupdf.open(source) as doc:
        return [{furniture_key(line.text) for line in _lines(page)} for page in doc]


BAG_KINDS = ("number", "unit", "sign", "script")


def score(md: str, expected: str, name: str) -> dict:
    """The evaluator's scores, plus the key tokens compared as multisets (``bag_missing`` / ``bag_extra``): the
    evaluator aligns them in order (LCS), so a block placed elsewhere counts as missing and extra; the multiset
    shows what changed regardless of place."""
    scores = _scores_of(evaluate_markdown(md, expected, name=name))
    ours, theirs = extract_key_tokens(md), extract_key_tokens(expected)
    scores["bag_missing"] = {k: sum((Counter(map(str, theirs[k])) - Counter(map(str, ours[k]))).values()) for k in BAG_KINDS}
    scores["bag_extra"] = {k: sum((Counter(map(str, ours[k])) - Counter(map(str, theirs[k]))).values()) for k in BAG_KINDS}
    return scores


_EXACT = re.compile(r"[a-z0-9\u03b1-\u03c9]")  # normalized Latin, digits, Greek: the text layer holds them exactly


def written_checks(data: dict, page: dict, seen_text: str, local_text: str = "") -> list[dict]:
    """Every write part against the lines it replaces (and, with none, against the page's local reading).  A missing
    character other than a Latin or Greek letter or a digit counts only when the local reading also sees it: one no
    one else sees is a mis-mapped glyph of the text layer (Q70, ``tools/formulas._lost``), listed apart."""
    text_of = {f"L{k}": line["text"] for k, line in enumerate(page["lines"], 1)}
    seen = Counter(normalize(seen_text))
    local = Counter(normalize(local_text))
    seen_numbers = numbers(seen_text)
    out = []
    for block in data["blocks"]:
        for part in block["parts"]:
            if part["kind"] != "write":
                continue
            names = [n for ref in part["lines"] for n in [f"L{k}" for k in contract.expand(ref) or []]]
            written = part["text"]
            item = {"block": block["id"], "type": block["type"], "lines": len(names),
                    "text": written if len(written) <= 400 else written[:400] + "…"}
            if names:
                original = "\n".join(text_of.get(n, "") for n in names)
                item["numbers_missing"] = dict(numbers(original) - numbers(written))
                item["numbers_added"] = dict(numbers(written) - numbers(original))
                missing = sorted((letters(original) - letters(written)).elements())
                item["chars_missing"] = "".join(ch for ch in missing if _EXACT.fullmatch(ch) or local[ch])
                item["glyphs_unseen"] = "".join(ch for ch in missing if not (_EXACT.fullmatch(ch) or local[ch]))
                item["chars_added"] = "".join(sorted((letters(written) - letters(original)).elements()))
            else:  # content no text line holds: is it on the page (local reading)?
                item["numbers_unseen"] = dict(numbers(written) - seen_numbers)
                item["chars_unseen"] = "".join(sorted((letters(written) - seen).elements()))
            out.append(item)
    return out


def signals(data: dict, page: dict, keys: list[set[str]], n: int) -> dict[str, list[str]]:
    """Review items the program can raise from the allocation (only pointers, the output is not changed; Q56):

    - ``aside_unrepeated``: a line excluded as page furniture that no other page of the document repeats (running
      heads, feet and page numbers repeat; digits are not compared);
    - ``copied_scripts``: a line copied (or in a table) without its script candidates (contracts 2–4: the switch
      off; from v5 a copy takes them), or with glyphs the text layer does not map — what the model may have needed to
      write;
    - ``possible_duplicate``: a written part whose added letters and digits are all in the copied lines next to the
      lines it replaces."""
    lines = page["lines"]
    others = set().union(*(k for i, k in enumerate(keys, 1) if i != n)) if len(keys) > 1 else set()
    out: dict[str, list[str]] = {"aside_unrepeated": [], "copied_scripts": [], "possible_duplicate": []}
    for item in data.get("aside", []):
        if item["to"] != contract.EXCLUDED:
            continue
        for name in (x for ref in item["lines"] for x in contract._names(ref)):
            key = furniture_key(lines[int(name[1:]) - 1]["text"])
            if key and key not in others:
                out["aside_unrepeated"].append(name)
    where = contract.destinations(data, page)
    for block in data["blocks"]:
        for part in block["parts"]:
            names = [x for ref in part["lines"] for x in contract._names(ref) if 1 <= int(x[1:]) <= len(lines)]
            if part["kind"] in ("copy", "table"):
                out["copied_scripts"] += [x for x in names if lines[int(x[1:]) - 1].get("odd")
                                          or (lines[int(x[1:]) - 1].get("scripts") and part.get("scripts") is False)]
            elif names:
                original = "\n".join(lines[int(x[1:]) - 1]["text"] for x in names)
                added = letters(part["text"]) - letters(original)
                if sum(added.values()) < 2:
                    continue
                nums = sorted(int(x[1:]) for x in names)
                near = [k for k in (nums[0] - 2, nums[0] - 1, nums[-1] + 1, nums[-1] + 2)
                        if 1 <= k <= len(lines) and where.get(f"L{k}", "").startswith("copy:")]
                if near and not added - letters("\n".join(lines[k - 1]["text"] for k in near)):
                    out["possible_duplicate"].append(f"{block['id']}:{','.join(contract.compress(nums))}")
    return out


def allocation_of(checked_data: dict | None, page: dict) -> dict | None:
    """Lines allocated exactly once in one answer (schema-valid), and the rest by kind."""
    if checked_data is None:
        return None
    total = len(page["lines"])
    counts = Counter(n for _w, _r, n in contract.allocations(checked_data) if n is not None and 1 <= n <= total)
    once = sum(1 for n in range(1, total + 1) if counts[n] == 1)
    return {"lines": total, "once": once, "missing": sum(1 for n in range(1, total + 1) if counts[n] == 0),
            "repeated": sum(1 for n in range(1, total + 1) if counts[n] > 1),
            "bad_refs": sum(1 for _w, _r, n in contract.allocations(checked_data) if n is None or not 1 <= n <= total)}


def destination_shares(result: dict, page: dict) -> dict:
    chars = {f"L{k}": len("".join(line["text"].split())) for k, line in enumerate(page["lines"], 1)}
    by: Counter = Counter()
    by_chars: Counter = Counter()
    for name, where in result["destinations"].items():
        kind = where.split(":")[0]
        if kind == "aside":
            kind = "aside:excluded" if where.endswith(":excluded") else "aside:block"
        by[kind] += 1
        by_chars[kind] += chars.get(name, 0)
    return {"lines": dict(by), "chars": dict(by_chars)}


def run_score(run_dir: Path) -> dict:
    pages = Pages()
    table: dict = {"pages": {}, "configs": {}}
    for doc, n in PAGES:
        pid = page_id(doc, n)
        page = load(run_dir, pid)
        info = pages.of(doc)
        expected = info["expected"][n - 1]
        entry = {"arms": {arm: score(slices[n - 1], expected, f"{pid}:{arm}") for arm, slices in info["arms"].items()},
                 "configs": {}}
        for name, *_ in CONFIGS:
            path = run_dir / "results" / name / f"{pid}.json"
            if not path.exists():
                continue
            result = json.loads(path.read_text(encoding="utf-8"))
            rounds = result["rounds"]
            first = contract.check(rounds[0].get("text", ""), page)
            entry["configs"][name] = {
                "lines": len(page["lines"]), "status": result["final"]["status"], "levels": [r["level"] for r in rounds],
                "finish": [r.get("finish") for r in rounds],
                "timeouts": sum(a.get("timeout", False) for r in rounds for a in r.get("failed_attempts", [])),
                "failed_attempts": sum(len(r.get("failed_attempts", [])) for r in rounds),
                "errors": [r.get("error") for r in rounds if r.get("error")],
                "first_allocation": allocation_of(first.data if first.level in ("invariants", "valid") else None, page),
                "repairs": result["final"]["repairs"],
                "destinations": destination_shares(result, page),
                "written": written_checks(result["final"]["data"], page, info["texts"][n - 1], info["local"][n - 1]),
                "signals": signals(result["final"]["data"], page, info["keys"], n),
                "unresolved": result["final"]["data"].get("unresolved", []),
                "score": score(result["markdown"], expected, f"{pid}:{name}"),
                "tokens": result["tokens"], "seconds": result["seconds"], "usd": result["usd"],
            }
        table["pages"][pid] = entry
    (run_dir / "scores.json").write_text(json.dumps(table, ensure_ascii=False, indent=1), encoding="utf-8")
    (run_dir / "summary.md").write_text(summary(table), encoding="utf-8")
    return table


# ── the summary tables ──────────────────────────────────────────────────


def summary(table: dict) -> str:
    names = [c[0] for c in CONFIGS if any(c[0] in p["configs"] for p in table["pages"].values())]
    out = ["# P0 summary (generated by p0_score.py)", ""]
    out += ["## Structured output and allocation, per configuration", "",
            "| configuration | first valid | schema-valid first | valid after retry | repaired | failed | cut off | empty | "
            "timeouts | errors | lines once (first answer) | lines put back | $ total | s / page |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for name in names:
        rows = [p["configs"][name] for p in table["pages"].values() if name in p["configs"]]
        status = Counter(r["status"] for r in rows)
        schema_first = sum(r["levels"][0] in ("invariants", "valid") for r in rows)
        cut = sum(any(f and (f.startswith("incomplete") or f == "length") for f in r["finish"]) for r in rows)
        empty = sum("empty" in r["levels"] for r in rows)
        once = sum(r["first_allocation"]["once"] for r in rows if r["first_allocation"])
        total = sum(r["lines"] for r in rows)  # a first answer that cannot be read allocates none
        put_back = sum(len(re.findall(r"L\d+", " ".join(x for x in r["repairs"] if x.startswith("补回")))) for r in rows)
        usd = [r["usd"] for r in rows]
        out.append(f"| {name} | {status['first_valid']}/{len(rows)} | {schema_first} | {status['valid_after_retry']} | "
                   f"{status['repaired']} | {status['failed']} | {cut} | {empty} | {sum(r['timeouts'] for r in rows)} | "
                   f"{sum(len(r['errors']) for r in rows)} | {once}/{total} ({once / total:.1%}) | {put_back} | "
                   f"{'?' if None in usd else f'{sum(usd):.3f}'} | {sum(r['seconds'] for r in rows) / len(rows):.0f} |"
                   if total else f"| {name} | … |")
    out += ["", "## Key content errors per page (evaluator 2.5; lower is better), paired with M, R, L", ""]
    arms = list(ARMS)
    out += ["| page | " + " | ".join(arms + names) + " |", "|---|" + "---|" * (len(arms) + len(names))]
    for pid, p in table["pages"].items():
        cells = [str(p["arms"].get(a, {}).get("key_errors", "—")) for a in arms]
        cells += [str(p["configs"].get(n, {}).get("score", {}).get("key_errors", "—")) for n in names]
        out.append(f"| {pid} | " + " | ".join(cells) + " |")
    totals = [sum(p["arms"].get(a, {}).get("key_errors", 0) for p in table["pages"].values()) for a in arms]
    totals += [sum(p["configs"].get(n, {}).get("score", {}).get("key_errors", 0) for p in table["pages"].values())
               for n in names]
    out.append("| **sum** | " + " | ".join(str(t) for t in totals) + " |")
    out += ["", "## char_f1 per page", "", "| page | " + " | ".join(arms + names) + " |",
            "|---|" + "---|" * (len(arms) + len(names))]
    for pid, p in table["pages"].items():
        cells = [_f(p["arms"].get(a, {}).get("char_f1")) for a in arms]
        cells += [_f(p["configs"].get(n, {}).get("score", {}).get("char_f1")) for n in names]
        out.append(f"| {pid} | " + " | ".join(cells) + " |")
    out += ["", "## Key error classes summed over the ten pages (missing/extra)", "",
            "| arm | number | unit | sign | script | unmapped | attribution | lost runs (chars) | added runs (chars) | "
            "formulas paired / expected | formula similarity (mean) |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for label, get in [(a, lambda p, a=a: p["arms"].get(a)) for a in arms] + \
                      [(n, lambda p, n=n: p["configs"].get(n, {}).get("score")) for n in names]:
        rows = [get(p) for p in table["pages"].values() if get(p)]
        cls = lambda k: f"{sum(r['key_missing'].get(k, 0) for r in rows)}/{sum(r['key_extra'].get(k, 0) for r in rows)}"  # noqa: E731
        sims = [r["formula_similarity"] for r in rows if r.get("formula_similarity") is not None]
        out.append(f"| {label} | {cls('number')} | {cls('unit')} | {cls('sign')} | {cls('script')} | {cls('unmapped')} | "
                   f"{cls('attribution')} | {sum(r['lost_runs'] for r in rows)} ({sum(r['lost_chars'] for r in rows)}) | "
                   f"{sum(r['added_runs'] for r in rows)} ({sum(r['added_chars'] for r in rows)}) | "
                   f"{sum(r['formulas_paired'] or 0 for r in rows)}/{sum(r['formulas_expected'] or 0 for r in rows)} | "
                   f"{(sum(sims) / len(sims)) if sims else float('nan'):.3f} |")
    out += ["", "## The same classes as multisets (order ignored; missing/extra summed over the ten pages)", "",
            "| arm | number | unit | sign | script | sum |", "|---|---|---|---|---|---|"]
    for label, get in [(a, lambda p, a=a: p["arms"].get(a)) for a in arms] + \
                      [(n, lambda p, n=n: p["configs"].get(n, {}).get("score")) for n in names]:
        rows = [get(p) for p in table["pages"].values() if get(p)]
        bag = lambda k: (sum(r["bag_missing"][k] for r in rows), sum(r["bag_extra"][k] for r in rows))  # noqa: E731
        cells = [bag(k) for k in BAG_KINDS]
        out.append(f"| {label} | " + " | ".join(f"{m}/{e}" for m, e in cells) + f" | {sum(m + e for m, e in cells)} |")
    out += ["", "## Where the text layer went (lines; summed over pages)", "",
            "| configuration | copy | write | table | aside:excluded | aside:block |", "|---|---|---|---|---|---|"]
    for name in names:
        c: Counter = Counter()
        for p in table["pages"].values():
            if name in p["configs"]:
                c.update(p["configs"][name]["destinations"]["lines"])
        out.append(f"| {name} | {c['copy']} | {c['write']} | {c['table']} | {c['aside:excluded']} | {c['aside:block']} |")
    out += ["", "## Written text against what it replaces (the comparison's signals)", "",
            "| configuration | write parts | over lines | numbers differ | letters/digits differ | without lines | "
            "unseen numbers there |", "|---|---|---|---|---|---|---|"]
    for name in names:
        parts = [w for p in table["pages"].values() if name in p["configs"] for w in p["configs"][name]["written"]]
        over = [w for w in parts if w["lines"]]
        free = [w for w in parts if not w["lines"]]
        out.append(f"| {name} | {len(parts)} | {len(over)} | "
                   f"{sum(bool(w['numbers_missing'] or w['numbers_added']) for w in over)} | "
                   f"{sum(bool(w['chars_missing'] or w['chars_added']) for w in over)} | {len(free)} | "
                   f"{sum(bool(w['numbers_unseen']) for w in free)} |")
    out += ["", "## Review signals raised from the allocation (summed over pages)", "",
            "| configuration | excluded, not repeated | copied with candidates or odd glyphs | possible duplicate | "
            "text-layer glyphs no reading sees |", "|---|---|---|---|---|"]
    for name in names:
        rows = [p["configs"][name] for p in table["pages"].values() if name in p["configs"]]
        sig = lambda k: sum(len(r["signals"][k]) for r in rows)  # noqa: E731
        unseen = sum(bool(w.get("glyphs_unseen")) for r in rows for w in r["written"])
        out.append(f"| {name} | {sig('aside_unrepeated')} | {sig('copied_scripts')} | {sig('possible_duplicate')} | "
                   f"{unseen} parts |")
    out += ["", "## Tokens, seconds, cost per page (mean over the ten pages)", "",
            "| configuration | input | cached | output | reasoning | seconds | $ / page |", "|---|---|---|---|---|---|---|"]
    for name in names:
        rows = [p["configs"][name] for p in table["pages"].values() if name in p["configs"]]
        mean = lambda k: sum(r["tokens"][k] for r in rows) / len(rows)  # noqa: E731
        usd = [r["usd"] for r in rows]
        out.append(f"| {name} | {mean('input'):.0f} | {mean('cached'):.0f} | {mean('output'):.0f} | "
                   f"{mean('reasoning'):.0f} | {sum(r['seconds'] for r in rows) / len(rows):.0f} | "
                   f"{'?' if None in usd else f'{sum(usd) / len(rows):.4f}'} |")
    return "\n".join(out) + "\n"


def _f(v) -> str:
    return "—" if v is None else f"{v:.3f}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    run_score(args.run_dir)
    print((args.run_dir / "summary.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
