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
                    │  本地读数 · 表格 · 图片 · 公式 · 标题   Agent（Codex，或自己的循环）用同一套工具、看页面图，核对被指出的地方
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
  - **混合方案**（默认）先跑固定流水线；有待核对项时，再交给 Agent（Codex CLI，或自己的循环），由它看页面图，按证据修正。
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

需要什么：

| | 用途 | 必需 |
|---|---|---|
| 扫描引擎：GLM-OCR（智谱开放平台，与 `glm-5.3-flashx` 同一个 key）；或 PaddleOCR-VL（AI Studio jobs API，要 token） | 扫描页，图片里的文字和表格 | 有扫描页或图片时必需 |
| 服务模型：`qwen3.8-flash`（阿里云百炼，要 key），或配置里的其他模型 | 图片描述、公式编辑、表格重读 | 是 |
| Agent：本机的 [Codex CLI](https://github.com/openai/codex)（`codex login`，默认 gpt-6.1-sol），或自己的循环加配置里的某个模型（如 `deepseek-flash`，按该模型的 API 计费） | 复核，得到最好的结果 | 推荐 |
| LibreOffice（`soffice`） | `.doc` 输入；Word 里的矢量图（EMF/WMF）转成图片 | 处理 `.doc` 时必需 |
| 版面模型（约 130 MB，首次下载到 `~/.cache/parserx/models`）与本地页面读数 | 在 CPU 上做版面检测；与页面图像比对 | 是（自动下载） |

不需要 GPU。Python 3.13 由 `uv` 提供。

## 安装

```bash
uv tool install /path/to/parserx-0.1.0-py3-none-any.whl   # 或：uv tool install git+<仓库地址>
parserx init          # 写出 ~/.config/parserx/config.yaml，并下载版面模型
$EDITOR ~/.config/parserx/config.yaml                      # 填上要用的 key
parserx check         # 逐项检查：配了没有、连得上没有、缺了怎么补
parserx parse report.pdf
```

系统工具：处理 `.doc` 要装 LibreOffice（`brew install --cask libreoffice`、`apt install libreoffice`）；Agent 要装
Codex CLI（`npm install -g @openai/codex`，再 `codex login`）。没有 Codex 时可以用 `--agent <模型>` 改用自己的循环
（如 `--agent deepseek-flash`）；Agent 用不了时输出标准处理的结果并说明原因。

开发：`git clone <仓库> && cd ParserX && uv sync`，然后 `uv run parserx …`。

## 使用

```bash
parserx parse report.pdf                       # 先做标准处理，需要时再交 Agent → ./output/report/
parserx parse a.pdf b.docx docs/ -o out/       # 多个文件；目录取其中的文档
parserx parse docs/ -r -o out/                 # 连同子目录；输出保持相对路径
parserx parse https://example.org/report.pdf   # 网址（http、https）：先下载再处理
parserx parse scan.jpg                         # 图片（JPG、PNG、TIFF、BMP、WebP）：当作扫描页处理
parserx parse report.pdf --no-agent            # 只做标准处理：结果确定
parserx parse report.pdf --agent deepseek-flash  # 不用 Codex，改用自己的循环加配置里的某个模型
parserx parse report.pdf --vlm glm-5.3-flashx  # 换一个服务模型
parserx parse report.pdf --stdout              # Markdown 输出到 stdout
parserx parse report.pdf --json                # 结果摘要以 JSON 输出到 stdout（进度在 stderr）
parserx parse report.pdf --lang en             # 英文界面，Markdown 里新加的说明也用英文（默认中文）
parserx parse report.pdf --report --sidecar    # 另写摘要 JSON 和块级记录
parserx parse report.pdf --no-ocr              # 不用扫描引擎：扫描页不识别（结果为 partial）
parserx parse report.pdf --no-vlm              # 不用服务模型：图片不描述
parserx parse report.pdf --set runtime.formulas=false   # 覆盖任意配置
```

Ctrl-C 中断后工作目录保留，再次运行同一命令会从中断处继续。处理第一篇文档之前，`parse` 会先说明哪项服务按当前配置无法工作。

**输出**（`./output/<文件名>/`）：Markdown 和它引用的图片。

| 文件 | 内容 |
|---|---|
| `<文件名>.md` | Markdown：标题、段落、表格（GFM；有合并单元格时用 HTML）、图片（见下）、LaTeX 公式、页码锚点（`<!-- PAGE n -->`，扫描识别的页为 `<!-- PAGE n scanned -->`）；未能识别的地方有一行说明（`> 〔未识别〕第 3 页：…`） |
| `images/` | Markdown 引用的图片 |
| `<文件名>.json`（`--report`） | 摘要：状态（`complete` / `partial` / `failed`）、大纲、表格、图片、缺失内容及原因、待核对项、处理过程与费用 |
| `<文件名>.blocks.json`（`--sidecar`） | 块级记录：每个块的来源、决定与去向账目（开发与审计用） |

**图片**按用途处理：

- 界面截图、图表、示意图、照片、印章：原图，后面一行 `> 图片说明（模型生成）：…`（一两句，点出要点、标出的地方和关键数字）；图里的文字不转写。图片说明是服务模型看图写的概述，不是原文：引用名称、编号、数字请以转写或原图为准。
- 发票、证照、文件页、表格图片、公式这类"字就是内容"的图：文字转写成正文，放在以 `> **〔图片识别〕** 图片说明（模型生成）：…　[原图](images/…)` 开头的引用块里：第一行是模型的概述，下面才是逐字转写。转写经本地读数核对完整时不显示原图（标签行链到它）；有漏字或含公式时原图显示在引用块上方。
- 引用块前后有注释 `<!-- parserx:image-text src="images/…" page=n -->` 与 `<!-- /parserx:image-text -->`，下游程序可据此知道哪些文字来自图片识别、原图是哪个文件。

同一位置有同名文档时，后到的输出到 `<文件名>-2/`。

退出码：全部写出（complete 或 partial）为 0，有文档失败为 1，Ctrl-C 为 130。

## 配置

分层叠加，后面的覆盖前面的：内置默认（[`parserx/config/defaults.yaml`](parserx/config/defaults.yaml)：生产设置与已知模型）→
`./parserx.yaml`（项目自己的设置，可选）→ 个人配置 `~/.config/parserx/config.yaml`（key、个人端点、各角色用哪个模型；
可用 `PARSERX_CONFIG_DIR` 改位置）→ `--config 文件`。`${VAR}` 读环境变量；不读 `.env` 文件。

个人配置示例：

```yaml
models:                     # 内置条目已写明端点和参数：补上 key 即可
  gpt-6-luna: {api_key: sk-...}
  gpt-6-sol: {api_key: sk-...}          # 用于 --agent gpt-6-sol（自己的循环）
  deepseek-flash: {api_key: sk-...}     # 用于 --agent deepseek-flash（自己的循环）
  glm-5.3-flashx: {api_key: ...}        # 智谱：也是扫描引擎 GLM-OCR（默认）的 key
  qwen3.8-flash: {api_key: sk-...}      # 阿里云百炼（DashScope）：服务模型（默认）
builders:
  ocr: {engine: paddleocr, token: ...}  # 改用 PaddleOCR-VL，token 在 https://aistudio.baidu.com/paddleocr 获取
services:
  vlm: {use: gpt-6-luna}                # 换一个服务模型
runtime:
  mode: hybrid                          # 或 fixed：不用 Agent
  agent: {codex_model: gpt-6.1-sol}     # Codex 用的模型（默认）；或 {engine: loop, use: deepseek-flash}
```

模型条目写明怎样与这个模型对话：端点、`api_style`（responses / chat）、是否接受 temperature、接受哪些思考强度（`efforts`）、
最强能用哪种结构化输出（`structured_output`）。`parserx check --model 名字` 实测这个模型，并与条目对照。
旧版本的配置键（`processors`、`providers`、`pipeline` 等）会被忽略；`parserx init` 把旧的个人配置另存为
`config.yaml.v1.bak`，并把旧 `~/.config/parserx/.env` 里的值搬过来。

## 评测

```bash
uv run parserx check                  # 扫描引擎与 VLM 可用
uv run pytest -q --ignore=tests/test_live_e2e.py         # L0：离线单元与契约测试
uv run python scripts/regression_test.py --core --repeat 2   # L1：核心文档离线回放两次
uv run python scripts/regression_test.py --replay eval_runs/<run>   # 回放冻结 run
uv run parserx dev eval ground_truth/ -o report.md            # 对照标注评分
```

指标：
- 字符 F1，以及考虑顺序的编辑距离；
- 表格单元格 F1（位置与合并单元格）；
- 标题 F1（文字与层级）与角色 F1（只看文字）；
- 关键内容错误；
- 真实请求数与费用。

冻结 run（`eval_runs/`）保存输出、服务响应与环境，可以离线复现比较。`parserx dev tool-eval` 在同一套标注上运行其他解析工具（LlamaParse、MinerU、marker/Datalab、PaddleOCR-VL）、打分并并排显示（[docs/tool_eval.md](docs/tool_eval.md)）。

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
├── tools/       # 工具包及其 JSON CLI：Agent 的四个工具 read_draft、view_source、edit_draft、submit_draft，以及做出初稿的 run_pipeline
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
