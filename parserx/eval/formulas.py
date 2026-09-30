"""Display formula similarity (Q70 v3, E1; reported, not a regression check yet).

char_f1 compares characters as written: ``x'``, ``x^{\\prime}`` and ``x^{^{\\prime}}`` are three strings for one
formula, and an equation number is a tag in one annotation and a parenthesis in another.  Here each display formula
of the annotation is paired with one of the output's by their characters (the letters and digits a reader sees,
``characters`` below), and the pair is compared in one notation:

- primes (``'``, ``^{\\prime}``, ``^{^{\\prime}}``) are ``'``; an equation number (``\\tag{n}``, ``\\eqno(n)``,
  ``\\quad (n)``, or none where the annotation writes it after the formula) is dropped, and so is ``\\notag``;
- letter commands are their letters (``\\beta`` is β), Unicode sub- and superscripts their characters (NFKC);
- markup that changes no symbol is dropped: ``\\left``/``\\right`` and size commands, font commands (``\\mathbf``,
  ``\\boldsymbol``, ``\\mathrm``, ``\\text`` …) — their arguments stay —, spacing, alignment (``&``, ``\\\\``),
  environment names (``aligned`` is ``align``), braces, dollars and whitespace.

Per document: the annotation's display formulas, how many are paired (share of the annotation's characters found
in one output formula ≥ ``PAIRED``), and the mean similarity (rapidfuzz ratio of the two notations, 0–1) over the
paired ones.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass

from rapidfuzz import fuzz

from parserx.content.text import normalize_fullwidth_ascii

_COMMAND = re.compile(r"\\\\|\\([A-Za-z]+)")  # a line break (\\) first: in "\\f" the f is a letter, not \f

# The evaluation reads LaTeX by its own tables, not the pipeline's (``content/latex.py``): a change to how the
# pipeline compares its readings must not change the measure of its output.  Both follow LaTeX's own definitions.
_LETTERS = {name: chr(code) for name, code in (
    ("alpha", 0x3B1), ("beta", 0x3B2), ("gamma", 0x3B3), ("delta", 0x3B4), ("epsilon", 0x3B5), ("varepsilon", 0x3B5),
    ("zeta", 0x3B6), ("eta", 0x3B7), ("theta", 0x3B8), ("vartheta", 0x3D1), ("iota", 0x3B9), ("kappa", 0x3BA),
    ("lambda", 0x3BB), ("mu", 0x3BC), ("nu", 0x3BD), ("xi", 0x3BE), ("pi", 0x3C0), ("rho", 0x3C1), ("sigma", 0x3C3),
    ("tau", 0x3C4), ("upsilon", 0x3C5), ("phi", 0x3C6), ("varphi", 0x3C6), ("chi", 0x3C7), ("psi", 0x3C8),
    ("omega", 0x3C9), ("Gamma", 0x393), ("Delta", 0x394), ("Theta", 0x398), ("Lambda", 0x39B), ("Xi", 0x39E),
    ("Pi", 0x3A0), ("Sigma", 0x3A3), ("Phi", 0x3A6), ("Psi", 0x3A8), ("Omega", 0x3A9), ("Upsilon", 0x3A5),
    ("varpi", 0x3D6), ("varrho", 0x3F1), ("varsigma", 0x3C2),
    ("ell", 0x2113), ("imath", 0x131), ("jmath", 0x237), ("hbar", 0x127), ("aleph", 0x2135), ("Re", 0x211C),
    ("Im", 0x2111))}
# named operators show their own name (``\min`` is the letters m, i, n)
_OPERATORS = frozenset({
    "arccos", "arcsin", "arctan", "arg", "cos", "cosh", "cot", "coth", "csc", "deg", "det", "dim", "exp", "gcd", "hom",
    "inf", "ker", "lg", "lim", "liminf", "limsup", "ln", "log", "max", "min", "Pr", "sec", "sin", "sinh", "sup", "tan",
    "tanh"})
_ENVIRONMENT = re.compile(r"\\begin\{(?:array|tabular)\}\{[^{}]*\}|\\(?:begin|end)\{[^{}]*\}")
_LENGTH = re.compile(r"(?<=\\\\)\[[^\]]*\]|\\(?:hspace|vspace)\*?\{[^{}]*\}|\\rule\{[^{}]*\}\{[^{}]*\}"
                     r"|\\(?:kern|mkern|hskip|mskip)\s*-?[\d.]+\s*[a-z]{2}")


def characters(latex: str) -> str:
    """The characters a reader sees in LaTeX: letter commands as their letters, operator commands as their names;
    other command names, environments, an array's column spec and lengths are markup (their arguments stay)."""
    latex = _LENGTH.sub(" ", _ENVIRONMENT.sub(" ", latex))
    return _COMMAND.sub(_character, latex)


def _character(match: re.Match) -> str:
    name = match.group(1)
    if name is None:  # a line break
        return " "
    return _LETTERS.get(name, name if name in _OPERATORS else "")


def one_prime(text: str) -> str:
    """Primes in one notation (``x^{\\prime}``, ``x^\\prime``, ``x^{'}``, ``x′`` are ``x'``)."""
    return _PRIME.sub("'", text)


def symbols(text: str) -> Counter[str]:
    """Letters and digits as a reader sees them (NFKC, full width folded; case kept)."""
    return Counter(ch for ch in unicodedata.normalize("NFKC", normalize_fullwidth_ascii(text)) if ch.isalnum())

PAIRED = 0.6  # share of an annotated formula's letters and digits an output formula must hold to be its pair

_DISPLAY = re.compile(r"\$\$(.+?)\$\$|\\\[(.+?)\\\]", re.S)
_BARE_ENV = re.compile(r"(?<!\$)\\begin\{(align\*?|aligned|equation\*?|gather\*?|gathered|array|cases)\}.+?"
                       r"\\end\{\1\}", re.S)
_NUMBER = re.compile(r"\\tag\*?\s*\{[^{}]*\}|\\eqno\s*[（(]?\s*[0-9.\-]+[a-z]?\s*[)）]?"
                     r"|(?:\\q?quad|~|\s)*[（(]\s*[0-9]+(?:[.\-][0-9]+)*[a-z]?\s*[)）]\s*$")
_PRIME = re.compile(r"\^\s*\{\s*\^\s*\{\s*\\prime\s*\}\s*\}|\^\s*\{\s*\\prime\s*\}|\^\s*\\prime|\\prime|\^\s*\{\s*'\s*\}|′")
_DROP = re.compile(
    r"\\(?:left|right|big|Big|bigg|Bigg|bigl|bigr|Bigl|Bigr|middle|displaystyle|textstyle|scriptstyle|"
    r"mathbf|boldsymbol|bm|mathrm|mathit|mathsf|mathtt|mathcal|mathbb|operatorname|text|textbf|textit|mbox|cal|"
    r"quad|qquad|notag|nonumber)(?![A-Za-z])|\\[,;:! ]|\\\\|\\(?:begin|end)\{[^{}]*\}(?:\{[^{}]*\})?|[{}&$\s]")


@dataclass
class FormulaMetrics:
    expected: int = 0  # the annotation's display formulas
    paired: int = 0  # of them, paired with an output formula
    similarity: float | None = None  # mean over the paired, 0–1; None when none is paired

    @property
    def pairing(self) -> float | None:
        return self.paired / self.expected if self.expected else None


def display_formulas(markdown: str) -> list[str]:
    """The display formulas of a Markdown text: ``$$…$$``, ``\\[…\\]`` and a bare environment of rows."""
    found = [next(g for g in m.groups() if g is not None) for m in _DISPLAY.finditer(markdown)]
    rest = _DISPLAY.sub(" ", markdown)
    found += [m.group(0) for m in _BARE_ENV.finditer(rest)]
    return [f.strip() for f in found if f.strip()]


def notation(latex: str, *, numbers: bool = False) -> str:
    """A formula in one notation (see the module); with *numbers*, its equation number stays (the text metrics,
    where the number is content)."""
    text = unicodedata.normalize("NFKC", latex)
    text = text if numbers else _NUMBER.sub("", text)
    text = _PRIME.sub("'", text)
    text = _COMMAND.sub(lambda m: _LETTERS.get(m.group(1), m.group(0)), text)
    return _DROP.sub("", text)


def compute_formula_metrics(output: str, expected: str) -> FormulaMetrics:
    wanted = display_formulas(expected)
    if not wanted:
        return FormulaMetrics()
    candidates = display_formulas(output)
    marks = [symbols(characters(f)) for f in candidates]
    pairs: list[tuple[float, int, int]] = []
    for i, formula in enumerate(wanted):
        own = symbols(characters(formula))
        total = sum(own.values())
        for j, other in enumerate(marks):
            share = sum((own & other).values()) / total if total else 0.0
            if share >= PAIRED:
                pairs.append((share, i, j))
    used_i: set[int] = set()
    used_j: set[int] = set()
    scores = []
    for share, i, j in sorted(pairs, key=lambda p: (-p[0], p[1], p[2])):  # the closest first, one-to-one
        if i in used_i or j in used_j:
            continue
        used_i.add(i)
        used_j.add(j)
        scores.append(fuzz.ratio(notation(wanted[i]), notation(candidates[j])) / 100)
    return FormulaMetrics(expected=len(wanted), paired=len(scores),
                          similarity=round(sum(scores) / len(scores), 4) if scores else None)
