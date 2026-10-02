"""Markdown contract (guide §4.5, docs/v2_phase1_interfaces.md §5.6).

- ATX headings for titles with a level; a title whose level is still
  pending is a plain paragraph (the renderer never invents structure);
- no hard line breaks inside a paragraph (CJK-aware joining); a fenced code block in a block's text (the agent's
  code) keeps its lines, and one the text leaves open is closed at the block's end: no block turns the rest of the
  document into code (round 1, a fence on a joined line);
- tables from ``TableGrid``: GFM, or HTML when GFM cannot express them;
- figures: ``![<type>](images/<file>)`` followed by the note ``> 图片说明：…`` (one or two sentences, Q121) — the
  layout the evaluator strips
  as a description; the label never repeats the description;
- text read inside an image (IO6-5): a quote opening with ``**〔图片识别〕**``, between the comments
  ``<!-- parserx:image-text src="images/<file>" page=n -->`` and ``<!-- /parserx:image-text -->`` (for programs).
  An image whose words are its content (described as content) and whose local reading the text has in full is not
  shown: the label line carries its note and a link to the original; any other image stays above its text;
- ``<!-- PAGE n -->`` for every PDF page, ``<!-- PAGE n scanned -->`` for one read by the scan engine, between blocks
  (a paragraph or table continued across the page is written whole before it); the page's running heads, feet and
  page numbers go into it (``<!-- PAGE 2 · 页眉：… · 页码：624 -->``; ``page_furniture``: "comment", the default —
  kept, out of the reader's way, never cutting a paragraph; "text" writes them as lines after it; "omit" leaves
  them to the sidecar, user 2026-09-30); DOCX only
  has ``<!-- PAGE-BREAK -->`` and ``<!-- SECTION k -->`` (no physical pages without a layout engine);
- hidden blocks (excluded, merged, duplicate) and failed blocks are not
  rendered; they stay in the sidecar.  A scan image whose recognition failed
  stays visible.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from parserx.content.lists import bulleted, strip_bullet
from parserx.render.emphasis import SCRIPTS, emphasize
from parserx.content.text import unbreak, whole_words, escape_strikethrough, join_wrapped
from parserx.ir.anchor import AssetAnchor, PdfAnchor
from parserx.content.equation_numbers import number_of, tagged
from parserx.ir.asset import Asset
from parserx.ir.block import Block
from parserx.ir.enums import BlockKind, BlockStatus, RelationKind
from parserx.ir.semantic import note_of
from parserx.ir.state import DocumentState
from parserx.reading.compare import has_math, read_inside, text_stands_for_image
from parserx.workspace.queries import JOINABLE, block_unit, ordered, scanned_pages

_VISIBLE = frozenset({BlockStatus.OK, BlockStatus.DEGRADED})
_MARKUP_START = re.compile(r"^(\s*)([#>])")
_PARAGRAPH = re.compile(r"\n[ \t]*\n")
_MATH_START = ("$", "\\[", "\\(")  # delimited already; a bare \begin{aligned} still needs $$ to render


def image_file(asset: Asset) -> str:
    """File name of an asset inside the export's image directory."""
    return PurePosixPath(asset.path).name


def render_markdown(state: DocumentState, *, image_dir: str = "images", lang: str = "zh",
                    page_furniture: str = "comment") -> str:
    assets = {a.id: a for a in state.assets}
    missing = {m.block: _missing_note(state, m, lang) for m in state.missing}  # Q117: said where it is missing
    joined = _continuations(state)  # a paragraph continued in later blocks is rendered once, at its start
    skipped = {b.id for chain in joined.values() for b in chain[1:]}
    inside = read_inside(state)  # rendered with their image, not where they stand
    skipped |= {b.id for blocks in inside.values() for b in blocks}
    images = {figure: _image_text(state, figure, blocks, assets, image_dir, missing, lang)
              for figure, blocks in inside.items()}
    transcribed = _transcription_starts(state)
    scanned = scanned_pages(state)
    numbers = number_of(state)  # a display formula's number, merged into it: its \tag
    body = body_face(state)
    whole = whole_words(b.text for b in state.blocks if b.status in _VISIBLE and b.kind in _UNBROKEN)
    by_unit: dict[int | None, list[Block]] = {}
    for block in ordered(state):
        if block.id in skipped:
            continue
        number = numbers.get(block.id)
        if block.id in joined:
            chain = joined[block.id]
            if block.kind == BlockKind.FORMULA:  # a formula a page splits: one display, numbered where a part is
                block = block.model_copy(update={"text": "\n".join(_math_body(b.text) for b in chain)})
                number = next((numbers[b.id] for b in chain if b.id in numbers), None)
            else:
                block = _joined(block, chain)
        if block.kind in _UNBROKEN and block.text and code_face(block, body) is None:
            block = block.model_copy(update={"text": unbreak(block.text, whole)})  # a word a line end broke
        if number is not None:
            block = block.model_copy(update={"text": tagged(block.text, number)})
        by_unit.setdefault(block_unit(state, block), []).append(block)
    parts: list[str] = []
    section = 1
    furniture = _furniture(state) if page_furniture != "omit" else {}
    for page in state.pages:
        if state.format == "pdf":
            items = furniture.get(page.n, [])
            head = f"PAGE {page.n}" + (" scanned" if page.n in scanned else "")
            if items and page_furniture == "comment":
                head += "".join(f" · {_FURNITURE_LABEL[lang][kind]}{_comment_safe(text)}" for kind, text in items)
            parts.append(f"<!-- {head} -->")
            if page_furniture == "text":
                parts.extend(_MARKUP_START.sub(r"\1\\\2", text) for _kind, text in items)
        elif page.starts_with == "page_break":
            parts.append("<!-- PAGE-BREAK -->")
        elif page.starts_with == "section_break":
            section += 1
            parts.append(f"<!-- SECTION {section} -->")
        parts.extend(_render_all(by_unit.pop(page.n, []), assets, image_dir, transcribed, body, missing, lang, images))
    for blocks in by_unit.values():  # content outside any page or segment (none in a well-formed state)
        parts.extend(_render_all(blocks, assets, image_dir, transcribed, body, missing, lang, images))
    return "\n\n".join(parts) + "\n"


_FURNITURE_KINDS = (BlockKind.HEADER, BlockKind.FOOTER, BlockKind.PAGE_NUMBER)
_FURNITURE_LABEL = {"zh": {BlockKind.HEADER: "页眉：", BlockKind.FOOTER: "页脚：", BlockKind.PAGE_NUMBER: "页码："},
                    "en": {BlockKind.HEADER: "header: ", BlockKind.FOOTER: "footer: ",
                           BlockKind.PAGE_NUMBER: "page number: "}}


def _furniture(state: DocumentState) -> dict[int, list[tuple[BlockKind, str]]]:
    """Page → its running heads, feet and page numbers (excluded from the flow), top to bottom."""
    out: dict[int, list[tuple[float, float, BlockKind, str]]] = {}
    for block in state.blocks:
        text = " ".join(block.text.split())
        if block.kind in _FURNITURE_KINDS and block.status == BlockStatus.EXCLUDED and text:
            anchor = block.anchors[0] if block.anchors else None
            y, x = (anchor.bbox[1], anchor.bbox[0]) if isinstance(anchor, PdfAnchor) else (0.0, 0.0)
            out.setdefault(block_unit(state, block), []).append((y, x, block.kind, text))
    return {n: [(kind, text) for _y, _x, kind, text in sorted(items, key=lambda i: (i[0], i[1]))]
            for n, items in out.items() if n is not None}


def _comment_safe(text: str) -> str:
    """Text that cannot end an HTML comment early."""
    return text.replace("--", "- -")


def _continuations(state: DocumentState) -> dict[str, list[Block]]:
    """Chains of visible text blocks — or parts of a formula — linked by ``continues`` (earlier → later, guide
    §6.9), by their first block."""
    blocks = {b.id: b for b in state.blocks}

    def joinable(block_id: str) -> bool:
        block = blocks.get(block_id)
        return block is not None and block.status in _VISIBLE and (block.kind in JOINABLE
                                                                   or block.kind == BlockKind.FORMULA)

    following = {r.src: r.dst for r in sorted(state.relations, key=lambda r: r.id)
                 if r.kind == RelationKind.CONTINUES and joinable(r.src) and joinable(r.dst)}
    continued = set(following.values())
    chains: dict[str, list[Block]] = {}
    for head in sorted(following):
        if head in continued:
            continue
        chain, seen = [blocks[head]], {head}
        while chain[-1].id in following and following[chain[-1].id] not in seen:
            nxt = following[chain[-1].id]
            chain.append(blocks[nxt])
            seen.add(nxt)
        chains[head] = chain
    return chains


PICTURES_FROM_ABOVE = "<!-- 以下是上方〔图 n〕处的图片 -->"
IMAGE_TEXT_LABEL = {"zh": "〔图片识别〕", "en": "[Text from image]"}
ORIGINAL = {"zh": "原图", "en": "original"}


def _image_text(state: DocumentState, figure: str, inside: list[Block], assets: dict[str, Asset], image_dir: str,
                missing: dict[str, str], lang: str) -> str:
    """An image and the text read inside it (IO6-5): the text quoted under a label, between comments naming the
    image; the image itself left out when its words are its content and the text has all its local reading."""
    block = next(b for b in state.blocks if b.id == figure)
    asset = next((assets.get(a.asset) for a in block.anchors if isinstance(a, AssetAnchor)), None)
    src = f"{image_dir}/{image_file(asset)}" if asset is not None else None
    note = note_of(block.semantic)
    content = note is not None and note.type == "content"
    hidden = content and src is not None and text_stands_for_image(state, block)
    label = f"**{IMAGE_TEXT_LABEL[lang]}**" + (f" {note.caption}" if content and note.caption else "") \
        + (f"　[{ORIGINAL[lang]}]({src})" if hidden else "")
    pieces = [label]
    for child in inside:
        text = join_wrapped((child.text or "").split("\n"))
        if child.kind == BlockKind.TITLE and text and not has_math(text):  # the image's title, not the document's
            rendered = f"**{text}**"
        elif child.kind == BlockKind.TITLE:
            rendered = _MARKUP_START.sub(r"\1\\\2", text)
        else:
            rendered = _render(child, assets, image_dir, lang)
        if rendered:
            pieces.append(rendered)
        if child.id in missing:
            pieces.append(missing[child.id])
    quoted = "\n".join(f"> {line}" if line else ">" for line in "\n\n".join(pieces).split("\n"))
    page = next((a.page for a in block.anchors if isinstance(a, PdfAnchor)), None) if state.format == "pdf" else None
    opening = "<!-- parserx:image-text" + (f' src="{src}"' if src else "") + (f" page={page}" if page else "") + " -->"
    text = f"{opening}\n{quoted}\n<!-- /parserx:image-text -->"
    if hidden:
        return text
    shown = f"![{_label(block, lang)}]({src})" if content else _render(block, assets, image_dir, lang)
    return f"{shown}\n\n{text}" if shown else text


def _transcription_starts(state: DocumentState) -> dict[str, str]:
    """The note before the first shown block contained by a shown block that is not an image: the pictures cut
    from a table cell or a paragraph (marked 〔图n〕 there).  Text read inside an image is rendered with it."""
    blocks = {b.id: b for b in state.blocks}
    children: dict[str, list[Block]] = {}
    for relation in state.relations:
        src, dst = blocks.get(relation.src), blocks.get(relation.dst)
        if relation.kind == RelationKind.CONTAINS and src is not None and dst is not None \
                and src.status in _VISIBLE and dst.status in _VISIBLE:
            children.setdefault(src.id, []).append(dst)
    return {min(kids, key=lambda b: (b.order, b.id)).id: PICTURES_FROM_ABOVE
            for src, kids in children.items() if blocks[src].kind != BlockKind.FIGURE}


def _render_all(blocks: list[Block], assets: dict[str, Asset], image_dir: str,
                notes: dict[str, str] | None = None, body: str | None = None,
                missing: dict[str, str] | None = None, lang: str = "zh",
                images: dict[str, str] | None = None) -> list[str]:
    out = []
    code: list[str] = []  # lines of the code block being collected
    face: str | None = None
    missing = missing or {}
    for block in blocks:
        if block.status not in _VISIBLE:
            if block.id in missing:  # not shown: the note stands in its place
                out.append(missing[block.id])
            continue
        style = _style(block)
        this = code_face(block, body)
        if this is None and face is not None and block.kind in _CODE_ROLES and style is not None \
                and style.font == face:
            this = face  # a line in the same face too short to measure (a rule of dashes)
        if this is not None and (face is None or this == face) and not (notes and block.id in notes):
            code.append((block.text or "").strip("\n"))
            face = this
            continue
        if code:
            out.append(_code_block(code))
            code, face = [], None
        if this is not None:  # code in another face starts right away
            code, face = [(block.text or "").strip("\n")], this
            continue
        rendered = images[block.id] if images and block.id in images else _render(block, assets, image_dir, lang)
        if rendered:
            if notes and block.id in notes:
                out.append(notes[block.id])
            out.append(rendered)
        if block.id in missing:  # shown as it is (a page image), its content not read: the note follows
            out.append(missing[block.id])
    if code:
        out.append(_code_block(code))
    return out


def _math_body(text: str) -> str:
    """A display formula's LaTeX without its delimiters ($$ … $$, \\[ … \\])."""
    body = text.strip()
    for opening, closing in (("$$", "$$"), ("\\[", "\\]")):
        if body.startswith(opening) and body.endswith(closing) and len(body) >= len(opening) + len(closing):
            return body[len(opening):len(body) - len(closing)].strip()
    return body


def _code_block(lines: list[str]) -> str:
    """Lines as a fenced code block, its fence longer than any run of backticks the lines hold (so none closes it)."""
    longest = max((len(run) for line in lines for run in re.findall(r"`+", line)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}\n" + "\n".join(lines) + f"\n{fence}"


def _render(block: Block, assets: dict[str, Asset], image_dir: str, lang: str = "zh") -> str:
    kind = block.kind
    if kind == BlockKind.TABLE:
        grid = block.cells
        if grid is None or not grid.cells:
            return ""
        return grid.to_html() if grid.needs_html else grid.to_gfm()
    if kind in (BlockKind.FIGURE, BlockKind.SCAN):
        asset = next((assets.get(a.asset) for a in block.anchors if isinstance(a, AssetAnchor)), None)
        if asset is None:
            return ""
        label = _label(block, lang) if kind == BlockKind.FIGURE else SCAN_LABEL[lang]
        image = f"![{label}]({image_dir}/{image_file(asset)})"
        semantic = semantic_block(block, lang)
        return f"{image}\n\n{semantic}" if semantic else image
    text = join_wrapped(block.text.split("\n"))
    if not text:
        return ""
    if kind == BlockKind.FORMULA:
        return text if text.startswith(_MATH_START) else f"$$\n{text}\n$$"
    marks = _marks(block)  # inline bold and underline (R3), sub- and superscripts (Q143 ③)
    if kind == BlockKind.TITLE and block.level is not None:  # a title is not set in bold, its scripts are written
        text, _ = emphasize(text, [m for m in marks if m.kind in SCRIPTS])
        return f"{'#' * block.level} {escape_strikethrough(text)}"
    if bulleted(block):  # a bulleted item: "- " in place of the page's bullet
        body, _ = emphasize(strip_bullet(text), marks)
        return "- " + escape_strikethrough(body)
    # a paragraph's lines are joined; a blank line (the scan engine's paragraph break) keeps paragraphs apart; a
    # fenced code block keeps its lines
    out = []
    for fenced, segment in fences(block.text):
        if fenced:
            out.append(segment)
            continue
        for paragraph in (join_wrapped(part.split("\n")).strip() for part in _PARAGRAPH.split(segment)):
            if paragraph:
                paragraph, marks = emphasize(paragraph, marks)
                out.append(_MARKUP_START.sub(r"\1\\\2", escape_strikethrough(paragraph)))
    return "\n\n".join(out)


_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")


def fences(text: str) -> list[tuple[bool, str]]:
    """*text* in segments, each a fenced code block (True: its lines as they are, closed at the end of the text when
    it is left open) or the text between (False).  Fences as CommonMark reads them: three or more backticks or
    tildes at the start of a line (an info string after backticks has none), closed by a line of at least as many of
    the same character and nothing else."""
    out: list[tuple[bool, str]] = []
    lines = text.split("\n")
    plain: list[str] = []
    i = 0
    while i < len(lines):
        match = _FENCE.match(lines[i])
        if match is None or (match.group(1)[0] == "`" and "`" in match.group(2)):
            plain.append(lines[i])
            i += 1
            continue
        if plain:
            out.append((False, "\n".join(plain)))
            plain = []
        mark = match.group(1)
        close = re.compile(rf"^ {{0,3}}{re.escape(mark[0])}{{{len(mark)},}}\s*$")
        end = next((j for j in range(i + 1, len(lines)) if close.match(lines[j])), None)
        body = lines[i:end + 1] if end is not None else lines[i:] + [mark]
        out.append((True, "\n".join(body)))
        i = len(lines) if end is None else end + 1
    if plain:
        out.append((False, "\n".join(plain)))
    return out


def _joined(head: Block, chain: list[Block]) -> Block:
    """A paragraph continued in later blocks as one block to render: their texts, and each part's marks in order
    (marks are found in the text one after another, so each part's scripts and emphasis are its own)."""
    marks = [m for b in chain for m in _marks(b)]
    reading = next((o.id for o in head.observations if o.id == head.chosen_observation),
                   head.observations[0].id if head.observations else None)
    observations = [o.model_copy(update={"marks": marks}) if o.id == reading else o for o in head.observations]
    return head.model_copy(update={"text": "\n".join(b.text for b in chain), "observations": observations})


def _marks(block: Block) -> list:
    """The block's inline marks: its chosen reading's, else the first reading that has any (a split or a line
    break keeps the text, so the source's marks still name its spans)."""
    chosen = next((o for o in block.observations if o.id == block.chosen_observation), None)
    if chosen is not None and chosen.marks:
        return list(chosen.marks)
    return list(next((o.marks for o in block.observations if o.marks), []))


def _style(block: Block):
    chosen = next((o for o in block.observations if o.id == block.chosen_observation), None)
    return chosen.style if chosen is not None else None


def body_face(state: DocumentState) -> str | None:
    """The face most of the document's prose is set in (by characters, blocks not set in a monospaced face)."""
    weights: dict[str, int] = {}
    for block in state.blocks:
        style = _style(block)
        if block.kind == BlockKind.TEXT and style is not None and style.font and not style.monospace:
            weights[style.font] = weights.get(style.font, 0) + len("".join((block.text or "").split()))
    return max(sorted(weights), key=weights.get) if weights else None


def code_face(block: Block, body: str | None) -> str | None:
    """The face of a code block: text set in a monospaced face that is not the body's (P4-6), else None.  A document
    without prose in another face has no code by this evidence.  Code keeps its lines whatever role the block is
    given — set as a list item, its commands are not joined into a paragraph (round 2) — but a title stays a title."""
    style = _style(block)
    if body is None or block.kind not in _CODE_ROLES or style is None or not style.monospace \
            or style.monospace == body:
        return None
    return style.monospace


_CODE_ROLES = frozenset({BlockKind.TEXT, BlockKind.LIST, BlockKind.CAPTION, BlockKind.FOOTNOTE, BlockKind.OTHER})
_UNBROKEN = _CODE_ROLES | {BlockKind.TITLE}  # the text kinds whose words a line end may break


# ── Missing content (Q117) ──────────────────────────────────────────────

MISSING_MARK = {"zh": "〔未识别〕", "en": "〔Not recognised〕"}
_WHAT = {BlockKind.SCAN: ("扫描内容", "scanned content"), BlockKind.FIGURE: ("一张图片", "an image"),
         BlockKind.TABLE: ("一个表格", "a table"), BlockKind.FORMULA: ("一个公式", "a formula")}
_WHY = (("not configured", "未配置识别服务", "the recognition service is not configured"),
        ("budget", "超出处理预算", "over the processing budget"),
        ("timeout", "识别超时", "recognition timed out"),
        ("time out", "识别超时", "recognition timed out"))


def _missing_note(state: DocumentState, missing, lang: str) -> str:
    """A reader's line where content could not be read: which page, what, and why in plain words."""
    block = next((b for b in state.blocks if b.id == missing.block), None)
    en = lang == "en"
    what = _WHAT.get(block.kind if block is not None else None, ("一处内容", "some content"))[1 if en else 0]
    reason = missing.reason.lower()
    why = next(((w_en if en else w_zh) for key, w_zh, w_en in _WHY if key in reason),
               "recognition failed" if en else "识别失败")
    unit = block_unit(state, block) if block is not None and state.format == "pdf" else None
    if en:
        where = f"page {unit}: " if unit is not None else ""
        return f"> {MISSING_MARK['en']} {where}{what} could not be read ({why})"
    where = f"第 {unit} 页：" if unit is not None else ""
    return f"> {MISSING_MARK['zh']}{where}{what}未能识别（{why}）"


# ── Figure notes (Q118, Q121) ───────────────────────────────────────────

TYPE_NAMES = {"zh": {"content": "图片", "chart": "图表", "diagram": "示意图", "photo": "照片", "screenshot": "截图", "seal": "印章或标志",
                     "other": "图片"},
              "en": {"content": "Image", "chart": "Chart", "diagram": "Diagram", "photo": "Photo", "screenshot": "Screenshot",
                     "seal": "Seal or logo", "other": "Image"}}
NOTE_LABEL = {"zh": "图片说明：", "en": "Image description: "}
SCAN_LABEL = {"zh": "扫描图像", "en": "Scanned page"}


def _label(block: Block, lang: str = "zh") -> str:
    """The image's alternative text: what kind of image it is (the note itself follows the image line)."""
    note = note_of(block.semantic)
    return TYPE_NAMES[lang][note.type] if note is not None else TYPE_NAMES[lang]["other"]


def semantic_block(block: Block, lang: str = "zh") -> str:
    return semantic_text(block.semantic, lang)


def semantic_text(semantic, lang: str = "zh") -> str:
    """A figure's note as the Markdown shows it: one blockquote line right after the image line ("> 图片说明：…"),
    which is what the evaluation drops as a description."""
    note = note_of(semantic)
    if note is None or not note.caption:
        return ""
    return f"> {NOTE_LABEL[lang]}{escape_strikethrough(' '.join(note.caption.split()))}"


