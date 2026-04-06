"""Run multiple parsing tools and score their Markdown outputs."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from parserx.eval.metrics import (
    CostMetrics,
    EvalResult,
    compute_heading_metrics,
    compute_residual_diagnostics,
    compute_table_metrics,
    compute_text_metrics,
)
from parserx.tool_eval.adapters import (
    BuiltinDocPdfAdapter,
    LlamaParseAdapter,
    LiteParseAdapter,
    ParserXAdapter,
    ToolAdapter,
)


@dataclass
class ToolEvalRecord:
    """Single tool/document evaluation record."""

    tool: str
    document_name: str
    status: str
    artifact_dir: str
    output_path: str
    error: str = ""
    metrics: EvalResult | None = None
    metadata: dict | None = None


class MultiToolEvalRunner:
    """Generate Markdown with multiple tools and score them uniformly."""

    def __init__(
        self,
        *,
        tools: list[ToolAdapter] | None = None,
    ):
        self._tools = tools or [
            LlamaParseAdapter(),
            LiteParseAdapter(),
            BuiltinDocPdfAdapter(),
            ParserXAdapter(),
        ]

    def evaluate_dir(
        self,
        ground_truth_dir: Path,
        artifacts_dir: Path,
        *,
        include_docs: set[str] | None = None,
    ) -> list[ToolEvalRecord]:
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        records: list[ToolEvalRecord] = []

        for doc_dir in _iter_doc_dirs(ground_truth_dir, include_docs=include_docs):
            expected_path = doc_dir / "expected.md"
            input_path = _resolve_input_path(doc_dir)
            expected_md: str | None = None
            if expected_path.exists():
                expected_md = expected_path.read_text(encoding="utf-8")

            for tool in self._tools:
                tool_dir = artifacts_dir / tool.name / doc_dir.name
                tool_dir.mkdir(parents=True, exist_ok=True)
                output_path = tool_dir / "output.md"
                metadata_path = tool_dir / "metadata.json"

                try:
                    parse_result = tool.parse(input_path, tool_dir)
                    output_path.write_text(parse_result.markdown, encoding="utf-8")
                    merged_metadata: dict = {
                        "tool": tool.name,
                        "document_name": doc_dir.name,
                        "input_path": str(input_path.resolve()),
                        "artifact_dir": str(tool_dir.resolve()),
                        "wall_time_seconds": parse_result.wall_time_seconds,
                        "warnings": parse_result.warnings,
                        "api_calls": parse_result.api_calls,
                        **(parse_result.metadata or {}),
                    }
                    if expected_md is not None:
                        merged_metadata["expected_path"] = str(expected_path.resolve())
                    metadata_path.write_text(
                        json.dumps(merged_metadata, indent=2, ensure_ascii=False),
                        encoding="utf-8",
                    )

                    for name, content in parse_result.extra_files.items():
                        if name in {"output.md", "metadata.json"}:
                            continue
                        (tool_dir / name).write_text(content, encoding="utf-8")

                    metrics: EvalResult | None = None
                    status = "ok"
                    if expected_md is not None:
                        metrics = _score_markdown(
                            document_name=doc_dir.name,
                            output_md=parse_result.markdown,
                            expected_md=expected_md,
                            wall_time_seconds=parse_result.wall_time_seconds,
                            warnings=parse_result.warnings,
                            api_calls=parse_result.api_calls,
                        )
                    else:
                        status = "artifact_only"
                    records.append(
                        ToolEvalRecord(
                            tool=tool.name,
                            document_name=doc_dir.name,
                            status=status,
                            artifact_dir=str(tool_dir.resolve()),
                            output_path=str(output_path.resolve()),
                            metrics=metrics,
                            metadata=merged_metadata,
                        )
                    )
                except Exception as exc:
                    error_text = str(exc).strip() or exc.__class__.__name__
                    (tool_dir / "error.txt").write_text(error_text + "\n", encoding="utf-8")
                    metadata = {
                        "tool": tool.name,
                        "document_name": doc_dir.name,
                        "input_path": str(input_path.resolve()),
                        "artifact_dir": str(tool_dir.resolve()),
                        "status": "error",
                        "error": error_text,
                    }
                    metadata_path.write_text(
                        json.dumps(metadata, indent=2, ensure_ascii=False),
                        encoding="utf-8",
                    )
                    records.append(
                        ToolEvalRecord(
                            tool=tool.name,
                            document_name=doc_dir.name,
                            status="error",
                            artifact_dir=str(tool_dir.resolve()),
                            output_path=str(output_path.resolve()),
                            error=error_text,
                            metadata=metadata,
                        )
                    )

        manifest = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "ground_truth_dir": str(ground_truth_dir.resolve()),
            "artifacts_dir": str(artifacts_dir.resolve()),
            "records": [_record_to_json(record) for record in records],
        }
        (artifacts_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return records

    @staticmethod
    def format_report(
        records: list[ToolEvalRecord],
        *,
        ground_truth_dir: Path,
        artifacts_dir: Path,
    ) -> str:
        if not records:
            return "No results."

        tools = sorted({record.tool for record in records})
        docs = sorted({record.document_name for record in records})
        lines = [
            "# Multi-Tool Markdown Evaluation",
            "",
            "## Run Summary",
            "",
            f"- Ground truth: `{ground_truth_dir.resolve()}`",
            f"- Artifacts: `{artifacts_dir.resolve()}`",
            f"- Tools: {', '.join(tools)}",
            f"- Documents: {len(docs)}",
            "",
            "## Tool Summary",
            "",
            "| Tool | OK | Failed | Avg Edit Dist | Avg Char F1 | Avg Heading F1 | Avg Table F1 | Total Warn | Total Time | Artifact Root |",
            "|------|----|--------|---------------|-------------|----------------|--------------|------------|------------|---------------|",
        ]

        for tool in tools:
            tool_records = [record for record in records if record.tool == tool]
            ok_records = [record for record in tool_records if record.status == "ok" and record.metrics is not None]
            failed_count = sum(1 for record in tool_records if record.status == "error")
            artifact_root = artifacts_dir.resolve() / tool
            if ok_records:
                lines.append(
                    "| "
                    + " | ".join(
                        [
                            tool,
                            str(len(ok_records)),
                            str(failed_count),
                            f"{_avg(ok_records, lambda r: r.metrics.text.edit_distance):.3f}",
                            f"{_avg(ok_records, lambda r: r.metrics.text.char_f1):.3f}",
                            f"{_avg(ok_records, lambda r: r.metrics.headings.f1):.3f}",
                            f"{_avg(ok_records, lambda r: r.metrics.tables.cell_f1):.3f}",
                            str(sum(record.metrics.cost.warning_count for record in ok_records)),
                            f"{sum(record.metrics.cost.wall_time_seconds for record in ok_records):.1f}s",
                            f"`{artifact_root}`",
                        ]
                    )
                    + " |"
                )
            else:
                lines.append(
                    f"| {tool} | 0 | {failed_count} | - | - | - | - | - | - | `{artifact_root}` |"
                )

        lines.extend(
            [
                "",
                "## Per Document",
                "",
                "| Document | Tool | Status | Edit Dist | Char F1 | Heading F1 | Table F1 | Warn | Time | Output |",
                "|----------|------|--------|-----------|---------|------------|----------|------|------|--------|",
            ]
        )

        for record in sorted(records, key=lambda item: (item.document_name, item.tool)):
            if record.metrics is None:
                label = record.status if record.status != "ok" else "no_gt"
                lines.append(
                    f"| {record.document_name} | {record.tool} | {label} | - | - | - | - | - | - | `{record.artifact_dir}` |"
                )
                continue

            metrics = record.metrics
            lines.append(
                "| "
                + " | ".join(
                    [
                        record.document_name,
                        record.tool,
                        record.status,
                        f"{metrics.text.edit_distance:.3f}",
                        f"{metrics.text.char_f1:.3f}",
                        f"{metrics.headings.f1:.3f}",
                        f"{metrics.tables.cell_f1:.3f}",
                        str(metrics.cost.warning_count),
                        f"{metrics.cost.wall_time_seconds:.1f}s",
                        f"`{record.output_path}`",
                    ]
                )
                + " |"
            )

        failures = [record for record in records if record.status == "error"]
        if failures:
            lines.extend(["", "## Failures", ""])
            for record in failures:
                lines.append(
                    f"- `{record.tool}` / `{record.document_name}`: {record.error} "
                    f"(artifacts: `{record.artifact_dir}`)"
                )

        return "\n".join(lines) + "\n"


def _iter_doc_dirs(
    ground_truth_dir: Path,
    *,
    include_docs: set[str] | None = None,
) -> list[Path]:
    """Yield document directories that contain an input file.

    Directories are accepted whether or not ``expected.md`` exists — this
    allows the tool-eval workflow to produce artifacts for manual review
    before ground truth has been written.
    """
    if _has_input_file(ground_truth_dir):
        if include_docs is not None and ground_truth_dir.name not in include_docs:
            return []
        return [ground_truth_dir]

    doc_dirs: list[Path] = []
    for doc_dir in sorted(ground_truth_dir.iterdir()):
        if not doc_dir.is_dir():
            continue
        if include_docs is not None and doc_dir.name not in include_docs:
            continue
        if not _has_input_file(doc_dir):
            continue
        doc_dirs.append(doc_dir)
    return doc_dirs


def _has_input_file(doc_dir: Path) -> bool:
    """Check whether *doc_dir* contains at least one recognised input file."""
    return any((doc_dir / f"input{ext}").exists() for ext in (".pdf", ".docx", ".doc"))


def _resolve_input_path(doc_dir: Path) -> Path:
    for ext in (".pdf", ".docx", ".doc"):
        candidate = doc_dir / f"input{ext}"
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"No input file found in {doc_dir}")


def _score_markdown(
    *,
    document_name: str,
    output_md: str,
    expected_md: str,
    wall_time_seconds: float,
    warnings: list[str],
    api_calls: dict[str, int],
) -> EvalResult:
    return EvalResult(
        document_name=document_name,
        text=compute_text_metrics(output_md, expected_md),
        headings=compute_heading_metrics(output_md, expected_md),
        tables=compute_table_metrics(output_md, expected_md),
        cost=CostMetrics(
            wall_time_seconds=wall_time_seconds,
            ocr_calls=api_calls.get("ocr", 0),
            vlm_calls=api_calls.get("vlm", 0),
            llm_calls=api_calls.get("llm", 0),
            warning_count=len(warnings),
        ),
        warnings=list(warnings),
        residuals=compute_residual_diagnostics(output_md, expected_md),
    )


def _avg(records: list[ToolEvalRecord], fn) -> float:
    values = [fn(record) for record in records]
    return sum(values) / len(values)


def _record_to_json(record: ToolEvalRecord) -> dict:
    payload = asdict(record)
    if record.metrics is not None:
        payload["metrics"] = asdict(record.metrics)
    return payload
