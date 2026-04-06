"""Configuration schema and loader for ParserX."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import load_dotenv
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
    endpoint: str = ""
    token: str = ""
    model: str = "PaddleOCR-VL-1.5"
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
    vlm_prompt_style: str = "strict_auto"
    vlm_response_format: str = "json"
    vlm_structured_output_mode: Literal["off", "json_object", "json_schema"] = "json_schema"
    vlm_retry_attempts: int = 1
    vlm_max_tokens: int = 8192
    vlm_max_description_chars: int = 1200
    vlm_skip_large_text_overlap_chars: int = 1200
    vlm_correction_mode: bool = True
    vlm_debug_raw_preview_chars: int = 1200


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


class ContentValueConfig(ProcessorToggle):
    llm_fallback: bool = False
    suppress_low_value: bool = True
    low_value_threshold: float = 0.25
    gray_zone_margin: float = 0.1
    max_llm_candidates: int = 12


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
    content_value: ContentValueConfig = Field(default_factory=ContentValueConfig)
    reading_order: ReadingOrderConfig = Field(default_factory=ReadingOrderConfig)


class ServiceConfig(BaseModel):
    """Configuration for an AI service (LLM or VLM)."""

    provider: str = "openai"
    endpoint: str = ""
    model: str = ""
    api_key: str = ""
    api_style: Literal["auto", "responses", "chat"] = "auto"
    extra_body: dict[str, Any] = Field(default_factory=dict)
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
    product_quality_check: bool = True
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
_DEFAULT_CONFIG_FILENAME = "parserx.yaml"
_EXTENDS_KEY = "extends"


@dataclass(frozen=True)
class ConfigLoadResult:
    """Structured config-load result for CLI visibility and debugging."""

    config: ParserXConfig
    resolved_path: Path | None
    source: str
    requested_path: Path | None = None


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


def _deep_merge_dicts(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if (
            key in merged
            and isinstance(merged[key], dict)
            and isinstance(value, dict)
        ):
            merged[key] = _deep_merge_dicts(merged[key], value)
        else:
            merged[key] = value
    return merged


def _load_raw_config(path: Path, seen: set[Path]) -> dict[str, Any]:
    resolved_path = path.resolve()
    if resolved_path in seen:
        raise ValueError(f"Config extends cycle detected at {resolved_path}")

    seen = set(seen)
    seen.add(resolved_path)

    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    if not isinstance(raw, dict):
        raise ValueError(f"Config file must contain a YAML mapping: {path}")

    extends = raw.pop(_EXTENDS_KEY, None)
    if extends is None:
        return raw

    extend_paths = extends if isinstance(extends, list) else [extends]
    merged: dict[str, Any] = {}

    for extend_value in extend_paths:
        base_path = Path(extend_value)
        if not base_path.is_absolute():
            base_path = (path.parent / base_path).resolve()
        if not base_path.exists():
            raise FileNotFoundError(f"Extended config not found: {base_path}")
        merged = _deep_merge_dicts(merged, _load_raw_config(base_path, seen))

    return _deep_merge_dicts(merged, raw)


def load_config(path: str | Path | None = None) -> ParserXConfig:
    """Backward-compatible wrapper for config-only callers."""
    return load_config_with_result(path).config


def load_config_with_result(path: str | Path | None = None) -> ConfigLoadResult:
    """Load configuration from YAML file with environment variable resolution.

    Loads .env file (if present) before resolving ${VAR} placeholders,
    so credentials can be managed via .env for local development.
    If no path is given, returns default config.
    """
    load_dotenv(override=False)

    requested_path = Path(path) if path is not None else None
    if path is None:
        default_path = Path.cwd() / _DEFAULT_CONFIG_FILENAME
        if not default_path.exists():
            return ConfigLoadResult(
                config=ParserXConfig(),
                resolved_path=None,
                source="defaults",
            )
        path = default_path

    path = Path(path)
    if not path.exists():
        return ConfigLoadResult(
            config=ParserXConfig(),
            resolved_path=path,
            source="missing",
            requested_path=requested_path,
        )

    raw = _load_raw_config(path, seen=set())
    resolved = _resolve_env_vars(raw)
    return ConfigLoadResult(
        config=ParserXConfig.model_validate(resolved),
        resolved_path=path,
        source="project" if requested_path is None else "explicit",
        requested_path=requested_path,
    )


def apply_overrides(
    config: ParserXConfig,
    overrides: list[str] | None = None,
) -> ParserXConfig:
    """Apply dotted-path overrides like ``processors.chapter.llm_fallback=false``."""
    if not overrides:
        return config

    data = config.model_dump()
    for override in overrides:
        if "=" not in override:
            raise ValueError(
                f"Invalid override '{override}'. Expected dotted.path=value."
            )

        dotted_path, raw_value = override.split("=", 1)
        parts = [part.strip() for part in dotted_path.split(".") if part.strip()]
        if not parts:
            raise ValueError(f"Invalid override path '{dotted_path}'.")

        current: dict[str, Any] = data
        for part in parts[:-1]:
            next_value = current.get(part)
            if not isinstance(next_value, dict):
                raise ValueError(f"Unknown config path '{dotted_path}'.")
            current = next_value

        leaf = parts[-1]
        if leaf not in current:
            raise ValueError(f"Unknown config path '{dotted_path}'.")

        current[leaf] = yaml.safe_load(raw_value)

    return ParserXConfig.model_validate(data)
