# Multi-Tool Markdown Evaluation

This workflow runs the same ground-truth dataset through four paths and writes
all generated Markdown into a single artifact tree for both automatic scoring
and manual review:

- `llamaparse`
- `liteparse`
- `builtin_doc_pdf`
- `parserx`

## What It Produces

For each document, ParserX now writes:

```text
reports/tool_eval_artifacts/
  <tool>/
    <doc_name>/
      output.md
      metadata.json
      error.txt              # only when a tool run fails
      raw.json               # LiteParse structured output
```

The runner also writes:

- `reports/tool_eval.md` - Markdown summary report
- `reports/tool_eval_artifacts/manifest.json` - machine-readable run manifest

## Setup

Python dependencies:

```bash
uv sync
```

Node dependencies:

```bash
npm install
```

LlamaParse requires:

```bash
LLAMA_CLOUD_API_KEY=...
```

The repo already loads `.env` automatically for Python commands, and the
Node helper inherits the same environment when launched from `parserx`.

## Run

Evaluate the public sample set:

```bash
uv run parserx tool-eval ground_truth_public \
  -o reports/tool_eval.md \
  --artifacts-dir reports/tool_eval_artifacts
```

Evaluate a private dataset with the same directory layout:

```bash
uv run parserx tool-eval "$PARSERX_PRIVATE_GT_DIR" \
  -o reports/private_tool_eval.md \
  --artifacts-dir reports/private_tool_eval_artifacts
```

Limit the run to a stable subset:

```bash
uv run parserx tool-eval ground_truth_public \
  --include-list ground_truth_public/subsets/warning_heavy.txt \
  -o reports/tool_eval_warning_heavy.md \
  --artifacts-dir reports/tool_eval_warning_heavy_artifacts
```

## Scoring

The automatic scores reuse the same metrics already used by ParserX:

- normalized edit distance
- character F1
- heading F1
- table cell F1
- warning count
- wall time

Only ParserX currently reports internal API-call counters and verification
warnings. External tools still receive the same text/structure scoring, and all
four tools write their Markdown outputs side by side so you can inspect them
manually.

For manual review, use [`docs/quality_rubric.md`](quality_rubric.md).

For the current design direction on page-identity retention and image/chart
output policy, see [`docs/header_footer_image_policy.md`](header_footer_image_policy.md).
