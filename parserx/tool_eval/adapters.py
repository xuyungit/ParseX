"""External document tools and ParserX, each turned into one Markdown file per document (docs/v2_benchmark_plan.md).

Every adapter writes what it gets into its own document directory, as the tool gave it: the Markdown, the images
the Markdown refers to (at the paths it refers to), and the raw response under ``raw/``.  The runner writes
``output.md`` and ``meta.json`` from the returned ``ToolRun``.  Only image paths are ever touched; the text is
never repaired, so a tool is compared on what it actually delivers.

Settings follow §3 of the plan: each tool's best documented mode, with the options that match what ParserX does
(merging across pages, heading levels, tables that keep merged cells) turned on.  Credentials come from ``.env``.
"""

from __future__ import annotations

import base64
import io
import json
import mimetypes
import os
import shutil
import subprocess
import tempfile
import time
import zipfile
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import requests
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
POLL_TIMEOUT = 1800  # s: one document, queueing included


@dataclass
class ToolRun:
    """What one tool produced for one document."""

    markdown: str
    config: dict[str, Any] = field(default_factory=dict)
    cost_usd: float | None = None  # None: free, or not reported
    cost_note: str = ""  # the tool's own unit, e.g. "10 credits"
    notes: list[str] = field(default_factory=list)  # how the input or output was handled, for the report


class ToolAdapter(ABC):
    name: str  # directory name and source id, e.g. "mineru-vlm"
    label: str  # shown in the report and the comparison page

    @abstractmethod
    def parse(self, input_path: Path, out_dir: Path) -> ToolRun:
        """Parse *input_path*; write images and ``raw/`` into *out_dir* (which exists and is empty)."""


def _env(name: str) -> str:
    load_dotenv(REPO_ROOT / ".env", override=False)
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is not set (.env)")
    return value


def _poll(check: Callable[[], dict | None], *, interval: float = 4.0, timeout: float = POLL_TIMEOUT) -> dict:
    """Call *check* until it returns a result; it raises on failure."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = check()
        if result is not None:
            return result
        time.sleep(interval)
    raise TimeoutError(f"no result after {timeout:.0f}s")


def _write(path: Path, data: bytes | str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        path.write_text(data, encoding="utf-8")
    else:
        path.write_bytes(data)


def _safe_relpath(name: str) -> Path:
    """A path from a response, kept inside the document directory."""
    parts = [p for p in Path(name).parts if p not in ("", ".", "..", "/")]
    if not parts:
        raise ValueError(f"unusable file name in response: {name!r}")
    return Path(*parts)


def page_count(path: Path) -> int | None:
    """Pages of a PDF; None for other inputs."""
    if path.suffix.lower() != ".pdf":
        return None
    import pymupdf

    with pymupdf.open(path) as doc:
        return doc.page_count


def docx_to_pdf(path: Path, out_dir: Path) -> Path:
    """A Word document as PDF, by LibreOffice with the system fonts (Chinese text is drawn)."""
    from parserx.content.vector import soffice_env

    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{path.stem}.pdf"
    if target.exists():
        return target
    with tempfile.TemporaryDirectory(prefix="parserx-bench-") as tmp:
        root = Path(tmp)
        result = subprocess.run(
            ["soffice", f"-env:UserInstallation={(root / 'profile').as_uri()}", "--headless",
             "--convert-to", "pdf", "--outdir", str(out_dir), str(path)],
            capture_output=True, text=True, timeout=300, env=soffice_env(root / "fontconfig"),
        )
    if result.returncode != 0 or not target.exists():
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"LibreOffice could not convert {path.name} to PDF: {detail or result.returncode}")
    return target


# ── LlamaParse ──────────────────────────────────────────────────────────


class LlamaParseAdapter(ToolAdapter):
    """LlamaParse Parse API v2 (REST): upload with configuration, poll the job, fetch the full Markdown."""

    base = "https://api.cloud.llamaindex.ai/api/v2/parse"
    usd_per_credit = 0.00125

    def __init__(self, tier: str = "agentic"):
        self.tier = tier
        self.name = f"llamaparse-{tier}"
        self.label = f"LlamaParse {tier}"

    def configuration(self) -> dict:
        return {
            "tier": self.tier,
            "version": "latest",
            "output_options": {
                "markdown": {"tables": {"output_tables_as_markdown": False, "merge_continued_tables": True}},
                "images_to_save": ["embedded"],
            },
        }

    def parse(self, input_path: Path, out_dir: Path) -> ToolRun:
        headers = {"Authorization": f"Bearer {_env('LLAMA_CLOUD_API_KEY')}"}
        config = self.configuration()
        with open(input_path, "rb") as f:
            r = requests.post(f"{self.base}/upload", headers=headers, files={"file": (input_path.name, f)},
                              data={"configuration": json.dumps(config)}, timeout=300)
        if r.status_code >= 300:
            raise RuntimeError(f"LlamaParse upload failed: HTTP {r.status_code} {r.text[:300]}")
        job_id = r.json()["id"]

        def check() -> dict | None:
            job = requests.get(f"{self.base}/{job_id}", headers=headers, timeout=60).json().get("job") or {}
            status = str(job.get("status", "")).upper()
            if status in ("FAILED", "ERROR", "CANCELLED"):
                raise RuntimeError(f"LlamaParse job {status}: {job.get('error_message')}")
            return job if status in ("COMPLETED", "SUCCESS") else None

        _poll(check)
        result = requests.get(f"{self.base}/{job_id}", headers=headers, timeout=300,
                              params={"expand": "markdown_full,images_content_metadata,usage"}).json()
        return read_llamaparse_result(result, out_dir, config=config, usd_per_credit=self.usd_per_credit)


def read_llamaparse_result(result: dict, out_dir: Path, *, config: dict, usd_per_credit: float) -> ToolRun:
    images = ((result.get("images_content_metadata") or {}).get("images")) or []
    for image in images:
        url = image.get("presigned_url")
        if url:
            _write(out_dir / "images" / _safe_relpath(image["filename"]), requests.get(url, timeout=120).content)
    # Presigned URLs carry temporary credentials: not kept.
    kept = json.loads(json.dumps(result))
    for image in ((kept.get("images_content_metadata") or {}).get("images")) or []:
        image.pop("presigned_url", None)
    _write(out_dir / "raw" / "result.json", json.dumps(kept, ensure_ascii=False, indent=2))

    credits = ((result.get("job") or {}).get("usage") or {}).get("credits")
    notes = []
    if images:
        notes.append(f"抽出 {len(images)} 张图片存在 images/，Markdown 里没有引用")
    return ToolRun(
        markdown=result.get("markdown_full") or "",
        config=config,
        cost_usd=round(credits * usd_per_credit, 4) if credits is not None else None,
        cost_note=f"{credits:g} credits" if credits is not None else "",
        notes=notes,
    )


# ── MinerU ──────────────────────────────────────────────────────────────


class MinerUAdapter(ToolAdapter):
    """MinerU online API v4 (mineru.net): request an upload URL, PUT the file, poll the batch, unpack the zip."""

    base = "https://mineru.net/api/v4"

    def __init__(self, model_version: str = "vlm"):
        self.model_version = model_version
        self.name = f"mineru-{model_version}"
        self.label = f"MinerU {model_version}"

    def configuration(self) -> dict:
        return {"model_version": self.model_version, "enable_table": True, "enable_formula": True}

    def parse(self, input_path: Path, out_dir: Path) -> ToolRun:
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {_env('MINER_U_API_KEY')}"}
        config = self.configuration()
        body = {"files": [{"name": input_path.name, "data_id": input_path.parent.name}], **config}
        r = requests.post(f"{self.base}/file-urls/batch", headers=headers, json=body, timeout=60)
        payload = r.json()
        if r.status_code != 200 or payload.get("code") != 0:
            raise RuntimeError(f"MinerU upload URL failed: HTTP {r.status_code} {payload.get('msg')}")
        batch_id = payload["data"]["batch_id"]
        with open(input_path, "rb") as f:
            up = requests.put(payload["data"]["file_urls"][0], data=f, timeout=300)
        if up.status_code != 200:
            raise RuntimeError(f"MinerU upload failed: HTTP {up.status_code} {up.text[:300]}")

        def check() -> dict | None:
            data = requests.get(f"{self.base}/extract-results/batch/{batch_id}", headers=headers, timeout=60).json()
            item = ((data.get("data") or {}).get("extract_result") or [{}])[0]
            if item.get("state") == "failed":
                raise RuntimeError(f"MinerU failed: {item.get('err_msg')}")
            return item if item.get("state") == "done" else None

        item = _poll(check, interval=5)
        _write(out_dir / "raw" / "result.json", json.dumps(item, ensure_ascii=False, indent=2))
        archive = requests.get(item["full_zip_url"], timeout=300).content
        return ToolRun(markdown=read_mineru_zip(archive, out_dir), config=config)


def read_mineru_zip(archive: bytes, out_dir: Path) -> str:
    """``full.md`` and its ``images/`` into *out_dir*, the JSON files into ``raw/``; the copy of the input is left out."""
    markdown = None
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        for info in zf.infolist():
            if info.is_dir() or info.filename.endswith("_origin.pdf"):
                continue
            rel = _safe_relpath(info.filename)
            data = zf.read(info)
            if rel.as_posix() == "full.md":
                markdown = data.decode("utf-8")
            elif rel.parts[0] == "images":
                _write(out_dir / rel, data)
            else:
                _write(out_dir / "raw" / rel, data)
    if markdown is None:
        raise RuntimeError("MinerU result has no full.md")
    return markdown


# ── Datalab (hosted marker) ─────────────────────────────────────────────


class DatalabAdapter(ToolAdapter):
    """Datalab convert API: one multipart request, poll ``request_check_url``."""

    url = "https://www.datalab.to/api/v1/convert"

    def __init__(self, mode: str = "accurate"):
        self.mode = mode
        self.name = f"datalab-{mode}"
        self.label = f"marker/Datalab {mode}"

    def parse(self, input_path: Path, out_dir: Path) -> ToolRun:
        headers = {"X-API-Key": _env("DATALAB_API_KEY")}
        pages = page_count(input_path)
        # Cross-page merging is billed per document (about $0.50): only where there is more than one page.
        config = {"output_format": "markdown", "mode": self.mode, "merge_cross_page": pages is None or pages > 1}
        form = {k: str(v).lower() if isinstance(v, bool) else v for k, v in config.items()}
        with open(input_path, "rb") as f:
            media = mimetypes.guess_type(input_path.name)[0] or "application/octet-stream"  # the service checks it
            r = requests.post(self.url, headers=headers, files={"file": (input_path.name, f, media)}, data=form,
                              timeout=300)
        submitted = r.json() if r.status_code == 200 else {}
        if r.status_code != 200 or not submitted.get("success", True):
            raise RuntimeError(f"Datalab submit failed: HTTP {r.status_code} {r.text[:300]}")
        check_url = submitted["request_check_url"]

        def check() -> dict | None:
            result = requests.get(check_url, headers=headers, timeout=120).json()
            return result if result.get("status") == "complete" else None

        return read_datalab_result(_poll(check, interval=3), out_dir, config=config)


def read_datalab_result(result: dict, out_dir: Path, *, config: dict) -> ToolRun:
    if not result.get("success"):
        raise RuntimeError(f"Datalab conversion failed: {result.get('error')}")
    for name, data in (result.get("images") or {}).items():
        _write(out_dir / _safe_relpath(name), base64.b64decode(data))
    kept = {k: v for k, v in result.items() if k not in ("markdown", "images", "html", "json", "chunks")}
    _write(out_dir / "raw" / "result.json", json.dumps(kept, ensure_ascii=False, indent=2))
    cents = (result.get("cost_breakdown") or {}).get("final_cost_cents")
    failed = (result.get("metadata") or {}).get("failed_pages") or []
    return ToolRun(
        markdown=result.get("markdown") or "",
        config=config,
        cost_usd=round(cents / 100, 4) if cents is not None else None,
        cost_note=f"{cents:g} cents" if cents is not None else "",
        notes=[f"失败的页：{failed}"] if failed else [],
    )


# ── PaddleOCR-VL (AI Studio) ────────────────────────────────────────────


class PaddleOCRVLAdapter(ToolAdapter):
    """PaddleOCR-VL through the AI Studio jobs API with its own page restructuring (tables merged, titles re-levelled).

    The service restructures within each slice of pages it returns (one JSONL line), not across slices; the page
    Markdown is joined in order as delivered (Q131).  Word documents go in as PDF (LibreOffice)."""

    name = "paddleocr-vl"
    label = "PaddleOCR-VL"

    def configuration(self) -> dict:
        from parserx.services.ocr import _OPTIONS  # the scan engine's own settings, plus restructuring

        return {**_OPTIONS, "restructurePages": True, "mergeTables": True, "relevelTitles": True}

    def parse(self, input_path: Path, out_dir: Path) -> ToolRun:
        endpoint = _env("PADDLE_OCR_ENDPOINT").rstrip("/")
        model = os.environ.get("PADDLE_OCR_MODEL", "").strip() or "PaddleOCR-VL-1.6"
        headers = {"Authorization": f"bearer {_env('PADDLE_OCR_TOKEN')}"}
        notes = []
        if input_path.suffix.lower() in (".docx", ".doc"):
            input_path = docx_to_pdf(input_path, out_dir / "raw")
            notes.append("Word 文档先由 LibreOffice 转成 PDF（该服务只收 PDF 和图片）")
        config = {"model": model, "optionalPayload": self.configuration()}

        def submit() -> dict | None:
            with open(input_path, "rb") as f:
                r = requests.post(endpoint, headers=headers, files={"file": (input_path.name, f)},
                                  data={"model": model, "optionalPayload": json.dumps(config["optionalPayload"])},
                                  timeout=300)
            body = r.json() if "json" in r.headers.get("content-type", "") else {}
            if r.status_code == 400 and body.get("code") == 10010:  # queue full: back-pressure, wait
                return None
            if r.status_code != 200 or body.get("code") != 0:
                raise RuntimeError(f"PaddleOCR-VL submit failed: HTTP {r.status_code} {r.text[:300]}")
            return body["data"]

        job_id = _poll(submit, interval=10)["jobId"]

        def check() -> dict | None:
            data = requests.get(f"{endpoint}/{job_id}", headers=headers, timeout=60).json().get("data") or {}
            if data.get("state") == "failed":
                raise RuntimeError(f"PaddleOCR-VL job failed: {data.get('errorMsg') or data}")
            return data if data.get("state") == "done" else None

        data = _poll(check)
        jsonl = requests.get(data["resultUrl"]["jsonUrl"], timeout=300).text
        _write(out_dir / "raw" / "result.jsonl", jsonl)
        markdown, images = read_paddle_jsonl(jsonl)
        for rel, url in images.items():
            _write(out_dir / _safe_relpath(rel), requests.get(url, timeout=120).content)
        return ToolRun(markdown=markdown, config=config, notes=notes)


def read_paddle_jsonl(text: str) -> tuple[str, dict[str, str]]:
    """The pages' Markdown joined in order, and the images it refers to (path → URL)."""
    pages: list[str] = []
    images: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("errorCode") not in (None, 0, "0"):
            raise RuntimeError(f"PaddleOCR-VL result error: {record.get('errorMsg') or record}")
        for page in (record.get("result") or {}).get("layoutParsingResults", []):
            md = page.get("markdown") or {}
            if (md.get("text") or "").strip():
                pages.append(md["text"].strip())
            images.update(md.get("images") or {})
    return "\n\n".join(pages) + "\n", images


# ── ParserX ─────────────────────────────────────────────────────────────


class ParserXFixedAdapter(ToolAdapter):
    """ParserX's fixed pipeline as a frozen run recorded it: the frozen Markdown, with its images.

    A frozen run keeps the Markdown but not the images, and the code may have moved on since (its cache then no
    longer answers every request).  The images come from exporting the document again, offline, from the frozen
    run's cache: they are cut from the source file and named by their content, so they are the ones the frozen
    Markdown refers to even where a figure description would now differ.  No request is made."""

    name = "parserx-fixed"
    label = "ParserX 固定流水线"

    def __init__(self, frozen_run: Path, config_path: Path = REPO_ROOT / "configs" / "regression.yaml",
                 name: str | None = None):
        self.frozen_run = Path(frozen_run)
        self.config_path = config_path
        if name:  # a second arm from another frozen run (e.g. parserx-fixed-R)
            self.name, self.label = name, f"{self.label}（{self.frozen_run.name}）"

    def parse(self, input_path: Path, out_dir: Path) -> ToolRun:
        import re

        from parserx.config.schema import apply_overrides, load_config_with_result
        from parserx.pipeline import Pipeline

        config = apply_overrides(load_config_with_result(self.config_path).config,
                                 ["cache.mode=read_only", f"cache.dir={self.frozen_run / 'cache'}"])
        with tempfile.TemporaryDirectory(prefix="parserx-bench-") as tmp:
            result = Pipeline(config).parse_result_to_dir(input_path, tmp)
            if (Path(tmp) / "images").is_dir():
                shutil.copytree(Path(tmp) / "images", out_dir / "images")
        notes = ["用时是离线回放的，不代表实际耗时"]
        frozen = self.frozen_run / "outputs" / f"{input_path.parent.name}.md"
        if frozen.exists():
            markdown = frozen.read_text(encoding="utf-8")
        else:
            if result.cache_misses:
                raise RuntimeError(f"not in the frozen run, and its cache lacks {dict(result.cache_misses)}")
            markdown = result.markdown
            notes.append("冻结 run 里没有这一篇：从它的缓存导出")
        missing = [ref for ref in re.findall(r"!\[[^\]]*\]\(([^)\s]+)\)", markdown) if not (out_dir / ref).exists()]
        if missing:
            notes.append(f"Markdown 引用的图片有 {len(missing)} 张没能重新导出")
        return ToolRun(markdown=markdown, config={"frozen_run": self.frozen_run.name, "runtime": "fixed"},
                       notes=notes)


class ParserXHybridAdapter(ToolAdapter):
    """ParserX's product default with the current code: the fixed pipeline, then the agent (Codex by default) for a
    document with open review items.  The summary, the sidecar and the agent's work directory are kept under
    ``raw/`` so a problem can be traced to what the program or the agent did."""

    name = "parserx-hybrid"
    label = "ParserX 混合方案（Agent）"

    def __init__(self, config_path: Path | None = REPO_ROOT / "configs" / "regression.yaml", *, always: bool = False):
        self.config_path = config_path
        self.always = always
        if always:  # every document read through by the agent (Q135), not only those with open items
            self.name, self.label = "parserx-agent", "ParserX Agent 通读"

    def parse(self, input_path: Path, out_dir: Path) -> ToolRun:
        with tempfile.TemporaryDirectory(prefix="parserx-bench-") as tmp:
            produced = Path(tmp)
            cmd = ["uv", "run", "parserx", "parse", str(input_path), "-o", tmp, "--runtime", "hybrid", "-q",
                   "--report", "--sidecar", "--keep-work"]
            if self.config_path:
                cmd += ["-c", str(self.config_path)]
            if self.always:
                cmd += ["--set", "runtime.agent_when=always"]
            done = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, timeout=3600)
            _write(out_dir / "raw" / "console.txt", (done.stdout or "") + (done.stderr or ""))
            if done.returncode != 0:
                raise RuntimeError((done.stderr or done.stdout).strip()[-800:] or f"exit {done.returncode}")
            md_files = sorted(produced.glob("*.md"))
            if len(md_files) != 1:
                raise RuntimeError(f"expected one Markdown file, found {[p.name for p in md_files]}")
            stem = md_files[0].stem
            for name in (f"{stem}.json", f"{stem}.blocks.json"):
                if (produced / name).exists():
                    shutil.copy2(produced / name, out_dir / "raw" / name.replace(stem, "summary", 1))
            if (produced / ".parserx-work").is_dir():
                shutil.copytree(produced / ".parserx-work", out_dir / "raw" / "work")
            if (produced / "images").is_dir():
                shutil.copytree(produced / "images", out_dir / "images")
            summary_path = produced / f"{stem}.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
            processing = summary.get("processing") or {}
            return ToolRun(markdown=md_files[0].read_text(encoding="utf-8"),
                           config={"runtime": "hybrid", "agent_when": "always" if self.always else "open",
                                   "config": str(self.config_path or "personal config")},
                           cost_usd=processing.get("cost_usd"), notes=hybrid_notes(summary))


def hybrid_notes(summary: dict) -> list[str]:
    """What the hybrid runtime did with the document, in the reader's words."""
    processing = summary.get("processing") or {}
    agent = processing.get("agent")
    review = summary.get("review") or {}
    if agent:
        cost = agent.get("usd_at_list_price")
        return [f"交 Agent（{agent.get('engine')} {agent.get('model')}）：待办 {agent.get('review_open_before')} → "
                f"{agent.get('review_open_after')}，改动 {agent.get('changes')} 处，看图补字 {agent.get('added')} 处，"
                f"核对关闭 {agent.get('closed')} 项，工具调用 {agent.get('tool_calls')} 次，用时 {agent.get('wall_s', 0):.0f} 秒"
                + (f"，按标价约 ${cost:.2f}" if cost else "")]
    runtime = processing.get("runtime") or ""
    note = processing.get("runtime_note")
    if runtime.startswith("hybrid:fallback"):
        return [f"没有交给 Agent：{note or runtime}"]
    return [f"没有待办，未交 Agent（待办 {review.get('open', 0)}）"] if runtime else []
