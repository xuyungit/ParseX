# ParserX 架构设计与实施计划

## 1. 架构总览

### 1.1 分层架构

ParserX 采用五层分层架构，数据单向流动。设计参考了 Marker 的 Provider→Builder→Processor→Renderer 四层模式，并增加了验证层。

```
┌──────────────────────────────────────────────────────────────┐
│                      配置层 (Configuration)                    │
│                   YAML 配置 + 环境变量注入                       │
└─────────────────────────┬────────────────────────────────────┘
                          │
┌─────────────────────────▼────────────────────────────────────┐
│                    输入层 (Providers)                          │
│         PDFProvider  │  DOCXProvider  │  ImageProvider         │
│                          │                                    │
│                    ↓ PageElements                              │
├───────────────────────────────────────────────────────────────┤
│                    分析层 (Builders)                           │
│       MetadataBuilder  │  LayoutBuilder  │  OCRBuilder         │
│                          │                                    │
│                    ↓ AnnotatedDocument                         │
├───────────────────────────────────────────────────────────────┤
│                    处理层 (Processors)                          │
│   HeaderFooter → Chapter → Table → Image → Formula →           │
│   LineUnwrap → TextClean → ReadingOrder                        │
│                          │                                    │
│                    ↓ ProcessedDocument                         │
├───────────────────────────────────────────────────────────────┤
│                    组装层 (Assembly)                            │
│   ChapterAssembler → CrossRefResolver → MarkdownRenderer       │
│                          │                                    │
│                    ↓ Final Markdown                            │
├───────────────────────────────────────────────────────────────┤
│                    验证层 (Verification)                        │
│   HallucinationDetector │ CompletenessChecker │                │
│   StructureValidator    │ QualityReporter                      │
└───────────────────────────────────────────────────────────────┘
```

### 1.2 核心数据模型

各层通过统一的 Pydantic 数据模型通信：

```python
# 页面元素（输入层输出）
class PageElement(BaseModel):
    type: Literal["text", "table", "image", "formula", "header", "footer"]
    content: str                    # 原始文本或图片路径
    bbox: tuple[float, float, float, float]  # (x0, y0, x1, y1)
    page_number: int
    metadata: dict                  # font_size, bold, font_name 等
    confidence: float = 1.0         # 提取置信度
    source: Literal["native", "ocr", "vlm"] = "native"

# 文档模型（贯穿分析层到处理层）
class Document(BaseModel):
    pages: list[Page]
    metadata: DocumentMetadata      # 全局元数据
    elements: list[PageElement]     # 所有元素

# 文档元数据（分析层提取）
class DocumentMetadata(BaseModel):
    font_stats: FontStatistics      # 字体统计
    page_geometry: PageGeometry     # 页眉页脚区域
    numbering_patterns: list[NumberingPattern]  # 编号模式
    page_types: dict[int, PageType] # 逐页类型(native/scanned/mixed)
```

---

## 2. 核心设计原则

### 2.1 确定性优先，AI 兜底

**问题**：当前 legacy pipeline 对页眉页脚、章节检测、换行修复都调用 LLM，但这些问题的 80-90% 可用规则解决。

**原则**：每个处理器采用两阶段设计——先用确定性代码处理能确定的部分，仅对剩余的模糊情况回退到 AI。

**应用**：

| 处理器 | 确定性方法 | AI 兜底条件 |
|--------|-----------|-------------|
| HeaderFooter | 几何位置 + 跨页重复检测 | <3 页或无法形成稳定模式 |
| Chapter | 字体大小/粗体/编号格式匹配 | 规则匹配置信度 <0.7 |
| LineUnwrap | 跨元素合并 + 标点/句式/字体/间距分析 | 中文长段落断行位置模糊 |
| ImageClassify | 尺寸/像素分析/轻量分类器 | 始终确定性，不用 AI |

### 2.2 选择性处理

**问题**：当前每张图片都做 OCR + VLM，浪费大量调用。

**原则**：先分类，再路由。不同类型的内容走不同的处理管道。

```
图片 ──→ 分类器 ──┬→ 装饰性图片 ──→ 跳过/最小标注
                  ├→ 信息性图片 ──→ VLM 描述（我们的核心价值）
                  ├→ 表格截图   ──→ 表格提取管道
                  ├→ 文字图片   ──→ 仅 OCR
                  └→ 图表       ──→ VLM + 图表专用 prompt
```

OCR 同理：仅对缺文字/乱码区域做 OCR，原生文本完好的区域跳过。

### 2.3 VLM 权威性与安全护栏

**核心假设**：VLM 同时接收原始图片和 OCR 文本作为参考，其信息量严格大于 OCR。因此当 VLM 和 OCR 对同一区域都有输出时，优先保留 VLM 的结果。

**安全护栏**：VLM 输出仅在为空、截断或结构无效时被拒绝。不用 OCR 去"纠正" VLM 的内容（因为 VLM 已经看过 OCR 文本）。但验证层会检测 VLM 幻觉和数值偏差，标记为警告供人工审查。

**设计演进**（2026-04-08）：早期设计尝试在 VLM 输出与 OCR 之间做编辑距离交叉验证。实践表明这会导致过多误报，因为 VLM 的修正本身就会改变原文。当前设计改为：VLM 权威，护栏最小化，验证层事后检测。

### 2.4 全局元数据，局部处理

**问题**：大文档放不进 LLM 上下文（P3）；分片处理丢失全局模式。

**原则**：用确定性代码提取轻量级全局元数据（字体统计、页面几何、编号模式），然后用元数据指导逐页/逐元素处理。

```
第一遍（快速、确定性）：
  扫描全文 → 提取 DocumentMetadata
    - 最常见字体/字号 = 正文
    - 比正文大/粗的字体 = 标题候选
    - 页面顶部/底部重复出现的内容 = 页眉页脚
    - 中文编号模式 → 编号层级模型

第二遍（逐元素处理）：
  每个元素 + DocumentMetadata → 处理器
    - 标题候选 + 编号模型 → 章节结构
    - 页眉页脚区域 → 过滤
    - 图片 + 上下文 → VLM 描述
```

### 2.5 一切可度量

**问题**：无自动化评估（P12），优化靠人。

**原则**：每个处理阶段的输出都可度量。内建评估框架支持回归测试和 A/B 对比。

### 2.6 信息价值优先，而不是界面类型优先

**问题**：截图、阅读器壳子、导航条、按钮文案、装饰图标等内容并不总能靠“是不是应用界面”来定义；过于具体的 UI 特判容易过拟合到样本。

**原则**：判断一个元素是否保留，应优先看它对文档主体理解是否有独立信息贡献，而不是先判断它属于哪种界面类型。

实现导向：
- 对文本块和图片块统一计算 `informational_value_score`
- 评分信号来自内容密度、与上下文的语义连续性、结构角色、几何位置、重复性、对称性、与主体内容的依附关系
- 低信息值元素优先弱化或跳过，高信息值元素优先提取证据并保留
- 灰区样本才引入 VLM 做信息价值判别，而不是让 VLM 全量裁决

---

## 3. 模块设计

### 3.1 输入层（Providers）

每个 Provider 负责从特定格式提取原始内容，输出统一的 `Document` 模型。

#### PDFProvider — ✅ 已实现 (`providers/pdf.py`)

```
输入：PDF 文件路径
输出：Document（含 Page/PageElement，每个元素带 FontInfo）

已实现：
  1. PyMuPDF page.get_text("dict") 提取字符级信息
     - 每个文本块的字体名、字号、粗体/斜体标志、位置坐标
  2. PyMuPDF page.find_tables() 提取原生表格 → Markdown 表格格式
  3. PyMuPDF page.get_image_info(xrefs=True) 提取图片引用（含 xref）
  4. 逐页分类：native / scanned / mixed
     - 基础信号：文本字符数 vs 图片覆盖率
     - OCR-layered scan 检测（2026-04-08）：当主图覆盖页面 >50%
       且 >70% 的文字在该图 bbox 内时，判定为 SCANNED。这捕获了
       "可搜索扫描件"（invisible/visible OCR 文本层 + 扫描底图）
       的情况，使其正确进入 OCR 重跑流程。
```

#### DOCXProvider — ✅ 已实现 (`providers/docx.py`)

```
输入：DOCX 文件路径（同时支持 .doc，通过 LibreOffice 前置转换为 .docx）
输出：Document

实现方案：
  1. 使用 Docling 库进行 OOXML 原生解析
     - 安装：`uv add docling`（已在 pyproject.toml 的 optional deps 中）
     - API：`DocumentConverter().convert(path)` → `DoclingDocument`
  2. 映射 DoclingDocument → ParserX Document 模型：
     - DoclingDocument.texts → PageElement(type="text")
     - DoclingDocument.tables → PageElement(type="table")
     - DoclingDocument.pictures → PageElement(type="image")
  3. DOCX 样式信息映射：
     - OOXML heading styles (Heading 1/2/3) → 直接设置 heading_level
     - OOXML bold/font-size → FontInfo（用于 MetadataBuilder）
  4. 表格结构保留：
     - DoclingDocument 的表格包含行列结构和合并单元格信息
     - 转为 Markdown 表格时展平合并单元格
  5. 图片提取：
     - Docling 自动提取嵌入图片到临时目录
     - 需处理旋转元数据（参考 legacy pipeline fix_rotation.py）
  6. .doc 支持：
     - pipeline.py `_convert_doc_to_docx()` 调用 LibreOffice headless 转换
     - 转换后走标准 DOCX 处理路径，source_path 保留原始 .doc 路径

  DOCX 流式文档处理路径：
  - DOCX 是流式文档，有样式语义但无页面几何信息（bbox 全为 0）
  - pipeline 在 DOCX 模式下跳过几何依赖处理器和 builder：
    跳过：HeaderFooterProcessor、CodeBlockProcessor、ContentValueProcessor、
          MetadataBuilder（字体统计）、OCRBuilder、ReadingOrderBuilder
    保留：ChapterProcessor → TableProcessor → ImageProcessor → FormulaProcessor
          → LineUnwrapProcessor → TextCleanProcessor
  - OOXML 有显式样式信息，标题检测是确定性的，ChapterProcessor 直接使用
```

#### ImageProvider — ❌ 待实现（低优先级）

```
输入：图片文件路径（PNG, JPG, TIFF）
输出：Document（单页，单个 image 元素）

实现：将图片包装为单页文档，统一走后续管道（OCR + VLM）。
```

### 3.2 分析层（Builders）

分析层负责从原始元素中提取结构化的分析信息，为处理层提供决策依据。

#### MetadataBuilder — ✅ 已实现 (`builders/metadata.py`)

用确定性代码提取文档级元数据，替代 LLM 密集的章节检测和页眉页脚处理。

```
输入：list[PageElement]
输出：DocumentMetadata

提取内容：

1. 字体统计（FontStatistics）
   - 统计全文档的字体名+字号+粗体组合的频率
   - 最高频组合 = 正文字体（body_font）
   - 频率低但字号大于正文的 = 标题候选
   - 为每个标题候选分配层级（H1/H2/H3）基于字号排序

2. 页面几何（PageGeometry）
   - 分析所有页面的元素分布
   - 识别页眉区域：顶部 Y% 区域内跨 >50% 页面重复出现的内容
   - 识别页脚区域：底部 Y% 区域内跨 >50% 页面重复出现的内容
   - 识别页码：页脚区域中的纯数字/编号模式

3. 编号模式（NumberingPatterns）
   - 分析标题候选中的中文编号模式
   - 支持：第X章、X.Y.Z、一/二/三、(一)/(二)、1./2./ 等
   - 可复用 legacy pipeline 中 chapter_outline_core.py 的编号检测正则
   - 构建编号层级模型（哪些模式是一级、二级、三级）

4. 逐页类型分类（PageTypes）
   - 每页独立判断：native / scanned / mixed
   - 基于该页文本提取产出 vs 页面面积的比值
```

#### LayoutBuilder — ❌ 待实现（当前由 PaddleOCR 布局分析 + 启发式替代）

```
输入：Document + 页面图片
输出：每个 PageElement 增加 layout_type 标注

当前状态：
  - 图片分类已由 ImageProcessor 用启发式规则实现（不依赖布局模型）
  - 表格区域已由 PDFProvider 的 find_tables() 标记
  - 页眉页脚区域已由 HeaderFooterProcessor 的几何分析检测

待实现方案（当需要处理复杂布局时）：
  - 使用 PaddleOCR 在线服务的布局分析能力（useLayoutDetection=True）
  - 或集成 Docling Heron（需本地 GPU）
  - 识别区域类型：text, table, figure, header, footer, formula, code
  - 主要服务于扫描件和复杂多栏布局的 PDF
```

#### OCRBuilder — ✅ 已实现 (`builders/ocr.py` + `services/ocr.py`)

```
输入：Document + 源文件路径
输出：Document（scanned/mixed 页面补充 OCR 文本元素）

选择逻辑（逐区域决策）：
  - 原生文本完好 → 跳过 OCR
  - PDFProvider 标记为 scanned 的页面 → 全页 OCR
    ∘ 包括 OCR-layered scan：先移除旧 native text/table 元素，
      再用新 OCR 结果替换（保留 image 元素供 VLM 处理）
  - 矢量渲染文字（原生提取为空但有渲染内容）→ 区域 OCR
  - 乱码字体（Unicode 块分布异常，参考 LiteParse）→ 区域 OCR
  - 图片中的文字（layout_type=figure 且含文字）→ 区域 OCR

可插拔引擎（通过配置切换）：
  - PaddleOCR（在线/本地）：CJK 能力强，当前已在用
  - RapidOCR（本地）：Docling 已集成，ONNX 跨平台
  - Tesseract（本地）：语言支持广
  - 自定义远程 API：统一 HTTP 接口

统一 OCR 接口：
  class OCREngine(Protocol):
      def recognize(self, image: Image, lang: str) -> list[OCRResult]:
          ...

  class OCRResult(BaseModel):
      text: str
      bbox: tuple[float, float, float, float]
      confidence: float
```

复杂表格补充约束：
  - 当 OCR 返回 HTML `<table>` 时，OCRBuilder 负责先规范化为 Markdown 表格
  - 规范化过程需要正确处理 `rowspan` / `colspan`、多行表头、缺失 `<th>` 的 OCR 脏 HTML
  - 单元格中的内联文本、图片、换行应尽量保留原始顺序，避免后续表格评测和检索信息丢失

**预期效果**：相比当前全量 OCR，减少 60-70% 的 OCR 调用。

### 3.3 处理层（Processors）

处理层由一系列顺序执行的处理器组成，每个处理器负责一个关注点。顺序固定，但每个处理器可通过配置单独启用/禁用。

所有处理器实现统一接口：

```python
class Processor(Protocol):
    def process(self, doc: Document) -> Document:
        """处理文档，返回修改后的文档"""
        ...
```

#### 3.3.1 HeaderFooterProcessor — ✅ 已实现 (`processors/header_footer.py`)

```
策略：确定性优先

主方法（无 LLM，覆盖 90%+ 场景）：
  1. 使用 MetadataBuilder 提供的 PageGeometry 中的页眉/页脚区域
  2. 对识别出的区域内容做跨页相似度检查
  3. 相似度 > 阈值 → 标记为页眉/页脚并移除
  4. 识别页码模式（连续数字、罗马数字等）并移除

LLM 兜底（仅当规则置信度低时）：
  - 文档 < 3 页（不足以形成重复模式）
  - 页眉内容高度不规则（每页都不同）
  - 发送可疑区域到 LLM 做分类判断
```

#### 3.3.2 ChapterProcessor — ✅ 已实现 (`processors/chapter.py`)

```
策略：两阶段，确定性 + LLM 兜底

阶段 1（规则引擎，覆盖 70-80% 场景）：
  1. 从 MetadataBuilder 获取标题候选（基于字体大小/粗体分析）
  2. 应用编号模式匹配：
     - 第X章/第X节 → H1/H2
     - X.Y.Z 数字编号 → 按层级深度分配
     - 一/二/三 中文数字 → 根据上下文判断层级
     - (一)/(二) 带括号 → 通常为 H2/H3
  3. 验证层级一致性（无跳级、无孤儿子节）
  4. 输出置信度分数

阶段 2（LLM 精化，已实现最小可用版）：
  - 仅收集“规则未确认、但仍有章节信号”的低置信候选
    a. 有字体信号但规则未接受
    b. 有编号信号但属于弱模式（如阿拉伯数字）且缺少强字体支撑
  - 不发送全文，而是批量发送：
    a. 候选文本
    b. 字体信息
    c. 前后相邻文本
  - 单批次调用 LLM，返回 `idx -> level(0/1/2/3)` 的 JSON 结果
  - 命中的元素会标记 `llm_fallback_used=True`，供 ParseResult/API 调用统计复用

从 legacy pipeline 可复用的代码：
  - chapter_outline_core.py 中的编号检测正则
  - detect_numbering_signal / has_numbering_signal / guess_numbering_level
```

#### 3.3.3 TableProcessor — ✅ 跨页合并已实现 (`processors/table.py`)

```
当前状态：
  - PDF 原生表格提取已在 PDFProvider 中实现（PyMuPDF find_tables() → Markdown）
  - 但没有独立的 TableProcessor 类

待实现（独立 Processor）：

  文件：processors/table.py

  阶段 1（已有）：PDFProvider.find_tables() 已提取原生表格为 Markdown 格式

  阶段 2（跨页表格合并）— 高优先级：
    检测条件：
      a. 页面 N 底部有 table 元素 + 页面 N+1 顶部有 table 元素
      b. 列数一致（比较 Markdown 表格的 | 分隔符数量）
      c. 页面 N+1 顶部表格无表头行（或表头与 N 的一致）
    合并策略：
      - 保留页面 N 的表头
      - 拼接页面 N+1 的数据行（去掉重复表头和分隔行）
    实现参考：
      - 解析 Markdown 表格为行列结构
      - 比较列数和表头文字
      - 拼接后重新生成 Markdown 表格

  阶段 3（复杂表格 VLM 兜底）— 中优先级：
    - 当 PyMuPDF find_tables() 提取失败或质量差时
    - 将表格区域截图发送给 VLM
    - 交叉验证：VLM 行列数 vs 原生提取行列数
    - 需要先实现 LayoutBuilder 来标记 table 区域

输出格式：Markdown 表格
  - 简单表格 → 标准 Markdown 表格（已实现）
  - 含合并单元格 → 展平处理（待实现）
```

#### 3.3.4 ImageProcessor — ✅ 已实现 (`processors/image.py`)

```
策略：信息价值优先的分类 → 分流 → 证据优先保真

阶段 1（分类）：
  使用轻量级分类器（Docling 的 picture classifier 或简单 CNN）：
  - decorative（边框、logo、背景图、分隔线）→ 跳过或标注 "[装饰性图片]"
  - informational（示意图、流程图、照片）→ VLM 描述
  - table_image（表格截图）→ 转入 TableProcessor
  - text_image（扫描文字页）→ 仅 OCR
  - chart（柱状图、饼图、折线图）→ VLM + 图表专用 prompt

  分类特征（可用于简单规则前置过滤）：
  - 面积 < 阈值 且 宽高比极端 → 可能是装饰/图标
  - 颜色方差极低 → 可能是空白/纯色
  - 嵌入在表格单元格内 → 可能是图标/标志
  - 与 OCR/native 文本强重叠 → 更可能是文字承载型图片而非纯视觉图片
  - 位于页边、成组重复、与小图标对称出现 → 倾向低信息量附属元素

阶段 2（按类型处理）：
  informational + chart → VLM 调用，携带上下文：
    - 图片所在章节标题
    - 图片前后的文本（3-5 行）
    - OCR 结果（如果有）作为参考
    - 输出：描述文本 + 结构化数据块（表格、文字）
    - 优先要求保留可见文字、数字、标签、图例、关系，而不是只生成泛化 summary

  table_image → 转入 TableProcessor 的阶段 2-3

  text_image → OCRBuilder 已处理，此处仅校验

阶段 3（信息价值平衡）：
  - 高信息值图片：
    - 保留 OCR/native 证据
    - 保留必要的结构化文本（visible_text / markdown / chart labels）
    - 再补充简洁的语义描述，服务检索与理解
  - 低信息值图片：
    - 跳过 VLM 或仅保留最小引用
  - 灰区图片：
    - 可以调用 VLM 判断其是否承载独立信息价值

注意：
  - 不能把“像界面截图”直接等同于“应删除”
  - 关键判断是：删除该元素后，文档的事实、结构、结论、步骤是否受损

预期效果：企业文档中 30-50% 图片为低信息量元素，可跳过 VLM；同时保留真正有价值的图文信息。

VLM Correction 架构（2026-04-06 实现，2026-04-08 简化）：

核心假设：VLM 同时看到原图和 OCR 文本，信息量严格大于 OCR，因此
VLM 输出优先级高于 OCR。

处理流程（统一路径，无分支）：
  1. 对每个非 skipped 的 image 元素，收集 overlapping OCR evidence
  2. VLM 接收图片 + OCR evidence，返回三类独立输出：
     - visible_text → vlm_corrected_text（修正后的文本）
     - markdown → vlm_corrected_table（修正后的表格）
     - summary → 图片描述（仅当携带独立语义信息时保留）
  3. 被 VLM 覆盖的 OCR 元素标记 skip_render（避免重复）
  4. 渲染层根据 metadata 分别输出文本/表格/描述

设计决策记录（2026-04-08）：
  - 删除了 is_fullpage_scan 检测和 _apply_vlm_supplement 分支。
    原因：(a) OCR Builder 层已正确处理全页扫描图标记；
    (b) supplement 模式丢弃了 VLM 的 table 和 description 输出，
    违反了"VLM > OCR"的核心假设；(c) 第二层检测的启发式不可靠
    （3 个 OCR 元素重叠就触发，无面积检查）。
  - 所有非 skipped 图片统一走 correction 路径，代码更简洁，
    VLM 三类输出全部保留。

✅ 已实现 — VLM Review Processor（页面级审校，`processors/vlm_review.py`）：
  在 ImageProcessor 之后运行，对选定页面进行页面级 VLM 审校：
  - 渲染页面为图片（PyMuPDF，200 DPI），连同当前提取摘要发送给 VLM
  - VLM 以"审校者"角色返回结构化修正（fix_text / add_missing / fix_table）
  - 修正 in-place 更新元素内容，标记 source="vlm"
  - 每页 1 次 VLM 调用，成本可控
  - 触发条件（选择性，非全量）：
    - SCANNED / MIXED 页面（OCR 结果可能有错误）
    - NATIVE 页面文本极少（疑似矢量渲染文本丢失）
  - 可通过 `processors.vlm_review.review_all_pages=true` 强制全量
  - 成本保护：`max_pages_per_doc=50`（默认）
  - 并发执行 ThreadPoolExecutor，DOCX 模式跳过
```

#### 3.3.5 FormulaProcessor — ✅ 已实现 (`processors/formula.py`)

```
文件：processors/formula.py

策略：正则化 Unicode→LaTeX 格式统一（无需 GPU）

当前实现：五种高置信度变换，按特异度从高到低排列：

  1. 温度归一化：
     (\d+)\s*[℃|°C] → $\d+^{\circ}\mathrm{C}$
     例：30℃ → $30^{\circ}\mathrm{C}$

  2. 化学式检测 + Unicode 下标转换：
     检测元素符号（118 种）+ Unicode 下标字符序列
     例：H₂SiCl₂ → $\mathrm{H_{2}SiCl_{2}}$
     保护：无下标的纯文本不触发（Fe and Cu → 不变）

  3. 微量单位：
     (\d+)\s*μ[LgmM] → $\d+\,\mathrm{\mu X}$
     裸单位 μg/mL → $\mathrm{\mu g/mL}$

  4. 独立数学符号（仅在 $...$ 之外转换）：
     ≥ → $\ge$, ≤ → $\le$, ± → $\pm$, × → $\times$ 等

  5. LaTeX 碎片整理：
     PyMuPDF 提取的碎片化 LaTeX（如 $ {}^{13} $C）合并为 $^{13}C$
     元素 + 分离下标（如 CDCl$ _3 $）合并为 $CDCl_{3}$

不做的事：
  - 不转换散文中的上下标（脚注 ¹²³ 等）
  - 不自动检测/包裹任意数学表达式
  - 不修改已正确的 LaTeX 内容

未来扩展方向：
  - 使用公式识别模型（UniMERNet）处理图片中的公式
  - 更深度的 LaTeX 碎片合并和 \mathrm{} 语义包裹
  - 行内公式 vs 独立公式分类

效果（2026-04-07）：
  - en_text_01: edit_dist 0.283→0.170 (↓40%)
  - en_text_02: edit_dist 0.088→0.051 (↓42%)
  - zh_text_02: edit_dist 0.238→0.166 (↓30%)
```

#### 3.3.6 LineUnwrapProcessor — ✅ 已实现 (`processors/line_unwrap.py`)

```
文件：processors/line_unwrap.py

策略：规则优先（三层递进架构）

当前实现（Tier 1 — 规则 + 启发式，两遍处理）：

  Pass 1 — 跨元素合并（2026-04-07 新增）：
    PyMuPDF 常将同一段落的每一行提取为独立 PageElement。
    遍历每页相邻 text 元素，满足以下条件时合并为单个元素：
    - 同页、同字体（font key 完全匹配）
    - 非 heading、非 skip_render
    - 前一元素行尾无句末标点（不是段落结束）
    - 后一元素不是新列表项（但前一元素是列表项的续行可合并）
    - 垂直间距 ≤ 2× 页面典型行间距（间距大 → 段落间隔，不合并）
    合并后 bbox 取并集，content 用语言感知拼接（CJK 无空格，英文加空格）

  Pass 2 — 元素内换行修复（原有逻辑）：
    遍历 Document 中所有 text 元素，检查内部的 \\n 硬换行：

    规则判断（覆盖 80-90% 场景）：
      - 行尾有句末标点（。！？!?.;；）→ 不合并，是正常段落结束
      - 行尾无句末标点 且 下一行非空行：
        - 中文：当前行长度与正文行平均长度相近（±20%）→ 合并
        - 英文：下一行首字母小写 → 合并
      - 行尾是连字符(-) 且 下一行首字母小写 → 合并去连字符
      - 列表项检测：下一行匹配 bullet/编号模式 → 不合并
      - heading 元素跳过：不处理标题类元素

  正文行平均长度：统计全文正文字体行的长度中位数（在两遍之前一次性计算）

  待增强（仍属 Tier 1）：
    - 缩进变化检测：保留行首空白，缩进不同 → 不合并
    - 连续短行检测：多行均远短于均值 → 可能是列表，不合并

演进方向（见 3.3.9 节"结构角色识别"设计）：
  - Tier 2：文档内自归纳，prefix shape mining → 自动发现列表/标题族
  - Tier 3：LLM 兜底（config: line_unwrap.llm_fallback=true，默认关闭）

  注意：中文文本的换行问题与英文不同——中文没有 word-break，
  但 PDF 提取时可能在不合理的位置断行（如句子中间）。
```

#### 3.3.7 TextCleanProcessor — ✅ 已实现 (`processors/text_clean.py`)

```
已实现：
  1. CJK 空格修复（从 legacy pipeline _fix_chinese_spaces 迁移）
  2. 编码修复（Windows-1252 C1 范围 → Unicode 映射）
  3. 控制字符清理
  4. 多余空白规范化
```

#### 3.3.8 ReadingOrderProcessor — ❌ 待实现（低优先级）

```
文件：processors/reading_order.py

大多数中文文档是单列布局，自然从上到下的阅读顺序即可。
仅当处理多栏 PDF（报纸、杂志）时才需要。

实现方案（简单版）：
  - 同一页面内的元素已按 bbox.y0 排序（PDFProvider 提取顺序）
  - 检测多栏：同一 y 位置有多个不重叠的 text 元素 → 多栏
  - 多栏时按列分组，列内按 y 排序，列间按 x 排序
```

#### 3.3.9 结构角色识别 — 设计讨论（跨 ChapterProcessor / LineUnwrapProcessor）

```
核心思路：
  不是"列出所有标题/列表格式"，而是"识别文档里哪些行在扮演结构角色"。
  从"枚举规则"切换到"文档内自归纳"，对格式漂移更稳健。

  该方案影响 ChapterProcessor（标题检测）和 LineUnwrapProcessor（列表项检测），
  可作为两者共享的结构分析基础设施。

方案设计（四步）：

  Step 1 — Prefix Shape 归一化
    把每行拆成 prefix + body，将前缀归一成 token shape：
      第<CN_NUM><KW>    →  第一章、第三节、第二条 ...
      <CN_NUM>、        →  一、二、三 ...
      （<CN_NUM>）      →  （一）（二）...
      <ARABIC>.<ARABIC> →  1.1、2.3 ...
      <ARABIC>)         →  1)、2) ...
      <ROMAN>.          →  I.、II. ...
      <BULLET>          →  •、-、* ...
    其中 <KW> 是一组很小的通用词：章/节/条/款/项/部分。
    不需要穷举所有可能文本，只需做形状抽象。

  Step 2 — 文档内重复前缀族挖掘
    同类 shape 在全文反复出现，且满足以下条件时，认定为结构标记族：
      - 位置分布规律（例如均匀分布在文档中）
      - 字体一致（name/size/bold 相同）
      - 缩进一致
      - 后续文本长度分布相似
    这比手工列规则更泛化——新样式也会被自动聚成一类。

  Step 3 — 多信号打分
    不问"它是不是匹配某个模式"，而问"它像不像标题/列表项"：
      - 前缀 shape 是否重复出现
      - 是否形成连续序列（一、二、三 / 1.1、1.2、1.3）
      - 字体是否更大/更粗
      - 缩进是否稳定
      - 是否短句（远短于正文平均行长）
      - 前后空白是否异常大
      - 下一行是否像正文（小写/CJK 续行）
      - 同层兄弟是否风格一致

  Step 4 — 全局最优化
    逐行独立判断最容易误伤。更好的做法是全篇一起看，给每行标签：
      body / heading_h1 / heading_h2 / list_item_l1 / list_item_l2
    加一致性约束：
      - H1 后面更可能跟 H2/正文，不太可能直接跳到 H4
      - 一、二、三 应该成组出现
      - 第一条、第二条 应该有序
      - 列表项的缩进和前缀风格应相近
    可先用简单 DP/beam search 实现，不一定需要复杂模型。

与现有系统的整合：
  - 保留少量"强规则"做高精度锚点
    像 第X章、第一条、（一） 等高频公文模式仍然保留，
    但它们只是锚点，不是全部系统。

  - ChapterProcessor 改造路径：
    1. 先做 prefix shape mining（Step 1-2）
    2. 再做 global scoring（Step 3-4）
    3. 低置信候选交给 LLM fallback（已有 config 支持）

  - LineUnwrapProcessor 受益：
    结构角色识别的结果可直接用于换行判断——
    已标记为 heading/list_item 的行不参与合并。

LLM 的定位：
  不是让 LLM 替代规则，而是分层协作：
    1. 轻量规则做候选提取（高召回）
    2. LLM 判断候选的类型（heading / list_item / body / metadata），
       利用上下文、层级关系、语义连贯性
    3. 全局一致性修正（层级不跳级、同类列表成组出现）
```

### 3.4 组装层（Assembly）

#### ChapterAssembler — ✅ 已实现 (`assembly/chapter.py`)

```
输入：ProcessedDocument（含章节标记）
输出：
  - final.md（完整文档）
  - index.md（目录）
  - chapters/ch_01.md, ch_02.md, ...（按 H2 切分的章节文件）
  - images/（所有图片资源）

逻辑：
  1. 按 ChapterProcessor 标记的章节边界切分
  2. 生成目录（标题 + 链接）
  3. 图片路径统一为相对路径
  4. 可复用 legacy pipeline 的 chapter_output_packager.py 的成熟逻辑
```

#### CrossReferenceResolver — ✅ 已实现 (`assembly/crossref.py`)

```
文件：assembly/crossref.py

功能：
  1. 图片-图注关联
     - 检测 "图 X" / "Figure X" / "图表 X" 模式
     - 将图注文本关联到最近的图片元素
  2. 表格-表题关联
     - 检测 "表 X" / "Table X" 模式
     - 将表题文本关联到最近的表格元素

当前实现范围：
  - 已支持同页内图片/表格 caption 识别与最近元素关联
  - 已在 MarkdownRenderer 中消费：
    - 图片 caption 渲染为图片下方说明
    - 表格 caption 渲染在表格上方
  - 被识别为 caption 的原始 text 元素会跳过重复渲染

后续增强：
  - 脚注-引用关联
  - 跨页图注/表题关联
```

#### MarkdownRenderer — ✅ 已实现 (`assembly/markdown.py`)

```
输入：ProcessedDocument
输出：Markdown 文本

渲染规则：
  - 正文 → 段落文本
  - 标题 → # / ## / ### （按 ChapterProcessor 分配的层级）
  - 表格 → Markdown 表格格式
    | 列1 | 列2 | 列3 |
    |-----|-----|-----|
    | 数据 | 数据 | 数据 |
  - 图片 → ![VLM描述](images/xxx.png)
  - 公式 → $...$ 或 $$...$$
  - 代码 → ```lang ... ```
  - 列表 → - / 1. 格式

图片渲染原则补充：
  - 输出应优先保留图片中的有效文本证据，而不是只保留泛化 alt text
  - 对文字重图片、图表、流程图，应尽量让输出可被全文检索命中
  - 对低信息量图片，可采用最小占位或直接省略，避免污染检索语料
  - 对灰区截图，优先保留其中承载业务信息的部分，而不是机械复制界面壳层文案
```

### 3.5 验证层（Verification）

#### HallucinationDetector — ✅ 已实现 (`verification/hallucination.py`)

```
文件：verification/hallucination.py

检测对象：VLM 输出的文字内容

实现方案：
  1. 对每个有 VLM 描述的图片元素：
     a. 如果同一区域有 OCR 结果（source="ocr"），提取 OCR 文字
     b. 如果有原生提取文字，提取原生文字
  2. 计算 VLM 描述中的文字内容与 OCR/原生文字的编辑距离
  3. 偏差 > 阈值（config: hallucination_threshold=0.3）→ 标记为 low_confidence
  4. 在 element.metadata["vlm_confidence"] 中存储置信度
  5. 在 element.metadata["vlm_vs_ocr_distance"] 中存储编辑距离

  对表格的特殊处理：
  - 比对 VLM 表格的行数/列数 vs 原生提取的行数/列数
  - 行列数不一致 → low_confidence

  可复用已有代码：
  - eval/metrics.py 的 compute_edit_distance()
```

#### CompletenessChecker — ✅ 已实现 (`verification/completeness.py`)

```
文件：verification/completeness.py

检查项（返回 warnings 列表）：
  1. 页数匹配：output 涉及的页面数 == doc.metadata.page_count
  2. 文本体量：output 字符数在 PDFProvider 提取总量的 ±20% 范围内
  3. 图片引用：所有 needs_vlm=True 且未 skipped 的图片在 output 中有引用
  4. 表格计数：doc 中 type="table" 的元素数 == output 中 Markdown 表格数
```

#### StructureValidator — ✅ 已实现 (`verification/structure.py`)

```
文件：verification/structure.py

检查项（返回 warnings 列表）：
  1. 标题层级有效：无跳级（H1 后直接 H3，中间无 H2）
  2. 无孤儿子节：无 H3 出现在任何 H2 之前
  3. 标题非空：所有 heading_level > 0 的元素有非空 content
  4. 章节文件完整：如果做了 chapter_split，验证每个章节文件非空
```

---

## 4. 配置系统

### 4.1 配置文件格式

使用 YAML 配置文件（`parserx.yaml`），所有凭据通过环境变量注入：

```yaml
# 输入层配置
providers:
  pdf:
    engine: pymupdf           # pymupdf | pdfplumber
  docx:
    engine: docling
  image:
    engine: default

# 分析层配置
builders:
  metadata:
    heading_font_ratio: 1.2   # 比正文字号大 20% 视为标题候选
    header_zone_ratio: 0.08   # 页面顶部 8% 为页眉区域
    footer_zone_ratio: 0.08   # 页面底部 8% 为页脚区域
    repetition_threshold: 0.5 # 超过 50% 页面重复 → 页眉/页脚

  layout:
    enabled: true
    model: paddleocr-online   # paddleocr-online | docling-heron | yolox | none
    # 当前阶段使用 PaddleOCR 在线服务，未来可切换到本地模型

  ocr:
    engine: paddleocr         # paddleocr | rapidocr | tesseract | remote
    lang: ch_sim+en           # OCR 语言
    endpoint: null            # 远程 OCR 端点（engine=remote 时）
    selective: true           # 启用选择性 OCR
    force_full_page: false    # 强制全页 OCR（调试用）

# 处理层配置
processors:
  header_footer:
    enabled: true
    llm_fallback: true        # 允许 LLM 兜底

  chapter:
    enabled: true
    llm_fallback: true
    confidence_threshold: 0.7 # 低于此值触发 LLM

  table:
    enabled: true
    structure_model: tableformer  # tableformer | none
    vlm_fallback: true
    cross_page_merge: true

  image:
    enabled: true
    classification: true      # 图片分类
    vlm_description: true     # VLM 描述信息性图片
    skip_decorative: true     # 跳过装饰性图片

  formula:
    enabled: false            # 默认关闭，按需开启
    model: unimernet

  line_unwrap:
    enabled: true
    llm_fallback: false       # 默认不用 LLM

  text_clean:
    enabled: true
    fix_cjk_spaces: true
    fix_encoding: true

  reading_order:
    enabled: true
    method: geometric         # geometric | model

# AI 服务配置（凭据通过环境变量）
services:
  vlm:
    provider: openai          # openai | anthropic | local
    endpoint: ${OPENAI_BASE_URL}
    model: ${VLM_MODEL:gpt-4o}
    api_key: ${OPENAI_API_KEY}
    max_concurrent: 6
    timeout: 180
    max_retries: 3

  llm:
    provider: openai
    endpoint: ${OPENAI_BASE_URL}
    model: ${LLM_MODEL:gpt-4o-mini}
    api_key: ${OPENAI_API_KEY}

# 验证层配置
verification:
  hallucination_detection: true
  completeness_check: true
  structure_validation: true
  hallucination_threshold: 0.3  # 编辑距离阈值

# 输出配置
output:
  format: markdown
  chapter_split: true
  image_dir: images
  table_format: markdown      # markdown | html
```

### 4.2 AI 服务抽象

统一的 LLM/VLM 接口，屏蔽不同提供商的差异：

```python
class LLMService(Protocol):
    async def complete(self, messages: list[Message], **kwargs) -> str: ...

class VLMService(Protocol):
    async def describe_image(self, image: Image, context: str, **kwargs) -> ImageDescription: ...

# 工厂函数根据配置创建具体实现
def create_llm_service(config: ServiceConfig) -> LLMService: ...
def create_vlm_service(config: ServiceConfig) -> VLMService: ...
```

支持的后端：OpenAI-compatible API、Anthropic API、本地模型（Ollama 等）。

---

## 5. 评估框架

### 5.1 评估指标

| 维度 | 指标 | 说明 | 参考来源 |
|------|------|------|---------|
| 文本准确性 | Edit Distance | 归一化编辑距离 | Unstructured |
| 文本准确性 | BLEU | n-gram 匹配 | docling-eval |
| 文本准确性 | ChrF++ | 字符级 F-score | LlamaParse benchmark |
| 表格质量 | TEDS | 树编辑距离相似度 | docling-eval, Marker |
| 结构质量 | Heading P/R/F1 | 标题检测精确率/召回率 | 自定义 |
| 图片描述 | LLM-as-judge | LLM 对描述质量打分（1-5） | Marker |
| 综合 | Composite Score | 加权平均 | 自定义 |
| 成本 | API Calls | 各类 API 调用次数 | 自定义 |
| 成本 | Tokens | LLM/VLM token 消耗 | 自定义 |
| 成本 | Wall Time | 端到端耗时 | 自定义 |

### 5.2 评估工作流

```
parserx eval --input docs/ --ground-truth gt/ --config parserx.yaml

工作流：
  1. 对 docs/ 下的每个文档运行解析管道
  2. 将输出与 gt/ 下对应的 ground truth 做比较
  3. 计算各维度指标
  4. 输出评估报告（JSON + 可读摘要）
```

### 5.3 A/B 对比

```
parserx compare --input docs/ --config-a base.yaml --config-b experiment.yaml

工作流：
  1. 对每个文档分别用两个配置运行
  2. 对比各维度指标
  3. 输出差异报告
  4. 高亮改进和退化的维度
```

### 5.4 回归测试

```
parserx test --suite regression

工作流：
  1. 运行预定义的文档集（含 ground truth）
  2. 比较当前结果与基线
  3. 任何指标退化超过阈值 → 测试失败
  4. 可集成到 CI
```

### 5.5 Ground Truth 管理

```
ground_truth/
  ├── chinese_gov_doc/
  │   ├── input.pdf
  │   ├── expected.md         # 期望的 Markdown 输出
  │   ├── expected_headings.json  # 期望的标题结构
  │   └── expected_tables.json    # 期望的表格（HTML 格式，用于 TEDS）
  ├── scanned_report/
  │   ├── input.pdf
  │   ├── expected.md
  │   └── ...
  └── ...

文档类别建议（每类 2-3 个文档）：
  - 中文政府/企业文档（结构化、有编号、含表格）
  - 扫描件（全页扫描）
  - 混合文档（部分扫描、部分原生）
  - 无线表格文档
  - 矢量渲染文字的 PDF
  - 含大量图片的技术文档
  - 英文文档
```

---

## 6. 成本优化策略

### 6.1 优化量化估算

以典型 200 页中文企业文档（~120 张图片）为基准：

| 优化措施 | 当前调用数 | 优化后调用数 | 减少比例 |
|---------|-----------|-------------|---------|
| 图片分类后再决定 VLM | 120 次 VLM | ~60 次 VLM | 50% |
| 选择性 OCR | 120 次 OCR | ~40 次 OCR | 67% |
| 规则化页眉页脚 | 1 次 LLM | 0 次 LLM | 100%（90%+ 文档） |
| 规则化章节检测 | 3 次 LLM | 0-1 次 LLM | 67-100% |
| 规则化换行修复 | 1 次 LLM | 0 次 LLM | 100%（95%+ 文档） |
| **合计** | **~245 次 API** | **~100 次 API** | **~60%** |

### 6.2 进一步优化路径

- 本地 OCR（RapidOCR）：消除 OCR 的 API 成本和网络延迟
- 本地布局模型（Docling Heron）：消除布局检测的远程调用
- 本地图片分类器：消除分类的远程调用
- 最终状态：仅 VLM 图片描述需要远程 API（~60 次/文档），其余全部本地化

---

## 7. 实施阶段

### Phase 1: 基础设施（第 1-2 周）

**目标**：搭建项目骨架，实现基本的文档到 Markdown 转换。

**交付物**：

```
parserx/
  ├── __init__.py
  ├── cli.py                    # CLI 入口
  ├── config/
  │   ├── __init__.py
  │   ├── schema.py             # Pydantic 配置模型
  │   └── loader.py             # YAML 加载 + 环境变量替换
  ├── models/
  │   ├── __init__.py
  │   ├── elements.py           # PageElement, Document, DocumentMetadata
  │   └── results.py            # ParseResult, EvalResult
  ├── providers/
  │   ├── __init__.py
  │   ├── base.py               # Provider 协议
  │   ├── pdf.py                # PDFProvider（PyMuPDF 字符级提取）
  │   └── docx.py               # DOCXProvider（Docling 封装）
  ├── assembly/
  │   ├── __init__.py
  │   └── markdown.py           # 基础 MarkdownRenderer
  ├── pipeline.py               # 管道编排
  └── services/
      ├── __init__.py
      ├── llm.py                # LLM 服务抽象
      └── ocr.py                # OCR 服务抽象
tests/
  └── ...
parserx.yaml                    # 默认配置
pyproject.toml                  # 项目配置
```

**关键任务**：
1. 项目结构和 pyproject.toml（依赖：pymupdf, docling, pydantic, pyyaml）
2. 配置系统：YAML 加载、环境变量替换、Pydantic 校验
3. 数据模型：PageElement、Document、DocumentMetadata
4. PDFProvider：PyMuPDF `page.get_text("dict")` 字符级提取
5. DOCXProvider：Docling 封装
6. 基础 MarkdownRenderer：文本 + 标题 + 表格的基本渲染
7. Pipeline 编排框架
8. pytest 测试基础设施

**验收标准**：给定一个简单 PDF/DOCX，能输出基本的 Markdown（含文本和标题），结构可能不完美但文本无丢失。

### Phase 2: 核心管道（第 3-5 周）

**目标**：实现核心处理器，端到端管道可用。

**新增模块**：

```
parserx/
  ├── builders/
  │   ├── __init__.py
  │   ├── metadata.py           # MetadataBuilder
  │   ├── layout.py             # LayoutBuilder
  │   └── ocr.py                # OCRBuilder（选择性 OCR）
  ├── processors/
  │   ├── __init__.py
  │   ├── base.py               # Processor 协议
  │   ├── header_footer.py      # HeaderFooterProcessor
  │   ├── chapter.py            # ChapterProcessor
  │   ├── table.py              # TableProcessor（基础版）
  │   ├── text_clean.py         # TextCleanProcessor
  │   └── reading_order.py      # ReadingOrderProcessor
  └── assembly/
      ├── chapter.py            # ChapterAssembler
      └── markdown.py           # 增强的 MarkdownRenderer
```

**关键任务**：
1. MetadataBuilder：字体统计、页面几何、编号模式、逐页类型
2. LayoutBuilder：集成 Docling Heron 布局模型
3. OCRBuilder：选择性 OCR + PaddleOCR 集成
4. HeaderFooterProcessor：几何位置 + 跨页重复
5. ChapterProcessor：字体/编号规则 + LLM 兜底
6. TextCleanProcessor：CJK 空格修复、编码修复
7. 基础 TableProcessor：原生表格提取 + 结构模型
8. ChapterAssembler：章节切分和目录生成
9. Pipeline 串联所有组件

**验收标准**：标准中文企业文档（有章节编号、有表格、有页眉页脚）能正确解析为结构化 Markdown。页眉页脚被移除，章节层级正确，表格结构基本正确。

### Phase 3: 质量提升（第 6-8 周）

**目标**：处理复杂场景，提升解析质量。

**新增模块**：

```
parserx/
  ├── processors/
  │   ├── image.py              # ImageProcessor（分类 + VLM）
  │   ├── formula.py            # FormulaProcessor
  │   └── line_unwrap.py        # LineUnwrapProcessor
  ├── assembly/
  │   └── crossref.py           # CrossReferenceResolver
  └── verification/
      ├── __init__.py
      ├── hallucination.py      # HallucinationDetector
      ├── completeness.py       # CompletenessChecker
      └── structure.py          # StructureValidator
```

**关键任务**：
1. ImageProcessor：图片分类 + 选择性 VLM 描述
2. 复杂表格处理：VLM 兜底 + 交叉验证
3. 跨页表格合并
4. FormulaProcessor：公式检测 + LaTeX 转换 ✅ (正则化归一化已实现)
5. HallucinationDetector：VLM 输出交叉校验
6. LineUnwrapProcessor：规则化换行修复
7. CrossReferenceResolver：图文关联
8. CompletenessChecker + StructureValidator

**验收标准**：能正确处理含大量图片的技术文档，复杂无线表格，矢量渲染文字的 PDF。图片描述准确，表格结构完整，VLM 幻觉可检测。

### Phase 4: 评估与优化（第 9-10 周）

**目标**：建立评估体系，数据驱动优化。

**新增模块**：

```
parserx/
  └── eval/
      ├── __init__.py
      ├── runner.py             # 评估运行器
      ├── metrics.py            # 各维度指标计算
      ├── compare.py            # A/B 对比
      └── report.py             # 报告生成
ground_truth/
  ├── chinese_gov_doc/
  ├── scanned_report/
  ├── complex_table/
  └── ...
```

**关键任务**：
1. 评估框架：benchmark runner + 指标计算（edit distance, TEDS, heading P/R/F1）
2. 制作 ground truth 文档集（10-20 个文档，覆盖各类场景）
3. 运行基线评估，确定当前质量水位
4. 基于评估结果调优（阈值、prompt、模型选择）
5. A/B 对比工具
6. 成本追踪和报告

**验收标准**：有完整的评估报告，各指标有基线数值。A/B 对比工具可用。

### Phase 5: 生产加固（第 11-12 周）

**目标**：生产可用，文档齐全。

**关键任务**：
1. 错误处理：重试逻辑、超时管理、优雅降级
2. 并发优化：页面级并行、OCR/VLM 并发调用
3. CLI 接口完善：`parserx parse`、`parserx eval`、`parserx compare`
4. 集成测试：50+ 文档多样化语料
5. 性能分析和瓶颈优化
6. API 文档和用户指南

**验收标准**：能稳定处理多样化文档语料，有完整的 CLI 接口和文档，性能满足生产需求。

### 7.1 当前阶段优化版执行顺序（基于现状重排）

> 本小节用于校准原始 Phase 规划与当前真实进度之间的差异。
> Phase 1/2 的主体能力已基本完成，当前重点是“补闭环、补护栏、再做语义化增强”。

#### 当前判断

- 端到端原型已经可运行：`parse`、`parse_to_dir`、`eval` 主链路可用。
- 当前缺的不是“能不能跑”，而是“验证层、自检能力、困难场景 fallback”。
- 3.3.9 的“结构角色识别 / 语义化章节识别”是高价值优化主线，但**不是当前硬阻塞点**。
- 在验证层未补齐前，直接大改 `ChapterProcessor` 风险偏高，退化后不易定位。

#### 推荐执行顺序

1. 先补验证层最小闭环
   - `verification/structure.py`
   - `verification/completeness.py`
   - `verification/hallucination.py`

2. 再做章节识别的语义化增强
   - `processors/chapter.py` 的 LLM fallback
   - 按 3.3.9 的“结构角色识别”思路推进

3. 然后补组装层的关联能力
   - `assembly/crossref.py`

4. 最后做优化工具和低优先级增强
   - `eval/compare.py`
   - `builders/layout.py`
   - `processors/reading_order.py`
   - `processors/formula.py`

#### 重排理由

- 验证层是后续大改章节/列表识别的安全网。
- `ChapterProcessor` 的语义化增强收益很高，但应在可度量、可校验的前提下进行。
- `CrossReferenceResolver` 对最终可读性和消费体验有价值，但不阻塞当前主链路。
- `LayoutBuilder` / `ReadingOrderProcessor` 仍属于增强项，而非当前原型闭环的缺口。
- `FormulaProcessor` 已实现正则化 Unicode→LaTeX 归一化（2026-04-07），不需 GPU。

---

## 附录 A: 技术栈选择

| 组件 | 选择 | 理由 |
|------|------|------|
| 语言 | Python 3.13 | 团队主力语言，生态丰富 |
| PDF 提取 | PyMuPDF | 字符级元数据，活跃维护 |
| DOCX 提取 | Docling | 已验证有效，MIT 许可 |
| 布局检测 | PaddleOCR 在线服务 | 现有可用，无需本地 GPU；未来可替换 |
| 表格结构 | PaddleOCR 布局分析 + VLM 兜底 | 现有可用；复杂表格走 VLM |
| 图片分类 | 启发式规则（尺寸/像素分析） | Mac CPU 可运行，无额外依赖 |
| OCR | PaddleOCR 在线服务（默认），接口可插拔 | CJK 能力强，现有服务 |
| VLM/LLM | OpenAI-compatible API（可配置） | 默认使用现有端点，可切换任意模型 |
| 配置 | YAML + Pydantic | 类型安全，可校验 |
| 测试 | pytest | 标准选择 |
| 包管理 | uv | 团队规范 |

## 附录 B: 从 legacy pipeline 可迁移的代码

来源：legacy pipeline 内部代码库

### B.1 高优先级 — 已迁移

| 来源文件 | 函数 | 迁移到 | 状态 |
|---------|------|--------|------|
| `chapter_outline_core.py` | `detect_numbering_signal()` L109-123 | `builders/metadata.py` | ✅ 已迁移 |
| `chapter_outline_core.py` | `guess_numbering_level()` L133-139 | `builders/metadata.py` | ✅ 已迁移 |
| `pdf_extract.py` | `_fix_chinese_spaces()` L49-57 | `processors/text_clean.py` | ✅ 已迁移 |
| `pdf_extract.py` | `_is_blank_image()` L145-151 | `processors/image.py` (`classify_image_file`) | ✅ 已迁移 |
| `pdf_extract.py` | `_is_trivial_raster_fragment()` L162-177 | `processors/image.py` (`classify_image_element`) | ✅ 已迁移 |
| `openai_image_reads.py` | `_encode_image()` L49-54 | `services/llm.py` (`_encode_image_data_url`) | ✅ 已迁移 |
| `openai_image_reads.py` | `_build_prompt()` L85-150 | `processors/image.py` (`_build_vlm_prompt`) | ✅ 已迁移（简化版） |
| `openai_image_reads.py` | 流式 Responses API 调用 | `services/llm.py` (`_describe_responses`) | ✅ 已迁移 |

### B.2 未迁移（待后续实现使用）

| 来源文件 | 函数 | 用途 | 对应待实现模块 |
|---------|------|------|--------------|
| `remove_headers_footers.py` | `_validate_regex()` L317-344 | 正则安全校验 | HeaderFooter LLM fallback |
| `remove_headers_footers.py` | `_ai_classify()` L225+ | AI 分类页眉页脚 | HeaderFooter LLM fallback |
| `chapter_outline_core.py` | `apply_outline_to_markdown()` L284-381 | 大纲应用到 Markdown | LLM fallback 章节重建 |
| `chapter_outline_core.py` | `is_metadata_field()` L141-155 | 元数据字段检测 | 封面/元数据页识别 |
| `image_tasks.py` | `_extract_context_windows()` L96-118 | 图片上下文提取 | 已部分迁移到 `_get_context_before` |
| `pipeline.py` | `_call_paddleocr_async()` L223+ | 异步批量 OCR | OCRBuilder 异步模式 |

### B.3 仅参考（不直接迁移）

| 来源文件 | 参考价值 |
|---------|---------|
| `pipeline.py` L99-130 | 后端工厂模式，用于插件架构设计参考 |
| `pipeline.py` L287-301 | 并发任务执行 + 状态追踪模式 |
| `pipeline.py` L1068-1176 | 层级结构推断逻辑 |
| `pdf_extract.py` L261-424 | 矢量区域捕获逻辑（PyMuPDF 专用） |

## 附录 C: 测试文档清单

测试文档需自行准备，或使用 `python -m parserx.eval.benchmark` 下载公开评估集。

| 文件 | 类型 | 测试场景 |
|------|------|---------|
| `real_doc01.docx/.pdf` | 企业文档 | 基础 DOCX/PDF 解析、章节检测 |
| `real_doc02.docx/.pdf` | 大型企业文档（~150MB） | 大文档处理、性能测试 |
| `real_doc02_pic_heavy.docx` | 图片密集文档（~211MB） | 图片分类和 VLM 描述 |
| `real_doc03.docx/.pdf` | 企业文档 | 通用测试 |
| `text_pic01.docx/.pdf` | 图文混排 | 图文关联、图片处理 |
| `text_pic01-wps.pdf` | WPS 生成的 PDF | 矢量渲染文字（P5） |
| `text_pic02.docx/.pdf` | 图文混排 | 多种图片类型处理 |
| `text_pic02-wps.pdf` / `-v2.pdf` | WPS/变体 PDF | 矢量渲染、不同 PDF 生成器 |
| `text_pic03.docx` | 图文混排 | 图片处理 |
| `公路...JTG 3362.pdf` | 扫描件 OCR（国标规范） | 扫描 PDF、复杂表格、公式 |
| `ocr01.docx/.pdf` | OCR 文档 | OCR 路径验证 |
| `pdf_text01.pdf` | 纯文本 PDF | 基础文本提取 |
| `deepseek.pdf` | 英文技术文档 | 英文支持、结构化内容 |
| `receipt*.pdf` | 票据 | 特殊格式/布局 |
| `text_table_libreoffice.pdf` | LibreOffice 生成 | 表格提取 |

标准评估集：**OmniDocBench**（`opendatalab/OmniDocBench`，HuggingFace，981 页/279 文档，含中英文）

---

## 附录 D: 决策日志

| 日期 | 决策 | 理由 |
|------|------|------|
| 2026-04-04 | 不引入本地 GPU 服务（MinerU、Docling 重模型等） | 开发阶段在 Mac 上运行，不依赖 GPU |
| 2026-04-04 | 布局检测使用 PaddleOCR 在线服务 | 现有可用服务，避免新增依赖；未来可通过接口替换 |
| 2026-04-04 | 表格结构识别优先用 PaddleOCR 能力 | 同上；复杂表格走 VLM 兜底 |
| 2026-04-04 | 图片分类使用启发式规则 | Mac CPU 可运行，不引入分类模型 |
| 2026-04-04 | VLM/LLM 使用现有 skill 端点 | 已验证可用；通过 OpenAI 兼容 API 抽象，支持替换 |
| 2026-04-04 | 以 legacy pipeline skill 为起点 | 迁移验证过的代码，不从零开始 |
| 2026-04-04 | 配置文件改为 YAML + 环境变量 | 替代硬编码，支持 A/B 测试 |
| 2026-04-05 | 结构角色识别（3.3.9）定为高价值优化分支，而非当前硬阻塞 | 当前主链路已可运行，先补验证层护栏，再做 ChapterProcessor 语义化升级更稳妥 |
| 2026-04-05 | ChapterProcessor 长期方向调整为“候选召回 + LLM 语义判别 + 全局一致性修正” | 单靠枚举 regex 很难覆盖真实文档的格式漂移，应更充分利用大模型的语言理解与适应性 |
| 2026-04-05 | 评测集采用“公开 + 私有”双轨策略 | 公开集用于可复现回归，私有集用于验证真实业务文档表现 |
| 2026-04-05 | 下一阶段优先级从“加新模块”切换到“评测闭环 + 报告可见性” | 主链路已跑通，当前更需要稳定基线和 A/B 对比能力 |

---

## 附录 E: 实施状态追踪

> **新 session 入口**：先读本节，获取当前状态、已实现清单和下一步优先级。
> 每次迭代后更新本节。

### 当前状态总览

**阶段**：Phase 3（质量提升）进行中 | **测试**：132 passing, 4 skipped

### 原型闭环判断

- 当前 ParserX 已具备可运行的 E2E prototype：PDF/DOCX → 处理链 → Markdown / chapter split / eval。
- 当前缺口主要在验证护栏、语义化 fallback、图文关联，而不是主链路缺失。
- 因此下一轮工作的重点应从“补主链路”切换为“补闭环 + 提高鲁棒性”。

### 当前工作区理解（供新 session 接力）

- 主链路已经具备：解析、章节切分、验证告警、评估框架。
- `ChapterProcessor` 已进入“两阶段”状态：
  - 阶段 1：规则检测
  - 阶段 2：低置信候选批量 LLM fallback
- `CrossReferenceResolver` 基础版已可用：
  - 图注/表题识别
  - 渲染去重
  - review 中提出的误匹配边界已补测试
- 当前本地测试基线：
  - `uv run pytest -q`
  - 结果：`132 passed, 4 skipped`
- 当前最大的工程缺口不是再补一个模块，而是把评测集和对比流程真正用起来。

### 模块实现状态

| 层 | 模块 | 文件 | 状态 | 说明 |
|----|------|------|------|------|
| 配置 | ParserXConfig | `config/schema.py` | ✅ | YAML + `${VAR:default}` 环境变量 + Pydantic |
| 数据 | PageElement/Document | `models/elements.py` | ✅ | 含 FontInfo, PageType, DocumentMetadata |
| 输入 | PDFProvider | `providers/pdf.py` | ✅ | PyMuPDF 字符级提取（字体元数据 + 表格 + 图片） |
| 输入 | DOCXProvider | `providers/docx.py` | ✅ | Docling OOXML 解析，样式→heading_level，表格→Markdown；支持 .doc（LibreOffice 转换）；DOCX 模式跳过几何依赖处理器 |
| 分析 | MetadataBuilder | `builders/metadata.py` | ✅ | 字体统计 + 7 种编号模式 + 逐页类型 |
| 分析 | OCRBuilder | `builders/ocr.py` | ✅ | 选择性 OCR（仅 scanned/mixed 页） |
| 分析 | ImageExtractor | `builders/image_extract.py` | ✅ | 选择性图片提取（跳过装饰性） |
| 分析 | LayoutBuilder | — | ❌ | 当前由启发式 + PaddleOCR 替代 |
| 处理 | HeaderFooterProcessor | `processors/header_footer.py` | ✅ | 几何 + 跨页重复，无 LLM |
| 处理 | ChapterProcessor | `processors/chapter.py` | ✅ | 字体+编号规则 + 低置信候选批量 LLM fallback |
| 处理 | ImageProcessor | `processors/image.py` | ✅ | 启发式分类 + 并发 VLM 描述 |
| 处理 | TextCleanProcessor | `processors/text_clean.py` | ✅ | CJK 空格 + C1 编码 + 控制字符 |
| 处理 | TableProcessor | `processors/table.py` | ✅ | 跨页表格合并（列数匹配 + 表头去重） |
| 处理 | LineUnwrapProcessor | `processors/line_unwrap.py` | ✅ | 规则化换行修复，中文/英文续行 + 连字符合并 |
| 处理 | FormulaProcessor | `processors/formula.py` | ✅ | Unicode→LaTeX 正则归一化（温度、化学式、单位、数学符号、碎片整理） |
| 处理 | ContentValueProcessor | `processors/content_value.py` | ✅ | 信息价值评分 + 低价值元素抑制（仅 PDF；DOCX 模式跳过） |
| 处理 | ReadingOrderBuilder | `builders/reading_order.py` | ✅ | 多栏布局检测 + 阅读顺序重排（仅 PDF；DOCX 模式跳过） |
| 处理 | ReadingOrderProcessor | — | ❌ | 低优先级，大部分文档单列 |
| 组装 | MarkdownRenderer | `assembly/markdown.py` | ✅ | text/table/image/formula 渲染 |
| 组装 | ChapterAssembler | `assembly/chapter.py` | ✅ | H1 切分 + index.md |
| 组装 | CrossReferenceResolver | `assembly/crossref.py` | ✅ | 图注/表题关联并接入 Markdown 渲染；已补误匹配防护与边界测试；脚注关联仍待扩展 |
| 服务 | LLM/VLM Service | `services/llm.py` | ✅ | Responses API + Chat Completions 自动切换 |
| 服务 | OCR Service | `services/ocr.py` | ✅ | PaddleOCR 在线 API |
| 验证 | HallucinationDetector | `verification/hallucination.py` | ✅ | VLM 描述 vs OCR/native 交叉校验，支持数字不一致告警 |
| 验证 | CompletenessChecker | `verification/completeness.py` | ✅ | 页码、文本体量、图片引用、表格计数完整性检查 |
| 验证 | StructureValidator | `verification/structure.py` | ✅ | 标题跳级/孤儿子节/空标题/章节文件完整性检查 |
| 评估 | Eval Framework | `eval/metrics.py` + `eval/runner.py` | ✅ | edit dist + heading P/R/F1 + table cell F1 + cost |
| 评估 | Ground Truth Strategy | `docs/evaluation.md` | ✅ | 已明确公开/私有双轨策略；仓库内公开评测集仍待补充 |
| CLI | parse + eval | `cli.py` | ✅ | `parserx parse` + `parserx eval` |

### 设计原则（已验证）

1. **确定性优先，AI 兜底**：页眉页脚和章节检测均无 LLM，覆盖 70-80% 场景
2. **选择性处理**：图片分类 80% 为装饰性被跳过；OCR 仅对非原生页调用
3. **规则不过拟合**：只用普适性强的硬规则（日期/TOC 排除），不针对测试文档优化
4. **弱信号需强信号配合**：阿拉伯数字编号仅在有字体信号时才接受为标题

### VLM 使用方式

```bash
# Set credentials in .env or export directly:
OPENAI_BASE_URL="https://your-api-endpoint/v1" \
OPENAI_API_KEY="your-api-key" \
VLM_MODEL="gpt-5.4-mini" \
uv run parserx parse input.pdf -o output_dir/ --split-chapters -c parserx.yaml -v
```

不设置环境变量时 VLM 步骤自动跳过。

### 下一步优先级（优化版）

| 优先级 | 任务 | 涉及文件 | 设计详情 |
|--------|------|---------|---------|
| **P0** | 建立公开评测集首批样本 | `ground_truth_public/` + `docs/evaluation.md` | 先提交一小批可开源样本，形成可复现基线 |
| **P0** | ParseResult/CLI warning 展示增强 | `models/results.py` + `cli.py` | 让 warnings、api_calls、fallback 命中情况更可见 |
| **P1** | A/B 对比工具 | `eval/compare.py` | 支持 `llm_fallback=false/true` 的配置对比 |
| **P1** | ChapterProcessor fallback 精化 | `processors/chapter.py` | 在有评测基线后再加强 prompt、批次策略和全局层级一致性修正 |
| **P2** | CrossReferenceResolver 扩展 | `assembly/crossref.py` | 继续补脚注/引用关联与跨页 caption 关联 |
| **P2** | StructureRoleAnalyzer（新建议模块） | `builders/structure_roles.py` 或 `processors/structure_roles.py` | 对应 3.3.9；作为 Chapter/LineUnwrap 共享基础设施 |
| ~~P3~~ | ~~FormulaProcessor~~ | `processors/formula.py` | ✅ 已实现（正则化归一化，不需 GPU） |
| **P3** | LayoutBuilder | `builders/layout.py` | 需本地 GPU 或远程服务 |
| **P3** | ReadingOrderProcessor | `processors/reading_order.py` | 大部分文档单列 |

### 评测策略结论

- 仓库内评测：
  - 使用 `ground_truth_public/`
  - 目标是可复现、可分享、适合回归
- 仓库外评测：
  - 使用本地私有 ground truth
  - 目标是覆盖真实业务文档
- 每次非平凡解析改动后，都应同时跑：
  - `uv run pytest -q`
  - `uv run parserx eval ground_truth_public`
  - `uv run parserx eval "$PARSERX_PRIVATE_GT_DIR"`
- 章节/LLM 类改动，除了质量指标，还应看：
  - warnings 数量
  - `api_calls.llm`
  - wall time

### 下一次新会话建议开场动作

1. 先运行 `git status --short`，确认是否基于当前未提交工作继续。
2. 再运行 `uv run pytest -q`，确认本地基线仍是 `132 passed, 4 skipped`。
3. 阅读本节、[evaluation.md](./evaluation.md) 和 3.3.9 节。
4. 如果进入实现，优先从公开评测集、CLI warning 展示或 `eval/compare.py` 开始。
5. 改 ChapterProcessor / CrossReferenceResolver 时，不只看测试是否通过，也要同步看公开集和私有集评测结果。

### 验证数据

**多文档测试（5 个 PDF）**：

| 文档 | 页数 | 正文字体 | 标题数 | 图片（信息/装饰） | VLM 调用 |
|------|------|---------|--------|-----------------|---------|
| pdf_text01.pdf（采购文件） | 55 | SimSun 12pt | 57 | 0/0 | 0 |
| real_doc01.pdf（企业文档） | 56 | SimSun 12pt | 53 | 0/0 | 0 |
| text_pic01.pdf（图文混排） | 36 | MicrosoftYaHei 13.9pt | 46 | 20/83 | 5 |
| deepseek.pdf（英文技术） | 1 | PingFang 16pt | 0 | 4/8 | 0 |
| text_table_libreoffice.pdf | 3 | HiraginoSans 16pt | 3 | 0/0 | 0 |

**关键质量验证**：
- "第一章" → `# 第一章` (H1) ✓
- "一、项目概况" → `## ...` (H2) ✓
- 页码 "- 3 -" → 已移除 ✓
- 日期行 "2026 年3 月至" → 不被误判为标题 ✓
- 公章图片 → VLM 描述："红色圆形印章...广西楼栋集团有限公司" ✓
- 图片选择性：103 张图 → 83 装饰性跳过 → 5 次 VLM（vs legacy pipeline 206 次 API）✓
- 跨页表格合并：pdf_text01 中 5 组跨页表格成功合并 ✓
- DOCX 样式 heading_level 直接映射，ChapterProcessor 自动跳过已有标题 ✓

**历史本地评测记录（非仓库内固定公开基线）**：

| 文档 | Edit Dist | Char F1 | Heading F1 | Table F1 | 说明 |
|------|-----------|---------|------------|----------|------|
| text_table01 | 0.025 | 0.988 | 1.000 | — | 纯文本+标题，近乎完美 |
| text_table_libreoffice | 0.197 | 0.890 | 0.800 | 1.000 | 表格完美；标题拆分导致精确率偏低 |
| pdf_text01_tables | 0.438 | 0.720 | — | 0.660 | 跨页合并有效，但文本/表格重复提取 |
| deepseek | 0.317 | 0.796 | — | — | ChatGPT 导出，含 UI 元素 |
| **平均** | **0.244** | **0.848** | | | |

说明：
- 这组数字来自此前的本地 ground truth 评测记录，不代表仓库内当前已提交的公开基线。
- 后续应以 `ground_truth_public/` + 私有评测目录的双轨策略来维护新的稳定基线。

### 迭代历史

| 迭代 | 日期 | 内容 | 提交 |
|------|------|------|------|
| #0-1 | 2026-04-04 | Phase 1：项目骨架、配置、PDFProvider、TextClean、Pipeline、CLI | a1e7c43 |
| #2 | 2026-04-04 | Phase 2a：MetadataBuilder、HeaderFooter、ChapterProcessor | da613bf |
| #3 | 2026-04-04 | Phase 2b：ChapterProcessor 调优（187→57）、ChapterAssembler | 7d64044 |
| #4 | 2026-04-04 | Phase 2c：OCRBuilder、ImageProcessor、多文档验证 | 0f03db1 |
| #5 | 2026-04-04 | Phase 3a：ImageExtractor、VLM 描述流程 | 157b1d5 |
| #6 | 2026-04-04 | Phase 3b：VLM 端到端验证、Responses API 自动切换 | ef2a87f |
| #7 | 2026-04-04 | Phase 3c：并发 VLM、评估框架 | 851101d |
| #8 | 2026-04-04 | 文档补齐：README、架构文档修正、实施状态重写 | 2b6a82c |
| #9 | 2026-04-04 | Phase 3d：DOCXProvider、TableProcessor 跨页合并、Ground Truth 基线评估 | 待提交 |
| #10 | 2026-04-04 | 评估打磨：修复双重解析、表格指标、4 个完整 GT、99 测试 | 见下方 |
| #11 | 2026-04-05 | Phase 3e：LineUnwrapProcessor 接入主链路，补中文列表保护；该轮结束时全量测试 109 passed / 4 skipped | 待提交 |
| #12 | 2026-04-05 | Phase 3f：补验证层最小闭环、接入 `parse_result()`、补 OCR bbox 透传；该轮结束时全量测试 113 passed / 4 skipped | 待提交 |
| #13 | 2026-04-05 | Review 收口：解耦 verification/eval 依赖、统一验证入口、补负向测试与 OCR bbox 分支测试，当前全量测试 122 passed / 4 skipped | 待提交 |
| #14 | 2026-04-08 | DOCX 流式文档处理路径修复：跳过几何依赖处理器（HeaderFooter、CodeBlock、ContentValue）和无意义 builder（MetadataBuilder、OCR、ReadingOrder）；新增 .doc → .docx 转换（LibreOffice）；新增 text_report01 评测样本 | 待提交 |
