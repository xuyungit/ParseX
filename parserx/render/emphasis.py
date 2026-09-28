"""Inline emphasis in the Markdown (R3): an observation's marks written around the spans they name.

A mark names its span by its text; the span is found in the rendered paragraph ignoring whitespace (a native line
break, a space the text layer lacks), after the marks of the same kind found before it.  Bold is written
``**…**`` with its leading and trailing punctuation left outside (CommonMark does not open ``**`` before a
Chinese bracket that follows a letter); underline ``<u>…</u>``.  A paragraph with bold writes a literal ``*`` as
``\\*``.  A mark whose span is not found is carried to the next paragraph of the block, then dropped: the text is
never changed to fit a mark.
"""

from __future__ import annotations

import unicodedata

from parserx.ir.observation import Mark

_OPEN = {"bold": "**", "underline": "<u>"}
_CLOSE = {"bold": "**", "underline": "</u>"}


def emphasize(text: str, marks: list[Mark]) -> tuple[str, list[Mark]]:
    """*text* with the marks found in it written in; the marks not found."""
    index = [i for i, ch in enumerate(text) if not ch.isspace()]
    squashed = "".join(text[i] for i in index)
    cursors = {"bold": 0, "underline": 0}
    spans: list[tuple[int, int, str]] = []
    left: list[Mark] = []
    for mark in marks:
        key = "".join(mark.text.split())
        at = squashed.find(key, cursors[mark.kind]) if key else -1
        if at < 0:
            left.append(mark)
            continue
        cursors[mark.kind] = at + len(key)
        start, end = index[at], index[at + len(key) - 1] + 1
        while start < end and (text[start].isspace() or (mark.kind == "bold" and _mark_char(text[start]))):
            start += 1
        while end > start and (text[end - 1].isspace() or (mark.kind == "bold" and _mark_char(text[end - 1]))):
            end -= 1
        if start < end and not any(s < end and start < e and not (s <= start and end <= e) and
                                   not (start <= s and e <= end) for s, e, _ in spans):
            spans.append((start, end, mark.kind))
    if not spans:
        return text, left
    bold = any(kind == "bold" for _, _, kind in spans)
    out: list[str] = []
    ranked = list(enumerate(spans))
    opening = [span for _, span in sorted(ranked, key=lambda t: (t[1][0] - t[1][1], t[0]))]  # outer first
    closing = [span for _, span in sorted(ranked, key=lambda t: (t[1][1] - t[1][0], -t[0]))]  # inner first
    for i in range(len(text) + 1):
        out += [_CLOSE[k] for s, e, k in closing if e == i]
        out += [_OPEN[k] for s, e, k in opening if s == i]
        if i < len(text):
            out.append("\\*" if bold and text[i] == "*" else text[i])
    return "".join(out), left


def _mark_char(ch: str) -> bool:
    return unicodedata.category(ch)[0] in "PS"
