# ParserX 需求文档

## 1. 背景与动机

### 1.1 为什么需要更好的文档解析工具

企业文档（采购文件、技术规范、标准文件、合同等）包含丰富的结构化信息：正文、表格、图片、公式、章节层级。当前的文档解析工具存在明显的两极分化：

- **简单提取**（pdfplumber、PyMuPDF 等）：速度快、成本低，但丢失表格结构、图片含义、章节层级
- **高保真解析**（我们现有的 legacy pipeline skill）：质量较好，但每个文档需要 200+ 次 API 调用（OCR + VLM + LLM），成本高、速度慢、不可评估

我们需要一个在质量和成本之间取得更好平衡的文档解析工具。

### 1.2 下游使用场景

| 场景 | 对解析质量的要求 |
|------|-----------------|
| **知识库构建** | 文档被切分为可检索的结构化块，章节层级决定切分边界 |
| **检索增强生成（RAG）** | 块质量直接影响 LLM 回答质量，表格/图片信息丢失导致答非所问 |
| **文档理解** | 自动摘要、对比分析、合规检查需要完整的文档结构 |
| **数据分析** | 从表格/图表中提取结构化数据用于统计分析 |

### 1.3 目标输出格式

**高保真 Markdown**，兼顾机器消费和人类可读性：

- 表格：Markdown 表格格式（非 HTML、非图片）
- 图片：保留原始图片，附加 VLM 生成的描述作为 alt text
- 公式：LaTeX 格式（`$...$` 行内，`$$...$$` 独立公式）
- 章节层级：Markdown 标题（`#` / `##` / `###`）
- 代码块：围栏代码块

该格式可被下游 LLM、向量数据库、搜索引擎和人类读者直接消费。

---

## 2. 现状分析

### 2.1 现有 legacy pipeline 技能架构

legacy pipeline 采用四阶段流水线架构：

```
阶段 1: 提取（Extract）
  ├── DOCX → Docling 原生 OOXML 解析
  └── PDF  → PyMuPDF4LLM 提取文本/表格/图片

阶段 2: OCR + 任务生成
  ├── PaddleOCR 在线服务对所有图片做 OCR
  ├── 生成图片解读任务（image_tasks）
  └── 章节结构检测

阶段 3: AI 图片解读
  └── OpenAI VLM API 逐张解读图片（并发 6，每张最长 180 秒）

阶段 4: 组装 + 章节重建
  ├── 合并 OCR + VLM 结果到 Markdown
  ├── 三轮 LLM 章节重建（检测轮廓 → 提取大纲 → 审查）
  └── 输出 final.md, index.md, chapters/*.md, images/
```

**关键服务依赖**：

| 服务 | 用途 | 配置方式 |
|------|------|---------|
| OpenAI VLM（自定义端点） | 图片解读、章节重建 | 硬编码在 `config.py:12-14` |
| PaddleOCR（在线服务） | 图片 OCR | 硬编码在 `pipeline.py:41-44` |
| LibreOffice | WMF/EMF 矢量图转换 | 系统依赖 |

### 2.2 从代码中发现的关键问题

**凭据硬编码**：`config.py` 硬编码了 API URL、模型名和 API Key。`chapter_outline_core.py` 重复了相同的硬编码。

**无差别 OCR**：每张提取的图片都调用 PaddleOCR，无论是照片、装饰元素还是实际含文字的图片。一个 200 页文档可能有 100+ 张图片，意味着 100+ 次 OCR API 调用。

**无差别 VLM**：每张图片都通过 `openai_image_reads.py` 做完整的 VLM 解读。加上 OCR，单个文档可触发 200+ 次 API 调用。

**章节重建成本高**：3 次独立 LLM 调用（`STRUCTURE_SCHEMA_SYSTEM` 检测文档轮廓、`FULL_DOC_SYSTEM` 提取大纲、`FULL_DOC_REVIEW_SYSTEM` 审查）。大文档需截断至 8000 行（`chapter-max-lines`），截断后 LLM 看不到全貌。

**矢量文本启发式阈值**：`pdf_extract.py` 使用 >8 个矢量碎片的硬编码阈值触发全页截图。该启发式脆弱——部分包含多个小图片的页面被不必要地全页截图，而某些矢量渲染文字的页面因碎片数不足而被遗漏。

**无自动化评估**：仅 `collect_quality_warnings` 做基本完整性检查（图片描述比率、重复检测）。没有 TEDS、编辑距离、BLEU 等度量，没有回归测试，没有与 ground truth 的比较。

**OCR 提供商单一**：仅支持 PaddleOCR 在线服务，无本地回退选项，无法切换引擎。

**无跨页表格合并**：跨页的表格被切分为两个独立片段。

**DOCX 旋转元数据丢失**：Docling 不保留 OOXML 中的图片旋转元数据，需要专门的 `fix_rotation.py` 变通处理。

**扫描/原生二选一**：`detect_document_profile` 在文档级别分类（`scan_dominant` / `mixed_with_images` / `plain_text`），而非逐页判断。

---

## 3. 痛点全景

### P1: 页眉页脚/页码检测

频率法要求 >50% 的页面重复才能识别；对不规则页眉（如左右页交替页眉）失效。LLM 正则生成阶段可能产生过宽的匹配模式（`remove_headers_footers.py` 中有 `_OVERLY_BROAD_PATTERNS` 安全检查，但仍有过度删除风险）。

### P2: 章节重构

三次 LLM 调用；`chapter_outline_core.py` 中的启发式规则覆盖了常见的中文编号模式，但无法穷举所有变体。大文档的 8000 行截断导致 LLM 无法看到完整的章节模式。

### P3: 大文档全局 vs 局部矛盾

全文超出 LLM 上下文窗口。分片处理丢失文档级别的模式（如编号方案在不同章节间的变化）。这是当前章节重建和全文润色的核心限制。

### P4: 复杂中文表格

无线表格、嵌套单元格、合并单元格在中文政府/企业文档中非常常见。PyMuPDF 的 `find_tables()` 对这些情况处理不佳。PaddleOCR 的布局分析对无线表格识别率低。

补充要求：当 OCR 或外部工具直接返回 HTML 表格片段时，系统需要将其稳定规范化为 Markdown 表格，并保留复杂表格的关键信息结构，包括合并单元格、多级表头和单元格内顺序内容。

### P5: PDF 中矢量渲染的文本/表格线

部分 PDF 生成器（Word、WPS）将中文文字渲染为矢量路径。PyMuPDF 提取时得到空白文本。当前方案是全页截图 + OCR 兜底，成本高且信息损失大。

### P6: VLM 幻觉/篡改原文

VLM 在图片解读过程中可能"纠正"原始文字，引入错误。当前没有校验机制来对比 VLM 输出与源文本。`openai_image_reads.py` 中仅通过 prompt 约束（"只描述可见内容"）和低 temperature（0.1）来缓解，但无法根除。

### P7: 海量 OCR 调用

每张图片无论内容类型都调用 OCR。装饰性图片、照片、不含文字的示意图都在浪费 OCR 调用。

### P8: 海量 VLM 调用

每张图片一次 VLM 调用（约 120 次/文档）+ 章节重建 3 次 + 页眉页脚 1 次 + 视觉换行 1 次。单个文档总计可超 200 次 LLM/VLM API 调用。

### P9: 视觉换行处理

PDF 中的硬换行需要 LLM 判断是否为段内换行。额外的 LLM 调用；对中文文本效果不稳定（中文没有 word-break，但有不合理分段的问题）。

### P10: 空白/装饰性图片检测

空白检测使用像素标准差 <1.0 的硬编码阈值；装饰图标使用尺寸阈值判断。导致双向误判：有意义的小图标被误杀，大面积纯色背景图未被过滤。

### P11: 模型/服务不可替换

API 端点和模型名硬编码在代码中。无法 A/B 测试不同模型效果，无法在不改代码的情况下切换提供商。

### P12: 无自动化评估

优化后的效果只能靠人工判断。无法量化改进幅度，无法做回归测试，无法对比不同配置的效果。这是最大的工程短板。

### P13: DOCX 图片旋转丢失

Docling 不保留 OOXML 中的图片旋转元数据。需要 `fix_rotation.py` 手动修复，逻辑脆弱、易遗漏。

### P14: 跨页表格与图文关联

跨页表格未合并，被视为两个独立表格。图片与图注分属不同页时关联丢失。

### P15: 扫描页/原生页混合处理

以文档为单位判断类型（扫描/原生），而非逐页判断。实际文档可能 90% 页面是原生文本，10% 是扫描页，逐文档分类导致对其中一种类型处理不当。

---

## 4. 行业调研

### 4.1 商业方案：LlamaParse

LlamaIndex 的商业文档解析服务，GenAI 原生设计。

**核心能力**：
- 4 个解析档位：Fast / Cost Effective / Agentic / Agentic Plus，自动选择策略
- 6 种底层解析模式：纯文本、LLM 辅助、视觉模型（LVM）、Agent 推理、布局 Agent、文档级模式
- 8 种输出格式：text / markdown / JSON / XLSX / PDF / images / screenshots / structured
- 跨页表格合并（文档级模式支持 `merged_from_pages` / `merged_into_page` 标记）
- 130+ 文件格式，100+ 语言

**精细控制**：
- 页面裁剪（归一化比率 0.0-1.0）
- 小文字保留（`preserve_very_small_text`）
- 斜体文字过滤（`ignore_diagonal_text`）
- 列布局保持（`do_not_unroll_columns`）
- 超时控制：基础 300 秒 + 每页额外 30 秒
- 失败容忍度：`allowed_page_failure_ratio` 默认 10%

**质量基准**：ChrF++ 81%，编辑相似度 78%，$0.003/页。

**对 ParserX 的启示**：跨页表格合并和精细控制参数是质量的基本要求；多档位策略（快速/标准/高质量）值得借鉴。

### 4.2 开源方案：LiteParse（LlamaIndex）

TypeScript 实现，完全本地运行，零云依赖。Apache-2.0 许可。

**核心技术**：
- 双引擎架构：PDF.js（文本提取）+ PDFium（页面渲染/图片边界提取）
- 智能选择性 OCR：不是对所有页面做 OCR，而是检测哪些区域需要 OCR
  - 全页 OCR：仅当页面原生文本 <100 字符或有嵌入图片时
  - 定向 OCR：仅对检测到乱码字体的区域做 OCR
  - 跳过 OCR：有充足干净文本且无图片的页面
- 乱码字体检测：分析 Unicode 块分布（Private Use Area 字符、Arabic/Latin 混合、C1 控制字符等）
- Adobe Glyph List 恢复：拦截 PDF.js 的字形标记，通过 Adobe 字形列表映射到 Unicode
- 网格投影算法：锚点检测 + 列对齐，有效保持多列布局的空间关系
- OCR 去重：多层空间重叠检查（已有文本 2pt 容差，乱码区域 5pt 容差）

**局限性**：只做文本提取，不做语义理解、表格结构化、章节重建、图片描述。

**对 ParserX 的启示**：选择性 OCR 方案是我们控制成本的关键参考；乱码字体检测和 OCR 去重值得直接借鉴。

### 4.3 开源方案：Docling（IBM）

MIT 许可，LF AI & Data 基金会托管。57k stars。

**核心架构**：
- 插件化设计（基于 pluggy）：OCR、布局、表格结构、图片描述均可插拔
- 双流水线：`StandardPdfPipeline`（传统多模型）/ `VlmPipeline`（VLM 单模型）
- `DoclingDocument`（Pydantic v2）作为通用中间表示

**模型矩阵**：

| 任务 | 模型 | 备注 |
|------|------|------|
| 布局检测 | Heron（默认）/ Egret-Medium/Large/XLarge | Heron 最快，Egret 更准 |
| 表格结构 | TableFormer v1/v2 | FAST 和 ACCURATE 两种模式 |
| OCR | RapidOCR / EasyOCR / Tesseract / macOS Vision | 多引擎可选 |
| 图片分类 | document_figure_classifier_v2 | 分类图片类型 |
| 图片描述 | SmolVLM-256M / Granite Vision / Pixtral / Qwen | VLM 可选 |
| VLM 转换 | GraniteDocling-258M / SmolDocling / DeepSeek-OCR 等 | 整页 VLM |

**评估框架（docling-eval）**：
- 布局检测：mAP@[0.5:0.95]
- 表格结构：TEDS（Tree Edit Distance Score）
- 阅读顺序：ARD（Average Reading Distance）
- 文本质量：BLEU、编辑距离

**对 ParserX 的启示**：我们已经使用 Docling 做 DOCX 提取；其插件架构和评估框架是直接参考模板；MIT 许可无商业风险。

### 4.4 开源方案：Marker

GPL-3.0 许可。Provider → Builder → Processor → Renderer 四层架构。

**核心设计**：
- 29 个顺序执行的处理器（Processor），每个处理一个关注点
- Surya 模型全家桶：布局检测、文本检测、文本识别、表格结构、OCR 错误检测
- `--use_llm` 混合模式：可选启用 LLM 增强（支持 Gemini、Claude、OpenAI、Ollama）
- 28+ 种 Block 类型（Pydantic 模型）

**关键性能数据**：

| 方法 | 平均耗时 | 启发式得分 | LLM 评分 (1-5) |
|------|---------|-----------|----------------|
| **Marker** | 2.84s | **95.67** | **4.24** |
| LlamaParse | 23.35s | 84.24 | 3.98 |
| Mathpix | 6.36s | 86.43 | 4.16 |
| Docling | 3.70s | 86.71 | 3.70 |

**表格提取（FinTabNet，Tree Edit Distance）**：

| 方法 | TEDS |
|------|------|
| Marker | 0.816 |
| Marker + use_llm | **0.907** |
| Gemini alone | 0.829 |

**评估框架**：
- `benchmarks/overall/`：端到端转换质量（启发式 + LLM-as-judge）
- `benchmarks/table/`：表格提取质量（TEDS）
- `benchmarks/throughput/`：吞吐量和 VRAM 使用

**对 ParserX 的启示**：四层架构设计最为清晰，是我们架构的首要参考；LLM 混合模式证明了"基础模型 + LLM 增强困难场景"的路线；自带评估框架是标杆。GPL-3.0 许可需注意，不能直接复用代码，但可以参考设计。

### 4.5 开源方案：MinerU

AGPL-3.0 许可。58k stars。上海 AI 实验室出品。

**三引擎架构**：

| 引擎 | 描述 | OmniDocBench 得分 | 硬件要求 |
|------|------|-------------------|---------|
| pipeline | 传统多模型管道，无幻觉 | 86+ | 最低 4GB VRAM 或纯 CPU |
| vlm-engine | MinerU2.5 VLM（1.2B 参数） | 90+ | 最低 8GB VRAM |
| hybrid-engine | 原生文本提取 + VLM 结合 | 90+ | 最低 8GB VRAM |

**模型矩阵**：

| 任务 | 模型 |
|------|------|
| 布局检测 | PPDocLayoutV2 |
| 公式识别 | UniMERNet（英文为主）/ PP-FormulaNet-Plus-M（中文） |
| OCR | PytorchPaddleOCR（109 种语言） |
| 表格分类 | PaddleTableClsModel（有线/无线分类） |
| 有线表格识别 | UnetTableModel + OCR |
| 无线表格识别 | SLANet-Plus + OCR |
| 页面方向检测 | PaddleOrientationClsModel |

**v3.0 生产特性**：滑动窗口机制（支持万页文档）、流式写入、线程安全并发、多 GPU 路由（`mineru-router`）。

**对 ParserX 的启示**：CJK 支持一流；表格有线/无线分类是好思路；公式识别（UniMERNet）可作为模块集成；hybrid-engine 验证了混合路线。AGPL-3.0 许可有传染性，不宜直接依赖，但可参考设计。

### 4.6 开源方案：Unstructured

Apache-2.0 许可。14k stars。

**核心设计**：
- Element 类型体系：每个元素带类型（Title / NarrativeText / Table / Image / Formula 等）、坐标、置信度、来源标注（`detection_origin`：pdfminer / yolox / ocr_tesseract 等）
- 三策略 PDF 解析：`hi_res`（布局模型 + OCR）/ `fast`（纯 PDFMiner）/ `ocr_only`
- 自动策略选择：检查 PDF 是否有可提取文本，是否需要表格推断
- YOLOX 布局检测 + Microsoft Table Transformer 表格结构

**评估框架**：
- `calculate_accuracy()`：文本提取准确率
- `calculate_element_type_percent_match()`：元素类型分类准确率
- `ObjectDetectionEvalProcessor`：布局检测评估
- `TableEvalProcessor`：表格提取评估

**对 ParserX 的启示**：Element 级别的置信度和来源标注（`detection_origin`）对验证层有参考价值；Apache-2.0 许可安全。

### 4.7 值得关注的专项工具

| 工具 | 擅长领域 | 许可 | 对 ParserX 的价值 |
|------|---------|------|------------------|
| **Surya** | OCR + 布局 + 阅读顺序 + 表格，90+ 语言 | GPL-3.0 | Marker 的底层引擎 |
| **GOT-OCR 2.0** | 通用 VLM-OCR，580M 参数 | Apache-2.0 | 轻量 VLM 方案，可替代 OpenAI 做图片解读 |
| **UniMERNet** | 数学公式识别（图片→LaTeX） | — | MinerU 的公式引擎，可独立集成 |
| **DocTR** | 文字检测+识别，TF/PyTorch 双后端 | Apache-2.0 | 比 Tesseract 更现代的 OCR 方案 |
| **pdfplumber** | 原生 PDF 字符级精确提取 | MIT | 适合做 fast 模式的文本提取 |
| **olmOCR** | VLM 大规模 PDF 线性化（Qwen2-VL 7B） | Open | 展示了 VLM 整页理解路线 |
| **Nougat** | 学术论文 → LaTeX/Markdown | MIT | 学术文档效果极好，通用性差 |

---

## 5. 设计目标

### 5.1 输出质量

高保真 Markdown 输出。文字零丢失（以编辑距离衡量）。表格用 Markdown 表格格式。图片保留原图并附加描述。公式用 LaTeX。章节层级正确。

### 5.2 表格完整性

完整的表格结构，包括合并单元格、嵌套单元格。支持跨页表格合并。以 TEDS 分数衡量。

### 5.3 图片信息还原

每张信息性图片获得准确描述（含义、表格数据、文字内容）。装饰性图片被正确识别并跳过。这是我们相对于开源方案的差异化优势。

补充约束（2026-04-06 迭代澄清）：
- 图片处理的目标不是“给所有图片写一句泛化描述”，而是把图片中对文档理解有贡献的信息转成可检索、可比对、可引用的文本表示。
- 对含文字、图表、流程、坐标、标注、图例、关键数字的图片，应优先保留其中的显式证据，再生成补充性的语义描述。
- 对低信息量图片（纯装饰、分隔、logo、无独立语义的小图标），可以弱化为最小占位或跳过，但不能影响正文理解。
- 对截图类图片不能仅按“像不像界面”决定保留与否；关键判断标准是它是否承载独立的信息价值。
- 图片描述应优先服务下游检索与理解，而不是追求视觉修辞或冗长叙述。

### 5.4 章节结构正确性

正确的标题层级（H1/H2/H3）。以标题精确率/召回率衡量。

### 5.5 成本控制

从每文档 200+ 次 API 调用降至 ~30-50 次。核心策略：能用确定性代码解决的不用 AI。

### 5.6 可评估性

内建自动化评估框架。支持回归测试和 A/B 对比。包含文本、表格、结构、图片描述四个维度的度量。

### 5.7 可配置性

模型、服务、策略均可通过配置文件切换。凭据通过环境变量注入。每个处理阶段可单独启用/禁用。

### 5.8 可靠性

VLM 输出有交叉校验机制。幻觉可检测并标记。处理失败时有优雅降级方案。

### 5.9 信息价值优先的内容取舍

ParserX 的目标不是机械地“保留所有可见元素”，也不是激进地“删除所有疑似噪声”，而是尽可能保留对文档主体有贡献的信息。

判断原则：
- 能传递事实、观点、数据、结论、步骤、约束的信息，保留。
- 能表达文档结构的内容，保留，例如标题、图注、表题、列表锚点。
- 只是交互、导航、装饰、品牌壳子、重复模板、容器文案，且脱离原载体后几乎没有独立信息价值的内容，可弱化或删除。
- 灰区内容优先尝试“提取其中的有效信息”，而不是直接删除。

这一定义适用于正文、页眉页脚、截图、图片中的文字、图表说明和 UI/阅读器壳层噪声，是后续去噪、图片保真、检索友好输出的统一准则。

---

## 6. 行业趋势总结

### 6.1 双轨并行是共识方向

所有头部项目都在走"传统多模型管道 + VLM 混合"路线：
- MinerU：pipeline / vlm-engine / hybrid-engine 三选一
- Docling：StandardPdfPipeline / VlmPipeline 双流水线
- Marker：基础模型 + `--use_llm` 可选增强

纯规则/模型管道和纯 VLM 各有优劣，混合是最优解。

### 6.2 评估框架是标配

| 项目 | 评估能力 |
|------|---------|
| Marker | 启发式得分 + LLM-as-judge，自带 benchmark 数据集 |
| MinerU | OmniDocBench（1355 页，9 类文档，text/table/formula 三维度） |
| Docling | docling-eval（mAP、TEDS、ARD、BLEU） |
| Unstructured | text accuracy、element type match、table eval |

没有评估框架的解析工具无法持续优化。

### 6.3 选择性处理是成本控制的关键

LiteParse 的选择性 OCR（仅对乱码/缺文字区域 OCR）是成本控制的典范。同样的思路可以推广到 VLM 调用（仅对信息性图片调 VLM）和 LLM 调用（仅对规则无法处理的困难情况调 LLM）。

### 6.4 许可证影响技术选型

| 许可 | 项目 | 商业风险 |
|------|------|---------|
| MIT | Docling | 无风险，可自由使用 |
| Apache-2.0 | Unstructured, DocTR, GOT-OCR | 无风险 |
| GPL-3.0 | Marker, Surya | 传染性，不能作为库依赖 |
| AGPL-3.0 | MinerU | 强传染性，网络服务也受限 |

参考设计思路不受许可限制，但代码复用需注意许可兼容性。
