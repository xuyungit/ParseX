"""Text added from the local page reading, read again by the scan engine (tools/added_text.py): which lines make one
place, and when the engine's reading is taken — offline, with a fake scan engine."""

import pymupdf
import pytest

from parserx.config.schema import OCRBuilderConfig, ParserXConfig
from parserx.ir.anchor import PdfAnchor
from parserx.ir.block import Block
from parserx.ir.decision import Decision
from parserx.ir.enums import BlockKind, BlockStatus, DecisionStage
from parserx.ir.state import PageReading, ReadRegion
from parserx.reading.compare import READING_ACTOR
from parserx.services.ocr import PaddleOCRService
from parserx.tools import ToolContext, workspace_init
from parserx.tools import added_text
from parserx.workspace import Workspace

FOOTNOTE = (40, 700, 400, 760)


def _added(bid, bbox, text, kind=BlockKind.FOOTNOTE, order=0, status=BlockStatus.OK):
    anchor = PdfAnchor(page=1, bbox=bbox, coord_space="page_pt")
    return Block(id=bid, kind=kind, order=order, status=status, anchors=[anchor], text=text,
                 decisions=[Decision(stage=DecisionStage.CONTENT_SOURCE, choice="added", actor=READING_ACTOR,
                                     reason="r", evidence={})])


@pytest.fixture
def ws(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((50, 715), "4Rule 823 also demonstrates the", fontsize=8)
    page.insert_text((230, 722), "following week.This language", fontsize=8)
    page.insert_text((50, 725), "occursonMonday will settle", fontsize=8)
    page.insert_text((50, 300), "a side note", fontsize=8)
    path = tmp_path / "doc.pdf"
    doc.save(path)
    workspace_init(path, tmp_path / "ws", config=ParserXConfig())
    workspace = Workspace(tmp_path / "ws")
    with workspace.txn("test") as state:  # two footnote columns whose lines interleave in reading order
        state.blocks = [_added("b-1", (50, 708, 200, 718), "4Rule 823 also demonstrates the", order=1),
                        _added("b-2", (230, 715, 380, 725), "following week.This language", order=2),
                        _added("b-3", (50, 718, 200, 728), "occursonMonday will settle", order=3),
                        _added("b-4", (50, 293, 120, 303), "a side note", kind=BlockKind.TEXT, order=4),
                        _added("b-5", (50, 400, 120, 410), "a mark", kind=BlockKind.HEADER, order=5,
                               status=BlockStatus.EXCLUDED)]
        state.readings = [PageReading(n=1, engine="local", dpi=150, roles=[
            ReadRegion(label="footnote", bbox=(45, 705, 205, 730)), ReadRegion(label="footnote", bbox=(225, 712, 385, 728))])]
    return workspace


def test_places_are_footnote_regions_whatever_the_order_and_lines_set_one_under_another(ws):
    groups = added_text.places(ws.load())
    assert [[b.id for b in g] for g in groups] == [["b-1", "b-3"], ["b-2"], ["b-4"]]  # b-5 is hidden


def _context(readings):
    class Context(ToolContext):
        def _new_ocr(self):
            service = PaddleOCRService(OCRBuilderConfig(endpoint="https://x/api/v2/ocr/jobs", token="t"))

            def run_job(file_bytes, filename, mime, job_key=None):
                with pymupdf.open(stream=file_bytes, filetype="pdf") as sub:
                    assert sub.page_count == len(readings)  # every place in one request
                return {"layoutParsingResults": [
                    {"prunedResult": {"width": 100, "height": 20, "parsing_res_list": [
                        {"block_label": "footnote", "block_content": text, "block_bbox": [0, 0, 100, 20],
                         "block_order": 1}]}} for text in readings]}

            service._run_job = run_job
            return service
    return Context


def test_an_agreeing_reading_is_taken_by_the_place_and_the_rest_merge_into_it(ws):
    readings = ["$^{4}$Rule 823 also demonstrates the occurs on Monday will settle",
                "something else entirely, read from a neighbouring line", "a side note"]
    config = ParserXConfig()
    config.cache.mode = "off"
    adopted, failures = added_text.read_again(_context(readings)(ws, config))
    assert failures == [] and adopted == 3  # b-1 with b-3, and b-4; b-2's reading does not agree
    blocks = {b.id: b for b in ws.load().blocks}
    assert blocks["b-1"].text == readings[0] and blocks["b-1"].kind == BlockKind.FOOTNOTE
    assert blocks["b-3"].status == BlockStatus.MERGED
    assert blocks["b-2"].text == "following week.This language"  # kept as the local reading wrote it
    assert added_text.places(ws.load()) == []  # each read once, taken or not
