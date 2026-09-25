"""Office Math to LaTeX (Q9) on hand-written OMML."""

import pytest
from lxml import etree

from parserx.content.omml import omml_to_latex

NS = 'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"'


def r(text):
    return f"<m:r><m:t>{text}</m:t></m:r>"


def math(inner):
    return etree.fromstring(f"<m:oMath {NS}>{inner}</m:oMath>")


@pytest.mark.parametrize("inner,latex", [
    (f"<m:f><m:num>{r('a')}</m:num><m:den>{r('b+c')}</m:den></m:f>", r"\frac{a}{b+c}"),
    (f"<m:sSup><m:e>{r('x')}</m:e><m:sup>{r('2')}</m:sup></m:sSup>", "x^2"),
    (f"<m:sSubSup><m:e>{r('T')}</m:e><m:sub>{r('max')}</m:sub><m:sup>{r('n')}</m:sup></m:sSubSup>", r"T_{\max}^n"),
    (f"<m:rad><m:radPr><m:degHide m:val=\"1\"/></m:radPr><m:deg/><m:e>{r('x+1')}</m:e></m:rad>", r"\sqrt{x+1}"),
    (f"<m:rad><m:deg>{r('3')}</m:deg><m:e>{r('y')}</m:e></m:rad>", r"\sqrt[3]{y}"),
    (f"<m:d><m:e>{r('a')}</m:e><m:e>{r('b')}</m:e></m:d>", r"\left( a|b \right)"),
    (f"<m:d><m:dPr><m:begChr m:val=\"[\"/><m:endChr m:val=\"]\"/></m:dPr><m:e>{r('0,1')}</m:e></m:d>",
     r"\left[ 0,1 \right]"),
    (f"<m:nary><m:naryPr><m:chr m:val=\"∑\"/></m:naryPr><m:sub>{r('i=1')}</m:sub><m:sup>{r('n')}</m:sup>"
     f"<m:e>{r('x_i')}</m:e></m:nary>", r"\sum_{i=1}^n {x\_i}"),
    (f"<m:func><m:fName>{r('sin')}</m:fName><m:e>{r('θ')}</m:e></m:func>", r"\sin θ"),
    (f"<m:acc><m:accPr><m:chr m:val=\"̄\"/></m:accPr><m:e>{r('x')}</m:e></m:acc>", r"\bar{x}"),
    (f"<m:m><m:mr><m:e>{r('1')}</m:e><m:e>{r('0')}</m:e></m:mr><m:mr><m:e>{r('0')}</m:e><m:e>{r('1')}</m:e></m:mr></m:m>",
     r"\begin{matrix}1 & 0 \\ 0 & 1\end{matrix}"),
    (r("50% &amp; #1"), r"50\% \& \#1"),
])
def test_structures(inner, latex):
    assert omml_to_latex(math(inner)) == latex


def test_unknown_elements_keep_their_text():
    assert omml_to_latex(math(f"<m:newThing>{r('k')}</m:newThing>")) == "k"


def test_docx_equations_become_inline_or_display_latex(tmp_path):
    from docx import Document
    from docx.oxml import parse_xml

    from parserx.content.docx import extract_docx

    doc = Document()
    body = doc.element.body
    w = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    body[-1].addprevious(parse_xml(
        f'<w:p {w} {NS}><w:r><w:t xml:space="preserve">Area </w:t></w:r><m:oMath>{r("A=")}'
        f'<m:sSup><m:e>{r("r")}</m:e><m:sup>{r("2")}</m:sup></m:sSup></m:oMath></w:p>'))
    body[-1].addprevious(parse_xml(f'<w:p {w} {NS}><m:oMathPara><m:oMath>'
                                   f'<m:f><m:num>{r("1")}</m:num><m:den>{r("2")}</m:den></m:f></m:oMath>'
                                   f'</m:oMathPara></w:p>'))
    path = tmp_path / "math.docx"
    doc.save(path)
    texts = [b.text for b in extract_docx(path).blocks]
    assert texts == ["Area $A=r^2$", r"$$\frac{1}{2}$$"]
