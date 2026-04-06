"""Adapters for comparing ParserX against external document tools."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pdfplumber
from docx import Document as WordDocument
from docx.document import Document as WordDocumentType
from docx.table import Table
from docx.text.paragraph import Paragraph
from dotenv import load_dotenv

from parserx.config.schema import ParserXConfig
from parserx.pipeline import Pipeline

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
NODE_BIN_DIR = REPO_ROOT / "node_modules" / ".bin"
LIT_BIN = NODE_BIN_DIR / "lit"
TSX_BIN = NODE_BIN_DIR / "tsx"
LLAMAPARSE_SCRIPT = REPO_ROOT / "scripts" / "llamaparse_to_markdown.ts"


@dataclass
class ToolParseResult:
    """Structured output from a single tool run."""

    markdown: str
    wall_time_seconds: float
    warnings: list[str] = field(default_factory=list)
    api_calls: dict[str, int] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    extra_files: dict[str, str] = field(default_factory=dict)


class ToolAdapter(ABC):
    """Common interface for document-to-Markdown adapters."""

    name: str

    @abstractmethod
    def parse(self, input_path: Path, artifact_dir: Path) -> ToolParseResult:
        """Parse ``input_path`` and write any sidecar artifacts to ``artifact_dir``."""


class ParserXAdapter(ToolAdapter):
    """Run the in-repo ParserX pipeline."""

    name = "parserx"

    def __init__(self, config: ParserXConfig | None = None):
        self._pipeline = Pipeline(config or ParserXConfig())

    def parse(self, input_path: Path, artifact_dir: Path) -> ToolParseResult:
        start = time.perf_counter()
        result = self._pipeline.parse_result(input_path)
        elapsed = time.perf_counter() - start
        return ToolParseResult(
            markdown=result.markdown,
            wall_time_seconds=round(elapsed, 2),
            warnings=list(result.warnings),
            api_calls=dict(result.api_calls),
            metadata={
                "tool": self.name,
                "page_count": result.page_count,
                "element_count": result.element_count,
                "images_total": result.images_total,
                "images_skipped": result.images_skipped,
                "llm_fallback_hits": result.llm_fallback_hits,
            },
        )


class BuiltinDocPdfAdapter(ToolAdapter):
    """Use local Python doc/pdf skills to convert files into basic Markdown."""

    name = "builtin_doc_pdf"

    def parse(self, input_path: Path, artifact_dir: Path) -> ToolParseResult:
        start = time.perf_counter()
        suffix = input_path.suffix.lower()
        if suffix == ".docx":
            markdown = self._docx_to_markdown(input_path)
            parser_kind = "python-docx"
        elif suffix == ".pdf":
            markdown = self._pdf_to_markdown(input_path)
            parser_kind = "pdfplumber"
        else:
            raise ValueError(f"{self.name} does not support: {suffix}")

        elapsed = time.perf_counter() - start
        return ToolParseResult(
            markdown=markdown,
            wall_time_seconds=round(elapsed, 2),
            metadata={
                "tool": self.name,
                "parser": parser_kind,
                "source_format": suffix.lstrip("."),
            },
        )

    def _docx_to_markdown(self, path: Path) -> str:
        document = WordDocument(str(path))
        blocks: list[str] = []

        for block in _iter_block_items(document):
            if isinstance(block, Paragraph):
                text = _collapse_ws(block.text)
                if not text:
                    continue

                style_name = (block.style.name if block.style else "").strip()
                heading_level = _heading_level_from_style(style_name)
                if heading_level:
                    blocks.append(f"{'#' * heading_level} {text}")
                    continue

                style_lower = style_name.lower()
                if "list bullet" in style_lower:
                    blocks.append(f"- {text}")
                    continue
                if "list number" in style_lower:
                    blocks.append(f"1. {text}")
                    continue

                blocks.append(text)
                continue

            if isinstance(block, Table):
                table_md = _table_to_markdown(_table_rows(block))
                if table_md:
                    blocks.append(table_md)

        return _join_markdown_blocks(blocks)

    def _pdf_to_markdown(self, path: Path) -> str:
        blocks: list[str] = []
        with pdfplumber.open(path) as pdf:
            for page_number, page in enumerate(pdf.pages, start=1):
                page_text = (page.extract_text(layout=True) or "").strip()
                if page_text:
                    blocks.append(f"<!-- PAGE {page_number} -->\n{page_text}")

                for table in page.extract_tables():
                    if not table:
                        continue
                    table_md = _table_to_markdown(table)
                    if table_md:
                        blocks.append(table_md)

        return _join_markdown_blocks(blocks)


class LiteParseAdapter(ToolAdapter):
    """Run LiteParse locally and convert its page text into Markdown."""

    name = "liteparse"

    def __init__(
        self,
        *,
        ocr_language: str | None = None,
        dpi: int = 150,
        max_pages: int = 10000,
    ):
        self._ocr_language = ocr_language
        self._dpi = dpi
        self._max_pages = max_pages

    def parse(self, input_path: Path, artifact_dir: Path) -> ToolParseResult:
        if not LIT_BIN.exists():
            raise RuntimeError(
                "LiteParse CLI not installed. Run `npm install` in the repo root first."
            )

        raw_json_path = artifact_dir / "raw.json"
        cmd = [
            str(LIT_BIN),
            "parse",
            str(input_path),
            "--format",
            "json",
            "-o",
            str(raw_json_path),
            "--dpi",
            str(self._dpi),
            "--max-pages",
            str(self._max_pages),
            "-q",
        ]
        if self._ocr_language:
            cmd.extend(["--ocr-language", self._ocr_language])

        start = time.perf_counter()
        _run_subprocess(cmd, cwd=REPO_ROOT)
        elapsed = time.perf_counter() - start

        raw_text = raw_json_path.read_text(encoding="utf-8")
        payload = json.loads(raw_text)
        pages = payload.get("pages", [])
        markdown_blocks: list[str] = []
        for page in pages:
            page_number = page.get("page")
            text = (page.get("text") or "").strip()
            if not text:
                continue
            markdown_blocks.append(f"<!-- PAGE {page_number} -->\n{text}")

        return ToolParseResult(
            markdown=_join_markdown_blocks(markdown_blocks),
            wall_time_seconds=round(elapsed, 2),
            metadata={
                "tool": self.name,
                "page_count": len(pages),
                "dpi": self._dpi,
                "ocr_language": self._ocr_language or "default",
                "command": cmd,
            },
            extra_files={"raw.json": raw_text},
        )


class LlamaParseAdapter(ToolAdapter):
    """Run LlamaParse through the official TypeScript SDK helper."""

    name = "llamaparse"

    def __init__(
        self,
        *,
        tier: str = "agentic",
        version: str = "latest",
        custom_prompt: str | None = None,
    ):
        self._tier = tier
        self._version = version
        self._custom_prompt = custom_prompt

    def parse(self, input_path: Path, artifact_dir: Path) -> ToolParseResult:
        load_dotenv(override=False)
        if not os.environ.get("LLAMA_CLOUD_API_KEY"):
            raise RuntimeError("LLAMA_CLOUD_API_KEY is not set.")
        if not TSX_BIN.exists():
            raise RuntimeError(
                "tsx is not installed. Run `npm install` in the repo root first."
            )
        if not LLAMAPARSE_SCRIPT.exists():
            raise RuntimeError(f"LlamaParse script not found: {LLAMAPARSE_SCRIPT}")

        output_path = artifact_dir / "output.md"
        metadata_path = artifact_dir / "metadata.generated.json"
        cmd = [
            str(TSX_BIN),
            str(LLAMAPARSE_SCRIPT),
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            "--metadata",
            str(metadata_path),
            "--tier",
            self._tier,
            "--version",
            self._version,
        ]
        if self._custom_prompt:
            cmd.extend(["--custom-prompt", self._custom_prompt])

        start = time.perf_counter()
        _run_subprocess(cmd, cwd=REPO_ROOT)
        elapsed = time.perf_counter() - start

        markdown = output_path.read_text(encoding="utf-8")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["command"] = cmd
        return ToolParseResult(
            markdown=markdown,
            wall_time_seconds=round(elapsed, 2),
            metadata=metadata,
        )


def _run_subprocess(cmd: list[str], *, cwd: Path) -> None:
    log.debug("Running command: %s", " ".join(cmd))
    completed = subprocess.run(
        cmd,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode == 0:
        return

    stderr = completed.stderr.strip()
    stdout = completed.stdout.strip()
    detail = stderr or stdout or f"exit code {completed.returncode}"
    raise RuntimeError(detail)


def _iter_block_items(parent: WordDocumentType):
    parent_element = parent.element.body
    for child in parent_element.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            yield Paragraph(child, parent)
        elif tag == "tbl":
            yield Table(child, parent)


def _heading_level_from_style(style_name: str) -> int | None:
    match = re.search(r"(\d+)$", style_name)
    if match and any(token in style_name.lower() for token in ("heading", "标题")):
        level = int(match.group(1))
        return min(max(level, 1), 6)
    return None


def _table_rows(table: Table) -> list[list[str]]:
    rows: list[list[str]] = []
    for row in table.rows:
        rows.append([_collapse_ws(cell.text) for cell in row.cells])
    return rows


def _table_to_markdown(rows: list[list[str] | tuple[str, ...]]) -> str:
    normalized_rows = [
        [_escape_table_cell(_collapse_ws(str(cell))) for cell in row]
        for row in rows
        if any(_collapse_ws(str(cell)) for cell in row)
    ]
    if not normalized_rows:
        return ""

    column_count = max(len(row) for row in normalized_rows)
    padded_rows = [
        row + [""] * (column_count - len(row))
        for row in normalized_rows
    ]
    header = padded_rows[0]
    lines = [
        "| " + " | ".join(header) + " |",
        "|" + "|".join(["---"] * column_count) + "|",
    ]
    for row in padded_rows[1:]:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _escape_table_cell(text: str) -> str:
    return text.replace("|", "\\|")


def _collapse_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _join_markdown_blocks(blocks: list[str]) -> str:
    content = "\n\n".join(block for block in blocks if block.strip()).strip()
    return f"{content}\n" if content else ""
