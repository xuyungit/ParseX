# Iteration Backlog

Updated: 2026-04-12

Active backlog for choosing the next iteration. For completed iteration
records, see [iteration_history.md](iteration_history.md).

## Current Baseline (2026-04-10, 15 ground truth docs)

| Document | Edit Dist | Char F1 | Heading F1 | Table F1 | Notes |
|----------|-----------|---------|------------|----------|-------|
| text_table01 | 0.000 | 1.000 | 1.000 | — | Perfect |
| text_table_libreoffice | 0.000 | 1.000 | 1.000 | 1.000 | Perfect |
| text_table_word | 0.029 | 0.987 | 1.000 | 1.000 | Good |
| receipt | 0.034 | 0.982 | 1.000 | — | Good |
| pdf_text01_tables | 0.041 | 0.979 | 0.000 | 0.992 | Heading 缺失 |
| text_code_block | 0.050 | 0.975 | 0.500 | — | Code fence 问题 |
| deepseek | 0.077 | 0.939 | 0.000 | — | 繁体→简体 |
| text_report01 | 0.098 | 0.954 | 0.526 | 1.000 | Heading 漏检 |
| ocr01 | 0.157 | 0.964 | 0.667 | 0.897 | 表格对齐偏差 |
| ocr_scan_jtg3362 | 0.207 | 0.886 | 0.091 | 0.839 | Heading 几乎全失 |
| text_pic02 | 0.277 | 0.911 | 0.250 | 0.224 | DOCX 表格/图片 |
| paper01 | 0.327 | 0.975 | 0.640 | — | Unicode 字符损坏 |
| paper_chn01 | 0.456 | 0.902 | 0.667 | 1.000 | 图片描述已修复(Iter15) |
| paper_chn02 | 0.650 | 0.776 | 0.182 | — | HTML 表头丢失 |
| **patent01** | **0.955** | **0.159** | **0.000** | — | **灾难性失败** |
| **Average** | **0.224** | **0.893** | **0.501** | **0.463** | |

## Backlog Candidates

### Patent Document Remaining Issues (patent01: char_f1=0.883 after GT fix)

- 2026-04-10: expected.md 已从 1 页补全到 9 页正文（附图页排除），
  char_f1 从 0.159 升至 0.883，table_f1 从 0.0 升至 0.974。
- 剩余问题：
  - **章节标题误删**：`权 利 要 求 书`（pages 2-3）、`说 明 书`（pages 4-9）、
    `附 图`（pages 10-14）等章节标题在页面顶部重复出现，被
    HeaderFooterProcessor 误判为页眉删除。36 个元素被删，远超合理范围。
    需要 HeaderFooterProcessor 增加"首次出现保留"或"章节标题排除"逻辑。
  - **附图页文字泄露**：pages 10-14 的图表 axis tick 值、图例标签作为
    正文文本输出（应仅保留图片引用）
  - **段落号合并**：`[0005]...[0006]` 在一个段落内，应为两个独立段落
  - **VLM 调用过多**：22 张 informational 图 + 31 VLM calls，但大部分
    附图页的图只需简单引用不需 VLM 描述
  - **首页 reading order**：公告号/日期与标签分离（左右两栏交错提取）

### Paper Heading Detection Improvement (paper01: 0.706, paper_chn02: 0.182)

- 学术论文的 heading_f1 普遍偏低：
  - paper01 (英文, 19页): heading_f1=0.706, 漏检 bold-only sub-headings
  - paper_chn02 (中文): heading_f1=0.182, 几乎所有 heading 丢失
  - paper_chn01: heading_f1=0.667, 中等水平
- **已修复**：
  - heading 数字/标题分离（`_join_block_lines()` y 坐标感知合并）
  - 单字符图表标签误判为 heading（`_is_false_positive` 加 `len <= 1` 检查）
  - numbered list 误判为 heading（`section_arabic_root` 不参与 coherence，
    body font 的 `N.` 格式不进入 LLM fallback）
  - LLM fallback 长度阈值与 `_detect_heading` 不一致（统一为 `_is_short_heading_text`）
- **未修复 — bold-only heading 漏检**（paper01 核心剩余问题）：
  - `Operations and Kernels`, `Sessions`, `Variables`, `Devices`, `Tensors`,
    `Data Parallel Training` 等 sub-headings 使用 bold 10pt，与 body 同字号，
    仅 font weight 不同。当前系统依赖字号差异，无法检测。
  - `Abstract` (bold 12pt 无编号) 也漏检。
  - 需要 **font weight/bold 信号**：PyMuPDF rawdict `font.flags` bit 4 = bold。
    MetadataBuilder 的 heading candidate 检测已支持 "same size but bold"，
    但被 frequency filter 排除（bold 10pt 字符数超过 body 的 10%）。
    需要调整 frequency filter 或增加 bold-only heading 的独立检测路径。
- **未修复 — 标题文本跨元素截断**：
  - `5.2 Controlling Data Communication and` — 标题太长，后半部分在下一个元素。
  - `5.4 Optimized Libraries for Kernel Imple-` — 同上，带连字符断词。
  - 需要跨元素 heading 片段合并。

### LLM Heading Fallback 提示词改进

- 当前提示词传递 `font_level_hint`，引导 LLM 倾向于将候选判断为 heading。
- 应强调让 LLM 根据**语义和上下文**判断，而非仅依赖 font hint。
- 作者行（82 chars, font 12pt）被 LLM 判断为 H1 的案例表明提示词有误导性。
- 改进方向：弱化 hint 引导，增加"以下候选可能是也可能不是标题"的中性表述。

### Bold / Font Style Preservation

- 当前 parser 将所有文本输出为纯文本，不保留原文档中的粗体、斜体等格式。
- 竞品（如 LlamaParse、markitdown）会保留 `**粗体**`、`*斜体*` 标记。
- 影响多文档：patent01 首页、text_report01 表单字段名、学术论文 heading。
- 实现方向：
  - PyMuPDF rawdict 的 `font.flags` 包含 bold/italic 信息（bit 0 = bold）
  - 在 `_reconstruct_line_from_chars()` 中检测 font flag 变化，插入标记
  - 注意边界：避免在整段都是 bold 的情况下加标记（如 heading 本身）
- 与 heading detection 共享基础设施（font flags 解析）。

### DOCX Table & Image Handling (text_pic02: table_f1=0.224)

- text_pic02 是 DOCX 文档，table_f1 仅 0.224，heading_f1=0.250。
- 问题：
  - **合并单元格**处理错误：版本控制表 10 列变 4-6 列
  - **图片引用丢失**：内嵌图片在转换中被跳过或错位
  - **内容丢失 26%**：source 11,466 chars → output 8,487 chars
- 可能的方向：
  - 改进 DOCX provider 对合并单元格的处理
  - DOCX 图片提取与引用标记
  - 或：用 markitdown 作为 DOCX 的替代 provider（已加依赖）

### Unicode / Encoding Recovery (paper01, deepseek)

- paper01: accent 字符损坏 (Martín → Mart´ın)，LaTeX 残留
- deepseek: 繁体字未统一 (⽤户→用户, ⻓期→长期)
- 根本原因：PDF 内部 ToUnicode 映射不完整
- 可能的方向：
  - 后处理 Unicode 规范化（NFKC + 常见 CID 映射修复）
  - 繁简转换可选后处理步骤

### Vector Figure Extraction + Text Leakage (paper01, patent01)

- **矢量图未提取**：paper01 Figure 2-9 是矢量图（无独立 image object），
  PyMuPDF 无法提取为图片文件。只有 page 13/15/16 的位图 figure 被提取。
- **图内文字泄露为正文**：
  - paper01 节点标签 `C`, `b`, `W`, `x`, `MatMul`, `ReLU` 等
  - patent01 pages 10-14 附图页的 axis tick、图例标签
- **2026-04-12 已完成的修复**（Iteration 15）：
  - **OCR layout figure detection**：OCR builder 检测 `image`/`figure` 标签，
    创建 `vector_figure=True` 元素，渲染区域为 PNG。
  - **vfig/native 去重**：当 OCR 检测的 figure 区域与原生 PDF 图片重叠 >50%
    时，抑制 vfig，优先使用原生图片。
  - **ImageMask 反色修正**：检测 `/ImageMask true` 并用 PIL 反转。
  - **VLM 描述始终保留**：summary 不再被 visible_text/evidence 替代。
  - **Pipeline 默认加载配置**：`Pipeline()` 自动加载 `parserx.yaml`。
- **仍未解决**：
  - paper01 矢量图（无任何 image object 的页面）仍无法提取
  - 图内文字泄露仍需文字抑制逻辑改进
  - vfig 文件去重后残留在磁盘上（可清理）

### Image Description Language Consistency

- VLM 生成的图片描述语言不一致：同一文档中部分图片中文描述，部分英文描述。
- 原因：语言检测是逐图片基于上下文文本做的，而非文档级。system prompt
  也未明确要求 summary 使用特定语言。
- 改进方向：
  - 文档级语言检测：扫描全文 CJK vs Latin 比例，确定主语言
  - 在 system prompt 中明确要求 summary 使用文档主语言
  - 可选配置参数 `vlm_description_language` 作为覆盖

### Smart Routing for Fast / Broad-Coverage Document Conversion

- 轻量路由层，根据文档类型/质量信号选择处理路径：
  - PDF 默认走 ParserX native path
  - 非 PDF / 低保真需求 → markitdown fallback
- 开放设计问题：
  - 路由信号：文件类型、页面质量、文本可提取性、速度/质量模式
  - 路由粒度：per-document vs per-page
  - 输出中如何标注 backend 来源

### Scanned Document Heading Recovery (ocr_scan_jtg3362: heading_f1=0.091)

- OCR 文本质量差，heading 级别不稳定
- 可能的方向：
  - VLM heading 级别校正（cross-page heading 层级一致性）
  - OCR 后处理的常见中文错字修复

### Numbered List Misdetected as Headings — 已修复

- paper01 Section 7 的 6 条编号建议被 coherence pass 误判为 H2 heading。
- 修复：
  - `section_arabic_root`（`N.` 格式）不再参与 coherence promotion，
    因为该格式同时匹配 section headings 和 ordered list items，歧义性太强。
  - `section_arabic_root` 在有真实字体信息（font.size > 0）且无 heading 字体信号时，
    不进入 LLM fallback 候选。
  - 单字符文本（如图表节点标签 C, b, W, x）加入 false positive 过滤。
- paper01: heading_f1 0.667 → 0.706, edit_distance 0.328 → 0.320。

### Visual Line Break Merging Issues (paper01 Section 7)

- paper01 Section 7 的正文中仍有部分连字符断词未合并（`demon-\nstrated`）。
- 不再被 heading 误检阻塞（已修复），但跨元素的 hyphen-continuation 合并
  仍受限于 LineUnwrapProcessor 的条件（可能因列宽/字体不匹配）。
- 优先级中低，待后续 LineUnwrap 改进。

### Heading Title Cross-Element Truncation (paper01)

- `### 5.2 Controlling Data Communication and` — 标题太长，后半部分
  (`Memory Usage`) 在下一个 PageElement 中。
- `### 5.4 Optimized Libraries for Kernel Imple-` — 同上，带连字符断词。
- 需要跨元素 heading 片段合并（类似 `_merge_cover_heading_fragments`
  但适用于任意页面的 numbered heading）。

### LLM Heading Fallback 提示词改进

- 当前提示词传递 `font_level_hint`，引导 LLM 倾向于将候选判断为 heading。
- 应强调让 LLM 根据语义和上下文判断，弱化 font hint 引导。
- 作者行（82 chars, font 12pt）曾被 LLM 判断为 H1 的案例表明提示词有误导性。
- 已通过阈值对齐修复（`_is_short_heading_text` 统一），但提示词仍可改进。

### Code Block Fence Detection (text_code_block)

- text_code_block: Code fence 识别不完整，代码块内容与正文混淆，heading_f1=0.500
- paper01 page 3 代码块已修复（NimbusMonL 等宽字体识别）。

### Multi-Column Tier 2: PaddleOCR Layout Fallback

- paper01 仍有 4/19 页未检测到双栏（pages 3, 13, 15, 16 — 太稀疏或图片主导）。
- 方向：渲染页面为图片 → PaddleOCR layout API → 用 `block_order` 排序。
- 优先级中等，已有 15/19 页覆盖。

### Chart Extraction & Chart-Body Integration

- 图表标题和图表内容常缺失。
- 需要：检测图表区域、保留标题/说明、生成简洁描述、近叙述位置放置。
- 与 Chart/Drawing Page Text Leakage 相关但更广：不仅要抑制泄露，还要提取有用信息。

## Design Principles

以下原则在迭代过程中逐步明确，后续工作应遵循：

- **信息价值优先**：噪声抑制应基于信息价值，而非 UI 特定启发式。
  模糊内容优先提取/保留，而非删除。
- **图片是核心差异化能力**：有价值的图片应转为可搜索的文本证据 + 语义描述。
- **泛化优于特化**：偏好几何/结构信号，避免文档特定的启发式规则。
- **确定性优先，AI 兜底**：规则方法可复现，LLM/VLM 作为最后手段。
- **VLM 权威高于 OCR**：VLM 同时看图片和 OCR 文本，信息量更大。
