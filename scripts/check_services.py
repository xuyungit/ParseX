#!/usr/bin/env python3
"""Smoke-check the external services ParserX depends on: the scan engine (OCR) and the VLM.

Usage:
    uv run python scripts/check_services.py [--config PATH] [--skip ocr,vlm]
    uv run python scripts/check_services.py --model NAME   # one entry of `models` (Q100)

Each check makes one small real call with the resolved configuration and
prints OK / FAIL with latency.  Exit code is 1 if any check fails.

With --model, the entry is probed directly (no fallbacks, no cache): is the
model listed, does it answer text and an image, does it take temperature,
which reasoning efforts does it accept, which structured output does it
honour, does it return its reasoning (reasoning_content).  What the entry
says and what the probe found are compared; a difference fails.
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
    parser.add_argument("--skip", default="", help="Comma-separated checks to skip: ocr,vlm")
    parser.add_argument("--model", help="probe one entry of the config's models (Q100) instead")
    args = parser.parse_args()
    skip = {s.strip() for s in args.skip.split(",") if s.strip()}

    cfg = load_config(args.config)
    if args.model:
        return probe_model(cfg, args.model)
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


# ── --model: what one model accepts (Q100 §2.6) ─────────────────────────

_SCHEMA = {"type": "object", "properties": {"word": {"type": "string"}, "number": {"type": "integer"}},
           "required": ["word", "number"], "additionalProperties": False}
_ASK = 'Reply in JSON with the keys "word" (the word OK) and "number" (the number 7).'


def probe_model(cfg, name: str) -> int:
    import base64
    import json

    from openai import BadRequestError, OpenAI

    from parserx.config.schema import EFFORTS

    if name not in cfg.models:
        print(f"no model {name!r} in models ({', '.join(cfg.models) or 'none'})")
        return 1
    entry = cfg.models[name]
    client = OpenAI(api_key=entry.api_key or "no-key", base_url=entry.endpoint or None, max_retries=0, timeout=120,
                    default_headers={"User-Agent": entry.user_agent} if entry.user_agent else None)
    chat = entry.api_style == "chat"
    print(f"model {name}: {entry.model} at {entry.endpoint} ({'chat' if chat else entry.api_style})")

    def request(content, *, effort=None, temperature=None, fmt=None, max_tokens=256):
        if chat:
            kwargs = {"model": entry.model, "messages": [{"role": "user", "content": content}], "max_tokens": max_tokens,
                      **({"reasoning_effort": effort} if effort else {}), **({"temperature": temperature}
                                                                              if temperature is not None else {})}
            if fmt == "json_schema":
                kwargs["response_format"] = {"type": "json_schema",
                                             "json_schema": {"name": "probe", "schema": _SCHEMA, "strict": True}}
            elif fmt == "json_object":
                kwargs["response_format"] = {"type": "json_object"}
            if entry.extra_body:
                kwargs["extra_body"] = dict(entry.extra_body)
            answer = client.chat.completions.create(**kwargs)
            message = answer.choices[0].message
            return message.content or "", (getattr(message, "model_extra", None) or {}).get("reasoning_content")
        kwargs = {"model": entry.model, "input": [{"role": "user", "content": content}], "max_output_tokens": max_tokens,
                  **({"reasoning": {"effort": effort}} if effort else {}),
                  **({"temperature": temperature} if temperature is not None else {})}
        if fmt == "json_schema":
            kwargs["text"] = {"format": {"type": "json_schema", "name": "probe", "schema": _SCHEMA, "strict": True}}
        elif fmt == "json_object":
            kwargs["text"] = {"format": {"type": "json_object"}}
        if entry.extra_body:
            kwargs["extra_body"] = dict(entry.extra_body)
        answer = client.responses.create(**kwargs)
        return answer.output_text or "", None

    def verdict(fn):
        """True: accepted; False: rejected (400, with the provider's words); None: the check could not be made."""
        try:
            return True, fn()
        except BadRequestError as exc:
            return False, str(exc)[:160]
        except Exception as exc:  # noqa: BLE001 - a transport failure says nothing about the model
            return None, f"{type(exc).__name__}: {str(exc)[:120]}"

    problems: list[str] = []
    try:
        listed = {m.id for m in client.models.list()}
        print(f"  listed        {'yes' if entry.model in listed else 'NO'} ({len(listed)} models)")
        if entry.model not in listed:
            problems.append(f"{entry.model} is not among the endpoint's models")
    except Exception as exc:  # noqa: BLE001 - some endpoints do not list
        print(f"  listed        (the endpoint does not list models: {type(exc).__name__})")

    ok, out = verdict(lambda: request("Reply with the word OK."))
    reasoning = out[1] if ok else None
    print(f"  text          {'OK ' + repr(out[0].strip()[:30]) if ok else 'FAIL ' + str(out)}")
    if not ok:
        problems.append("no text answer")
    with tempfile.TemporaryDirectory() as tmp:
        data = base64.b64encode(_sample_image(Path(tmp) / "check.png").read_bytes()).decode()
    url = f"data:image/png;base64,{data}"
    image = ([{"type": "text", "text": "Transcribe the text in this image."}, {"type": "image_url", "image_url": {"url": url}}]
             if chat else [{"type": "input_text", "text": "Transcribe the text in this image."},
                           {"type": "input_image", "image_url": url}])
    ok, out = verdict(lambda: request(image, max_tokens=512))
    print(f"  image         {'OK ' + repr(out[0].strip()[:40]) if ok else 'FAIL ' + str(out)}")
    if not ok or "12345" not in out[0]:
        problems.append("no image reading")
    print(f"  reasoning     {'returned (reasoning_content)' if reasoning else 'not returned'}")

    ok, out = verdict(lambda: request("Reply with the word OK.", temperature=0.0))
    takes = ok is True
    print(f"  temperature   {'accepted' if takes else 'rejected: ' + str(out) if ok is False else '? ' + str(out)}")
    if ok is not None and entry.send_temperature is False and takes:
        problems.append("temperature is accepted, the entry says send_temperature: false")
    if ok is False and entry.send_temperature is not False:
        problems.append("temperature is rejected: set send_temperature: false")

    accepted, unknown = [], []
    for effort in EFFORTS:
        ok, out = verdict(lambda: request("Reply with the word OK.", effort=effort))
        (accepted if ok else unknown if ok is None else []).append(effort)
        print(f"  effort {effort:<7}{'accepted' if ok else 'rejected: ' + str(out)[:100] if ok is False else '? ' + str(out)}")
    if entry.efforts is None:
        if accepted != list(EFFORTS):
            problems.append(f"efforts not listed, but only {accepted} are accepted: efforts: {accepted}")
    elif sorted(entry.efforts, key=EFFORTS.index) != [e for e in accepted if e not in unknown]:
        problems.append(f"efforts: the entry says {entry.efforts}, the model accepts {accepted}")

    strongest = "off"
    for fmt in ("json_schema", "json_object"):
        ok, out = verdict(lambda: request(_ASK, fmt=fmt, max_tokens=512))
        valid = False
        if ok:
            try:
                valid = json.loads(out[0]).get("word") == "OK"
            except (ValueError, AttributeError):
                valid = False
        print(f"  {fmt:<13} {'honoured' if valid else 'accepted, not honoured: ' + repr(out[0][:60]) if ok else 'rejected: ' + str(out)[:100] if ok is False else '? ' + str(out)}")
        if valid and strongest == "off":
            strongest = fmt
    said = entry.structured_output or "json_schema"
    if said != strongest:
        problems.append(f"structured_output: the entry says {said}, the model honours {strongest}")

    for problem in problems:
        print(f"  MISMATCH  {problem}")
    print("the entry matches the model" if not problems else "the entry does not match the model")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
