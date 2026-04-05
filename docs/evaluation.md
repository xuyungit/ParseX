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

## Current Gap

The codebase already has:
- `parserx.eval.metrics`
- `parserx.eval.runner`
- `parserx.eval.benchmark`

What is still missing operationally:
- a checked-in `ground_truth_public/` subset
- a documented private dataset path convention in daily use
- an A/B compare command for config or feature toggles

## Immediate Next Steps

1. Create the first small `ground_truth_public/` set in-repo.
2. Add a compare workflow for `llm_fallback=false` vs `llm_fallback=true`.
3. Extend CLI/reporting so `warnings` and API call counts are easier to read.
4. Use both public and private evaluation before refining ChapterProcessor prompts.
