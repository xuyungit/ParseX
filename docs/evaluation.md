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

## What To Measure (metric version 2.1, 2026-09-24; 2.0 on 2026-09-23)

Metric definitions changed on 2026-09-23 to close the five counterexamples in
`docs/redesign_guide.md` §9.2 (tests: `tests/test_eval_counterexamples.py`).
Every result record carries `metric_version`; records with different versions
are never compared, and baselines computed before 2.0 are not reused.

Reports always follow this order (guide §9.3):

1. **Hard checks** — failed documents, not-executed documents (requested but
   missing input / `expected.md`), missing tables, extra tables.
2. **Table structure** (`parserx/eval/tables.py`) — GFM and HTML tables are
   both parsed into `TableGrid` (`parserx/tables/`). Tables are paired by
   cell-content similarity (pairs below 0.2 Dice count as one missing plus one
   extra). Unpaired tables stay in the denominators. Within a pair, rows and
   columns are aligned by dynamic programming (as in GriTS), so one inserted
   row only costs that row. `cell_f1` counts a cell as correct when an output
   cell sits at the aligned position with the same rowspan, colspan and
   normalized content. Also reported: header association (data cell under the
   same header path, punctuation-insensitive, so `A > B` equals a two-row
   header `A` / `B`) and merged-cell accuracy. Not applicable (`—`) when
   neither side has a table. **2.1**: an annotated table without any merged
   cell (every GFM table) cannot express merges, and annotators either repeat a
   merged value in each row or write it once with blanks around it; against
   such a table an output cell matches an annotated cell at any position it
   covers, spans are not compared, recall counts annotated cells and precision
   counts distinct output cells. Annotations with spans keep the 2.0 rule.
3. **Text** (`parserx/eval/normalize.py`) — both sides are canonicalized:
   HTML comments removed; tables replaced by row-major cell text (table format
   never changes text scores; 2.1: a cell repeating the one directly above or
   to its left is written once, so merged values count once in every
   notation); image placeholders removed and counted
   (`![…](…)`, `> [图片] …`, and a blockquote directly after an image line —
   descriptions are scored separately later, §9.3); NFKC; heading markers and
   all whitespace dropped. `char_f1` is LCS-based (order-aware);
   `char_bag_f1` keeps the old order-blind character-frequency F1 as a
   diagnostic; `edit_distance` is exact Levenshtein / max length (rapidfuzz,
   no chunking).
4. **Reading order** (`parserx/eval/order.py`) — expected text is split into
   blocks on blank lines; each block is located in the output by 8-character
   anchors unique on both sides (median position). Reported: Kendall tau
   (1 − 2 · pairwise inversion rate), inversions, and coverage of locatable
   blocks.
5. **Headings** — unchanged matching (normalized text + identical level);
   `f1` is `—` when neither side has headings.
6. **Key-content errors** (`parserx/eval/key_content.py`) — numbers, numbers
   with units, negation words and dates (spellings normalized) are extracted
   in order; token sequences are aligned by LCS and unaligned tokens are
   counted as missing / extra per kind.
7. **Real requests** — recorded at the service boundary by
   `parserx/scheduling/meter.py` (one batch OCR job is one request; OCR pages
   and network attempts reported separately), no longer inferred from elements.
8. **Wall time**.
9. **Cost** — `—` until token usage is recorded (Phase 1).

Averages ignore documents where a metric is not applicable.

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

### Regression test (recommended first step)

```bash
# Specific docs, scores reported (no baseline):
uv run python scripts/regression_test.py --include text_table01 deepseek

# Save this run's record, and compare a later run against it:
uv run python scripts/regression_test.py --include deepseek --json-out /tmp/run.json
uv run python scripts/regression_test.py --include deepseek --baseline /tmp/run.json

# Markdown report (L2 reports: eval_reports/<date>_<phase>_<topic>.md):
uv run python scripts/regression_test.py --report eval_reports/2026-09-23_p0-1_metric_fix.md
```

The default config is `configs/regression.yaml`: `parserx.yaml` with every
LLM call turned off (quality check, heading fallback, line-unwrap and
content-value fallbacks), so results depend only on OCR/VLM responses. The
report header and the run record carry a config fingerprint (sha256 of the
resolved config without credentials); comparing against a baseline with a
different fingerprint prints a note.

**Response cache (P0-3).** Every OCR / VLM / LLM response goes through
`parserx/scheduling/gateway.py` and is recorded under `.parserx_cache/raw/`
(`cache:` in `parserx.yaml`; not in git). The key covers the full request
semantics (service, endpoint host/path, model and generation settings, prompt
texts, the bytes of every file sent, the JSON schema) and never credentials or
temp-file paths. Modes: `read_write` (default in `parserx.yaml`; the schema
default is `off`), `read_only` (offline replay: a miss raises, the document is
reported as *not executed* rather than scored on degraded output), `refresh`
(always request and record), `off`.

```bash
# Record, then replay offline and save outputs for comparison:
uv run python scripts/regression_test.py --include receipt --cache-mode read_write --outputs-dir /tmp/run1
uv run python scripts/regression_test.py --include receipt --cache-mode read_only --outputs-dir /tmp/run2
# Debug unstable keys: log the key material of every miss
PARSERX_CACHE_DEBUG=1 uv run python scripts/regression_test.py --include receipt
```

Run records carry `output_sha256` per document. Cache settings are excluded
from the config fingerprint (replay does not change processing).

Exit codes: `0` pass, `1` a score regressed beyond `--tolerance` (default
0.005; key-content error counts use 0) against `--baseline`, `2` a hard check
failed. Hard checks: any failed or not-executed document (absolute); missing
or extra tables above the baseline (relative; without a baseline they are only
recorded, so a first freeze is never blocked); a baseline with a different
`metric_version`.

**Tiers (P0-4).** L1: `uv run python scripts/regression_test.py --core
--repeat 2` replays `configs/regression_core.txt` offline (cache `read_only`),
runs it twice in one process and fails (exit 2) if any output differs; a cache
miss marks the document *not executed* and suggests `--allow-calls`
(`read_write`). `--list FILE` runs any document list; `--gt-dir` is
repeatable (L2: `--gt-dir ground_truth --gt-dir ground_truth_public`).
`parserx eval` accepts `--cache-mode`.

**Frozen runs (P0-5).** Phase acceptance uses a frozen run, not
`best_scores.json`:

```bash
uv run python scripts/regression_test.py --gt-dir ground_truth --gt-dir ground_truth_public \
    --freeze p0_v1_gpt-6-luna          # → eval_runs/<date>_p0_v1_gpt-6-luna/
uv run python scripts/regression_test.py --replay eval_runs/<date>_p0_v1_gpt-6-luna
uv run python scripts/regression_test.py --core --baseline eval_runs/<date>_p0_v1_gpt-6-luna
```

`--freeze` records fresh responses into the run's own `cache/`, and writes
`manifest.json` (commit and working-tree state, redacted config and
fingerprint, metric/cache schema versions, service identities, per-document
input/expected hashes and isolation flag), `metrics.json`, `outputs/` and
`report.md`. It is built under `eval_runs/.<id>.partial/` and renamed only
when no document failed or went unexecuted; a refused attempt keeps its
responses, so rerunning resumes from them. `--replay` re-runs the frozen
documents offline from that cache and fails (exit 2) unless every output
hash and score matches (timings, request and cache counters excepted).
Frozen runs never go into git (guide §14 Q15). Isolation-set documents
(`configs/isolation_set.txt`) are reported in a separate section.

`--update-baseline` was removed: baselines are frozen runs outside
`ground_truth/` (Phase 0 P0-5), and `best_scores.json` is read only by the
deprecated `--deterministic-only` selector (use `--core`).

### Local iteration checklist

After each non-trivial parsing change:

1. Run regression test (offline)
```bash
uv run python scripts/regression_test.py --gt-dir ground_truth --deterministic-only
```

2. Run unit/integration tests
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

3. Run public evaluation
```bash
uv run parserx eval ground_truth_public -o reports/public_eval.md
```

4. Run private evaluation
```bash
uv run parserx eval "$PARSERX_PRIVATE_GT_DIR" -o reports/private_eval.md
```

5. Compare:
- heading F1
- edit distance / char F1
- warning count
- API calls
- wall time
- semi-automatic product-quality warnings where available
- human rubric notes on representative docs

For ParserX, parser changes should not be treated as fully validated until:
- the offline regression test passes
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

## Current State (2026-04-09)

The codebase has:
- `parserx.eval.metrics` — edit distance, char F1, heading F1, table cell F1
- `parserx.eval.runner` — full eval runner with per-document reporting
- `parserx.eval.benchmark` — OmniDocBench public benchmark download
- `parserx.eval.warnings` — 23 categorized warning types with `summarize_warning_types()`
- `parserx.eval.reporting` — config/model metadata in report headers
- `parserx.eval.compare` — A/B comparison reporting
- `parserx compare` CLI with `--set-a`/`--set-b` config overrides
- `ground_truth_public/` with 10 docs + `subsets/warning_heavy.txt`
- `ground_truth/` with 7 internal docs
- `ProductQualityChecker` with 4 semi-automatic checks
- `scripts/regression_test.py` — regression test against best-known baselines
- `ground_truth/best_scores.json` + `ground_truth_public/best_scores.json` — per-document best-known scores with `requires_services` classification

What is still missing operationally:
- semi-automatic checks for document identity retention (first-page title/org/date)
- chart-specific asset-linkage checks
- richer public datasets (financial/report PDFs, academic docs with formulas)
- repeated-page-identity over-retention warnings

## Full-Cycle Evaluation Playbook

This section documents the complete end-to-end evaluation workflow, including
multi-VLM comparison and cross-tool (LlamaParse) comparison. Run this after
any significant iteration to get a holistic view of quality.

### Prerequisites

```bash
# Python dependencies
uv pip install 'parserx[bench]'

# Node dependencies (for LlamaParse)
npm install

# Environment variables required:
#   OPENAI_BASE_URL, OPENAI_API_KEY, VLM_MODEL    — default VLM (config A)
#   OPENAI_BASE_URL_B, OPENAI_API_KEY_B, VLM_MODEL_B — VLM config B
#   OPENAI_BASE_URL_C, OPENAI_API_KEY_C, VLM_MODEL_C — VLM config C
#   PADDLE_OCR_ENDPOINT, PADDLE_OCR_TOKEN          — PaddleOCR
#   LLAMA_CLOUD_API_KEY                             — LlamaParse
```

### Step 1: Run ParserX eval on both test sets

```bash
# Public test set (9-10 docs, OmniDocBench + smoke samples)
uv run parserx eval ground_truth_public \
  -o reports/full_eval_public.md

# Private test set (7 docs, internal business documents)
uv run parserx eval ground_truth \
  -o reports/full_eval_internal.md
```

### Step 2: Multi-VLM A/B comparison

Three VLM configurations are available:

| Config | File | Env Vars | Notes |
|--------|------|----------|-------|
| A (default) | `parserx.yaml` | `OPENAI_BASE_URL`, `VLM_MODEL` | Default model |
| B | `configs/vlm_b.yaml` | `OPENAI_BASE_URL_B`, `VLM_MODEL_B` | Alternative model |
| C | `configs/vlm_c.yaml` | `OPENAI_BASE_URL_C`, `VLM_MODEL_C` | Alternative model (responses API) |

Run pairwise comparisons:

```bash
# A vs B
uv run parserx compare ground_truth_public \
  --config-a parserx.yaml \
  --config-b configs/vlm_b.yaml \
  --label-a "vlm-a" --label-b "vlm-b" \
  -o reports/compare_vlm_a_vs_b_public.md

uv run parserx compare ground_truth \
  --config-a parserx.yaml \
  --config-b configs/vlm_b.yaml \
  --label-a "vlm-a" --label-b "vlm-b" \
  -o reports/compare_vlm_a_vs_b_internal.md

# A vs C
uv run parserx compare ground_truth_public \
  --config-a parserx.yaml \
  --config-b configs/vlm_c.yaml \
  --label-a "vlm-a" --label-b "vlm-c" \
  -o reports/compare_vlm_a_vs_c_public.md

uv run parserx compare ground_truth \
  --config-a parserx.yaml \
  --config-b configs/vlm_c.yaml \
  --label-a "vlm-a" --label-b "vlm-c" \
  -o reports/compare_vlm_a_vs_c_internal.md
```

To narrow to the warning-heavy subset:

```bash
uv run parserx compare ground_truth_public \
  --include-list ground_truth_public/subsets/warning_heavy.txt \
  --config-a parserx.yaml \
  --config-b configs/vlm_b.yaml \
  --label-a "vlm-a" --label-b "vlm-b" \
  -o reports/compare_vlm_a_vs_b_warning_heavy.md
```

### Step 3: Cross-tool comparison and human review

Superseded: see [tool_eval.md](tool_eval.md) (`parserx dev tool-eval run | score | view`) and
[v2_benchmark_plan.md](v2_benchmark_plan.md).

### Quick reference: all evaluation commands

```bash
# ── L1 core regression (offline replay, twice) ──
uv run python scripts/regression_test.py --core --repeat 2
uv run python scripts/regression_test.py --core --allow-calls   # record missing responses

# ── Regression test (full, needs services) ──
uv run python scripts/regression_test.py --gt-dir ground_truth

# ── Compare against a saved run record (replaces --update-baseline) ──
uv run python scripts/regression_test.py --gt-dir ground_truth --json-out /tmp/run.json
uv run python scripts/regression_test.py --gt-dir ground_truth --baseline /tmp/run.json

# ── Single-config eval ──
uv run parserx eval <gt_dir> -o <report.md>

# ── A/B config compare ──
uv run parserx compare <gt_dir> \
  --config-a <a.yaml> --config-b <b.yaml> \
  --label-a <name> --label-b <name> \
  -o <report.md>

# ── External tools vs ParserX (docs/tool_eval.md) ──
uv run parserx dev tool-eval run --docs-file configs/bench_round1.txt
uv run parserx dev tool-eval view

# ── Feature toggle experiment ──
uv run parserx compare <gt_dir> \
  --set-a processors.image.vlm_refine_all_ocr=false \
  --set-b processors.image.vlm_refine_all_ocr=true \
  --label-a "ocr-only" --label-b "vlm-refine" \
  -o <report.md>
```

## Remaining Evaluation Improvements

1. Add first-page identity retention check to `ProductQualityChecker`.
2. Add financial/report PDFs and academic formula docs to ground truth sets.
3. Add chart-specific asset-linkage check (depends on chart extraction work).
4. Consider LLM-as-judge evaluation for reading quality beyond text fidelity.
