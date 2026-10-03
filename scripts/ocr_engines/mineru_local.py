"""Worker: MinerU 4 on this Mac (docs/v2_ocr_engines.md; protocol in tool_eval/ocr_engines.py).

Runs in ``~/parserx-exp/ocr-engines/mineru/.venv`` (``mineru``; models download on first use).  ``--tier``: flash,
basic, standard (the default, recommended), advanced.  Image analysis (the model describing pictures) is off: every
engine is compared on what it reads, not what it adds.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time

_DATA_URI = re.compile(r"data:image/[a-z]+;base64,[A-Za-z0-9+/=]+")


def main() -> None:
    answers, sys.stdout = sys.stdout, sys.stderr  # stdout carries answers only: libraries print elsewhere
    parser = argparse.ArgumentParser()
    parser.add_argument("--tier", default="standard")
    args = parser.parse_args()
    from mineru import MinerUParser

    mineru = MinerUParser(tier=args.tier, parse_mode="ocr", image_analysis=False)
    print(f"ready: {args}", file=sys.stderr, flush=True)
    for line in sys.stdin:
        if not line.strip():
            continue
        started = time.monotonic()
        try:
            result = mineru.parse(json.loads(line)["image"])
            blocks = json.dumps(result.structured_content(), ensure_ascii=False, default=str)
            answer = {"markdown": _DATA_URI.sub("image", result.markdown()),  # pictures are not compared
                      "blocks": json.loads(_DATA_URI.sub("image", blocks)), "seconds": round(time.monotonic() - started, 2)}
        except Exception as exc:  # noqa: BLE001 - reported to the caller, the worker goes on
            answer = {"error": f"{type(exc).__name__}: {exc}"}
        print(json.dumps(answer, ensure_ascii=False, default=str), file=answers, flush=True)


if __name__ == "__main__":
    main()
