# ParserX

**高保真文档解析，面向知识库、RAG 和文档分析。**

ParserX 将 PDF 和 DOCX 文件转换为结构良好的 Markdown — 保留章节层级、表格、图片和公式 — 同时通过选择性、规则优先的处理策略控制 API 成本。

[English](README.md) | 中文

## 要解决什么问题

当前的文档解析工具分为两个极端：

| 方案 | 代表工具 | 优点 | 缺点 |
|------|---------|------|------|
| **简单提取** | pdfplumber、PyMuPDF | 速度快、成本低、无 GPU | 丢失表格结构、章节层级、图片含义 |
| **全 AI 解析** | 逐页 LLM 流水线 | 质量较高 | 单文档 200+ 次 API 调用，慢且昂贵，结果不确定 |

ParserX 走第三条路：**确定性规则优先，AI 仅在必要时介入**。流水线通过字体元数据、页面几何信息和编号模式分析，在无需任何 LLM 调用的情况下完成 70–80% 的结构检测。AI（OCR、VLM）仅在必要时选择性调用 — 只对扫描页做 OCR，只对信息性图片调用 VLM — 相比暴力全量调用，API 成本降低 10–20 倍。

## 与同类工具的对比

| 能力 | PyMuPDF | Marker | MinerU | Docling | **ParserX** |
|------|---------|--------|--------|---------|-------------|
| 章节/标题检测 | - | 启发式 + LLM | 布局模型 | 布局模型 | **字体分析 + 编号模式**（7 种中英文模式，无需 LLM） |
| 页眉页脚移除 | - | - | 布局模型 | 布局模型 | **几何定位 + 跨页重复检测**（无需 LLM） |
| 表格提取 | 基础 | Surya | GPU 模型 | TableFormer | **PyMuPDF 原生 + 跨页合并** |
| 扫描件 OCR | - | Surya (GPU) | PaddlePaddle (GPU) | EasyOCR | **选择性 OCR**（仅处理扫描/混合页，后端可插拔） |
| 图片处理 | - | - | - | SmolVLM | **启发式分类 + 选择性 VLM**（跳过 80%+ 装饰性图片） |
| 需要 GPU | 否 | 是 | 是 | 可选 | **否**（使用远程 API 服务） |
| 许可证 | AGPL | GPL-3.0 | AGPL | MIT | **MIT** |

### 核心差异化

- **无需 GPU。** 笔记本即可运行。OCR 和 VLM 使用可配置的远程 API 服务。
- **CJK 优先设计。** 中文编号模式（第X章、一/二/三、(一)(二)(三) 等）、CJK 空格修复、中英文混排支持均为一等公民。
- **质量可度量。** 内建评估框架，支持文本编辑距离、标题 P/R/F1、表格单元格 F1、处理成本统计。支持 [OmniDocBench](https://huggingface.co/datasets/opendatalab/OmniDocBench) 公开基准测试。
- **精简可审计。** 约 35 个源文件，无重度框架依赖。每个处理步骤都是独立模块，可以单独阅读、测试和替换。

## 处理流程

```
                    ┌─────────────────────────────────────────────┐
  PDF/DOCX ──────▶  │  Provider  │  提取文本、表格、图片            │
                    │            │  附带字符级元数据                 │
                    └──────┬──────────────────────────────────────┘
                           │
                    ┌──────▼──────────────────────────────────────┐
                    │  Builders  │  字体统计、页面类型分类、          │
                    │            │  选择性 OCR、图片提取             │
                    └──────┬──────────────────────────────────────┘
                           │
                    ┌──────▼──────────────────────────────────────┐
                    │ Processors │  页眉页脚 → 章节 → 表格 →        │
                    │            │  图片 → 文本清洗                  │
                    └──────┬──────────────────────────────────────┘
                           │
                    ┌──────▼──────────────────────────────────────┐
                    │  Assembly  │  Markdown 渲染、                 │
                    │            │  章节切分                        │
                    └─────────────────────────────────────────────┘
```

**Provider（输入层）** — 从 PDF（PyMuPDF 字符级提取，支持基于间距的词空格恢复）或 DOCX（Docling OOXML 解析）中提取原始内容。每个文本片段都携带字体名、字号和加粗标记 — 这些元数据驱动下游的标题检测，无需 LLM。

**Builders（分析层）** — 分析提取的内容：
- *MetadataBuilder* 计算字体统计（正文字体 vs 标题候选），检测 7 种编号模式
- *OCRBuilder* 将每页分类为原生/扫描/混合，仅对需要的页面进行 OCR — 对混合页做文本去重，避免重复提取
- *ImageExtractor* 从文档中提取图片，跳过装饰性图片

**Processors（处理层）** — 逐步转换标注后的文档：
- *HeaderFooterProcessor* 通过几何区域 + 跨页频率移除重复的页眉页脚文本
- *ChapterProcessor* 根据字体大小比例 + 编号信号分配标题层级
- *TableProcessor* 合并跨页断裂的表格
- *ImageProcessor* 分类图片（装饰性/信息性/图表），仅对有信息量的图片调用 VLM 生成描述
- *TextCleanProcessor* 修复 CJK 空格伪影和编码问题

**Assembly（组装层）** — 将处理后的文档渲染为 Markdown，可选按章节切分输出。

## 快速开始

```bash
git clone https://github.com/your-org/ParserX.git
cd ParserX
uv sync
```

### 基本用法

```bash
# 解析 PDF 到标准输出
uv run parserx parse document.pdf

# 输出到文件
uv run parserx parse document.pdf -o output.md

# 按章节切分输出
uv run parserx parse document.pdf -o output_dir/ --split-chapters

# 使用配置文件 + 详细日志
uv run parserx parse document.pdf -c parserx.yaml -v
```

### 启用 VLM 图片描述

设置环境变量（或使用 `.env` 文件）指向 OpenAI 兼容的 API 端点：

```bash
OPENAI_BASE_URL="https://api.openai.com/v1" \
OPENAI_API_KEY="your-key" \
uv run parserx parse document.pdf -c parserx.yaml -v
```

未设置时，VLM 步骤自动跳过，其余功能正常工作。

### 启用 OCR（扫描件处理）

```bash
PADDLE_OCR_ENDPOINT="your-paddleocr-endpoint" \
PADDLE_OCR_TOKEN="your-token" \
uv run parserx parse scanned.pdf -c parserx.yaml
```

未配置 OCR 凭据时，扫描页将被跳过。所有可用环境变量参见 `.env.example`。

### 真实端到端测试

ParserX 现在包含一组 live E2E pytest 测试，会读取 `.env` 中的真实凭据并实际调用
在线 OCR、LLM、VLM 服务：

```bash
uv run pytest tests/test_live_e2e.py -q
```

如果 `.env` 中已经配置好相关凭据，执行 `uv run pytest tests/ -q`
时也会自动把这组 live 测试一起跑掉。

## 评估

ParserX 内建评估框架：

```bash
# 对 ground truth 目录批量评估
uv run parserx eval ground_truth/ -o report.md

# 下载公开基准测试集（OmniDocBench 子集）
uv pip install 'parserx[bench]'
uv run python -m parserx.eval.benchmark --output-dir ground_truth_public
```

评估指标：归一化编辑距离、字符 F1、标题精确率/召回率/F1、表格单元格 F1、处理成本。

调优 VLM 时，可以直接用 `parserx compare` 做提示词或模型对比，例如：
- `--set-a processors.image.vlm_prompt_style=strict_bilingual`
- `--set-b processors.image.vlm_prompt_style=strict_en`
- `--set-a services.vlm.model=model-a`
- `--set-b services.vlm.model=model-b`

## 配置

所有设置在 `parserx.yaml` 中管理，凭据通过环境变量注入：

```yaml
services:
  vlm:
    endpoint: ${OPENAI_BASE_URL}
    model: ${VLM_MODEL:gpt-5.4-mini}
    api_key: ${OPENAI_API_KEY}

builders:
  ocr:
    engine: paddleocr          # 或 "none" 禁用 OCR
    endpoint: ${PADDLE_OCR_ENDPOINT}
    token: ${PADDLE_OCR_TOKEN}
    selective: true             # 仅对扫描/混合页做 OCR

processors:
  header_footer:
    enabled: true
  chapter:
    enabled: true
  image:
    vlm_description: true
    skip_decorative: true
```

完整默认配置参见 [`parserx.yaml`](parserx.yaml)。

## 项目状态

ParserX 正在积极开发中。核心流水线已可用并经过测试。

| 组件 | 状态 | 说明 |
|------|------|------|
| PDF 提取（PyMuPDF） | ✅ 已完成 | 字符级字体元数据 |
| DOCX 提取（Docling） | ✅ 已完成 | 样式 → 标题层级映射 |
| 页眉页脚移除 | ✅ 已完成 | 几何定位 + 跨页重复检测 |
| 章节/标题检测 | ✅ 已完成 | 字体比例 + 7 种编号模式 |
| 表格提取 + 跨页合并 | ✅ 已完成 | 列数匹配 + 表头去重 |
| 选择性 OCR | ✅ 已完成 | 页面分类 + 混合页文本去重 |
| 图片分类 + VLM 描述 | ✅ 已完成 | 启发式分类 + 并发 VLM 调用 |
| 文本清洗（CJK） | ✅ 已完成 | 空格修复 + 编码修复 |
| 评估框架 | ✅ 已完成 | 编辑距离、标题/表格 F1、OmniDocBench |
| 换行修复 | ✅ 已完成 | CJK/英文续行检测、跨元素合并 |
| 章节检测 LLM 兜底 | ✅ 已完成 | 低置信候选批量确认 |
| 公式提取 | ✅ 已完成 | Unicode→LaTeX 正则归一化 |
| 幻觉检测 | ✅ 已完成 | VLM 输出与原生文本交叉验证 |
| 阅读顺序 | ✅ 已完成 | 多栏布局检测 + 文档级传播 |

## 开发

```bash
# 运行测试
uv run pytest tests/ -v

# 使用真实文档运行测试（设置样本目录）
PARSERX_SAMPLE_DIR=/path/to/test/docs uv run pytest tests/ -v
```

### 项目结构

```
parserx/
├── config/       # YAML 配置 + Pydantic 校验
├── models/       # 核心数据模型 (PageElement, Document)
├── providers/    # 格式提取器 (PDF, DOCX)
├── builders/     # 分析层 (元数据, OCR, 图片提取)
├── processors/   # 处理层 (页眉页脚, 章节, 表格, 图片, 文本清洗)
├── services/     # AI 服务抽象 (LLM/VLM, OCR)
├── assembly/     # 输出层 (Markdown 渲染, 章节切分)
├── eval/         # 评估框架 + OmniDocBench 基准测试
└── verification/ # 输出验证 (规划中)
```

## 文档

- [架构设计](docs/architecture.md) — 技术方案、模块设计、实施状态
- [需求文档](docs/requirements.md) — 背景、痛点、行业调研、设计目标

## 许可证

MIT
