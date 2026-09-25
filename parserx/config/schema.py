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
    heading_max_char_ratio: float = 0.10
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
    model: str = "PaddleOCR-VL-1.6"
    selective: bool = True
    force_full_page: bool = False
    batch: bool = True  # Batch OCR: assemble pages into temp PDF, one API call
    vector_figure_extraction: bool = True  # Detect vector figures via OCR layout
    vector_figure_render_dpi: int = 200    # DPI for rendering vector figure regions
    vector_figure_min_drawings: int = 5    # Min drawing commands to trigger OCR on NATIVE pages
    use_layout_reading_order: bool = True  # Use OCR layout engine for reading order on multi-column native pages


class QualityCheckConfig(BaseModel):
    """Page quality check: deterministic layout analysis + LLM formula detection."""

    enabled: bool = True
    layout_complexity_check: bool = False  # Deterministic: reclassify complex-layout pages for OCR
    pre_filter_short_ratio: float = 0.25
    max_text_chars: int = 2000


class BuildersConfig(BaseModel):
    metadata: MetadataBuilderConfig = Field(default_factory=MetadataBuilderConfig)
    layout: LayoutBuilderConfig = Field(default_factory=LayoutBuilderConfig)
    ocr: OCRBuilderConfig = Field(default_factory=OCRBuilderConfig)
    quality_check: QualityCheckConfig = Field(default_factory=QualityCheckConfig)


class ProcessorToggle(BaseModel):
    enabled: bool = True
    llm_fallback: bool = True


class TableProcessorConfig(ProcessorToggle):
    vlm_fallback: bool = True
    cross_page_merge: bool = True
    vlm_refine_merged_tables: bool = False


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
    vlm_refine_all_ocr: bool = False
    vlm_debug_raw_preview_chars: int = 1200


class FormulaProcessorConfig(BaseModel):
    enabled: bool = True
    model: str = "unimernet"
    vlm_correction: bool = False
    vlm_structured_output_mode: Literal["off", "json_object", "json_schema"] = "json_schema"
    vlm_max_candidates: int = 6
    vlm_candidate_max_chars: int = 1200
    vlm_max_tokens: int = 1200


class CodeBlockConfig(BaseModel):
    enabled: bool = True


class LineUnwrapConfig(BaseModel):
    enabled: bool = True
    llm_fallback: bool = False
    llm_batch_size: int = 30


class TextCleanConfig(BaseModel):
    enabled: bool = True
    fix_cjk_spaces: bool = True
    fix_encoding: bool = True
    normalize_fullwidth: bool = True


class ContentValueConfig(ProcessorToggle):
    llm_fallback: bool = False
    suppress_low_value: bool = True
    low_value_threshold: float = 0.25
    gray_zone_margin: float = 0.1
    max_llm_candidates: int = 12


class ReadingOrderConfig(BaseModel):
    enabled: bool = True
    method: str = "geometric"


class VLMReviewConfig(BaseModel):
    """Page-level VLM review for OCR correction and missing-text recovery."""

    enabled: bool = True
    review_all_pages: bool = False
    min_text_chars_for_skip: int = 500
    render_dpi: int = 200
    max_pages_per_doc: int = 50
    max_tokens: int = 4096
    structured_output_mode: Literal["off", "json_object", "json_schema"] = "json_schema"


class HeaderFooterConfig(ProcessorToggle):
    """Header/footer detection and first-page identity retention."""

    max_retained_identity: int = 2


class ProcessorsConfig(BaseModel):
    header_footer: HeaderFooterConfig = Field(default_factory=HeaderFooterConfig)
    code_block: CodeBlockConfig = Field(default_factory=CodeBlockConfig)
    chapter: ProcessorToggle = Field(default_factory=ProcessorToggle)
    table: TableProcessorConfig = Field(default_factory=TableProcessorConfig)
    image: ImageProcessorConfig = Field(default_factory=ImageProcessorConfig)
    formula: FormulaProcessorConfig = Field(default_factory=FormulaProcessorConfig)
    line_unwrap: LineUnwrapConfig = Field(default_factory=LineUnwrapConfig)
    text_clean: TextCleanConfig = Field(default_factory=TextCleanConfig)
    content_value: ContentValueConfig = Field(default_factory=ContentValueConfig)
    reading_order: ReadingOrderConfig = Field(default_factory=ReadingOrderConfig)
    vlm_review: VLMReviewConfig = Field(default_factory=VLMReviewConfig)


class ServiceConfig(BaseModel):
    """Configuration for an AI service (LLM or VLM)."""

    provider: str = "openai"
    endpoint: str = ""
    model: str = ""
    api_key: str = ""
    api_style: Literal["auto", "responses", "chat"] = "auto"
    extra_body: dict[str, Any] = Field(default_factory=dict)
    # User-Agent sent to the endpoint. Defaults to a non-SDK value because some
    # OpenAI-compatible proxies sit behind a WAF (e.g. Cloudflare) that blocks
    # the stock ``openai-python`` User-Agent with a 403. Set to "" to keep the
    # SDK default.
    user_agent: str = "parserx/0.1"
    # Reasoning-model controls. ``reasoning_effort`` (none/low/medium/high) is
    # forwarded when set; a backend that rejects it is detected from its 400
    # and the parameter is dropped for the rest of the session.  The same
    # applies to ``temperature`` (``send_temperature`` None = auto-detect).
    # ``min_output_tokens`` raises every caller's token budget to at least
    # this value so reasoning tokens cannot starve the visible answer.
    reasoning_effort: str | None = None
    send_temperature: bool | None = None
    min_output_tokens: int = 0
    max_concurrent: int = 6
    timeout: int = 180
    # Longest pause in a streamed answer (Responses API) before the request counts as stalled: a transport
    # failure the gateway retries (guide §8.2), found long before ``timeout``.  A long answer keeps streaming.
    stream_idle_timeout: int = 60


class RetryConfig(BaseModel):
    """Transport retries in the gateway (network, 5xx, 429, OCR queue full); SDKs never retry."""

    max_attempts: int = 3
    backoff_s: float = 2.0
    max_backoff_s: float = 30.0


class PriceConfig(BaseModel):
    """USD per million tokens."""

    input: float
    output: float
    cached_input: float = 0.0


class BudgetConfig(BaseModel):
    """Per-document limits (guide §8.2); None / missing = unlimited."""

    deadline_s: float | None = None
    requests: dict[str, int] = Field(default_factory=dict)  # per service: ocr / vlm / llm
    usd: float | None = None
    # Held per request until its real cost is known, so concurrent requests cannot overrun ``usd``.
    reserve_usd: dict[str, float] = Field(default_factory=lambda: {"vlm": 0.01, "llm": 0.01})


class SchedulingConfig(BaseModel):
    retry: RetryConfig = Field(default_factory=RetryConfig)
    prices: dict[str, PriceConfig] = Field(default_factory=dict)  # by model name
    budget: BudgetConfig = Field(default_factory=BudgetConfig)


class ToolsConfig(BaseModel):
    """Document toolkit settings (guide §5)."""

    # Figure description and table review use the VLM service with their own reasoning effort
    # (guide §10.3: transcription / review ``none``, description ``low``); None keeps the service's.
    describe_reasoning_effort: str | None = "low"
    review_reasoning_effort: str | None = None
    describe_max_tokens: int = 2048
    ask_reasoning_effort: str | None = "low"  # ask_image: the agent's questions about an image (P2-5)
    ask_max_tokens: int = 1024
    review_max_tokens: int = 4096
    read_dpi: int = 150  # default page render resolution for ``read``
    strip_dpi: int = 300  # ``ask_image --rows``: a band of table rows, sharp enough for small digits
    reading_dpi: int = 150  # page renders for the local page reading (guide §9.5, Q56)
    crop_pad_pt: float = 6.0
    scan_batch_pages: int = 100  # pages per scan-engine request (guide §8.2: bounded batches)


class LayoutConfig(BaseModel):
    """Local layout detector (guide §6.2). Phase 1 runs it in shadow: it records, it does not decide."""

    model: str = "pp_doc_layoutv3"
    conf_thresh: float = 0.5
    page_dpi: int = 100  # page renders for detection (A4 ≈ 827 × 1169 px)
    check_tables: bool = True  # a native ruled grid is read as a table only where the detector sees one (Phase 3 D3)


class RoutingConfig(BaseModel):
    """Embedded image routing (guide §6.5). Thresholds are starting points, tuned only on the whole corpus."""

    # Cheap filter: decorative candidates are saved but neither recognised nor shown.
    decorative_short_side: int = 30  # px
    decorative_std: float = 1.0  # pixel standard deviation (blank images)
    decorative_aspect: float = 12.0  # long / short edge (rules, bars)
    # Trivial images (v1's rule since Iter 12): icons and logos small in both area and long edge.
    trivial_max_area: int | None = 12000  # px²; None disables
    trivial_max_long_edge: int = 160  # px
    # Routing by detected areas (t = text-like, f = figure-like, both over the whole image).
    scan_min_t: float = 0.6
    scan_max_f: float = 0.2
    figure_min_f: float = 0.5
    figure_max_t: float = 0.2
    low: float = 0.2  # t and f both below → UNCERTAIN
    low_confidence: float = 0.5  # every detection below → UNCERTAIN


class RuntimeConfig(BaseModel):
    """The v2 fixed-sequence runtime (guide §7, plan P1-10)."""

    describe_figures: bool = True  # describe shown figures (each at most once, within the budget)
    layout_shadow: bool = True  # run the layout detector and image routing (P1-9)
    page_reading: bool = True  # read every PDF page locally and compare it with the output (guide §9.5, Q56)
    workspace_root: str | None = None  # keep each document's workspace here; None = a temporary directory


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


class CacheConfig(BaseModel):
    """Response cache for OCR / VLM / LLM (guide §8.3).

    off: no cache · read_write: replay hits, record misses · read_only:
    offline replay, a miss is an error · refresh: always request and record.
    """

    mode: Literal["off", "read_write", "read_only", "refresh"] = "off"
    dir: str = ".parserx_cache"


class ParserXConfig(BaseModel):
    """Top-level ParserX configuration."""

    # v1: the processor pipeline; v2: workspace + toolkit through the fixed-sequence runtime.
    pipeline: Literal["v1", "v2"] = "v1"

    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    builders: BuildersConfig = Field(default_factory=BuildersConfig)
    processors: ProcessorsConfig = Field(default_factory=ProcessorsConfig)
    services: ServicesConfig = Field(default_factory=ServicesConfig)
    verification: VerificationConfig = Field(default_factory=VerificationConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)
    scheduling: SchedulingConfig = Field(default_factory=SchedulingConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    layout: LayoutConfig = Field(default_factory=LayoutConfig)
    routing: RoutingConfig = Field(default_factory=RoutingConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)


# ── Loader ──────────────────────────────────────────────────────────────

_ENV_VAR_PATTERN = re.compile(r"\$\{([^}:]+)(?::([^}]*))?\}")
_DEFAULT_CONFIG_FILENAME = "parserx.yaml"
_EXTENDS_KEY = "extends"
_GLOBAL_CONFIG_DIR = Path.home() / ".config" / "parserx"


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


def load_raw_config(path: str | Path) -> dict[str, Any]:
    """The config file with its ``extends`` chain merged and ``${VAR}`` references left unresolved."""
    return _load_raw_config(Path(path), seen=set())


def load_config(path: str | Path | None = None) -> ParserXConfig:
    """Backward-compatible wrapper for config-only callers."""
    return load_config_with_result(path).config


def load_config_with_result(path: str | Path | None = None) -> ConfigLoadResult:
    """Load configuration from YAML file with environment variable resolution.

    Lookup order for .env:  ./.env → ~/.config/parserx/.env
    Lookup order for config (when no --config given):
      ./parserx.yaml → ~/.config/parserx/config.yaml → built-in defaults
    """
    # Load .env: project-local first, then global
    load_dotenv(override=False)
    global_env = _GLOBAL_CONFIG_DIR / ".env"
    if global_env.exists():
        load_dotenv(global_env, override=False)

    requested_path = Path(path) if path is not None else None
    if path is None:
        # Try project-local, then global config
        default_path = Path.cwd() / _DEFAULT_CONFIG_FILENAME
        global_config = _GLOBAL_CONFIG_DIR / "config.yaml"
        if default_path.exists():
            path = default_path
        elif global_config.exists():
            path = global_config
        else:
            return ConfigLoadResult(
                config=ParserXConfig(),
                resolved_path=None,
                source="defaults",
            )

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
