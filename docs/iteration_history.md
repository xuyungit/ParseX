# Iteration History

This file archives completed iteration records from the ParserX development
process. Each section documents what was done, measured impact, design
decisions, and remaining issues identified at the time.

For the current active backlog, see [iteration_backlog.md](iteration_backlog.md).

---

## Iteration 13: LLM Line Unwrap Fallback + Batch OCR (2026-04-10)

### What Was Done

**1. Three-way merge decision + LLM fallback** (`processors/line_unwrap.py`)

- Implemented the design from `project_llm_unwrap_next.md`:
  `_should_merge_lines` now returns a three-way signal (merge/keep/uncertain)
  instead of boolean.
- Uncertain cases (CJK short lines, abbreviation periods, semicolons,
  uppercase proper nouns) are collected and sent to LLM in batch.
- Body-font filter prevents over-merging of titles and table headers.
- DOCX documents skip LineUnwrapProcessor entirely (no visual line breaks
  to unwrap).

**2. Batch OCR** (`services/ocr.py`)

- Pages needing OCR are extracted from the original PDF into a temporary
  PDF and sent in a single PaddleOCR API call (`fileType=0`) instead of
  per-page image uploads.
- Auto-splits at 100 pages to stay within API limits.
- Falls back to per-page on failure.
- Toggle via `builders.ocr.batch` config (default: on).

**3. VLM review per-page retry** (`processors/vlm_review.py`)

- Streaming responses can fail mid-transfer (peer closed connection).
  The OpenAI SDK's `max_retries` only covers request-level failures,
  not mid-stream disconnects.
- Added application-level retry (3 attempts, exponential backoff) around
  each page's VLM review call.

### Measured Impact

_(Metrics compared to previous iteration baseline where available)_

| Document | Metric | Before | After | Change |
|----------|--------|--------|-------|--------|
| (pending full regression comparison) | | | | |

---

## Iteration 12: Rawdict Word Space Recovery + Multi-Column Propagation (2026-04-10)

### What Was Done

**1. Character-level gap detection for word space recovery** (`providers/pdf.py`)

- Problem: PDF text extraction via `get_text("dict")` lost inter-word spaces.
- Fix: switched to `get_text("rawdict")` which returns per-character bounding
  boxes. Added `_reconstruct_line_from_chars()` that measures gaps between
  consecutive character bboxes and inserts a space when
  `gap > font_size * 0.25`.
- CJK-aware: suppresses space insertion between CJK ideographs and fullwidth
  punctuation, but NOT between fullwidth ASCII letters which need word spacing.

**2. Multi-column hint detection for mixed-layout pages** (`builders/reading_order.py`)

- Problem: page 1 of Chinese academic papers has mixed layout. Only 2
  column-width elements remained after filtering. Detection blocked on
  minimum thresholds.
- Fix: relaxed thresholds to `col_sized < 2` and `left_edges < 1`.

**3. CJK continuation signals for line unwrap** (`processors/line_unwrap.py`)

- Added `_CJK_CONTINUATION_RE` regex for orphaned punctuation and bracketed
  references at line start as continuation signals.
- Removed overly broad 1-2 CJK char orphan pattern that caused false merges.

### Measured Impact

| Document | Metric | Before | After | Change |
|----------|--------|--------|-------|--------|
| paper_chn02 (new) | English spaces | missing | recovered | fixed |
| receipt | edit_distance | 0.031 | 0.030 | -0.001 |
| paper01 | edit_distance | 0.328 | 0.325 | -0.003 |
| paper_chn01 | edit_distance | 0.506 | 0.503 | -0.003 |

### Key Design Decisions

- Gap threshold `0.25 * font_size` chosen empirically: word gaps ~3.69pt
  (font 10.3pt), intra-word gaps <=0. Threshold of 2.57 cleanly separates.
- Fullwidth ASCII letters excluded from CJK suppression (Latin text rendered
  wide needs word space detection).
- Column hint relaxation safe because hint gutter validated from other pages.

---

## Iteration 11: VLM Format Guard + Zero-Signal Heading Fallback (2026-04-09)

### What Was Done

**0. VLM review prompt optimization + format guard** (`processors/vlm_review.py`)

- Prompt fix: explicit FORBIDDEN block listing disallowed changes.
- Code guard: `_is_format_only_change()` normalizes both strings and rejects
  format-only corrections.
- ocr_scan_jtg3362: 12 format-only corrections rejected per run.

**1. Zero-signal LLM fallback for short headings** (`processors/chapter.py`)

- Allow OCR elements (font.size=0) with short text (<=30 chars) into LLM
  fallback pool. Native PDF elements with known body-size fonts still rejected.
- Added `zero_signal` flag and prompt annotation.

**2. OCR heading suppression fall-through** (`processors/chapter.py`)

- Changed `continue` to `pass` so suppressed OCR headings fall through to
  standard detection pipeline. Preserved original OCR heading level as
  `ocr_level_hint` metadata.

### Measured Impact

| Document | Metric | Before | After | Change |
|----------|--------|--------|-------|--------|
| text_table_word | heading_f1 | 0.667 | 1.000 | +0.333 |
| ocr_scan_jtg3362 | heading_f1 | 0.000 | 0.100 | +0.100 |

---

## Iteration 10: VLM Review Eval + Outlined Text Detection (2026-04-09)

### What Was Done

**0. ocr_scan_jtg3362 ground truth correction** — expected.md rewritten to
match actual PDF content. char_f1 jumped from 0.572 to 0.881.

**1. VLM review end-to-end evaluation** — Net effect negative (char_f1
0.881->0.865, table_f1 0.86->0.74). fix_text accurate but fix_table/formatting
drift causes regressions.

**2. Outlined text OCR recovery** (`pipeline.py _check_page_quality`) — Detect
NATIVE pages with tables whose header row has >=50% empty cells, reclassify to
SCANNED. text_table_word: table_cell_f1 0.913->1.000, char_f1 0.973->0.987.

**3. fix_table duplication bug fix** — Use full element replacement instead of
prefix replacement. ocr01: table_cell_f1 0.111->0.941.

**4. VLM provider A/B/C comparison** (14-document full eval) — Provider A
(proxy gpt-5.4-mini) best overall; VLM review is non-deterministic.

### Measured Impact

| Document | Metric | Before | After | Change |
|----------|--------|--------|-------|--------|
| ocr_scan_jtg3362 | char_f1 | 0.572 | 0.891 | +0.319 (GT fix + OCR) |
| ocr_scan_jtg3362 | table_f1 | 0.476 | 0.857 | +0.381 |
| text_table_word | char_f1 | 0.973 | 0.987 | +0.014 |
| text_table_word | table_f1 | 0.913 | 1.000 | +0.087 |
| ocr01 | table_f1 | 0.111 | 0.941 | +0.830 |

---

## Iteration 9: Gutter Refinement + Adaptive Line Unwrap (2026-04-09)

### What Was Done

**0. Column detection gutter refinement** (`builders/reading_order.py`) — Skip
elements spanning across gutter during boundary refinement.

**1. Adaptive CJK line unwrap for narrow columns** (`processors/line_unwrap.py`)
— Block-local column width estimate (P75) + `last_raw_len` tracking to prevent
accumulated merge length from masking short breaks.

**2. patent01 ground truth** — New test document: Chinese invention patent,
14 pages. Two-column metadata + single-column body.

### Measured Impact

patent01 PAGE 1: column ordering fixed, line breaks merged, abstract body
merged. Regression tests: no regressions from code changes.

### Key Design Decisions

- Gutter-spanning exclusion mirrors `classify_element()`.
- P75 instead of max for local column width (robust against CJK+ASCII outliers).
- `last_raw_len` only affects within-element unwrap path.

---

## Iteration 8: Heading Fix + Two-Column + Layout Complexity (2026-04-09)

### What Was Done

**0. Layout complexity detection** — Deterministic check for figure-heavy pages.
Default OFF (OCR quality insufficient to be net positive).

**1. Multiline heading number resolution** (`processors/chapter.py`) — Joins
pure-number first line with heading-like second line. Applied in 6 call sites.

**2. Document-level two-column propagation** (`builders/reading_order.py`) —
Two-pass approach: independent detection then hint-based propagation using
median gutter position.

**3. VLM Review truncation fix** — Strip trailing `...` from `corr.original`.

**4. paper01 GT heading level correction** — Corrected to standard academic
hierarchy.

### Measured Impact

| Document | Metric | Before | After | Delta |
|----------|--------|--------|-------|-------|
| paper01 | heading_F1 | 0.167 | 0.667 | +0.500 |
| paper_chn01 | heading_F1 | 0.690 | 0.774 | +0.084 |
| paper_chn01 | edit_distance | 0.520 | 0.506 | -0.014 |

---

## Iteration 7: OCR Graceful Degradation (2026-04-09)

### What Was Done

**OCR 三层降级容错** (`services/ocr.py`, `builders/ocr.py`)

1. 指数退避重试 (5次)
2. 关闭 Layout Detection 重试 (2次)
3. 跳过失败页面继续处理

ocr01 从解析失败 -> 完整解析成功。

---

## Iteration 6: Heading Detection — Numbering Coherence (2026-04-09)

### What Was Done

1. Fixed `section_arabic_spaced` regex to include "0".
2. Document-level numbering coherence detection with density guard (>8 skip).
3. OCR-assigned heading level correction with colon filter.

### Measured Impact

| Document | Metric | Before | After | Delta |
|----------|--------|--------|-------|-------|
| paper_chn01 | heading_F1 | 0.230 | ~0.69-0.71 | +0.46-0.48 |
| text_code_block | heading_F1 | 0.191 | 0.500 | +0.309 |

---

## Iteration 5: VLM Review Real-Document Validation (2026-04-08)

### What Was Done

- VLM Review prompt and parser fix (few-shot examples, bare array fallback).
- Page selection narrowed to SCANNED/MIXED only (NATIVE review causes regressions).

### Key Findings

1. VLM model quality is the bottleneck (gpt-5.4-mini introduces as many errors
   as it fixes).
2. VLM non-determinism is significant even with temperature=0.0.
3. VLM tends to "improve" text rather than faithfully transcribing.

---

## Iteration 4: VLM Review Processor + Header/Footer Identity (2026-04-08)

### What Was Done

- New VLMReviewProcessor: page-level VLM review for SCANNED/MIXED pages.
- Header/Footer first-page identity retention tightened (max 2 elements,
  ranked by text length).
- 25 new tests for VLM review, 4 new tests for header/footer.

---

## Iteration 3: paper_chn01 Baseline (2026-04-08)

Initial: edit_dist=0.874, char_f1=0.295. After: edit_dist=0.503, char_f1=0.891.

1. Full-width -> half-width ASCII normalization (char_f1 0.295->0.671)
2. Garbled text -> OCR fallback via U+FFFD ratio (char_f1 0.671->0.723)
3. LLM-based page quality check for formula OCR (char_f1 0.723->0.804)
4. expected.md baseline corrections (table_cell_f1 0.00->1.00)
5. LaTeX prime simplification (char_f1 0.804->0.891)

---

## Iteration 2: DOCX Pipeline Fix & .doc Support (2026-04-08)

- DOCX 流式文档处理路径修复 (skip geometry-dependent processors).
- .doc -> .docx conversion via LibreOffice headless.
- New eval sample: text_report01.
- text_report01: edit_dist=0.097, char_f1=0.955, heading_f1=0.714, table_f1=1.000.

---

## Iteration 1: OCR-Scan Detection & VLM Path Simplification (2026-04-08)

- OCR-layered scan detection (spatial coverage + text containment).
- OCR Builder native text replacement on SCANNED pages.
- VLM correction path simplification (removed supplement mode).
- New GT: ocr_scan_jtg3362, text_table_word.

---

## Iteration 0: Image Pipeline & VLM Correction (2026-04-06)

- Image output contract + ProductQualityChecker.
- VLM-authoritative correction architecture (three-field output).
- ContentValueProcessor OCR exemptions.
- VLM prompt improvements.
- Public eval: warnings 10->0, edit_dist 0.145->0.101, char_f1 0.936->0.964.

---

## Pre-iteration: Verification Fixes, Duplication Elimination & Line Unwrap (2026-04-07)

- Verification layer false positive elimination (text_pic02: 4->0 warnings).
- Cross-page table VLM duplication fix (ocr01: 13->1 VLM calls).
- Receipt heading over-detection (14->2 warnings).
- Line unwrap polish (text_table01, text_table_libreoffice: edit_dist 0.030->0.000).
- Total warnings 22->2, avg edit_dist 0.075->0.038.

---

## Pre-iteration: Formula Format Normalization (2026-04-07)

- FormulaProcessor: temperature, chemical formulas, micro-units, math symbols,
  LaTeX fragment cleanup.
- Public eval: avg edit_dist 0.101->0.077, avg char_f1 0.964->0.977.
