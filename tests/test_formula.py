"""Tests for FormulaProcessor formula normalization."""

from pathlib import Path

import pytest
from PIL import Image

from parserx.config.schema import FormulaProcessorConfig
from parserx.models.elements import Document, Page, PageElement
from parserx.processors.formula import (
    FormulaProcessor,
    _build_formula_correction_prompt,
    _collect_formula_candidates,
    _estimate_candidate_bbox,
    _is_reasonable_formula_correction,
    normalize_formulas,
)


# ── Temperature ───────────────────────────────────────────────────────


class TestTemperature:
    def test_celsius_unicode(self):
        assert normalize_formulas("30℃") == r"$30^{\circ}\mathrm{C}$"

    def test_celsius_with_space(self):
        assert normalize_formulas("30 ℃") == r"$30^{\circ}\mathrm{C}$"

    def test_celsius_decimal(self):
        assert normalize_formulas("37.5℃") == r"$37.5^{\circ}\mathrm{C}$"

    def test_celsius_degree_c(self):
        assert normalize_formulas("100°C") == r"$100^{\circ}\mathrm{C}$"

    def test_celsius_in_sentence(self):
        result = normalize_formulas("将样品放入30℃培养箱中")
        assert r"$30^{\circ}\mathrm{C}$" in result
        assert "℃" not in result


# ── Chemical formulas ─────────────────────────────────────────────────


class TestChemicalFormula:
    def test_water(self):
        assert normalize_formulas("H₂O") == r"$\mathrm{H_{2}O}$"

    def test_co2(self):
        assert normalize_formulas("CO₂") == r"$\mathrm{CO_{2}}$"

    def test_dichlorosilane(self):
        result = normalize_formulas("H₂SiCl₂")
        assert result == r"$\mathrm{H_{2}SiCl_{2}}$"

    def test_cdcl3(self):
        # CDCl₃ is a common NMR solvent
        assert normalize_formulas("CDCl₃") == r"$\mathrm{CDCl_{3}}$"

    def test_formula_in_sentence(self):
        result = normalize_formulas("the reaction of H₂SiCl₂ with pyridine")
        assert r"$\mathrm{H_{2}SiCl_{2}}$" in result
        assert "₂" not in result

    def test_charged_formula_with_single_superscript(self):
        assert normalize_formulas("NH₄⁺") == r"$\mathrm{NH_{4}^{+}}$"

    def test_charged_formula_with_multiple_superscripts(self):
        assert normalize_formulas("PO₄³⁻") == r"$\mathrm{PO_{4}^{3-}}$"

    def test_charged_formula_in_sentence(self):
        result = normalize_formulas("Sulfate is SO₄²⁻ in solution.")
        assert result == r"Sulfate is $\mathrm{SO_{4}^{2-}}$ in solution."

    def test_no_false_positive_on_plain_text(self):
        # Words should not be treated as chemical formulas
        assert normalize_formulas("Hello world") == "Hello world"

    def test_no_false_positive_on_uppercase_words(self):
        assert normalize_formulas("The RESULTS section") == "The RESULTS section"

    def test_no_conversion_without_subscript(self):
        # Plain element symbols without subscripts should be left alone
        assert normalize_formulas("Fe and Cu") == "Fe and Cu"


# ── Micro-units ───────────────────────────────────────────────────────


class TestMicroUnits:
    def test_microliters_with_digits(self):
        assert normalize_formulas("200μL") == r"$200\,\mathrm{\mu L}$"

    def test_microliters_with_space(self):
        assert normalize_formulas("200 μL") == r"$200\,\mathrm{\mu L}$"

    def test_micrograms(self):
        assert normalize_formulas("500μg") == r"$500\,\mathrm{\mu g}$"

    def test_bare_micro_unit(self):
        result = normalize_formulas("μg/mL")
        assert r"$\mathrm{\mu g/mL}$" in result

    def test_micro_unit_in_sentence(self):
        result = normalize_formulas("加入 800 μL 不同浓度")
        assert r"$800\,\mathrm{\mu L}$" in result


# ── Math symbols ──────────────────────────────────────────────────────


class TestMathSymbols:
    def test_ge(self):
        assert normalize_formulas("≥1") == r"$\ge$1"

    def test_le(self):
        assert normalize_formulas("≤10") == r"$\le$10"

    def test_pm(self):
        assert normalize_formulas("±0.5") == r"$\pm$0.5"

    def test_times(self):
        assert normalize_formulas("2×10") == r"2$\times$10"

    def test_symbol_not_in_math_mode(self):
        # Symbol already in math mode should not be double-wrapped
        result = normalize_formulas(r"$x \ge 1$")
        assert result.count(r"\ge") == 1

    def test_symbol_in_display_math(self):
        result = normalize_formulas("$$x ≥ 1$$")
        assert result == "$$x ≥ 1$$"

    def test_multiple_symbols(self):
        result = normalize_formulas("值≥2.1时则")
        assert r"$\ge$" in result


# ── LaTeX fragment cleanup ────────────────────────────────────────────


class TestLatexCleanup:
    def test_superscript_fragment(self):
        # $ {}^{13} $C → $^{13}C$
        result = normalize_formulas("$ {}^{13} $C")
        assert result == "$^{13}C$"

    def test_superscript_fragment_with_space(self):
        result = normalize_formulas("$ {}^{1} $H")
        assert result == "$^{1}H$"

    def test_subscript_fragment(self):
        # CDCl$ _3 $ → $CDCl_{3}$
        result = normalize_formulas("CDCl$ _3 $")
        assert "$CDCl_{3}$" in result

    def test_subscript_fragment_with_braces(self):
        result = normalize_formulas("CDCl$ _{3} $")
        assert "$CDCl_{3}$" in result

    def test_subscript_fragment_with_multiple_digits(self):
        result = normalize_formulas("C $ _23 $")
        assert result == "$C_{23}$"

    def test_merge_adjacent_chemical_fragments(self):
        result = normalize_formulas("C $ _23 $H $ _20 $$O_{2}$")
        assert result == "$C_{23}H_{20}O_{2}$"


# ── No-op cases ───────────────────────────────────────────────────────


class TestNoOp:
    def test_plain_text(self):
        text = "This is plain English text without formulas."
        assert normalize_formulas(text) == text

    def test_existing_latex(self):
        text = r"The value is $\delta = 165.8$ ppm."
        assert normalize_formulas(text) == text

    def test_empty_string(self):
        assert normalize_formulas("") == ""

    def test_chinese_text(self):
        text = "定量杀菌检验，消毒剂与菌液作用方法"
        assert normalize_formulas(text) == text


class FakeVLMService:
    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls: list[tuple[Path, str, str]] = []

    def describe_image(
        self,
        image_path: Path,
        prompt: str,
        *,
        context: str = "",
        temperature: float = 0.0,
        max_tokens: int = 1200,
        structured_output_mode: str = "off",
        json_schema: dict | None = None,
        json_schema_name: str = "parserx_formula_correction",
    ) -> str:
        self.calls.append((image_path, prompt, context))
        if not self._responses:
            return ""
        return self._responses.pop(0)


def _write_formula_crop(path: Path) -> Path:
    image = Image.new("RGB", (480, 120), color="white")
    image.save(path, format="PNG")
    return path


def test_formula_processor_uses_vlm_for_suspicious_ocr_formula(tmp_path: Path):
    crop_path = _write_formula_crop(tmp_path / "formula.png")
    elem = PageElement(
        type="text",
        content="Sulfate is SO4 2- in solution.",
        bbox=(10.0, 10.0, 200.0, 50.0),
        page_number=1,
        source="ocr",
        confidence=0.88,
    )
    doc = Document(
        pages=[Page(number=1, elements=[elem])],
    )
    vlm = FakeVLMService([
        '{"confidence":"high","change_kind":"ocr_fix","replacement_text":"SO₄²⁻"}'
    ])
    processor = FormulaProcessor(
        FormulaProcessorConfig(vlm_correction=True, vlm_max_candidates=2),
        vlm_service=vlm,
        crop_renderer=lambda source, page, bbox: crop_path,
    )

    processor.process(doc)

    assert elem.content == r"Sulfate is $\mathrm{SO_{4}^{2-}}$ in solution."
    assert elem.metadata["formula_vlm_correction_used"] is True
    assert len(vlm.calls) == 1


def test_formula_processor_rejects_low_confidence_vlm_result(tmp_path: Path):
    crop_path = _write_formula_crop(tmp_path / "low-confidence.png")
    elem = PageElement(
        type="text",
        content="The sample contains H₂O and NH₄⁺.",
        bbox=(10.0, 10.0, 220.0, 50.0),
        page_number=1,
        source="ocr",
        confidence=0.82,
    )
    doc = Document(pages=[Page(number=1, elements=[elem])])
    vlm = FakeVLMService([
        '{"confidence":"low","change_kind":"ocr_fix","replacement_text":"wrong"}'
    ])
    processor = FormulaProcessor(
        FormulaProcessorConfig(vlm_correction=True, vlm_max_candidates=1),
        vlm_service=vlm,
        crop_renderer=lambda source, page, bbox: crop_path,
    )

    processor.process(doc)

    assert elem.content == r"The sample contains $\mathrm{H_{2}O}$ and $\mathrm{NH_{4}^{+}}$."
    assert "formula_vlm_correction_used" not in elem.metadata
    assert len(vlm.calls) == 1


def test_formula_processor_skips_vlm_for_plain_ocr_text(tmp_path: Path):
    crop_path = _write_formula_crop(tmp_path / "plain.png")
    elem = PageElement(
        type="text",
        content="This is ordinary OCR prose without scientific notation.",
        bbox=(10.0, 10.0, 220.0, 50.0),
        page_number=1,
        source="ocr",
        confidence=0.70,
    )
    doc = Document(pages=[Page(number=1, elements=[elem])])
    vlm = FakeVLMService([
        '{"confidence":"high","change_kind":"ocr_fix","replacement_text":"unused"}'
    ])
    processor = FormulaProcessor(
        FormulaProcessorConfig(vlm_correction=True, vlm_max_candidates=1),
        vlm_service=vlm,
        crop_renderer=lambda source, page, bbox: crop_path,
    )

    processor.process(doc)

    assert elem.content == "This is ordinary OCR prose without scientific notation."
    assert len(vlm.calls) == 0


def test_formula_vlm_prompt_requires_localized_candidate_correction():
    prompt = _build_formula_correction_prompt(
        ocr_text="Sulfate is SO4 2- in solution.",
        normalized_text="Sulfate is SO4 2- in solution.",
        candidate="SO4 2-",
    )

    assert "Correct only the target candidate itself." in prompt
    assert "return an empty replacement_text" in prompt
    assert "replacement_text" in prompt
    assert "change_kind" in prompt
    assert "format_only" in prompt
    assert "SO₄²⁻" in prompt
    assert "SO4 2-" in prompt
    assert "prefer standard scientific notation" in prompt
    assert "Do not simplify a structured formula into plain text." in prompt
    assert "C23H20O2" in prompt


def test_collect_formula_candidates_finds_local_formula_spans():
    candidates = _collect_formula_candidates(
        "Sulfate is SO4 2- in solution and C $ _23 $H $ _20 $$O_{2}$ is listed."
    )

    assert "SO4 2-" in candidates
    assert any("C $ _23 $" in candidate for candidate in candidates)
    assert "$$O_{2}$" in candidates or "$O_{2}$" in candidates


def test_collect_formula_candidates_skips_generic_technical_tokens():
    candidates = _collect_formula_candidates(
        "3.22 (sept, 1H, J=6.8 Hz, iPr); $ {}^{13} $C NMR (100 MHz, CDCl $ _3 $): $ \\delta $=165.8"
    )

    assert "$ {}^{13} $C" in candidates
    assert "CDCl $ _3 $" in candidates
    assert "$ \\delta $" in candidates
    assert "J=6" not in candidates
    assert "MHz" not in candidates
    assert "NMR" not in candidates


def test_formula_processor_rejects_structure_flattening_vlm_result(tmp_path: Path):
    crop_path = _write_formula_crop(tmp_path / "flattened.png")
    elem = PageElement(
        type="text",
        content="elemental analysis calcd (%) for C $ _23 $H $ _20 $O $ _2 $.",
        bbox=(10.0, 10.0, 260.0, 50.0),
        page_number=1,
        source="ocr",
        confidence=0.78,
    )
    doc = Document(pages=[Page(number=1, elements=[elem])])
    vlm = FakeVLMService([
        '{"confidence":"high","change_kind":"ocr_fix","replacement_text":"C23H20O2"}'
    ])
    processor = FormulaProcessor(
        FormulaProcessorConfig(vlm_correction=True, vlm_max_candidates=1),
        vlm_service=vlm,
        crop_renderer=lambda source, page, bbox: crop_path,
    )

    processor.process(doc)

    assert elem.content == r"elemental analysis calcd (%) for $C_{23}H_{20}O_{2}$."
    assert "formula_vlm_correction_used" not in elem.metadata
    assert len(vlm.calls) == 1


def test_formula_processor_ignores_format_only_vlm_result(tmp_path: Path):
    crop_path = _write_formula_crop(tmp_path / "format-only.png")
    elem = PageElement(
        type="text",
        content=r"The signal is $ \delta $ = 7.2.",
        bbox=(10.0, 10.0, 220.0, 50.0),
        page_number=1,
        source="ocr",
        confidence=0.81,
    )
    doc = Document(pages=[Page(number=1, elements=[elem])])
    vlm = FakeVLMService([
        '{"confidence":"medium","change_kind":"format_only","replacement_text":"$\\delta$"}'
    ])
    processor = FormulaProcessor(
        FormulaProcessorConfig(vlm_correction=True, vlm_max_candidates=1),
        vlm_service=vlm,
        crop_renderer=lambda source, page, bbox: crop_path,
    )

    processor.process(doc)

    assert elem.content == r"The signal is $\delta$ = 7.2."
    assert "formula_vlm_correction_used" not in elem.metadata
    assert len(vlm.calls) == 1


def test_formula_acceptance_allows_harmless_math_whitespace_normalization():
    assert _is_reasonable_formula_correction(
        original_text=r"$ \delta $",
        candidate_text=r"$\delta$",
    )


def test_formula_acceptance_rejects_structure_flattening():
    assert not _is_reasonable_formula_correction(
        original_text=r"$C_{23}H_{20}O_{2}$",
        candidate_text="C23H20O2",
    )


def test_estimate_candidate_bbox_shrinks_to_local_span():
    elem = PageElement(
        type="text",
        content="Sulfate is SO4 2- in solution.",
        bbox=(10.0, 20.0, 310.0, 60.0),
        page_number=1,
        source="ocr",
    )

    crop_bbox = _estimate_candidate_bbox(elem, "SO4 2-")

    assert crop_bbox[0] > elem.bbox[0]
    assert crop_bbox[2] < elem.bbox[2]
    assert (crop_bbox[2] - crop_bbox[0]) < (elem.bbox[2] - elem.bbox[0])
    assert crop_bbox[1] == elem.bbox[1]
    assert crop_bbox[3] == elem.bbox[3]
