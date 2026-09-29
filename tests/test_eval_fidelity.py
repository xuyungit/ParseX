"""Checks the character metrics cannot see (metric 2.5, R5 of the vision-first review): each category of
meaning-changing difference is counted, and each notation of the same content is not."""

import pytest

from parserx.eval.key_content import compute_key_content_errors
from parserx.eval.omission import compute_omission

# ── Signs ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("expected, output", [
    ("数值为 10", "数值为 -10"),
    ("温度降到-5℃以下", "温度降到5℃以下"),
    ("The offset is -0.25 mm.", "The offset is 0.25 mm."),
    ("$a = -3$", "$a = 3$"),
    ("误差 ±0.5", "误差 0.5"),
])
def test_a_flipped_or_lost_sign_is_counted(expected, output):
    errors = compute_key_content_errors(output, expected)
    assert errors.missing["sign"] + errors.extra["sign"] >= 1


@pytest.mark.parametrize("expected, output", [
    ("值为 −10", "值为 -10"),  # U+2212 and the hyphen-minus are one sign
    ("2019-2020 年度", "2019–2020 年度"),  # a range, not a sign
    ("ISSN 0258-2724", "ISSN 0258-2724"),
    ("$x-1$", "$x - 1$"),  # subtraction
    ("刚度 N m<sup>-1</sup>", "刚度 N m⁻¹"),  # an exponent is a script, not a signed number
    ("$y=-1$", "$y = - 1$"),  # spacing in math is not writing
])
def test_notations_of_one_sign_are_not_errors(expected, output):
    assert compute_key_content_errors(output, expected).missing["sign"] == 0
    assert compute_key_content_errors(output, expected).extra["sign"] == 0


# ── Units ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("expected, output", [
    ("长 10 mm", "长 10 m"),  # the unit changed
    ("面积 25 m²", "面积 25 m"),  # its exponent dropped
    ("速度 3 m/s", "速度 3 m"),  # a compound unit cut short
])
def test_a_changed_unit_is_counted(expected, output):
    errors = compute_key_content_errors(output, expected)
    assert errors.missing["unit"] == 1 and errors.extra["unit"] == 1


def test_notations_of_one_unit_exponent_are_equal():
    assert compute_key_content_errors("面积 25 m<sup>2</sup>", "面积 25 m²").missing["unit"] == 0


@pytest.mark.parametrize("expected, output", [
    ("$F = 12 m x' + 3 l m$", "$F=12mx'+3lm$"),  # spacing in math is not writing
    ("$$\\Delta = 4 m^{2} z$$", "$$\\Delta=4m^2z$$"),
])
def test_products_in_math_are_not_units(expected, output):
    errors = compute_key_content_errors(output, expected)
    assert errors.missing["unit"] == 0 and errors.extra["unit"] == 0


def test_a_unit_written_in_math_and_in_prose_is_one_unit():
    errors = compute_key_content_errors("其刚度 k9 = 30 MN/m²，按", "其刚度 $k_9 = 30\\text{ MN}/\\text{m}^2$，按")
    assert errors.missing["unit"] == 0 and errors.extra["unit"] == 0
    errors = compute_key_content_errors("其刚度 $k_9 = 30\\,\\mathrm{kN}$", "其刚度 $k_9 = 30\\text{ MN}$")
    assert errors.missing["unit"] == 1 and errors.extra["unit"] == 1


def test_a_unit_in_prose_beside_math_is_still_counted():
    errors = compute_key_content_errors("取 $k_1$ 为 6 kN，长 10 m", "取 $k_1$ 为 6 MN，长 10 m")
    assert errors.missing["unit"] == 1 and errors.extra["unit"] == 1


# ── Super- and subscripts (not folded by NFKC) ──────────────────────────


@pytest.mark.parametrize("expected, output", [
    ("$x^2$", "$x_2$"),
    ("β₁ 与 β₂", "β¹ 与 β²"),
    ("质量为 m<sub>0</sub>", "质量为 m<sup>0</sup>"),
])
def test_a_superscript_and_subscript_swapped_is_counted(expected, output):
    errors = compute_key_content_errors(output, expected)
    assert errors.missing["script"] >= 1 and errors.extra["script"] >= 1


@pytest.mark.parametrize("expected, output", [
    ("面积 10 m²", "面积 10 m2"),  # flattened: NFKC would make these equal
    ("H₂O", "H2O"),
])
def test_a_flattened_script_is_counted(expected, output):
    assert compute_key_content_errors(output, expected).missing["script"] == 1


def test_an_invented_script_is_counted():
    errors = compute_key_content_errors("第 ₅₀卷，₂₀₁₅年", "第 50 卷，2015 年")
    assert errors.extra["script"] == 2


@pytest.mark.parametrize("expected, output", [
    ("$x^{2}$", "x²"),
    ("$\\beta_{1}$", "β₁"),
    ("k<sub>10</sub>", "$k_{10}$"),
    ("[K]<sup>-1</sup>", "[K]⁻¹"),
    ("自振频率<sup>[1]</sup>", "自振频率[1]"),  # a reference mark in brackets is notation
    ("$x'$", "$x^{\\prime}$"),  # a prime is notation
    ("$A^\\text{T}$", "$A^{\\mathrm{T}}$"),  # a command takes its group
    ("$\\eta_{_{k}}$", "$\\eta_{k}$"),  # a script of an empty base
    ("with $ \\mathrm{CH_{2} Cl_{2}} $ afforded", "with CH₂Cl₂ afforded"),  # spaces inside the dollars
])
def test_notations_of_one_script_are_not_errors(expected, output):
    errors = compute_key_content_errors(output, expected)
    assert errors.missing["script"] == 0 and errors.extra["script"] == 0


def test_prices_are_not_math():
    # between two prices "$…$" is not math: its underscore is no subscript
    assert compute_key_content_errors("costs $12 or $15_2 each", "costs $12 or $15 2 each").total == 0


# ── Attribution ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("expected, output", [
    ("甲为 10，乙为 20", "乙为 10，甲为 20"),
    ("A 组 15 人；B 组 30 人", "B 组 15 人；A 组 30 人"),
    ("the width is 4 m, the height is 9 m", "the height is 4 m, the width is 9 m"),
])
def test_values_swapped_between_objects_are_counted(expected, output):
    errors = compute_key_content_errors(output, expected)
    assert errors.missing["number"] == 0 and errors.extra["number"] == 0  # the numbers alone look right
    assert errors.missing["attribution"] == 2


def test_the_same_values_under_the_same_labels_are_not_errors():
    text = "甲为 10，乙为 20。the width is 4 m"
    assert compute_key_content_errors(text.replace("，", ", "), text).missing["attribution"] == 0


# ── Glyphs without a character ──────────────────────────────────────────


def test_private_use_and_replacement_characters_are_counted():
    errors = compute_key_content_errors("multigirder �", "multi-girder")
    assert errors.extra["unmapped"] == 2


# ── Omission ────────────────────────────────────────────────────────────

_PARAGRAPHS = [
    "第一段介绍了桥梁的基本情况与本文研究的主要目的。",
    "第二段给出有限元模型的建立过程以及各部分的参数取值。",
    "第三段比较了修正前后的计算结果并讨论了误差的来源。",
    "第四段总结全文的主要结论并指出今后需要改进的方向。",
]


def test_a_whole_paragraph_missing_is_counted():
    expected = "\n\n".join(_PARAGRAPHS)
    output = "\n\n".join(_PARAGRAPHS[:2] + _PARAGRAPHS[3:])
    omission = compute_omission(output, expected)
    assert omission.lost_blocks == 1 and omission.lost_runs == 1
    assert len(_PARAGRAPHS[2]) - 2 <= omission.lost_chars <= len(_PARAGRAPHS[2])  # its closing mark "。" is found
    assert omission.added_runs == 0


def test_a_sentence_missing_inside_a_paragraph_is_a_lost_run():
    expected = "".join(_PARAGRAPHS)
    output = "".join(_PARAGRAPHS[:1] + _PARAGRAPHS[2:])
    omission = compute_omission(output, expected)
    assert omission.lost_blocks == 0 and omission.lost_runs == 1


def test_scattered_character_errors_are_not_omissions():
    expected = "\n\n".join(_PARAGRAPHS)
    output = expected.replace("桥梁", "桥粱").replace("误差", "误羞")
    omission = compute_omission(output, expected)
    assert (omission.lost_runs, omission.added_runs) == (0, 0)


def test_notations_of_one_formula_are_not_omissions():
    expected = "由式可得 $\\alpha_{i} + \\beta^{2} = \\gamma_{ij}$，其中各量均为无量纲的系数。"
    output = "由式可得 αᵢ + β² = γᵢⱼ，其中各量均为无量纲的系数。"
    omission = compute_omission(output, expected)
    assert (omission.lost_runs, omission.added_runs) == (0, 0)


def test_notations_of_one_prime_are_not_omissions():
    formula = "24 m^{2} z_{1}' z_{2}' - 6 m l z_{1}' z_{2}' + 24 m x' z_{1}' z_{2}' (12 l z_{1}' z_{2}' E I \\delta_{11})^{-1}"
    expected = f"由此得到 $$ {formula} $$ 其余各式同理可以求得。"
    output = "由此得到 $$ " + formula.replace("'", "^{\\prime}").replace(" ", "") + " $$ 其余各式同理可以求得。"
    omission = compute_omission(output, expected)
    assert (omission.lost_runs, omission.added_runs) == (0, 0)


def test_a_letter_after_a_line_break_in_math_is_read():
    expected = "$$\\begin{aligned}f_{ij}&=w_{j}-e_{j}\\\\f_{ij}&=0\\end{aligned}$$"
    output = "$$\\begin{aligned}f_{ij}&=w_{j}-e_{j}\\\\ij&=0\\end{aligned}$$"  # the second f lost
    assert compute_omission(output, expected).lost_runs == 0  # a single letter is no run …
    from parserx.eval.formulas import characters
    assert "f" in characters("\\\\f_{ij}")  # … but it is a character, not the command \\f


def test_invented_text_is_an_added_run():
    expected = "\n\n".join(_PARAGRAPHS)
    output = expected + "\n\n模型自己写出的一段原文里没有的内容，不能算作原文。"
    omission = compute_omission(output, expected)
    assert omission.added_runs == 1 and omission.lost_runs == 0
