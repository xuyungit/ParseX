# Iteration Backlog

Updated: 2026-04-14 (post Iteration 20 Track A)

Active backlog for choosing the next iteration. For completed iteration
records, see [iteration_history.md](iteration_history.md).

## Current Baseline (2026-04-12, 15 ground truth docs)

| Document | Edit Dist | Char F1 | Heading F1 | Table F1 | Notes |
|----------|-----------|---------|------------|----------|-------|
| text_table01 | 0.000 | 1.000 | 1.000 | — | Perfect (Iter16) |
| text_table_libreoffice | 0.000 | 1.000 | 1.000 | 1.000 | Perfect |
| text_table_word | 0.029 | 0.987 | 1.000 | 1.000 | Good |
| receipt | 0.034 | 0.982 | 1.000 | — | Good |
| pdf_text01_tables | 0.041 | 0.979 | 0.000 | 0.992 | Heading 缺失 |
| text_code_block | 0.050 | 0.975 | 0.500 | — | Code fence 问题 |
| deepseek | 0.077 | 0.939 | 0.000 | — | 繁体→简体 |
| text_report01 | 0.101 | 0.949 | 0.625 | 1.000 | Heading 漏检; best_scores 过时 |
| ocr01 | 0.157 | 0.964 | 0.667 | 0.897 | 表格对齐偏差 |
| ocr_scan_jtg3362 | ~0.21 | ~0.89 | ~0.10 | ~0.86 | Heading 几乎全失; OCR 服务器不稳 |
| text_pic02 | 0.277 | 0.911 | 0.250 | 0.224 | DOCX 表格/图片 |
| paper01 | ~0.22 | 0.976 | 0.725 | — | Unicode 字符; heading 改善(Iter14) |
| paper_chn01 | ~0.69 | ~0.76 | 0.867 | 1.000 | 需 OCR 服务; best_scores 过时 |
| paper_chn02 | 0.650 | 0.776 | 0.182 | — | HTML 表头丢失 |
| **patent01** | **0.955** | **0.159** | **0.000** | — | **灾难性失败** |

Note: `~` 表示最近一次运行值，受 OCR 服务器稳定性影响。

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
- **下一步候选（按 ROI 排序）**：
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
