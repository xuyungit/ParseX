"""Configuration schema and loader for ParserX."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


# ── Sub-configs ─────────────────────────────────────────────────────────


class PDFProviderConfig(BaseModel):
    engine: str = "pymupdf"


class DOCXProviderConfig(BaseModel):
    engine: str = "docling"


class ProvidersConfig(BaseModel):
    pdf: PDFProviderConfig = Field(default_factory=PDFProviderConfig)
    docx: DOCXProviderConfig = Field(default_factory=DOCXProviderConfig)


class MetadataBuilderConfig(BaseModel):
    heading_font_ratio: float = 1.2
    header_zone_ratio: float = 0.08
    footer_zone_ratio: float = 0.08
    repetition_threshold: float = 0.5


class LayoutBuilderConfig(BaseModel):
    enabled: bool = True
    model: str = "paddleocr-online"


class OCRBuilderConfig(BaseModel):
    engine: str = "paddleocr"
    lang: str = "ch_sim+en"
    endpoint: str | None = None
    token: str | None = None
    selective: bool = True
    force_full_page: bool = False


class BuildersConfig(BaseModel):
    metadata: MetadataBuilderConfig = Field(default_factory=MetadataBuilderConfig)
    layout: LayoutBuilderConfig = Field(default_factory=LayoutBuilderConfig)
    ocr: OCRBuilderConfig = Field(default_factory=OCRBuilderConfig)


class ProcessorToggle(BaseModel):
    enabled: bool = True
    llm_fallback: bool = True


class TableProcessorConfig(ProcessorToggle):
    vlm_fallback: bool = True
    cross_page_merge: bool = True


class ImageProcessorConfig(ProcessorToggle):
    classification: bool = True
    vlm_description: bool = True
    skip_decorative: bool = True


class FormulaProcessorConfig(BaseModel):
    enabled: bool = False
    model: str = "unimernet"


class LineUnwrapConfig(BaseModel):
    enabled: bool = True
    llm_fallback: bool = False


class TextCleanConfig(BaseModel):
    enabled: bool = True
    fix_cjk_spaces: bool = True
    fix_encoding: bool = True


class ReadingOrderConfig(BaseModel):
    enabled: bool = True
    method: str = "geometric"


class ProcessorsConfig(BaseModel):
    header_footer: ProcessorToggle = Field(default_factory=ProcessorToggle)
    chapter: ProcessorToggle = Field(default_factory=ProcessorToggle)
    table: TableProcessorConfig = Field(default_factory=TableProcessorConfig)
    image: ImageProcessorConfig = Field(default_factory=ImageProcessorConfig)
    formula: FormulaProcessorConfig = Field(default_factory=FormulaProcessorConfig)
    line_unwrap: LineUnwrapConfig = Field(default_factory=LineUnwrapConfig)
    text_clean: TextCleanConfig = Field(default_factory=TextCleanConfig)
    reading_order: ReadingOrderConfig = Field(default_factory=ReadingOrderConfig)


class ServiceConfig(BaseModel):
    """Configuration for an AI service (LLM or VLM)."""

    provider: str = "openai"
    endpoint: str = ""
    model: str = ""
    api_key: str = ""
    max_concurrent: int = 6
    timeout: int = 180
    max_retries: int = 3


class ServicesConfig(BaseModel):
    vlm: ServiceConfig = Field(default_factory=ServiceConfig)
    llm: ServiceConfig = Field(default_factory=ServiceConfig)


class VerificationConfig(BaseModel):
    hallucination_detection: bool = True
    completeness_check: bool = True
    structure_validation: bool = True
    hallucination_threshold: float = 0.3


class OutputConfig(BaseModel):
    format: str = "markdown"
    chapter_split: bool = True
    image_dir: str = "images"
    table_format: str = "markdown"


# ── Top-level config ────────────────────────────────────────────────────


class ParserXConfig(BaseModel):
    """Top-level ParserX configuration."""

    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    builders: BuildersConfig = Field(default_factory=BuildersConfig)
    processors: ProcessorsConfig = Field(default_factory=ProcessorsConfig)
    services: ServicesConfig = Field(default_factory=ServicesConfig)
    verification: VerificationConfig = Field(default_factory=VerificationConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)


# ── Loader ──────────────────────────────────────────────────────────────

_ENV_VAR_PATTERN = re.compile(r"\$\{([^}:]+)(?::([^}]*))?\}")


def _resolve_env_vars(value: Any) -> Any:
    """Recursively resolve ${VAR} and ${VAR:default} in string values."""
    if isinstance(value, str):
        def _replacer(m: re.Match) -> str:
            var_name = m.group(1)
            default = m.group(2)
            return os.environ.get(var_name, default if default is not None else "")
        return _ENV_VAR_PATTERN.sub(_replacer, value)
    if isinstance(value, dict):
        return {k: _resolve_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env_vars(item) for item in value]
    return value


def load_config(path: str | Path | None = None) -> ParserXConfig:
    """Load configuration from YAML file with environment variable resolution.

    If no path is given, returns default config.
    """
    if path is None:
        return ParserXConfig()

    path = Path(path)
    if not path.exists():
        return ParserXConfig()

    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    resolved = _resolve_env_vars(raw)
    return ParserXConfig.model_validate(resolved)
