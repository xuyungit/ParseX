"""Numbers the local reading sees that the output does not write as one number at that place (reading/numbers.py,
Q148), on synthetic states: written apart, left out, and what is not a difference."""

from parserx.ir.anchor import AssetAnchor, PdfAnchor
from parserx.ir.block import Block
from parserx.ir.enums import (BlockKind, BlockStatus, DocumentStatus, ImageRoute, ObservationStatus, PageStatus,
                              RelationKind, TaskKind)
from parserx.ir.observation import Observation
from parserx.ir.relation import Relation
from parserx.ir.state import DocumentState, ImageRecord, PageReading, PageState, ReadLine, ReadRegion
from parserx.reading.numbers import line_numbers, number_findings
from parserx.tools.envelope import UnresolvedKind
from parserx.tools.views import unresolved_items


def _block(bid, bbox, text, *, kind=BlockKind.TEXT, engine="paddleocr", status=BlockStatus.OK, anchor=None):
    anchor = anchor or PdfAnchor(page=1, bbox=bbox, coord_space="page_pt")
    obs = Observation(id=f"o-{bid}", engine=engine, engine_version="v", task=TaskKind.RECOGNIZE, anchor=anchor,
                      text=text, status=ObservationStatus.OK)
    return Block(id=bid, kind=kind, order=0, status=status, anchors=[anchor], text=text, observations=[obs],
                 chosen_observation=obs.id)


def _line(text, bbox):
    return ReadLine(bbox=bbox, text=text, score=0.98)


def _state(blocks, lines, *, roles=(), images=(), relations=()):
    return DocumentState(
        id="d", source="x.pdf", source_sha256="0" * 64, format="pdf", status=DocumentStatus.IN_PROGRESS,
        pages=[PageState(n=1, unit="pdf_page", status=PageStatus.DONE, size_pt=(595, 842))], blocks=blocks,
        readings=[PageReading(n=1, engine="local", dpi=150, lines=lines, roles=list(roles))],
        images=list(images), relations=list(relations))


def test_a_number_written_apart_is_found():
    assert line_numbers("7.3.5.2将以上稀释的消毒药中加菌液2.5mL", "7. 3.5.2 将以上稀释的消毒药中加菌液 2.5 mL",
                        in_math=False) == [("7.3.5.2", "split")]
    assert line_numbers("见7.3.5节", "见 7.3.5 节", in_math=False) == []


def test_what_is_written_differently_is_not_a_difference():
    # a thousands space, punctuation the local reader dropped, a list after a comma, a superscript beside its base
    assert line_numbers("共1720元", "共 1 720 元", in_math=False) == []
    assert line_numbers("1 570", "1570", in_math=False) == []  # a thousands space the local reader kept
    assert line_numbers("FR Doc.95383", "FR Doc. 95-383", in_math=False) == []
    assert line_numbers("δ=165.3,150.6", "δ=165.3, 150.6", in_math=False) == []
    assert line_numbers("面积1000m2，取10-3", "面积 $1000\\mathrm{m}^{2}$，取 $10^{-3}$", in_math=False) == []


def test_a_number_left_out_counts_only_on_strong_evidence():
    assert line_numbers("总股本2741百万股", "总股本 百万股", in_math=False) == [("2741", "missing")]
    assert line_numbers("取3次", "取三次", in_math=False) == []  # one digit: a local misreading as likely
    # in a formula the local reader takes δ for 8 and glues scripts: only a number in brackets counts there
    assert line_numbers("8a=K-1F (3)", "$\\delta_a = K^{-1} F$", in_math=True) == [("3", "missing")]
    assert line_numbers("8a=K-1F", "$\\delta_a = K^{-1} F$", in_math=True) == []


def test_a_number_in_brackets_must_be_in_brackets():
    # the formula beside it has a 1 of its own; written with \tag it is there
    assert line_numbers("(1)", "$$ x_{1} + y_{1} = 1 $$", in_math=True) == [("1", "missing")]
    assert line_numbers("(1)", "$$ x_{1} + y_{1} = 1 \\tag{1} $$", in_math=True) == []
    assert line_numbers("(7)", "$$ \\mu_ {k + 1} := 0.1 \\mu_ {k} \\tag {7} $$", in_math=True) == []
    assert line_numbers("（2）采用橡胶", "(2)采用橡胶", in_math=False) == []


def test_on_the_page_a_formula_number_beside_its_formula_is_listed_on_the_formula():
    formula = _block("b-f", (150, 200, 400, 230), "$$ \\sum f_{i} P_{i} = A_{1} $$", kind=BlockKind.FORMULA)
    prose = _block("b-t", (60, 100, 500, 160), "7. 3.5.2 加菌后 5 min")
    state = _state([formula, prose], [_line("7.3.5.2加菌后5min", (60, 102, 300, 114)), _line("(1)", (480, 208, 500, 222))])
    found = {f.target: f.numbers for f in number_findings(state)}
    assert found == {"b-t": [("7.3.5.2", "split")], "b-f": [("1", "missing")]}
    items = [u for u in unresolved_items(state) if u.kind == UnresolvedKind.NUMBER_UNACCOUNTED]
    assert [(u.target, [q.doc_text for q in u.quotes]) for u in items] == [("b-t", ["7.3.5.2加菌后5min"]), ("b-f", ["(1)"])]


def test_text_layer_numbers_furniture_and_excluded_text_are_not_compared():
    native = _block("b-n", (60, 100, 500, 160), "7. 3.5.2 节", engine="native_pdf")
    excluded = _block("b-x", (60, 700, 500, 720), "第 12 页", status=BlockStatus.EXCLUDED)
    lines = [_line("7.3.5.2节", (60, 102, 300, 114)), _line("第12页", (60, 702, 200, 716)), _line("38", (280, 800, 300, 812))]
    state = _state([native, excluded], lines, roles=[ReadRegion(label="number", bbox=(270, 795, 310, 815))])
    assert number_findings(state) == []


def test_an_image_transcription_that_lacks_the_formula_number_is_listed_on_the_image():
    figure = _block("b-img", (100, 300, 400, 340), "", kind=BlockKind.FIGURE,
                    anchor=AssetAnchor(asset="a-1", bbox=(0, 0, 600, 80), image_size=(600, 80)))
    inside = _block("b-in", (0, 0, 600, 80), "$ d_{k}=-(J_{k}^{T}J_{k}+\\mu_{k}I)^{-1}J_{k}^{T}F_{k} $",
                    anchor=AssetAnchor(asset="a-1", bbox=(0, 0, 500, 80), image_size=(600, 80)))
    record = ImageRecord(id="a-1", route=ImageRoute.SCAN, shown=True,
                         reading=[_line("d=-(JJk+μ)-JF", (10, 20, 480, 60)), _line("(6)", (540, 25, 590, 55))])
    relation = Relation(id="r-1", kind=RelationKind.CONTAINS, src="b-img", dst="b-in")
    state = _state([figure, inside], [], images=[record], relations=[relation])
    assert [(f.target, f.lines, f.numbers) for f in number_findings(state)] == [("b-img", ["(6)"], [("6", "missing")])]


def test_a_line_an_image_transcription_lacks_whole_is_left_to_the_text_check():
    # GLM-OCR leaves a scanned report's running head out: the line is listed whole (text_unaccounted), not again for
    # its numbers; a line that is there with a number written apart is listed
    figure = _block("b-img", (100, 300, 400, 340), "", kind=BlockKind.FIGURE,
                    anchor=AssetAnchor(asset="a-1", bbox=(0, 0, 600, 400), image_size=(600, 400)))
    inside = _block("b-in", (0, 0, 600, 400), "4. 1 本合同货物交付地点和相关服务的履行地",
                    anchor=AssetAnchor(asset="a-1", bbox=(0, 100, 600, 140), image_size=(600, 400)))
    record = ImageRecord(id="a-1", route=ImageRoute.SCAN, shown=True,
                         reading=[_line("报告编号：ZJA1-X001-2023000001B", (10, 10, 400, 40)),
                                  _line("4.1本合同货物交付地点和相关服务的履行地", (10, 100, 590, 140))])
    relation = Relation(id="r-1", kind=RelationKind.CONTAINS, src="b-img", dst="b-in")
    state = _state([figure, inside], [], images=[record], relations=[relation])
    assert [(f.lines, f.numbers) for f in number_findings(state)] == [
        (["4.1本合同货物交付地点和相关服务的履行地"], [("4.1", "split")])]
    kinds = {u.kind for u in unresolved_items(state) if u.target == "b-img"}
    assert kinds == {UnresolvedKind.TEXT_UNACCOUNTED, UnresolvedKind.NUMBER_UNACCOUNTED}
