"""How often the text layer's script candidates are right (Q143 ③): no model, the annotation as the reference.

For every page of every annotated PDF whose text layer the pipeline uses (``assess_native_layer``) — the regression
set (patent01, paper01, text_pic02) left out — the candidates of ``p0_inputs.line_records`` (a run set smaller than
the glyph it is attached to, its baseline shifted), each judged against the annotation's page (``split_pages``):

- **right**: the annotation writes that base with that content as a script of that kind (Unicode, HTML, LaTeX);
- **wrong**: the annotation writes base and content flat, side by side;
- **right, other base**: not found with its base, but the annotation has that content as a script of that kind;
- **unknown**: neither (the line is not in the annotation, or written some other way);
- **prime**: a prime (′) set high: notation, the same to a reader raised or not (the evaluator folds primes);
- **unmapped**: a glyph without a readable character (private use, U+FFFD): written from the image anyway;
- **fragment**: only brackets or separators raised (a citation's brackets without its digits): a wrong reading.

Counted by where the line is (a table of the native extraction, a formula region of the layout detector, other)
and by what the script holds (a bracketed reference, digits, letters, other).  Occurrences are used up: a page's
three candidates k₁ need three k₁ in the annotation.

    uv run python scripts/vision_first/script_audit.py --out eval_runs/<run>   # → audit.json, audit.md
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

import pymupdf  # noqa: E402

from p0_inputs import DPI, GT_DIRS, MATH_LABELS, detector_regions, extraction_tables, line_records  # noqa: E402

from parserx.cache import ResponseCache  # noqa: E402
from parserx.config.schema import load_config  # noqa: E402
from parserx.content.latex import characters  # noqa: E402
from parserx.content.pdf_native import _lines, _signals, assess_native_layer  # noqa: E402
from parserx.eval.key_content import _SUPERSCRIPTS, _scripts  # noqa: E402
from parserx.eval.pages import page_texts, split_pages  # noqa: E402
from parserx.tool_eval.runner import _derived_cache  # noqa: E402

REGRESSION = frozenset({"patent01", "paper01", "text_pic02"})
_REFERENCE = re.compile(r"^[\[［(（][\d\s,，、\-–—~]+[\]］)）]$")
_MINUS = str.maketrans({c: "-" for c in "−‐‑–﹣－"})
_PRIMES = "′″‴'"
_BRACKETS = set("[]［］()（）{}【】,，.。、;；:：=")  # raised alone: part of a larger group, never a script by itself


def documents() -> list[tuple[str, Path]]:
    out = []
    for root in GT_DIRS:
        for d in sorted(root.iterdir()) if root.exists() else []:
            if d.name not in REGRESSION and (d / "input.pdf").exists() and (d / "expected.md").exists():
                out.append((d.name, d / "input.pdf"))
    return out


def marked(text: str) -> str:
    """The annotation's text with every script written ``^{…}`` / ``_{…}`` (LaTeX commands as the characters they
    print), NFKC, without whitespace: what a candidate is looked up in."""
    parts, cursor = [], 0
    for s in _scripts(text):
        parts.append(text[cursor:s.start])
        piece = text[s.start:s.end]
        if piece[0] in "^_":
            parts.append(piece)
        else:
            position = ("^" if piece.lower().startswith("<sup") else "_") if piece.startswith("<") else \
                ("^" if piece[0] in _SUPERSCRIPTS else "_")
            parts.append(f"{position}{{{s.text}}}")
        cursor = s.end
    parts.append(text[cursor:])
    shown = characters(re.sub(r"\\([#%&$])", r"\1", "".join(parts)))  # \# prints #
    flat = re.sub(r"\s+", "", unicodedata.normalize("NFKC", shown).translate(_MINUS)).replace("'", "′")
    return re.sub(r"\^\{(′+)\}|\^(′+)", lambda m: m.group(1) or m.group(2), flat)  # a prime is not a script


def _plain(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text).translate(_MINUS))


def what(content: str) -> str:
    if content and all(c in _PRIMES for c in content):
        return "prime"
    if any(0xE000 <= ord(c) <= 0xF8FF or c == "\ufffd" for c in content):
        return "unmapped"
    if all(c in _BRACKETS for c in content):
        return "punctuation"
    if _REFERENCE.match(content):
        return "reference"
    if content.isdigit():
        return "digits"
    if content.isalpha():
        return "letters"
    return "other"


def scripted_pattern(base: str, position: str, content: str) -> re.Pattern:
    body = re.escape(content)
    script = rf"\{{{body}\}}" + (f"|{body}" if len(content) == 1 else "")
    other = "^" if position == "_" else "_"  # the other script of the same base between them: q_2^{x'}
    return re.compile(rf"{re.escape(base)}\}}?(?:\{other}(?:\{{[^{{}}]*\}}|.))?\{position}(?:{script})")


def audit_page(doc: str, n: int, page: pymupdf.Page, expected: str, tables: list[dict], regions: list[dict]) -> list[dict]:
    records = line_records(page, DPI / 72.0)
    in_table = {line for t in tables for line in t["lines"]}
    math = [r["box"] for r in regions if r["label"] in MATH_LABELS]
    found = []
    for record in records:
        for c in record.get("scripts", ()):
            x = (record["box"][0] + record["box"][2]) / 2
            y = (record["box"][1] + record["box"][3]) / 2
            where = ("table" if record["id"] in in_table else
                     "formula" if any(b[0] <= x <= b[2] and b[1] <= y <= b[3] for b in math) else "other")
            content = _plain(c["t"])
            found.append({"doc": doc, "page": n, "line": record["id"], "text": record["text"],
                          "scripted": record.get("_scripted", ""), "where": where,
                          "base": _plain(c["base"]), "content": content, "kind": c["kind"], "what": what(content)})
    target = marked(expected)
    # used-up counts, per (base, position, content): scripted, then flat; then the content alone as a script
    left: dict[tuple, dict] = {}
    anywhere: dict[tuple, int] = {}
    for f in found:
        if f["what"] in ("prime", "unmapped", "punctuation"):
            f["verdict"] = {"prime": "prime", "unmapped": "unmapped", "punctuation": "fragment"}[f["what"]]
    for f in found:
        if "verdict" in f:
            continue
        position = "^" if f["kind"] == "sup" else "_"
        key = (f["base"], position, f["content"])
        if key not in left:
            left[key] = {"right": len(scripted_pattern(f["base"], position, f["content"]).findall(target)),
                         "wrong": target.count(f["base"] + f["content"]) if f["base"] else 0}
        if (position, f["content"]) not in anywhere:
            anywhere[(position, f["content"])] = len(scripted_pattern("", position, f["content"]).findall(target))
    for f in found:
        if "verdict" in f:
            continue
        position = "^" if f["kind"] == "sup" else "_"
        count = left[(f["base"], position, f["content"])]
        if count["right"] > 0:
            count["right"] -= 1
            anywhere[(position, f["content"])] -= 1
            f["verdict"] = "right"
        elif count["wrong"] > 0:
            count["wrong"] -= 1
            f["verdict"] = "wrong"
        else:
            f["verdict"] = "unknown"
    for f in found:
        position = "^" if f["kind"] == "sup" else "_"
        if f["verdict"] == "unknown" and anywhere[(position, f["content"])] > 0:
            anywhere[(position, f["content"])] -= 1
            f["verdict"] = "right_other_base"
    return found


def run(out_dir: Path) -> dict:
    config = load_config(REPO_ROOT / "configs" / "regression.yaml")
    cache_dir = Path(config.cache.dir)
    derived = ResponseCache(cache_dir if cache_dir.is_absolute() else REPO_ROOT / cache_dir, "read_write")
    candidates: list[dict] = []
    pages: list[dict] = []
    for doc, path in documents():
        expected = (path.parent / "expected.md").read_text(encoding="utf-8")
        slices = split_pages(expected, page_texts(path, _derived_cache()))
        tables = extraction_tables(doc, config, derived)
        with pymupdf.open(path) as pdf:
            for n, page in enumerate(pdf, 1):
                images = [i for i in page.get_image_info(xrefs=True) if i.get("bbox")]
                if not assess_native_layer(_signals(page, _lines(page), images)).ok:
                    continue
                expected_page = slices[n - 1] if n - 1 < len(slices) else ""
                found = audit_page(doc, n, page, expected_page, tables.get(n, []),
                                   detector_regions(page, config, derived))
                candidates += found
                pages.append({"doc": doc, "page": n, "candidates": len(found),
                              "annotation_scripts": sum(1 for s in _scripts(expected_page) if s.token)})
    result = {"documents": sorted({p["doc"] for p in pages}), "pages": pages, "candidates": candidates}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "audit.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / "audit.md").write_text(summary(result), encoding="utf-8")
    return result


VERDICTS = ("right", "right_other_base", "wrong", "fragment", "unknown", "prime", "unmapped")


def _table(rows: dict[str, Counter], label: str) -> list[str]:
    out = [f"| {label} | " + " | ".join(VERDICTS) + " | total |", "|---|" + "---|" * (len(VERDICTS) + 1)]
    for name, c in rows.items():
        out.append(f"| {name} | " + " | ".join(str(c[v]) for v in VERDICTS) + f" | {sum(c.values())} |")
    return out


def summary(result: dict) -> str:
    cands = result["candidates"]
    by_where: dict[str, Counter] = defaultdict(Counter)
    by_what: dict[str, Counter] = defaultdict(Counter)
    by_doc: dict[str, Counter] = defaultdict(Counter)
    for f in cands:
        by_where[f["where"]][f["verdict"]] += 1
        by_what[f["what"]][f["verdict"]] += 1
        by_doc[f["doc"]][f["verdict"]] += 1
    total = Counter(f["verdict"] for f in cands)
    pages_with = [p for p in result["pages"] if p["candidates"]]
    out = ["# Script candidates against the annotation (generated by script_audit.py)", "",
           f"Documents: {', '.join(result['documents'])}; native pages {len(result['pages'])}, "
           f"with candidates {len(pages_with)}; candidates {len(cands)}.", "",
           *_table({"all": total}, "set"), "", *_table(dict(by_where), "where"), "",
           *_table(dict(by_what), "what"), "", *_table(dict(by_doc), "document"), ""]
    for verdict in ("wrong", "fragment", "unknown"):
        out += [f"## {verdict}", "", "| doc | page | line | where | base | kind | content | text layer | scripted |",
                "|---|---|---|---|---|---|---|---|---|"]
        for f in cands:
            if f["verdict"] == verdict:
                out.append(f"| {f['doc']} | {f['page']} | {f['line']} | {f['where']} | {f['base']} | {f['kind']} | "
                           f"{f['content']} | {_cell(f['text'])} | {_cell(f['scripted'])} |")
        out.append("")
    return "\n".join(out) + "\n"


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")[:120]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    run(args.out)
    print((args.out / "audit.md").read_text(encoding="utf-8").split("\n## wrong")[0])


if __name__ == "__main__":
    main()
