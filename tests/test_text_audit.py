"""Characters that no reader can use (plan P2-7, from round 1's audit_text.py) on synthetic text."""

from parserx.content.text_audit import suspicious_characters


def test_unmapped_glyphs_and_replacement_characters_are_reported():
    doc = ["文章编号：02582724（2015）", "正常的一段文字。"]
    assert suspicious_characters(doc[0], doc) == "1 unreadable character (U+E011)"
    assert suspicious_characters("� π �", ["� π �"]) == "2 unreadable characters (U+FFFD)"
    assert suspicious_characters(doc[1], doc) is None


def test_a_script_seen_only_a_few_times_in_the_document_is_a_hint():
    doc = ["L/4跨及梁端处腹板斜ハノノ筋", "钢筋混凝土梁的设计。" * 20]
    assert suspicious_characters(doc[0], doc) == "3 characters of a script found nowhere else in the document (KATAKANA)"
    japanese = ["これは日本語の文書です。" * 5]  # a script the document is written in is not suspicious
    assert suspicious_characters(japanese[0], japanese) is None
    greek = ["应力 σ 与应变 ε 的关系"]  # Greek letters are symbols in technical text
    assert suspicious_characters(greek[0], greek) is None
