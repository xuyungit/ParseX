"""Evaluation runner — run parsing pipeline and compare against ground truth."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from parserx.config.schema import ParserXConfig
from parserx.eval.metrics import (
    CostMetrics,
    EvalResult,
    compute_residual_diagnostics,
    compute_heading_metrics,
    compute_table_metrics,
    compute_text_metrics,
)
from parserx.eval.reporting import ReportMetadata, append_metadata_section
from parserx.pipeline import Pipeline
from parserx.eval.warnings import summarize_warning_types, warning_label

log = logging.getLogger(__name__)


class EvalRunner:
    """Run evaluation on documents with ground truth.

    Ground truth structure:
        ground_truth_dir/
            doc_name/
                input.pdf (or .docx)
                expected.md          # Expected Markdown output
    """

    def __init__(self, config: ParserXConfig | None = None):
        self._config = config or ParserXConfig()
        self._pipeline = Pipeline(self._config)

    def evaluate_single(
        self, input_path: Path, expected_md_path: Path, name: str = "",
    ) -> EvalResult:
        """Evaluate a single document against its ground truth."""
        expected_md = expected_md_path.read_text(encoding="utf-8")

        # Single pipeline run — get parse result with verification metadata
        start = time.time()
        parse_result = self._pipeline.parse_result(input_path)
        elapsed = time.time() - start

        # Compute metrics
        text_metrics = compute_text_metrics(parse_result.markdown, expected_md)
        heading_metrics = compute_heading_metrics(parse_result.markdown, expected_md)
        table_metrics = compute_table_metrics(parse_result.markdown, expected_md)

        return EvalResult(
            document_name=name or input_path.stem,
            text=text_metrics,
            headings=heading_metrics,
            tables=table_metrics,
            cost=CostMetrics(
                wall_time_seconds=round(elapsed, 2),
                ocr_calls=parse_result.api_calls.get("ocr", 0),
                vlm_calls=parse_result.api_calls.get("vlm", 0),
                llm_calls=parse_result.api_calls.get("llm", 0),
                warning_count=len(parse_result.warnings),
                llm_fallback_hits=parse_result.llm_fallback_hits,
                pages_processed=parse_result.page_count,
                images_total=parse_result.images_total,
                images_skipped=parse_result.images_skipped,
            ),
            warnings=parse_result.warnings,
            residuals=compute_residual_diagnostics(parse_result.markdown, expected_md),
        )

    def evaluate_dir(
        self,
        ground_truth_dir: Path,
        *,
        include_docs: set[str] | None = None,
    ) -> list[EvalResult]:
        """Evaluate all documents in a ground truth directory."""
        if (ground_truth_dir / "expected.md").exists():
            if include_docs is not None and ground_truth_dir.name not in include_docs:
                log.info("Skipping %s (not in include set)", ground_truth_dir.name)
                return []
            result = self._evaluate_doc_dir(ground_truth_dir)
            return [result] if result is not None else []

        results = []

        for doc_dir in sorted(ground_truth_dir.iterdir()):
            if not doc_dir.is_dir():
                continue
            if include_docs is not None and doc_dir.name not in include_docs:
                continue
            result = self._evaluate_doc_dir(doc_dir)
            if result is not None:
                results.append(result)

        return results

    def _evaluate_doc_dir(self, doc_dir: Path) -> EvalResult | None:
        expected_path = doc_dir / "expected.md"
        if not expected_path.exists():
            log.warning("No expected.md in %s, skipping", doc_dir.name)
            return None

        input_path = None
        for ext in (".pdf", ".docx", ".doc"):
            candidate = doc_dir / f"input{ext}"
            if candidate.exists():
                input_path = candidate
                break

        if not input_path:
            log.warning("No input file in %s, skipping", doc_dir.name)
            return None

        log.info("Evaluating: %s", doc_dir.name)
        result = self.evaluate_single(input_path, expected_path, name=doc_dir.name)
        log.info(
            "  Text: edit_dist=%.3f, char_f1=%.3f | Headings: P=%.2f R=%.2f F1=%.2f (%d/%d) | Tables: %d found, %.2f cell_f1 | Warn: %d | API O/V/L: %d/%d/%d | Time: %.1fs",
            result.text.edit_distance,
            result.text.char_f1,
            result.headings.precision,
            result.headings.recall,
            result.headings.f1,
            result.headings.correct_count,
            result.headings.expected_count,
            result.tables.detected_count,
            result.tables.cell_f1,
            result.cost.warning_count,
            result.cost.ocr_calls,
            result.cost.vlm_calls,
            result.cost.llm_calls,
            result.cost.wall_time_seconds,
        )
        return result

    @staticmethod
    def format_report(
        results: list[EvalResult],
        *,
        metadata: ReportMetadata | None = None,
    ) -> str:
        """Format evaluation results as a human-readable report."""
        if not results:
            return "No results."

        lines = ["# ParserX Evaluation Report", ""]
        append_metadata_section(lines, title="Run Metadata", metadata=metadata)
        total_docs = len(results)
        total_warnings = sum(r.cost.warning_count for r in results)
        total_ocr = sum(r.cost.ocr_calls for r in results)
        total_vlm = sum(r.cost.vlm_calls for r in results)
        total_llm = sum(r.cost.llm_calls for r in results)
        total_fallback_hits = sum(r.cost.llm_fallback_hits for r in results)
        total_time = sum(r.cost.wall_time_seconds for r in results)
        avg_ed = sum(r.text.edit_distance for r in results) / total_docs
        avg_cf1 = sum(r.text.char_f1 for r in results) / total_docs
        avg_hf1 = sum(r.headings.f1 for r in results) / total_docs
        avg_tf1 = sum(r.tables.cell_f1 for r in results) / total_docs

        lines.extend([
            "## Summary",
            "",
            f"- Documents: {total_docs}",
            f"- Avg edit distance: {avg_ed:.3f}",
            f"- Avg char F1: {avg_cf1:.3f}",
            f"- Avg heading F1: {avg_hf1:.3f}",
            f"- Avg table cell F1: {avg_tf1:.3f}",
            f"- Total warnings: {total_warnings}",
            f"- API calls (OCR/VLM/LLM): {total_ocr}/{total_vlm}/{total_llm}",
            f"- LLM fallback hits: {total_fallback_hits}",
            f"- Total wall time: {total_time:.1f}s",
            "",
            "## Per Document",
            "",
        ])

        # Summary table
        lines.append("| Document | Edit Dist | Char F1 | Heading F1 | Table F1 | Warn | API O/V/L | Fallback | Time |")
        lines.append("|----------|-----------|---------|------------|----------|------|-----------|----------|------|")

        for r in results:
            lines.append(
                f"| {r.document_name} "
                f"| {r.text.edit_distance:.3f} "
                f"| {r.text.char_f1:.3f} "
                f"| {r.headings.f1:.3f} "
                f"| {r.tables.cell_f1:.3f} "
                f"| {r.cost.warning_count} "
                f"| {r.cost.ocr_calls}/{r.cost.vlm_calls}/{r.cost.llm_calls} "
                f"| {r.cost.llm_fallback_hits} "
                f"| {r.cost.wall_time_seconds:.1f}s |"
            )

        # Averages
        if total_docs > 1:
            lines.append(
                f"| **Average** | **{avg_ed:.3f}** | **{avg_cf1:.3f}** "
                f"| **{avg_hf1:.3f}** | **{avg_tf1:.3f}** "
                f"| **{total_warnings}** | **{total_ocr}/{total_vlm}/{total_llm}** "
                f"| **{total_fallback_hits}** | **{total_time:.1f}s** |"
            )

        warning_hotspots = [r for r in results if r.warnings]
        residual_theme_counts: dict[str, int] = {}
        for result in results:
            for theme in result.residuals.themes:
                residual_theme_counts[theme] = residual_theme_counts.get(theme, 0) + 1

        if residual_theme_counts:
            lines.extend([
                "",
                "## Residual Themes",
                "",
                "| Theme | Docs |",
                "|-------|------|",
            ])
            for theme, count in sorted(
                residual_theme_counts.items(),
                key=lambda item: (-item[1], item[0]),
            ):
                lines.append(f"| {theme} | {count} |")

        if warning_hotspots:
            warning_type_counts = summarize_warning_types(
                [warning for result in results for warning in result.warnings]
            )
            lines.extend([
                "",
                "## Warning Types",
                "",
                "| Warning Type | Count |",
                "|--------------|-------|",
            ])
            for code, count in warning_type_counts.items():
                lines.append(f"| {warning_label(code)} | {count} |")

            lines.extend([
                "",
                "## Warning Hotspots",
                "",
            ])
            for result in sorted(warning_hotspots, key=lambda item: (-item.cost.warning_count, item.document_name)):
                preview = "; ".join(result.warnings[:2])
                if len(result.warnings) > 2:
                    preview += "; ..."
                lines.append(f"- {result.document_name}: {result.cost.warning_count} warning(s) — {preview}")

        residual_hotspots = [
            result for result in results
            if result.residuals.extra.text or result.residuals.missing.text
        ]
        if residual_hotspots:
            lines.extend([
                "",
                "## Residual Diagnostics",
                "",
            ])
            for result in sorted(
                residual_hotspots,
                key=lambda item: (-item.text.edit_distance, item.document_name),
            ):
                themes = ", ".join(result.residuals.themes) if result.residuals.themes else "(none)"
                lines.append(f"### {result.document_name}")
                lines.append("")
                lines.append(f"- Themes: `{themes}`")
                lines.append(
                    f"- Blocks: extra={result.residuals.extra_block_count}, missing={result.residuals.missing_block_count}"
                )
                if result.residuals.extra.text:
                    lines.append(f"- Output-only excerpt: `{result.residuals.extra.text}`")
                if result.residuals.missing.text:
                    lines.append(f"- Expected-only excerpt: `{result.residuals.missing.text}`")
                lines.append("")

        lines.append("")
        return "\n".join(lines)
