"""Text helpers: joining visually wrapped lines into one paragraph (output contract, guide §4.5); full-width ASCII."""

from __future__ import annotations

import unicodedata


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
