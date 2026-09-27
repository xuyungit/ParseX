"""Text helpers of the content layer: joining wrapped lines (output contract, guide §4.5), full-width forms."""

from parserx.content.text import join_wrapped


def test_wide_characters_join_tight_and_others_with_a_space():
    assert join_wrapped(["第一行中文", "接着一行", "and English", "words"]) == "第一行中文接着一行 and English words"


def test_a_word_broken_at_a_hyphen_keeps_the_hyphen_without_a_space():
    assert join_wrapped(["performance character-", "istics of the", "well-", "known system"]) == \
        "performance character-istics of the well-known system"
    assert join_wrapped(["range 10 -", "20", "A-", "Z"]) == "range 10 - 20 A- Z"  # not a word broken in two


def test_fullwidth_letters_digits_and_math_become_halfwidth_chinese_punctuation_stays():
    from parserx.content.text import normalize_fullwidth_ascii

    assert normalize_fullwidth_ascii("ＥＩδ１１ ａ＋ｂ＝ｃ ｘ＜ｙ ｆ／ｇ Ｆｉｇ．１") == "EIδ11 a+b=c x<y f/g Fig.1"
    assert normalize_fullwidth_ascii("第１章 问题：答案；备注！疑问？（一），。") == "第1章 问题：答案；备注！疑问？（一），。"


def test_radical_code_points_become_their_equivalent_unified_ideographs():
    import unicodedata

    from parserx.content.text import unify_radicals

    kangxi = "".join(chr(cp) for cp in range(0x2F00, 0x2FD6))
    assert unify_radicals(kangxi) == unicodedata.normalize("NFKC", kangxi)  # Kangxi radicals: the same as NFKC
    assert unify_radicals("使⽤⻓度，⻜机⺟") == "使用长度，飞机母"  # the supplement mostly has no NFKC mapping
    # only the two radical blocks: ⺀ has no equivalent; a stroke (㇐), a compatibility ideograph (更, NFKC 更)
    # and full-width forms stay
    assert unify_radicals("⺀㇐更（Ａ１）") == "⺀㇐更（Ａ１）"


def test_replaced_radicals_are_listed_for_the_record():
    from parserx.content.text import UNIFIED, radicals_decision, radicals_in

    found = radicals_in("使⽤⽤⻓，用")
    assert found == {"⽤": 2, "⻓": 1}
    decision = radicals_decision(found, "program:test")
    assert decision.choice == UNIFIED and decision.stage == "content_source" and decision.evidence == {"chars": 3}
    assert decision.reason.endswith("⽤→用 ×2, ⻓→长")
    assert radicals_decision(radicals_in("使用"), "program:test") is None
