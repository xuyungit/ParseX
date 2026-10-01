"""Configuration schema and loader for ParserX."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator


# ── Sub-configs ─────────────────────────────────────────────────────────


class OCRBuilderConfig(BaseModel):
    """The scan engine (PaddleOCR-VL through the AI Studio jobs API, guide §10.2).  It stays under
    ``builders.ocr``, where earlier versions kept it, so existing config files keep working (Q75)."""

    engine: str = "paddleocr"  # "none": no scan engine (``parserx parse --no-ocr``)
    endpoint: str = ""
    token: str = ""
    model: str = "PaddleOCR-VL-1.6"


class BuildersConfig(BaseModel):
    ocr: OCRBuilderConfig = Field(default_factory=OCRBuilderConfig)


EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")  # reasoning efforts, low to high


def effort_for(requested: str | None, accepted: list[str] | None, *, higher: bool = False) -> str | None:
    """The effort to send for *requested* to a model that accepts only *accepted* (Q100 §2.4): itself when accepted
    (or when the model's efforts are not listed, or either is not a known effort), else the nearest accepted one;
    on a tie the lower one — services are economy first (Q40) — or, with *higher*, the higher one — the agent is
    capability first (Q103).  None stays None, and a model that takes no effort (``[]``) is sent none."""
    if requested is None or accepted is None or requested in accepted:
        return requested
    if not accepted:
        return None
    if requested not in EFFORTS:
        return requested
    known = [e for e in accepted if e in EFFORTS]
    if not known:
        return requested
    at = EFFORTS.index(requested)
    return min(known, key=lambda e: (abs(EFFORTS.index(e) - at), -EFFORTS.index(e) if higher else EFFORTS.index(e)))


class ModelProfile(BaseModel):
    """How to talk to one model (Q100): where it is and which parameters it takes.  The services and the agent
    choose one by name (``use``); what the model accepts is written here, by the user, not guessed from errors."""

    endpoint: str = ""
    api_key: str = ""
    model: str = ""
    api_style: Literal["auto", "responses", "chat"] = "auto"
    send_temperature: bool | None = None
    efforts: list[str] | None = None  # the reasoning efforts it accepts; None: whatever is asked is sent; []: none
    structured_output: Literal["json_schema", "json_object", "off"] | None = None  # the strongest it honours
    min_output_tokens: int = 0
    extra_body: dict[str, Any] = Field(default_factory=dict)
    user_agent: str = "parserx/0.1"


class ServiceConfig(BaseModel):
    """Configuration for an AI service (LLM or VLM)."""

    use: str | None = None  # a ``models`` entry: its fields fill the ones not written here (Q100)
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
    # What the model accepts (Q100, from its ``models`` entry): the efforts it takes — a task's effort is sent as
    # the nearest of them — and the strongest structured output it honours — the fallback starts there.
    efforts: list[str] | None = None
    structured_output: Literal["json_schema", "json_object", "off"] | None = None
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


class Reader(BaseModel):
    """A model that reads an image again (``tools/second_reading.py``): a ``models`` entry and its effort."""

    use: str
    reasoning_effort: str | None = None


class ToolsConfig(BaseModel):
    """Document toolkit settings (guide §5)."""

    # Figure description and table review use the VLM service with their own reasoning effort
    # (guide §10.3: transcription / review ``none``, description ``low``); None keeps the service's.
    describe_reasoning_effort: str | None = "low"
    review_reasoning_effort: str | None = None
    describe_max_tokens: int = 300  # a note of one or two sentences (Q121)
    ask_reasoning_effort: str | None = "low"  # ask_image: the agent's questions about an image (P2-5)
    # scanned content with mathematics read again (runtime.second_reading): each reader on its own, all at once; with
    # several, a block is listed only where they all differ from the scan engine on a same character.  Readers whose
    # entry is missing or not configured are left out; none left: the service model alone.  Measured 2026-10-01
    # (docs/v2_pipeline_scripts.md §10.4): luna + DeepSeek caught 8 of 9 engine misreads with 3 false items in 40
    second_readers: list[Reader] = Field(default_factory=lambda: [Reader(use="gpt-6-luna", reasoning_effort="low"),
                                                                   Reader(use="deepseek-flash", reasoning_effort="medium")])
    # a correction of characters read again on the block's image alone (tools/edit.py): it stands only where every
    # reader shows it.  Measured on round 2's corrections (docs §11.4): DeepSeek alone judged 34 of 42 right, with luna
    # 30 (luna's own misreads refuse right corrections), luna alone 24; same fallbacks as ``second_readers``
    recheck_readers: list[Reader] = Field(default_factory=lambda: [Reader(use="deepseek-flash",
                                                                          reasoning_effort="medium")])
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
    # where its ONNX file lives (Q111): model_path names a file placed by hand; otherwise <model_dir>/<model>.onnx,
    # downloaded there on first use from the source rapid-layout publishes (checked against its SHA-256)
    model_path: str | None = None
    model_dir: str = "~/.cache/parserx/models"
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


class AgentConfig(BaseModel):
    """The main agent of the hybrid runtime (Q13, Q40, Q57): chosen apart from the service models."""

    engine: Literal["codex", "loop"] = "codex"  # loop: our own function-calling loop (runtimes/loop.py, Q86)
    use: str | None = None  # the loop: a ``models`` entry for its model, endpoint, key, api, efforts (Q100)
    model: str = "gpt-6-sol"
    effort: str = "medium"  # reasoning effort, always explicit on the command line (Q35)
    # agent: it looks at the source itself; tool: the service VLM answers its questions (Q47).  Round 2 (2026-10-01):
    # the agent looking itself made the other side's final drafts better (156 looks against none; analysis §4)
    vision: Literal["tool", "agent"] = "agent"
    # the loop's model API (Q88): responses (OpenAI) or chat (OpenAI-compatible Chat Completions); endpoint and key
    # default to the service VLM's
    api: Literal["responses", "chat"] = "responses"
    endpoint: str = ""
    api_key: str = ""
    extra_body: dict[str, Any] = Field(default_factory=dict)
    efforts: list[str] | None = None  # the loop: the efforts its model accepts; ``effort`` is sent as the nearest
    cache_markers: bool = False  # chat: mark cache breakpoints (providers that cache only marked prompts, DashScope)
    budget_usd: float | None = None  # the loop: at it, the agent is asked to submit; at 1.2 times it, stopped
    clear_at_tokens: int = 100_000  # the loop: a context this long gets its older tool results cleared
    deadline_min: int = 30  # Q39: documents of up to large_pages pages
    large_pages: int = 100
    large_deadline_min: int = 90


class RuntimeConfig(BaseModel):
    """The v2 runtimes (guide §7): the fixed sequence (plan P1-10) and, for ``parserx parse``, the hybrid (Q13):
    the fixed sequence first, then the agent on documents with open review items or not complete."""

    mode: Literal["hybrid", "fixed"] = "hybrid"  # read by ``parserx parse``; evaluation runs the fixed sequence
    # when the hybrid hands a document to the agent (Q135): open review items or not complete, or always — a document
    # without open items still read through by the agent (what the program does not list, the agent may see)
    agent_when: Literal["open", "always"] = "open"
    agent: AgentConfig = Field(default_factory=AgentConfig)
    describe_figures: bool = True  # describe shown figures (each at most once, within the budget)
    layout_shadow: bool = True  # run the layout detector and image routing (P1-9)
    page_reading: bool = True  # read every PDF page locally and compare it with the output (guide §9.5, Q56)
    formulas: bool = True  # display formulas of native PDF pages read as LaTeX by the scan engine (Q70)
    second_reading: bool = True  # scanned content with mathematics read again by the service model, differences listed
    workspace_root: str | None = None  # keep each document's workspace here; None = a temporary directory


class InputConfig(BaseModel):
    """Inputs given as web addresses (Q119, Q123): downloaded before they are read, within these limits."""

    max_download_mb: int = 200
    download_timeout_s: int = 120


class OutputConfig(BaseModel):
    """What the user gets (Q116, Q120): the Markdown and the images it links, always; the summary (``report``) and the
    block-level sidecar (``sidecar``) on request.  ``lang``: the language of the text ParserX adds to the Markdown
    (figure notes, notes on missing content) and of the console."""

    report: bool = False
    sidecar: bool = False
    lang: Literal["zh", "en"] = "zh"
    # running heads, feet and page numbers (user 2026-09-30): in the page's marker comment (kept, not in the reader's
    # way), as lines of text, or left to the sidecar
    page_furniture: Literal["comment", "text", "omit"] = "comment"


class ServicesConfig(BaseModel):
    vlm: ServiceConfig = Field(default_factory=ServiceConfig)  # the tools' VLM tasks (Q40: economy model)


# ── Top-level config ────────────────────────────────────────────────────


class CacheConfig(BaseModel):
    """Response cache for OCR / VLM / LLM (guide §8.3).

    off: no cache · read_write: replay hits, record misses · read_only:
    offline replay, a miss is an error · refresh: always request and record.
    """

    mode: Literal["off", "read_write", "read_only", "refresh"] = "off"
    dir: str = ".parserx_cache"


class ParserXConfig(BaseModel):
    """Top-level ParserX configuration.  Keys of earlier versions (``pipeline``, ``providers``, ``processors``,
    ``verification``, ``services.llm``, v1's ``output.format`` …) are ignored."""

    models: dict[str, ModelProfile] = Field(default_factory=dict)  # by name, chosen with ``use`` (Q100)
    builders: BuildersConfig = Field(default_factory=BuildersConfig)
    services: ServicesConfig = Field(default_factory=ServicesConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)
    scheduling: SchedulingConfig = Field(default_factory=SchedulingConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    layout: LayoutConfig = Field(default_factory=LayoutConfig)
    routing: RoutingConfig = Field(default_factory=RoutingConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    input: InputConfig = Field(default_factory=InputConfig)

    @model_validator(mode="before")
    @classmethod
    def _expand_uses(cls, data: Any) -> Any:
        return expand_uses(data) if isinstance(data, dict) else data


# Where a model is used, and which of its entry's fields go there under which name (the agent's ``api`` is the
# entry's ``api_style``; the loop has no temperature, output floor or structured output of its own).
_USE_SITES: dict[tuple[str, str], dict[str, str]] = {
    ("services", "vlm"): {f: f for f in ModelProfile.model_fields},
    ("runtime", "agent"): {"endpoint": "endpoint", "api_key": "api_key", "model": "model", "api_style": "api",
                           "extra_body": "extra_body", "efforts": "efforts"},
}


def expand_uses(data: dict[str, Any], *, replace: set[tuple[str, str]] = frozenset()) -> dict[str, Any]:
    """Fill each place that names a model (``use``) from its ``models`` entry; what the place writes itself wins.
    Places in *replace* take every field their entry has, over what they had: the model was changed."""
    models = data.get("models") or {}
    out = dict(data)
    for (section, key), fields in _USE_SITES.items():
        site = (out.get(section) or {}).get(key)
        if not isinstance(site, dict) or not site.get("use"):
            continue
        name = site["use"]
        if name not in models:
            raise ValueError(f"{section}.{key}.use: no model {name!r} in models ({', '.join(models) or 'none'})")
        entry = models[name]
        entry = entry.model_dump(exclude_unset=True) if isinstance(entry, BaseModel) else dict(entry)
        filled = {fields[f]: v for f, v in entry.items() if f in fields}
        if fields.get("api_style") == "api" and filled.get("api") not in ("responses", "chat"):
            filled.pop("api", None)  # the loop needs a definite API; "auto" leaves its own
        own = {k: v for k, v in site.items() if (section, key) not in replace or k not in filled}
        out[section] = {**out[section], key: {**filled, **own}}
    return out


# ── Loader ──────────────────────────────────────────────────────────────

_ENV_VAR_PATTERN = re.compile(r"\$\{([^}:]+)(?::([^}]*))?\}")
_DEFAULT_CONFIG_FILENAME = "parserx.yaml"
_EXTENDS_KEY = "extends"
DEFAULTS_FILE = Path(__file__).with_name("defaults.yaml")  # the built-in layer: production settings, known models


def config_dir() -> Path:
    """The personal config directory: ``$PARSERX_CONFIG_DIR``, else ``~/.config/parserx`` (Q107)."""
    return Path(os.environ.get("PARSERX_CONFIG_DIR") or Path.home() / ".config" / "parserx").expanduser()


def personal_config() -> Path:
    return config_dir() / "config.yaml"


@dataclass(frozen=True)
class ConfigLoadResult:
    """Structured config-load result for CLI visibility and debugging."""

    config: ParserXConfig
    resolved_path: Path | None
    source: str  # defaults: only the built-in layer · files: project / personal layers · explicit · missing
    requested_path: Path | None = None
    layers: tuple[Path, ...] = ()  # the files merged, first to last (the built-in defaults first)


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
    """The configuration, in layers each deep-merged over the one before (Q100, Q107-Q108): the built-in defaults,
    ``./parserx.yaml`` (a project's own settings), the personal ``~/.config/parserx/config.yaml`` (keys, endpoints,
    which model does what), and an explicit file with its ``extends`` chain.  ``${VAR}`` reads the environment;
    no ``.env`` file is read."""
    requested = Path(path) if path is not None else None
    layers = [DEFAULTS_FILE]
    for candidate in (Path.cwd() / _DEFAULT_CONFIG_FILENAME, personal_config()):
        if candidate.is_file() and all(candidate.resolve() != layer.resolve() for layer in layers):
            layers.append(candidate)
    source = "files" if len(layers) > 1 else "defaults"
    if requested is not None:
        if requested.exists():
            layers.append(requested)
            source = "explicit"
        else:
            source = "missing"
    raw: dict[str, Any] = {}
    for layer in layers:
        raw = _deep_merge_dicts(raw, _load_raw_config(layer, seen=set()))
    return ConfigLoadResult(
        config=ParserXConfig.model_validate(_resolve_env_vars(raw)),
        resolved_path=requested if requested is not None else (layers[-1] if len(layers) > 1 else None),
        source=source,
        requested_path=requested,
        layers=tuple(layers),
    )


def apply_overrides(
    config: ParserXConfig,
    overrides: list[str] | None = None,
) -> ParserXConfig:
    """Apply dotted-path overrides like ``processors.chapter.llm_fallback=false``."""
    if not overrides:
        return config

    data = config.model_dump()
    changed: set[tuple[str, str]] = set()
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

        current[leaf] = yaml.safe_load(raw_value) if raw_value != "" else ""  # key= empties a setting
        if leaf == "use" and len(parts) == 3:
            changed.add((parts[0], parts[1]))

    return ParserXConfig.model_validate(expand_uses(data, replace=changed))
