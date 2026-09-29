# 视觉优先方案：P0 会话启动提示词（2026-09-29）

复制下面整段作为新会话的第一条消息。上一会话（[启动提示词](vision_first_session_prompt.md)）完成了 E0；本会话从 P0 开始。

---

你在 /Users/xuyun/Projects/ParserX 工作。**第一步先进入已有的 worktree**：`/Users/xuyun/Projects/ParserX/.claude/worktrees/vision-first`（分支 `vision-first`，基于 main 的 d28ed8b，已有 E0 的提交 a65e8bd、ec9d910 及其后的文档提交；未合并、未推送）。用 EnterWorktree 的 `path` 进入，不要新建 worktree（新建会从落后几个月的 origin 开分支），也不要在 main 的检出里改代码。这是一个把 PDF / DOCX（含扫描件与图片）转成适合大模型使用的 Markdown 的工具，设计与研发指导在 docs/redesign_guide.md，所有决策以它为准。

**这次会话的任务**：按本方执行计划 docs/v2_vision_first_execution.md 做 **P0 探针**（§3 的输入、契约、指标，§7 的顺序；共同计划 docs/v2_vision_first_plan.md §6.1），写 P0 报告向用户汇报，按结论再做 V。

## 1. 已完成与已定（不要重做、不要再问）

- **E0 已完成**（报告在 worktree 的 `eval_reports/2026-09-29_vision_first_e0.md`，gitignore 的本地文件）：
  - 评测器 2.5（`parserx/eval/key_content.py`、`omission.py`）：关键内容加正负号、上下标（不经 NFKC；Unicode、HTML、LaTeX 同一写法）、归属、无映射字形，单位带指数；整块遗漏、遗漏段、多出段。
  - 逐页打分（`parserx/eval/pages.py`：`page_texts` 由原件的文字层加本地读数给每页文字，`split_pages` 把任一 Markdown 按页切开，标注与各臂用同一切法）。
  - 同口径基线表 `eval_runs/bench/`（`report.md`、`pages.md`、`scores.json`、`page_scores.json`、`e0_manifest.json`）：M = `parserx-fixed`（冻结 run `2026-09-29_bench2f_fixed_full`），R = `parserx-fixed-R`（`2026-09-29_contentA_fixed_full`），外部工具与混合方案的已有输出按 2.5 重算；混合列来自 4 个提交，只作参考。
  - 运行 manifest 模板 `scripts/vision_first/manifest.py`（`run_manifest`），P0 起每个结果目录旁写一份。
- **用户已定**：启动提示词 [vision_first_session_prompt.md](vision_first_session_prompt.md) §2 的八条；指导 §14 的 Q137–Q140。Q140（2026-09-29）：评测器 2.5 的口径照用；**P0 的十页**：

| 页 | 看什么 |
|---|---|
| paper_chn01 第 2 页 | 多编号公式 (2)(3)、上横线字形"珚" |
| paper_chn01 第 3 页 | 式 (12)–(15) 一块四个编号、"⁻¹ （12）"、上横线 |
| paper_chn01 第 7 页 | 跨栏的参考文献、私用区字形（U+E5D2） |
| paper_chn02 第 1 页 | 首页：R 的假下标的来源（`第 50 卷`、`2015 年`）、行内私用区连字符 |
| paper_chn02 第 3 页 | 两个矩阵、范数公式 |
| paper_chn02 第 4 页 | 范数公式（M 在这页一个公式也没配上） |
| ocr01 第 1 页 | 扫描的图标卡片（白片、粉片） |
| receipt 第 2 页 | 角标（"Save 3% … .¹"） |
| paper_chn02 第 5 页 | 干净的正文页（无行间公式） |
| pdf_text01_tables 第 1 页 | 干净的原生正文与表格页 |

## 2. 先读

1. docs/v2_vision_first_execution.md 全文（尤其 §1 勘误、§2 R1–R6 的处理、§3 执行设计、§4 C0）；
2. docs/v2_vision_first_plan.md 的 §3、§5.1、§6.1；
3. E0 报告（上面的路径）；
4. docs/audits/2026-09-29/vision-first-plan-review-astra.md 的 R1、R2 与 §4；
5. 指导 §3.3、§5.1、§6.3、§6.4、§8.2，§14 的 Q56、Q70、Q105、Q137–Q140。

## 3. 先跑通（任一不过先处理）

1. `uv run parserx check`，再对四个服务模型逐个 `uv run parserx check --model <名字>`：gpt-6-luna、gpt-6-sol、deepseek-flash、glm-5.3-flashx，都应是 "the entry matches the model"（E0 已给 gpt-6-sol 的条目补了 `send_temperature: false` 与 `efforts`）。
2. L0：`uv run pytest -q --ignore=tests/test_live_e2e.py`，预期 794 通过。
3. 回放 M：`uv run python scripts/regression_test.py --gt-dir ground_truth --gt-dir ground_truth_public --replay /Users/xuyun/Projects/ParserX/eval_runs/2026-09-29_bench2f_fixed_full`，PASS（冻结 run 是指标 2.4 打的分，回放只比输出，会有黄色提示，属正常）。

## 4. P0 怎么做（执行计划 §3；这里只列实现上的提示）

- **代码**放 `scripts/vision_first/`，**不改流水线**；结果先存 JSON，放独立的 `eval_runs/<日期>_p0_…/` 目录（带 manifest 与该目录自己的响应缓存，提示词不变时能离线回放）；不写进工作区。
- **输入**（§3.1）：
  - 页面图：沿用现有渲染与旋转约定（`parserx/content/scan.py` 的 `render_page_at`、`parserx/tools/imaging.py`、`parserx/ir/rotation.py`），不另起坐标系。
  - 文字层逐行：行号、行内 span 的原字符、框、字号、字体、基线、映射异常（私用区、U+FFFD、Symbol 字体部件）。现有抽取见 `parserx/content/pdf_native.py`（`_lines`、`_line_typography`）；PyMuPDF 的 rawdict 给逐字形信息。
  - 上下标候选单独一栏：只按字号与基线（R 的"小一号就算"已证明是错的，不要带）。
  - 有公式的文档（paper_chn01、paper_chn02）加扫描引擎条目（框与 LaTeX）：M 的冻结 run 缓存里有这两篇的整页读数，用现有的引擎客户端与缓存取（`parserx/content/scan.py`、`parserx/tools/formulas.py` 里怎么取的）。检测器区域只作提示。
- **输出契约**（§3.2）：文字层每行恰好一个去处（`copy`、被 `write` 的 `replaces` 声明、`table`、`aside` + 理由）；没有文字行锚点的视觉内容另列（以区域、引擎条目或图片为来源）；`aside` 与图内文字写明最终去处。
- **结构化输出**（Q139）：`models.<名字>.structured_output` 定方式；弱于 json_schema 时服务层自动把 schema 写进提示、剥掉围栏（`parserx/services/llm.py` 的 `_structured_output_kwargs`、`_schema_note`）。提示里同时给 schema 与一个简短 JSON 样例。**三级校验**（能解析、合 schema、满足不变量）加**一次带错误信息的重问**要在探针里写：现有网关（`parserx/scheduling/gateway.py` 的 `call(parse=…)`）只会把 `UnparseableResponse` 原样重发一次、不带错误；空答复在那里被当作不能解析（`parserx/tools/vlm_tasks.py` 的 `_json`）。截断（finish_reason length）、空回复、超时单列。
- **模型与强度**（§3.5）：luna low、medium 各跑两次；gpt-6-sol low、medium（参照，不称上限）；deepseek-flash low、medium（输出下限 8192 已在条目里）；glm-5.3-flashx low、high。十页 × 十种配置约一百次请求（加重问），sol 占费用的大头，事先估算并在报告里分层列出。
- **指标**（§3.3，分开报告）：结构化输出五级；输入分配合法率（首次与程序补回分开）；原件内容覆盖率；转写正确率；具体的误改与漏项；每页 token、用时、费用。与 M、R、L 在**同一页**上配对比较：`split_pages` 切出该页的标注与各臂输出，用 `evaluate_markdown` 打分（`eval_runs/bench/page_scores.json` 已有各臂的逐页分）。
- **整页核对原件**：`copy`、`write`、`aside`、表格、图、脚注、阅读顺序都看。页面图可以直接用 Read 看 PNG；另做一个并排的核对页给用户（页面图、分配结果按类着色、与 M/R 的差异）。失败按"服务模型能力不足、图片不可读、契约不合适、分配错误、原文证据错误、评测误判"分类，不以追加文档特例解决。
- **停止条件**：改一轮提示词后，若所有服务模型的分配合法率仍低，或出现没被比对发现的数字改动，就停下向用户汇报，不铺开 V。
- **报告**：`eval_reports/<日期>_vision_first_probe.md`。

## 5. 操作上的注意（上一会话踩过的）

- **沙箱**：带复杂 heredoc 的命令、字符串里含 `eval` 字样的 grep、带变量循环调 python 的命令会被拒。把脚本写成文件（放 scratchpad）再 `uv run python <文件>`；grep 的 `--include` 要加引号（zsh）。Write 工具不能写 main 检出里的路径：报告写 worktree 的 `eval_reports/`（gitignore），合并时再复制到 main。
- **数据**：gitignore 的数据从 main 检出软链进 worktree（`ground_truth/` 各文档、`ground_truth_public/` 各文档、`ground_truth_unseen`、`eval_runs`、`.parserx_cache`、`.env`、`reports`）。`git status` 会把根目录的四个软链列为未跟踪：**提交时逐个写路径，不要在根目录 `git add -A`**。`eval_runs/` 实际在 main 检出里，写进去的东西在 worktree 删掉后仍在。
- 本地读数（RapidOCR）的派生缓存在 `~/.cache/parserx`；`uv run parserx dev tool-eval score` 连逐页约 2–3 分钟；`tool-eval run --parserx-name` 给第二个冻结 run 另起结果名。

## 6. 约束（同上一会话，不变）

- 只用 uv；**不加内容规则**——写任何条件或常数之前先问：人是不是看一眼就能判断？是的话交给服务模型（提出）与 Agent（定夺）。
- **两组独立**：冻结本方候选之前，不读取另一团队 worktree（`~/.codex/worktrees/vision-agent-comparison`）里的实现与运行结果；不读、不复制 `~/.codex` 下的任何凭据。
- **模型**：只用上面四个服务模型；不用 `.env` 的 `_C` 组；换别的先问用户。密钥在 `~/.config/parserx/config.yaml`，不写进文档或实验目录。Agent（到 C1 才用）是 Codex（gpt-6-sol，medium），经 `scripts/agent_explore.py`。
- **费用**按每篇或每页，实付、标价估算、未知分开；缺测记未知。**非确定性**：主要候选跑两次，完成状态不同或严重错误只在一次出现时补第三次，全部报告。
- **标注**以原件为证据，改标注记入 docs/annotation_changes.md，所有臂统一重算。冻结 run、缓存、标注、报告不删；被取代的改名保留。

## 7. 每完成一步

报告 L0 的数字；回放 M 确认未改动的部分逐字节不变；评测报告写入 `eval_reports/`（文件名含日期与步骤）；新的决策写入指导 §14，§15 加变更记录；提交一次（不推送）；向用户汇报，按结论继续。
