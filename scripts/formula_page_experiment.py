#!/usr/bin/env python3
"""Formulas on native pages: whole-page scan-engine reading vs formula crops vs the text layer (Q70 follow-up).

    uv run python scripts/formula_page_experiment.py DOC1,DOC2 OUT_DIR [--fixed eval_runs/<fixed full run>]

Per document:
1. every page read by the scan engine (one batched job), as scanned pages are;
2. display formulas: each detector ``display_formula`` region matched to its annotated formula by the text layer's
   characters (independent of the engines); the page reading's formula block there vs the crop reading;
3. inline math of the annotation (``$…$``) found in the current output vs in the page reading;
4. per native text block, the page reading's text over it: characters the text layer has that the reading lacks
   (lost) — exact, 1–2, more — and after reconciliation (where the two differ one character for one, the text
   layer's character is taken);
5. char_f1 of the page reading as a whole document vs the current output.
"""

from __future__ import annotations

import difflib
import json
import re
import sys
import tempfile
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import pymupdf  # noqa: E402
from rapidfuzz import fuzz  # noqa: E402

from parserx.config.schema import load_config  # noqa: E402
from parserx.content import scan  # noqa: E402
from parserx.eval.metrics import evaluate_markdown  # noqa: E402
from parserx.ir.anchor import PdfAnchor  # noqa: E402
from parserx.layout.detector import detect_cached  # noqa: E402
from parserx.reading.compare import normalize  # noqa: E402
from parserx.tools import workspace_init  # noqa: E402
from parserx.tools.context import ToolContext  # noqa: E402
from parserx.tools.formulas import _symbols  # noqa: E402
from parserx.workspace import Workspace  # noqa: E402

_FORMULA = {"display_formula", "formula"}


def canon(latex: str) -> str:
    """LaTeX without presentation (spacing, sizing, fonts, primes written three ways) for comparison."""
    s = latex or ""
    s = re.sub(r"\\tag\{([^}]*)\}", r"(\1)", s)
    s = re.sub(r"\^\{\^\{\\prime\}\}|\^\{\\prime\}|\\prime", "'", s)
    s = re.sub(r"\^\{'\}", "'", s)
    s = re.sub(r"\\(big|Big|bigg|Bigg|left|right)(?=[\[\]()\\|.{}])", "", s)
    s = re.sub(r"\\begin\{[a-z*]+\}(\{[a-z]+\})?|\\end\{[a-z*]+\}", "", s)
    s = re.sub(r"\\(mathrm|text|operatorname|displaystyle|mathit|mathbf|quad|qquad|,|;|!|:)", "", s)
    return re.sub(r"[{}\s$&]|\\\\", "", s)


def display_formulas(md: str) -> list[str]:
    return [f.strip() for f in re.findall(r"\$\$(.+?)\$\$", md, flags=re.S) if f.strip()]


def inline_math(md: str) -> list[str]:
    body = re.sub(r"\$\$.+?\$\$", " ", md, flags=re.S)
    return [m.strip() for m in re.findall(r"(?<!\$)\$([^$\n]+?)\$(?!\$)", body) if len(canon(m.strip())) >= 2]


def lost(native: str, reading: str) -> Counter:
    return Counter(normalize(native)) - Counter(normalize(_symbols(reading)))


def reconcile(native: str, reading: str) -> str:
    """The reading with the text layer's character where the two differ one for one (letters and digits)."""
    a, b = normalize(native), normalize(_symbols(reading))
    fixes = {}
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "replace" and i2 - i1 == j2 - j1:
            for k in range(i2 - i1):
                fixes[j1 + k] = a[i1 + k]
    if not fixes:
        return reading
    out, pos = [], 0  # walk the reading's letters and digits in the order normalize sees them
    for ch in _symbols(reading):
        if normalize(ch):
            out.append(fixes.get(pos, ch))
            pos += 1
        else:
            out.append(ch)
    return "".join(out)


def main() -> int:
    docs, out = sys.argv[1].split(","), Path(sys.argv[2])
    fixed = Path(sys.argv[sys.argv.index("--fixed") + 1]) if "--fixed" in sys.argv else None
    out.mkdir(parents=True, exist_ok=True)
    config = load_config(REPO / "configs" / "regression_v2.yaml")
    config.cache.mode, config.cache.dir = "read_write", str(out / "cache")
    report = {}
    for doc in docs:
        gt = next(g / doc for g in (REPO / "ground_truth", REPO / "ground_truth_public") if (g / doc).is_dir())
        expected = (gt / "expected.md").read_text(encoding="utf-8")
        tmp = Path(tempfile.mkdtemp())
        workspace_init(gt / "input.pdf", tmp / "ws", config=config)
        ws = Workspace.open(tmp / "ws")
        ctx = ToolContext(ws, config)
        state = ws.load()
        with pymupdf.open(gt / "input.pdf") as pdf:
            pages = list(range(1, pdf.page_count + 1))
            results = ctx.ocr().recognize_pdf(scan.batch_pdf(pdf, pages))
            entries = {}  # page → [(label, content, bbox in page points)]
            for n, res in zip(pages, results):
                pr = res.raw["layoutParsingResults"][0].get("prunedResult", {})
                k = pdf[n - 1].rect.width / (pr.get("width") or pdf[n - 1].rect.width)
                entries[n] = [(e.get("block_label", ""), str(e.get("block_content", "")),
                               tuple(v * k for v in e.get("block_bbox", [0, 0, 0, 0])))
                              for e in pr.get("parsing_res_list", [])]
            regions = []
            for page in pdf:
                png = page.get_pixmap(dpi=config.layout.page_dpi).tobytes("png")
                for r in detect_cached(ctx.detector(), png, ctx.cache):
                    if r.label == "display_formula":
                        box = tuple(v * 72 / config.layout.page_dpi for v in r.bbox)
                        regions.append((page.number + 1, box, page.get_text("text", clip=pymupdf.Rect(box)).strip()))
        page_md = "\n\n".join(c for n in pages for _, c, _ in entries[n] if c.strip())
        current = (fixed / "outputs" / f"{doc}.md").read_text(encoding="utf-8") if fixed else ""

        # 2. display formulas: page reading vs the annotation, where the text layer matches a whole formula
        shown = display_formulas(expected)
        disp = []
        for n, box, native in regions:
            best = max(((fuzz.ratio(normalize(native), normalize(e)), e) for e in shown), default=(0, None))
            if best[0] < 70:
                continue
            over = [c for label, c, b in entries[n] if label in _FORMULA and _overlap(b, box)]
            reading = " ".join(over).replace("$$", " ")
            disp.append(fuzz.ratio(canon(reading), canon(best[1])) if over else 0)

        # 3. inline math
        wanted = inline_math(expected)
        cur_c, page_c = canon(current), canon(page_md)
        inline = {"expected": len(wanted), "in_output": sum(canon(m) in cur_c for m in wanted),
                  "in_page_reading": sum(canon(m) in page_c for m in wanted)}

        # 4. disagreement per page-reading block (with the native text blocks under it), before and after
        #    reconciliation; only blocks that hold math (a $ in the reading) — the ones a formula step would touch
        buckets, after, samples = Counter(), Counter(), []
        native = [b for b in state.blocks if b.kind.value == "text" and b.text and isinstance(b.anchors[0], PdfAnchor)]
        for n in pages:
            for label, content, box in entries[n]:
                if "$" not in content or label in ("image", "chart"):
                    continue
                under = [b for b in native if b.anchors[0].page == n and _centre_in(b.anchors[0].bbox, box)]
                if not under:
                    buckets["no native text"] += 1
                    continue
                text = "\n".join(b.text for b in under)
                for counter, reading in ((buckets, content), (after, reconcile(text, content))):
                    missing = sum(lost(text, reading).values())
                    total = max(len(normalize(text)), 1)
                    counter["exact" if missing == 0 else "1-2 chars" if missing <= 2 else
                            "≤5%" if missing / total <= 0.05 else "more"] += 1
                if 0 < sum(lost(text, content).values()) and len(samples) < 6:
                    samples.append({"lost": "".join(sorted(lost(text, content).elements()))[:40],
                                    "native": text[:120], "reading": content[:160]})

        exp_scores = evaluate_markdown(page_md, expected, name=doc).text.char_f1
        cur_scores = evaluate_markdown(current, expected, name=doc).text.char_f1 if current else None
        report[doc] = {"pages": len(pages), "display_scored": len(disp),
                       "display_page_mean": round(sum(disp) / len(disp), 1) if disp else None,
                       "inline": inline, "blocks_lost": dict(buckets), "blocks_lost_after_reconcile": dict(after), "samples": samples,
                       "char_f1_page_reading": round(exp_scores, 4),
                       "char_f1_current": round(cur_scores, 4) if cur_scores is not None else None}
        print(doc, json.dumps(report[doc], ensure_ascii=False), flush=True)
        (out / f"{doc}.page_reading.md").write_text(page_md, encoding="utf-8")
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


def _overlap(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _centre_in(inner, outer) -> bool:
    cx, cy = (inner[0] + inner[2]) / 2, (inner[1] + inner[3]) / 2
    return outer[0] <= cx <= outer[2] and outer[1] <= cy <= outer[3]


if __name__ == "__main__":
    sys.exit(main())
