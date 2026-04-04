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

**问题**：当前 doc-refine 对页眉页脚、章节检测、换行修复都调用 LLM，但这些问题的 80-90% 可用规则解决。

**原则**：每个处理器采用两阶段设计——先用确定性代码处理能确定的部分，仅对剩余的模糊情况回退到 AI。

**应用**：

| 处理器 | 确定性方法 | AI 兜底条件 |
|--------|-----------|-------------|
| HeaderFooter | 几何位置 + 跨页重复检测 | <3 页或无法形成稳定模式 |
| Chapter | 字体大小/粗体/编号格式匹配 | 规则匹配置信度 <0.7 |
| LineUnwrap | 标点/句式分析 | 中文长段落断行位置模糊 |
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

### 2.3 交叉验证，不盲信

**问题**：VLM 可能篡改原文（P6）。

**原则**：VLM 输出与 OCR/原生提取结果交叉比对。偏差大的标记为低置信。

```
VLM 提取的文字 ←对比→ OCR/原生提取的文字
  │                          │
  └── 编辑距离 > 阈值 ──→ 标记为低置信，保留两个版本
```

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
  4. 逐页分类：native / scanned / mixed（基于文本字符数 vs 图片覆盖率）
```

#### DOCXProvider — ❌ 待实现

```
输入：DOCX 文件路径
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
     - 需处理旋转元数据（参考 doc-refine fix_rotation.py）
  
  注意事项：
  - Docling 在 doc-refine 中已验证有效，可参考 pipeline.py L739-770
  - OOXML 有显式样式信息，标题检测是确定性的，ChapterProcessor 可跳过
  - 需在 pipeline.py `_extract()` 中注册 .docx 路由
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
   - 可复用 doc-refine 中 chapter_outline_core.py 的编号检测正则
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
  - MetadataBuilder 标记为 scanned 的页面 → 全页 OCR
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

阶段 2（LLM 精化，仅当阶段 1 置信度 < 0.7）：
  - 不发送全文，而是发送：
    a. 标题候选列表（行号 + 文字 + 字体信息）
    b. 每个候选的上下文（前后 2-3 行）
    c. MetadataBuilder 检测到的编号模式
  - 一次 LLM 调用（替代当前的三次）
  - LLM 输出：确认/修正标题层级

从 doc-refine 可复用的代码：
  - chapter_outline_core.py 中的编号检测正则
  - detect_numbering_signal / has_numbering_signal / guess_numbering_level
```

#### 3.3.3 TableProcessor — ⚠️ 部分实现

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
策略：分类 → 分流

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

阶段 2（按类型处理）：
  informational + chart → VLM 调用，携带上下文：
    - 图片所在章节标题
    - 图片前后的文本（3-5 行）
    - OCR 结果（如果有）作为参考
    - 输出：描述文本 + 结构化数据块（表格、文字）

  table_image → 转入 TableProcessor 的阶段 2-3

  text_image → OCRBuilder 已处理，此处仅校验

预期效果：企业文档中 30-50% 图片为装饰性，可跳过 VLM 调用。
```

#### 3.3.5 FormulaProcessor — ❌ 待实现（低优先级）

```
策略：模型检测 + 专业识别
  - 默认关闭（config: formula.enabled: false）
  - 需要 LayoutBuilder 先标记 formula 区域
  - 使用公式识别模型（UniMERNet 或类似）将图片转为 LaTeX
  - 行内公式 → $...$，独立公式 → $$...$$
  - 依赖本地 GPU 或远程推理服务，当前阶段不实现
```

#### 3.3.6 LineUnwrapProcessor — ❌ 待实现

```
文件：processors/line_unwrap.py

策略：规则优先

实现方案：
  遍历 Document 中所有 text 元素，检查内部的硬换行：

  规则判断（覆盖 95%+ 场景）：
    - 行尾有句末标点（。！？!?.;；）→ 不合并，是正常段落结束
    - 行尾无句末标点 且 下一行非空行：
      - 中文：当前行长度与正文行平均长度相近（±20%）→ 合并
      - 英文：下一行首字母小写 → 合并
    - 行尾是连字符(-) 且 下一行首字母小写 → 合并去连字符

  正文行平均长度：从 MetadataBuilder 的 font_stats 推算
    - 页面宽度 / 正文字号 ≈ 每行字符数上限
    - 或统计所有正文行的长度中位数

  LLM 兜底：默认关闭。仅当 line_unwrap.llm_fallback=true 时启用。

  注意：中文文本的换行问题与英文不同——中文没有 word-break，
  但 PDF 提取时可能在不合理的位置断行（如句子中间）。
```

#### 3.3.7 TextCleanProcessor — ✅ 已实现 (`processors/text_clean.py`)

```
已实现：
  1. CJK 空格修复（从 doc-refine _fix_chinese_spaces 迁移）
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
  4. 可复用 doc-refine 的 chapter_output_packager.py 的成熟逻辑
```

#### CrossReferenceResolver — ❌ 待实现

```
文件：assembly/crossref.py

功能：
  1. 图片-图注关联
     - 检测 "图 X" / "Figure X" / "图表 X" 模式
     - 将图注文本关联到最近的图片元素
  2. 脚注-引用关联
     - 检测上标数字/符号
     - 关联到页脚的脚注文本
  3. 表格-表题关联
     - 检测 "表 X" / "Table X" 模式
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
```

### 3.5 验证层（Verification）

#### HallucinationDetector — ❌ 待实现

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

#### CompletenessChecker — ❌ 待实现

```
文件：verification/completeness.py

检查项（返回 warnings 列表）：
  1. 页数匹配：output 涉及的页面数 == doc.metadata.page_count
  2. 文本体量：output 字符数在 PDFProvider 提取总量的 ±20% 范围内
  3. 图片引用：所有 needs_vlm=True 且未 skipped 的图片在 output 中有引用
  4. 表格计数：doc 中 type="table" 的元素数 == output 中 Markdown 表格数
```

#### StructureValidator — ❌ 待实现

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
4. FormulaProcessor：公式检测 + LaTeX 转换
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

## 附录 B: 从 doc-refine 可迁移的代码

来源目录：`/Users/xuyun/IEC/skills/doc-refine/scripts/`

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

来源目录：`/Users/xuyun/IEC/doc_special/sample_docs/`

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
| 2026-04-04 | 以 doc-refine skill 为起点 | 迁移验证过的代码，不从零开始 |
| 2026-04-04 | 配置文件改为 YAML + 环境变量 | 替代硬编码，支持 A/B 测试 |

---

## 附录 E: 实施状态追踪

> **新 session 入口**：先读本节，获取当前状态、已实现清单和下一步优先级。
> 每次迭代后更新本节。

### 当前状态总览

**阶段**：Phase 3（质量提升）进行中 | **测试**：67 passing | **提交**：7 次 | **源文件**：31 个

### 模块实现状态

| 层 | 模块 | 文件 | 状态 | 说明 |
|----|------|------|------|------|
| 配置 | ParserXConfig | `config/schema.py` | ✅ | YAML + `${VAR:default}` 环境变量 + Pydantic |
| 数据 | PageElement/Document | `models/elements.py` | ✅ | 含 FontInfo, PageType, DocumentMetadata |
| 输入 | PDFProvider | `providers/pdf.py` | ✅ | PyMuPDF 字符级提取（字体元数据 + 表格 + 图片） |
| 输入 | DOCXProvider | — | ❌ | 需 Docling 封装，详见 3.1 节设计 |
| 分析 | MetadataBuilder | `builders/metadata.py` | ✅ | 字体统计 + 7 种编号模式 + 逐页类型 |
| 分析 | OCRBuilder | `builders/ocr.py` | ✅ | 选择性 OCR（仅 scanned/mixed 页） |
| 分析 | ImageExtractor | `builders/image_extract.py` | ✅ | 选择性图片提取（跳过装饰性） |
| 分析 | LayoutBuilder | — | ❌ | 当前由启发式 + PaddleOCR 替代 |
| 处理 | HeaderFooterProcessor | `processors/header_footer.py` | ✅ | 几何 + 跨页重复，无 LLM |
| 处理 | ChapterProcessor | `processors/chapter.py` | ✅ | 字体+编号规则，LLM fallback 未做 |
| 处理 | ImageProcessor | `processors/image.py` | ✅ | 启发式分类 + 并发 VLM 描述 |
| 处理 | TextCleanProcessor | `processors/text_clean.py` | ✅ | CJK 空格 + C1 编码 + 控制字符 |
| 处理 | TableProcessor | — | ⚠️ | 基础表格在 PDFProvider 中，无独立 Processor |
| 处理 | LineUnwrapProcessor | — | ❌ | 详见 3.3.6 节设计 |
| 处理 | FormulaProcessor | — | ❌ | 低优先级，需本地 GPU |
| 处理 | ReadingOrderProcessor | — | ❌ | 低优先级，大部分文档单列 |
| 组装 | MarkdownRenderer | `assembly/markdown.py` | ✅ | text/table/image/formula 渲染 |
| 组装 | ChapterAssembler | `assembly/chapter.py` | ✅ | H1 切分 + index.md |
| 组装 | CrossReferenceResolver | — | ❌ | 图文关联、脚注关联 |
| 服务 | LLM/VLM Service | `services/llm.py` | ✅ | Responses API + Chat Completions 自动切换 |
| 服务 | OCR Service | `services/ocr.py` | ✅ | PaddleOCR 在线 API |
| 验证 | HallucinationDetector | — | ❌ | 详见 3.5 节设计 |
| 验证 | CompletenessChecker | — | ❌ | 详见 3.5 节设计 |
| 验证 | StructureValidator | — | ❌ | 详见 3.5 节设计 |
| 评估 | Eval Framework | `eval/metrics.py` + `eval/runner.py` | ✅ | edit dist + heading P/R/F1 + cost |
| CLI | parse + eval | `cli.py` | ✅ | `parserx parse` + `parserx eval` |

### 设计原则（已验证）

1. **确定性优先，AI 兜底**：页眉页脚和章节检测均无 LLM，覆盖 70-80% 场景
2. **选择性处理**：图片分类 80% 为装饰性被跳过；OCR 仅对非原生页调用
3. **规则不过拟合**：只用普适性强的硬规则（日期/TOC 排除），不针对测试文档优化
4. **弱信号需强信号配合**：阿拉伯数字编号仅在有字体信号时才接受为标题

### VLM 使用方式

```bash
OPENAI_BASE_URL="https://your-api-endpoint/v1" \
OPENAI_API_KEY="REDACTED_API_KEY" \
VLM_MODEL="gpt-5.4-mini" \
uv run parserx parse input.pdf -o output_dir/ --split-chapters -c parserx.yaml -v
```

不设置环境变量时 VLM 步骤自动跳过。

### 下一步优先级

| 优先级 | 任务 | 涉及文件 | 设计详情 |
|--------|------|---------|---------|
| **P0** | DOCXProvider | `providers/docx.py` | 3.1 节有详细方案 |
| **P0** | 跨页表格合并 (TableProcessor) | `processors/table.py` | 3.3.3 节有详细方案 |
| **P0** | Ground truth 样本 + 基线评估 | `ground_truth/` | 5.5 节有格式说明 |
| **P1** | HallucinationDetector | `verification/hallucination.py` | 3.5 节有详细方案 |
| **P1** | LineUnwrapProcessor | `processors/line_unwrap.py` | 3.3.6 节有详细方案 |
| **P1** | ChapterProcessor LLM fallback | `processors/chapter.py` | 3.3.2 节"阶段 2" |
| **P2** | CrossReferenceResolver | `assembly/crossref.py` | 3.4 节有设计 |
| **P2** | CompletenessChecker | `verification/completeness.py` | 3.5 节有设计 |
| **P2** | StructureValidator | `verification/structure.py` | 3.5 节有设计 |
| **P2** | A/B 对比工具 | `eval/compare.py` | 5.3 节有设计 |
| **P3** | FormulaProcessor | `processors/formula.py` | 需本地 GPU |
| **P3** | LayoutBuilder | `builders/layout.py` | 需本地 GPU 或远程服务 |
| **P3** | ReadingOrderProcessor | `processors/reading_order.py` | 大部分文档单列 |

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
- 图片选择性：103 张图 → 83 装饰性跳过 → 5 次 VLM（vs doc-refine 206 次 API）✓

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
| #8 | 2026-04-04 | 文档补齐：README、架构文档修正、实施状态重写 | 见下方 |
