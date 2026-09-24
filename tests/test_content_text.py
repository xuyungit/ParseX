"""Joining wrapped lines (output contract, guide §4.5)."""

from parserx.content.text import join_wrapped


def test_wide_characters_join_tight_and_others_with_a_space():
    assert join_wrapped(["第一行中文", "接着一行", "and English", "words"]) == "第一行中文接着一行 and English words"


def test_a_word_broken_at_a_hyphen_keeps_the_hyphen_without_a_space():
    assert join_wrapped(["performance character-", "istics of the", "well-", "known system"]) == \
        "performance character-istics of the well-known system"
    assert join_wrapped(["range 10 -", "20", "A-", "Z"]) == "range 10 - 20 A- Z"  # not a word broken in two
