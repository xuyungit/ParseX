"""Report metadata helpers for evaluation and comparison output."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Any

from parserx.config.schema import ConfigLoadResult, ParserXConfig, ServiceConfig, effort_for


ReportMetadata = list[tuple[str, str]]


def build_config_report_metadata(
    config: ParserXConfig,
    *,
    loaded: ConfigLoadResult | None = None,
    overrides: Sequence[str] | None = None,
) -> ReportMetadata:
    """Summarize the resolved runtime config for report headers."""
    from parserx.eval.metrics import METRIC_VERSION

    metadata: ReportMetadata = []
    metadata.append(("Metric version", METRIC_VERSION))
    metadata.append(("Config fingerprint", config_fingerprint(config)))
    metadata.append(("Config source", _format_config_source(loaded)))
    metadata.append(("Overrides", ", ".join(overrides) if overrides else "(none)"))
    metadata.append(("Scan engine", f"{config.builders.ocr.engine} | model={config.builders.ocr.model}"))
    metadata.append(("VLM service", _format_service(config.services.vlm)))
    metadata.append(("Runtime", " | ".join([
        f"describe_figures={_on_off(config.runtime.describe_figures)}",
        f"layout={_on_off(config.runtime.layout_shadow)}",
        f"page_reading={_on_off(config.runtime.page_reading)}",
        f"formulas={_on_off(config.runtime.formulas)}",
    ])))
    return metadata


def append_metadata_section(
    lines: list[str],
    *,
    title: str,
    metadata: ReportMetadata | None,
) -> None:
    """Append a markdown metadata section if metadata is present."""
    if not metadata:
        return

    lines.extend([
        f"## {title}",
        "",
    ])
    for key, value in metadata:
        lines.append(f"- {key}: `{value}`")
    lines.append("")


def _format_config_source(loaded: ConfigLoadResult | None) -> str:
    if loaded is None:
        return "runtime config"
    if loaded.source in {"explicit", "project"} and loaded.resolved_path is not None:
        return str(loaded.resolved_path.resolve())
    if loaded.source == "missing" and loaded.resolved_path is not None:
        return f"built-in defaults (missing: {loaded.resolved_path})"
    return "built-in defaults"


def _format_service(service: ServiceConfig) -> str:
    endpoint = service.endpoint or "(provider default)"
    return " | ".join([
        f"provider={service.provider}",
        f"model={service.model or '(unset)'}",
        f"api_style={service.api_style}",
        f"endpoint={endpoint}",
    ])


def _on_off(value: bool) -> str:
    return "on" if value else "off"


# Credential fields never enter the fingerprint (or any report).
_SECRET_KEYS = frozenset({"api_key", "token"})


def redacted_config(config: ParserXConfig) -> dict[str, Any]:
    """The resolved processing config without credentials or cache settings."""
    return _strip_secrets(config.model_dump(mode="json", exclude={"cache"}))


# Settings that cannot change a replayed output: transport retries and the
# price table only affect how requests are sent and what they cost; the model
# entries and the names that choose them (Q100) are already expanded into the
# places that use them, which is what the processing sees.
_NOT_PROCESSING = {"scheduling": ("retry", "prices"), "layout": ("model_path", "model_dir"),  # where a file lives
                   "output": ("report", "sidecar")}  # which files are handed over, not what they hold
_NAMES = (("services", "vlm"), ("runtime", "agent"))


def config_fingerprint(config: ParserXConfig) -> str:
    """Short hash of ``redacted_config`` minus ``_NOT_PROCESSING``: what the processing actually used.  What a
    model accepts (Q100) counts as what it makes of the settings: each reasoning effort as it is sent, the
    structured output it starts at; a list the model accepts that changes nothing sent changes nothing here."""
    material = redacted_config(config)
    material.pop("models", None)
    for section, key in _NAMES:
        material.get(section, {}).get(key, {}).pop("use", None)
    vlm, tools, agent = material["services"]["vlm"], material["tools"], material["runtime"]["agent"]
    accepted = vlm.pop("efforts", None)
    vlm["reasoning_effort"] = effort_for(vlm["reasoning_effort"], accepted)
    for name in [k for k in tools if k.endswith("_reasoning_effort")]:
        tools[name] = effort_for(tools[name], accepted)
    vlm["structured_output"] = vlm.get("structured_output") or "json_schema"
    agent["effort"] = effort_for(agent["effort"], agent.pop("efforts", None), higher=True)
    for section, keys in _NOT_PROCESSING.items():
        for key in keys:
            material.get(section, {}).pop(key, None)
    blob = json.dumps(material, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def resolved_fingerprint(resolved: dict[str, Any]) -> str:
    """Fingerprint of a frozen run's resolved config, computed as today's ``config_fingerprint`` would.

    Fields added to the config schema after the freeze take their defaults on
    both sides, and fields removed from it are ignored (``pipeline``, the v1
    sections: Phase 5), so an old frozen run stays replayable as long as the
    settings that still exist are unchanged.
    """
    return config_fingerprint(ParserXConfig.model_validate(resolved))


def _strip_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _strip_secrets(v) for k, v in value.items() if k not in _SECRET_KEYS}
    if isinstance(value, list):
        return [_strip_secrets(v) for v in value]
    return value
