"""Display formula similarity (Q70 v3, E1): paired by characters, compared in one notation."""

from parserx.eval.formulas import compute_formula_metrics, display_formulas, notation


def test_one_notation():
    assert notation(r"x^{\prime}+y'") == notation(r"x^{^{\prime}}+y^\prime") == "x'+y'"
    assert notation(r"a=b \tag{3}") == notation(r"a=b \eqno(3)") == notation(r"a=b \quad (3)") == "a=b"
    assert notation(r"\begin{aligned}\mathbf{A}&=\left(x\right)\\ b&=1\end{aligned}") == \
        notation(r"\begin{align}\boldsymbol{A} &= (x) \notag \\ b &= 1\end{align}") == "A=(x)b=1"
    assert notation(r"\beta_{1}") == notation("β_1") == "β_1"


def test_display_formulas_of_a_markdown_text():
    md = "text $x$ and\n\n$$ a=b $$\n\n\\begin{aligned} c&=d \\end{aligned}\n\n\\[ e \\]"
    assert display_formulas(md) == ["a=b", "e", "\\begin{aligned} c&=d \\end{aligned}"]


def test_formulas_are_paired_by_their_characters():
    expected = "$$ E=mc^{2} \\tag{1} $$\n\n$$ F=ma $$\n\n$$ \\sum_{i=1}^{n} x_i $$"
    output = "$$\nF=m a\n$$\n\n$$ E=mc^2 $$\n\ntext"
    metrics = compute_formula_metrics(output, expected)
    assert metrics.expected == 3 and metrics.paired == 2 and metrics.pairing == 2 / 3
    assert metrics.similarity is not None and metrics.similarity > 0.8
    assert compute_formula_metrics(output, "no formulas").expected == 0
