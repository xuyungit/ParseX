# ParserX

**面向大模型使用的文档解析：把 PDF、DOCX（含扫描件与嵌入图片）转成忠实的 Markdown。**

[English](README.md) | 中文

ParserX 把 PDF、DOCX、DOC 转成大模型可用的 Markdown，同时给出机器可读的记录：每一份内容去了哪里。重点是中文与中英混排的文档：报告、规范、合同、论文、扫描表单。

优先级：**页面上的信息不丢** > **标题层级正确** > 各类基准分数。

## 工作方式

```
文档 ─▶ 工作区 ─▶ 标准处理（固定流水线） ─▶ 有待核对项？ ──否──▶ 导出
                    │                                   │ 是
                    │  文字层 · 扫描引擎 · 版面检测         ▼
                    │  本地读数 · 表格 · 图片 · 公式 · 标题   Agent（Codex）用同一套工具、看页面图，核对被指出的地方
                    ▼                                   │
             去向检查：每份发现的内容都要有去向  ◀─────────┘
             （输出、合并、重复、有理由的排除，或记录在案的失败）
```

- **文档工作区**：页面、块、每块的来源（文字层、OOXML、扫描引擎、VLM）、每个决定、内容去向账目都保存在工作区里，不在模型的上下文里。
- **工具包与程序约束**：工具负责读取、识别、复核表格、描述图片、调整结构。模型提出的修改只是候选，程序能核实才采用。例如：
  - 数字不能违背证据改写；
  - 调整结构不改原文；
  - 大纲层级必须合法。

  无理由消失的内容视为缺陷，每次运行都由去向检查核对。
- **两种运行时，同一套工具**：
  - 固定流水线按固定顺序做标准处理，确定、快；
  - **混合方案**（默认）先跑固定流水线；有待核对项时，再交给 Agent（Codex CLI），由它看页面图，按证据修正。
- **信号只指路，由 Agent 判断**：待核对项来自正确输出必须满足的性质：
  - 页面上看得到的都在输出里（与每页的本地读数比对）；
  - 独立读数一致；
  - 文档自身一致（数字相加、编号连续）；
  - 结构与版面一致。
- **标题由一致的证据决定**：一个段落要有两种独立证据同时成立才算标题。证据包括：
  - 排版与正文不同；
  - 版面检测器标为标题；
  - 带章节号；
  - 编号延伸某个标题的编号。

  层级来自文档自身的排版与编号；DOCX 还用文件的样式与大纲级别。没有关键词表。

用到的服务：
- 远程扫描引擎（PaddleOCR-VL，AI Studio jobs API）：识别扫描页与带文字的图片；
- VLM（OpenAI，默认 `gpt-6-luna`）：描述图片、复核；
- 本地 CPU 模型：版面检测与页面读数。

不需要 GPU。

## 安装

```bash
# 作为命令行工具
uv tool install -e /path/to/ParserX
parserx init                 # 生成 ~/.config/parserx/config.yaml 与 .env
vim ~/.config/parserx/.env   # 填写服务凭据

# 开发
git clone <repo> && cd ParserX && uv sync
uv run parserx --help
```

`psx` 是 `parserx` 的简写。凭据写在 `~/.config/parserx/.env` 或 `./.env`：

```bash
OPENAI_API_KEY=...                      # VLM
PADDLE_OCR_ENDPOINT=https://paddleocr.aistudio-app.com/api/v2/ocr/jobs
PADDLE_OCR_TOKEN=...                    # 扫描引擎，在 https://aistudio.baidu.com/paddleocr 获取
```

混合方案需要安装并登录 [Codex CLI](https://github.com/openai/codex)（`codex login`）。没有安装或没有登录时，采用固定流水线的结果，并说明原因。

## 使用

```bash
parserx parse report.pdf                       # 混合方案（默认）→ ./output/report/
parserx parse a.pdf b.docx docs/ -o out/       # 多个文件；目录取其中的 PDF/DOCX/DOC
parserx parse report.pdf --runtime fixed       # 只用固定流水线：确定、不调用 Agent
parserx parse report.pdf --stdout              # Markdown 输出到 stdout
parserx parse report.pdf --json                # 结果摘要以 JSON 输出到 stdout（进度在 stderr）
parserx parse report.pdf --lang en             # 英文界面（默认中文）
parserx parse report.pdf --no-ocr              # 不用扫描引擎：扫描页不识别（结果为 partial）
parserx parse report.pdf --no-vlm              # 不用 VLM：图片不描述
parserx parse report.pdf --set runtime.formulas=false   # 覆盖任意配置
```

Ctrl-C 中断后工作目录保留，再次运行同一命令会从中断处继续。

**输出**（`./output/<文件名>/`）：

| 文件 | 内容 |
|---|---|
| `<文件名>.md` | Markdown：标题、段落、表格（GFM；有合并单元格时用 HTML）、图片及描述、LaTeX 公式、页码锚点 |
| `<文件名>.json` | 摘要：状态（`complete` / `partial` / `failed`）、大纲、表格、图片、缺失内容及原因、待核对项、处理过程与费用 |
| `<文件名>.blocks.json` | sidecar：每个块的来源、决定与去向账目 |
| `images/` | Markdown 引用的图片 |

退出码：全部写出（complete 或 partial）为 0，有文档失败为 1，Ctrl-C 为 130。

## 配置

查找顺序：`--config` > `./parserx.yaml` > `~/.config/parserx/config.yaml` > 默认值。主要设置：

```yaml
builders:
  ocr:                    # 扫描引擎
    engine: paddleocr     # "none" 即 --no-ocr
    endpoint: ${PADDLE_OCR_ENDPOINT}
    token: ${PADDLE_OCR_TOKEN}
services:
  vlm:
    endpoint: ${OPENAI_BASE_URL:https://api.openai.com/v1}
    model: ${VLM_MODEL:gpt-6-luna}
    api_key: ${OPENAI_API_KEY}
runtime:
  mode: hybrid            # 或 fixed
  agent: {model: gpt-6-sol, effort: medium}
cache:
  mode: read_write        # 响应缓存与回放（off / read_only / refresh）
scheduling:
  budget: {}              # 每篇文档的请求数、费用、时间上限
```

完整的生产配置见 [`parserx.yaml`](parserx.yaml)。旧版本的配置键（`processors`、`providers`、`pipeline` 等）会被忽略。

## 评测

```bash
uv run python scripts/check_services.py                  # 扫描引擎与 VLM 可用
uv run pytest -q --ignore=tests/test_live_e2e.py         # L0：离线单元与契约测试
uv run python scripts/regression_test.py --core --repeat 2   # L1：核心文档离线回放两次
uv run python scripts/regression_test.py --replay eval_runs/<run>   # 回放冻结 run
uv run parserx eval ground_truth/ -o report.md            # 对照标注评分
```

指标：
- 字符 F1，以及考虑顺序的编辑距离；
- 表格单元格 F1（位置与合并单元格）；
- 标题 F1（文字与层级）与角色 F1（只看文字）；
- 关键内容错误；
- 真实请求数与费用。

冻结 run（`eval_runs/`）保存输出、服务响应与环境，可以离线复现比较。`parserx tool-eval` 在同一套标注上比较其他解析工具（需安装可选组 `bench`）。

## 目录结构

```
parserx/
├── ir/          # 数据模型：Block、SourceAnchor、Observation、Relation、Asset、Decision
├── workspace/   # 文档工作区：状态、查询、完整性
├── content/     # 原生 PDF、DOCX（OOXML）、扫描引擎、选择步骤、页眉页脚、阅读顺序
├── layout/      # 本地版面检测            routing/   # 图片路由
├── reading/     # 本地页面读数与输出的双向比对
├── tables/      # TableGrid、GFM/HTML、跨页合并、算术核对
├── hierarchy/   # 标题与层级、结构修改的合法性
├── accounting/  # 去向检查
├── tools/       # 工具包及其 JSON CLI（process、read、recognize、review_table、describe_figure 等）
├── runtimes/    # 固定流水线、混合方案、Codex 适配
├── console/     # parse 命令的进度与结果（中文 / 英文）
├── render/      # Markdown、sidecar、摘要
├── scheduling/  # 服务网关：预算、重试、有序并发、费用
├── cache/       # 响应缓存                services/  # 扫描引擎与 VLM 客户端
├── skills/      # Agent 的任务指导           prompts/   # VLM 任务提示词
└── eval/        # 指标、冻结 run、回归门、OmniDocBench 转换
```

## 文档

- [设计与研发指导](docs/redesign_guide.md)：架构、决策（§14）、阶段与状态（§12）。
- [评测](docs/evaluation.md)：指标定义。
- [需求](docs/requirements.md)：背景与痛点。
- v1 流水线（基于规则的处理器，用到 2026-09）：见 [docs/architecture.md](docs/architecture.md)，代码保存在本地 git 标签 `v1-final`。

## 许可

MIT
