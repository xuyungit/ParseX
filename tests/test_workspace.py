"""Workspace (docs/v2_phase1_interfaces.md §2.10): persistence, transactions, call log, lock, queries."""

import json
import subprocess
import sys

import pytest

from parserx.ir.anchor import DocxAnchor, PdfAnchor
from parserx.ir.block import Block
from parserx.ir.enums import BlockKind, DocumentStatus, PageStatus
from parserx.ir.state import DocumentState, PageState
from parserx.workspace import VersionConflict, Workspace, WorkspaceExists, WorkspaceLocked, queries


def _anchor(page: int, y: float = 0) -> PdfAnchor:
    return PdfAnchor(page=page, bbox=(0, y, 100, y + 10), coord_space="page_pt")


def _state() -> DocumentState:
    blocks = [
        Block(id="b-p001-0001", kind=BlockKind.TITLE, level=1, order=0, anchors=[_anchor(1)], text="第一章"),
        Block(id="b-p001-0002", kind=BlockKind.TEXT, order=1, anchors=[_anchor(1, 20)], text="正文一"),
        Block(id="b-p002-0001", kind=BlockKind.TITLE, level=2, order=2, anchors=[_anchor(2)], text="1.1 节"),
        Block(id="b-p002-0002", kind=BlockKind.TEXT, order=3, anchors=[_anchor(2, 20)], text="正文二"),
    ]
    return DocumentState(
        id="doc", source="input.pdf", source_sha256="0" * 64, format="pdf", status=DocumentStatus.IN_PROGRESS,
        pages=[PageState(n=1, unit="pdf_page", status=PageStatus.PENDING),
               PageState(n=2, unit="pdf_page", status=PageStatus.PENDING)],
        blocks=blocks,
    )


@pytest.fixture
def ws(tmp_path):
    source = tmp_path / "input.pdf"
    source.write_bytes(b"%PDF-synthetic")
    return Workspace.create(tmp_path / "ws", _state(), source)


# ── Persistence ─────────────────────────────────────────────────────────


def test_create_and_open_round_trip(ws, tmp_path):
    again = Workspace.open(tmp_path / "ws")
    assert again.load() == ws.load()
    assert again.load().version == 1  # creation is the first committed change
    assert again.source_path.read_bytes() == b"%PDF-synthetic"


def test_create_refuses_a_non_empty_directory(ws, tmp_path):
    with pytest.raises(WorkspaceExists):
        Workspace.create(tmp_path / "ws", _state(), tmp_path / "input.pdf")


def test_open_requires_a_workspace(tmp_path):
    with pytest.raises(FileNotFoundError):
        Workspace.open(tmp_path / "nothing")


# ── Transactions ────────────────────────────────────────────────────────


def test_committed_transaction_bumps_version(ws):
    with ws.txn("test") as state:
        state.pages[0].status = PageStatus.DONE
    after = ws.load()
    assert after.version == 2 and after.pages[0].status == PageStatus.DONE


def test_failed_transaction_writes_nothing(ws):
    before = ws.state_path.read_bytes()
    with pytest.raises(RuntimeError):
        with ws.txn("test") as state:
            state.pages[0].status = PageStatus.DONE
            raise RuntimeError("tool failed half way")
    assert ws.state_path.read_bytes() == before


def test_invalid_state_is_not_committed(ws):
    before = ws.state_path.read_bytes()
    with pytest.raises(ValueError):
        with ws.txn("test") as state:
            # list mutation bypasses assignment validation; the commit re-validates everything
            state.blocks.append(state.blocks[1].model_copy(update={"level": 3}))
    assert ws.state_path.read_bytes() == before


def test_expect_version_detects_stale_callers(ws):
    with pytest.raises(VersionConflict) as err:
        with ws.txn("test", expect_version=7):
            pass
    assert err.value.actual == 1
    with ws.txn("test", expect_version=1):
        pass
    assert ws.load().version == 2


def test_transactions_are_logged_in_order(ws):
    with ws.txn("tool:recognize"):
        pass
    ws.log_call({"tool": "overview", "ok": True})
    records = [json.loads(line) for line in ws.calls_path.read_text().splitlines()]
    assert [r.get("actor") or r.get("tool") for r in records] == ["workspace:create", "tool:recognize", "overview"]
    assert [r["version"] for r in records if r["type"] == "txn"] == [1, 2]


def test_lock_blocks_a_second_process(ws):
    holder = subprocess.Popen(
        [sys.executable, "-c",
         "import fcntl,sys; f=open(sys.argv[1],'a'); fcntl.flock(f, fcntl.LOCK_EX); "
         "print('locked', flush=True); sys.stdin.read()", str(ws.lock_path)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    )
    try:
        assert holder.stdout.readline().strip() == "locked"
        with pytest.raises(WorkspaceLocked):
            with ws.txn("test", timeout=0.2):
                pass
    finally:
        holder.stdin.close()
        holder.wait(timeout=5)
    with ws.txn("test", timeout=1.0):  # released
        pass


# ── Assets ──────────────────────────────────────────────────────────────


def test_assets_are_content_addressed(ws):
    a = ws.add_asset(b"\x89PNG-fake", media_type="image/png", width=2, height=3, role="original")
    b = ws.add_asset(b"\x89PNG-fake", media_type="image/png", width=2, height=3, role="original")
    assert a == b and a.id.startswith("a-") and a.path == f"assets/{a.id}.png"
    assert (ws.root / a.path).read_bytes() == b"\x89PNG-fake"


# ── Queries ─────────────────────────────────────────────────────────────


def test_queries_follow_reading_order(ws):
    state = ws.load()
    assert [b.id for b in queries.blocks_on_page(state, 2)] == ["b-p002-0001", "b-p002-0002"]
    assert [b.id for b in queries.neighbors(state, "b-p001-0002", 1)] == ["b-p001-0001", "b-p001-0002", "b-p002-0001"]
    assert [(b.id, b.level) for b in queries.outline(state)] == [("b-p001-0001", 1), ("b-p002-0001", 2)]
    with pytest.raises(KeyError):
        queries.neighbors(state, "b-missing", 1)


def test_docx_blocks_belong_to_segments():
    anchor = DocxAnchor(part="word/document.xml", node_path="/w:body/w:p[9]", segment=2)
    block = Block(id="b-d00009", kind=BlockKind.TEXT, order=0, anchors=[anchor], text="x")
    state = DocumentState(id="d", source="a.docx", source_sha256="0" * 64, format="docx",
                          status=DocumentStatus.IN_PROGRESS, blocks=[block])
    assert queries.block_unit(state, block) == 2
    assert queries.blocks_on_page(state, 2) == [block]
