"""Characters no reader can use (plan P2-7, from round 1's ``audit_text.py``; guide §9.3 silent errors).

- **Unreadable**: private-use code points, U+FFFD and control characters.  A native text layer holds them when a
  font maps its glyphs to no real character (a dash stored as U+E011 in "0258\\ue0112724"); the Markdown then shows
  garbage where the page shows text.
- **A script found nowhere else in the document**: at most a few characters of a script the rest of the document
  does not use (katakana in a Chinese standard) — often a recognition error on a symbol or a drawing.  Latin, digits
  and Greek are everywhere in technical text and are never reported this way.

Both are hints for the agent to compare with the image; the text is not changed.
"""

from __future__ import annotations

import unicodedata
from collections import Counter

_RARE = 3  # at most this many characters of a script in the whole document
_COMMON = frozenset({"LATIN", "DIGIT", "CJK", "GREEK"})
_SCRIPTS = ("CJK", "LATIN", "DIGIT", "HIRAGANA", "KATAKANA", "HANGUL", "GREEK", "CYRILLIC", "ARABIC", "HEBREW",
            "DEVANAGARI", "BENGALI", "TAMIL", "THAI", "ETHIOPIC", "GEORGIAN", "ARMENIAN")


def suspicious_characters(text: str, document: list[str]) -> str | None:
    """What is suspicious in *text* given all the texts of the *document*, or None."""
    unreadable = Counter(ch for ch in text if is_unreadable(ch))
    if unreadable:
        n = sum(unreadable.values())
        codes = ", ".join(f"U+{ord(ch):04X}" for ch, _ in unreadable.most_common(3))
        return f"{n} unreadable character{'s' if n > 1 else ''} ({codes})"
    here = Counter(s for s in map(_script, text) if s and s not in _COMMON)
    if not here:
        return None
    everywhere = Counter(s for t in document for s in map(_script, t) if s in here)
    rare = sorted(s for s in here if everywhere[s] <= _RARE)
    if not rare:
        return None
    n = sum(here[s] for s in rare)
    return f"{n} character{'s' if n > 1 else ''} of a script found nowhere else in the document ({', '.join(rare)})"


def is_unreadable(ch: str) -> bool:
    """A character the text layer does not map to a readable one: U+FFFD, private use, a control character."""
    category = unicodedata.category(ch)
    return ch == "�" or category == "Co" or (category == "Cc" and ch not in "\n\t\r")


def _script(ch: str) -> str | None:
    name = unicodedata.name(ch, "")
    return next((s for s in _SCRIPTS if s in name), None)
