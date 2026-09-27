"""Accounting check (guide §3.3, plan P1-5): every discovered item has exactly one destination."""

from parserx.accounting import check
from parserx.ir.anchor import AssetAnchor, PdfAnchor
from parserx.ir.asset import Asset
from parserx.ir.block import Block
from parserx.ir.decision import Decision
from parserx.ir.enums import BlockKind, BlockStatus, DocumentStatus, PageStatus
from parserx.ir.relation import Relation
from parserx.ir.state import DocumentState, LedgerEntry, Missing, PageState


def _a(page=1):
    return PdfAnchor(page=page, bbox=(0, 0, 10, 10), coord_space="page_pt")


def _state(**kw) -> DocumentState:
    blocks = [
        Block(id="b-out", kind=BlockKind.TEXT, order=0, anchors=[_a()], text="正文"),
        Block(id="b-dup", kind=BlockKind.TEXT, order=1, anchors=[_a()], status=BlockStatus.DUPLICATE, text="旧"),
        Block(id="b-merged", kind=BlockKind.SCAN, order=2, anchors=[_a()], status=BlockStatus.MERGED),
        Block(id="b-del", kind=BlockKind.TEXT, order=3, anchors=[_a()], status=BlockStatus.EXCLUDED, text="删",
              decisions=[Decision(stage="exclude", choice="revision_deleted", reason="r", evidence={}, actor="x")]),
    ]
    ledger = [
        LedgerEntry(item="i-1", unit="native_line", source=_a(), chars=2, disposition="output", block="b-out"),
        LedgerEntry(item="i-2", unit="native_line", source=_a(), chars=1, disposition="duplicate", block="b-dup"),
        LedgerEntry(item="i-3", unit="pdf_image", source=_a(), chars=0, disposition="merged", block="b-merged"),
        LedgerEntry(item="i-4", unit="docx_deleted", source=_a(), chars=1, disposition="excluded", block="b-del"),
    ]
    fields = dict(id="d", source="x.pdf", source_sha256="0" * 64, format="pdf", status=DocumentStatus.IN_PROGRESS,
                  pages=[PageState(n=1, unit="pdf_page", status=PageStatus.DONE)], blocks=blocks, ledger=ledger,
                  relations=[Relation(id="r-1", kind="duplicate_of", src="b-dup", dst="b-out")])
    fields.update(kw)
    return DocumentState(**fields)


def test_balanced_ledger_is_complete_and_exportable():
    result = check(_state())
    s = result.accounting
    assert (s.discovered, s.output, s.duplicate, s.merged, s.excluded, s.failed, s.unassigned) == (4, 1, 1, 1, 1, 0, 0)
    assert s.discovered == s.output + s.merged + s.duplicate + s.excluded + s.failed + s.unassigned
    assert result.exportable and result.document_status == DocumentStatus.COMPLETE
    assert result.pages[0].native_chars == 3 and result.pages[0].blocks == 4


def test_unassigned_item_blocks_export():
    state = _state()
    state.ledger.append(LedgerEntry(item="i-5", unit="native_line", source=_a(), chars=3))
    result = check(state)
    assert result.unassigned == ["i-5"] and not result.exportable


def test_output_item_on_a_hidden_block_is_a_silent_loss():
    state = _state()
    state.ledger[0].block = "b-dup"  # says "output" but the block is not rendered
    result = check(state)
    assert result.mismatched == ["i-1"] and not result.exportable


def test_a_duplicate_whose_original_is_hidden_is_a_silent_loss():
    # §11.5: content judged a duplicate must still reach the output through what it duplicates
    state = _state()
    state.blocks[0].status = BlockStatus.EXCLUDED  # the original is gone ...
    state.ledger[0].disposition = "excluded"
    state.blocks[0].decisions.append(Decision(stage="exclude", choice="ui", reason="r", evidence={}, actor="x"))
    result = check(state)
    assert result.mismatched == ["i-2"] and not result.exportable  # ... so the duplicate's text is lost
    state.blocks.append(Block(id="b-new", kind=BlockKind.TEXT, order=4, anchors=[_a()], text="新"))
    state.ledger.append(LedgerEntry(item="i-5", unit="ocr_block", source=_a(), chars=1, disposition="output",
                                    block="b-new"))
    state.blocks[0].status, state.ledger[0].disposition = BlockStatus.DUPLICATE, "duplicate"
    state.relations.append(Relation(id="r-2", kind="duplicate_of", src="b-out", dst="b-new"))
    assert check(state).mismatched == []  # a chain of duplicates ending in a shown block is a destination
    state.relations = [r for r in state.relations if r.src != "b-dup"]
    assert check(state).mismatched == []  # superseded without an overlapping block: the page reading checks it


def test_illegal_references_are_listed():
    state = _state()
    state.ledger[0].block = "b-nowhere"
    state.relations.append(Relation(id="r-2", kind="continues", src="b-out", dst="b-gone"))
    state.blocks[0].anchors.append(AssetAnchor(asset="a-missing", bbox=(0, 0, 1, 1), image_size=(1, 1)))
    state.blocks[3].decisions[0].refs.append("o-unknown")
    result = check(state)
    targets = sorted(r.target for r in result.illegal_refs)
    assert targets == ["a-missing", "b-gone", "b-nowhere", "o-unknown"] and not result.exportable


def test_duplicate_ids_are_illegal():
    state = _state()
    state.blocks.append(state.blocks[0].model_copy(update={"order": 9}))
    assert any(r.field == "id" and r.target == "b-out" for r in check(state).illegal_refs)


def test_budget_skip_is_partial_but_exportable():
    state = _state()
    state.pages[0].status = PageStatus.SKIPPED
    state.blocks.append(Block(id="b-fail", kind=BlockKind.SCAN, order=4, anchors=[_a()], status=BlockStatus.FAILED))
    state.ledger.append(LedgerEntry(item="i-6", unit="pdf_image", source=_a(), chars=0, disposition="failed",
                                    block="b-fail"))
    state.missing.append(Missing(block="b-fail", reason="budget exhausted"))
    result = check(state)
    assert result.exportable and result.document_status == DocumentStatus.PARTIAL
    assert [m.block for m in result.missing] == ["b-fail"] and result.accounting.failed == 1


def test_failed_block_without_a_missing_entry_is_reported():
    state = _state()
    state.blocks.append(Block(id="b-fail", kind=BlockKind.OTHER, order=4, anchors=[_a()], status=BlockStatus.FAILED,
                              decisions=[Decision(stage="content_source", choice="unsupported", reason="textbox",
                                                  evidence={}, actor="x")]))
    state.ledger.append(LedgerEntry(item="i-6", unit="docx_unsupported", source=_a(), chars=4,
                                    disposition="failed", block="b-fail"))
    result = check(state)
    assert [(m.block, m.reason) for m in result.missing] == [("b-fail", "textbox")]
    assert result.document_status == DocumentStatus.PARTIAL


def test_pending_page_is_not_exportable():
    state = _state()
    state.pages[0].status = PageStatus.PENDING
    result = check(state)
    assert not result.exportable and result.document_status == DocumentStatus.IN_PROGRESS


def test_nothing_reaching_the_output_is_a_failed_document():
    scan = Block(id="b-scan", kind=BlockKind.SCAN, order=0, anchors=[_a()], status=BlockStatus.FAILED)
    state = _state(pages=[PageState(n=1, unit="pdf_page", status=PageStatus.FAILED)], blocks=[scan], relations=[],
                   ledger=[LedgerEntry(item="i-1", unit="pdf_image", source=_a(), chars=0, disposition="failed",
                                       block="b-scan")])
    assert check(state).document_status == DocumentStatus.FAILED
    assert check(_state(pages=[PageState(n=1, unit="pdf_page", status=PageStatus.FAILED)])).document_status \
        == DocumentStatus.PARTIAL  # a failed page whose fallback text is still output


def test_missing_asset_files_are_found(tmp_path):
    asset = Asset.from_bytes(b"png", media_type="image/png", width=1, height=1, role="original")
    state = _state(assets=[asset])
    result = check(state, root=tmp_path)
    assert result.missing_assets == [asset.id] and not result.exportable
    (tmp_path / asset.path).parent.mkdir(parents=True)
    (tmp_path / asset.path).write_bytes(b"png")
    assert check(state, root=tmp_path).exportable
