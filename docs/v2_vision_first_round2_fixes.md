# 视觉优先：第二轮修复说明（2026-10-01，分支 `vision-first`）

按第二轮规则（`/Users/xuyun/parserx-exp/round2/plan.md` §3、§5）修第一轮发现的问题。
每条写明问题、做法，以及为什么是通用的修法。代码里没有按文档名、页号或特定文字写的规则：
- 运行路径上唯一的文档名默认值（`v_run.py` 的 `DOCS`）已删除，`--docs` 改为必填；
- 实验脚本里剩下的文档名属于第一轮之前的 P0/S0 实验与计分脚本，不在运行路径上；
- `parserx/` 里的文档名只出现在注释中。

## 1. 修了什么

| # | 第一轮的问题 | 做法（通用在哪里） | 提交 |
|---|---|---|---|
| 1 | 图片形式的公式写成 LaTeX 后，图片的识读块没有撤下，公式出现两次 | 页面上放置的图片若在服务模型写出的某个公式的同一行带里、横向与它重叠或紧挨（间隔不超过一行高），就作为该公式的重复撤下。依据是几何关系，与文字无关 | b7a3006 |
| 2 | 段号（`[0020]`）被并进公式，有时丢失 | 公式块里 LaTeX 不含其字符的行单独成块。只有段号一行的公式（公式本身是图片），放到版面检测器在这行旁边找到的公式区域。公式裁图也取这块区域 | b7a3006 |
| 3 | 文字层里映射不到的字形（私用区）写成 `〔?〕` | 从本文档里服务模型重写过的行学习：对齐原行与重写后的文字，同一字形至少两处、全部读法一致才采用，再用到全文所有页 | b7a3006 |
| 4 | 表格块带 `copy` 部件时丢行，账目不符、导出被拒 | 表格网格外的行另成文字块；任何没有被放进块的行，都按原位置抄回（兜底） | b7a3006 |
| 5 | 命令行输出的 ASCII 表被压成一行 | 等宽字体排的行保留换行，成为代码块 | b7a3006 |
| 6 | `ENGINE_READINGS` 按文档名写死 | 删除。所有公式文档的公式页一视同仁地取扫描引擎读数，公式页按流水线自己的版面标签判断 | d550ec3 |
| 7 | Agent 按意思改原件（`No`→`N_0`） | **写入工具强制**：Agent 用别的字符替换原有字符时，若至少两份独立读数都与初稿一致、没有一份显示改后的写法，就拒绝修改（`as_printed`）。<br>• 独立读数：文字层、识别引擎、本地识读。<br>• 服务模型不算：它也按意思读，而且是拿着初稿读的。<br>• 比对方式：大小写照原样，写法差异（全半角、重音、LaTeX 与 HTML 标记）不算。<br>• 只读到一部分的读数（如公式的本地识读）不算任何一方。<br>• 第一轮回放：60 处替换中拒绝 7 处，5 处拒得对（`No`→`N_0` 4 处、原件错字"数字李生"被改 1 处），2 处是两个识别引擎都读错的 `μL`（读成 `pL`）。<br>• skill 写明照原件写、疑似笔误写进最终报告。 | b03ceb6 |
| 8 | 导出后代码块围栏损坏，九成正文渲染成代码 | **原因在本方渲染器**：Agent 在文字块里写的代码块，围栏原本单独成行，被渲染器的"段内折行合并"拼成一行。<br>• 现在文字块里按 CommonMark 识别的围栏段原样保留换行；<br>• 没闭合的围栏在本块末尾补上；<br>• 按字体收集的代码块，围栏比内容里最长的反引号串更长。<br>第一轮的状态重新渲染后：代码 19 行、标题 46 个（原来 496 行、6 个） | 4eca4dc |
| 9 | 扫描页左右栏交错 | 扫描页加阅读顺序的分歧信号：按版面切栏，模型的顺序回到已离开的栏、引擎的顺序不回时，保留引擎顺序，记 `order_disagreement` 待办交给 Agent。表单（行对齐的成对字段）不算。第一轮全部扫描页上只在交错的那一页触发 | 5d7e2a5 |
| 10 | 页眉混入正文（Agent 用 `include` 把页眉放回正文） | `include` 或改角色作用于被排除的页面装饰块时须有证据（规则 `furniture_without_evidence`）；扫描页适配器按版面标签和跨页重复排除页面装饰 | 5d7e2a5 |
| 11 | 实验脚本：没有标注的文档报错；Word 文档两次运行共用缓存 | 无标注的文档跳过计分；每个配置用自己的流水线缓存（只带 OCR 回答和本地派生数据，服务模型请求现发） | d550ec3 |
| 12 | Codex 容量失败没有退避重试 | 容量类错误：同一工作区开新会话（工作区就是检查点），最多 2 次，间隔 30 s 起；重试次数记入运行记录 | 381edcd |
| 13 | 审计误报（开发中发现） | 喂给 `./px` 的 here-document 是工具请求，不再当作 shell 路径扫描；喂给其他程序（如 `python3 -`）的仍扫描 | 1425163 |

**两边共用的老代码**：Word 目录条目被当成标题。两边各写了一个补丁：本方 ad27038，对方 ccd469bd。
- **对方补丁**只改 Word 样式那一步（`docx_styles`），另认中文样式名"目录 N"。
- **本方补丁**两步都改：Word 样式一步，以及按字体字号判标题的一步（`typography_titles`）。
- 31 篇里只有 real_doc01 有目录条目（6 条 `toc 1`）。第一轮里其中 2 条是按字体字号判成一级标题的（决策记录 `program:hierarchy.typography`），对方补丁管不到这一步。
- **建议**：两边都撤回自己的补丁，换用合并后的同一个补丁（本方两步 + 对方的"目录 N"和测试）。
  - 补丁文件：`/Users/xuyun/Projects/ParserX/.claude/worktrees/vision-first/eval_reports/2026-10-01_shared_patch/union/`；
  - 以 main 为底，两方测试 11 个通过；
  - 已核对：两边冻结的提交撤回各自补丁后都能干净地打上。

## 2. 没修的

- **paper_chn02 大矩阵 $A_{11,m}$、$A_{21,m}$ 末行一项错到第 3 列**（应在第 4 列）：服务模型的读法错误，现在没有任何信号能指出来。可作为增强：用文字层各片段的位置核对矩阵的行列（见第 5 节）。
- **服务模型读公式图的个别字母错误**（patent01 第 3 页式 (8) 的 `J_K` 写成 `J_k`）：
  - 识读块的读法与公式请求不一致时，已作为候选交给 Agent；
  - 开发自检中，Agent 改对了第 5、7 页，第 3 页看不清，没有改，待办保留。
- **两个识别引擎都读错的同一个字**（`μL` 读成 `pL`）：第 7 条的代价。Agent 会在最终报告里列出被拒绝的修改。
- **文字层的 U+FFFD 读不出**：服务模型也没写出来时，保留 `〔?〕`（paper_chn02 两处）。

## 3. 开发自检（不作为对比结果）

**V 初稿**，`eval_runs/2026-10-01_r2dev_vision_first`：
- patent01：公式图片全部撤下，只剩第 8 页两张数据表的识读；段号 99 个，与标注一致；第 8 页导出。
- paper_chn02：`〔?〕` 9 → 2。
- text_pic02：ASCII 表为代码块。
- 扫描研报页：保留引擎顺序，记 `order_disagreement`。

**V + Agent**（快照 `/Users/xuyun/parserx-exp/vision-first-r2dev/dev`）：
- omnidoc_book_zh_text_02：`No` 照原件；`μL` 的修改被拒，Agent 在报告里列出。
- paper01：代码块正常，标题 46 个。
- patent01：式 (6) 由 `d_t` 改对为 `d_k`；第 5、7 页式 (8) 为 `J_K`。

## 4. 交给中立会话（冻结后补提交号）

**目录**：所有命令都在 `/Users/xuyun/Projects/ParserX/.claude/worktrees/vision-first` 下运行。

**离线测试**：
```bash
uv run pytest -q --ignore=tests/test_live_e2e.py
```
应有 858 个通过。

**只跑初稿（V）**：
```bash
uv run python scripts/vision_first/v_run.py --run-dir eval_runs/<日期>_r2_vision_first --configs luna-medium-r1,luna-medium-r2 --scanned --live-pipeline --docs <31 篇，逗号分隔>
```
- 服务模型的请求都现发：页面分配、公式、流水线自己的看图。
- 扫描引擎的回答默认回放自冻结记录 `eval_runs/2026-09-29_bench2f_fixed_full/cache/raw/ocr`。
  - 要用中立会话统一的 OCR 记录：第一次运行前把它放到 `<run-dir>/pipeline_cache/raw/ocr`（与 `parserx` 的响应缓存同一格式）。有了这个目录就不再复制默认记录；路由、引擎读数和各配置的流水线都从它取 OCR 回答。
  - 记录里没有的公式页，第一次读后存进 `<run-dir>/engine_cache`。
- 结果：`<run-dir>/docs/<配置>/<文档>/<文档>.md`。

**带 Agent**：先做工具快照，每篇都跑，同时最多 2 个。
```bash
uv run python scripts/agent_explore.py --exp-root <实验目录> snapshot --round r2 --rules r2 --model gpt-6.1-sol --effort medium
<实验目录>/r2/_toolkit/venv/bin/python -P scripts/vision_first/c1_run.py --v-run eval_runs/<日期>_r2_vision_first --configuration luna-medium-r1 --doc <文档> --vision tool --effort medium --tag r1 --out <实验目录>/r2/_c1
```
- 第二次运行把配置换成 `luna-medium-r2`，标签换成 `--tag r2`。
- 每篇的 `record.json` 记录：Agent 前的待办数（`before.open`）、Agent 后的待办数、用时、tokens、重试次数、卫生审计。
- 结果：`<out>/<文档>.tool.medium.r<n>/out/<文档>.md`。

**配置**：
- `configs/regression.yaml`，密钥在 `~/.config/parserx/config.yaml`；
- 服务模型 gpt-6-luna medium；Agent 用 Codex gpt-6.1-sol medium，看图交给工具（`--vision tool`）。

## 5. 可做的增强（待定）

1. **矩阵行列核对**：原生页上用文字层各片段的位置，核对服务模型写的矩阵每一项所在的行列。不一致时挂待办，指明哪一项。
2. 其他由双方商定。
