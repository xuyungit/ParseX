# 视觉优先：本方版本小结（2026-09-30，分支 `vision-first`，提交 95b419b 起）

供与另一团队对比用。详细过程见 `docs/redesign_guide.md` v1.55–v1.72、`docs/annotation_changes.md`，以及 worktree 里的报告 `eval_reports/2026-09-30_vision_first_c1.md`、`eval_reports/2026-09-30_scanned_probe.md`。

## 1. 做法

像人整理文档一样分工：
- **服务模型**（gpt-6-luna）看页面图，把页面分配成块序列：
  - 原生页上，文字层的每一行恰好用一次，能照抄就照抄，抄不了才照图写；
  - 扫描页上，扫描引擎读出的每一块恰好用一次。
- **程序**负责记账、检查、比对，只挂信号，不替模型改写。
- **Agent**（Codex）在初稿上看图定夺。
- 只送有信号的页，没有信号的页用流水线的结果。

## 2. 主要修改

- **分配契约 v7**（`scripts/vision_first/p0_contract.py`、`s0_contract.py`）：
  - 块由几类部件组成：照抄、重写、表格、单格改写；
  - 行间公式另请一次，裁图单写 LaTeX；
  - 程序建出的表格给模型看，按格改；
  - 按字号和基线推出的上下标候选；
  - 照原件写，不改原文的错字。
- **扫描页接进 V**：按流水线待办和新信号 `engine_short` 送页（引擎某块读数不到框内本地识读的一半）；适配器 `s_adapter.py` 把分配结果写回工作区。
- **Agent 的权限：**
  - 改原生数字或字母、采用区域重读，从"拒绝"改为"记信号"；
  - 摘要列出"Agent 越过比对的改动"；
  - 新待办 `reading_disagreement`：照图重写而两份读数都不支持时，列出被替换的读数；
  - skill 和服务模型的提示都写明原件的错字照印的写。
- **页眉页脚页码**写进每页的页标记注释（方案 B），不打断正文。
- **评测器 2.5 → 2.11：** 只修正把写法差异算成错误的地方，每版都重算了所有组。改动包括：
  - 数学式统一成一种写法；
  - 撇号、上下标写法统一；
  - 单位按 LaTeX 写法读，只在一个表格格子里读；
  - 否定词只在数学式外读；
  - 表格里同一列上下相同的值，不论写成重复、留空还是合并，都只算一次。
- **标注：** 按原件修订，并定了一份按语义判断标题的标准，每条都附原件证据（`docs/annotation_changes.md`）。

## 3. 核心提升

以下都是评测器 2.11 的关键内容错误，越少越好。

**调参集 4 篇：**

| 文档 | 补丁版 M | Datalab | 视觉优先 V | V + Agent |
|---|---|---|---|---|
| paper_chn01 | 172 | 99 | 79 / 99 | **50–54** |
| paper_chn02 | 305 | 31 | 52 / 61 | 33（最好 31） |
| ocr01 | 10 | 7 | 10 | **6–7** |
| receipt | 6 | 5 | 4 | **4** |

**调参集其余 24 篇：**
- V + Agent 前后：关键内容错误 234 → 133，标题 F1 0.69 → 0.91。
- 其中各组都有输出的 14 篇：

  | 组 | 关键内容错误 |
  |---|---|
  | 补丁版 M | 43 |
  | LlamaParse | 118 |
  | 混合（上一代 Codex） | 35 |
  | Datalab | 67 |
  | **V + Agent** | **31** |

**12 篇含扫描页的文档：**
- 初稿 227 → 扫描页进 V 142 → 加 Agent 119。
- 与 Datalab 比（7 篇两边都有结果）：

  | | 关键内容错误 | 标题 F1 | 表格 F1 |
  |---|---|---|---|
  | V + Agent | 29 | 0.78 | 普遍更高（如 unseen_pdf_tables01 0.97） |
  | Datalab | 35 | 0.53 | 如 unseen_pdf_tables01 0.58 |

**费用与时间：**
- 服务模型每页约 $0.003–0.006，扫描页约 27 秒一页；
- Agent 每篇 2.6–5.5 分钟，论文类 7–16 分钟。

## 4. 重点开发工作

- 完整分配的契约、检查、修复与渲染，原生页和扫描页共用一套；有探针 P0，扫描页也有一轮探针。
- V 运行器（`v_run.py`）：按信号送页，适配器把分配写回工作区（原生页 `v_adapter.py`，扫描页 `s_adapter.py`）。
- C1 运行器（`c1_run.py`）：在工具快照上跑 Agent，完成后做卫生审计和工作区完整性检查。
- 产品代码：
  - Agent 权限改为信号，并列入摘要；
  - 待办种类 `reading_disagreement`；
  - 页标记；
  - 评测器 2.5–2.11。

## 5. 怎么用

**只用产品**（流水线 + Agent，不含 V 的分配步骤）：

```
uv run parserx parse input.pdf -o out/ --agent codex --report
```

`--runtime fixed` 或 `--no-agent` 只跑流水线。

**完整流程**（V + Agent；V 目前还在实验脚本里，没有进产品运行时）：

1. V：
   ```
   uv run python scripts/vision_first/v_run.py --run-dir eval_runs/<run> --configs luna-medium-r1 --docs <doc>[,…] --scanned
   ```
   `--scanned-only` 表示只送扫描页。
2. 工具快照：
   ```
   uv run python scripts/agent_explore.py --exp-root <root> snapshot --round <r> --rules r2 --model gpt-6.1-sol --effort medium
   ```
3. Agent：
   ```
   <root>/<r>/_toolkit/venv/bin/python -P scripts/vision_first/c1_run.py --v-run eval_runs/<run> --configuration luna-medium-r1 --doc <doc> --vision tool --effort medium --tag r1 --out <root>/<r>/_c1
   ```
4. 评分：
   ```
   uv run python scripts/vision_first/v_score.py --run-dir eval_runs/<run>
   uv run python scripts/vision_first/c1_score.py --runs <root>/<r>/_c1 --out eval_runs/<scores>
   ```

## 6. 测试

- **离线单元测试：** `uv run pytest --ignore=tests/test_live_e2e.py`，853 个通过，契约、适配器、信号、评测器都先写测试再改。
- **探针：**
  - P0：10 页 × 12 个模型配置；
  - 扫描页：8 个难页 × 2 个模型 × 2 次。
- **V：** 调参集 28 篇，主要配置各跑两次。
- **C1（Agent）：**
  - 4 篇 × 两种看图方式 × 两种思考强度 × 2 次；
  - 另 24 篇各 1 次；
  - 12 篇扫描件各 1 次。
  - 每次都做卫生审计和工作区完整性检查，全部通过。
- **结果留档：** 运行都有缓存，可离线重放；清单 manifest 记录模型、提示、输入的哈希。
- **没做的：**
  - 盲测文档；
  - 回归集原生页的 V + Agent（patent01 的 71 个错误在原生页）；
  - 用时优化。

## 7. 配置

- **服务模型：** gpt-6-luna，reasoning medium，输出上限 131 072，超时 1800 秒。第二候选 deepseek-flash medium，更贵、更慢，也不比 luna 稳。
- **Agent：** Codex gpt-6.1-sol，思考强度 medium，委托服务模型看图（`vision tool`）；high 作为质量选项。
- **扫描引擎：** PaddleOCR-VL-1.6。本地识读：RapidOCR 3.7.0。版面检测：pp_doc_layoutv3（rapid-layout）。
- **图像分辨率：** 页面图 150 dpi，公式裁图 300 dpi。
- **配置文件：** `configs/regression.yaml`。评测器 METRIC_VERSION 2.11。
- **页眉页脚：** `output.page_furniture=comment`（写进页标记）。
