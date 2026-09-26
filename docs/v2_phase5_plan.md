# 阶段五"清理"工作分解

> **状态**：已确认（2026-09-26）：Q72–Q77 均按建议。P5-1 ✅（[报告](../eval_reports/2026-09-26_p5-1_correctness_tests.md)）；P5-2 ⬜。
>
> **依据**：
> - [redesign_guide.md](redesign_guide.md) §10.4（依赖）、§11.4（删除清单）、§11.5（正确性要求）、§12（阶段五：测试全绿、依赖清单与 §10.4 一致）、§14 Q59、Q61；
> - [next_session_prompt.md](next_session_prompt.md) 列出的已知事实与要回答的问题；
> - [运行 B 报告](../eval_reports/2026-09-26_p4-7_full_run_b.md)、[P4-3 标题报告](../eval_reports/2026-09-25_p4-3_headings.md)。
>
> **执行原则**（用户）：先让测试覆盖要保留的行为，再删除；每删一批跑 L0 与 L1；删除只经 git 提交；从简，不做计划之外的扩展。

## 0. 目标与退出条件

**目标**：仓库里只剩 v2 一条路径，文档与命令行如实反映它。v1 的代码（`processors/`、`providers/`、`builders/`、`assembly/`、`verification/`、`models/elements.py`、`text_utils.py`、`pipeline.py` 的 v1 部分）共约 12,500 行。

1. 标题不再依赖 v1 的代码（adapter:v1 退役），标题质量不降低；
2. §11.4 与 §10.4 的删除逐项执行，每一项确认没有引用；
3. §11.5 的 7 条正确性要求都有 v2 的测试；
4. README 与 `parserx --help` 只写真实可用的功能。

**退出条件**：

1. L0 全绿：v1 的 4 个已知失败随模块删除，不再有已知失败。
2. L1（只剩 v2 的一个）PASS；v2 冻结 run 以空缓存重新冻结，`--replay` PASS。
3. **标题不降低**（Q72、Q73）：
   - 固定流水线，全语料离线：heading_f1 与角色 F1 的平均值不低于现在（带 adapter:v1）；
   - 混合方案，全语料真实运行：平均值不低于运行 B；
   - 两者都逐篇列出下降超过 0.005 的文档，并按 §9.5 定位。
4. 信息类指标（char_f1、表格 F1）逐篇与现在的固定流水线相比变化不超过 0.005，超过的逐条说明。
5. `pyproject.toml` 的依赖与 §10.4 一致；`grep` 确认被删的模块与包没有引用。
6. 指导 §0、§10.4、§11、§12、§14、§15 更新；报告写入 `eval_reports/`。

## 1. 进入阶段五时的事实（2026-09-26 核实）

### 1.1 基线

| 项 | 结果 |
|---|---|
| 服务 | `check_services.py` 三项 OK |
| L0 | 919 通过、4 个已知失败（v1 的 `test_image_processor` 1、`test_line_unwrap` 2、`test_verification` 1）、5 跳过 |
| L1 | v1 的 L1 PASS；v2 的 L1 PASS |
| 冻结 run 回放 | v1 `2026-09-23_p0_v1_gpt-6-luna` PASS；v2 `2026-09-25_p4_v2_toolkit` PASS |
| 全语料固定流水线（离线，p4-7b 的缓存） | 46 s，31 篇，与冻结时一致 |

### 1.2 v2 对 v1 代码的依赖（按 import 核实）

| v2 模块 | 用到的 v1 代码 | 处理 |
|---|---|---|
| `runtimes/v1_structure.py`（adapter:v1） | PDF：`PDFProvider`、`MetadataBuilder`、`ReadingOrderBuilder`、`HeaderFooterProcessor`、`CodeBlockProcessor`、`ChapterProcessor`；DOCX：`DOCXProvider`（docling）、`MetadataBuilder`、`ChapterProcessor` | P5-2 替代 |
| `content/pdf_native.py` | `providers.pdf._join_block_lines`、`_reconstruct_line_from_chars`；`processors.text_clean.normalize_fullwidth_ascii` | P5-3 迁入 `content/` |
| `reading/compare.py` | `processors.text_clean.normalize_fullwidth_ascii` | P5-3 |
| `eval/metrics.py` | `text_utils.compute_edit_distance`、`normalize_for_comparison` | P5-3 迁入 `eval/` |
| `eval/benchmark.py` | `builders.ocr.html_table_to_markdown` | P5-3 改用 `tables/html.py`（`TableGrid.from_html` → `to_gfm`） |
| `eval/runner.py`、`tool_eval/adapters.py`、`cli.py` | `pipeline.Pipeline` | P5-3：`Pipeline` 改为只有 v2 的薄入口（保留类名，调用方不变） |
| `runtimes/pipeline.py`、`cli.py` | `models.results.ParseResult` | 保留 `models/results.py`，只删 `models/elements.py` |

v1 模块反过来只依赖 v2 的 `config`、`services`、`scheduling`、`cache`、`tables/html.py`，删除 v1 不影响 v2。`builders/image_extract.py`（§11.1 曾列为可复用）现在只有 v1 用：v2 的 `content/docx.py` 自己读 OOXML 图片。

### 1.3 adapter:v1 在标题上的份量（本次离线测量）

方法：固定流水线，全语料 31 篇，p4-7b 冻结 run 的缓存只读回放；只替换 `process` 的标题来源，其余不变；探测脚本在会话临时目录，没有改仓库代码。v1 列为 v1 冻结输出按现行标注重算。

| 标题来源 | heading_f1 / 角色 F1（有 v1 的 24 篇） | （有标题标注的 29 篇） |
|---|---|---|
| v1 | 0.494 / 0.614 | — |
| **A 现在**：adapter:v1 + 扫描引擎标签 + DOCX 样式 | **0.582 / 0.690** | **0.523 / 0.645** |
| B 去掉 adapter:v1 | 0.258 / 0.343 | 0.218 / 0.320 |
| C B + 原生页 `layout_titles`（检测器标题 且 排版与正文不同）直接采用 | 0.435 / 0.634 | 0.377 / 0.573 |
| D B + 只看排版（比正文大或加粗，单行，≤ 60 字；按字号分三级） | 0.394 / 0.593 | 0.329 / 0.534 |
| E C 与 D 的并集 | 0.440 / 0.639 | 0.375 / 0.572 |

逐篇看，差距集中在三处：

1. **标题与下一行在同一个原生块里**：text_table01（A 1.000 → C/D/E 0）、text_table_word、receipt、text_code_block。v2 的块只记整块的主要字号与字重（`content/pdf_native._style`），标题行的排版被正文淹没；adapter:v1 靠逐行的字体信息找到标题，再用 `split` 拆出来（P4-3）。
2. **手工排版的 DOCX**：real_doc01 0.765 → 0.020、text_report01 0.714 → 0、unseen_word_spec01 0.900 → 0。v2 的 DOCX 标题只来自样式与大纲级别；加粗、放大的编号段落全靠 adapter:v1（经 docling）。
3. **层级**：C 找到的标题不少（角色 F1 0.634，接近 A 的 0.690），但层级差（heading_f1 0.435）。检测器只有两种标题标签，层级主要靠编号；adapter:v1 用文档内字号的高低排出层级。

反过来，C 在 3 篇上比 A 好：text_pic02 0.143 → 0.960、patent01 0 → 0.625、basic_report 0 → 0.500（v1 在这几篇上找不到标题）。所以 v2 的来源不是全面更差，而是缺上面三样。

结论：不能直接删 adapter:v1；v2 需要补上逐行排版、DOCX 的直接格式、按文档自身字号排层级这三样，才可能替代它。

### 1.4 v1 的其他用处

| 用处 | 现状 |
|---|---|
| `pipeline: v1` 开关 | `config.pipeline`、`parserx parse --pipeline v1`、`Pipeline` 的 v1 分支、`configs/regression.yaml`（`regression_v2.yaml` 继承它再改 `pipeline: v2`） |
| v1 的 L1 | `regression_test.py --core`，默认配置是 `configs/regression.yaml`（v1） |
| v1 冻结 run | `eval_runs/2026-09-23_p0_v1_gpt-6-luna`，阶段四所有比较的基线；`scripts/phase4_compare.py` 只读它的 `outputs/`，不跑 v1 代码 |
| 只对 v1 有效的命令行参数 | `--split-chapters`（v1 的章节拆分）、`--no-formula`、`--no-table-vlm`、`--no-llm`、`--llm-model`、`--ocr-lang`：v2 不读这些配置 |
| 只对 v1 有效的配置 | `providers`、`builders.metadata`、`builders.layout`、`builders.ocr` 中除引擎与端点外的项、`processors`、`verification`、`output`；`services.llm`（v2 不调用 LLM，只用 VLM 与 OCR） |
| 配置指纹 | 冻结 run 按配置指纹核对。`resolved_fingerprint` 只处理"后来新增的字段"，**删除字段会使旧冻结 run 的指纹对不上、`--replay` 失败** |

### 1.5 §11.4 删除清单与依赖（逐项核对）

见 §2.4、§2.5 的表格，每一项都标了现在的引用方。

### 1.6 §11.5 正确性要求

7 条中：已覆盖 2 条（1、5）；部分覆盖 4 条（2、3、6、7），其中 3、6 需要小改动，不只是测试；第 4 条与 v2 的设计相反（Q77）。详见 §2.1。

## 2. 工作项

### 2.1 P5-1 §11.5 的七条要求落到 v2 测试

核对方法：逐条读 v1 的来源测试，找 v2 中断言同一性质的测试并运行（51 条全部通过）。4 个已知失败对应第 1 条（`test_image_processor` 1）、第 2 条（`test_line_unwrap` 2）、第 3 条（`test_verification` 1）；第 7 条的 v1 测试现在通过。

| # | 要求 | v2 已有的测试 | 状态 | 要补的 |
|---|---|---|---|---|
| 1 | 数字与证据冲突不得覆盖（100 万元 → 999 万元） | `test_select.py::test_text_candidate_cannot_rewrite_numbers_against_evidence`（正是这个例子）、`::test_native_numbers_are_never_overwritten`；`test_tools_contract.py::test_a_correction_needs_the_image_and_keeps_native_numbers` | 已覆盖 | — |
| 2 | 合并块不丢来源 | `test_table_merge.py::test_merge_appends_rows_and_keeps_every_source`；`test_content_pdf.py::test_every_line_and_image_is_on_the_ledger` | 部分 | v1 的具体性质是"合并后的框是各行框的并集"。补：合成 PDF 两行左右边界不同，`extract_pdf` 得到一个块，其 `bbox` 等于两行的并集（`content/pdf_native.py` 的 `_union`） |
| 3 | 有文字重叠的图片不同时输出描述与重复正文 | `test_tools_contract.py::test_process_transcribes_scan_and_mixed_images_and_does_not_describe_scans`（已转写的扫描图不再描述） | 部分 | 原生正文压在图上、图的描述"可见文字"又重复这些字，目前没有检查。**先在全语料上统计是否出现**（p4-7b 输出中"可见文字"与相邻正文重合的条数）：出现才补守卫，没有出现只补测试，把现行行为固定下来 |
| 4 | 页眉页脚首页身份信息保留上限 | 无；`test_content_pdf.py::test_running_headers_and_page_numbers_are_excluded` 断言的恰好相反 | 按设计不适用 | v2 按 §6.9（Q34）排除全部页眉页脚，包括第 1 页，文字留在 sidecar。这条要求要么撤销，要么改做法——Q77 |
| 5 | 跨页表格列数不同不合并 | `test_table_merge.py::test_what_is_not_a_candidate`（2 列与 3 列不是候选） | 已覆盖 | 另补一条：Agent 用 `merge_tables` 合并列数不同的表，被合法性检查拒绝（`not_merge_candidate`） |
| 6 | 被跳过或纠正的图片，区域内容必须有去向 | `test_page_reading.py::test_a_superseded_reading_is_no_destination`、`test_accounting.py::test_output_item_on_a_hidden_block_is_a_silent_loss`、`test_render.py::test_a_shown_image_without_description_or_text_is_a_review_item` | 部分 | 两处漏洞，需要测试加小改动：(a) 去向检查不核对 `duplicate` 块是否有指向可见块的 `duplicate_of`（`content/select.py` 找不到重叠块时仍标为重复）——补：没有目标或目标不可见的重复块，`check` 列出并不能导出；(b) `reading/compare.py` 把任何图片块内的文字都算有承接，不看图片是否被排除——补：被排除的图片里的文字列入 `text_unaccounted` |
| 7 | 并发任务的输入不依赖完成顺序 | `test_scheduling.py::test_results_apply_in_task_order_whatever_the_completion_order`；`test_tools_contract.py::test_batch_results_do_not_depend_on_completion_order` | 部分（只覆盖结果，没覆盖输入） | 补：一批图片描述，第一张故意最慢，与逐张单独描述比较，每块的请求键（图像、提示词、上下文）相同；`recognize`、`ask_image`、公式同样用 `run_ordered`，各补一条 |

**验收**：七条各有 v2 测试（第 4 条按 Q77 的决定），测试名写进指导 §11.5 的"迁往"列；6(a)(b) 的改动在全语料上离线核对（去向检查的新项与 `text_unaccounted` 的变化逐条看）；L0、L1 通过。

### 2.2 P5-2 标题的 v2 自有路径，adapter:v1 退役（Q59、Q72）

**两个候选的比较**：

| | 候选一：把 adapter:v1 依赖的排版判断迁到 v2 | 候选二：v2 自有路径替代 |
|---|---|---|
| 内容 | 把 `PDFProvider`、`MetadataBuilder`、`ReadingOrderBuilder`、`HeaderFooterProcessor`、`CodeBlockProcessor`、`ChapterProcessor`、`DOCXProvider` 的标题相关部分搬进 `hierarchy/`、`content/` | 在 v2 的数据上补 §1.3 的三样，与已有的检测器标签、编号嵌套、合法性检查合成一条路径 |
| 规模 | 依赖闭包约 4,200 行，含 `chapter.py` 1,599 行、38 个辅助函数与 `PageElement.metadata` 自由字典 | 预计新增 300–500 行 |
| 与原则 | `chapter.py` 与自由字典正是 §11.4 要删的；其中多是针对单篇症状的守卫与关键词判断（日期、"标签：值"、目录……），与 §2.3、Q56 相悖 | 信号来自文档自身的比较（比正文大、比正文重、同文档字号排序），符合 Q56 |
| 风险 | 低（等于现状）；docling 也要留下 | 可能达不到现状；需要测量 |

**建议：候选二**，按下面的做法，并约定一个退路（Q72）。

**做法**（每步先写测试，用合成输入）：

1. **逐行排版**（`content/pdf_native.py`、`ir/observation.py`）：原生块的观测保留每一行的字号、字重、字体（`TextStyle.lines` 或同等字段，只存来源里有的值）。整块的主要排版照旧，其他用法不变。
2. **排版标题**（新 `hierarchy/typography_titles.py`）：
   - 文档的正文排版 = 按字数最多的排版（已有 `layout_titles._body_style`）；
   - 一行（或连续几行）与正文不同——字号更大，或正文不加粗而它加粗——而且独占一行或位于块首，就是标题候选；位于块首的，按 P4-3 的做法先在换行处 `split`（不改文字，照常过合法性检查）；
   - 层级：本文档里标题候选用到的字号从大到小排序，同字号不加粗的排在加粗之后；再交给 `unify_levels` 按编号统一（已有）；
   - 检测器的 `paragraph_title` 标签作佐证：与排版一致的直接采用；只有一方的，是否采用由全语料测量决定（C 与 D 的差异说明两者各有长短），不在设计时定死；
   - 不加关键词表；不加针对某篇文档的阈值。"≤ 某字数""字号大多少"这类数字若测量表明需要，只作测量容差并登记理由。
3. **DOCX 的直接格式**（`content/docx.py` 已读出段落的 `TextStyle`）：没有标题样式与大纲级别的段落，与正文排版不同（加粗、字号更大）且是短段落的，同第 2 步成为标题候选；样式已声明标题或列表项的不变（Q48 的现行做法）。
4. **合成**：`process` 的 `pdf_titles` / `docx_titles` 改用新来源，actor 改为 `program:hierarchy.typography`；`title_candidate` 信号保留，只列新来源没有采用的。
5. **退役**：删除 `runtimes/v1_structure.py` 与其测试；`process.py` 不再引用它。

**测量方案**：

- 脚本 `scripts/heading_sources.py`（把本次的探测脚本整理入库）：固定流水线、全语料、指定冻结 run 的缓存只读回放；可切换标题来源；输出逐篇与平均的 heading_f1、角色 F1，以及 char_f1、表格 F1 的变化。
- 对照：A（现在）与 v1；每次改动都跑全语料（约 1 分钟，无费用）。
- 固定流水线达标后，混合方案在新快照上真实运行全语料（约 $4、40 分钟），与运行 B 比；卫生审计照旧。
- 调参只用有标注的语料；隔离验证集照 §2.4 的规则不参与调参。

**退路**（Q72）：若固定流水线用尽上面的做法仍低于现在，按下面两种之一处理，由用户决定：

- 甲：接受差距，由混合方案弥补——要求混合方案不低于运行 B；
- 乙：保留 adapter:v1 所需的最小闭包（放到 `parserx/legacy/`，只供标题用），其余 v1 照删，docling 留下；以后再议。

**验收**：退出条件 3、4；`grep -rn "v1_structure\|adapter:v1" parserx tests` 只剩历史记录（Decision 的 actor 字符串在旧冻结 run 里）。

### 2.3 P5-3 v2 与 v1 解耦（先搬，后删）

| 搬什么 | 从 | 到 |
|---|---|---|
| `normalize_fullwidth_ascii` | `processors/text_clean.py` | `content/text.py`（连同 `test_text_clean.py` 中覆盖它的用例） |
| `_join_block_lines`、`_reconstruct_line_from_chars` | `providers/pdf.py` | `content/pdf_native.py`（连同 `test_pdf_provider.py` 中覆盖它们的用例） |
| `compute_edit_distance`、`normalize_for_comparison` | `text_utils.py` | `eval/`（只有评测用） |
| `html_table_to_markdown` | `builders/ocr.py` | `eval/benchmark.py` 改用 `TableGrid.from_html(...).to_gfm()`（先写一条等价性测试） |
| `Pipeline` | `pipeline.py`（v1 + v2 分支） | 同一文件只留 v2：`parse_result`、`parse_result_to_dir` 转给 `runtimes/pipeline.py`；类名与签名不变，`eval/runner.py`、`tool_eval`、测试不用改 |

**验收**：`grep` 确认 v2 模块（`parserx/` 下除 v1 目录外）不再 import `processors`、`providers`、`builders`、`assembly`、`verification`、`models.elements`、`text_utils`；L0、L1、v2 回放通过（输出逐字节不变）。

### 2.4 P5-4 删除 v1

先打本地标签 `v1-final`（不推送），以后要看或跑 v1，可从这个标签建工作树。

**删除清单**（§11.4 逐项，另加核对时发现的）：

| 删除 | §11.4 | 删除前的引用方（核实） |
|---|---|---|
| `processors/chapter.py` | ✓ | adapter:v1（P5-2 后无）、v1 `Pipeline`、`test_chapter*.py` |
| `processors/image.py` | ✓ | v1 `Pipeline`、`test_image_processor.py` |
| `processors/vlm_review.py` | ✓ | v1 `Pipeline`、`test_vlm_review_processor.py` |
| `builders/ocr.py` | ✓（结果整合与去重；实际整个文件只有 v1 与 `eval/benchmark.py` 用） | P5-3 后只剩 v1 |
| `models/elements.py` | ✓ | v1 全部；`models/results.py` 保留 |
| `processors/` 其余（`code_block`、`content_value`、`formula`、`header_footer`、`line_unwrap`、`table`、`text_clean`、`base`） | 另加 | 只有 v1 `Pipeline` 与 adapter:v1；v2 各有自己的实现（`content/`、`tables/`、`tools/formulas.py`） |
| `providers/`（`pdf.py`、`docx.py`、`base.py`） | 另加（§11.2 已写"v1 的 provider 保留到阶段五"） | 同上；P5-3 后 v2 不用 |
| `builders/`（`metadata`、`reading_order`、`image_extract`） | 另加 | 只有 v1 与 adapter:v1 |
| `assembly/`（`chapter`、`crossref`、`markdown`） | 另加（§11.2：由 `render/` 替代） | 只有 v1 |
| `verification/` | 另加（§11.2：由去向检查替代） | 只有 v1 |
| `text_utils.py` | 另加 | P5-3 后无 |
| v1 的测试：20 个只测 v1 的文件（`test_chapter*`、`test_image_processor`、`test_line_unwrap`、`test_verification`、`test_pdf_provider` 等）；`test_pipeline.py` 中 `pipeline="v1"` 的用例；`test_runtime_pipeline.py` 中测 adapter:v1 的一条 | 另加 | 删除前确认 P5-1 已覆盖 §11.5；`test_tool_eval.py`、`test_live_e2e.py` 改为走 v2 |
| `pipeline: v1` 开关、`--pipeline` 参数、`_cmd_parse_v1` | Q61 | — |
| 只对 v1 有效的命令行参数（§1.4） | 另加 | — |
| 只对 v1 有效的配置段：`config/schema.py`、`parserx.yaml`、`config/template.yaml`、`configs/*.yaml` | 另加 | `eval/reporting.py` 的报告头也要改 |
| 根目录 `eng.traineddata` | ✓ | 无引用；未入 git，删除不可经 git 恢复（tesseract 的公开数据，可重新下载） |

**顺序**（每一批：删 → `grep` 确认无引用 → L0 → L1 → v2 回放 → 提交）：

1. 命令行与 `pipeline: v1` 开关（入口先关，后面删的代码就确实无人调用）；
2. `processors/`、`builders/`、`assembly/`、`verification/`、`providers/`、`models/elements.py`、`text_utils.py` 及其测试；
3. 配置段与配置模板；同时让 `resolved_fingerprint` 忽略已删除的字段（先写测试：旧冻结 run 的配置去掉这些字段后指纹不变），使 v2 的冻结 run 仍能回放；
4. 回归配置合并：`configs/regression.yaml` 改为 v2 的回归配置，`regression_v2.yaml` 保留为继承它的一行（旧命令与旧冻结 run 的 `command` 仍然有效）；`regression_test.py --core` 就是 L1。

**v1 冻结 run**：目录不删（约束）。删除 v1 后不能再回放；它的 `outputs/` 与 `.rescored-2.2.json` 仍是比较基线，`phase4_compare.py` 不受影响。指导 §0 注明"只作静态基线"。

### 2.5 P5-5 依赖

| 包 | §10.4 | 现在的引用 | 处置 |
|---|---|---|---|
| docling | 阶段四移除 | `providers/docx.py`、adapter:v1、`test_runtime_pipeline.py` 一条测试 | P5-2、P5-4 后删除（主依赖与 `docx` 可选组都删）；`eval/freeze.py` 的 `_PACKAGES` 去掉 |
| pypdf、llama-parse | 删除 | 无 | 删除 |
| pdfplumber | 移到 `bench` | 只有 `tool_eval/adapters.py` | 移到 `bench` 可选组；`tool_eval` 顶层 import 改为用到时再 import |
| python-docx | 保留 | `tool_eval`、`builders/image_extract.py`（v1）、测试造 DOCX、`scripts/make_injection_doc.py` | v2 产品代码不用：移到 `dev` 组与 `bench` 组 |
| huggingface-hub | `bench` | `eval/benchmark.py` | 不变 |
| fonttools（dev） | — | 无引用 | 删除 |
| pymupdf 1.27 → 1.28 | 阶段五升级 | 使用中 | Q76 |
| openai 2.x → 3.x | 验证后再升 | 使用中 | 不在阶段五（列入"以后"） |

**验收**：`uv sync` 后 L0、L1 通过；`uv tree` 中不再有 docling；§10.4 按实际更新。

### 2.6 P5-6 README 与 `parserx --help`

- `parserx --help` 与 `parse --help` 只列 v2 真实读取的参数；`tool-eval`、`compare`、`eval` 的说明核对一遍。
- README 重写"Processing Pipeline""Quick Start""Configuration""Project Status""Project Structure"：混合方案与只用固定流水线、输出包（`.md`、`.blocks.json`、摘要）、Codex 的前提、中断续跑、`--json`、`parserx init`；v1 的历史指向 `docs/architecture.md` 与 `v1-final` 标签。
- 加一条测试：`parse --help` 中的每个参数都被 v2 读取（防止再出现无效参数）。

### 2.7 P5-7 收尾

- 空缓存重新冻结 v2 的验收 run（P5-2 改变了验收文档的标题，必须重新冻结），`--replay` 验证；
- 全语料固定流水线重新冻结，混合方案全语料真实运行一次（与 P5-2 的混合方案运行可合并）；
- 报告 `eval_reports/2026-xx-xx_p5_cleanup.md`：退出条件逐条、删除清单与行数、依赖变化；
- 指导 §0、§10.4、§11、§12、§14、§15，`next_session_prompt.md`。

## 3. 顺序与依赖

```
P5-1 §11.5 测试 → P5-2 标题（测量 → 实现 → 混合方案验证）→ P5-3 解耦 → P5-4 删除 v1（四批）→ P5-5 依赖 → P5-6 README 与 --help → P5-7 收尾
```

- P5-1、P5-3 不改输出，可与 P5-2 的测量交错做；
- P5-4 必须在 P5-1、P5-2、P5-3 之后：先有测试与替代，再删；
- P5-2 若触发退路乙，P5-4 与 P5-5 中与 adapter:v1 闭包、docling 相关的删除跳过，其余照做。

## 4. 每项完成时

- 先写测试（合成输入，不用标注内容）；
- 跑 L0 与 L1（P5-4 之前两个，之后一个）；
- 影响验收文档输出时，空缓存重新冻结并用 `--replay` 验证；
- 更新指导 §12、§15，新决策写入 §14；
- 提交一次（不推送）；报告写入 `eval_reports/`。

## 5. 待决问题

| # | 问题 | 建议（2026-09-26 用户均按建议决定） |
|---|---|---|
| Q72 ✅ | adapter:v1 的出路：候选一（迁移 v1 的排版判断）还是候选二（v2 自有路径）；达不到时走哪条退路 | **候选二**（§2.2）。退路建议**甲**（混合方案不低于运行 B 即可），理由：v1 的闭包正是要删的规则堆，留下它等于阶段五没有完成；混合方案本来就是默认 |
| Q73 ✅ | "标题不降低"的对照 | 固定流水线对现在（0.582 / 0.690，24 篇），混合方案对运行 B（0.756 / 0.868）；平均值不降，逐篇下降超过 0.005 的列明并定位。不以 v1 为对照（现在已高于 v1） |
| Q74 ✅ | v1 的去留 | 删除 `pipeline: v1` 开关与 `--pipeline`、删除 v1 的 L1；v1 冻结 run 目录保留作静态基线；删除前打本地标签 `v1-final` |
| Q75 ✅ | 只对 v1 有效的参数与配置 | 删除 `--split-chapters`、`--no-formula`、`--no-table-vlm`、`--no-llm`、`--llm-model`、`--ocr-lang` 与对应配置段。旧配置文件里多出的键照旧被忽略（pydantic 默认），用户的全局 config.yaml 不会因此报错；`services.llm` 从模板与 `check_services.py` 去掉（v2 不调用 LLM），需要时再加。扫描引擎的配置仍在 `builders.ocr`，**不改路径**（改了会使已有的全局配置失效） |
| Q76 ✅ | pymupdf 1.27 → 1.28 是否在本阶段升级（§10.4 原定阶段五） | 放在最后单独一项：升级后全语料离线对比，输出不变或只变好就升，否则推迟并记录原因 |
| Q77 ✅ | §11.5 第 4 条（页眉页脚第 1 页身份信息保留上限）怎么处理 | **撤销这条要求**：v2 已按 §6.9 与 Q34 排除全部页眉页脚，文字在 sidecar 可查，header_footer_cleanup 上的分数差按标注写法列明；在 §11.5 注明撤销与依据 |

## 6. 以后（不在阶段五范围，由用户决定是否纳入）

- Agent 定标题层级不稳定（运行 A、B 之间，real_doc01 等）；
- 公式待核对项多时逐项交 Agent，成本高：paper_chn02 有 68 项，花了 $1.01；可分批或按页交；
- 扫描 PDF 上不可见的 OCR 文字层可作为独立读数，用于守恒信号；
- DOCX 页面层（Q69 推迟）；
- 第三方 Agent 框架（Q62–Q64）；
- openai 3.x 升级。
