"""Titles on native text from independent evidence that agrees (guide §6.8, Q56, Q72): the v2 path that replaced
adapter:v1.

A paragraph of native text (PDF text layer or DOCX) is a title when two independent kinds of evidence say so:

- **typography**: it is set apart from the document's body text — a larger size, bold where the body is regular,
  or another face (the body = the typography most of the document's text, code aside, is set in);
- **layout**: the layout detector labels its region a title — every region the paragraph lies in (PDF pages);
- **numbering**: it starts with a section number followed by words (``numbering_signature``) and is not a finished
  sentence (a numbered sentence is a list item);
- **nesting**: its dotted number extends the number of a title (``5.1`` under ``5``) — the document's own outline.

One kind alone is not enough — body text has bold phrases, list items are numbered, the detector labels short
lines; such a paragraph stays text, and the worklist's ``title_candidate`` points the agent at the ones the page
image shows.  Only one-line paragraphs are considered, unless the typography and the layout detector both take the
whole paragraph for a title (a title wrapped over two lines).  A title whose text a later title repeats is an entry
of the table of contents.

Levels: the document's title (the paragraph set largest where the document opens) is level 1; the other titles rank
by the document's own typography — larger before smaller, bold before regular — and the numbering then makes the
outline consistent (``unify_levels``).  No numbering table and no keyword list.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter

from parserx.hierarchy.legality import NUMBERING_RE, numbering_signature
from parserx.hierarchy.levels import leading_number
from parserx.ir.anchor import AssetAnchor
from parserx.ir.block import Block
from parserx.ir.enums import BlockKind, TaskKind
from parserx.ir.observation import TextStyle
from parserx.ir.state import DocumentState
from parserx.workspace.queries import HIDDEN, ordered

ACTOR = "program:hierarchy.typography"
REASON = "title by agreeing evidence (typography, layout label, numbering)"
ENGINES = frozenset({"native_pdf", "docx"})
_TITLE_LABELS = frozenset({"paragraph_title", "doc_title"})
# a finished sentence (not "1." or "3.1."), or a word cut at the line end (the paragraph goes on)
_SENTENCE_END = re.compile(r"[。！？；!?;]\s*$|(?<=[^\d\s.])\.\s*$|(?<=[A-Za-z])-\s*$")
_WORD = re.compile(r"[^\W\d_]")
_PAGE_REFERENCE = re.compile(r"[\s.．·…_\-]*\d+\s*$")

Typography = tuple[str | None, float | None, bool]


def typography_titles(state: DocumentState, *, skip: set[str] = frozenset(), titled: bool = False,
                      others: set[str] = frozenset()) -> list[tuple[str, str, int, dict]]:
    """(block id, text, proposed level, evidence) in reading order for the paragraphs two kinds of evidence make
    titles, and the document's title.  Blocks in *skip* (already decided by stronger evidence, e.g. DOCX heading
    styles) are left alone; *titled*: another source already gives the document its title; *others*: titles from
    another source ranked below a document title (the scan engine's labels) — these titles use the same scale."""
    body = body_typography(state, skip=skip)
    paragraphs = [(b, s) for b, s in _paragraphs(state) if b.id not in skip]
    found: list[tuple[Block, TextStyle, dict]] = []
    for block, style in paragraphs:
        evidence = _evidence(block, style, body)
        if len(evidence) >= 2 and (_one_line(block) or {"typography", "layout"} <= set(evidence)):
            found.append((block, style, evidence))
    found = _nested(paragraphs, found, body)
    found = _without_contents_entries(found, paragraphs)
    first_other = min((i for i, b in enumerate(ordered(state)) if b.id in others), default=None)
    title = None if titled else _document_title(paragraphs, found, body, state, first_other)
    if title is not None and title[0].id not in {b.id for b, _, _ in found}:
        found.insert(0, title)
    levels = _typographic_levels([(b.id, s) for b, s, _ in found], None if title is None else title[0].id,
                                 titled=titled or bool(others))
    return [(b.id, b.text, levels[b.id], {**ev, "kinds": ", ".join(sorted(ev))}) for b, _s, ev in found]


def _nested(paragraphs, found, body) -> list[tuple[Block, TextStyle, dict]]:
    """Add numbered paragraphs whose dotted number extends a title's number (``5.1`` under the title ``5``,
    ``5.1.1`` under ``5.1``): the document's own numbering says they sit in its outline (the second evidence)."""
    numbers = {n for b, _, _ in found if (n := leading_number(b.text)) is not None}
    chosen = {b.id for b, _, _ in found}
    added: dict[str, tuple[Block, TextStyle, dict]] = {}
    grew = True
    while grew:
        grew = False
        for block, style in paragraphs:
            if block.id in chosen or not _one_line(block):
                continue
            evidence = _evidence(block, style, body)
            number = leading_number(block.text)
            if "numbering" in evidence and number is not None and len(number) > 1 and number[:-1] in numbers:
                evidence["nesting"] = ".".join(map(str, number[:-1]))
                added[block.id] = (block, style, evidence)
                chosen.add(block.id)
                numbers.add(number)
                grew = True
    order = {b.id: i for i, (b, _) in enumerate(paragraphs)}
    return sorted([*found, *added.values()], key=lambda t: order[t[0].id])


def _document_title(paragraphs, found, body, state, first_other) -> tuple[Block, TextStyle, dict] | None:
    """The document's title: the paragraph set largest among the one-line, unnumbered, set-apart paragraphs that
    open the document before its first title (its position is the second evidence), when no title is set larger
    and the titles set like it are numbered sections (unnumbered ones would be its siblings);
    else the first title, when it is set larger than every other and comes before the other source's titles
    (*first_other*: the reading position of the first of them)."""
    sizes = [s.font_size or 0.0 for _, s, _ in found]
    first = found[0][0].id if found else None
    opening = []
    for block, style in paragraphs:
        if block.id == first:
            break
        apart = set_apart(style, body)
        if apart and _one_line(block) and numbering_signature(block.text) is None:
            opening.append((block, style, {"typography": ", ".join(apart), "position": "opens the document"}))
    if opening:
        top = max(s.font_size or 0.0 for _, s, _ in opening)
        lead = next(o for o in opening if (o[1].font_size or 0.0) == top)
        # set like some titles, it heads them only when they are numbered sections; else it is their sibling
        alike = [b for b, s, _ in found if (s.font_size or 0.0, bool(s.bold)) == (top, bool(lead[1].bold))]
        if all(top >= size for size in sizes) and all(numbering_signature(b.text) for b in alike):
            return lead
        if alike:  # set like titles that are not numbered: their sibling (a title at their level, not over them)
            found.insert(0, (lead[0], lead[1], {**lead[2], "sibling": alike[0].id}))
    if not found or not all(sizes[0] > size for size in sizes[1:]):
        return None
    if first_other is not None:
        position = next(i for i, b in enumerate(ordered(state)) if b.id == found[0][0].id)
        return found[0] if position < first_other else None
    return found[0] if len(sizes) > 1 else None


def _without_contents_entries(found: list[tuple[Block, TextStyle, dict]],
                              paragraphs: list[tuple[Block, TextStyle]]) -> list[tuple[Block, TextStyle, dict]]:
    """An entry of the table of contents points at a title: the document's own cross-reference, not a second title.
    A title is such an entry when its text (less a trailing page number and leader dots) is repeated by a later
    title, or — when it ends in a page number — by any later paragraph."""
    titles = {b.id: (b, s, ev) for b, s, ev in found}
    later_titles: set[str] = set()
    later_text: set[str] = set()
    kept = []
    for block, _style in reversed(paragraphs):
        if block.id in titles:
            key = _contents_key(block.text)
            paged = _PAGE_REFERENCE.search(block.text or "") is not None
            if key not in later_titles and not (paged and key in later_text):
                kept.append(titles[block.id])
            later_titles.add(_squash(block.text))
        later_text.add(_squash(block.text))
    return kept[::-1]


def _contents_key(text: str) -> str:
    return _squash(_PAGE_REFERENCE.sub("", text or ""))


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or ""))


def body_typography(state: DocumentState, *, skip: set[str] = frozenset()) -> Typography | None:
    """The typography most of the document's native text is set in (by characters); titles already decided (*skip*)
    and code do not count."""
    weights: Counter[Typography] = Counter()
    for block in state.blocks:
        style = _native_style(block)
        if block.kind == BlockKind.TEXT and style is not None and not style.monospace and block.id not in skip:
            weights[_typography(style)] += len("".join((block.text or "").split()))
    return weights.most_common(1)[0][0] if weights else None


def _paragraphs(state: DocumentState):
    for block in ordered(state):
        if block.kind != BlockKind.TEXT or block.status in HIDDEN or isinstance(block.anchors[0], AssetAnchor):
            continue
        style = _native_style(block)
        text = (block.text or "").strip()
        if style is not None and text and not style.monospace:  # code is neither body text nor a title
            yield block, style


def _one_line(block: Block) -> bool:
    return "\n" not in (block.text or "").strip()


def _evidence(block: Block, style: TextStyle, body: Typography | None) -> dict:
    evidence: dict = {}
    apart = set_apart(style, body)
    if apart:
        evidence["typography"] = ", ".join(apart)
    labels = {o.label for o in block.observations if o.task == TaskKind.LAYOUT}
    if labels and labels <= _TITLE_LABELS:  # every detected region the block lies in is a title region
        evidence["layout"] = ", ".join(sorted(labels))
    signature = numbering_signature(block.text)
    text = (block.text or "").strip()
    if signature is not None and _words_after_number(text) and not _SENTENCE_END.search(text):
        evidence["numbering"] = signature  # a number with words after it, not a numbered sentence (a list item)
    return evidence


def _words_after_number(text: str) -> bool:
    """The number is followed by words ("193." or "（2）：109-130." is a number alone)."""
    match = NUMBERING_RE.match(text)
    return match is not None and _WORD.search(text[match.end():]) is not None


def set_apart(style: TextStyle, body: Typography | None) -> list[str]:
    if body is None:
        return []
    font, size, bold = body
    apart = []
    if style.font_size and size and style.font_size > size:
        apart.append(f"size {style.font_size} (body {size})")
    if style.bold and not bold:
        apart.append("bold (body regular)")
    if style.font and font and style.font != font:
        apart.append(f"font {style.font} (body {font})")
    return apart


def _typographic_levels(titles: list[tuple[str, TextStyle]], title: str | None, *, titled: bool) -> dict[str, int]:
    """The document's *title* is level 1; the other titles rank by their typography within the document — larger
    first, then bold first — from level 2 when the document has a title (here or from another source), else 1."""
    keys = {block_id: (-(style.font_size or 0.0), not style.bold) for block_id, style in titles if block_id != title}
    ranks = {key: i for i, key in enumerate(sorted(set(keys.values())))}
    below = 2 if title is not None or titled else 1
    levels = {block_id: ranks[key] + below for block_id, key in keys.items()}
    if title is not None:
        levels[title] = 1
    return levels


def _typography(style: TextStyle) -> Typography:
    return (style.font, style.font_size, bool(style.bold))


def _native_style(block: Block) -> TextStyle | None:
    chosen = next((o for o in block.observations if o.id == block.chosen_observation), None)
    return chosen.style if chosen is not None and chosen.engine in ENGINES else None
