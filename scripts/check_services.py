#!/usr/bin/env python3
"""Smoke-check the external services ParserX depends on: OCR, LLM, VLM.

Usage:
    uv run python scripts/check_services.py [--config PATH] [--skip ocr,llm,vlm]

Each check makes one small real call with the resolved configuration and
prints OK / FAIL with latency.  Exit code is 1 if any check fails.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from parserx.config.schema import load_config  # noqa: E402


def _sample_image(path: Path) -> Path:
    img = Image.new("RGB", (640, 200), "white")
    draw = ImageDraw.Draw(img)
    draw.text((20, 40), "PARSERX SERVICE CHECK", fill="black")
    draw.text((20, 100), "Line two: 12345 ABCDE", fill="black")
    img.save(path)
    return path


def _run(name: str, fn) -> bool:
    t0 = time.monotonic()
    try:
        detail = fn()
        print(f"{name:<4} OK    {time.monotonic() - t0:6.1f}s  {detail}")
        return True
    except Exception as exc:  # noqa: BLE001 - report anything
        print(f"{name:<4} FAIL  {time.monotonic() - t0:6.1f}s  {type(exc).__name__}: {str(exc)[:160]}")
        return False


def _usage(meter, service: str) -> str:
    """Token usage must be reported; without it cost accounting is blind."""
    tokens = meter.snapshot().tokens.get(service)
    if not tokens or not tokens["input"]:
        raise RuntimeError(f"{service} answered but reported no token usage")
    return f"tokens={tokens['input']}/{tokens['output']}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=None, help="Config file (default: auto-detect)")
    parser.add_argument("--skip", default="", help="Comma-separated checks to skip: ocr,llm,vlm")
    args = parser.parse_args()
    skip = {s.strip() for s in args.skip.split(",") if s.strip()}

    cfg = load_config(args.config)
    # Live contract check: real requests (no cache) through the same gateway the pipeline uses.
    from parserx.scheduling import MeteredService, RequestMeter, ServiceGateway

    meter = RequestMeter()
    gateway = ServiceGateway.from_config(meter, None, cfg.scheduling)
    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        img = _sample_image(Path(tmp) / "check.png")

        if "ocr" not in skip:
            oc = cfg.builders.ocr

            def check_ocr():
                if oc.engine == "none" or not oc.endpoint or not oc.token:
                    raise RuntimeError("OCR not configured (engine/endpoint/token)")
                from parserx.services.ocr import PaddleOCRService

                service = PaddleOCRService(oc)
                service.gateway = gateway
                result = service.recognize(img)
                if not result.blocks:
                    raise RuntimeError("no blocks returned")
                return f"model={oc.model} blocks={len(result.blocks)} text={result.blocks[0].text[:30]!r}"

            ok &= _run("ocr", check_ocr)

        if "llm" not in skip:
            lc = cfg.services.llm

            def check_llm():
                if not lc.endpoint or not lc.api_key:
                    raise RuntimeError("LLM not configured (endpoint/api_key)")
                from parserx.services.llm import create_llm_service

                llm = MeteredService(create_llm_service(lc), meter, "llm", gateway=gateway)
                out = llm.complete("Reply with the single word OK.", "ping", temperature=0.0, max_tokens=16)
                if not out.strip():
                    raise RuntimeError("empty response")
                return f"model={lc.model} endpoint={lc.endpoint} -> {out.strip()[:20]!r} {_usage(meter, 'llm')}"

            ok &= _run("llm", check_llm)

        if "vlm" not in skip:
            vc = cfg.services.vlm

            def check_vlm():
                if not vc.endpoint or not vc.api_key:
                    raise RuntimeError("VLM not configured (endpoint/api_key)")
                from parserx.services.llm import create_vlm_service

                vlm = MeteredService(create_vlm_service(vc), meter, "vlm", gateway=gateway)
                out = vlm.describe_image(img, "Transcribe the text in this image.", temperature=0.0, max_tokens=64)
                if not out.strip():
                    raise RuntimeError("empty response")
                return f"model={vc.model} endpoint={vc.endpoint} -> {out.strip()[:40]!r} {_usage(meter, 'vlm')}"

            ok &= _run("vlm", check_vlm)

    snap = meter.snapshot()
    cost = "unpriced" if snap.cost_usd is None else f"${snap.cost_usd:.6f}"
    print(f"requests {snap.requests} attempts {snap.attempts} cost {cost}")
    print("all checks passed" if ok else "some checks FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
