"""Office Math (OMML) to LaTeX (Q9, 2026-09-26): Word's equations keep their structure in the output.

Covers the elements of ECMA-376 Part 1 §22.1 that carry structure: fractions, sub- and superscripts, radicals,
delimiters, n-ary operators (sums, integrals), functions, accents and bars, limits, group characters, matrices,
equation arrays, boxes.  Text runs are kept as they are (Unicode symbols such as ≈, θ, ∑ are valid in KaTeX and
MathJax); only LaTeX's special characters are escaped, and the names of the usual functions (sin, tan, log …)
become their commands.  An element not listed contributes its text, so nothing is lost.
"""

from __future__ import annotations

import re

M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
_SPECIAL = {"\\": r"\backslash ", "{": r"\{", "}": r"\}", "#": r"\#", "%": r"\%", "&": r"\&", "$": r"\$",
            "_": r"\_", "^": r"\^{}", "~": r"\sim "}
_FUNCTIONS = ("arcsin", "arccos", "arctan", "sinh", "cosh", "tanh", "sin", "cos", "tan", "cot", "sec", "csc", "log",
              "ln", "lg", "exp", "lim", "max", "min", "sup", "inf", "det", "deg", "arg")
_FUNCTION = re.compile(r"(?<![A-Za-z\\])(" + "|".join(_FUNCTIONS) + r")(?![A-Za-z])")
_NARY = {"∑": r"\sum", "∏": r"\prod", "∐": r"\coprod", "∫": r"\int", "∬": r"\iint", "∭": r"\iiint", "∮": r"\oint",
         "⋃": r"\bigcup", "⋂": r"\bigcap", "⋁": r"\bigvee", "⋀": r"\bigwedge"}
_ACCENT = {"̂": r"\hat", "̃": r"\tilde", "̄": r"\bar", "̇": r"\dot", "̈": r"\ddot", "⃗": r"\vec", "̌": r"\check",
           "́": r"\acute", "̀": r"\grave", "̆": r"\breve"}
_DELIMITER = {"(": "(", ")": ")", "[": "[", "]": "]", "{": r"\{", "}": r"\}", "|": "|", "‖": r"\|", "⟨": r"\langle",
              "⟩": r"\rangle", "⌈": r"\lceil", "⌉": r"\rceil", "⌊": r"\lfloor", "⌋": r"\rfloor", "": "."}


def _m(tag: str) -> str:
    return f"{{{M}}}{tag}"


def _local(node) -> str:
    return node.tag.split("}", 1)[1] if isinstance(node.tag, str) and "}" in node.tag else ""


def _prop(node, pr: str, name: str, default: str | None = None) -> str | None:
    props = node.find(_m(pr))
    if props is None:
        return default
    item = props.find(_m(name))
    if item is None:
        return default
    return item.get(_m("val"), default)


def _child(node, name: str):
    return node.find(_m(name))


def _arg(node, name: str) -> str:
    part = _child(node, name)
    return _children(part) if part is not None else ""


def _group(latex: str) -> str:
    """Braces only where they are needed: one symbol stands alone."""
    latex = latex.strip()
    return latex if len(latex) == 1 else f"{{{latex}}}"


def _children(node) -> str:
    return "".join(_convert(child) for child in node)


def _text(run) -> str:
    raw = "".join(t.text or "" for t in run.iter(_m("t")))
    escaped = "".join(_SPECIAL.get(ch, ch) for ch in raw)
    return _FUNCTION.sub(r"\\\1 ", escaped)


def _convert(node) -> str:
    name = _local(node)
    if name == "r":
        return _text(node)
    if name == "f":
        num, den = _arg(node, "num"), _arg(node, "den")
        kind = _prop(node, "fPr", "type")
        if kind == "lin":
            return f"{num}/{den}"
        if kind == "noBar":
            return rf"\genfrac{{}}{{}}{{0pt}}{{}}{{{num}}}{{{den}}}"
        return rf"\frac{{{num}}}{{{den}}}"
    if name == "sSup":
        return f"{_group(_arg(node, 'e'))}^{_group(_arg(node, 'sup'))}"
    if name == "sSub":
        return f"{_group(_arg(node, 'e'))}_{_group(_arg(node, 'sub'))}"
    if name == "sSubSup":
        return f"{_group(_arg(node, 'e'))}_{_group(_arg(node, 'sub'))}^{_group(_arg(node, 'sup'))}"
    if name == "sPre":
        return f"{{}}_{{{_arg(node, 'sub')}}}^{{{_arg(node, 'sup')}}}{{{_arg(node, 'e')}}}"
    if name == "rad":
        degree = _arg(node, "deg")
        hidden = _prop(node, "radPr", "degHide") in ("1", "on", "true")
        return rf"\sqrt[{degree}]{{{_arg(node, 'e')}}}" if degree and not hidden else rf"\sqrt{{{_arg(node, 'e')}}}"
    if name == "d":
        begin = _prop(node, "dPr", "begChr", "(")
        end = _prop(node, "dPr", "endChr", ")")
        separator = _prop(node, "dPr", "sepChr", "|")
        items = [_children(e) for e in node.findall(_m("e"))]
        sep = _DELIMITER.get(separator, separator)
        return rf"\left{_DELIMITER.get(begin, begin)} {sep.join(items) if len(items) > 1 else ''.join(items)} " \
               rf"\right{_DELIMITER.get(end, end)}"
    if name == "nary":
        char = _prop(node, "naryPr", "chr", "∫")
        operator = _NARY.get(char, char)
        sub, sup = _arg(node, "sub"), _arg(node, "sup")
        limits = (f"_{_group(sub)}" if sub else "") + (f"^{_group(sup)}" if sup else "")
        return f"{operator}{limits} {_group(_arg(node, 'e'))}"
    if name == "func":
        return f"{_arg(node, 'fName')}{_group(_arg(node, 'e'))}"
    if name == "acc":
        char = _prop(node, "accPr", "chr", "̂")
        return f"{_ACCENT.get(char, r'\hat')}{{{_arg(node, 'e')}}}"
    if name == "bar":
        top = _prop(node, "barPr", "pos", "bot") == "top"
        return (r"\overline" if top else r"\underline") + f"{{{_arg(node, 'e')}}}"
    if name == "groupChr":
        char = _prop(node, "groupChrPr", "chr", "⏟")
        below = _prop(node, "groupChrPr", "pos", "bot") != "top"
        if char in ("⏟", "︸"):
            return rf"\underbrace{{{_arg(node, 'e')}}}"
        if char in ("⏞", "︷"):
            return rf"\overbrace{{{_arg(node, 'e')}}}"
        return (r"\underset" if below else r"\overset") + f"{{{char}}}{{{_arg(node, 'e')}}}"
    if name == "limLow":
        return f"{{{_arg(node, 'e')}}}_{{{_arg(node, 'lim')}}}"
    if name == "limUpp":
        return f"{{{_arg(node, 'e')}}}^{{{_arg(node, 'lim')}}}"
    if name == "m":
        rows = [" & ".join(_children(e) for e in row.findall(_m("e"))) for row in node.findall(_m("mr"))]
        return r"\begin{matrix}" + r" \\ ".join(rows) + r"\end{matrix}"
    if name == "eqArr":
        return r"\begin{aligned}" + r" \\ ".join(_children(e) for e in node.findall(_m("e"))) + r"\end{aligned}"
    if name in ("box", "borderBox", "phant", "e", "num", "den", "sub", "sup", "deg", "lim", "fName", "oMath"):
        return _arg(node, "e") if name in ("box", "borderBox", "phant") else _children(node)
    if name.endswith("Pr") or name in ("ctrlPr", "argPr"):
        return ""  # properties carry no content
    return _children(node)  # an element not listed: its content, nothing lost


def omml_to_latex(node) -> str:
    """LaTeX of an ``m:oMath`` or ``m:oMathPara`` element (without delimiters)."""
    if _local(node) == "oMathPara":
        parts = [omml_to_latex(math) for math in node.findall(_m("oMath"))]
        return r" \\ ".join(p for p in parts if p)
    return re.sub(r"\s+", " ", _children(node)).strip()
