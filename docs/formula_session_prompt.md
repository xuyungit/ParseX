# 公式专题会话启动提示词（2026-09-29）

复制下面整段作为新会话的第一条消息。它只管公式；其他主题的交接见 [next_session_prompt.md](next_session_prompt.md)。

---

你在 /Users/xuyun/Projects/ParserX 工作。这是一个把 PDF / DOCX（含扫描件与图片）转成适合大模型使用的 Markdown 的工具。设计与研发指导在 docs/redesign_guide.md，所有决策以它为准。

**这次会话的任务**：把公式的处理作为一个整体重新设计。上一轮会话在对标第二轮里遇到一连串公式问题，逐个打了补丁。用户的判断是已经钻进了死胡同：规则越修越细、越来越多，反而更危险。所以这次先整体分析、提出方案、和用户讨论，再动代码。

补丁可以保留、改写，也可以撤掉，按新设计定，不要默认它们是对的。

## 1. 现在的机制（Q70 第二版，`parserx/tools/formulas.py`）

1. 版面检测器在原生页上标出行间或行内公式时，整页交扫描引擎（PaddleOCR-VL）批量读，得到带 LaTeX 的读数。
2. 读数的块和原生文字层的块按位置配对，组成"段落"。块的中心落在对方块里就算同一段。只处理读数里含数学的段落。
3. 守恒规则：读数保留了文字层的每个字母和数字，就采用读数；文字层的块标为 duplicate。
4. 不守恒时交"编辑"：服务模型 gpt-6-luna，看这一段的截图，同时拿到两份读数（A 是文字层，B 是扫描读数），写出一份。编辑的版本守恒就采用。
5. 仍不守恒：保留文字层，列为待办 `formula_candidate`，交给 Agent。
6. Agent 有四个工具：`read_draft`、`view_source`、`edit_draft`、`submit_draft`。修改都是 `edit_draft` 里的操作：`replace_text`、`insert_text`、`set_cells`、`adopt` / `unadopt`（区域重读替换，Q87）、`dismiss`、`note`，以及结构操作。

扫描页整页识别，DOCX 的公式来自 OMML（Q9），都不走上面这条路。

## 2. 第二轮对标暴露的问题（证据都在 paper_chn01、paper_chn02、patent01）

1. **矩阵输出成碎片**（paper_chn02 第 3 页，A₁₁、A₂₁、diag 式）。
   - 文字层把一个排版好的公式切成很多块（这个矩阵 20 块），括号是 Symbol 字体的私用区部件（U+F8EB–F8F8）。
   - 扫描读数把 5.9 磅的上标 l（表示"左侧"）全读成了 1，按守恒被拒。编辑照抄读数，同样被拒。
   - 退路"保留文字层"对普通段落是安全的，对公式却是最差的结果：完全不可读。
2. **Agent 没接住**。
   - 它不直接看图，而是按块截图，再请服务模型看图回答。待办指向碎片里的第一块，截图只有矩阵最上面两行。
   - 它也没有办法改写跨多块的一段：`replace_text` 只改一块，接受门也不许在一块里加进别块的数字。
   - 最后它关闭了待办。
3. **公式编号另起一行**。
   - 扫描引擎把"(12)"作为单独一项（formula_number，`layout/labels.py` 映射为 TEXT），文字层也单独成块，渲染时各占一段。
   - 一个公式块里有几个编号时（aligned 里的几个方程），不知道哪行对应哪个编号。
   - 还有编号和截下的上标连在一块的情况："-1 （12）"。
4. **零碎的 x′、′**：文字层把公式上方的撇号单独成块，扫描读数的公式框没盖住它们，于是没归进段落，留在了输出里。
5. **跨栏重复**（paper_chn01）：扫描引擎把被分栏切开的段落整段读在第 1 栏的位置，第 2 栏栏首的文字层块没配上任何读数，于是输出了两遍。
6. **按个数的守恒有漏洞**：
   - 读数在别处有足够多的 l，也能"守恒"（paper_chn02 第 77 行的 δ^1 仍是错的）；
   - 两个 OCR 都没看到的字符会被当作错码豁免，但它们可能一起把 l 读成 1；
   - LaTeX 命令名曾经被当作字符算（`\left` 带着 l）。
7. **图片形式的公式**（patent01）：公式是转写过的图片，旁边的编号没有公式块可挂。
8. **不确定性**：服务模型不接受 temperature 参数，同样的提示，4 次里照做 1–4 次。
9. **评测噪声**：
   - paper_chn02 的标注有 24 处把 ^l 写成了 `^1`（LlamaParse 初稿的错，是否改待用户确认）；
   - LaTeX 写法不同（x' 和 x^{\prime}）也会让关键内容错误数变化。

## 3. 上一轮打的补丁（逐项评估，新设计可以替换或撤掉）

| 提交 | 内容 |
|---|---|
| 01cb743 | `_continued`：栏首或栏尾的原生块，其字母数字都在相邻段落读数的多余部分里时，归入该段（CARRIED、CARRIED_MATCH） |
| 8c8f4e4 | 编辑的提示里列出两份读数不一致的字母数字；编辑最多两轮（EDITOR_ROUNDS）；拉丁字母、数字、希腊字母不再享受错码豁免（`_EXACT`）；命令名不算字符；提示"只写 A 这一段" |
| cf3fc88 | `\ell`、`\hbar` 等字母命令按字母算（现在在 `content/latex.py`） |
| a3801c6 | `content/equation_numbers.py`：公式行右侧的编号归到公式（新关系 NUMBERS，渲染为 `\tag{n}`；一块多个编号时不挂）。`_enclosed`：落在段落原生块框内的碎片归入该段。`passage_of` / `passage_box`：Agent 看公式待办时截整段；待办里写明段落的块数和不一致的字符 |
| 8ccb89a | 对照页：`\tag` 不再盖住比栏宽的公式 |

冻结 run：`eval_runs/2026-09-29_bench2f_fixed_full`（对照页的固定流水线列用它）。原因和修复记录见 eval_reports/2026-09-28_bench_round2.md §5、§6。

## 4. 用户的要求

- **整体解决，不要越修越细。** 要问的是：公式在哪一步、以什么为单位被处理才对？为什么会出现需要这么多补丁的情况？
- **工具的抽象要一致，优先用现有工具。** 不要给 Agent 加公式专用的操作：为公式加一段，文字要不要也加？代码呢？表格呢？先看现有工具（`view_source` 的区域重读、`adopt`、`replace_text`、结构操作）能不能解决。解决不了，要说明缺的是什么通用能力。
- 其他：算法要通用，不针对测试文档；信息不丢、可读，优先于分数；新模型或新服务商先问；不要用 `.env` 里 `_C` 结尾的配置；Python 一律用 `uv`。

## 5. 可以从这里想（只是线索，不是结论）

- **处理的单位**：现在以文字层的块为单位，再用框的几何关系把读数配上去，配不上的地方（跨栏、上标碎片、编号）就一个个打补丁。
  - 版面检测器和扫描引擎本来就给出了公式区域（display_formula、formula_number）。公式区域能否一开始就成为一个块：区域内的文字层碎片归它，文字层只作为证据？
  - 编号是区域的一部分，还是单独的块？一块多个编号时，KaTeX 支持 `align` / `gather` 每行一个 `\tag`，已验证。
- **两份读数不一致时谁说了算**：文字层字符准确但没有结构，读数有结构但会认错字。
  - 现在的规则是"文字层每个字母数字都在"加"退路保留文字层"。表格的接受门（`content/select.py`）面对的是同一类问题。能否有一条不专属于公式的规则？
  - 退路是否应该看可读性？l 和 1 这类易混字符怎么处理？
- **Agent 的路径**：Q87 的区域重读（`view_source` 用 as=text 加 page 和 bbox）加 `adopt`，本来就是"替换一个区域的块"。
  - 它用在公式上缺什么：`_adopt_region` 的守恒是 RECALL 0.9 加原生数字不变，采用后还要用 `replace_text` 改错字，而改错字受接受门约束。
  - Agent 截图按块的框：这个问题对表格、代码、段落是否同样存在？
- **图片里的公式**（patent01）和扫描页的公式，要不要走同一套？

## 6. 从哪里读起

- 设计：docs/redesign_guide.md 的 Q70、Q87（§14）；docs/v2_agent_design.md §1；eval_reports/2026-09-26_q70_formula_experiment.md（引擎对比、整页和抠图的比较）。
- 代码：
  - `parserx/tools/formulas.py`
  - `parserx/content/equation_numbers.py`、`parserx/content/latex.py`
  - `parserx/tools/edit.py`（`adopt`、`_adopt_region`、`replace_text` 与 `_correct`）
  - `parserx/tools/source.py`（`_place_image`）
  - `parserx/tools/views.py`（`formula_candidate`）
  - `parserx/content/select.py`（接受门）
  - `parserx/layout/labels.py`、`parserx/content/scan.py`
  - `parserx/skills/transcription.md`（公式待办一节）
- 实验脚本：`scripts/formula_engines.py`、`formula_page_experiment.py`、`formula_merge_experiment.py`。
- 看效果：`uv run parserx dev tool-eval view`（http://127.0.0.1:8765/，选 paper_chn01、paper_chn02，原件对照各来源）；LlamaParse agentic 在这两篇上公式最好，可作参照。
- 测试：
  - L0：`uv run pytest --ignore=tests/test_live_e2e.py`（745 通过）；
  - 全语料对冻结 run：`uv run python scripts/regression_test.py --gt-dir ground_truth --gt-dir ground_truth_public --cache-mode read_write --cache-dir <冻结 run 缓存的副本> --baseline eval_runs/2026-09-29_bench2f_fixed_full`；
  - 公式文档：paper_chn01、paper_chn02、patent01、paper01、text_pic02，以及 ground_truth_public 的 omnidoc 论文页。

## 7. 建议的第一步

先盘点。列出所有和公式有关的规则和补丁，说清各自为了解决什么，把第 2 节的问题归到少数几个根因上。然后写一份设计稿（放 docs/，比如 `v2_formula_plan.md`），给出两三个整体方案及各自的取舍：单位、读数取舍、Agent 路径、编号、图片公式、评测。先和用户讨论，确认后再实施。实施按 TDD，每步全语料对冻结 run 比较。
