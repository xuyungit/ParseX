"""Tests for text cleaning processor."""

from parserx.processors.text_clean import (
    clean_control_chars,
    fix_c1_encoding,
    fix_chinese_spaces,
    normalize_whitespace,
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
