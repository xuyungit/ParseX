"""Tests for text cleaning processor."""

from parserx.processors.text_clean import (
    clean_control_chars,
    fix_c1_encoding,
    fix_chinese_spaces,
    normalize_fullwidth_ascii,
    normalize_whitespace,
    simplify_latex_primes,
)


def test_fix_chinese_spaces_basic():
    assert fix_chinese_spaces("你 好 世 界") == "你好世界"


def test_fix_chinese_spaces_preserves_english():
    assert fix_chinese_spaces("Hello World 你好") == "Hello World 你好"


def test_fix_chinese_spaces_with_punctuation():
    assert fix_chinese_spaces("你好 ，世界") == "你好，世界"
    assert fix_chinese_spaces("《 标题 》") == "《标题》"


def test_fix_chinese_spaces_preserves_newlines():
    text = "第一行\n第 二 行"
    result = fix_chinese_spaces(text)
    assert result == "第一行\n第二行"


def test_fix_c1_encoding():
    # Smart quotes
    assert fix_c1_encoding("\x93hello\x94") == "\u201Chello\u201D"
    # Em dash
    assert fix_c1_encoding("\x97") == "\u2014"


def test_clean_control_chars():
    assert clean_control_chars("hello\x00world\x07") == "helloworld"
    # Preserve tabs and newlines
    assert clean_control_chars("hello\tworld\n") == "hello\tworld\n"


def test_normalize_whitespace():
    assert normalize_whitespace("hello   world") == "hello world"
    assert normalize_whitespace("hello\t\tworld") == "hello world"
    # Preserve newlines
    assert normalize_whitespace("hello  \n  world") == "hello \n world"


# ── Full-width normalization ──────────────────────────────────────────


def test_normalize_fullwidth_letters_digits():
    assert normalize_fullwidth_ascii("ＥＩδ１１") == "EIδ11"
    assert normalize_fullwidth_ascii("Ｆ") == "F"
    assert normalize_fullwidth_ascii("ａｂｃ") == "abc"


def test_normalize_fullwidth_math_symbols():
    assert normalize_fullwidth_ascii("ａ＋ｂ＝ｃ") == "a+b=c"
    assert normalize_fullwidth_ascii("ｘ＜ｙ") == "x<y"
    assert normalize_fullwidth_ascii("ｆ／ｇ") == "f/g"
    assert normalize_fullwidth_ascii("Ｆｉｇ．１") == "Fig.1"


def test_normalize_fullwidth_preserves_chinese_punctuation():
    assert normalize_fullwidth_ascii("你好，世界。") == "你好，世界。"
    assert normalize_fullwidth_ascii("测试（一）") == "测试（一）"
    assert normalize_fullwidth_ascii("问题：答案；备注！疑问？") == "问题：答案；备注！疑问？"


def test_normalize_fullwidth_mixed():
    assert normalize_fullwidth_ascii("第１章 Ｆｉｇ．１") == "第1章 Fig.1"
    assert normalize_fullwidth_ascii("ＤＩＩＬＳＲ") == "DIILSR"
    # Chinese text with interspersed full-width ASCII
    assert normalize_fullwidth_ascii("式（１６），（１７）") == "式（16），（17）"


# ── LaTeX prime simplification ────────────────────────────────────────


def test_simplify_latex_double_prime():
    assert simplify_latex_primes(r"x^{^{\prime}}") == "x'"
    assert simplify_latex_primes(r"(x^{^{\prime}})^{3}") == "(x')^{3}"


def test_simplify_latex_single_prime():
    assert simplify_latex_primes(r"x^{\prime}") == "x'"
    assert simplify_latex_primes(r"z^{\prime}EI") == "z'EI"


def test_simplify_latex_preserves_other():
    # Normal superscripts should not be affected
    assert simplify_latex_primes(r"x^{2}") == r"x^{2}"
    assert simplify_latex_primes("no primes here") == "no primes here"
