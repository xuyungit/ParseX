# 独立发布：梳理与分解（2026-09-28，用户确认，Q107–Q115 按建议；不换版面引擎）

**目标**：ParserX 能在另一台机器上装好、配好、跑起来，不依赖这个仓库和开发者的环境。
- **必须配的**：扫描引擎（PaddleOCR-VL）、视觉服务模型（VLM）。
- **推荐配的**：Agent，保证高质量输出。两种方式：本机的 Codex，或自己的循环加一个配置好的模型。
- **怎么配**：都写在配置里。缺了什么，工具要一开始就说清楚。

**范围**：先梳理依赖、配置和首次运行，再按梳理出的问题给出工作项。这份文档确认之前，不动代码。

## 1. 事实

### 1.1 依赖清单

| 依赖 | 用途 | 必需 | 怎样装 | 现状 |
|---|---|---|---|---|
| Python ≥ 3.13 与 15 个包（pymupdf、onnxruntime、rapid-layout、rapidocr、openai、lxml……） | 全部处理 | 是 | 都是预编译的 wheel，不需要编译器 | 打好的 wheel 用 `uv tool install` 装进空目录，6 s（本机有缓存），占 386 MB；`uv` 会自带 Python 3.13 |
| 版面检测模型 `pp_doc_layoutv3.onnx`（130 MB） | 版面检测 | 是 | rapid-layout 首次使用时从 modelscope.cn 下载 | 下载时不提示，下载到安装目录里（只读安装会失败），境外可能较慢；rapid-layout 支持指定模型路径 |
| 本地 OCR 模型（rapidocr，15 MB，随包） | 本地读数：只用于待办信号（Q56），不进输出 | 否，可关（`runtime.page_reading`） | 随 pip 安装，无系统依赖 | 质量不及扫描引擎，只用来比对、指路 |
| 扫描引擎：PaddleOCR-VL 1.6（AI Studio 异步任务接口，要 token） | 扫描页、图片里的文字与表格 | 是（有扫描页或图片时） | 网络服务 | 以后可能有别的服务可选，现在只有这一种 |
| 视觉服务模型：gpt-6-luna（OpenAI 官方 API，要 key） | 图片描述、公式编辑、表格重读、Agent 的看图问答 | 是 | 网络服务 | 条目与 `use` 已有（Q100 决定一）；Q106 定为默认 |
| Agent 甲：Codex CLI（本机，`codex login`，ChatGPT 套餐） | 高质量复核 | 推荐 | npm 或 Homebrew 安装 | 本机为 codex-cli 0.157.0；我们读它的 JSON 事件流，版本变了可能读不懂 |
| Agent 乙：自己的循环 + 一个模型条目（API 计费） | 同上 | 可选 | 只需配置和 key | 已测过能用：gpt-6-sol、gpt-6-luna、deepseek-flash、glm-5.3-flashx（d1、M5） |
| LibreOffice（`soffice`） | ① `.doc` 转 `.docx`；② DOCX 里的 EMF / WMF 矢量图转 PNG | ① 处理 `.doc` 时必需；② 可选 | 系统安装（Homebrew、apt……）；画中文要系统里有中文字体（fontconfig 由我们生成） | 没有时：`.doc` 报错；矢量图保留原件 |
| git | 只在冻结评测 run 时用 | 否（开发用） | — | — |

- 只在 macOS 上装过、跑过。Linux 没有测过：本机没有 Docker；Windows 不在考虑之内。
- wheel 里带着开发和评测用的代码：`eval`、`tool_eval`（含 llamaparse、liteparse 适配器）。

### 1.2 配置现状

- **只读一个文件，不叠加**：依次找 `./parserx.yaml`、`~/.config/parserx/config.yaml`，都没有就用代码里的默认值。
- **密钥在 `.env`**：从当前目录和 `~/.config/parserx/` 读，配置里用 `${VAR}` 引用。一个模型的配置因此拆在两个文件里。
- **生产设置有两份**：`parserx init` 写出的全局配置是 `template.yaml` 的完整拷贝，靠测试保证与仓库的 `parserx.yaml` 一致（Q67）。
- **三类角色的配置分散**：
  - 扫描引擎在 `builders.ocr`；
  - VLM 在 `services.vlm`，用 `use` 选模型条目；
  - Agent 在 `runtime.agent`：`engine: codex | loop`，Codex 用 `model` 和 `effort`，循环用 `use`。
  - 运行方式在 `runtime.mode`：`hybrid` 或 `fixed`。
- **命令行**：`parse` 有 `--no-vlm`、`--no-ocr`、`--vlm-model`（Q100 之前的写法），没有选 Agent 的开关，也没有检查配置的命令（`scripts/check_services.py` 只在仓库里）。
- **命令混在一起**：`parse`、`init` 和开发用的 `eval`、`compare`、`tool-eval`、`workspace`、`tool` 并列；帮助里的程序名显示为 `psx`（`parserx` 和 `psx` 两个入口）。

### 1.3 首次运行（空的 HOME，装好的 wheel，没有配置、没有 key）

- **原生 PDF**：能完成，15 s，其中大半花在不声不响地下载版面模型上。开头有一行"没有找到配置文件，使用内置默认配置"。
- **扫描 PDF**：
  - 输出只剩几张页面图片和一行"扫描全能王 创建"，状态"部分完成"；
  - 中间只显示"p1,p2,p3：服务错误（不可重试）"，原因"scan engine not configured"要到最后的缺失列表里才看得到；
  - Agent 一行说得清楚："未找到 Codex CLI（安装并运行 `codex login` 后可用）"。

## 2. 范围

**做**：
- 配置分层与合一：Q100 决定二；
- 三类角色的配置与选择；
- 检查命令；
- 开始处理前说清缺什么；
- 模型文件的下载；
- 命令整理；
- 安装说明与验证。

**不做**：
- **本地 OCR 作为识别的降级**：用户判断本地 OCR 质量不够，本地读数只作信号；
- **接入别的扫描引擎**：保留 `builders.ocr.engine` 这个选择点，以后再加；
- **Docker 镜像、Windows**：见 Q113。

## 3. 工作项

| 项 | 内容 | 验收 |
|---|---|---|
| R1 配置分层 | 按 Q100 §3 实施：<br>① 包内默认 `parserx/config/defaults.yaml`：生产设置，加已知模型的条目，不含 key；<br>② 项目 `./parserx.yaml`，可选；<br>③ 个人 `~/.config/parserx/config.yaml`：key、个人端点、各角色用哪个模型；<br>④ 显式 `--config`，优先级最高。<br>后面的深度合并到前面的上。不再读 `.env`，`${VAR}` 照旧可用。删去 `template.yaml` 与"两份一致"的测试 | L0 覆盖叠加顺序；生产配置展开后与现在相同，指纹不变，两个冻结 run 回放 PASS |
| R2 三类角色 | 配置里一目了然：<br>`builders.ocr`：扫描引擎，现在只有 paddleocr；<br>`services.vlm.use`：服务模型；<br>`runtime.agent`：`engine: codex`（本机 Codex，带 `model`、`effort`）、`engine: loop`（带 `use`），或关掉 Agent（`runtime.mode: fixed`）。<br>命令行：`--agent codex`、`--agent <模型条目>`、`--no-agent`、`--vlm <模型条目>`，代替 `--vlm-model` | L0；README 的配置一节 |
| R3 `parserx check` | 检查每个角色配了没有、连得上没有：扫描引擎发一个小任务，VLM 发一次文字加一次看图，Codex 查版本与登录状态，循环的模型发一次小请求。`--model <条目>` 做 M2 的逐项探测。结果按角色列出，缺的说明怎么补 | 本机跑通；缺 key、缺 Codex 时的输出清楚 |
| R4 开始前说清缺什么 | `parse` 开始时列出三个角色的状态。扫描引擎没配而文档有扫描页或图片时，第一屏就说"扫描页不会被识别，运行 `parserx init` / `parserx check`"；每页的失败信息带上原因 | 用 1.3 的两份文档复现，提示在开头 |
| R5 模型文件 | 版面模型下载到用户缓存目录（如 `~/.cache/parserx/models`），不写进安装目录。`parserx init` 时下载，显示进度；首次运行才下载的，也要显示。下载失败时说明怎么手动放置 | 只读安装、离线（模型已放好）两种情况都能跑 |
| R6 命令整理 | 面向用户的命令：`parse`、`init`、`check`；开发命令收进 `parserx dev …`，或不在帮助里显示；程序名统一为 `parserx`（Q114、Q115） | 帮助输出 |
| R7 安装与部署 | README 写清：<br>① `uv tool install`（wheel 或 git，Q113）；<br>② 系统依赖：LibreOffice（`.doc` 必需、矢量图可选），Codex CLI（推荐）；<br>③ `parserx init` → 填 key → `parserx check` → `parserx parse`。<br>在干净环境里从 wheel 装起，按 README 走一遍 | macOS 走通；Linux 视 Q113 |

## 4. 顺序

R1 → R2 → R3 → R4 → R5 → R6 → R7。
- R1 是其余各项的基础；
- R3、R4 用到 R2 的角色划分；
- R7 最后，按完成后的样子写说明、做安装验证。

每项完成后跑 L0、L1 和两个冻结 run 的回放，提交一次。

## 5. 退出条件

- **干净环境**（空 HOME，只装 wheel 和系统依赖）：按 README 的四步能处理原生 PDF、扫描 PDF、DOCX、`.doc`。
- **缺东西时说清楚**：缺 key、缺 Codex、缺 LibreOffice、离线时，`parserx check` 和 `parse` 的开头都说清缺什么、怎么补。
- **三类角色**：都在配置里；Agent 可在 Codex、循环加某个模型条目、不用 Agent 三者之间切换，命令行可以临时指定。
- **不退步**：生产配置的处理结果不变，冻结 run 回放 PASS；L0、L1 通过。

## 6. 待决问题

- **Q107 个人配置文件放哪里。**
  - **建议**：`~/.config/parserx/config.yaml`。在任何目录运行都能读到，也已在 Agent 审计的禁区里。
  - **另一个办法**：项目目录下一个被 git 忽略的 `parserx.local.yaml`。
- **Q108 仓库根目录的 `parserx.yaml` 还要不要。**
  - **建议**：删去。生产设置全在包内默认层；开发与评测用 `configs/` 下的文件，显式传 `--config`。
- **Q109 现有 `.env` 怎么迁移。**
  - **建议**：`parserx init` 发现 `~/.config/parserx/.env` 时，把其中的值写进个人配置的对应位置（一次性），然后提示 `.env` 已不再读取。
  - **仓库自己的 `.env`**（含 `_B` / `_D` / `_G` 后缀组）：由我改写成个人配置里的模型条目，交你确认。
- **Q110 本地读数（rapidocr）是否保留。**
  - **建议**：保留，作为待办信号：表格待办信号、漏识行都靠它。它随 pip 装好，没有系统依赖，也可以在配置里关掉。
  - **不做**：不用它产出文字。
- **Q111 版面模型从哪里下载。**
  - **建议**：默认仍从 modelscope.cn 下载；配置里可以写本地路径，下载不了时手动放置。
- **Q112 Agent 默认值。**
  - **建议**：`engine: codex`，模型 gpt-6-sol、medium（Q35、Q106）。没有 Codex 时退回固定流水线，并提示可以改用循环加某个模型条目。
- **Q113 分发方式。**
  - **建议**：先用 `uv tool install`（从 wheel 或 git 仓库装）。发布到 PyPI、做带 LibreOffice 和中文字体的 Docker 镜像，等有服务器部署的需要再做。
  - **Linux**：本机没有 Docker，没法验证，需要你给一台 Linux 环境，或者先不验证。
- **Q114 开发命令与评测代码。**
  - **建议**：`eval`、`compare`、`tool-eval`、`workspace`、`tool` 收进 `parserx dev`，仍随包发布。和其他工具做对比时要用 `tool-eval`。
- **Q115 `psx` 这个别名。**
  - **建议**：删去，只留 `parserx`。

## 7. 结果（2026-09-28）

| 项 | 提交 | 内容 |
|---|---|---|
| R1 | 993bade | 配置分四层；key 只在个人配置；不读 `.env`；删去仓库的 `parserx.yaml` 与模板；`parserx init` 写个人配置并迁移旧文件；Agent 的 px 不读个人配置；两个冻结 run 的指纹不变 |
| R2 | 1aa0f0f | `parse --agent codex / <模型> / --no-agent`、`--vlm <模型>`（代替 `--vlm-model`），名字不对时列出可选的模型；循环的模型没有 key 时事先说明；没有 Codex 时提示可改用 `--agent <模型>` |
| R3、R5 | 3343f33 | `parserx check`：按角色检查并说明怎么补，`--model` 探测，`--offline` 只查配置。版面模型放在 `~/.cache/parserx/models`（或 `layout.model_path`），首次使用时下载并校验 SHA-256，不写进安装目录；`parserx init` 与 `parse` 在缺模型时先下载 |
| R4 | 46f547a | `parse` 开始前说明哪项服务无法工作；服务缺 key 或 token 时的失败码为 `not_configured`，页级提示写明是哪项服务；另修正 `--no-vlm`（空值原被当成 null 而拒绝） |
| R6 | d11eada | 用户命令只有 `parse`、`check`、`init`；开发命令收进 `parserx dev`；删去 `psx` |
| R7 | 本次 | README（中英）重写安装、使用、配置三节 |

**干净环境验证（macOS）**：
- 从 wheel 装进空目录，并把安装目录设为只读。
- 用空的 HOME 运行 `parserx init`：写出个人配置骨架（0600），16 s 下载版面模型到用户缓存。
- `parserx check`：逐项指出缺 token、缺 key、Codex 未登录。
- 填好 key（用本机的个人配置）后，处理四类文档都完成：原生 PDF 1.4 s；DOCX 6 s；`.doc` 经 LibreOffice 转换，交 Codex 复核，2 分 53 秒；扫描 PDF 交 Codex 复核，3 分 23 秒。

**测试**：L0 678 通过，L1 PASS，两个冻结 run 回放 PASS。

**未验证**：Linux（Q113，本机没有 Docker）。
