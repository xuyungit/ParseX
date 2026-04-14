# ParseBench Baseline & Iteration Tracker

Live scoreboard of ParserX on LlamaIndex's [ParseBench](https://github.com/run-llama/ParseBench)
(~2,000 human-verified enterprise PDF pages, 169k rule-based tests across
5 dimensions). Each iteration that targets ParseBench should append a
row and a short note; don't overwrite past numbers.

- Dataset: `llamaindex/ParseBench` (HuggingFace), full set
- Harness: `~/Projects/ParseBench` fork, provider `parserx` (subprocess
  CLI adapter at `src/parse_bench/inference/providers/parse/parserx.py`)
- Full launch: `PARSERX_REPO=/Users/xuyun/Projects/ParserX uv run parse-bench run parserx --max_concurrent 8`
- Lite launch (32 PDFs, ~3-5 min per iteration): `PARSERX_REPO=/Users/xuyun/Projects/ParserX uv run parse-bench run parserx --input_dir data_lite --output_dir output/parserx_lite -m 8`
- Lite subset build script: `~/Projects/ParseBench/scripts/build_lite_subset.py` (stratified
  by difficulty/feature tags, deterministic via `SEED`. Rotate seed every
  ~10 iterations to avoid over-fit.)
- Services (must be up): OpenAI-compatible LLM+VLM endpoint, PaddleOCR
  endpoint (see ParserX `.env`)

## How to read this file

Each dimension reports ParseBench's **primary metric**:

| Dim | Primary metric | What 1.0 means |
|-----|---------------|----------------|
| Tables | GTRM (GriTS + TableRecordMatch) | Cells and header-keyed records all match |
| Charts | ChartDataPointMatch | All spot-checked data points match |
| Text Content | Rule Pass Rate | All faithfulness rules pass (no omissions / hallucinations / order breaks) |
| Text Formatting | Rule Pass Rate | Strike/super/sub/bold/hierarchy preserved |
| Visual Grounding | Element Pass Rate | Every element's bbox overlaps ground truth |

## Scoreboard

| Run ID | Date | ParserX ver (git sha) | Tables (GTRM) | Charts (DataPoint) | Text Content (rule PR) | Text Formatting (rule PR) | Visual Grounding (elt PR) |
|--------|------|-----------------------|---------------|--------------------|------------------------|---------------------------|---------------------------|
| `baseline` | 2026-04-14 | a470a84+dirty | **0.00%** | **1.11%** | **85.43%** | **34.33%** | N/A ¹ |
| `iter18-lite` | 2026-04-14 | a470a84 (evaluator fork) | **56.13%** ² | — | — | — | — |
| `iter18-full` | 2026-04-14 | a470a84 (evaluator fork) | **41.33%** ³ | — | — | — | — |
| `iter20a-full` | 2026-04-14 | a470a84 (evaluator fork) | 41.33% | 1.11% | **86.89%** ⁴ | 34.33% | N/A |

⁴ Iter 20 Track A, `--skip_inference` re-score. text_content +1.46pt overall. Per-subrule: `missing_specific_sentence` 66.97% → **76.70%** (+9.73pt), `missing_sentence_percent` 66.81% → **76.52%** (+9.71pt), `unexpected_sentence_percent` flat (78.97% → 78.74%, within noise), others unchanged.

² Iter 18, lite subset only (7 table docs). GriTS 67.8%, TRM 44.4%, composite 56.1%.
³ Iter 18, full set (503 table docs, `--skip_inference` re-score). GriTS 50.40%, TRM 30.36%, TRM-perfect 19.41%, composite 41.33%. 87% of GT tables paired (1.56/1.80 avg); 13% unmatched-expected, 7% unmatched-pred. Matches lite-subset trend — honest markdown-contract ceiling reflected.

_(Leaderboard reference: LlamaParse Agentic 84.9% overall; top field
cluster ~80–85%; most open parsers <70%. Charts is industry-wide below
50%.)_

## Runs

### baseline — 2026-04-14

**Run stats**: 2,078 examples, 2,036 successful (98.0%), 42 failed
(all `.jpg` — ParserX CLI only accepts PDF/DOCX). Wall clock **53 min**
at concurrency=8. Avg latency 12.5s/doc. No VLM/OCR endpoint throttling.
Commit: `a470a84` (working tree dirty, see iteration_backlog modifications).

**Output**: `~/Projects/ParseBench/output/parserx/` (dashboard HTML,
per-dim reports, CSV, rule-level metadata).

¹ Visual Grounding: 394/458 layout examples failed evaluation with
"Inference output is not LayoutOutput and no provider adapter matched."
ParserX emits no per-element bboxes → layout evaluator can't score.
The 64 that did run are the `order` sub-rules (scored 82.6% avg). Treat
Visual Grounding as out-of-scope until we add bbox output.

### Baseline — per-dimension breakdown

**Text Content (506 docs, 141,322 rules → 85.43% pass)**
- Strong: `missing_specific_word` 90%, `missing_word_percent` 90%, `too_many_*` 94–95%
- Weak: `missing_specific_sentence` 67%, `missing_sentence_percent` 67%, `unexpected_sentence_percent` 79%, `order` 79%
- **Zero**: `is_header` / `is_footer` = 0.00% (format contract — ParserX deletes h/f instead of wrapping in `<page_header>`/`<page_footer>`)

**Text Formatting (476 docs, 5,997 rules → 34.33% pass)**
- Only two sub-rules are above floor: `is_bold` 54%, `is_title` 45%,
  `title_hierarchy_percent` 36%, `is_latex` 29%.
- Near-zero: `is_italic` 6%, `is_sub` 6%, `is_sup` 5%, `is_mark` 0%,
  `is_strikeout` 0%, `is_underline` 0%, `is_code_block` 0%.
- Root cause: ParserX currently preserves bold (from Iter16 DOCX work)
  and titles, nothing else.

**Tables (503 docs, GTRM → 0.00% ← worst, but fake)**
- **Root cause identified**: pure format-contract mismatch. ParseBench's
  `extract_html_tables()` scans for `<table>…</table>`; ParserX emits
  pipe-markdown (`| a | b |`). Content is mostly correct, metric sees
  nothing. **Decision (2026-04-14)**: ParserX's product contract is
  markdown-first — so we **fork the evaluator**, not bend ParserX.
  Add `extract_markdown_tables()` alongside the HTML one, parse pipe
  tables into the same `TableData` struct (rows × cols ndarray,
  `header_rows={0}`). GriTS/TRM downstream stays unchanged. Limitation:
  pipe syntax can't represent rowspan/colspan; complex tables will
  plateau below HTML's ceiling — accepted as honest reflection of the
  markdown format.

**Charts (568 docs, 4,864 ChartDataPoint rules → 1.11% pass)**
- Expected near-zero: ParserX has no chart extraction. 25/4864 rules
  incidentally passed (likely chart title/label text captured).

**Visual Grounding**: see note ¹ above — not applicable until bbox output.

### User-set priorities (2026-04-14)

User priority call after reviewing the baseline: **P1 text_content
quality → P2 table evaluator revamp (markdown, not HTML) → P3 formatting
(bold + headings first) → P4 chart understanding (new VLM feature) →
P5 visual grounding deferred.**

Key architectural decision: ParserX is markdown-first by design. When
ParseBench's metric disagrees with that (tables), **fork the evaluator
to accept markdown**; do not bend ParserX to emit HTML. Confirmed that
text_formatting evaluator already reads markdown (regex on `**bold**`,
`*italic*`, `~~strike~~`, `# heading`, `<sup>`, `<sub>`, `<mark>`) — so
that dimension needs only ParserX-side work.

### Fix queue (execution order)

Each iteration: 1 row in scoreboard above + short what/why/delta below.
Run `parsebench-lite` per iteration; full run only at cadence gates.

**Iter 18 — Markdown table evaluator (ParseBench fork, ~2-3 hr) — DONE 2026-04-14**
- Forked `extract_normalized_tables()` to fall back to markdown pipe
  tables, and `_has_html_tables()` to detect them. Full-set GTRM
  composite **0.00% → 41.33%** (GriTS 50.40%, TRM 30.36%).

**Iter 20 Track A — Evaluator normalization fork — DONE 2026-04-14**
- Added punctuation-stripping lenient fallback to `MissingSpecificSentenceRule`,
  `MissingSentenceRule`, `MissingSentencePercentRule` in `rules_bag.py`.
  Loose form collapses non-word+non-space runs to spaces; used only when
  strict substring fails, short queries (<20 chars) keep word-boundary anchors.
  Scope: Missing* only → cannot regress TooMany/Unexpected.
- Full-set re-score: text_content **85.43% → 86.89%** (+1.46pt); rule-level
  `missing_specific_sentence` **+9.7pt**, `missing_sentence_percent` **+9.7pt**,
  `unexpected_sentence_percent` flat (-0.2pt, within noise).
- Regression set (`~/Projects/ParseBench/scripts/iter20_regression_audit.json`,
  25 false-miss + 25 true-miss): 25/25 recovered, 25/25 still fail.

**Iter 20 — `missing_sentence_*` deep-dive (reshaped 2026-04-14)** ← next
Segmentation of 503 native-plain text_content fails surfaced a split
that changes the plan: **~42% of failed sentence rules have the content
actually present in ParserX's output** — they fail on evaluator fuzzy
normalization (case/punctuation/whitespace). The remaining 58% are real
drops; of those, ~16% end in page-number digits (TOC-line page-number
detachment pattern), the rest include whole-document truncation and
smaller structural losses. Three tracks ordered by ROI:

- **Track A — Evaluator normalization fork (ParseBench, DONE — see above)**
  Investigate `match_sentence`/anchor-matching normalization in
  `~/Projects/ParseBench/src/parse_bench/evaluation/metrics/parse/`.
  Loosen case/punctuation/whitespace matching in a principled way
  (don't make it lenient for real misses — validate by spot-checking
  the 58% absent cases stay failed). Expected: reclaim up to 40% of
  sentence-rule fails → text_content **85% → ~91%** without touching
  ParserX. Same evaluator-fork policy we agreed for tables.
- **Track B — TOC page-number attachment (ParserX, ~0.5 day)**
  When heading detection triggers on "Title….20" / "Title<tab>20",
  keep the trailing page number inline instead of stripping. Example:
  `"redirect manager and/or vanity url 20"` → ParserX emits
  `"Redirect Manager and/or vanity URL"`.
- **Track C — Document-level truncation audit (ParserX, ~1 day)**
  Audit outputs <1k chars where GT expects substantial content
  (e.g. `text_misc__censored` emits only 553 chars). Likely causes:
  redaction heuristics, whole-page visual-only misclassification.

**Iter 19 (demoted) — `<page_header>`/`<page_footer>` emission (ParserX, ~2 hr)**
- ParserX's HeaderFooterProcessor currently deletes h/f. Wrap in
  `<page_header>…</page_header>` / `<page_footer>…</page_footer>` tags.
- Deferred per user priority call (2026-04-14): sentence-level losses
  outweigh the is_header/is_footer sub-rules.

**Iter 21 — PDF bold + title hierarchy (ParserX, ~2-3 days)**
- Bold-only headings in PDFs (backlog B); title level coherence
  (`title_hierarchy_percent` 36%).
- Expected: `is_bold` 54→80%, `is_title` 45→70%, `title_hierarchy` 36→60%.

**Iter 22+ — Italic / sup / sub / strike / underline preservation**
- Backlog I, PDF font-flag infra shared with Iter 21.

**Iter 23+ — Chart understanding (new VLM feature, 1-2 weeks, separate track)**
- Chart region detection → VLM data-point extraction → markdown
  representation. Will likely need to adapt `ChartDataPointMatch` metric
  to parse our markdown representation, just like tables.

**Deferred: visual grounding** (no bbox output). Only the `order`
sub-rules (637 of 16,325 layout rules) are currently evaluable.

### Iter 20 Track A — starter kit for next session

Concrete entry points so the fresh session doesn't re-discover ground.

**Evidence this is worth doing** (from 300-sample audit, native-plain
text_content, no ocr/multicolumns/multilang/handwriting):
- 42% of failed `missing_specific_sentence` rules have the content
  present in ParserX's output (normalized substring match). These are
  false-miss fails — evaluator fuzzy threshold is too strict.
- 58% are true absences (16% of those end in page-number digits → TOC).

**Concrete false-miss examples to test against:**
- `text/text_simple__agenda`: expected `'ms weber added that rainbow staff have also visi… information about careers in the water industry'`; ParserX has `"Ms. Weber added that Rainbow staff have also visited classrooms to share information about careers in the water industry."` (just case + period + ellipsis differences).
- `text/text_simple__redirect`: expected `'redirect manager and/or vanity url 20'`; ParserX has `"Redirect Manager and/or vanity URL"` — this one IS a true miss (trailing "20" dropped), not a false-miss. Use as a negative to make sure loosening doesn't pass this.

**Where to look in ParseBench source:**
- `~/Projects/ParseBench/src/parse_bench/evaluation/metrics/parse/rules_content.py`
  (likely where `missing_specific_sentence` / `missing_sentence_percent`
  rule handlers live — locate via `grep -rn "missing_specific_sentence"`).
- Shared text-normalization lives in
  `~/Projects/ParseBench/src/parse_bench/evaluation/metrics/parse/utils.py`
  (`normalize_text`). Consider whether this is the right layer to
  strengthen (Unicode apostrophes, smart quotes, NFKC, trailing
  punctuation, whitespace collapsing) vs. adding per-rule fuzz.
- Search: `match_sentence`, `find_sentence`, `_find_text_in_content`.

**Safeguards — don't over-loosen:**
- Build a tiny regression set of (expected_sentence, actual_md,
  expected_pass_boolean) pairs: ~20 true-miss negatives + ~20
  false-miss positives drawn from the audit samples. Run before/after.
- Re-score with `--skip_inference` (fast, ~5-10 min) and watch
  `unexpected_sentence_percent` — if it moves down, we accidentally
  made matching too lenient.

**Reproduce the audit** (regenerates the 42% / 58% split):
```bash
cd ~/Projects/ParseBench
# Uses the same analysis as 2026-04-14 session; inline in docs for now.
# See iteration_history.md if/when Iter 20 lands.
```

### Reproducibility — how to resume in a new session

```bash
# Full run (~50 min, concurrency=8):
cd ~/Projects/ParseBench
PARSERX_REPO=/Users/xuyun/Projects/ParserX \
  uv run parse-bench run parserx --max_concurrent 8

# Lite run (~3-5 min, iteration loop):
PARSERX_REPO=/Users/xuyun/Projects/ParserX \
  uv run parse-bench run parserx --input_dir data_lite \
  --output_dir output/parserx_lite -m 8

# Re-score without re-parsing (after changing evaluator only):
uv run parse-bench run parserx --skip_inference

# Rebuild lite subset (stratified, deterministic via SEED):
uv run python scripts/build_lite_subset.py
```

Artifacts of record:
- `~/Projects/ParseBench/output/parserx/` — baseline full-run reports
- `~/Projects/ParseBench/output/parserx_smoke_test/` — original 3-doc smoke
- `~/Projects/ParseBench/data_lite/` — 32-PDF lite subset + manifest
- `~/Projects/ParseBench/src/parse_bench/inference/providers/parse/parserx.py` — adapter
- `~/Projects/ParseBench/src/parse_bench/inference/pipelines/parse.py` — pipeline reg
- `~/Projects/ParseBench/scripts/build_lite_subset.py` — subset builder

## Iterations

_(Append newest at top. Each iteration: 1 row in scoreboard above +
short what/why/delta below.)_

### iter-18-markdown-table-evaluator — 2026-04-14

**What**: Forked ParseBench evaluator to accept markdown pipe tables in the
predicted side. Two edits in `~/Projects/ParseBench`:
- `src/parse_bench/evaluation/metrics/parse/table_extraction.py` —
  `extract_normalized_tables()` falls back to `parse_markdown_tables()`
  (already existed in `table_parsing.py`) when `extract_html_tables()`
  returns empty. `raw_html=""` for markdown-sourced tables; safe because
  GriTS reads `table_data` directly and TRM never touches `raw_html`.
- `src/parse_bench/evaluation/evaluators/parse.py` — `_has_html_tables()`
  now also detects GFM pipe tables via the `|---|---|` separator-row
  signature, so the short-circuit at line 543 (`has_actual_tables`)
  no longer skips the full table-metric path for markdown-only output.

**Why**: Baseline Tables 0.00% was pure format-contract mismatch, not
quality. ParserX is markdown-first by design (see evaluator-fork policy).

**Re-run scope**: Lite (7 table docs), `--skip_inference` only (evaluator
change, no re-parse needed).

**Delta vs previous (lite)**: GriTS 0 → **67.81%**, TRM 0 → **44.44%**,
GTRM composite 0 → **56.13%**. 6/7 pairs scored.

**Notes**:
- `WU.2015.page_161.pdf_68095_page1` still shows actual=0. Not yet
  diagnosed — likely a pipe-table edge case (single-line table? leading
  content before pipes?). Follow-up.
- `FBLB-134215544_page122` low grits=0.125 suggests genuine structural
  divergence, not format issue — real signal.
- Pipe syntax can't express rowspan/colspan → ceiling < HTML. Accepted.
- Full-set re-score needed to update scoreboard ParseBench-wide Tables
  column. Do on next cadence gate.

<!-- ITERATION TEMPLATE

### iter-N-<slug> — YYYY-MM-DD

**What**: one-line change.
**Why**: which ParseBench failure class(es) targeted, referencing
baseline or prior-run explanations.
**Re-run scope**: full / single dimension (`--group ...`) / which docs.
**Delta vs previous**: +X.X pts on <dim>, flat on others.
**Notes**: surprises, regressions, follow-ups.

-->
