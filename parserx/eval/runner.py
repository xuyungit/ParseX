"""Evaluation runner — run parsing pipeline and compare against ground truth."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from parserx.config.schema import ParserXConfig
from parserx.eval.metrics import (
    CostMetrics,
    EvalResult,
    compute_heading_metrics,
    compute_table_metrics,
    compute_text_metrics,
)
from parserx.pipeline import Pipeline

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

        # Single pipeline run — get Document, then render Markdown from it
        start = time.time()
        doc = self._pipeline.parse_to_document(input_path)
        elapsed = time.time() - start

        from parserx.assembly.markdown import MarkdownRenderer
        renderer = MarkdownRenderer(self._config.output)
        output_md = renderer.render(doc)

        images_total = len(doc.elements_by_type("image"))
        images_skipped = sum(
            1 for e in doc.elements_by_type("image")
            if e.metadata.get("skipped")
        )

        # Compute metrics
        text_metrics = compute_text_metrics(output_md, expected_md)
        heading_metrics = compute_heading_metrics(output_md, expected_md)
        table_metrics = compute_table_metrics(output_md, expected_md)

        return EvalResult(
            document_name=name or input_path.stem,
            text=text_metrics,
            headings=heading_metrics,
            tables=table_metrics,
            cost=CostMetrics(
                wall_time_seconds=round(elapsed, 2),
                pages_processed=len(doc.pages),
                images_total=images_total,
                images_skipped=images_skipped,
            ),
        )

    def evaluate_dir(self, ground_truth_dir: Path) -> list[EvalResult]:
        """Evaluate all documents in a ground truth directory."""
        results = []

        for doc_dir in sorted(ground_truth_dir.iterdir()):
            if not doc_dir.is_dir():
                continue

            expected_path = doc_dir / "expected.md"
            if not expected_path.exists():
                log.warning("No expected.md in %s, skipping", doc_dir.name)
                continue

            # Find input file
            input_path = None
            for ext in (".pdf", ".docx", ".doc"):
                candidate = doc_dir / f"input{ext}"
                if candidate.exists():
                    input_path = candidate
                    break

            if not input_path:
                log.warning("No input file in %s, skipping", doc_dir.name)
                continue

            log.info("Evaluating: %s", doc_dir.name)
            result = self.evaluate_single(input_path, expected_path, name=doc_dir.name)
            results.append(result)

            log.info(
                "  Text: edit_dist=%.3f, char_f1=%.3f | Headings: P=%.2f R=%.2f F1=%.2f (%d/%d) | Tables: %d found, %.2f cell_f1 | Time: %.1fs",
                result.text.edit_distance,
                result.text.char_f1,
                result.headings.precision,
                result.headings.recall,
                result.headings.f1,
                result.headings.correct_count,
                result.headings.expected_count,
                result.tables.detected_count,
                result.tables.cell_f1,
                result.cost.wall_time_seconds,
            )

        return results

    @staticmethod
    def format_report(results: list[EvalResult]) -> str:
        """Format evaluation results as a human-readable report."""
        if not results:
            return "No results."

        lines = ["# ParserX Evaluation Report", ""]

        # Summary table
        lines.append("| Document | Edit Dist | Char F1 | Heading F1 | H P/R | Table F1 | Time |")
        lines.append("|----------|-----------|---------|------------|-------|----------|------|")

        for r in results:
            lines.append(
                f"| {r.document_name} "
                f"| {r.text.edit_distance:.3f} "
                f"| {r.text.char_f1:.3f} "
                f"| {r.headings.f1:.3f} "
                f"| {r.headings.precision:.2f}/{r.headings.recall:.2f} "
                f"| {r.tables.cell_f1:.3f} "
                f"| {r.cost.wall_time_seconds:.1f}s |"
            )

        # Averages
        if len(results) > 1:
            avg_ed = sum(r.text.edit_distance for r in results) / len(results)
            avg_cf1 = sum(r.text.char_f1 for r in results) / len(results)
            avg_hf1 = sum(r.headings.f1 for r in results) / len(results)
            avg_tf1 = sum(r.tables.cell_f1 for r in results) / len(results)
            lines.append(
                f"| **Average** | **{avg_ed:.3f}** | **{avg_cf1:.3f}** "
                f"| **{avg_hf1:.3f}** | | **{avg_tf1:.3f}** | |"
            )

        lines.append("")
        return "\n".join(lines)
