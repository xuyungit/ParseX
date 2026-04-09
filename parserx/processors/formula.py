r"""Formula normalization processor.

Converts Unicode mathematical characters and scientific notation to LaTeX:
- Temperature: 30℃ → $30^{\circ}\mathrm{C}$
- Chemical formulas: H₂SiCl₂ → $\mathrm{H_{2}SiCl_{2}}$
- Micro-units: 200μL → $200\,\mathrm{\mu L}$
- Math symbols: ≥ → $\ge$, ≤ → $\le$
- LaTeX fragment cleanup: $ {}^{13} $C → $^{13}C$
"""

from __future__ import annotations

import json
import logging
import re
import tempfile
from pathlib import Path
from typing import Callable

import fitz  # PyMuPDF

from parserx.config.schema import FormulaProcessorConfig
from parserx.models.elements import Document, PageElement
from parserx.services.llm import OpenAICompatibleService
from parserx.text_utils import compute_edit_distance, normalize_for_comparison

log = logging.getLogger(__name__)

# ── Unicode subscript/superscript maps ────────────────────────────────

_SUBSCRIPT_MAP = str.maketrans(
    "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₒₓ",
    "0123456789+-=()aeox",
)

_SUPERSCRIPT_MAP = str.maketrans(
    "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿⁱ",
    "0123456789+-=()ni",
)

_SUBSCRIPT_CHARS = set("₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₒₓ")
_SUPERSCRIPT_CHARS = set("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿⁱ")

# ── Chemical element symbols ──────────────────────────────────────────

# Common element symbols (1-2 chars) that appear in chemical formulas.
_ELEMENTS = {
    "H", "He", "Li", "Be", "B", "C", "N", "O", "F", "Ne",
    "Na", "Mg", "Al", "Si", "P", "S", "Cl", "Ar",
    "K", "Ca", "Ti", "V", "Cr", "Mn", "Fe", "Co", "Ni", "Cu", "Zn",
    "Ga", "Ge", "As", "Se", "Br", "Kr",
    "Rb", "Sr", "Zr", "Nb", "Mo", "Ru", "Rh", "Pd", "Ag", "Cd",
    "In", "Sn", "Sb", "Te", "I", "Xe",
    "Cs", "Ba", "La", "Ce", "Pr", "Nd", "Sm", "Eu", "Gd", "Tb",
    "Dy", "Ho", "Er", "Tm", "Yb", "Lu",
    "Hf", "Ta", "W", "Re", "Os", "Ir", "Pt", "Au", "Hg",
    "Tl", "Pb", "Bi", "Po", "At", "Rn",
}

# ── Regex patterns ────────────────────────────────────────────────────

# Temperature: digits + ℃ (U+2103) or °C
# No \b after ℃ — it's a Unicode symbol, not a word character.
_TEMPERATURE_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:℃|°C)(?![A-Za-z])"
)

# Chemical formula with Unicode subscripts: e.g. H₂SiCl₂, CO₂, CDCl₃
# Pattern: (element_symbol + optional_subscript_digits)+ where at least one
# subscript is present.
_UNICODE_SUB = "[₀₁₂₃₄₅₆₇₈₉]"
_UNICODE_SUP = "[⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻]"
_ELEMENT = r"[A-Z][a-z]?"

_CHEM_FORMULA_RE = re.compile(
    rf"(?<![A-Za-z])"  # not preceded by letter (avoid mid-word)
    rf"("
    rf"(?:{_ELEMENT}(?:{_UNICODE_SUB}+)?)"  # first element + optional sub
    rf"(?:{_ELEMENT}(?:{_UNICODE_SUB}+)?)*"  # more elements
    rf"(?:{_UNICODE_SUP}+)?"
    rf")"
    rf"(?![A-Za-z₀-₉⁰-⁹⁺⁻])"  # not followed by letter, subscript, or superscript
)

# Micro-unit: digits + μ + unit letter (L, g, m, M, mol)
_MICRO_UNIT_RE = re.compile(
    r"(\d+)\s*μ([LgmM](?:ol)?)\b"
)

# Standalone micro-unit without leading digits (e.g. "μg/mL")
_MICRO_UNIT_BARE_RE = re.compile(
    r"(?<![A-Za-z])μ([LgmM](?:ol)?(?:/m[Ll])?)\b"
)

# Math symbols to convert (only when not inside $...$)
_MATH_SYMBOLS = {
    "≥": r"\ge",
    "≤": r"\le",
    "≫": r"\gg",
    "≪": r"\ll",
    "±": r"\pm",
    "∓": r"\mp",
    "×": r"\times",
    "÷": r"\div",
    "≠": r"\ne",
    "≈": r"\approx",
    "∞": r"\infty",
    "∝": r"\propto",
}

# LaTeX fragment cleanup: $ {}^{N} $X → $^{N}X$
# Matches: $ {optional_space}^{digits} $ followed by element/letter
_LATEX_FRAGMENT_RE = re.compile(
    r"\$\s*\{\}?\s*(\^{[^}]+})\s*\$\s*([A-Z][a-z]?\b)"
)

# Matches: text $ _{N} $ pattern → consolidate (e.g. CDCl$ _3 $)
_LATEX_SUBSCRIPT_FRAGMENT_RE = re.compile(
    r"((?:[A-Z][a-z]?)+)\s*\$\s*_\s*({[^}]+}|\d+)\s*\$"
)

_ADJACENT_INLINE_MATH_RE = re.compile(
    r"\$([^$\n]+)\$\s*\$([^$\n]+)\$"
)
_LATEX_COMMAND_RE = re.compile(r"\\[A-Za-z]+")
_FORMULA_FRAGMENT_RE = re.compile(r"\$\s*[_^]|\$_\{|\^\{")
_MIXED_FORMULA_TOKEN_RE = re.compile(
    r"\b(?:[A-Za-z]{1,3}\d+[A-Za-z0-9+\-]*){1,}\b"
)
_FORMULA_OPERATOR_RE = re.compile(r"[\[\]{}^_=~<>+\-*/]")
_REFUSAL_RE = re.compile(
    r"\b(?:sorry|cannot|can't|unable|unclear|illegible|not visible)\b",
    re.IGNORECASE,
)
_INLINE_MATH_CANDIDATE_RE = re.compile(r"\$[^$\n]{1,120}\$")
_LATEX_FRAGMENT_CANDIDATE_RE = re.compile(
    r"(?:\\[A-Za-z]+(?:\{[^{}]{0,40}\})?|\^\{[^{}]{1,20}\}|_\{[^{}]{1,20}\})"
)
_ASCII_CHARGED_FORMULA_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Z][a-z]?\s*\d*)+(?:\s*\d+\s*)?[+-](?![A-Za-z0-9])"
)
_MIXED_FORMULA_WITH_OPERATORS_RE = re.compile(
    r"\b[A-Za-z0-9]{1,20}(?:[_^=+\-/][A-Za-z0-9{}()+\-/]{1,30})+\b"
)
_SUBSCRIPT_FRAGMENT_CANDIDATE_RE = re.compile(
    r"(?:[A-Za-z]{1,4}\s*\$\s*_\s*(?:\{[^{}]{1,20}\}|\d{1,4})\s*\$)+"
)

_FORMULA_CORRECTION_JSON_SCHEMA_NAME = "parserx_formula_correction"
_FORMULA_CANDIDATE_CORRECTION_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["confidence", "change_kind", "replacement_text"],
    "properties": {
        "confidence": {
            "type": "string",
            "enum": ["low", "medium", "high"],
        },
        "change_kind": {
            "type": "string",
            "enum": ["none", "ocr_fix", "format_only"],
        },
        "replacement_text": {"type": "string"},
    },
}

_FORMULA_CORRECTION_SYSTEM_PROMPT = """\
You are a conservative OCR correction assistant for scientific and technical document snippets.

You will see:
- a tight image crop around one candidate formula/text span
- the OCR transcript for the whole region
- one exact target candidate copied from the OCR transcript
- an optional rule-based normalization preview

Rules:
- Correct only the target candidate, not the whole passage
- Preserve surrounding prose, punctuation, numbers, units, and identifiers
- Prefer standard scientific notation when the crop supports it
- Preserve formula structure such as subscripts, superscripts, charges, brackets, Greek letters, operators, and LaTeX markers
- It is allowed to repair broken formula notation or fragmented LaTeX when the crop supports it
- Never flatten structured notation into plain text (for example, do not turn C_{23}H_{20}O_{2} into C23H20O2)
- If the crop is unclear, return no correction
- The correction must be minimal and evidence-backed
- Classify the result as one of:
  - none: no justified change
  - ocr_fix: visible OCR/formula repair
  - format_only: harmless formatting-only normalization
- Return the corrected form of the target candidate only, or an empty string if no change is justified
- Return JSON only
"""

CropRenderer = Callable[[Path | None, int, tuple[float, float, float, float]], Path | None]


def _has_formula_indicators(text: str) -> bool:
    """Quick check if text contains any characters worth processing."""
    for ch in text:
        if ch in _SUBSCRIPT_CHARS or ch in _SUPERSCRIPT_CHARS:
            return True
        if ch in ("℃", "°", "μ", "≥", "≤", "≫", "≪", "±", "×", "÷", "≠", "≈"):
            return True
    # Also check for fragmented LaTeX patterns
    if "$ {}" in text or "$ _" in text:
        return True
    return False


def _convert_subscripts(s: str) -> str:
    """Convert Unicode subscript chars to LaTeX _{...} notation."""
    result = []
    i = 0
    while i < len(s):
        if s[i] in _SUBSCRIPT_CHARS:
            sub_chars = []
            while i < len(s) and s[i] in _SUBSCRIPT_CHARS:
                sub_chars.append(s[i].translate(_SUBSCRIPT_MAP))
                i += 1
            result.append("_{" + "".join(sub_chars) + "}")
        else:
            result.append(s[i])
            i += 1
    return "".join(result)


def _convert_superscripts(s: str) -> str:
    """Convert Unicode superscript chars to LaTeX ^{...} notation."""
    result = []
    i = 0
    while i < len(s):
        if s[i] in _SUPERSCRIPT_CHARS:
            sup_chars = []
            while i < len(s) and s[i] in _SUPERSCRIPT_CHARS:
                sup_chars.append(s[i].translate(_SUPERSCRIPT_MAP))
                i += 1
            result.append("^{" + "".join(sup_chars) + "}")
        else:
            result.append(s[i])
            i += 1
    return "".join(result)


def _is_chemical_formula(text: str) -> bool:
    """Check if text contains element symbols with Unicode subscripts."""
    has_sub = any(c in _SUBSCRIPT_CHARS for c in text)
    if not has_sub:
        return False
    # Must contain at least one recognized element symbol
    cleaned = re.sub(
        rf"[{''.join(_SUBSCRIPT_CHARS)}{''.join(_SUPERSCRIPT_CHARS)}]",
        "",
        text,
    )
    # Split into potential element tokens
    tokens = re.findall(r"[A-Z][a-z]?", cleaned)
    return any(t in _ELEMENTS for t in tokens)


def _convert_chemical_formula(match: re.Match) -> str:
    """Convert a chemical formula match to LaTeX."""
    formula = match.group(1)
    if not _is_chemical_formula(formula):
        return match.group(0)  # not a real chemical formula, leave as-is
    converted = _convert_subscripts(formula)
    converted = _convert_superscripts(converted)
    return rf"$\mathrm{{{converted}}}$"


def _in_math_mode(text: str, pos: int) -> bool:
    """Check if position is inside inline or display math delimiters."""
    in_inline_math = False
    in_display_math = False
    i = 0
    while i < pos:
        if text[i] == "\\" and i + 1 < pos:
            i += 2
            continue
        if text[i] == "$":
            if i + 1 < len(text) and text[i + 1] == "$":
                in_display_math = not in_display_math
                i += 2
                continue
            if not in_display_math:
                in_inline_math = not in_inline_math
        i += 1
    return in_inline_math or in_display_math


def _is_simple_chemical_latex_fragment(text: str) -> bool:
    """Return True for simple inline chemistry fragments like C_{23} or H."""
    return bool(re.fullmatch(r"[A-Za-z](?:[A-Za-z]|_\{[^}]+\})*", text))


def _merge_adjacent_chemical_fragments(text: str) -> str:
    """Merge adjacent inline chemistry fragments into one formula."""
    while True:
        changed = False

        def repl(match: re.Match) -> str:
            nonlocal changed
            left = match.group(1).strip()
            right = match.group(2).strip()
            if not (
                _is_simple_chemical_latex_fragment(left)
                and _is_simple_chemical_latex_fragment(right)
            ):
                return match.group(0)
            if "_{" not in left and "_{" not in right:
                return match.group(0)
            changed = True
            return f"${left}{right}$"

        updated = _ADJACENT_INLINE_MATH_RE.sub(repl, text)
        if not changed:
            return updated
        text = updated


def _has_bbox(element: PageElement) -> bool:
    return element.bbox != (0.0, 0.0, 0.0, 0.0)


def _extract_json_object(text: str) -> dict | None:
    stripped = text.strip()
    if not stripped:
        return None
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            parsed = json.loads(stripped[start : end + 1])
        except json.JSONDecodeError:
            return None
    return parsed if isinstance(parsed, dict) else None


def _formula_signal_score(text: str) -> int:
    compact = text.strip()
    if not compact:
        return 0

    score = 0
    if _has_formula_indicators(compact):
        score += 3
    if _LATEX_COMMAND_RE.search(compact):
        score += 2
    if "$" in compact:
        score += 2
    if _FORMULA_FRAGMENT_RE.search(compact):
        score += 2
    if _MIXED_FORMULA_TOKEN_RE.search(compact):
        score += 2
    if re.search(r"[A-Za-z]", compact) and re.search(r"\d", compact):
        operator_hits = len(_FORMULA_OPERATOR_RE.findall(compact))
        if operator_hits >= 2:
            score += 1
    return score


def _should_attempt_vlm_formula_correction(
    elem: PageElement,
    *,
    original_text: str,
    normalized_text: str,
    config: FormulaProcessorConfig,
) -> bool:
    if not config.vlm_correction:
        return False
    if elem.source != "ocr" or elem.type != "text":
        return False
    if not _has_bbox(elem):
        return False

    original_len = len(normalize_for_comparison(original_text))
    normalized_len = len(normalize_for_comparison(normalized_text))
    if max(original_len, normalized_len) == 0:
        return False
    if max(original_len, normalized_len) > config.vlm_candidate_max_chars:
        return False

    score = max(
        _formula_signal_score(original_text),
        _formula_signal_score(normalized_text),
    )
    if score >= 3:
        return True
    return score >= 2 and elem.confidence < 0.95


def _build_formula_correction_prompt(
    *,
    ocr_text: str,
    normalized_text: str,
    candidate: str,
) -> str:
    return (
        "Return one JSON object with keys: confidence, change_kind, replacement_text.\n\n"
        "Task: inspect the tight crop and decide whether the target candidate should be corrected.\n"
        "Correct only the target candidate itself.\n"
        "Do not rewrite the sentence or any surrounding prose.\n"
        "For formulas, prefer standard scientific notation and preserve visible structure.\n"
        "Keep subscripts, superscripts, charges, brackets, operators, and LaTeX markers when they are supported by the crop.\n"
        "Do not simplify a structured formula into plain text.\n"
        "Use change_kind=ocr_fix only for visible OCR mistakes or broken formula notation.\n"
        "Use change_kind=format_only only for harmless spacing or style normalization.\n"
        "If the current text is already a valid formula and the difference is only stylistic, prefer change_kind=none.\n"
        "If the crop does not clearly justify a correction, return an empty replacement_text.\n\n"
        f"Target candidate:\n{candidate}\n\n"
        "Good examples:\n"
        '{"confidence":"high","change_kind":"ocr_fix","replacement_text":"SO₄²⁻"}\n'
        '{"confidence":"high","change_kind":"ocr_fix","replacement_text":"$C_{23}H_{20}O_{2}$"}\n'
        '{"confidence":"medium","change_kind":"none","replacement_text":""}\n'
        "Bad normalization to avoid:\n"
        'C23H20O2\n'
        "Formatting-only example:\n"
        '{"confidence":"medium","change_kind":"format_only","replacement_text":"$\\delta$"}\n\n'
        f"OCR transcript:\n{ocr_text[:1800]}\n\n"
        f"Rule-based normalization preview:\n{normalized_text[:1800]}"
    )


def _formula_structure_score(text: str) -> int:
    score = 0
    if "$" in text:
        score += 2
    if _LATEX_COMMAND_RE.search(text):
        score += 2
    if any(ch in text for ch in _SUBSCRIPT_CHARS | _SUPERSCRIPT_CHARS):
        score += 2
    if "_{" in text or "^{" in text:
        score += 2
    if any(ch in text for ch in "[]{}"):
        score += 1
    if re.search(r"(?<!\s)[+-](?!\s)", text):
        score += 1
    return score


def _normalize_formula_boundary_whitespace(text: str) -> str:
    """Normalize harmless whitespace around math delimiters for equivalence checks."""
    text = re.sub(r"\$\s+", "$", text)
    text = re.sub(r"\s+\$", "$", text)
    return text.strip()


def _is_formula_whitespace_equivalent(original_text: str, candidate_text: str) -> bool:
    return (
        _normalize_formula_boundary_whitespace(original_text)
        == _normalize_formula_boundary_whitespace(candidate_text)
    )


def _passes_formula_basic_acceptance(candidate_text: str) -> bool:
    candidate = candidate_text.strip()
    if not candidate:
        return False
    if _REFUSAL_RE.search(candidate):
        return False
    return True


def _passes_formula_change_guardrails(
    *,
    original_text: str,
    candidate_text: str,
) -> bool:
    original_norm = normalize_for_comparison(original_text)
    candidate_norm = normalize_for_comparison(candidate_text)
    if not candidate_norm:
        return False

    original_len = max(len(original_norm), 1)
    candidate_len = len(candidate_norm)
    if candidate_len < max(4, original_len // 4):
        return False
    if candidate_len > original_len * 3:
        return False
    if compute_edit_distance(candidate_text, original_text) > 0.75:
        return False
    return True


def _preserves_formula_structure(
    *,
    original_text: str,
    candidate_text: str,
) -> bool:
    """Reject replacements that collapse structured notation into plain text."""
    original_structure = max(
        _formula_structure_score(original_text),
        _formula_structure_score(normalize_formulas(original_text)),
    )
    candidate_structure = max(
        _formula_structure_score(candidate_text),
        _formula_structure_score(normalize_formulas(candidate_text)),
    )
    if original_structure >= 2 and candidate_structure == 0:
        return False
    return True


def _is_reasonable_formula_correction(
    *,
    original_text: str,
    candidate_text: str,
) -> bool:
    candidate = candidate_text.strip()
    if not _passes_formula_basic_acceptance(candidate):
        return False

    # Acceptance uses a small set of explicit, generic constraints:
    # 1. non-empty / non-refusal output
    # 2. bounded edit magnitude
    # 3. structure preservation for formula-like content
    # 4. harmless whitespace normalization is always acceptable
    if _is_formula_whitespace_equivalent(original_text, candidate):
        return True
    if not _passes_formula_change_guardrails(
        original_text=original_text,
        candidate_text=candidate,
    ):
        return False
    if not _preserves_formula_structure(
        original_text=original_text,
        candidate_text=candidate,
    ):
        return False
    return True


def _apply_formula_candidate_replacement(
    text: str,
    *,
    candidate: str,
    replacement_text: str,
) -> str | None:
    if not candidate or candidate not in text:
        return None
    if not replacement_text or replacement_text == candidate:
        return None
    if not _is_reasonable_formula_correction(
        original_text=candidate,
        candidate_text=replacement_text,
    ):
        return None
    return text.replace(candidate, replacement_text, 1)


def _dedupe_candidates(candidates: list[str]) -> list[str]:
    ordered = sorted(
        {candidate.strip() for candidate in candidates if candidate.strip()},
        key=len,
        reverse=True,
    )
    result: list[str] = []
    for candidate in ordered:
        if any(candidate in existing for existing in result):
            continue
        result.append(candidate)
    return result


def _candidate_priority(candidate: str) -> tuple[int, int]:
    score = _formula_signal_score(candidate)
    if "$" in candidate:
        score += 2
    if any(ch in candidate for ch in _SUBSCRIPT_CHARS | _SUPERSCRIPT_CHARS):
        score += 2
    if _ASCII_CHARGED_FORMULA_RE.fullmatch(candidate):
        score += 3
    return (-score, len(candidate))


def _is_vlm_formula_candidate(candidate: str) -> bool:
    candidate = candidate.strip()
    if not candidate:
        return False
    if _INLINE_MATH_CANDIDATE_RE.fullmatch(candidate):
        return True
    if _SUBSCRIPT_FRAGMENT_CANDIDATE_RE.fullmatch(candidate):
        return True
    if _ASCII_CHARGED_FORMULA_RE.fullmatch(candidate):
        return True
    if any(ch in candidate for ch in _SUBSCRIPT_CHARS | _SUPERSCRIPT_CHARS):
        return True
    if _LATEX_FRAGMENT_CANDIDATE_RE.search(candidate):
        return True
    if _MIXED_FORMULA_TOKEN_RE.fullmatch(candidate):
        return True
    if _MIXED_FORMULA_WITH_OPERATORS_RE.fullmatch(candidate):
        operator_hits = len(_FORMULA_OPERATOR_RE.findall(candidate))
        return (
            operator_hits >= 2
            and bool(re.search(r"[A-Za-z]", candidate))
            and bool(re.search(r"\d", candidate))
        )
    return False


def _collect_formula_candidates(text: str) -> list[str]:
    candidates: list[str] = []

    candidates.extend(match.group(0).strip() for match in _CHEM_FORMULA_RE.finditer(text))
    for pattern in (
        _INLINE_MATH_CANDIDATE_RE,
        _SUBSCRIPT_FRAGMENT_CANDIDATE_RE,
        _ASCII_CHARGED_FORMULA_RE,
        _MIXED_FORMULA_TOKEN_RE,
        _MIXED_FORMULA_WITH_OPERATORS_RE,
    ):
        candidates.extend(match.group(0).strip() for match in pattern.finditer(text))

    latex_fragments: list[str] = []
    for match in _LATEX_FRAGMENT_CANDIDATE_RE.finditer(text):
        fragment = match.group(0).strip()
        if len(fragment) >= 3:
            latex_fragments.append(fragment)
    candidates.extend(latex_fragments)

    if "$ _" in text:
        candidates.extend(match.group(0).strip() for match in _LATEX_SUBSCRIPT_FRAGMENT_RE.finditer(text))
    if "$ {}" in text:
        candidates.extend(match.group(0).strip() for match in _LATEX_FRAGMENT_RE.finditer(text))

    deduped = _dedupe_candidates(candidates)
    deduped = [candidate for candidate in deduped if _is_vlm_formula_candidate(candidate)]
    return sorted(deduped, key=_candidate_priority)


def _estimate_candidate_bbox(
    element: PageElement,
    candidate: str,
) -> tuple[float, float, float, float]:
    if not _has_bbox(element) or not candidate:
        return element.bbox

    lines = element.content.splitlines() or [element.content]
    line_index = 0
    char_start = 0
    char_end = len(candidate)
    matched = False
    for idx, line in enumerate(lines):
        pos = line.find(candidate)
        if pos >= 0:
            line_index = idx
            char_start = pos
            char_end = pos + len(candidate)
            matched = True
            break

    if not matched:
        pos = element.content.find(candidate)
        if pos < 0:
            return element.bbox
        joined = "\n".join(lines)
        prefix = joined[:pos]
        line_index = prefix.count("\n")
        line = lines[min(line_index, len(lines) - 1)]
        last_newline = prefix.rfind("\n")
        char_start = pos if last_newline < 0 else pos - last_newline - 1
        char_end = char_start + len(candidate)
    else:
        line = lines[line_index]

    x0, y0, x1, y1 = element.bbox
    width = max(x1 - x0, 1.0)
    height = max(y1 - y0, 1.0)
    line_count = max(len(lines), 1)
    line_height = height / line_count
    line_len = max(len(line), 1)

    start_ratio = char_start / line_len
    end_ratio = char_end / line_len
    crop_x0 = x0 + width * start_ratio
    crop_x1 = x0 + width * min(end_ratio, 1.0)
    crop_y0 = y0 + line_height * line_index
    crop_y1 = crop_y0 + line_height

    pad_x = max((crop_x1 - crop_x0) * 0.35, 8.0)
    pad_y = max(line_height * 0.35, 6.0)
    return (
        max(x0, crop_x0 - pad_x),
        max(y0, crop_y0 - pad_y),
        min(x1, crop_x1 + pad_x),
        min(y1, crop_y1 + pad_y),
    )


def _render_formula_crop(
    source_path: Path | None,
    page_number: int,
    bbox: tuple[float, float, float, float],
) -> Path | None:
    if source_path is None or not source_path.exists() or source_path.suffix.lower() != ".pdf":
        return None
    if page_number < 1:
        return None

    try:
        fitz_doc = fitz.open(str(source_path))
    except Exception:
        return None

    try:
        if page_number > len(fitz_doc):
            return None
        page = fitz_doc[page_number - 1]
        rect = fitz.Rect(bbox)
        if rect.is_empty or rect.width <= 1 or rect.height <= 1:
            return None

        pad_x = max(rect.width * 0.12, 6.0)
        pad_y = max(rect.height * 0.18, 6.0)
        clip = fitz.Rect(
            max(page.rect.x0, rect.x0 - pad_x),
            max(page.rect.y0, rect.y0 - pad_y),
            min(page.rect.x1, rect.x1 + pad_x),
            min(page.rect.y1, rect.y1 + pad_y),
        )
        if clip.is_empty or clip.width <= 1 or clip.height <= 1:
            return None

        pix = page.get_pixmap(matrix=fitz.Matrix(220 / 72, 220 / 72), clip=clip)
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        try:
            pix.save(tmp.name)
            return Path(tmp.name)
        except Exception:
            Path(tmp.name).unlink(missing_ok=True)
            raise
    finally:
        fitz_doc.close()


def normalize_formulas(text: str) -> str:
    """Apply formula normalization transforms to a text string.

    Transforms are applied in order of specificity (most specific first)
    to avoid conflicts between patterns.
    """
    if not _has_formula_indicators(text):
        return text

    # 1. Temperature: ℃ → LaTeX
    text = _TEMPERATURE_RE.sub(
        lambda m: rf"${m.group(1)}^{{\circ}}\mathrm{{C}}$",
        text,
    )

    # 2. Chemical formulas with Unicode subscripts
    text = _CHEM_FORMULA_RE.sub(_convert_chemical_formula, text)

    # 3. Micro-units with leading digits: 200μL → $200\,\mathrm{\mu L}$
    text = _MICRO_UNIT_RE.sub(
        lambda m: rf"${m.group(1)}\,\mathrm{{\mu {m.group(2)}}}$",
        text,
    )

    # 3b. Bare micro-units: μg/mL → $\mathrm{\mu g/mL}$
    text = _MICRO_UNIT_BARE_RE.sub(
        lambda m: rf"$\mathrm{{\mu {m.group(1)}}}$",
        text,
    )

    # 4. Standalone math symbols (only outside math mode)
    for symbol, latex in _MATH_SYMBOLS.items():
        if symbol not in text:
            continue
        parts = []
        last_end = 0
        for m in re.finditer(re.escape(symbol), text):
            if not _in_math_mode(text, m.start()):
                parts.append(text[last_end:m.start()])
                parts.append(f"${latex}$")
                last_end = m.end()
        if parts:
            parts.append(text[last_end:])
            text = "".join(parts)

    # 5. LaTeX fragment cleanup: $ {}^{13} $C → $^{13}C$
    text = _LATEX_FRAGMENT_RE.sub(
        lambda m: f"${m.group(1)}{m.group(2)}$",
        text,
    )

    # 5b. Element $ _N $ → consolidate: CDCl$ _3 $ → $\mathrm{CDCl_{3}}$ (skip for now, complex)
    text = _LATEX_SUBSCRIPT_FRAGMENT_RE.sub(
        lambda m: f"${m.group(1)}_{{{m.group(2).strip('{}')}}}$",
        text,
    )

    text = _merge_adjacent_chemical_fragments(text)

    return text


class FormulaProcessor:
    """Normalize Unicode mathematical/scientific notation to LaTeX."""

    def __init__(
        self,
        config: FormulaProcessorConfig | None = None,
        *,
        vlm_service: OpenAICompatibleService | None = None,
        crop_renderer: CropRenderer | None = None,
    ):
        self._config = config or FormulaProcessorConfig()
        self._vlm = vlm_service
        self._crop_renderer = crop_renderer or _render_formula_crop

    def _correct_formula_with_vlm(
        self,
        *,
        elem: PageElement,
        original_text: str,
        normalized_text: str,
        candidate: str,
        source_path: Path | None,
    ) -> str | None:
        if not self._vlm:
            return None

        crop_bbox = _estimate_candidate_bbox(elem, candidate)
        crop_path = self._crop_renderer(source_path, elem.page_number, crop_bbox)
        if crop_path is None:
            return None

        try:
            raw = self._vlm.describe_image(
                crop_path,
                _build_formula_correction_prompt(
                    ocr_text=original_text,
                    normalized_text=normalized_text,
                    candidate=candidate,
                ),
                context=_FORMULA_CORRECTION_SYSTEM_PROMPT,
                temperature=0.0,
                max_tokens=self._config.vlm_max_tokens,
                structured_output_mode=self._config.vlm_structured_output_mode,
                json_schema=_FORMULA_CANDIDATE_CORRECTION_JSON_SCHEMA,
                json_schema_name=_FORMULA_CORRECTION_JSON_SCHEMA_NAME,
            )
        except Exception as exc:
            log.warning(
                "Formula VLM correction failed on page %d: %s",
                elem.page_number,
                exc,
            )
            crop_path.unlink(missing_ok=True)
            return None

        crop_path.unlink(missing_ok=True)
        payload = _extract_json_object(raw)
        if payload is None:
            return None

        confidence = str(payload.get("confidence", "")).strip().lower()
        if confidence == "low":
            return None
        change_kind = str(payload.get("change_kind", "")).strip().lower()
        if change_kind in {"", "none", "format_only"}:
            return None
        replacement_text = str(payload.get("replacement_text", "")).strip()
        corrected_text = _apply_formula_candidate_replacement(
            original_text,
            candidate=candidate,
            replacement_text=replacement_text,
        )
        if corrected_text is None:
            return None
        return normalize_formulas(corrected_text)

    def process(self, doc: Document) -> Document:
        if not self._config.enabled:
            return doc

        converted_count = 0
        vlm_corrected_count = 0
        vlm_candidates_used = 0
        source_path = Path(doc.metadata.source_path) if doc.metadata.source_path else None

        for page in doc.pages:
            for elem in page.elements:
                if elem.type != "text":
                    continue

                original = elem.content
                elem.content = normalize_formulas(elem.content)
                if elem.content != original:
                    converted_count += 1

                if vlm_candidates_used >= self._config.vlm_max_candidates:
                    continue
                if not _should_attempt_vlm_formula_correction(
                    elem,
                    original_text=original,
                    normalized_text=elem.content,
                    config=self._config,
                ):
                    continue
                candidates = _collect_formula_candidates(original)
                if not candidates:
                    continue

                for candidate in candidates:
                    if vlm_candidates_used >= self._config.vlm_max_candidates:
                        break
                    corrected = self._correct_formula_with_vlm(
                        elem=elem,
                        original_text=original,
                        normalized_text=elem.content,
                        candidate=candidate,
                        source_path=source_path,
                    )
                    vlm_candidates_used += 1
                    if corrected and corrected != elem.content:
                        elem.metadata["formula_vlm_correction_used"] = True
                        elem.metadata["formula_vlm_original_text"] = original
                        elem.metadata["formula_vlm_candidate"] = candidate
                        elem.content = corrected
                        vlm_corrected_count += 1
                        break

        if converted_count > 0:
            log.info("Formula normalization: %d elements modified", converted_count)
        if vlm_corrected_count > 0:
            log.info(
                "Formula VLM correction: %d element(s) corrected from %d call(s)",
                vlm_corrected_count,
                vlm_candidates_used,
            )
        return doc
