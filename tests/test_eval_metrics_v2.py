"""Metric definitions v2.0 (guide §9.2): tables, text normalization, order, key content, gate."""

from parserx.eval.gate import evaluate_gate, run_record
from parserx.eval.key_content import compute_key_content_errors
from parserx.eval.metrics import (
    compute_heading_metrics,
    compute_table_metrics,
    compute_text_metrics,
    evaluate_markdown,
)
from parserx.eval.normalize import canonicalize
from parserx.eval.order import compute_order_metrics

# ── Tables ──────────────────────────────────────────────────────────────


def _table(rows: list[list[str]]) -> str:
    head, *body = rows
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    lines += ["| " + " | ".join(r) + " |" for r in body]
    return "\n".join(lines) + "\n"


def test_inserted_row_only_costs_that_row():
    rows = [["k", "v"]] + [[f"r{i}", str(i)] for i in range(10)]
    expected = _table(rows)
    output = _table(rows[:1] + [["插入", "x"]] + rows[1:])
    metrics = compute_table_metrics(output, expected)
    assert metrics.cell_recall == 1.0
    assert 0.9 < metrics.cell_precision < 1.0


def test_extra_table_is_counted():
    expected = _table([["a", "b"], ["1", "2"]])
    output = expected + "\n" + _table([["x", "y"], ["8", "9"]])
    metrics = compute_table_metrics(output, expected)
    assert (metrics.extra_tables, metrics.missing_tables) == (1, 0)
    assert metrics.cell_precision == 0.5


def test_merged_cells_and_header_association():
    expected = (
        "<table><tr><th rowspan='2'>项目</th><th colspan='2'>指标</th></tr>"
        "<tr><th>长</th><th>宽</th></tr>"
        "<tr><td>甲</td><td>10</td><td>20</td></tr></table>"
    )
    flattened = _table([["项目", "指标 > 长", "指标 > 宽"], ["甲", "10", "20"]])
    same = compute_table_metrics(expected, expected)
    flat = compute_table_metrics(flattened, expected)
    assert same.merged_cell_accuracy == 1.0 and same.header_association == 1.0
    assert flat.merged_cell_accuracy == 0.0  # GFM cannot keep the spans
    assert flat.header_association == 1.0  # but every value still sits under the right header path


def test_no_tables_anywhere_is_not_applicable():
    metrics = compute_table_metrics("正文", "正文")
    assert metrics.cell_f1 is None


# ── Text normalization ──────────────────────────────────────────────────


def test_table_format_does_not_change_text_scores():
    gfm = "前言\n\n" + _table([["甲", "乙"], ["10", "20"]])
    html = "前言\n\n<table><tr><th>甲</th><th>乙</th></tr><tr><td>10</td><td>20</td></tr></table>\n"
    metrics = compute_text_metrics(html, gfm)
    assert metrics.char_f1 == 1.0 and metrics.edit_distance == 0.0


def test_image_descriptions_are_excluded_but_counted():
    expected = "正文\n\n> [图片] 一张标准的示意图\n\n结尾"
    output = "正文\n\n![模型的措辞](images/a.png)\n\n> 完全不同的描述句子\n\n结尾"
    canon = canonicalize(output)
    assert canon.image_count == 1 and "描述" not in canon.text
    metrics = compute_text_metrics(output, expected)
    assert metrics.char_f1 == 1.0
    assert (metrics.images_expected, metrics.images_output) == (1, 1)


def test_blockquote_not_after_image_is_kept():
    canon = canonicalize("正文\n\n> 引用的原文\n")
    assert "引用的原文" in canon.text


def test_compatibility_characters_and_whitespace_are_ignored():
    # U+2F08 KANGXI RADICAL MAN vs U+4EBA, full-width digits, spacing
    metrics = compute_text_metrics("发件⼈： ２０２５ 年", "发件人:2025年")
    assert metrics.char_f1 == 1.0


# ── Reading order ───────────────────────────────────────────────────────

_P1 = "第一段落讲述项目背景和建设目标，内容较长。"
_P2 = "第二段落描述施工方案与主要工程数量安排。"
_P3 = "第三段落总结质量控制措施和验收标准要求。"


def test_identical_order_has_tau_one():
    md = f"{_P1}\n\n{_P2}\n\n{_P3}\n\n{_P2}"  # a repeated block cannot be located; it is not counted
    metrics = compute_order_metrics(md, md)
    assert metrics.tau == 1.0 and metrics.coverage == 1.0


def test_swapped_paragraphs_lower_tau():
    expected = f"{_P1}\n\n{_P2}\n\n{_P3}"
    output = f"{_P3}\n\n{_P2}\n\n{_P1}"
    metrics = compute_order_metrics(output, expected)
    assert metrics.tau == -1.0 and metrics.inversions == 3


def test_missing_block_affects_coverage_not_tau():
    expected = f"{_P1}\n\n{_P2}\n\n{_P3}"
    output = f"{_P1}\n\n{_P3}"
    metrics = compute_order_metrics(output, expected)
    assert metrics.tau == 1.0 and metrics.coverage < 1.0


def test_rewrapped_lines_still_locate_blocks():
    expected = f"{_P1}{_P2}"
    output = f"{_P1[:10]}\n{_P1[10:]}{_P2[:5]}\n{_P2[5:]}"
    assert compute_order_metrics(output, expected).coverage == 1.0


# ── Key content ─────────────────────────────────────────────────────────


def test_changed_amount_is_a_number_and_unit_error():
    errors = compute_key_content_errors("合同金额999万元", "合同金额100万元")
    assert errors.missing["number"] == 1 and errors.extra["number"] == 1
    assert errors.missing["unit"] == 1 and errors.extra["unit"] == 1


def test_date_spellings_are_equivalent():
    errors = compute_key_content_errors("日期: 2025-11-24", "日期：2025年11月24日")
    assert errors.total == 0


def test_dropped_negation_is_counted():
    errors = compute_key_content_errors("该批次合格", "该批次不合格")
    assert errors.missing["negation"] == 1


def test_thousands_separator_is_normalized():
    assert compute_key_content_errors("共 1,234.5 元", "共1234.5元").total == 0


# ── Headings ────────────────────────────────────────────────────────────


def test_headings_absent_on_both_sides_is_not_applicable():
    assert compute_heading_metrics("正文", "正文").f1 is None


# ── Gate ────────────────────────────────────────────────────────────────


def _record(**docs):
    results = [evaluate_markdown(md_out, md_exp, name=name) for name, (md_out, md_exp) in docs.items()]
    return run_record(results, failed=[], not_executed=[])


def test_not_executed_documents_fail_the_gate():
    record = run_record([], failed=[], not_executed=[("text_table01", "not found")])
    assert evaluate_gate(record, baseline=None).exit_code == 2


def test_new_missing_table_fails_only_against_a_baseline():
    table = _table([["a", "b"], ["1", "2"]])
    good = _record(doc=(table, table))
    bad = _record(doc=("正文", table))
    assert evaluate_gate(bad, baseline=None).exit_code == 0  # first freeze only records
    outcome = evaluate_gate(bad, baseline=good)
    assert outcome.exit_code == 2
    assert any("missing_tables" in line for line in outcome.hard_failures)


def test_metric_regression_exits_one():
    base = _record(doc=(f"{_P1}\n\n{_P2}", f"{_P1}\n\n{_P2}"))
    worse = _record(doc=(f"{_P2}\n\n{_P1}", f"{_P1}\n\n{_P2}"))
    outcome = evaluate_gate(worse, baseline=base)
    assert outcome.exit_code == 1 and outcome.regressions


def test_metric_version_mismatch_refuses_comparison():
    base = _record(doc=("x", "x"))
    base["metric_version"] = "1.0"
    assert evaluate_gate(_record(doc=("x", "x")), baseline=base).exit_code == 2


# ── Metric 2.1: merged cells against annotations that cannot express them (Q28) ──

_SPANNED_OUTPUT = (
    "<table><tr><th>种类</th><th>设计值</th><th>备注</th></tr>"
    "<tr><td rowspan=\"3\">钢绞线</td><td>1720</td><td rowspan=\"3\">390</td></tr>"
    "<tr><td>1860</td></tr><tr><td>1960</td></tr></table>\n"
)


def test_gfm_annotation_leaving_merged_rows_blank_accepts_a_rowspan():
    expected = _table([["种类", "设计值", "备注"], ["钢绞线", "1720", ""], ["", "1860", "390"], ["", "1960", ""]])
    metrics = compute_table_metrics(_SPANNED_OUTPUT, expected)
    assert (metrics.cell_precision, metrics.cell_recall) == (1.0, 1.0)
    assert metrics.merged_cell_accuracy is None  # the annotation cannot say anything about spans


def test_gfm_annotation_repeating_merged_values_accepts_a_rowspan():
    expected = _table([["种类", "设计值", "备注"], ["钢绞线", "1720", "390"], ["钢绞线", "1860", "390"],
                       ["钢绞线", "1960", "390"]])
    metrics = compute_table_metrics(_SPANNED_OUTPUT, expected)
    assert (metrics.cell_precision, metrics.cell_recall) == (1.0, 1.0)
    text = compute_text_metrics(_SPANNED_OUTPUT, expected)
    assert text.char_f1 == 1.0  # a merged value counts once however it is written


def test_wrong_value_under_a_span_is_still_wrong():
    expected = _table([["种类", "设计值", "备注"], ["钢绞线", "1720", ""], ["", "1860", "391"], ["", "1960", ""]])
    assert compute_table_metrics(_SPANNED_OUTPUT, expected).cell_recall < 1.0


def test_annotation_with_spans_still_requires_the_same_spans():
    expected = _SPANNED_OUTPUT
    flat = _table([["种类", "设计值", "备注"], ["钢绞线", "1720", "390"], ["", "1860", ""], ["", "1960", ""]])
    metrics = compute_table_metrics(flat, expected)
    assert metrics.merged_cell_accuracy == 0.0 and metrics.cell_recall < 1.0


def test_metric_version_is_2_7():
    from parserx.eval.metrics import METRIC_VERSION

    assert METRIC_VERSION == "2.7"


def test_inline_emphasis_is_not_text_and_is_scored_apart():
    # R3, metric 2.4: bold and underline marks do not change the text scores; the spans are matched on their own
    from parserx.eval.metrics import evaluate_markdown

    plain = evaluate_markdown("项目负责人：邹贻军。发件人：Apple", "项目负责人：邹贻军。**发件人**：Apple")
    marked = evaluate_markdown("项目负责人：<u>邹贻军</u>。**发件人**：Apple", "项目负责人：邹贻军。**发件人**：Apple")
    assert plain.text.char_f1 == marked.text.char_f1 == 1.0
    assert (marked.formatting.bold.matched, marked.formatting.bold.expected) == (1, 1)
    assert (marked.formatting.underline.output, marked.formatting.underline.expected) == (1, 0)


def test_headings_skip_fenced_code_and_compare_after_nfkc():
    # Q68 (metric 2.2): a shell comment inside a code block is not a heading; "9．监督" is "9.监督"
    from parserx.eval.metrics import compute_heading_metrics

    expected = "# 换盘步骤\n\n```bash\n# 参考命令如下\nparted /dev/sdf\n```\n\n## 9.监督\n"
    output = "# 换盘步骤\n\n\\# 参考命令如下\n\nparted /dev/sdf\n\n## 9．监督\n"
    metrics = compute_heading_metrics(output, expected)
    assert metrics.expected_count == 2 and metrics.detected_count == 2 and metrics.f1 == 1.0
    assert compute_heading_metrics("~~~\n# not a title\n~~~\n# Title\n", "# Title\n").f1 == 1.0
