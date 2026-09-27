# 文字层的部首码位：分解（2026-09-27，用户已确认；已完成）

> **完成**（2026-09-27）：R1–R5 全部完成，结果见 [报告](../eval_reports/2026-09-27_radicals.md)。与预期不同的两处：(1) text_pic02 的缓存未命中还连带 1 个扫描引擎请求（描述缺失后守恒步骤转写的批次变了）；(2) 重新请求的描述中有一张截图少列了可见文字，被转写成界面表格，text_pic02 char_f1 −0.012（与替换无关，Q92 一类）。另有一处预期之外的改善：跨页续接（部首属符号类，原来挡住了续接）。

**目标**：有些 PDF 的原生文字层把普通汉字存成字形相同的部首码位（⽤ U+2F64 代替"用"，⻓ U+2ED3 代替"长"）。程序在读出文字层时，把它们换成 Unicode 规定的等价统一汉字，并把原字符记在 Decision 里，改动可追溯。这是通用的一步，不再留给 Agent 逐处改（DeepSeek 对比报告 §3：Agent 在 text_pic02 上手工改了 100 多处）。

**费用**：代码与全语料回放都离线。要花钱的只有两处，都是服务层 VLM（gpt-6-luna，现有配置），都需要先问用户（Q97）：
- L1 缓存补录 receipt 的 2 个图片描述请求；
- 冻结 run 重新冻结，全语料新增 8 个描述请求。

## 1. 事实

**哪些文档有**。逐篇扫描语料全部 26 个 PDF 的文字层：

| 文档 | 文字层 | 进入输出 | 字符 |
|---|---|---|---|
| text_pic02 | 152 | 134 | ⽤ ⽹ ⾦ ⼀ ⼊ ⾏ ⼯ ⽅ ⼝ ⽂ ⾯ ⽆ ⾥ … 23 种；另有 ⻜（补充区）×4 在第 1 页 |
| receipt | 20 | 20 | ⽇ ⽉ ⼈ ⼦ ⽂ ⼩ ⽌ ⽬ ⽀ ⽤ |
| deepseek | 16 | 16 | ⼤ ⽤ ⽅ ⾯ ⽕ ⽽ ⼊ ⼏ ⽣；⻓（补充区）×2 |
| text_code_block | 10 | 10 | ⽤ ⾃ ⽌ ⽴ ⼀ ⾏ ⼯ |

- 输出里 180 个都来自 `native_pdf` 读数。text_pic02 第 1 页交扫描引擎，那 18 个在不输出的原生块里，扫描引擎读成了"飞"等统一汉字。
- 扫描引擎的输出和 8 个 Word 文件里都没有。
- 语料里也没有兼容汉字（U+F900–FAFF、U+2F800–）和 CJK 笔画（U+31C0–）。

**对读者是错，指标看不出**。
- 指标先做 NFKC。康熙部首都有 NFKC 映射，所以 char_f1 看不出。
- 补充区的只有 ⺟、⻳ 两个有 NFKC 映射。deepseek 的 ⻓ 在指标里算错字。
- 检索"使用"找不到"使⽤"。

**顺带的空格错误**。
- `pdf_native._reconstruct_line_from_chars` 在字符间隙超过 0.25 个字号时插空格，除非两边都是 CJK。判断用的 `_is_cjk_or_fullwidth_punct` 不含两个部首区。
- 所以两端对齐的行里，部首两边会多出空格：text_pic02 的"资源类填 ⼊ 与实例规格"（间隙 5.34 pt，字号 8 pt）。全语料 1 处，2 个空格。标注是"填⼊与"。

**Unicode 的等价表**。EquivalentUnifiedIdeograph.txt 为 Unicode 18.0.0（2026-02-03），410 行，18 KB，自带版权与使用条款说明：
- 康熙部首 214 个全部有映射，与 NFKC 逐个相同；
- 部首补充区 115 个中 114 个有映射，只有 ⺀ U+2E80 REPEAT 没有；
- 补充区中 16 个是偏旁的变形，映射到扩展区的字（⺮→𥫗、⻊→𧾷），在正文里几乎不会作为整字出现；
- 表中还有 CJK 笔画区的 19 行，不在本次范围。

**读文字层的地方只有一个**：`content/pdf_native.py`。
- **行文字**：`_lines`，经 rawdict 的逐字符重建。
- **单元格文字**：`_grid` 与 `_unruled_table`，来自 PyMuPDF 表格代码的字符（`Table.extract()`、`extract_text`）。
- **已有的折叠**：这三处都已先做全角 ASCII 折叠（`content/text.py` 的 `normalize_fullwidth_ascii`），没有记录。
- **保持原样**：`glyphs` / `origins` 用原字符，作为与 PyMuPDF 表格字符对应的几何键。
- **其他模块**：其他用到 PyMuPDF 的模块只渲染图像。DOCX 从 OOXML 的 `w:t` 读文字（`content/docx.py`）。

**已有的审计不报**。`text_audit.suspicious_characters` 报两类：读不出的字符（私用区、U+FFFD、控制字符），以及全文罕见的文字体系。它不报部首：
- 康熙部首的 Unicode 名称（"KANGXI RADICAL USE"）不含任何体系名，不参与计数；
- 补充区名称含"CJK"，算作常见体系。

**含原生文字的请求会变**。
- **图片描述**：`describe_figure` 把图片附近的文字作为上下文发给 VLM，缓存键包含上下文。
- **命中的请求**：全语料冻结 run 中有 8 个描述请求的上下文含部首，text_pic02 6 个、receipt 2 个。改完后这 8 个键会变。
- **回放的后果**：离线回放时缓存未命中，文档不执行，算硬失败。受影响的有：
  - L1：receipt 在核心集里；
  - 验收冻结 run `2026-09-26_q80_v2_toolkit`：含 receipt；
  - 全语料冻结 run `2026-09-27_tables_fixed_full`：含 receipt 和 text_pic02。
- **不受影响的**：公式编辑器也发原生文字，但公式页都没有部首。

**标注里也有**。
- **数量**：text_pic02 的 expected.md 有 151 个，其中 ⻜ ×4 在第 1 页；receipt 有 13 个。
- **对分数的影响**：经 NFKC 后只有 ⻜ 算差异。我们第 1 页的输出已是"飞"，所以这是标注的错。
- **是否在 git 中**：text_pic02 的 expected.md 在 git 中，receipt 的不在。

## 2. 范围

**做**：
- 在原生 PDF 读出文字层时换成等价统一汉字，两个区：
  - 康熙部首 U+2F00–U+2FDF；
  - CJK 部首补充 U+2E80–U+2EFF。
- 放在插空格的判断之前，空格错误随之消失。
- 每个有替换的块写一条 Decision，列出原字符。
- 对照表按原样放进仓库，运行时不联网。
- 测试、回放、重新冻结（Q97）、标注（Q98）。
- DOCX 视 Q96 而定。

**不做**：
- 全文 NFKC；
- CJK 笔画、兼容汉字：语料里没有，用户限定两个区；
- 扫描引擎与 Agent 写的文字；
- `text_suspicious` 视 Q99 而定。

## 3. 工作项

### R1 对照表与函数

- **对照表**：`parserx/content/data/EquivalentUnifiedIdeograph.txt`，Unicode 原文件逐字节放入（含其版权头），模块里记下版本与 sha256。
- **函数**：`content/text.py` 新增 `unify_radicals(text)`。
  - 只取两个部首区的映射，第一次使用时解析一次；
  - 一对一替换，长度不变；
  - 另给一个列出文本中会被替换的字符的函数，供记录用。
- **单元测试**：
  - 214 个康熙部首的结果与 NFKC 相同；
  - ⻓→长、⻜→飞；⺀ 不变；
  - 笔画区（㇐ U+31D0）不变；
  - 其他文字不变（含全角标点与兼容汉字）；
  - 长度不变。

### R2 原生 PDF 读出

**替换**：
- `_reconstruct_line_from_chars` 先逐字符替换，再判断间隙，部首因此按 CJK 处理，不插空格；
- `_grid` 与 `_unruled_table` 的单元格文字，与全角折叠放在同一处；
- `glyphs` / `origins` 保持原字符。

**记录**：块里的行有被替换的字符时，`_block` 写一条 Decision：
- `stage=content_source`，`choice=unified_ideographs`；
- reason 列出每一对及次数，例如 "the text layer stores 3 characters as radical code points; output as their equivalent unified ideographs (Unicode EquivalentUnifiedIdeograph): ⽤→用 ×2, ⻓→长"；
- `evidence={"chars": 3}`，actor 为 `program:content.pdf_native`；
- 放在页面来源 Decision 之前：`decisions[-1]` 被当作失败原因读取，保持是来源 Decision。

**为什么记在 Decision、不在 Observation**：
- Observation 的文字已经是折叠后的，全角折叠也是这样；
- `restore`、`unadopt`、选择步骤会从 Observation 取回文字，如果那里保留部首，部首会被带回输出；
- Decision 是"谁、为什么改了什么"的既有记录，按块给出原字符与次数。

**测试**（合成输入）：
- 用 ToUnicode CMap 把字形映射到部首码位造 PDF，这就是真实文档的成因，已验证 PyMuPDF 读出 ⽤⻓⾦；
- 正文块：文字换成统一汉字，Decision 的次数与字符对；
- 有框线表格：单元格换成统一汉字；
- 两端对齐的间隙：部首两侧不插空格；
- 没有部首的 PDF：不写这条 Decision。

### R3 DOCX（Q96）

- 段落与单元格文字读出处用同一函数，写同一种 Decision（actor 为 `program:content.docx`）；
- 合成 DOCX 测试；
- 语料无影响。

### R4 标注（Q98）

- text_pic02、receipt 的 expected.md 中的部首换成统一汉字；
- receipt 的原件先加入 git；
- 记入 docs/annotation_changes.md。

### R5 验证与重新冻结

- **L0**：`uv run pytest -q --ignore=tests/test_live_e2e.py`。
- **L1**：先用 `--core --allow-calls` 补录 receipt 的 2 个描述响应（Q97），再跑 `--core --repeat 2`。
- **全语料回放**（对 `2026-09-27_tables_fixed_full`），预期：
  - 27 篇输出逐字节不变；
  - deepseek、text_code_block 的输出只差部首替换；
  - receipt、text_pic02 因 8 个缓存未命中不执行，核对未命中的正是预期的 8 个。
- **重新冻结**（Q97），再核对 receipt、text_pic02：
  - 文字只差部首替换与那 2 个空格；
  - 另有 8 张图的描述是新请求的结果。
- **逐篇核对的方法**：用脚本把旧输出按对照表替换、去掉那 2 个空格，与新输出比较，差异只能在图片描述里。
- **收尾**：
  - 两个新冻结 run 各自回放通过；
  - 被取代的冻结 run 改名保留；
  - 报告写入 eval_reports/；
  - 更新指导 §12、§14（Q96–Q99）、§15，以及 next_session_prompt；
  - 提交一次，不推送。

## 4. 顺序

R1 → R2 →（R3）→ L0 → 回放 → 请用户确认重新冻结 →（R4）→ L1 补录 → 重新冻结 → 报告与指导 → 提交。

## 5. 退出条件

- **字符**：
  - 4 篇的输出中，两个区的可映射字符为 0（现 180）；
  - "填 ⼊ 与"的两个空格消失；
  - 有替换的块都有 Decision，次数合计等于文字层中进入这些块的部首数。
- **不退步**：
  - 其余 27 篇输出逐字节不变；
  - 4 篇的差异只是替换、那 2 个空格和 8 张图的新描述；
  - char_f1 逐篇不低于冻结值（deepseek 因 ⻓ 应略升）。
- **规则**：只按 Unicode 的等价表替换两个区，不针对文档写条件；有单元测试。
- **测试**：
  - L0 通过（现 645，加上新测试）；
  - L1 PASS；
  - 两个新冻结 run 的回放 PASS。

## 6. 待决问题

用户 2026-09-27 全部按建议决定：Q96 DOCX 也做；Q97 全语料以旧缓存为起点、验收集以空缓存重新冻结，L1 补录 receipt 的 2 个响应；Q98 两篇标注都改；Q99 `text_suspicious` 不改。

- **Q96** DOCX 也做吗？
  - **建议**：做。约束是"正文用统一汉字"，与成因无关。从这类 PDF 复制进 Word 的文字带着同样的码位。代价是几行代码和一个测试，语料上无影响。
  - **另一个办法**：只做 PDF，DOCX 出现时再加。
- **Q97** 重新冻结怎么做？
  - **L1**：用 `--core --allow-calls` 补录 receipt 的 2 个描述响应，写入 `.parserx_cache`。
  - **全语料冻结 run**：建议以 `2026-09-27_tables_fixed_full` 的缓存为起点重新冻结。只有那 8 个描述请求是新请求，其余逐字节相同，两个冻结 run 之间只差这次改动。表格那轮也是离线从上一个缓存冻结的。
  - **验收冻结 run** `2026-09-26_q80_v2_toolkit`：按惯例，改动影响验收文档时以空缓存重新冻结。4 篇的请求全部重发：jtg3362 的扫描引擎请求和各篇的描述请求。
  - **另一个办法**：也从旧缓存起步，只新增 receipt 的 2 个请求。
  - 以上都是 gpt-6-luna 的现有服务配置，不涉及新模型或服务商。
- **Q98** 标注里的部首改不改？
  - **建议**：改。text_pic02 151 个、receipt 13 个换成统一汉字，记入 annotation_changes.md。
  - **证据**：页面上是这些字；部首码位是文字层的错，标注从文字层继承了它。
  - **对分数的影响**：只有 text_pic02 第 1 页的 ⻜ ×4。
- **Q99** `text_suspicious` 要不要报部首？
  - **建议**：不加。读出时已经替换，改完后语料里没有剩下的。剩下的只可能是：
    - ⺀：没有等价字，多半本来就是部首；
    - 扫描引擎或 Agent 写出的：从未见过。
  - **另一个办法**：把两个区里剩下的字符作为可疑字符报出。
