"""Per-page scoring (vision-first E0, execution plan §4.2): a Markdown document split into its pages.

Neither the annotations nor most tools mark pages, so each page's own text is taken from the source, independent of
every tool: the PDF text layer plus the local reading of the page render (scanned pages, text in images; guide
§9.5).  The Markdown is read as the text metrics read it (``canonicalize``: tables as their rows of cell text,
images and comments out) and cut into units: lines, with a code block or display math whole.  Pages follow the
reading order, so the units get pages that never go back and share the most runs of ``ANCHOR_LEN`` characters with
them (one notation as for omission; dynamic programming).  A unit sharing none stays with the unit before it (the
first ones with the first placed).  The annotation and every output are split the same way, so page scores of
different arms compare the same pages.  Tables are scored per document: on a page they are text.
"""

from __future__ import annotations

import re
from pathlib import Path

from parserx.eval.normalize import canonicalize
from parserx.eval.omission import folded, grams

_BLANK_LINE_RE = re.compile(r"\n[ \t]*\n")
READING_DPI = 150  # the page reading's resolution (guide §9.5), so its derived cache is shared


def markdown_blocks(markdown: str) -> list[str]:
    """Blocks between blank lines; an HTML table, a fenced code block or display math with blank lines inside
    stays one block."""
    blocks: list[str] = []
    for chunk in _BLANK_LINE_RE.split(markdown):
        if blocks and _open(blocks[-1]):
            blocks[-1] += "\n\n" + chunk
        elif chunk.strip():
            blocks.append(chunk)
    return blocks


def _open(block: str) -> bool:
    return (block.lower().count("<table") > block.lower().count("</table>") or block.count("```") % 2 == 1
            or block.count("$$") % 2 == 1)


def units(markdown: str) -> list[str]:
    """The text as the metrics read it, in lines; a code block or display math is one unit."""
    found: list[str] = []
    for block in markdown_blocks(canonicalize(markdown).text):
        if "```" in block or "$$" in block:
            found.append(block)
        else:
            found += [line for line in block.split("\n") if line.strip()]
    return found


def split_pages(markdown: str, pages: list[str]) -> list[str]:
    """One Markdown per page (empty where no block goes)."""
    blocks = units(markdown)
    out: list[list[str]] = [[] for _ in pages] or [[]]
    if not pages or not blocks:
        out[0] += blocks
        return ["\n\n".join(b) for b in out]
    page_grams = [grams(folded(p)) for p in pages]
    hits = [[len(grams(folded(b)) & g) for g in page_grams] for b in blocks]
    placed = _in_order(hits)
    for i, row in enumerate(hits):  # no evidence: with the block before it (the first ones: the first placed)
        if not any(row):
            placed[i] = placed[i - 1] if i else next((placed[j] for j, r in enumerate(hits) if any(r)), 0)
    for block, page in zip(blocks, placed):
        out[page].append(block)
    return ["\n\n".join(b) for b in out]


def _in_order(hits: list[list[int]]) -> list[int]:
    """Pages for the blocks, never going back, that share the most runs with them (dynamic programming)."""
    pages = len(hits[0])
    best: list[list[int]] = []
    back: list[list[int]] = []
    for i, row in enumerate(hits):
        scores, froms, top, arg = [], [], -1, 0
        for p in range(pages):
            before = best[i - 1][p] if i else 0
            if before > top:
                top, arg = before, p
            scores.append(row[p] + top)
            froms.append(arg)
        best.append(scores)
        back.append(froms)
    page = max(range(pages), key=lambda p: (best[-1][p], -p))
    placed = [page]
    for i in range(len(hits) - 1, 0, -1):
        page = back[i][page]
        placed.append(page)
    return placed[::-1]


def page_texts(source: Path, cache=None) -> list[str]:
    """Each page's text as the source shows it: text layer and local reading (a PDF; other inputs are one page
    without text, scored whole)."""
    import pymupdf

    from parserx.content.scan import render_page_at
    from parserx.reading.local import LocalReader, read_cached

    if Path(source).suffix.lower() != ".pdf":
        return []
    reader = LocalReader()
    texts = []
    with pymupdf.open(source) as doc:
        for n in range(1, doc.page_count + 1):
            png, _, _ = render_page_at(doc, n, READING_DPI)
            lines = read_cached(reader, png, cache)
            texts.append(doc[n - 1].get_text() + "\n" + "\n".join(text for _, text, _ in lines))
    return texts
