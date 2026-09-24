"""Joining visually wrapped lines into one paragraph (output contract, guide §4.5)."""

from __future__ import annotations

import unicodedata


def _is_wide(ch: str) -> bool:
    """CJK ideographs, kana, hangul and full-width punctuation: no spaces between words."""
    return unicodedata.east_asian_width(ch) in ("W", "F")


def join_wrapped(lines: list[str]) -> str:
    """Join wrapped lines: a break between two wide characters disappears, any other becomes a space
    (pandoc's ``east_asian_line_breaks`` convention)."""
    parts = [line.strip() for line in lines if line.strip()]
    if not parts:
        return ""
    out = parts[0]
    for part in parts[1:]:
        out += part if _is_wide(out[-1]) and _is_wide(part[0]) else " " + part
    return out
