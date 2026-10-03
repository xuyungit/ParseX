"""The hard cases of the image-reading probe, read by OCR engines (docs/v2_ocr_engines.md §4.8).

    uv run --frozen python scripts/ocr_probe.py --engines paddle-vl-local-pdf,glm-ocr-local,…
    uv run --frozen python scripts/ocr_probe.py --engines … --rescore

The probe (docs/v2_model_probe.md, ``ground_truth/model_probe``) asks models about 83 crops of our corpus.  Its
transcription, formula and table questions (72) test what is hard for a scan engine: superscripts and subscripts,
look-alike glyphs, copying the original's own errors, formulas, table cells, and nine blocks the scan engine has read
wrong.  Here each engine reads each crop its own way (no prompt of ours): PaddleOCR-VL gets the crop as a one-page
PDF of the crop's pixel size, as ParserX sends images to the scan engine (``scan.image_batch_pdf``); the others get
the PNG.  The answer is the engine's Markdown (``ocr_engines.tidy``, pictures dropped), scored by the probe's own
``score_text`` and ``ability_scores``.  Figure and question cases are model tasks, left out.

Answers: ``eval_runs/ocr_probe/<engine>/<case>.json`` (kept; asked once); report ``eval_runs/ocr_probe/report.md``.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import model_probe  # noqa: E402

from parserx.tool_eval import ocr_engines  # noqa: E402

OUT = ROOT / "eval_runs" / "ocr_probe"
KINDS = ("transcribe", "formula", "table")
ABILITIES = {"A1": "逐字转写", "A2": "数字、单位、符号", "A3": "上下标", "A4": "公式结构", "A5": "易混字形",
             "A6": "忠实原件", "A7": "表格", "A9": "版面和阅读顺序"}
_PICTURE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_COMMENT = re.compile(r"<!--.*?-->", re.S)


def reading_of(markdown: str) -> str:
    """The engine's Markdown as an answer: tidied, without pictures and comments."""
    text = _COMMENT.sub("", _PICTURE.sub("", ocr_engines.tidy(markdown)))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def reader(engine):
    """A function reading one crop (PNG path) to Markdown with *engine*."""
    if isinstance(engine, ocr_engines.LocalPdfEngine):
        import pymupdf

        from parserx.content.scan import image_batch_pdf

        def read_pdf(image: Path) -> str:
            with pymupdf.open(image) as im:
                width, height = im[0].rect.width, im[0].rect.height
            pdf = OUT / "_pdf" / f"{image.stem}.pdf"  # (image: the copy under OUT, see ``crop``)
            if not pdf.exists():
                pdf.parent.mkdir(parents=True, exist_ok=True)
                pdf.write_bytes(image_batch_pdf([(image.read_bytes(), int(width), int(height))]))
            pages = engine.worker.ask({"pdf": str(pdf.resolve())}).get("pages") or []
            return "\n\n".join(p.get("markdown") or "" for p in pages)

        return read_pdf
    if isinstance(engine, ocr_engines.GlmOcrApiPdf):
        import pymupdf

        from parserx.content.scan import image_batch_pdf

        def ask_pdf(image: Path) -> str:
            with pymupdf.open(image) as im:
                width, height = im[0].rect.width, im[0].rect.height
            answer = engine.ask(image_batch_pdf([(image.read_bytes(), int(width), int(height))]))
            return answer.get("md_results") or ""

        return ask_pdf
    return lambda image: engine.read(image).get("markdown") or ""


def crop(case: dict) -> Path:
    """The case's image, copied under OUT: engines may write derived images next to what they read, and the
    probe's own directory stays as it is."""
    path = OUT / "_images" / Path(case["image"]).name
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((model_probe.CASES_DIR / case["image"]).read_bytes())
    return path


def ask(names: list[str], cases: list[dict]) -> None:
    makers = ocr_engines.makers()
    for name in names:
        engine = makers[name]()
        read = reader(engine)
        done = 0
        for case in cases:
            path = OUT / name / f"{case['id']}.json"
            if path.exists():
                continue
            started = time.monotonic()
            try:
                record = {"text": read(crop(case)), "error_kind": None}
            except Exception as exc:  # noqa: BLE001 - a failed answer is recorded, not fatal
                record = {"text": "", "error_kind": "error", "error": f"{type(exc).__name__}: {exc}"[:300]}
            record["seconds"] = round(time.monotonic() - started, 2)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
            done += 1
        worker = getattr(engine, "worker", None)
        if worker is not None:
            worker.close()
        print(f"{name}: {done} asked")


def score(names: list[str], cases: list[dict]) -> str:
    rows, wrong_blocks, traps, per_case = {}, {}, {}, defaultdict(dict)
    for name in names:
        by_ability, totals, failed = defaultdict(list), [], 0
        caught = trap_count = printed = corrected = 0
        for case in cases:
            path = OUT / name / f"{case['id']}.json"
            if not path.exists():
                continue
            record = json.loads(path.read_text(encoding="utf-8"))
            record["text"] = reading_of(record.get("text") or "")
            s = model_probe.score_text(case, record["text"]) if record["text"] else None
            if s is None:
                failed += 1
                per_case[case["id"]][name] = None
                continue
            abilities = model_probe.ability_scores(case, s)
            for a, v in abilities.items():
                by_ability[a].append(v)
            total = statistics.mean(abilities.values())
            totals.append(total)
            per_case[case["id"]][name] = total
            if case.get("engine_wrong") and s.places:
                caught += s.places[0][2]
            for _, outcome in s.traps:
                trap_count += 1
                printed += outcome == "printed"
                corrected += outcome == "corrected"
        rows[name] = (by_ability, totals, failed)
        wrong_blocks[name] = caught
        traps[name] = (printed, corrected, trap_count)
    n_wrong = sum(1 for c in cases if c.get("engine_wrong"))
    used = sorted({a for by, _, _ in rows.values() for a in by})
    lines = ["# OCR 引擎答读图摸底的难题", "",
             f"题目：读图摸底 1.1 的转写、公式、表格题，共 {len(cases)} 道（图片理解和提问题是模型的任务，不考）。"
             "每个引擎用自己的方式读截图，不加我们的提示词；评分照读图摸底（`scripts/model_probe.py`）。",
             "", "| 能力 | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    for a in used:
        cells = []
        for name in names:
            vals = rows[name][0].get(a, [])
            cells.append(f"{100 * statistics.mean(vals):.0f}（{len(vals)}）" if vals else "—")
        lines.append(f"| {a} {ABILITIES.get(a, '')} | " + " | ".join(cells) + " |")
    lines.append("| **总分** | " + " | ".join(
        f"**{100 * statistics.mean(rows[n][1]):.0f}**" if rows[n][1] else "—" for n in names) + " |")
    lines.append(f"| 现用引擎读错的 {n_wrong} 块里读对的 | " + " | ".join(str(wrong_blocks[n]) for n in names) + " |")
    lines.append("| 原件的错字：照印的写 / 按意思改了 / 共几处 | "
                 + " | ".join(f"{traps[n][0]} / {traps[n][1]} / {traps[n][2]}" for n in names) + " |")
    lines.append("| 没有读出东西的题 | " + " | ".join(str(rows[n][2]) for n in names) + " |")
    lines += ["", "## 逐题（每题的能力分平均，0–100）", "", "| 题 | 类 | 现用引擎读错过 | " + " | ".join(names) + " |",
              "|---|---|---|" + "---|" * len(names)]
    for case in cases:
        got = per_case.get(case["id"], {})
        lines.append(f"| {case['id']} | {case['kind']} | {'是' if case.get('engine_wrong') else ''} | " + " | ".join(
            "—" if got.get(n) is None else f"{100 * got[n]:.0f}" for n in names) + " |")
    report = "\n".join(lines) + "\n"
    (OUT / "report.md").write_text(report, encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engines", required=True)
    parser.add_argument("--rescore", action="store_true", help="score the kept answers only")
    args = parser.parse_args()
    names = [e.strip() for e in args.engines.split(",") if e.strip()]
    data = model_probe.load_cases(model_probe.CASES_DIR)
    cases = [c for c in data["cases"] if c["kind"] in KINDS]
    if not args.rescore:
        ask(names, cases)
    print(score(names, cases).split("## 逐题")[0])


if __name__ == "__main__":
    main()
