"""Worker: PaddleOCR 3.x pipelines on this machine (docs/v2_ocr_engines.md; protocol in tool_eval/ocr_engines.py).

Runs in its own environment (``~/parserx-exp/ocr-engines/paddle/.venv``: paddlepaddle, paddleocr[doc-parser]).

    --pipeline structure  PP-StructureV3 (layout, tables, formulas) with the PP-OCR text models of --ocr-version
    --pipeline ocr        text lines only (PP-OCR det + rec), joined top to bottom
    --pipeline vl         PaddleOCR-VL with the scan engine's settings; the 0.9B recognizer behind --vl-backend
                          (``mlx-vlm-server`` at --vl-server-url, or ``native``)

A request names a page image (``{"image": path}``) or a PDF (``{"pdf": path}``: the pipeline renders its pages
itself, as the AI Studio service does).  mlx_vlm.server must run with ``--max-num-seqs 1``: with its default
continuous batching, answers to the pipeline's concurrent region requests came back wrong now and then (a table
read in the text-spotting format, 2026-10-02).
"""

from __future__ import annotations

import argparse
import json
import sys
import time

# The scan engine's settings (parserx/services/ocr.py _OPTIONS), in the local pipeline's names.
VL_OPTIONS = {"use_doc_orientation_classify": True, "use_doc_unwarping": False, "use_layout_detection": True,
              "use_ocr_for_image_block": True, "use_chart_recognition": False}


def build(args):
    if args.pipeline == "structure":
        from paddleocr import PPStructureV3

        return PPStructureV3(
            text_detection_model_name=f"PP-OCR{args.ocr_version}_det",
            text_recognition_model_name=f"PP-OCR{args.ocr_version}_rec",
            use_doc_orientation_classify=True, use_doc_unwarping=False, use_textline_orientation=False,
            use_seal_recognition=False, use_chart_recognition=False, use_table_recognition=True,
            use_formula_recognition=True, device=args.device)
    if args.pipeline == "ocr":
        from paddleocr import PaddleOCR

        return PaddleOCR(text_detection_model_name=f"PP-OCR{args.ocr_version}_det",
                         text_recognition_model_name=f"PP-OCR{args.ocr_version}_rec",
                         use_doc_orientation_classify=True, use_doc_unwarping=False,
                         use_textline_orientation=False, device=args.device)
    from paddleocr import PaddleOCRVL

    kwargs = {"pipeline_version": args.vl_version, "vl_rec_backend": args.vl_backend, "device": args.device,
              **VL_OPTIONS}  # set at construction too: the orientation model is only loaded then
    if args.vl_server_url:
        kwargs["vl_rec_server_url"] = args.vl_server_url
    if args.vl_api_model:
        kwargs["vl_rec_api_model_name"] = args.vl_api_model
    return PaddleOCRVL(**kwargs)


def read(pipeline, args, request: dict) -> dict:
    """``{"image": path}``: one page; ``{"pdf": path}``: every page of a PDF, rendered by the pipeline itself (as
    the AI Studio service does), as ``{"pages": [...]}``."""
    started = time.monotonic()
    extra = VL_OPTIONS if args.pipeline == "vl" else {}
    if "pdf" in request:
        pages = [page_answer(args, page) for page in pipeline.predict(request["pdf"], **extra)]
        return {"pages": pages, "seconds": round(time.monotonic() - started, 2)}
    answer = page_answer(args, list(pipeline.predict(request["image"], **extra))[0])
    answer["seconds"] = round(time.monotonic() - started, 2)
    return answer


def page_answer(args, page) -> dict:
    data = page.json.get("res", page.json)
    if args.pipeline == "ocr":
        lines = sorted(zip(data.get("rec_polys", []), data.get("rec_texts", [])),
                       key=lambda pt: (min(p[1] for p in pt[0]), min(p[0] for p in pt[0])))
        markdown = "\n\n".join(text for _, text in lines if text.strip())
        blocks = [{"label": "text_line", "bbox": _box(poly), "content": text} for poly, text in lines]
    else:
        markdown = (page.markdown or {}).get("markdown_texts", "")
        blocks = [{"label": b.get("block_label"), "bbox": b.get("block_bbox"), "order": b.get("block_order"),
                   "content": b.get("block_content")} for b in data.get("parsing_res_list", [])]
    return {"markdown": markdown, "blocks": blocks, "size": [data.get("width"), data.get("height")]}


def _box(poly) -> list[float]:
    xs, ys = [float(p[0]) for p in poly], [float(p[1]) for p in poly]
    return [min(xs), min(ys), max(xs), max(ys)]


def main() -> None:
    answers, sys.stdout = sys.stdout, sys.stderr  # stdout carries answers only: libraries print elsewhere
    parser = argparse.ArgumentParser()
    parser.add_argument("--pipeline", choices=["structure", "ocr", "vl"], required=True)
    parser.add_argument("--ocr-version", default="v6_medium", help="PP-OCR text models: v6_medium, v5_server …")
    parser.add_argument("--vl-version", default="v1.6")
    parser.add_argument("--vl-backend", default="mlx-vlm-server")
    parser.add_argument("--vl-server-url", default=None)
    parser.add_argument("--vl-api-model", default=None)
    parser.add_argument("--device", default="cpu")
    args = parser.parse_args()
    pipeline = build(args)
    print(f"ready: {args}", file=sys.stderr, flush=True)
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            answer = read(pipeline, args, json.loads(line))
        except Exception as exc:  # noqa: BLE001 - reported to the caller, the worker goes on
            answer = {"error": f"{type(exc).__name__}: {exc}"}
        print(json.dumps(answer, ensure_ascii=False), file=answers, flush=True)


if __name__ == "__main__":
    main()
