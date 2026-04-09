# Iteration Backlog

Updated: 2026-04-09 (heading fix + two-column + layout complexity + GT fix)

This file records concrete follow-up tasks after the current baseline
assessment, so we can choose the next iteration from a shared list instead of
re-deriving priorities each time.

## Latest Iteration: Heading Fix + Two-Column + Layout Complexity (2026-04-09)

### What Was Done

**0. Layout complexity detection — OCR fallback gate** (`pipeline.py`, `config/schema.py`)

- Deterministic check in `_check_page_quality()`: detects NATIVE pages where
  PyMuPDF extraction struggles due to complex visual layout (figure-heavy pages
  with many tiny text fragments or single-char heading-font elements).
- Two signals: `tiny_ratio > 0.5` (more than half of elements are tiny fragments)
  and `single_char_big > 2` (graph node labels at heading-size font).
- Validated on 12-doc ground truth: triggers only on paper01 pages 3, 5, 7, 11.
  Zero false positives on all other documents.
- **Default OFF** (`layout_complexity_check: false`). When enabled, reclassifies
  flagged pages to SCANNED → OCR replaces text + provides layout structure.
- Current OCR+VLM quality is insufficient: enabling causes heading_F1 regression
  (0.312→0.233) because OCR text quality is lower than PyMuPDF native extraction
  on these pages. Architecture is ready; awaiting better OCR/VLM models.
- Config: `builders.quality_check.layout_complexity_check: true` to enable.

**1. Multiline heading number resolution** (`processors/chapter.py`)

- Root cause: Chinese academic papers often have section number and title on
  separate lines within one text block (e.g., `"5\n算例分析"`). The existing
  code extracted only the first line (`"5"`), which matched `_PURE_NUMBER_RE`
  and was rejected as a false positive (intended to filter page numbers).
- Fix: new `_resolve_heading_text(content)` helper joins pure-number first
  line with short heading-like second line → `"5 算例分析"`, which passes
  all existing filters and matches `section_arabic_spaced` numbering pattern.
- Applied in 6 call sites: `_detect_heading()`, `_promote_coherent_numbering()`
  (collection + root promotion + nested promotion), OCR heading correction,
  and LLM fallback candidate building.
- Generic rule: activates only when first line is a pure number; zero behavior
  change for all other heading detection paths.

**2. Document-level two-column propagation** (`builders/reading_order.py`)

- Root cause: paper01 (19-page two-column academic paper) had geometric column
  detection succeed on only 11/19 pages. The 8 failed pages had different
  failure modes: too few elements (pages 14-16), side imbalance (pages 3, 13),
  gutter obstruction from figures (pages 5, 11), and title page sparsity (page 1).
- Fix: two-pass approach in `ReadingOrderBuilder.build()`:
  1. Pass 1: per-page independent detection (existing logic, unchanged)
  2. Pass 2: if ≥40% of pages detected, compute median gutter position and
     attempt `detect_columns_with_hint()` on undetected pages using the
     known gutter as a hint.
- `detect_columns_with_hint()`: relaxed detection that skips gutter scanning
  and uses the hint directly. Guards against false propagation:
  - `col_sized < 4`: skip content-sparse pages
  - `tiny_count > col_sized * 1.5`: skip figure-dominated pages
  - `left_edges < 2 or right_edges < 2`: require elements on both sides
- Generic approach: applies to any multi-column document, not paper01-specific.

### Additional Changes

**3. VLM Review truncation fix** (`processors/vlm_review.py`)

- Bug: when element text > 200 chars, the extraction summary sent to VLM
  was truncated with `...`. VLM echoed the `...` back in `original` field,
  causing `_apply_fix()` substring match to fail → corrections silently
  dropped.
- Fix: strip trailing `...` from `corr.original` before matching.
- Generic fix: affects any VLM correction on elements > 200 chars.

**4. paper01 ground truth heading level correction** (`ground_truth/paper01/expected.md`)

- Previous GT had inconsistent heading levels (e.g., `# 2 Programming Model`
  as H1, `## 3.1` as H2, `## 3.2.2` as H2 same level as `## 3.2`).
- Corrected to follow standard academic hierarchy:
  H1=title, H2=top sections (1-12), H3=subsections (3.1, 4.1...), H4=sub-sub (3.2.1).

### Measured Impact

**Internal ground truth (10 docs, deterministic):**

| Document | Metric | Before iteration | After | Delta |
|----------|--------|------------------|-------|-------|
| paper01 | heading_F1 | 0.167 | **0.667** | **+0.500** |
| paper01 | edit_distance | 0.313 | 0.330 | +0.017 |
| paper_chn01 | heading_F1 | 0.690 | **0.774** | **+0.084** |
| paper_chn01 | edit_distance | 0.520 | 0.506 | -0.014 |

paper01 heading_F1 improvement breakdown:
- +0.145 from multiline heading number resolution (code fix)
- +0.355 from ground truth level correction (GT fix)

Other documents unchanged. 384 tests passed, 4 skipped.

**Public ground truth (1 doc):** No regressions.

### Key Design Decisions

1. **`_resolve_heading_text` is conservative by design.** Only activates when
   first line is a pure number. Falls back to original behavior for everything
   else. The `_looks_like_body_text` guard prevents joining with paragraph text.

2. **Propagation requires strong document-level evidence.** The 40% threshold
   means at least ~40% of pages must independently confirm two-column layout
   before propagation kicks in. This prevents false positives on single-column
   documents with occasional wide figures.

3. **Figure-dominated pages are skipped.** The `tiny_count > col_sized * 1.5`
   guard prevents reordering pages where figure labels outnumber body text,
   which caused edit_distance regressions in initial testing.

4. **Hint-based detection uses lower confidence (0.5).** This signals to
   downstream consumers that the column layout is less certain than
   independently-detected layouts.

### Remaining Issues

- paper01 heading_F1 = 0.667: remaining gap is from bold-only sub-headings
  (Operations and Kernels, Sessions, Fault Tolerance, etc.) which have no
  font-size signal — only bold. Current heading detection requires size
  difference or numbering pattern. OCR `block_label: title` can detect these
  but OCR text quality tradeoff makes `layout_complexity_check` net negative
  for now.
- paper01 OCR fallback quality: when `layout_complexity_check: true`, heading
  structure improves (F1 0.667→0.721) but text quality drops (char_f1
  0.975→0.950). Main culprit: page 5 OCR hallucinates GPU0-GPU99 from a
  diagram. VLM Review (gpt-5.4-mini) fails to catch this hallucination.
- paper_chn01 heading_F1 = 0.774 (not 1.0): VLM non-determinism on
  SCANNED/MIXED pages.
- VLM Review correction matching: fixed the `...` truncation bug, but VLM
  model still returns `"page_quality": "ok"` for obviously hallucinated
  content (page 5 GPU list). Model capability bottleneck.

## Previous Iteration: OCR Graceful Degradation (2026-04-09)

### What Was Done

**OCR 三层降级容错** (`services/ocr.py`, `builders/ocr.py`)

- Root cause: PaddleOCR 服务端对特定图片（ocr01 page 7: 绿色背景+表格,
  1240x1754px）启用 Layout Detection 时返回 500。同一图片关闭 Layout Detection
  或降低 DPI 均可成功。这是服务端 bug，非客户端问题。
- 新增三层降级策略:
  1. **指数退避重试** (5 次, 2s→4s→8s→16s→30s)：处理临时性网络/服务故障
  2. **关闭 Layout Detection 重试** (2 次)：绕过服务端对特定图片的处理崩溃
  3. **跳过失败页面继续处理**：OCRBuilder 捕获页面级异常，记录错误日志但不中断
     整个文档解析流程
- 重构 `recognize()` 方法，抽取 `_post_with_retries()` 复用重试逻辑
- ocr01 从解析失败 → 完整解析成功，page 7 走降级路径（无 Layout Detection）

### Measured Impact

| Document | Metric | Before | After | Notes |
|----------|--------|--------|-------|-------|
| ocr01 | status | FAILED (500) | **SUCCESS** | 文档不再因单页 OCR 失败而中断 |
| ocr01 | heading_F1 | 0.714 | 0.833 | 受益于 numbering coherence |
| ocr01 | char_F1 | 0.970 (best) | 0.822 | page 7 降级 OCR 质量下降 |

**回归检查**: 其他文档无影响（OCR 降级仅在重试耗尽后触发）。

### Key Insights

1. **服务端 bug 不可控，客户端必须容错。** PaddleOCR 对特定图片+参数组合会崩溃，
   且不同时间表现可能不同。客户端不能假设 OCR 服务永远可用。

2. **降级优于失败。** 关闭 Layout Detection 的 OCR 结果质量较低（丢失表格结构、
   版面分析），但仍优于完全丢失该页。最终兜底（跳过页面）确保文档解析流程永不中断。

3. **ocr01 page 7 的 500 是确定性可复现的。** 不是临时故障——同一请求在所有重试中
   都返回 500。触发条件：1240x1754px PNG + Layout Detection + 绿色背景+表格。

## Previous Iteration: Heading Detection — Numbering Coherence (2026-04-09)

### What Was Done

**1. Fixed `section_arabic_spaced` regex to include "0"** (`builders/metadata.py`)
- Changed `^[1-9]\d{0,2}` to `^\d{1,3}` — now matches "0 引 言" (section 0).
- Root cause: many Chinese academic papers and standards start section numbering
  from 0. The previous regex excluded single-digit "0", making such headings
  completely undetectable — they weren't even LLM fallback candidates.

**2. Document-level numbering coherence detection** (`processors/chapter.py`)
- New `_promote_coherent_numbering()` method runs after per-element detection,
  before LLM fallback.
- Scans all text elements for arabic numbering signals (spaced, root, nested).
- Root-level: if ≥3 elements form a near-sequential series (e.g., 0,1,2,3,4),
  promotes all undetected members to H2 deterministically.
- Nested-level: if ≥2 subsections under the same root (e.g., 2.1, 2.2),
  promotes all to H3.
- Density guard: if >8 root-level entries found, skip promotion (likely a
  numbered list, not section headings).
- New `_is_coherent_sequence()` helper: checks max gap ≤2, coverage ≥50%.

**3. OCR-assigned heading level correction** (`processors/chapter.py`)
- When OCR pre-assigns heading_level to an element, now checks for numbering
  signal and corrects level accordingly (e.g., OCR says H2 but "2.1 xxx"
  pattern → corrected to H3).
- New filter: OCR headings ending with colon (：:) suppressed — these are
  introductory clauses, not headings.

**4. Targeted colon filter** (`processors/chapter.py`)
- New `_ends_with_colon()` helper applied in coherence pass and OCR heading
  suppression, but NOT in sidebar heading inference (where colon-ending labels
  are legitimate section markers).
- Prevents false positives like "具体实施步骤如下：" and "2 次加载具体方式如下：".

### Measured Impact

**Target document:**

| Document | Metric | Before | After | Delta |
|----------|--------|--------|-------|-------|
| paper_chn01 | heading_F1 | 0.230 | ~0.69-0.71 | **+0.46-0.48** |
| paper_chn01 | char_F1 | 0.891 | 0.889 | -0.002 (noise) |
| text_code_block | heading_F1 | 0.191 | 0.500 | **+0.309** |

**Regression (internal, 12 docs):** heading_F1 avg 0.538 → 0.561 (+0.023).
No regressions: char_F1 avg ±0.001, table_F1 unchanged.

**Tests:** 373 passed, 4 skipped, 0 failures (6 new tests).

### Key Design Decisions

1. **Coherence is a document-level signal.** Per-element detection treats each
   heading in isolation. Coherence detection leverages the sequential structure
   of the entire document, which is a much stronger signal than font size alone.

2. **Deterministic over LLM.** The coherence pass is purely rule-based — no LLM
   calls needed. This makes heading detection reproducible and eliminates the
   non-determinism of LLM fallback for well-numbered documents.

3. **Density guard prevents numbered-list false positives.** The same pattern
   ("1. xxx", "2. xxx") applies to both section headings and numbered list items.
   The >8 threshold distinguishes them: real section headings rarely exceed 8,
   while procedural lists (like text_code_block's 13 steps) are filtered out.

4. **Targeted colon filter.** Adding colon to the global `_looks_like_body_text`
   broke sidebar heading inference. Instead, `_ends_with_colon()` is applied
   only in coherence collection and OCR heading suppression, preserving the
   sidebar label promotion path.

### Remaining Issues

- paper_chn01 heading_F1 ≈ 0.69-0.71 (not 1.0): "Abstract" is a false positive
  (font-based detection), sections 5-6 are completely absent from extraction
  (pages not extracted — provider/OCR issue, not heading detection).
- paper_chn01 heading non-determinism: VLM corrections on OCR pages cause
  small variations in text content, affecting heading matching.
- paper01 heading_F1 = 0.167: two-column layout causes heading fragmentation —
  not addressed by numbering coherence (different root cause).
- ocr_scan_jtg3362 heading_F1 = 0.000: OCR quality too low for numbering
  pattern recognition.

## Previous Iteration: VLM Review Real-Document Validation (2026-04-08)

### What Was Done

**VLM Review prompt and parser fix**
- Original prompt was not being followed by VLM (gpt-5.4-mini) — VLM returned
  bare JSON arrays instead of the `{"corrections": [...], "page_quality": "..."}`
  format. `json_schema` structured output mode did not take effect on this endpoint.
- Fixed: embedded format instructions and few-shot examples directly in user prompt.
- Fixed: parser now handles bare array fallback format.
- Added faithful transcription instruction: "Provide the EXACT text as shown in
  the image, character by character. Do NOT rephrase, correct grammar, improve
  wording, or normalize formatting."

**Page selection narrowed to SCANNED/MIXED only**
- Original design also reviewed NATIVE pages with sparse text (`min_text_chars_for_skip`).
- Real-document testing showed VLM corrections on NATIVE pages cause regressions:
  `fix_text` overwrites correctly-extracted text, `fix_table` corrupts correct tables.
- Root cause: gpt-5.4-mini is not reliable enough to correct well-extracted content.
  It "improves" text based on its understanding rather than faithfully transcribing.
- Decision: disabled NATIVE page review entirely. Only SCANNED/MIXED pages where
  OCR errors are expected benefit from VLM correction.

### Measured Impact

**Target documents:**

| Document | Metric | Before VLM Review | After | Delta |
|----------|--------|-------------------|-------|-------|
| ocr_scan_jtg3362 | char_F1 | 0.562 | ~0.55-0.58 | ±0.02 (non-deterministic) |
| ocr_scan_jtg3362 | heading_F1 | 0.000 | 0.000 | +0.000 |
| ocr_scan_jtg3362 | table_F1 | 0.476 | 0.476 | +0.000 |
| text_table_word | char_F1 | 0.973 | 0.973 | +0.000 (NATIVE, now skipped) |
| text_table_word | heading_F1 | 0.667 | 0.667 | +0.000 (NATIVE, now skipped) |
| text_table_word | table_F1 | 0.913 | 0.913 | +0.000 (NATIVE, now skipped) |

**Regression (internal, 12 docs):** char_F1 -0.001, heading_F1 +0.000, table_F1 +0.000.
NATIVE documents show zero delta. SCANNED documents show small non-deterministic variance.

**Regression (public, 9 docs):** char_F1 -0.068 due to VLM non-determinism on
documents with SCANNED/MIXED pages (omnidoc series). Not a logic issue.

**Tests:** 366 passed, 4 skipped, 0 failures.

### Key Findings

1. **VLM model quality is the bottleneck.** gpt-5.4-mini cannot reliably correct
   document text — it introduces as many errors as it fixes. On NATIVE pages,
   corrections are net negative. On SCANNED pages, corrections are roughly neutral
   with high variance.

2. **VLM non-determinism is significant.** Even with temperature=0.0, the same
   input produces different correction sets across runs. This makes VLM Review
   inherently non-reproducible with current models.

3. **Faithful transcription vs. understanding.** VLM tends to "improve" or
   "correct" text based on its understanding rather than faithfully reading the
   image. This makes it unreliable for OCR correction where exact transcription
   is required.

4. **Overfitting trap.** During development, we tried many targeted filters
   (NATIVE page skip, heading-only corrections, text length thresholds, table
   content dedup, heading detection second pass). Each filter fixed one test
   case but broke others. This violated the generalization principle — the
   correct response to "VLM corrections hurt" is not more heuristics, but
   better VLM capabilities.

### Unsolved Problems

- **text_table_word heading_F1=0.667**: "专家评审组名单" is 593 bezier curves,
  invisible to text extraction. VLM page-level review CAN detect this (confirmed
  in testing), but enabling NATIVE page review causes widespread regressions.
  Needs either: (a) a more capable VLM model, or (b) a non-VLM approach to
  detect vector-rendered text (e.g., path density analysis at extraction level).

- **text_table_word table_F1=0.913**: table column headers rendered as vector
  curves. Same root cause as above.

- **ocr_scan_jtg3362 char_F1≈0.56**: PaddleOCR quality on low-resolution scans
  is limited. VLM correction does not reliably improve it. Higher-quality VLM
  or better OCR engine would help.

- **VLM Review on SCANNED pages**: corrections are roughly neutral. With a more
  capable model (or targeted prompt tuning per document type), there is potential
  for significant OCR error correction.

## Previous Iteration: VLM Review Processor + Header/Footer Identity (2026-04-08)

### What Was Done

**VLM Review Processor (`processors/vlm_review.py`) — NEW**
- New page-level VLM review capability that addresses two previously unsolvable
  problems: scanned page OCR errors and vector-rendered text loss.
- Renders selected pages as images (PyMuPDF, 200 DPI), sends page image +
  current extraction summary to VLM as a "reviewer".
- VLM returns structured JSON corrections: `fix_text`, `add_missing`, `fix_table`.
- Corrections applied in-place: text replacement, new element insertion,
  source tagged as `"vlm"`, original preserved in `vlm_review_original` metadata.
- Selective triggering: SCANNED/MIXED pages always reviewed, NATIVE pages
  reviewed only when text is suspiciously sparse (< `min_text_chars_for_skip`).
- Cost controls: `max_pages_per_doc=50`, concurrent execution via ThreadPoolExecutor.
- Config: `processors.vlm_review.enabled`, `review_all_pages`, `render_dpi`,
  `max_tokens`, `structured_output_mode`.
- Runs after ImageProcessor + image extraction/VLM description, before FormulaProcessor.
- DOCX mode skips (added to `_GEOMETRY_PROCESSORS`).

**Header/Footer First-Page Identity Retention Tightened**
- Previously: ALL repeated furniture on page 1 (except page numbers) was retained.
- Now: maximum `max_retained_identity` elements retained (default 2), ranked by
  text length (information density). Excess elements are removed.
- Retained elements now also receive `exclude_from_heading_detection=True` metadata
  for downstream safety.
- New `HeaderFooterConfig` extends `ProcessorToggle` with `max_retained_identity`.

**Config Changes (`config/schema.py`)**
- New `VLMReviewConfig`: enabled, review_all_pages, min_text_chars_for_skip,
  render_dpi, max_pages_per_doc, max_tokens, structured_output_mode.
- New `HeaderFooterConfig(ProcessorToggle)`: max_retained_identity (default 2).
- `ProcessorsConfig.header_footer` type changed from `ProcessorToggle` to
  `HeaderFooterConfig` (backward compatible — inherits enabled + llm_fallback).
- `ProcessorsConfig.vlm_review` added.

### Tests

- 25 new tests in `test_vlm_review_processor.py`: page selection, extraction
  summary, JSON parsing, correction application, config disabled, end-to-end.
- 4 new tests in `test_header_footer.py`: max_retained_identity (default/custom),
  exclude_from_heading_detection, page numbers still removed with high limit.
- Full suite: 357 passed, 4 skipped, 0 failures.

### Key Design Decisions

1. **VLM Review is a reviewer, not a re-extractor.** It receives both the page
   image and the current extraction results. This prevents it from hallucinating
   content and focuses corrections on actual extraction errors.

2. **Selective page triggering** keeps costs manageable. A 100-page native PDF
   with clean extraction triggers zero VLM review calls. Only problematic pages
   (scanned, mixed, sparse text) are reviewed.

3. **Header/footer identity limit = 2** prevents bloated first-page retention
   while keeping the most information-dense elements. Ranking by text length is
   a simple but effective proxy for information density.

4. **Pipeline integration**: VLMReviewProcessor runs inline after image
   extraction (not in `_build_processors`), because it needs `source_path` for
   PyMuPDF rendering. Created fresh in each `_run` call.

### Remaining Gaps

- VLM Review has not been tested on real documents yet — needs end-to-end eval
  on `ocr_scan_jtg3362` (target: char_F1 >> 0.562) and `text_table_word`
  (target: heading_F1 >> 0.667).
- VLM prompt may need tuning based on real correction quality.
- `exclude_from_heading_detection` is set but ChapterProcessor already uses
  `retained_page_identity` check — both signals now present for safety.
- No warning integration yet for VLM review stats (correction count,
  review failures).

## Baseline: paper_chn01 (2026-04-08)

Initial baseline: edit_dist=0.874, char_f1=0.295, heading_f1=0.08, table_cell_f1=0.00.
After all fixes: **edit_dist=0.503, char_f1=0.891, heading_f1=0.23, table_cell_f1=1.00**.

### What Was Done

**1. Full-width → half-width ASCII normalization** (`processors/text_clean.py`)
- New `normalize_fullwidth_ascii()` function using `str.maketrans`: converts
  full-width digits (FF10-FF19), letters (FF21-FF3A, FF41-FF5A), and selected
  math/bracket symbols to half-width. Preserves Chinese punctuation（，。：；！？）.
- Applied at extraction time in `PDFProvider._extract_text_elements` and
  `_extract_tables` so all downstream processors see clean text.
- Also applied in `TextCleanProcessor._clean()` as safety net for text from
  VLM review / OCR.
- New config: `TextCleanConfig.normalize_fullwidth: bool = True`.
- Impact: char_f1 0.295 → 0.671.

**2. Garbled text → OCR fallback** (`providers/pdf.py`)
- `_classify_page()` now counts U+FFFD replacement characters.  If ratio
  exceeds 5% of total text chars, page is classified as SCANNED so OCR fully
  replaces the garbled native text.
- Root cause: CFF Type1 fonts (`FzBookMaker*`) with custom glyph names
  (G21, G22...) in /Differences but no ToUnicode CMap.  PyMuPDF and pdfminer
  both fail to map these to Unicode.  Even PDF viewers' copy-paste produces
  garbled text for some characters — confirming the mapping is genuinely
  missing from the PDF, not just a library limitation.
- Using SCANNED (not MIXED) is critical: MIXED mode deduplicates and keeps
  garbled native text; SCANNED mode replaces all native text with OCR output.
- Impact: page 1 fully recovered by OCR; char_f1 0.671 → 0.723.

**3. LLM-based page quality check for formula OCR** (`pipeline.py`)
- New `_check_page_quality()` runs between extraction and OCR.
- For each NATIVE page, pre-filters by short-line ratio (>25%), then sends
  extracted text to LLM asking whether formula fragmentation is present.
- If LLM confirms → page reclassified to SCANNED → OCR produces complete
  LaTeX formulas (`$$ \begin{aligned}...$$`) instead of character fragments.
- Tested generalization: LLM correctly identified pages 2-5 as formula-heavy
  while keeping pages 6-7 (prose + references) as NATIVE.  No false positives
  on 11 other test documents (tables, code, receipts, etc.).
- New config: `QualityCheckConfig` (enabled, pre_filter_short_ratio,
  max_text_chars).
- Impact: char_f1 0.723 → 0.804, formulas now render as proper LaTeX.

**4. expected.md baseline corrections**
- Converted HTML `<table>` to Markdown pipe format (eval only parses pipe
  tables).
- Fixed LlamaParse formatting artifacts in table cells: `[6 m, 7 m]` →
  `[6m，7m）` to match PDF original (Chinese comma, half-open interval).
- Removed figure-chart data mistakenly represented as text table (图5 is a
  line chart, not a data table).
- Impact: table_cell_f1 0.00 → 1.00.

**5. LaTeX prime simplification** (`processors/text_clean.py`)
- New `simplify_latex_primes()`: `x^{^{\prime}}` → `x'` and
  `x^{\prime}` → `x'`.  Both render identically; the apostrophe form is
  standard and matches expected.md.
- Applied in `TextCleanProcessor._clean()` after other normalizations.
- Impact: char_f1 0.804 → 0.891, edit_dist 0.536 → 0.503.

### Remaining Issues

- `paper_chn01`: edit_dist=0.503, char_f1=0.891, heading_f1=0.23, table_cell_f1=1.00.
  1. **Heading detection** (heading_f1=0.23, 3/14): Section headings on pages
     6-7 (NATIVE) not detected — same font size as body text.  Pages 2-5
     (OCR) headings partially detected but numbering patterns may differ from
     expected format.  ChapterProcessor needs adaptation for journals where
     headings share body font size.
  2. **edit_dist=0.503**: Remaining gap is mostly formatting noise — superscript
     reference style (`<sup>[1]</sup>` vs `$ ^{[1]} $`), LaTeX block structure
     (multi-line vs compact), minor OCR text inaccuracies (e.g. spurious
     hyphen in `DI-ILSR`).  These do not affect rendering quality or
     readability.  Further optimization is not cost-effective.

## Previous Iteration: DOCX Pipeline Fix & .doc Support (2026-04-08)

### What Was Done

**DOCX 流式文档处理路径修复 (`pipeline.py`)**
- 根因：DOCX 元素 bbox 全为 `(0, 0, 0, 0)`（流式文档无页面几何信息），导致
  ContentValueProcessor 的 `wide_sparse_banner`（`width >= page.width * 0.75`
  → `0 >= 0` 始终 true）和 `image_cluster`（所有元素重叠在原点）惩罚全面误触发。
  绝大多数文本被错误标记 `skip_render`，封面、目录、子标题、列表项等全部丢失。
- 修复：pipeline 在 DOCX 模式下跳过三类几何依赖处理器
  （`_GEOMETRY_PROCESSORS = (HeaderFooterProcessor, CodeBlockProcessor,
  ContentValueProcessor)`），同时跳过无意义的 builder 步骤（MetadataBuilder
  字体统计、OCRBuilder、ReadingOrderBuilder）。
- DOCX 处理链简化为：Extract → Chapter → Table → Image → Formula → LineUnwrap
  → TextClean → Render。
- 设计原则：DOCX 是流式文档，有样式语义（标题、列表、表格），不需要几何推断。
  提取原始信息 + 章节识别即可。

**新增 .doc 格式支持 (`pipeline.py`)**
- `_convert_doc_to_docx()`: 调用 LibreOffice headless (`soffice --convert-to docx`)
  将 .doc 转换为 .docx，保留语义信息后走 DOCX 处理路径。
- `source_path` 保留原始 .doc 路径以便追溯。
- 不将 DOC/DOCX 转成 PDF 处理，因为转 PDF 会丢失样式语义信息（标题样式→几何位置），
  反而增加处理复杂度。

**新增评测样本 `text_report01`**
- 来源：四川省重大技术装备首台套申请模板 Word 文档。
- 包含封面、目录、多级子标题、表格、图片——典型的企业 Word 表格文档。
- LlamaParse baseline 已校正（清除 HTML 表格、分页线、错误标题等 artifact）。

### Measured Impact

| Document | Edit Dist | Char F1 | Heading F1 | Table F1 | Warn | Notes |
|----------|-----------|---------|------------|----------|------|-------|
| text_report01 (before) | — | — | — | — | 1 | 701 chars output, volume drift warning |
| text_report01 (after) | 0.097 | 0.955 | 0.714 | 1.000 | 0 | 1017 chars output, no warnings |

**回归检查**：全量 ground_truth/ 回归正常，其他 PDF 文档指标无变化。

### Key Insights

1. **流式文档 vs 页面文档**：DOCX 和 PDF 是根本不同的文档模型。PDF 有页面几何，
   适合基于 bbox 的处理器（页眉页脚检测、信息价值评分、等宽字体检测）。DOCX 是
   流式的，有样式语义但无几何信息，应走简化路径。不应将 DOCX 转 PDF 来统一处理——
   转换会丢失样式语义，引入额外的布局偏差。

2. **ContentValueProcessor 的零值陷阱**：当 `page.width = 0` 时，
   `width >= page.width * 0.75` 等条件始终为 true，所有元素都被惩罚。
   这类"零值导致条件反转"的 bug 在依赖几何信号的模块中需要警惕。

3. **.doc → .docx 是语义等价转换**：同为 Word 格式家族，LibreOffice 转换
   保留标题样式、列表类型、表格结构等语义信息。而 .doc/.docx → PDF 是降维转换，
   会丢失语义并引入不必要的几何复杂度。

### Remaining Gaps

- text_report01 封面区域的结构化格式（列表项 `- **标签**：` vs 纯文本行）取决于
  Docling 提取层面的段落样式识别，非 pipeline 处理层面问题。
- DOCX 图片提取为零（Docling 提取流程未输出嵌入图片），需排查 ImageExtractor
  的 `extract_docx` 路径。

## Previous Iteration: OCR-Scan Detection & VLM Path Simplification (2026-04-08)

### What Was Done

**OCR-Layered Scan Detection (`providers/pdf.py`)**
- New spatial detection in `_classify_page()`: if a dominant image covers >50%
  of the page and >70% of text characters sit inside that image's bbox, the
  page is classified as SCANNED.
- This correctly identifies "searchable scan" PDFs where a previous OCR tool
  embedded a visible (or invisible) text layer on top of scan images. Without
  this detection, these pages were misclassified as NATIVE, causing ParserX to
  trust the (often poor quality) embedded OCR text.
- Validated on JTG 3362-2018 (268-page OCR scan PDF): 264/268 pages correctly
  detected. Zero false positives on 14 other normal PDFs and 2 vector-table PDFs.
- Helper method `_count_chars_inside_bbox()` added.

**OCR Builder: Native Text Replacement (`builders/ocr.py`)**
- On SCANNED pages, if pre-existing native text/table elements are found (from
  an embedded OCR text layer), they are removed before adding fresh OCR results.
- Image elements are preserved for downstream VLM processing.
- This ensures ParserX re-OCRs with its own PaddleOCR engine rather than
  trusting the embedded text layer.

**VLM Correction Path Simplification (`processors/image.py`)**
- Removed `_apply_vlm_supplement()` function (~70 lines).
- Removed `is_fullpage_scan` detection block from `_apply_vlm_corrections()`.
- All non-skipped images now follow a single VLM correction path.
- Rationale: the fullpage scan detection was redundant (OCR Builder already
  handles it), used unreliable heuristics (3 OCR elements overlapping = trigger),
  and silently discarded VLM table and description outputs.

**Ground Truth Expansion**
- Added `ocr_scan_jtg3362`: 4-page subset of JTG 3362-2018 (OCR text layer +
  scan images, many OCR character errors). Tests OCR-layered scan detection.
- Added `text_table_word`: Word-exported PDF where table headers are rendered as
  vector curves (outlined text, invisible to text extraction). Tests detection
  of missing vector-rendered text.

### Measured Impact

| Document | Edit Dist | Char F1 | Heading F1 | Table F1 | Notes |
|----------|-----------|---------|------------|----------|-------|
| ocr_scan_jtg3362 | 0.648 | 0.562 | 0.000 | 0.476 | PaddleOCR re-ran; quality limited by scan resolution |
| text_table_word | 0.050 | 0.973 | 0.667 | 0.913 | Vector header text still missing |
| text_table_libreoffice | 0.000 | 1.000 | 1.000 | 1.000 | Control: no regression |

**Tests:** 307 passed (8 new: 7 for page classification, 1 for OCR text replacement).

### Key Insights from Architecture Discussion

1. **VLM > OCR principle**: When VLM has OCR text as reference, its output should
   be prioritized. The supplement mode violated this by discarding VLM table/
   description corrections on full-page scans.

2. **Classification is the foundation**: The OCR-layered scan problem was not a
   VLM issue — it was a classification issue. Correct classification (SCANNED vs
   NATIVE) determines whether OCR re-runs and whether the right processing path
   is taken.

3. **Two unsolved edge cases require a new VLM capability**:
   - Pure scanned pages: VLM completely absent (scan image skipped, no other
     image elements). OCR errors remain uncorrected.
   - Vector-rendered text: Characters converted to curves, no image element for
     VLM to process, text extraction fails silently.
   Both point to a **page-level VLM review** capability (see Next Priorities).

4. **Generalization over specificity**: Avoid fine-grained heuristics tied to
   specific document features (image size ratios, overlap counts). Prefer
   principled geometric signals (spatial containment) that generalize across
   document types.

### Open Issues

- `paper01` (TensorFlow whitepaper, 19pp): edit_dist 0.530→0.319 after Tier 1
  reading order fix (11/19 pages reordered). Remaining issues:
  1. ~~**Two-column reading order**~~: ✅ Tier 1 geometric detection works on
     11/19 pages. 8 pages still undetected — mostly pages with large
     cross-column figures/tables that break gutter continuity. Needs
     **Tier 2 PaddleOCR layout fallback** for these ambiguous pages.
  2. **Images/figures missing**: 5+ figures not extracted or poorly handled.
  3. **Heading detection chaotic**: single digits ("2"), figure labels
     ("C", "b", "x") detected as `##` headings. Section numbers split
     from heading text due to column break.
  4. ~~**Width guard breaks two-column merge**~~: ✅ Fixed with per-column
     `column_right_margin` metadata. LineUnwrap now uses column-aware
     right margin on multi-column pages.
  5. **Code block (Figure 1)** not fenced: Python code in Courier font
     rendered as plain text with comments merged into code lines.
  6. **Table 1** not extracted as table (rendered as narrative text).
- `ocr_scan_jtg3362` char_F1=0.562: PaddleOCR quality on low-resolution scans
  is limited. VLM page-level review could improve this.
- `text_table_word` heading_F1=0.667: "专家评审组名单" rendered as 593 bezier
  curves, completely invisible to text extraction. No current mechanism can
  recover this without page rendering + VLM.
- 4 pages of JTG 3362 not detected as OCR-scan (image coverage <50% on those
  pages — e.g., cover page with partial scan image).
- `text_code_block`: ✅ Core issues resolved by CodeBlockProcessor (2026-04-08).
  Shell `#` no longer becomes H1, code line breaks preserved. Remaining:
  inline code detection (commands mid-sentence lack backticks) — deferred.

## Previous Iteration: Formula Format Normalization (2026-04-07)

### What Was Done

**FormulaProcessor — Unicode-to-LaTeX normalization**
- New `FormulaProcessor` in `parserx/processors/formula.py` with five transforms:
  1. Temperature: `30℃` → `$30^{\circ}\mathrm{C}$`
  2. Chemical formulas: `H₂SiCl₂` → `$\mathrm{H_{2}SiCl_{2}}$` (element detection
     + Unicode subscript conversion)
  3. Micro-units: `200μL` → `$200\,\mathrm{\mu L}$` (with and without leading digits)
  4. Math symbols: `≥` → `$\ge$`, `≤` → `$\le$`, `±` → `$\pm$`, etc. (only outside
     existing `$...$` delimiters)
  5. LaTeX fragment cleanup: `$ {}^{13} $C` → `$^{13}C$` (consolidate fragmented
     LaTeX from PyMuPDF extraction)
- Runs after ImageProcessor, before LineUnwrapProcessor (no effect on heading
  detection or verification)
- 32 unit tests covering all transforms and no-op cases

### Measured Impact

**Public eval (9/10 docs):**

| Document | Before | After | Change |
|----------|--------|-------|--------|
| en_text_01 (formulas) | 0.283 | 0.170 | ↓ 40% |
| en_text_02 (chemistry) | 0.088 | 0.051 | ↓ 42% |
| zh_text_02 (units/temp) | 0.238 | 0.166 | ↓ 30% |
| Avg edit distance | 0.101 | 0.077 | ↓ 24% |
| Avg char F1 | 0.964 | 0.977 | ↑ 1.3% |

0 warnings. No regressions on other documents.

**Internal eval (7 docs):** edit_dist 0.038→0.036, 2 warnings (receipt, unchanged).

**Tests:** 278 passed, 0 failures.

### Known Issues

- en_text_01 still has residual gap (0.170) from `\mathrm{}` wrapping differences
  and space normalization in LaTeX. Further improvement would require deeper
  LaTeX fragment analysis.
- LLM chapter fallback on zh_text_02 shows occasional non-determinism (7 spurious
  orphan-heading warnings in ~1/3 runs). Not caused by FormulaProcessor.

## Previous Iteration: Verification Fixes, Duplication Elimination & Line Unwrap (2026-04-07)

### What Was Done

**Verification Layer — False Positive Elimination (text_pic02: 4→0 warnings)**
- HallucinationDetector: skip edit-distance check for `vlm_summary` descriptions
  when the image already has `vlm_corrected_text`/`vlm_corrected_table`. Semantic
  summaries are inherently different from raw OCR text.
- CompletenessChecker: `_is_renderable` now treats images with VLM corrections as
  renderable even when skipped. `_check_text_volume` includes VLM-corrected
  content in source volume. `_check_table_count` counts VLM-produced tables from
  image elements.

**Cross-Page Table Duplication Fix (ocr01: 1→0 warnings, VLM 13→1 calls)**
- Root cause: TableProcessor merges cross-page tables (e.g., pages 3-6), but
  ImageProcessor independently VLM-processes images on those pages, producing
  duplicate content.
- Fix: ImageProcessor now builds a `table_covered_pages` set from
  `merged_from_pages` metadata and skips VLM for images on those pages.
- Optional `vlm_refine_merged_tables` config toggle: when enabled, sends ALL
  page images of a merged table to VLM in a single multi-image call to
  correct/refine the merged table.
- VLM service extended with `describe_images()` for multi-image requests.

**Receipt Heading Over-Detection (receipt: 14→2 warnings)**
- MetadataBuilder: added font-size-group frequency filter — font sizes whose
  total character count exceeds 10% of body text are excluded from heading
  candidates. Catches secondary body fonts used for labels/nav links.
- ChapterProcessor: added false-positive patterns for prices (`$200.00`) and
  navigation links (`管理订阅 ›`).
- Remaining 2 warnings: single label "账单与付款" at a unique font size (edge case).

**Line Unwrap Polish (text_table01, text_table_libreoffice: edit_dist 0.030→0.000)**
- Root cause: PyMuPDF extracts each visual line as a separate PageElement. The
  within-element unwrap processor never sees them together.
- Fix: two-pass approach in LineUnwrapProcessor:
  1. Cross-element merging: adjacent text elements with same font, no sentence-end
     punctuation, and close vertical proximity get merged into single elements.
  2. Within-element unwrapping: existing `_unwrap_text_block` handles remaining `\n`.
- List item continuation lines now merge correctly (only next-line list markers
  block merging, not current-line markers).

### Measured Impact

**Internal eval (7 docs):**

| Metric | Before | After | Change |
|--------|--------|-------|--------|
| Total warnings | 22 | 2 | ↓ 91% |
| Avg edit distance | 0.075 | 0.038 | ↓ 49% |
| Avg char F1 | 0.968 | 0.981 | ↑ 1.3% |
| Avg heading F1 | 0.571 | 0.714 | ↑ 25% |
| ocr01 edit_dist | 0.274 | 0.057 | ↓ 79% |
| text_table01 edit_dist | 0.030 | 0.000 | ↓ 100% |
| text_table_libreoffice edit_dist | 0.030 | 0.000 | ↓ 100% |
| receipt warnings | 14 | 2 | ↓ 86% |
| text_pic02 warnings | 4 | 0 | ↓ 100% |
| ocr01 VLM calls | 13 | 1 | ↓ 92% |

**Public eval (9/10 docs):** 0 warnings, no regressions.

**Tests:** 246 passed, 0 failures.

### Known Issues

- `receipt`: 2 remaining orphan-heading warnings ("账单与付款" at unique font size —
  font-based detection edge case for non-document layouts)
- `omnidoc_research_report_zh_table_01`: PaddleOCR HTTP 500 (external service bug,
  not ParserX — consistently fails across runs)
- `omnidoc_academic_literature_en_text_01`: edit_dist 0.283 (formula format gap —
  ParserX emits H₂SiCl₂ while expected uses LaTeX `$\mathrm{H_{2}SiCl_{2}}$`)
- `omnidoc_book_zh_text_02`: edit_dist 0.238 (formula + math symbol gap, similar
  to en_text_01)
- `deepseek`: edit_dist 0.094 (CJK full-width vs half-width char normalization;
  list item line-break formatting difference)
- `pdf_text01_tables`: minor table cell spacing (e.g., "60型" vs "60 型")

## Previous Iteration: Image Pipeline & VLM Correction (2026-04-06)

### What Was Done

**Image Output Contract & Quality Checks**
- Eliminated placeholder text leak, added `ProductQualityChecker` (4 checks)
- Fixed chapter file image paths, completeness checker alignment
- tool-eval supports artifact-only mode (no expected.md required)

**VLM-Authoritative Correction Architecture**
- VLM is authoritative, OCR is initial draft. VLM receives both the original
  image and OCR evidence, so it has strictly more information.
- Three-field output: `vlm_corrected_text` (refined text), `vlm_corrected_table`
  (refined table), `description` (image semantic description) — stored and
  rendered independently in their natural formats
- OCR elements suppressed via bbox overlap + text containment matching
- `vlm_refine_all_ocr` config switch: when enabled, ALL scanned-page images go
  through VLM refinement (higher quality, higher cost); when disabled (default),
  images well-covered by OCR are skipped to save cost

**ContentValueProcessor Fixes**
- OCR elements exempt from position/fragmentation penalties (edge_band,
  side_edge, multi_short_lines) — OCR block positions reflect document layout,
  not UI structure
- Body-column short text penalty reduced (-0.10 vs -0.32 for edges)
- OCR baseline boost +0.15

**VLM Prompt Improvements**
- visible_text policy: "transcribe ALL readable text including icons, labels"
- Route hints: text-heavy → capture all visible text; table → non-table text
  goes to visible_text; diagram → readable labels in visible_text
- Evidence-first policy rewritten for completeness over compactness

**Eval & Reliability**
- Per-document error tolerance in eval runner
- OCR: 5 retries with exponential backoff (cap 30s)
- Internal test set expanded from 4 to 7 documents (+ocr01, text_pic02, receipt)

### Measured Impact (Public Eval, 9/10 docs)

| Metric | Before | After | Change |
|--------|--------|-------|--------|
| Warnings | 10 | 0 | ↓ 100% |
| Avg edit distance | 0.145 | 0.101 | ↓ 30% |
| Avg char F1 | 0.936 | 0.964 | ↑ 3% |
| VLM calls | 5 | 0 | ↓ 100% |
| Wall time | 44.6s | 6.5s | ↓ 85% |

Internal eval: original 4 docs zero regression; 3 new docs provide expanded
coverage (ocr01: scanned+tables, text_pic02: mixed text/images, receipt:
non-standard layout).

### Known Issues

- `omnidoc_research_report_zh_table_01`: PaddleOCR HTTP 500 on large images
  (service-side bug, not ParserX)
- `receipt`: 14 orphan-heading warnings (ChapterProcessor too aggressive on
  non-document layouts)
- `ocr01`: VLM sometimes puts table content in visible_text instead of markdown
  (prompt/response quality, partially mitigated by image_type + tabular heuristic)

## Alignment Summary

This section records the current alignment between:

- the original architecture / requirements planning
- the latest baseline evaluation
- an external expert review of the current iteration

### What Both Sides Agree On

- The current iteration has already completed most of the originally planned
  P0/P1 evaluation infrastructure:
  - checked-in public smoke benchmark
  - richer eval reporting (`warnings`, `api_calls`, `llm_fallback_hits`)
  - `parserx compare`
  - `--set` config overrides
  - live E2E service tests
- The next iterations should be driven by measured benchmark results, not by
  intuition alone.
- Automatic evaluation and human review should now be treated as complementary,
  not competing, signals.
- The current LLM/OCR/VLM stack is now testable end-to-end, which means future
  work should be judged against real online-service baselines.
- It is worth explicitly recording iteration decisions in-repo so that future
  work follows a stable sequence.

### New Evaluation Alignment

After the four-tool comparison run, we should explicitly distinguish between:

- core fidelity metrics, where ParserX is often strong
- reader-facing document quality, where LlamaParse currently wins on several
  mixed-layout finance/report samples

Current shared conclusion:

- ParserX should not optimize only for edit distance and char F1
- document identity retention, figure/chart handling, and Markdown readability
  must become first-class evaluation targets
- some of these targets can be automated partially, and should move out of
  purely manual review over time

### Internal Dataset Findings

The in-repo `ground_truth/` set revealed a different profile from the public
OmniDoc-style samples:

- on native text plus native table documents, ParserX currently has a clear
  quality advantage
- on long cross-page engineering tables, ParserX is already a strong baseline
- on ordinary internal prose documents, ParserX now achieves perfect edit
  distance (0.000) after the line-unwrap two-pass fix
- on webpage-like or screenshot-derived content, we still need a better policy
  for deciding what page identity to keep and what UI chrome to drop

Representative takeaways:

- `pdf_text01_tables`:
  - ParserX cross-page table merging is a real strength and should be protected
  - LlamaParse keeps more structure metadata in HTML form, but is weaker for
    clean Markdown table output
- `text_table01`:
  - ParserX now achieves edit_dist=0.000 after cross-element line unwrap fix
  - previously LlamaParse felt smoother; now ParserX is on par or better
- `deepseek`:
  - webpage-style identity and navigation need a dedicated policy
  - "keep everything" is noisy, but "strip aggressively" is also wrong
- `text_table_libreoffice`:
  - ParserX achieves edit_dist=0.000 on clean office-export PDFs
  - no remaining quality gaps on this document type

### What Has Already Been Fixed

The following review concerns were valid when raised, but are already handled:

- `ChapterProcessor._classify_candidates()` attempted-flag initialization
- non-greedy / brace-aware VLM JSON extraction
- compare warnings for non-overlapping document sets
- metadata pollution across failed VLM retries
- explicit CLI config-path reporting and fallback warnings
- stable warning-heavy public subset and warning-type eval summaries
- VLM evidence-first routing for text-heavy images
- model / backend A/B support with config overlays
- provider-specific VLM request knobs (`api_style`, `extra_body`)
- VLM structured-output constraints with OCR fallback for truncated JSON

- explicit CLI config-path printing, missing-config warnings, regression tests
- stable warning-heavy public subset (`subsets/warning_heavy.txt`)
- per-warning-type evaluation summary (`summarize_warning_types()`)
- config/model metadata in eval report headers (`build_config_report_metadata()`)
- receipt heading over-detection (14→2 warnings via frequency filter + false-positive patterns)
- text_pic02 verification false positives (4→0 via VLM-correction-aware checks)
- cross-page table VLM duplication (ocr01: 13→1 VLM calls)
- line unwrap polish (text_table01, text_table_libreoffice: edit_dist 0.030→0.000)
- formula format normalization (FormulaProcessor: temperature, chemical formulas,
  micro-units, math symbols, LaTeX fragment cleanup → en_text_01 0.283→0.170,
  en_text_02 0.088→0.051, zh_text_02 0.238→0.166)

These should not be treated as open next-step items anymore.

### Where the Views Differed

The main disagreement was about the next primary optimization target.

External-review leaning:
- move next into `ChapterProcessor` fallback refinement
- continue with fallback prompt / batching / hierarchy consistency work

Current baseline-driven conclusion:
- `ChapterProcessor` fallback is not yet a net-positive aggregate win
- the most visible current quality risk is VLM drift, not chapter fallback
- therefore VLM drift reduction should come before deeper chapter-fallback work

Why this conclusion was adopted:
- public compare shows chapter fallback can reduce warnings, but does not yet
  improve aggregate core quality enough to justify making it the primary next
  optimization target
- public live-config eval shows repeated `low-confidence VLM description`
  warnings and number-mismatch patterns

### Adopted Iteration Order

We will follow this order unless new benchmark evidence strongly contradicts it:

1. ~~Stabilize and expose the benchmark workflow~~ ✅
2. ~~Reduce VLM drift~~ ✅
3. ~~Image output contract + product quality checks~~ ✅
4. ~~VLM-authoritative correction model + three-field output~~ ✅
5. ~~Internal test set expansion (ocr01, text_pic02, receipt)~~ ✅
6. ~~Fix receipt heading over-detection (ChapterProcessor)~~ ✅
7. ~~Header/footer retention policy (first-page identity preservation)~~ ✅
8. ~~text_pic02 residual warnings (low-confidence VLM + duplicates)~~ ✅
9. ~~Formula format normalization (FormulaProcessor)~~ ✅
10. ~~Line unwrap polish (native PDF hard-wrap scars)~~ ✅
11. Run VLM model / prompt / routing A/B tests
12. Revisit `ChapterProcessor` fallback refinement
13. Deeper structure work (`StructureRoleAnalyzer`)

Items 1-6, 8-10 are completed.  Also completed: cross-page table VLM
duplication fix, multi-image VLM service extension, OCR-scan detection and
VLM path simplification (2026-04-08).

14. **VLM Review Processor** ✅ (2026-04-08): page-level OCR correction and missing-text recovery
15. **OCR-layered scan detection** ✅ (2026-04-08)
16. **Code block detection** ✅ (2026-04-08): CodeBlockProcessor, body font
    recalculation, heading density guard, line width guard.
17. **Multi-column reading order — Tier 1** ✅ (2026-04-08): Geometric gutter
    detection + zone-based reordering + per-column right margin in LineUnwrap.
    paper01: edit_dist 0.530→0.319 (↓40%), 11/19 pages reordered.
18. **Multi-column reading order — Tier 2** (PaddleOCR layout fallback for
    pages where geometric detection fails, e.g. large cross-column figures)
19. **Multiline heading number resolution** ✅ (2026-04-09): `_resolve_heading_text`
    joins split number+title lines. paper_chn01 heading_F1 0.690→0.774.
20. **Document-level two-column propagation** ✅ (2026-04-09): median gutter
    hint for undetected pages. paper01 15/19 pages reordered (was 11/19).

### Next Priorities

**Near-term (next 1-2 iterations):**

1. **VLM Review Processor end-to-end eval** — run on ocr_scan_jtg3362
   (target: char_F1 >> 0.562) and text_table_word (target: heading_F1 >> 0.667).
   Tune VLM prompt based on real correction quality. May need to adjust
   structured output schema or add fallback parsing.

2. **Multi-column reading order — Tier 2 (PaddleOCR fallback)** — for the
   4/19 pages of paper01 where document-level propagation is still blocked
   (pages 3, 13, 15, 16 — too sparse or figure-dominated). Render page as
   image → PaddleOCR layout API → use `block_order` for reading order.

2b. **Cross-element heading fragment merging** — paper01 headings split across
    columns into separate PageElements (not multiline within one element).
    Current `_merge_cover_heading_fragments` only works on page 1. Generalize
    to merge heading fragments on any page where column layout is detected.

3. **`vlm_refine_merged_tables=true` quality evaluation** — now implemented but
   default off. Test on ocr01 to compare VLM-refined merged tables vs OCR-only.

**Mid-term:**

4. **`vlm_refine_all_ocr=true` quality evaluation** — compare on/off mode on
   the internal set to quantify the quality vs cost tradeoff and decide whether
   to change the default.

5. **VLM model / prompt A/B tests** — now that we have a stable baseline with
   2 warnings, it's a good time to compare different VLM models/prompts.

6. **Revisit `ChapterProcessor` fallback refinement** — receipt edge case
   "账单与付款" (2 warnings) may benefit from LLM fallback or additional
   heuristic refinement.

7. **Deeper structure work (`StructureRoleAnalyzer`)** — document self-induction
   for chapter/list detection.

8. **OCR async batch mode** — merged-PDF submission for scanned PDFs,
   2-3x speed improvement.

**Test data gaps:**

- Financial/report PDFs (for header/footer retention evaluation)
- Academic documents with formulas (needed for formula recognition)
- These should be added to `ground_truth/` before starting those iterations.

### Completed in This Iteration

- ~~receipt heading over-detection~~ (14→2 warnings): frequency-based heading
  candidate filter + price/nav-link false-positive patterns
- ~~text_pic02 residual warnings~~ (4→0): verification layer now accounts for
  VLM-corrected content on image elements
- ~~Cross-page table VLM duplication~~ (ocr01 duplicate content eliminated):
  ImageProcessor skips VLM on pages covered by merged tables
- ~~Line unwrap polish~~ (text_table01, text_table_libreoffice → perfect scores):
  cross-element merging joins adjacent continuation-line elements

## P0: Must Fix

### 0. Expand evaluation beyond pure text fidelity

Current signals:
- automatic metrics underweight reader-facing quality
- cross-tool review showed that document identity, chart retention, and layout
  readability can dominate user preference even when text scores are lower

Tasks:
- adopt the layered evaluation model in `docs/evaluation.md`
- use `docs/quality_rubric.md` as the common manual-review standard
- add semi-automatic checks for:
  - first-page identity retention
  - HTML leakage in Markdown-first outputs
  - duplicate OCR/body overlap
  - image-placeholder leakage
  - chart/image asset linkage

Why:
- if we do not score these dimensions, we will keep optimizing the wrong thing

### 1. Rework header/footer retention policy

Current signals:
- ParserX currently removes some page-level metadata that readers consider
  essential, especially on finance/report title pages
- aggressive cleanup can improve heading metrics while making the document feel
  incomplete

Tasks:
- separate "repeated furniture" from "document identity metadata"
- preserve important first-page header blocks when they carry identity signals
- test whether to:
  - keep first-page metadata only
  - or keep repeated metadata on every page when confidence is low
- ensure retained header blocks do not poison chapter detection
- add warnings when header/footer removal deletes likely title-page metadata

Why:
- this is one of the clearest current gaps between ParserX scores and human
  preference

### 2. Redesign image output contract

Current signals:
- current outputs leak internal placeholder text such as
  `Text content preserved in OCR body text.`
- chart and figure handling is weaker than user expectations
- users want linked image assets, not base64, and want image retention to be
  selective rather than all-or-nothing

Tasks:
- save image assets/screenshots under a stable subdirectory such as `images/`
- reference them from Markdown via relative links
- never inline image bytes as base64
- classify images into:
  - decorative
  - text-only
  - table-only
  - chart / mixed informational
- keep chart / mixed informational images with descriptions
- usually drop text-only / table-only images after reliable body extraction
- remove internal placeholder/debug strings from final output
- preserve image descriptions only when they add user value

Status (2026-04-06):
- placeholder text leak eliminated: OCR-overlap text-heavy images now render
  as minimal `![](path)` (with file) or are suppressed (without file)
- internal marker fragment guard added to `get_image_reference_text()`
- `ProductQualityChecker` added with placeholder leakage detection
- image asset linkage checks verify Markdown↔disk consistency

Remaining:
- chart / mixed informational image classification refinement
- chart-body integration (see item 3)

Why:
- image policy now directly affects perceived completeness and readability

### 3. Improve chart extraction and chart-body integration

Current signals:
- chart titles and chart-derived information are often missing in ParserX
- LlamaParse currently does better on samples such as the
  "常熟银行与沪深300指数行情走势图" chart because it keeps the chart, names it,
  and provides a rough extracted table

Tasks:
- detect chart regions and preserve chart title/caption
- keep linked chart image assets in Markdown
- generate concise chart descriptions
- extract chart text/table hints into the body when reliable
- mark extracted chart data as approximate when confidence is limited
- keep chart block near the surrounding narrative instead of isolating it

Why:
- charts are user-visible proof of "high fidelity"; losing them is costly even
  when plain-text metrics look acceptable

Design reference:
- `docs/header_footer_image_policy.md`

### 4. Make project-config loading explicit in CLI

Status (2026-04-07): **Completed.**
- Default auto-discovery of `parserx.yaml` in place.
- `_log_config_resolution()` prints resolved config path in eval/compare.
- Missing config triggers `logging.warning()`.
- Regression tests: `test_cmd_compare_warns_when_both_configs_omitted()`,
  `test_cmd_eval_logs_resolved_project_config_path()` in `tests/test_cli.py`.

### 5. Reduce VLM drift against OCR/native evidence

Status (2026-04-07): **Completed.**
- VLM drift largely resolved in 2026-04-06 iteration.
- Remaining false positives (low-confidence VLM on vlm_summary descriptions,
  text volume drift from VLM corrections, table count mismatch) fixed in
  2026-04-07 iteration by making the verification layer VLM-correction-aware.
- text_pic02: 4→0 warnings.

Why:
- this is the biggest quality warning source in the current baseline

### 6. Re-evaluate default-on LLM chapter fallback

Current signals:
- warning count can improve
- aggregate quality metrics do not clearly improve
- latency increases

Tasks:
- tighten candidate selection before fallback
- compare fallback only on documents with weak numbering / weak font signals
- add per-document fallback gain/loss analysis
- consider defaulting fallback off until it shows a net-positive benchmark result

Why:
- the current fallback is not yet a clean default behavior

## P1: High-Value Experiments

### 7. VLM model A/B compare

Tasks:
- compare current VLM model against at least one alternative model endpoint
- run on the public warning-heavy subset first
- record warning count, char F1, edit distance, and latency

Status:
- completed for three configs on the warning-heavy subset
- repeated benchmark after the structured-output fix is now stable enough to trust

Suggested slices:
- `omnidoc_book_zh_text_02`
- `omnidoc_academic_literature_en_text_01`
- `omnidoc_research_report_zh_table_01`

Why:
- current evidence suggests prompt changes alone may not solve drift

### 8. Prompt-style A/B compare at small scale

Tasks:
- compare `strict_bilingual`, `strict_zh`, `strict_en`
- measure whether the prompt language should match document language
- check whether bilingual prompts improve stability or just add verbosity

Why:
- we now have the config hooks to test this cheaply

### 9. OCR-first routing for image descriptions

Tasks:
- classify image pages into text-heavy vs diagram-heavy before VLM summarization
- on text-heavy images, prefer OCR transcript + minimal summary
- on diagram-heavy images, prefer summary + small visible text supplement

Status:
- partially completed
- text-heavy + strong-overlap images now skip VLM and use OCR overlap evidence directly
- diagram-heavy routing can still be refined

Why:
- image types need different output policies, not just different prompts

### 10. Preserve useful formatting signals

Status (2026-04-07): **Partially completed (formula normalization done).**
- FormulaProcessor handles Unicode→LaTeX conversion for temperatures, chemical
  formulas, micro-units, math symbols, and fragmented LaTeX cleanup.
- en_text_01 0.283→0.170, en_text_02 0.088→0.051, zh_text_02 0.238→0.166.

Remaining:
- bold emphasis preservation
- deeper LaTeX normalization (\mathrm{} wrapping, fragment consolidation)
- inline math/symbol degradation during OCR/VLM cleanup

### 11. Integrate image-derived text/tables into the surrounding body flow

Current signals:
- ParserX can extract image-derived content, but often emits it as detached
  blocks or placeholders
- readers want chart/table/text content to appear in the right narrative place

Tasks:
- place image-derived table/text blocks near the nearest caption or section
- reduce duplicated emission between image summary and OCR body text
- ensure chart/table blocks are not emitted before the title or identity block

Why:
- correctness alone is not enough if reading order still feels broken

Design reference:
- `docs/header_footer_image_policy.md`

## P1: Quality and Evaluation Infrastructure

### 12. Build a stable public warning-heavy subset

Status (2026-04-07): **Completed.**
- `ground_truth_public/subsets/warning_heavy.txt` checked in.
- Used as default A/B benchmark set for OCR/VLM iteration.
- CLI supports `--include-list` for subset evaluation.

### 13. Add per-warning-type evaluation summary

Status (2026-04-07): **Completed.**
- `summarize_warning_types()` in `parserx/eval/warnings.py` groups and counts
  warnings by 23 categorized types.
- Eval reports include "## Warning Types" table with per-type breakdown.
- Compare reports include warning delta columns.

### 14. Track config and model metadata in eval reports

Status (2026-04-07): **Completed.**
- `build_config_report_metadata()` in `parserx/eval/reporting.py` extracts and
  formats config source, overrides, provider engines, OCR/VLM/LLM service
  details, image routing settings, chapter fallback, and verification toggles.
- Appended as "## Run Metadata" section at top of every eval/compare report.

### 15. Add semi-automatic product-quality checks

Tasks:
- add checks for first-page identity retention
- add duplicate-content / overlap warnings
- add image-placeholder leakage warnings
- add chart/image asset-linkage checks
- add HTML table leakage counts for Markdown-first outputs
- add repeated-page-identity over-retention warnings

Status (2026-04-06):
- `ProductQualityChecker` implemented in `parserx/verification/product_quality.py`
- four checks live: placeholder leakage, HTML table leakage, image asset
  linkage (Markdown↔disk), duplicate body text (image desc vs page text)
- wired into pipeline with `verification.product_quality_check` toggle
- four new warning categories registered in `parserx/eval/warnings.py`

Remaining:
- first-page identity retention check
- repeated-page-identity over-retention warnings
- chart/image asset-linkage (chart-specific, depends on item 3)

Why:
- these checks can convert subjective complaints into actionable regressions

### 16. Improve line-unwrapping polish for native-text internal PDFs

Status (2026-04-07): **Completed.**
- Root cause: PyMuPDF extracts each visual line as a separate PageElement;
  within-element unwrap never sees adjacent elements.
- Fix: two-pass `LineUnwrapProcessor` — cross-element merging (pass 1) joins
  adjacent same-font text elements that are continuation lines, then
  within-element unwrap (pass 2) handles remaining `\n`.
- List item continuation lines merge correctly; new list markers block merging.
- Vertical gap heuristic prevents merging across paragraph breaks.
- text_table01: edit_dist 0.030→0.000, char_f1 0.986→1.000.
- text_table_libreoffice: edit_dist 0.030→0.000, char_f1 0.985→1.000.

## P2: Reliability / Production Hardening

### 17. Add degraded-service integration tests

Tasks:
- simulate OCR timeout
- simulate VLM malformed JSON
- simulate partial VLM failures in multi-image documents
- verify retries, warnings, and graceful degradation

Why:
- we currently have a success-path baseline, not a failure-path baseline

### 18. Improve compare visibility for unmatched documents

Status:
- log warnings are now emitted

Next work:
- surface unmatched docs in the compare report body
- distinguish parse failure vs missing ground truth vs filtered-out sample

Why:
- compare should help us spot regressions in coverage, not just shared successes

### 19. Separate API-call semantics more cleanly

Tasks:
- standardize where request counts live
- avoid dual-source counting patterns where possible
- add explicit metrics for `llm_requests`, `llm_fallback_hits`, `vlm_requests`

Why:
- cost accounting needs one authoritative source

## Suggested Next Iteration

Current state (2026-04-08, post OCR-scan detection iteration):
- Internal eval (9 docs): edit_dist 0.232 (avg incl. new hard docs), char_f1 0.845
- Internal eval (original 7 docs): 2 warnings, edit_dist 0.036, char_f1 0.982
- Public eval: 0 warnings, edit_dist 0.077, char_f1 0.977
- OCR-layered scan PDFs now correctly classified and re-OCR'd.
- VLM correction path simplified (single path, no supplement branch).
- Two new ground truth documents added (ocr_scan_jtg3362, text_table_word).
- Remaining quality gaps: scanned page OCR quality (needs VLM review),
  vector-rendered text recovery (needs VLM review), header/footer identity.

Recommended next iteration priorities:

1. **VLM Review Processor** — the highest-impact new capability. A new processor
   that renders selected pages as images and sends them to VLM as a "reviewer"
   to identify and correct extraction errors. Addresses two unsolved problems:
   - Scanned pages where OCR errors remain uncorrected (ocr_scan_jtg3362:
     char_F1=0.562)
   - Vector-rendered text invisible to extraction (text_table_word: heading
     "专家评审组名单" missing)
   Design: one VLM call per page, structured correction output, in-place update.

2. **Header/footer first-page identity retention** — current implementation is
   overly broad (retains all repeated furniture on page 1). Consider limiting to
   max 1-2 retained elements. Lower priority than VLM review.

3. **Deeper LaTeX normalization** — en_text_01 still has 0.170 edit distance from
   `\mathrm{}` wrapping differences and space normalization.

4. **Chart retention and chart-body integration** — chart titles and chart data
   often missing. Needs chart-type image detection and chart-aware rendering.

5. **LLM chapter fallback determinism** — zh_text_02 shows occasional spurious
   orphan-heading warnings due to LLM non-determinism. Low priority.

## Newly Clarified Product Requirements

The latest iteration discussion clarified several constraints that future work
must preserve:

- Noise suppression must optimize for `information value`, not for narrow UI-
  specific heuristics.
- Image handling is a core product differentiator: valuable images should be
  converted into searchable textual evidence plus concise semantic description.
- For image-heavy or screenshot-like content, the first question is not
  "is this UI?" but "does this region carry standalone information useful to
  document understanding?"
- Ambiguous content should prefer extract-first / preserve-first handling over
  delete-first handling.
- Public `warning-heavy` results are no longer enough on their own; internal
  evaluation sets should continue to drive generalized fixes and catch blind
  spots that public slices miss.

## Next Diagnostic / Optimization Tracks

### 13. Add information-value scoring for low-value block suppression

Tasks:
- introduce a generic `informational_value_score` for text/image blocks
- combine content density, continuity with neighboring body text, edge-band
  location, repetition, symmetry, and decorative/icon evidence
- use it to suppress low-value shell/chrome/noise without hard-coding for one
  app or export format

Why:
- this is the generic version of the current `deepseek` residual-noise problem
- it should generalize to navigation bars, readers, watermarks, sidebars, and
  app-export chrome

### 14. Preserve informative screenshot / image content as searchable evidence

Tasks:
- define a clearer output contract for informative images:
  `visible_text` / `chart labels` / `markdown table evidence` / concise summary
- ensure screenshot-like images keep their informative region text, numbers,
  labels, and relations when those are useful for retrieval
- avoid collapsing informative screenshots into generic "UI screenshot" prose

Why:
- ParserX's differentiator is not just text extraction but multimodal
  information preservation

### 15. Use internal evaluation sets as first-class optimization drivers

Tasks:
- keep running the internal repo set alongside the public warning-heavy subset
- record per-document residual error themes, not just aggregate metrics
- expand diagnosis around:
  - `text_table_libreoffice` heading/title splitting
  - `deepseek` residual shell/chrome text
- require proposed fixes to show no regression on both public and internal sets

Why:
- internal samples currently expose more realistic generalization gaps than the
  public set

### 16. OCR async batch mode — merged-PDF submission

Tasks:
- Add async job API support to `PaddleOCRService`: submit → poll jobId → download JSONL result
- Implement `_build_image_bundle_pdf()`: merge multiple page images into a single PDF for one-shot submission
- Add auto-split on 413 (payload too large): recursively halve the batch and retry
- Strategy selection: auto / sync_images / async_pdf_bundle
  - `auto`: use sync for <=2 pages, async bundle for >=3 pages
  - Expose as CLI flag or config: `builders.ocr.mode`
- Validate page-count alignment between submitted bundle and returned JSONL
- Reference implementation: legacy doc-refine `pipeline.py` lines 223-301 (async API), 1260-1300 (bundle PDF builder)
- Requires: confirm async job endpoint URL (separate from current sync endpoint); add `job_url` and `async_auth_scheme` to `OCRBuilderConfig`

Why:
- Current sync per-page OCR is the main bottleneck for scanned PDFs (3 pages = 3 sequential HTTP round-trips, often minutes of wait)
- Async bundle mode sends one request for all pages, server processes in parallel, typically 2-3x faster
- Legacy pipeline validated this pattern on 268-page scan PDFs
  small public subset alone
