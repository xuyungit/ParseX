"""Rendering (guide §4.5, plan P1-6): Markdown contract, evaluator compatibility, sidecar schema."""

import json

from parserx.eval.normalize import canonicalize
from parserx.ir.anchor import AssetAnchor, DocxAnchor, PdfAnchor
from parserx.ir.asset import Asset
from parserx.ir.block import Block
from parserx.ir.enums import BlockKind, BlockStatus, DocumentStatus, EvidenceLevel, PageStatus
from parserx.ir.schema import validate_sidecar
from parserx.ir.semantic import ChartSemantic, Evidenced, GenericSemantic, Series
from parserx.ir.state import DocumentState, LedgerEntry, PageState
from parserx.render import export_sidecar, render_markdown, sidecar_json, write_export
from parserx.tables import Cell, TableGrid, find_tables

ASSET = Asset.from_bytes(b"\x89PNG-img", media_type="image/png", width=40, height=30, role="original")


def _pdf(page, y=0):
    return PdfAnchor(page=page, bbox=(0, y, 100, y + 10), coord_space="page_pt")


def _block(bid, kind, order, page=1, **kw):
    return Block(id=bid, kind=kind, order=order, anchors=kw.pop("anchors", [_pdf(page, order)]), **kw)


def _figure(bid, order, page=1, semantic=None, status=BlockStatus.OK, kind=BlockKind.FIGURE):
    anchors = [_pdf(page, order), AssetAnchor(asset=ASSET.id, bbox=(0, 0, 40, 30), image_size=(40, 30))]
    return Block(id=bid, kind=kind, order=order, anchors=anchors, semantic=semantic, status=status)


def _state(blocks, *, pages=2, fmt="pdf", page_states=None):
    return DocumentState(
        id="d", source="x.pdf", source_sha256="0" * 64, format=fmt, status=DocumentStatus.COMPLETE,
        pages=page_states or [PageState(n=n, unit="pdf_page", status=PageStatus.DONE) for n in range(1, pages + 1)],
        blocks=blocks, assets=[ASSET],
    )


_SPANNED = TableGrid(n_rows=2, n_cols=2, header_rows=1, cells=[
    Cell(row=0, col=0, colspan=2, content="合计", is_header=True), Cell(row=1, col=0, content="1"),
    Cell(row=1, col=1, content="2")])
_PLAIN = TableGrid(n_rows=2, n_cols=2, header_rows=1, cells=[
    Cell(row=0, col=0, content="名称", is_header=True), Cell(row=0, col=1, content="数值", is_header=True),
    Cell(row=1, col=0, content="甲"), Cell(row=1, col=1, content="10")])


def test_headings_paragraphs_and_page_anchors():
    md = render_markdown(_state([
        _block("b1", BlockKind.TITLE, 0, level=1, text="总则"),
        _block("b2", BlockKind.TEXT, 1, text="第一行中文\n接着一行\nand English\nwords"),
        _block("b3", BlockKind.TITLE, 2, text="待定标题"),  # role known, level pending: no heading marker
    ]))
    assert md == ("<!-- PAGE 1 -->\n\n# 总则\n\n第一行中文接着一行 and English words\n\n待定标题\n\n"
                  "<!-- PAGE 2 -->\n")


def test_tables_render_as_gfm_or_html_and_are_found():
    md = render_markdown(_state([_block("t1", BlockKind.TABLE, 0, cells=_PLAIN),
                                 _block("t2", BlockKind.TABLE, 1, cells=_SPANNED)], pages=1))
    spans = find_tables(md)
    assert [s.fmt for s in spans] == ["gfm", "html"]
    assert spans[0].grid == _PLAIN and spans[1].grid == _SPANNED


def test_figure_semantic_block_is_stripped_by_the_evaluator():
    chart = ChartSemantic(
        chart_type=Evidenced(value="bar", level=EvidenceLevel.VISIBLE),
        title=Evidenced(value="月产量", level=EvidenceLevel.VISIBLE),
        series=[Series(name=Evidenced(value="产量", level=EvidenceLevel.VISIBLE),
                       values=[Evidenced(value=120, level=EvidenceLevel.VISIBLE),
                               Evidenced(value=150, level=EvidenceLevel.ESTIMATED)])])
    md = render_markdown(_state([_block("p", BlockKind.TEXT, 0, text="正文"), _figure("f", 1, semantic=chart),
                                 _block("q", BlockKind.TEXT, 2, text="后文")], pages=1))
    image_file = ASSET.path.split("/")[-1]
    assert f"![chart: 月产量](images/{image_file})\n\n> [图片语义] chart · bar（可见）\n" in md
    assert "> 系列 产量：120（可见）；150（估读）" in md
    canonical = canonicalize(md)
    assert canonical.image_count == 1
    assert "月产量" not in canonical.text and "正文" in canonical.text and "后文" in canonical.text


def test_figure_without_semantic_is_just_the_image():
    md = render_markdown(_state([_figure("f", 0)], pages=1))
    assert md.endswith("![图片](images/" + ASSET.path.split("/")[-1] + ")\n")


def test_hidden_blocks_are_not_rendered_but_degraded_and_unrecognised_scans_are():
    md = render_markdown(_state([
        _block("a", BlockKind.TEXT, 0, text="保留", status=BlockStatus.DEGRADED),
        _block("b", BlockKind.TEXT, 1, text="重复", status=BlockStatus.DUPLICATE),
        _block("c", BlockKind.HEADER, 2, text="页眉", status=BlockStatus.EXCLUDED),
        _block("d", BlockKind.OTHER, 3, text="文本框", status=BlockStatus.FAILED),
        _figure("s", 4, kind=BlockKind.SCAN),  # scan engine gave no result: the page image stays visible
        _figure("m", 5, kind=BlockKind.SCAN, status=BlockStatus.MERGED),
    ], pages=1))
    assert "保留" in md and "重复" not in md and "页眉" not in md and "文本框" not in md
    assert md.count("![") == 1


def test_block_text_cannot_turn_into_markup():
    md = render_markdown(_state([_block("a", BlockKind.TEXT, 0, text="# 不是标题"),
                                 _block("b", BlockKind.TEXT, 1, text="> 不是引用")], pages=1))
    assert "\\# 不是标题" in md and "\\> 不是引用" in md


def test_formulas_are_delimited_once():
    md = render_markdown(_state([_block("a", BlockKind.FORMULA, 0, text="E = mc^2"),
                                 _block("b", BlockKind.FORMULA, 1, text="$$a+b$$")], pages=1))
    assert "$$\nE = mc^2\n$$" in md and "$$a+b$$" in md and "$$\n$$a+b" not in md


def test_docx_segments_render_break_and_section_anchors():
    def seg(n):
        return [DocxAnchor(part="word/document.xml", node_path=f"/w:body/w:p[{n}]", segment=n)]

    pages = [PageState(n=1, unit="docx_segment", status=PageStatus.DONE),
             PageState(n=2, unit="docx_segment", status=PageStatus.DONE, starts_with="page_break"),
             PageState(n=3, unit="docx_segment", status=PageStatus.DONE, starts_with="section_break")]
    md = render_markdown(_state([_block("a", BlockKind.TEXT, 0, anchors=seg(1), text="一"),
                                 _block("b", BlockKind.TEXT, 1, anchors=seg(2), text="二"),
                                 _block("c", BlockKind.TEXT, 2, anchors=seg(3), text="三")],
                                fmt="docx", page_states=pages))
    assert md == "一\n\n<!-- PAGE-BREAK -->\n\n二\n\n<!-- SECTION 2 -->\n\n三\n"


def test_rendering_is_deterministic():
    state = _state([_block("a", BlockKind.TEXT, 0, text="x"), _figure("f", 1)], pages=1)
    assert render_markdown(state) == render_markdown(state.model_copy(deep=True))


def test_sidecar_passes_the_schema_and_is_byte_stable():
    photo = GenericSemantic(type="photo", summary=Evidenced(value="桥", level=EvidenceLevel.INFERRED))
    state = _state([_block("a", BlockKind.TEXT, 0, text="正文"), _figure("f", 1, semantic=photo)], pages=1)
    state.ledger.append(LedgerEntry(item="i-1", unit="native_line", source=_pdf(1), chars=2, disposition="output",
                                    block="a"))
    data = export_sidecar(state)
    assert validate_sidecar(data) == [] and data["accounting"]["output"] == 1
    assert sidecar_json(state) == sidecar_json(state.model_copy(deep=True))
    assert json.loads(sidecar_json(state)) == data


def test_export_writes_the_package(tmp_path):
    # Q42: Markdown, every extracted image (hidden ones too), the document summary and the block sidecar
    decorative = Asset.from_bytes(b"\x89PNG-icon", media_type="image/png", width=20, height=20, role="original")
    ws = tmp_path / "ws"
    (ws / "assets").mkdir(parents=True)
    (ws / ASSET.path).write_bytes(b"\x89PNG-img")
    (ws / decorative.path).write_bytes(b"\x89PNG-icon")
    icon = Block(id="icon", kind=BlockKind.FIGURE, order=2, status=BlockStatus.EXCLUDED,
                 anchors=[_pdf(1, 2), AssetAnchor(asset=decorative.id, bbox=(0, 0, 20, 20), image_size=(20, 20))])
    photo = GenericSemantic(type="photo", summary=Evidenced(value="一座桥", level=EvidenceLevel.INFERRED))
    state = _state([_block("h", BlockKind.TITLE, 0, text="第一章", level=1), _figure("f", 1, semantic=photo), icon],
                   pages=1)
    state.assets = [ASSET, decorative]
    paths = write_export(state, ws, tmp_path / "out", "doc")
    assert paths.markdown.read_text() == render_markdown(state)
    assert sorted(p.name for p in (tmp_path / "out" / "images").iterdir()) == sorted(
        [ASSET.path.split("/")[-1], decorative.path.split("/")[-1]])
    assert decorative.path.split("/")[-1] not in paths.markdown.read_text()
    assert validate_sidecar(json.loads(paths.sidecar.read_text())) == []
    summary = json.loads(paths.summary.read_text())
    assert paths.summary.name == "doc.json" and summary["status"] == "complete" and summary["pages"] == 1
    assert summary["outline"] == [{"level": 1, "text": "第一章", "block": "h", "page": 1}]
    images = {i["block"]: i for i in summary["images"]}
    assert images["f"]["shown"] is True and images["f"]["type"] == "photo" and images["f"]["summary"] == "一座桥"
    assert images["icon"]["shown"] is False and images["icon"]["file"].startswith("images/")
    assert summary["files"] == {"markdown": "doc.md", "blocks": "doc.blocks.json", "images": "images/"}


def test_blocks_joined_by_continues_render_as_one_paragraph():
    from parserx.ir.relation import Relation

    blocks = [_block("a", BlockKind.TEXT, 0, page=1, text="跨页的句子前半"), _block("x", BlockKind.TEXT, 1, page=1, text="页末"),
              _block("b", BlockKind.TEXT, 2, page=2, text="后半，完。"), _block("c", BlockKind.TEXT, 3, page=2, text="新段")]
    state = _state(blocks)
    state.relations = [Relation(id="r-continues-a-b", kind="continues", src="a", dst="b")]
    md = render_markdown(state)
    assert "跨页的句子前半后半，完。" in md and md.count("后半") == 1
    assert md.index("跨页的句子前半后半") < md.index("<!-- PAGE 2 -->") < md.index("新段")
    state.relations = []
    assert "跨页的句子前半\n\n页末" in render_markdown(state)  # without the relation nothing changes


def test_pictures_taken_from_a_table_follow_it_with_a_note():
    from parserx.ir.relation import Relation

    state = _state([_block("t", BlockKind.TABLE, 0, cells=_PLAIN), _figure("f", 1), _block("p", BlockKind.TEXT, 2, text="后文")],
                   pages=1)
    state.relations = [Relation(id="r-contains-t-f", kind="contains", src="t", dst="f")]
    md = render_markdown(state)
    assert md.index("| 甲 | 10 |") < md.index("<!-- 以下是上方〔图 n〕处的图片 -->") < md.index("![图片]") < md.index("后文")

