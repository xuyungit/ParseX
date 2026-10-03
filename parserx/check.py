"""`parserx check` (release R3): is every role configured and reachable?

- **Scan engine** (required for scanned pages and the text of images): a small real job.
- **Service model** (required): one image request through the same gateway the processing uses.
- **Agent** (recommended): Codex on this machine — installed, its version, logged in; or our own loop — its model has
  a key and answers.
- **LibreOffice** (needed for .doc input; draws the vector images of Word files) and the **layout model** (local).

Each line says what is missing and how to add it.  The exit code is 1 when a required role does not work.
``--model NAME`` probes one entry of ``models`` instead: which parameters the model accepts (Q100 §2.6).
``--offline`` checks the configuration only, without requests.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from parserx.config.schema import ConfigLoadResult, ParserXConfig

_TEXT = {
    "zh": {"title": "ParserX 配置检查", "config": "配置", "defaults": "内置默认", "ocr": "扫描引擎", "vlm": "服务模型",
           "agent": "Agent", "office": "LibreOffice", "layout": "版面模型", "all_ok": "都可以用",
           "required_missing": "必需的服务不可用：{names}", "optional_missing": "可选项未就绪：{names}",
           "no_personal": "没有个人配置：运行 `parserx init`，把 key 写进 {path}",
           "ocr_off": "关闭（builders.ocr.engine: none）：扫描页和图片里的文字不会被识别",
           "ocr_no_token": "没有 token：写进个人配置的 builders.ocr.token",
           "ocr_no_glm_key": "没有 GLM-OCR 的 key：智谱账号的 key 写进个人配置的 models.{entry}.api_key（或 builders.ocr.glm.api_key）",
           "no_key": "{model} 没有 key：写进个人配置的 models.{entry}.api_key",
           "no_model": "没有选模型（services.vlm.use）",
           "agent_off": "不用 Agent（runtime.mode: fixed）：只做标准处理",
           "codex_missing": "没有 Codex CLI：安装后运行 `codex login`；或用 --agent <模型> 改用自己的循环",
           "codex_logged_out": "Codex 未登录：运行 `codex login`",
           "codex_ok": "Codex {version}，已登录（{model} · {effort}）",
           "loop_no_model": "循环没有指定模型（runtime.agent.use）",
           "office_missing": "没有：.doc 文件无法处理，Word 里的矢量图保留原件（可选）",
           "layout_ready": "已就绪（{path}）", "layout_download": "首次运行时下载（约 130 MB，来自 modelscope.cn）",
           "offline": "（未发请求）"},
    "en": {"title": "ParserX check", "config": "Config", "defaults": "built-in defaults", "ocr": "Scan engine",
           "vlm": "Service model", "agent": "Agent", "office": "LibreOffice", "layout": "Layout model",
           "all_ok": "everything works", "required_missing": "required services do not work: {names}",
           "optional_missing": "optional items not ready: {names}",
           "no_personal": "no personal config: run `parserx init` and put the keys in {path}",
           "ocr_off": "off (builders.ocr.engine: none): scanned pages and the text of images are not recognised",
           "ocr_no_token": "no token: put it under builders.ocr.token in the personal config",
           "ocr_no_glm_key": "no GLM-OCR key: put the Zhipu account's key under models.{entry}.api_key in the personal config (or builders.ocr.glm.api_key)",
           "no_key": "{model} has no key: put it under models.{entry}.api_key in the personal config",
           "no_model": "no model chosen (services.vlm.use)",
           "agent_off": "no agent (runtime.mode: fixed): the standard processing only",
           "codex_missing": "no Codex CLI: install it and run `codex login`; or use our own loop with --agent <model>",
           "codex_logged_out": "Codex is not logged in: run `codex login`",
           "codex_ok": "Codex {version}, logged in ({model} · {effort})",
           "loop_no_model": "no model for the loop (runtime.agent.use)",
           "office_missing": "missing: .doc files cannot be read; vector images of Word files stay as they are (optional)",
           "layout_ready": "ready ({path})", "layout_download": "downloaded on first use (about 130 MB, modelscope.cn)",
           "offline": "(no requests)"},
}


@dataclass
class Item:
    name: str
    ok: bool | None  # None: off or not needed
    detail: str
    required: bool
    seconds: float | None = None


def check(config: ParserXConfig, loaded: ConfigLoadResult | None = None, *, lang: str = "zh",
          offline: bool = False) -> list[Item]:
    txt = _TEXT[lang]
    items: list[Item] = []
    with tempfile.TemporaryDirectory() as tmp:
        image = _sample_image(Path(tmp) / "check.png")
        items.append(_timed(txt["ocr"], True, lambda: _ocr(config, image, txt, offline)))
        items.append(_timed(txt["vlm"], True, lambda: _vlm(config, image, txt, offline)))
    items.append(_timed(txt["agent"], False, lambda: _agent(config, txt, offline)))
    items.append(_timed(txt["office"], False, lambda: _office(txt)))
    items.append(_timed(txt["layout"], False, lambda: _layout(config, txt)))
    return items


def report(items: list[Item], loaded: ConfigLoadResult | None, *, lang: str = "zh") -> tuple[str, int]:
    from parserx.config.schema import personal_config

    txt = _TEXT[lang]
    lines = [txt["title"]]
    if loaded is not None:
        layers = [txt["defaults"]] + [str(p) for p in loaded.layers[1:]]
        lines.append(f"{txt['config']}  " + " + ".join(layers))
        if not personal_config().is_file():
            lines.append(f"  ⚠ {txt['no_personal'].format(path=personal_config())}")
    width = max(_width(i.name) for i in items) + 2
    for i in items:
        mark = "✓" if i.ok else ("✗" if i.ok is False and i.required else ("–" if i.ok is None else "⚠"))
        took = f"  {i.seconds:.1f} s" if i.seconds is not None and i.seconds >= 0.05 else ""
        lines.append(f"{mark} {i.name}{' ' * (width - _width(i.name))}{i.detail}{took}")
    failed = [i.name for i in items if i.required and i.ok is False]
    optional = [i.name for i in items if not i.required and i.ok is False]
    if failed:
        lines.append(txt["required_missing"].format(names="、".join(failed) if lang == "zh" else ", ".join(failed)))
    elif optional:
        lines.append(txt["optional_missing"].format(names="、".join(optional) if lang == "zh" else ", ".join(optional)))
    else:
        lines.append(txt["all_ok"])
    return "\n".join(lines), 1 if failed else 0


def _width(text: str) -> int:
    """Columns *text* takes in a terminal: wide (CJK) characters take two."""
    import unicodedata

    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


# ── the roles ───────────────────────────────────────────────────────────


def _timed(name: str, required: bool, fn) -> Item:
    t0 = time.monotonic()
    try:
        ok, detail = fn()
    except Exception as exc:  # noqa: BLE001 - a check reports anything
        ok, detail = False, f"{type(exc).__name__}: {str(exc)[:160]}"
    return Item(name, ok, detail, required, time.monotonic() - t0)


def _host(url: str) -> str:
    from urllib.parse import urlparse

    return urlparse(url).netloc or url


def _ocr(config: ParserXConfig, image: Path, txt: dict, offline: bool):
    oc = config.builders.ocr
    if oc.engine == "none":
        return None, txt["ocr_off"]
    if oc.engine == "glm-ocr":
        from parserx.services.glm_ocr import glm_api_key

        if not glm_api_key(config):
            return False, txt["ocr_no_glm_key"].format(entry=oc.glm.account)
        name = f"{oc.glm.model}（{_host(oc.glm.endpoint)}）"
    else:
        if not oc.token:
            return False, txt["ocr_no_token"]
        name = f"{oc.model}（{_host(oc.endpoint)}）"
    if offline:
        return True, f"{name} {txt['offline']}"
    from parserx.scheduling import RequestMeter, ServiceGateway
    from parserx.services.ocr import new_scan_engine

    service = new_scan_engine(config)
    service.gateway = ServiceGateway.from_config(RequestMeter(), None, config.scheduling)
    result = service.recognize(image)
    if not result.blocks:
        return False, f"{name}: no text returned"
    return True, name


def _vlm(config: ParserXConfig, image: Path, txt: dict, offline: bool):
    vc = config.services.vlm
    entry = vc.use or vc.model
    if not vc.model:
        return False, txt["no_model"]
    if not vc.api_key:
        return False, txt["no_key"].format(model=vc.model, entry=entry)
    name = f"{vc.model}（{_host(vc.endpoint)}）"
    if offline:
        return True, f"{name} {txt['offline']}"
    from parserx.scheduling import MeteredService, RequestMeter, ServiceGateway
    from parserx.services.llm import create_vlm_service

    meter = RequestMeter()
    vlm = MeteredService(create_vlm_service(vc), meter, "vlm",
                         gateway=ServiceGateway.from_config(meter, None, config.scheduling))
    out = vlm.describe_image(image, "Transcribe the text in this image.", temperature=0.0, max_tokens=64)
    if "12345" not in out:
        return False, f"{name}: the answer does not read the sample ({out.strip()[:40]!r})"
    return True, name


def _agent(config: ParserXConfig, txt: dict, offline: bool):
    agent = config.runtime.agent
    if config.runtime.mode == "fixed":
        return None, txt["agent_off"]
    if agent.engine == "codex":
        if shutil.which("codex") is None:
            return False, txt["codex_missing"]
        version = subprocess.run(["codex", "--version"], capture_output=True, text=True, timeout=30).stdout.strip()
        status = subprocess.run(["codex", "login", "status"], capture_output=True, text=True, timeout=60,
                                stdin=subprocess.DEVNULL)
        if status.returncode != 0:
            return False, txt["codex_logged_out"]
        return True, txt["codex_ok"].format(version=version, model=agent.codex_model, effort=agent.effort)
    if not agent.use and not agent.endpoint:
        return False, txt["loop_no_model"]
    if not agent.api_key:
        return False, txt["no_key"].format(model=agent.model, entry=agent.use or agent.model)
    name = f"loop · {agent.model}（{_host(agent.endpoint)}）"
    if offline:
        return True, f"{name} {txt['offline']}"
    from openai import OpenAI

    client = OpenAI(api_key=agent.api_key, base_url=agent.endpoint or None, max_retries=0, timeout=120)
    if agent.api == "chat":
        client.chat.completions.create(model=agent.model, messages=[{"role": "user", "content": "Reply with OK."}],
                                       max_tokens=256)
    else:
        client.responses.create(model=agent.model, input="Reply with OK.", max_output_tokens=256)
    return True, name


def _office(txt: dict):
    if shutil.which("soffice") is None:
        return False, txt["office_missing"]
    try:
        out = subprocess.run(["soffice", "--version"], capture_output=True, text=True, timeout=60).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        out = "soffice"
    return True, " ".join(out.split()[:2]) or "soffice"


def _layout(config: ParserXConfig, txt: dict):
    from parserx.layout.detector import model_file

    path = model_file(config.layout)
    return (True, txt["layout_ready"].format(path=path)) if path.is_file() else (None, txt["layout_download"])


def _sample_image(path: Path) -> Path:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (640, 200), "white")
    draw = ImageDraw.Draw(img)
    draw.text((20, 40), "PARSERX SERVICE CHECK", fill="black")
    draw.text((20, 100), "Line two: 12345 ABCDE", fill="black")
    img.save(path)
    return path


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




def fetch_layout_model(config: ParserXConfig, *, lang: str = "zh", stream=None) -> Path | None:
    """The layout model, downloaded now with a progress line when it is missing (R5); None when it cannot be."""
    import sys

    from parserx.layout.detector import ensure_model, model_file

    stream = stream or sys.stderr
    if model_file(config.layout).is_file():
        return model_file(config.layout)
    label = "下载版面模型" if lang == "zh" else "Downloading the layout model"
    tty = getattr(stream, "isatty", lambda: False)()

    def progress(done: int, total: int | None) -> None:
        if tty:
            share = f" {done * 100 // total}%" if total else ""
            stream.write(f"\r{label} {config.layout.model}：{done >> 20} MB{share}   ")
            stream.flush()

    stream.write(f"{label} {config.layout.model}（{model_file(config.layout)}）…\n" if not tty else "")
    try:
        path = ensure_model(config.layout, progress)
    except Exception as exc:  # noqa: BLE001 - reported, the run goes on without it
        stream.write(("\n" if tty else "") + (f"⚠ 版面模型下载失败：{exc}；可手动下载后写进 layout.model_path\n" if lang == "zh"
                                             else f"⚠ layout model download failed: {exc}; place it by hand and set "
                                                  f"layout.model_path\n"))
        return None
    stream.write("\n" if tty else "")
    return path


def main(argv: list[str] | None = None) -> int:
    import argparse

    from parserx.config.schema import load_config_with_result

    parser = argparse.ArgumentParser(prog="parserx check", description=__doc__.splitlines()[0])
    add_arguments(parser)
    return run(parser.parse_args(argv))


def add_arguments(parser) -> None:
    import os

    parser.add_argument("-c", "--config", type=Path, help="a config file over the built-in, project and personal ones")
    parser.add_argument("--model", help="probe one entry of models: which parameters the model accepts")
    parser.add_argument("--offline", action="store_true", help="check the configuration only, send no requests")
    parser.add_argument("--lang", choices=("zh", "en"), default=os.environ.get("PARSERX_LANG", "zh"))


def run(args) -> int:
    import logging

    from parserx.config.schema import load_config_with_result

    for name in ("httpx", "httpx2", "openai", "urllib3"):  # request logs are not the check's output
        logging.getLogger(name).setLevel(logging.WARNING)

    loaded = load_config_with_result(args.config)
    if args.model:
        return probe_model(loaded.config, args.model)
    text, code = report(check(loaded.config, loaded, lang=args.lang, offline=args.offline), loaded, lang=args.lang)
    print(text)
    return code
