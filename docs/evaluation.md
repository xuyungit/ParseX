# Evaluation Guide

ParserX now has a usable parsing pipeline. The next stage of iteration is
not "add more modules first", but "measure every meaningful change on a
stable evaluation set".

This document defines the evaluation strategy we want to use going forward.

## Goals

- Make regressions visible after each parsing change.
- Evaluate both open, reproducible documents and real internal documents.
- Compare quality gains against added cost, latency, and API calls.
- Support A/B testing for changes such as ChapterProcessor LLM fallback.
- Track not only fidelity metrics, but also product-quality signals that affect
  human readers directly.

## Dataset Strategy

We use two evaluation tracks in parallel.

### 1. Public Ground Truth

Purpose:
- reproducible results
- shareable benchmark inputs
- CI-friendly regression checks

Recommended location:
```text
ground_truth_public/
  doc_name/
    input.pdf
    expected.md
    meta.json
```

Recommended sources:
- OmniDocBench subset prepared by `parserx.eval.benchmark`
- public government notices
- public standards/specifications
- public technical manuals with tables and mixed layouts

### 2. Private Ground Truth

Purpose:
- validate behavior on real business documents
- catch issues public datasets do not cover
- assess practical ROI of LLM/OCR/VLM changes

Recommended location:
- outside the repository

Recommended convention:
```text
$PARSERX_PRIVATE_GT_DIR/
  doc_name/
    input.pdf
    expected.md
    meta.json
```

The directory structure should match `ground_truth_public/` so the same
evaluation runner can be reused.

## What To Measure

ParserX already computes:
- text edit distance
- character F1
- heading precision / recall / F1
- table cell F1
- wall time
- warning count
- API calls (`ocr` / `vlm` / `llm`)
- per-document `llm_fallback_hits`

For model-assisted features, we should also track:
- warning count
- `api_calls.llm`
- `api_calls.vlm`
- `api_calls.ocr`
- per-document count of `llm_fallback_used`

## Layered Evaluation Model

We should evaluate ParserX in three layers, not one.

### 1. Automatic Core Fidelity Metrics

These remain the default regression metrics:

- edit distance
- character F1
- heading precision / recall / F1
- table cell F1
- latency
- warning count
- API-call counts

These metrics are still critical, but they are not enough to judge whether the
Markdown is actually pleasant and useful to read.

### 2. Semi-Automatic Product-Quality Checks

The following quality checks should be added to the framework whenever
possible, so they do not remain purely subjective forever:

- first-page identity retention:
  - title
  - organization / broker / issuer
  - report date
  - recommendation or analyst block when present
- duplicate-body detection:
  - repeated paragraphs caused by OCR + image text overlap
- image placeholder quality:
  - leaked internal strings such as `Text content preserved in OCR body text.`
- HTML leakage in Markdown-first outputs:
  - count `<table>` or other raw HTML blocks
- chart retention:
  - chart title preserved
  - linked image asset exists
  - optional chart-derived table or summary exists
- image asset linkage:
  - Markdown image reference exists but file missing
  - file exists but is never referenced
- rough reading-order sanity:
  - title or identity block should not disappear entirely
  - large body blocks should not precede the title page metadata in obvious cases

These checks should produce warnings or scored hints, not absolute truth.

### 3. Human Review

Human review should focus only on what automation cannot yet judge reliably:

- whether repeated headers are useful metadata or just clutter
- whether preserving a chart image adds value or only redundancy
- whether section ordering "feels right" in complex layouts
- whether formatting loss is acceptable for the target use case

See [`docs/quality_rubric.md`](quality_rubric.md) for the quality dimensions
and definitions we want human reviewers and future heuristics to share.

## Recommended Workflow

For cross-tool Markdown comparison that writes side-by-side artifacts for
manual review, see [`docs/tool_eval.md`](tool_eval.md).

When reviewing `tool-eval` outputs, use [`docs/quality_rubric.md`](quality_rubric.md)
instead of relying only on aggregate scores.

### Public benchmark setup

```bash
uv pip install 'parserx[bench]'
uv run python -m parserx.eval.benchmark --output-dir ground_truth_public
uv run parserx eval ground_truth_public -o reports/public_eval.md
```

For a fast in-repo smoke run, a tiny checked-in sample set also lives in
`ground_truth_public/`.

For OCR/VLM iteration, use the checked-in warning-heavy slice:

```bash
uv run parserx eval ground_truth_public \
  --include-list ground_truth_public/subsets/warning_heavy.txt \
  -o reports/public_eval_warning_heavy.md
```

### Private benchmark run

```bash
uv run parserx eval "$PARSERX_PRIVATE_GT_DIR" -o reports/private_eval.md
```

### Local iteration checklist

After each non-trivial parsing change:

1. Run unit/integration tests
```bash
uv run pytest tests/ -q
```

When `.env` contains live OCR/LLM/VLM credentials, that command also runs the
real end-to-end suite in `tests/test_live_e2e.py`. Those tests make actual
network calls and cover:
- scanned PDF -> online OCR
- informational image -> VLM description
- weak heading candidate -> LLM fallback

To run only the live suite:

```bash
uv run pytest tests/test_live_e2e.py -q
```

2. Run public evaluation
```bash
uv run parserx eval ground_truth_public -o reports/public_eval.md
```

3. Run private evaluation
```bash
uv run parserx eval "$PARSERX_PRIVATE_GT_DIR" -o reports/private_eval.md
```

4. Compare:
- heading F1
- edit distance / char F1
- warning count
- API calls
- wall time
- semi-automatic product-quality warnings where available
- human rubric notes on representative docs

For ParserX, parser changes should not be treated as fully validated until:
- the offline regression suite passes
- the live E2E suite passes with services configured in `.env`

### A/B compare workflow

```bash
uv run parserx compare ground_truth_public \
  --label-a no-fallback \
  --label-b fallback \
  --set-a processors.chapter.llm_fallback=false \
  --set-b processors.chapter.llm_fallback=true
```

`parserx eval` and `parserx compare` both support repeatable
`--set dotted.path=value` overrides, which is useful for quick feature
toggle experiments without creating extra YAML files.

Useful VLM ablations:

```bash
# Prompt style compare
uv run parserx compare ground_truth_public/some_doc \
  --config-a parserx.yaml \
  --config-b parserx.yaml \
  --label-a auto-json \
  --label-b en-json \
  --set-a processors.image.vlm_prompt_style=strict_auto \
  --set-a processors.image.vlm_response_format=json \
  --set-b processors.image.vlm_prompt_style=strict_en \
  --set-b processors.image.vlm_response_format=json

# Model compare
uv run parserx compare ground_truth_public/some_doc \
  --config-a parserx.yaml \
  --config-b configs/vlm_model_b.yaml \
  --label-a model-a \
  --label-b model-b \
  --set-a services.vlm.model=your-model-a
```

For alternate models, prefer a tiny overlay config instead of copying the full
main config. ParserX now supports `extends` in YAML:

```yaml
# configs/vlm_model_b.yaml
extends: ../parserx.yaml

services:
  vlm:
    endpoint: ${OTHER_OPENAI_BASE_URL:${OPENAI_BASE_URL}}
    api_key: ${OTHER_OPENAI_API_KEY:${OPENAI_API_KEY}}
    model: your-model-b
```

Then compare with:

```bash
uv run parserx compare ground_truth_public \
  --include-list ground_truth_public/subsets/warning_heavy.txt \
  --config-a parserx.yaml \
  --config-b configs/vlm_model_b.yaml \
  --label-a current-model \
  --label-b alt-model
```

The same A/B runs can be narrowed to the stable warning-heavy slice:

```bash
uv run parserx compare ground_truth_public \
  --include-list ground_truth_public/subsets/warning_heavy.txt \
  --label-a baseline \
  --label-b experiment \
  --set-a processors.image.vlm_prompt_style=strict_auto \
  --set-b processors.image.vlm_prompt_style=strict_en
```

## Current Gap

The codebase already has:
- `parserx.eval.metrics`
- `parserx.eval.runner`
- `parserx.eval.benchmark`

What is still missing operationally:
- a documented private dataset path convention in daily use
- richer public datasets beyond the initial smoke subset
- semi-automatic checks for document identity retention, image policy, and
  Markdown usability

## Immediate Next Steps

1. Create the first small `ground_truth_public/` set in-repo.
2. Add a compare workflow for `llm_fallback=false` vs `llm_fallback=true`.
3. Extend CLI/reporting so `warnings` and API call counts are easier to read.
4. Use both public and private evaluation before refining ChapterProcessor prompts.
