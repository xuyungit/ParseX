"""v2 IR (guide §4, docs/v2_phase1_interfaces.md §2): round trips, strictness, structural validators."""

import json

import pytest
from pydantic import TypeAdapter, ValidationError

from parserx.ir import ids
from parserx.ir.anchor import DocxAnchor, PdfAnchor
from parserx.ir.asset import Asset
from parserx.ir.block import Block
from parserx.ir.decision import Decision
from parserx.ir.enums import (
    BlockKind,
    BlockStatus,
    DecisionStage,
    DocumentStatus,
    EvidenceLevel,
    ImageRoute,
    ObservationStatus,
    PageStatus,
    RelationKind,
    TaskKind,
)
from parserx.ir.observation import Numbering, Observation, TextStyle
from parserx.ir.relation import Relation
from parserx.ir.schema import sidecar_json_schema, validate_sidecar
from parserx.ir.semantic import ChartSemantic, Evidenced, FigureSemantic, GenericSemantic, Series
from parserx.ir.state import (
    AccountingSummary,
    DocumentState,
    ImageRecord,
    LedgerEntry,
    Missing,
    PageState,
    Sidecar,
    Stats,
    TokenUsage,
)
from parserx.tables import Cell, TableGrid

PDF_ANCHOR = PdfAnchor(page=1, bbox=(10, 20, 200, 40), coord_space="page_pt")


def _obs(**kw) -> Observation:
    fields = dict(id="o-b-p001-0001-native_pdf-1", engine="native_pdf", engine_version="pymupdf-1.27",
                  task=TaskKind.EXTRACT, anchor=PDF_ANCHOR, text="第一章 总则", status=ObservationStatus.OK)
    fields.update(kw)
    return Observation(**fields)


def _block(**kw) -> Block:
    fields = dict(id="b-p001-0001", kind=BlockKind.TEXT, order=0, anchors=[PDF_ANCHOR])
    fields.update(kw)
    return Block(**fields)


def _state() -> DocumentState:
    obs = _obs(style=TextStyle(font_size=16.0, bold=True))
    grid = TableGrid(n_rows=1, n_cols=2, cells=[Cell(row=0, col=0, content="甲"), Cell(row=0, col=1, content="10")])
    blocks = [
        _block(
            kind=BlockKind.TITLE, level=1, observations=[obs], chosen_observation=obs.id, text=obs.text,
            decisions=[Decision(
                stage=DecisionStage.HEADING_ROLE, choice="title", reason="adapter", evidence={"font_size": 16.0},
                actor="program:hierarchy.typography", refs=[obs.id],
            )],
        ),
        _block(id="b-p001-0002", kind=BlockKind.TABLE, order=1, cells=grid),
        _block(
            id="b-p001-0003", kind=BlockKind.FIGURE, order=2,
            semantic=GenericSemantic(type="photo", summary=Evidenced(value="一张桥梁照片", level=EvidenceLevel.INFERRED)),
        ),
    ]
    return DocumentState(
        id="doc", source="input.pdf", source_sha256="0" * 64, format="pdf", status=DocumentStatus.COMPLETE,
        engines={"native_pdf": "pymupdf-1.27"}, prompt_hashes={},
        pages=[PageState(n=1, unit="pdf_page", status=PageStatus.DONE, size_pt=(595.0, 842.0))],
        blocks=blocks,
        relations=[Relation(id=ids.relation_id(RelationKind.CONTINUES, "b-p001-0001", "b-p001-0002"),
                            kind=RelationKind.CONTINUES, src="b-p001-0001", dst="b-p001-0002")],
        assets=[Asset(id=ids.asset_id("ab" * 32), sha256="ab" * 32, path="assets/x.png", media_type="image/png",
                      width=10, height=10, role="original", source=PDF_ANCHOR)],
        images=[ImageRecord(id=ids.asset_id("ab" * 32), route=ImageRoute.FIGURE, shown=True, t=0.1, f=0.8)],
        ledger=[LedgerEntry(item=ids.ledger_item_pdf(1, 1), unit="native_line", source=PDF_ANCHOR, chars=5,
                            disposition="output", block="b-p001-0001")],
        missing=[], stats=Stats(requests={"ocr": 1}, tokens={"vlm": TokenUsage(input=10, output=3)}, cost_usd=0.0),
        warnings=[], version=3,
    )


# ── Round trips ─────────────────────────────────────────────────────────


def test_document_state_json_round_trip():
    state = _state()
    again = DocumentState.model_validate_json(state.model_dump_json())
    assert again == state
    assert isinstance(again.blocks[2].semantic, GenericSemantic)
    assert again.blocks[0].observations[0].style.font_size == 16.0


def test_figure_semantic_discriminates_on_type():
    adapter = TypeAdapter(FigureSemantic)
    chart = adapter.validate_python({
        "type": "chart", "chart_type": {"value": "bar", "level": "visible"},
        "series": [{"name": {"value": "产量", "level": "visible"},
                    "values": [{"value": 120, "level": "visible"}, {"value": 150, "level": "estimated"}]}],
    })
    diagram = adapter.validate_python({
        "type": "diagram", "diagram_type": {"value": "flow", "level": "visible"},
        "edges": [{"src": "A", "dst": "B", "direction": "unknown"}],
    })
    assert isinstance(chart, ChartSemantic) and chart.series[0].values[1].level == EvidenceLevel.ESTIMATED
    assert diagram.edges[0].label is None
    assert Series(name=Evidenced(value="x", level="visible")).values == []


# ── Strictness ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("model, fields", [
    (Observation, {**_obs().model_dump(), "flag": True}),
    (Relation, {"id": "r", "kind": "continues", "src": "a", "dst": "b", "metadata": {}}),
    (Decision, {"stage": "exclude", "choice": "x", "reason": "y", "evidence": {}, "actor": "pipeline", "note": 1}),
    (LedgerEntry, {"item": "i", "unit": "native_line", "source": PDF_ANCHOR.model_dump(), "chars": 1, "extra": 0}),
])
def test_unknown_fields_are_rejected(model, fields):
    with pytest.raises(ValidationError):
        model.model_validate(fields)


def test_decision_evidence_rejects_nested_values():
    with pytest.raises(ValidationError):
        Decision(stage="exclude", choice="x", reason="y", evidence={"nested": {"a": 1}}, actor="pipeline")


def test_failed_observation_needs_error():
    with pytest.raises(ValidationError):
        _obs(status=ObservationStatus.FAILED)
    assert _obs(status=ObservationStatus.FAILED, error="timeout").error == "timeout"


# ── Block structure ─────────────────────────────────────────────────────


def test_block_needs_an_anchor():
    with pytest.raises(ValidationError):
        _block(anchors=[])


def test_level_only_on_titles_and_in_range():
    assert _block(kind=BlockKind.TITLE, level=2).level == 2
    assert _block(kind=BlockKind.TITLE).level is None  # role known, level pending
    with pytest.raises(ValidationError):
        _block(kind=BlockKind.TEXT, level=1)
    with pytest.raises(ValidationError):
        _block(kind=BlockKind.TITLE, level=7)


def test_cells_only_on_tables_and_tables_carry_no_text():
    grid = TableGrid(n_rows=1, n_cols=1, cells=[Cell(row=0, col=0, content="a")])
    with pytest.raises(ValidationError):
        _block(kind=BlockKind.TEXT, cells=grid)
    with pytest.raises(ValidationError):
        _block(kind=BlockKind.TABLE, cells=grid, text="a")


def test_semantic_only_on_figures():
    semantic = GenericSemantic(type="seal", summary=Evidenced(value="公章", level=EvidenceLevel.VISIBLE))
    with pytest.raises(ValidationError):
        _block(kind=BlockKind.TEXT, semantic=semantic)


def test_chosen_observation_must_exist():
    with pytest.raises(ValidationError):
        _block(observations=[_obs()], chosen_observation="o-missing")
    block = _block(observations=[_obs()], chosen_observation=_obs().id)
    with pytest.raises(ValidationError):  # validate_assignment keeps the rule on later edits
        block.chosen_observation = "o-missing"


def test_multi_anchor_block_keeps_every_source():
    second = PdfAnchor(page=2, bbox=(10, 20, 200, 40), coord_space="page_pt")
    block = _block(anchors=[PDF_ANCHOR, second])
    assert Block.model_validate_json(block.model_dump_json()).anchors == [PDF_ANCHOR, second]


def test_style_carries_docx_evidence():
    style = TextStyle(style_name="heading 2", outline_level=1, numbering=Numbering(num_id="3", level=1, text="1.2"))
    obs = _obs(anchor=DocxAnchor(part="word/document.xml", node_path="/w:body/w:p[4]"), style=style)
    assert Observation.model_validate_json(obs.model_dump_json()).style.numbering.text == "1.2"


# ── Deterministic ids ───────────────────────────────────────────────────


def test_ids_are_deterministic():
    assert ids.block_id_pdf(3, 12) == "b-p003-0012"
    assert ids.block_id_docx(7) == "b-d00007"
    assert ids.observation_id("b-p003-0012", "paddleocr", 1) == "o-b-p003-0012-paddleocr-1"
    assert ids.relation_id(RelationKind.CONTINUES, "b-1", "b-2") == "r-continues-b-1-b-2"
    assert ids.asset_id("0123456789abcdef" * 4) == "a-0123456789abcdef"
    assert ids.ledger_item_pdf(2, 5) == "i-p002-00005"
    assert ids.ledger_item_docx(9) == "i-d00009"
    with pytest.raises(ValueError):
        ids.asset_id("not-a-digest")


# ── Sidecar schema ──────────────────────────────────────────────────────


def _sidecar_dict() -> dict:
    state = _state()
    summary = AccountingSummary(discovered=1, output=1, merged=0, duplicate=0, excluded=0, failed=0, unassigned=0)
    sidecar = Sidecar(**state.model_dump(), accounting=summary)
    return json.loads(sidecar.model_dump_json())


def test_sidecar_schema_validates_a_document_state():
    schema = sidecar_json_schema()
    assert schema["additionalProperties"] is False
    assert validate_sidecar(_sidecar_dict()) == []


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(status="done"),                                  # not a DocumentStatus
    lambda d: d["blocks"][0].update(flags={"is_heading": True}),        # free-form flags
    lambda d: d.pop("ledger"),                                          # required section
    lambda d: d["pages"][0].update(unit="sheet"),
])
def test_sidecar_schema_rejects_invalid_documents(mutate):
    data = _sidecar_dict()
    mutate(data)
    assert validate_sidecar(data)


def test_missing_and_statuses_serialize_as_plain_strings():
    state = _state().model_copy(update={"status": DocumentStatus.PARTIAL,
                                        "missing": [Missing(block="b-p001-0003", reason="budget")]})
    data = json.loads(state.model_dump_json())
    assert data["status"] == "partial" and data["missing"] == [{"block": "b-p001-0003", "reason": "budget"}]
    assert data["blocks"][0]["status"] == BlockStatus.OK.value
