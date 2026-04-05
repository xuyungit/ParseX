# Iteration Backlog

Updated: 2026-04-05

This file records concrete follow-up tasks after the current baseline
assessment, so we can choose the next iteration from a shared list instead of
re-deriving priorities each time.

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
- The current LLM/OCR/VLM stack is now testable end-to-end, which means future
  work should be judged against real online-service baselines.
- It is worth explicitly recording iteration decisions in-repo so that future
  work follows a stable sequence.

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

1. Stabilize and expose the benchmark workflow
2. Reduce VLM drift
3. Run VLM model / prompt / routing A/B tests
4. Add OCR-first routing for text-heavy images
5. Revisit `ChapterProcessor` fallback refinement
6. Move to deeper structure work such as `StructureRoleAnalyzer`

This ordering reflects current evidence: VLM output quality is the largest
measured regression surface, while chapter fallback is still a secondary,
experimental enhancement.

## P0: Must Fix

### 1. Make project-config loading explicit in CLI

Status:
- default auto-discovery of `parserx.yaml` is now in place

Next work:
- print the resolved config path in `parserx eval` / `parserx compare`
- warn when no project config file is found and the CLI falls back to bare defaults
- add one regression test for `compare` with both config flags omitted

Why:
- avoid false baselines
- make service-enabled vs service-disabled runs obvious to the operator

### 2. Reduce VLM drift against OCR/native evidence

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

### 3. Re-evaluate default-on LLM chapter fallback

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

### 4. VLM model A/B compare

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

### 5. Prompt-style A/B compare at small scale

Tasks:
- compare `strict_bilingual`, `strict_zh`, `strict_en`
- measure whether the prompt language should match document language
- check whether bilingual prompts improve stability or just add verbosity

Why:
- we now have the config hooks to test this cheaply

### 6. OCR-first routing for image descriptions

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

## P1: Quality and Evaluation Infrastructure

### 7. Build a stable public warning-heavy subset

Tasks:
- create a checked-in shortlist from the current public set
- include representative English text, Chinese text, and table-heavy scanned samples
- use it as the default A/B benchmark set for OCR/VLM work

Why:
- full public eval is useful, but too broad for rapid iteration

### 8. Add per-warning-type evaluation summary

Tasks:
- group warnings by type in eval reports
- count `number mismatch`, `orphan heading`, `text volume drift`, etc.
- show warning deltas in compare reports

Why:
- warning counts alone are too coarse to guide iteration

### 9. Track config and model metadata in eval reports

Tasks:
- include resolved OCR engine, VLM model, LLM model, and key feature toggles
- print them in the report header

Why:
- a baseline without config metadata is hard to trust later

## P2: Reliability / Production Hardening

### 10. Add degraded-service integration tests

Tasks:
- simulate OCR timeout
- simulate VLM malformed JSON
- simulate partial VLM failures in multi-image documents
- verify retries, warnings, and graceful degradation

Why:
- we currently have a success-path baseline, not a failure-path baseline

### 11. Improve compare visibility for unmatched documents

Status:
- log warnings are now emitted

Next work:
- surface unmatched docs in the compare report body
- distinguish parse failure vs missing ground truth vs filtered-out sample

Why:
- compare should help us spot regressions in coverage, not just shared successes

### 12. Separate API-call semantics more cleanly

Tasks:
- standardize where request counts live
- avoid dual-source counting patterns where possible
- add explicit metrics for `llm_requests`, `llm_fallback_hits`, `vlm_requests`

Why:
- cost accounting needs one authoritative source

## Suggested Next Iteration

Latest repeated benchmark conclusion:

- `unstructured output` is no longer the dominant instability
- `qwen-dashscope` currently leads on text quality, but still carries `number mismatch`
- all three configs are now dominated by `orphan heading` and residual `text volume drift`

If we want the highest-signal next step, do this:

1. Triage `orphan heading` by document and determine whether it is a chapter-fallback issue, a heading detector issue, or a verification threshold issue.
2. Break down `text volume drift` into OCR loss vs cleanup loss vs layout loss on the warning-heavy subset.
3. Add one more VLM numeric-consistency pass for models that still emit `number mismatch`.
4. Record resolved OCR/VLM/LLM metadata in eval report headers so repeated runs are easier to audit.
5. After that, re-evaluate whether `ChapterProcessor` fallback refinement should become the next primary quality project.

That path is more likely to move quality than spending another round only on
prompt wording.
