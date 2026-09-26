"""DOCX reader: OOXML read directly (guide §6.9, plan R1/R3, Q26).

Docling cannot name OOXML nodes or enumerate what it read, so this reader
walks ``word/document.xml`` itself and accounts for every content node.

Supported in Phase 1:

- body paragraphs in order, through hyperlinks, content controls, smart tags
  and custom XML; ``mc:AlternateContent`` is read once (its first Choice) so
  drawings and textboxes are not duplicated through their VML fallback;
- tracked changes as the final view with all changes accepted (Q26):
  ``w:ins`` / ``w:moveTo`` text is content; ``w:del`` / ``w:moveFrom`` text
  becomes an excluded block with a ``revision_deleted`` Decision; a deleted
  paragraph mark joins the paragraph to the next one;
- fields: result text only (instruction text is not content);
- tables with ``gridSpan`` / ``vMerge``; nested tables are flattened into their
  cell with a warning;
- images (DrawingML blip, VML imagedata, OLE preview images) as Assets;
- explicit page breaks and section breaks split the body into segments;
- style, outline level (direct or inherited along ``basedOn``) and list
  numbering (rendered, e.g. ``"1.2."``) as ``TextStyle`` evidence; the
  rendered number is part of the paragraph text, as Word displays it;
- header and footer parts as excluded page furniture.

- textboxes (Q44): each paragraph becomes a text block right after the
  paragraph that anchors the textbox (no style evidence).

- footnotes and endnotes (Q9): the reference becomes ``[^1]`` (``[^e1]``)
  where Word shows its number, the note a Markdown footnote definition right
  after the paragraph; comments are excluded (a reviewer's note, kept in the
  sidecar); Office Math as LaTeX, inline ``$…$`` or display ``$$…$$``.

Not supported yet (warning, ``failed`` ledger item, text kept on a failed
block in the sidecar): linked images, charts and SmartArt (their text read
from their own part: titles, series, categories and cached values; node text).
Paragraph roles (title, list) are the structure step's decision.
"""

from __future__ import annotations

import io
import posixpath
import unicodedata
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree
from PIL import Image

from parserx.content import vector
from parserx.content.extraction import Extraction
from parserx.content.omml import omml_to_latex
from parserx.ir import ids
from parserx.ir.anchor import AssetAnchor, DocxAnchor
from parserx.ir.asset import Asset
from parserx.ir.block import Block
from parserx.ir.decision import Decision
from parserx.ir.enums import BlockKind, BlockStatus, DecisionStage, ObservationStatus, PageStatus, TaskKind
from parserx.ir.observation import Numbering, Observation, TextStyle
from parserx.ir.state import LedgerEntry, Missing, PageState
from parserx.layout import labels
from parserx.tables.grid import Cell, TableGrid

ENGINE = "docx"
ENGINE_VERSION = "ooxml-reader-1"
ACTOR = "program:content.docx"

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "v": "urn:schemas-microsoft-com:vml",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    "wps": "http://schemas.microsoft.com/office/word/2010/wordprocessingShape",
    "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
}
_PREFIX = {uri: prefix for prefix, uri in NS.items()}
_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
_OFFICE_DOC = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
_EMU_PER_PX = 9525  # 96 dpi


def _w(tag: str) -> str:
    return f"{{{NS['w']}}}{tag}"


def _attr(elem, name: str, ns: str = "w") -> str | None:
    return elem.get(f"{{{NS[ns]}}}{name}") if elem is not None else None


def _qname(elem) -> str:
    q = etree.QName(elem)
    return f"{_PREFIX.get(q.namespace, 'x')}:{q.localname}"


def _on(elem) -> bool:
    """A toggle property (w:b, w:pageBreakBefore …) is on unless its val says otherwise."""
    return elem is not None and _attr(elem, "val") not in ("0", "false", "off")


# ── Package, styles, numbering ──────────────────────────────────────────


class _Package:
    def __init__(self, path: Path):
        self.zip = zipfile.ZipFile(path)
        self.names = set(self.zip.namelist())
        self._types = self._content_types()
        rels = self.rels("")
        self.main = next((t for t, kind in rels.values() if kind == _OFFICE_DOC), "word/document.xml")

    def xml(self, part: str):
        if part not in self.names:
            return None
        return etree.fromstring(self.zip.read(part))

    def rels(self, part: str) -> dict[str, tuple[str, str]]:
        """rId → (target part path, relationship type); external targets keep their URL."""
        folder, name = posixpath.split(part)
        rels_part = posixpath.join(folder, "_rels", f"{name}.rels")
        root = self.xml(rels_part)
        out: dict[str, tuple[str, str]] = {}
        if root is None:
            return out
        for rel in root.findall(f"{{{_REL}}}Relationship"):
            target = rel.get("Target", "")
            if rel.get("TargetMode") != "External":
                target = posixpath.normpath(posixpath.join(folder, target)).lstrip("/")
            out[rel.get("Id", "")] = (target, rel.get("Type", ""))
        return out

    def content_type(self, part: str) -> str:
        return self._types.get("/" + part) or self._types.get(posixpath.splitext(part)[1].lstrip(".").lower(), "")

    def _content_types(self) -> dict[str, str]:
        root = self.xml("[Content_Types].xml")
        types: dict[str, str] = {}
        if root is None:
            return types
        for elem in root:
            local = etree.QName(elem).localname
            if local == "Default":
                types[elem.get("Extension", "").lower()] = elem.get("ContentType", "")
            elif local == "Override":
                types[elem.get("PartName", "")] = elem.get("ContentType", "")
        return types


@dataclass
class _Style:
    name: str | None = None
    based_on: str | None = None
    outline: int | None = None
    num: tuple[str, int] | None = None
    size: float | None = None
    bold: bool | None = None
    east_asia: str | None = None  # w:rFonts: the face of CJK text
    ascii: str | None = None  # the face of other text


class _Styles:
    def __init__(self, root):
        self.styles: dict[str, _Style] = {}
        self.default_paragraph: str | None = None
        self.default_size: float | None = None
        self.default_bold: bool | None = None
        self.default_faces: tuple[str | None, str | None] = (None, None)
        if root is None:
            return
        defaults = root.find(f"{_w('docDefaults')}/{_w('rPrDefault')}/{_w('rPr')}")
        self.default_size, self.default_bold, self.default_faces = _run_props(defaults)
        for style in root.findall(_w("style")):
            sid = _attr(style, "styleId") or ""
            ppr = style.find(_w("pPr"))
            outline = ppr.find(_w("outlineLvl")) if ppr is not None else None
            num = ppr.find(_w("numPr")) if ppr is not None else None
            size, bold, (east_asia, ascii_face) = _run_props(style.find(_w("rPr")))
            self.styles[sid] = _Style(
                name=_attr(style.find(_w("name")), "val"),
                based_on=_attr(style.find(_w("basedOn")), "val"),
                outline=int(_attr(outline, "val")) if outline is not None and (_attr(outline, "val") or "").isdigit() else None,
                num=_num_pr(num),
                size=size, bold=bold, east_asia=east_asia, ascii=ascii_face,
            )
            if _attr(style, "type") == "paragraph" and _attr(style, "default") in ("1", "true"):
                self.default_paragraph = sid

    def chain(self, sid: str | None) -> list[_Style]:
        out, seen = [], set()
        while sid and sid in self.styles and sid not in seen:
            seen.add(sid)
            out.append(self.styles[sid])
            sid = self.styles[sid].based_on
        return out

    def first(self, sid: str | None, attr: str):
        return next((getattr(s, attr) for s in self.chain(sid) if getattr(s, attr) is not None), None)


def _run_props(rpr) -> tuple[float | None, bool | None, tuple[str | None, str | None]]:
    """Size (pt), bold and the (East Asian, ASCII) faces a run properties element sets; None where it sets none.
    A theme face is named by its theme slot (``minorEastAsia``)."""
    if rpr is None:
        return None, None, (None, None)
    sz = rpr.find(_w("sz"))
    b = rpr.find(_w("b"))
    fonts = rpr.find(_w("rFonts"))
    size = float(_attr(sz, "val")) / 2 if sz is not None and (_attr(sz, "val") or "").isdigit() else None
    faces = (None, None)
    if fonts is not None:
        faces = (_attr(fonts, "eastAsia") or _attr(fonts, "eastAsiaTheme"), _attr(fonts, "ascii") or _attr(fonts, "asciiTheme"))
    return size, (_on(b) if b is not None else None), faces


def _num_pr(num) -> tuple[str, int] | None:
    if num is None:
        return None
    num_id = _attr(num.find(_w("numId")), "val")
    ilvl = _attr(num.find(_w("ilvl")), "val")
    return (num_id, int(ilvl) if ilvl and ilvl.isdigit() else 0) if num_id is not None else None


@dataclass
class _Level:
    fmt: str = "decimal"
    text: str = ""
    start: int = 1
    suffix: str = "tab"
    legal: bool = False


class _Numbering:
    """Renders list numbers as Word shows them; counters are kept per abstract numbering."""

    def __init__(self, root):
        self.abstract: dict[str, dict[int, _Level]] = {}
        self.nums: dict[str, tuple[str, dict[int, int]]] = {}
        self._counters: dict[str, dict[int, int]] = {}
        self._overridden: set[tuple[str, int]] = set()
        if root is None:
            return
        for abstract in root.findall(_w("abstractNum")):
            levels: dict[int, _Level] = {}
            for lvl in abstract.findall(_w("lvl")):
                start = _attr(lvl.find(_w("start")), "val")
                levels[int(_attr(lvl, "ilvl") or 0)] = _Level(
                    fmt=_attr(lvl.find(_w("numFmt")), "val") or "decimal",
                    text=_attr(lvl.find(_w("lvlText")), "val") or "",
                    start=int(start) if start and start.lstrip("-").isdigit() else 1,
                    suffix=_attr(lvl.find(_w("suff")), "val") or "tab",
                    legal=lvl.find(_w("isLgl")) is not None,
                )
            self.abstract[_attr(abstract, "abstractNumId") or ""] = levels
        for num in root.findall(_w("num")):
            overrides = {}
            for override in num.findall(_w("lvlOverride")):
                start = _attr(override.find(_w("startOverride")), "val")
                if start and start.isdigit():
                    overrides[int(_attr(override, "ilvl") or 0)] = int(start)
            self.nums[_attr(num, "numId") or ""] = (_attr(num.find(_w("abstractNumId")), "val") or "", overrides)

    def next(self, num_id: str, ilvl: int) -> tuple[str, str] | None:
        """(number text, suffix) for the next paragraph of list *num_id* at *ilvl*; None if not numbered."""
        if num_id in ("", "0") or num_id not in self.nums:
            return None
        abstract_id, overrides = self.nums[num_id]
        levels = self.abstract.get(abstract_id, {})
        level = levels.get(ilvl)
        if level is None:
            return None
        counters = self._counters.setdefault(abstract_id, {})
        if ilvl in overrides and (num_id, ilvl) not in self._overridden:
            self._overridden.add((num_id, ilvl))
            counters[ilvl] = overrides[ilvl] - 1
        counters[ilvl] = counters.get(ilvl, level.start - 1) + 1
        for deeper in [k for k in counters if k > ilvl]:
            del counters[deeper]
        if level.fmt == "bullet":
            return _bullet(level.text), level.suffix
        if level.fmt == "none":
            return "", level.suffix
        text = level.text
        for k in range(9):
            if f"%{k + 1}" in text:
                other = levels.get(k, _Level())
                value = counters.get(k, other.start)
                fmt = "decimal" if level.legal else other.fmt
                text = text.replace(f"%{k + 1}", _format_number(value, fmt))
        return text, level.suffix


def _bullet(text: str) -> str:
    return "•" if not text or any(0xE000 <= ord(ch) <= 0xF8FF for ch in text) else text


_CN_DIGITS = "〇一二三四五六七八九"


def _chinese(n: int) -> str:
    if n <= 0 or n >= 10000:
        return str(n)
    units = ["", "十", "百", "千"]
    digits = [int(d) for d in str(n)]
    out, zero = "", False
    for i, d in enumerate(digits):
        place = len(digits) - i - 1
        if d == 0:
            zero = True
            continue
        if zero and out:
            out += "零"
        zero = False
        out += _CN_DIGITS[d] + units[place]
    return out[1:] if out.startswith("一十") else out


def _roman(n: int) -> str:
    table = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
             (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]
    out = ""
    for value, sym in table:
        while n >= value:
            out, n = out + sym, n - value
    return out


def _letters(n: int) -> str:
    return chr(ord("A") + (n - 1) % 26) * ((n - 1) // 26 + 1) if n > 0 else str(n)


def _format_number(n: int, fmt: str) -> str:
    if fmt == "decimalZero":
        return f"{n:02d}"
    if fmt in ("upperRoman", "lowerRoman"):
        r = _roman(n)
        return r if fmt == "upperRoman" else r.lower()
    if fmt in ("upperLetter", "lowerLetter"):
        s = _letters(n)
        return s if fmt == "upperLetter" else s.lower()
    if fmt in ("chineseCounting", "chineseCountingThousand", "japaneseCounting", "taiwaneseCounting",
               "ideographTraditional", "chineseLegalSimplified"):
        return _chinese(n)
    if fmt == "ideographDigital":
        return "".join(_CN_DIGITS[int(d)] for d in str(n))
    if fmt in ("decimalEnclosedCircle", "decimalEnclosedCircleChinese") and 1 <= n <= 20:
        return chr(0x2460 + n - 1)
    if fmt == "decimalFullWidth":
        return "".join(chr(0xFF10 + int(d)) for d in str(n))
    return str(n)


# ── Paragraph content ───────────────────────────────────────────────────


@dataclass
class _Piece:
    """Something found inside a paragraph besides its text."""

    kind: str  # deleted · moved · image · textbox · footnote · endnote · comment · linked_image · chart · diagram
    path: str
    text: str = ""
    rid: str | None = None
    extent: tuple[int, int] | None = None
    note_id: str | None = None
    paragraphs: list[str] = field(default_factory=list)  # textbox: its paragraphs' text


@dataclass
class _Para:
    parts: list[str] = field(default_factory=list)  # text between page breaks
    pieces: list[_Piece] = field(default_factory=list)
    # chars, size, bold, rStyle, (East Asian, ASCII) faces, mostly CJK
    runs: list[tuple[int, float | None, bool | None, str | None, tuple[str | None, str | None], bool]] = \
        field(default_factory=list)
    breaks: int = 0  # explicit page breaks inside the paragraph
    inserted: int = 0  # tracked insertions / move destinations read as content
    fields: list[str] = field(default_factory=list)  # field stack: "instr" | "result"

    def add(self, text: str) -> None:
        if all(state == "result" for state in self.fields):
            self.parts[-1] += text


def _read_paragraph(p, path: str) -> _Para:
    para = _Para(parts=[""])
    _inline(p, para, path)
    return para


def _inline(node, para: _Para, path: str) -> None:
    counts: Counter[str] = Counter()
    for child in node:
        if not isinstance(child.tag, str):
            continue
        name = _qname(child)
        counts[name] += 1
        child_path = f"{path}/{name}[{counts[name]}]"
        if name == "w:r":
            _run(child, para, child_path)
        elif name in ("w:ins", "w:moveTo", "w:hyperlink", "w:smartTag", "w:customXml", "w:fldSimple", "w:dir",
                      "w:bdo"):
            if name in ("w:ins", "w:moveTo"):
                para.inserted += 1
            _inline(child, para, child_path)
        elif name == "w:sdt":
            content = child.find(_w("sdtContent"))
            if content is not None:
                _inline(content, para, f"{child_path}/w:sdtContent")
        elif name in ("w:del", "w:moveFrom"):
            text = "".join(t.text or "" for t in child.iter(_w("delText"), _w("t")))
            if text.strip():
                para.pieces.append(_Piece("deleted" if name == "w:del" else "moved", child_path, text))
        elif name in ("m:oMath", "m:oMathPara"):  # Q9: Office Math as LaTeX, inline or display
            latex = omml_to_latex(child)
            if latex:
                para.add(f"$${latex}$$" if name == "m:oMathPara" else f"${latex}$")
        elif name == "mc:AlternateContent":
            choice = child.find(f"{{{NS['mc']}}}Choice")
            if choice is not None:
                _inline(choice, para, f"{child_path}/mc:Choice[1]")


def _run(r, para: _Para, path: str) -> None:
    rpr = r.find(_w("rPr"))
    size, bold, faces = _run_props(rpr)
    style = _attr(rpr.find(_w("rStyle")), "val") if rpr is not None else None
    before = "".join(para.parts)
    _run_children(r, para, path)
    added = len("".join(para.parts)) - len(before)
    if added:
        text = "".join(para.parts)[len(before):]
        cjk = sum(1 for ch in text if unicodedata.east_asian_width(ch) in "WF")
        para.runs.append((added, size, bold, style, faces, cjk * 2 > len(text.strip() or text)))


def _run_children(node, para: _Para, path: str) -> None:
    counts: Counter[str] = Counter()
    for child in node:
        if not isinstance(child.tag, str):
            continue
        name = _qname(child)
        counts[name] += 1
        child_path = f"{path}/{name}[{counts[name]}]"
        if name == "w:t":
            para.add(child.text or "")
        elif name in ("w:tab", "w:ptab"):
            para.add(" ")
        elif name == "w:br":
            if _attr(child, "type") == "page":
                para.parts.append("")
                para.breaks += 1
            else:
                para.add("\n")
        elif name == "w:cr":
            para.add("\n")
        elif name == "w:noBreakHyphen":
            para.add("-")
        elif name == "w:sym":
            code = _attr(child, "char") or ""
            try:
                ch = chr(int(code, 16))
            except ValueError:
                ch = ""
            if ch and not 0xE000 <= ord(ch) <= 0xF8FF:
                para.add(ch)
        elif name == "w:fldChar":
            kind = _attr(child, "fldCharType")
            if kind == "begin":
                para.fields.append("instr")
            elif kind == "separate" and para.fields:
                para.fields[-1] = "result"
            elif kind == "end" and para.fields:
                para.fields.pop()
        elif name in ("w:drawing", "w:pict", "w:object"):
            _graphics(child, para, child_path)
        elif name == "mc:AlternateContent":
            choice = child.find(f"{{{NS['mc']}}}Choice")
            if choice is not None:
                _run_children(choice, para, f"{child_path}/mc:Choice[1]")
        elif name in ("w:footnoteReference", "w:endnoteReference"):  # Q9: a Markdown footnote reference
            kind = name[2:-9]
            para.add(f"[^{note_label(kind, _attr(child, 'id'))}]")
            para.pieces.append(_Piece(kind, child_path, note_id=_attr(child, "id")))
        elif name == "w:commentReference":
            para.pieces.append(_Piece("comment", child_path, note_id=_attr(child, "id")))


def _graphics(node, para: _Para, path: str) -> None:
    """Images and textboxes inside a drawing, VML picture or embedded object."""
    boxes = list(node.iter(_w("txbxContent")))
    for index, box in enumerate(boxes, 1):
        paragraphs = [_plain_text(p) for p in box.iter(_w("p"))]
        para.pieces.append(_Piece("textbox", f"{path}//w:txbxContent[{index}]", "\n".join(paragraphs).strip(),
                                  paragraphs=paragraphs))
    extent = node.find(f".//{{{NS['wp']}}}extent")
    size = None
    if extent is not None:
        try:
            size = (int(extent.get("cx", 0)) // _EMU_PER_PX, int(extent.get("cy", 0)) // _EMU_PER_PX)
        except ValueError:
            size = None
    rids: list[tuple[str | None, bool]] = []
    for blip in node.iter(f"{{{NS['a']}}}blip"):
        rids.append((blip.get(f"{{{NS['r']}}}embed"), blip.get(f"{{{NS['r']}}}link") is not None))
    for data in node.iter(f"{{{NS['v']}}}imagedata"):
        rids.append((data.get(f"{{{NS['r']}}}id"), False))
    for index, (rid, linked) in enumerate(rids, 1):
        if rid:
            para.pieces.append(_Piece("image", f"{path}#image{index}", rid=rid, extent=size))
        elif linked:
            para.pieces.append(_Piece("linked_image", f"{path}#image{index}"))
    # charts and SmartArt keep their content in a part of their own: not read yet, but accounted for (P4-5, Q9)
    for index, data in enumerate(node.iter(f"{{{NS['a']}}}graphicData"), 1):
        uri = data.get("uri", "")
        kind = "chart" if uri.endswith("/chart") else "diagram" if uri.endswith("/diagram") else None
        if kind is None:
            continue
        ref = next(iter(data), None)
        rid = None if ref is None else ref.get(f"{{{NS['r']}}}id") or ref.get(f"{{{NS['r']}}}dm")
        para.pieces.append(_Piece(kind, f"{path}#{kind}{index}", rid=rid))


def note_label(kind: str, note_id: str | None) -> str:
    """The Markdown footnote label of a Word footnote (its id) or endnote (``e`` + its id)."""
    return f"{'e' if kind == 'endnote' else ''}{note_id or '?'}"


def _plain_text(p) -> str:
    para = _read_paragraph(p, "")
    return "".join(para.parts).strip()


# ── Extraction ──────────────────────────────────────────────────────────


class _Reader:
    def __init__(self, path: Path):
        self.pkg = _Package(path)
        self.part = self.pkg.main
        self.rels = self.pkg.rels(self.part)
        self.styles = _Styles(self.pkg.xml(self._related("styles") or "word/styles.xml"))
        self.numbering = _Numbering(self.pkg.xml(self._related("numbering") or "word/numbering.xml"))
        self.ext = Extraction(format="docx", engines={ENGINE: ENGINE_VERSION})
        self.segment = 1
        self.ext.pages.append(PageState(n=1, unit="docx_segment", status=PageStatus.DONE))
        self.revisions: Counter[str] = Counter()
        self.unsupported: Counter[str] = Counter()
        self.carry: tuple[str, list[str]] | None = None  # text and ledger items of paragraphs whose mark was deleted
        self.notes_seen: set[tuple[str, str]] = set()
        self.vectors: dict[str, bytes] | None = None  # EMF / WMF part → PNG rendering, made on first use
        self.unrendered = 0

    def _related(self, suffix: str) -> str | None:
        return next((t for t, kind in self.rels.values() if kind.endswith(f"/{suffix}")), None)

    # ── ids, ledger, pages ──────────────────────────────────────────────

    def _block_id(self) -> str:
        return ids.block_id_docx(len(self.ext.blocks) + 1)

    def _ledger(self, unit: str, anchor: DocxAnchor, chars: int, disposition: str, block: str) -> str:
        item = ids.ledger_item_docx(len(self.ext.ledger) + 1)
        self.ext.ledger.append(LedgerEntry(item=item, unit=unit, source=anchor, chars=chars,
                                           disposition=disposition, block=block))
        return item

    def _anchor(self, path: str, part: str | None = None, segment: int | None = -1) -> DocxAnchor:
        return DocxAnchor(part=part or self.part, node_path=path, segment=self.segment if segment == -1 else segment)

    def _new_segment(self, kind: str) -> None:
        self.segment += 1
        self.ext.pages.append(PageState(n=self.segment, unit="docx_segment", status=PageStatus.DONE,
                                        starts_with=kind))

    # ── body ────────────────────────────────────────────────────────────

    def run(self) -> Extraction:
        root = self.pkg.xml(self.part)
        body = root.find(_w("body")) if root is not None else None
        if body is None:
            self.ext.warnings.append("no document body found")
            return self.ext
        self._container(body, "/w:body")
        self._flush_carry("/w:body")
        self._furniture()
        self._summaries()
        return self.ext

    def _container(self, node, path: str) -> None:
        counts: Counter[str] = Counter()
        for child in node:
            if not isinstance(child.tag, str):
                continue
            name = _qname(child)
            counts[name] += 1
            child_path = f"{path}/{name}[{counts[name]}]"
            if name == "w:p":
                self._paragraph(child, child_path)
            elif name == "w:tbl":
                self._flush_carry(child_path)
                self._table(child, child_path)
            elif name == "w:sdt":
                content = child.find(_w("sdtContent"))
                if content is not None:
                    self._container(content, f"{child_path}/w:sdtContent")
            elif name == "w:customXml":
                self._container(child, child_path)
            elif name == "mc:AlternateContent":
                choice = child.find(f"{{{NS['mc']}}}Choice")
                if choice is not None:
                    self._container(choice, f"{child_path}/mc:Choice[1]")

    def _paragraph(self, p, path: str) -> None:
        ppr = p.find(_w("pPr"))
        if ppr is not None and _on(ppr.find(_w("pageBreakBefore"))):
            self._flush_carry(path)
            self._new_segment("page_break")
        para = _read_paragraph(p, path)
        self.revisions["inserted"] += para.inserted
        style, prefix = self._style(ppr, para)
        mark_deleted = ppr is not None and ppr.find(f"{_w('rPr')}/{_w('del')}") is not None
        for index, raw in enumerate(para.parts):
            if index:
                self._flush_carry(path)
                self._new_segment("page_break")
            if index == 0 and prefix and raw.strip():
                raw = prefix + raw.lstrip()
            if index == len(para.parts) - 1 and mark_deleted:
                # the paragraph mark is deleted: this text joins the next paragraph (final view)
                carried, items = self.carry or ("", [])
                chars = len("".join(raw.split()))
                self.carry = (carried + raw, items + ([(path, chars)] if chars else []))
                continue
            self._text_block(raw, path, style)
        self._pieces(para.pieces, path)
        if ppr is not None and ppr.find(_w("sectPr")) is not None:
            self._flush_carry(path)
            self._new_segment("section_break")

    def _text_block(self, raw: str, path: str, style: TextStyle | None) -> None:
        carried, items = self.carry or ("", [])
        self.carry = None
        text = _clean(carried + raw)
        if not text:
            return
        block_id = self._block_id()
        anchor = self._anchor(path)
        obs = Observation(id=ids.observation_id(block_id, ENGINE, 1), engine=ENGINE, engine_version=ENGINE_VERSION,
                          task=TaskKind.EXTRACT, anchor=anchor, text=text, style=style, status=ObservationStatus.OK)
        self.ext.blocks.append(Block(
            id=block_id, kind=labels.to_kind(ENGINE, "paragraph"), order=len(self.ext.blocks), anchors=[anchor],
            observations=[obs], chosen_observation=obs.id, text=text, decisions=[_source()]))
        own = len("".join(text.split())) - sum(chars for _, chars in items)
        for item_path, chars in items:
            self._ledger("docx_paragraph", self._anchor(item_path), chars, "merged", block_id)
        if own > 0:
            self._ledger("docx_paragraph", anchor, own, "output", block_id)

    def _flush_carry(self, path: str) -> None:
        """Paragraphs whose mark was deleted but no paragraph follows: they stand alone."""
        if self.carry and _clean(self.carry[0]):
            carried, items = self.carry
            carried = _clean(carried)
            self.carry = None
            first_path = items[0][0] if items else path
            block_id = self._block_id()
            anchor = self._anchor(first_path)
            obs = Observation(id=ids.observation_id(block_id, ENGINE, 1), engine=ENGINE,
                              engine_version=ENGINE_VERSION, task=TaskKind.EXTRACT, anchor=anchor, text=carried,
                              status=ObservationStatus.OK)
            self.ext.blocks.append(Block(
                id=block_id, kind=labels.to_kind(ENGINE, "paragraph"), order=len(self.ext.blocks), anchors=[anchor],
                observations=[obs], chosen_observation=obs.id, text=carried, decisions=[_source()]))
            for item_path, chars in items:
                self._ledger("docx_paragraph", self._anchor(item_path), chars, "output", block_id)
        self.carry = None

    def _style(self, ppr, para: _Para) -> tuple[TextStyle, str]:
        """Style evidence and the rendered list-number prefix ("" when not numbered)."""
        sid = _attr(ppr.find(_w("pStyle")), "val") if ppr is not None else None
        sid = sid or self.styles.default_paragraph
        outline_elem = ppr.find(_w("outlineLvl")) if ppr is not None else None
        outline = None
        if outline_elem is not None and (_attr(outline_elem, "val") or "").isdigit():
            outline = int(_attr(outline_elem, "val"))
        if outline is None:
            outline = self.styles.first(sid, "outline")
        num = _num_pr(ppr.find(_w("numPr"))) if ppr is not None else None
        if num is None:
            num = self.styles.first(sid, "num")
        numbering, prefix = None, ""
        if num is not None:
            rendered = self.numbering.next(num[0], num[1])  # empty numbered paragraphs still count, as in Word
            if rendered is not None:
                numbering = Numbering(num_id=num[0], level=num[1], text=rendered[0])
                if rendered[0]:
                    prefix = rendered[0] + ("" if rendered[1] == "nothing" else " ")
        sizes: Counter[float] = Counter()
        faces: Counter[str] = Counter()
        bold_chars = total = 0
        p_size, p_bold = self.styles.first(sid, "size"), self.styles.first(sid, "bold")
        for chars, size, bold, rstyle, (east_asia, ascii_face), cjk in para.runs:
            slot = "east_asia" if cjk else "ascii"
            face = (east_asia if cjk else ascii_face) or self.styles.first(rstyle, slot) \
                or self.styles.first(sid, slot) or self.styles.default_faces[0 if cjk else 1]
            if face:
                faces[face] += chars
            size = size if size is not None else self.styles.first(rstyle, "size") or p_size or self.styles.default_size
            bold = bold if bold is not None else self.styles.first(rstyle, "bold")
            bold = bold if bold is not None else (p_bold if p_bold is not None else self.styles.default_bold)
            if size is not None:
                sizes[size] += chars
            bold_chars += chars if bold else 0
            total += chars
        return TextStyle(
            font_size=sizes.most_common(1)[0][0] if sizes else p_size or self.styles.default_size,
            bold=(bold_chars * 2 > total) if total else None,
            font=faces.most_common(1)[0][0] if faces else None,
            style_name=self.styles.styles[sid].name if sid in self.styles.styles else None,
            outline_level=outline, numbering=numbering,
        ), prefix

    # ── pieces: revisions, images, unsupported ──────────────────────────

    def _pieces(self, pieces: list[_Piece], path: str) -> None:
        for piece in pieces:
            if piece.kind in ("deleted", "moved"):
                self.revisions[piece.kind] += 1
                self._excluded_revision(piece)
            elif piece.kind == "image":
                self._image(piece)
            elif piece.kind in ("footnote", "endnote", "comment"):
                self._note(piece)
            elif piece.kind in ("chart", "diagram"):
                self._failed(piece.kind, piece.path, self._part_text(piece.rid), labels.to_kind(ENGINE, piece.kind))
            elif piece.kind == "textbox":  # Q44: its paragraphs follow the paragraph that anchors it
                for index, text in enumerate(piece.paragraphs, 1):
                    self._text_block(text, f"{piece.path}/w:p[{index}]", None)
            else:
                self._failed(piece.kind, piece.path, piece.text, labels.to_kind(ENGINE, piece.kind))

    def _excluded_revision(self, piece: _Piece) -> None:
        block_id = self._block_id()
        anchor = self._anchor(piece.path)
        reason = ("tracked deletion; the output is the final view with all changes accepted" if piece.kind == "deleted"
                  else "tracked move source; the moved text appears at its destination")
        self.ext.blocks.append(Block(
            id=block_id, kind=labels.to_kind(ENGINE, "deleted"), order=len(self.ext.blocks),
            status=BlockStatus.EXCLUDED, anchors=[anchor], text=piece.text, decisions=[Decision(
                stage=DecisionStage.EXCLUDE, choice="revision_deleted", reason=reason,
                evidence={"revision": "w:del" if piece.kind == "deleted" else "w:moveFrom",
                          "chars": len("".join(piece.text.split()))}, actor=ACTOR)]))
        self._ledger("docx_deleted", anchor, len("".join(piece.text.split())), "excluded", block_id)

    def _image(self, piece: _Piece) -> None:
        target, _kind = self.rels.get(piece.rid or "", ("", ""))
        if not target or target not in self.pkg.names:
            self._failed("missing_image", piece.path, "", labels.to_kind(ENGINE, "missing_image"))
            return
        data = self.pkg.zip.read(target)
        media = (self.pkg.content_type(target) or "application/octet-stream").replace("image/jpg", "image/jpeg")
        width, height = piece.extent or (0, 0)
        rendered: dict[str, str | int | float | bool] = {}
        try:
            with Image.open(io.BytesIO(data)) as image:
                width, height = image.size
                if media in vector.VECTOR_MEDIA:
                    buf = io.BytesIO()
                    image.save(buf, "PNG")
                    rendered = {"rendered_from": media, "renderer": "pillow"}
                    data, media = buf.getvalue(), "image/png"
        except Exception:  # noqa: BLE001 - unreadable here (e.g. EMF outside Windows): LibreOffice below
            pass
        if media in vector.VECTOR_MEDIA:
            png = self._vector(target)
            if png is not None:
                with Image.open(io.BytesIO(png)) as image:
                    width, height = image.size
                rendered = {"rendered_from": media, "renderer": vector.RENDERER}
                data, media = png, "image/png"
        anchor = self._anchor(piece.path)
        asset = self.ext.add_asset(Asset.from_bytes(data, media_type=media, width=max(width, 1), height=max(height, 1),
                                                    role="original", source=anchor), data)
        block_id = self._block_id()
        self.ext.blocks.append(Block(
            id=block_id, kind=labels.to_kind(ENGINE, "image"), order=len(self.ext.blocks),
            anchors=[anchor, AssetAnchor(asset=asset.id, bbox=(0, 0, asset.width, asset.height),
                                         image_size=(asset.width, asset.height))],
            decisions=[_source(rendered)]))
        self._ledger("docx_image", anchor, 0, "output", block_id)

    def _vector(self, target: str) -> bytes | None:
        """The PNG rendering of an EMF / WMF part; all of the package's are rendered in one go on first use."""
        if self.vectors is None:
            items = {name: (self.pkg.zip.read(name), media) for name in sorted(self.pkg.names)
                     if (media := self.pkg.content_type(name)) in vector.VECTOR_MEDIA}
            rendered = vector.render_vectors(items)
            self.vectors = rendered.images
            if rendered.version is not None:
                self.ext.engines[vector.RENDERER] = rendered.version
        png = self.vectors.get(target)
        if png is None:
            self.unrendered += 1
        return png

    def _note(self, piece: _Piece) -> None:
        kind = piece.kind
        key = (kind, piece.note_id or "")
        if key in self.notes_seen:
            return
        self.notes_seen.add(key)
        part = self._related(f"{kind}s")
        root = self.pkg.xml(part) if part else None
        text, note_path = "", piece.path
        if root is not None:
            for note in root.findall(_w(kind)):
                if _attr(note, "id") == piece.note_id:
                    text = "\n".join(t for t in (_plain_text(p) for p in note.iter(_w("p"))) if t)
                    note_path = f"/w:{kind}s/w:{kind}[@w:id='{piece.note_id}']"
                    break
        if root is None or not text:
            self._failed(kind, note_path, text, labels.to_kind(ENGINE, kind), part=part if root is not None else None)
            return
        anchor = self._anchor(note_path, part=part)  # in the referencing paragraph's segment: rendered after it
        block_id = self._block_id()
        chars = len("".join(text.split()))
        if kind == "comment":  # a reviewer's note, not document content (Q9): excluded, kept in the sidecar
            self.ext.blocks.append(Block(
                id=block_id, kind=labels.to_kind(ENGINE, kind), order=len(self.ext.blocks), status=BlockStatus.EXCLUDED,
                anchors=[anchor], text=text, decisions=[Decision(
                    stage=DecisionStage.EXCLUDE, choice="comment", actor=ACTOR, evidence={"chars": chars},
                    reason="a reviewer's comment, not document content; kept in the sidecar")]))
            self._ledger("docx_comment", anchor, chars, "excluded", block_id)
            return
        # footnotes and endnotes (Q9): a Markdown footnote definition right after the paragraph that refers to it
        text = f"[^{note_label(kind, piece.note_id)}]: " + " ".join(text.split("\n"))
        obs = Observation(id=ids.observation_id(block_id, ENGINE, 1), engine=ENGINE, engine_version=ENGINE_VERSION,
                          task=TaskKind.EXTRACT, anchor=anchor, text=text, status=ObservationStatus.OK)
        self.ext.blocks.append(Block(
            id=block_id, kind=labels.to_kind(ENGINE, kind), order=len(self.ext.blocks), anchors=[anchor],
            observations=[obs], chosen_observation=obs.id, text=text, decisions=[_source()]))
        self._ledger("docx_note", anchor, chars, "output", block_id)

    def _part_text(self, rid: str | None) -> str:
        """The text of the part a relationship names (a chart's titles, series, categories and cached values; a
        SmartArt's node text), one text node per line."""
        target, _kind = self.rels.get(rid or "", ("", ""))
        if not target or target not in self.pkg.names:
            return ""
        root = etree.fromstring(self.pkg.zip.read(target))
        texts = [(node.text or "").strip() for node in root.iter()
                 if isinstance(node.tag, str) and etree.QName(node).localname in ("t", "v")]
        return "\n".join(text for text in texts if text)

    def _failed(self, what: str, path: str, text: str, kind: BlockKind, part: str | None = None) -> None:
        self.unsupported[what] += 1
        block_id = self._block_id()
        anchor = self._anchor(path, part=part, segment=None if part else -1)
        reason = f"{what.replace('_', ' ')} not supported by the Phase 1 DOCX reader; text kept in the sidecar"
        self.ext.blocks.append(Block(
            id=block_id, kind=kind, order=len(self.ext.blocks), status=BlockStatus.FAILED, anchors=[anchor],
            text=text, decisions=[Decision(stage=DecisionStage.CONTENT_SOURCE, choice="unsupported", reason=reason,
                                           evidence={"element": what}, actor=ACTOR)]))
        self.ext.missing.append(Missing(block=block_id, reason=reason))
        self._ledger("docx_unsupported", anchor, len("".join(text.split())), "failed", block_id)

    # ── tables ──────────────────────────────────────────────────────────

    def _table(self, tbl, path: str) -> None:
        rows: list[list[dict]] = []
        header_rows, header_open = 0, True
        pieces: list[_Piece] = []
        open_merge: dict[int, dict] = {}
        nested = 0
        tr_count = 0
        for tr in tbl.iter(_w("tr")):
            if tr.getparent() is not tbl and not _direct_row(tr, tbl):
                continue
            tr_count += 1
            tr_path = f"{path}/w:tr[{tr_count}]"
            trpr = tr.find(_w("trPr"))
            if trpr is not None and trpr.find(_w("del")) is not None:
                text = " ".join(_plain_text(p) for p in tr.iter(_w("p")))
                if text.strip():
                    pieces.append(_Piece("deleted", tr_path, text))
                continue
            is_header = trpr is not None and _on(trpr.find(_w("tblHeader")))
            header_open = header_open and is_header
            header_rows += 1 if header_open else 0
            row: list[dict] = []
            before = _attr(trpr.find(_w("gridBefore")), "val") if trpr is not None else None
            col = int(before) if before and before.isdigit() else 0
            tc_count = 0
            for tc in tr.iter(_w("tc")):
                if _owning_row(tc) is not tr:
                    continue
                tc_count += 1
                tc_path = f"{tr_path}/w:tc[{tc_count}]"
                tcpr = tc.find(_w("tcPr"))
                span_elem = tcpr.find(_w("gridSpan")) if tcpr is not None else None
                span = int(_attr(span_elem, "val")) if span_elem is not None and (_attr(span_elem, "val") or "").isdigit() else 1
                vmerge = tcpr.find(_w("vMerge")) if tcpr is not None else None
                lines, cell_nested = self._cell_text(tc, tc_path, pieces)
                nested += cell_nested
                text = "\n".join(lines)
                continuing = vmerge is not None and _attr(vmerge, "val") not in ("restart",)
                if continuing and col in open_merge and open_merge[col]["colspan"] == span:
                    origin = open_merge[col]
                    origin["rowspan"] += 1
                    if text:
                        origin["text"] = _join(origin["text"], text)
                else:
                    cell = {"row": len(rows), "col": col, "colspan": span, "rowspan": 1, "text": text}
                    row.append(cell)
                    for c in range(col, col + span):
                        open_merge.pop(c, None)
                    if vmerge is not None:
                        open_merge[col] = cell
                col += span
            rows.append(row)
        grid = _grid(rows, header_rows)
        if grid is None:
            self.ext.warnings.append(f"{path}: overlapping cell spans; table kept without spans")
            grid = _grid([[dict(c, rowspan=1, colspan=1) for c in row] for row in rows], header_rows, force=True)
        if nested:
            self.ext.warnings.append(f"{path}: {nested} nested table(s) flattened into their cells")
        block_id = self._block_id()
        anchor = self._anchor(path)
        obs = Observation(id=ids.observation_id(block_id, ENGINE, 1), engine=ENGINE, engine_version=ENGINE_VERSION,
                          task=TaskKind.EXTRACT, anchor=anchor, cells=grid, status=ObservationStatus.OK)
        self.ext.blocks.append(Block(
            id=block_id, kind=labels.to_kind(ENGINE, "table"), order=len(self.ext.blocks), anchors=[anchor],
            observations=[obs], chosen_observation=obs.id, cells=grid, decisions=[_source()]))
        chars = sum(len("".join(c.content.split())) for c in grid.cells)
        self._ledger("docx_table", anchor, chars, "output", block_id)
        self._pieces(pieces, path)

    def _cell_text(self, tc, path: str, pieces: list[_Piece]) -> tuple[list[str], int]:
        lines: list[str] = []
        nested = 0
        counts: Counter[str] = Counter()
        for child in tc:
            if not isinstance(child.tag, str):
                continue
            name = _qname(child)
            counts[name] += 1
            child_path = f"{path}/{name}[{counts[name]}]"
            if name == "w:p":
                para = _read_paragraph(child, child_path)
                _style, prefix = self._style(child.find(_w("pPr")), para)
                text = _clean(" ".join(para.parts))
                if text and prefix:
                    text = prefix + text
                if text:
                    lines.append(text)
                pieces.extend(para.pieces)
            elif name == "w:tbl":
                nested += 1
                for row in child.iter(_w("tr")):
                    cells = [_clean(" ".join(_plain_text(p) for p in tc2.iter(_w("p"))))
                             for tc2 in row.findall(_w("tc"))]
                    if any(cells):
                        lines.append(" | ".join(cells))
            elif name == "w:sdt":
                content = child.find(_w("sdtContent"))
                if content is not None:
                    sub_lines, sub_nested = self._cell_text(content, f"{child_path}/w:sdtContent", pieces)
                    lines.extend(sub_lines)
                    nested += sub_nested
        return lines, nested

    # ── headers, footers, summaries ─────────────────────────────────────

    def _furniture(self) -> None:
        done: set[str] = set()
        for rid, (target, kind) in sorted(self.rels.items(), key=lambda kv: kv[1][0]):
            local = kind.rsplit("/", 1)[-1]
            if local not in ("header", "footer") or target in done:
                continue
            done.add(target)
            root = self.pkg.xml(target)
            if root is None:
                continue
            block_kind = labels.to_kind(ENGINE, local)
            root_name = _qname(root)
            for index, p in enumerate(root.iter(_w("p")), 1):
                text = _plain_text(p)
                if not text:
                    continue
                block_id = self._block_id()
                anchor = DocxAnchor(part=target, node_path=f"/{root_name}//w:p[{index}]")
                self.ext.blocks.append(Block(
                    id=block_id, kind=block_kind, order=len(self.ext.blocks), status=BlockStatus.EXCLUDED,
                    anchors=[anchor], text=text, decisions=[Decision(
                        stage=DecisionStage.EXCLUDE, choice=block_kind.value,
                        reason=f"page furniture: DOCX {local} part", evidence={"part": target}, actor=ACTOR)]))
                self._ledger("docx_paragraph", anchor, len("".join(text.split())), "excluded", block_id)

    def _summaries(self) -> None:
        if self.revisions["inserted"] or self.revisions["deleted"] or self.revisions["moved"]:
            self.ext.warnings.append(
                f"document contains tracked changes ({self.revisions['inserted']} insertions kept, "
                f"{self.revisions['deleted']} deletions and {self.revisions['moved']} move sources excluded); "
                "output is the final view with all changes accepted")
        if self.unrendered:
            self.ext.warnings.append(
                f"{self.unrendered} EMF / WMF image(s) could not be rendered to PNG (LibreOffice missing or unable "
                "to read them); the original files are kept and cannot be described")
        for what, count in sorted(self.unsupported.items()):
            self.ext.warnings.append(
                f"{count} {what.replace('_', ' ')}(s) not supported by the Phase 1 DOCX reader; "
                "text kept in the sidecar, ledger items marked failed")


def extract_docx(path: Path | str) -> Extraction:
    return _Reader(Path(path)).run()


# ── helpers ─────────────────────────────────────────────────────────────


def _source(evidence: dict[str, str | int | float | bool] | None = None) -> Decision:
    return Decision(stage=DecisionStage.CONTENT_SOURCE, choice="docx", reason="read from OOXML",
                    evidence=evidence or {}, actor=ACTOR)


def _clean(text: str) -> str:
    """Tabs become spaces; line breaks inside a paragraph are kept; outer whitespace trimmed."""
    return "\n".join(line.strip() for line in text.replace("\t", " ").split("\n")).strip()


def _join(a: str, b: str) -> str:
    return a + b if a and b else a or b


def _owning_row(tc):
    node = tc.getparent()
    while node is not None and etree.QName(node).localname != "tr":
        node = node.getparent()
    return node


def _direct_row(tr, tbl) -> bool:
    """A row belongs to *tbl* (possibly through content controls), not to a nested table."""
    node = tr.getparent()
    while node is not None and node is not tbl:
        if etree.QName(node).localname == "tbl":
            return False
        node = node.getparent()
    return node is tbl


def _grid(rows: list[list[dict]], header_rows: int, force: bool = False) -> TableGrid | None:
    n_cols = max((c["col"] + c["colspan"] for row in rows for c in row), default=0)
    cells = [Cell(row=c["row"], col=c["col"], rowspan=c["rowspan"], colspan=c["colspan"], content=c["text"],
                  is_header=c["row"] < header_rows) for row in rows for c in row]
    try:
        return TableGrid(n_rows=len(rows), n_cols=n_cols, cells=cells, header_rows=min(header_rows, len(rows)))
    except ValueError:
        if not force:
            return None
        raise
