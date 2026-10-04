# 视觉优先方案：本方工作会话启动提示词（2026-09-29）

复制下面整段作为新会话的第一条消息。它只管视觉优先方案的本方工作（U-other）；其他主题的交接见 [next_session_prompt.md](next_session_prompt.md)。

---

你在 /Users/xuyun/Projects/ParserX 工作，分支 main。这是一个把 PDF / DOCX（含扫描件与图片）转成适合大模型使用的 Markdown 的工具。设计与研发指导在 docs/redesign_guide.md，所有决策以它为准。

**这次会话的任务**：按本方执行计划 docs/v2_vision_first_execution.md 推进视觉优先方案，从 E0 开始，依次做 P0、V、C1，直到冻结本方候选。每个决策点（E0 的基线表、P0 报告、V 报告、C1 报告）先向用户汇报，按结论再继续。

## 1. 背景（先读，不要重新论证）

- 对标第二轮之后，公式问题先打了五个补丁（main 上的 01cb743 … 8ccb89a），又按区域整体重做（分支 `content-by-region`，冻结 run `eval_runs/2026-09-29_contentA_fixed_full`）。规则盘点（`eval_reports/2026-09-29_content_by_region.md` §3，37 条逐条去掉实测）表明：起作用的是把"看"交给页面读数、以及从 PDF 读出的排版事实；出问题的是看到一处错误后补的条件，它们互相牵制。用户判定这条路是死胡同（Q137）。
- 新方向：**视觉优先**。服务模型（服务层 VLM，现默认 gpt-6-luna；不是 Agent）看页面图，以文字层逐行为"可复制的原文"写出**完整分配**：每一行复制、被它写的块替换、或带理由放到一边；没有文字行锚点的视觉内容另行登记。程序只保证不变量（账目、引用、配平、无映射字形不静默删除），比对只作证据与保守默认值；**最终判断归 Agent**（Codex），不用逐字相同一类的机械规则限制它，靠准确的 Skill、提示词、证据与审计记录控制。
- **两个团队**按同一共同计划 docs/v2_vision_first_plan.md（提交 c5ed64f，原文不改）独立实现：本方 U-other 升级现有 ParserX；另一团队做 U-codex（同一计划的独立实现）与 A-codex（纯 Codex + 技能包，不用 ParserX、OCR、服务模型）。各自冻结后按共同验收口径 C0 交叉检查、吸收、再定最终版本。
- 另一团队的审核意见（R1–R6、C0）与他们的执行计划存档在 docs/audits/2026-09-29/。本方全部接受，处理与勘误写在执行计划 §1、§2。

## 2. 已定的事（用户，2026-09-29，不要再问）

1. 分支 `content-by-region` 不合并，作对照臂 R；补丁版冻结 run `eval_runs/2026-09-29_bench2f_fixed_full` 是对照臂 M。公式相似度指标与 paper_chn02 标注修订已迁到 main（5c7f828）。
2. 分流：有公式证据的文档整篇送服务模型；其余只送有证据的页（检测器看到图而页面没放图、无映射字形、本地识别与文字层不一致），不设新阈值。
3. 表格、标题保留现有路径：服务模型只标出范围、引用行，块类型只作标题步骤的证据。
4. 扫描引擎读数只加在有公式的文档上。
5. Agent 凭看图证据可以采用与原生文字层不同的数字，程序记录原值、新值、区域、证据、理由并在摘要列出，对错由独立评测判定（Q138）。实施时 `content/select.correct`、`tools/edit._adopt_region`、Skill、测试同步改；引用、来源、范围、历史不放宽（执行计划 §3.4）。
6. 结构化输出用参数配置，不写适配层（Q139）：DeepSeek、GLM 只有 `json_object`，schema 与样例写进提示；程序做三级校验（能解析、合 schema、满足不变量）加一次带错误的重问；空答复当失败重试。
7. 服务模型比较 gpt-6-luna、gpt-6-sol（参照，不称上限）、deepseek-flash、glm-5.3-flashx；推理强度按各自接受的值（执行计划 §3.5）。Agent 用 Codex，也可在自己的循环里换 DeepSeek 或 GLM。
8. 盲测先用现有测试数据，有必要时再挑新文档；结论称"开发验证"。patent01、paper01、text_pic02 已在开发中大量使用，改作回归集（Q137）。

## 3. 先读

1. docs/v2_vision_first_execution.md 全文（本方执行计划：勘误、R1–R6 的处理、执行设计、C0、顺序）；
2. docs/v2_vision_first_plan.md 全文（共同计划）；
3. docs/audits/2026-09-29/vision-first-plan-review-astra.md（审核意见与 C0 原文）；
4. eval_reports/2026-09-29_content_by_region.md §3、§4（规则盘点与未达成项）；
5. 指导 §2、§3、§5、§9.5、§11.5，§14 的 Q40、Q47、Q56、Q70、Q85–Q88、Q137–Q139；
6. eval_reports/2026-09-26_q70_formula_experiment.md、eval_reports/2026-09-28_model_comparison.md。

## 4. 先跑通（任一不过先处理）

1. `uv run parserx check`：扫描引擎与服务模型都要 ✓；四个服务模型逐个 `uv run parserx check --model <名字>`。
2. L0：`uv run pytest -q --ignore=tests/test_live_e2e.py`，预期 748 通过。
3. 回放对照臂 M：`uv run python scripts/regression_test.py --gt-dir ground_truth --gt-dir ground_truth_public --replay eval_runs/2026-09-29_bench2f_fixed_full`，PASS。

## 5. 从 E0 开始（执行计划 §4、§7）

1. **评测器先修**：补正负号、单位、上下标（不经 NFKC）、数值与对象或单元格的归属、整块遗漏的检查；每类用通用反例类别写测试（符号翻转、上下标互换、归属交换、整段缺失）。已知 `compute_key_content_errors` 对 `数值为 10`/`数值为 -10`、`$x^2$`/`$x_2$`、`甲为 10，乙为 20`/`乙为 10，甲为 20` 都记 0，但不要把这三个字符串写成特例。
2. **同口径基线表**：对标报告（`eval_runs/bench/report.md`）对用时、费用、关键错误是**求和**，其余求均值；当前报告里 ParserX 的列混用了 M 与 R 两个冻结 run。固定每臂的冻结 run、提交、标注哈希、评测器版本、输入哈希后重算：M、R 分成两列（`uv run parserx dev tool-eval run --tools parserx --parserx-run <run>`，需要时给 R 另起一个结果名），L 等工具的已有输出用同一评测器重算；逐文档、逐页；费用分实付、标价估算、未知（MinerU、PaddleOCR-VL 没有记录费用 = 未知）；回放用时单列。
3. **运行 manifest 模板**：候选提交或补丁、输入、标注、Skill、提示词、模型与推理参数、允许的工具、缓存模式、评测器、输出哈希。
4. 向用户汇报 E0 的基线表，再做 P0。

## 6. P0 要点（执行计划 §3、共同计划 §6.1）

- 页：从调参集取十来个难页——paper_chn02 的矩阵页与范数公式页（第 3、4 页）、paper_chn01 的多编号公式页（式 (2)(3)、式 (12)–(15)）与上横线字形页与跨栏页、ocr01 的图标卡片页（扫描）、receipt 的角标页——加两个干净的正文页。不用 patent01、paper01、text_pic02。
- 输入：页面图；文字层行号与行内 span 的原字符、框、字号、字体、基线、映射异常；上下标只作单独的候选；有公式的文档加扫描引擎条目。
- 输出：完整分配（`copy`、`write` + `replaces`、`table`、`aside` + 理由），外加没有文字行锚点的视觉内容。
- 代码放 `scripts/vision_first/`，结果放独立的 `eval_runs/` 目录，**不改流水线**；结果先存 JSON，不写进工作区。
- 指标分开报告：结构化输出（首次合法、schema 合法、重试后合法、机械修复、最终失败；截断、空回复、超时单列）；输入分配合法率（首次与补回分开）；原件内容覆盖率；转写正确率；误改与漏项；每页 token、用时、费用。整页对原件人工核对。
- 停止条件：改一轮提示词后，若所有服务模型的分配合法率仍低、或出现没被比对发现的数字改动，就停下向用户汇报，不铺开 V。

## 7. 约束

- **只用 uv**（`uv run python …`、`uv add …`）。
- **不加内容规则**。写任何条件或常数之前先问：人是不是看一眼就能判断？是的话交给服务模型（提出）与 Agent（定夺）。
- **两组独立**：冻结本方候选之前，不读取另一团队 worktree（`~/.codex/worktrees/vision-agent-comparison`）里的实现与运行结果；计划与审核类文件由用户转交。不读、不复制 `~/.codex` 下的任何凭据。
- **模型与服务商**：只用上面四个服务模型；不用 `.env` 的 `_C` 组；换别的先问用户。密钥在个人配置 `~/.config/parserx/config.yaml`，不写进文档或实验目录。
- **Agent 运行**：Codex（gpt-6-sol，medium），经 `scripts/agent_explore.py`（先 snapshot 干净的 HEAD，再 parse），实验根目录在仓库外。
- **费用口径**：按每篇（求和除以篇数）、实付与标价估算分开；缺测记未知，不当作免费。
- **非确定性**：主要候选每篇跑两次，完成状态不同或严重错误只在一次出现时补第三次，全部报告；回归检查不变量，提示词不变时靠缓存回放。
- **标注**：以原件为证据；改标注记入 docs/annotation_changes.md，所有臂统一重算。
- **冻结 run、缓存、标注、报告**不删；被取代的冻结 run 改名保留。

## 8. 每完成一步

- 报告 L0 的数字；回放 M 确认未改动的部分逐字节不变；
- 评测报告写入 eval_reports/，文件名含日期与步骤（如 `<日期>_vision_first_probe.md`）；
- 新的决策写入指导 §14，§15 加变更记录；
- 提交一次（不推送）；
- 向用户汇报，按结论继续。
