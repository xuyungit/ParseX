"""What tools show of the workspace: compact views, document text always in DocText."""

from __future__ import annotations

from collections import Counter
from typing import Literal

from parserx.ir.anchor import SourceAnchor
from parserx.ir.base import Affine, IRModel
from parserx.ir.block import Block
from parserx.ir.enums import (BlockKind, BlockStatus, DocumentStatus, ObservationStatus, PageStatus, RelationKind,
                              TaskKind)
from parserx.ir.observation import Observation
from parserx.ir.state import ClosedItem, DocumentState
from parserx.tables.grid import TableGrid
from parserx.content.text_audit import suspicious_characters
from parserx.tables.arithmetic import arithmetic_issues
from parserx.tables.merge import merge_candidates
from parserx.tools.envelope import DocText, Unresolved, UnresolvedKind
from parserx.workspace.queries import HIDDEN, block_unit, ordered, outline
from parserx.hierarchy.layout_titles import layout_titles
from parserx.hierarchy.numbering_gaps import numbering_gaps, series_successors, unclear_nesting
from parserx.reading.compare import added_from_reading, unaccounted_lines, unseen_segments, within_tables

# The label of a block's reading replaced by a rewrite from the page image where no reading confirms the rewrite
# (vision-first, scanned pages): kept on the block, listed as ``reading_disagreement`` until one is chosen or the
# item is closed (user 2026-09-30: signals, not reverts).
REWRITE_CANDIDATE = "replaced_reading"
ORDER_DISAGREEMENT = "order_disagreement"  # the decision choice that records it (``vision_first`` scanned pages)

NATIVE_ENGINES = frozenset({"native_pdf", "docx"})  # exact numbers: nothing to re-read on the image

PREVIEW = 80  # characters of document text in outline previews


class DocInfo(IRModel):
    id: str
    source: str
    format: Literal["pdf", "docx"]
    pages: int
    status: DocumentStatus
    version: int


class CellView(IRModel):
    row: int
    col: int
    rowspan: int
    colspan: int
    is_header: bool
    content: DocText


class TableView(IRModel):
    n_rows: int
    n_cols: int
    header_rows: int
    cells: list[CellView]


class BlockView(IRModel):
    id: str
    kind: BlockKind
    order: int
    status: BlockStatus
    level: int | None
    page: int | None  # PDF page or DOCX segment
    text: DocText | None
    table: TableView | None
    anchors: list[SourceAnchor] | None  # only when geometry was asked for
    chosen_observation: str | None


class ObservationView(IRModel):
    id: str
    block: str
    engine: str
    engine_version: str
    task: TaskKind
    status: ObservationStatus
    label: str | None
    text: DocText | None
    table: TableView | None
    det_confidence: float | None
    rec_confidence: float | None
    raw_ref: str | None
    error: str | None
    anchor: SourceAnchor | None  # only when geometry was asked for


class OutlineNode(IRModel):
    block: str
    level: int | None
    text: DocText  # preview


class ImageRef(IRModel):
    asset: str
    path: str  # absolute path of the image file
    width: int
    height: int
    coord_space: Literal["image_px"] = "image_px"
    transform: Affine | None = None  # image px → page pt (PDF); None when there is no page geometry


def table_view(grid: TableGrid | None) -> TableView | None:
    if grid is None:
        return None
    return TableView(n_rows=grid.n_rows, n_cols=grid.n_cols, header_rows=grid.header_rows, cells=[
        CellView(row=c.row, col=c.col, rowspan=c.rowspan, colspan=c.colspan, is_header=c.is_header,
                 content=DocText(doc_text=c.content)) for c in grid.cells])


def block_view(state: DocumentState, block: Block, *, geometry: bool = True) -> BlockView:
    return BlockView(
        id=block.id, kind=block.kind, order=block.order, status=block.status, level=block.level,
        page=block_unit(state, block), text=DocText(doc_text=block.text) if block.text else None,
        table=table_view(block.cells), anchors=block.anchors if geometry else None,
        chosen_observation=block.chosen_observation,
    )


def observation_view(block: Block, obs: Observation, *, geometry: bool = True) -> ObservationView:
    return ObservationView(
        id=obs.id, block=block.id, engine=obs.engine, engine_version=obs.engine_version, task=obs.task,
        status=obs.status, label=obs.label, text=DocText(doc_text=obs.text) if obs.text else None,
        table=table_view(obs.cells), det_confidence=obs.det_confidence, rec_confidence=obs.rec_confidence,
        raw_ref=obs.raw_ref, error=obs.error, anchor=obs.anchor if geometry else None,
    )


def outline_nodes(state: DocumentState) -> list[OutlineNode]:
    return [OutlineNode(block=b.id, level=b.level, text=DocText(doc_text=b.text[:PREVIEW])) for b in outline(state)]


def doc_info(state: DocumentState) -> DocInfo:
    return DocInfo(id=state.id, source=state.source, format=state.format, pages=len(state.pages),
                   status=state.status, version=state.version)


OUTLINE_QUOTES = 8  # outline_review: the lines quoted


def unresolved_items(state: DocumentState) -> list[Unresolved]:
    """Open work in the document, in page / block order; signals the agent checked and closed are left out."""
    closed = {(c.target, c.kind, tuple(c.quotes)) for c in state.closed}
    return [u for u in _all_items(state) if (u.target, u.kind.value, tuple(q.doc_text for q in u.quotes)) not in closed]


def _all_items(state: DocumentState) -> list[Unresolved]:
    items: list[Unresolved] = []
    for page in state.pages:
        if page.status.value == "pending":
            items.append(Unresolved(target=f"p{page.n}", kind=UnresolvedKind.PAGE_PENDING,
                                    detail="content not acquired yet"))
    skipped = {m.block for m in state.missing if "budget" in m.reason}
    for missing in state.missing:
        kind = UnresolvedKind.BUDGET_SKIPPED if missing.block in skipped else UnresolvedKind.BLOCK_FAILED
        items.append(Unresolved(target=missing.block, kind=kind, detail=missing.reason))
    listed = {m.block for m in state.missing}
    for block in state.blocks:
        if block.status == BlockStatus.FAILED and block.id not in listed:
            items.append(Unresolved(target=block.id, kind=UnresolvedKind.BLOCK_FAILED,
                                    detail=block.decisions[-1].reason if block.decisions else "failed"))
        elif block.kind == BlockKind.TITLE and block.level is None and block.status not in HIDDEN:
            items.append(Unresolved(target=block.id, kind=UnresolvedKind.STRUCTURE_PENDING,
                                    detail="title level not decided"))
        elif block.status == BlockStatus.DEGRADED and any(d.choice == "pending" for d in block.decisions):
            items.append(Unresolved(target=block.id, kind=UnresolvedKind.STRUCTURE_PENDING,
                                    detail="structure left pending"))
    shown = [b for b in ordered(state) if b.status not in HIDDEN]
    texts = {b.id: _all_text(b) for b in shown}
    document = list(texts.values())
    for block in shown:  # characters no reader can use: font encodings without Unicode, stray scripts
        found = suspicious_characters(texts[block.id], document) if texts[block.id] else None
        if found:
            items.append(Unresolved(target=block.id, kind=UnresolvedKind.TEXT_SUSPICIOUS,
                                    detail=found + "; compare with the image"))
    for block in ordered(state):  # recognized tables whose own arithmetic points at a misread digit
        if block.kind == BlockKind.TABLE and block.cells is not None and block.status not in HIDDEN \
                and _chosen_engine(block) not in NATIVE_ENGINES:
            items += [Unresolved(target=block.id, kind=UnresolvedKind.TABLE_ARITHMETIC, detail=issue)
                      for issue in arithmetic_issues(block.cells)]
    for block in shown:  # text the program added from the local page reading where the output had none (Q133)
        if added_from_reading(block):
            items.append(Unresolved(
                target=block.id, kind=UnresolvedKind.TEXT_ADDED, quotes=_quotes([block.text or ""]),
                detail="the program added this line from its local reading of the page image, where the output had "
                       "nothing; look at the page: correct its text (replace_text), set its role (a title, part of a "
                       "neighbouring paragraph: join), exclude it if the local reading took a drawing or a mark for "
                       "text, or close the item if it is right as it stands"))
    in_tables, on_pages = within_tables(state, unaccounted_lines(state))
    for n, lines in on_pages.items():  # the local page reading sees text no block has (Q56)
        items.append(Unresolved(
            target=f"p{n}", kind=UnresolvedKind.TEXT_UNACCOUNTED, quotes=_quotes([ln.text for ln in lines]),
            detail=f"{len(lines)} line(s) seen on the page image are in no block, at {_places(lines)} (page points); "
                   "the quotes are the local reading; look at the page"))
    for found in in_tables:  # ... inside a table's region: what the table lacks
        sharing = (f"; the region is shared with {', '.join(found.sharing)} (one frame cut into several tables), "
                   "the lines may belong to any of them") if found.sharing else ""
        items.append(Unresolved(
            target=found.table, kind=UnresolvedKind.TEXT_UNACCOUNTED, quotes=_quotes([ln.text for ln in found.lines]),
            detail=f"{len(found.lines)} line(s) seen on the page image inside this table's region are in none of its "
                   f"cells, at {_places(found.lines)} (page points){sharing}; the quotes are the local reading. A head "
                   "row, a row or part of a cell may be lost: look at the region, then re-read the table (view_source "
                   "as table, a structure issue naming the rows) or the region (as text with page and bbox) and adopt "
                   "the reading, or fill the cells with set_cells; if the local reading took a drawing or a symbol for "
                   "text, close the item"))
    tables = {b.id for b in state.blocks if b.kind == BlockKind.TABLE}
    for block_id, segments in unseen_segments(state).items():
        what = "cell text(s) of this table" if block_id in tables else "segment(s) of this block"
        items.append(Unresolved(
            target=block_id, kind=UnresolvedKind.TEXT_NOT_SEEN, quotes=_quotes(segments),
            detail=f"{len(segments)} {what} are not seen on the page image where the block sits; "
                   "compare with the image"))
    from parserx.tools.formulas import disagreement, listed, passage_of, pending_candidates

    by_id = {b.id: b for b in state.blocks}
    for block_id, candidate in pending_candidates(state):  # the page reading has this passage with formulas (Q70)
        members = passage_of(state, block_id)
        lacks, adds = disagreement("\n".join(b.text or "" for b in members or [by_id[block_id]]), candidate)
        span = (f"the passage is {len(members)} blocks of the text layer ({members[0].id} … {members[-1].id}), "
                f"a look at {block_id} shows all of it; " if members else "")
        items.append(Unresolved(
            target=block_id, kind=UnresolvedKind.FORMULA_CANDIDATE, quotes=_quotes([candidate]),
            detail="the page reading writes this passage with its formulas as LaTeX, but it (and the editor's "
                   f"version) lacks characters the text layer has ({listed(lacks, ', ')}; it has "
                   f"{listed(adds, ', ') or 'nothing'} more — a reading can take a superscript l for 1); {span}"
                   "look at the image: where the passage's formulas need their structure written (or the text "
                   f"layer cut it into blocks), write it whole with transcribe_passage on {block_id}; for a few "
                   "characters, replace_text; where the output is right, close the item"))
    from parserx.tools import second_reading

    for block in ordered(state):  # scanned content with mathematics the second readings do not agree with
        read = second_reading.readings(block)
        if block.status in HIDDEN or not read or block.chosen_observation in {o.id for o in read}:
            continue
        found = second_reading.differing(block.text or "", [o.text or "" for o in read])
        if found is not None:
            lacks, adds = found
            who = ", ".join(o.engine_version.split(":", 1)[-1] for o in read)
            items.append(Unresolved(
                target=block.id, kind=UnresolvedKind.SECOND_READING, quotes=_quotes([o.text or "" for o in read]),
                detail=f"the scan engine read this block from the image (no text layer checks it); {who} read it "
                       f"again, each on its own (the quotes), and {'each' if len(read) > 1 else 'it'} differs from the "
                       f"output: the output has {listed(lacks, ', ') or 'nothing'} the second reading"
                       f"{'s' if len(read) > 1 else ''} lack and lacks {listed(adds, ', ') or 'nothing'} "
                       f"{'they have' if len(read) > 1 else 'it has'}. Models misread a small image, Greek letters or "
                       "a letter's case, and \"correct\" what is printed alike: decide each difference by the image "
                       "alone, as printed; close the item where the output is right"))
    for block in ordered(state):  # a rewrite no reading confirms: the reading it replaced, for a look at the image
        replaced = next((o for o in reversed(block.observations) if o.label == REWRITE_CANDIDATE), None)
        if block.status in HIDDEN or replaced is None or block.chosen_observation == replaced.id:
            continue
        has, had = disagreement(block.text or "", replaced.text or "")
        items.append(Unresolved(
            target=block.id, kind=UnresolvedKind.READING_DISAGREEMENT, quotes=_quotes([replaced.text or ""]),
            detail=f"this block was rewritten from the page image; the quote is the reading it replaced, and where "
                   f"they differ neither reading holds the rewrite (it has {listed(has, ', ') or 'nothing'} more, the "
                   f"replaced reading {listed(had, ', ') or 'nothing'}); look at the image: if the rewrite is right, "
                   "close the item; where the replaced reading is right, correct the block there (replace_text, "
                   "set_cells) — write what the image prints, typos of the original included"))
    for block in ordered(state):  # a page whose reading order two readings disagree on: the order kept, the other listed
        for d in block.decisions:
            if d.choice == ORDER_DISAGREEMENT and block_unit(state, block) is not None:
                items.append(Unresolved(
                    target=f"p{block_unit(state, block)}", kind=UnresolvedKind.ORDER_DISAGREEMENT,
                    detail="the scan engine read this page column by column and that order is kept; the service model "
                           f"read it in another order ({d.evidence.get('model_order', '')}), going back to columns it "
                           "had left; look at the page: if the kept order is right, close the item; else put the "
                           "blocks in the page's order (move)"))
    for block_id in figures_without_content(state):  # an image shown with nothing a reader who cannot see it gets
        items.append(Unresolved(
            target=block_id, kind=UnresolvedKind.FIGURE_WITHOUT_CONTENT,
            detail="this image is shown without a description and without transcribed text after it; look at it: "
                   "describe it if it carries information, or close the item with the reason (a code, a logo …)"))
    from parserx.reading.compare import lacking_in_transcription, read_inside
    from parserx.tools.describe_figure import unseen_in_description

    blocks = {b.id: b for b in state.blocks}
    for figure, inside in read_inside(state).items():  # the image's local reading has lines its text lacks (IO6-5)
        lacking = lacking_in_transcription(state, blocks[figure])
        if lacking:
            items.append(Unresolved(
                target=figure, kind=UnresolvedKind.TEXT_UNACCOUNTED, quotes=_quotes(lacking),
                detail=f"{len(lacking)} line(s) the local reading sees in this image are not in the text read from it "
                       "(the blocks after it); the quotes are the local reading. Look at the image and add what is "
                       "missing (insert_text, or set_cells in a table); if the local reading took a drawing or a "
                       "symbol for text, close the item. The image is shown above its text until nothing is missing"))

    for block in ordered(state):  # a picture's description quotes a number its image's reading lacks (IO6-4)
        numbers = unseen_in_description(block) if block.kind == BlockKind.FIGURE and block.status not in HIDDEN \
            and block.semantic is not None else []
        if numbers:
            items.append(Unresolved(
                target=block.id, kind=UnresolvedKind.CAPTION_NUMBER_UNSEEN, quotes=_quotes(numbers),
                detail="the description quotes these numbers, but the local reading of the image does not have them: "
                       "the model may have misread a digit (or the local reader a blurred or styled one); look at the "
                       "image and describe it again (view_source as description, then adopt), or close the item"))
    for block_id, text, level, evidence in layout_titles(state):  # the page image shows a title the outline lacks
        items.append(Unresolved(
            target=block_id, kind=UnresolvedKind.TITLE_CANDIDATE, quotes=_quotes([text]),
            detail=f"the layout detector marks this line a section title and it is set apart from the body text "
                   f"({evidence['set_apart']}); the outline does not have it — look at the page and, if it is a "
                   f"title, set its role and level (proposed level {level})"))
    listed = {u.target for u in items if u.kind == UnresolvedKind.TITLE_CANDIDATE}
    for block_id, text, level, evidence in numbering_gaps(state):  # a numbered title sequence misses this number
        if block_id not in listed:
            items.append(Unresolved(
                target=block_id, kind=UnresolvedKind.TITLE_CANDIDATE, quotes=_quotes([text]),
                detail=f"the numbered titles around it miss the number this paragraph starts with "
                       f"({evidence['numbering']}); if it is a title, set its role and level (proposed level {level})"))
    listed = {u.target for u in items if u.kind == UnresolvedKind.TITLE_CANDIDATE}
    for block_id, text, level, evidence in series_successors(state):  # the next number of a title's series
        if block_id not in listed:
            items.append(Unresolved(
                target=block_id, kind=UnresolvedKind.TITLE_CANDIDATE, quotes=_quotes([text]),
                detail=f"this paragraph starts with {evidence['numbering']} in the same style; look at the page and, "
                       f"if it is a title set like that one, set its role and level (proposed level {level})"))
    from parserx.tools.draft import title_like

    asked = {u.target for u in items if u.kind == UnresolvedKind.TITLE_CANDIDATE}
    like = [b for b in title_like(state) if b.id not in asked]
    shown = [b for b in ordered(state) if b.status not in HIDDEN and (b.text or "").strip()]
    if any(p.status == PageStatus.PENDING for p in state.pages):  # the outline is looked at once every page is read
        like, shown = [], []
    if shown and not any(b.kind == BlockKind.TITLE for b in shown):  # an outline nobody would look at otherwise
        items.append(Unresolved(
            target=(like or shown)[0].id, kind=UnresolvedKind.OUTLINE_REVIEW,
            quotes=_quotes([b.text or "" for b in (like or shown)[:OUTLINE_QUOTES]]),
            detail="the output has no title. Look at the first page: if the document has a title or section headings "
                   "(the quotes are its first lines, or lines numbered or set like titles), set their roles and "
                   "levels; if it has none (a form, a receipt, a single table), close the item"))
    elif like:
        items.append(Unresolved(
            target=like[0].id, kind=UnresolvedKind.OUTLINE_REVIEW,
            quotes=_quotes([b.text or "" for b in like[:OUTLINE_QUOTES]]),
            detail=f"{len(like)} one-line paragraph(s) numbered or set like titles are not titles (the quotes, the "
                   "first of them): a level of headings the outline lacks, or list items, labels, notes. Look at the "
                   "outline (read_draft view outline) and the page; set the roles and levels of those that are "
                   "headings, then close the item"))
    for block_id, text, above in unclear_nesting(state):  # which of two numbering styles is the outer one
        items.append(Unresolved(
            target=block_id, kind=UnresolvedKind.TITLE_LEVEL_UNCLEAR, quotes=_quotes([text, above]),
            detail="this title's numbering style begins here, right after a title of another style, and not at its "
                   "first number: either the text began inside a section of this style (a page cut from a document; "
                   "then the titles of the other style belong one level below this one) or its list's earlier items "
                   "were not found and it nests one level below the title above. Look at the page — how the two "
                   "styles are set (bold, italic, size, indentation) — and set the levels of both series"))
    for candidate in merge_candidates(state):
        items.append(Unresolved(target=candidate.second, kind=UnresolvedKind.TABLE_MERGE_CANDIDATE,
                                detail=f"may continue {candidate.first}: "
                                       + ", ".join(f"{k}={v}" for k, v in candidate.evidence.items())))
    return items


def figures_without_content(state: DocumentState) -> list[str]:
    """Shown figures whose content does not reach a reader who cannot see them (Q66, conservation): processing gave
    them content — a description was attempted, or text was transcribed from them — and none of it is in the output
    (the description failed, the transcription was removed).  Figures nothing was tried on yet are not listed."""
    shown = {b.id: b for b in state.blocks if b.status not in HIDDEN}
    contained: dict[str, list[str]] = {}
    for r in state.relations:
        if r.kind == RelationKind.CONTAINS:
            contained.setdefault(r.src, []).append(r.dst)
    out = []
    for block in ordered(state):
        if block.id not in shown or block.kind != BlockKind.FIGURE or block.semantic is not None:
            continue
        described = any(o.task == TaskKind.DESCRIBE for o in block.observations)
        transcribed = contained.get(block.id, [])
        if any(d in shown and _all_text(shown[d]).strip() for d in transcribed):
            continue
        if described or transcribed:
            out.append(block.id)
    return out


_QUOTES = 5  # quoted texts per item; the count says how many there are


def _places(lines: list) -> str:
    return ", ".join("(" + ", ".join(f"{v:.0f}" for v in ln.bbox) + ")" for ln in lines[:_QUOTES])


def _quotes(texts: list[str]) -> list[DocText]:
    return [DocText(doc_text=t) for t in ClosedItem.quoted(texts)]


def _all_text(block: Block) -> str:
    cells = " ".join(c.content for c in block.cells.cells) if block.cells is not None else ""
    return (block.text or "") + (" " + cells if cells else "")


def _chosen_engine(block: Block) -> str | None:
    chosen = next((o for o in block.observations if o.id == block.chosen_observation), None)
    return chosen.engine if chosen is not None else None


def unresolved_counts(state: DocumentState) -> dict[UnresolvedKind, int]:
    return dict(sorted(Counter(u.kind for u in unresolved_items(state)).items()))
