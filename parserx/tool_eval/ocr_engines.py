"""OCR engines compared on the same page images (docs/v2_ocr_engines.md).

Every page of a document becomes one image first: a scanned page at the resolution of its scan, any other page
rendered at ``RENDER_DPI``.  Every engine reads only those images; an engine that takes files gets an image-only PDF
of them, so none can use a text layer.  The images and that PDF are made once per document under
``<results>/_inputs/<document>/`` and shared by all engines.

Engines that run on this machine live in their own environments (``ENGINE_HOME/<engine>/.venv``: their
dependencies — torch, paddle, mlx — never meet ParserX's).  Each runs as a worker started once per comparison run:
``scripts/ocr_engines/<worker>.py`` reads one JSON request per line on stdin (``{"image": path}``) and answers with
one JSON line on stdout (``{"markdown", "blocks", "seconds"}`` or ``{"error"}``), so a model is loaded once, not
once per page.  Each page's answer is kept in ``raw/``; the document's Markdown is the pages' Markdown in order.
"""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from parserx.tool_eval.adapters import REPO_ROOT, ToolAdapter, ToolRun, _write

RENDER_DPI = 200  # pages without a full-page scan
MAX_SCAN_DPI = 300  # a scan is passed at its own resolution, but no finer than this
SCAN_COVER = 0.8  # an image over this share of the page makes it a scanned page
ENGINE_HOME = Path(os.environ.get("PARSERX_OCR_ENGINES", Path.home() / "parserx-exp" / "ocr-engines"))
WORKERS = REPO_ROOT / "scripts" / "ocr_engines"
PAGE_TIMEOUT = 900  # s for one page on a local engine
MLX_SERVER = os.environ.get("PARSERX_MLX_SERVER", "http://127.0.0.1:8111/")  # mlx_vlm.server for MLX models


@dataclass(frozen=True)
class PageImage:
    path: Path
    width_pt: float  # the page's size, for the image-only PDF
    height_pt: float
    dpi: float


def page_images(pdf: Path, out_dir: Path) -> list[PageImage]:
    """One PNG per page of *pdf* in *out_dir* (made once; kept for every engine)."""
    import pymupdf

    out_dir.mkdir(parents=True, exist_ok=True)
    images: list[PageImage] = []
    with pymupdf.open(pdf) as doc:
        for page in doc:
            dpi = scan_dpi(page) or RENDER_DPI
            path = out_dir / f"p{page.number + 1:03d}.png"
            if not path.exists():
                pix = page.get_pixmap(dpi=dpi, alpha=False)
                tmp = path.with_suffix(".tmp")
                tmp.write_bytes(pix.tobytes("png"))
                tmp.replace(path)
            images.append(PageImage(path, page.rect.width, page.rect.height, dpi))
    return images


def scan_dpi(page) -> float | None:
    """The resolution of the image covering *page* (a scan), capped at ``MAX_SCAN_DPI``; None if no image does."""
    import pymupdf

    unrotated = pymupdf.Rect(0, 0, page.cropbox.width, page.cropbox.height)  # image boxes ignore /Rotate
    best = None
    for info in page.get_image_info():
        bbox = pymupdf.Rect(info["bbox"])
        if (bbox & unrotated).get_area() / (unrotated.get_area() or 1.0) < SCAN_COVER:
            continue
        best = max(best or 0.0, info["width"] / (bbox.width / 72))
    return None if best is None else min(round(best), MAX_SCAN_DPI)


def image_pdf(images: list[PageImage], path: Path) -> Path:
    """An image-only PDF of *images*, each on a page of its original size (made once)."""
    if path.exists():
        return path
    import pymupdf

    doc = pymupdf.open()
    for image in images:
        page = doc.new_page(width=image.width_pt, height=image.height_pt)
        page.insert_image(page.rect, filename=str(image.path))
    tmp = path.with_suffix(".tmp")
    doc.save(str(tmp), deflate=True)
    doc.close()
    tmp.replace(path)
    return path


_TABLE = re.compile(r"<table\b.*?</table>", re.S | re.I)
_ESCAPED_NEWLINE = re.compile(r"\\n(?![A-Za-z])")  # "\n" written out, not a LaTeX command such as \nu


_PICTURE_TEXT = re.compile(r"(<div\b[^>]*>\s*<img\b[^>]*>)(.*?)(</div>)", re.S | re.I)
_HTML_IMAGE = re.compile(r"<img\b[^>]*?\bsrc=[\"']([^\"']*)[\"'][^>]*>", re.I)
_DIV = re.compile(r"</?div\b[^>]*>", re.I)


def tidy(markdown: str) -> str:
    """The changes made to every engine's Markdown before it is compared (the answers in ``raw/`` stay as given):

    - a line break written out as the two characters "\n" inside a table cell (PaddleOCR-VL does it, the AI
      Studio service only sometimes) becomes a space, as the service writes it otherwise;
    - text an engine read inside a picture and wrote into the picture's ``<div><img …>…</div>`` (PaddleOCR-VL
      with the scan engine's ``useOcrForImageBlock``: ParserX keeps it as evidence, not as output) is left out —
      the annotations do not transcribe pictures, and the other engines are not asked to;
    - a picture written in HTML (``<img src=…>``) is written as a Markdown picture, and ``<div>`` wrappers go:
      the scorer reads Markdown pictures as pictures but HTML tags as text."""
    markdown = _PICTURE_TEXT.sub(lambda m: m.group(1) + m.group(3), markdown)
    markdown = _DIV.sub("", _HTML_IMAGE.sub(lambda m: f"![]({m.group(1)})", markdown))
    return _TABLE.sub(lambda m: _ESCAPED_NEWLINE.sub(" ", m.group(0)), markdown)


def inputs_dir(out_dir: Path) -> Path:
    """``<results>/_inputs/<document>`` for a tool's document directory ``<results>/<tool>/<document>``."""
    return out_dir.parent.parent / "_inputs" / out_dir.name


# ── Engines that take a file: the image-only PDF ─────────────────────────


class ImagesOnly(ToolAdapter):
    """Another adapter, given the image-only PDF of the document instead of the document."""

    def __init__(self, inner: ToolAdapter, name: str | None = None, label: str | None = None):
        self.inner = inner
        self.name = name or inner.name
        self.label = label or inner.label

    def parse(self, input_path: Path, out_dir: Path) -> ToolRun:
        source = inputs_dir(out_dir)
        pdf = image_pdf(page_images(input_path, source), source / "pages.pdf")
        run = self.inner.parse(pdf, out_dir)
        run.markdown = tidy(run.markdown)
        run.notes.append("输入是逐页图片组成的 PDF（无文字层）")
        return run


# ── Engines that read one page image at a time ──────────────────────────


class PageEngine(ToolAdapter):
    """Reads page images one by one (``read``); the document is the pages' Markdown in order."""

    config: dict
    concurrency = 1  # pages read at once (a service; a local worker reads one at a time)

    def read(self, image: Path) -> dict:
        """``{"markdown", "blocks"?, "seconds"?, "cost_usd"?}`` for one page image."""
        raise NotImplementedError

    def _timed(self, image: Path) -> dict:
        started = time.monotonic()
        answer = self.read(image)
        answer.setdefault("seconds", round(time.monotonic() - started, 2))
        return answer

    def parse(self, input_path: Path, out_dir: Path) -> ToolRun:
        from concurrent.futures import ThreadPoolExecutor

        pages = page_images(input_path, inputs_dir(out_dir))
        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            answers = list(pool.map(self._timed, [p.path for p in pages]))
        parts, seconds, cost, notes = [], [], 0.0, []
        for n, answer in enumerate(answers, 1):
            seconds.append(round(answer["seconds"], 2))
            cost += answer.get("cost_usd") or 0.0
            _write(out_dir / "raw" / f"p{n:03d}.json", json.dumps(answer, ensure_ascii=False, indent=1))
            parts.append(f"<!-- PAGE {n} -->\n\n{(answer.get('markdown') or '').strip()}")
            if not (answer.get("markdown") or "").strip():
                notes.append(f"第 {n} 页没有读出内容")
        _write(out_dir / "raw" / "pages.json", json.dumps(
            {"seconds": seconds, "dpi": [p.dpi for p in pages]}, ensure_ascii=False))
        return ToolRun(markdown=tidy("\n\n".join(parts) + "\n"), config=self.config,
                       cost_usd=round(cost, 5) if cost else None, notes=notes)


class LocalWorker:
    """One long-lived worker process of a local engine (see the module docstring)."""

    def __init__(self, engine: str, worker: str, args: list[str] | None = None):
        if worker.endswith(".py"):  # a script of WORKERS run by the engine's own Python
            self.program = ENGINE_HOME / engine / ".venv" / "bin" / "python"
            self.command = [str(self.program), str(WORKERS / worker), *(args or [])]
        else:  # a program built into the engine's directory (apple_vision.swift)
            self.program = ENGINE_HOME / engine / worker
            self.command = [str(self.program), *(args or [])]
        self.log_path = ENGINE_HOME / engine / "worker.log"
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()

    def ask(self, request: dict) -> dict:
        with self._lock:
            proc = self._start()
            proc.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
            proc.stdin.flush()
            line = _readline(proc, PAGE_TIMEOUT)
            if not line:
                self.close()
                raise RuntimeError(f"worker {self.command[1]} stopped (see {self.log_path})")
            answer = json.loads(line)
            if answer.get("error"):
                raise RuntimeError(f"worker: {answer['error']}")
            return answer

    def _start(self) -> subprocess.Popen:
        if self._proc is not None and self._proc.poll() is None:
            return self._proc
        if not self.program.exists():
            raise RuntimeError(f"engine environment missing: {self.program} (docs/v2_ocr_engines.md §3)")
        log = open(self.log_path, "a", encoding="utf-8")
        self._proc = subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log,
                                      text=True, encoding="utf-8", bufsize=1, cwd=str(ENGINE_HOME))
        return self._proc

    def close(self) -> None:
        if self._proc is not None:
            try:
                self._proc.stdin.close()
                self._proc.wait(timeout=30)
            except Exception:  # noqa: BLE001 - a stuck worker is killed
                self._proc.kill()
            self._proc = None


def _readline(proc: subprocess.Popen, timeout: float) -> str:
    """One line from the worker, or "" if it does not answer within *timeout* (it is then killed)."""
    result: list[str] = []
    reader = threading.Thread(target=lambda: result.append(proc.stdout.readline()), daemon=True)
    reader.start()
    reader.join(timeout)
    if reader.is_alive():
        proc.kill()
        return ""
    return result[0] if result else ""


class LocalEngine(PageEngine):
    """A page engine served by a ``LocalWorker``."""

    def __init__(self, name: str, label: str, engine: str, worker: str, args: list[str] | None = None,
                 config: dict | None = None):
        self.name, self.label = name, label
        self.worker = LocalWorker(engine, worker, args)
        self.config = {"engine": engine, "worker": worker, "args": args or [], **(config or {})}

    def read(self, image: Path) -> dict:
        return self.worker.ask({"image": str(image.resolve())})  # the worker runs in its own directory


class LocalPdfEngine(ToolAdapter):
    """A local engine given the image-only PDF of the document, rendering its pages itself — the way the scan
    engine's online service is used (``ImagesOnly``), so a local copy of that service is compared like for like."""

    def __init__(self, name: str, label: str, engine: str, worker: str, args: list[str] | None = None,
                 config: dict | None = None):
        self.name, self.label = name, label
        self.worker = LocalWorker(engine, worker, args)
        self.config = {"engine": engine, "worker": worker, "args": args or [], "input": "image-only PDF",
                       **(config or {})}

    def parse(self, input_path: Path, out_dir: Path) -> ToolRun:
        source = inputs_dir(out_dir)
        pdf = image_pdf(page_images(input_path, source), source / "pages.pdf")
        answer = self.worker.ask({"pdf": str(pdf.resolve())})
        pages = answer.get("pages") or []
        each = round(answer.get("seconds", 0) / max(len(pages), 1), 2)
        parts = []
        for n, page in enumerate(pages, 1):
            _write(out_dir / "raw" / f"p{n:03d}.json", json.dumps(page, ensure_ascii=False, indent=1))
            parts.append(f"<!-- PAGE {n} -->\n\n{(page.get('markdown') or '').strip()}")
        _write(out_dir / "raw" / "pages.json", json.dumps({"seconds": [each] * len(pages)}))
        return ToolRun(markdown=tidy("\n\n".join(parts) + "\n"), config=self.config,
                       notes=["输入是逐页图片组成的 PDF（无文字层），由引擎自己渲染"])


def sent_image(image: Path, max_pixels: int) -> Path:
    """The page image for a service with a request size limit: the image itself when it is within *max_pixels*
    and 10 MB, else a JPEG (quality 92) of at most *max_pixels* pixels next to it (made once)."""
    from PIL import Image

    with Image.open(image) as im:
        fits = im.width * im.height <= max_pixels
    if fits and image.stat().st_size <= 10_000_000:
        return image
    target = image.with_name(f"{image.stem}.{max_pixels}.jpg")
    if not target.exists():
        with Image.open(image) as im:
            im = im.convert("RGB")
            scale = (max_pixels / (im.width * im.height)) ** 0.5
            if scale < 1:
                im = im.resize((int(im.width * scale), int(im.height * scale)), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=92)
        tmp = target.with_suffix(".tmp")
        tmp.write_bytes(buf.getvalue())
        tmp.replace(target)
    return target


# ── A vision model transcribing the page (our own service models) ───────

PAGE_PROMPT = (
    "把这一页转写成 Markdown。按阅读顺序逐字照抄页面上的全部正文：标题写成 # 标题（按层级用 #、##、###）；"
    "表格写成 HTML <table>，合并单元格用 rowspan、colspan；数学用 LaTeX，行内用 $…$，独立成行的公式用 $$…$$，"
    "公式编号照抄；代码放进 ``` 代码块。页眉、页脚、页码不抄；插图、照片、图表里的字不抄，图的位置写 <!-- figure -->，"
    "图题照抄。照原件写，不改写、不补全、不纠正原件的错字；看不清的字写〔?〕。只输出 Markdown。")


class VisionModelPage(PageEngine):
    """A ``models`` entry of the configuration reading each page with ``PAGE_PROMPT``."""

    MAX_TOKENS = 32768
    MAX_PIXELS = 2_621_440  # what qwen3.8-flash reads at most (larger images it scales down itself)
    concurrency = 6

    def __init__(self, model: str, effort: str, name: str, label: str):
        from parserx.config.schema import apply_overrides, load_config
        from parserx.scheduling.usage import PriceTable

        self.name, self.label, self.model, self.effort = name, label, model, effort
        overrides = [f"services.vlm.use={model}", f"services.vlm.reasoning_effort={effort}",
                     "services.vlm.timeout=600", "services.vlm.stream_idle_timeout=600"]
        self._config = apply_overrides(load_config(REPO_ROOT / "configs" / "regression.yaml"), overrides)
        self._prices = PriceTable.from_config(dict(self._config.scheduling.prices))
        self.config = {"model": self._config.services.vlm.model, "effort": effort, "prompt": PAGE_PROMPT,
                       "max_tokens": self.MAX_TOKENS}

    def read(self, image: Path) -> dict:
        from parserx.services.llm import OpenAICompatibleService

        service = OpenAICompatibleService(self._config.services.vlm)
        usage: list[tuple[int, int, int]] = []
        service.usage_hook = lambda _m, i, c, o: usage.append((i, c, o))
        started = time.monotonic()
        text = service.describe_image(sent_image(image, self.MAX_PIXELS), PAGE_PROMPT, temperature=0.0,
                                      max_tokens=self.MAX_TOKENS)
        served = self._config.services.vlm.model
        costs = [self._prices.cost(served, input_tokens=i, cached_input_tokens=c, output_tokens=o) for i, c, o in usage]
        return {"markdown": _unfenced(str(text or "")), "seconds": round(time.monotonic() - started, 2),
                "usage": usage, "cost_usd": sum(c for c in costs if c)}


def _unfenced(text: str) -> str:
    """The answer without a ```markdown fence around all of it."""
    stripped = text.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        first = stripped.find("\n")
        if first > 0 and stripped[3:first].strip().lower() in ("", "markdown", "md"):
            return stripped[first + 1:-3].strip()
    return text


# ── A model behind a local OpenAI-compatible server (mlx_vlm.server) ────

DOTS_PROMPT = """Please output the layout information from the PDF image, including each layout element's bbox, its category, and the corresponding text content within the bbox.

1. Bbox format: [x1, y1, x2, y2]

2. Layout Categories: The possible categories are ['Caption', 'Footnote', 'Formula', 'List-item', 'Page-footer', 'Page-header', 'Picture', 'Section-header', 'Table', 'Text', 'Title'].

3. Text Extraction & Formatting Rules:
    - Picture: For the 'Picture' category, the text field should be omitted.
    - Formula: Format its text as LaTeX.
    - Table: Format its text as HTML.
    - All Others (Text, Title, etc.): Format their text as Markdown.

4. Constraints:
    - The output text must be the original text from the image, with no translation.
    - All layout elements must be sorted according to human reading order.

5. Final Output: The entire output must be a single JSON object.
"""  # dots.mocr's prompt_layout_all_en, verbatim (rednote-hilab/dots.mocr dots_mocr/utils/prompts.py)


def dots_markdown(raw: str) -> tuple[str, list]:
    """dots.mocr's layout JSON as Markdown (as its own converter does: no page header or footer, no pictures'
    text); an answer that is not JSON is kept as text."""
    try:
        cells = json.loads(raw)
    except ValueError:
        return raw, []
    if isinstance(cells, dict):
        cells = next((v for v in cells.values() if isinstance(v, list)), [cells])
    parts = []
    for cell in cells:
        kind, text = cell.get("category", ""), (cell.get("text") or "").strip()
        if kind in ("Page-header", "Page-footer", "Picture") or not text:
            continue
        if text.startswith("#"):  # a title already written as Markdown
            parts.append(text)
            continue
        parts.append({"Title": f"# {text}", "Section-header": f"## {text}",
                      "Formula": text if text.startswith("$$") else f"$$\n{text}\n$$"}.get(kind, text))
    return "\n\n".join(parts), cells


class ServerPage(PageEngine):
    """A page image and a prompt sent to an OpenAI-compatible server on this machine; *convert* turns the answer
    into (Markdown, blocks)."""

    def __init__(self, name: str, label: str, url: str, model: str, prompt: str, convert=None,
                 max_tokens: int = 16384, max_pixels: int | None = None):
        self.name, self.label, self.url, self.model, self.prompt = name, label, url.rstrip("/"), model, prompt
        self.convert, self.max_tokens, self.max_pixels = convert, max_tokens, max_pixels
        self.config = {"server": url, "model": model, "prompt": prompt, "max_tokens": max_tokens,
                       "max_pixels": max_pixels}

    def read(self, image: Path) -> dict:
        import base64

        import requests

        sent = sent_image(image, self.max_pixels) if self.max_pixels else image
        media = "image/jpeg" if sent.suffix == ".jpg" else "image/png"
        body = {"model": self.model, "temperature": 0.0, "max_tokens": self.max_tokens, "messages": [{
            "role": "user", "content": [
                {"type": "image_url",
                 "image_url": {"url": f"data:{media};base64,{base64.b64encode(sent.read_bytes()).decode()}"}},
                {"type": "text", "text": self.prompt}]}]}
        started = time.monotonic()
        r = requests.post(f"{self.url}/v1/chat/completions", json=body, timeout=PAGE_TIMEOUT)
        r.raise_for_status()
        answer = r.json()
        raw = answer["choices"][0]["message"]["content"] or ""
        markdown, blocks = self.convert(raw) if self.convert else (raw, [])
        return {"markdown": markdown, "blocks": blocks, "raw": raw, "usage": answer.get("usage"),
                "finish": answer["choices"][0].get("finish_reason"), "seconds": round(time.monotonic() - started, 2)}


# ── GLM-OCR's online API (Zhipu) ─────────────────────────────────────────


class GlmOcrApi(PageEngine):
    """GLM-OCR through Zhipu's layout-parsing API (``open.bigmodel.cn``, model ``glm-ocr``): each page image as a
    data URI; the answer's ``md_results`` is the Markdown, ``layout_details`` the regions with their boxes.  The key
    is the Zhipu account's, the one the ``glm-5.3-flashx`` models entry uses (personal config).  Price: ¥0.2 per
    million tokens (2026-10-03), estimated from the usage each answer reports."""

    URL = "https://open.bigmodel.cn/api/paas/v4/layout_parsing"
    CNY_PER_MILLION_TOKENS = 0.2
    MAX_BYTES = 10_000_000  # the API's limit for an image
    concurrency = 4

    def __init__(self, account: str = "glm-5.3-flashx"):
        from parserx.config.schema import load_config

        self.name, self.label = "glm-ocr-api", "GLM-OCR 在线 API（智谱）"
        self._key = load_config(REPO_ROOT / "configs" / "regression.yaml").models[account].api_key
        if not self._key:
            raise RuntimeError(f"no key for the Zhipu account: models.{account}.api_key in the personal config")
        self.config = {"url": self.URL, "model": "glm-ocr", "account": f"models.{account}"}

    def read(self, image: Path) -> dict:
        import base64

        import requests

        sent = image if image.stat().st_size <= self.MAX_BYTES else sent_image(image, 16_777_216)
        media = "image/jpeg" if sent.suffix == ".jpg" else "image/png"
        body = {"model": "glm-ocr", "file": f"data:{media};base64,{base64.b64encode(sent.read_bytes()).decode()}"}
        headers = {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"}
        started = time.monotonic()
        for attempt in range(4):
            r = requests.post(self.URL, json=body, headers=headers, timeout=PAGE_TIMEOUT)
            if r.status_code not in (429, 500, 502, 503, 504) or attempt == 3:
                break
            time.sleep(5 * (attempt + 1))
        if r.status_code != 200:
            raise RuntimeError(f"GLM-OCR API HTTP {r.status_code}: {r.text[:300]}")
        answer = r.json()
        usage = answer.get("usage") or {}
        tokens = usage.get("total_tokens") or 0
        return {"markdown": answer.get("md_results") or "", "blocks": answer.get("layout_details"), "usage": usage,
                "cost_cny": round(tokens * self.CNY_PER_MILLION_TOKENS / 1e6, 6),
                "seconds": round(time.monotonic() - started, 2)}


# ── ParserX's own local reading (guide §9.5), as a line reader ───────────


class LocalReading(PageEngine):
    """``reading/local.py`` (RapidOCR, the PP-OCR models it ships) on each page image: its lines top to bottom.  The
    page reading only checks other readings for missed text, so what matters is which characters it sees."""

    def __init__(self):
        from parserx.reading.local import LocalReader

        self.name, self.label = "local-reading", "ParserX 本地读数（RapidOCR）"
        self.reader = LocalReader()
        self.config = {"reader": self.reader.version}

    def read(self, image: Path) -> dict:
        started = time.monotonic()
        lines = self.reader.read(image.read_bytes())
        lines.sort(key=lambda line: (line[0][1], line[0][0]))
        return {"markdown": "\n\n".join(text for _, text, _ in lines if text.strip()),
                "blocks": [{"bbox": list(box), "content": text, "confidence": score} for box, text, score in lines],
                "seconds": round(time.monotonic() - started, 2)}


# ── The engines (`parserx dev tool-eval run --tools …`) ─────────────────


def makers() -> dict:
    """Engine name → a function making its adapter."""
    from parserx.tool_eval import adapters

    return {
        "paddle-vl-aistudio": lambda: ImagesOnly(adapters.PaddleOCRVLAdapter(restructure=False),
                                                 "paddle-vl-aistudio", "PaddleOCR-VL-1.6（AI Studio，现用）"),
        "mineru-api": lambda: ImagesOnly(adapters.MinerUAdapter("vlm"), "mineru-api", "MinerU 在线 API（vlm）"),
        "datalab-api": lambda: ImagesOnly(adapters.DatalabAdapter("accurate", merge_pages=False), "datalab-api",
                                          "Datalab 在线 API（accurate，Chandra 2）"),
        "qwen-page": lambda: VisionModelPage("qwen3.8-flash", "low", "qwen-page", "qwen3.8-flash 整页转写（low）"),
        "ppstructure-v6": lambda: LocalEngine("ppstructure-v6", "PP-StructureV3 + PP-OCRv6（本机）", "paddle",
                                              "paddle_local.py", ["--pipeline", "structure"]),
        "glm-ocr-local": lambda: LocalEngine(
            "glm-ocr-local", "GLM-OCR（本机，MLX）", "glmocr", "glmocr_local.py", [],
            {"server": "mlx_vlm.server --model mlx-community/GLM-OCR-bf16 (port 8112)", "sdk": "glmocr 0.1.5",
             "config": "~/parserx-exp/ocr-engines/glmocr/config.yaml"}),
        "mineru-local": lambda: LocalEngine("mineru-local", "MinerU 4.0 standard（本机）", "mineru", "mineru_local.py",
                                            ["--tier", "standard"], {"package": "mineru 4.0.10"}),
        "apple-vision": lambda: LocalEngine("apple-vision", "Apple Vision 文档识别（本机，系统自带）", "apple-vision",
                                            "vision-worker", [], {"request": "RecognizeDocumentsRequest",
                                                                   "languages": ["zh-Hans", "en-US"]}),
        "apple-vision-lines": lambda: LocalEngine("apple-vision-lines", "Apple Vision 逐行识字（本机，系统自带）",
                                                  "apple-vision", "vision-worker", ["--lines"],
                                                  {"request": "RecognizeTextRequest, accurate",
                                                   "languages": ["zh-Hans", "en-US"]}),
        "chandra2-local": lambda: LocalEngine(
            "chandra2-local", "Chandra OCR 2（本机，MLX）", "chandra", "chandra_local.py", [],
            {"server": "mlx_vlm.server --model datalab-to/chandra-ocr-2 (bf16, port 8113)",
             "package": "chandra-ocr 0.2.0, prompt ocr_layout"}),
        "dots-mocr-local": lambda: ServerPage("dots-mocr-local", "dots.mocr（本机，MLX）", "http://127.0.0.1:8114",
                                              "dots-studio/dots.mocr", DOTS_PROMPT, dots_markdown),
        "paddle-vl-local-pdf": lambda: LocalPdfEngine(
            "paddle-vl-local-pdf", "PaddleOCR-VL-1.6（本机，MLX，收 PDF）", "paddle", "paddle_local.py",
            ["--pipeline", "vl", "--vl-backend", "mlx-vlm-server", "--vl-server-url", MLX_SERVER,
             "--vl-api-model", "PaddlePaddle/PaddleOCR-VL-1.6"],
            {"server": "mlx_vlm.server --model PaddlePaddle/PaddleOCR-VL-1.6 --max-num-seqs 1 (bf16)"}),
        "glm-ocr-api": GlmOcrApi,
        "local-reading": LocalReading,
        "ppocr-v6-lines": lambda: LocalEngine("ppocr-v6-lines", "PP-OCRv6 文字行（本机）", "paddle",
                                              "paddle_local.py", ["--pipeline", "ocr"]),
    }
