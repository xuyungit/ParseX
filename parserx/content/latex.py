"""The characters of LaTeX, as a text layer would hold them: what the formula readings are compared on (Q70)."""

from __future__ import annotations

import re

_LETTERS = {name: chr(code) for name, code in (
    ("alpha", 0x3B1), ("beta", 0x3B2), ("gamma", 0x3B3), ("delta", 0x3B4), ("epsilon", 0x3B5), ("varepsilon", 0x3B5),
    ("zeta", 0x3B6), ("eta", 0x3B7), ("theta", 0x3B8), ("vartheta", 0x3D1), ("iota", 0x3B9), ("kappa", 0x3BA),
    ("lambda", 0x3BB), ("mu", 0x3BC), ("nu", 0x3BD), ("xi", 0x3BE), ("pi", 0x3C0), ("rho", 0x3C1), ("sigma", 0x3C3),
    ("tau", 0x3C4), ("upsilon", 0x3C5), ("phi", 0x3C6), ("varphi", 0x3C6), ("chi", 0x3C7), ("psi", 0x3C8),
    ("omega", 0x3C9), ("Gamma", 0x393), ("Delta", 0x394), ("Theta", 0x398), ("Lambda", 0x39B), ("Xi", 0x39E),
    ("Pi", 0x3A0), ("Sigma", 0x3A3), ("Phi", 0x3A6), ("Psi", 0x3A8), ("Omega", 0x3A9), ("Upsilon", 0x3A5),
    ("varpi", 0x3D6), ("varrho", 0x3F1), ("varsigma", 0x3C2),
    # letter-like symbols (ℓ is the letter l, NFKC)
    ("ell", 0x2113), ("imath", 0x131), ("jmath", 0x237), ("hbar", 0x127), ("aleph", 0x2135), ("Re", 0x211C),
    ("Im", 0x2111))}
# Operator names LaTeX prints as their own upright letters (\min is "min"): LaTeX's and amsmath's predefined ones.
_OPERATORS = frozenset({
    "arccos", "arcsin", "arctan", "arg", "cos", "cosh", "cot", "coth", "csc", "deg", "det", "dim", "exp", "gcd", "hom",
    "inf", "ker", "lg", "lim", "liminf", "limsup", "ln", "log", "max", "min", "Pr", "sec", "sin", "sinh", "sup", "tan",
    "tanh"})
# A line break (\\) first: in "\\f_{ij}" the f is a letter, not the command \f.
_COMMAND = re.compile(r"\\\\|\\([A-Za-z]+)")
_ENVIRONMENT = re.compile(r"\\begin\{(?:array|tabular)\}\{[^{}]*\}|\\(?:begin|end)\{[^{}]*\}")


def characters(latex: str) -> str:
    """LaTeX's characters, for comparing them with a text layer's: letter commands (Greek, ``\\ell`` …) as their
    letters (LaTeX's own definitions); other command names, environments and an array's column spec are markup,
    not characters (``\\left`` carries no l) — the arguments stay.  Operator names (``\\min``) print their letters; a
    line break (``\\\\``) is none."""
    return _COMMAND.sub(_character, _ENVIRONMENT.sub(" ", latex))


def _character(match: re.Match) -> str:
    name = match.group(1)
    if name is None:  # a line break
        return " "
    return f" {name} " if name in _OPERATORS else _LETTERS.get(name, "")
