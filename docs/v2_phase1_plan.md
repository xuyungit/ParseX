# 阶段一"文档工具包 v1"工作分解

> 状态：**草案**（2026-09-24）。依据 [redesign_guide.md](redesign_guide.md) v1.9 的 §3–§6、§8、§11、§12，接口以 [v2_phase1_interfaces.md](v2_phase1_interfaces.md) 为准。新会话开始时先确认 §6 的待决问题，再按 §3 的顺序实施；每完成一项更新指导 §12 与 §15。

## 0. 目标与退出条件

**目标**：让"工作区 + 七个工具 + 程序约束"可用。用同一套工具组成固定序列运行时，在验收文档上端到端跑通，为阶段二的 Agent 探索提供可直接调用的工具包。阶段一不追求质量提升，只要求不退步，同时把 v1 看不见的问题（丢内容、多余请求）变成可见的账目。

**退出条件**（细化指导 §12）：

1. **L0 覆盖**：
   - 五个 IR 概念与 Decision 的往返与校验，TableGrid 的解析与渲染往返；
   - 七个工具的契约：信封、`DocText`、失败码、版本冲突、`apply_structure` 的每条合法性规则；
   - 去向检查；调度层的预算、按确定顺序合并结果、重试分类。
2. **与 v1 基线比较**（`eval_runs/2026-09-23_p0_v1_gpt-6-luna`）：text_table01、receipt、simple_doc01 三篇的 v2 冻结 run 满足：
   - 信息类指标都不低于 v1：char_f1、编辑距离、表格 F1、阅读顺序 τ 与覆盖率、关键内容错误、漏表和多余表数；
   - heading_f1 也不低于 v1；
   - 以上分数容差 0.005，计数类容差 0；
   - 真实请求数 O/V/L 各自不超过 v1。
3. **扫描路径**（待定 Q23）：ocr_scan_jtg3362 能执行、去向平衡、信息类指标不低于 v1；标题不设门槛。
4. **每篇文档**：`check` 无未归属条目；sidecar 通过 JSON Schema 校验；v2 冻结 run 能离线回放（`--replay` 通过）。
5. **影子运行**：`layout/` 与 `routing/image.py` 写出检测 Observation、`images` 记录和 `image_route` Decision，不影响输出。
6. **可离线回放**：v2 的 L1（`--core --config configs/regression_v2.yaml --repeat 2`）离线回放两遍一致。

## 1. 分析设计与语料时发现的事实

| 事实 | 证据 | 影响 |
|---|---|---|
| receipt 是原生 PDF（每页有原生文字，只有几个小图标），不是扫描件 | PyMuPDF：各页原生字符 412 / 1691 / 157，图片面积占比 < 1% | 指导 §9.1 的标注有误，已改；原定三篇验收文档都没有扫描页 |
| 三篇验收文档都覆盖不到扫描页引擎和真实表格 | text_table01 的标注没有表格；simple_doc01 是 DOCX | 阶段二离不开扫描页工具，需要一篇扫描文档检查这条路径（Q23） |
| simple_doc01 的 v1 分数低，根因是**修订记录**：104 处 `w:ins`、25 处 `w:del`、2 处 `w:moveFrom`；Docling 丢掉了插入的文字 | v1 输出只有 830 字节，标注有 1700 字节；标题只剩编号 | 标注按"接受全部修订后的最终视图"；DOCX 应直接读 OOXML，并当即确定修订的处理方式（Q9 的一部分，见 Q26） |
| Docling 给不出 OOXML 节点路径，也无法枚举已发现的节点 | Docling 的条目只有 `#/texts/12` 这类引用 | 用 Docling 适配做不到 `DocxAnchor` 和去向账目，接口草案里"DOCX 暂经 v1 provider 适配"的写法需要改 |
| 语料中的其他 DOCX：real_doc02 有 302 张图片但没有标注，chn_doc01 有 151 处域代码但没有标注 | OOXML 统计 | real_doc02 可作为隔离集的 DOCX 候选（需要人工标注） |
| heading_f1 是退出条件之一，但章节组织（§6.8）在阶段四 | v1 基线：text_table01 与 receipt 的标题 F1 为 1.000，simple_doc01 为 0.295 | 阶段一需要一个明确标注为临时的结构来源（Q24） |
| 评测把"图片行 + 紧随其后的引用块"当作图片描述剔除 | `parserx/eval/normalize.py` | §4.5 的语义块必须是紧跟图片行的引用块，否则描述会被计入正文分数 |
| v1 会对原生页做 OCR，结果又全部被去重丢弃 | P0-1：deepseek、pdf_text01_tables、text_table_libreoffice | v2 只在原生层质量判定失败时才调用扫描页引擎，请求数自然下降 |
| 阶段零修掉的五个 v1 缺陷都属于同一类问题：多余请求，以及并发结果按完成顺序生效 | 指导 §15 v1.3–v1.7 | 写成调度层与开发规范中的硬规则（指导 §8.2、§13） |
| 费用列目前一直显示"—" | `services/llm.py` 只返回字符串 | 调度层需要记录 token 用量并按价格表结算 |

## 2. 设计修订（已写回指导 v1.9 与接口文档）

- **R1 DOCX 直接读 OOXML**：阶段一的 DOCX 读取器支持正文段落、表格（`gridSpan` / `vMerge`）、内嵌与浮动图片、修订最终视图、域代码结果文字、显式分页与分节，并把样式、大纲级别、编号作为证据。文本框、脚注尾注、批注、嵌套表格记入 warnings，阶段四按 Q9 补齐。
- **R2 结构证据用类型化字段**：Observation 增加 `style: TextStyle | None`，包括字号、加粗、样式名、大纲级别、编号信息。不用自由字典（§4.2）。
- **R3 修订记录计入去向**：被删除的修订文字作为账目条目，去向记为 `excluded`，配一条 `exclude` Decision，`choice=revision_deleted`。"文件里有过的内容"都有去向。
- **R4 阶段一的归属方式**：原生 PDF 用 PyMuPDF 的文本块，版面检测只做影子运行；§6.3 的"按检测框归属"在阶段四根据影子结果决定是否启用。这样检测器的误差不会在阶段一进入输出。
- **R5 渲染与语义块**：新增 `parserx/render/`，v1 的 `assembly/` 保持不动。图片语义块必须是紧跟在图片行之后的引用块，首行为 `> [图片语义]`。
- **R6 调度层硬规则**：
  - 所有远程调用必须经过 `ServiceGateway`；
  - 并发只用于发请求，结果按任务顺序生效；
  - 只对传输错误和无法解析的响应重试；
  - 已知的模型参数约束写进配置，不靠 400 去试探；
  - OpenAI SDK 设 `max_retries=0`，尝试次数由调度层统计；
  - 记录 token 用量与费用。
- **R7 v1 与 v2 并存**：配置增加 `pipeline: v1 | v2`；v2 的 `parse_result` 返回同样的 `ParseResult`，回归、冻结、回放工具直接复用；冻结 run 另存每篇的 sidecar。
- **R8 阶段一的结构来源**（Q24）：
  - DOCX 走 §6.8 允许的确定性路径（样式与大纲级别），Q12 的 DOCX 部分随之决定；
  - 原生 PDF 用临时适配器 `parserx/runtimes/v1_structure.py`：只运行 v1 不调用服务的元数据构建与章节处理，把它的标题判断经 `apply_structure` 写入（actor 记为 `adapter:v1`）。阶段四换成 §6.8 的实现后删除。

## 3. 顺序与依赖

```
P1-1 IR 与 TableGrid 渲染 ─→ P1-2 工作区 ─┬─→ P1-4 内容获取 ─┬─→ P1-6 渲染与导出 ─→ P1-7 工具与 CLI ─→ P1-10 固定序列运行时 ─→ P1-11 验收
                                          ├─→ P1-5 去向检查 ──┘                      │（可选 P1-7b 工具形态试用，Q25）
P1-3 调度层（可与 P1-1/P1-2 并行）──────────┘                                         │
P1-8 Skill 草稿、P1-9 版面与路由影子运行：P1-1 之后任意时间；P1-9 须在 P1-10 之前 ──────┘
```

## 4. 各项分解

### P1-1 IR 完整模型与 TableGrid 渲染

- **内容**：
  - 按接口文档 §2 补齐 `parserx/ir/` 的模型：`enums.py`、`observation.py`（含 R2 的 `TextStyle`）、`relation.py`、`asset.py`、`decision.py`、`block.py`、`semantic.py`、`state.py`（PageState、LedgerEntry、ImageRecord、Missing、Stats、DocumentState）；
  - `ids.py`：确定性 ID 的生成函数；
  - `schema.py`：导出 sidecar 的 JSON Schema；
  - `parserx/tables/grid.py` 补 `to_gfm()`（有合并单元格时报错）和 `to_html()`。
- **测试**：
  - `tests/test_ir_models.py`：JSON 往返；`extra="forbid"`；Block 的结构校验（`level` 只能出现在 title、`cells` 只能出现在 table、`chosen_observation` 必须存在、至少一个 anchor）；确定性 ID；
  - `tests/test_table_grid.py`：GFM / HTML 渲染往返，含合并单元格的表只能输出 HTML。
- **验收**：L0 全绿（4 个已知失败除外）；JSON Schema 能生成，并能校验一份合成的 DocumentState。

### P1-2 工作区

- **内容**：`parserx/workspace/`：
  - `Workspace.create/open`；
  - `state.json`、`assets/`、`calls.jsonl`、文件锁；
  - `txn(actor)` 事务：失败时不写入，成功时版本号加一；
  - 概况与读取的查询函数，供工具层使用。
- **测试**：`tests/test_workspace.py`：
  - 创建、打开往返；
  - 事务中抛出异常时状态不变；
  - `expect_version` 冲突；
  - 调用日志逐条追加；
  - 两个进程同时写入时被锁挡住。

### P1-3 调度层

- **内容**：在 `parserx/scheduling/` 现有网关上增加：
  - **预算**：截止时间、各服务请求数、费用；请求前预留，完成后结算；超限时任务状态记为 `skipped_budget`。
  - **token 用量与费用**：`llm.py` 通过回调报告 usage；价格表放在配置里（§10.3 的定价）。
  - **`run_ordered`**：并发发出请求，结果按任务顺序交给处理函数，把阶段零的修复做成通用机制。
  - **重试**：可重试错误分类（网络、5xx、429、OCR 10010）；SDK 设 `max_retries=0`。
  - **OCR**：`job_id` 持久化，中断后恢复轮询而不是重新提交；校验返回页数等于提交页数。
- **测试**：`tests/test_scheduling.py`，全部用假服务：
  - 预算耗尽时记为 `skipped_budget`；
  - 费用结算；
  - `run_ordered` 在完成顺序被打乱时，处理顺序仍然不变；
  - 重试分类；
  - 恢复 `job_id`；
  - 页数不符时报失败。
- **验收**：L0；v1 的 L1 仍然 PASS（v1 也走同一个网关）。

### P1-4 内容获取

- **`parserx/content/pdf_native.py`**：
  - 用 PyMuPDF 的 dict 结构，每一行生成一个账目条目（`native_pdf`，记录字符数）；
  - 每个文本块生成一个 Block，带 PdfAnchor 和 `TextStyle`；
  - 图片生成 Asset（原图）和 figure Block，复用 v1 `image_extract` 的 ImageMask 反色处理；
  - 原生层质量判定：复用 v1 页面分类的信号（乱码比例、图像覆盖率、矢量文字）。判定失败的页交给扫描页引擎。
- **`parserx/content/scan.py`**：
  - paddleocr 引擎：渲染整页，或组装单页子 PDF（`no_new_id`），经网关提交；
  - `parsing_res_list` 转成 Observation（`image_px` 坐标 + 到 `page_pt` 的变换），再转成 Block；标签映射只在 `layout/labels.py` 中做；
  - 表格经 `TableGrid.from_html` 转成 table Block。
- **`parserx/content/docx.py`**（R1、R3）：
  - 按正文顺序读取段落，保留 `w:ins` 与 `w:moveTo` 的文字，把 `w:del` 与 `w:moveFrom` 记为排除；
  - 表格转成 TableGrid（处理 `gridSpan` / `vMerge`）；
  - 图片（`a:blip` 的 `r:embed` 指向媒体文件）生成 Asset；
  - 域代码只取结果文字，丢弃指令文字；
  - 显式分页与分节切成 PageState 段；
  - 样式继承链、大纲级别、编号填入 `TextStyle`；
  - 暂不支持的元素（文本框、脚注、批注、嵌套表格）记入 warnings，并在账目中记为 `failed`，原因写明"阶段一不支持"。
- **`parserx/content/select.py`**：选择步骤。
  - 原生与扫描两路择一，写 `content_source` Decision；
  - 复核候选的接受门：修改位置有图像证据、数字与证据不冲突（迁移 §11.5 第一条）、结构合法。通过则采用，写 `review_accept` Decision。
- **测试**：用 fitz 生成的合成 PDF、python-docx 加手写 XML 构造的 DOCX（含插入、删除、合并单元格）、合成的 `parsing_res_list` 做夹具。不使用 ground truth 内容。
  - `tests/test_content_pdf.py`
  - `tests/test_content_docx.py`：修订最终视图、表格合并、分页分节、样式证据
  - `tests/test_content_scan.py`：坐标变换、表格转换
  - `tests/test_select.py`：接受门的三项检查，数字冲突时不得覆盖

### P1-5 去向检查

- **内容**：`parserx/accounting/check.py` 实现 `check(state) -> CheckResult`。
  - 账目恒等式：`discovered = output + merged + duplicate + excluded + failed`；
  - 未归属条目、非法引用、缺失资源、各页状态；
  - 文档状态为 complete、partial 或 failed，其中 partial 要列出缺失原因；
  - 未归属条目或非法引用存在时，`exportable=False`。
- **测试**：`tests/test_accounting.py`：
  - 账目平衡；
  - 有未归属条目时拒绝导出；
  - 预算跳过时状态为 partial，但仍可导出；
  - 修订删除的条目计入 excluded。

### P1-6 渲染与导出

- **内容**：`parserx/render/markdown.py` 实现 §4.5 的 Markdown 契约：
  - ATX 标题；段落内不保留硬换行；
  - 表格由 TableGrid 生成 GFM 或 HTML；
  - 图片行后紧跟 `> [图片语义]` 引用块（R5）；
  - PDF 输出 `<!-- PAGE n -->`，DOCX 输出 PAGE-BREAK / SECTION 锚点；
  - excluded 块不输出。

  `parserx/render/sidecar.py` 导出 `.blocks.json`，并用 P1-1 的 Schema 校验。
- **测试**：`tests/test_render.py`：
  - 各类块的渲染；
  - 与评测的兼容性：渲染结果经 `canonicalize` 后，图片语义块被剔除，表格能被 `find_tables` 找到；
  - sidecar 通过 Schema 校验。

### P1-7 工具与 CLI

- **内容**：
  - `parserx/tools/`：信封（`cost` 取自计数器快照，`failures` 为列表）、`DocText`；
  - 七个工具，外加 `workspace init` 与 `tool schema <name>`；
  - 在 `parserx/cli.py` 中加 `tool` 和 `workspace` 子命令；stdout 只输出信封，退出码 0 / 1 / 2；
  - `describe_figure` 按 §6.7 的类型化 schema 返回，带证据层级；
  - `review_table` 生成候选，并在工具结束时同步执行选择步骤（Q20）；
  - 提示词放在 `parserx/prompts/`，文件内容哈希计入缓存键；
  - `apply_structure` 的合法性检查放在 `parserx/hierarchy/legality.py`：只能引用存在的块、只能改结构类 kind、`level` 只能用于 title、层级不得跳级、同一编号模式同层级、不得形成顺序环、关系不得重复。
- **测试**：`tests/test_tools_contract.py`：
  - 每个工具的信封 schema；
  - 文档文字只出现在 `DocText` 中；
  - 每个失败码都能触发；
  - 版本冲突；
  - 每条合法性规则都有一个被拒绝的例子；
  - 请求模型里根本没有文字字段。

  VLM 用假服务；另加少量 `live_e2e` 用例。
- **P1-7b（可选，Q25）工具形态试用**：只用 overview、read、recognize、check、export 五个工具，选一篇文档，让 Claude Code 或 Codex 试用约 30 分钟，不做调优。
  - 只检查三件事：图片是否真正进入了模型的视觉上下文、返回的 JSON 长度是否可控、失败信息能否指导下一步。
  - 结果记录到报告中，不属于阶段二的探索。

### P1-8 Skill 草稿

- **内容**：
  - `parserx/skills/` 下三份 Markdown：忠实转录与纠错、图片理解与描述、文档结构与章节组织；
  - 按 §5.4 写明目标、取证方法、输出要求、停止条件，不写阈值规则；
  - `load_skill(name) -> (text, sha256)`；
  - 文件头注明"草稿，阶段二修订"。
- **测试**：文件存在，哈希稳定，内容变化时哈希随之变化。

### P1-9 版面检测与图片路由（影子运行）

- **内容**：
  - `uv add rapid-layout onnxruntime`；
  - `parserx/layout/detector.py`：封装检测器，模型缓存在 `~/.cache`，单元测试中禁止下载；
  - `labels.py`：把三套标签映射到 BlockKind；
  - `area.py`：计算 t 与 f 的并集面积；
  - `parserx/routing/image.py`：§6.5 的路由；
  - 影子运行：对页面渲染图和嵌入图片运行检测，写入 layout Observation（无文字）、ImageRecord 和 `image_route` Decision，不影响输出。
- **测试**：
  - 用假检测器验证面积统计和路由表；
  - 标签映射完整：三套标签的每个值都有去处；
  - 一个 `live_layout` 标记的用例跑真实模型，默认跳过。
- **Q6**：阶段一只汇报检测结果；召回率需要人工标注，挪到阶段四回答。

### P1-10 固定序列运行时

- **内容**：`parserx/runtimes/pipeline.py`，直接调用工具函数（不经 CLI），依次执行：
  1. `workspace init`（提取与建账目）
  2. `recognize`（仅对原生层判定失败的页）
  3. 版面与路由影子运行
  4. `describe_figure`（受预算约束）
  5. 结构（DOCX 用确定性路径，PDF 用 `adapter:v1`，均经 `apply_structure` 写入）
  6. `check`
  7. `export`

  另外：
  - `pipeline: v1 | v2` 开关：`Pipeline.parse_result` 按开关分派，v2 返回同样的 `ParseResult`，并附 sidecar 路径；
  - 新增 `configs/regression_v2.yaml`（继承 `regression.yaml`，设 `pipeline: v2`）；
  - 冻结 run 另存 `outputs/<doc>.blocks.json`。
- **测试**：`tests/test_runtime_pipeline.py`：用合成 PDF 与合成 DOCX 加假服务端到端运行，验证去向平衡、sidecar 通过校验、输出确定（运行两次字节一致）。

### P1-11 验收

1. v2 的 L1：先 `--core --config configs/regression_v2.yaml --allow-calls` 录制响应，再离线跑 `--repeat 2`，两遍一致。
2. 在验收文档上做一次 v2 冻结（空缓存，全部真实请求），例如 `--include text_table01 receipt simple_doc01 ocr_scan_jtg3362 --config configs/regression_v2.yaml --freeze p1_v2_toolkit`；并用 `--replay` 验证。
3. 以 v1 冻结基线为 `--baseline` 比较，逐项核对 §0 的退出条件。
4. 报告写入 `eval_reports/<日期>_p1_toolkit_acceptance.md`；指导 §12 阶段一标 ✅。

## 5. 每项完成时的固定动作

1. 跑 L0 并汇报（通过数、已知失败数、耗时）。
2. 从 P1-3 起，每项结束时确认 v1 的 L1（`--core --repeat 2`）仍然 PASS；从 P1-10 起，v2 的 L1 也要 PASS。
3. 更新指导 §12 与 §15；有新决策时写入 §14。
4. 需要评测报告的项目，报告写入 `eval_reports/`，文件名含日期和阶段号。
5. 每项提交一次（用户已习惯"提交并继续"），提交信息说明改动针对的信号和验证范围。

## 6. 待决问题（新会话开始时确认）

| 编号 | 问题 | 建议 |
|---|---|---|
| Q23 | 阶段一的验收集是否加入扫描文档 | 加 ocr_scan_jtg3362，标准见 §0 第 3 条。理由：原定三篇都不含扫描页，阶段二需要扫描页工具 |
| Q24 | 阶段一的结构（标题）来源 | DOCX 走样式与大纲级别的确定性路径；原生 PDF 用临时适配器 `adapter:v1` 复用 v1 不调用服务的标题判断，阶段四删除。理由：heading_f1 是退出条件，而 §6.8 在阶段四 |
| Q25 | 是否在 P1-7 之后做一次工具形态试用 | 做，约 30 分钟，一篇文档，不调优。理由：尽早发现图片进不了视觉上下文、JSON 过长这类接口问题，比到阶段二再改成本低 |
| Q26 | DOCX 修订记录的处理（Q9 的一部分） | 按"接受全部修订"后的最终视图提取；被删除的文字计入账目、去向为 excluded；sidecar 标注文档含修订。理由：simple_doc01 的标注就是最终视图，这也是阅读者看到的内容 |
