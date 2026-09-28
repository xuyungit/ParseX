"""Markdown contract (guide §4.5, docs/v2_phase1_interfaces.md §5.6).

- ATX headings for titles with a level; a title whose level is still
  pending is a plain paragraph (the renderer never invents structure);
- no hard line breaks inside a paragraph (CJK-aware joining);
- tables from ``TableGrid``: GFM, or HTML when GFM cannot express them;
- figures: ``![<type>](images/<file>)`` followed by the note ``> 图片说明：…`` (one or two sentences, Q121) — the
  layout the evaluator strips
  as a description; the label never repeats the description;
- ``<!-- PAGE n -->`` for every PDF page; DOCX only has ``<!-- PAGE-BREAK -->``
  and ``<!-- SECTION k -->`` (no physical pages without a layout engine);
- hidden blocks (excluded, merged, duplicate) and failed blocks are not
  rendered; they stay in the sidecar.  A scan image whose recognition failed
  stays visible.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from parserx.content.text import join_wrapped
from parserx.ir.anchor import AssetAnchor
from parserx.ir.asset import Asset
from parserx.ir.block import Block
from parserx.ir.enums import BlockKind, BlockStatus, RelationKind
from parserx.ir.semantic import note_of
from parserx.ir.state import DocumentState
from parserx.workspace.queries import JOINABLE, block_unit, ordered

_VISIBLE = frozenset({BlockStatus.OK, BlockStatus.DEGRADED})
_MARKUP_START = re.compile(r"^(\s*)([#>])")
_MATH_START = ("$", "\\[", "\\(")  # delimited already; a bare \begin{aligned} still needs $$ to render


def image_file(asset: Asset) -> str:
    """File name of an asset inside the export's image directory."""
    return PurePosixPath(asset.path).name


def render_markdown(state: DocumentState, *, image_dir: str = "images", lang: str = "zh") -> str:
    assets = {a.id: a for a in state.assets}
    missing = {m.block: _missing_note(state, m, lang) for m in state.missing}  # Q117: said where it is missing
    joined = _continuations(state)  # a paragraph continued in later blocks is rendered once, at its start
    skipped = {b.id for chain in joined.values() for b in chain[1:]}
    transcribed = _transcription_starts(state)
    by_unit: dict[int | None, list[Block]] = {}
    for block in ordered(state):
        if block.id in skipped:
            continue
        if block.id in joined:
            block = block.model_copy(update={"text": "\n".join(b.text for b in joined[block.id])})
        by_unit.setdefault(block_unit(state, block), []).append(block)
    parts: list[str] = []
    section = 1
    body = body_face(state)
    for page in state.pages:
        if state.format == "pdf":
            parts.append(f"<!-- PAGE {page.n} -->")
        elif page.starts_with == "page_break":
            parts.append("<!-- PAGE-BREAK -->")
        elif page.starts_with == "section_break":
            section += 1
            parts.append(f"<!-- SECTION {section} -->")
        parts.extend(_render_all(by_unit.pop(page.n, []), assets, image_dir, transcribed, body, missing, lang))
    for blocks in by_unit.values():  # content outside any page or segment (none in a well-formed state)
        parts.extend(_render_all(blocks, assets, image_dir, transcribed, body, missing, lang))
    return "\n\n".join(parts) + "\n"


def _continuations(state: DocumentState) -> dict[str, list[Block]]:
    """Chains of visible text blocks linked by ``continues`` (earlier → later, guide §6.9), by their first block."""
    blocks = {b.id: b for b in state.blocks}

    def joinable(block_id: str) -> bool:
        block = blocks.get(block_id)
        return block is not None and block.status in _VISIBLE and block.kind in JOINABLE

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


TRANSCRIBED_FROM_IMAGE = "<!-- 以下转录自上图 -->"
PICTURES_FROM_ABOVE = "<!-- 以下是上方〔图 n〕处的图片 -->"


def _transcription_starts(state: DocumentState) -> dict[str, str]:
    """The note before the first shown block contained by a shown block: the text read inside an image (Q42), or
    the pictures cut from a table cell or a paragraph (marked 〔图n〕 there)."""
    blocks = {b.id: b for b in state.blocks}
    children: dict[str, list[Block]] = {}
    for relation in state.relations:
        src, dst = blocks.get(relation.src), blocks.get(relation.dst)
        if relation.kind == RelationKind.CONTAINS and src is not None and dst is not None \
                and src.status in _VISIBLE and dst.status in _VISIBLE:
            children.setdefault(src.id, []).append(dst)
    return {min(kids, key=lambda b: (b.order, b.id)).id:
            TRANSCRIBED_FROM_IMAGE if blocks[src].kind == BlockKind.FIGURE else PICTURES_FROM_ABOVE
            for src, kids in children.items()}


def _render_all(blocks: list[Block], assets: dict[str, Asset], image_dir: str,
                notes: dict[str, str] | None = None, body: str | None = None,
                missing: dict[str, str] | None = None, lang: str = "zh") -> list[str]:
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
        if this is None and face is not None and block.kind == BlockKind.TEXT and style is not None \
                and style.font == face:
            this = face  # a line in the same face too short to measure (a rule of dashes)
        if this is not None and (face is None or this == face) and not (notes and block.id in notes):
            code.append((block.text or "").strip("\n"))
            face = this
            continue
        if code:
            out.append("```\n" + "\n".join(code) + "\n```")
            code, face = [], None
        if this is not None:  # code in another face starts right away
            code, face = [(block.text or "").strip("\n")], this
            continue
        rendered = _render(block, assets, image_dir, lang)
        if rendered:
            if notes and block.id in notes:
                out.append(notes[block.id])
            out.append(rendered)
        if block.id in missing:  # shown as it is (a page image), its content not read: the note follows
            out.append(missing[block.id])
    if code:
        out.append("```\n" + "\n".join(code) + "\n```")
    return out


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
    if kind == BlockKind.TITLE and block.level is not None:
        return f"{'#' * block.level} {text}"
    if kind == BlockKind.FORMULA:
        return text if text.startswith(_MATH_START) else f"$$\n{text}\n$$"
    return _MARKUP_START.sub(r"\1\\\2", text)


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
    without prose in another face has no code by this evidence."""
    style = _style(block)
    if body is None or block.kind != BlockKind.TEXT or style is None or not style.monospace or style.monospace == body:
        return None
    return style.monospace


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
    return f"> {NOTE_LABEL[lang]}{' '.join(note.caption.split())}"


