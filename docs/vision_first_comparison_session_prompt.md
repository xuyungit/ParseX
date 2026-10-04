# 新会话提示词：两个团队版本的对比运行（2026-10）

> 把本文件整篇交给新会话。新会话没有之前的上下文，下面把需要的都写在这里。

## 0. 你的角色和边界

你是**中立的对比执行者**：
- 用两个团队各自**冻结**的版本，在**同一批原件**上运行；
- 用两边的评测口径统一计分；
- 对照原件核对，写出对比报告。

边界：
- **不改代码：** 不改两边的代码、提示词、Skill、配置和标注。发现问题记下来，不修。
- **不提交、不推送：** 不在两边的 worktree 里提交，不 `git push`。只有用各自的运行脚本生成新的运行目录是允许的。
- **结果放中立目录：** 所有结果和报告都放在中立目录 `~/parserx-exp/comparison-<日期>/`，日期用开始当天，例如 `2026-10-01`。
- **Python：** 一律用 `uv`，不往系统 Python 装东西。
- **密钥：**
  - 服务密钥在 `~/.config/parserx/config.yaml`，由程序自己读取；不要把密钥写进任何文档或目录。
  - 不要读 `~/.codex` 下的登录和配置文件（`auth.json`、`config.toml` 等）。
- **模型：** 服务模型只用 gpt-6-luna，Agent 只用 Codex gpt-6.1-sol。不启用任何新模型或服务商；要用先问用户。
- **读对方目录：** 对方 worktree 在 `~/.codex/worktrees/…` 下，读取它可能被权限检查拦下。被拦时请用户放行，不要绕过。
- **删除：** 不删除任何已有结果、缓存、标注和报告。
- **和用户交流：** 用中文，用平实的说法（"对比组""方案"，不要"臂"之类的译词），给出绝对路径。

## 1. 背景

- ParserX 把 PDF、Word、扫描件转成忠实、可读的 Markdown。
- 两个团队按同一份共同计划（仓库里的 `docs/v2_vision_first_plan.md`）各自独立改进，现在做头对头比较。
- 共同验收口径是 C0，见 `docs/audits/2026-09-29/vision-first-plan-review-astra.md` §5，要点：
  - 同一原件；
  - 每个主要候选各独立跑两次；两次完成状态不同，或某个严重错误只在一次里出现，就补第三次；
  - 失败计入分母；
  - 逐文档、逐页报告，质量先于成本；
  - 成本分层记录；
  - 口径有分歧时单列结果，不混成一个排名。

## 2. 两边的冻结版本

| | 本方（视觉优先，Claude 会话） | 对方（Codex 会话） |
|---|---|---|
| 目录 | `/Users/xuyun/Projects/ParserX/.claude/worktrees/vision-first` | `/Users/xuyun/.codex/worktrees/vision-agent-comparison/ParserX` |
| 分支 | `vision-first` | `codex/vision-agent-comparison` |
| 冻结代码 | `24fc652`（其后只有文档提交） | `1ee17e9` |
| 做法 | 服务模型看页面图做"完整分配"：原生页文字层每行、扫描页引擎每块恰好用一次，只送有信号的页（V）；再由 Codex Agent 看图定夺（V + Agent） | 增强流水线（碎片表格重建、跨栏跨页归属、乱码和公式处理），加上按需的 Codex 审阅：默认只在有待办时介入 |
| 小结 | 本方目录下 `docs/v2_vision_first_summary.md` | 对方目录下 `docs/audits/2026-09-30/small-hybrid-comparison-codex.md` 与 `workspace-cleanup-codex.md` |
| 离线测试 | 853 通过 | 845 通过 |

**配对比较：**
- 对方"只跑流水线"（`--no-agent`）对本方 V（不带 Agent）；
- 对方"流水线加 Codex"对本方 V + Agent；
- 另附旧的冻结对照组（补丁版 M、规则版 R、LlamaParse、Datalab），只读，在 `/Users/xuyun/Projects/ParserX/eval_runs/bench/<组名>/<文档>/output.md`，不是每篇都有。

**双方配置：**
- Agent：Codex gpt-6.1-sol，medium。本方委托服务模型看图（`--vision tool`）；对方读取偏好为 `auto`。
- 工作模型：gpt-6-luna。
- 扫描引擎：PaddleOCR-VL；本地识读：RapidOCR；版面检测：pp_doc_layoutv3。
- 本方的 Agent 每篇都跑；对方只在有待办时介入。这是方案差异，照实记录，不去对齐。

## 3. 文档集

标注共 33 篇：
- 主目录 `/Users/xuyun/Projects/ParserX/ground_truth/` 23 篇；
- `ground_truth_public/` 10 篇（omnidoc 单页、basic_report、header_footer_cleanup）；
- `ground_truth_unseen/` 是空的，目前没有新的盲测文档。

分两组，分开报告：

- **留出组**，结论以它为重：`patent01`、`paper01`、`text_pic02`（共同计划的隔离集），以及 `chn_doc01`、`real_doc02`（本方从未用于开发）。对方是否用这几篇调过，要查对方的记录，写进报告。
- **开发组**，两边都可能调过：
  - `paper_chn01`、`paper_chn02`、`ocr01`、`receipt`；
  - `basic_report`、`deepseek`、`header_footer_cleanup`、`ocr_scan_jtg3362`、`pdf_text01_tables`、`real_doc01`、`simple_doc01`、`text_code_block`、`text_report01`、`text_table01`、`text_table_libreoffice`、`text_table_word`、`unseen_pdf_tables01`、`unseen_scan_form01`、`unseen_word_spec01`、`val_word_template01`；
  - 8 篇 `omnidoc_*`。

**原件：**
- 两边都读主目录里同一份原件（`input.pdf`、`input.docx` 等），只读。
- 先在中立目录写 `inputs.json`：文档名、原件绝对路径、sha256、页数、类型（原生 PDF、扫描、Word）。
- 运行前后各核对一次 sha256。

**新文档：** 若用户要加新的盲测文档，放在主目录 `ground_truth_unseen/<名字>/input.*`。标注 `expected.md` 另存，交给评分脚本，不让 Agent 看到。

## 4. 同时运行时互不干扰

**写入分开：**

| 方 | 写到哪里 |
|---|---|
| 本方 V | 本方目录的 `eval_runs/<日期>_cmp_vision_first`。本方 `eval_runs` 链接到主目录的 `eval_runs`，名字必须唯一 |
| 本方 Agent | `~/parserx-exp/comparison-<日期>/ours/` |
| 对方 | `~/parserx-exp/comparison-<日期>/codex/{fixed,hybrid}/r{1,2}/<文档>/` |

- 谁都不写 `eval_runs/bench`，不写 `~/parserx-exp/vision-first-*`，也不写对方原有的实验目录。
- 最终 Markdown 与摘要复制到中立目录：
  - `outputs/<方案>/<模式>/r<n>/<文档>.md`
  - `outputs/<方案>/<模式>/r<n>/<文档>.json`

**缓存：**
- 本方：
  - 服务模型回答在运行目录的 `cache/`；
  - 流水线的扫描引擎回答在 `pipeline_cache/`，复制自冻结的补丁版运行，已有回答直接回放；
  - 派生读数（本地识读、版面检测）在各自目录的 `.parserx_cache` 和 `~/.cache/parserx`。派生读数按内容寻址，原子写入，两边共用是安全的。
- 运行期间谁都不清缓存。
- "统一缓存条件"指：服务模型和 Agent 的请求两边都现发，不复用旧回答；扫描引擎的回答尽量都来自同一份冻结记录。
- 如果对方的流水线现发了引擎请求，而本方是回放，就记下来：这会影响时间和费用，一般不影响质量比较。

**并发：**
- 两边用同一个服务账号和同一个 Codex 账号。
- Codex 同时最多 4 个会话，每方 2 个。
- 本方服务模型每个配置并发 4，是脚本默认值。
- 遇到限流或超时，按各自工具的重试规则处理，并记入失败清单，不悄悄重跑换结果。

**时间：**
- 同时运行会拖慢彼此，墙钟时间只作参考，报告里注明"并行运行"。
- 要比较时间，另选 3–5 篇，各自单独、冷缓存再跑一次。

**不泄露答案：**
- 交给 Agent 的任务里不出现标注路径、分数或另一方的输出。
- 本方 Agent 运行自带卫生审计，读仓库或 V 运行目录会命中。
- 对方按其自己的隔离办法。

## 5. 本方怎么跑

每篇两次：V 的两个配置 `luna-medium-r1`、`luna-medium-r2`，各接一次 Agent（`r1`、`r2`）。命令都在本方目录下运行：`cd /Users/xuyun/Projects/ParserX/.claude/worktrees/vision-first`。

1. **核对：**
   ```bash
   git log -1 --format=%h
   git status --short | grep -v '^??'
   uv run pytest -q --ignore=tests/test_live_e2e.py
   ```
   `git log` 在 `24fc652` 之后应只有文档提交；`git status` 应无输出；测试应有 853 个通过。
2. **V：** 扫描页也按信号送。
   ```bash
   uv run python scripts/vision_first/v_run.py --run-dir eval_runs/<日期>_cmp_vision_first --configs luna-medium-r1,luna-medium-r2 --scanned --docs <文档,逗号分隔>
   ```
   - `chn_doc01`、`real_doc02` 不在冻结的补丁版缓存里，用同一个 `--run-dir` 另跑一次，并加 `--live-pipeline`。
   - Word 文档没有页面图，V 只跑流水线。
   - 结果在 `<run-dir>/docs/<配置>/<文档>/<文档>.md`。
3. **Agent：** 先做工具快照，再跑。
   ```bash
   uv run python scripts/agent_explore.py --exp-root ~/parserx-exp/comparison-<日期>/ours snapshot --round cmp --rules r2 --model gpt-6.1-sol --effort medium
   ~/parserx-exp/comparison-<日期>/ours/cmp/_toolkit/venv/bin/python -P scripts/vision_first/c1_run.py --v-run eval_runs/<日期>_cmp_vision_first --configuration luna-medium-r1 --doc <文档> --vision tool --effort medium --tag r1 --out ~/parserx-exp/comparison-<日期>/ours/cmp/_c1
   ```
   - 第二次运行把配置换成 `luna-medium-r2`，标签换成 `--tag r2`。
   - 同时最多 2 个。
   - 结果在 `<out>/<文档>.tool.medium.r<n>/out/<文档>.md`；`record.json` 里有用时、tokens 和审计。
4. **本方口径的分数：**
   ```bash
   uv run python scripts/vision_first/v_score.py --run-dir eval_runs/<日期>_cmp_vision_first
   uv run python scripts/vision_first/c1_score.py --runs ~/parserx-exp/comparison-<日期>/ours/cmp/_c1 --out eval_runs/<日期>_cmp_c1_vision_first
   ```

## 6. 对方怎么跑

以对方小结为准，命令都在对方目录下运行：`cd /Users/xuyun/.codex/worktrees/vision-agent-comparison/ParserX`。

1. **核对：**
   ```bash
   git log -1 --format=%h
   ```
   应为 `1ee17e9`。再按对方文档跑一遍离线测试，应有 845 个通过。
2. **只跑流水线：**
   ```bash
   uv run parserx parse <原件绝对路径> -o ~/parserx-exp/comparison-<日期>/codex/fixed/r1/<文档> --no-agent --report --sidecar
   ```
3. **流水线加 Codex：**
   ```bash
   uv run parserx parse <原件绝对路径> -o ~/parserx-exp/comparison-<日期>/codex/hybrid/r1/<文档> --report --sidecar --keep-work
   ```
   同时最多 2 个。
4. **第二次运行：** 输出到 `r2`。
5. **不清楚的地方：** 对方的配置文件、缓存目录、费用记录的位置，先读对方文档；仍不清楚就问用户，不要猜。

## 7. 统一计分

两边的评测器和标注都各自改过：
- 本方评测器 2.11，修正了多处把写法差异算成错误的地方；标注按原件修订过，记录在 `docs/annotation_changes.md`。
- 对方的改动见对方文档。

所以每份输出**用两边的口径各算一遍**：

- 在中立目录写一个计分脚本 `scores/score.py`：
  - 读 `outputs/` 下的全部 Markdown；
  - 用 `parserx.eval.metrics.evaluate_markdown` 对该方目录下的标注（`ground_truth*/<文档>/expected.md`）计分；
  - 写出逐文档的 JSON。
- 分别在两个目录里各运行一次，得到两套分数：
  ```bash
  cd <该方目录> && uv run python ~/parserx-exp/comparison-<日期>/scores/score.py ...
  ```
- 对照组（M、R、LlamaParse、Datalab）一并计分。

**报告每篇、每次的：**
- 完成状态；
- 关键内容错误（按数字、单位、否定、上下标等分类），char_f1，表格 F1，标题 F1，公式相似度；
- 未解决的待办数；
- 费用（服务实付、Agent tokens、未知的写"未知"）；
- 时间（注明并行）。

**口径不一致时：**
- 两套分数结论相反的地方，以原件为准：打开页面图核对，写明谁对。
- 标注不一致的地方并列列出。本会话不改标注；需要改先问用户。
- 严重错误逐条对照原件：丢内容、数字改错、按意思"纠正"了原件。格式上的改善不能抵消严重错误。

## 8. 交付

中立目录下：

- `manifest.json`：两边的提交、配置、模型设置、命令、原件与标注及评测器的哈希、全部输出的哈希、缓存使用情况。
- `report.md`，中文、简洁：
  1. 覆盖范围与失败清单；
  2. 留出组、开发组分开，两套口径各自的汇总表；
  3. 逐文档谁好谁坏及原因（对照原件）；
  4. 两次运行之间的差异；
  5. 费用与时间；
  6. 各方的已知问题：对方自述 Agent 时延、Word 标题评分回归、复杂公式语义错误；本方见 `docs/v2_vision_first_summary.md` 的"没做的"；
  7. 不能支持的结论。

## 9. 开始之前先问用户

1. 跑哪些文档：建议先跑留出组 5 篇，再跑开发组 28 篇；是否另加盲测文档。
2. 每方每种模式各跑两次（C0），是否同意；时间预算多少。
3. 是否允许读取对方目录、运行对方命令。对方目录在 `~/.codex` 下，可能要用户放行。
4. 计分以哪套口径为主，还是两套并列。建议并列。
