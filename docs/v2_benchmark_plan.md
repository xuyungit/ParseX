# 对标工具比较：分解（2026-09-28，第一批四个工具由用户确认；Q129–Q131 按建议，Q132 待定）

**目标**：把同样的文档交给外部工具和 ParserX 解析，用同一套指标打分，再逐篇并排阅读。要回答两个问题：
- 我们的质量在同类工具里处在什么位置；
- 哪些地方别人做得更好、值得改进。改进本身另立分解，不在这里做。

**进展**（2026-09-28）：B1–B3、B5 完成；第一轮 10 篇跑完（含 ParserX 混合方案，Codex）。用户反馈与比较发现的问题逐条查到代码原因，见 [eval_reports/2026-09-28_bench_round1.md](../eval_reports/2026-09-28_bench_round1.md)（P1–P11，Q133–Q136）。第二轮 8 篇（4–9 页，`configs/bench_round2.txt`）跑完，七个来源；结果与发现见 [eval_reports/2026-09-28_bench_round2.md](../eval_reports/2026-09-28_bench_round2.md)。

**第一批**（用户确认，2026-09-28）：LlamaParse、MinerU、marker（Datalab 托管）、PaddleOCR-VL 整套流程。四个都走在线接口，不在本机部署。测试文档可以上传云端（用户确认；类似招标的那份已脱敏）。

## 1. 事实

### 1.1 已有的

- **`parserx dev tool-eval`**（`parserx/tool_eval/`）：v1 时期写的，v2 下还能导入。
  - 适配器：LlamaParse（经 `scripts/llamaparse_to_markdown.ts` 调 Node SDK）、LiteParse、内置 PDF 读取、ParserX 本身。
  - 打分调 `evaluate_markdown`，与回归是同一个函数。
  - 只在 chn_doc01 上跑过一次（2026-04-12）。
  - 报告只有四栏：编辑距离、char_f1、标题 F1、表格 F1。回归报告里的表头关联、合并单元格、阅读顺序、关键内容错误都没有。
  - ParserX 一侧每次重跑固定流水线，不读冻结 run。
- **表格**：
  - ParserX 的表格没有合并单元格时输出管道表，有就输出 HTML（指导 §4.5，`render/markdown.py`）。冻结 run 里有 11 篇带 HTML 表格。
  - 打分器两种都读（`tables/grid.py` 的 `find_tables`），并比较合并单元格。别家输出 HTML 表格，打分器不用改。
- **规范化**（`eval/normalize.py`）：
  - 剔除紧跟图片行的引用块（我们的图片说明）；
  - 剔除 HTML 注释（页码 `<!-- PAGE n -->`）；
  - 别家的非内容标记没有核对过：分页线、图片链接、自动生成的图片说明、公式定界符。
  - 已见一例：MinerU 把正文里的百分数写成行内公式（`$18.9\%$`），我们和标注都写 `18.9%`。
  - Datalab 自动给图片写说明，同一段话既放进图片的替代文字，又在图片后单独成段；单独那段不是引用块，打分时会被当作多出来的正文。
  - Datalab 把中文全角标点换成半角（`，`→`, `、`（`→` (`），并在汉字和数字之间加空格。打分前的 NFKC 规范化会抹平这些差别，分数看不出来，要在并排阅读时记下。
- **语料**：有标注的 31 篇。
  - PDF 26 篇，共 112 页；
  - Word 5 篇（real_doc01、simple_doc01、text_report01、unseen_word_spec01、val_word_template01），约 30 页；
  - 其中 `ground_truth/` 21 篇，`ground_truth_public/` 10 篇（含 OmniDocBench 8 页）。
- **标注来源**：
  - `ground_truth/` 的标注按 [evaluation_workflow.md](evaluation_workflow.md) §2 的流程，先用 LlamaParse（agentic）生成初稿，再人工修改。LlamaParse 在这一组上会占便宜。逐篇来源没有记录。
  - real_doc01 的标注参考过 ParserX 的运行结果（Q83）。在这一篇上，占便宜的是我们。
  - OmniDocBench 8 页用的是它自己的人工标注。OmniDocBench 由 MinerU 团队（opendatalab）维护，标注的写法可能贴近 MinerU 的输出，这一组上 MinerU 可能占便宜。
- **冻结 run**：
  - `2026-09-28_io_fixed_full`：固定流水线；
  - `2026-09-28_io_v2_toolkit`：混合方案。

  两者的 `outputs/<名称>.md` 可以直接拿来打分。

### 1.2 四个工具

| | LlamaParse | MinerU | marker（Datalab） | PaddleOCR-VL 整套流程 |
|---|---|---|---|---|
| 接入 | 已有 key（`LLAMA_CLOUD_API_KEY`） | 已注册，key 在 `.env` 的 `MINER_U_API_KEY`（`sk-` 开头）。2026-09-28 验证：v4 接口用 `Bearer` 可用，1 页文档共 32 秒（排队约 15 秒） | 已注册，key 在 `.env` 的 `DATALAB_API_KEY`。不用信用卡，每月免费额度：工作邮箱 $20，个人邮箱 $10；没有查余额的接口，每次结果带 `cost_breakdown`，余额在网页控制台看。2026-09-28 验证：accurate 1 页 52 秒，扣 1 美分 | 已有（扫描引擎在用的 AI Studio token） |
| 接口 | Parse API v2：先上传，再调 `parsing.parse(tier, version, expand=["markdown"])`。SDK `@llamaindex/llama-cloud` 2.16；我们锁的是 `^2.2.0`，升级后就会用上 2.16 | 精准解析 v4：<br>① `POST /api/v4/file-urls/batch` 取上传地址；<br>② PUT 文件；<br>③ 轮询 `GET /api/v4/extract-results/batch/{batch_id}`；<br>④ 下载 zip，内有 `full.md`、`content_list.json` | `POST /api/v1/convert`（multipart，头 `X-API-Key`），然后轮询 `request_check_url`。完成后要看 `success`：失败也会以"完成"状态返回 | 在用的 `/api/v2/ocr/jobs`。结果里每页有 `markdown.text`，另有 `markdownUrl`，是不是整篇未核实 |
| 档位与关键参数 | `tier: agentic`（标注初稿用的也是这一档）；`merge_continued_tables`；表格输出 HTML（`output_tables_as_markdown: false`） | `model_version: vlm`（MinerU2.5-Pro）；`enable_table`、`enable_formula` 打开 | `mode: accurate`（实际是 Chandra 2 模型）；`merge_cross_page`（测试版，合并跨页的表格、段落、列表） | 现有参数加 `restructurePages: true`，其下的 `mergeTables`、`relevelTitles` 默认打开 |
| DOCX | 收 | 收 | 收 | 不收 |
| 表格输出 | HTML（按上面的参数） | HTML | Markdown 或 HTML | HTML |
| 限额 | 每月免费 1 万积分，agentic 每页 10 积分 | 每篇最多 200 页；每天 1000 页优先处理（旧文档写 2000） | 免费档每分钟 10 次请求 | 每天 3000 页 |
| 本语料跑一次的代价 | 约 1500 积分，在免费额度内 | 免费 | accurate 约 $1.5；`merge_cross_page` 每篇约 $0.5，多页文档约 15 篇，约 $7.5。在免费额度内 | 免费 |
| ParseBench 总分（参考） | 87.0（agentic） | 72.8（MinerU2.5-Pro） | 70.0（accurate） | 67.4 |

**本机部署**（备用）：每个工具约 2 GB 模型，另加 Python 环境。MinerU 和 marker 在 Apple Silicon 上有官方路径。只在在线接口用不了时才考虑。

## 2. 范围

**做**：
- 四个工具的适配器；
- 在 31 篇上各跑一次，原始响应存下来，重新打分不再花钱；
- 用回归的同一套指标和报告格式打分，ParserX 一侧用冻结 run 的输出；
- 逐篇对照页，供人工并排阅读；
- 差距清单和可改进点，写成报告。

**不做**：
- 为了对标改 ParserX：找到的改进点另立分解；
- 本机部署，除非在线接口用不了；
- OCRFlux：只能在 NVIDIA 显卡上跑，权重不能商用，所有标题都输出成 H1，图片被丢掉；
- 第二批工具（TextIn、Docling、LiteParse、直接用大模型逐页转）：第一批做完再定；
- ParseBench：放在最后，见 §5。

## 3. 比较的规则

- **各家用自己最好的配置**：取各家推荐的最高质量档，并打开与我们能力对应的选项（跨页表格合并、标题层级、能保留合并单元格的 HTML 表格）。配置原样写进每个工具的 manifest。
- **不替任何一方修输出**：别家输出里有规范化没处理的非内容标记时，只加对所有工具（包括 ParserX）一样的通用规则，每条写明理由。
- **不收 DOCX 的工具**（PaddleOCR-VL）：先用 LibreOffice 转成 PDF 再送，报告里标明。
- **标注偏差**：报告把文档分两组列出：
  - LlamaParse 初稿改出来的标注；
  - 独立标注（OmniDocBench 8 页）。

  比较 LlamaParse 和我们，主要看第二组和人工阅读。
- **对手比标注更对时**：按标注修订规则，带证据修改标注。改之前先把原件加进 git，把清单列给用户看，改后逐处记入 [annotation_changes.md](annotation_changes.md)。
- **重点看什么**，按优先级：
  1. 信息有没有丢，特别是扫描件和图片里的文字；
  2. 标题层级；
  3. 表格，含跨页表格；
  4. 可读性。

  分数只是入口，结论以并排阅读为准。

## 4. 工作项

| 项 | 内容 | 验收 |
|---|---|---|
| B1 注册与凭据 | 用户注册 mineru.net 和 datalab.to，把 key 写进 `.env`：`MINER_U_API_KEY`、`DATALAB_API_KEY`（2026-09-28 均已完成并验证）。它们不是产品依赖，`parserx check` 不检查 | 两个 key 各在一篇两页的文档上跑通 |
| B2 适配器 | `tool_eval/adapters.py` 改动：<br>① LlamaParse 改用 v2 参数，SDK 升到 2.16；<br>② 新增 MinerU、Datalab、PaddleOCR-VL 三个适配器。<br>每个适配器都是"提交 → 轮询 → 下载"：<br>- 原始响应（JSON、zip）存进工具目录；<br>- 已有原始响应的文档不再请求；<br>- 每篇记页数、用时、费用或积分 | L0：用录好的响应离线测三个新适配器的解析。<br>每个工具在一篇扫描件、一篇 DOCX 上真实跑通 |
| B3 打分对齐 | ① tool-eval 的报告改用回归报告的全部栏目：硬检查；表格 F1 及表头关联、合并单元格；char_f1；阅读顺序；标题 F1（只算有大纲的文档）；关键内容错误。<br>② ParserX 一侧直接读冻结 run 的 `outputs/`。<br>③ 核对别家输出里的非内容标记，缺的只加通用规则 | L0；冻结 run 的输出经 tool-eval 打分，与它自己的 `metrics.json` 逐篇一致 |
| B4 全语料运行 | 四个工具在 31 篇上各跑一次，分三轮（§4.1）。结果放 `eval_runs/bench/<工具>/<文档>/`：`output.md`、图片、`raw/`、`meta.json`；打分写 `eval_runs/bench/scores.json`、`report.md`（[tool_eval.md](tool_eval.md)） | 31 篇都有输出，或写明失败原因；费用在 §1.2 的估计内 |
| B5 对照页 | 用户要求（2026-09-28）：既有自动评分，也有手工评分。<br>本地页面 `parserx dev tool-eval view`（用法见 [tool_eval.md](tool_eval.md)）：<br>① 从原件（页面图）、标注、各工具的输出中选 2–4 个来源并排；<br>② Markdown 渲染后显示，图片、HTML 表格、公式都显示，标题标出层级；<br>③ 每栏上方是自动评分，下面是手工评分：信息完整、标题层级、表格、可读性、总体，各 1–5 分，加备注，存进 `manual_scores.json`；<br>④ 总表：每篇每个来源的自动分和手工总体分 | 用户能逐篇翻看、打分 |
| B6 分析报告 | `eval_reports/<日期>_benchmarks.md`，内容包括：<br>- 总表，两组标注分开列；<br>- 逐篇结果；<br>- 差距清单：别人好在哪、原因归类、影响多少篇；<br>- 可改进点候选；<br>- 需要改的标注（清单） | 用户看过报告，决定下一步改哪些 |
| B7 ParseBench | 最后做，见 §5 | — |

### 4.1 顺序

文档由少到多、由简到繁，分三轮（用户要求，2026-09-28）：
- 第一轮：1–3 页的短文档，每类都有，10 篇（`configs/bench_round1.txt`）；
- 第二轮：4–9 页，扫描件、论文、跨页表格；
- 第三轮：长而复杂的文档（专利、标准、长 Word）。

工作项的顺序：

1. 用户做 B1；同时做 B2 里 LlamaParse 和 PaddleOCR-VL 的部分，这两家不用新注册；
2. B2 其余部分；
3. B3；
4. B4；
5. B5；
6. B6；
7. 用户看报告；
8. B7。

每项跑 L0，完成后提交一次。

### 4.2 退出条件

- 四个工具在 31 篇上的输出和原始响应都已存下，重新打分不再请求；
- 报告和对照页交给用户；
- 改进点和要改的标注列成清单，等用户决定。

## 5. ParseBench（最后做）

- **是什么**：LlamaIndex 2026 年 4 月发布的基准（arXiv 2604.08538）。
  - 2,078 页英文企业 PDF，来自保险、金融、政府，基本是单页；
  - 五个维度：表格、图表、内容忠实度、语义格式、视觉定位；
  - 中文约 20 页，没有 DOCX、标准、专利；
  - 测不到跨页合并、整篇标题层级、图片信息。
- **我们的状态**：4 月用 v1 跑过，见 [parsebench_baseline.md](parsebench_baseline.md)。9 月发布的 v1.0.0 改了评分，旧分数不能和现在的榜单比。新版可以用插件接入，不用再 fork。
- **用途**（用户意见，2026-09-28）：
  - 放在最后做；
  - 可以挑一部分页面进我们自己的测试集，补上语料里少的几类：英文企业文档、图表、文字格式。
- **做法**：B6 之后再定（Q132）。

## 6. 待决问题

- **Q129 LlamaParse 跑哪一档。** Agentic Plus 在 ParseBench 上排第一（90.2），每页 45 积分，本语料约 6700 积分。和 agentic 一起跑，会超出每月免费的 1 万积分。
  - ✅ 用户按建议决定（2026-09-28）。
  - **建议**：只跑 agentic。它是标准档，也是标注初稿用的档。看了报告后，如果 LlamaParse 在某类文档上明显领先，再在那几篇上补跑 Agentic Plus。
- **Q130 marker 用哪个模式。** `accurate` 实际用的是 Datalab 的 Chandra 2 模型；`balanced` 才是 marker 本身（Surya 模型）。两者在 ParseBench 上只差 0.4 分。
  - ✅ 用户按建议决定（2026-09-28）。
  - **建议**：跑 `accurate`，看 Datalab 能给出的最好结果。`balanced` 不单独跑。
- **Q131 PaddleOCR-VL 的整篇重组。** 在线接口打开 `restructurePages` 后，是否给出整篇合并的 Markdown（`markdownUrl`），还没核实。
  - ✅ 用户按建议决定（2026-09-28）。
  - **建议**：先试在线参数。没有整篇结果的话，就把每页的 Markdown 顺序拼接，不在我们这边重写它的重组逻辑，报告里写明"未做跨页重组"。
- **Q132 ParseBench 怎样进我们的测试集。** ParseBench 的标注是一条条规则，不是整篇 Markdown。
  - **建议**：B6 之后再定。有两条路：
    - 用它的规则打分：接入 parse-bench 1.0 的插件；
    - 挑几十页写 `expected.md`，放进 `ground_truth_public/`。
- **Q133–Q136**：第一轮发现的问题引出的四个问题（本地识读补字、表单式表格、每篇通读的开关、字面 `\n`），✅ 用户按建议决定（2026-09-28），见 [第一轮报告](../eval_reports/2026-09-28_bench_round1.md) §5。**Q136 撤回（用户，2026-09-30）**：印出来的字面 `\n` 是原件的内容，照原样输出，不加规则转成分段（`content/text.literal_breaks` 删除，receipt 标注恢复原样）。
