"""The characters of LaTeX, as a text layer would hold them: what the formula readings are compared on (Q70)."""

from __future__ import annotations

import re
import unicodedata

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


# Mathematics opens and closes on these (``$`` and ``$$`` toggle); ``\$`` is a dollar sign.
_DELIMITERS = {"\\(": True, "\\[": True, "\\)": False, "\\]": False}
_TAG = re.compile(r"<(/?)(\w*)[^>]*>")
_DELIMITED = re.compile(r"(?<!\\)\$|\\[(\[]")


def placed(text: str, math: bool = False) -> list[tuple[str, str]]:
    """The characters of *text* (LaTeX commands as theirs, ``characters``; HTML tags none), each with the script it
    is written in — "sub", "sup" or "" — by ``_`` and ``^`` in mathematics, a Unicode script form (² as 2) or an
    HTML ``<sub>``/``<sup>``; in a script inside a script, the inner one.  What a correction moving a character into or
    out of a script changes (``N_o`` is not ``No``).  *math*: the text is mathematics (a formula block's LaTeX) —
    unless it has delimiters of its own, which say where."""
    out: list[tuple[str, str]] = []
    _scan(_ENVIRONMENT.sub(" ", text), "", math and not _DELIMITED.search(text), out)
    return out


def _scan(text: str, kind: str, math: bool, out: list[tuple[str, str]]) -> None:
    tags: list[str] = []
    i = 0
    while i < len(text):
        ch, pair = text[i], text[i:i + 2]
        if pair in _DELIMITERS or pair == "$$" or ch == "$":
            math = _DELIMITERS.get(pair, not math)
            i += 1 if ch == "$" and pair != "$$" else 2
            continue
        if ch == "\\":
            command = _COMMAND.match(text, i)
            if command:
                out += [(c, kind) for c in _character(command)]
            i = command.end() if command else i + 2  # \{, \$: a sign, no letter
            continue
        tag = _TAG.match(text, i) if ch == "<" else None
        if tag:
            if tag.group(2).lower() in ("sub", "sup"):
                if not tag.group(1):
                    tags.append(tag.group(2).lower())
                elif tags:
                    tags.pop()
            i = tag.end()
            continue
        if math and ch in "_^":
            argument, i = _argument(text, i + 1)
            _scan(argument, "sub" if ch == "_" else "sup", True, out)
            continue
        own = unicodedata.decomposition(ch)
        if own.startswith(("<super>", "<sub>")):
            out += [(c, "sup" if own.startswith("<super>") else "sub") for c in unicodedata.normalize("NFKC", ch)]
        else:
            out.append((ch, tags[-1] if tags else kind))
        i += 1


def _argument(text: str, i: int) -> tuple[str, int]:
    """A script's argument starting at *i* — a braced group, a command or one character — and where it ends."""
    while i < len(text) and text[i] == " ":
        i += 1
    if i >= len(text):
        return "", i
    if text[i] == "{":
        depth, j = 0, i
        while j < len(text):
            if text[j] == "\\":
                j += 2
                continue
            depth += {"{": 1, "}": -1}.get(text[j], 0)
            if depth == 0:
                return text[i + 1:j], j + 1
            j += 1
        return text[i + 1:], len(text)
    command = _COMMAND.match(text, i) if text[i] == "\\" else None
    return (command.group(0), command.end()) if command else (text[i], i + 1)


_ESCAPED = re.compile(r"\\[\\$%{}]")  # a line break, an escaped sign: no delimiter, no brace
_BEGIN_END = re.compile(r"\\(begin|end)\{([^{}]*)\}")
_LEFT_RIGHT = re.compile(r"\\(left|right)(?![A-Za-z])")


def problems(text: str) -> list[str]:
    """What keeps *text*'s mathematics from rendering: a ``$``/``$$`` left open, a brace without its pair, an
    environment or ``\\left`` without its end.  Empty when none."""
    plain = _ESCAPED.sub(" ", text)
    out = []
    display = plain.count("$$")
    if display % 2:
        out.append("a $$ is not closed")
    if plain.replace("$$", "").count("$") % 2:
        out.append("a $ is not closed")
    depth = 0
    for ch in plain:
        depth += {"{": 1, "}": -1}.get(ch, 0)
        if depth < 0:
            break
    if depth:
        out.append("a } has no {" if depth < 0 else "a { is not closed")
    stack: list[str] = []
    for kind, name in _BEGIN_END.findall(plain):
        if kind == "begin":
            stack.append(name)
        elif not stack or stack.pop() != name:
            out.append(f"\\end{{{name}}} does not close the environment open there")
            break
    out += [f"\\begin{{{name}}} is not closed" for name in stack]
    sides = [m.group(1) for m in _LEFT_RIGHT.finditer(plain)]
    if sides.count("left") != sides.count("right"):
        out.append("\\left and \\right do not pair")
    return out
