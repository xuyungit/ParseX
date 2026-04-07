"""Image processor — classify images and generate VLM descriptions.

Strategy: classify first, process selectively.
VLM calls are batched and executed concurrently via ThreadPoolExecutor.
"""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
from PIL import Image

from parserx.config.schema import ImageProcessorConfig, TableProcessorConfig
from parserx.models.elements import Document, Page, PageElement
from parserx.services.llm import OpenAICompatibleService
from parserx.text_utils import compute_edit_distance, normalize_for_comparison

log = logging.getLogger(__name__)

# ── Image classification thresholds ─────────────────────────────────────

BLANK_STD_THRESHOLD = 1.0
TRIVIAL_AREA_MAX = 12000
TRIVIAL_LONG_EDGE_MAX = 160
STRIP_SHORT_EDGE_MAX = 80
STRIP_ASPECT_MIN = 12.0
MIN_DIMENSION = 30


class ImageClassification:
    DECORATIVE = "decorative"
    INFORMATIONAL = "informational"
    TABLE_IMAGE = "table_image"
    TEXT_IMAGE = "text_image"
    BLANK = "blank"


def classify_image_element(elem: PageElement) -> str:
    """Classify an image element using heuristic rules.

    The ``layout_type`` branches (TABLE_IMAGE / TEXT_IMAGE) depend on an
    upstream provider or builder populating :pyattr:`PageElement.layout_type`
    on image elements.  As of now **no provider/builder does this** — the
    planned LayoutBuilder is not yet implemented — so these branches are
    effectively dormant.  They are kept for forward-compatibility; when the
    LayoutBuilder lands, images will automatically route through them.
    """
    width = elem.metadata.get("width", 0)
    height = elem.metadata.get("height", 0)

    if width == 0 or height == 0:
        return ImageClassification.BLANK

    short_edge = min(width, height)
    long_edge = max(width, height)
    area = width * height
    aspect = long_edge / max(short_edge, 1)

    if short_edge <= 4:
        return ImageClassification.DECORATIVE
    if short_edge <= STRIP_SHORT_EDGE_MAX and aspect >= STRIP_ASPECT_MIN:
        return ImageClassification.DECORATIVE
    if area <= TRIVIAL_AREA_MAX and long_edge <= TRIVIAL_LONG_EDGE_MAX:
        return ImageClassification.DECORATIVE
    if width < MIN_DIMENSION or height < MIN_DIMENSION:
        return ImageClassification.DECORATIVE

    # NOTE: layout_type routing — currently dormant (see docstring).
    layout = elem.layout_type
    if layout == "table":
        log.debug("Image classified as TABLE_IMAGE via layout_type")
        return ImageClassification.TABLE_IMAGE
    if layout == "text":
        log.debug("Image classified as TEXT_IMAGE via layout_type")
        return ImageClassification.TEXT_IMAGE

    return ImageClassification.INFORMATIONAL


def classify_image_file(image_path: Path) -> str:
    """Classify by loading pixels — catches blank images."""
    try:
        arr = np.array(Image.open(image_path).convert("L"))
    except Exception:
        return ImageClassification.BLANK
    if arr.std() < BLANK_STD_THRESHOLD:
        return ImageClassification.BLANK
    return ImageClassification.INFORMATIONAL


# ── VLM prompt ──────────────────────────────────────────────────────────

_VLM_RESPONSE_SCHEMA = {
    "image_type": "table|text|diagram|chart|photo|other",
    "summary": "brief grounded description",
    "visible_text": "exact visible text transcription",
    "markdown": "markdown table or other markdown when appropriate",
}

_VLM_JSON_SCHEMA_NAME = "parserx_image_description"
_VLM_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["image_type", "summary", "visible_text", "markdown"],
    "properties": {
        "image_type": {
            "type": "string",
            "enum": ["table", "text", "diagram", "chart", "photo", "other"],
        },
        "summary": {"type": "string"},
        "visible_text": {"type": "string"},
        "markdown": {"type": "string"},
    },
}

_STRICT_ZH_SYSTEM_PROMPT = """\
你是一个严谨的文档图片解读助手。你必须只依据图片中肉眼可见的内容作答，不能补全、推测或改写数字。

输出要求：
- 只返回一个 JSON 对象，不要输出 Markdown 代码块，不要输出解释
- JSON 字段必须包含：image_type, summary, visible_text, markdown
- 如果图片里没有明显文字，visible_text 置为空字符串
- 如果图片里不是表格，markdown 置为空字符串
- summary 保持简洁，最多 3 句
- 所有数字、日期、编号必须与图片中可见内容完全一致
- 如果看不清，宁可留空，也不要猜
"""

_STRICT_EN_SYSTEM_PROMPT = """\
You are a strict document-image interpreter. Only describe content that is clearly visible in the image. Never infer, normalize, or correct numbers.

Output rules:
- Return one JSON object only, with no markdown code fences and no extra explanation
- The JSON object must contain: image_type, summary, visible_text, markdown
- Use an empty string for visible_text when no clear text is present
- Use an empty string for markdown when the image is not a table
- Keep summary brief, at most 3 sentences
- All numbers, dates, and identifiers must match the image exactly
- If something is unclear, leave it empty instead of guessing
"""

_STRICT_BILINGUAL_SYSTEM_PROMPT = """\
You are a strict bilingual document-image interpreter / 你是一个严谨的双语文档图片解读助手。

Return exactly one JSON object with these keys:
- image_type
- summary
- visible_text
- markdown

Rules:
- Only use clearly visible image content / 只根据图片中可清晰确认的内容作答
- Do not infer hidden meaning or missing numbers / 不要推测缺失内容或补全数字
- visible_text should be literal transcription when text is readable / visible_text 必须尽量逐字转录
- markdown should be used only for real tables / markdown 仅用于表格
- summary must stay short and grounded / summary 必须简短且基于可见证据
- Return JSON only, with no code fences / 只能输出 JSON，不要包代码块
"""


_EVIDENCE_FIRST_POLICY = """\
Output policy — the three fields serve different purposes and can all be non-empty:
- visible_text: transcribe ALL readable text in the image, including text inside icons, labels, headings, and captions. Even short phrases matter. Use OCR/native reference text to keep wording and numbers aligned, but do not omit text that is clearly visible.
- markdown: if the image contains a table, reproduce it as a Markdown table. Include all rows and columns. Do not repeat the table content in visible_text.
- summary: add a brief description ONLY when the image carries visual meaning beyond its text/table content (e.g. chart trends, diagram relationships, photo subjects). Keep empty for pure text or table images.
- All numbers, dates, and identifiers must match the image exactly.
- Prefer omission over guesswork; do not "improve" wording or normalize identifiers.
"""


def _script_counts(text: str) -> tuple[int, int]:
    cjk = 0
    latin = 0
    for char in text:
        if "\u4e00" <= char <= "\u9fff":
            cjk += 1
        elif ("a" <= char.lower() <= "z"):
            latin += 1
    return cjk, latin


def _detect_prompt_language(*parts: str) -> str:
    combined = "\n".join(part for part in parts if part).strip()
    if not combined:
        return "bilingual"

    cjk, latin = _script_counts(combined)
    if cjk >= max(12, latin * 2):
        return "zh"
    if latin >= max(20, cjk * 2):
        return "en"
    return "bilingual"


def _build_vlm_system_prompt(
    style: str,
    *,
    preferred_language: str = "bilingual",
    retry: bool = False,
) -> str:
    if style == "strict_auto":
        style = {
            "zh": "strict_zh",
            "en": "strict_en",
            "bilingual": "strict_bilingual",
        }.get(preferred_language, "strict_bilingual")

    base = {
        "strict_zh": _STRICT_ZH_SYSTEM_PROMPT,
        "strict_en": _STRICT_EN_SYSTEM_PROMPT,
        "strict_bilingual": _STRICT_BILINGUAL_SYSTEM_PROMPT,
        "strict_auto": _STRICT_BILINGUAL_SYSTEM_PROMPT,
    }.get(style, _STRICT_BILINGUAL_SYSTEM_PROMPT)
    base = f"{base}\n{_EVIDENCE_FIRST_POLICY}"

    if not retry:
        return base
    return (
        f"{base}\n"
        "Previous output was invalid or too loose. "
        "Return valid JSON only, and keep every field grounded in visible evidence."
    )


def _build_vlm_prompt(
    elem: PageElement,
    context_before: str = "",
    *,
    evidence_text: str = "",
    route_hint: str = "",
    response_format: str = "json",
) -> str:
    parts = []
    if response_format == "json":
        parts.append(
            "Return one JSON object with keys: "
            + ", ".join(_VLM_RESPONSE_SCHEMA.keys())
            + "."
        )
    parts.append("Describe the image content conservatively / 请保守描述图片内容。")
    if context_before:
        parts.append(
            "\nContext before the image (reference only, not image evidence) / "
            "图片前文上下文（仅供参考，不是图片证据）：\n"
            f"{context_before[:500]}"
        )
    if evidence_text:
        parts.append(
            "\nOCR/native text overlapping the image region (reference to keep wording "
            "and numbers aligned when the same text is visible) / "
            "与图片区域重叠的 OCR/原生文本（仅在图片中确实可见时用于保持措辞和数字一致）：\n"
            f"{evidence_text[:800]}"
        )
    if route_hint:
        parts.append(
            "\nLikely image mode / 图片模式提示：\n"
            f"{route_hint}"
        )
    return "\n".join(parts)


def _extract_json_object(text: str) -> dict | None:
    stripped = text.strip()
    if stripped.startswith("```"):
        first_nl = stripped.find("\n")
        stripped = stripped[first_nl + 1:] if first_nl >= 0 else ""
        if stripped.endswith("```"):
            stripped = stripped[:-3]
        stripped = stripped.strip()

    try:
        parsed = json.loads(stripped)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass

    for candidate in _iter_json_object_candidates(stripped):
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue
    return None


def _iter_json_object_candidates(text: str):
    """Yield likely JSON object substrings without greedy brace capture."""
    nongreedy = re.search(r"\{.*?\}", text, re.DOTALL)
    if nongreedy:
        yield nongreedy.group(0)

    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escaped = False
        for idx in range(start, len(text)):
            char = text[idx]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue

            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    yield text[start: idx + 1]
                    break
        start = text.find("{", start + 1)


def _truncate_description(text: str, limit: int) -> str:
    compact = text.strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"


def _bbox_overlap_ratio(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    inter_x0 = max(ax0, bx0)
    inter_y0 = max(ay0, by0)
    inter_x1 = min(ax1, bx1)
    inter_y1 = min(ay1, by1)
    if inter_x1 <= inter_x0 or inter_y1 <= inter_y0:
        return 0.0
    inter_area = (inter_x1 - inter_x0) * (inter_y1 - inter_y0)
    a_area = max((ax1 - ax0) * (ay1 - ay0), 1.0)
    b_area = max((bx1 - bx0) * (by1 - by0), 1.0)
    return inter_area / min(a_area, b_area)


def _has_bbox(element: PageElement) -> bool:
    return element.bbox != (0.0, 0.0, 0.0, 0.0)


def _extract_numbers(text: str) -> list[str]:
    return re.findall(r"\d+(?:\.\d+)?", text)


def _normalized_len(text: str) -> int:
    return len(normalize_for_comparison(text))


def _looks_like_tabular(text: str) -> bool:
    """Heuristic: text has tabular structure even without markdown pipes.

    Detects patterns like repeated category/drug/instruction blocks
    common in OCR/VLM output from table images.
    """
    if not text or len(text) < 40:
        return False
    # Multiple lines with similar structure suggest tabular layout.
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if len(lines) >= 4:
        return True
    # Single long line with many short phrases separated by spaces
    # (flattened table rows).
    words = text.split()
    if len(words) >= 15 and text.count("禁忌") + text.count("不能用") >= 2:
        return True
    return False


def _is_strong_overlap(best_overlap: float, evidence_text: str) -> bool:
    return best_overlap >= 0.5 or (
        best_overlap >= 0.3 and _normalized_len(evidence_text) >= 24
    )


def _looks_text_heavy(
    *,
    image_class: str,
    visible_text: str,
    evidence_text: str,
) -> bool:
    if image_class == ImageClassification.TEXT_IMAGE:
        return True
    if _normalized_len(visible_text) >= 40:
        return True
    if _normalized_len(evidence_text) >= 40:
        return True
    if visible_text.count("\n") >= 2 and _normalized_len(visible_text) >= 20:
        return True
    return False


def _has_number_mismatch(candidate: str, evidence: str) -> bool:
    candidate_numbers = _extract_numbers(candidate)
    evidence_numbers = _extract_numbers(evidence)
    return bool(candidate_numbers) and bool(evidence_numbers) and candidate_numbers != evidence_numbers


def _collect_overlapping_evidence(
    image: PageElement,
    page_elements: list[PageElement],
) -> dict[str, object]:
    overlaps: list[tuple[float, PageElement]] = []

    if not _has_bbox(image):
        return {
            "text": "",
            "table_text": "",
            "best_overlap": 0.0,
            "has_table": False,
        }

    for elem in page_elements:
        if elem is image or elem.type not in {"text", "table"}:
            continue
        if elem.source not in {"ocr", "native"}:
            continue
        if not elem.content.strip() or not _has_bbox(elem):
            continue

        overlap = _bbox_overlap_ratio(image.bbox, elem.bbox)
        if overlap > 0:
            overlaps.append((overlap, elem))

    if not overlaps:
        return {
            "text": "",
            "table_text": "",
            "best_overlap": 0.0,
            "has_table": False,
        }

    overlaps.sort(key=lambda item: item[0], reverse=True)
    joined = "\n".join(elem.content.strip() for _, elem in overlaps)
    table_text = "\n\n".join(
        elem.content.strip()
        for _, elem in overlaps
        if elem.type == "table"
    )
    return {
        "text": joined,
        "table_text": table_text,
        "best_overlap": overlaps[0][0],
        "has_table": bool(table_text),
    }


def _select_vlm_description(
    *,
    elem: PageElement,
    summary: str,
    visible_text: str,
    markdown: str,
    evidence: dict[str, object],
    max_chars: int,
) -> tuple[str, dict[str, object]]:
    updates: dict[str, object] = {}
    evidence_text = str(evidence.get("text", "")).strip()
    evidence_table = str(evidence.get("table_text", "")).strip()
    best_overlap = float(evidence.get("best_overlap", 0.0) or 0.0)
    strong_overlap = _is_strong_overlap(best_overlap, evidence_text)

    image_class = str(elem.metadata.get("image_class", ""))
    text_heavy = _looks_text_heavy(
        image_class=image_class,
        visible_text=visible_text,
        evidence_text=evidence_text,
    )

    if summary:
        updates["vlm_summary"] = _truncate_description(summary, min(max_chars, 400))
    if visible_text:
        updates["vlm_visible_text"] = _truncate_description(visible_text, max_chars)
    if markdown:
        updates["vlm_markdown"] = _truncate_description(markdown, max_chars)
    if evidence_text:
        updates["vlm_overlap_evidence"] = _truncate_description(evidence_text, max_chars)
        updates["vlm_overlap_ratio"] = round(best_overlap, 4)
    if text_heavy:
        updates["text_heavy_image"] = True

    if markdown:
        if evidence_table and _has_number_mismatch(markdown, evidence_table):
            updates["description_source"] = "ocr_table_evidence"
            updates["vlm_number_mismatch"] = True
            return _truncate_description(evidence_table, max_chars), updates
        updates["description_source"] = "vlm_markdown"
        if summary:
            updates["vlm_summary_suppressed"] = True
        return _truncate_description(markdown, max_chars), updates

    if strong_overlap and visible_text:
        if _has_number_mismatch(visible_text, evidence_text):
            if evidence_text:
                updates["description_source"] = "ocr_overlap_evidence"
                updates["vlm_number_mismatch"] = True
                return _truncate_description(evidence_text, max_chars), updates
        updates["description_source"] = "vlm_visible_text"
        if summary:
            updates["vlm_summary_suppressed"] = True
        return _truncate_description(visible_text, max_chars), updates

    if strong_overlap and text_heavy and evidence_text:
        updates["description_source"] = "ocr_overlap_evidence"
        if summary:
            updates["vlm_summary_suppressed"] = True
        return _truncate_description(evidence_text, max_chars), updates

    if summary:
        if _has_number_mismatch(summary, evidence_text):
            updates["vlm_number_mismatch"] = True
            if visible_text:
                updates["description_source"] = "vlm_visible_text"
                updates["vlm_summary_suppressed"] = True
                return _truncate_description(visible_text, max_chars), updates
            if evidence_text:
                updates["description_source"] = "ocr_overlap_evidence"
                updates["vlm_summary_suppressed"] = True
                return _truncate_description(evidence_text, max_chars), updates
            updates["vlm_summary_suppressed"] = True
        elif visible_text and text_heavy:
            updates["description_source"] = "vlm_visible_text"
            updates["vlm_summary_suppressed"] = True
            return _truncate_description(visible_text, max_chars), updates
        else:
            updates["description_source"] = "vlm_summary"
            return _truncate_description(summary, max_chars), updates

    if visible_text:
        updates["description_source"] = "vlm_visible_text"
        return _truncate_description(visible_text, max_chars), updates

    if evidence_text and strong_overlap:
        updates["description_source"] = "ocr_overlap_evidence"
        return _truncate_description(evidence_text, max_chars), updates

    return "", updates


def _apply_vlm_supplement(
    *,
    image: PageElement,
    visible_text: str,
    page_elements: list[PageElement],
    max_chars: int,
) -> tuple[str, dict[str, object]]:
    """Supplement OCR with VLM-discovered text on full-page scan images.

    Unlike ``_apply_vlm_corrections``, this does NOT suppress any OCR
    elements.  It extracts lines from VLM's visible_text that OCR
    missed and stores them as ``vlm_corrected_text`` so they appear in
    the output alongside OCR content.

    The image itself is skipped (OCR already covers the page body).
    """
    updates: dict[str, object] = {"vlm_supplement_mode": True}

    if not visible_text:
        image.metadata["skipped"] = True
        image.metadata["skip_reason"] = "fullpage_scan_no_supplement"
        return "", updates

    # Collect all OCR text on this page for deduplication.
    ocr_texts: set[str] = set()
    for elem in page_elements:
        if elem is image:
            continue
        if elem.source != "ocr" or elem.type not in ("text", "table"):
            continue
        if elem.metadata.get("skip_render"):
            continue
        ocr_texts.add(normalize_for_comparison(elem.content.strip()))

    # Keep only VLM lines that OCR didn't extract.
    supplement_lines: list[str] = []
    for line in visible_text.splitlines():
        line = line.strip()
        if not line:
            continue
        line_norm = normalize_for_comparison(line)
        if not line_norm:
            continue
        # Check if any OCR element contains this line or vice versa.
        covered = any(
            line_norm in ocr_norm or ocr_norm in line_norm
            for ocr_norm in ocr_texts
        )
        if not covered:
            supplement_lines.append(line)

    # Skip the image — OCR content is the primary source.
    image.metadata["skipped"] = True
    image.metadata["skip_reason"] = "fullpage_scan_ocr_primary"

    if supplement_lines:
        supplement_text = "\n".join(supplement_lines)
        image.metadata["vlm_corrected_text"] = _truncate_description(
            supplement_text, max_chars,
        )
        image.metadata["skipped"] = False  # Need to render supplement
        updates["vlm_supplement_lines"] = len(supplement_lines)
        updates["vlm_route_text"] = True
        log.info(
            "VLM supplement: %d lines OCR missed on fullpage scan",
            len(supplement_lines),
        )
    else:
        updates["vlm_supplement_lines"] = 0

    return "", updates


def _apply_vlm_corrections(
    *,
    image: PageElement,
    visible_text: str,
    markdown: str,
    summary: str,
    evidence: dict[str, object],
    page_elements: list[PageElement],
    max_chars: int,
) -> tuple[str, dict[str, object]]:
    """Use VLM output as the authoritative version of the image region.

    VLM receives both the original image *and* the OCR evidence as
    reference, so it has strictly more information than OCR alone.
    When VLM returns text or table content that covers the same page
    region as existing OCR elements, the VLM output is preferred:

    1. Overlapping OCR elements are suppressed (``skip_render``).
    2. The VLM content is stored in ``vlm_corrected_content`` on the
       image element so the renderer can emit it as body text instead
       of an image reference.
    3. If VLM also returns a *summary* that carries independent semantic
       information (chart interpretation, diagram meaning), it is kept as
       the image description.

    Safety guards are minimal — we only reject VLM output when it is
    empty, truncated, or structurally invalid.  We do *not* use OCR to
    second-guess VLM content (numbers, wording), because VLM already
    had the OCR text as reference when it produced its answer.

    Returns ``(remaining_description, metadata_updates)``.
    """
    updates: dict[str, object] = {}
    evidence_text = str(evidence.get("text", "")).strip()
    best_overlap = float(evidence.get("best_overlap", 0.0) or 0.0)
    strong_overlap = _is_strong_overlap(best_overlap, evidence_text)

    # ── Detect full-page scan images ────────────────────────────────
    #
    # Full-page scan images cover the entire page.  OCR has already
    # extracted body text from the page; VLM supplements OCR by
    # finding text that OCR missed (e.g. colored logos, small edge
    # text, vertical labels).  In this mode VLM should NOT suppress
    # existing OCR elements — it should only add what's missing.
    is_fullpage_scan = False
    if _has_bbox(image):
        ocr_count = sum(
            1 for e in page_elements
            if e is not image and e.source == "ocr"
            and e.type in ("text", "table")
            and not e.metadata.get("skip_render")
        )
        if ocr_count >= 3:
            img_w = image.bbox[2] - image.bbox[0]
            img_h = image.bbox[3] - image.bbox[1]
            page_area = max(img_w * img_h, 1.0)
            # If 3+ OCR elements exist and all overlap the image,
            # this is a full-page scan, not a local illustration.
            overlapping = sum(
                1 for e in page_elements
                if e is not image and e.source == "ocr"
                and e.type in ("text", "table")
                and _has_bbox(e) and _bbox_overlap_ratio(image.bbox, e.bbox) > 0.3
            )
            if overlapping >= 3:
                is_fullpage_scan = True

    if is_fullpage_scan:
        return _apply_vlm_supplement(
            image=image,
            visible_text=visible_text,
            page_elements=page_elements,
            max_chars=max_chars,
        )

    # ── Store each VLM field separately ─────────────────────────────
    #
    # VLM returns three independent outputs that can freely combine:
    #   visible_text → corrected text  (replaces OCR text)
    #   markdown     → corrected table (replaces OCR table)
    #   summary      → image description (kept if it adds info)
    #
    # Each is stored in its own metadata key so the renderer can
    # format them appropriately (table as markdown, text as prose,
    # description attached to the image).

    image_type = str(image.metadata.get("vlm_image_type", ""))
    corrected_text = ""
    corrected_table = ""

    # Table: from markdown field, or from visible_text when VLM
    # identified the image as a table but used the wrong field.
    if markdown:
        corrected_table = _truncate_description(markdown, max_chars)
    elif visible_text and image_type == "table":
        corrected_table = _truncate_description(visible_text, max_chars)
    elif visible_text and _looks_like_tabular(visible_text):
        corrected_table = _truncate_description(visible_text, max_chars)

    # Text: from visible_text.  When markdown already provided a table,
    # visible_text may still carry non-table text (titles, labels,
    # metadata) that should be preserved as corrected_text.
    if visible_text:
        # If visible_text was already consumed as table, skip.
        if corrected_table and corrected_table == _truncate_description(visible_text, max_chars):
            pass
        elif image_type == "text" or strong_overlap or _normalized_len(visible_text) >= 40:
            corrected_text = _truncate_description(visible_text, max_chars)

    # Need at least one non-empty correction to proceed.
    all_corrections = f"{corrected_text} {corrected_table}".strip()
    if _normalized_len(all_corrections) < 10:
        return "", {}  # VLM didn't return useful content — fallback.

    # ── Suppress OCR elements covered by VLM output ──────────────────
    suppressed = _suppress_ocr_covered_by_vlm(
        all_corrections, image, page_elements,
    )
    if suppressed:
        updates["ocr_elements_suppressed"] = suppressed
    elif strong_overlap and _normalized_len(evidence_text) >= 40:
        # No OCR elements, but native text covers the region.
        image.metadata["skipped"] = True
        image.metadata["skip_reason"] = "content_covered_by_native_text"
        updates["native_text_overlap_skip"] = True
        return "", updates

    # Store fields separately for the renderer.
    if corrected_text:
        image.metadata["vlm_corrected_text"] = corrected_text
        updates["vlm_route_text"] = True
    if corrected_table:
        image.metadata["vlm_corrected_table"] = corrected_table
        updates["vlm_route_table"] = True

    # ── Check whether summary adds independent info ──────────────────
    remaining_desc = ""
    if summary:
        from parserx.verification.product_quality import _char_overlap_ratio

        overlap = _char_overlap_ratio(
            normalize_for_comparison(summary),
            normalize_for_comparison(all_corrections),
        )
        if overlap <= 0.6:
            remaining_desc = _truncate_description(summary, min(max_chars, 400))
            updates["vlm_summary_independent"] = True
        else:
            updates["vlm_summary_suppressed"] = True

    return remaining_desc, updates


def _suppress_ocr_covered_by_vlm(
    vlm_content: str,
    image: PageElement,
    page_elements: list[PageElement],
) -> int:
    """Suppress OCR elements that VLM output supersedes.

    Two strategies:
    1. **Bbox overlap**: OCR elements whose bbox overlaps the image are
       the direct targets of VLM correction (VLM saw and refined them).
       These are always suppressed.
    2. **Text containment**: OCR elements outside the image bbox whose
       text appears verbatim in the VLM output are duplicates.

    Native-source elements are never suppressed.
    """
    count = 0
    has_bbox = _has_bbox(image)
    vlm_norm = normalize_for_comparison(vlm_content) if vlm_content else ""

    for elem in page_elements:
        if elem is image:
            continue
        if elem.type not in {"text", "table"}:
            continue
        if elem.source != "ocr":
            continue
        if elem.metadata.get("skip_render") or elem.metadata.get("skipped"):
            continue

        suppress = False

        # Strategy 1: bbox overlap — VLM is correcting this element.
        if has_bbox and _has_bbox(elem):
            if _bbox_overlap_ratio(image.bbox, elem.bbox) > 0.3:
                suppress = True

        # Strategy 2: text containment — OCR text already in VLM output.
        if not suppress and vlm_norm:
            elem_text = elem.content.strip()
            if elem_text and len(elem_text) >= 2:
                elem_norm = normalize_for_comparison(elem_text)
                if elem_norm and elem_norm in vlm_norm:
                    suppress = True

        if suppress:
            elem.metadata["skip_render"] = True
            elem.metadata["suppressed_by_vlm_correction"] = True
            count += 1
    return count


def _skip_vlm_for_large_text_overlap(
    *,
    elem: PageElement,
    evidence: dict[str, object],
    max_chars: int,
    overlap_char_threshold: int,
) -> tuple[str, dict[str, object]] | None:
    if overlap_char_threshold <= 0:
        return None

    evidence_text = str(evidence.get("text", "")).strip()
    evidence_table = str(evidence.get("table_text", "")).strip()
    if not evidence_text or evidence_table:
        return None

    best_overlap = float(evidence.get("best_overlap", 0.0) or 0.0)
    image_class = str(elem.metadata.get("image_class", ""))
    text_heavy = _looks_text_heavy(
        image_class=image_class,
        visible_text="",
        evidence_text=evidence_text,
    )
    if not text_heavy or not _is_strong_overlap(best_overlap, evidence_text):
        return None
    if _normalized_len(evidence_text) < overlap_char_threshold:
        return None

    updates: dict[str, object] = {
        "description_source": "ocr_overlap_evidence",
        "text_heavy_image": True,
        "vlm_skipped_due_to_large_text_overlap": True,
        "vlm_overlap_ratio": round(best_overlap, 4),
        "vlm_overlap_evidence": _truncate_description(evidence_text, max_chars),
    }
    return _truncate_description(evidence_text, max_chars), updates


def _build_route_hint(
    *,
    elem: PageElement,
    evidence: dict[str, object],
    visible_text_hint: str = "",
) -> str:
    evidence_text = str(evidence.get("text", "")).strip()
    evidence_table = str(evidence.get("table_text", "")).strip()
    image_class = str(elem.metadata.get("image_class", ""))

    if evidence_table or image_class == ImageClassification.TABLE_IMAGE:
        return (
            "table-like: reproduce the full table in markdown. Put any non-table text "
            "(titles, footnotes, labels) in visible_text. Keep summary empty unless "
            "the table has a visual meaning that text alone cannot convey."
        )

    if _looks_text_heavy(
        image_class=image_class,
        visible_text=visible_text_hint,
        evidence_text=evidence_text,
    ):
        return (
            "text-heavy: transcribe ALL visible text in visible_text, including short "
            "labels, icon text, and headings. Keep summary empty unless a brief "
            "structural note is needed."
        )

    return (
        "diagram/photo-like: put any readable text (labels, captions, numbers) in "
        "visible_text. Use summary for a brief description of the visual content."
    )


def _normalize_vlm_output(
    raw: str,
    elem: PageElement,
    *,
    page_elements: list[PageElement],
    response_format: str,
    max_chars: int,
    debug_raw_preview_chars: int,
    correction_mode: bool = True,
) -> tuple[str, bool, dict[str, object]]:
    if response_format != "json":
        return _truncate_description(raw, max_chars), True, {}

    payload = _extract_json_object(raw)
    if payload is None:
        updates = {
            "vlm_unstructured_output": True,
            "vlm_raw_excerpt": _truncate_description(raw, debug_raw_preview_chars),
        }
        evidence = _collect_overlapping_evidence(elem, page_elements)
        evidence_text = str(evidence.get("text", "")).strip()
        best_overlap = float(evidence.get("best_overlap", 0.0) or 0.0)
        if evidence_text and _is_strong_overlap(best_overlap, evidence_text):
            updates["vlm_unstructured_reason"] = "truncated_json" if _looks_like_truncated_json(raw) else "invalid_json"
            updates["description_source"] = "ocr_overlap_evidence"
            updates["vlm_overlap_ratio"] = round(best_overlap, 4)
            updates["vlm_overlap_evidence"] = _truncate_description(evidence_text, max_chars)
            return _truncate_description(evidence_text, max_chars), True, updates
        return "", False, updates

    summary = str(payload.get("summary", "")).strip()
    visible_text = str(payload.get("visible_text", "")).strip()
    markdown = str(payload.get("markdown", "")).strip()
    image_type = str(payload.get("image_type", "")).strip()
    updates: dict[str, object] = {"vlm_raw": payload}
    if image_type:
        updates["vlm_image_type"] = image_type

    evidence = _collect_overlapping_evidence(elem, page_elements)

    # ── VLM correction path: use VLM output to fix OCR, not as a
    #    parallel description.
    if not correction_mode:
        correction_updates: dict[str, object] = {}
        remaining_desc = ""
    else:
        remaining_desc, correction_updates = _apply_vlm_corrections(
            image=elem,
            visible_text=visible_text,
            markdown=markdown,
            summary=summary,
            evidence=evidence,
            page_elements=page_elements,
            max_chars=max_chars,
        )
    if correction_updates:
        updates.update(correction_updates)
        updates["vlm_route"] = "correction"
        if remaining_desc:
            updates["description_source"] = "vlm_summary"
            return remaining_desc, True, updates
        return "", True, updates

    # ── Fallback: original description-selection path ──────────────────
    description, routed_updates = _select_vlm_description(
        elem=elem,
        summary=summary,
        visible_text=visible_text,
        markdown=markdown,
        evidence=evidence,
        max_chars=max_chars,
    )
    updates.update(routed_updates)

    if not description and evidence.get("text") and visible_text:
        evidence_text = str(evidence["text"]).strip()
        if compute_edit_distance(visible_text, evidence_text) <= 0.2:
            updates["description_source"] = "ocr_overlap_evidence"
            return _truncate_description(evidence_text, max_chars), True, updates

    return description, True, updates


def _looks_like_truncated_json(raw: str) -> bool:
    stripped = raw.strip()
    return stripped.startswith("{") and not stripped.endswith("}")


def _get_context_before(elem: PageElement, page_elements: list[PageElement]) -> str:
    context_lines = []
    for e in page_elements:
        if e is elem:
            break
        if e.type == "text" and e.content.strip():
            context_lines.append(e.content.strip())
    return "\n".join(context_lines[-3:])


class ImageProcessor:
    """Classify images and optionally generate VLM descriptions.

    VLM calls are collected and executed concurrently for speed.
    Concurrency level is controlled by config (services.vlm.max_concurrent).
    """

    def __init__(
        self,
        config: ImageProcessorConfig | None = None,
        vlm_service: OpenAICompatibleService | None = None,
        max_concurrent: int = 6,
        table_config: TableProcessorConfig | None = None,
    ):
        self._config = config or ImageProcessorConfig()
        self._vlm = vlm_service
        self._max_concurrent = max_concurrent
        self._table_config = table_config or TableProcessorConfig()

    def process(self, doc: Document) -> Document:
        if not self._config.enabled:
            return doc

        # ── Identify pages already covered by cross-page merged tables ──
        table_covered_pages = self._find_table_covered_pages(doc)
        if table_covered_pages:
            log.info(
                "Pages covered by merged tables (VLM skipped): %s",
                sorted(table_covered_pages),
            )

        # ── Optional: refine merged tables via multi-image VLM ──────────
        if (
            self._table_config.vlm_refine_merged_tables
            and self._vlm
            and table_covered_pages
        ):
            self._refine_merged_tables(doc)

        stats = {"decorative": 0, "informational": 0, "table": 0, "text": 0, "blank": 0, "vlm_called": 0}
        vlm_tasks: list[tuple[PageElement, list[PageElement], Path]] = []

        for page in doc.pages:
            for elem in page.elements:
                if elem.type != "image":
                    continue

                # Already marked by an earlier stage (e.g. fullpage scan
                # images marked by OCRBuilder) — skip all further work.
                if elem.metadata.get("skipped"):
                    continue

                # Step 1: Classify
                classification = classify_image_element(elem)
                elem.metadata["image_class"] = classification

                if classification == ImageClassification.DECORATIVE:
                    stats["decorative"] += 1
                    if self._config.skip_decorative:
                        elem.metadata["skipped"] = True
                        elem.metadata["description"] = ""
                elif classification == ImageClassification.BLANK:
                    stats["blank"] += 1
                    elem.metadata["skipped"] = True
                    elem.metadata["description"] = ""
                elif classification == ImageClassification.TABLE_IMAGE:
                    stats["table"] += 1
                    elem.metadata["needs_vlm"] = True
                elif classification == ImageClassification.TEXT_IMAGE:
                    stats["text"] += 1
                elif classification == ImageClassification.INFORMATIONAL:
                    stats["informational"] += 1
                    elem.metadata["needs_vlm"] = True

                # Step 2: Skip images on pages already covered by merged tables
                if elem.page_number in table_covered_pages:
                    elem.metadata["skipped"] = True
                    elem.metadata["skip_reason"] = "content_covered_by_merged_table"
                    elem.metadata["description"] = ""
                    continue

                # Step 3: Collect VLM tasks
                if (self._vlm
                        and self._config.vlm_description
                        and elem.metadata.get("needs_vlm")
                        and elem.metadata.get("saved_abs_path")):
                    saved_path = Path(elem.metadata["saved_abs_path"])
                    if saved_path.exists():
                        file_class = classify_image_file(saved_path)
                        if file_class == ImageClassification.BLANK:
                            elem.metadata["image_class"] = ImageClassification.BLANK
                            elem.metadata["skipped"] = True
                            elem.metadata["description"] = ""
                            stats["blank"] += 1
                        else:
                            vlm_tasks.append((elem, page.elements, saved_path))

        # Step 3: Execute VLM calls concurrently
        if vlm_tasks:
            log.info("Running %d VLM calls (max %d concurrent)", len(vlm_tasks), self._max_concurrent)
            stats["vlm_called"] = self._run_vlm_concurrent(vlm_tasks)

        log.info(
            "Images: %d informational, %d decorative, %d table, %d text, %d blank, %d VLM calls",
            stats["informational"], stats["decorative"],
            stats["table"], stats["text"], stats["blank"],
            stats["vlm_called"],
        )
        return doc

    def _run_vlm_concurrent(
        self, tasks: list[tuple[PageElement, list[PageElement], Path]]
    ) -> int:
        """Run VLM descriptions concurrently. Returns actual API call count."""
        api_call_count = 0

        def _describe(task: tuple[PageElement, list[PageElement], Path]) -> tuple[PageElement, str, int]:
            elem, page_elements, image_path = task
            context = _get_context_before(elem, page_elements)
            evidence = _collect_overlapping_evidence(elem, page_elements)
            if not self._config.vlm_refine_all_ocr:
                skipped = _skip_vlm_for_large_text_overlap(
                    elem=elem,
                    evidence=evidence,
                    max_chars=self._config.vlm_max_description_chars,
                    overlap_char_threshold=self._config.vlm_skip_large_text_overlap_chars,
                )
                if skipped is not None:
                    description, metadata_updates = skipped
                    elem.metadata.update(metadata_updates)
                    return elem, description, 0

            attempts = max(self._config.vlm_retry_attempts, 0) + 1
            preferred_language = _detect_prompt_language(
                context,
                str(evidence.get("text", "")),
            )
            route_hint = _build_route_hint(elem=elem, evidence=evidence)
            calls_made = 0

            for attempt in range(attempts):
                prompt = _build_vlm_prompt(
                    elem,
                    context,
                    evidence_text=str(evidence.get("text", "")),
                    route_hint=route_hint,
                    response_format=self._config.vlm_response_format,
                )
                system_prompt = _build_vlm_system_prompt(
                    self._config.vlm_prompt_style,
                    preferred_language=preferred_language,
                    retry=attempt > 0,
                )
                try:
                    calls_made += 1
                    result = self._vlm.describe_image(
                        image_path,
                        prompt,
                        context=system_prompt,
                        temperature=0.0,
                        max_tokens=self._config.vlm_max_tokens,
                        structured_output_mode=self._config.vlm_structured_output_mode,
                        json_schema=_VLM_JSON_SCHEMA if self._config.vlm_response_format == "json" else None,
                        json_schema_name=_VLM_JSON_SCHEMA_NAME,
                    )
                except Exception as exc:
                    log.warning("VLM failed for %s (attempt %d/%d): %s", image_path.name, attempt + 1, attempts, exc)
                    continue

                normalized, ok, metadata_updates = _normalize_vlm_output(
                    result.strip(),
                    elem,
                    page_elements=page_elements,
                    response_format=self._config.vlm_response_format,
                    max_chars=self._config.vlm_max_description_chars,
                    debug_raw_preview_chars=self._config.vlm_debug_raw_preview_chars,
                    correction_mode=self._config.vlm_correction_mode,
                )
                if metadata_updates:
                    elem.metadata.update(metadata_updates)
                if normalized:
                    if attempt > 0:
                        elem.metadata["vlm_retry_used"] = True
                    return elem, normalized, calls_made
                if not ok:
                    excerpt = elem.metadata.get("vlm_raw_excerpt", "")
                    if excerpt:
                        log.warning(
                            "VLM returned unstructured output for %s (attempt %d/%d): %s",
                            image_path.name,
                            attempt + 1,
                            attempts,
                            excerpt,
                        )
                    else:
                        log.warning("VLM returned unstructured output for %s (attempt %d/%d)", image_path.name, attempt + 1, attempts)

            return elem, "", calls_made

        with ThreadPoolExecutor(max_workers=self._max_concurrent) as executor:
            futures = {executor.submit(_describe, task): task for task in tasks}
            for future in as_completed(futures):
                elem, description, calls_made = future.result()
                if description:
                    elem.metadata["description"] = description
                api_call_count += calls_made

        return api_call_count

    # ── Cross-page merged table support ─────────────────────────────────

    @staticmethod
    def _find_table_covered_pages(doc: Document) -> set[int]:
        """Return page numbers whose content is already in a merged table.

        When TableProcessor merges a table spanning pages [3, 4, 5, 6],
        the merged table element already contains the complete content
        from all pages.  Images on ALL of these pages (including page 3)
        are redundant — they show the same table content that the merged
        element already captured via OCR.
        """
        covered: set[int] = set()
        for elem in doc.elements_by_type("table"):
            merged_pages = elem.metadata.get("merged_from_pages")
            if merged_pages and len(merged_pages) > 1:
                covered.update(merged_pages)
        return covered

    def _refine_merged_tables(self, doc: Document) -> None:
        """Use multi-image VLM to refine cross-page merged tables.

        For each merged table, sends all constituent page images to VLM
        in a single call alongside the OCR-merged markdown.  VLM output
        replaces the merged table content.
        """
        assert self._vlm is not None

        page_map: dict[int, Page] = {p.number: p for p in doc.pages}

        for elem in doc.elements_by_type("table"):
            merged_pages = elem.metadata.get("merged_from_pages")
            if not merged_pages or len(merged_pages) < 2:
                continue

            # Collect page images for all constituent pages.
            image_paths: list[Path] = []
            for pn in merged_pages:
                page = page_map.get(pn)
                if page and page.image_path and page.image_path.exists():
                    image_paths.append(page.image_path)

            if len(image_paths) < 2:
                log.info(
                    "Skipping VLM refinement for merged table (pages %s): "
                    "not enough page images (%d)",
                    merged_pages, len(image_paths),
                )
                continue

            prompt = (
                "These images show consecutive pages of a SINGLE table in a "
                "scanned document. The OCR-merged result is below. Please "
                "correct any OCR errors, fix cell alignment issues, and "
                "return the complete table in clean Markdown format.\n\n"
                "OCR-merged table:\n"
                f"```\n{elem.content}\n```\n\n"
                "Return ONLY the corrected Markdown table, nothing else."
            )

            try:
                log.info(
                    "Refining merged table (pages %s) with %d-image VLM call",
                    merged_pages, len(image_paths),
                )
                result = self._vlm.describe_images(
                    image_paths,
                    prompt,
                    temperature=0.1,
                    max_tokens=self._config.vlm_max_tokens,
                )
                result = result.strip()

                # Basic validation: result should contain table markup.
                if "|" in result and "---" in result:
                    elem.content = result
                    elem.metadata["vlm_refined"] = True
                    log.info(
                        "Merged table (pages %s) refined by VLM (%d chars)",
                        merged_pages, len(result),
                    )
                else:
                    log.warning(
                        "VLM refinement for merged table (pages %s) did not "
                        "return valid table markup; keeping OCR version",
                        merged_pages,
                    )
            except Exception:
                log.exception(
                    "VLM refinement failed for merged table (pages %s); "
                    "keeping OCR version",
                    merged_pages,
                )
