"""Key-content error counts (metric version 2.5, guide §9.2 item 4; R5 of the vision-first review).

Tokens whose corruption changes meaning even when character scores barely move.  Each kind is extracted in reading
order from both sides; the token sequences are aligned by LCS and the unaligned tokens are reported as missing
(expected only) or extra (output only):

- ``number``, ``unit`` (a number with its unit, the unit's exponent and a compound unit: ``25 m²``, ``3 m/s``),
  ``negation``, ``date``;
- ``sign``: a number written with a sign (``-10``, ``+5``, ``±0.5``) — a sign directly before the digits, itself
  not after a Latin letter, a digit or a closing bracket (``2019-2020``, ``x-1`` are not signs); the spellings of
  the minus sign are one sign.  Numbers inside a script are left to ``script``;
- ``script``: super- and subscripts as a reader sees them, not folded by NFKC.  Unicode (``x²``), HTML
  (``<sup>2</sup>``) and LaTeX in math (``$x^{2}$``) are one notation: a token is the position and the content
  (``^2``, ``_2``).  A mark in square brackets (``<sup>[3]</sup>``: a reference, the brackets already set it apart)
  and primes (``x'``, ``x^{\\prime}``) are notation, not tokens;
- ``unmapped``: private-use characters and U+FFFD — a glyph whose character the file does not give;
- ``attribution``: a value under another object's label.  The numbers of the prose (math and scripts are left to
  the formula metric and ``script``) are aligned by value as for ``number``; an aligned pair counts when its two
  labels differ and each is a label of some number on the other side (``甲为 10，乙为 20`` against
  ``乙为 10，甲为 20``: two).  A label changed by a character error or a line-end hyphen is no other object's
  label: it is left to the character metrics.  A label is the last ``LABEL_WORDS`` words before the number in its
  clause (a Han or kana character is one word); a number without one is not counted.  Counted as missing only.
  Values in tables are left to the table metric, which compares cells by position and header.

Before 2.5 the text was folded by NFKC first: ``x²`` read as ``x2``, and ``10²`` as the number 102.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from rapidfuzz.distance import LCSseq

from parserx.eval.formulas import characters
from parserx.eval.normalize import canonicalize

KINDS = ("number", "unit", "negation", "date", "sign", "script", "attribution", "unmapped")
LABEL_WORDS = 3  # enough to tell "the width is" from "the height is", "A 组" from "B 组"; a measurement choice

_DATE_RE = re.compile(
    r"(?P<y>\d{4})\s*年\s*(?P<m>\d{1,2})\s*月(?:\s*(?P<d>\d{1,2})\s*日)?"
    r"|(?P<y2>\d{4})[-/.](?P<m2>\d{1,2})[-/.](?P<d2>\d{1,2})(?!\d)"
)
_NUMBER = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
_CJK_UNITS = (
    "万元 亿元 千米 厘米 毫米 公里 千克 公斤 毫升 分钟 小时 平方米 立方米 "
    "万 亿 元 米 克 吨 升 秒 天 年 月 日 次 片 个 件 台 套 张 页 度 倍 人 项 条 位 岁 块 分"
).split()
_ATOM = "|".join(
    [re.escape(u) for u in sorted(_CJK_UNITS, key=len, reverse=True)]
    + [r"%", r"‰", r"℃", r"°C?", r"[A-Za-zμΩ]{1,4}(?![A-Za-z])"]
)
_EXPONENT = r"\^\{?[-+]?\d+\}?"
_UNIT = rf"(?:{_ATOM})(?:{_EXPONENT})?(?:\s?[/·⋅]\s?(?:{_ATOM})(?:{_EXPONENT})?){{0,2}}(?!/)"  # not a path
_NUMBER_RE = re.compile(_NUMBER)
_UNIT_RE = re.compile(rf"(?P<n>{_NUMBER})\s?(?P<u>{_UNIT})")
_NEGATION_RE = re.compile(r"[不没无未非否勿莫]|\b(?:not|no|never|none|without|cannot)\b|n't", re.IGNORECASE)

# ── Scripts ─────────────────────────────────────────────────────────────

_SUPERSCRIPTS: dict[str, str] = {}
_SUBSCRIPTS: dict[str, str] = {}
for _cp in range(0x80, 0x10000):
    _decomposition = unicodedata.decomposition(chr(_cp))
    if _decomposition.startswith("<super>"):
        _SUPERSCRIPTS[chr(_cp)] = unicodedata.normalize("NFKC", chr(_cp))
    elif _decomposition.startswith("<sub>"):
        _SUBSCRIPTS[chr(_cp)] = unicodedata.normalize("NFKC", chr(_cp))
_UNICODE_SCRIPT_RE = re.compile(
    "[" + re.escape("".join(_SUPERSCRIPTS)) + "]+|[" + re.escape("".join(_SUBSCRIPTS)) + "]+")
_HTML_SCRIPT_RE = re.compile(r"<(sup|sub)>(.*?)</\1>", re.IGNORECASE | re.DOTALL)
# math: $$…$$, \[…\], \(…\), and $…$ paired in order within a paragraph as TeX does; a closing dollar is not
# followed by a digit (between two prices is not math)
_MATH_RE = re.compile(r"\$\$(.+?)\$\$|\\\[(.+?)\\\]|\\\((.+?)\\\)"
                      r"|(?<![\\$])\$(?!\$)((?:[^$\n\\]|\\.|\n(?![ \t]*\n))+?)(?<!\\)\$(?![\d$])", re.DOTALL)
_COMMAND_RE = re.compile(r"\\(?:[A-Za-z]+|.)")
_TAG_RE = re.compile(r"<[^>]+>")
_MINUS = "−‐‑–﹣－"
_REFERENCE_RE = re.compile(r"^[\[［][\d\s,，、\-–—~]*[\]］]$")
_SCRIPT_KEEP = set("+-^_'=,.")


@dataclass(frozen=True)
class _Script:
    start: int
    end: int
    token: str | None  # None: notation only (a bracketed reference, primes, nothing readable)
    text: str = ""  # its content as plain text (Unicode and HTML scripts)


def _script_token(position: str, raw: str) -> str | None:
    raw = raw.strip()
    if _REFERENCE_RE.match(re.sub(r"[{}\s]", "", _TAG_RE.sub("", raw))):
        return None
    text = characters(_TAG_RE.sub("", raw).replace("\\prime", "'"))
    text = unicodedata.normalize("NFKC", text).translate({ord(c): "-" for c in _MINUS})
    content = "".join(c for c in text if c.isalnum() or c in _SCRIPT_KEEP).replace("'", "")
    # a marker left without content (a prime's) goes; a script of an empty base (``_{_{k}}``) is the script itself
    content = re.sub(r"[\^_](?=[\^_]|$)", "", content).lstrip("^_")
    return position + content if content else None


def _group_end(text: str, start: int) -> int:
    """The index after the brace group opening at *start* (the text's end when it never closes)."""
    depth, end = 0, start
    while end < len(text):
        if text[end] == "\\":
            end += 2
            continue
        depth += {"{": 1, "}": -1}.get(text[end], 0)
        end += 1
        if depth == 0:
            break
    return min(end, len(text))


def _latex_scripts(math: str, offset: int) -> list[_Script]:
    found = []
    i = 0
    while i < len(math):
        if math[i] == "\\":
            i += len(_COMMAND_RE.match(math, i).group(0))
            continue
        if math[i] not in "^_":
            i += 1
            continue
        j = i + 1
        while j < len(math) and math[j].isspace():
            j += 1
        if j >= len(math):
            break
        if math[j] == "{":
            end = _group_end(math, j)
            raw = math[j + 1:end - 1]
        elif math[j] == "\\":  # a command with the groups it takes: ^\prime, ^\text{T}, _\frac{1}{2}
            end = j + len(_COMMAND_RE.match(math, j).group(0))
            while end < len(math) and math[end] == "{":
                end = _group_end(math, end)
            raw = math[j:end]
        else:
            end, raw = j + 1, math[j]
        found.append(_Script(offset + i, offset + end, _script_token("^" if math[i] == "^" else "_", raw)))
        i = end
    return found


def _scripts(text: str) -> list[_Script]:
    """Every super- and subscript of *text* in reading order."""
    found = [_Script(m.start(), m.end(), _script_token("^" if m.group(1).lower() == "sup" else "_", m.group(2)),
                     _TAG_RE.sub("", m.group(2)))
             for m in _HTML_SCRIPT_RE.finditer(text)]
    for m in _UNICODE_SCRIPT_RE.finditer(text):
        position = "^" if m.group(0)[0] in _SUPERSCRIPTS else "_"
        table = _SUPERSCRIPTS if position == "^" else _SUBSCRIPTS
        content = "".join(table[c] for c in m.group(0))
        found.append(_Script(m.start(), m.end(), _script_token(position, content), content))
    for m in _MATH_RE.finditer(text):
        group = next(g for g in range(1, 5) if m.group(g) is not None)
        found += _latex_scripts(m.group(group), m.start(group))
    kept: list[_Script] = []
    for s in sorted(found, key=lambda s: (s.start, -s.end)):
        if not kept or s.start >= kept[-1].end:  # one inside another (<sup>²</sup>) is the outer one
            kept.append(s)
    return kept


def _marked(text: str, scripts: list[_Script], *, drop: bool = False) -> str:
    """*text* with every Unicode or HTML script written as ``^{…}`` / ``_{…}`` (LaTeX already is) — or, with
    *drop*, every script removed."""
    parts, cursor = [], 0
    for s in scripts:
        parts.append(text[cursor:s.start])
        piece = text[s.start:s.end]
        if drop:
            parts.append(" " if s.token else s.text)
        elif piece[0] in "^_":
            parts.append(piece)
        else:
            parts.append(f"{s.token[0]}{{{s.text}}}" if s.token else s.text)
        cursor = s.end
    parts.append(text[cursor:])
    return "".join(parts)


# ── Signs, attribution ──────────────────────────────────────────────────

_SIGNED_RE = re.compile(rf"(?P<s>[-+±∓{_MINUS}])(?P<space>\s*)(?P<n>{_NUMBER})")
_NOT_BEFORE_SIGN = set(".)]}>|/\\'′")
_CJK_RE = re.compile(r"[぀-ヿ㐀-鿿豈-﫿]")
_WORD_RE = re.compile(r"[぀-ヿ㐀-鿿豈-﫿]|(?:(?![぀-ヿ㐀-鿿豈-﫿])"
                      r"[^\W\d_])+")
_CLAUSE_RE = re.compile(r"[,.](?!\d)|[，。；;：:！？!?、\n]")


def _signed(text: str) -> list[str]:
    """Signed numbers; inside math, spacing is not writing (``= - 1`` is ``=-1``)."""
    text = text.replace("\\pm", "±").replace("\\mp", "∓")
    math = [(m.start(), m.end()) for m in _MATH_RE.finditer(text)]
    tokens = []
    for m in _SIGNED_RE.finditer(text):
        in_math = any(start <= m.start() < end for start, end in math)
        if m.group("space") and not in_math:
            continue
        before = text[:m.start()].rstrip()[-1:] if in_math else text[m.start() - 1:m.start()]
        before = before or " "
        if before in _NOT_BEFORE_SIGN or (before.isalnum() and not _CJK_RE.match(before)):
            continue
        sign = "-" if m.group("s") in _MINUS else m.group("s")
        tokens.append(sign + _plain(m.group("n")))
    return tokens


def _labelled_numbers(text: str) -> list[tuple[str, str]]:
    """(number, label) in reading order; the label is the last words before the number in its clause."""
    found = []
    clause_start = 0
    breaks = [m.end() for m in _CLAUSE_RE.finditer(text)]
    b = 0
    for m in _NUMBER_RE.finditer(text):
        while b < len(breaks) and breaks[b] <= m.start():
            clause_start = breaks[b]
            b += 1
        words = _WORD_RE.findall(text[clause_start:m.start()])
        found.append((_plain(m.group(0)), " ".join(words[-LABEL_WORDS:]).lower()))
    return found


# ── Metric ──────────────────────────────────────────────────────────────


@dataclass
class KeyContentMetrics:
    missing: dict[str, int] = field(default_factory=lambda: dict.fromkeys(KINDS, 0))
    extra: dict[str, int] = field(default_factory=lambda: dict.fromkeys(KINDS, 0))

    @property
    def total(self) -> int:
        return sum(self.missing.values()) + sum(self.extra.values())


def extract_key_tokens(markdown: str) -> dict[str, list]:
    raw = canonicalize(markdown).text
    scripts = _scripts(raw)
    minus = {ord(c): "-" for c in _MINUS}
    text = unicodedata.normalize("NFKC", _marked(raw, scripts)).translate(minus)
    dates: list[str] = []

    def take_date(match: re.Match) -> str:
        y = match.group("y") or match.group("y2")
        m = match.group("m") or match.group("m2")
        d = match.group("d") or match.group("d2")
        dates.append(f"{int(y):04d}-{int(m):02d}" + (f"-{int(d):02d}" if d else ""))
        return " " * len(match.group(0))

    text = _DATE_RE.sub(take_date, text)
    without_scripts = _DATE_RE.sub(lambda m: " " * len(m.group(0)),
                                   unicodedata.normalize("NFKC", _marked(raw, scripts, drop=True)))
    return {
        "number": [_plain(m.group(0)) for m in _NUMBER_RE.finditer(text)],
        "unit": [_plain(m.group("n")) + re.sub(r"[{}\s]", "", m.group("u")) for m in _UNIT_RE.finditer(text)],
        "negation": [m.group(0).lower() for m in _NEGATION_RE.finditer(text)],
        "date": dates,
        "sign": _signed(without_scripts),
        "script": [s.token for s in scripts if s.token],
        "labelled": _labelled_numbers(_MATH_RE.sub("\n", without_scripts)),
        "unmapped": [c for c in raw if 0xE000 <= ord(c) <= 0xF8FF or ord(c) >= 0xF0000 or c == "�"],
    }


def compute_key_content_errors(output: str, expected: str) -> KeyContentMetrics:
    out_tokens = extract_key_tokens(output)
    exp_tokens = extract_key_tokens(expected)
    metrics = KeyContentMetrics()
    for kind in KINDS:
        if kind == "attribution":
            metrics.missing[kind] = _misattributed(exp_tokens["labelled"], out_tokens["labelled"])
            continue
        common = LCSseq.similarity(exp_tokens[kind], out_tokens[kind])
        metrics.missing[kind] = len(exp_tokens[kind]) - common
        metrics.extra[kind] = len(out_tokens[kind]) - common
    return metrics


def _misattributed(expected: list[tuple[str, str]], output: list[tuple[str, str]]) -> int:
    """Numbers aligned by value whose labels differ, where each side's label is a label on the other side too."""
    exp_labels = {label for _, label in expected}
    out_labels = {label for _, label in output}
    blocks = LCSseq.editops([n for n, _ in expected], [n for n, _ in output]).as_matching_blocks()
    pairs = [(expected[b.a + k][1], output[b.b + k][1]) for b in blocks for k in range(b.size)]
    return sum(e != o and e in out_labels and o in exp_labels for e, o in pairs if e and o)


def _plain(number: str) -> str:
    return number.replace(",", "")
