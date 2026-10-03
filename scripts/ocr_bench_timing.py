"""Speed of the local OCR engines, one at a time on an idle machine (docs/v2_ocr_engines.md §3).

    uv run --frozen python scripts/ocr_bench_timing.py --engines paddle-vl-local,glm-ocr-local,…

Ten page images of the comparison set (``PAGES``: scanned and born-digital, Chinese and English, tables, formulas,
handwriting, a newspaper), already made by the comparison run under ``<out>/_inputs``.  Each engine first reads one
page to warm up (model loading is reported apart), then the ten in order; nothing else should run meanwhile.
Results go to ``<out>/timing.json`` (merged with earlier engines) and are printed.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from parserx.tool_eval import ocr_engines  # noqa: E402

PAGES = ["ocr01/p001", "ocr_scan_jtg3362/p004", "unseen_scan_form01/p001", "paper_chn02/p002", "paper01/p001",
         "unseen_pdf_tables01/p001", "omni_note_zh_01/p001", "omni_newspaper_zh_01/p001", "omni_exam_paper_en_01/p001",
         "omni_fuzzy_scan_zh_01/p001"]


def pdf_reader(engine, out: Path):
    """For an engine given PDFs (``LocalPdfEngine``): each page image as a one-page image PDF of the page's own
    size, read by the engine's worker."""
    from parserx.tool_eval import runner

    docs = {d.name: d for d in runner.find_documents([ROOT / "ground_truth", ROOT / "ground_truth_public",
                                                      ROOT / "ground_truth_ocr"])}

    def read(image: Path):
        doc, n = image.parent.name, int(image.stem[1:])
        page = ocr_engines.page_images(docs[doc].input, image.parent)[n - 1]
        pdf = ocr_engines.image_pdf([page], image.with_suffix(".pdf"))
        return engine.worker.ask({"pdf": str(pdf.resolve())})

    return read


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engines", required=True)
    parser.add_argument("--out", type=Path, default=ROOT / "eval_runs" / "ocr_bench")
    args = parser.parse_args()
    images = [args.out / "_inputs" / f"{p}.png" for p in PAGES]
    missing = [str(p) for p in images if not p.exists()]
    if missing:
        sys.exit(f"page images missing (run the comparison first): {missing}")
    path = args.out / "timing.json"
    record = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    makers = ocr_engines.makers()
    for name in [e.strip() for e in args.engines.split(",") if e.strip()]:
        engine = makers[name]()
        read = pdf_reader(engine, args.out) if isinstance(engine, ocr_engines.LocalPdfEngine) else engine.read
        started = time.monotonic()
        read(images[0])
        warm = time.monotonic() - started
        seconds = []
        for image in images:
            started = time.monotonic()
            read(image)
            seconds.append(round(time.monotonic() - started, 2))
        worker = getattr(engine, "worker", None)
        if worker is not None:
            worker.close()
        record[name] = {"pages": PAGES, "seconds": seconds, "first_call_seconds": round(warm, 1),
                        "median": statistics.median(seconds), "total": round(sum(seconds), 1),
                        "measured": time.strftime("%Y-%m-%dT%H:%M:%S")}
        print(f"{name:20s} median {statistics.median(seconds):6.1f} s/page  total {sum(seconds):6.1f} s  "
              f"first call {warm:.1f} s")
        path.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
