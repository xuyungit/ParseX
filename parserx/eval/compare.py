"""A/B comparison helpers for ParserX evaluations."""

from __future__ import annotations

from dataclasses import dataclass
import logging

from parserx.eval.metrics import EvalResult, fmt_metric, mean_defined
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

    # (label, per-result getter, better direction) in guide §9.3 order.
    metrics = [
        ("Table cell F1", lambda r: r.tables.cell_f1, "higher"),
        ("Char F1", lambda r: r.text.char_f1, "higher"),
        ("Edit distance", lambda r: r.text.edit_distance, "lower"),
        ("Reading order τ", lambda r: r.order.tau, "higher"),
        ("Heading F1", lambda r: r.headings.f1, "higher"),
    ]
    totals = [
        ("Missing tables", lambda r: r.tables.missing_tables),
        ("Key content errors", lambda r: r.key_content.total),
        ("OCR requests", lambda r: r.cost.ocr_calls),
        ("VLM requests", lambda r: r.cost.vlm_calls),
        ("LLM requests", lambda r: r.cost.llm_calls),
        ("Warnings", lambda r: r.cost.warning_count),
    ]
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
        f"| Metric | {label_a} | {label_b} | Delta ({label_b}-{label_a}) | Better |",
        "|--------|----|----|------------------------|--------|",
    ])
    for name, get, better in metrics:
        a = mean_defined(get(row.result_a) for row in rows)
        b = mean_defined(get(row.result_b) for row in rows)
        lines.append(f"| {name} | {fmt_metric(a)} | {fmt_metric(b)} | {_delta(a, b)} | {better} |")
    for name, get in totals:
        a = sum(get(row.result_a) for row in rows)
        b = sum(get(row.result_b) for row in rows)
        lines.append(f"| {name} | {a} | {b} | {b - a:+d} | lower |")
    lines.extend([
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
        "| Document | Table F1 Δ | Char F1 Δ | Edit Dist Δ | Order τ Δ | Heading F1 Δ | Key err Δ "
        "| Warn Δ | LLM Δ | Time Δ |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ])

    for row in rows:
        a = row.result_a
        b = row.result_b
        lines.append(
            f"| {row.document_name} "
            f"| {_delta(a.tables.cell_f1, b.tables.cell_f1)} "
            f"| {_delta(a.text.char_f1, b.text.char_f1)} "
            f"| {_delta(a.text.edit_distance, b.text.edit_distance)} "
            f"| {_delta(a.order.tau, b.order.tau)} "
            f"| {_delta(a.headings.f1, b.headings.f1)} "
            f"| {b.key_content.total - a.key_content.total:+d} "
            f"| {b.cost.warning_count - a.cost.warning_count:+d} "
            f"| {b.cost.llm_calls - a.cost.llm_calls:+d} "
            f"| {b.cost.wall_time_seconds - a.cost.wall_time_seconds:+.1f}s |"
        )

    lines.append("")
    return "\n".join(lines)


def _delta(a: float | None, b: float | None) -> str:
    return "—" if a is None or b is None else f"{b - a:+.3f}"
