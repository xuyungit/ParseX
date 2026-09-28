"""Rendering (guide §4.5, plan P1-6): Markdown contract, evaluator compatibility, sidecar schema."""

import json

from parserx.eval.normalize import canonicalize
from parserx.ir.anchor import AssetAnchor, DocxAnchor, PdfAnchor
from parserx.ir.asset import Asset
from parserx.ir.block import Block
from parserx.ir.enums import BlockKind, BlockStatus, DocumentStatus, EvidenceLevel, PageStatus, RelationKind
from parserx.ir.schema import validate_sidecar
from parserx.ir.semantic import ChartSemantic, Evidenced, FigureNote, GenericSemantic, Series
from parserx.ir.state import DocumentState, LedgerEntry, PageState, ReadLine
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


def test_a_figure_note_follows_its_image_and_is_stripped_by_the_evaluator():
    # Q118, Q121: the type as the image's text, the note in one line after it; older shapes render the same way
    note = FigureNote(type="chart", caption="柱状图：各月产量，6 月最高，约 150 吨。")
    md = render_markdown(_state([_block("p", BlockKind.TEXT, 0, text="正文"), _figure("f", 1, semantic=note),
                                 _block("q", BlockKind.TEXT, 2, text="后文")], pages=1))
    image_file = ASSET.path.split("/")[-1]
    assert f"![图表](images/{image_file})\n\n> 图片说明：柱状图：各月产量，6 月最高，约 150 吨。\n" in md
    canonical = canonicalize(md)
    assert canonical.image_count == 1
    assert "产量" not in canonical.text and "正文" in canonical.text and "后文" in canonical.text
    english = render_markdown(_state([_figure("f", 1, semantic=note)], pages=1), lang="en")
    assert f"![Chart](images/{image_file})\n\n> Image description: 柱状图" in english
    older = ChartSemantic(chart_type=Evidenced(value="bar", level=EvidenceLevel.VISIBLE),
                          title=Evidenced(value="月产量", level=EvidenceLevel.VISIBLE))
    assert "> 图片说明：bar：月产量" in render_markdown(_state([_figure("f", 1, semantic=older)], pages=1))


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
    # Q116: images/ holds what the Markdown links; the excluded icon stays in the work package only
    assert sorted(p.name for p in (tmp_path / "out" / "images").iterdir()) == [ASSET.path.split("/")[-1]]
    assert decorative.path.split("/")[-1] not in paths.markdown.read_text()
    assert validate_sidecar(json.loads(paths.sidecar.read_text())) == []
    summary = json.loads(paths.summary.read_text())
    assert paths.summary.name == "doc.json" and summary["status"] == "complete" and summary["pages"] == 1
    assert summary["outline"] == [{"level": 1, "text": "第一章", "block": "h", "page": 1}]
    images = {i["block"]: i for i in summary["images"]}
    assert images["f"]["shown"] is True and images["f"]["type"] == "photo" and images["f"]["summary"] == "一座桥"
    assert images["icon"]["shown"] is False and images["icon"]["file"].startswith("images/")
    assert summary["files"] == {"markdown": "doc.md", "blocks": "doc.blocks.json", "images": "images/"}
    assert summary["review"] == {"open": 0, "by_kind": {}, "items": [], "checked": 0, "checked_by_kind": {},
                                 "occluded": []}  # processing done, nothing left to check


def test_the_summary_lists_what_is_left_to_check_apart_from_the_status(tmp_path):
    # Q30: "processing complete" (status) and "review complete" (no open items) are reported separately
    ws = tmp_path / "ws"
    (ws / "assets").mkdir(parents=True)
    (ws / ASSET.path).write_bytes(b"\x89PNG-img")
    state = _state([_block("h", BlockKind.TITLE, 0, text="待定标题")], pages=1)  # a title whose level is open
    paths = write_export(state, ws, tmp_path / "out", "doc")
    summary = json.loads(paths.summary.read_text())
    assert summary["status"] == "complete" and summary["review"]["open"] == 1
    assert summary["review"]["items"][0]["target"] == "h" and sum(summary["review"]["by_kind"].values()) == 1


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



def test_every_linked_image_is_exported_and_working_images_are_not(tmp_path):
    # a figure rendered from the page (its embedded bytes unusable, e.g. a seal) is linked, so it is exported;
    # a page render and a crop made to look at a region (read / ask_image) are working images
    rendered = Asset.from_bytes(b"\x89PNG-seal", media_type="image/png", width=171, height=114, role="render",
                                source=_pdf(1), dpi=144.0)
    page = Asset.from_bytes(b"\x89PNG-page", media_type="image/png", width=1241, height=827, role="render",
                            source=_pdf(1), dpi=150.0)
    look = Asset.from_bytes(b"\x89PNG-look", media_type="image/png", width=600, height=300, role="crop",
                            derived_from=page.id)
    ws = tmp_path / "ws"
    (ws / "assets").mkdir(parents=True)
    for asset in (rendered, page, look):
        (ws / asset.path).write_bytes(asset.id.encode())
    seal = Block(id="seal", kind=BlockKind.FIGURE, order=0, status=BlockStatus.OK,
                 anchors=[_pdf(1), AssetAnchor(asset=rendered.id, bbox=(0, 0, 171, 114), image_size=(171, 114))])
    state = _state([seal], pages=1)
    state.assets = [rendered, page, look]
    paths = write_export(state, ws, tmp_path / "out", "doc")
    linked = rendered.path.split("/")[-1]
    assert f"](images/{linked})" in paths.markdown.read_text()
    assert [p.name for p in (tmp_path / "out" / "images").iterdir()] == [linked]
    assert [i["file"] for i in json.loads(paths.summary.read_text())["images"]] == [f"images/{linked}"]


def test_a_shown_image_without_description_or_text_is_a_review_item():
    # Q66: a shown image carries its content to a reader who cannot see it — a description, or its text after it
    from parserx.ir.relation import Relation
    from parserx.tools.envelope import UnresolvedKind
    from parserx.tools.views import unresolved_items

    photo = GenericSemantic(type="photo", summary=Evidenced(value="桥", level=EvidenceLevel.INFERRED))
    from parserx.ir.enums import ObservationStatus, TaskKind
    from parserx.ir.observation import Observation

    failed = Observation(id="o-bare", engine="vlm", engine_version="gpt-6-luna", task=TaskKind.DESCRIBE,
                         anchor=_pdf(1), status=ObservationStatus.FAILED, error="timeout")
    bare = _figure("bare", 1)
    bare.observations = [failed]  # the description was attempted and failed
    blocks = [_figure("described", 0, semantic=photo), bare, _figure("read", 2), _figure("emptied", 3),
              _figure("untried", 7),
              _figure("hidden", 4, status=BlockStatus.EXCLUDED),
              _block("read-t", BlockKind.TEXT, 5, text="图中的文字"),
              _block("emptied-t", BlockKind.TITLE, 6, text="税", status=BlockStatus.EXCLUDED)]
    state = _state(blocks, pages=1)
    state.relations = [Relation(id=f"r-{f}", kind=RelationKind.CONTAINS, src=f, dst=f"{f}-t") for f in ("read", "emptied")]
    items = [u.target for u in unresolved_items(state) if u.kind == UnresolvedKind.FIGURE_WITHOUT_CONTENT]
    assert items == ["bare", "emptied"]


def test_missing_content_is_said_where_it_is():
    # Q117: a page image whose content was not read keeps its image and gets a note after it; a failed block that is
    # not shown gets the note in its place; evaluation ignores the notes
    from parserx.ir.state import Missing

    scan = _figure("s", 1, kind=BlockKind.SCAN)
    table = _block("t", BlockKind.TABLE, 2, status=BlockStatus.FAILED)
    state = _state([_block("h", BlockKind.TITLE, 0, text="概述", level=1), scan, table], pages=1)
    state.assets = [ASSET]
    state.missing = [Missing(block="s", reason="scan engine: scan engine not configured (builders.ocr)"),
                     Missing(block="t", reason="vlm budget exhausted")]
    md = render_markdown(state)
    lines = md.rstrip("\n").split("\n\n")
    assert lines[3] == "> 〔未识别〕第 1 页：扫描内容未能识别（未配置识别服务）" and lines[2].startswith("![扫描图像]")
    assert lines[4] == "> 〔未识别〕第 1 页：一个表格未能识别（超出处理预算）"
    assert "page 1: scanned content could not be read" in render_markdown(state, lang="en")
    assert "未识别" not in canonicalize(md).text


# ── Text read inside an image (IO6-5) ──────────────────────────────────


def _read_image(kind="content", reading=("营业执照", "名称", "数值", "甲", "10"), children=True):
    """A figure the scan engine read: a title and a table follow it, contained by it; *reading* is the local
    reading of its image (None: not read)."""
    from parserx.ir.enums import ImageRoute
    from parserx.ir.relation import Relation
    from parserx.ir.state import ImageRecord

    inside = AssetAnchor(asset=ASSET.id, bbox=(0, 0, 40, 10), image_size=(40, 30))
    blocks = [_block("p", BlockKind.TEXT, 0, text="正文"),
              _figure("f", 1, semantic=FigureNote(type=kind, caption="某公司营业执照（副本）")),
              _block("q", BlockKind.TEXT, 4, text="后文")]
    if children:
        blocks[2:2] = [Block(id="f-r1", kind=BlockKind.TITLE, order=2, level=2, anchors=[inside], text="营业执照"),
                       Block(id="f-r2", kind=BlockKind.TABLE, order=3, anchors=[inside], cells=_PLAIN)]
    state = _state(blocks, pages=1)
    if children:
        state.relations = [Relation(id=f"r-contains-f-{c}", kind=RelationKind.CONTAINS, src="f", dst=c)
                           for c in ("f-r1", "f-r2")]
    state.images = [ImageRecord(id=ASSET.id, route=ImageRoute.MIXED, shown=True, reading=None if reading is None else
                                [ReadLine(bbox=(1, 1 + 2 * i, 39, 2 + 2 * i), text=t, score=0.9)
                                 for i, t in enumerate(reading)])]
    return state


def test_a_content_image_read_in_full_is_its_text_in_a_labelled_quote():
    # the image is not shown: its text is quoted under 〔图片识别〕 with the note and a link to the original;
    # a title read in it is bold, not a heading of the document; comments tell a program where it came from
    image = ASSET.path.split("/")[-1]
    md = render_markdown(_read_image())
    assert f"![" not in md
    assert (f'正文\n\n<!-- parserx:image-text src="images/{image}" page=1 -->\n'
            f"> **〔图片识别〕** 某公司营业执照（副本）　[原图](images/{image})\n>\n> **营业执照**\n>\n"
            f"> | 名称 | 数值 |\n> | --- | --- |\n> | 甲 | 10 |\n<!-- /parserx:image-text -->\n\n后文") in md
    english = render_markdown(_read_image(), lang="en")
    assert f"> **[Text from image]** 某公司营业执照（副本）　[original](images/{image})" in english
    canonical = canonicalize(md)  # the evaluator reads the transcription as text, without the label
    assert "营业执照" in canonical.text and "甲 10" in canonical.text and "副本" not in canonical.text


def test_a_content_image_whose_reading_the_text_lacks_is_shown_too():
    # the local reading of the image has a line the transcription does not: the image stays, above its text
    image = ASSET.path.split("/")[-1]
    for state in (_read_image(reading=("营业执照", "统一社会信用代码 91510000")), _read_image(reading=None)):
        md = render_markdown(state)
        assert md.index(f"![图片](images/{image})") < md.index("<!-- parserx:image-text") < md.index("**〔图片识别〕**")
        assert "[原图]" not in md and "> 图片说明" not in md  # the note is on the label line
        assert "甲 10" in canonicalize(md).text  # not taken for a description


def test_text_read_in_a_picture_follows_its_note():
    # a picture (no description said content) whose text was read, e.g. by its route: image, note, then the text
    md = render_markdown(_read_image(kind="screenshot"))
    assert md.index("![截图]") < md.index("> 图片说明：某公司营业执照（副本）") < md.index("> **〔图片识别〕**\n>\n> **营业执照**")


def test_a_scanned_page_is_marked():
    state = _state([_block("s", BlockKind.TEXT, 0, text="扫描出的文字"), _block("n", BlockKind.TEXT, 1, page=2,
                                                                          text="原生文字")])
    state.ledger = [LedgerEntry(item="i-s", unit="ocr_block", chars=6, disposition="output", block="s",
                                source=PdfAnchor(page=1, bbox=(0, 0, 10, 10), coord_space="image_px",
                                                  image_size=(100, 100))),
                    LedgerEntry(item="i-n", unit="native_line", chars=4, disposition="output", block="n",
                                source=_pdf(2))]
    md = render_markdown(state)
    assert "<!-- PAGE 1 scanned -->" in md and "<!-- PAGE 2 -->" in md
    assert "扫描出的文字" in canonicalize(md).text
    state.ledger.append(LedgerEntry(item="i-f", unit="ocr_block", chars=4, disposition="output", block="n",
                                    source=PdfAnchor(page=2, bbox=(0, 0, 10, 10), coord_space="image_px",
                                                     image_size=(100, 100))))  # a formula read from page 2's image
    assert "<!-- PAGE 2 -->" in render_markdown(state)  # its text layer is still output: a native page


def test_a_content_image_text_the_transcription_lacks_is_open_work():
    from parserx.tools.envelope import UnresolvedKind
    from parserx.tools.views import unresolved_items

    lacking = [u for u in unresolved_items(_read_image(reading=("营业执照", "统一社会信用代码 91510000")))
               if u.kind == UnresolvedKind.TEXT_UNACCOUNTED]
    assert [(u.target, [q.doc_text for q in u.quotes]) for u in lacking] == [("f", ["统一社会信用代码 91510000"])]
    assert not [u for u in unresolved_items(_read_image()) if u.kind == UnresolvedKind.TEXT_UNACCOUNTED]


def test_a_formula_read_in_an_image_keeps_the_image():
    # the local reader does not read formulas: the transcription cannot be checked, so the image stays above it;
    # a formula the engine labelled a title is not set in bold
    from parserx.tools.envelope import UnresolvedKind
    from parserx.tools.views import unresolved_items

    state = _read_image(reading=("[6a]=[F]",))
    title = next(b for b in state.blocks if b.id == "f-r1")
    title.text = "$ [\\delta_{a}]=[F] $"
    md = render_markdown(state)
    assert md.index("![图片]") < md.index("**〔图片识别〕**") < md.index("> $ [\\delta_{a}]=[F] $")
    assert "**$" not in md and "[原图]" not in md
    assert not [u for u in unresolved_items(state) if u.kind == UnresolvedKind.TEXT_UNACCOUNTED]


def test_text_in_a_seal_or_excluded_as_furniture_is_not_lacking():
    # compared as a page is: a seal the layout detector found in the image, or a footer the scan engine read and
    # excluded, is not text the transcription lacks
    from parserx.ir.enums import ObservationStatus, TaskKind
    from parserx.ir.observation import Observation
    from parserx.ir.relation import Relation
    from parserx.reading.compare import lacking_in_transcription

    state = _read_image(reading=("营业执照", "发票专用章", "国家市场监督管理总局监制"))
    figure = next(b for b in state.blocks if b.id == "f")
    assert lacking_in_transcription(state, figure) == ["发票专用章", "国家市场监督管理总局监制"]
    figure.observations.append(Observation(
        id="o-f-layout-1", engine="layout", engine_version="d", task=TaskKind.LAYOUT, label="seal",
        status=ObservationStatus.OK, anchor=AssetAnchor(asset=ASSET.id, bbox=(0, 2, 40, 4), image_size=(40, 30))))
    footer = Block(id="f-r3", kind=BlockKind.FOOTER, order=3, status=BlockStatus.EXCLUDED, text="国家市场监督管理总局监制",
                   anchors=[AssetAnchor(asset=ASSET.id, bbox=(0, 25, 40, 30), image_size=(40, 30))])
    state.blocks.append(footer)
    state.relations.append(Relation(id="r-contains-f-f-r3", kind=RelationKind.CONTAINS, src="f", dst="f-r3"))
    assert lacking_in_transcription(state, figure) == []
