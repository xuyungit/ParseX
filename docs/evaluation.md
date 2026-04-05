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

## Recommended Workflow

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

## Immediate Next Steps

1. Create the first small `ground_truth_public/` set in-repo.
2. Add a compare workflow for `llm_fallback=false` vs `llm_fallback=true`.
3. Extend CLI/reporting so `warnings` and API call counts are easier to read.
4. Use both public and private evaluation before refining ChapterProcessor prompts.
