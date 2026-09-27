"""Text helpers: joining visually wrapped lines into one paragraph (output contract, guide §4.5); full-width ASCII;
radical code points read as the ideographs they stand for."""

from __future__ import annotations

import unicodedata
from collections import Counter
from functools import cache
from pathlib import Path

from parserx.ir.decision import Decision
from parserx.ir.enums import DecisionStage


def _is_wide(ch: str) -> bool:
    """CJK ideographs, kana, hangul and full-width punctuation: no spaces between words."""
    return unicodedata.east_asian_width(ch) in ("W", "F")


def join_wrapped(lines: list[str]) -> str:
    """Join wrapped lines: a break between two wide characters disappears, any other becomes a space
    (pandoc's ``east_asian_line_breaks`` convention); a word broken at a hyphen is joined without the space,
    keeping the hyphen (it cannot be told from a compound's: ``well-known``)."""
    parts = [line.strip() for line in lines if line.strip()]
    if not parts:
        return ""
    out = parts[0]
    for part in parts[1:]:
        tight = (_is_wide(out[-1]) and _is_wide(part[0])) or _hyphen_break(out, part)
        out += part if tight else " " + part
    return out


def _hyphen_break(before: str, after: str) -> bool:
    return len(before) > 1 and before[-1] == "-" and before[-2].isalpha() and after[0].islower()


# ── Full-width → half-width normalization ─────────────────────────────────

# Build translation table: full-width ASCII letters, digits, and selected
# math/bracket symbols → half-width equivalents.
# Chinese punctuation（，。：；！？）is intentionally EXCLUDED.
_FULLWIDTH_TABLE = str.maketrans(
    {
        # Digits FF10-FF19
        **{chr(0xFF10 + i): chr(0x30 + i) for i in range(10)},
        # Uppercase letters FF21-FF3A
        **{chr(0xFF21 + i): chr(0x41 + i) for i in range(26)},
        # Lowercase letters FF41-FF5A
        **{chr(0xFF41 + i): chr(0x61 + i) for i in range(26)},
        # Math and bracket symbols (NOT Chinese punctuation)
        "\uFF0B": "+",   # ＋
        "\uFF0D": "-",   # －
        "\uFF0E": ".",   # ．  (full-width full stop, NOT Chinese period 。)
        "\uFF0F": "/",   # ／
        "\uFF1D": "=",   # ＝
        "\uFF1C": "<",   # ＜
        "\uFF1E": ">",   # ＞
        "\uFF3B": "[",   # ［
        "\uFF3C": "\\",  # ＼
        "\uFF3D": "]",   # ］
        "\uFF3E": "^",   # ＾
        "\uFF3F": "_",   # ＿
        "\uFF40": "`",   # ｀
        "\uFF5B": "{",   # ｛
        "\uFF5D": "}",   # ｝
        "\uFF5E": "~",   # ～
    }
)


def normalize_fullwidth_ascii(text: str) -> str:
    """Convert full-width ASCII letters, digits, and math symbols to half-width.

    Preserves Chinese-standard full-width punctuation（，。：；！？）which
    is correct in CJK text.
    """
    return text.translate(_FULLWIDTH_TABLE)


# ── Radical code points → unified ideographs ──────────────────────────────

# Unicode's Equivalent_Unified_Ideograph property, the file as published (Unicode 18.0.0, sha256 c86c80f6…bf72).
EQUIVALENTS = Path(__file__).parent / "data" / "EquivalentUnifiedIdeograph.txt"
_RADICAL_BLOCKS = ((0x2E80, 0x2EFF), (0x2F00, 0x2FDF))  # CJK Radicals Supplement, Kangxi Radicals
UNIFIED = "unified_ideographs"  # the Decision's choice


@cache
def _radicals() -> dict[int, str]:
    table: dict[int, str] = {}
    for line in EQUIVALENTS.read_text(encoding="utf-8").splitlines():
        data = line.split("#", 1)[0].strip()
        if not data:
            continue
        points, target = (field.strip() for field in data.split(";"))
        first, _, last = points.partition("..")
        for cp in range(int(first, 16), int(last or first, 16) + 1):
            if any(lo <= cp <= hi for lo, hi in _RADICAL_BLOCKS):
                table[cp] = chr(int(target, 16))
    return table


def unify_radicals(text: str) -> str:
    """Radical code points (Kangxi Radicals, CJK Radicals Supplement) as the unified ideographs Unicode gives as their
    equivalents — for Kangxi radicals the same as NFKC.  Some fonts draw a radical and an ideograph with one glyph,
    and the text layer of a PDF made with them stores the radical: "使⽤" where the page shows "使用", so a search for
    "使用" finds nothing.  One code point for one: positions do not move.  A radical without an equivalent (⺀)
    stays, and nothing outside the two blocks changes (no NFKC)."""
    return text.translate(_radicals())


def radicals_in(text: str) -> Counter[str]:
    """The characters of *text* that ``unify_radicals`` replaces, with their counts."""
    table = _radicals()
    return Counter(ch for ch in text if ord(ch) in table)


def radicals_decision(found: Counter[str], actor: str) -> Decision | None:
    """The record of a block's replaced radicals (the characters as the source stores them), or None."""
    n = sum(found.values())
    if not n:
        return None
    pairs = ", ".join(f"{ch}→{unify_radicals(ch)}" + (f" ×{k}" if k > 1 else "") for ch, k in found.most_common())
    return Decision(stage=DecisionStage.CONTENT_SOURCE, choice=UNIFIED, actor=actor, evidence={"chars": n},
                    reason=f"{n} character{'s' if n > 1 else ''} stored as radical code point{'s' if n > 1 else ''}, "
                           f"output as the equivalent unified ideograph{'s' if n > 1 else ''} "
                           f"(Unicode Equivalent_Unified_Ideograph): {pairs}")
