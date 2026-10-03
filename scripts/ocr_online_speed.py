"""How fast the online scan engines answer, on the same documents at the same time (docs/v2_ocr_engines.md §4.11).

    uv run --frozen python scripts/ocr_online_speed.py [--rounds 2] [--docs a,b,…]

Each document's image-only PDF (made by the comparison run under ``eval_runs/ocr_bench/_inputs``) is read, with no
cache, three ways:

- ``paddle``: the scan engine as ParserX calls it — the whole PDF as one AI Studio job (submit, poll, download the
  JSONL; ``PaddleOCRService._run_job``, the scan engine's settings);
- ``glm-pdf``: GLM-OCR's online API given the whole PDF in one request;
- ``glm-pages``: GLM-OCR's online API, one request per page image, four at a time.

Every request carries files no service has seen: the PDF gets a random title in its metadata and each page PNG a
random text chunk (same pixels), so a service cannot answer from a cache of earlier uploads (AI Studio answered a
3-page job resent unchanged in 0.7 s).  Rounds alternate which engine goes first.  Results:
``eval_runs/ocr_bench/online_speed.json`` and printed.
"""

from __future__ import annotations

import argparse
import base64
import json
import statistics
import sys
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import requests  # noqa: E402

from parserx.config.schema import load_config  # noqa: E402
from parserx.services.ocr import PaddleOCRService  # noqa: E402
from parserx.tool_eval.ocr_engines import GlmOcrApi  # noqa: E402

INPUTS = ROOT / "eval_runs" / "ocr_bench" / "_inputs"
DOCS = ["ocr01", "ocr_scan_jtg3362", "unseen_scan_form01", "paper_chn02", "patent01", "paper01",
        "omni_newspaper_zh_01", "omni_exam_paper_zh_01", "omni_note_zh_01", "omni_book_en_01"]


def fresh_pdf(pdf: Path) -> bytes:
    """The PDF with a random title: same pages, bytes never sent before."""
    import pymupdf

    with pymupdf.open(pdf) as doc:
        doc.set_metadata({"title": uuid.uuid4().hex})
        return doc.tobytes()


def fresh_pages(pdf: Path, into: Path) -> list[Path]:
    """The page PNGs with a random text chunk each, in *into*."""
    from PIL import Image
    from PIL.PngImagePlugin import PngInfo

    out = []
    for image in sorted(pdf.parent.glob("p[0-9][0-9][0-9].png")):
        info = PngInfo()
        info.add_text("nonce", uuid.uuid4().hex)
        target = into / image.name
        with Image.open(image) as im:
            im.save(target, pnginfo=info)
        out.append(target)
    return out


def paddle(service: PaddleOCRService, pdf: Path) -> dict:
    """One job; also how long it waited in the queue (state ``pending``) before it ran."""
    states: dict[str, float] = {}
    service.on_wait = lambda state, waited: states.setdefault(state, waited)  # first time each state is seen
    result = service._run_job(fresh_pdf(pdf), "document.pdf", "application/pdf")
    queued = states.get("running")  # seconds from submission until it was first seen running
    return {"pages": len(result.get("layoutParsingResults", [])), "queued_s": round(queued, 1) if queued else None}


def glm_pdf(glm: GlmOcrApi, pdf: Path) -> dict:
    body = {"model": "glm-ocr", "file": "data:application/pdf;base64," + base64.b64encode(fresh_pdf(pdf)).decode()}
    r = requests.post(glm.URL, json=body, headers={"Authorization": f"Bearer {glm._key}"}, timeout=1800)
    r.raise_for_status()
    answer = r.json()
    return {"pages": len(answer.get("layout_details") or []), "tokens": (answer.get("usage") or {}).get("total_tokens")}


def glm_pages(glm: GlmOcrApi, pdf: Path) -> dict:
    with tempfile.TemporaryDirectory(prefix="ocr-speed-") as tmp:
        images = fresh_pages(pdf, Path(tmp))  # counted in the time, like the PDF re-made for the others
        with ThreadPoolExecutor(max_workers=glm.concurrency) as pool:
            answers = list(pool.map(glm.read, images))
    return {"pages": len(answers), "tokens": sum((a.get("usage") or {}).get("total_tokens") or 0 for a in answers)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--docs", default=",".join(DOCS))
    args = parser.parse_args()
    service = PaddleOCRService(load_config(ROOT / "configs" / "regression.yaml").builders.ocr)
    glm = GlmOcrApi()
    ways = {"paddle": lambda pdf: paddle(service, pdf), "glm-pdf": lambda pdf: glm_pdf(glm, pdf),
            "glm-pages": lambda pdf: glm_pages(glm, pdf)}
    rows = []
    for round_ in range(args.rounds):
        order = list(ways) if round_ % 2 == 0 else list(reversed(ways))
        for doc in args.docs.split(","):
            pdf = INPUTS / doc / "pages.pdf"
            for way in order:
                started = time.monotonic()
                try:
                    info, error = ways[way](pdf), None
                except Exception as exc:  # noqa: BLE001 - a failure is a result here
                    info, error = {}, f"{type(exc).__name__}: {str(exc)[:200]}"
                seconds = round(time.monotonic() - started, 1)
                rows.append({"round": round_ + 1, "doc": doc, "way": way, "seconds": seconds, "error": error,
                             "at": datetime.now().isoformat(timespec="seconds"), **info})
                print(f"r{round_ + 1} {doc:24s} {way:9s} {seconds:7.1f}s {info} {error or ''}", flush=True)
    out = ROOT / "eval_runs" / "ocr_bench" / "online_speed.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\nway        docs  pages  total s  s/page  median s/doc  failures")
    for way in ways:
        ok = [r for r in rows if r["way"] == way and not r["error"]]
        pages = sum(r.get("pages") or 0 for r in ok)
        total = sum(r["seconds"] for r in ok)
        print(f"{way:9s} {len(ok):5d} {pages:6d} {total:8.1f} {total / max(pages, 1):7.2f} "
              f"{statistics.median([r['seconds'] for r in ok]) if ok else 0:12.1f} "
              f"{sum(1 for r in rows if r['way'] == way and r['error']):9d}")


if __name__ == "__main__":
    main()
