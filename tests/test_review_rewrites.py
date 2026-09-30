"""A rewrite from the page image that no reading confirms (vision-first, scanned pages; user 2026-09-30): the scan
engine's reading is listed for the agent — its own review kind, not a formula's."""

from parserx.ir.anchor import PdfAnchor
from parserx.ir.block import Block
from parserx.ir.enums import BlockKind, DocumentStatus, ObservationStatus, PageStatus, TaskKind
from parserx.ir.observation import Observation
from parserx.ir.state import DocumentState, PageState
from parserx.tools.envelope import UnresolvedKind
from parserx.tools.views import REWRITE_CANDIDATE, unresolved_items

ANCHOR = PdfAnchor(page=1, bbox=(100, 100, 400, 130), coord_space="page_pt")


def _obs(oid, engine, text, label=None):
    return Observation(id=oid, engine=engine, engine_version="v", task=TaskKind.RECOGNIZE, anchor=ANCHOR, label=label,
                       text=text, status=ObservationStatus.OK)


def _state(chosen):
    block = Block(id="b1", kind=BlockKind.TEXT, order=0, anchors=[ANCHOR], text="式中 No 为消毒前菌数，共 18 个",
                  observations=[_obs("o-e", "paddleocr", "式中 No 为消毒前菌数，共 12 个", "text"),
                                _obs("o-v", "vlm", "式中 No 为消毒前菌数，共 18 个", "vision_allocation"),
                                _obs("o-c", "vlm", "式中 No 为消毒前菌数，共 12 个", REWRITE_CANDIDATE)],
                  chosen_observation=chosen)
    return DocumentState(id="d", source="x.pdf", source_sha256="0" * 64, format="pdf",
                         status=DocumentStatus.IN_PROGRESS, blocks=[block],
                         pages=[PageState(n=1, unit="pdf_page", status=PageStatus.DONE, size_pt=(595, 842))])


def test_an_unconfirmed_rewrite_lists_the_engines_reading_until_it_is_decided():
    items = [u for u in unresolved_items(_state("o-v")) if u.kind == UnresolvedKind.READING_DISAGREEMENT]
    assert len(items) == 1 and items[0].quotes[0].doc_text == "式中 No 为消毒前菌数，共 12 个"
    assert "8×1" in items[0].detail and "2×1" in items[0].detail and "typos" in items[0].detail
    assert not any(u.kind == UnresolvedKind.FORMULA_CANDIDATE for u in unresolved_items(_state("o-v")))
    # the engine's reading adopted: nothing left to decide
    assert not any(u.kind == UnresolvedKind.READING_DISAGREEMENT for u in unresolved_items(_state("o-c")))
