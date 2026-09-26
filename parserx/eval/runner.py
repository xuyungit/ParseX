"""Evaluation runner — run parsing pipeline and compare against ground truth."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from parserx.config.schema import ParserXConfig
from parserx.eval.outline import has_outline
from parserx.eval.metrics import (
    CostMetrics,
    EvalResult,
    evaluate_markdown,
    fmt_metric,
    mean_defined,
)
from parserx.eval.key_content import KINDS as KEY_KINDS
from parserx.eval.reporting import ReportMetadata, append_metadata_section
from parserx.eval.warnings import summarize_warning_types, warning_label

log = logging.getLogger(__name__)


class NotReplayable(RuntimeError):
    """Offline replay lacked cached responses; the document's result would be degraded."""


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
        from parserx.pipeline import Pipeline  # imported here: the pipeline's modules use parserx.eval helpers

        self._pipeline = Pipeline(self._config)
        self.failed_docs: list[tuple[str, str]] = []
        # Requested (via include_docs) but never evaluated: a hard-check failure.
        self.not_executed: list[tuple[str, str]] = []
        # Directories without ground truth found during a full scan: informational.
        self.skipped: list[tuple[str, str]] = []
        # Markdown produced per document in the last evaluate_dir call.
        self.outputs: dict[str, str] = {}
        # v2 only: the sidecar (.blocks.json text) per document.
        self.sidecars: dict[str, str] = {}

    def evaluate_single(
        self, input_path: Path, expected_md_path: Path, name: str = "",
    ) -> EvalResult:
        """Evaluate a single document against its ground truth."""
        expected_md = expected_md_path.read_text(encoding="utf-8")

        # Single pipeline run — get parse result with verification metadata
        start = time.time()
        parse_result = self._pipeline.parse_result(input_path)
        elapsed = time.time() - start
        if parse_result.cache_misses:
            # v1 processors swallow service errors and degrade; a replay that
            # missed responses must not be scored as if it were complete.
            missed = ", ".join(f"{k} {v}" for k, v in sorted(parse_result.cache_misses.items()))
            raise NotReplayable(f"cache miss ({missed}); rerun with calls allowed")
        self.outputs[name or input_path.stem] = parse_result.markdown
        if parse_result.sidecar_json is not None:
            self.sidecars[name or input_path.stem] = parse_result.sidecar_json

        return evaluate_markdown(
            parse_result.markdown,
            expected_md,
            name=name or input_path.stem,
            cost=CostMetrics(
                wall_time_seconds=round(elapsed, 2),
                ocr_calls=parse_result.api_calls.get("ocr", 0),
                vlm_calls=parse_result.api_calls.get("vlm", 0),
                llm_calls=parse_result.api_calls.get("llm", 0),
                ocr_pages=parse_result.ocr_pages,
                attempts=dict(parse_result.api_attempts),
                cache_hits=dict(parse_result.cache_hits),
                cost_usd=parse_result.cost_usd,
                warning_count=len(parse_result.warnings),
                llm_fallback_hits=parse_result.llm_fallback_hits,
                pages_processed=parse_result.page_count,
                images_total=parse_result.images_total,
                images_skipped=parse_result.images_skipped,
            ),
            warnings=parse_result.warnings,
        )

    def evaluate_dir(
        self,
        ground_truth_dir: Path,
        *,
        include_docs: set[str] | None = None,
    ) -> list[EvalResult]:
        """Evaluate all documents in a ground truth directory.

        Documents that fail during parsing are logged and skipped so that
        one broken document does not abort the entire evaluation run.
        Failed document names are stored in ``self.failed_docs``.
        """
        self.failed_docs.clear()
        self.not_executed.clear()
        self.skipped.clear()
        self.outputs.clear()
        self.sidecars.clear()

        if (ground_truth_dir / "expected.md").exists():
            doc_dirs = [ground_truth_dir]
        elif ground_truth_dir.is_dir():
            doc_dirs = sorted(d for d in ground_truth_dir.iterdir() if d.is_dir())
        else:
            doc_dirs = []

        results = []
        seen: set[str] = set()
        for doc_dir in doc_dirs:
            if include_docs is not None and doc_dir.name not in include_docs:
                continue
            seen.add(doc_dir.name)
            result = self._evaluate_doc_dir(doc_dir, requested=include_docs is not None)
            if result is not None:
                results.append(result)

        for name in sorted((include_docs or set()) - seen):
            self.not_executed.append((name, f"not found in {ground_truth_dir}"))
        return results

    def _evaluate_doc_dir(self, doc_dir: Path, *, requested: bool = False) -> EvalResult | None:
        # A requested document that cannot run is a hard-check failure; an
        # unrequested directory without ground truth is only noted.
        missing = self.not_executed if requested else self.skipped
        expected_path = doc_dir / "expected.md"
        if not expected_path.exists():
            log.warning("No expected.md in %s, skipping", doc_dir.name)
            missing.append((doc_dir.name, "no expected.md"))
            return None

        input_path = None
        for ext in (".pdf", ".docx", ".doc"):
            candidate = doc_dir / f"input{ext}"
            if candidate.exists():
                input_path = candidate
                break

        if not input_path:
            log.warning("No input file in %s, skipping", doc_dir.name)
            missing.append((doc_dir.name, "no input file"))
            return None

        log.info("Evaluating: %s", doc_dir.name)
        try:
            result = self.evaluate_single(input_path, expected_path, name=doc_dir.name)
            result.outline = has_outline(doc_dir)
        except NotReplayable as exc:
            log.error("  NOT EXECUTED: %s — %s", doc_dir.name, exc)
            self.not_executed.append((doc_dir.name, str(exc)))
            return None
        except Exception as exc:
            log.error("  FAILED: %s — %s", doc_dir.name, exc)
            self.failed_docs.append((doc_dir.name, str(exc)))
            return None
        log.info(
            "  Tables: F1=%s miss/extra=%d/%d | Text: char_f1=%.3f edit=%.3f | Order: tau=%s"
            " | Headings: F1=%s | Key errors: %d | Requests O/V/L: %d/%d/%d | Time: %.1fs",
            fmt_metric(result.tables.cell_f1),
            result.tables.missing_tables,
            result.tables.extra_tables,
            result.text.char_f1,
            result.text.edit_distance,
            fmt_metric(result.order.tau),
            fmt_metric(result.headings.f1),
            result.key_content.total,
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
        failed_docs: list[tuple[str, str]] | None = None,
        not_executed: list[tuple[str, str]] | None = None,
    ) -> str:
        """Human-readable report; sections follow the fixed order of guide §9.3."""
        failed_docs = failed_docs or []
        not_executed = not_executed or []
        if not results and not failed_docs and not not_executed:
            return "No results."

        lines = ["# ParserX Evaluation Report", ""]
        append_metadata_section(lines, title="Run Metadata", metadata=metadata)

        missing_tables = sum(r.tables.missing_tables for r in results)
        extra_tables = sum(r.tables.extra_tables for r in results)
        lines.extend([
            "## Hard Checks",
            "",
            f"- Failed documents: {len(failed_docs)}",
            f"- Not executed: {len(not_executed)}",
            f"- Missing tables: {missing_tables}",
            f"- Extra tables: {extra_tables}",
            "",
        ])

        def avg(values) -> str:
            return fmt_metric(mean_defined(values))

        def applicable(values) -> int:
            return sum(v is not None for v in values)

        table_f1 = [r.tables.cell_f1 for r in results]
        order_tau = [r.order.tau for r in results]
        heading_f1 = [r.headings.f1 for r in results if r.outline]  # documents with an outline (Q79)
        key_missing = {k: sum(r.key_content.missing[k] for r in results) for k in KEY_KINDS}
        key_extra = {k: sum(r.key_content.extra[k] for r in results) for k in KEY_KINDS}
        total_ocr = sum(r.cost.ocr_calls for r in results)
        total_vlm = sum(r.cost.vlm_calls for r in results)
        total_llm = sum(r.cost.llm_calls for r in results)
        total_pages = sum(r.cost.ocr_pages for r in results)
        total_time = sum(r.cost.wall_time_seconds for r in results)
        costs = [r.cost.cost_usd for r in results]
        total_cost = "—" if any(c is None for c in costs) else f"${sum(costs):.4f}"

        lines.extend([
            "## Summary",
            "",
            f"- Documents: {len(results)}",
            f"- Table cell F1: {avg(table_f1)} ({applicable(table_f1)} docs with tables)"
            f" | header association {avg(r.tables.header_association for r in results)}"
            f" | merged cells {avg(r.tables.merged_cell_accuracy for r in results)}",
            f"- Char F1: {avg(r.text.char_f1 for r in results)}"
            f" | edit distance {avg(r.text.edit_distance for r in results)}"
            f" | char_bag_f1 (diagnostic) {avg(r.text.char_bag_f1 for r in results)}",
            f"- Reading order tau: {avg(order_tau)} ({applicable(order_tau)} docs)"
            f" | coverage {avg(r.order.coverage for r in results)}",
            f"- Heading F1: {avg(heading_f1)} ({applicable(heading_f1)} docs with an outline)",
            "- Key content errors (missing/extra): "
            + ", ".join(f"{k} {key_missing[k]}/{key_extra[k]}" for k in KEY_KINDS),
            f"- Real requests (OCR/VLM/LLM): {total_ocr}/{total_vlm}/{total_llm} (OCR pages {total_pages})",
            f"- Total wall time: {total_time:.1f}s",
            f"- Cost: {total_cost}",
            f"- Total warnings: {sum(r.cost.warning_count for r in results)}",
            f"- LLM fallback hits: {sum(r.cost.llm_fallback_hits for r in results)}",
            "",
            "## Per Document",
            "",
            "| Document | Miss/Extra tables | Table F1 | Char F1 | Edit Dist | Order τ | Heading F1 "
            "| Key errors | Requests O/V/L | Time | Cost |",
            "|---|---|---|---|---|---|---|---|---|---|---|",
        ])
        for r in results:
            lines.append(
                f"| {r.document_name} "
                f"| {r.tables.missing_tables}/{r.tables.extra_tables} "
                f"| {fmt_metric(r.tables.cell_f1)} "
                f"| {r.text.char_f1:.3f} "
                f"| {r.text.edit_distance:.3f} "
                f"| {fmt_metric(r.order.tau)} "
                f"| {fmt_metric(r.headings.f1)} "
                f"| {r.key_content.total} "
                f"| {r.cost.ocr_calls}/{r.cost.vlm_calls}/{r.cost.llm_calls} "
                f"| {r.cost.wall_time_seconds:.1f}s "
                f"| {fmt_metric(r.cost.cost_usd, 4)} |"
            )
        if len(results) > 1:
            lines.append(
                f"| **Average / total** | **{missing_tables}/{extra_tables}** "
                f"| **{avg(table_f1)}** "
                f"| **{avg(r.text.char_f1 for r in results)}** "
                f"| **{avg(r.text.edit_distance for r in results)}** "
                f"| **{avg(order_tau)}** "
                f"| **{avg(heading_f1)}** "
                f"| **{sum(r.key_content.total for r in results)}** "
                f"| **{total_ocr}/{total_vlm}/{total_llm}** "
                f"| **{total_time:.1f}s** | **{total_cost}** |"
            )

        _append_diagnostics(lines, results)

        if failed_docs:
            lines.extend(["", "## Failed Documents", ""])
            lines.extend(f"- **{name}**: {error}" for name, error in failed_docs)
        if not_executed:
            lines.extend(["", "## Not Executed", ""])
            lines.extend(f"- **{name}**: {reason}" for name, reason in not_executed)

        lines.append("")
        return "\n".join(lines)


def _append_diagnostics(lines: list[str], results: list[EvalResult]) -> None:
    """Residual themes, warnings and residual excerpts (after the scored sections)."""
    residual_theme_counts: dict[str, int] = {}
    for result in results:
        for theme in result.residuals.themes:
            residual_theme_counts[theme] = residual_theme_counts.get(theme, 0) + 1
    if residual_theme_counts:
        lines.extend(["", "## Residual Themes", "", "| Theme | Docs |", "|-------|------|"])
        for theme, count in sorted(residual_theme_counts.items(), key=lambda item: (-item[1], item[0])):
            lines.append(f"| {theme} | {count} |")

    warning_hotspots = [r for r in results if r.warnings]
    if warning_hotspots:
        warning_type_counts = summarize_warning_types(
            [warning for result in results for warning in result.warnings]
        )
        lines.extend(["", "## Warning Types", "", "| Warning Type | Count |", "|--------------|-------|"])
        for code, count in warning_type_counts.items():
            lines.append(f"| {warning_label(code)} | {count} |")
        lines.extend(["", "## Warning Hotspots", ""])
        for result in sorted(warning_hotspots, key=lambda item: (-item.cost.warning_count, item.document_name)):
            preview = "; ".join(result.warnings[:2])
            if len(result.warnings) > 2:
                preview += "; ..."
            lines.append(f"- {result.document_name}: {result.cost.warning_count} warning(s) — {preview}")

    residual_hotspots = [r for r in results if r.residuals.extra.text or r.residuals.missing.text]
    if residual_hotspots:
        lines.extend(["", "## Residual Diagnostics", ""])
        for result in sorted(residual_hotspots, key=lambda item: (-item.text.edit_distance, item.document_name)):
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
