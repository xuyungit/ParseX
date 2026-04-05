"""Resolve figure and table captions into nearby elements."""

from __future__ import annotations

import re
from dataclasses import dataclass

from parserx.models.elements import Document, Page, PageElement

_NUM_TOKEN = r"[A-Za-z0-9零〇一二三四五六七八九十百千万两IVXivx\-\.\(\)]+"
_FIGURE_RE = re.compile(
    rf"^\s*(?P<label>图表|图|Figure|Fig\.?)\s*(?P<number>{_NUM_TOKEN})?\s*"
    r"(?:[:：.\-]\s*|\s+)?(?P<title>.+?)\s*$",
    re.IGNORECASE,
)
_TABLE_RE = re.compile(
    rf"^\s*(?P<label>表|Table)\s*(?P<number>{_NUM_TOKEN})?\s*"
    r"(?:[:：.\-]\s*|\s+)?(?P<title>.+?)\s*$",
    re.IGNORECASE,
)

_FIGURE_LABELS = {"图", "图表", "figure", "fig."}
_TABLE_LABELS = {"表", "table"}
_CAPTION_MAX_LEN = 160
_FIGURE_MAX_GAP = 140.0
_TABLE_MAX_GAP = 110.0


@dataclass(frozen=True)
class _CaptionCandidate:
    element: PageElement
    kind: str
    text: str


class CrossReferenceResolver:
    """Attach nearby figure/table captions to their target elements."""

    def resolve(self, doc: Document) -> Document:
        for page in doc.pages:
            self._resolve_page(page)
        return doc

    def _resolve_page(self, page: Page) -> None:
        for index, element in enumerate(page.elements):
            element.metadata.setdefault("_page_order", index)

        captions = self._find_caption_candidates(page.elements)
        if not captions:
            return

        targets = [
            elem for elem in page.elements
            if elem.type in {"image", "table"} and not elem.metadata.get("skipped")
        ]
        if not targets:
            return

        proposals: list[tuple[float, _CaptionCandidate, PageElement]] = []
        for caption in captions:
            for target in targets:
                score = self._score_match(caption, target)
                if score is not None:
                    proposals.append((score, caption, target))

        used_captions: set[int] = set()
        used_targets: set[int] = set()

        for _, caption, target in sorted(proposals, key=lambda item: item[0], reverse=True):
            caption_id = id(caption.element)
            target_id = id(target)
            if caption_id in used_captions or target_id in used_targets:
                continue

            target.metadata["caption"] = caption.text
            target.metadata["caption_kind"] = caption.kind
            caption.element.metadata["skip_render"] = True
            caption.element.metadata["caption_target"] = target.type

            used_captions.add(caption_id)
            used_targets.add(target_id)

    def _find_caption_candidates(
        self,
        elements: list[PageElement],
    ) -> list[_CaptionCandidate]:
        candidates: list[_CaptionCandidate] = []

        for elem in elements:
            if elem.type != "text":
                continue
            if elem.metadata.get("heading_level") or elem.metadata.get("skip_render"):
                continue

            text = " ".join(part.strip() for part in elem.content.splitlines() if part.strip()).strip()
            if not text or len(text) > _CAPTION_MAX_LEN:
                continue

            kind = self._classify_caption(text)
            if kind is None:
                continue

            candidates.append(_CaptionCandidate(element=elem, kind=kind, text=text))

        return candidates

    def _classify_caption(self, text: str) -> str | None:
        match = _FIGURE_RE.match(text)
        if match:
            label = match.group("label").lower()
            if label in _FIGURE_LABELS and self._looks_like_caption(match.group("number"), match.group("title")):
                return "figure"

        match = _TABLE_RE.match(text)
        if match:
            label = match.group("label").lower()
            if label in _TABLE_LABELS and self._looks_like_caption(match.group("number"), match.group("title")):
                return "table"

        return None

    def _looks_like_caption(self, number: str | None, title: str | None) -> bool:
        if number:
            return True
        if not title:
            return False
        compact = title.strip()
        return 2 <= len(compact) <= 60

    def _score_match(
        self,
        caption: _CaptionCandidate,
        target: PageElement,
    ) -> float | None:
        if caption.kind == "figure" and target.type != "image":
            return None
        if caption.kind == "table" and target.type != "table":
            return None

        if self._has_bbox(caption.element) and self._has_bbox(target):
            score = self._score_bbox_match(caption, target)
            if score is not None:
                return score

        return self._score_sequential_match(caption, target)

    def _score_bbox_match(
        self,
        caption: _CaptionCandidate,
        target: PageElement,
    ) -> float | None:
        cx0, cy0, cx1, cy1 = caption.element.bbox
        tx0, ty0, tx1, ty1 = target.bbox

        overlap = self._horizontal_overlap_ratio((cx0, cx1), (tx0, tx1))
        caption_center = (cx0 + cx1) / 2
        centered = tx0 <= caption_center <= tx1
        if overlap < 0.2 and not centered:
            return None

        gap_above = ty0 - cy1
        gap_below = cy0 - ty1
        candidates = []
        if gap_above >= 0:
            candidates.append(("above", gap_above))
        if gap_below >= 0:
            candidates.append(("below", gap_below))
        if not candidates:
            return None

        placement, gap = min(candidates, key=lambda item: item[1])
        max_gap = _TABLE_MAX_GAP if caption.kind == "table" else _FIGURE_MAX_GAP
        if gap > max_gap:
            return None

        preferred = (
            caption.kind == "table" and placement == "above"
        ) or (
            caption.kind == "figure" and placement == "below"
        )

        return overlap * 2.0 + (0.45 if preferred else 0.1) - (gap / max_gap)

    def _score_sequential_match(
        self,
        caption: _CaptionCandidate,
        target: PageElement,
    ) -> float | None:
        caption_order = caption.element.metadata.get("_page_order")
        target_order = target.metadata.get("_page_order")
        if caption_order is None or target_order is None:
            return None

        distance = abs(caption_order - target_order)
        if distance > 1:
            return None

        preferred = (
            caption.kind == "table" and caption_order < target_order
        ) or (
            caption.kind == "figure" and caption_order > target_order
        )
        return 0.75 - distance * 0.2 + (0.15 if preferred else 0.0)

    def _has_bbox(self, element: PageElement) -> bool:
        return element.bbox != (0.0, 0.0, 0.0, 0.0)

    def _horizontal_overlap_ratio(
        self,
        a: tuple[float, float],
        b: tuple[float, float],
    ) -> float:
        ax0, ax1 = a
        bx0, bx1 = b
        overlap = min(ax1, bx1) - max(ax0, bx0)
        if overlap <= 0:
            return 0.0
        width = max(min(ax1 - ax0, bx1 - bx0), 1.0)
        return overlap / width
