# Iteration Backlog

Updated: 2026-04-15 (post Iteration 23, re-prioritized)

Active backlog for choosing the next iteration. For completed iteration
records, see [iteration_history.md](iteration_history.md).

## Re-prioritization 2026-04-15

After Iter 18–23, review of ParseBench remaining items by
product-value (not leaderboard score):

**Kept as valuable:**
- Iter 20 Track C — document truncation audit (real content loss)
- `is_sub` preservation (chemistry/math semantics; low cost, reuses
  Iter 21/22 span infra)
- `is_code_block` — fold into backlog L (code-block boundary)
- Chart understanding (new capability, not leaderboard vanity)

**Dropped as low-value:**
- Iter 20 Track B (TOC trailing page number) — regex-only win, product
  should strip page numbers anyway
- Iter 19 (`<page_header>`/`<page_footer>` tag wrap) — format contract
  only, no downstream use
- `is_underline` regex chasing — Iter 22 proved poor ROI
- `is_mark` / `is_strikeout` polishing — aesthetic

**Execution order:**
1. **Iter 24 — `[NNNN]` paragraph segmentation — DONE 2026-04-15.**
   `line_unwrap` 新增 `[\d+]` list marker + bare-marker 下吸规则。
   patent01 剩余（章节标题、首页 reading order、expected.md 补全）
   + Track C truncation audit 合并进 Iter 25。
2. **Iter 25 Track 1 — 节页眉→H1 提升 — DONE 2026-04-15。**
   `header_footer._promote_section_page_headers` 捕获 `<name> N/M 页`
   惯例，首次出现提升 H1，后续去除。纯结构化（无关键词表），
   迁移到法规/标准/技术规范同类文档。patent01 三节标题全部落位。
3. **Iter 25 Track 2 — ParseBench Track C truncation audit — DONE 2026-04-15。**
   `PDFProvider._classify_page` 新增矢量页判据：text<500 且
   drawings>200 → SCANNED。修复 landfill(1→3679 chars)、
   finra(241→5853 chars)。纯视觉信号。
4. **Iter 26 — 跨元素标题合并 — DONE 2026-04-15。**
   `chapter._merge_split_section_headings` + `_split_heading_body_elements`
   rewrite + inline_spans invalidation tail (commit `e37c87b`)。
   paper01 heading_f1 0.667 → 0.747 (+0.080)，char_f1 0.975 → 0.982，
   edit_distance 0.328 → 0.250。
5. **Iter 27 — Dotted-numbering heading depth — DONE 2026-04-15。**
   `_heading_level_from_numbering` 的 section_arabic_nested 分支按
   点号深度返回 H2/H3/H4/…（`3.2.1` → H4）。paper01 heading_f1
   0.667 → 0.791（+0.124），edit_distance 0.328 → 0.264。
   paper_chn01 heading_f1 0.774 → 0.867（+0.093）。
   glyph-only 候选过滤 + `:` 续行合并实验失败（级联降分）已回退。
6. **Iter 28 Track A — Bold-at-body-size 几何 gating — DONE 2026-04-15**。
   `_has_heading_vertical_isolation` + `heading_geometric_reject`
   标记（commit `9fa78f9`）。paper01 heading_f1 0.791 → 0.818
   (+0.027)；spurious `### Variables/Devices/Tensors` 去除。
   Track B / C 延后单独迭代。
   **未解决遗留**：
   - paper01 `## Model Parallel Training` 等被 fallback 升级到 H2
     （GT 为 H3/非 heading）——`_build_fallback_candidate` level
     hint 校准。
   - paper01 首页 `## Abstract` 漏检（PDF 无字面词）。
   - Track B（page-1 双行 H1 `TensorFlow:` + `Large-Scale…` 合并）。
   - Track C（code-block boundary，含 `# of Relu` 伪 H1）。

7. **Iter 29a — Fallback level-hint 校准 — DONE 2026-04-16**
   （commit `9618876`）。`_apply_llm_fallback` 在写入 `pending` 前把
   LLM level clamp 到 `numbering_level_hint` / `font_level_hint` floor。
   paper01 edit_distance 0.255 → 0.234。heading_f1 持平（fallback
   影响层级而非 heading 判定）。详见 history Iter 29a 段。
   OCR `paragraph_title` 层级二义性（两次 demote 方案均回退）未解决，
   作为独立候选留在下方 (a')。

8. **Iter 30 Track B — Multi-line title split — DONE 2026-04-16**。
   `_split_heading_body_elements` 识别多行 H1 title 或 colon-ended
   heading，emit 多个 PageElement 保留 heading_level 而非合并。
   paper01 `TensorFlow:` + `Large-Scale Machine Learning…` 两行都
   渲染为 `# ...`。新增 helpers `_is_subtitle_line` /
   `_is_multiline_title` / `_count_title_lines` 做通用判定（≤80 chars、
   非 body、非 metadata/括号），触发条件为 H1 或 colon-ended，无文档
   关键词。paper01 heading_f1 0.818 → 0.83 (+0.012)，其他文档无回退。

9. **Iter 31 — Document-level hierarchy recognition — DONE 2026-04-17**。
   Step 1 诊断：throwaway patch (H2→H3) 在 VLM on/off 下跑 paper_chn01，
   4 格 char_f1 均在 ±0.02 噪声内——VLM 级联假说未复现。
   Step 2：`vlm_review.py:388-389` heading_level 改为布尔 `is_heading`
   (VLM 合约健全化，无功能变化)。
   Step 3：`chapter.py::_infer_ocr_paragraph_title_level(doc)` 基于
   native 层 font-hierarchy depth (≥3 distinct heading-candidate sizes)
   + native-dominance guard (char-ratio ≥0.5) 决定 OCR paragraph_title
   默认层级。numbering 始终压过推理。paper01 `Model Parallel Training`
   / `Concurrent Steps…` 从 H2 → H3 (正确)。paper01 heading_f1
   0.832 → 0.841；paper_chn01 char_f1 0.717 → 0.726 / heading_f1 0.812
   稳定；avg heading_f1 -0.011 (ocr_scan_jtg3362 波动所致)。详见
   iteration_history.md Iter 31 段。bbox-height 聚类 signal 未实施
   (文档级 font-depth 已足够)，留作 backlog 候选。

10. **Iter 32 候选（优先级排序）**：

    c. **Track C — code-block boundary 扩展**（backlog L 归并）：
       `# of Relu` 伪 H1 + text_code_block heading_f1=0.500。
    d. **Abstract 页顶 heading 补插**：首页 body-sized Bold + 后接
       大正文段落 + 距 title ≥ 2×line-height → 插入 virtual "Abstract"
       heading。高风险需 guard。
    e. **`is_sub` preservation**（chemistry/math 语义）、paper_chn02
       HTML 表头 / Chinese doc class、DOCX table/image quality
       (backlog C)，然后 Chart track (M)。
    f. **simple_doc01 DOCX 编号标题修复**：Docling provider 对
       auto-numbering heading 只提取到编号（`1.3.1`），丢失标题文本。
       char_f1=0.458。需调查 Docling 对 `w:numPr` 的处理。
    g. **ocr_scan_jtg3362 native-fallback heading 层级**：`公路钢筋…`
       被 native LLM fallback 路径降到 H3（expected H2）。该路径独立
       于 Iter 31 OCR 分支，需单独调查。

## Current Baseline (2026-04-17 post Iter 31, 16 ground truth docs)

Full regression run: `eval_reports/iter31_baseline_2026-04-17.md`
avg edit_dist=0.239, char_f1=0.901, heading_f1=0.531, table_f1=0.485.
paper01 heading_f1 0.832 → 0.841 (+0.009)。paper_chn01 char_f1 0.717 → 0.726。
avg heading_f1 -0.011 源于 ocr_scan_jtg3362 0.111 → 0.095 波动 (该文档在
backlog 中标记为 OCR 服务器不稳定)。

Prior baseline (2026-04-16): `eval_reports/iter30_baseline_2026-04-16.md`

| Document | Edit Dist | Char F1 | Heading F1 | Table F1 | Notes |
|----------|-----------|---------|------------|----------|-------|
| text_table01 | 0.004 | 0.998 | 1.000 | — | Near-perfect |
| text_table_libreoffice | 0.006 | 0.997 | 1.000 | 1.000 | Near-perfect |
| text_table_word | 0.035 | 0.984 | 1.000 | 1.000 | Good |
| pdf_text01_tables | 0.041 | 0.979 | 0.000 | 0.992 | Heading 缺失 |
| text_code_block | 0.050 | 0.975 | 0.500 | — | Code fence 问题 |
| deepseek | 0.077 | 0.939 | 0.000 | — | 繁体→简体 |
| receipt | 0.089 | 0.958 | 1.000 | — | Good |
| text_report01 | 0.122 | 0.937 | 0.526 | 1.000 | Heading 漏检 |
| ocr01 | 0.139 | 0.960 | 0.737 | 0.903 | ocr01 heading 改善 |
| text_pic02 | 0.182 | 0.975 | 0.250 | 0.195 | DOCX 表格/图片 |
| ocr_scan_jtg3362 | 0.239 | 0.884 | 0.095 | 0.714 | OCR 服务器不稳 |
| paper01 | 0.252 | 0.979 | **0.832** | — | Iter 30 title split 改善 |
| patent01 | 0.425 | 0.908 | 0.500 | 1.000 | 大幅改善 (was 0.955/0.159) |
| paper_chn02 | 0.633 | 0.807 | 0.129 | — | HTML 表头丢失 |
| paper_chn01 | 0.707 | 0.714 | 0.812 | 1.000 | OCR 服务器波动 |
| simple_doc01 | 0.711 | 0.458 | 0.286 | — | **新文档，未追踪**；DOCX 编号型 heading 提取丢失正文 |
| **Average** | **0.232** | **0.903** | **0.542** | **0.488** | |

Note: simple_doc01 是本地未提交的 DOCX GT（2026-04-10 创建），
DOCX 自动编号标题导致内容丢失（只提取到 `#### 1.3.1` 数字，无文字）。
不属于 Iter 30 回退，是 Docling provider DOCX 编号样式问题。

## Next Iteration Candidates (优先级排序)

### Tier 1: 高影响、可执行

#### A. Patent 文档修复 (patent01: char_f1=0.159 → 目标 0.85+)

**当前最严重的质量问题**。expected.md 补全后实际 char_f1=0.883，但需代码修复：

1. **HeaderFooterProcessor 章节标题保留**：`权利要求书`、`说明书`、`附图`
   在页面顶部重复 → 被误删为页眉。需"首次出现保留"或"章节标题排除"逻辑。
2. **段落号分段**：`[0005]...[0006]` 合并为一段 → 需按 `[NNNN]` 模式分段。
3. **附图页文字泄露**：axis tick、图例标签作为正文输出。
4. **首页 reading order**：公告号/日期与标签交错。

**预估工作量**：中等（主要改 HeaderFooterProcessor + TextCleanProcessor）。

#### B. PDF Bold-only Heading Detection (paper01: 0.725, paper_chn02: 0.182)

学术论文最大短板。`Operations and Kernels`, `Sessions`, `Abstract` 等
bold-only sub-headings（与 body 同字号）全部漏检。

- **方案**：PyMuPDF rawdict `font.flags` bit 4 提取 bold 信号 →
  MetadataBuilder frequency filter 调整（当前 bold 10pt 被排除因超 10% 阈值）→
  独立 bold-only heading candidate path
- **跨元素标题合并**：`5.2 Controlling Data Communication and` 截断问题
- 与 Iter16 DOCX bold heading detection 类似思路，但 PDF 有 font size 可用

**预估工作量**：中等偏大（MetadataBuilder + ChapterProcessor + 字体 flags 解析）。

#### C. DOCX 表格/图片质量 (text_pic02: table_f1=0.224)

text_pic02 是 DOCX 文档，当前：
- 合并单元格处理错误（10 列 → 4-6 列）
- 图片引用丢失
- 内容丢失 26%

**方案选择**：
1. 改进 Docling 表格合并单元格处理（grid 扩展）
2. 用 markitdown 作为 DOCX 替代 provider（已加依赖）
3. 混合策略：markitdown 提取 + Docling heading 结构

**预估工作量**：中等。

### Tier 2: 中等影响

#### D. Unicode / 编码恢复 (paper01, deepseek)

- paper01: accent 字符损坏 (Martín → Mart´ın)
- deepseek: 繁体字未统一 (⽤户→用户)
- 方向：NFKC 规范化 + CID 映射修复 + 可选繁简转换

#### E. LLM Heading Fallback 提示词改进

弱化 `font_level_hint` 引导，增加中性表述。作者行 82 chars 被误判为 H1。

#### F. Image Description 语言一致性

文档级语言检测 → system prompt 明确要求主语言 → 可选配置覆盖。

#### G. Scanned Document Heading Recovery (ocr_scan_jtg3362: heading_f1=0.091)

OCR 文本质量差，heading 级别不稳定。VLM heading 级别校正。

### Tier 3: 长期改进

#### H. Vector Figure 提取 + 文字泄露抑制

paper01 矢量图无法提取（无 image object），图内文字泄露为正文。

#### I. Bold / Font Style Preservation (PDF)

PDF 文本中的 bold/italic 保留为 markdown 标记。与 heading detection 共享
font flags 基础设施。Iter16 已完成 DOCX 部分。

#### J. Smart Routing (markitdown fallback)

轻量路由层：PDF → ParserX, 非 PDF / 低保真 → markitdown。

#### K. Multi-Column Tier 2 (PaddleOCR Layout)

paper01 仍有 4/19 页未检测到双栏。渲染页面为图片 → PaddleOCR layout。

#### L. Code Block Fence Detection

text_code_block heading_f1=0.500。代码块边界识别不完整。

#### M. Chart Extraction & Integration

检测图表区域、保留标题/说明、生成简洁描述。

### Tier 1: 高影响、可执行 (续)

#### N. ParseBench 集成 (泛化评估主驱动) — Stage 1/2 DONE, Stage 3 IN PROGRESS

**进度 (2026-04-14)**：详见
[parsebench_baseline.md](parsebench_baseline.md) 及
[iteration_history.md](iteration_history.md) Iter 17 / 18 / 20 Track A。

- **Iter 17（Stage 1）**：Provider 适配器 ✅
- **Iter 18**：markdown-table 评估器 fork → Tables GTRM **0 → 41.33%** ✅
- **Iter 20 Track A**：句子匹配标点容忍 fork → text_content
  **85.43% → 86.89%** ✅
- **Iter 21**：PDF inline formatting (bold/italic) → text_formatting
  **34.33% → 43.22%** ✅
- **Iter 22**：PDF superscript + underline → text_formatting
  **43.22% → 45.36%** ✅（sup +32pt 主要贡献）
- **Iter 23**：Hybrid column-aware extraction via PaddleOCR layout →
  text_content **86.59% → 86.83%** / text_formatting **45.36% → 45.64%**
  ✅（paper_cn_trad / atlantic / strikeUnderline / reverRo 大幅改善；
  caldera / gridofimages 仍难 —— 物理 overflow 布局）
- **后续候选（按 ROI 排序）**：
  - **Iter 20 Track B**（ParserX 侧，~0.5 天）：TOC 行页码 inline 保留。
    当前 heading 检测把 `"Redirect Manager and/or vanity URL 20"` 末尾
    页码 `20` 剥离，约覆盖剩余 true-miss 失败的 16%。
  - **Iter 20 Track C**（ParserX 侧，~1 天）：输出极端截断审计
    （如 `text_misc__censored` 553 chars）。原因可能是去 redaction 启发式
    过于激进，或整页视觉-only 误分类。
  - **Iter 21**（ParserX 侧，~2-3 天）：PDF bold-only headings + title
    hierarchy；对应 backlog B，text_formatting 维度从 34.3% 拉升的首发。

#### N-OLD. (legacy Stage 描述 — 已归档到 Iter 17 history)

LlamaIndex 的 ParseBench（Apache 2.0，`github.com/run-llama/ParseBench`，
HF 数据集 `llamaindex/ParseBench`）~2000 页企业文档（保险 SERFF / 金融 /
政府），~167k 条基于规则的测试，5 个维度：tables / charts / content
faithfulness / semantic formatting / visual grounding。作为 **主要泛化
驱动**，与真实 CJK docs/pdfs 并行。

**分阶段落地**：
1. **Stage 1 — Provider 适配器**：在其 `src/parse_bench/inference/providers/`
   下写 ParserX adapter（CLI: `parserx` → markdown），跑 `--test` 小集先
   打通链路；目标：在他们 leaderboard 上拿到初始分数。
2. **Stage 2 — 指标对齐**：把 `TableRecordMatch`（表格按 header-keyed
   record 集合比较）和 faithfulness 规则格式借鉴到 ParserX 自己的
   `scripts/regression_test.py` / `docs/evaluation.md`；比现有文本相似度
   更能反映语义正确性，特别适合 table 与 image/VLM 路径。
3. **Stage 3 — 短板攻关**：Charts（行业普遍 <50%）和 semantic formatting
   是我们目前几乎没覆盖的维度，对应 backlog M (Chart Extraction) 和 I
   (Bold/Font Style Preservation)；ParseBench 分数可作为这两项的验收信号。

**边界**：ParseBench 为 PDF + 英文企业文档，不覆盖 CJK line-unwrap /
DOCX。作为现有 `ground_truth/` 的**补充**而非替代，两条评估流并行。

**预估工作量**：Stage 1 小（~1 天），Stage 2 中，Stage 3 大（新能力）。

## Design Principles

以下原则在迭代过程中逐步明确，后续工作应遵循：

- **信息价值优先**：噪声抑制应基于信息价值，而非 UI 特定启发式。
  模糊内容优先提取/保留，而非删除。
- **图片是核心差异化能力**：有价值的图片应转为可搜索的文本证据 + 语义描述。
- **泛化优于特化**：偏好几何/结构信号，避免文档特定的启发式规则。
- **确定性优先，AI 兜底**：规则方法可复现，LLM/VLM 作为最后手段。
- **VLM 权威高于 OCR**：VLM 同时看图片和 OCR 文本，信息量更大。
