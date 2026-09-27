"""Cross-page paragraph continuation (guide §6.9) on synthetic block lists."""

from parserx.content.continuation import propose_continuations
from parserx.ir.anchor import DocxAnchor, PdfAnchor
from parserx.ir.block import Block
from parserx.ir.enums import BlockKind, BlockStatus, DocumentStatus, ObservationStatus, PageStatus, TaskKind
from parserx.ir.observation import Observation, TextStyle
from parserx.ir.relation import Relation
from parserx.ir.state import DocumentState, PageState

T, TITLE, FIG, CAP, FORMULA = BlockKind.TEXT, BlockKind.TITLE, BlockKind.FIGURE, BlockKind.CAPTION, BlockKind.FORMULA


def _block(bid, kind, page, text="", size=None, bold=None, status=BlockStatus.OK):
    anchor = PdfAnchor(page=page, bbox=(0, 0, 100, 10), coord_space="page_pt")
    observations = []
    if size is not None or bold is not None:
        observations = [Observation(id=f"o-{bid}", engine="native_pdf", engine_version="1", task=TaskKind.EXTRACT,
                                    anchor=anchor, text=text, style=TextStyle(font_size=size, bold=bold),
                                    status=ObservationStatus.OK)]
    return Block(id=bid, kind=kind, order=0, anchors=[anchor], text=text, status=status, observations=observations,
                 chosen_observation=observations[0].id if observations else None)


def _state(blocks, fmt="pdf"):
    for order, block in enumerate(blocks):
        block.order = order
    pages = sorted({b.anchors[0].page for b in blocks if isinstance(b.anchors[0], PdfAnchor)}) or [1, 2]
    return DocumentState(id="d", source="x.pdf", source_sha256="0" * 64, format=fmt,
                         status=DocumentStatus.COMPLETE, blocks=blocks,
                         pages=[PageState(n=n, unit="pdf_page", status=PageStatus.DONE)
                                for n in range(1, max(pages) + 1)])


def _pairs(state):
    return [(c["first"], c["second"]) for c in propose_continuations(state)]


def test_a_sentence_cut_by_the_page_continues_across_furniture():
    state = _state([
        _block("a", T, 1, "供货方应在合同签订后分两批交货，第一批"),
        _block("f", BlockKind.FOOTER, 1, "第 1 页", status=BlockStatus.EXCLUDED),
        _block("n", BlockKind.FOOTNOTE, 1, "① 注释。"),
        _block("h", BlockKind.HEADER, 2, "某工程采购说明", status=BlockStatus.EXCLUDED),
        _block("b", T, 2, "不少于总量的 60%。"),
    ])
    assert [(c["op"], c["first"], c["second"]) for c in propose_continuations(state)] == [("join", "a", "b")]


def test_finished_sentences_and_new_items_do_not_continue():
    for end, start in [("第一批已交货。", "第二批"), ("规定如下：", "本条适用于"), ("完成（见附件）。”", "后续"),
                       ("主要包括", "2.3 材料"), ("主要包括", "（二）材料"), ("主要包括", "• 材料")]:
        assert _pairs(_state([_block("a", T, 1, end), _block("b", T, 2, start)])) == [], (end, start)


def test_symbols_and_repeated_lines_are_not_sentences_cut_by_the_page():
    # interface labels ("管理订阅 ›"), and the same line again on the next page
    assert _pairs(_state([_block("a", T, 1, "管理订阅 ›"), _block("b", T, 2, "查看账单")])) == []
    assert _pairs(_state([_block("a", T, 1, "某工程采购说明"), _block("b", T, 2, "某工程采购说明")])) == []
    assert _pairs(_state([_block("a", T, 1, "材料分为钢材、"), _block("b", T, 2, "水泥和砂石。")])) == [("a", "b")]


def test_caption_labels_start_something_new():
    # a figure label on each page of drawings, or a caption the layout did not mark as one
    assert _pairs(_state([_block("a", T, 1, "图3"), _block("b", T, 2, "图4")])) == []
    assert _pairs(_state([_block("a", T, 1, "can be inserted into the graph,"),
                          _block("b", T, 2, "Figure 10: TensorBoard graph visualization")])) == []


def test_a_closing_bracket_after_an_unfinished_sentence_continues():
    assert _pairs(_state([_block("a", T, 1, "应符合现行标准（见表 1）"), _block("b", T, 2, "的规定。")])) == [("a", "b")]


def test_english_continues_across_the_page():
    state = _state([_block("a", T, 1, "The results of the"), _block("b", T, 2, "experiment show a clear trend.")])
    assert _pairs(state) == [("a", "b")]


def test_only_page_furniture_may_lie_between_the_two_parts():
    # after a figure the text may be a new step (a screenshot in a how-to) or the rest of the sentence: the agent's call
    floats = [_block("a", T, 1, "然后使用菜单“清理”"), _block("fig", FIG, 2), _block("cap", CAP, 2, "图 1 对话框"),
              _block("b", T, 2, "在弹出的对话框里填入步骤")]
    assert _pairs(_state(floats)) == []
    for kind in (FORMULA, TITLE):
        blocks = [_block("a", T, 1, "其计算式为"), _block("x", kind, 2, "M = F·l"), _block("b", T, 2, "式中 F 为荷载。")]
        assert _pairs(_state(blocks)) == [], kind


def test_different_typesetting_is_not_one_paragraph():
    state = _state([_block("a", T, 1, "施工要求", size=16, bold=True), _block("b", T, 2, "支座安装前", size=10.5, bold=False)])
    assert _pairs(state) == []
    same = _state([_block("a", T, 1, "支座安装前应", size=10.5), _block("b", T, 2, "检查合格证。", size=10.5)])
    assert _pairs(same) == [("a", "b")]


def test_hidden_blocks_existing_relations_and_docx_are_left_alone():
    hidden = _state([_block("a", T, 1, "未完的句子"), _block("b", T, 2, "重复", status=BlockStatus.DUPLICATE),
                     _block("c", T, 2, "接着写完。")])
    assert _pairs(hidden) == [("a", "c")]
    hidden.relations = [Relation(id="r", kind="continues", src="a", dst="c")]
    assert _pairs(hidden) == []
    docx = [Block(id=i, kind=T, order=n, text=t, anchors=[DocxAnchor(part="word/document.xml",
                                                                     node_path=f"/w:p[{n}]", segment=n + 1)])
            for n, (i, t) in enumerate([("a", "未完的句子"), ("b", "接着写完。")])]
    assert propose_continuations(_state(docx, fmt="docx")) == []


def test_only_consecutive_pages():
    state = _state([_block("a", T, 1, "未完的句子"), _block("fig", FIG, 2), _block("b", T, 3, "接着写完。")])
    assert _pairs(state) == []
