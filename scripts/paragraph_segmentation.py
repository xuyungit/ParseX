#!/usr/bin/env python3
"""Paragraph segmentation of native PDF text, measured against the annotations (Q80).

For every pair of consecutive text lines on a page (the library's line order), the annotation says whether they are
in one paragraph (``expected.md`` split at blank lines; list items and headings are paragraphs of their own; tables,
images and descriptions left out) and the extraction says whether it put them in one block.  Only lines that can be
placed in the annotation are counted (at least 8 letters or digits).  Reported: join precision, recall, F1, wrong
and missed joins — overall and per document.

    uv run python scripts/paragraph_segmentation.py            # the production layout hook (cached detections)
    uv run python scripts/paragraph_segmentation.py --no-layout  # geometry only
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import pymupdf  # noqa: E402

from parserx.config.schema import load_config  # noqa: E402
from parserx.content.pdf_native import extract_pdf  # noqa: E402
from parserx.tools.init import _page_layout  # noqa: E402

GT_DIRS = (REPO_ROOT / "ground_truth", REPO_ROOT / "ground_truth_public")
MIN_CHARS = 8
_LIST = re.compile(r"^\s*([-*+]|\d+[.)]|[a-z][.)]|[ivx]+\.)\s")


def norm(text: str) -> str:
    return re.sub(r"[\s#*`$\\|>_~\-]+", "", unicodedata.normalize("NFKC", text)).lower()


def paragraphs(md: str) -> list[str]:
    md = re.sub(r"<!--.*?-->", "", md, flags=re.S)
    md = re.sub(r"<table.*?</table>", "\n\n", md, flags=re.S)
    md = re.sub(r"```.*?```", "\n\n", md, flags=re.S)  # code blocks: their comment lines are not headings
    out: list[str] = []
    for chunk in re.split(r"\n\s*\n", md):
        lines = [line for line in chunk.split("\n") if line.strip()]
        if not lines or lines[0].lstrip().startswith(("|", "![", ">")):
            continue
        current: list[str] = []
        for line in lines:
            if (_LIST.match(line) or line.startswith("#")) and current:
                out.append(" ".join(current))
                current = []
            current.append(line)
            if line.startswith("#"):
                out.append(" ".join(current))
                current = []
        if current:
            out.append(" ".join(current))
    return out


def page_lines(pdf: Path) -> list[list[tuple[str, tuple]]]:
    pages = []
    with pymupdf.open(pdf) as doc:
        for page in doc:
            raw = page.get_text("rawdict", flags=pymupdf.TEXT_PRESERVE_WHITESPACE)
            pages.append([("".join(c["c"] for s in line["spans"] for c in s["chars"]), line["bbox"])
                          for block in raw["blocks"] if block["type"] == 0 for line in block["lines"]])
    return pages


def score(pdf: Path, expected: str, layout) -> Counter:
    ext = extract_pdf(pdf, layout=layout)
    # line → block: the ledger's native_line entries carry each line's page and box
    block_of = {(e.source.page, tuple(round(v, 1) for v in e.source.bbox)): e.block
                for e in ext.ledger if e.unit == "native_line"}
    paras = paragraphs(expected)
    text, owner = [], []
    for i, p in enumerate(paras):
        n = norm(p)
        text.append(n)
        owner += [i] * len(n)
    joined, pointer, counts = "".join(text), 0, Counter()
    for n, lines in enumerate(page_lines(pdf), 1):
        ids, blocks = [], []
        for line_text, bbox in lines:
            t = norm(line_text)
            pos = joined.find(t, max(0, pointer - 3000)) if len(t) >= MIN_CHARS else -1
            if pos < 0 and len(t) >= MIN_CHARS:
                pos = joined.find(t)
            placed = pos >= 0 and owner[pos] == owner[pos + len(t) - 1]
            ids.append(owner[pos] if placed else None)
            if placed:
                pointer = pos + len(t)
            blocks.append(block_of.get((n, tuple(round(v, 1) for v in bbox))))
        for i in range(len(ids) - 1):
            if None in (ids[i], ids[i + 1], blocks[i], blocks[i + 1]):
                continue
            counts[(ids[i] == ids[i + 1], blocks[i] == blocks[i + 1])] += 1
    return counts


def summary(label: str, c: Counter) -> str:
    tp, fp, fn = c[(True, True)], c[(False, True)], c[(True, False)]
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return f"{label:42} P {p:.3f} R {r:.3f} F1 {f:.3f}  wrong {fp:4}  missed {fn:4}  pairs {sum(c.values())}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-layout", action="store_true", help="geometry only (no layout detector)")
    args = ap.parse_args()
    layout = None if args.no_layout else _page_layout(load_config(REPO_ROOT / "configs" / "regression.yaml"))
    total = Counter()
    for gt in GT_DIRS:
        for d in sorted(gt.iterdir()):
            pdf, expected = d / "input.pdf", d / "expected.md"
            if pdf.is_file() and expected.is_file():
                c = score(pdf, expected.read_text(encoding="utf-8"), layout)
                total += c
                if c[(False, True)] or c[(True, False)]:
                    print(summary(d.name, c))
    print(summary("ALL", total))
    return 0


if __name__ == "__main__":
    sys.exit(main())
