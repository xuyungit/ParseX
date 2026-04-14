# Iteration History

This file archives completed iteration records from the ParserX development
process. Each section documents what was done, measured impact, design
decisions, and remaining issues identified at the time.

For the current active backlog, see [iteration_backlog.md](iteration_backlog.md).

---

## Iteration 22 — PDF Superscript + Underline (2026-04-14)

**范围**: ParserX 端，在 Iter 21 的 inline_spans 基础上扩展 sup/underline
两类格式信号。详见 [parsebench_baseline.md](parsebench_baseline.md)。

### What Was Done

- `parserx/providers/pdf.py`：
  - `_reconstruct_line_segments`：segment 扩展两个字段 `underline`
    和 `sup`；sup 来自 PyMuPDF span flag bit 0。
  - `_collect_underline_rects`：从 `page.get_drawings()` 提取细长横向
    矩形（h<1.5pt, w>3pt）作为 baked-in 下划线候选。
  - `_char_is_underlined`：字符底边附近 1-3pt 内若存在候选矩形且横向
    重叠 ≥30%，标记为下划线。
  - `_merge_line_segments`：格式分组扩展到 (bold, italic, underline, sup)
    四元组。
- `parserx/assembly/markdown.py`：`_render_inline_spans` 为 underline
  包 `<u>…</u>`，为 sup 包 `<sup>…</sup>`；嵌套在 bold/italic 外层内部，
  保留正确的 marker 顺序。

### 评测结果（full）

| Sub-rule | Iter 21 | Iter 22 | Δ |
|---|---|---|---|
| **`is_sup`** | 4.98% | **36.99%** | **+32.01pt** |
| `is_bold` | 61.55% | 62.30% | +0.75pt |
| `is_title` | 53.30% | 53.91% | +0.61pt |
| `title_hierarchy_percent` | 43.94% | 44.34% | +0.40pt |
| `is_latex` | 35.29% | 36.85% | +1.56pt |
| `is_underline` | 0.00% | 0.34% | +0.34pt（基本未动）|
| **text_formatting 整体** | 43.22% | **45.36%** | **+2.14pt** |
| text_content 整体 | 86.38% | 86.59% | +0.21pt |

### Decisions

- **is_sup** 单点暴涨：PyMuPDF 直接给出 flag，rule 只需包短文本
  (`<sup>44</sup>`)，评估器 regex 命中率极高。接受。
- **is_underline** 未起：评估器强制 `<u>exact_text</u>` 连续命中，但
  中文长句被我们的 line-joiner 和 gap-based 空格插入打断（例如 rule
  要 `1万人超`，我们输出 `1 万人超`；rule 要 `,` 我们输出 `、`）。
  需要跨行 underline 合并 + CJK 标点归一化才能真正拿分；相对收益低
  于 Iter 21 的 bold/italic，不在本次迭代内继续。
- 其余 `is_sub` / `is_strikeout` / `is_mark` / `is_code_block` 均仍为
  0 或接近 0，规则数量较小 (14 / 44 / 88 / 10)，放入后续迭代。

### Remaining Issues / Next

- 探索 underline 跨行合并 + CJK 标点归一化的可行性（或评估器 fork）。
- `is_mark` (高亮) 需要读取 PDF 注释 / color fill，范围不同。
- text_content 长尾文档（`paper_cn_trad`, `reverRo`, `caldera`）仍是
  最大剩余机会。

---

## Iteration 21 — PDF Inline Formatting (Bold/Italic) (2026-04-14)

**范围**: ParserX 端，PDF 解析路径加入 inline 格式提取。详见
[parsebench_baseline.md](parsebench_baseline.md)。

### What Was Done

- `parserx/providers/pdf.py`：新增 `_reconstruct_line_segments`（行内按
  bold/italic 切段，空格按与 `_reconstruct_line_from_chars` 相同的 gap
  策略归入后一段）+ `_merge_line_segments`（块内按行合并，不同行之间插入
  一条 plain `\n` 段，避免 `**` 跨行；whitespace-only 格式段并入邻居）。
  仅当块内存在混合 bold/italic 时写入 `inline_spans` 元数据。
- `parserx/assembly/markdown.py`：渲染前校验 span concat == content，不
  匹配则回退到 plain content（防止后续 processor 改写 content 后 span
  陈旧导致截断）。
- `parserx/processors/line_unwrap.py` / `processors/text_clean.py`：在
  合并 / 清理时同步更新 inline_spans，保住段落间的 bold/italic 信号。

### 评测结果（full re-parse）

| Sub-rule | baseline | Iter 21 | Δ |
|---|---|---|---|
| `is_bold` | 54.00% | **61.55%** | +7.55pt |
| `is_italic` | 5.85% | **31.65%** | +25.80pt |
| `is_title` | 45.00% | **53.30%** | +8.30pt |
| `title_hierarchy_percent` | 36.00% | **43.94%** | +7.94pt |
| `is_latex` | 29.00% | 35.29% | +6.29pt |
| **text_formatting 整体** | 34.33% | **43.22%** | **+8.89pt** |
| text_content 整体 | 86.89% (20A) | 86.38% | -0.51pt |

### Decisions & Trade-offs

- text_content 小幅回落（-0.51pt vs 20A；仍 +0.95pt vs baseline）。
  原因是 `**` 标记让部分句子匹配路径略敏感，深挖收益低，接受。
- `is_sup` / `is_sub` / `is_underline` / `is_mark` / `is_strikeout`
  / `is_code_block` 仍为 0 或接近 0，需要额外基础设施（HTML 标签
  emission / Unicode 上下标映射 / 代码块识别），放入后续迭代。

### Remaining Issues / Next

- 考虑提取 text_formatting 中 sub/sup / underline / strikeout 特性。
- text_content 长尾：top-failing docs (`paper_cn_trad`, `reverRo`,
  `caldera` 等) 单文档 50+ 规则失败，结构性问题，可能需要 reading order
  / layout 修复。

---

## Iteration 20 Track A — ParseBench: Sentence-Match Normalization Fork (2026-04-14)

**范围**: ParseBench 端评估器修正（不动 ParserX）。详见
[parsebench_baseline.md](parsebench_baseline.md)。

### What Was Done

- 审计 native-plain 文档 7,590 条 `missing_specific_sentence` 规则，
  严格 fail 2,245 条；其中 **39.9%（896 条）** 若在两侧都去除非字母数字
  标点后可恢复匹配 → 为 false-miss。剩余 60.1% 为 true-miss。
- 根因：ParserX 正确输出了句子内容，但 markdown 表格管道
  （`| Hui | 28,080 |`）将整句切断，而评估器用严格 substring。
- 在 `~/Projects/ParseBench/src/parse_bench/evaluation/metrics/parse/rules_bag.py`
  新增 `_loose_strip_punct` + `_count_sentence_lenient`，应用于
  `MissingSpecificSentenceRule` / `MissingSentenceRule` /
  `MissingSentencePercentRule`。短查询（<20 字）仍保留词边界锚定避免误配。
  作用域仅 Missing*，TooMany/Unexpected 不受影响。
- 回归集（`~/Projects/ParseBench/scripts/iter20_regression_audit.json`，
  25 false-miss + 25 true-miss）：25/25 false-miss 恢复，
  25/25 true-miss 仍然失败。

### 评测结果（`--skip_inference` 全量 re-score）

| Sub-rule | 前 | 后 | Δ |
|---|---|---|---|
| `missing_specific_sentence` | 66.97% | **76.70%** | +9.73pt |
| `missing_sentence_percent` | 66.81% | **76.52%** | +9.71pt |
| `unexpected_sentence_percent` | 78.97% | 78.74% | -0.23pt（噪声内） |
| text_content 整体 pass rate | 85.43% | **86.89%** | +1.46pt |

### Decisions

- 按 2026-04-14 定下的 evaluator-fork 策略：product contract 是
  markdown-first，表格/匹配的评估器争议优先改评估器。
- 仅对 Missing* 放宽，不影响 TooMany / Unexpected — 不会因让步而引入
  false positive。

### Next

- Track B（ParserX 侧，~0.5 天）：TOC 标题后的页码被 heading 检测剥离，
  导致 `"redirect manager and/or vanity url 20"` → 输出去掉了 "20"。
  保留 TOC 行末页码 inline。约覆盖剩余 true-miss 的 16%。
- Track C（ParserX 侧，~1 天）：输出长度远小于 GT 的极端截断审计。

---

## Iteration 18 — ParseBench: Markdown Table Evaluator Fork (2026-04-14)

**范围**: ParseBench 端评估器适配（不动 ParserX）。

### What Was Done

- Baseline 全量跑（2078 docs，53min@c=8）后发现 Tables 维度 GTRM **0.00%** —
  根因不是质量问题，是格式契约不匹配：ParseBench 的
  `extract_html_tables()` 只扫 `<table>…</table>`，而 ParserX 始终输出
  pipe-markdown（`| a | b |`）。
- ParserX 的 product contract 是 markdown-first，决定 **fork 评估器**，
  不回头改 ParserX 输出 HTML。两处编辑：
  - `src/parse_bench/evaluation/metrics/parse/table_extraction.py` —
    `extract_normalized_tables()` 在 HTML 路径空时回退到
    `parse_markdown_tables()`。
  - `src/parse_bench/evaluation/evaluators/parse.py` — `_has_html_tables()`
    也识别 GFM 分隔行 `|---|---|`，否则 table 路径会被短路。
- lite（7 docs）验证 GTRM 0→56.13%；full-set re-score GTRM 0→**41.33%**
  (GriTS 50.40%，TRM 30.36%)。

### Limits & Follow-ups

- pipe 语法无法表达 rowspan/colspan → 复杂表格天花板低于 HTML，接受。
- `WU.2015.page_161.pdf_68095_page1` 仍 actual=0，疑似 pipe-table 边界用例。

---

## Iteration 17 — Stage 1: ParseBench Adapter Bootstrap (2026-04-14)

Goal: wire ParserX into the LlamaIndex ParseBench harness so it can run
against the external PDF/EN benchmark alongside the internal CJK ground
truth. Backlog item N, Stage 1 only (adapter + smoke run). No metrics
work, no full run.

### What Was Done

- Cloned `run-llama/ParseBench` to `~/Projects/ParseBench`, `uv sync
  --extra runners`, `parse-bench download --test` (45 files, ~15 PDFs).
- Verified harness end-to-end by running the stock `pymupdf_text`
  pipeline on text_content/--test (557/597 rules passed).
- Confirmed ParserX CLI contract suits subprocess use:
  `parserx parse <pdf> --stdout` is non-interactive, exit-coded, emits
  Markdown on stdout. Latent bug logged (not blocking): `--no-vlm` /
  `--no-llm` override `services.*.endpoint=` which fails pydantic `str`
  validation — fix deferred.
- Wrote ParseBench provider at
  `src/parse_bench/inference/providers/parse/parserx.py` and registered
  pipeline `parserx` in `pipelines/parse.py`. Provider shells out via
  `subprocess.run` with `cwd=$PARSERX_REPO` so ParserX's `.env` loads.
  Splits stdout on `<!-- PAGE N -->` markers for per-page `PageIR`.
- Smoke run `parse-bench run parserx --test --group text_content
  --max_concurrent 1`: 3/3 docs parsed successfully, avg 7.6s/page,
  **560/597 rules passed (93.8%)** vs pymupdf 557/597 (93.3%).

### Per-rule Scores (text_content --test, n=597 rules / 3 docs)

| Rule | Pass rate |
|------|-----------|
| missing_specific_word | 0.997 |
| missing_word_percent | 0.994 |
| too_many_sentence_occurence | 0.963 |
| unexpected_word_percent | 0.959 |
| too_many_word_occurence | 0.945 |
| unexpected_sentence_percent | 0.926 |
| order | 0.883 |
| missing_specific_sentence | 0.854 |
| **overall** | **0.961** |

Order (0.883) and missing_specific_sentence (0.854) are the weakest — not
actionable from 3 docs; revisit after a larger-scale run.

### Decisions

- Stage 1 scope satisfied; open questions for Stage 2/3 in backlog N.
- Full 5-dimension run deferred: estimated ~4 hours and significant
  VLM/LLM spend. Need explicit user approval before launching.
- Visual grounding left as a future iteration — ParserX emits no bboxes
  today; expect 0 on that dimension until we add them.

### Remaining Issues / Next Steps

1. Run other 4 dimensions on `--test` to get a first full-shape picture
   (tables, charts, formatting, layout).
2. Decide on full (non-test) run budget.
3. Borrow `TableRecordMatch` + faithfulness rule format into our own
   `scripts/regression_test.py` (Stage 2 in backlog N).

---

## Iteration 16: DOCX Parsing — ListItem, Fragment Merge, Inline Formatting (2026-04-12)

Target document: chn_doc01 (竞争性谈判文件, 40+ pages, complex structure).

### What Was Done

**1. ListItem handling** (`providers/docx.py`)
- Problem: Docling's `ListItem` type (58 items in chn_doc01) was silently dropped
  by `_convert_item()` because only `SectionHeaderItem`, `TextItem`, `TableItem`,
  `PictureItem` were handled. ListItems contain contract clause headings, numbered
  sub-items, and compliance section titles.
- Fix: Added `ListItem` check BEFORE `TextItem` (critical: `ListItem` inherits
  from `TextItem`, so `isinstance(item, TextItem)` matches ListItems too).
  Docling's internal `marker` is NOT used (it's list-level ordering, not document
  section numbering).

**2. Formatting-boundary fragment merge** (`providers/docx.py`)
- Problem: Docling splits a single DOCX paragraph into multiple TextItems at each
  formatting change (underline on/off, bold on/off). "2025年9月10日" became 6
  fragments; "投标物资中支座计量方式为点件计量。" became 4.
- Fix: Two-phase extraction: first collect all items with their Docling `parent`
  group refs, then merge consecutive elements sharing the same `#/groups/N` parent
  into a single PageElement. Per-span formatting (bold/italic/underline) is
  recorded in `metadata["inline_spans"]` for the renderer.
- ListItems are excluded from merging (each is a separate paragraph even within
  the same group).

**3. Inline formatting rendering** (`assembly/markdown.py`)
- Added `_render_inline_spans()` to output per-span `**bold**`, `*italic*`,
  `<u>underline</u>` from merged DOCX spans.
- Adjacent spans with same bold/italic state are consolidated to avoid
  `**a****b**` → produces `**ab**` instead.
- Single (non-merged) elements only render underline; bold/italic on whole
  paragraphs is skipped to avoid PDF metric regression.

**4. DOCX bold+numbering heading detection** (`processors/chapter.py`)
- Problem: MetadataBuilder is skipped for DOCX, so `heading_candidates` is empty
  and `_heading_level_from_font()` always returns None. DOCX headings with
  numbering patterns were missed.
- Fix: Fallback detection when `heading_candidates` is empty: bold + numbering
  pattern → heading. Bold alone is NOT sufficient (too many false positives from
  cover pages, table headers, emphasized text).

**5. Underline extraction** (`providers/docx.py`)
- Docling's `formatting.underline` is now extracted and stored in element metadata.
  Previously only bold/italic were captured.

### Impact

| Document | Metric | Before | After | Change |
|----------|--------|--------|-------|--------|
| text_table01 | edit_distance | 0.003 | 0.000 | -0.003 ✅ |
| text_table01 | char_f1 | — | 1.000 | ✅ Perfect |
| chn_doc01 | Content coverage | 58 items lost | Recovered | ✅ |
| chn_doc01 | Text fragmentation | 6+ fragments | Single paragraph | ✅ |
| chn_doc01 | Inline formatting | None | bold/italic/underline | ✅ |

All deterministic documents pass or improve. No PDF regressions.

### Remaining DOCX Issues

- Docling's SectionHeaderItem is unreliable for DOCX (only 7 empty H3 headers
  in a 40-page document). Real headings come as TextItem/ListItem.
- Docling sometimes loses fill-in field content (e.g., "采购资金来自<u>项目工程
  计量款</u>" → "采购资金来自。") — upstream Docling limitation.
- Table header cells extracted as duplicate standalone TextItems alongside the
  table → potential false heading detection.
- best_scores.json baselines for text_report01 and paper_chn01 are stale.

---

## Iteration 15: Image Pipeline — Dedup, ImageMask, Description, Config (2026-04-12)

Target document: paper_chn01 (中文学术论文, 7 pages, two-column layout).

### What Was Done

**1. Vector figure / native image deduplication** (`builders/image_extract.py`)
- Problem: OCR layout detection creates `vector_figure=True` elements for figure
  regions, while PyMuPDF separately extracts native embedded images by xref.
  Both appeared in output as duplicate figures.
- Fix: `_dedup_vfig_native()` checks bbox overlap between vfig and native img
  elements on each page. When overlap >50%, the vfig is suppressed in favor of
  the native image (higher resolution, original encoding).

**2. ImageMask color inversion** (`builders/image_extract.py`)
- Problem: PDF images stored as ImageMask (stencil masks) have inverted colors
  when raw-extracted. PDF readers apply the mask correctly, but `extract_image()`
  gives black-on-white inverted output.
- Fix: After extracting image bytes, check `fitz_doc.xref_object(xref)` for
  `/ImageMask true`. If found, invert via PIL `ImageOps.invert()` and save as
  1-bit PNG. Ported from legacy codebase (`doc-refine/scripts/pdf_extract.py`).

**3. Image description always preserved** (`processors/image.py`)
- Problem: VLM summary (the actual image description) was suppressed in multiple
  code paths:
  - `_apply_vlm_corrections()` suppressed summary when >60% char overlap with
    corrections, and `_normalize_vlm_output()` returned empty description.
  - `_select_vlm_description()` had complex routing that replaced summary with
    visible_text labels (e.g., "F a b c d1 d2") or OCR evidence when overlap
    was strong.
- Fix:
  - `_normalize_vlm_output()`: when correction path returns empty `remaining_desc`
    but `summary` is non-empty, preserve summary as description
    (`description_source = "vlm_summary_after_correction"`).
  - `_select_vlm_description()`: simplified to always use summary as description
    when available. visible_text/evidence are for OCR correction, not description.
- Design principle: OCR correction and image description are independent outputs.
  Corrections fix OCR text/tables; description describes the image. They should
  never suppress each other.

**4. Description rendered as visible text** (`assembly/markdown.py`)
- Problem: Short descriptions were placed only in alt text (`![desc](path)`),
  invisible in rendered markdown.
- Fix: Always render description as a visible blockquote below the image:
  `![desc](path)\n\n> desc`.

**5. Pipeline default config loading** (`pipeline.py`)
- Problem: `Pipeline()` without explicit config used empty `ParserXConfig()`
  instead of loading from `parserx.yaml` / `~/.config/parserx/config.yaml`.
  This caused VLM/LLM/OCR services to be `None` when called programmatically.
- Fix: Changed `config or ParserXConfig()` to `config if config is not None else load_config()`.

**6. OCR vector figure detection + caption attachment** (`builders/ocr.py`)
- Enhanced OCR builder to detect figure regions via PaddleOCR layout labels
  (`image`, `figure`) and create `vector_figure=True` elements.
- Added `_attach_figure_captions()`: attaches nearby `figure_title` labels
  as captions to detected vector figures by vertical proximity.
- Added table column dedup and improved table/text deduplication logic.

**7. Cross-reference caption improvements** (`assembly/crossref.py`)
- Pre-populate captions from OCR `figure_title` labels (`ocr_caption` metadata).
- Relaxed caption length check to allow longer captions to be classified.

### Measured Impact

| Document | Metric | Before | After | Change |
|----------|--------|--------|-------|--------|
| paper_chn01 | Duplicate figures | 3 pairs (6 images) | 5 unique images | Fixed |
| paper_chn01 | Inverted images | 5 inverted | 0 inverted | Fixed |
| paper_chn01 | Images with description | 0/5 | 5/5 | Fixed |
| paper_chn01 | VLM calls | 0 (service was None) | 5 | Fixed |

### Key Lessons

- **VLM summary 是图片描述，不可替代**：visible_text 是标签文字转录，不是描述。
  两者服务不同目的，不应互相抑制。
- **Pipeline 配置加载需要防御性设计**：`Pipeline()` 无参调用应该自动加载配置文件，
  否则所有 AI 服务都是 None，且没有明确的错误提示。
- **ImageMask 是 PDF 图片反色的常见原因**：PDF 用 stencil mask 表示二值图像，
  `extract_image()` 返回的原始字节是反色的。检查 xref object 的 `/ImageMask` 属性即可。

### Remaining Issues

- **图片描述语言不一致**：VLM 有时返回中文描述，有时返回英文描述。
  需要文档级语言检测 + system prompt 中指定 summary 输出语言。
- **vfig 文件残留**：去重后 vfig 文件仍在磁盘上但未被引用，触发 verification warning。
  可以在去重时跳过渲染，或在提取后清理未引用的文件。

---

## Iteration 14: Paper01 Quality — Heading + Code + Bold Detection (2026-04-11)

Target document: paper01 (TensorFlow whitepaper, 19 pages, two-column layout).

### What Was Done

**1. PDF same-row line joining** (`providers/pdf.py`)
- Root cause: PyMuPDF splits same-visual-row text into separate `line` objects
  when there's a large horizontal gap (e.g., "1" and "Introduction").
- Fix: `_join_block_lines()` checks y-coordinate overlap (>50%) and joins
  with space instead of newline.
- Impact: headings, body text, inline references all benefit.

**2. Heading false positive filters** (`processors/chapter.py`)
- Single-character text (diagram labels C, b, W, x) → false positive filter.
- `section_arabic_root` ("N.") excluded from coherence promotion (ambiguous
  with ordered lists). Also excluded from LLM fallback when font matches body.
- LLM fallback length threshold aligned with `_detect_heading` (80 chars
  via `_is_short_heading_text`, was inconsistent at 120).

**3. Bold heading candidate detection** (`builders/metadata.py`)
- Frequency filter changed from grouping by size to (size, bold).
  Bold 10pt (278 chars) no longer masked by Regular 10pt (58000 chars).
- Enables detection of bold-only sub-headings: Operations and Kernels,
  Sessions, Variables, Devices, Tensors, Data Parallel Training.

**4. Code block detection** (`processors/code_block.py`)
- Added `nimbus\s*mon` pattern for NimbusMonL (URW/TeX Nimbus Mono).
- Added generic `\bmono\b` and `\bfixed\b` fallback patterns.
- Paper01 Figure 1 Python code now properly fenced.

**5. Vector figure detection** (attempted, reverted)
- Tried rule-based drawing clustering → too many parameters, poor generalization.
- Tried OCR layout detection → correct figure bboxes but coordinate conversion,
  image referencing, and text suppression had multiple unresolved issues.
- Reverted all vector figure code. Detailed lessons recorded in backlog.

### Measured Impact

| Document | Metric | Before | After | Change |
|----------|--------|--------|-------|--------|
| paper01 | edit_distance | 0.328 | ~0.300 | -0.028 |
| paper01 | heading_f1 | 0.667 | ~0.725 | +0.058 |

No regressions on deterministic ground truth documents.

### Key Lessons

- **Font frequency filter 需要按 (size, bold) 分组**，不是仅按 size。
  否则 body font 的大字符量会掩盖低频的 bold heading 候选。
- **`section_arabic_root` ("N.") 是最易产生误判的编号格式**，
  因为它同时匹配 section heading 和 ordered list。需要额外的 font
  或上下文信号才能区分。
- **矢量图检测不适合纯规则方案**。核心困难是精确聚类（不多不少地
  把一个图的所有元素聚在一起）。OCR layout detection 能解决聚类问题，
  但与现有 pipeline 的集成（坐标转换、图片引用流程）需要更系统的设计。

---

## Iteration 13: LLM Line Unwrap Fallback + Batch OCR (2026-04-10)

### What Was Done

**1. Three-way merge decision + LLM fallback** (`processors/line_unwrap.py`)

- Implemented the design from `project_llm_unwrap_next.md`:
  `_should_merge_lines` now returns a three-way signal (merge/keep/uncertain)
  instead of boolean.
- Uncertain cases (CJK short lines, abbreviation periods, semicolons,
  uppercase proper nouns) are collected and sent to LLM in batch.
- Body-font filter prevents over-merging of titles and table headers.
- DOCX documents skip LineUnwrapProcessor entirely (no visual line breaks
  to unwrap).

**2. Batch OCR** (`services/ocr.py`)

- Pages needing OCR are extracted from the original PDF into a temporary
  PDF and sent in a single PaddleOCR API call (`fileType=0`) instead of
  per-page image uploads.
- Auto-splits at 100 pages to stay within API limits.
- Falls back to per-page on failure.
- Toggle via `builders.ocr.batch` config (default: on).

**3. VLM review per-page retry** (`processors/vlm_review.py`)

- Streaming responses can fail mid-transfer (peer closed connection).
  The OpenAI SDK's `max_retries` only covers request-level failures,
  not mid-stream disconnects.
- Added application-level retry (3 attempts, exponential backoff) around
  each page's VLM review call.

### Measured Impact

_(Metrics compared to previous iteration baseline where available)_

| Document | Metric | Before | After | Change |
|----------|--------|--------|-------|--------|
| (pending full regression comparison) | | | | |

---

## Iteration 12: Rawdict Word Space Recovery + Multi-Column Propagation (2026-04-10)

### What Was Done

**1. Character-level gap detection for word space recovery** (`providers/pdf.py`)

- Problem: PDF text extraction via `get_text("dict")` lost inter-word spaces.
- Fix: switched to `get_text("rawdict")` which returns per-character bounding
  boxes. Added `_reconstruct_line_from_chars()` that measures gaps between
  consecutive character bboxes and inserts a space when
  `gap > font_size * 0.25`.
- CJK-aware: suppresses space insertion between CJK ideographs and fullwidth
  punctuation, but NOT between fullwidth ASCII letters which need word spacing.

**2. Multi-column hint detection for mixed-layout pages** (`builders/reading_order.py`)

- Problem: page 1 of Chinese academic papers has mixed layout. Only 2
  column-width elements remained after filtering. Detection blocked on
  minimum thresholds.
- Fix: relaxed thresholds to `col_sized < 2` and `left_edges < 1`.

**3. CJK continuation signals for line unwrap** (`processors/line_unwrap.py`)

- Added `_CJK_CONTINUATION_RE` regex for orphaned punctuation and bracketed
  references at line start as continuation signals.
- Removed overly broad 1-2 CJK char orphan pattern that caused false merges.

### Measured Impact

| Document | Metric | Before | After | Change |
|----------|--------|--------|-------|--------|
| paper_chn02 (new) | English spaces | missing | recovered | fixed |
| receipt | edit_distance | 0.031 | 0.030 | -0.001 |
| paper01 | edit_distance | 0.328 | 0.325 | -0.003 |
| paper_chn01 | edit_distance | 0.506 | 0.503 | -0.003 |

### Key Design Decisions

- Gap threshold `0.25 * font_size` chosen empirically: word gaps ~3.69pt
  (font 10.3pt), intra-word gaps <=0. Threshold of 2.57 cleanly separates.
- Fullwidth ASCII letters excluded from CJK suppression (Latin text rendered
  wide needs word space detection).
- Column hint relaxation safe because hint gutter validated from other pages.

---

## Iteration 11: VLM Format Guard + Zero-Signal Heading Fallback (2026-04-09)

### What Was Done

**0. VLM review prompt optimization + format guard** (`processors/vlm_review.py`)

- Prompt fix: explicit FORBIDDEN block listing disallowed changes.
- Code guard: `_is_format_only_change()` normalizes both strings and rejects
  format-only corrections.
- ocr_scan_jtg3362: 12 format-only corrections rejected per run.

**1. Zero-signal LLM fallback for short headings** (`processors/chapter.py`)

- Allow OCR elements (font.size=0) with short text (<=30 chars) into LLM
  fallback pool. Native PDF elements with known body-size fonts still rejected.
- Added `zero_signal` flag and prompt annotation.

**2. OCR heading suppression fall-through** (`processors/chapter.py`)

- Changed `continue` to `pass` so suppressed OCR headings fall through to
  standard detection pipeline. Preserved original OCR heading level as
  `ocr_level_hint` metadata.

### Measured Impact

| Document | Metric | Before | After | Change |
|----------|--------|--------|-------|--------|
| text_table_word | heading_f1 | 0.667 | 1.000 | +0.333 |
| ocr_scan_jtg3362 | heading_f1 | 0.000 | 0.100 | +0.100 |

---

## Iteration 10: VLM Review Eval + Outlined Text Detection (2026-04-09)

### What Was Done

**0. ocr_scan_jtg3362 ground truth correction** — expected.md rewritten to
match actual PDF content. char_f1 jumped from 0.572 to 0.881.

**1. VLM review end-to-end evaluation** — Net effect negative (char_f1
0.881->0.865, table_f1 0.86->0.74). fix_text accurate but fix_table/formatting
drift causes regressions.

**2. Outlined text OCR recovery** (`pipeline.py _check_page_quality`) — Detect
NATIVE pages with tables whose header row has >=50% empty cells, reclassify to
SCANNED. text_table_word: table_cell_f1 0.913->1.000, char_f1 0.973->0.987.

**3. fix_table duplication bug fix** — Use full element replacement instead of
prefix replacement. ocr01: table_cell_f1 0.111->0.941.

**4. VLM provider A/B/C comparison** (14-document full eval) — Provider A
(proxy gpt-5.4-mini) best overall; VLM review is non-deterministic.

### Measured Impact

| Document | Metric | Before | After | Change |
|----------|--------|--------|-------|--------|
| ocr_scan_jtg3362 | char_f1 | 0.572 | 0.891 | +0.319 (GT fix + OCR) |
| ocr_scan_jtg3362 | table_f1 | 0.476 | 0.857 | +0.381 |
| text_table_word | char_f1 | 0.973 | 0.987 | +0.014 |
| text_table_word | table_f1 | 0.913 | 1.000 | +0.087 |
| ocr01 | table_f1 | 0.111 | 0.941 | +0.830 |

---

## Iteration 9: Gutter Refinement + Adaptive Line Unwrap (2026-04-09)

### What Was Done

**0. Column detection gutter refinement** (`builders/reading_order.py`) — Skip
elements spanning across gutter during boundary refinement.

**1. Adaptive CJK line unwrap for narrow columns** (`processors/line_unwrap.py`)
— Block-local column width estimate (P75) + `last_raw_len` tracking to prevent
accumulated merge length from masking short breaks.

**2. patent01 ground truth** — New test document: Chinese invention patent,
14 pages. Two-column metadata + single-column body.

### Measured Impact

patent01 PAGE 1: column ordering fixed, line breaks merged, abstract body
merged. Regression tests: no regressions from code changes.

### Key Design Decisions

- Gutter-spanning exclusion mirrors `classify_element()`.
- P75 instead of max for local column width (robust against CJK+ASCII outliers).
- `last_raw_len` only affects within-element unwrap path.

---

## Iteration 8: Heading Fix + Two-Column + Layout Complexity (2026-04-09)

### What Was Done

**0. Layout complexity detection** — Deterministic check for figure-heavy pages.
Default OFF (OCR quality insufficient to be net positive).

**1. Multiline heading number resolution** (`processors/chapter.py`) — Joins
pure-number first line with heading-like second line. Applied in 6 call sites.

**2. Document-level two-column propagation** (`builders/reading_order.py`) —
Two-pass approach: independent detection then hint-based propagation using
median gutter position.

**3. VLM Review truncation fix** — Strip trailing `...` from `corr.original`.

**4. paper01 GT heading level correction** — Corrected to standard academic
hierarchy.

### Measured Impact

| Document | Metric | Before | After | Delta |
|----------|--------|--------|-------|-------|
| paper01 | heading_F1 | 0.167 | 0.667 | +0.500 |
| paper_chn01 | heading_F1 | 0.690 | 0.774 | +0.084 |
| paper_chn01 | edit_distance | 0.520 | 0.506 | -0.014 |

---

## Iteration 7: OCR Graceful Degradation (2026-04-09)

### What Was Done

**OCR 三层降级容错** (`services/ocr.py`, `builders/ocr.py`)

1. 指数退避重试 (5次)
2. 关闭 Layout Detection 重试 (2次)
3. 跳过失败页面继续处理

ocr01 从解析失败 -> 完整解析成功。

---

## Iteration 6: Heading Detection — Numbering Coherence (2026-04-09)

### What Was Done

1. Fixed `section_arabic_spaced` regex to include "0".
2. Document-level numbering coherence detection with density guard (>8 skip).
3. OCR-assigned heading level correction with colon filter.

### Measured Impact

| Document | Metric | Before | After | Delta |
|----------|--------|--------|-------|-------|
| paper_chn01 | heading_F1 | 0.230 | ~0.69-0.71 | +0.46-0.48 |
| text_code_block | heading_F1 | 0.191 | 0.500 | +0.309 |

---

## Iteration 5: VLM Review Real-Document Validation (2026-04-08)

### What Was Done

- VLM Review prompt and parser fix (few-shot examples, bare array fallback).
- Page selection narrowed to SCANNED/MIXED only (NATIVE review causes regressions).

### Key Findings

1. VLM model quality is the bottleneck (gpt-5.4-mini introduces as many errors
   as it fixes).
2. VLM non-determinism is significant even with temperature=0.0.
3. VLM tends to "improve" text rather than faithfully transcribing.

---

## Iteration 4: VLM Review Processor + Header/Footer Identity (2026-04-08)

### What Was Done

- New VLMReviewProcessor: page-level VLM review for SCANNED/MIXED pages.
- Header/Footer first-page identity retention tightened (max 2 elements,
  ranked by text length).
- 25 new tests for VLM review, 4 new tests for header/footer.

---

## Iteration 3: paper_chn01 Baseline (2026-04-08)

Initial: edit_dist=0.874, char_f1=0.295. After: edit_dist=0.503, char_f1=0.891.

1. Full-width -> half-width ASCII normalization (char_f1 0.295->0.671)
2. Garbled text -> OCR fallback via U+FFFD ratio (char_f1 0.671->0.723)
3. LLM-based page quality check for formula OCR (char_f1 0.723->0.804)
4. expected.md baseline corrections (table_cell_f1 0.00->1.00)
5. LaTeX prime simplification (char_f1 0.804->0.891)

---

## Iteration 2: DOCX Pipeline Fix & .doc Support (2026-04-08)

- DOCX 流式文档处理路径修复 (skip geometry-dependent processors).
- .doc -> .docx conversion via LibreOffice headless.
- New eval sample: text_report01.
- text_report01: edit_dist=0.097, char_f1=0.955, heading_f1=0.714, table_f1=1.000.

---

## Iteration 1: OCR-Scan Detection & VLM Path Simplification (2026-04-08)

- OCR-layered scan detection (spatial coverage + text containment).
- OCR Builder native text replacement on SCANNED pages.
- VLM correction path simplification (removed supplement mode).
- New GT: ocr_scan_jtg3362, text_table_word.

---

## Iteration 0: Image Pipeline & VLM Correction (2026-04-06)

- Image output contract + ProductQualityChecker.
- VLM-authoritative correction architecture (three-field output).
- ContentValueProcessor OCR exemptions.
- VLM prompt improvements.
- Public eval: warnings 10->0, edit_dist 0.145->0.101, char_f1 0.936->0.964.

---

## Pre-iteration: Verification Fixes, Duplication Elimination & Line Unwrap (2026-04-07)

- Verification layer false positive elimination (text_pic02: 4->0 warnings).
- Cross-page table VLM duplication fix (ocr01: 13->1 VLM calls).
- Receipt heading over-detection (14->2 warnings).
- Line unwrap polish (text_table01, text_table_libreoffice: edit_dist 0.030->0.000).
- Total warnings 22->2, avg edit_dist 0.075->0.038.

---

## Pre-iteration: Formula Format Normalization (2026-04-07)

- FormulaProcessor: temperature, chemical formulas, micro-units, math symbols,
  LaTeX fragment cleanup.
- Public eval: avg edit_dist 0.101->0.077, avg char_f1 0.964->0.977.
