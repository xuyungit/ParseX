"""A/B comparison helpers for ParserX evaluations."""

from __future__ import annotations

from dataclasses import dataclass
import logging

from parserx.eval.metrics import EvalResult
from parserx.eval.reporting import ReportMetadata, append_metadata_section
from parserx.eval.warnings import summarize_warning_types, warning_label

log = logging.getLogger(__name__)


@dataclass
class CompareRow:
    """Per-document comparison between two evaluation runs."""

    document_name: str
    result_a: EvalResult
    result_b: EvalResult


def compare_results(
    results_a: list[EvalResult],
    results_b: list[EvalResult],
) -> list[CompareRow]:
    """Align two eval result sets by document name."""
    by_name_a = {result.document_name: result for result in results_a}
    by_name_b = {result.document_name: result for result in results_b}
    missing_from_b = sorted(set(by_name_a) - set(by_name_b))
    missing_from_a = sorted(set(by_name_b) - set(by_name_a))

    if missing_from_b:
        log.warning(
            "Documents only present in compare A and omitted from diff: %s",
            ", ".join(missing_from_b),
        )
    if missing_from_a:
        log.warning(
            "Documents only present in compare B and omitted from diff: %s",
            ", ".join(missing_from_a),
        )

    shared_names = sorted(set(by_name_a) & set(by_name_b))
    return [
        CompareRow(
            document_name=name,
            result_a=by_name_a[name],
            result_b=by_name_b[name],
        )
        for name in shared_names
    ]


def format_compare_report(
    rows: list[CompareRow],
    *,
    label_a: str = "A",
    label_b: str = "B",
    metadata_a: ReportMetadata | None = None,
    metadata_b: ReportMetadata | None = None,
) -> str:
    """Render a compact A/B comparison report."""
    if not rows:
        return "No comparable results."

    avg_char_a = sum(row.result_a.text.char_f1 for row in rows) / len(rows)
    avg_char_b = sum(row.result_b.text.char_f1 for row in rows) / len(rows)
    avg_heading_a = sum(row.result_a.headings.f1 for row in rows) / len(rows)
    avg_heading_b = sum(row.result_b.headings.f1 for row in rows) / len(rows)
    avg_table_a = sum(row.result_a.tables.cell_f1 for row in rows) / len(rows)
    avg_table_b = sum(row.result_b.tables.cell_f1 for row in rows) / len(rows)
    avg_edit_a = sum(row.result_a.text.edit_distance for row in rows) / len(rows)
    avg_edit_b = sum(row.result_b.text.edit_distance for row in rows) / len(rows)
    warn_a = sum(row.result_a.cost.warning_count for row in rows)
    warn_b = sum(row.result_b.cost.warning_count for row in rows)
    llm_a = sum(row.result_a.cost.llm_calls for row in rows)
    llm_b = sum(row.result_b.cost.llm_calls for row in rows)
    time_a = sum(row.result_a.cost.wall_time_seconds for row in rows)
    time_b = sum(row.result_b.cost.wall_time_seconds for row in rows)

    improved_char = sum(1 for row in rows if row.result_b.text.char_f1 > row.result_a.text.char_f1)
    regressed_char = sum(1 for row in rows if row.result_b.text.char_f1 < row.result_a.text.char_f1)
    reduced_warn = sum(1 for row in rows if row.result_b.cost.warning_count < row.result_a.cost.warning_count)
    increased_warn = sum(1 for row in rows if row.result_b.cost.warning_count > row.result_a.cost.warning_count)

    lines = [
        "# ParserX Compare Report",
        "",
        f"Comparing **{label_a}** vs **{label_b}** on {len(rows)} document(s).",
        "",
    ]
    append_metadata_section(lines, title=f"{label_a} Metadata", metadata=metadata_a)
    append_metadata_section(lines, title=f"{label_b} Metadata", metadata=metadata_b)
    lines.extend([
        "## Summary",
        "",
        "| Metric | "
        f"{label_a} | {label_b} | Delta ({label_b}-{label_a}) | Better |",
        "|--------|"
        "----|----|------------------------|--------|",
        f"| Edit distance | {avg_edit_a:.3f} | {avg_edit_b:.3f} | {avg_edit_b - avg_edit_a:+.3f} | lower |",
        f"| Char F1 | {avg_char_a:.3f} | {avg_char_b:.3f} | {avg_char_b - avg_char_a:+.3f} | higher |",
        f"| Heading F1 | {avg_heading_a:.3f} | {avg_heading_b:.3f} | {avg_heading_b - avg_heading_a:+.3f} | higher |",
        f"| Table F1 | {avg_table_a:.3f} | {avg_table_b:.3f} | {avg_table_b - avg_table_a:+.3f} | higher |",
        f"| Warnings | {warn_a} | {warn_b} | {warn_b - warn_a:+d} | lower |",
        f"| LLM API calls | {llm_a} | {llm_b} | {llm_b - llm_a:+d} | lower |",
        f"| Wall time (s) | {time_a:.1f} | {time_b:.1f} | {time_b - time_a:+.1f} | lower |",
        "",
        f"- Char F1 improved on {improved_char} doc(s), regressed on {regressed_char}.",
        f"- Warning count dropped on {reduced_warn} doc(s), increased on {increased_warn}.",
        "",
    ])

    warnings_a = [warning for row in rows for warning in row.result_a.warnings]
    warnings_b = [warning for row in rows for warning in row.result_b.warnings]
    warning_types_a = summarize_warning_types(warnings_a)
    warning_types_b = summarize_warning_types(warnings_b)
    warning_codes = sorted(
        set(warning_types_a) | set(warning_types_b),
        key=lambda code: (
            -max(warning_types_a.get(code, 0), warning_types_b.get(code, 0)),
            warning_label(code),
            code,
        ),
    )

    if warning_codes:
        lines.extend([
            "",
            "## Warning Type Delta",
            "",
            f"| Warning Type | {label_a} | {label_b} | Delta ({label_b}-{label_a}) |",
            "|--------------|-----|-----|------------------------|",
        ])
        for code in warning_codes:
            count_a = warning_types_a.get(code, 0)
            count_b = warning_types_b.get(code, 0)
            lines.append(
                f"| {warning_label(code)} | {count_a} | {count_b} | {count_b - count_a:+d} |"
            )

    lines.extend([
        "",
        "## Per Document",
        "",
        "| Document | Edit Dist Δ | Char F1 Δ | Heading F1 Δ | Table F1 Δ | Warn Δ | LLM Δ | Time Δ |",
        "|----------|-------------|-----------|--------------|------------|--------|-------|--------|",
    ])

    for row in rows:
        a = row.result_a
        b = row.result_b
        lines.append(
            f"| {row.document_name} "
            f"| {b.text.edit_distance - a.text.edit_distance:+.3f} "
            f"| {b.text.char_f1 - a.text.char_f1:+.3f} "
            f"| {b.headings.f1 - a.headings.f1:+.3f} "
            f"| {b.tables.cell_f1 - a.tables.cell_f1:+.3f} "
            f"| {b.cost.warning_count - a.cost.warning_count:+d} "
            f"| {b.cost.llm_calls - a.cost.llm_calls:+d} "
            f"| {b.cost.wall_time_seconds - a.cost.wall_time_seconds:+.1f}s |"
        )

    lines.append("")
    return "\n".join(lines)
