"""Worker: GLM-OCR self-hosted (docs/v2_ocr_engines.md; protocol in tool_eval/ocr_engines.py).

Runs in ``~/parserx-exp/ocr-engines/glmocr/.venv`` (``glmocr[layout]``): layout PP-DocLayoutV3 here, each region
recognised by GLM-OCR 0.9B on an ``mlx_vlm.server`` — both set in ``--config`` (that directory's config.yaml).
"""

from __future__ import annotations

import argparse
import json
import sys
import time


def main() -> None:
    answers, sys.stdout = sys.stdout, sys.stderr  # stdout carries answers only: libraries print elsewhere
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="glmocr/config.yaml")
    parser.add_argument("--layout-device", default="cpu")
    args = parser.parse_args()
    from glmocr import GlmOcr

    ocr = GlmOcr(config_path=args.config, mode="selfhosted", layout_device=args.layout_device)
    print(f"ready: {args}", file=sys.stderr, flush=True)
    for line in sys.stdin:
        if not line.strip():
            continue
        started = time.monotonic()
        try:
            result = ocr.parse(json.loads(line)["image"])
            result = result[0] if isinstance(result, list) else result
            data = result.to_dict()
            answer = {"markdown": result.markdown_result or "", "blocks": data.get("json_result") or data,
                      "seconds": round(time.monotonic() - started, 2)}
        except Exception as exc:  # noqa: BLE001 - reported to the caller, the worker goes on
            answer = {"error": f"{type(exc).__name__}: {exc}"}
        print(json.dumps(answer, ensure_ascii=False, default=str), file=answers, flush=True)
    ocr.close()


if __name__ == "__main__":
    main()
