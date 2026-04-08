# Iteration Backlog

Updated: 2026-04-08

This file records concrete follow-up tasks after the current baseline
assessment, so we can choose the next iteration from a shared list instead of
re-deriving priorities each time.

## Latest Iteration: OCR-Scan Detection & VLM Path Simplification (2026-04-08)

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

- `ocr_scan_jtg3362` char_F1=0.562: PaddleOCR quality on low-resolution scans
  is limited. VLM page-level review could improve this.
- `text_table_word` heading_F1=0.667: "专家评审组名单" rendered as 593 bezier
  curves, completely invisible to text extraction. No current mechanism can
  recover this without page rendering + VLM.
- 4 pages of JTG 3362 not detected as OCR-scan (image coverage <50% on those
  pages — e.g., cover page with partial scan image).
- `text_code_block`: Code block detection missing, causing cascading failures.
  **Severe issues:**
  1. **Shell comments `#` become H1 headings**: Lines like `# 参考命令如下`
     are shell comments inside code regions, but rendered as Markdown `#`
     headings — completely wrong semantics.
  2. **Line breaks lost in code regions**: Multi-line code gets merged into
     single lines by LineUnwrapProcessor. E.g., three separate `ceph osd set`
     commands become one line; roman numeral sub-items (i/ii/iii) collapse;
     comment + command + comment all concatenated.
  **Minor issues:**
  3. Heading over-detection on numbered list items (not critical).
  4. Inline code not detected (commands mid-sentence lack backticks).
  **Root cause**: All severe issues trace to one gap — ParserX has no
  mechanism to detect monospace-font regions (Monaco 11.2pt / Menlo-Regular
  11.2pt vs body PingFangSC 12.8pt) and emit them as fenced code blocks.
  If code regions were identified, LineUnwrapProcessor would skip them and
  `#` comments would stay inside fences. This is a **new capability**
  requirement, not a bug in existing rules.

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
7. Header/footer retention policy (first-page identity preservation)
8. ~~text_pic02 residual warnings (low-confidence VLM + duplicates)~~ ✅
9. ~~Formula format normalization (FormulaProcessor)~~ ✅
10. ~~Line unwrap polish (native PDF hard-wrap scars)~~ ✅
11. Run VLM model / prompt / routing A/B tests
12. Revisit `ChapterProcessor` fallback refinement
13. Deeper structure work (`StructureRoleAnalyzer`)

Items 1-6, 8-10 are completed.  Also completed: cross-page table VLM
duplication fix, multi-image VLM service extension, OCR-scan detection and
VLM path simplification (2026-04-08).

14. **VLM Review Processor** (page-level OCR correction and missing-text recovery)
15. **OCR-layered scan detection** ✅ (2026-04-08)

### Next Priorities

**Near-term (next 1-2 iterations):**

1. **VLM Review Processor (page-level review)** — the highest-impact new
   capability. Addresses two unsolved edge cases simultaneously:
   - Pure scanned pages: OCR errors remain uncorrected (no VLM participation)
   - Vector-rendered text: text converted to curves, invisible to extraction
   Design: render selected pages as images, send page image + current extraction
   results to VLM as "reviewer" (not re-extractor). VLM returns structured
   corrections, applied in-place. One VLM call per page. Trigger conditions:
   SCANNED pages, pages with scan-like images, pages with suspected missing
   content. See architecture.md §3.3.4 for design details.

2. **Header/footer first-page identity retention** — backlog P0 #1. The clearest
   gap vs LlamaParse on finance/report documents. Design already in
   `docs/header_footer_image_policy.md`. Current implementation retains all
   repeated furniture on page 1 (except page numbers), which is overly broad
   but low-risk. May tighten with quantity limit (max 1-2 retained elements).

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

**Test data gaps:**

- Financial/report PDFs (needed for header/footer retention validation)
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
