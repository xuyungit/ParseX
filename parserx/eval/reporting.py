"""Report metadata helpers for evaluation and comparison output."""

from __future__ import annotations

from collections.abc import Sequence

from parserx.config.schema import ConfigLoadResult, ParserXConfig, ServiceConfig


ReportMetadata = list[tuple[str, str]]


def build_config_report_metadata(
    config: ParserXConfig,
    *,
    loaded: ConfigLoadResult | None = None,
    overrides: Sequence[str] | None = None,
) -> ReportMetadata:
    """Summarize the resolved runtime config for report headers."""
    metadata: ReportMetadata = []
    metadata.append(("Config source", _format_config_source(loaded)))
    metadata.append(("Overrides", ", ".join(overrides) if overrides else "(none)"))
    metadata.append(("PDF provider", config.providers.pdf.engine))
    metadata.append(("DOCX provider", config.providers.docx.engine))
    metadata.append((
        "OCR builder",
        f"{config.builders.ocr.engine} | model={config.builders.ocr.model} | lang={config.builders.ocr.lang}",
    ))
    metadata.append(("VLM service", _format_service(config.services.vlm)))
    metadata.append(("LLM service", _format_service(config.services.llm)))
    metadata.append((
        "Image routing",
        " | ".join([
            f"vlm_description={_on_off(config.processors.image.vlm_description)}",
            f"prompt={config.processors.image.vlm_prompt_style}",
            f"response={config.processors.image.vlm_response_format}",
            f"structured={config.processors.image.vlm_structured_output_mode}",
            f"retry={config.processors.image.vlm_retry_attempts}",
            f"max_tokens={config.processors.image.vlm_max_tokens}",
            f"skip_large_text_overlap_chars={config.processors.image.vlm_skip_large_text_overlap_chars}",
        ]),
    ))
    metadata.append((
        "Chapter fallback",
        _on_off(config.processors.chapter.llm_fallback),
    ))
    metadata.append((
        "Verification",
        " | ".join([
            f"hallucination={_on_off(config.verification.hallucination_detection)}",
            f"completeness={_on_off(config.verification.completeness_check)}",
            f"structure={_on_off(config.verification.structure_validation)}",
        ]),
    ))
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
