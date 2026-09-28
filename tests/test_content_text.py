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


def test_tildes_that_could_pair_into_strikethrough_are_escaped_outside_formulas_and_code():
    from parserx.content.text import escape_strikethrough
    from parserx.eval.normalize import canonicalize, normalize_cell
    from parserx.tables.grid import Cell, TableGrid, find_tables

    schedule = "2022.01~2022.03：市场调研；2022.04~2022.12：产品试制"
    assert escape_strikethrough(schedule) == "2022.01\\~2022.03：市场调研；2022.04\\~2022.12：产品试制"
    assert escape_strikethrough("压力 8~12MPa") == "压力 8~12MPa"  # a lone tilde cannot pair
    assert escape_strikethrough("$a~b$ 与 $c~d$ 和 `~/x`") == "$a~b$ 与 $c~d$ 和 `~/x`"
    grid = TableGrid(n_rows=2, n_cols=1, cells=[Cell(row=0, col=0, content="进度"), Cell(row=1, col=0, content=schedule)])
    assert "\\~" in grid.to_gfm() and find_tables(grid.to_gfm())[0].grid.cells[1].content == schedule
    # the evaluation reads the escaped tilde as the tilde
    assert canonicalize(escape_strikethrough(schedule)).text == canonicalize(schedule).text
    assert normalize_cell(escape_strikethrough(schedule)) == normalize_cell(schedule)


def test_a_literal_newline_between_words_of_prose_is_a_paragraph_break():
    # P5, Q136: a template printed its escaped newlines as two characters
    from parserx.content.text import literal_breaks

    assert literal_breaks(r"more information.\n2. Subject to credit approval.\n\nApple Payments") == \
        ["more information.", "2. Subject to credit approval.", "Apple Payments"]
    for kept in (r'printf("%d\n", x)', r"use \n to end a line", r"公式 $a\nb$ 与 `x\ny`", r"a \\n b"):
        assert literal_breaks(kept) == [kept]
