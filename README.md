# ParserX

高保真文档解析工具，将 PDF/DOCX 转换为结构化 Markdown，面向知识库、检索和分析场景。

## 核心能力

- **PDF → Markdown**：字符级字体元数据提取，自动识别章节、页眉页脚、表格、图片
- **章节检测**：字体分析 + 中文编号模式匹配（7 种模式），无需 LLM 即可覆盖 70-80% 场景
- **页眉页脚移除**：几何位置 + 跨页重复检测，无 LLM 依赖
- **图片智能处理**：启发式分类（装饰性/信息性/表格/文字/空白），仅对信息性图片调用 VLM 描述
- **选择性 OCR**：仅对扫描页/混合页调用 OCR，原生文本页跳过
- **VLM 图片描述**：并发调用，支持 Responses API 和 Chat Completions API 自动切换
- **评估框架**：文本编辑距离、标题 P/R/F1、成本统计
- **全链路可配置**：YAML 配置 + 环境变量，模型/服务/策略均可切换

## 快速开始

### 安装

```bash
# 克隆项目
git clone <repo_url>
cd ParserX

# 安装依赖（使用 uv）
uv sync
```

### 基本用法

```bash
# 解析 PDF 到标准输出
uv run parserx parse document.pdf

# 输出到文件
uv run parserx parse document.pdf -o output.md

# 章节切分模式（生成 final.md + index.md + chapters/）
uv run parserx parse document.pdf -o output_dir/ --split-chapters

# 详细日志
uv run parserx parse document.pdf -v
```

### 启用 VLM 图片描述

需要设置环境变量指向 OpenAI 兼容的 API 端点：

```bash
OPENAI_BASE_URL="http://your-api-endpoint/openai" \
OPENAI_API_KEY="your-api-key" \
VLM_MODEL="gpt-5.4-mini" \
uv run parserx parse document.pdf -o output_dir/ --split-chapters -c parserx.yaml -v
```

不设置环境变量时，VLM 步骤自动跳过，其余功能正常工作。

### 评估

```bash
# 对 ground truth 目录批量评估
uv run parserx eval ground_truth/ -o report.md

# Ground truth 目录结构：
# ground_truth/
#   doc_name/
#     input.pdf       # 待解析文档
#     expected.md     # 人工校正的期望输出
```

## 配置

默认配置文件 `parserx.yaml`，主要配置项：

```yaml
# AI 服务（凭据通过环境变量注入，不写入配置文件）
services:
  vlm:
    endpoint: ${OPENAI_BASE_URL}
    model: ${VLM_MODEL:gpt-5.4-mini}
    api_key: ${OPENAI_API_KEY}
    max_concurrent: 6        # VLM 并发数

# 处理器开关
processors:
  header_footer:
    enabled: true            # 页眉页脚移除
  chapter:
    enabled: true            # 章节检测
  image:
    enabled: true
    vlm_description: true    # VLM 图片描述
    skip_decorative: true    # 跳过装饰性图片
  formula:
    enabled: false           # 公式检测（默认关闭）
```

完整配置参见 [parserx.yaml](parserx.yaml)，配置模型定义参见 `parserx/config/schema.py`。

## 架构

```
Provider → MetadataBuilder → [OCRBuilder] → Processors → Renderer

Processors 执行顺序：
  HeaderFooter → Chapter → Image(分类) → [ImageExtract] → [Image(VLM)] → TextClean
```

详见 [docs/architecture.md](docs/architecture.md)。

## 开发

### 运行测试

```bash
uv run pytest tests/ -v
```

### 项目结构

```
parserx/
├── config/          # YAML 配置 + Pydantic 校验
├── models/          # 核心数据模型（PageElement, Document, FontInfo）
├── providers/       # 文档格式提取（PDF, 未来: DOCX）
├── builders/        # 分析层（MetadataBuilder, OCRBuilder, ImageExtractor）
├── processors/      # 处理层（HeaderFooter, Chapter, Image, TextClean）
├── services/        # AI 服务抽象（LLM/VLM, OCR）
├── assembly/        # 组装层（MarkdownRenderer, ChapterAssembler）
├── eval/            # 评估框架（metrics, runner）
└── verification/    # 验证层（待实现）
```

### 添加新的 Processor

1. 在 `parserx/processors/` 下创建新文件
2. 实现 `process(self, doc: Document) -> Document` 方法
3. 在 `parserx/config/schema.py` 的 `ProcessorsConfig` 中添加配置项
4. 在 `parserx/pipeline.py` 的 `_build_processors()` 中按正确顺序注册
5. 在 `tests/` 下添加对应的测试文件

### 添加新的 Provider

1. 在 `parserx/providers/` 下创建新文件
2. 实现 `extract(self, path: Path) -> Document` 方法，输出统一的 `Document` 模型
3. 在 `parserx/pipeline.py` 的 `_extract()` 中注册文件扩展名路由

### 设计原则

- **确定性优先，AI 兜底**：能用规则/代码解决的不用 LLM
- **选择性处理**：先分类再路由，不做无差别处理
- **交叉验证**：VLM 输出与 OCR/原生提取对比（待实现）
- **规则不过拟合**：只用普适性强的硬规则，不针对特定测试文档优化
- **一切可度量**：内建评估框架，支持回归测试

## 文档

- [需求文档](docs/requirements.md)：背景、痛点、行业调研、设计目标
- [架构设计](docs/architecture.md)：技术方案、模块设计、实施计划、当前状态

## 当前状态

项目处于 **Phase 3 阶段**（质量提升），核心管道可用。详见 [架构文档附录 E](docs/architecture.md#附录-e-实施状态追踪)。
