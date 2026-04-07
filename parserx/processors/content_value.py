"""Content-value processor.

Scores text/image elements by how much independent information they add to the
document, then suppresses low-value shell/chrome noise without relying on
app-specific keyword rules.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from parserx.config.schema import ContentValueConfig
from parserx.models.elements import Document, Page, PageElement
from parserx.services.llm import LLMService

log = logging.getLogger(__name__)

_SENTENCE_END_RE = re.compile(r"[。！？!?；;.]$")
_LIST_ITEM_RE = re.compile(r"^(?:[-*•]\s+|\d+[.)、]\s+)")
_PURE_SYMBOL_RE = re.compile(r"^[\W_?？!！]+$")
_METADATA_FIELD_RE = re.compile(r"^[\u4e00-\u9fffA-Za-z]{1,12}\s*[：:]\s*.+$")
_DATEISH_RE = re.compile(r"^\d{4}(?:[./\-年])\s*\d{1,2}(?:[./\-月])(?:\s*\d{1,2}(?:日)?)?$")
_SHORT_CJK_NAME_RE = re.compile(r"^[\u4e00-\u9fff]{2,6}$")

_LLM_SYSTEM = """\
你是文档信息价值判别助手。请判断候选块在脱离原界面/版式后，是否仍然对文档理解有独立信息价值。

返回 JSON 数组，每项格式如下：
{"idx": 1, "keep": true}

判断原则：
- 保留：事实、结论、数据、步骤、问题陈述、结构锚点、图注/表题、与正文强相关的短标签
- 删除：导航、按钮、输入框提示、品牌壳层、纯装饰、重复容器文案、无独立价值的边缘噪声
- 不要根据具体 App 名称做判断，只看信息价值
- 不要输出任何额外说明"""


@dataclass(frozen=True)
class _PageSignals:
    width: float
    height: float
    body_left: float
    body_right: float
    repeated_small_xrefs: set[int]


def _compact_text(text: str) -> str:
    return " ".join(part.strip() for part in text.splitlines() if part.strip())


def _text_len(text: str) -> int:
    return len(_compact_text(text))


def _line_count(text: str) -> int:
    return len([line for line in text.splitlines() if line.strip()]) or 1


def _weighted_quantile(values: list[tuple[float, int]], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values, key=lambda item: item[0])
    total = sum(weight for _, weight in ordered)
    threshold = total * quantile
    running = 0
    for value, weight in ordered:
        running += weight
        if running >= threshold:
            return value
    return ordered[-1][0]


def _x_overlap_ratio(elem: PageElement, left: float, right: float) -> float:
    x0, _y0, x1, _y1 = elem.bbox
    width = max(x1 - x0, 1.0)
    overlap = max(0.0, min(x1, right) - max(x0, left))
    return overlap / width


def _vertical_gap(a: PageElement, b: PageElement) -> float:
    return max(b.bbox[1] - a.bbox[3], a.bbox[1] - b.bbox[3], 0.0)


def _build_page_signals(page: Page) -> _PageSignals:
    body_candidates = [
        elem for elem in page.elements
        if elem.type == "text"
        and not elem.metadata.get("skip_render")
        and not elem.metadata.get("heading_level")
        and (
            _text_len(elem.content) >= 24
            or _SENTENCE_END_RE.search(_compact_text(elem.content))
        )
    ]
    x0s = [(elem.bbox[0], max(_text_len(elem.content), 1)) for elem in body_candidates]
    x1s = [(elem.bbox[2], max(_text_len(elem.content), 1)) for elem in body_candidates]
    if body_candidates:
        body_left = _weighted_quantile(x0s, 0.25)
        body_right = _weighted_quantile(x1s, 0.75)
    else:
        body_left = 0.0
        body_right = page.width

    xref_counts: dict[int, int] = {}
    for elem in page.elements:
        if elem.type != "image":
            continue
        xref = int(elem.metadata.get("xref", 0) or 0)
        width = elem.bbox[2] - elem.bbox[0]
        height = elem.bbox[3] - elem.bbox[1]
        if xref <= 0 or width > 180 or height > 180:
            continue
        xref_counts[xref] = xref_counts.get(xref, 0) + 1

    repeated_small_xrefs = {xref for xref, count in xref_counts.items() if count >= 2}
    return _PageSignals(
        width=page.width,
        height=page.height,
        body_left=body_left,
        body_right=body_right,
        repeated_small_xrefs=repeated_small_xrefs,
    )


class ContentValueProcessor:
    """Suppress low-information shell/chrome while preserving useful evidence."""

    def __init__(
        self,
        config: ContentValueConfig | None = None,
        llm_service: LLMService | None = None,
    ):
        self._config = config or ContentValueConfig()
        self._llm = llm_service

    def process(self, doc: Document) -> Document:
        if not self._config.enabled:
            return doc

        llm_candidates: list[dict[str, object]] = []
        for page in doc.pages:
            page_signals = _build_page_signals(page)
            for idx, elem in enumerate(page.elements):
                if elem.metadata.get("skip_render"):
                    continue
                if elem.type == "text":
                    decision = self._score_text(page, page_signals, idx, elem)
                    elem.metadata["informational_value_score"] = round(decision["score"], 3)
                    elem.metadata["informational_value_reason"] = decision["reason"]
                    if decision["decision"] == "drop":
                        self._suppress_text(elem, reason=decision["reason"])
                    elif (
                        decision["decision"] == "gray"
                        and self._config.llm_fallback
                        and self._llm is not None
                        and len(llm_candidates) < self._config.max_llm_candidates
                    ):
                        llm_candidates.append({
                            "page": page,
                            "elem": elem,
                            "idx": len(llm_candidates) + 1,
                            "text": _compact_text(elem.content),
                            "reason": decision["reason"],
                        })
                elif elem.type == "image":
                    score, reason = self._score_image(page_signals, elem)
                    elem.metadata["informational_value_score"] = round(score, 3)
                    elem.metadata["informational_value_reason"] = reason
                    if score < self._config.low_value_threshold:
                        self._suppress_image(elem, reason)

        self._apply_llm_review(llm_candidates)
        return doc

    def _score_text(
        self,
        page: Page,
        page_signals: _PageSignals,
        elem_idx: int,
        elem: PageElement,
    ) -> dict[str, object]:
        text = _compact_text(elem.content)
        if not text:
            return {"score": 0.0, "decision": "drop", "reason": "empty_text"}

        if elem.metadata.get("heading_level") or elem.metadata.get("caption"):
            return {"score": 1.0, "decision": "keep", "reason": "structure_anchor"}
        if _LIST_ITEM_RE.match(text):
            return {"score": 0.8, "decision": "keep", "reason": "list_item"}

        score = 0.0
        reasons: list[str] = []
        char_count = len(text)
        line_count = _line_count(elem.content)
        width = max(elem.bbox[2] - elem.bbox[0], 1.0)
        density = char_count / width
        y0, y1 = elem.bbox[1], elem.bbox[3]

        in_body = _x_overlap_ratio(elem, page_signals.body_left, page_signals.body_right) >= 0.55
        top_edge = bool(page.height) and y0 <= page.height * 0.1
        bottom_edge = bool(page.height) and y1 >= page.height * 0.9
        left_edge = bool(page.width) and elem.bbox[0] <= page.width * 0.08
        right_edge = bool(page.width) and elem.bbox[2] >= page.width * 0.92
        compact_list_item = self._looks_like_compact_list_item(
            page.elements,
            elem_idx,
            elem,
            page_signals,
        )
        closing_signature = self._looks_like_trailing_signature(page.elements, elem_idx, elem)

        is_ocr = elem.source == "ocr"

        if char_count >= 24:
            score += 0.35
            reasons.append("content_dense")
        if _SENTENCE_END_RE.search(text):
            score += 0.18
            reasons.append("sentence_like")
        if self._looks_like_cover_title(page, elem, in_body=in_body, top_edge=top_edge):
            score += 0.65
            reasons.append("cover_title")
        if self._looks_like_cover_metadata(page, elem, top_edge=top_edge):
            score += 0.4
            reasons.append("cover_metadata")
        if text.endswith(("：", ":")):
            score += 0.14
            reasons.append("prompt_anchor")
        if in_body:
            score += 0.22
            reasons.append("body_column")
        if elem.source == "ocr":
            # OCR elements come from actual document page content,
            # not from UI chrome or navigation.  Give them a strong
            # baseline so position/fragmentation penalties don't filter
            # out real content — even short labels carry information
            # value in scanned documents (stock codes, ratings, etc.).
            score += 0.25
            reasons.append("ocr_content")
        if self._has_body_continuity(page.elements, elem_idx, elem, page_signals):
            score += 0.18
            reasons.append("body_continuity")
        if closing_signature:
            score += 0.35
            reasons.append("closing_signature")
        if compact_list_item:
            score += 0.28
            reasons.append("compact_list_item")
        if char_count <= 16 and density < 0.09 and not is_ocr:
            # Short sparse text penalty — only for native (non-OCR) elements.
            # OCR content comes from actual document pages and even short
            # labels (stock codes, ratings, metadata) carry information
            # value.  Penalizing them causes identity metadata loss.
            if compact_list_item or closing_signature:
                score -= 0.08
            elif in_body:
                score -= 0.10
            else:
                score -= 0.32
            reasons.append("sparse_short_text")
        # OCR blocks on scanned pages get their position from the
        # document layout, not from UI structure.  Position-based and
        # fragmentation penalties are lighter for OCR sources.
        if not is_ocr and line_count >= 2 and char_count <= 24:
            avg_line_len = char_count / max(line_count, 1)
            if avg_line_len <= 8:
                score -= 0.22
                reasons.append("multi_short_lines")
        if not is_ocr and (top_edge or bottom_edge):
            score -= 0.18
            reasons.append("edge_band")
        if not is_ocr and (left_edge or right_edge):
            score -= 0.12
            reasons.append("side_edge")
        if width >= page_signals.width * 0.75 and char_count <= 20:
            score -= 0.22
            reasons.append("wide_sparse_banner")
        if char_count <= 24 and self._is_near_image_cluster(page.elements, elem_idx, elem):
            score -= 0.1 if compact_list_item else 0.25
            reasons.append("image_cluster")
        if _PURE_SYMBOL_RE.match(text) or char_count <= 1:
            score -= 0.4
            reasons.append("symbol_only")
        if (
            char_count <= 12
            and line_count == 1
            and in_body
            and self._anchors_following_body(page.elements, elem_idx, elem, page_signals)
        ):
            score += 0.4
            reasons.append("short_section_anchor")

        threshold = self._config.low_value_threshold
        if score < threshold:
            return {
                "score": score,
                "decision": "drop",
                "reason": ",".join(reasons) or "low_information_value",
            }
        if score < threshold + self._config.gray_zone_margin:
            return {
                "score": score,
                "decision": "gray",
                "reason": ",".join(reasons) or "gray_zone",
            }
        return {"score": score, "decision": "keep", "reason": ",".join(reasons) or "body_text"}

    def _score_image(self, page_signals: _PageSignals, elem: PageElement) -> tuple[float, str]:
        if elem.metadata.get("skipped"):
            return 0.0, "already_skipped"
        if elem.metadata.get("caption"):
            return 0.8, "captioned_image"
        if elem.metadata.get("text_heavy_image"):
            return 0.85, "text_evidence_image"
        if elem.metadata.get("description") or elem.metadata.get("ocr_overlap_text"):
            return 0.7, "described_or_evidence_image"

        xref = int(elem.metadata.get("xref", 0) or 0)
        width = elem.bbox[2] - elem.bbox[0]
        height = elem.bbox[3] - elem.bbox[1]
        if (
            xref > 0
            and xref in page_signals.repeated_small_xrefs
            and width <= 180
            and height <= 180
        ):
            return 0.05, "repeated_small_asset"
        return 0.5, "default_image"

    def _apply_llm_review(self, candidates: list[dict[str, object]]) -> None:
        if not candidates or self._llm is None or not self._config.llm_fallback:
            return

        user_payload = [
            {
                "idx": candidate["idx"],
                "text": candidate["text"],
                "reason": candidate["reason"],
            }
            for candidate in candidates
        ]
        try:
            response = self._llm.complete(
                _LLM_SYSTEM,
                json.dumps(user_payload, ensure_ascii=False),
                temperature=0.0,
                max_tokens=1024,
            )
            decisions = json.loads(response)
        except Exception as exc:
            log.warning("Content-value LLM review failed: %s", exc)
            return

        keep_map = {
            int(item["idx"]): bool(item.get("keep"))
            for item in decisions
            if isinstance(item, dict) and str(item.get("idx", "")).isdigit()
        }
        for candidate in candidates:
            elem = candidate["elem"]
            idx = int(candidate["idx"])
            keep = keep_map.get(idx)
            if keep is None:
                continue
            elem.metadata["informational_value_llm_reviewed"] = True
            if not keep:
                self._suppress_text(elem, reason="llm_low_information_value")
            else:
                elem.metadata["informational_value_reason"] = (
                    str(elem.metadata.get("informational_value_reason", "")) + ",llm_keep"
                ).strip(",")

    def _has_body_continuity(
        self,
        elements: list[PageElement],
        elem_idx: int,
        elem: PageElement,
        page_signals: _PageSignals,
    ) -> bool:
        prev_elem = self._neighbor_text(elements, elem_idx, step=-1)
        next_elem = self._neighbor_text(elements, elem_idx, step=1)
        for neighbor in (prev_elem, next_elem):
            if neighbor is None:
                continue
            if _text_len(neighbor.content) < 18:
                continue
            if _x_overlap_ratio(neighbor, page_signals.body_left, page_signals.body_right) < 0.5:
                continue
            if _vertical_gap(elem, neighbor) <= 70:
                return True
        return False

    def _looks_like_cover_title(
        self,
        page: Page,
        elem: PageElement,
        *,
        in_body: bool,
        top_edge: bool,
    ) -> bool:
        if page.number != 1 or not top_edge or not in_body:
            return False
        text = _compact_text(elem.content)
        if not text:
            return False
        if _METADATA_FIELD_RE.match(text):
            return False
        if _SENTENCE_END_RE.search(text):
            return False
        return 6 <= len(text) <= 28

    def _looks_like_cover_metadata(
        self,
        page: Page,
        elem: PageElement,
        *,
        top_edge: bool,
    ) -> bool:
        if page.number != 1 or not top_edge:
            return False
        text = _compact_text(elem.content)
        return bool(_METADATA_FIELD_RE.match(text))

    def _looks_like_trailing_signature(
        self,
        elements: list[PageElement],
        elem_idx: int,
        elem: PageElement,
    ) -> bool:
        text = _compact_text(elem.content)
        if not text or len(text) > 20:
            return False

        prev_elem = self._neighbor_text(elements, elem_idx, step=-1)
        if prev_elem is None:
            return False

        next_elem = self._neighbor_text(elements, elem_idx, step=1)
        if next_elem is not None and len(_compact_text(next_elem.content)) > 20:
            return False

        prev_text = _compact_text(prev_elem.content)
        if len(prev_text) < 24 and _SHORT_CJK_NAME_RE.match(prev_text):
            prev_prev = self._neighbor_text(elements, elements.index(prev_elem), step=-1)
            if prev_prev is None or len(_compact_text(prev_prev.content)) < 24:
                return False
        elif len(prev_text) < 24:
            return False

        if _DATEISH_RE.match(text):
            return True
        if _SHORT_CJK_NAME_RE.match(text):
            return True
        return False

    def _anchors_following_body(
        self,
        elements: list[PageElement],
        elem_idx: int,
        elem: PageElement,
        page_signals: _PageSignals,
    ) -> bool:
        next_elem = self._neighbor_text(elements, elem_idx, step=1)
        if next_elem is None:
            return False
        if _text_len(next_elem.content) < 24:
            return False
        if _x_overlap_ratio(next_elem, page_signals.body_left, page_signals.body_right) < 0.5:
            return False
        return _vertical_gap(elem, next_elem) <= 50

    def _looks_like_compact_list_item(
        self,
        elements: list[PageElement],
        elem_idx: int,
        elem: PageElement,
        page_signals: _PageSignals,
    ) -> bool:
        text = _compact_text(elem.content)
        if len(text) > 40:
            return False
        if _x_overlap_ratio(elem, page_signals.body_left, page_signals.body_right) < 0.5:
            return False

        prev_elem = self._neighbor_text(elements, elem_idx, step=-1)
        next_elem = self._neighbor_text(elements, elem_idx, step=1)

        if (
            prev_elem is not None
            and _compact_text(prev_elem.content).endswith(("：", ":"))
            and _vertical_gap(prev_elem, elem) <= 70
        ):
            return True

        sibling_count = 0
        text_has_list_shape = any(marker in text for marker in ("（", "(", "、", "：", ":"))
        for neighbor in (prev_elem, next_elem):
            if neighbor is None:
                continue
            if len(_compact_text(neighbor.content)) > 40:
                continue
            if _vertical_gap(elem, neighbor) > 70:
                continue
            if abs(neighbor.bbox[0] - elem.bbox[0]) > 80:
                continue
            sibling_count += 1
        return sibling_count >= 1 and text_has_list_shape

    def _is_near_image_cluster(
        self,
        elements: list[PageElement],
        elem_idx: int,
        elem: PageElement,
    ) -> bool:
        nearby = 0
        for candidate in elements:
            if candidate.type != "image":
                continue
            vertical_distance = min(
                abs(candidate.bbox[1] - elem.bbox[3]),
                abs(elem.bbox[1] - candidate.bbox[3]),
                abs(((candidate.bbox[1] + candidate.bbox[3]) / 2) - ((elem.bbox[1] + elem.bbox[3]) / 2)),
            )
            horizontal_overlap = max(
                0.0,
                min(candidate.bbox[2], elem.bbox[2]) - max(candidate.bbox[0], elem.bbox[0]),
            )
            if vertical_distance <= 80 or horizontal_overlap >= 40:
                nearby += 1
        return nearby >= 2

    def _neighbor_text(
        self,
        elements: list[PageElement],
        elem_idx: int,
        *,
        step: int,
    ) -> PageElement | None:
        i = elem_idx + step
        while 0 <= i < len(elements):
            candidate = elements[i]
            if candidate.type == "text" and not candidate.metadata.get("skip_render"):
                return candidate
            i += step
        return None

    def _suppress_text(self, elem: PageElement, *, reason: str) -> None:
        if not self._config.suppress_low_value:
            return
        elem.metadata["skip_render"] = True
        elem.metadata["low_information_value"] = reason

    def _suppress_image(self, elem: PageElement, reason: str) -> None:
        if not self._config.suppress_low_value:
            return
        elem.metadata["skipped"] = True
        elem.metadata["skip_render"] = True
        elem.metadata["needs_vlm"] = False
        elem.metadata["low_information_value"] = reason
