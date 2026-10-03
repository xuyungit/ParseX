"""Worker: Chandra OCR 2 (Datalab) on this Mac (docs/v2_ocr_engines.md; protocol in tool_eval/ocr_engines.py).

Runs in ``~/parserx-exp/ocr-engines/chandra/.venv`` (``chandra-ocr``, without torch): the package's own prompt
(``ocr_layout``), image scaling, retries and output parsing, through its OpenAI-compatible client (meant for vLLM),
here pointed at an ``mlx_vlm.server`` holding ``datalab-to/chandra-ocr-2`` (--server, --model).  Headers and
footers are left out, as the package does by default.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time


def main() -> None:
    answers, sys.stdout = sys.stdout, sys.stderr  # stdout carries answers only: libraries print elsewhere
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", default="http://127.0.0.1:8113/v1")
    parser.add_argument("--model", default="datalab-to/chandra-ocr-2")
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()
    os.environ["VLLM_API_BASE"], os.environ["VLLM_MODEL_NAME"] = args.server, args.model  # read at import
    from PIL import Image

    from chandra.model import InferenceManager
    from chandra.model.schema import BatchInputItem

    manager = InferenceManager(method="vllm")
    print(f"ready: {args}", file=sys.stderr, flush=True)
    for line in sys.stdin:
        if not line.strip():
            continue
        started = time.monotonic()
        try:
            with Image.open(json.loads(line)["image"]) as image:
                item = BatchInputItem(image=image.convert("RGB"), prompt_type="ocr_layout")
                out = manager.generate([item], include_images=False, include_headers_footers=False,
                                       vllm_api_base=args.server, max_workers=args.workers)[0]
            answer = {"markdown": out.markdown, "blocks": out.chunks, "raw": out.raw, "tokens": out.token_count,
                      "failed": out.error, "seconds": round(time.monotonic() - started, 2)}
        except Exception as exc:  # noqa: BLE001 - reported to the caller, the worker goes on
            answer = {"error": f"{type(exc).__name__}: {exc}"}
        print(json.dumps(answer, ensure_ascii=False, default=str), file=answers, flush=True)


if __name__ == "__main__":
    main()
