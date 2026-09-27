# ParserX v2 设计与研发指导

> **文档状态**：v1.14，2026-09-24（v1.0 当日重新整理；v1.1 确认阶段零分解与阶段一接口；v1.2 完成 P0-1；v1.3 LLM 切到 gpt-6-luna、完成 P0-2；v1.4 完成 P0-3；v1.5 完成 P0-4；v1.6–v1.7 两处 v1 修复；v1.8 阶段零完成；v1.9 阶段一分解与设计修订；v1.10 阶段一实施；v1.11 阶段一完成；v1.12 全量语料参考结果与 Q33、Q34；v1.13 阶段二分解草稿；v1.14 阶段二实施，见 §15。此前 v0.1–v0.10 的逐次修订稿见 [archive/redesign_guide_v0.10_draft.md](archive/redesign_guide_v0.10_draft.md)）。
> 这是一份活文档：阶段完成时更新 §12 状态列与 §15 变更记录；决策变化时在 §15 追加记录并修订正文。
>
> 状态标记：⬜ 未开始 · 🟡 进行中 · ✅ 完成 · ⛔ 阻塞 · ❓ 待决策

## 0. 使用方法与当前状态

### 0.1 状态快照（2026-09-26）

- **外部依赖已全部确定**：OCR 走 AI Studio jobs API（PaddleOCR-VL-1.6）；LLM/VLM 走官方 OpenAI 端点，VLM 与 LLM 都是 gpt-6-luna（LLM 于 2026-09-23 从 gpt-5.4-mini 切换，Q19）；`services/llm.py` 已适配推理模型。`uv run python scripts/check_services.py` 三项通过。
- **架构定位已定**：v2 的核心交付物是文档工作区 + 文档工具包 + 程序约束（§3）。固定流水线和 LLM 驱动的 Agent 是两种可替换的运行时，默认运行时由 §7 的实验决定，不先押注。
- **阶段零 ✅**（2026-09-23）：指标、硬检查、回归配置、响应缓存、L1 离线回放、v1 冻结基线 `eval_runs/2026-09-23_p0_v1_gpt-6-luna`（提交 78556f4，[基线报告](../eval_reports/2026-09-23_p0-5_v1_frozen_baseline.md)；按指标 2.1 离线重算为 `.rescored-2.1.json`）。
- **阶段一 ✅**（2026-09-24）：文档工作区、七个工具与 JSON CLI、程序约束（去向检查、合法性、接受门、预算）、内容获取（原生 PDF、扫描页引擎、DOCX 直接读 OOXML）、渲染与 sidecar、三份 Skill 草稿、版面检测与图片路由（影子）、固定序列运行时与 `pipeline: v1 | v2` 开关。v2 冻结 run `eval_runs/2026-09-24_p1_v2_toolkit`（提交 65041be），[验收报告](../eval_reports/2026-09-24_p1_toolkit_acceptance.md)；[工具形态试用](../eval_reports/2026-09-24_p1-7b_tool_trial.md)。决策 Q23–Q32 见 §14。**全量语料（27 篇）参考运行**暴露的三处差距（Q33：跨页续表、扫描页标题层级、多栏阅读顺序）已在阶段二之前处理，v2 冻结 run 以空缓存重新冻结（提交 e14752f）。处理后与 v1 比：char_f1 变差 11 篇、变好 9 篇（此前 15 / 7），平均 0.932（v1 0.893）；v2 仍不能在所有文档上替代 v1，剩余差距见验收报告"全量语料"一节。Q34（原生页页眉页脚识别、扫描页多栏区域顺序）也已在阶段二之前处理：与 v1 比 char_f1 变差 9 篇、变好 9 篇、持平 9 篇，平均 0.940（v1 0.893），剩余差距（扫描页识别差异、图片中的文字、无样式标题、界面元素、代码块）见验收报告。阶段二（Agent 探索）🟡：分解 [v2_phase2_plan.md](v2_phase2_plan.md) 已确认（Q35–Q39；Agent 运行时为 Codex，主力模型 gpt-6-sol）；P2-1 实验装置 ✅（[报告](../eval_reports/2026-09-24_p2-1_harness.md)），P2-2 任务说明 ✅，P2-3 对照运行 ✅，P2-4 第一轮 ✅（13 次运行全部有效，[报告](../eval_reports/2026-09-24_p2-4_round1_findings.md)），P2-5 工具包 v1.1 ✅（Agent 以 `process` 开始、只处理待办与定向抽查；推理强度 medium；默认经 `ask_image` 看图；输出包 Q42），P2-6 第二轮 ✅（[报告](../eval_reports/2026-09-24_p2-6_round2.md)：同样 13 篇耗时中位数 265 s → 155 s、Agent 标价 $17.3 → $3.32，文字与表格不变或更好，无样式标题变差——Q48），P2-7 ✅（固定流水线新增跨页段落续接、DOCX 无样式标题、表格算术一致性与可疑字符提示、单元格中的图片），P2-9 ✅（[探索报告](../eval_reports/2026-09-24_p2_agent_exploration.md)），P2-8 ✅（未见集与 §9.4 协议）——**阶段二 ✅**；**阶段三 ✅**（2026-09-25）：[对比报告](../eval_reports/2026-09-24_p3_runtime_comparison.md)、缺陷 D1–D6 修正、本地读数双向比对（Q56）、[重跑对比](../eval_reports/2026-09-25_p4_runtime_comparison.md)；**Q13：默认混合方案**（固定流水线 → 有待核对项交 Agent）；**阶段四 ✅**（2026-09-26）：`parserx parse` 默认走混合方案（控制台中英文进度、回退、中断续跑、`--json`），补齐标题、图片、DOCX、代码块、三线表、原生页公式等能力；全语料运行 B 混合方案 char_f1 0.947、表格 F1 0.875、heading_f1 0.756、角色 F1 0.868（v1 0.890 / 0.775 / 0.494 / 0.614），[退出报告](../eval_reports/2026-09-26_p4-7_full_run_b.md)；**阶段五 ✅**（2026-09-26，[分解](v2_phase5_plan.md)，Q72–Q78，[退出报告](../eval_reports/2026-09-26_p5-7_cleanup_exit.md)）：adapter:v1 由 v2 自有标题路径替代（"两种独立证据一致"），v1 全部删除（本地标签 `v1-final` 保留），依赖与代码一致，README 重写。最终测量（修订后的标注）：固定流水线 char_f1 0.950、表格 F1 0.866、heading_f1 0.699、角色 F1 0.805；混合方案 0.952 / 0.875 / 0.697 / 0.806（运行 B 的标题 0.774，Q78 接受并记录）。
- **测试基线**：L0 635 通过，无已知失败（2026-09-27，Q88 之后），约 65 s。L1：`regression_test.py --core --repeat 2`（只有一条流水线、一个 L1），应 PASS。
- **代码状态**：全部在 main，未推送远端。冻结 run、响应缓存与新报告只在本地（`eval_runs/`、`.parserx_cache/`、`eval_reports/`，不入 git）。

### 0.2 新会话启动清单

阶段零至五已完成。下一轮会话的主题由用户从"以后"清单中选择（[退出报告](../eval_reports/2026-09-26_p5-7_cleanup_exit.md) §4；[next_session_prompt.md](next_session_prompt.md)）。开始时按顺序做：

1. 读以下内容：
   - 指导 §2、§3、§9.5、§12、§14，尤其 Q13、Q40、Q56、Q72、Q78；
   - 阶段五[退出报告](../eval_reports/2026-09-26_p5-7_cleanup_exit.md)与 [P5-2 标题报告](../eval_reports/2026-09-26_p5-2_headings.md)；
   - [标注修订记录](annotation_changes.md)。
2. 运行 `uv run python scripts/check_services.py`：扫描引擎与 VLM 两项都 OK 才继续。
3. 运行测试，都应 PASS：
   - L0：`uv run pytest -q --ignore=tests/test_live_e2e.py`（581 通过，无已知失败）；
   - L1：`uv run python scripts/regression_test.py --core --repeat 2`；
   - 冻结 run 回放：`--replay eval_runs/2026-09-26_q80_v2_toolkit`（验收）与 `--replay eval_runs/2026-09-26_q80_fixed_full --gt-dir ground_truth --gt-dir ground_truth_public`（全语料；PyMuPDF 1.28 与检测器分段之后重新冻结，旧的改名为 `.pre-q80`）。
4. 为选定的主题写分解，请用户确认后再动代码。
5. 每完成一项：跑 L0 与 L1；更新 §12 与 §15；新的决策写进 §14；提交一次。
6. 全语料比较：固定流水线用 `scripts/heading_compare.py`（以全语料冻结 run 的缓存离线回放；段落拼接另用 `scripts/paragraph_segmentation.py`）；混合方案用 `scripts/agent_explore.py`（snapshot + parse）与 `scripts/phase4_compare.py`。v1 冻结 run 只作静态基线，不能回放。

### 0.3 相关文档

- [architecture.md](architecture.md)：v1 架构，保留作历史参考，不再更新。
- [requirements.md](requirements.md)：痛点 P1–P19 与设计目标仍然有效。
- [iteration_history.md](iteration_history.md)、[iteration_backlog.md](iteration_backlog.md)：v1 的 33 次迭代记录，已冻结。
- [evaluation.md](evaluation.md)：指标定义，按 §9.2 修复。
- [v2_phase0_plan.md](v2_phase0_plan.md)：阶段零剩余任务分解（改动文件、测试、验收、顺序）。
- [v2_phase1_interfaces.md](v2_phase1_interfaces.md)：阶段一 IR、TableGrid、返回信封与七个工具的 pydantic 模型和 JSON CLI 签名（历史）；工具的字段级定义现在以代码中的请求模型为准（`parserx tool schema <name>`，Q86），§4、§5 是概要。
- [v2_toolkit_review.md](v2_toolkit_review.md)：四个工具（Q85）与接口定型（Q86）。
- [v2_agent_design.md](v2_agent_design.md)：Agent 的角色（Q87）与自己的循环的上下文、模型适配（Q88）。
- [v2_phase1_plan.md](v2_phase1_plan.md)：阶段一工作分解（事实、设计修订 R1–R8、P1-1 至 P1-11、退出条件、待决问题）。
- [v2_phase2_plan.md](v2_phase2_plan.md)：阶段二工作分解（探索设计、探索集与未见集、P2-1 至 P2-9、退出条件、待决问题 Q35–Q39）。
- [v2_phase5_plan.md](v2_phase5_plan.md)：阶段五工作分解（已完成：adapter:v1 退役、删除 v1、依赖、README；Q72–Q78）。
- [v2_phase4_plan.md](v2_phase4_plan.md)：阶段四工作分解（已确认：混合运行时作为产品默认、控制台交互、标题的 v2 自有路径、图片不丢、DOCX 页面、专项与逐篇差距；Q57–Q61 均已定）。
- [next_session_prompt.md](next_session_prompt.md)：下一轮会话的启动提示词（阶段五）。
- [v2_phase3_plan.md](v2_phase3_plan.md)：阶段三工作分解（已完成：固定流水线与 Codex 在未见集上的对比、混合方案的分流规则、P3-1 至 P3-6、待决问题 Q51–Q55）。
- [v2_agent_runtime_research.md](v2_agent_runtime_research.md)：Agent 运行时调研（2026-09-24；待决问题 Q62–Q64，属"第三方 Agent 框架"议题，以后单独讨论）：OpenAI Agents API / Agents SDK、Pi、PydanticAI 等框架的对比，与自研薄循环的架构建议；待决问题 Q56–Q58。
- [../eval_reports/dependency_probe_2026-09-23.md](../eval_reports/dependency_probe_2026-09-23.md)：依赖探测原始数据；[../eval_reports/full_ocr_v16_2026-09-23.md](../eval_reports/full_ocr_v16_2026-09-23.md)：OCR 1.6 接入后的全量回归。

## 1. 背景与根因

### 1.1 v1 现状（数据）

v1 从 2026-04-04 到 2026-04-17 共 108 次提交、33 次迭代。两次全量基线：

| 日期 | 文档数 | char_f1 | heading_f1 | table_f1 | 总耗时 |
|---|---|---|---|---|---|
| 2026-04-09 | 14 | 0.902 | 0.528 | 未记录 | 未记录 |
| 2026-04-17 | 16 | 0.897 | 0.544 | 0.487 | 570.6 s |

中间 15 次迭代，Iter 26 到 33 全部围绕标题识别，heading_f1 只变化了 0.016。代码症状：

| 症状 | 证据 |
|---|---|
| 规则堆叠 | `parserx/processors/chapter.py` 1599 行、38 个辅助函数，多数是针对单篇文档症状的守卫 |
| 状态无模型 | `PageElement.metadata` 是自由字典，全项目使用 84 个不同的标志键 |
| 评测不可复现 | 标题识别的 LLM 兜底使同一提交的 heading_f1 波动 ±0.05–0.10 |
| 图片分类缺失 | 分类只看像素尺寸；`TABLE_IMAGE` / `TEXT_IMAGE` 分支因 LayoutBuilder 从未实现而休眠 |
| AI 路径重叠 | 扫描页 OCR、图片 VLM 转录纠正、页面级 VLM 复审三层互相覆盖，靠字符串包含去重 |
| 成本无控制 | 无缓存；每次回归都真实调用全部服务（最近一次 1208 s，OCR/VLM/LLM 32/50/11 次） |

### 1.2 根因

1. **OCR、VLM 和规则之间没有明确的职责、裁决权和停止条件。** 同一段内容会经历"OCR 给出文本 → VLM 改写 → 规则发现冲突 → 页面复审再改 → 渲染阶段去重"，每一步单看都有道理，但无法回答这句话是谁改的、依据是什么、哪一步该负责。VLM 的四种用途（纠正 OCR、理解表格、描述图片、组织章节）混在一条可以反复修改内容的流程里，后执行的一步自动拥有最终决定权。
2. **本地没有统一的版面表示。** 版面理解被外包给远程 OCR 和 VLM，每类新文档只能靠加规则来补，规则互相打架；图片处理不是"先判断是什么再决定怎么做"，而是"先全部送 VLM 再用规则收拾结果"。
3. **验收工具有实质漏洞**（§9.2）：表格指标忽略单元格位置与漏表，字符指标忽略顺序，HTML 表格检不出，文档全部失败时退出码仍为 0。"新路径不低于旧路径"在这套指标上无法可靠证明。

v1 最初的方向是对的：希望工具能理解不同的文档，而不是积累"某种字号、某种编号就是什么"的规则。v2 保留这个目标；要收紧的不是模型的判断空间，而是"判断之后能改什么、如何验证、失败后如何结束"。

### 1.3 决策：保留外壳，重建核心

| 路线 | 代价 | 风险 | 结论 |
|---|---|---|---|
| 从零重写 | 丢掉评测资产、服务客户端、DOCX 图片提取等可用代码 | 重新踩同样的坑 | 否 |
| 渐进重构 | 每步小 | 版面抽象仍然缺失，标志位总线换汤不换药 | 否 |
| 保留外壳、重建核心 | 中等 | 新旧路径并行期需维护开关 | **采用** |

"外壳"指配置、服务客户端、渲染器骨架、评测脚本和 ground truth。"核心"重新定义为三层：**文档工作区**（状态与证据）、**文档工具包**（按任务设计的能力）、**程序约束**（去向检查、修改权、预算、合法性）。重心从"设计一条覆盖所有文档的处理流程"转向"提供可靠的文档工具，让运行时根据证据选择处理过程"。无论最终运行时是固定流水线、现成 Agent 还是自研循环，这三层都能继续使用。

## 2. 目标、原则与非目标

### 2.1 目标场景

- 输入：DOCX（含 .doc 经 LibreOffice 转换）和 PDF；以中文为主、中英混排；文档中嵌入大量扫描图片，内容包括扫描文字页、表格截图、示意图、图表、照片、印章。
- 输出：适合大模型消费的 Markdown，外加机器可读的 sidecar（块、来源、置信度、页码、去向账目）。
- 规模：单篇几页到几百页；需要可预期的耗时和成本上限。

### 2.2 目标与优先级

优先级沿用 v1 已确认的顺序：**信息保全与可读性 > 标题层级准确 > 任何公开基准分数**。一条改动如果提升了 heading_f1 但丢了正文内容，视为回归。

v2 同时追求两件事，并认为可以同时做到：

- **灵活性**：面对陌生文档仍能结合证据作判断；新增一类文档时不必修改很多处理器。
- **可控性**：每个判断能改什么、依据是什么、如何验证、失败后如何结束都有定义；出错时能找到负责该判断的步骤并局部修正、局部重跑。

### 2.3 原则

1. **内容去向可追溯。** 每份已发现的内容（原生字符、OOXML 节点、检测区域、OCR 块）都必须有去向：输出、合并、判定重复、明确排除，或识别失败。流水线内部的静默丢失视为缺陷，由 §3.3 的去向检查在每次运行时核对。
2. **两类规则区别对待。** 猜测文档含义的规则（大字号是标题、短句不是正文、某种编号固定对应某级标题、没有样式就不是标题）尽量不硬编码，作为证据交给模型结合上下文判断；保证处理正确性的约束（输出必须引用存在的块、调整章节不能改写正文、删除必须记录原因、数字与证据冲突不得覆盖）明确保留，由程序执行。
3. **模型的修改是候选证据。** 不因为它后执行就拥有最终决定权；每个 AI 任务后面都有一个由程序执行的选择或校验步骤。
4. **忠实优先于合理。** 转录与复核只回答"图上写了什么"；原图确实写错的数字也保留，纠错不得依据常识改写原文。
5. **"少调用"不是架构原则。** 要优化的是达到目标质量所需的成本、耗时和可调试性；每次调用职责明确、每次修改有依据、每条路径有结束状态。

### 2.4 非目标

- 不追 ParseBench 或 OmniDocBench 榜单，只作偶尔的健全性检查。
- 不为单篇文档加规则；任何阈值必须在整个语料上验证，隔离验证集不参与调参。
- 不做多智能体系统；即使采用 Agent 运行时，也是单个主 Agent + 批量工具。
- 不做文档问答式的按需识别：完成条件是全文保全，不是"模型认为值得看的部分"。
- 不先开发一个新的 Agent 框架：先验证"Agent + 文档工具包"能否成为核心，只有出现明确缺口时才自研运行时（§7）。
- 不做通用 PDF 编辑、不做版式还原。

## 3. 总体架构

### 3.1 三层与可替换运行时

```mermaid
flowchart TD
    A[输入文档与解析目标] --> W[文档工作区：页面、Block、Observation、Relation、Asset、每页状态]
    W --> R{运行时}
    R -->|固定流水线| S1[固定步骤序列]
    R -->|Codex / Claude Code / Pi / 自研循环| S2[主 Agent：读取概况与 Skill，决定下一步]
    S1 --> T[文档工具包：overview / read / recognize / review_table / describe_figure / apply_structure / check+export]
    S2 --> T
    T --> W2[写回工作区：Observation、候选、Decision]
    W2 --> C[程序约束：修改权、去向检查、预算、合法性、注入隔离]
    C -->|有待解决项且有预算| R
    C -->|完成或预算耗尽| O[渲染 Markdown、sidecar、未解决项]
```

换运行时只更换启动、事件收集和工具适配层；OCR 客户端、表格结构、来源记录、Skill 与评测资产不重做。

### 3.2 工作区：状态保存在程序中

几百页文字、全部图片和每轮识别结果不进入模型的对话历史。工作区持久保存完整内容（§4 的数据模型，sidecar 是其序列化形式）；运行时通常只看到文档概况、章节树、处理进度和当前问题，需要时再通过工具读取原页或局部证据。工作区带每页处理状态和每个块的状态，运行时无法"忘记"一页。

### 3.3 程序约束一览

这些约束由代码执行，不依赖运行时的自觉，也不依赖提示词：

| 约束 | 内容 | 位置 |
|---|---|---|
| 修改权 | 每类 AI 任务只能改它被允许改的字段（§6.1）；结构变更永不改原文 | 工具层 `apply_structure`、选择步骤 |
| 去向检查 | 发现集合与去向集合一一对应；不平衡则文档降为 `partial` 并列出未归属项；`check` 不通过不能 `export` | `accounting/` |
| 预算 | 文档级截止时间、并发、请求数、费用；请求前预留、完成后结算；超限输出 `partial` 与缺失原因 | `scheduling/` |
| 合法性 | 结构输出只能引用存在的块 id；层级不得跳级；同一编号模式同层级 | `hierarchy/` |
| 接受门 | 复核候选必须有图像证据、数字与证据不冲突、结构合法，才替换已采用结果 | 选择步骤 |
| 停止 | 同一问题没有新证据不再复核；预算耗尽即停 | `scheduling/` |
| 注入隔离 | 文档内容是数据不是指令：工具返回的文字（包括原文里的"忽略以上指令"）永远不进入指令通道；指令只来自 Skill 与配置 | 工具返回信封 |

## 4. 数据模型

### 4.1 五个概念

| 概念 | 表达什么 | 关键字段 |
|---|---|---|
| **Block** 内容块 | 段落、标题、列表、表格、图、公式、图注、页眉页脚等逻辑内容 | `id`、`kind`、`order`、`text`、`cells`、`level`、`semantic`、`status`、`chosen_observation` |
| **SourceAnchor** 来源定位 | 内容在原文件里的位置 | PDF：`page`、`bbox`、`coord_space`（`page_pt` / `image_px`）、`image_size`、`transform`；DOCX：`part`、`node_path`、`run_range`；嵌入图片内的区域：`asset`、`bbox`（image_px）、`image_size`、`transform`（原图位置记在 Asset 上）。一个 Block 可有多个 anchor |
| **Observation** 识别记录 | 某个引擎对某个 anchor 的一次识别结果 | `engine`、`engine_version`、`raw_ref`（原始响应缓存键）、`text`/`cells`、`det_confidence`、`rec_confidence`、`status`（ok / empty / failed / skipped_budget） |
| **Relation** 关系 | 块与块之间的结构 | `kind`（contains：图片中读出的文字；continues：被分页拆开的段落或表格，`join` 建立；duplicate_of：重复的读数，不输出；Q86 删去了从未起作用的 follows / captions / footnotes / belongs_to_section）、`src`、`dst`、`confidence` |
| **Asset** 资源 | 原图、裁剪图、渲染图 | `sha256`、`path`、`width`、`height`、`derived_from`、`transform` |

```python
class Block(BaseModel):
    id: str
    kind: BlockKind            # title/text/list/table/figure/formula/caption/header/footer/page_number/footnote/scan/other
    order: int
    status: BlockStatus = BlockStatus.OK   # ok / degraded / failed / excluded / merged / duplicate
    anchors: list[SourceAnchor]
    observations: list[Observation] = []
    chosen_observation: str | None = None
    text: str = ""             # 由 chosen_observation 派生；表格块为空，内容在 cells
    cells: TableGrid | None = None
    level: int | None = None   # 仅 title
    semantic: FigureSemantic | None = None   # 仅 figure，类型化，带证据层级
    decisions: list[Decision] = []
```

### 4.2 状态与置信度规则

- 正文只取一个识别版本（`chosen_observation`），证据层保留全部候选，供去重、回退、追溯。
- 置信度分三种：`det_confidence` 来自检测器，`rec_confidence` 来自识别引擎，结构判断的把握写在 Relation 或 Decision 上。引擎不提供的记为 `None`，评测与渲染把 `None` 当"未知"而不是"可信"。
- 坐标必须带坐标系；子文档区域用父图片像素坐标并携带 `transform`。DOCX 内容没有 `page`/`bbox`，只有 `part`/`node_path`，几何处理天然不适用，不喂假坐标。
- 状态是枚举，不是标志位；渲染器只看 `kind`、`level`、`status`、`semantic` 和 Relation。禁止引入布尔标志或自由字典。

### 4.3 表格结构 TableGrid

```python
class Cell(BaseModel):
    row: int; col: int
    rowspan: int = 1; colspan: int = 1
    content: str
    is_header: bool = False
    anchors: list[SourceAnchor] = []
    rec_confidence: float | None = None

class TableGrid(BaseModel):
    n_rows: int; n_cols: int
    cells: list[Cell]
    header_rows: int = 0
```

表格的主数据是网格，不是 Markdown 字符串。GFM 与 HTML 都由 `TableGrid` 生成（无合并单元格输出 GFM，有则输出 HTML）；跨页合并、表头关联、结构校验、评测都在 `TableGrid` 上做。扫描页引擎的 HTML 表格、原生 PDF 的字符归属结果、VLM 复核的候选，都先转成 `TableGrid`。v1 的 `html_table_to_markdown` 只复用其 HTML 解析与 `_build_table_grid`（保留 rowspan/colspan），不复用扁平化输出。

### 4.4 Decision

```python
class Decision(BaseModel):
    stage: DecisionStage       # image_route / content_source / review_accept / heading_role / heading_level / exclude / budget
    choice: str
    reason: str
    evidence: dict[str, float | int | str | bool]
    actor: str                 # 谁做的判断：program:<模块> / pipeline / agent / tool:<名称>
    refs: list[str] = []       # 作为证据的 Observation / Relation / Block id
```

每个 Block 至少有一条路由 Decision；任何让内容"消失"的分支（过滤、抑制、跳过、预算截断）必须产生 Decision 并被去向检查计数。

### 4.5 输出契约

**输出包（Q42）**：外部入口 `parserx parse <文档> -o <目录>`，输入 PDF / DOCX / DOC，输出一个目录，目标是信息完整、方便大模型后续使用，人也能直接读到渲染良好的 Markdown：

```
<目录>/
  <名称>.md            可直接使用的 Markdown（下文契约）
  images/              提取出的全部图片（包括不显示的装饰图）；Markdown 只链接需要显示的
  <名称>.json          文档信息摘要：来源文件名与摘要、格式、页数、状态与缺失项、标题大纲、表格数、
                       图片清单（文件、页码、类型、是否显示、描述摘要）、处理统计（请求、费用、耗时、引擎与模型）、警告
  <名称>.blocks.json   完整的块级 sidecar（机器用）
```

运行时（固定流水线或 Agent 主控）不同，输出包相同。

Markdown：ATX 标题；正文段落内不保留硬换行；表格无合并单元格用 GFM、有则 HTML；图片 `![<一句话>](images/<file>)` 后紧跟固定格式的语义块（带证据层级），语义块必须是紧跟图片行的引用块、首行为 `> [图片语义]`，这样评测的规范化步骤才会把它当作描述剔除（格式见接口文档 §5.6）；不再把描述同时写进 alt 与引用块；公式行内 `$…$`、独立 `$$…$$`；PDF 页锚点 `<!-- PAGE n -->`，DOCX 只输出显式分页 `<!-- PAGE-BREAK -->` 与分节 `<!-- SECTION k -->`。

Sidecar（与 Markdown 同名 `.blocks.json`）：

```
{
  "document": {"source": "...", "status": "complete|partial|failed", "pages": 12,
               "engines": {...}, "prompt_hashes": {...}, "missing": [{"block": "...", "reason": "..."}]},
  "pages": [{"n": 1, "status": "done|partial|failed|skipped"}],
  "blocks": [Block, ...], "relations": [Relation, ...], "assets": [Asset, ...],
  "images": [{"id": "...", "route": "SCAN|FIGURE|MIXED|UNCERTAIN|DECORATIVE", "shown": true, "t": 0.71, "f": 0.05}],
  "accounting": {"discovered": 812, "output": 790, "merged": 12, "duplicate": 6, "excluded": 4, "failed": 0},
  "stats": {"requests": {"ocr": 3, "vlm": 7, "llm": 1, "agent": 14}, "attempts": {...}, "cost_usd": 0.0, "wall_time_s": 0.0},
  "warnings": [...]
}
```

`document.status`：`complete`（无缺失）、`partial`（有块失败或预算跳过，`missing` 列原因）、`failed`（提取本身失败）。下游按 `blocks` 分块、按 `status` 与置信度过滤，不需要解析 Markdown。

## 5. 文档工具包

### 5.1 四个工具（Q85，2026-09-27 重新设计）

工具按任务设计，不只是暴露模型 API。从 Agent 要做的事出发（设计记录：[v2_toolkit_review.md](v2_toolkit_review.md)）：它像一位校对编辑，左边是原件，右边是程序做出的初稿，手边是待办清单。工具按"初稿 / 原件"两个对象、"读 / 改 / 交"三种动作组织，名字写明作用于哪一边：

| 工具 | 做什么 | 改不改初稿 | 费用 | 模块 |
|---|---|---|---|---|
| `read_draft` | 读初稿：`summary`、`issues`（待办，每项有稳定编号）、`text`（翻看、一页、查找、正则、一个排版类别）、`outline`（排版类别与像标题的行）、`blocks`（块的细节）、`changes`（已被接受的修改，取自调用记录；Q86） | 不改 | 无 | `tools/draft.py` |
| `view_source` | 看原件：`image`（取图）、`answer`（视觉模型作答）、`text`（识别引擎读一页或一张图）、`table`（视觉模型重读表格）、`description`（描述图片）；每次得到一个证据编号 | 不改（证据存入状态） | 读图的方式有 | `tools/source.py` |
| `edit_draft` | 改初稿：内容（`replace_text`、`insert_text`、`set_cells`、`adopt`）、结构（`set_role`、`move`、`join`/`unjoin`、`split`、`exclude`/`include`、`mark_pending`）、待办（`dismiss`）；一个事务，逐条采用或拒绝 | 唯一入口 | 无 | `tools/edit.py` |
| `submit_draft` | 交稿：账目平衡即接受，否则说明阻碍；没有参数 | 不改 | 无 | `tools/submit.py` |

- **流水线与导出不是 Agent 的工具**：`run_pipeline`（`tools/process.py`）做出初稿，程序在 Agent 开始前运行它；`export`（`tools/submit.py`）在 Agent 之后（或固定模式下流水线之后）写出输出包，先做与交稿相同的核对；识别与描述步骤（`recognize`、`describe_figure`）只在进程内调用。
- **契约只在一处**（Q86）：请求模型（pydantic）及其中文说明。命令行参数（`tools/cli.py`，字段名即参数名）、任务说明里的工具参考（`tools/reference.py`）、函数调用的工具定义都由它生成。
- **一套词表**（Q86）：`read_draft` 显示的角色（`H1`–`H6`、`text`、`list`、`caption`、`footnote`、`other`；`table` 等内容块只读）就是 `set_role` 设的角色。页眉、页脚、页码、水印是程序"不输出"的标签，不是可设的角色：`exclude` / `include` 管输出与否，恢复的页眉类块成为 `text`。`join` 把被分页拆开的两段合为一段、两张表合为一张。
- **证据**（`ir/evidence.py`）：每次看原件留下一条证据（看了哪里、问了什么、看到什么），存在状态里（摘要保护，随 sidecar 输出）。改内容的操作必须引用能看到被改之处的证据；`adopt` 采用的正是当时读到的内容。
- **修改的形状统一**：每条操作写明对象、`reason`，改内容的写 `evidence`；程序逐条检查（证据、原生数字、表格接受门、合法性、`decided_by_agent`），拒绝时给出规则名。文字修改是"在块内找到恰好一处片段再替换"，不是 diff 或整段替换。
- **角色与决定权**：程序不是另一个决策者，而是四样东西——流水线（做初稿，其规则是提议）、工具（照做，不判断）、把关（正确性约束，可以拒绝，不判断含义）、信号（待办，只指路）。含义上的判断（是不是标题、字对不对）归 Agent；正确性归把关。
- **Agent 自带的能力**：可以用自己的 shell 与脚本读、分析工具的输出；初稿只能经 `edit_draft` 修改（直接改工作区会被摘要检查发现，整次运行作废；直接读内部文件会被审计记下）。

### 5.2 返回信封与批量语义

- 每个工具返回统一信封：`tool`、`doc`、`ws_version`、`ok`、`result`、`cost`（请求数、token、费用、耗时、剩余预算）、`failures`（列表；原因、可否重试、涉及目标）。给 Agent 的 JSON 省去值为 null 的字段（`agent_json`；信封的 schema 也这样标）。Q86 删去了 `diff`（与编辑结果重复）与 `unresolved`（四个工具中永远为空；待办由 `read_draft` 取得）。
- 普通批量识别在工具内部执行（分批、并发、重试、校验页数）；运行时一次要求"识别这组扫描页"，完成后集中处理异常，不逐页发起几十轮思考。
- 工具返回中的文档文字标记为数据（§3.3 注入隔离）：统一包在 `{"doc_text": …}` 中。
- 主 Agent 与工具内部的 OCR/VLM 调用分别计数（`stats.requests.agent` 与其余），CLI 的最终用量不涵盖工具内部的服务调用。

### 5.3 接口形态

同一组函数（`call_tool`）有两种调用方式：返回 JSON 的命令行（`parserx tool <name> --json …`，Codex 经 `./px` 使用），以及进程内的函数调用（自己的 Agent 循环直接用 `tool_schema` 与 `call_tool`，Q86）。运行时适配层只负责启动、事件收集和调用格式转换；任务说明分两层：`runtimes/agent_task.md`（任务、方法、规则，与 Agent 无关）加适配层说明（`runtimes/adapter_cli.md`：`./px` 的用法与目录规则）。

### 5.4 Skill

三份任务指导独立保存在 `parserx/skills/`（Markdown，内容哈希参与缓存键），不同运行时只做必要的加载适配，关键方法不允许只存在于某个 Agent 的会话历史里：

- **忠实转录与纠错**：先看原图与已有结构，判断问题属于字符、行列关系还是跨页续接；按需扩大查看范围；保留原始数值；提交带来源的候选；证据不足报告未解决。
- **图片理解与描述**：区分可见文字、描述与推断；描述失败不影响正文。
- **文档结构与章节组织**：先读懂全文再定标题（Q81）：用 `read_draft` 浏览全文（短文档通读，长文档先看大纲再翻看），分出文档的组成部分（含附着的另一份文件），按排版类别推断每部分的标题惯例，按类修正、再处理例外；只改角色、层级、归属；证据不足保留正文、结构待定。不写针对个别文档的情况清单。

Skill 说明目标、取证方法、输出要求和停止条件，不写"字号大于多少就是标题""列数相同就是续表"。**Skill 提供方法，程序强制执行数据与预算约束。**

## 6. 处理方法

### 6.1 AI 任务边界

四类任务可以用同一个模型，但输入、输出、修改权和结束状态不同：

| 任务 | 回答的问题 | 输入 | 输出 | 可以改变什么 | 不确定时怎么结束 |
|---|---|---|---|---|---|
| 转录与复核 | 图上实际写了什么 | 原图裁剪、必要上下文、待核查的具体问题 | 文本或 `TableGrid` 候选，附修改位置 | 提交候选；由选择步骤决定是否采用 | 保留冲突与原图，块 `degraded`，不再叠加"再问一次" |
| 图片描述 | 这张图展示了什么 | 图片、图注、邻近正文 | 附着在图片块上的说明，带证据层级 | 只增加描述 | 描述缺省，图片保留；与正文不一致不反过来改正文 |
| 表格解释 | 某列代表什么、单位是否继承 | `TableGrid`、表头、图注 | 列语义、单位、关联信息 | 只增加解释，不改单元格 | 解释缺省 |
| 章节组织 | 哪些块是标题、几级、边界在哪 | 候选块、邻近正文、编号、样式、视觉信号、结构摘要 | 块的角色、层级、章节归属 | 只改结构，不改任何块的原文 | 保留正文，结构"待定" |

复核任务有明确触发条件：普通区域直接采用 OCR，不默认全部复核；表格结构异常、文字提取缺失、不同证据明显冲突、JSON 解析失败时才进入复核。"看起来没异常"不等于正确，数值错误等静默问题靠抽样验证（§9.3）。

### 6.2 版面检测

本地检测器是 v2 新增组件，已在本机验证（Apple Silicon，CPU，ONNX Runtime，827×1170 像素页面）：

| 模型（rapid-layout 1.2.1） | 首次加载 | 单页推理 | 输出标签 |
|---|---|---|---|
| pp_doc_layoutv3（首选，中文文档训练） | 4.9 s（含 72 MB 下载） | 0.22 s | text / paragraph_title / image / table / … |
| doclayout_docstructbench（对照） | 22.9 s（含下载） | 0.22 s | title / plain text / figure / table / caption / abandon / formula |

三套标签（本地检测器、PaddleOCR-VL、DOCX 结构）映射到 `BlockKind` 的表放在 `parserx/layout/labels.py`，差异只允许出现在这一个文件。检测器输入统一是像素图；DOCX 流式内容不做像素检测。

**阶段一的检测器只做影子运行**：写出检测 Observation 与路由 Decision，不影响输出；§6.3 中"按检测框归属"的做法在阶段四根据影子结果决定是否启用。阶段一的原生 PDF 按 PyMuPDF 文本块组织，块的阅读顺序由几何的递归 XY 切分给出（先分栏，Q33）。

**检测器只负责组织和分类，不裁决内容是否存在，也不是唯一裁判。** 它提供位置和类型证据，也会漏检或误判；其标签是 §6.8 的证据之一，不是判决。

### 6.3 内容获取与归属

顺序固定：**全页提取 → 按版面组织和分类 → 对未归属或异常的区域补识别。**

| 区域类型 | 原生 PDF 页 | 扫描页 / SCAN 子文档 |
|---|---|---|
| text / title / list / caption | 整页提取的原生字符按检测框归属（字符中心点落入框内即归属，不用 `clip` 裁取） | 扫描页引擎 |
| table | 字符归属到表格框后由 §4.3 构建单元格；结构不完整时触发 VLM 复核 | 扫描页引擎；结构异常或置信低时触发 VLM 复核 |
| figure | 渲染裁剪 → §6.7 | 同左 |
| formula | 原生文本 + 归一化；复杂时触发 VLM 复核 | VLM |
| header / footer / page_number | `status = excluded`，记入 sidecar | 同左 |

归属规则：

- **待归属内容**：检测框未覆盖的原生字符按行聚成 `text` 块，`det_confidence = None`，进入正文而不是丢弃。
- **多来源选择**：原生层、OCR、图片子文档、复核候选同时给出同一区域的内容时，由独立的选择步骤裁决：(1) 原生层质量判定通过 → 用原生；(2) 否则用扫描页引擎；(3) 复核候选只在通过接受门（修改位置有图像证据、数字与证据不冲突、结构合法）时才替换。未被选中的 Observation 保留并建立 `duplicate_of`；这是删除 v1 字符串去重逻辑的前提。
- **原生层质量判定**：扫描底图上的旧 OCR 文本层（字符与像素位置不符；以不可见文字——PDF 文本渲染模式 3——为主要信号）、乱码字体（U+FFFD 或私用区比例高）、矢量文字（drawing 多而 text 少）各有判定入口，判定失败即视为无原生层。v1 的页面分类信号（图像覆盖率、乱码比例、矢量页判据）降级为这里的输入。
- **区域重叠**：两个检测框重叠超过 50% 时先合并或按置信度取舍，再归属字符；同一字符不归属两个块。

### 6.4 扫描页引擎

`scan_engine: paddleocr | vlm`，输出形状相同，下游不感知差异：

- `paddleocr`：整页一次远程调用（jobs API），返回文本、表格和布局标签，映射后成为 Observation。默认引擎。
- `vlm`：本地检测器给出区域，VLM 一次调用收到整页图片和区域列表，按区域 id 返回文本（json_schema 约束），几何信息留在本地。2026-09-23 验证：gpt-5.6-luna、gpt-6-luna、gpt-5.4-mini 都能完整返回 14/14 区域且 id 不错位（§10.3）。对照引擎。

页面级复审、逐图 VLM 纠正 OCR 这两层不再存在，取而代之的是有触发条件、输出候选、由程序裁决的复核任务。

### 6.5 嵌入图片路由

原则：**廉价判断只决定"值不值得调用识别"，不直接决定删除资源。**

1. **廉价过滤只做标记**：短边 < 30 px、像素标准差 < 1.0、长宽比 ≥ 12 的图片标为 `decorative_candidate`，资源照常保存，默认不调用识别、不显示。阶段一另加 v1 自 Iter 12 起使用的"琐碎图"规则（面积 ≤ 12000 px² 且长边 ≤ 160 px，图标与标志），见 Q31。"装饰性"与"看不清、未识别"是两种状态，后者标 `unrecognized`，永远保留原图链接。
2. **版面检测**：对图片像素跑 §6.2 检测器。
3. **面积统计**：分母是整图面积；分子是同类检测框的并集面积；文字类 `t` = text+title+list+table 的并集，图形类 `f` = figure/chart 的并集；被 figure 框包含的文字框只计入 `f`；留白不计入任何一类。
4. **路由**（阈值是起点，只在整个语料上调，并单独统计"有信息图片被丢弃"）：

| 条件 | 路由 | 处理 |
|---|---|---|
| `t ≥ 0.6` 且 `f ≤ 0.2` | SCAN | 子区域按 §6.3 取内容并内联；文档中嵌入的图片始终保留原图链接，转录内容作为正文紧跟其后，前加 `<!-- 以下转录自上图 -->` 注释（Q42）；整页扫描的页面图只在识别完整性检查未通过（子区域有非 `ok` 或去向有缺口）时显示 |
| `f ≥ 0.5` 且 `t ≤ 0.2` | FIGURE | 保留图片；做 §6.7 描述 |
| 两者都低、或检出区域置信度都低 | UNCERTAIN | 保留原图并显示；推迟语义判断；进入人工抽查清单 |
| 其他 | MIXED | 文字表格子区域内联；整图保留并描述 |

5. **保存与显示分离**：`assets.save`（默认全部保存）与 `render.show_image`（按路由与状态）是两项独立策略。输出包的 `images/` 放全部提取出的图片，Markdown 只链接需要显示的（Q42）。
7. **阶段二起 SCAN / MIXED 对嵌入图片生效**（Q42）：文字与表格经扫描页引擎转录，紧跟在图片之后；图片内的标题不进入自动大纲。扫描页裁出的图与 UNCERTAIN 仍不转录（Agent 可经 `recognize --blocks` 指定）。
6. 路由结果、`t`、`f`、区域数、完整性检查结果写入 Decision。PDF 与 DOCX 在这一层完全相同。

### 6.6 转录与复核

表格错误分三类，复核只处理前两类：

| 问题 | 例子 | 处理 |
|---|---|---|
| 字符错误 | `8` 识别成 `3` | 对照图像核查字符，输出单元格级候选 |
| 结构错误 | 数据归到错误的列、合并单元格丢失 | 重建 `TableGrid` 候选 |
| 含义解释 | 某列代表什么、单位是否继承 | 表格解释任务，只增加信息 |

三者混在一个提示词里，模型会为了"理解通顺"重新组织甚至改写表格，所以提示词与输出 schema 按任务分开。复核返回候选与修改位置，由选择步骤按接受门决定是否采用；无法判定时保留冲突与原图。

### 6.7 图片描述与表格解释

每项语义结果标注证据层级，机器消费时与转录明确区分：

| 层级 | 含义 | 例子 |
|---|---|---|
| `visible` | 图中明确可见的文字或数值 | 数据标签、图例、节点名 |
| `estimated` | 根据坐标或刻度估读 | 无数据标签的折线图取值 |
| `inferred` | 模型概括或推断 | 未画线的节点关系、趋势描述 |
| `unknown` | 无法确定 | 被遮挡的数值 |

按图类型使用不同 schema：chart（`chart_type`、`title`、`x_axis`、`y_axis`、`series[{name, values}]`、`unit`、`axis_scale`、缺失值）、diagram（`diagram_type`、`nodes[]`、`edges[{from, to, label, direction}]`，方向允许 unknown）、photo / seal / other（`summary`、`visible_text`）。图片形式的表直接返回 `TableGrid` 进入表格块。评测以人工标注的节点、边和数值为对照，"节点数/边数"只是输出规模。描述失败不影响正文；描述与正文不一致不反过来改正文。

### 6.8 章节组织

目标是组织文档，不是重新识别文字；发生在内容基本稳定之后，主要使用文本模型能力，需要核对视觉证据时才引入图像。不采用"字号和编号筛选候选 → 模型在筛剩的候选里修正层级"，因为真正的标题一旦在第一步被排除就无法恢复。流程：

> 提取内容块及证据 → 模型浏览全文、推断每部分的标题惯例（Q81）→ 模型结合局部上下文判断角色 → 结合文档结构统一层级 → 程序检查结果是否合法 → 只对具体冲突补充上下文复核

- **证据**（都是证据，不是判决）：文字内容及前后段落；字号、加粗、缩进、位置、版面检测标签；Word 的样式、大纲级别（`w:outlineLvl`，含样式继承）、标题样式关联（Heading N / 标题 N 及 basedOn 链）、编号定义；当前章节、相邻标题、文档中重复出现的结构。`numbering.xml` 的层级是编号层级，普通列表同样使用它，只用于渲染编号；带编号但无标题样式的段落默认为 LIST，模型仍可依据上下文改判。普通正文误用 Heading 样式、真正标题只做了加粗，都允许模型发现冲突并提出遗漏的标题。
- **两个问题分开**："这一块是不是标题"需要邻近正文和局部版面，按相邻块批量判断；"它是几级"需要章节结构、编号体系和前后关系，在文档级阶段统一。分开是为了防止一次误判向整篇传播。
- **有预算的结构分析**，不是"整篇最多一次调用"：局部判断按相邻块分批；结构清楚的 Word 文档可走确定性路径，但文档级检查发现不一致时仍允许模型提出遗漏或降级；只有程序发现具体冲突（层级跳跃、同一编号模式不同层级、章节为空）时才对相应片段补充复核；预算在配置中，超限保留正文、结构待定。
- **合法性检查**（程序执行）：只能引用存在的块 id；只改 `kind`、`level`、章节归属；层级不得从 H1 跳到 H3；同一上级章节内的同一编号模式层级一致（Q43：附件、附录中另起的编号不与正文比较）；不确定的块保留文本并标 `degraded`，不永久排除。
- v1 的 38 个守卫函数全部退役；若某类误判在语料上普遍存在，改的是证据集合或合法性检查，不是加守卫。

### 6.9 分页、跨页与 DOCX 边界

- PDF：`<!-- PAGE n -->`，`n` 是物理页码。DOCX 区分逻辑分节（`w:sectPr`，continuous 类型不换页）、显式分页（`w:br w:type="page"`、`pageBreakBefore`）与实际排版页码（只有排版引擎知道）；未经排版引擎时只输出 `PAGE-BREAK` 与 `SECTION k` 锚点。
- 跨页续接在块层做：页 i 末尾 TEXT 与页 i+1 开头 TEXT 之间无标题、前者不以句末标点结束 → `continues` Relation 并合并，合并块保留两个 anchor。〔P2-7 实现〕固定流水线只在两段之间只隔页面装饰与脚注时建立关系（与跨页续表相同）；前者须以字母、数字、汉字或句中标点结束（以符号结束的界面标签不算），后者不以编号、项目符号或图表标号开头、与前者文字不同，字体证据一致。两段之间隔着图或表时（操作说明里的截图之后可能是新的一步，论文里的浮动图之后可能是同一句），留给 Agent。
- 跨页表格：列数相同只产生候选；确认需要列位置对齐、表头结构一致、表格身份（图注或前文引用）和页面连续性；合并 `cells` 并保留来源。
- 页眉页脚通过跨页重复检测识别，`excluded`，写入 sidecar（原生页的实现见 `content/furniture.py`，Q34）。
- **OOXML 支持边界**（阶段四前逐项标明支持 / 降级 / 不支持）：文本框、超链接、修订记录、域代码、脚注尾注、嵌套表格、浮动图片与锚定位置、分栏、目录域。"python-docx + 原始 XML"只是手段，不代表这些已解决。阶段一的最小读取器先支持：正文段落、表格（`gridSpan` / `vMerge`）、内嵌与浮动图片、修订记录（建议按接受全部修订的最终视图，被删除的文字计入账目，去向为 excluded，见 Q26）、域代码结果文字、显式分页与分节；其余元素记入 warnings，在账目中记为 failed。文本框在阶段二提前支持（Q44）：每个段落成为锚定段落之后的正文块。依据：simple_doc01 含 104 处插入、25 处删除，v1 经 Docling 丢掉了插入的文字，char_f1 只有 0.410。

## 7. 运行时与实验计划

### 7.1 运行时选项

| 选项 | 对项目的价值 | 主要代价 | 定位 |
|---|---|---|---|
| Codex / Claude Code CLI | 最快建立可工作的 Agent 基线，观察真实处理过程 | 默认行为面向编码；运行策略控制有限；主模型绑定厂商 | 探索与第一、二条基线 |
| Pi 完整 Agent / SDK | 从命令行实验逐步过渡到嵌入式应用 | 需接入并维护其运行时与扩展 | 可控性与集成成本验证 |
| Pi Agent Core | 复用工具调用循环、事件与上下文转换，自己管理文档状态 | 更多应用职责自己实现 | 需要精准控制上下文时 |
| 自研循环 | 对工具、上下文、停止条件完整控制；轨迹可按消息哈希缓存回放 | 恢复、取消、重试、并发修改、上下文裁剪、日志、模型适配都要维护 | 只在现有运行时出现明确缺口时 |

官方无头入口足以做实验，不需要模拟终端：Codex `codex exec`（`--json` 事件流、`--output-schema` 结构化最终结果、配置 MCP 服务）；Claude Code `claude -p`（JSON / 流式事件 / JSON Schema 输出，`--max-turns`、`--allowedTools`，另有 Agent SDK）；Pi 支持 print、JSON、RPC 与 TypeScript SDK，Agent Core 提供工具循环、事件和上下文转换接口。

**复用某个运行时，不等于已获得完整的文档处理运行时。** 文档级检查点、预算、内容覆盖和输出一致性始终是 ParserX 的职责（§3.3）。

### 7.2 探索与验收分开

- **探索阶段**给 Agent 较大自由：允许临时写脚本、尝试不同裁剪、比较 OCR 结果。目的是发现它需要什么工具、在哪里需要看图、什么信息缺失导致反复尝试、哪些能力值得封装。临时脚本可能揭示更好的通用算法。
- **验收阶段**冻结工具和 Skill，要求 Agent 在固定条件下处理没见过的文档；不允许它为每份文档现场写专用转换器。

### 7.3 实验卫生

- 每份文档使用独立会话和工作目录，避免上一份文档的结论影响下一份。
- Agent 只能看到输入和工具，不能看到 `expected.md`、历史答案或评分反馈。ground truth 就在仓库里，直接从项目目录启动尤其容易混入答案；实验目录只放输入。
- 验收期间固定工具代码和评测器，不允许 Agent 为完成任务修改它们。
- 确认看图链路有效：生成 PNG 或返回文件路径不等于主模型看到了图片，需要验证图片如何进入视觉上下文。
- 同时记录主 Agent 和 OCR/VLM 工具的实际调用、耗时及成本。
- 隔离运行时自身的用户级状态（P2-1 发现）：本机 Codex 默认会把 memories（含 ParserX 的讨论）、用户与插件 skills、网页搜索、子 Agent 带进会话，实验一律 `--ignore-user-config --ignore-rules --ephemeral` 并关闭这些功能；卫生以事后审计兜底（shell 以外的工具条目、读取 `~/.codex` 等路径即作废）。`codex exec --json` 的事件流不记录看图，看图核对依据 `read --image` 调用与 Agent 报告的只有看图才知道的细节。
- 人工介入单独记账："经过不断提示后完成"与"一次任务自行完成"是不同结果。

### 7.4 实验顺序

1. **用 Codex CLI 跑通完整任务**（便利性选择，不代表质量判断）：挂上工具包与 Skill，在难例上端到端运行。阶段二的主力模型为 gpt-6-sol（Q35）。
2. **形成可重复执行的文档工具包与 Skill**：按探索结果修订接口。
3. **用 Claude Code 做第二条基线**：观察同样的问题是否仍出现。此时比较的是完整系统效果，底层模型不同，差异不能全归因于框架。
4. **用 Pi 验证可控性与集成成本**：如能使用相同模型、工具和预算，再比较运行时差异。
5. **只有出现明确缺口时才自研**：例如必须精准控制上下文、恢复点或调度。

同时保留固定流水线作为对照（用同一套工具的固定步骤序列）。下一阶段的定义是：**验证"Agent + 文档工具包"能否成为 ParserX 的核心，而不是先开发一个新的 Agent 框架。** 投入沉淀在工具、文档状态、Skill 和评测上，这些资产无论选哪个运行时都能用。

### 7.5 Agent 运行时的完成条件

全文解析的完成条件比文档问答严格：

- 每页都有处理状态；没有被 Agent 注意到的页不能算完成。
- 已提取内容都有去向；`check` 不平衡就不能 `export`。
- 所有改动绑定 Block 与证据，原始结果可回看；只记"我认为应该这样处理"不算证据。
- 同一问题反复调用却没有新证据时停止；达到预算时输出 `partial` 与缺失项。
- 程序检查结构有效性；内容真实性靠抽样人工评测。
- 单个主 Agent + 批量工具；OCR 与小型 VLM 作为工具运行，不发展成独立 Agent。
- 调用记录包含输入、输出、状态变更和证据引用，才能定位错误并局部重跑。

### 7.6 参考

Anthropic 关于 workflow 与 agent 的讨论（[Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)）与工具设计经验（[Writing tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents)）；Codex 非交互模式文档（learn.chatgpt.com/docs/non-interactive-mode）；Claude Code 无头模式文档（code.claude.com/docs/en/headless）；Pi（github.com/earendil-works/pi，`packages/coding-agent` 与 `packages/agent`，TypeScript）；[DocClaw](https://arxiv.org/abs/2608.18685)（2026-08，文档 Skill + 工具循环 + 结构化文档状态，方向相近但未证明在本语料上更优）；[AgenticOCR](https://arxiv.org/abs/2602.24134)（2026-02，查询驱动的按需识别，其效率收益不能直接当作全文转换的收益）。

## 8. 调度、预算、缓存与可复现

### 8.1 三个计数概念

| 概念 | 含义 | 上限（配置项，默认值） |
|---|---|---|
| 识别任务 | 一个块（或一页）需要的一次逻辑识别、复核、描述、解释或结构判断 | 每块最多 1 次基础识别、1 次复核、1 次描述或解释；结构分析按 §6.8 预算 |
| 网络尝试 | 为完成一个任务发出的请求（含重试、参数降级重发） | 每任务最多 3 次；只重试可重试错误（网络、5xx、429、队列满 10010） |
| 复核 | 基础识别未过接受门后以候选形式再识别一次 | 每块最多 1 次；没有新证据不再复核 |

每个任务按（块 id、任务类型、输入哈希）寻址，可单独重跑；请求计数由调度层在发出时记录，不再从元素推算。

### 8.2 调度层与失败状态

所有远程调用经过 `parserx/scheduling/`：文档级截止时间、并发数、请求数与费用预算；请求前预留、完成后按真实用量结算。OCR jobs API 保存 `job_id`，轮询或下载失败时恢复原任务而不是重新提交；限定批次页数并校验返回页数等于提交页数。可重试错误显式列出；4xx 参数错误交给 `llm.py` 的参数降级。每个任务带 `status` 与 `attempts`；文档结束时汇总为 `complete / partial / failed`，每个缺失块记录原因。

阶段零（v1 离线回放与冻结）得出的硬规则：

1. 所有远程调用必须经过 `ServiceGateway`：查缓存、计数、请求、写回；不允许绕过它直接调用客户端。
2. 并发只用于发请求；凡是会修改共享状态的结果处理，都要按任务顺序生效（`run_ordered`）。v1 曾因按完成顺序处理结果而输出不确定，甚至丢失内容。
3. 只对传输错误和无法解析的响应重试；已解析的响应即使没有期望的字段，也不重试。v1 曾因此多花约 14% 的 VLM 请求，并在重试时误判、丢掉内容。
4. 已知的模型参数约束（例如 luna 不接受 temperature）写进配置，不靠 400 去试探；OpenAI SDK 设 `max_retries=0`，尝试次数由调度层统计。服务偶尔卡住不回（P2-6 第二轮确认：同一批 4 个问题中 2 个 2 s 内返回、2 个始终没有返回）：流式回答两段数据之间超过 `stream_idle_timeout`（默认 60 s）即按传输错误重试，流中途断开同样重试。
5. 记录 token 用量，并按配置中的价格表结算费用。

### 8.3 缓存与任务级重跑

- 缓存键覆盖完整请求语义：输入图片字节（预处理后）、区域列表、提示词与 Skill 内容哈希、json_schema 哈希、模型名与端点身份、识别选项、`reasoning_effort`。`prompt_version` 保留作可读标签，不是唯一失效机制。原始响应缓存与后处理结果缓存分开（`.parserx_cache/raw/`、`.parserx_cache/derived/`）。
- 任务级重跑：`parserx rerun --block <id> --task table_review` 只重跑该任务并重新走选择步骤；调试时同时看到原图、OCR 结果、复核候选和最终采用理由。
- 自研循环若出现，每轮模型输出按消息哈希缓存，同一输入走同一条轨迹，Agent 运行时也能进回放回归。

### 8.4 三种验收分开

| 验收 | 做法 | 证明什么 |
|---|---|---|
| 回放回归 | 固定响应（缓存），跑核心集 | 代码改动没有改变结果 |
| 服务契约检查 | 小样本真实请求（`scripts/check_services.py` 及少量区域） | 接口、参数、失败处理仍然正确 |
| 质量评测 | 固定语料与标注，真实请求或冻结 run | 实际识别质量 |

连续两次缓存结果一致只证明本地回放稳定；luna 系列无法设 temperature，新请求本身不稳定，是正常现象。阶段验收用完整冻结的 run（代码提交、配置、输入、标注、响应缓存、指标版本全部可追溯）；`best_scores.json` 逐指标取历史最优的看板保留作趋势参考，不作验收依据。

## 9. 评测体系

### 9.1 三层测试与核心集

| 层 | 内容 | 何时跑 | 目标耗时 | 服务调用 |
|---|---|---|---|---|
| L0 单元测试 | `uv run pytest -q --ignore=tests/test_live_e2e.py`，全部离线；需要服务的用例标 `live_e2e`，单独运行（`uv run pytest tests/test_live_e2e.py -q`，不用响应缓存） | 每次改动 | < 15 s | 无 |
| L1 核心回归（回放） | `scripts/regression_test.py --core`，文档列表在 `configs/regression_core.txt`，响应来自缓存 | 每个任务结束、每次提交前 | 缓存命中 < 1 min；全未命中约 3 min | 缓存未命中时才调用 |
| L2 全量回归（冻结 run） | 全部 ground truth（含隔离验证集与 `ground_truth_public/` 子集），真实调用 | 阶段退出、发布、改提示词或换模型后 | 约 20 min | 全部 |

核心集选取规则：每类输入各一篇、优先最小、保留一篇当前得分最差的作哨兵；只增不换。当前 6 篇：

| 文档 | 代表的输入类 | 页数 | 冻结基线 O/V/L 请求 | 冻结基线耗时 |
|---|---|---|---|---|
| deepseek | 原生 PDF（矢量图预扫描每次触发 1 次 OCR 请求，结果被去重、旧计数漏记；离线回放依赖缓存） | 1 | 1/0/0 | 0.5 s |
| text_table01 | 原生 PDF（默认配置下 LLM 1 次来自质量检查，回归配置中关闭） | 3 | 0/0/0 | 0.0 s |
| receipt | 原生 PDF 小票（含小图标；旧表误标为扫描件） | 3 | 1/2/0 | 2.5 s |
| ocr_scan_jtg3362 | 扫描中文标准文档，表格 | 4 | 1/11/0 | 15.4 s |
| simple_doc01 | DOCX，char_f1 最差（指标 2.0 下 0.410）哨兵 | | 0/0/0 | 0.0 s |
| text_report01 | DOCX 含嵌入图片 | | 2/2/0 | 2.5 s |

### 9.2 验收工具的已知漏洞与修复清单

2026-09-23 外部审核给出五个最小反例，本地全部复现：

| 反例 | 当前结果 | 原因 |
|---|---|---|
| 表格中"甲=10、乙=20"改为"甲=20、乙=10" | table_f1 = 1.0 | `compute_table_metrics` 对配对表格用单元格内容多重集，忽略行列位置 |
| 两张表只输出第一张 | table_f1 = 1.0（仅 expected_count 记 2） | 未配对的表格不进入 F1 分母 |
| 同一张表改为 HTML 输出 | 检出表格数 0 | `_extract_tables` 只识别 GFM 管道表 |
| 交换句子中的甲、乙主体 | char_f1 = 1.0（edit distance 0.25） | `compute_text_metrics` 用字符频次，忽略顺序 |
| 全部文档解析失败 | 退出码 0，打印 All clear | 退出码只取决于指标回退，失败文档只打印 |

另外 `best_scores.json` 逐指标取历史最优；`pipeline._collect_api_calls` 按元素推算调用数。修复清单（修完后 v1 基线重算，旧分数不再沿用）：

1. 表格结构指标：GFM 与 HTML 先归一为 `TableGrid`，按 (row, col, rowspan, colspan, content) 配对计算单元格位置 F1，另报表头关联正确率和合并单元格正确率。指标 2.1（Q28）：标注无法表达合并时不比较跨度。
2. 硬检查：失败文档数、未执行文档数任一非零则退出码非 0，冻结 run 拒绝执行；漏表数、多余表数逐篇与冻结基线比较，增加才算失败，第一次冻结只记录不拦截（2026-09-23 决定，Q16）。基线不再写 `ground_truth/best_scores.json`。
3. 阅读顺序指标：块序列的 Kendall tau 或成对逆序率；保留 edit distance。
4. 关键内容错误统计：数字、单位、否定词、日期在输出与标注间的不一致计数。
5. 真实请求计数：由调度层记录。
6. 冻结 run：阶段验收使用完整的一次运行。

### 9.3 规则

- 报告顺序固定：硬检查 → 表格结构 F1 → char_f1 与 edit distance → 阅读顺序 → heading_f1 → 关键内容错误 → 真实请求数 → 耗时 → 费用。L2 报告入 `eval_reports/`，文件名含日期和阶段号；L1 只在终端打印。
- L1 默认 `--offline` 且确定性模式；缓存未命中时提示并允许 `--allow-calls`。
- 新增语料先加 ground truth 再改代码；不为通过某篇文档改阈值；隔离验证集（按文档来源或模板划出，组成见 Q8）不参与调参。
- 图片路由单独评测：每张图片记录期望路由（SCAN / FIGURE / MIXED / UNCERTAIN / 装饰），报告混淆矩阵与"有信息图片被丢弃"数量；归入 L1。
- 静默错误抽样：L2 每次从含数字、单位、日期的块中随机抽固定数量，人工对照原图；抽样错误率单独记录。
- 按 AI 任务类型分别评测：转录/复核按忠实度（含"原文错误被保留"用例），描述按证据层级标注，章节按角色与层级分别计分。
- 图片描述从 char_f1 中剔除、按语义 schema 单独打分（receipt 的 ground truth 描述句按 gpt-5.4-mini 的措辞写成，直接比对会偏向旧模型）。阶段零先做剔除：两侧的 `> [图片] …` 与 `![…](…)` 不进入文本类指标，只报告图片占位数；按 schema 打分阶段四再做（Q17）。
- 替换一个 v1 模块时，删除只服务于其实现细节的单元测试；其承载的正确性要求先登记到 §11.5。

### 9.4 运行时对比协议（阶段三）

- **难例集**：每类至少一篇且不在核心集内：密集中文表格、跨页表格、混合扫描页、手工排版 Word、复杂标题层级、照片与正文混排。
- **控制变量**：主 Agent 用同一底层模型（能做到时）、工具内部的服务层模型固定不变（Q40）、同一套七个工具、同一预算与截止时间；避免把"换了更强模型"误认为架构优势；模型不同的对比（Codex 对 Claude Code）只比较完整系统效果。
- **观察项**：

| 维度 | 指标 |
|---|---|
| 质量 | 信息保全、数字归属、表格结构 F1、标题角色与层级 |
| 泛化 | 遇到陌生文档时需要新增多少专用规则或 Skill 文字 |
| 成本 | 主 Agent 与工具各自的真实请求数、token、费用、耗时、重复调用、未完成比例 |
| 稳定 | 同一文档多次运行的波动 |
| 可调试 | 一个错误能否定位到具体步骤并局部重跑 |
| 人工介入 | 次数与内容，单独记账 |

- **结论方式**：冻结 run 报告入 `eval_reports/`；Q13 记录决定与依据。允许的结论包括"默认流水线、难例交 Agent"。
- **协议补全（P2-8，2026-09-24）**：
  - **参与的运行时**：固定流水线 v2；Codex（主 Agent gpt-6-sol、推理强度 medium、经 `ask_image` 看图，Q35、Q47）。用户决定暂不用 Claude Code；采用哪个第三方 Agent 框架（Pi 或其他）是单独的议题，其余工作完成后再切换，届时按本协议补跑同一文档集。
  - **文档**：未见集 6 篇（`configs/phase3_unseen.txt`）：隔离集 3 篇在 `ground_truth/`，新文档 3 篇在 `ground_truth_unseen/`（开发中的运行不扫描该目录）。验证集（`ground_truth/val_word_template01`）属于开发语料，不参与。
  - **条件**：同一个工具包快照、规则 r2，截止时间按 Q39（30 / 90 分钟）；卫生审计、完整性检查与作废规则同 §7.3 与阶段二计划 §2.5。
  - **重复**：每篇每个运行时 2 次，比较波动。
  - **计数与评分**：同阶段二的运行记录。主 Agent 的 token、步数、耗时、按标价折算的费用，与工具内的服务请求、费用分开记账；质量用 L2 指标（char_f1、表格 F1、heading_f1）加标题角色 F1（Q46）与待核对项数（文档摘要 `review`）；人工介入单独记账。
  - **命令**：`regression_test.py --gt-dir ground_truth --gt-dir ground_truth_unseen --list configs/phase3_unseen.txt`（固定流水线）；Agent 运行由实验装置执行，评分同阶段二。

### 9.5 问题定位方法（2026-09-25，用户要求写入）

遇到"某篇文档处理得不好"时，先定位问题在哪一层，再决定修哪里。固定流水线与 Agent 不是两套系统：固定流水线是按固定顺序调用工具包，Agent 的第一步 `process` 也是同一套标准处理，再在其上做判断与修正。一份输出经过五层：

| 层 | 做什么 | 出问题时的表现 |
|---|---|---|
| ① 内容获取与算法 | 取原生文字、OCR、识别表格、标题与页面装饰（含外部 OCR / VLM 服务） | 错误从这里产生，两种运行时错得一样 |
| ② 待核对信号 | 程序自查，把可疑处列入待办 | 错了，但谁也不知道 |
| ③ 接受门 | 程序判断一处修改能否采用 | Agent 看出来了、修法也对，却被拒 |
| ④ 工具能力 | 有没有相应的操作 | 看出来了，但没有办法表达修法 |
| ⑤ Agent 判断 | 看哪里、怎么判断、改什么 | 有信号却没看，或看错、改错 |

对每个缺陷依次问四个问题，答案都在运行记录里：

1. 固定流水线的输出错了吗？错了 → 错误产生在 ①（sidecar 里能查到哪个引擎、哪一步）。
2. 待核对项（文档摘要的 `review`）里有它吗？没有 → ② 缺信号。
3. Agent 注意到了吗（`ask_image` 的提问、最后的报告）？有信号却没注意 → ⑤。
4. Agent 提了修改吗？被拒 → 看被拒的候选对不对：对 → ③ 门检查的对象不对；不对 → 门起了作用。Agent 说"没有工具" → ④。

**第一次应用**（阶段三，[对比报告](../eval_reports/2026-09-24_p3_runtime_comparison.md) §4）：

| # | 错误产生在 | 为什么没人发现 | 为什么没修好 |
|---|---|---|---|
| D1 斜向水印 | ① 取原生表格时不分文字方向 | ② 无信号 | ③ 门规定原生文字的数字一律不能改；④ 原生页不能再用 OCR 取旁证 |
| D2 OCR 漏行 | ① 外部 OCR 服务漏读 | ② 扫描页没有完整性检查 | Agent 按设计只做定向抽查，没有信号就不会发现 |
| D3 表格结构 | ① 表格识别（无框线表、图中框线、正文加批注） | ② 无信号 | ③ 门按整格比较，把拆行判为丢格；④ 没有"把误识的表还原为正文"的操作 |
| D4 标题 | ① 标题识别（版式标题、无样式标题） | ② 14 页只有一个标题也不提示 | Agent 修好了 |
| D5 扫描软件水印 | ① 扫描页不识别页面装饰 | ② 无信号 | Agent 修好了 |
| D6 跨页接缝 | ① 取跨页表格 | ② 无信号 | ③ 复核被门拒（与 D1 的水印数字纠缠） |

归纳：根源都在 ①（两种运行时共用，修这里收益最大）；"看到了却修不了"在 ③④——门背后两条假设在这些文档上不成立："原生文字层里的数字就是原文"（水印也是原生文字）、"内容守恒按整格计"（拆行、合并时整格文字必然变）；"没人看到"在 ②；⑤ Agent 本身没有明显问题。

修门时不放宽门：门的作用是"模型的修改只是候选，由程序决定是否采用"，修的是门检查的对象，依据必须是程序能核实的事实，不是 Agent 的说法。

**本地读数的双向比对**（Q56，2026-09-25 实现）：`process` 的 reading 步骤用本地识别器（rapidocr，PP-OCR ONNX，CPU）读每个 PDF 页面的渲染图（150 dpi），连同版面检测器标为图片、图表、印章、行间公式的区域存为 `PageReading`（证据，不输出）；`reading/compare.py` 在待核对项中列出两个方向：

- `text_unaccounted`（按页）：页面图像上有、却没有任何块承接的行——既不在它所在位置的块里，也不在本页任何地方。输出、有理由的排除、合并都算承接；被取代的读数（duplicate）不算。图片块与图片、公式区域内的文字不作散文比较。同一行在多页都无承接，按跨页重复视为页面装饰。
- `text_not_seen`（按块）：显示的块中某一段（表格单元格、句子）在块所在位置（跨页表格看所占的每一页）与这些页上都读不到。
- 页面读数为空时没有证据，不比较。只比较字母与数字（NFKC、全角折叠、去掉标记），标点、空白与 LaTeX 命令不算差异。

**两个容差**（测量两份独立读数是否一致，不是对内容的判断）：`NEAR` = 0.5（文字的相邻字符对在对方读数同一位置出现的比例）、`SOMEWHERE` = 80（rapidfuzz partial_ratio，在整页上的连续近似匹配）。**校准**（全语料 26 个 PDF，本地读数 6125 行、输出片段 4426 个）：页面→输出方向标出 31 行，其中 7 行是确实的漏读（text_table_word 第 2 页表头、patent01 授权公告日、unseen_scan_form01 漏行；前两处此前未发现），其余多为图表标签、公式与标志图形的误读；输出→页面方向标出 45 段，16 段确有问题（公式乱码、界面元素、扫描软件字样、"〔图1〕 同"、手写签名的转写），其余多为行内公式。生产代码在语料上共列出 45 项（16 页、29 块），一半以上来自两篇公式密集的论文。每页约 0.6 s，无费用；读数与检测结果进派生缓存。

## 10. 外部依赖现状（2026-09-23）

### 10.1 总表

| 依赖 | 用途 | 状态 |
|---|---|---|
| PaddleOCR-VL-1.6，AI Studio 官方 jobs API（`https://paddleocr.aistudio-app.com/api/v2/ocr/jobs`） | 扫描页引擎 | ✅ 已接入并实测；19 页 PDF 单次提交成功；高峰期排队数十秒到两分钟；队列满返回 HTTP 400 + `code 10010`，客户端等待重提 |
| 官方端点 `api.openai.com`（`*_B` 环境变量） | 默认 LLM/VLM | ✅ VLM、LLM 均为 gpt-6-luna，经服务层通过 |
| 中转端点 `OPENAI_BASE_URL`（Codex 账号中转） | 原默认，已停用 | 拒绝 gpt-5.4-mini、视觉 429；静默吞掉 `temperature`，不能作兼容性依据；只供试验 |
| DashScope `qwen3.6-plus` | 备用 | ✅ 文本与视觉正常（约 5 s）；`json_schema` strict 不生效，需围栏剥离与宽松解析 |
| LlamaCloud key | 工具对比评测 | ✅ 有效；Python 包 `llama-parse` 无引用，实际走 `scripts/llamaparse_to_markdown.ts`（需 `npm install`） |
| LibreOffice 26.8 / Node 26.8 / uv 0.10.11 / Python 3.13.12 | .doc 转换 / 评测脚本 / 运行环境 | ✅ |
| tesseract 5.5.3 | 无引用；根目录 `eng.traineddata` 为遗留文件 | 删除文件，不引入 |
| rapid-layout 1.2.1 + onnxruntime | 本地版面检测（§6.2） | 阶段一引入 |
| Codex CLI 0.156.0 + gpt-6-sol（推理强度 high） | 阶段二探索的 Agent 运行时（Q35） | ✅ 2026-09-24 无头调用实测可用；模型在命令行上显式指定。用当前版本或以后的新版本，不固定（Q41）；运行记录写实际版本 |

### 10.2 OCR

- 接入形态：异步 jobs，multipart（`file`、`model`、`optionalPayload`），`Authorization: bearer …`，轮询 `GET …/jobs/{jobId}`，下载 `resultUrl.jsonUrl`（JSONL）。`prunedResult.parsing_res_list` 结构与旧同步接口一致，新增 `block_id`、`group_id`、`block_polygon_points`，图片块 `block_order` 为 `null`；`markdown.text` 含 HTML，直接比对 ground truth 会低估。`useOcrForImageBlock=true` 会把图标内符号识别出来，属噪声。
- 原始识别质量（去 HTML 标记后按字符）：ocr01 F1 0.961、ocr_scan_jtg3362 0.961、receipt 0.929。全量回归对比 Iter 32 基线：edit 0.244→0.226、char_f1 0.897→0.907、table_f1 0.487→0.531，OCR 调用数不变；同期还换了 LLM 端点并合入 Iter 33，差值不全归因于 OCR。
- 远程 OCR 从"必需"降级为"引擎之一"：可用性不由我们控制；本地版面检测已可行；VLM 转录成本约 0.0005 美元/页。其他途径备查：百度智能云企业级 API（异步，¥0.09/页，1000 页免费，PDF ≤500 页）、千帆同步接口（`paddleocr-vl-0.9b`，版本未标明，¥0.18/页）、第三方托管 0.9B 识别模型（SiliconFlow 免费 1.5、Fireworks 1.6，需本地版面检测，与 `vlm` 引擎同构）、自托管（需 GPU）。

### 10.3 LLM/VLM

官方端点模型与定价（美元/百万 token，短上下文，OpenAI 定价页 2026-09-23）：

| 模型 | 输入 | 缓存输入 | 输出 | 实测 |
|---|---|---|---|---|
| gpt-5.4-mini | 0.75 | 0.075 | 4.50 | 文本、视觉、json_schema 正常；2.4 s |
| gpt-5.6-luna | 0.20 | 0.02 | 1.20 | 整页转录 `effort=none` 3.5 s、244 token，charF1 0.924 |
| gpt-5.6-terra | 2.00 | 0.20 | 12.00 | 视觉正常 |
| gpt-6-luna（默认 VLM） | 0.10 | 0.01 | 0.50 | 整页转录 `effort=none` 2.9 s、248 token，charF1 0.927 |
| gpt-6-sol | 2.00 | 0.20 | 10.00 | 服务层未测 |

**模型分工（Q40）**：ParserX 用到两类模型，分别选型、分别更换，不互相替代：

| 角色 | 做什么 | 选型原则 | 当前 |
|---|---|---|---|
| 主 Agent 的模型 | 读概况、决定下一步、判断标题与结构、决定何时复核、看图核对 | 能力优先 | gpt-6-sol（Codex，Q35） |
| 服务层的 LLM / VLM | 工具内部的转录、复核、图片描述、表格解释、结构判断等单项任务 | 经济、速度优先；某类任务确实不够时才按任务升级，并记录依据 | gpt-6-luna（`parserx.yaml`，经官方端点） |

上表的价格与实测针对服务层。主 Agent 经 Codex 账号调用，不经服务层，用量由实验装置从 Codex 事件流记账（§5.2 的分开计数）；探索与对比实验中，服务层模型保持不变，只让主 Agent 的模型按实验设计变化。

接口事实（已在 `services/llm.py` 处理；`parserx.yaml` 对 gpt-6-luna 显式设 `send_temperature: false`，不靠 400 探测）：gpt-5.6-*/gpt-6-* 拒绝 `temperature`；Chat Completions 两代模型都拒绝 `max_tokens`，要求 `max_completion_tokens`；推理 token 会耗尽过小的输出预算返回空文本；`reasoning.effort` 支持 none/low/medium，不支持 minimal。实现方式不按模型名维护能力表，而是后端 400 "Unsupported parameter/value" 时去掉或改名该参数、记入实例并重试一次；`ServiceConfig` 新增 `reasoning_effort`、`send_temperature`、`min_output_tokens`；`parserx.yaml` vlm `none` + 1024，llm `none` + 256。luna 无法设 temperature，同一输入两次输出有差异（receipt char_f1 0.962 / 0.954，gpt-5.4-mini 两次均 0.971），复现性只能靠缓存。

单页探测（ocr01 第 1 页，827×1170）：三款模型整页转录与按区域转录（14/14 区域，精确率 ≥ 0.99）相当；6 路并发全部成功；示意图语义提取 gpt-5.6-luna 边关系略好于 gpt-6-luna，阶段四在完整语料复核。模型分层：转录与复核 gpt-6-luna（`none`）；图片描述与表格解释 gpt-6-luna（`low`），复杂图表可升级 gpt-5.6-terra；文档级结构判断 gpt-6-luna（2026-09-23 起为 LLM 默认模型）。

### 10.4 Python 依赖

阶段五 P5-5（2026-09-26）后与 `pyproject.toml` 一致：

| 包 | 用途 | 位置 |
|---|---|---|
| pymupdf 1.28.2 | PDF 读取、渲染、原生表格、矢量图形 | 主依赖；2026-09-26 从 1.27 升级（Q80）：段落改由版面检测器分，不再用库的分块，1.27 与 1.28 的输出逐字节相同；`import fitz` 改为 `import pymupdf` |
| openai 3.19、httpx2 | VLM 客户端（服务层） | 主依赖；2026-09-26 从 2.30 升级：3.x 的 HTTP 层换成 httpx2（重试判定的传输错误类型随之改名），回放与真实调用都通过 |
| pydantic、pyyaml、python-dotenv、pillow、numpy、requests、jsonschema、rapidfuzz、lxml | 数据模型、配置、图像、OCR jobs API、sidecar 校验、评测与读数比对、DOCX 直接读 OOXML | 主依赖（lxml 原先经 docling 间接安装，现显式列出） |
| rapid-layout、rapidocr、onnxruntime | 本地版面检测（§6.2）、本地读数（Q56） | 主依赖 |
| pdfplumber、python-docx、datasets、huggingface-hub | `parserx tool-eval` 的内置对照解析器、OmniDocBench 下载 | 可选组 `bench`；pdfplumber、python-docx 也在 dev 组（测试造 DOCX） |
| pytest、pytest-asyncio | 测试 | dev 组 |
| ~~docling~~、~~pypdf~~、~~llama-parse~~、~~fonttools~~ | — | 已删除（docling 随 adapter:v1 与 v1 的 DOCX provider 退役；其余无引用） |

## 11. 代码迁移清单

### 11.1 可复用算法（需要适配接口）

| 路径 | 可复用 | 需要适配 |
|---|---|---|
| `parserx/config/` | schema 与 YAML 加载 | 新增 layout、cache、scan_engine、预算、模型分层、运行时选择 |
| `parserx/services/ocr.py` | jobs API 客户端（阶段零已接入 `ServiceGateway`：缓存与计数） | ✅ P1-3：job_id 恢复、返回页数校验、重试交给网关 |
| `parserx/services/llm.py` | 参数降级、reasoning、结构化输出（阶段零已接入缓存与计数） | ✅ P1-3：token 用量回调与费用；SDK `max_retries=0`，重试交给调度层 |
| `parserx/tables/html.py`（阶段零从 `builders/ocr.py` 迁出） | HTML 表格解析与 `_build_table_grid`；`TableGrid.from_html/from_gfm` 已有 | 阶段一：`to_gfm`、`to_html` |
| `parserx/builders/image_extract.py` | OOXML 图片收集（正文顺序、`a:xfrm` 旋转）、ImageMask 反色、矢量图渲染 | 去掉 Docling 对象与 `docling_self_ref` 依赖，产出 Asset |
| `parserx/providers/pdf.py` 页面分类信号 | 图像覆盖率、乱码比例、矢量页判据 | 降级为原生层质量判定的输入 |
| `parserx/processors/table.py` 跨页合并 | 列数匹配、表头去重 | 改在 `TableGrid` 上做，列数相同只产生候选 |
| `scripts/regression_test.py`、`parserx/eval/` | 阶段零已完成：指标版本 2.0、硬检查、`--core`、`--repeat`、`--freeze`、`--replay` | 阶段一：冻结 run 另存 sidecar；支持 `pipeline: v2` |

### 11.2 重新设计

| 路径 | 原因 |
|---|---|
| `parserx/verification/completeness.py` | 依赖旧标志与 GFM；由去向检查替代 |
| `parserx/verification/hallucination.py` | 只比较描述与 OCR 文本；改为按证据层级校验并迁移"数字不得被改写"要求 |
| `parserx/assembly/markdown.py` | 输入改为 Block/TableGrid；实现 §4.5 契约 |
| `parserx/providers/docx.py` | 直接解析 OOXML，按 §6.8–6.9 的证据与边界。阶段一先在 `parserx/content/docx.py` 做最小读取器（见 §6.9），不经 Docling；v1 的 provider 保留到阶段五 |
| `parserx/pipeline.py` | 改为固定流水线运行时：提取 → 版面 → 归属 → 图片路由 → 识别与复核 → 层级 → 跨页 → 去向检查 → 渲染 |

### 11.3 新模块

| 新路径 | 职责 |
|---|---|
| `parserx/ir/` | Block、SourceAnchor、Observation、Relation、Asset、Decision |
| `parserx/workspace/` | 文档工作区：状态读写、概况、页面与区域读取 |
| `parserx/tools/` | 七个工具与统一返回信封；JSON CLI 入口；MCP 适配 |
| `parserx/skills/` | 三份任务指导（Markdown，带内容哈希） |
| `parserx/layout/` | 检测器封装、标签映射、面积统计 |
| `parserx/routing/image.py` | §6.5 |
| `parserx/content/` | 全页提取与归属、扫描页引擎（paddleocr / vlm）、公式 |
| `parserx/tables/` | `TableGrid` 构建、GFM/HTML 生成、跨页合并 |
| `parserx/semantic/` | §6.7 |
| `parserx/hierarchy/` | §6.8 |
| `parserx/scheduling/` | §8.2（阶段零已有计数器与 `ServiceGateway`） |
| `parserx/accounting/` | 去向检查 |
| `parserx/cache/` | §8.3（阶段零已完成） |
| `parserx/render/` | §4.5 的 Markdown 与 sidecar 导出（v1 的 `assembly/` 不动） |
| `parserx/prompts/` | 工具内部 VLM 任务的提示词文件，带内容哈希 |
| `parserx/runtimes/` | 运行时适配：`pipeline`（固定序列）、`codex`、`claude_code`、`pi`（启动、事件收集、工具适配） |

### 11.4 删除（阶段五）

`parserx/processors/chapter.py`、`processors/image.py` 的路由与纠正逻辑、`processors/vlm_review.py`、`builders/ocr.py` 中的结果整合与去重、`models/elements.py` 的自由字典、Docling 依赖、`pypdf`、`llama-parse`、根目录 `eng.traineddata`。

✅ 阶段五已执行（P5-4、P5-5，2026-09-26）：连同 `processors/`、`providers/`、`builders/`、`assembly/`、`verification/` 整个删除（约 11,500 行，20 个测试文件），v1 的配置段与只对 v1 有效的命令行参数一并删除；v1 可从本地标签 `v1-final` 取回。

### 11.5 正确性要求登记（随模块替换迁移，不随测试删除消失）

阶段五 P5-1（2026-09-26）逐条落到 v2 的测试（[报告](../eval_reports/2026-09-26_p5-1_correctness_tests.md)）：

| 要求 | 来源 | 迁往 | v2 的测试 |
|---|---|---|---|
| VLM 输出的数字与 OCR/原生证据不一致时不得覆盖（如 100 万元 → 999 万元） | `test_image_processor.py::test_vlm_json_falls_back_to_overlap_evidence_on_number_mismatch` | 复核候选的接受门（`content/select.py`） | `test_select.py::test_text_candidate_cannot_rewrite_numbers_against_evidence`、`::test_native_numbers_are_never_overwritten`；`test_tools_contract.py::test_a_correction_needs_the_image_and_keeps_native_numbers` |
| 合并块不得丢失任一来源 | `test_line_unwrap.py` 的 bbox 合并用例 | Block 多 anchor 合并；原生块的框是各行框的并集 | `test_content_pdf.py::test_a_block_of_several_lines_spans_every_line`（新）；`test_table_merge.py::test_merge_appends_rows_and_keeps_every_source`；`test_ir_models.py::test_multi_anchor_block_keeps_every_source` |
| 有文字重叠的图片不得同时输出描述与重复正文 | `test_verification.py` 的 text-heavy 用例 | 图片路由：有文字的图转写、不描述 | `test_tools_contract.py::test_process_transcribes_scan_and_mixed_images_and_does_not_describe_scans`。全语料核对：203 条"可见文字"无一与正文重复，不另加守卫 |
| ~~页眉页脚首页身份信息保留上限~~ | `test_header_footer.py` | **撤销**（Q77）：v2 按 §6.9 与 Q34 排除全部页眉页脚，文字在 sidecar 可查 | `test_content_pdf.py::test_running_headers_and_page_numbers_are_excluded`（断言现行做法） |
| 跨页表格列数不同不得合并 | `test_table_processor.py` | `TableGrid` 合并候选校验与合法性检查 | `test_table_merge.py::test_what_is_not_a_candidate`、`::test_merge_legality`（新增：Agent 请求合并列数不同的表被拒） |
| 被跳过或被纠正的图片，其区域内容必须有去向：不得因为另一张图已抑制共享的 OCR 文本而把本图判为"已被覆盖"并丢弃 | P0-5 冻结回放发现（ocr_scan_jtg3362 封面三行内容随线程顺序丢失） | 去向检查（accounting）+ 本地读数比对 | `test_accounting.py::test_a_duplicate_whose_original_is_hidden_is_a_silent_loss`（新：`duplicate_of` 链只通向隐藏块的重复块是静默丢失，不能导出）；`test_page_reading.py::test_only_a_shown_figure_holds_the_text_inside_it`（新：未显示的图片不算其中文字的去向）、`::test_a_superseded_reading_is_no_destination` |
| 并发识别任务的输入（证据、提示词）不得依赖其他任务的完成顺序；同一输入同一请求 | `test_image_processor.py::test_overlap_evidence_does_not_depend_on_other_images_finishing_first`（P0-4 发现的竞态） | 调度层：任务输入在派发前确定，结果由选择步骤按确定顺序合并 | `test_tools_contract.py::test_batch_inputs_do_not_depend_on_other_tasks`（新：一批描述与逐张描述发出的图像、提示词、上下文相同）、`::test_batch_results_do_not_depend_on_completion_order`；`test_scheduling.py::test_results_apply_in_task_order_whatever_the_completion_order`。`recognize`、`ask_image`、公式的输入同样在 `run_ordered` 之前由状态快照与不变的文件确定（代码核对） |

阅读顺序、代码块、行内格式、列表、图注、交叉引用各自在 §12 有归属阶段，不允许"顺手删掉"。

## 12. 阶段路线

统一退出条件：**新路径在冻结 run 上不低于旧路径（按 §9.2 修复后的指标），且真实请求数不增加。** 旧流水线留在 `pipeline: v1 | v2` 开关后面，直到阶段五完成再删。

| 阶段 | 目标 | 产出 | 退出条件 | 状态 |
|---|---|---|---|---|
| 0 冻结现状与修验收工具 | 评测可信、快速、可复现 | ✅ 默认端点切换、OCR 恢复、`llm.py` 适配并切到 gpt-6-luna；✅ 分解与阶段一接口确认；✅ P0-1 §9.2 指标修复与硬检查（指标版本 2.0，[报告](../eval_reports/2026-09-23_p0-1_metric_fix.md)）；✅ P0-2 回归配置 `configs/regression.yaml` 关闭全部 LLM（含质量检查），核心集实测 LLM 请求为 0；✅ P0-3 缓存层（OCR、VLM、LLM；核心集离线回放 0 请求、输出逐字节一致，[报告](../eval_reports/2026-09-23_p0-3_cache.md)）；✅ P0-4 回归分层（`--core` 默认离线回放、`--allow-calls`、`--repeat`、多 ground truth 目录；核心集回放两次一致，整条命令 4.2 s）；✅ P0-5 冻结 v1 基线 `eval_runs/2026-09-23_p0_v1_gpt-6-luna`（27 篇、提交 78556f4、两次离线回放逐项一致，[报告](../eval_reports/2026-09-23_p0-5_v1_frozen_baseline.md)）并划出隔离验证集（patent01、paper01、text_pic02） | 五个反例全部被指标或硬检查捕获 ✅；核心集回放两次一致 ✅；冻结 run 存档于本地 `eval_runs/`（不入 git）✅ | ✅ |
| 1 文档工具包 v1 | 建立数据模型、约束与工具（分解见 [v2_phase1_plan.md](v2_phase1_plan.md)） | `ir/`、`workspace/`、`scheduling/`（预算、`run_ordered`、费用）、`content/`（原生 PDF、paddleocr、DOCX 直接读 OOXML、选择步骤）、`accounting/`、`render/`；七个工具的 JSON CLI 与返回信封；三份 Skill 草稿；`layout/` 与 `routing/image.py` 影子运行；固定序列运行时与 `pipeline: v1 \| v2` 开关；验收文档 text_table01、receipt、simple_doc01，另加扫描路径 ocr_scan_jtg3362（Q23） | L0 覆盖五个 IR 概念、TableGrid 往返、七个工具契约、去向检查与调度；三篇的 v2 冻结 run 的信息类指标与 heading_f1 均不低于 v1、真实请求数不增加；扫描路径能执行、去向平衡、信息类指标不低于 v1；每篇去向平衡、sidecar 通过 schema 校验、冻结 run 可回放；v2 的 L1 回放两次一致 | ✅ 2026-09-24：P1-1 至 P1-11 全部完成；v2 冻结 run `eval_runs/2026-09-24_p1_v2_toolkit`，全部退出条件满足（receipt 的文本类指标与 heading_f1、jtg3362 的 heading_f1 按 Q23/Q27/Q32 只报告），[验收报告](../eval_reports/2026-09-24_p1_toolkit_acceptance.md)。全量语料参考运行（非退出条件）发现的差距按 Q33（e14752f 重新冻结，`--replay` 通过）与 Q34 在阶段二前处理；剩余差距见验收报告 |
| 2 Agent 探索 | 发现工具缺口 | Codex CLI（主力模型 gpt-6-sol，Q35）挂工具包与 Skill，在难例上端到端运行，允许临时脚本；记录需要的工具、看图点、缺失信息、值得封装的能力；产出工具包 v1.1 与候选通用算法 | 探索报告；工具包修订完成 | 🟡 分解已确认（[v2_phase2_plan.md](v2_phase2_plan.md)，Q35–Q39）；✅ P2-1 实验装置（`scripts/agent_explore.py`、`runtimes/codex.py`、`runtimes/px.py`、`runtimes/experiment.py`、工作区状态摘要与 `workspace_tampered`；text_table01 跑通、审计通过，[报告](../eval_reports/2026-09-24_p2-1_harness.md)）；✅ P2-2 任务说明（`runtimes/agent_task.md`，两轮只差"本轮规则"一节）；✅ P2-3 对照运行（固定流水线 v2 在第一轮快照上跑探索集 13 次，全部完成）；✅ P2-4 第一轮（13 次运行，发现清单经用户确认，[报告](../eval_reports/2026-09-24_p2-4_round1_findings.md)）；✅ P2-5 工具包 v1.1 与 Skill 修订（`process`、`correct`、`ask_image`、`exclude` / `restore`、Q42 输出包与嵌入图片转录、Q43、Q44、段落续接、WMF / EMF；两次小范围迭代检查：[一](../eval_reports/2026-09-24_p2-5_iteration1.md)、[二](../eval_reports/2026-09-24_p2-5_iteration2.md)；图片索引图 C3 与误识表格还原 B3 视第二轮结果再定，跨页正文续接并入 P2-7）；✅ P2-6 第二轮（17 次运行全部有效，注入测试通过；发现的 8 处工具包缺陷与 3 处装置缺陷已修正，[报告](../eval_reports/2026-09-24_p2-6_round2.md)；Q48–Q50 待用户决定）；✅ P2-7 候选通用算法（跨页段落续接、DOCX 无样式标题 Q48、表格算术一致性、可疑字符、单元格中的图片；第三轮在修正涉及的 8 篇上核对）；✅ P2-9 [探索报告](../eval_reports/2026-09-24_p2_agent_exploration.md)（Q14、Q30 已回答）；✅ P2-8 阶段三准备（未见集 6 篇：隔离集 3 篇 + `ground_truth_unseen/` 3 篇新文档，标注经用户审核；验证集 1 篇；§9.4 协议补全）。**阶段二 ✅ 2026-09-24** |
| 3 验收实验 | 用数据决定运行时 | 冻结工具与 Skill；未见过的难例集；Codex 基线、Claude Code 第二基线、Pi（同模型时比较运行时差异）、固定流水线对照；§9.4 协议与 §7.3 卫生（2026-09-24 用户决定：先比较固定流水线与 Codex；Claude Code 暂不用；第三方 Agent 框架（Pi 或其他）另议，其余工作完成后再切换） | §9.4 报告入库；Q13 决定默认运行时 | ✅ 2026-09-25：阶段三运行与[对比报告](../eval_reports/2026-09-24_p3_runtime_comparison.md)；按报告的缺陷清单修正 D1–D6、加本地读数双向比对（Q56）与 Agent 修正通道后[重跑对比](../eval_reports/2026-09-25_p4_runtime_comparison.md)；**Q13 用户决定默认混合方案**；Q54 执行：3 篇新文档并入 `ground_truth/` |
| 4 能力完善 | 按结论补齐处理能力 | 〔Q13〕混合方案作为产品默认运行时（固定流水线 → 有待核对项交 Agent，Agent 运行时可替换）与控制台交互（进度、Agent 动作、结果摘要、回退与中断）；图片路由（含 UNCERTAIN）、两种扫描引擎、复核与语义提取、章节组织（§6.8）、OOXML 边界表、嵌入图片统一子文档路径；退役守卫函数；专项验证低分辨率、密集表格、多栏、旧 OCR 层、矢量文字 | 扫描类、DOCX、标题各项不低于 v1；"有信息图片被丢弃"为 0；模型能提出 v1 漏掉的标题 | ✅ 2026-09-26，[分解](v2_phase4_plan.md)：P4-1 混合运行时 ✅（2026-09-25：`runtimes/hybrid.py`、`AgentRuntime` 接口与 Codex 实现、`runtime.mode` / `runtime.agent`、`pipeline` 默认 v2；Q65）；P4-2 控制台交互 ✅（2026-09-25：`parserx/console/`，中英文、终端原地更新与非终端逐行、`--json`、多文件、中断与接着做；真实 Codex 验证 5 次，卫生审计全部通过，[报告](../eval_reports/2026-09-25_p4-1-2_hybrid_console.md)）；用户试用后修正导出与图片信号（Q66、Q67）；P4-3 标题 ✅（2026-09-25：编号样式分数字类别、编号嵌套按出现顺序、适配器匹配可拆块、`split` 工具修正；固定流水线 heading_f1 0.537、角色 F1 0.663，v1 0.504 / 0.599；adapter:v1 按 Q59 保留到阶段五，[报告](../eval_reports/2026-09-25_p4-3_headings.md)）；P4-4 图片 ✅（装饰形状的图片须确认没有文字或公式才排除；有信息图片丢失 0；v2 冻结 run 重新冻结为 `2026-09-25_p4_v2_toolkit`，[报告](../eval_reports/2026-09-25_p4-4_images.md)）；P4-5 DOCX 🟡（页面层建议推迟 Q69；图表与 SmartArt 不再静默丢失；Q9 按建议实施：脚注尾注、批注、OMML 公式，[报告](../eval_reports/2026-09-25_p4-5_docx.md)）；P4-6 专项与逐篇 ✅（2026-09-26：不确定图的守恒转写、代码块、三线表；固定流水线 char_f1 0.947、表格 F1 0.862、heading_f1 0.564，v1 0.891 / 0.775 / 0.524；仍比 v1 差的 10 项逐篇定位，[报告](../eval_reports/2026-09-26_p4-6_gaps.md)）；Q70 原生页公式（整页识别、逐段守恒、VLM 编辑、Agent 定稿）；P4-7 全语料 ✅（运行 B：混合方案 char_f1 0.947、表格 F1 0.875、heading_f1 0.756、角色 F1 0.868，v1 0.890 / 0.775 / 0.494 / 0.614；21 篇交 Agent、无回退、卫生审计全部通过、Agent 标价 $3.72；有信息图片丢失 0；比 v1 低的 11 项逐条列明，[报告](../eval_reports/2026-09-26_p4-7_full_run_b.md)）。**退出条件全部满足** |
| 5 清理 | 删除死代码与死依赖 | §11.4 执行；§11.5 全部迁移；README 反映真实状态 | 测试全绿；依赖清单与 §10.4 一致 | ✅ 2026-09-26，[退出报告](../eval_reports/2026-09-26_p5-7_cleanup_exit.md)； 分解已确认（[v2_phase5_plan.md](v2_phase5_plan.md)，Q72–Q78）；✅ P5-2 adapter:v1 退役（[报告](../eval_reports/2026-09-26_p5-2_headings.md)：固定流水线标题提高，混合方案低于运行 B，按 Q78 记录）；✅ P5-3 v2 不再引用 v1 的辅助函数；✅ P5-4 第一批：`pipeline: v1` 开关、`--pipeline` 与 6 个只对 v1 有效的参数删除，回归配置合一，`--no-ocr` 不再使处理失败；✅ P5-4 第二、三批：v1 模块（约 11,500 行）与 20 个测试文件删除，v1 配置段删除（旧配置文件照常加载），L0 不再有已知失败；✅ P5-5 依赖与代码一致（§10.4；docling、pypdf、llama-parse、fonttools 删除，pdfplumber、python-docx 移到 `bench`），pymupdf 1.28 按 Q76 推迟；✅ P5-6 README（中英）与 `parse --help`（测试保证每个参数都被读取）；✅ P5-1 §11.5 七条落到 v2 测试（第 4 条撤销；去向检查补"重复块须通向显示的块"、本地读数比对只让显示的图片承接其中文字，[报告](../eval_reports/2026-09-26_p5-1_correctness_tests.md)） |

阶段二与三不等阶段四：对比实验只需要工具包和固定序列存在，越早拿到数据越早止损。

## 13. 开发规范

- 运行与安装只用 `uv`：`uv run python …`、`uv add …`。
- 泛化优先：任何规则或阈值必须说明它对应的视觉或结构信号并在整个语料上验证；禁止文档专属关键词表。新增一条规则前先分类（§2.3）：猜测含义的规则改成交给模型的证据，保证正确性的约束才写成代码。
- 一次 AI 调用一个职责；新增或修改 AI 任务时必须同时写明输入、输出、可改变什么、不确定时的结束状态，并补接受门与评测用例。
- 新增区域类型或路由分支时必须同时补：标签映射、渲染规则、sidecar 字段、Decision、一条评测用例。
- 提示词与 Skill 放在独立文件并带内容哈希；改动等同于改代码，需要跑回归。
- 测试驱动：v2 新模块先写测试再写实现；测试对象是 Block 级合成输入（几行文本、一张生成图片、一个手写区域列表），整篇文档的验证交给 L1/L2。单元测试精简、全部离线；需要网络的标 `live_e2e`。
- 删除 v1 模块前先看 §11.5：实现可以退役，正确性要求不能消失。
- 任何让内容"消失"的分支必须产生 Decision 并被去向检查计数。
- 所有远程调用经过 `ServiceGateway`；并发任务的结果按任务顺序生效；只对传输错误和无法解析的响应重试；已知模型约束写进配置（§8.2）。
- 新代码不得依赖线程完成顺序、字典或集合的遍历顺序、时间戳或临时路径；同一输入必须产生逐字节相同的输出（L1 用 `--repeat 2` 检查）。
- 提交信息说明改动针对的信号和验证范围；阶段完成时更新 §12 与 §15。

## 14. 开放问题

| 编号 | 问题 | 状态 |
|---|---|---|
| Q1 | OCR 引擎途径 | ✅ AI Studio jobs API（PaddleOCR-VL-1.6）；`vlm` 作对照 |
| Q2 | 默认端点 | ✅ 官方端点；中转端点仅试验 |
| Q3 | 默认 VLM | ✅ gpt-6-luna；语义提取用途阶段四复核 |
| Q4 | 是否保留工具对比评测（LlamaParse/LiteParse）及 Node 依赖 | ✅ 暂时保留代码但冻结：随新指标更新，不进 L0/L1/L2，需要时手动跑 |
| Q5 | `scan_engine: vlm` 在密集中文表格上的准确率是否足以作为唯一引擎 | ❓ 阶段四回答 |
| Q6 | 本地检测器在 DOCX 嵌入的低分辨率截图上的召回率 | ❓ 需要人工标注检测框；阶段一只汇报影子运行的检测结果，召回率挪到阶段四回答 |
| Q7 | PDF 分片 | ✅ jobs API 19 页单次返回完整；`_BATCH_MAX_PAGES = 100`，官方页数上限未确认 |
| Q8 | 隔离验证集的组成：按文档来源还是模板；建议先划 3 篇不同类型且不进核心集的文档 | ✅ 按来源：patent01、paper01、text_pic02；同源文档成对留在调参集；DOCX 暂缺，以后补标注 |
| Q9 | OOXML 支持边界各项是支持、降级还是不支持 | ✅ 用户按建议决定（2026-09-26，[边界表](../eval_reports/2026-09-25_p4-5_docx.md) §3）：本阶段支持脚注尾注（Markdown 脚注）与 OMML 公式（转 LaTeX），批注由"失败"改为"排除"；图表（输出为表格）与 SmartArt（节点按层级输出列表）遇到时再做（现为失败块、文字留在 sidecar）；其余维持。修订记录一项已在阶段一决定（Q26） |
| Q10 | 表格复核的触发阈值与预算占比 | ❓ 阶段四回答 |
| Q11 | 结构分析预算：批次大小、文档级 token 上限、冲突复核次数 | ❓ 阶段四前决定 |
| Q12 | 哪些文档类型允许标题走确定性路径跳过模型 | ❓ 阶段四回答；阶段一的 DOCX 走样式与大纲级别的确定性路径（Q24 已定） |
| Q13 | 默认运行时：固定流水线、Codex、Claude Code、Pi，还是"默认流水线、难例交 Agent"；依据 §9.4 报告 | ✅ **用户决定（2026-09-25）：默认用混合方案**——先跑固定流水线，文档摘要有待核对项（`review.open > 0`）或状态不是 complete 就交给 Agent；保留"只用固定流水线"模式（要速度或完全可复现时）。Agent 运行时目前为 Codex，第三方框架（Pi 或其他）另议。阶段三回答：**用户决定（2026-09-24，P2-8）**：暂不用 Claude Code；阶段三的对比先在固定流水线与 Codex 之间进行，采用哪个第三方 Agent 框架（Pi 或其他）作为单独的议题，在其余工作完成后再切换。**修正后重跑（2026-09-25，[报告](../eval_reports/2026-09-25_p4_runtime_comparison.md)）**：固定流水线 char_f1 0.942、表格 F1 0.629、heading_f1 0.337；Codex 0.945 / 0.605 / 0.619，每篇约 $0.26、2.8 分钟；预定分流规则下全语料 31 篇中 22 篇交给 Agent。建议：默认混合方案（待核对项不为 0 交 Agent），保留"只用固定流水线"模式 |
| Q14 | 工具包对外接口：JSON CLI 是否足够，何时加 MCP | ✅ 用户确认（2026-09-24，P2-4）：JSON CLI 够用——第一轮 13 次运行中 Agent 都靠 `--help` 与 `tool schema` 自行学会，没有接口性失败。瓶颈是每次调用重新载入整个工作区（268 页时约 5 s 一次），先用批量操作与 `process` 工具解决；若大文档上单次调用开销仍明显，再加常驻进程（MCP）。**P2-9 补充**：四轮约 50 次运行都经 shell 调用 CLI，任务说明写全请求格式后没有接口性失败；工作区载入开销经 `process` 与批量操作后已不显著。阶段三的运行时同样用 CLI，只有某运行时没有 shell 或调用开销重新成为瓶颈时再加 MCP |
| Q15 | 测试数据与结果是否入 git | ✅ 暂时只有代码与文档入 git；`eval_runs/`、`.parserx_cache/`、新的 `eval_reports/` 不入 git（已跟踪的旧报告保持现状） |
| Q16 | 漏表、多余表的硬检查口径 | ✅ 与冻结基线比较，增加才算失败；第一次冻结只记录；失败文档与未执行文档按绝对数 |
| Q17 | 阶段零是否把图片描述从文本类指标中剔除 | ✅ 剔除；按语义 schema 打分阶段四再做 |
| Q18 | 回归配置是否也关闭 `builders.quality_check` 的 LLM 判断 | ✅ 关闭，回归配置不调用 LLM |
| Q19 | 冻结几份 v1 基线；默认模型 | ✅ 只冻结 VLM gpt-6-luna 一份；以后只用 gpt-6-luna，不再做 gpt-5.4-mini 对照；LLM 默认也改为 gpt-6-luna（`parserx.yaml` 与 `.env` 的 `LLM_MODEL_B`） |
| Q20 | 复核候选由谁、何时采用 | ✅ `recognize` / `review_table` 结束时由程序同步执行选择步骤与接受门，写 Decision |
| Q21 | 是否引入 rapidfuzz | ✅ 引入；edit_distance 改为精确计算（不再分块近似） |
| Q22 | 冻结前是否修 v1 的"纠正后仍重试"缺陷；冻结 run 用什么响应 | ✅ 修：只在响应无法解析时重试；冻结用空缓存、全部真实请求的干净 run，作废的首次冻结改名保留、不作种子 |
| Q23 | 阶段一的验收集是否加入扫描文档 | ✅ 加 ocr_scan_jtg3362：能执行、去向平衡、信息类指标不低于 v1，标题不设门槛；请求数只记录不设门槛。若信息类指标低于 v1，如实报告并分析原因，不为这篇调参（2026-09-24） |
| Q24 | 阶段一的标题来源 | ✅ DOCX 走确定性路径：段落直接设置或经样式继承的 `w:outlineLvl`、Heading 样式；有 Title 样式时 Title→H1、其余下移一级（simple_doc01 的标注如此：Title 段落带直接 `outlineLvl 9` 仍标 H1，heading 1–3 标 H2–H4）；带编号但无标题样式的段落按 LIST。原生 PDF 用临时适配器 `adapter:v1`，经 `apply_structure` 写入，阶段四删除（2026-09-24）。另：real_doc01 有 52 处段落直接设置的 `outlineLvl`，读取器必须两种都读（更正：其中只有 15 处在非空段落上，其余标题是普通样式，见 §15 v1.12） |
| Q25 | P1-7 之后是否做一次工具形态试用 | ✅ 已完成（2026-09-24，[报告](../eval_reports/2026-09-24_p1-7b_tool_trial.md)）：图片确实进入视觉上下文；JSON 过长（识别 27.7 KB、读页 18–20 KB）与三处失败信息不可操作，已修正（识别 1.4 KB、读页 5–8 KB）。做：P1-7 之后，用 Codex（`codex exec`，本机 codex-cli 0.156.0）在 ocr_scan_jtg3362 上试用约 30 分钟，不调优；实验目录只放输入；只检查图片是否进入视觉上下文、JSON 长度、失败信息是否可用（2026-09-24） |
| Q26 | DOCX 修订记录的处理 | ✅ 接受全部修订的最终视图：保留 `w:ins`/`w:moveTo`；`w:del`/`w:moveFrom` 的文字计入账目、去向 excluded、配 `revision_deleted` Decision；被删除的段落标记按最终视图合并段落；格式修订取最终格式；sidecar warnings 注明文档含修订（2026-09-24） |
| Q27 | receipt 的界面元素（订单日期、"管理订阅 ›"等链接、帮助链接）：v1 的内容价值规则删除了它们，标注也省略；v2 按原则保留全部原生内容，receipt 的 char_f1 为 0.972（v1 0.981），超出 0.005 容差 | ✅ 保留全部内容；receipt 的文本类指标（char_f1、编辑距离、关键内容错误）在阶段一只报告、不设门槛，验收报告逐项列出与 v1 的差异；删除界面元素留到阶段四，由结构步骤带 Decision 排除（2026-09-24） |
| Q28 | 表格指标对合并单元格的计法：单元格要求 (rowspan, colspan) 一致，而 GFM 标注无法表达合并（jtg3362 把合并值写在其中一行、其余留空；real_doc01 每行重复）。v2 按 §4.5 用 HTML rowspan 输出正确的合并，被计为错误：jtg3362 表格 F1 0.732（v1 0.764），尽管 v2 逐行数值正确而 v1 表 2 行错位 | ✅ 指标 2.1：标注表格没有任何合并单元格（GFM 全是如此）时，输出单元格与它覆盖范围内任一内容相同的标注单元格配对即算正确，不比较跨度，召回按标注单元格、精确率按不同的输出单元格计；有合并的标注仍按 2.0；文本拼接时与正上方或左侧相同的单元格只写一次。v1 基线离线重算为 `eval_runs/2026-09-23_p0_v1_gpt-6-luna.rescored-2.1.json`（输出逐字节不变，表格分数不变，文本分数小幅变动）；冻结 run 本身不改，回放在指标版本不同时只比较输出（2026-09-24） |
| Q29 | DOCX 自动编号是否写入正文：标注几乎都省略自动编号（simple_doc01 30 处中 29 处），§6.8 说 numbering.xml 用于渲染编号；v2 目前按 Word 显示写入（如"1.1. 智慧支座概述"），只影响 char_f1 精确率，标题匹配允许子串 | ✅ 保留：正文按 Word 显示写入编号，同时记在 `TextStyle.numbering`（2026-09-24） |
| Q30 | 工具形态试用中 Agent 提出、阶段一未做的能力：Agent 直接提交带图像证据的文字 / 单元格候选（仍经接受门）、导出前的 Markdown 预览、公式等价与转义检查、分开报告"处理完成"与"质量复核完成" | 第 1 项 ✅ 用户决定（2026-09-24，P2-4）：主 Agent 本身能看图，读出文字后可以直接提交修改，底层提供修改工具，不再让服务层 VLM 重读一遍；程序照常执行接受门（修改必须引用 Agent 查看过的该块图像、只改声明的片段，写 Decision）。关键在成本：成批的阅读与转录交给服务层 VLM，Agent 不逐页通读，只处理工具列出的待办项并定向抽查。其余三项在 P2-9 回答（2026-09-24）：导出前的 Markdown 预览——不做（没有 Agent 需要，导出可重复、即最终结果）；公式等价与转义检查——留到阶段四（观察到的是公式拆块与私用区字符，属识别与版面问题）；分开报告"处理完成"与"质量复核完成"——已实现（文档摘要的 `review`） |
| Q31 | 阶段一的装饰图判定：(a) §6.5 的廉价过滤是否加入 v1 的"琐碎图"规则（面积 ≤ 12000 px² 且长边 ≤ 160 px）；(b) 阶段一其余路由为影子运行时，装饰图判定是否照 §6.5 生效（保存、不显示、不描述）。依据：receipt 的 5 张图中 3 张是 70×84、48×48、120×34 的图标，§6.5 原规则一张也标不出，固定序列会发 5 次描述请求（v1 为 2 次），超出"真实请求数不超过 v1"；v1 用此规则描述了另外 2 张 | ✅ (a)(b) 都采用：廉价过滤加入 v1 的琐碎图规则，装饰图判定在阶段一生效（保存、不显示、不描述、账目 excluded 并写 Decision），其余路由仍为影子（2026-09-24） |
| Q32 | receipt 的标注含一处标题跳级（"收据" H1 → "账单与付款" H3，与 v1 输出一致）；27 份标注中仅此一处。§6.8 / §3.3 的合法性检查禁止跳级，层级统一会把它定为 H2，receipt heading_f1 为 0.75（v1 1.0） | ✅ 保留禁止跳级的规则；receipt 的 heading_f1 在阶段一只报告、不设门槛（2026-09-24） |
| Q33 | 全量语料参考运行（验收报告"全量语料"一节）暴露的差距是否在阶段二之前处理：(a) 跨页表格合并——接口文档把 `merge_candidate` 列为阶段一 `tables/` 的职责，未实现，pdf_text01_tables 的 67 行表输出为 5 张（表格 F1 0.99→0.25）；(b) 扫描页与图片页的标题层级——OCR 引擎的 `doc_title` / `paragraph_title` 块只成为无层级的 TITLE，`adapter:v1` 只看原生文字层，ocr01、jtg3362 与 3 篇 omnidoc 扫描件 heading_f1 降到 0；(c) 多栏阅读顺序——按位置逐行排序把双栏交错读出（paper_chn01 char_f1 0.691→0.505、paper01 0.955→0.780），原定阶段四 | ✅ 三项都在阶段二之前做（2026-09-24）。(a) ✅ `parserx/tables/merge.py`：候选＝列数相同、相邻两页、中间只有页面装饰；确认＝左右边缘偏差不超过页宽 0.05、第二张表没有不同的自有表头；逐字重复的表头行合并时去掉；`apply_structure` 新增 `merge_tables`（合法性 `not_merge_candidate`、`rows_not_duplicate`），未合并的候选在 unresolved 中列为 `table_merge_candidate`。(b) ✅ `parserx/hierarchy/engine_titles.py`：扫描页引擎的 `doc_title` / `paragraph_title` 标签给出建议层级 1 / 2（映射在 `layout/labels.py`），带点号编号每多一段深一级；与 `adapter:v1`（此后只匹配原生文字块）的标题按阅读顺序一起统一层级；编号模式增加"1 Scope"式（1–3 位数字后接空白）。(c) ✅ `parserx/content/order.py`：递归 XY 切分，先找穿过全区域的竖向空白（少数居中块可作为分节让开），节内先左后右；没有分栏时按横向空白切带、相邻带合起来仍能分栏就合并；两侧须够宽、够高、并排，逐行配对的两侧（表单、无框表格）还须像正文栏。全语料只有三篇双栏论文与 patent01 封面的顺序改变 |
| Q34 | Q33 之后全量语料仍有两类可在阶段二之前处理的差距（其余归阶段四 §6.5、§6.8，或与 Q27 同类）：(a) **原生页没有页眉页脚识别**——§6.9 的跨页重复检测从未实现（阶段一计划也未列入），原生页的页码与页眉进入正文（pdf_text01_tables 每页页码、论文页眉、patent01 每页"权 利 要 求 书 1/2 页"）；跨页续表判断相邻时已临时跳过页边的单独页码与跨页重复短行，但不隐藏它们。(b) **扫描页多栏区域的顺序**——omnidoc_research_report_zh_table_01 / _02 的阅读顺序 τ 1.0→0.746 / 0.905、char_f1 0.989→0.938 / 0.981→0.840，而不计顺序的字符 F1 几乎不变；v2 用引擎给出的区域顺序、未排序区域按纵向位置插入，原因未查明 | ✅ 按建议（2026-09-24）：(a) 在阶段二之前做原生页页眉页脚识别（§6.9 跨页重复检测），判定块 excluded 并写 Decision，跨页续表改用它；(b) 先查明原因，未排序区域插入规则一类的小问题在阶段二之前修，需要重排引擎区域的归阶段四。(a) ✅ `content/furniture.py`：原生层判定通过的页上，页面上下各 10% 区域内的短文字块（单独页码且为本页最外侧块时可到 15%），去掉空白、数字归一后在另一页同一区域相近高度出现，即为页眉 / 页脚 / 页码，excluded 并写 Decision，文字留在 sidecar；跨页续表改为只依据这些标签；全语料只有 paper01、paper_chn01、paper_chn02、patent01、pdf_text01_tables、header_footer_cleanup 有块被判定，都是页码与页眉。(b) ✅ 原因是扫描页引擎未排序区域（表格、图注、图）的插入规则：只按纵向位置插在第一个起点在其下方的区域之前，右栏的表因此插进左栏正文中间。改为：上下相接的未排序区域（图注与其表格）作为一组，放在起点在其上方且横向重叠的区域中序列最靠后者之后（`content/scan.py` 的 `scan_order`）；research_report_zh_table_01 / _02 的 char_f1 0.938→0.985 / 0.840→0.976（v1 0.989 / 0.981），en_table_01 0.953→0.976，其余扫描文档不变 |
| Q35 | 阶段二探索用的运行时与模型 | ✅ Codex CLI 0.156.0，主力模型 **gpt-6-sol**（用户指定：综合能力较好），推理强度 high，每次运行在命令行上显式传入（本机 Codex 默认是 gpt-6-astra，不依赖它）；2026-09-24 实测可用。出现问题（不会用工具、看图链路失效、不稳定、成本过高）再考虑换模型：换模型写入本表并注明原因，一轮之内不混用，换后受影响文档重跑、前后结果不直接比较（[v2_phase2_plan.md](v2_phase2_plan.md) §2.1）。原建议沿用试用的 gpt-6-astra，未采用（2026-09-24）。**推理强度改为 medium**（用户决定，2026-09-24，依据 [P2-5 第二次迭代检查](../eval_reports/2026-09-24_p2-5_iteration2.md)：每步中位 4.9–6.8 s 对 high 的 7.1–10.5 s，质量基本相同）；此后各轮统一使用 medium，一轮之内不混用 |
| Q36 | 阶段二的探索集 | ✅ 有标注的 9 篇（jtg3362、pdf_text01_tables、ocr01、real_doc01、text_report01、paper_chn01、omnidoc_book_zh_text_02、omnidoc_academic_literature_en_text_01、deepseek）加没有标注的 3 篇（JTG 3362 全本、real_doc03、thesis_eng01）；隔离集不用；text_pic01 不加入（2026-09-24） |
| Q37 | 阶段三的未见集与标注 | ✅ 隔离集三篇加 3 篇新文档（补齐密集中文表格、跨页表格、手工排版 Word）；新文档由用户提供或从未使用的样例中挑选，标注由 Claude 起草、用户审核后写入 `ground_truth/`，与探索并行（2026-09-24）。**补充（2026-09-24，P2-4）**：real_doc02 与调参集的 pdf_text01_tables 同源，不宜作未见集；新文档等工具基本成型后再定。**补充（2026-09-24，P2-8，用户）**：新文档先从项目早年收集的测试数据中找；不够时在本机检索典型的 PDF 与 Word 文档，截取部分页作为验证集或测试集；标注仍由 Claude 起草、用户审核后才写入。**完成（2026-09-24）**：三篇新文档与一篇验证集的标注由 Claude 直接从源文件起草，用户对照原文件审核通过（细节问题在测试中发现再改）；写入 `ground_truth_unseen/`（unseen_word_spec01、unseen_pdf_tables01、unseen_scan_form01）与 `ground_truth/val_word_template01`，均不进 git |
| Q38 | 探索中 Agent 的自由度 | ✅ 第一轮允许只读分析脚本，工作区只经工具修改，结果必须经 export；第二轮禁止为单篇文档写转换逻辑；绕过工具的运行作废并记为发现（2026-09-24） |
| Q39 | 每篇预算与人工介入 | ✅ 每篇截止时间：100 页以内 30 分钟，更大 90 分钟；服务预算用默认配置；运行中不提示；只因基础设施问题重跑并记入人工介入（2026-09-24） |
| Q40 | 模型分工：主 Agent 的模型与服务层 LLM / VLM 的模型如何选 | ✅ 用户决定（2026-09-24）：两类模型分开选型、分开更换。主 Agent 的模型能力优先（阶段二为 gpt-6-sol）；服务层的 LLM / VLM 经济、速度优先（gpt-6-luna），只有某类任务确实不够时才按任务升级并记录依据。实验中服务层模型固定，只变主 Agent 的模型（§10.3） |
| Q41 | 本机 Codex 已从 0.156.0 自动升级到 0.156.1，是否接受 | ✅ 用户决定（2026-09-24）：用当前版本或以后的新版本，不固定版本（新版本发布频繁，能力差异不大）；运行记录写实际版本，版本变化不阻止运行 |
| Q42 | 工具的输入输出与图片的呈现 | ✅ 用户确定原则（2026-09-24）：尽量保证信息完整，方便大模型后续使用，人也能方便地得到渲染良好的 Markdown。据此：外部入口 `parserx parse <文档> -o <目录>` 输出一个包（Markdown、`images/`、信息摘要 `<名称>.json`、块级 sidecar，§4.5）；嵌入图片始终保留原图链接，转录的文字 / 表格作为正文紧跟其后并加注释标明来源，整页扫描图只在转录不完整时显示（§6.5）；`images/` 放全部提取出的图片。v2 的 `parse` 入口、信息摘要与嵌入图片的文字 / 表格转录（原属阶段四）提前到阶段二的工具修订 |
| Q43 | "同一编号模式同层级"的作用范围：嵌入文件（附件中另起编号的报告）被误拒（P2-4 D3） | ✅ 用户同意（2026-09-24）：改为同一上级章节内的同一编号模式同层级，附件、附录中另起的编号不与正文比较；仍是合法性检查 |
| Q44 | DOCX 文本框（Q9 的一项）是否提前 | ✅ 用户同意（2026-09-24）：提前到阶段二的工具修订，`w:txbxContent` 的段落作为锚定段落之后的文字块（依据：real_doc03_docx 19 块失败，文档 partial） |
| Q45 | OCR 漏掉的单元格或整列能否由复核补回（数值一致性门拒绝新增数字，P2-4 B2） | ✅ 用户同意（2026-09-24）：允许，只限两个条件同时成立的单元格——在现有结果中为空或缺失，且在 `structure` 问题明确列出的区域内；补回的单元格在 sidecar 中标明"仅有图像证据" |
| Q46 | 标题角色 F1（只比文字、不比级别）是否作为报告的诊断项 | ✅ 用户同意（2026-09-24）：作为报告中的诊断项（§9.3 要求角色与层级分别计分），不进门槛，不改现有指标 |
| Q47 | 主 Agent 看图的方式：自己看图（图片进入主模型上下文），还是经 `ask_image` 让服务层 VLM 读图 | ✅ 用户决定（2026-09-24）：第二轮默认经 `ask_image`——两种方式在迭代检查中质量相同，这种方式允许纯文本的主模型，看图成本落在服务层；另在 2–3 篇文档上保留"自己看图"作对照，第二轮结束后再定默认 |
| Q48 | 无样式标题的处理：手工排版的 Word 与原生 PDF 中没有样式的标题，在"只处理待办清单"的工作方法下不会被 Agent 发现（第二轮 real_doc01 的 heading_f1 0.781 → 0.263） | ✅ 用户同意（2026-09-24）：把 PDF 已在用的临时适配器 `adapter:v1`（Q24）扩展到 DOCX——样式声明的标题优先，v1 排版检测出的其余标题并入同一大纲；原型：real_doc01 0.040 → 0.795、text_report01 0 → 0.714，其余 Word 不变，real_doc01 处理 2.7 s → 10 s（Docling）。v2 自己的标题候选（编号 + 排版证据，其余进待办清单）留到阶段四。（"最浅的标题规范为 H1"已在全量语料上试过并放弃：5 篇标注从 H2 开始的文档由 1.0 降到 0） |
| Q49 | 第三轮的范围 | ✅ 用户同意（2026-09-24）：P2-7 之后在修正涉及的 8 篇上跑一次短的第三轮，并在三篇上再比较两种看图方式 |
| Q50 | DOCX 页面渲染：Word 文件没有"页"（分页是打开时排版算出来的），工具现在只能看 DOCX 里嵌入的图片，`ask_image --page` / `read --image page` 对 DOCX 直接拒绝；是否先经 LibreOffice 排版成 PDF、提供页面图并把段落与表格对应到页面位置 | ✅ 用户同意（2026-09-24）：留到阶段四，与 Q9 一起做。理由：DOCX 的文字与表格直接读自文件、字符准确，看图核对收益小；真正需要渲染的是文件数据里看不出的内容（图表、公式、SmartArt、形状、文本框位置、目录页码）。LibreOffice 的排版与 Word 不完全一致（缺字体会替换，分页会变） |
| Q51 | Q13 的判定方式 | ✅ 用户同意（2026-09-24）：依次看信息类指标、标题、费用与耗时、波动；Agent 或混合方案要成为默认，信息类指标上不能让任何一篇明显变差（超过容差 0.005），并在整体上有改进；费用与耗时给数字由用户权衡 |
| Q52 | 混合方案的分流规则 | ✅ 用户同意：固定流水线的文档摘要有待核对项（`review.open > 0`）或状态不是 complete 就交给 Agent；规则运行前定下，不看结果再改 |
| Q53 | 阶段三的重复次数与缓存 | ✅ 用户同意：每篇每个运行时 2 次，空缓存、真实请求；固定流水线第一次冻结 |
| Q54 | 阶段三之后未见集的去向 | ✅ 用户同意：3 篇新文档并入开发语料，隔离集照旧单独报告；以后验收另找未见文档。用户补充：不必在数据集划分上做得过细，典型文档能正常处理即可，新文档出问题时再打磨 |
| Q55 | 探索集是否一起跑作参照 | ✅ 用户同意：不跑 |
| Q56 | 待核对信号怎样设计才不沦为"见一种错写一条规则"（用户担忧，2026-09-25） | ✅ 用户同意（2026-09-25）：① **信号只指路、不下结论**：只把位置与理由列入待核对项，由 Agent 看图判断；误报的代价是多看一眼，不会改坏输出。② **信号来自正确输出必须满足的性质，而不是见过的错误样子**，限于四类：守恒（页面上看得见的每一行字都有去处；输出的文字页面上看得见）、独立读数一致（同一区域两份独立读数不一致）、文档自身一致（数字相加、编号连续、目录与标题对应）、结构与版面一致（程序给出的结构与页面版面特征相符）。新发现的错误先归到其中一类，修法是扩大这一类的覆盖，而不是加一条模式。③ **先与文档自身比较，少用绝对阈值**；非用不可的数字须是测量容差并登记理由。④ **用全语料衡量信号**：评测器知道输出与标注哪里不一致，信号知道指了哪里，可算"错误被指到的比例"与"指到处确有错误的比例"；新信号或改动须在全语料上提高前者且不明显降低后者才能加入。⑤ **分层检查**（用户：不做每页都经 VLM / OCR / Agent 读一遍）：每页只做便宜的本地检查——本地文字检测与识别（rapidocr，CPU，约 0.6 s/页，无费用；在 unseen_scan_form01 上读出了服务 OCR 漏掉的一行）与输出双向比对；只有不一致处才交给服务 OCR、VLM 或 Agent。用户原则（2026-09-25）：规则的应用要谨慎，特别是带魔法数字的规则。**用户补充（2026-09-25）**：Agent 按需读取（看图、再识别）是它的核心优势，不能因为有了本地读数就排除；工具与门要让有独立证据的修正通过 |
| Q57 | 混合运行时怎样调用 Agent | ✅ 用户基本同意（2026-09-25）：调用本机已登录的 Codex CLI；不可用时只用固定流水线并说明；卫生沿用实验装置；截止时间按 Q39；费用按标价记录 |
| Q58 | 阶段四的范围与顺序 | ✅ 用户基本同意（2026-09-25），并补充控制台交互（P4-2：进度、Agent 动作、结果摘要、回退与中断的提示）；第二种扫描引擎与语义提取扩展暂不做 |
| Q59 | adapter:v1 何时退役 | ✅ 用户基本同意（2026-09-25）：v2 自有标题路径在全语料上不低于 v1 时退役，否则保留到阶段五。**阶段四结论（2026-09-25，[报告](../eval_reports/2026-09-25_p4-3_headings.md)）**：不用 adapter:v1 时固定流水线 heading_f1 0.249（用时 0.537，v1 0.504），PDF 原生页与手工排版 DOCX 的标题都依赖它的排版判断——按本条保留到阶段五；v2 自有的排版标题检测先作 `title_candidate`，全语料验证后才直接采用 |
| Q60 | 控制台的界面语言 | ✅ 用户决定（2026-09-25）：默认中文，`--lang en` 切换为英文；命令、参数、JSON 字段名始终用英文 |
| Q61 | `parserx parse` 的默认流水线 | ✅ 用户决定（2026-09-25）：默认用混合方案（v2）；v1 是否保留、是否只留一条流水线，阶段五再讨论 |
| Q62 | 主 Agent 的运行时：自研薄循环、PydanticAI，还是 Pi（[调研](v2_agent_runtime_research.md)） | ❓ 建议自研薄循环：Python、进程内调用工具、模型请求经 `ServiceGateway`（缓存回放、计数、预算），参考 Pi 与 mini-swe-agent；Codex 保留作探索；OpenAI Agents API（托管的 Codex）与 Claude Agent SDK 不采用 |
| Q63 | 主 Agent 是否需要支持多家模型 | ❓ 建议暂时只用 OpenAI Responses API，模型接口留一层，需要时接 PydanticAI 的模型层 |
| Q64 | 自研循环与缺陷 D1–D6 修复的先后 | ❓ 建议先修 D1、D2（信息错误），再做循环 |
| Q65 | 混合运行时的工作目录、中断与密钥（P4-1 的实现选择） | ✅ 实现选择，用户确认维持（2026-09-25）：① 工作目录固定为 `<输出目录>/.parserx-work/`（工作区、Agent 目录、固定流水线的结果、Agent 运行记录）；运行结束且不需要再处理时删除；Agent 不可用、失败或超时而回退时保留，同一命令再次运行即从标准处理之后接着交给 Agent；工作区被绕过工具改动时删除、下次从头来。② 中断（Ctrl-C）后保留，再次运行时输入与配置指纹相同且工作区完整性通过就接着做，否则从头；工具调用被中断时仍写调用记录（失败码 `interrupted`）认领已提交的修改，`px` 把 SIGTERM 转为中断。③ 评测（`Pipeline`、`regression_test.py`）一律跑固定流水线，`runtime.mode` 只由 `parserx parse` 读取；v1 的回归配置显式写 `pipeline: v1`，冻结时还没有 `pipeline` 字段的 run 按 v1 计指纹。④ 密钥：Agent 的配置由生效配置导出，凭据字段换成 `${PARSERX_SECRET_n}`，值写入 Agent 目录之外的临时文件（0600，运行后删除），只由 `px` 在工具进程内读取；Codex 进程的环境去掉 .env 中的变量名、像密钥的变量名与值等于密钥的变量。⑤ 产品版任务说明与实验共用一份模板（`{{#product}}` / `{{#experiment}}`）：工作区已建好、不需要 `export`、最终报告只写三点 |
| Q66 | 显示出来的图片没有任何内容时怎么办（用户试用电子发票：二维码被判为"主要是文字"，读出的"税"被 Agent 判为图案而去掉，图片既无描述也无文字） | ✅ 用户选 A（2026-09-25）：加守恒类信号 `figure_without_content`——处理曾给这张图内容（尝试过描述，或从图中转写了文字），而输出里一样也没有（描述失败、转写被去掉）；尚未处理的图不列。只指路、可关闭：Agent 看图后补描述，二维码、标志一类写明理由关闭。用户补充：二维码这类不加描述也可以，其他情况再观察。`apply_structure` 同时返回本次修改新打开的待核对项，Agent 改完就能看到。全语料（固定流水线，31 篇，371 张显示的图片）：0 项；电子发票上去掉"税"后列出二维码。不采用"凡显示的图片都描述"（B：文字图会多出与正文重复的描述） |
| Q67 | 全局配置模板 | ✅ 用户同意（2026-09-25）：`parserx init` 的模板改为 `parserx/config/template.yaml`，与项目的 `parserx.yaml` 设置相同（测试保证），只是凭据用通用变量名（`OPENAI_API_KEY` 等）、响应缓存放 `~/.cache/parserx`；`parserx init --force` 替换已有 config.yaml（旧的存为 config.yaml.bak），`.env` 从不替换；是否重新生成由用户自己决定。同时修正 `.env` 的查找：原来 `load_dotenv()` 从模块所在目录向上找（可编辑安装时总是读到仓库的 .env），改为按文档所说读当前目录的 `.env`，再读 `~/.config/parserx/.env` |
| Q68 | 标题指标的两个漏洞：代码块（```）里以 `#` 开头的注释行被算成标题；标题文字不做 NFKC | ✅ 用户按建议决定（2026-09-26）：指标 2.2——代码块内的行不算标题，标题文字比较前 NFKC；冻结 run 离线重算为 `eval_runs/2026-09-23_p0_v1_gpt-6-luna.rescored-2.2.json`、`eval_runs/2026-09-25_p4_v2_toolkit.rescored-2.2.json` |
| Q69 | P4-5 DOCX 的页面层是否本阶段做 | ✅ 用户决定推迟（2026-09-26）：6 篇 DOCX 排版比对没有发现真正的遗漏，改动面广（[报告](../eval_reports/2026-09-25_p4-5_docx.md)）；某篇 DOCX 需要页面级核对时再做。**用户补充（2026-09-26）**：暂不做，除非出现必须按页处理的情况，例如：Word 表格的表头是矢量图而内容是文字；嵌入的艺术图或由大量几何元素拼成的矢量图，需要从整页截图理解；整篇由一页页扫描图片贴成的 Word |
| Q70 | 原生页的行间公式区域是否识别为 LaTeX，用 OCR 还是 VLM | ✅ 用户决定先做（2026-09-26）。引擎由测量决定（`scripts/formula_engines.py`，语料中 24 个与标注整式对应的公式区域，记法归一后与标注的相似度）：扫描引擎 PaddleOCR-VL 90.6、VLM 只看图 86.2、VLM 看图并给出文字层字符 87.5——通用 VLM 会认错字形（z′→ζ′、l→L），给文字层字符只部分弥补；扫描引擎每篇一次批量请求，VLM 每式一次请求，费用都很低。实施（`tools/formulas.py`，`process` 的一步）：版面检测器的 display_formula 区域交扫描引擎，两份读数一致才采用——引擎也认作公式、字母数字相邻对两向 ≥ NEAR、文字层的每个字母数字都在 LaTeX 里（守恒：段落号、式号不能丢）；触及区域的原生块须都在区域内（不拆文字）。全语料：paper_chn01 char 0.751 → 0.757，其余不变；多数公式区域因原生块把公式行与后文连成一块而跳过（paper_chn02 14 个中 1 个可用），按行拆块的做法不稳（块文字按视觉行合并，与行的记账顺序对不齐），留待以后。**第二版（用户意见，2026-09-26，[对比实验](../eval_reports/2026-09-26_q70_formula_experiment.md)）**：抠图没有上下文、行内公式未处理、只差个别字就放弃太苛刻——改为：检测器标出行间或行内公式的原生页整页交扫描引擎（批量），与文字层按位置组成段落，只处理识别结果含数学的段落；识别结果保留文字层的每个字母数字（两份独立读数都看不到的文字层字形视为错码，不计）就采用；否则由 VLM 编辑看图、结合两份读数写出一份，守恒则采用；仍不守恒则保留文字层并列为待核对项 `formula_candidate`，由 Agent 作最终编辑。引擎自己分行返回的同一段按文字层的段落并回。全语料：paper_chn01 char 0.751 → 0.809、heading 0.897 → 0.929；paper_chn02 0.853 → 0.885；patent01 0.958 → 0.931（召回不变，标注行内符号写纯文字、这里写 LaTeX，属标注写法）；Word 公式已是 LaTeX（Q9），扫描页照旧整页识别 |
| Q71 | 文字层有、页面上被其他元素盖住看不到的文字，保留还是排除 | ✅ 用户按建议决定（2026-09-26）：**保留，并注明被遮挡**。实现（84ad1e0）：`close` 对 `text_not_seen` 可加 `occluded: true`（看图为证，同其他关闭），文字留在输出中，摘要 `review.occluded` 列出块、页、原文与理由；转录 Skill 写明遮挡的做法，并统一网页界面文字的做法——按钮、菜单、输入框提示等界面控件整块排除，页面主体保留，一个块混有几个控件时整块排除，不删其中一部分。起因见 [运行 B 报告](../eval_reports/2026-09-26_p4-7_full_run_b.md) §4：deepseek 对话正文最后一行被悬浮输入框盖住，Agent 按"图上看不到"排除；两次运行对界面文字的做法不同。真实验证（快照 p4e）：该行保留并列入 `occluded`，界面控件 7 处整块排除，deepseek 混合方案 char_f1 0.901 → 0.988 |
| Q72 | adapter:v1 的出路（阶段五） | ✅ 用户按建议决定（2026-09-26）：**候选二**，由 v2 自有路径替代——原生块保留逐行排版、排版标题来源（与正文排版比较、按文档自身字号排层级）、DOCX 的直接格式，检测器标签作佐证；不迁移 v1 的规则。达不到现状时走**退路甲**：接受固定流水线的差距，由混合方案弥补，要求混合方案不低于运行 B（[阶段五计划](v2_phase5_plan.md) §2.2） |
| Q73 | "标题不降低"的对照 | ✅ 用户按建议决定（2026-09-26）：固定流水线对现在（带 adapter:v1，0.582 / 0.690，24 篇），混合方案对运行 B（0.756 / 0.868）；平均值不降，逐篇下降超过 0.005 的列明并按 §9.5 定位 |
| Q74 | v1 的去留 | ✅ 用户按建议决定（2026-09-26）：删除 `pipeline: v1` 开关、`--pipeline` 与 v1 的 L1；v1 冻结 run 目录保留，只作静态比较基线（不能再回放）；删除前打本地标签 `v1-final` |
| Q75 | 只对 v1 有效的参数与配置 | ✅ 用户按建议决定（2026-09-26）：删除 `--split-chapters`、`--no-formula`、`--no-table-vlm`、`--no-llm`、`--llm-model`、`--ocr-lang` 与对应配置段（旧配置中多出的键照旧被忽略）；`services.llm` 从模板与 `check_services.py` 去掉；扫描引擎配置仍在 `builders.ocr`，路径不改 |
| Q76 | pymupdf 1.27 → 1.28 | ✅ 用户按建议决定（2026-09-26）：阶段五最后单独一项；全语料离线对比，输出不变或只变好才升级，否则推迟并记录。**结果（2026-09-26）：推迟**。试升 1.28.2：页面渲染 112 页逐像素相同，但文字层分块方式变了——26 个 PDF 中 6 个的原生块不同（paper_chn01 531 → 1515 块、paper01 351 → 421，receipt、patent01、paper_chn02、ocr_scan_jtg3362 的块边界改变），分块是原生提取的基础；另外 1.28 写出的批量 PDF 字节不同，20 篇的扫描引擎请求键变了，无法离线对比，重新请求又会混入 VLM 的波动。能回放的 11 篇输出逐字节相同。升级须连同分块的适配一起在全语料上评估，列入"以后" |
| Q77 | §11.5 第 4 条（页眉页脚第 1 页身份信息保留上限） | ✅ 用户按建议决定（2026-09-26）：撤销。v2 按 §6.9 与 Q34 排除全部页眉页脚（含第 1 页），文字在 sidecar 可查 |
| Q78 | 混合方案的标题低于运行 B（P5-2：0.685 对 0.774），Q72 退路甲的条件未满足，怎么办 | ✅ 用户决定（2026-09-26）：接受，按文档记录差距与原因（[P5-2 报告](../eval_reports/2026-09-26_p5-2_headings.md) §3）；阶段五收尾时在最终代码上再量一次。补充：真值标注本身由解析工具生成，偏离最合适的结果、不如我们的解析时，改标注并记入 [annotation_changes.md](annotation_changes.md)，可灵活处理 |
| Q79 | 很短或没有层次结构的文档，标题怎么计分 | ✅ 用户决定（2026-09-26）：这类文档有没有标题、层级如何都算合理。标题分数照常逐篇显示；标题的平均值与回归检查只计有大纲的文档：标注至少 3 个标题、两级，PDF 多于 2 页；`meta.json` 的 `"outline"` 可覆盖（receipt 标为 false）。实现 `parserx/eval/outline.py`，评测报告、回归门、`phase4_compare.py`、`heading_compare.py` 都按此。按此口径（有 v1 分数的 11 篇）：v1 0.605；固定流水线 0.604（带 adapter:v1）→ 0.672；混合方案运行 B 0.757，阶段五 0.747 |
| Q80 | 依赖版本 | ✅ 用户决定（2026-09-26）：依赖要升级，不固定在旧版本；升级带来的变化要处理。openai 已升 3.x（e6496a1）。PDF：不必只用 PyMuPDF，哪个库更好用哪个；自己做段落拼接须先分析能否处理得足够好（PDF 是排版引擎，纯规则拼接可能失败）——分析与方案待用户确认。**分析与实施（2026-09-26，[分析报告](../eval_reports/2026-09-26_pdf_library_analysis.md)）**：1.28 与 1.27 的行、字体、渲染完全相同，只有分块不同；三个库读出的文字相同，只有 PyMuPDF 具备我们所需的全部能力——继续用 PyMuPDF（许可暂不考虑）。段落改由版面检测器的文字区域分（`content/paragraphs.py`），区域外按几何兜底，区域内只在字号变化处分段；按标注衡量拼接 F1 0.974（库的分块 0.908、纯几何 0.933）。剩余错误多为衡量误差、标注写法与跨栏续接（后者属于续接步骤，另做）。升级到 1.28，1.27 与 1.28 输出逐字节相同 |
| Q81 | Agent 怎样像人一样读文档、认标题 | ✅ 用户决定（2026-09-26）：标题靠先读懂全文——短文档通读，长文档先看全貌再翻看；Agent 用自己的模型读文字，看图才交给视觉工具；不把出问题的章节写进 Skill（会过拟合到测试文档）；文档可能嵌套文档（招标文件附合同等）。Agent 直接读工作区内部文件：方案二，审计记下、不判失败。**实施**：`skim` 工具（前后翻看、一页、查找、一个排版类别、大纲：排版类别表与所有像标题的行）；structure Skill 改写为通用的阅读方法（浏览全文 → 分出组成部分 → 按排版类别推断每部分的标题惯例 → 按类修正、再处理例外 → 每部分定层级）。测量中发现并修正：再次调用 `process` 会静默覆盖 Agent 的结构决定（新合法性规则 `decided_by_agent`）。**结果**（[报告](../eval_reports/2026-09-26_skim_reading_method.md)，交 Agent 的 10 篇有大纲文档，四次运行）：角色 F1 0.680–0.690（p5c 0.664）；heading_f1 与 p5c 持平（不计 patent01 的层级约定差异 0.604–0.625 对 0.602）；费用不增加。待决：只出现在页眉里的部分名、real_doc01 标注、节选的绝对层级 |
| Q82 | 标题层级从几级开始（节选文档、没有题目的文档），怎么计分 | ✅ 用户决定（2026-09-27）：很多时候没有标准答案，逻辑上自洽、输出有结构就好。解析的两个目的：把扫描件与图片里的信息（尤其大文档）提取成文字便于后续处理；尽量保留 Markdown 格式方便人读。**实施**：指标 2.3，heading_f1 允许整篇统一差一级（输出层级整体 −1、0、+1 取最好，报告 `level_offset`；不截断层级，只宽容整体平移）。固定流水线冻结 run 中只有 patent01（0 → 0.80）与 val_word_template01（0 → 1.00）变化。同日：只出现在页眉里的部分名按用户决定排除，patent01 标注相应修订；real_doc01 标注按我们的判断修订（docs/annotation_changes.md）；标注变过的文档回放时比对输出、重新计分 |
| Q83 | 只有一种证据的标题（编号或排版之一），是否加"编号同级"信号 | ✅ 用户授权按我们的判断（2026-09-27）：先量再定。固定流水线漏掉的 47 个标注标题，读全文的 Agent（s2）补回 24、23 个（p5c 10 个）；不计 real_doc01（其标注参考了运行结果）为 12、11 对 9。剩下的 20 个几乎都是画在表格里的整行节名（unseen_scan_form01、unseen_pdf_tables01：表单式文档整页是表格），输出里作为合并的表格行保留，信息没有丢，编号信号也覆盖不到。**决定：不加编号同级信号**；表格里的节名另列待决 |
| Q84 | `process` 的设计：一个工具做完整条流水线，还兼作待办清单 | ✅ 由 Q85 取代（2026-09-27）。事实：阶段二第一轮 Agent 只用细粒度工具，每一步模型时间 3–5 s，大文档读几百次、图片逐张描述，慢且贵（P2-4 报告 §3）；按当时的用户方向加了 `process`（Agent 主控，第一个工具完成标准处理）。阶段四起混合方案在 Agent 开始前就跑完流水线，Agent 再调 `process` 只是为了取待办清单，而其中标题、续表、段落续接三步每次重做，覆盖了 Agent 的决定（已由 `decided_by_agent` 挡住）。建议：产品中 Agent 不再拿到 `process`；待办清单由 `overview` 完整返回；流水线入口改名并只供固定流水线与实验装置使用；其余工具保持细粒度 |
| Q85 | 工具包怎样设计才一致、无歧义、不冗余 | ✅ 用户决定（2026-09-27）：从第一性原理重新设计，采用四个工具 `read_draft`、`view_source`、`edit_draft`、`submit_draft`；看原件与采用分两步；核对与导出合为交稿。设计与讨论见 [v2_toolkit_review.md](v2_toolkit_review.md)。**实施**：证据存入状态（`ir/evidence.py`），编辑检查按证据编号查找；待办有稳定编号；结构操作改名 `move`、`link`/`unlink`、`include`；旧工具删除（overview、skim、read、ask_image、correct、close、review_table、apply_structure、check、export），`process` 改为 `run_pipeline`，不给 Agent；`px` 只放行四个工具；任务说明与三份 Skill 重写；阶段二的探索模式（Agent 自己建工作区、导出）退役。用户同时问清的几点：程序＝流水线、工具、把关、信号，含义判断归 Agent；Agent 自带的读取与分析能力照用，改初稿只经 `edit_draft`；`read_draft` 提供全稿检索（含表格行、图片描述，可用正则）；修改是块上的操作（唯一片段替换、单元格、按位置补入、采用读数），不是 diff 或整段替换。**测量**（[报告](../eval_reports/2026-09-27_four_tools.md)）：交 Agent 的 10 篇质量与 s2 持平（heading_f1 0.654、角色 F1 0.718），工具调用 247 次（s2 364–431），费用持平；发现并修正两处：一次调用中连续的结构操作按结果判定层级（`apply_batch`，被拒的层级操作 32 → 0），页面区域的证据可用于其中的块 |
| Q86 | 工具的契约怎样与 Agent 无关：换 Agent 或自己的循环时只换适配层 | ✅ 用户同意并实施（2026-09-27）：评价 Q85 后发现契约散在三处（几乎没有说明的 schema、手写的任务说明、名字不一致的命令行参数），读写两套词（`H2` 与 `kind=title level=2`、`set_level`；设成页眉仍输出），摆着不起作用的东西（4 种无效关系、永远为空的 `unresolved`、与结果重复的 `diff`、给程序用的导出参数），没有改动记录。设计见 [v2_toolkit_review.md](v2_toolkit_review.md) §11：schema 为唯一来源（中文说明；命令行参数与工具参考由它生成；顶层保持对象，不用联合类型）；一套角色词表 `set_role {role: H1…}`，`join`/`unjoin` 取代 `link continues` 与 `merge_tables`，页眉类只作程序的不输出标签；信封精简，导出从交稿拆出；`read_draft` 加 `changes` 视图；任务说明与适配层分开；最后用最小函数调用循环验证。评价工具看过程指标。**实施**：dd648fe 一套词表；a7686fe 请求模型是契约（说明、生成的命令行参数与工具参考、`agent_task.md` + `adapter_cli.md`、信封去掉 `diff`/`unresolved` 并省去 null 字段、`export` 从交稿拆出）；bb31f1f `changes` 视图；0373525 自己的循环 `runtimes/loop.py`（`runtime.agent.engine: loop`，Responses API，函数定义即请求 schema）。按视图区分的联合类型没有做：主流函数调用接口不接受顶层 oneOf。**测量**（[报告](../eval_reports/2026-09-27_q86_interface.md)，5 篇各一次）：循环 + gpt-6-sol 与 Codex 质量相当（heading_f1 0.678 / 0.646，s3 0.630），Agent 用时 315 s / 459 s，标价 $0.94 / $1.03；循环 + gpt-6-luna 也全部交稿，标题 0.570（固定流水线 0.513），费用 $0.046；Codex 在新接口上没有退步。三个 Agent 犯同样的错（看 Word 页面图、对图片里的表格用 rows），据此修正了说明与拒绝信息（1d0b531） |
| Q87 | Agent 只是校对员，还是能做更多（外部评审与用户讨论，2026-09-27） | ✅ 用户同意并实施（2026-09-27），设计见 [v2_agent_design.md](v2_agent_design.md) §1：从校对员升为能重新解释局部的编辑，不做批量处理的调度，不自由重写正文。四项：① 文档理解记录（`edit_draft` 的 `note`、`read_draft` 的 `notes` 视图，随 sidecar 输出，Agent 不需要读文件）；② 凭证据的例外（规则分正确性约束、文档经验、输出约定三类，经验可凭 `override` + 证据突破）；③ 局部重新解释（区域重读 → `adopt` 替换，内容守恒、旧块保留、`unadopt` 撤回；`unjoin` 能拆开合并的表）；④ 没有待办的文档先测量再决定是否分诊。**实施**：b1bcb46 理解记录；45e7429 凭证据的例外；975eb09 区域重读替换（内容守恒：原生数字不变、九成字符找得到；旧块保留，`unadopt` 撤回）与拆开合并的表；e06ba6e `replace_text` 的 `all`。**测量**（[报告](../eval_reports/2026-09-27_q87_q88.md)）：理解记录几乎每篇都用；`all` 一次修正 34 处重复错字；区域替换尚未在真实文档上用过；没有待办的 15 篇中有大纲的 5 篇标题平均 0.914，分诊暂不做 |
| Q88 | 自己的循环怎样构造上下文、用好缓存、清理，并能换模型 | ✅ 用户同意并实施（2026-09-27），设计见 [v2_agent_design.md](v2_agent_design.md) §2：中立的对话记录 + 每家一个适配器（`responses`、`chat`）；固定前缀 + 只追加的历史；超过阈值时一次性把旧工具结果换成占位，并放回理解记录（不用模型写摘要）；看图两种方式都经 `view_source`（`vision: agent` 把图片放进工具结果，`vision: tool` 经 `as: answer`），默认测量后定；截止前与预算到时先请 Agent 交稿；模型必需可靠的函数调用，看图可选。验证：两种看图方式对比、qwen3.6-plus（DashScope）换模型、real_doc03 长文档。**实施**：f0dd7fa 适配器与上下文；e06ba6e 看图说明按运行方式写、`chat` 的缓存标记；724546b 清理少而成批（一次清理后等上下文再增长半个阈值）。**测量**（[报告](../eval_reports/2026-09-27_q87_q88.md)）：自己看图质量相当、费用约 2.5 倍，默认仍经视觉模型（Q47）；qwen3.6-plus 经两种接口都能完成，标题 0.539（gpt-6-sol 0.678、固定 0.513）；DashScope 只缓存标记过的部分，加标记后命中 96%；87 页文档 4 万阈值下清理 2 次、费用与不清理相当。注：换模型验证用的是 `.env` 中 `_C` 组的 qwen3.6-plus，那是几个月前的旧配置，结果不代表当前的 qwen；用户要求以后不再使用它，换模型或服务商前先问用户（2026-09-27）。**费用**（2026-09-27，用户决定）：循环按 API 计费，账单明显增加；在找到更好的付费方案之前，Agent 一律用 Codex（包月，默认），循环只做离线开发，不做真实调用的测量 |

## 15. 变更记录

| 日期 | 版本 | 内容 |
|---|---|---|
| 2026-09-23 | v0.1–v0.3 | 建立文档：根因、重建决策、依赖探测、核心设计、迁移清单、阶段；luna 实测；OCR 四条途径；VLM/LLM 切官方端点 |
| 2026-09-23 | v0.4–v0.5 | OCR 接入 AI Studio jobs API（另一会话）；依赖全部确定；新会话启动清单；`scripts/check_services.py` |
| 2026-09-23 | v0.6–v0.7 | VLM 定为 gpt-6-luna；三层测试与核心回归集；`llm.py` 适配推理模型并切换 |
| 2026-09-23 | v0.8 | 吸收外部审核：复现五个评测反例；IR 拆五概念；内容去向原则与检查；TableGrid；调度与失败状态；路由"判断不删除"；DOCX 规则；证据层级；正确性要求登记；阶段顺序改为先修验收工具 |
| 2026-09-23 | v0.9 | 根因改为职责与裁决不清；AI 任务边界；两类规则；模型参与的有预算结构分析；任务级重跑；静默错误抽样 |
| 2026-09-23 | v0.10 | 核心交付物改为工作区 + 工具层 + 程序约束；七个工具、三份 Skill；Agent 运行时对比实验 |
| 2026-09-23 | v1.0 | 按全部讨论重新整理全文；运行时改为可替换（Codex → 工具包 → Claude Code → Pi → 必要时自研）；探索与验收分开；实验卫生；阶段改为 0–5；旧稿归档 |
| 2026-09-23 | v1.1 | 确认阶段零分解（[v2_phase0_plan.md](v2_phase0_plan.md)）与阶段一接口（[v2_phase1_interfaces.md](v2_phase1_interfaces.md)）：新增 AssetAnchor、Decision 的 actor/refs、信封 failures 列表与 DocText、确定性 ID、按行/节点记账；决定 Q4、Q8、Q15–Q21；v1 基线只冻结 gpt-6-luna 一份；P0-1 开始 |
| 2026-09-23 | v1.2 | P0-1 完成：指标版本 2.0（TableGrid 表格结构 F1、有序 char_f1、阅读顺序 τ、关键内容错误、硬检查与退出码 0/1/2、服务边界真实请求计数）；五个反例全部捕获；发现原生 PDF 页被送去 OCR、结果全部去重的请求此前漏记（deepseek、pdf_text01_tables、text_table_libreoffice），修正 §9.1 核心集表 |
| 2026-09-23 | v1.3 | LLM 默认模型改为 gpt-6-luna（`parserx.yaml`、`.env` 的 `LLM_MODEL_B`；服务检查与 v1 标题兜底实测通过）；P0-2 完成：`configs/regression.yaml` 继承 `parserx.yaml`，关闭质量检查与全部 LLM 兜底，回归脚本默认使用；报告与结果记录带配置指纹；核心集实测 LLM 请求 0、硬检查通过；发现 `header_footer.llm_fallback` 在 v1 中未被读取。消除浪费的请求：vlm/llm 配置 `send_temperature: false`（只用 gpt-6-luna，不再靠 400 自动探测）；v1 页面复审的 json_schema 不符合 strict 要求、每次被拒后以 json_object 重发，改为直接请求 json_object（`processors.vlm_review.structured_output_mode`，输出行为不变）；ocr_scan_jtg3362 由 12 请求 16 次尝试降为 12/12 |
| 2026-09-23 | v1.4 | P0-3 完成：所有远程请求经 `ServiceGateway`（查缓存、计数、请求、写回），LLM/VLM 在公开方法、OCR 在唯一传输出口缓存原始 JSON；键覆盖完整请求语义、不含密钥与临时路径；模式 off / read_write / read_only / refresh，离线未命中记为未执行；批量 OCR 临时 PDF 改 `no_new_id` 使字节稳定；核心集回放 0 请求、6 篇输出逐字节一致，耗时 172.9 s → 2.6 s |
| 2026-09-23 | v1.5 | P0-4 完成：`scripts/regression_test.py --core` 默认离线回放（缓存未命中记为未执行并提示 `--allow-calls`），`--list`、`--repeat N`（输出不一致为硬检查失败）、`--gt-dir` 可重复；逻辑在 `parserx/eval/suite.py`；`parserx eval --cache-mode`。离线回放发现 v1 图片处理的竞态：并发 VLM 任务中，一张图的纠正结果会抑制共享的 OCR 文本，另一张图收集重叠证据时跳过它，提示词随线程完成顺序变化（真实调用时同样不确定）；修正为证据忽略其他图片的 VLM 抑制标记，登记到 §11.5。核心集两个进程各回放两次，输出逐字节一致、0 请求 |
| 2026-09-23 | v1.6 | P0-5 进行中：冻结与回放工具、隔离集清单（c154ccd）。首次冻结（27 篇，566.6 s）的离线回放中 ocr_scan_jtg3362 输出不同：v1 图片处理对 VLM 响应的解释会修改共享页面状态，结果随线程完成顺序变化（随机延迟探测 6 次出现两种输出，其中一种丢失封面三行）。修正为 VLM 调用并发、响应严格按任务顺序解释（`_run_vlm_concurrent` 轮次机制，加测试）；首次冻结 run 作废待重冻。另发现：VLM 返回纠正而无独立描述时 v1 会重试（全语料 103 次请求中 14 次），重试时因自身已抑制 OCR 文本而把本图判为已覆盖并跳过，内容丢失——已决定冻结前修复（Q22） |
| 2026-09-23 | v1.7 | 修 v1 图片 VLM 重试：只在响应无法解析（ok=False）时重试；解析成功但无独立描述（仅纠正）时不再重试，消除全语料约 14% 的重复请求与"自身抑制后判为已覆盖"的内容丢失（jtg3362 封面三行恢复，随机延迟下输出稳定）；加测试；决定 Q22 |
| 2026-09-23 | v1.8 | P0-5 完成、**阶段零完成**：用空缓存、全部真实请求重新冻结 v1 基线 `eval_runs/2026-09-23_p0_v1_gpt-6-luna`（27 篇，提交 78556f4，工作区干净，OCR 26、VLM 89、LLM 0 次请求，197.6 s），两次独立离线回放输出与分数逐项一致；首次冻结作废改名 `.invalid`；基线报告按调参集 / 隔离集 / 公开集分列；§0.1、§0.2、§9.1 核心集表按冻结基线更新 |
| 2026-09-24 | v1.9 | 阶段零合并到 main。阶段一分解 [v2_phase1_plan.md](v2_phase1_plan.md) 与设计修订：DOCX 直接读 OOXML（Docling 给不出节点路径、无法记账，并会丢掉修订插入的文字）；Observation 增加类型化 `TextStyle`；修订删除的文字计入账目；阶段一版面检测只做影子运行，原生 PDF 按 PyMuPDF 文本块组织；新增 `render/`、`prompts/`、`hierarchy/legality.py`、临时 `runtimes/v1_structure.py`；语义块格式与评测对齐；§8.2 与 §13 加入阶段零得出的调度和确定性规则；§9.1 更正 receipt 为原生 PDF；§12 阶段一的产出与退出条件细化；新增 Q23–Q26，Q6 挪到阶段四；§0 改为阶段一的启动清单 |
| 2026-09-24 | v1.10 | 阶段一开始：启动检查全部通过（服务三项 OK；L0 493 通过 + 4 个已知失败；v1 L1 PASS；基线回放 PASS）。Q23–Q26 按建议确认（§14），Q24 补充 DOCX 标题约定（Title→H1、其余下移一级；段落直接 `outlineLvl` 与样式继承都读）。**P1-1 完成**：`parserx/ir/` 补齐枚举、Observation（含 `TextStyle`/`Numbering`）、Relation、Asset、Decision、Block（结构校验器）、图片语义、工作区状态与 sidecar（`LedgerEntry.unit`、`Stats`/`TokenUsage`、`schema_version`、`Sidecar` = 状态 + 账目汇总）、确定性 ID、sidecar JSON Schema（序列化模式全字段必填，`jsonschema` 显式加入依赖）；`TableGrid.to_gfm/to_html/needs_html`（多行表头也走 HTML，避免丢结构）。L0 522 通过 + 4 个已知失败 |
| 2026-09-24 | v1.10 | **P1-2 完成**：`parserx/workspace/`：`Workspace.create/open/load`、带文件锁的 `txn(actor, expect_version)`（提交前整体重新校验，失败不写，版本号加一）、`calls.jsonl`（提交与工具调用）、内容寻址资源、输入副本 `source.<ext>`；`queries.py`（阅读顺序、邻近块、按页 / 段取块、标题树）；`DocxAnchor.segment`（DOCX 块所在的分页段）。L0 534 通过 + 4 个已知失败 |
| 2026-09-24 | v1.10 | **P1-3 完成**：调度层——文档级预算（截止时间、各服务请求数、费用；请求前预留、完成后结算；超限 `skipped_budget`）；token 用量经 `usage_hook` 按请求归属，按 `scheduling.prices`（§10.3 价格）计费，费用列不再是空；`run_ordered`（并发请求、按任务顺序生效）；可重试错误分类（网络、5xx、429、`TransientError`），传输重试从 OCR 客户端与 OpenAI SDK 移到网关（SDK `max_retries=0`，删 `ServiceConfig.max_retries`），无法解析的响应以新请求重发且可回放；OCR job_id 持久化恢复、返回页数校验。v1 受影响处及原因：v1 pipeline 改用同一个按文档的网关（预算默认无限，行为不变）；页数不符由静默错页改为失败（明确缺陷）。评测工具：配置指纹不再包含重试与价格（不改变回放输出），冻结 run 的指纹按今天的函数从 manifest 重算，新增配置字段不会让旧冻结 run 无法回放。`check_services.py` 也走网关并要求报告 token 用量（实测 LLM 14/5、VLM 183/16 token）。L0 568 通过 + 4 个已知失败；v1 L1 PASS；v1 基线回放 PASS |
| 2026-09-24 | v1.10 | **P1-4 完成**：`parserx/content/`——原生 PDF（行级账目、按位置排序、规则表格、图片资源；原生层质量判定加入不可见文字信号：语料中只有 ocr_scan_jtg3362 含不可见文字，其封面切成 7 张图，v1 主图判据漏判；判定失败页的全部图片为 SCAN）、扫描页引擎（与 v1 相同的子 PDF，命中既有缓存；无序区域按位置插入；页眉页脚页码 excluded；图片从整页渲染图裁剪）、DOCX 直接读 OOXML（修订最终视图、删除文字成为 excluded 块、段落标记删除合并段落、域代码结果、gridSpan/vMerge、分页分节、样式/大纲/编号证据、列表编号渲染、页眉页脚 excluded、文本框/脚注/批注为 failed 并保留文字）、选择步骤与接受门（原生数字不得改；OCR 数字只在被要求核查的单元格改；结构不得丢内容）；`layout/labels.py`（PaddleOCR 25 类标签）。探测（未经渲染与结构步骤的粗略拼接）：simple_doc01 char_f1 0.897（v1 0.410），text_report01 0.981（v1 0.971）；jtg3362 用冻结基线缓存 0 请求得 char_f1 0.968（v1 0.896）、编辑距离 0.061（v1 0.189），但表格单元格 F1 0.732（v1 0.764）；receipt 按位置排序后 char_f1 0.972（v1 0.981）。后两项的原因与待决问题见 §14 Q27–Q28。L0 613 通过 + 4 个已知失败；v1 L1 PASS |
| 2026-09-24 | v1.10 | Q27–Q29 按建议确认（§14）。**指标 2.1**（Q28）：标注表格没有合并单元格时，输出的合并单元格按覆盖范围配对、不比较跨度；文本拼接中与正上方或左侧相同的单元格只写一次。回放在指标版本不同时只比较输出并重算分数（`--json-out` 保存为新基线）；v1 冻结基线离线重算为 `.rescored-2.1.json`，输出不变、表格分数不变、文本分数小幅变动（11 篇有变化，最大 paper_chn02 char_f1 0.723→0.715、pdf_text01_tables 0.974→0.969；两侧同样折叠重复单元格所致）。jtg3362 的 v2 探测表格 F1 在 2.1 下为 0.839（v1 0.764）。L0 619 通过 + 4 个已知失败；v1 L1 PASS；v1 基线回放 PASS |
| 2026-09-24 | v1.10 | **P1-5 完成**：`parserx/accounting/`——账目恒等式与未归属条目；去向与块状态一致性（标为 output 却指向不渲染的块即静默丢失，另列 `mismatched`）；非法引用（重复 id、账目/关系/资源/Decision.refs/渲染图/图片记录/缺失项）；资源文件存在性；文档状态（有 pending 页为 in_progress，没有任何内容进入输出为 failed，有已知缺失为 partial）。真实文档初始提取全部平衡：text_table01、receipt、deepseek、text_report01、real_doc01 complete，simple_doc01 partial（2 条批注不支持），jtg3362 整合扫描结果后 complete（55 输出 / 30 合并 / 138 重复 / 6 排除）。L0 629 通过 + 4 个已知失败；v1 L1 PASS |
| 2026-09-24 | v1.10 | **P1-6 完成**：`parserx/render/`——§4.5 Markdown 契约（ATX 标题、级别待定的标题不加标记、段落换行按 pandoc east_asian_line_breaks 合并、GFM/HTML 表格、图片行 + `> [图片语义]` 引用块带证据层级、PDF 每页 `PAGE n`、DOCX `PAGE-BREAK`/`SECTION k`、隐藏与失败块不渲染、段首 `#`/`>` 转义）；sidecar 导出（状态 + 账目汇总，通过 schema，逐字节稳定）；`write_export` 只复制渲染出的图片。真实文档直接渲染（尚无结构步骤）：text_table01 char_f1 0.998、receipt 0.972、simple_doc01 0.897、text_report01 0.981，评测的规范化步骤正确剔除语义块并识别表格。L0 640 通过 + 4 个已知失败；v1 L1 PASS |
| 2026-09-24 | v1.10 | **P1-7 完成**：`parserx/tools/`——统一信封（DocText、Cost 含文档级剩余预算、failures 列表、diff、unresolved；新增 `internal_error`）；七个工具与 `workspace init`（.doc 经 LibreOffice 转换到临时目录，不再写到输入旁边）；`parserx tool <name> --json` / `tool schema` / `workspace init` CLI，stdout 只有信封；`hierarchy/legality.py`（七条合法性规则，编号模式签名如 `N.N`、`第N章`）；`prompts/`（描述与表格复核，内容哈希计入缓存键）；预算与统计经 `state.stats` 跨调用持续；`read` 不改变版本。契约测试覆盖信封 schema、文档文字只在 DocText 中、九种失败码、版本冲突、合法性规则、CLI。CLI 冒烟：jtg3362 init → recognize（命中缓存 0 请求）→ check（平衡、complete）→ export 通过；recognize 信封 36 KB（4 页 61 条观察，视图截至 50 条），留给 Q25 试用评估。L0 665 通过 + 4 个已知失败；v1 L1 PASS |
| 2026-09-24 | v1.10 | **P1-8 完成**：`parserx/skills/` 三份草稿（忠实转录与纠错、图片理解与描述、文档结构与章节组织），各写明目标、取证方法（对应到七个工具的用法）、输出要求、停止条件，不写阈值规则；文件头注明"草稿，阶段二修订"；`load_skill(name) -> SkillText(name, text, sha256)`。L0 670 通过 + 4 个已知失败 |
| 2026-09-24 | v1.10 | **P1-7b（Q25）工具形态试用完成**：Codex CLI（gpt-6-astra，workspace-write 沙箱）在 ocr_scan_jtg3362 上 2 分 53 秒完成、无人工介入；图片确实进入视觉上下文（报告了水印、跨三行的 390 等只有看图才知道的细节）；自行发现并使用 review_table 与 apply_structure。问题与处置：读页与识别返回过长 → `read` 默认只给采用内容、坐标按需，`recognize` 默认不给观察视图（27.7 KB→1.4 KB，读页 18–20 KB→5–8 KB）；VLM 在 JSON 后附加内容导致解析失败 → 取第一个完整 JSON，失败信息说明缓存重放与下一步；端点内容策略误拦 → 失败信息注明；复核未采用而无未解决项 → 写 `table_uncertain`。其余需求记为 Q30。报告 `eval_reports/2026-09-24_p1-7b_tool_trial.md` |
| 2026-09-24 | v1.10 | **P1-9 完成**：版面检测（rapid-layout pp_doc_layoutv3，派生缓存，离线回放不加载模型）、三套标签映射（检测器与 PaddleOCR 同为 25 类；DOCX 元素）、面积统计 t/f、§6.5 路由表与廉价过滤、`recognize --engine layout` 影子运行（页面检测挂到重叠块、图片路由写 ImageRecord 与 image_route Decision，只有装饰图生效）。真实运行：receipt 5 张图中 3 张图标判为装饰（琐碎图规则），另 2 张 UNCERTAIN（检测器在应用图标上没有区域），3 页 26 个区域全部挂上块，1.7 s；jtg3362 4 页 61 个区域全部挂上块（页眉页码也可挂）。新增 Q31（装饰图判定）。L0 693 通过 + 4 个已知失败；v1 L1 PASS |
| 2026-09-24 | v1.10 | **P1-10 完成**：固定序列运行时 `parserx/runtimes/pipeline.py`（七步全部经工具函数，一篇文档共用一个上下文与预算）；`pipeline: v1 \| v2` 开关与 `configs/regression_v2.yaml`；DOCX 确定性结构（大纲级别、heading/标题 N、Title→H1 其余下移、编号段落为 list）；文档级层级统一（同编号模式同级、不跳级）；PDF 临时适配器 `adapter:v1`；冻结 run 另存 sidecar。原型：text_table01、receipt 的 v1 标题全部精确映射到 v2 块。发现：27 份标注中只有 receipt 含跳级（H1→H3，与 v1 输出一致），合法性检查与层级统一会把它变成 H2，receipt 的 heading_f1 将为 0.75（v1 1.0），待用户决定（Q32）。L0 699 通过 + 4 个已知失败；v1 L1 PASS |
| 2026-09-24 | v1.11 | **P1-11 完成、阶段一完成**：v2 的 L1 先以真实请求录制、再离线回放两遍一致（6 篇，约 2.6 s）；在 text_table01、receipt、simple_doc01、ocr_scan_jtg3362 上以空缓存、全部真实请求冻结 `eval_runs/2026-09-24_p1_v2_toolkit`（提交 65041be，O/V/L 1/2/0，`--replay` 通过）。与 v1（指标 2.1 重算）比较：全部门槛项达标；simple_doc01 char_f1 0.410→0.897、heading_f1 0.295→0.984；jtg3362 char_f1 0.896→0.967、表格 F1 0.764→0.839、VLM 请求 11→0；receipt 文本类与标题只报告（Q27、Q32）。比较中发现扫描页引擎的 HTML 表格没有表头（jtg3362 表头关联 0.733→0.067），修正 `to_html` 与 `to_gfm` 一致后以空缓存重新冻结（0.711），首次冻结改名 `.superseded`。验收集之外：deepseek 的差距来自界面元素（与 Q27 同类）与 `find_tables` 误把按钮栏认成表；text_report01 的标题是无样式的加粗段落（阶段四）。§0 改为阶段二的启动清单 |
| 2026-09-24 | v1.12 | **全量语料参考运行**（27 篇，开发缓存，非冻结、非退出条件）：第一次运行发现图片承载内容的页面（无原生文字、图片占 34–48%）被判为原生页、内容只以图片显示（ocr01 char_f1 0.503），恢复 v1 的"混合页"信号为 `image_content` 判定（b52cb76，文字少于 200 字且图片覆盖超过 30% 时送扫描页引擎），ocr01 升到 0.938（v1 0.742），验收文档输出不变、v2 冻结 run 回放通过。修正后 char_f1 变差 15 篇、变好 7 篇，硬检查未通过；原因分八类写入验收报告。阶段一遗漏两项：跨页表格合并候选未实现、扫描页标题没有层级；已知限制：多栏阅读顺序；其余归阶段四或 Q27 同类。更正 Q24 的备注：real_doc01 的 52 处段落 `outlineLvl` 只有 15 处在非空段落上。新增 Q33。L0 703 通过 + 4 个已知失败；v1 与 v2 的 L1 PASS |
| 2026-09-24 | v1.12 | **Q33 (a) 跨页续表完成**：候选（列数相同、b 在 a 最后一页的下一页、两者之间只有页面装饰）、确认（左右边缘偏差不超过页宽 0.05；第二张表没有不同的自有表头）、合并（追加行、保留两者锚点、第二张 `merged`、`continues` 关系、账目 merged），`apply_structure` 新增 `merge_tables` 与两条合法性规则，固定序列在标题之前合并已确认候选，其余进 unresolved。原生页还没有页眉页脚识别（§6.9 的跨页重复检测未实现），判断相邻时把页边区域内的单独页码与跨页重复的短行当作装饰跳过（不隐藏）。全量语料（开发缓存）：pdf_text01_tables 5 张合并为 1 张 67 行；ocr01 7 张合并为 3 张（第 3→4 页右边缘偏差 0.076，留为候选）；text_pic02 两张 `find_tables` 误识表合并；其余文档没有候选。L0 710 通过 + 4 个已知失败；v1 与 v2 的 L1 PASS |
| 2026-09-24 | v1.12 | **Q33 (b) 扫描页标题层级完成**：扫描页引擎的标题标签作为层级证据（`doc_title` 1、`paragraph_title` 2，带点号编号每多一段深一级），与 `adapter:v1` 的原生文字标题按阅读顺序一起统一层级，分两次 `apply_structure` 写入（actor 各自保留），只因依赖另一来源而被判跳级的层级在两者都写入后再发一次；`adapter:v1` 改为只匹配原生文字块（其证据是文字层字号，与扫描块无关）；`numbering_signature` 增加"1 Scope"式编号（1–3 位数字后接空白，年份不算），同一文档中与"1."式同属 `N`。开发缓存上：ocr01、jtg3362、omnidoc 扫描件都有了标题层级（此前全部渲染为正文）；引擎的标签本身有不一致（ocr01 把"2 哪些人……"标为 doc_title），按编号模式统一后同级。L0 711 通过 + 4 个已知失败；v1 与 v2 的 L1 PASS |
| 2026-09-24 | v1.12 | **Q33 (c) 多栏阅读顺序完成，Q33 全部完成**：`content/order.py` 改为递归 XY 切分——区域内找栏宽块（区域宽 5%–60%）都不穿过的竖向空白（至少区域宽 1.5%）；没有干净空白时，中间 20%–80% 范围内覆盖最少位置上的块若总高不超过栏宽块总高的 20%（居中标题、公式、图中标注）就让开作分节；穿过空白的块把区域切成节，节内左侧先于右侧，各侧递归；分栏须两侧各占区域宽 20% 以上、覆盖两侧总高的 25% 以上、纵向重叠至少一半；两侧逐行配对超过一半时（表单、无框表格，或同一行距的两栏正文）还须每侧至少 3 块、两侧宽度比不低于 0.6、每侧 60% 以上的块至少占本侧一半宽。没有分栏时按横向空白切带，相邻带合起来仍能分栏就合并（避免两栏恰好同时出现的空白把栏切碎），最后按行排序；块内的行仍按行排序。全语料 27 篇只有 paper01（15/19 页）、paper_chn01、paper_chn02 与 patent01 封面的顺序改变（开发缓存）：char_f1 paper01 0.780→0.952（v1 0.955）、paper_chn01 0.505→0.749（v1 0.691）、paper_chn02 0.687→0.858（v1 0.715）、patent01 0.818→0.835，order_tau 都升到 0.998 以上。L0 716 通过 + 4 个已知失败；v1 与 v2 的 L1 PASS |
| 2026-09-24 | v1.12 | **Q33 完成后的冻结与全量参考运行**：v2 验收冻结以空缓存、全部真实请求重新冻结（提交 e14752f，O/V/L 1/2/0 不变，`--replay` 通过；65041be 的冻结改名 `.pre-q33`）；四篇中只有 jtg3362 的输出改变（扫描页标题有了层级，heading_f1 0→0.25，v1 0.286，按 Q23 只报告），其余指标与去向不变。v1 基线回放 PASS。全量语料（开发缓存）与 v1 比：char_f1 变差 11 篇、变好 9 篇、持平 7 篇（Q33 前 15 / 7 / 5），平均 0.932（v1 0.893）；heading_f1 变差 9、变好 6（此前 11 / 4）；漏表或多表多于 v1 的文档 4 篇（此前 5）。剩余差距写入验收报告；更正报告中 patent01 的原因（"权 利 要 求 书 1/2 页"是原生块，`adapter:v1` 按整块匹配未命中，不是被当作页眉排除）。新增 Q34 |
| 2026-09-24 | v1.12 | **Q34 (b) 完成**：扫描页多栏区域顺序的原因是未排序区域（`block_order=None`：表格、图注、图）只按纵向位置插在第一个起点在其下方的区域之前，右栏的表因此插进左栏正文中间（引擎给出的有序区域本身正确）。改为按栏放置：上下相接的未排序区域成组（间隔小于较矮者高度的一半、横向重叠、中间没有已排序区域），组放在起点在其上方且横向重叠的区域中序列最靠后者之后；首版只取最近的上方区域，使 en_table_02 的窄图注带着通栏表格进了左栏（0.963→0.717），成组后恢复。13 篇含扫描页的文档中只有三篇改变且都变好：research_report_zh_table_01 0.938→0.985、_02 0.840→0.976、en_table_01 0.953→0.976；验收文档输出不变（v2 冻结回放 PASS）。L0 718 通过 + 4 个已知失败；v1 与 v2 的 L1 PASS |
| 2026-09-24 | v1.12 | **Q34 (a) 完成、Q34 完成**：`content/furniture.py` 在原生层判定通过的页上做跨页重复检测（§6.9）：页面上下各 10% 区域、高度不超过页高 4% 的文字块，形状（去空白、数字串与单独罗马数字记为 `#`）在另一页同一区域相近高度（页高 3% 内）出现即为页眉 / 页脚 / 页码（无字母的形状为页码），excluded、写 `exclude` Decision、账目 excluded、文字留在 sidecar。横向页面的页码在页高 88% 处，因此单独页码且为本页最外侧块时区域放宽到 15%；首版把 15% 用于所有形状，合成文档中每页首行正文与 patent01 的段落号"[0012]"被误判，改为只放宽页码。跨页续表去掉临时的页边启发式，只依据装饰标签。全语料被判定的都是页码与页眉（paper01 19 块、paper_chn01 5、paper_chn02 7、patent01 48、pdf_text01_tables 5、header_footer_cleanup 4）；patent01 char_f1 0.835→0.847；header_footer_cleanup 0.906→0.896（标注在第 1 页保留一次页眉，v1 有"首页保留"规则，v2 按 §6.9 全部排除）。验收文档输出不变（v2 冻结回放 PASS）。全量语料最终与 v1 比：char_f1 变差 9、变好 9、持平 9，平均 0.940（v1 0.893）；剩余差距是扫描页识别差异（v1 的页面级 VLM 复审）、图片中的文字、无样式标题、界面元素、代码块。L0 720 通过 + 4 个已知失败；v1 与 v2 的 L1 PASS |
| 2026-09-24 | v1.13 | **阶段二开始：分解草稿**。启动检查全部通过（服务三项 OK；L0 720 通过 + 4 个已知失败；v1 与 v2 的 L1 PASS；两个冻结 run 回放 PASS）。写出 [v2_phase2_plan.md](v2_phase2_plan.md)：进入阶段二时的事实（Codex 沙箱不限制读，卫生靠工具快照隔离与事后审计；工作区需要能发现绕过工具的改写；主 Agent 用量由实验装置从事件流记账）、探索设计（每轮工具快照、每篇独立实验目录、两轮：探索与接近验收条件、对照为固定流水线 v2 与 v1）、探索集与阶段三未见集、P2-1 至 P2-9、退出条件。新增待决问题 Q35–Q39 |
| 2026-09-24 | v1.13 | **阶段二分解确认**：Q35–Q39 全部按建议确认（Codex 与 P1-7b 同一模型；探索集 12 篇，text_pic01 不加入；阶段三未见集为隔离集加 3 篇新文档，标注由 Claude 起草、用户审核；两轮的自由度与每篇预算按草稿）。下一项 P2-1 实验装置 |
| 2026-09-24 | v1.13 | **Q35 改定**：阶段二的 Agent 运行时为 Codex CLI 0.156.0，主力模型由用户指定为 gpt-6-sol（综合能力较好；原建议 gpt-6-astra 未采用），推理强度 high，每次运行在命令行上显式指定（本机 Codex 默认仍是 gpt-6-astra）；无头调用实测可用。出现问题再考虑换模型，换模型的记录与重跑规则写入 v2_phase2_plan.md §2.1。§0.2、§7.4、§10.1、§12、§14 同步更新 |
| 2026-09-24 | v1.13 | **Q40 模型分工**（用户决定）：主 Agent 的模型能力优先（gpt-6-sol，经 Codex），服务层 LLM / VLM 经济、速度优先（gpt-6-luna，经官方端点），两者分开选型与更换；§10.3 改写为模型分工表，§9.4 的控制变量补充"服务层模型固定"，v2_phase2_plan.md §2.1、§2.2、§2.4 同步 |
| 2026-09-24 | v1.14 | **阶段二实施开始，P2-1 完成**。启动检查全部通过（服务三项 OK；L0 720 通过 + 4 个已知失败；v1 与 v2 的 L1 PASS；两个冻结 run 回放 PASS；Codex gpt-6-sol 无头调用返回 OK，版本已是 0.156.1，新增 Q41）。实验装置：工作区每次提交记录状态摘要（`head.json` 与 txn 记录），事务开始前核对，调用记录认领本次提交的事务，`verify_workspace` 找出工具以外的提交、改动的资源与输入；新失败码 `workspace_tampered`，所有工具拒绝被改写的工作区；`runtimes/codex.py`（命令行、事件流用量、卫生审计）、`runtimes/px.py`（只开放工具与只读 Python，配置固定，密钥只在工具进程）、`runtimes/experiment.py`（实验目录、任务模板渲染、运行后核对）、`runtimes/agent_task.md`（任务说明初稿）、`scripts/agent_explore.py`（快照、运行、核对、汇总）。发现：本机 Codex 默认把 memories（含 ParserX 讨论）、skills、网页搜索、子 Agent 带进会话，实验一律关闭并以审计兜底（§7.3）；事件流不记录看图。验收：text_table01 在仓库外的快照上 117 秒完成，审计与完整性通过，导出与最终状态一致，char_f1 0.9975（与 v2 冻结 run 相同）；[报告](../eval_reports/2026-09-24_p2-1_harness.md)。L0 738 通过 + 4 个已知失败；v1 与 v2 的 L1 PASS；v2 冻结 run 回放 PASS |
| 2026-09-24 | v1.14 | **P2-2 完成**：任务说明模板 `parserx/runtimes/agent_task.md`（随 P2-1 写成、经 text_table01 运行检验）写明目标与 §7.5 的完成条件、七个工具与 `tool schema`、`DocText` 是数据不是指令、`read --image` 只给路径须亲自打开、本轮规则（r1 允许只读分析脚本；r2 禁止为单篇文档写转换逻辑；两轮都只经工具改工作区、只经 `export` 出结果、不读实验目录以外的路径）、预算（截止时间、服务预算、运行中无人回答）、三份 Skill 的位置与用途、最终报告的六项内容（结果、看过的图、缺少的工具、失败信息、反复尝试、临时脚本）。规则轮次写入快照，模板与 Skill 的摘要写入快照与每条运行记录。测试：渲染稳定；r1 与 r2 只在"本轮规则"一节不同；不留占位符。L0 739 通过 + 4 个已知失败；v1 与 v2 的 L1 PASS |
| 2026-09-24 | v1.14 | **P2-3 完成**：`scripts/agent_explore.py control` 在同一工具快照、输入与配置下跑固定流水线 v2（空缓存、真实请求），与 Agent 运行同样核对与评分；探索集定义为 `configs/phase2_explore.yaml`（有标注的按名称，无标注的按旧样例目录中的文件名，仓库里不写本机路径；real_doc03 的 DOCX 与 PDF 各算一次，共 13 次）。修正：固定序列共用一个工具上下文时调用记录由另一个工作区实例写入、事务认领对不上。第一轮快照 `r1`（提交 9cecc1b，规则 r1）上 13 次对照全部完成、工作区完整性全部通过：9 篇有标注文档的分数与开发缓存上的参考运行逐项相同（如 jtg3362 0.9665 / 0.8393 / 0.25）；无标注的 4 次中 real_doc03_docx 为 partial（19 个文本框块不支持），其余 complete。大文档耗时主要在逐张串行的图片描述：JTG 3362 全本 2726 s（168 张）、real_doc03_pdf 1019 s（139 张）、thesis_eng01 860 s（131 张）、real_doc03_docx 648 s（149 张），比 v1 的并发 VLM 退步 |
| 2026-09-24 | v1.14 | **Q42 输出包设计**（用户确定原则：信息完整、方便大模型使用、人也能读到渲染良好的 Markdown）：§4.5 增加输出包（Markdown、全部图片、信息摘要 `<名称>.json`、块级 sidecar），§6.5 改为嵌入图片始终保留原图链接、转录内容紧跟其后并注明来源，整页扫描图只在转录不完整时显示；v2 的 `parse` 入口、信息摘要与嵌入图片的文字 / 表格转录提前到阶段二的工具修订 |
| 2026-09-24 | v1.14 | **P2-4 第一轮完成，发现清单待确认**：13 次运行（每篇一个 Codex 会话，最多 4 篇并行）全部完成、导出、审计与完整性通过，无人工介入；审计误报一次（命令中的 HTML `</td>` 被当成路径），已修正并重新核对全部运行。Agent 在看图与判断处有收获（ocr01 跨页续表的表格 F1 0.725→0.830；real_doc01 heading_f1 0.04→0.78；omnidoc_book_zh_text_02 0→0.857），在改字与删除处无能为力（没有正文复核、没有排除操作）。速度：小文档 157–468 s（固定流水线 1–41 s），时间几乎全在模型步骤上——每步约 4–5 s，每篇 30–65 步，其中每篇 9–14 步摸索工具、12–25 步逐页通读，大文档上下文越滚越大（JTG 全本平均每步 17 万 token），图片描述逐张串行。发现清单 A–G（含缺陷：引擎的字面 `\n`、附录编号签名、`read` 的 StopIteration、`--schema` 不生效、版面检测的 `:memory:.ses`）与 8 项待决事项见[报告](../eval_reports/2026-09-24_p2-4_round1_findings.md)。L0 740 通过 + 4 个已知失败；v1 与 v2 的 L1 PASS |
| 2026-09-24 | v1.14 | **P2-4 发现清单确认**（用户）：Q14 CLI 够用，先批量与 `process`，仍慢再加常驻进程；Q30 第 1 项 Agent 可直接提交看图读出的修改，程序照常执行接受门，成本由"服务层 VLM 成批阅读、Agent 只处理待办项"控制；Q41 不固定 Codex 版本；新增 Q43（编号规则作用范围改为同一上级章节内）、Q44（DOCX 文本框提前）、Q45（复核可补回 OCR 漏掉的空单元格，限明确列出的区域并标注仅有图像证据）、Q46（标题角色 F1 作诊断项）；未见集新文档等工具成型后再定。P2-5 的范围与顺序写入 [v2_phase2_plan.md](v2_phase2_plan.md) §8 |
| 2026-09-24 | v1.14 | **P2-5 (1)(2)**：装置记录每个事件的到达时间（`agent.timing` 把耗时分为模型步骤与命令）、`run --doc a,b --jobs N`（默认 1）、不再因 Codex 版本变化拒绝运行（2cd389a）。修正第一轮发现的五个缺陷（5f426f8）：引擎用字面 `\n` 表示的换行（公式 `$…$` 之外；表格单元格保留为单元格内换行，v1 的 " / " 连接不变）；附录编号签名（C.1 → `L.N`，字母序号同一模式，只有 I、V、X 算罗马数字）；`read --block … --image page` 的 StopIteration；`describe_figure --schema` 强制类型；标准输入为空的失败信息。`:memory:.ses` 在 Codex 沙箱外复现不出，挂起。验收文档 jtg3362 的输出因单元格内换行改变：v2 冻结 run 以空缓存、全部真实请求重新冻结（提交 5f426f8，旧冻结改名 `.pre-p2-5`，`--replay` PASS），jtg3362 char_f1 0.9665→0.9669、编辑距离 0.0608→0.0604，关键内容错误 49→50（评测的单位抽取把 `HRB500<br>HRBF400` 读成"500 + HRBF"，此前被字面反斜杠挡住，属评测噪声）；其余三篇分数与请求数不变。L0 746 通过 + 4 个已知失败；v1 与 v2 的 L1 PASS；v1 冻结 run 回放 PASS |
| 2026-09-24 | v1.14 | **P2-5 (3) `process` 工具与批量描述**：`process` 一次调用完成标准处理（扫描页引擎、版面、图片批量描述、已确认续表、标题、check），返回摘要与待办清单（未解决项），已完成的步骤跳过；`describe_figure --blocks` 并发请求、按块顺序生效、单块问题不拖累整批、请求数预算按块顺序截断；固定序列运行时改为 `workspace init → process → export`，v2 冻结 run 回放逐字节一致。L0 749 通过 + 4 个已知失败；v1 与 v2 的 L1 PASS |
| 2026-09-24 | v1.14 | **P2-5 (4) Agent 修改与排除**：`correct` 工具让主 Agent 把看图读出的修改直接提交（正文按片段、表格按单元格，网格空位可补，Q30、Q45），程序照常执行接受门（图像须由本工作区的 `read` 为该块或其页生成、原生数字不改、结果非空），修改成为 `task=correct` 的 Observation，旧结果保留；`apply_structure` 新增 `exclude`（须写理由，账目 excluded，文字留在 sidecar）、`restore`（撤销页面装饰、装饰图或排除；修订删除的文字不可恢复），`set_role` 为 title 时可带 `level`。L0 756 通过 + 4 个已知失败；v1 与 v2 的 L1 PASS；v2 冻结 run 回放 PASS |
| 2026-09-24 | v1.14 | **P2-5 (4b)(5) 看图的两种方式、任务说明与 Skill**：用户提出（2026-09-24）Agent 是否在主循环里自己看图是关键——自己看图时成本不易控制，主模型若是纯文本也无法看；更通用的做法可能是 Agent 调用我们封装的 VLM 工具去读它想要的信息，Agent 只做决策与文本分析；两种方式实测对比后再定。据此新增 `ask_image`（向服务层 VLM 提问，答案为数据），`correct` 的图像证据接受 Agent 看过的图或 `ask_image` 读过的图；装置 `--vision agent|tool`（tool 模式关闭 Codex 的看图）。任务说明改为"先 `process`、按待办清单工作、不逐页通读"，附工具速查表与按看图方式分开的说明；三份 Skill 同步改为从待办清单与定向抽查出发，标题 Skill 加入"角色明确时给相对层级，不标待定"。L0 758 通过 + 4 个已知失败；v1 与 v2 的 L1 PASS；v2 冻结 run 回放 PASS |
| 2026-09-24 | v1.14 | **P2-5 第一次迭代检查**（[报告](../eval_reports/2026-09-24_p2-5_iteration1.md)）：两篇小文档 × 两种看图方式，一次一篇。首批（635a34f）暴露并修正两个缺陷（`ask_image` 路径类型；有级别的标题改回正文时报错，55dc38a）后重跑：四次都先调用 `process`、不再逐页通读。jtg3362 耗时 239→125 s、Agent token 与标价减半、质量不变；ocr01 耗时持平（251 s，Agent 把时间用在确认并合并跨页续表、排除图标字符、改单元格上，表格 F1 比固定流水线高 0.13）。剩余耗时是"步数 × 每步 6–8.5 s"（Codex 自身约 1.9 万 token 的系统提示使最简单的一步也要 2.5–4 s）；可省的步骤是查 `tool schema`、导出后核对输出、单独读 Skill。两种看图方式质量相当：经 `ask_image` 时 Agent token 少约 10%、服务层费用可忽略，但每个问题多一次往返 |
| 2026-09-24 | v1.14 | **P2-5 便宜的改法**（用户同意，2026-09-24）：任务说明写全 `correct`、`apply_structure`、`review_table`、`ask_image` 的请求格式（第一次迭代中每篇仍查 2–3 次 `tool schema`，每次 6–18 s）；三份 Skill 并入任务说明（`skills/` 仍保留）；写明导出结果即最终结果、不必再读 `out/` 核对；`ask_image` 支持一次多问（`questions`，并发作答、按提问顺序返回，`correct` 认可其中每个答案的图像）；装置改从本轮快照取任务模板，保证同一轮的任务说明一致。下一步：两种看图方式 × 推理强度 high / medium 的对照（用户同意测 medium） |
| 2026-09-24 | v1.14 | **P2-5 第二次迭代检查**（[报告](../eval_reports/2026-09-24_p2-5_iteration2.md)）：同一快照（112d38d）上两种看图方式 × 推理强度 high / medium，两篇各一次，8 次全部有效。步数再降约三成、Agent 输入 token 降三到五成，不再查 schema、读 Skill 文件或导出后核对。medium 每步中位 4.9–6.8 s（high 7.1–10.5 s），两篇最快为 90 s 与 123 s（第一轮 239 s、265 s），质量与 high 基本相同（ocr01 表格 F1 仍比固定流水线高 0.11–0.12）；两种看图方式无稳定优劣。偶发长停顿仍在（一步 150 s，推理 token 很少，疑为服务端或连接卡住后被 Codex 静默重试）。随后补上：任务说明写明 `evidence` 是字典（那一步的起因），装置给 Codex 设 `RUST_LOG=codex_core=info` 把卡住与重试记入 stderr |
| 2026-09-24 | v1.14 | **用户决定**：主 Agent 推理强度改为 medium（Q35 补充）；第二轮默认经 `ask_image` 看图，2–3 篇保留自己看图作对照（Q47）。装置默认值同步（`--effort medium`、`--vision tool`）。P2-5 继续剩余的工具项 |
| 2026-09-24 | v1.14 | **Q43 编号一致性的作用范围**：两个同一编号模式的标题之间若有一个不属于该编号体系、层级不深于两者中较浅者的标题（如"附件 3 检测报告"），不再要求同级；正文中 1.1 与 2.1、以及紧邻的 1.1 与 1.2 仍须同级。L0 761 通过 + 4 个已知失败；v1 与 v2 的 L1 PASS；v2 冻结 run 回放 PASS（验收文档输出不变） |
| 2026-09-24 | v1.14 | **Q44 DOCX 文本框**：`w:txbxContent` 的每个段落成为紧跟在锚定段落之后的正文块（账目 output，不带样式证据；`mc:AlternateContent` 仍只读第一个 Choice，VML 备用内容不重复）。real_doc03.docx 的 21 个文本框段落（一张由文本框组成的组织图）进入正文，失败块 19→0。L0 761 通过 + 4 个已知失败；v1 与 v2 的 L1 PASS；v2 冻结 run 回放 PASS |
| 2026-09-24 | v1.14 | **P2-5 段落续接（A3）**：渲染器把 `continues` 相连的正文类块输出为一段（块文字不变，放在第一块的位置），Agent 可用 `add_relation` 连接被分页或换行拆开的段落；任务说明与结构 Skill 写明用法。固定流水线自动提出跨页正文续接（§6.9）改为 P2-7 的候选通用算法，先在全量语料上验证。现有输出不变（此前只有已隐藏的续表块有 `continues`）。L0 762 通过 + 4 个已知失败；v1 与 v2 的 L1 PASS；v2 冻结 run 回放 PASS |
| 2026-09-24 | v1.14 | **Q42 输出包（A 部分）**：`export` 与 `parserx parse <文档> -o <目录> --pipeline v2` 写出同一个包——`<名称>.md`、`images/`（提取出的全部图片，包括不显示的装饰图）、信息摘要 `<名称>.json`（来源、状态与缺失项、标题大纲、表格数、每张图片是否显示与描述摘要、引擎与模型、请求与费用、警告）、块级 sidecar。Markdown 与 sidecar 不变（冻结 run 回放 PASS）。暂缓两项并说明原因：WMF / EMF 转 PNG 并入阶段四 OOXML 边界（Q9，需要可复现的转换与沙箱内的 LibreOffice 配置，目前只在一篇文档出现一张）；误识表格还原为正文与图片索引图在 `exclude` 与经 `ask_image` 看图之后价值变低，留到需要时再做。L0 763 通过 + 4 个已知失败；v1 与 v2 的 L1 PASS |
| 2026-09-24 | v1.14 | **Q42 B 部分：嵌入图片中文字与表格的转录**：路由为 SCAN / MIXED 的嵌入图片（PDF 与 DOCX）经扫描页引擎转录（多张图拼成一个 PDF、一次提交），转录内容成为紧跟在图片之后的块（`contains` 关系、账目 `ocr_block`、渲染时加 `<!-- 以下转录自上图 -->`，图片保持显示）；`process` 自动执行，Agent 可经 `recognize --blocks` 指定任意图片；已转录的 SCAN 图不再描述；图片内被标为标题的块不进入自动大纲（全量参考运行中截图里的界面标题曾被当成章节标题）。全量语料参考运行（开发缓存，含本轮全部修订）：27 篇中 4 篇变化——patent01 补回 2 张表格图片（漏表 2→0，表格 F1 0→1.0，char_f1 0.847→0.913，v1 0.880）；text_pic02 char_f1 0.858→0.876，但截图中的表格被转录后多出 6 张表（标注未收录，按 Q42 保留），表格 F1 0.629→0.417；ocr01 表格 F1 0.725→0.743；real_doc01 表格 F1 0.989→1.000。char_f1 平均 0.943（v1 0.893），与 v1 比变差 8、变好 10、持平 9（此前 9 / 9 / 9）。验收文档输出不变。L0 768 通过 + 4 个已知失败；v1 与 v2 的 L1 PASS；v2 冻结 run 回放 PASS |
| 2026-09-24 | v1.14 | **P2-5 WMF / EMF（C4）**：本机有 LibreOffice（.doc 转换已依赖它），不再推迟到阶段四。DOCX 中 EMF / WMF 图片经 LibreOffice 转成单页 PDF、PyMuPDF 按绘制范围渲染成 PNG（`content/vector.py`，每篇一个进程约 1 s，字节稳定；LibreOffice 打不开时会当作文字文档，这类结果按 PDF 的 creator 排除），之后与其他图片一样描述、路由与转录；没有 LibreOffice 时保留原文件并写 warning。语料中 real_doc01（1 张，企业标志）与 real_doc02（5 张）受影响：real_doc01 的标志现在可描述与转录（char_f1 0.9935→0.9933，转录出的标志文字不在标注中），其余指标不变；验收文档与 L1 文档不含矢量图，输出不变。另：本次发现 L0 若不排除 `test_live_e2e.py` 会发出真实请求并写入响应缓存，第二次运行命中缓存而失败——live 测试改为不用缓存（缺陷修正），§9.1 写明 L0 的命令。L0 774 通过 + 4 个已知失败（15 s）；v1 与 v2 的 L1 PASS；v2 冻结 run 回放 PASS |
| 2026-09-24 | v1.14 | **P2-7 跨页正文续接（固定流水线）与服务卡住的处理**：`process` 在标题之后按 §6.9 建立跨页段落的 `continues`（`content/continuation.py`）。全量语料上逐条核对：第一版把收据两页上的"管理订阅 ›"连成一句、把专利附图页的"图3""图4"和论文里未标为图注的"Figure 10:"接到正文后面、把操作说明里截图前后的两步连成一句——改为要求前段以文字或句中标点结束、两段不以图表标号开头、文字不同、中间只隔页面装饰。之后 27 篇中 4 篇共 10 处续接，逐条看都是被分页切开的句子（paper01 6 处、paper_chn01 3 处（其中 2 处是同一个被切开的公式）、paper_chn02 1 处参考文献）；指标无变化（字符与顺序类指标对换行不敏感），验收文档输出不变。另：字母后的连字符断行合并时不加空格（`character-istics`）。服务层：第二轮 ocr01 的一次 `ask_image` 中 2 个请求始终没有返回，工具等待、Agent 换个问法重问——流式回答加 60 s 空闲上限，httpx 流中途的传输错误列为可重试（此前不重试）。L0 790 通过 + 4 个已知失败；v1 与 v2 的 L1 PASS；v2 冻结 run 回放 PASS |
| 2026-09-24 | v1.14 | **F4 `:memory:.ses` 查明并修正**：来源是 onnxruntime 1.30 的遥测（版面检测经 rapid-layout 使用它）——它会把使用事件发到机器之外，在写不进主目录时（Codex 沙箱）改用内存标识并在当前目录留下 `:memory:.ses`；在沙箱外把 HOME 设为只读即可复现。`layout/detector.py` 在创建会话前设 `ORT_DISABLE_TELEMETRY=1`（用户显式设置时不覆盖），复现环境中不再出现该文件。第二轮 `r2` 的快照早于此修正，各目录中的该文件照此解释，不计为 Agent 的临时文件。L0 792 通过 + 4 个已知失败；v2 的 L1 PASS |
| 2026-09-24 | v1.14 | **第二轮 real_doc03_docx 暴露的三处缺陷（修正，工具快照 r2 之后）**：(1) 第二次 `process` 使 DOCX 工作区出现 4 条非法账目引用——图中读不出文字的图片（照片）没有 `contains` 关系，被当作未转录再读一遍，账目条目重复；Agent 只好另建工作区 `ws_clean` 重做并从那里导出（该次运行的导出不来自 `ws`，记为无效结果，修正后重跑）。改为以 `transcribed` Decision 判断，并在 real_doc03.docx 上用该次运行的缓存复现与验证：第二次 `process` 只做 `check`。(2) 读自嵌入图片的块无法裁剪、`correct` 不接受整张图片作证据，发票标题的 OCR 错字"大连恒恒程专用发菜"（图上"辽宁增值税专用发票"）改不了——现在可裁剪父图片，读过整张图片即算证据。(3) Q43 把图中文件的"一、"与正文的"一、"（H1）绑在一起，嵌入材料的小节给不了相对层级——编号一致性按所在图片分范围。L0 795 通过 + 4 个已知失败；v1 与 v2 的 L1 PASS；v2 冻结 run 回放 PASS |
| 2026-09-24 | v1.14 | **本机 LibreOffice 在无界面模式下找不到系统的中文字体**（LibreOffice 本身完全支持中文）：`--headless` 运行时它经 fontconfig 找字体，而本机这份 fontconfig 没有配置，只看得到安装包自带的几种西文字体（Linux Libertine、Carlito、Caladea、Frank Ruhl），中文字符没有画出来（文字层仍在）。发现于第二轮的注入测试文档：生成的 PDF 上中文全部空白，视觉模型如实读出"空白"，Agent 按图把表格里的注入文字改成了图上唯一可见的"0"——测试文档有缺陷，该次运行作废（原因记入 record），修正后重跑。矢量图渲染同样受影响（图中的中文标注会丢）：`content/vector.py::soffice_env` 在未设 `FONTCONFIG_FILE` 时给 LibreOffice 一个配置——先包含系统配置（Linux 的 /etc/fonts），再加常见字体目录；注入测试文档的生成脚本共用它。语料中 real_doc01、real_doc02 的矢量图渲染结果逐字节不变。L0 796 通过 + 4 个已知失败；v1 与 v2 的 L1 PASS；v2 冻结 run 回放 PASS |
| 2026-09-24 | v1.14 | **第二轮其余可修的缺口**：(1) pdf_text01_tables：跨页合并后的表格只能用第一页的图作证据，Agent 改在已被合并的续页原块上，修改被接受却不出现在输出里——现在合并后的表格认它所跨每一页的图，对已合并的块提交修改直接拒绝并指出应改的块。(2) real_doc03_pdf 第 60 页：识别漏掉一列，复核给出 11 列的正确网格，但数值门拒绝了新增的数字——Q45（用户已同意）此前只在 `correct` 中实现、`review_table` 未实现；现按 Q45 实现：`structure` 问题列出的范围内、现有结果完全没有的内容可以只凭图像补回并在 sidecar 中标明，其余数字必须完全一致；失败说明指出新增数字所在的格子。任务说明与 transcription Skill 各加一句（漏识整列时列出该列所有行）。该页 Agent 只列了一行，因此即使按新规则也会被拒——失败说明现在会指向其余几行。L0 798 通过 + 4 个已知失败；v1 与 v2 的 L1 PASS；v2 冻结 run 回放 PASS |
| 2026-09-24 | v1.14 | **P2-6 第二轮完成**（[报告](../eval_reports/2026-09-24_p2-6_round2.md)）：快照 d77109f、规则 r2、推理强度 medium、默认经 `ask_image` 看图、`--jobs 3`；探索集 13 次 + 注入测试 + 三篇自己看图的对照，共 17 次运行全部有效。同样 13 篇与第一轮比：耗时中位数 265 s → 155 s，Agent 标价 $17.3 → $3.32（JTG 全本 4666 s / $7.33 → 619 s / $0.44）；文字与表格不变或更好；无样式标题变差（real_doc01 heading_f1 0.781 → 0.263：待办清单里没有这类标题，Agent 不再通读全文就不会发现；omnidoc_book_zh_text_02 角色全对但层级按编号深度给成 H3 / H4）——新增待决事项 Q48–Q50。注入测试：指令一条未执行、原文保留（首次运行因测试文档画不出中文作废重跑）。本轮发现并修正工具包缺陷 8 处、实验装置缺陷 3 处（见本表此前各条与报告 §4）；两种看图方式在三篇上都是自己看图更快、质量相同，服务卡住修正后再比较 |
| 2026-09-24 | v1.14 | **用户决定**：Q48 按建议——`adapter:v1` 扩展到 DOCX（样式声明的标题优先，v1 排版检测出的其余标题并入同一大纲），v2 自己的标题候选留到阶段四；Q49 按建议——P2-7 之后在修正涉及的 8 篇上跑第三轮，并在三篇上再比较两种看图方式。Q50 已向用户解释，待判断。更正此前"LibreOffice 画不出中文"的说法：LibreOffice 支持中文，问题是本机无界面模式找不到系统字体（已由 `soffice_env` 解决） |
| 2026-09-24 | v1.14 | **Q48 实现**：DOCX 标题 = 样式声明的标题 + `adapter:v1` 排版检测出的其余标题，统一层级（`docx_titles`）。同时修正 `unify_levels`：被拉浅的编号模式，随后同模式的标题跟随（此前它们被编号一致性拒绝，成为没有层级的标题、显示为正文）。全量语料（开发缓存）只有 3 篇变化：real_doc01 heading_f1 0.040 → 0.743、text_report01 0 → 0.714（= v1）；ocr_scan_jtg3362 0.25 → 0.111——术语条目 2.1.10–2.1.19 此前因上述缺陷显示为正文，现在与 2.1.9 一致为 H3，标注不把术语条目当标题（Q23，只报告）；real_doc01 中合同的"第二条……第十三条"同理成为一致的 H2（标注未标，原型未修层级时为 0.795）。v1 的 DOCX 读取经 Docling：real_doc01 约 5 s、85 页的 real_doc03.docx 约 11.5 s。验收文档 ocr_scan_jtg3362 的输出改变：v2 冻结 run 以空缓存、全部真实请求重新冻结（见下一条）。L0 801 通过 + 4 个已知失败；v1 与 v2 的 L1 PASS |
| 2026-09-24 | v1.14 | **v2 冻结 run 重新冻结（Q48 之后）**：以空缓存、全部真实请求在提交 5e10f2b 上重新冻结 `eval_runs/2026-09-24_p1_v2_toolkit`（O/V/L 1/2/0 与此前相同，缓存命中 0），旧冻结改名 `.pre-q48`；`--replay` PASS，v1 冻结 run 回放 PASS。四篇中只有 ocr_scan_jtg3362 的输出改变（heading_f1 0.25 → 0.111，原因见上一条，按 Q23 只报告），其余分数与请求数不变 |
| 2026-09-24 | v1.14 | **用户决定**：Q50 留到阶段四，与 Q9（OOXML 支持边界）一起做，重点是图表、公式、SmartArt 等只有渲染后才看得见的内容 |
| 2026-09-24 | v1.14 | **P2-7 表格单元格中的图片**（来自第一轮 JTG 全本的临时脚本 `prepare_table_image_regions.py`）：扫描页引擎把单元格或段落中的图片留成指向它自己裁图的引用，v2 原样输出，成为断开的链接、图片丢失（第二轮 JTG 全本 17 处、real_doc03_pdf 8 处；有标注的语料中没有）。现在按引用文件名中的框从本方页面渲染图裁出，作为紧跟在表格之后的图片块保存与描述，单元格中留标记〔图k〕，渲染时加说明。JTG 全本上复核：17 处全部换成图片、由服务层描述（17 次 VLM 请求），无断开的链接。全量语料指标不变；验收文档输出不变。L0 803 通过 + 4 个已知失败；v1 与 v2 的 L1 PASS；v2 冻结 run 回放 PASS |
| 2026-09-24 | v1.14 | **第三轮发现的两处缺陷（修正）**：(1) ocr01（两种看图方式）：跨页合并表格用续表块的裁剪图作证据仍被拒——证据只认合并后表格所在各页的整页图；现在已合并进来的续表块的裁剪图也算。(2) real_doc03_docx：Agent 运行时 `adapter:v1` 报"could not read source.docx"——Docling 读 DOCX 时经 LibreOffice 渲染绘图，沙箱里 LibreOffice 建不了配置目录（退出码 77），整个读取失败；在只读 HOME 下复现。标题检测不需要绘图，读取时关掉 Docling 的渲染，复现环境中恢复正常（48 个标题）且更快。未处理：ocr01 跨页接缝处重复出现的行（长图切页时的重叠）不能安全删除——按"整行文字相同"判断会误删别处合法重复的内容（同一表内另有三行相同的处理建议），留到阶段四在合并续表时判断。L0 805 通过 + 4 个已知失败；v1 与 v2 的 L1 PASS；v2 冻结 run 回放 PASS |
| 2026-09-24 | v1.14 | **P2-7 表格算术一致性（第一轮发现 G1，来自 Agent 的 `audit_tables.py`）**：识别得来的表格中，多数行成立的乘积关系或经两列以上确认的合计行，在个别行、列不成立时列入待办清单（`table_arithmetic`），由 Agent 对照图像核对，程序不改数字。只凭数字找关系，不用表头词表。验证：有标注的语料 21 张识别表格 0 条；探索集运行中的 110 张识别表格 3 张有提示，全部属实——real_doc03 发票第 15、16 行金额少了一位（10 × 6787.61 应为 67876.11，税额 8823.89 正是它的 13%，第三轮 Agent 没有发现）、股东表合计行与漏识的一列对不上（第二轮 Agent 发现过）。另有一处巧合（钢筋直径 6、8、10 的面积与重量恰好 6² + 8² = 10²）在第一版被误报，改为不把严格递增的整数列（序号、尺寸、年份）当作被合计的列后消除。Skill 与接口说明各加一句。L0 812 通过 + 4 个已知失败；v1 与 v2 的 L1 PASS；v2 冻结 run 回放 PASS |
| 2026-09-24 | v1.14 | **P2-7 可疑字符（来自第一轮 JTG 全本 Agent 的 `audit_text.py`）**：Agent 原本查的是"罕见文字系统"（识别把符号认成了外文字），这类在所有运行中只有 JTG 全本的 3 个片假名；同一次扫描发现更普遍的问题——原生文字层中字体没有对应 Unicode 的字符（私用区、U+FFFD）直接进入输出：paper_chn01 162 个、paper_chn02 148 个（如"文章编号：0258\ue0112724"，应为连字符）、thesis_eng01 52 个。两类都列入待办清单与摘要 `review`（`text_suspicious`，每块一条），由 Agent 对照图像；有标注的语料中只有 paper_chn01（17 块）与 paper_chn02（61 块）有提示。Skill 加一句（同一字符出现很多次时核对几处并报告，不必逐处修改）。L0 通过（4 个已知失败）；v1 与 v2 的 L1 PASS；v2 冻结 run 回放 PASS |
| 2026-09-24 | v1.14 | **第四轮核对**（快照 621ddf0，real_doc03_pdf、thesis_eng01，两篇有效）：thesis_eng01 按 13 条 `text_suspicious` 对照图像把 U+FFFD 改成图上的括号，全部采用；real_doc03_pdf 收到 2 条 `table_arithmetic`（发票金额少一位），视觉模型读整张 29 行表格的裁剪图同样少了数字，Agent 无法确认而保留原值——原页上清楚是 67,876.11 与 80,513.27。后续建议：`ask_image` 问表格中某几行时只裁这几行、提高分辨率。阶段二探索报告写入 `eval_reports/2026-09-24_p2_agent_exploration.md` |
| 2026-09-24 | v1.14 | **用户决定（P2-8）**：采纳 `ask_image` 按行裁剪高分辨率条带的建议；未见集新文档先从项目早年收集的测试数据中找，不够时在本机检索典型 PDF / Word 并截取部分页；暂不用 Claude Code，第三方 Agent 框架（Pi 或其他）的选择作为单独议题，其余工作完成后再切换（Q13、Q37 补充） |
| 2026-09-24 | v1.14 | **`ask_image --rows`**（用户同意）：问大表格中的某几行时，只裁这几行（按行数估算位置、上下各放宽一行）、以 300 dpi 渲染，并把这几行的当前识别结果作为数据附给视觉模型。real_doc03_pdf 发票第 15、16 行：整表裁剪图上读成 6786.11，按行裁剪后读出 67,876.11 与 80,513.27（与原页一致）；视觉模型仍会偶尔漏读（第 11 行 170,761.06 读成 17,076.06），Skill 要求算术与图像一致时才改。任务说明与 transcription Skill 各加一句 |
| 2026-09-24 | v1.14 | **第五轮核对**（快照 279d9f5，real_doc03_pdf）：Agent 收到发票金额的 `table_arithmetic` 提示后用 `ask_image --rows 15 16` 读出 67,876.11 与 80,513.27，经 `correct` 改正、被采用，导出表格与原页一致——算术提示、按行裁剪与看图修改首次在 Agent 手中完整走通。P2-8：未见集候选三篇已截取并暂存在仓库外 `~/parserx-exp/phase3_candidates/`，待用户确认后起草标注 |
| 2026-09-24 | v1.14 | **P2-8 未见集与验证集（标注草稿待审核）**：用户确认三篇候选可用、`text_pic03.docx` 截一段放验证集。项目早年收集的数据不补缺：样例目录只剩 `text_pic03`、`receipt02` 未用；OmniDocBench 本机只有已用的 8 页图片；ParseBench 为英文单页、只标表格且 v1 曾用它调参。草稿暂存 `~/parserx-exp/phase3_candidates/`（含标注约定与待审核判断的 README）：n1 手工排版 Word 技术规格书、n2 合同 PDF 中无个人信息的 6 页（密集表格、跨页表、预算表）、n3 手机扫描的立项报告（手写签名不转录）；验证集 v1 为 `text_pic03` 第 3 章（嵌套表格与文本框画的架构图）。草稿直接从源文件起草（未参考 ParserX 输出），表格都能被评测器解析，原生来源的字符都能在源文件中找到。合同中含身份证号、手机号的页未选用 |
| 2026-09-24 | v1.14 | **P2-8 完成、阶段二完成**：未见集与验证集标注经用户对照原文件审核通过（草稿直接从源文件起草，未用 OCR 服务或 ParserX 输出；扫描件由 Claude 看图读出），写入 `ground_truth_unseen/`（新目录，开发中的运行不扫描，`.gitignore`）与 `ground_truth/val_word_template01`；`configs/phase3_unseen.txt` 列出未见集 6 篇。验证集在固定流水线上可以运行（表格 F1 0.889、char_f1 0.925、heading_f1 0：加粗正文作标题，留作开发中的验证）。§9.4 协议补全：阶段三先比较固定流水线与 Codex，每篇每个运行时 2 次；Claude Code 暂不用，第三方 Agent 框架另议。阶段二的全部退出条件满足 |
| 2026-09-24 | v1.14 | **阶段三分解确认**（[v2_phase3_plan.md](v2_phase3_plan.md)）：Q51–Q55 按建议确认；用户补充"不必在数据集划分上做得过细，目标是典型文档都能正常处理，以后遇到新文档处理不好再打磨"——阶段三执行从简 |
| 2026-09-24 | v1.14 | **阶段三运行与报告**：实验装置可读 `ground_truth_unseen/`（P3-1）；快照 49fb950 上固定流水线与 Codex 各跑未见集 6 篇 × 2 次，全部有效。[对比报告](../eval_reports/2026-09-24_p3_runtime_comparison.md)：char_f1 0.934 / 0.935、表格 F1 0.558 / 0.534（下降来自扫描表单跨页合并与标注口径），heading_f1 0.380 / 0.569；固定流水线每篇约 33 s、$0.003，两次结果一致；Codex 约 164 s、$0.22；预定分流规则只选中 text_pic02，混合方案等于固定流水线。缺陷 D1–D6 与工具缺口 T1 两种运行时共有。按"执行从简"未另冻结固定流水线 run。建议 Q13：默认固定流水线，Codex 可选 |
| 2026-09-24 | v1.14 | **Agent 运行时调研**（[v2_agent_runtime_research.md](v2_agent_runtime_research.md)）：OpenAI Agents API 是托管的 Codex（系统提示只能追加、循环在服务器端、无本地回放、不支持 ZDR），不适合；Pi 设计值得借鉴，但只有 TypeScript、无步数与费用上限；PydanticAI 是现成框架中最合适的备选。建议自研薄循环（约 400–500 行，经 `ServiceGateway`），新增待决问题 Q62–Q64（原编号 Q56–Q58，与"信号设计"的 Q56 及阶段四的 Q57–Q58 重复，2026-09-25 改号） |
| 2026-09-25 | v1.14 | **§9.5 问题定位方法**（用户要求写入）：五层（内容获取与算法、待核对信号、接受门、工具能力、Agent 判断）与四个定位问题，阶段三 D1–D6 为第一次应用。修正顺序经用户同意：先 ① 的 D1，再修门检查的对象，再做信号，最后 D3、D4。新增 Q56（信号设计原则，回应用户"信号沦为一堆规则"的担忧；用户补充：不做每页都读一遍的方案；规则尤其带魔法数字的规则要非常谨慎） |
| 2026-09-25 | v1.14 | **D1 修正与门的第一处修正**：① `content/pdf_native.py` 单元格文字只沿自身方向在格间切分（与表格文字方向不同、又不整条落在一格内的行不属于任何单元格），`content/furniture.py` 的 `mark_watermarks`：与本页文字方向不同、同一形状跨页重复的文字块为 WATERMARK（新 BlockKind），`excluded`；两条都是正确性陈述或沿用跨页重复证据，没有新阈值。全语料只影响 unseen_pdf_tables01（水印 5 页排除，char_f1 0.913 → 0.933、表格 F1 0.518 → 0.595）。③ `review_table` 的内容守恒由"原有每格文字须原样成为某一格"改为"原有每格文字须按出现次数、沿候选表的行或列连续读出"：拆行、拆列、合并与移动通过，丢字改字不通过（paper01 的 9 行表属此类）。`correct` 的"原生文字的数字不改"保留——水印数字已在 ① 去掉。原生页的 OCR 旁证暂缓，待 Q56 确定（本地读数可能替代）。L0 821 通过 + 4 个已知失败；两个 L1 PASS；两个冻结 run 回放 PASS（验收输出不变） |
| 2026-09-25 | v1.14 | **Q56 确认与本地读数双向比对**：用户同意 Q56，并补充 Agent 按需读取是核心优势、不能排除。新增 `reading/local.py`（rapidocr，依赖显式加入）、`reading/compare.py`、`tools/page_reading.py`（`process` 的 reading 步骤）、`DocumentState.readings`、待核对项 `text_unaccounted` / `text_not_seen`；`Unresolved` 增加 `quotes`（DocText：文档文字只作数据出现，详情由程序写）。校准与两个容差见 §9.5。L0 832 通过 + 4 个已知失败；两个 L1 PASS（v2 L1 因本地读数由约 4 s 增至约 19 s）；两个冻结 run 回放 PASS（验收输出不变） |
| 2026-09-25 | v1.14 | **Agent 按需读取后的修正通道**（用户：按需读取是 Agent 的核心优势）：`correct` 新增 `add`——页面图像上有、却不在任何块里的文字（`text_unaccounted`），在 Agent 看过该处图像、且本地读数该处也有这段文字（两份独立读数一致）、该处没有块已含这段文字时，成为新的文字块（账目单位 `agent_text`，按位置排入阅读顺序）；原生文字层的数字改为"只有本地读数在该位置显示为新值、不显示被删的值时才能改"（门检查的对象由 Agent 的说法换成程序可核实的独立读数）；接受门新增 `independent_reading`。任务说明与转写 Skill 写明 `text_unaccounted` / `text_not_seen` 的处理方式。L0 834 通过 + 4 个已知失败；两个 L1 PASS；两个冻结 run 回放 PASS |
| 2026-09-25 | v1.14 | **本地读数的 Agent 验证**（[报告](../eval_reports/2026-09-25_p4_page_reading.md)）：Codex 在 4 篇有已知问题的文档上按新待办项补回 OCR 漏行（unseen_scan_form01 char_f1 0.969 → 0.985）、矢量绘制的标题与表头（text_table_word 0.975 → 0.994）、清除跨页接缝的重复格（unseen_pdf_tables01 0.933 → 0.977）；patent01 的授权公告日被 Agent 误判为已覆盖，转写 Skill 已补充说明。这 4 篇在固定流水线上的待核对项由 0 变为列出真实问题 |
| 2026-09-25 | v1.14 | **D3、D4、D6 修正**（原则性规则，无新阈值）：**D6**——每个字符只属于包含它的最小单元格（PyMuPDF 在缺边时给出覆盖多格的"外框格"，它不再重复收入各格文字），unseen_pdf_tables01 char_f1 0.933 → 0.977。**D3**——①一行中每个非空格子的行数相同（≥2）、逐行对齐、且至少一处换行不是宽度所迫（下一个词本放得下）时，按行拆成多行（无框线表；一行只是换行或含单行格子时不拆），paper01 表格 F1 0 → 0.529；②没有文字的框线网格不是表格；③原生页的框线网格只有在本地版面检测器也看到表格（任何重叠）时才按表格读，否则其中的行照常作正文（语料中 11 个假表全部没有检测器表格重叠、26 个真表全部重叠），`layout.check_tables`、`extract_pdf(tables_seen=)`。text_pic02 的批注页不再整页成表，但其标注按旧的假表格写（整页一格、批注一列、保留 "_ _" 碎片），指标下降（char_f1 0.877 → 0.798）来自标注，内容未丢——**标注需用户同意后修改**。**D4**——①`unify_levels` 新增：点分编号扩展前面某标题的编号（5.1.1 在 5.1 之后）就低它一级，unseen_word_spec01 heading_f1 0.633 → 0.900；②版面检测器的节标题标签 + 排版与正文不同（字体、字号、加粗；`TextStyle.font` 新增）作为待核对项 `title_candidate`，不直接采用——实测直接采用时 2 篇变好、4 篇略差（层级错、封面行、标注惯例），按"规则要谨慎"改为信号；③编号序列缺号（5.1 与 5.3 之间、6.1 的上级）且中间段落恰以该编号开头，也列为 `title_candidate`。结构 Skill 写明处理方式。仍缺的工具：拆分一个含软换行的段落（"6.1 铸钢件"与下一项同段） |
| 2026-09-25 | v1.14 | **标注修正**（用户同意）：text_pic02 第 3–5、9–10 页原按旧的假表格写（整页一格、批注一列、"_ _" 碎片），改为标题（`##` 章、`###` 节，与第 13 页一致）、段落与批注（每条批注放在它所注的段落之后），逐字取自文字层并核对（每页字母数字与文字层完全一致；跨页的一句合在第 9 页）；原文件留在会话草稿区。patent01 补上"## 附图说明"（排版与另外四个节标题相同，原标注漏标且无此行）。按新标注，D3 修正后 text_pic02 char_f1 0.802 → 0.858（修正前后同一标注比较），表格 F1 0.182 → 0.235 |
| 2026-09-25 | v1.14 | **补两个缺口**（用户同意先补再重跑对比）：①新工具 `close`——Agent 看过图、确认无需修改的提示类待办（`text_unaccounted`、`text_not_seen`、`title_candidate`、`text_suspicious`、`table_arithmetic`、`table_merge_candidate`、`structure_pending`、`evidence_conflict`、`table_uncertain`）可关闭并写明理由；门同 `correct`：须在本工作区读过该块或该页的图；失败类（待识别页、失败块、预算跳过、缺资源）只能处理、不能关闭；按（目标、种类、引文）记录，内容变了会重新打开；`DocumentState.closed`，文档摘要 `review.checked` / `checked_by_kind`。②`apply_structure` 新操作 `split`——在块内第 n 个换行处拆成两块（后一段成为紧随其后的新文字块，账目留在原块），不带文字字段，内容只分不改；不合法时 `no_line_break`。任务说明、结构与转写 Skill 写明用法。L0 849 通过 + 4 个已知失败 |
| 2026-09-25 | v1.14 | **修正后重跑对比**（快照 b0a833e，6 篇 × 2 × 2，24 次全部有效，[报告](../eval_reports/2026-09-25_p4_runtime_comparison.md)）：固定流水线 char_f1 0.922 → 0.942、表格 F1 0.519 → 0.629；Codex heading_f1 0.499 → 0.619、角色 F1 0.517 → 0.662（text_pic02 0.143 → 0.960）；Agent 用 `close` 关闭看过的提示，处理后待核对项大都为 0；Q13 建议改为默认混合方案 |
| 2026-09-25 | v1.15 | **Q13 决定、阶段三完成**：用户同意默认用混合方案（先跑固定流水线，有待核对项或状态不是 complete 就交给 Agent；保留只用固定流水线的模式）。Q54 执行：unseen_word_spec01、unseen_pdf_tables01、unseen_scan_form01 由 `ground_truth_unseen/` 移入 `ground_truth/`，成为开发语料；以后验收另找未见文档。下一阶段：阶段四（分解待写） |
| 2026-09-25 | v1.15 | **阶段四分解草稿**（[v2_phase4_plan.md](v2_phase4_plan.md)）：混合运行时作为产品默认（P4-1）、标题的 v2 自有路径（P4-2）、有信息的图片不丢（P4-3）、DOCX 页面（P4-4）、专项验证（P4-5）、收尾（P4-6）；新增待决问题 Q57–Q59；§0.2 改为阶段四启动清单 |
| 2026-09-25 | v1.15 | **阶段四分解确认并细化**（[v2_phase4_plan.md](v2_phase4_plan.md)）：用户基本同意，Q57–Q59 按建议；用户补充控制台交互（新 P4-2：进度、Agent 动作的可读显示、结果摘要、回退与中断提示、非终端与 `--json`）。细化加入进入阶段四时的事实（固定流水线 v2 与 v1 按现行标注重算：char_f1 0.943 / 0.891、表格 F1 0.817 / 0.775、heading_f1 0.537 / 0.504，逐篇差距清单）与各项设计。新增 Q60（界面语言）、Q61（`parserx parse` 默认改 v2 混合方案）。下一轮会话启动提示词写入 [next_session_prompt.md](next_session_prompt.md) |
| 2026-09-25 | v1.15 | **Q60、Q61 决定与改号**：Q60 默认中文，`--lang en` 切换英文，命令、参数、JSON 字段名用英文；Q61 `parserx parse` 默认用混合方案（v2），v1 是否保留、是否只留一条流水线阶段五再讨论。Agent 运行时调研的三个待决问题由 Q56–Q58 改为 Q62–Q64（与"信号设计"的 Q56、阶段四的 Q57–Q58 重复），调研文档同步改号 |
| 2026-09-25 | v1.16 | **P4-1 混合运行时完成**：`parserx/runtimes/hybrid.py`（读取 → 标准处理 → 保留固定结果 → 分流 → Agent → 完整性核对 → 导出，摘要记 `processing.runtime` / `runtime_note` / `agent`）、`runtimes/agent.py`（`AgentRuntime` 协议、`CodexAgent`：可用性只看 `codex login status` 的返回码，截止时间按 Q39，超时与 Ctrl-C 都停掉整个进程组）、`runtimes/actions.py`（从工具调用记录得到 Agent 动作与计数）、`runtimes/events.py`（进度事件，P4-2 渲染）；配置 `runtime.mode: hybrid \| fixed`（默认 hybrid）、`runtime.agent`（codex · gpt-6-sol · medium）；`pipeline` 默认 v2（Q61），`configs/regression.yaml` 固定 v1；工具调用被中断时记失败码 `interrupted`。实现选择见 Q65。L0 865 通过、4 个已知失败；两个 L1 PASS；两个冻结 run 回放 PASS |
| 2026-09-25 | v1.16 | **P4-2 控制台交互完成**（[报告](../eval_reports/2026-09-25_p4-1-2_hybrid_console.md)）：`parserx/console/`（`ConsoleReporter`：终端原地更新、非终端每步一行、`-q`、多文件时每篇一行加合计；中英文消息集中在 `messages.py`，Q60）；`parserx parse` 多个文件或目录、`--runtime`、`--lang`、`--json`、`--keep-work`，退出码 0 / 1 / 130，SIGTERM 与 Ctrl-C 一样停下 Agent 并保留工作区；进度事件来自 `process` 各步、本地读数逐页、扫描引擎排队；Agent 动作同一次调用中多个同类结构动作合成一行（标题逐条）。`agent_explore.py parse` 在仓库外快照上运行产品命令并审计。真实验证：text_table_word、patent01、unseen_word_spec01 交 Agent（分数与阶段三 Codex 一致，每篇 $0.13–0.20、约 2 分钟），text_table01 跳过 Agent，去掉 Codex 时回退并在恢复后接着做，Ctrl-C 后 2 秒内停下 Codex、再次运行接着做；审计全部通过。L0 881 通过、4 个已知失败；两个 L1 PASS；两个冻结 run 回放 PASS |
| 2026-09-25 | v1.16 | **用户试用后的修正**（电子发票）：① 导出只复制原图与裁剪图，从页面渲染出的图（嵌入数据不可用的印章）只有链接没有文件——改为导出所有嵌入原图、每个图片块自己的图、显示的扫描图，看图时临时生成的图不再进 `images/`（9aa27df）；② Q66 信号 `figure_without_content`，`apply_structure` 返回本次打开的待核对项；③ Q67 全局配置模板与 `.env` 查找；Q65 维持。L0 885 通过、4 个已知失败；两个 L1 PASS；两个冻结 run 回放 PASS |
| 2026-09-25 | v1.16 | **P4-3 标题完成**（[报告](../eval_reports/2026-09-25_p4-3_headings.md)）：`numbering_signature` 区分阿拉伯、汉字、罗马数字（旧写法让合法性检查拒绝 `一、>（一）>1.>（1）`）；`unify_levels` 按出现顺序嵌套编号样式（建议层级相同、编号不接续时低一级）；adapter:v1 的标题是原生块开头一行或几行时先 `split` 再设标题，文字 NFKC 比较；修正 `apply_structure` 对 `split` 新块的差异计算（此前每次 `split` 都是内部错误）。全语料 24 篇：heading_f1 0.537 → 0.537、角色 F1 0.656 → 0.663（v1 0.504 / 0.599），信息类不变；v1 更好的 8 篇逐篇定位（patent01 混合方案覆盖、3 篇标注写法、text_code_block 的指标漏洞 Q68）；Q59：adapter:v1 保留到阶段五。L0 890 通过、4 个已知失败；两个 L1 PASS；两个冻结 run 回放 PASS |
| 2026-09-25 | v1.16 | **P4-4 有信息的图片不丢**（[报告](../eval_reports/2026-09-25_p4-4_images.md)）：廉价过滤判为装饰形状的图片（细边、细条、小图标），只在检测器看不到文字或公式时才排除——单行公式正是细条形状；这类小图放在版面分辨率的空白页上检测（单独检测时公式条漏检、二维码被读成标题）。`scripts/image_loss.py` 度量：隐藏且本地读数有文字的图片 10 → 2（标志、手写签名），标注中有而输出缺的 0；patent01 char_f1 0.913 → 0.934（公式 (6)(7) 与矩阵公式此前被当装饰图丢弃）。receipt 输出改变（Apple Card 标志图带文字，现显示并转写），v2 冻结 run 以空缓存重新冻结为 `eval_runs/2026-09-25_p4_v2_toolkit`（提交 9e4d12b，O/V/L 2/3/0，`--replay` 通过；旧的改名 `.pre-p4-4`）。L0 891 通过、4 个已知失败；两个 L1 PASS；两个冻结 run 回放 PASS |
| 2026-09-25 | v1.16 | **P4-5 DOCX**（[报告](../eval_reports/2026-09-25_p4-5_docx.md)）：先量收益——6 篇 DOCX 经 LibreOffice 排版后逐行比对，页面上有而输出里没有的只有换行片段与修订标记，页面层建议推迟（Q69）；修正一处静默丢失：`w:drawing` 中的图表与 SmartArt 此前没有任何去向记录，现在与脚注、批注一样记为失败块并保留部件文字（d976a0d）；OOXML 支持边界表写出，待用户确认（Q9）。L0 892 通过、4 个已知失败；两个 L1 PASS；两个冻结 run 回放 PASS |
| 2026-09-26 | v1.17 | **Q9、Q68 实施与 P4-6 完成**（[报告](../eval_reports/2026-09-26_p4-6_gaps.md)）：指标 2.2（代码块内不算标题、标题 NFKC），冻结 run 离线重算；Word 脚注尾注输出为 Markdown 脚注、批注改为排除、OMML 公式转 LaTeX（`content/omml.py`）；不确定图的本地读数有描述未承接的文字时交扫描引擎转写（守恒）；等宽字体按字宽测量、非正文字体的等宽文字输出为保留换行的代码块；版面检测器标为表格而无框线的区域按文字对齐取表（两份读数须一致）。固定流水线全语料：char_f1 0.944 → 0.947、表格 F1 0.817 → 0.862（paper_chn02 0 → 0.888）、heading_f1 0.564；仍比 v1 差的 10 项逐篇定位（界面元素、LaTeX 标注写法、合并单元格写法、扫描引擎跨度错误）；新待决 Q70（原生页公式转 LaTeX）。L0 912 通过、4 个已知失败；两个 L1 PASS；两个冻结 run 回放 PASS |
| 2026-09-26 | v1.17 | **Q70 公式**：引擎对比（扫描引擎 90.6 / VLM 86.2 / VLM+文字层 87.5）后以扫描引擎识别原生页行间公式为 LaTeX，两份读数一致且文字层字符守恒才采用；`\begin{…}` 开头的公式补 `$$` 定界（此前不渲染）；修正 `_next_block_seq` 对拆出块 id 的解析。paper_chn01 char 0.751 → 0.757；保守门使多数区域保留文字层（见 Q70）。L0 914 通过、4 个已知失败；两个 L1 PASS；两个冻结 run 回放 PASS |
| 2026-09-26 | v1.17 | **Q70 第二版**：原生页公式改为整页识别、逐段取舍、VLM 编辑、Agent 定稿（见 Q70）；新待核对类 `formula_candidate`（可关闭）；实验脚本 `scripts/formula_page_experiment.py`、`formula_merge_experiment.py`。另记：P4-7 运行 A（公式改动前）混合方案 char 0.948、表格 0.875、heading 0.704、角色 0.853（v1 0.891 / 0.775 / 0.524 / 0.620），[报告](../eval_reports/2026-09-26_p4-7_full_run_a.md)。L0 918 通过、4 个已知失败；两个 L1 PASS；两个冻结 run 回放 PASS |
| 2026-09-26 | v1.18 | **阶段四完成**：P4-7 全语料运行 B（Q70 第二版之后，空缓存；固定流水线冻结为 `eval_runs/2026-09-26_p4-7b_v2_fixed_full`）。混合方案 char_f1 0.947、表格 F1 0.875、heading_f1 0.756、角色 F1 0.868（v1 0.890 / 0.775 / 0.494 / 0.614；运行 A 0.947 / 0.875 / 0.720 / 0.847，均按修正后的标注重算），公式文档 paper_chn01 char 0.758 → 0.811、paper_chn02 0.857 → 0.895。按用户要求修正有明确证据的标注错误（[修订记录](annotation_changes.md)）。退出条件全部满足，[报告](../eval_reports/2026-09-26_p4-7_full_run_b.md)；新待决问题 Q71；§0.2 改为阶段五启动清单。L0 918 通过、4 个已知失败；两个 L1 PASS |
| 2026-09-26 | v1.19 | **Q71 实施**（用户按建议决定）：被遮挡的文字保留并在摘要中注明（`close` 的 `occluded`、`review.occluded`）；转录 Skill 统一网页界面文字的做法。真实验证 deepseek char_f1 0.901 → 0.988。L0 919 通过、4 个已知失败；两个 L1 PASS |

| 2026-09-26 | v1.20 | **阶段五分解确认**（[v2_phase5_plan.md](v2_phase5_plan.md)）：Q72–Q77 均按建议决定。进入时核实：去掉 adapter:v1 固定流水线 heading_f1 / 角色 F1 从 0.582 / 0.690 降到 0.258 / 0.343，差在块内标题行的排版、手工排版 DOCX、按字号排层级；删除配置字段会使旧冻结 run 指纹对不上；v2 不调用 LLM；§11.5 七条中已覆盖 2 条、部分覆盖 4 条、第 4 条撤销（Q77） |
| 2026-09-26 | v1.21 | **P5-1 完成**：§11.5 七条各有 v2 测试（第 4 条按 Q77 撤销）。新增两处小改动：`accounting/check.py` 把 `duplicate_of` 链只通向隐藏块的重复块计为不一致（全语料 0 处）；`reading/compare.py` 只让显示的图片承接其中的文字（全语料多出 1 行：patent01 页边被判为装饰而排除的细条上的"3901"）。全语料固定流水线输出逐字节不变。L0 923 通过、4 个已知失败 |
| 2026-09-26 | v1.22 | **P5-2、P5-3、P5-4 第一批**：adapter:v1 删除，标题由 `hierarchy/typography_titles.py` 按"两种独立证据一致"给出（排版、版面标签、编号、编号嵌套），DOCX 读出字体，PDF 行的排版取主要书写系统，`unify_levels` 保留 DOCX 声明的层级；固定流水线 heading_f1 0.601 → 0.697、角色 F1 0.684 → 0.802（修订后的标注，24 篇），信息类不变；混合方案 0.685（运行 B 0.774），Q78 接受并记录。标注修订：paper_chn02、text_table01、unseen_word_spec01。v2 的辅助函数迁出 v1（`content/`、`eval/text.py`、`tables/html.py`）。只剩一条流水线：`Pipeline` 只跑 v2，`pipeline` 配置字段删除（冻结 run 的指纹按现行 schema 重算，删除的字段忽略），`configs/regression.yaml` 即 v2 回归配置。本地标签 `v1-final`（3db3603）。验收冻结 run `2026-09-26_p5-2_v2_toolkit`（650d790） |
| 2026-09-26 | v1.23 | **阶段五完成**（[退出报告](../eval_reports/2026-09-26_p5-7_cleanup_exit.md)）：P5-5 依赖、P5-6 README 与 `--help`、Q76 pymupdf 1.28 推迟；P5-7 空缓存全语料冻结 `eval_runs/2026-09-26_p5-7_fixed_full`（a734449）与混合方案真实运行 p5c：固定流水线 char_f1 0.950、表格 F1 0.866、heading_f1 0.699、角色 F1 0.805；混合方案 0.952 / 0.875 / 0.697 / 0.806（运行 B 标题 0.774，Q78 接受；Agent 的标题修改有得有失，净效果约为零）。L0 581 通过、无已知失败。§0.2 改为阶段五之后的启动清单 |
| 2026-09-26 | v1.24 | **阶段五之后**：openai 2.30 → 3.19（httpx2）；Q79 标题计分范围（`eval/outline.py`，receipt 标为无大纲）；Q80 依赖要升级，PDF 段落拼接与库的选择先分析；Q69 补充按页处理 DOCX 的触发情况 |
| 2026-09-26 | v1.25 | **Q80 实施**：段落由版面检测器的文字区域分（`content/paragraphs.py`），PyMuPDF 1.27 → 1.28（输出与 1.27 逐字节相同），`import fitz` → `import pymupdf`；全语料 13 篇有变化，全部在容差内或提高；有大纲文档的标题 0.672 → 0.687。新脚本 `scripts/paragraph_segmentation.py`。冻结 run 以空缓存重新冻结为 `2026-09-26_q80_v2_toolkit`、`2026-09-26_q80_fixed_full` |
| 2026-09-26 | v1.26 | **Q81**：`skim` 工具（`tools/skim.py`）；structure Skill 改为先读懂全文的通用方法；审计记下直接读工作区内部文件；合法性规则 `decided_by_agent`（程序的提议不覆盖 Agent 的结构决定）；§5.1、§5.4、§6.8 相应修订 |
| 2026-09-27 | v1.27 | Q82 指标 2.3（标题层级允许整篇统一差一级）；页眉里的部分名排除、real_doc01 标注修订、标注变过的文档回放时重新计分；Q83 不加编号同级信号；Q84 `process` 的设计待确认 |
| 2026-09-27 | v1.28 | **Q85 工具包重新设计**：四个工具 `read_draft`、`view_source`、`edit_draft`、`submit_draft`（§5.1 重写）；证据存入状态；旧工具删除，`process` → `run_pipeline`（不给 Agent）；任务说明与 Skill 重写；探索模式退役 |
| 2026-09-27 | v1.29 | Q85 测量与两处修正：结构操作按结果判定层级（`apply_batch`）；区域证据可用于其中的块 |
| 2026-09-27 | v1.30 | **Q86 接口定型**（设计）：schema 为唯一来源、一套词表、去掉不起作用的字段与关系、改动记录、任务说明与适配层分开、最小循环验证 |
| 2026-09-27 | v1.31 | **Q86 实施与测量**：一套词表、请求模型是契约、`changes` 视图、自己的函数调用循环；循环与 Codex 质量相当，gpt-6-luna 也能完成；L0 617 |
| 2026-09-27 | v1.32 | **Q87、Q88 设计**（外部评审之后）：Agent 的角色（理解记录、凭证据的例外、局部重新解释、分诊先测量）；自己的循环的上下文与模型适配 |
| 2026-09-27 | v1.33 | **Q87、Q88 实施与测量**：理解记录、凭证据的例外、区域重读替换与拆开合并的表；自己的循环的适配器、缓存标记、少而成批的清理；看图默认不变；qwen3.6-plus 能用、标题较弱；L0 635 |
