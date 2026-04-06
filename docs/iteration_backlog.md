# Iteration Backlog

Updated: 2026-04-06

This file records concrete follow-up tasks after the current baseline
assessment, so we can choose the next iteration from a shared list instead of
re-deriving priorities each time.

## Latest Iteration: Image Pipeline & VLM Correction (2026-04-06)

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
- on ordinary internal prose documents, ParserX is usually faithful but can
  still feel less polished than LlamaParse because of visible line-wrap scars
- on webpage-like or screenshot-derived content, we still need a better policy
  for deciding what page identity to keep and what UI chrome to drop

Representative takeaways:

- `pdf_text01_tables`:
  - ParserX cross-page table merging is a real strength and should be protected
  - LlamaParse keeps more structure metadata in HTML form, but is weaker for
    clean Markdown table output
- `text_table01`:
  - LlamaParse can feel smoother for plain reading
  - ParserX remains structurally accurate but still needs unwrap polish
- `deepseek`:
  - webpage-style identity and navigation need a dedicated policy
  - "keep everything" is noisy, but "strip aggressively" is also wrong
- `text_table_libreoffice`:
  - ParserX is already strong on clean office-export PDFs
  - remaining work is polish, not basic extraction

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
6. Fix receipt heading over-detection (ChapterProcessor)
7. Header/footer retention policy (first-page identity preservation)
8. text_pic02 residual warnings (low-confidence VLM + duplicates)
9. Formula recognition (FormulaProcessor)
10. Line unwrap polish (native PDF hard-wrap scars)
11. Run VLM model / prompt / routing A/B tests
12. Revisit `ChapterProcessor` fallback refinement
13. Deeper structure work (`StructureRoleAnalyzer`)

Items 1-5 are completed.

### Next Priorities

**Near-term (next 1-2 iterations):**

1. **receipt heading over-detection** (14 warnings) — ChapterProcessor
   incorrectly promotes short text like "$200.00" and "管理订阅 ›" to headings
   on non-document layouts (receipts, emails). Needs more conservative candidate
   selection or a layout-type pre-filter.

2. **Header/footer first-page identity retention** — backlog P0 #1. The clearest
   gap vs LlamaParse on finance/report documents. Design already in
   `docs/header_footer_image_policy.md`. Needs a financial/report PDF in the
   internal test set to validate.

3. **text_pic02 residual warnings** (4 warnings) — low-confidence VLM on
   screenshot images + remaining duplicate_body_text. May improve with VLM
   prompt refinements already shipped.

**Mid-term:**

4. **Formula recognition** — eval residual analysis shows clear formula
   differences (H₂SiCl₂ vs $\mathrm{H_{2}SiCl_{2}}$). VLM is the highest-value
   correction target for formulas. Could be a dedicated FormulaProcessor or
   integrated into VLM correction.

5. **Line unwrap polish** — text_table01 still has visible hard-wrap scars on
   native PDFs. Backlog P1 #16.

6. **`vlm_refine_all_ocr=true` quality evaluation** — compare on/off mode on
   the internal set to quantify the quality vs cost tradeoff and decide whether
   to change the default.

7. **VLM model / prompt A/B tests** — now that we have a stable baseline with
   0 warnings, it's a good time to compare different VLM models/prompts.

**Test data gaps:**

- Financial/report PDFs (needed for header/footer retention validation)
- Academic documents with formulas (needed for formula recognition)
- These should be added to `ground_truth/` before starting those iterations.

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

Status:
- default auto-discovery of `parserx.yaml` is now in place

Next work:
- print the resolved config path in `parserx eval` / `parserx compare`
- warn when no project config file is found and the CLI falls back to bare defaults
- add one regression test for `compare` with both config flags omitted

Why:
- avoid false baselines
- make service-enabled vs service-disabled runs obvious to the operator

### 5. Reduce VLM drift against OCR/native evidence

Current signals:
- repeated `low-confidence VLM description`
- frequent number mismatch warnings
- rendered text volume drift on public samples

Tasks:
- bias image rendering toward OCR/native text when the image is text-heavy
- prefer `visible_text` over `summary` when OCR evidence strongly overlaps
- add a VLM post-filter for numeric consistency
- suppress overly long summaries when `markdown` or `visible_text` already exists

Status:
- largely completed in the current iteration
- repeated benchmark no longer shows `image missing reference` instability
- long-text images now route through OCR overlap evidence instead of brittle VLM transcription

Remaining follow-up:
- reduce residual `number mismatch` on the DashScope/Qwen path
- verify whether `text volume drift` is still VLM-related or now mostly OCR/cleanup-side

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

Current signals:
- LlamaParse currently outperforms ParserX on useful bold emphasis, formula
  readability, and paper-like presentation in some scientific samples

Tasks:
- preserve bold emphasis when it helps semantic interpretation
- improve formula normalization toward cleaner LaTeX-style output
- prevent inline math/symbol degradation during OCR/VLM cleanup
- measure raw HTML vs Markdown tradeoffs for math-heavy content

Why:
- formatting fidelity affects perceived trust and readability, not just style

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

Tasks:
- create a checked-in shortlist from the current public set
- include representative English text, Chinese text, and table-heavy scanned samples
- use it as the default A/B benchmark set for OCR/VLM work

Why:
- full public eval is useful, but too broad for rapid iteration

### 13. Add per-warning-type evaluation summary

Tasks:
- group warnings by type in eval reports
- count `number mismatch`, `orphan heading`, `text volume drift`, etc.
- show warning deltas in compare reports

Why:
- warning counts alone are too coarse to guide iteration

### 14. Track config and model metadata in eval reports

Tasks:
- include resolved OCR engine, VLM model, LLM model, and key feature toggles
- print them in the report header

Why:
- a baseline without config metadata is hard to trust later

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

Current signals:
- internal prose samples such as `text_table01` are mostly accurate, but still
  look visibly wrapped compared with smoother LlamaParse output

Tasks:
- reduce intra-paragraph hard-wrap scars in native PDFs
- keep paragraph boundaries stable while removing line-level visual breaks
- ensure unwrap does not collapse list structure or numbered items

Why:
- this is one of the clearest remaining gaps on otherwise well-parsed internal
  documents

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

Latest repeated benchmark conclusion:

- top priority should shift from "score-only optimization" toward
  "reader-visible quality plus measurable regressions"
- the first concrete improvement slice should be:
  1. header/footer retention policy
  2. image output contract
  3. chart retention and chart-body integration
  4. semi-automatic checks for the above

If we want the highest-signal next step, do this:

1. Triage `orphan heading` by document and determine whether it is a chapter-fallback issue, a heading detector issue, or a verification threshold issue.
2. Break down `text volume drift` into OCR loss vs cleanup loss vs layout loss on the warning-heavy subset.
3. Add one more VLM numeric-consistency pass for models that still emit `number mismatch`.
4. Record resolved OCR/VLM/LLM metadata in eval report headers so repeated runs are easier to audit.
5. After that, re-evaluate whether `ChapterProcessor` fallback refinement should become the next primary quality project.

That path is more likely to move quality than spending another round only on
prompt wording.

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
  small public subset alone
