# 下一轮会话启动提示词

（2026-09-26 生成，阶段五之后；Q79–Q86 之后更新，2026-09-27）

> **2026-09-29 更新**：本轮主题已定为内容获取的整体设计（原生页按区域、Word 页面层与矢量图、Agent 看图对比），计划与新会话的启动清单在 [v2_content_plan.md](v2_content_plan.md) §0，先读它；下面的通用清单仍适用。

复制下面整段作为新会话的第一条消息。

---

你在 /Users/xuyun/Projects/ParserX 工作。这是一个把 PDF / DOCX（含扫描件与图片）转成适合大模型使用的 Markdown 的工具。设计与研发指导在 docs/redesign_guide.md，所有决策以它为准。

**当前**：v2 重建的阶段零至五全部完成。只剩一条流水线：工作区 + 工具包 + 程序约束。Agent 的工具是四个：`read_draft`、`view_source`、`edit_draft`、`submit_draft`（Q85，指导 §5.1、docs/v2_toolkit_review.md）；`run_pipeline` 做出初稿、`export` 写出输出包，都不给 Agent。工具的契约是请求模型（Q86）：命令行参数、任务说明里的工具参考、函数定义都由它生成。

- `parserx parse` 默认走混合方案：先跑固定流水线，有待核对项时交给 Agent（默认 Codex；`runtime.agent.engine: loop` 用自己的函数调用循环，Q86）；
- `--runtime fixed` 只用固定流水线；
- v1 已删除，本地标签 `v1-final` 保留。

**最终测量**（阶段五退出，修订后的标注）：

| | char_f1 | 表格 F1 | heading_f1 | 角色 F1 |
|---|---|---|---|---|
| v1 | 0.890 | 0.775 | 0.509 | 0.608 |
| 固定流水线 | 0.950 | 0.866 | 0.699 | 0.805 |
| 混合方案 | 0.952 | 0.875 | 0.697 | 0.806 |

报告：eval_reports/2026-09-26_p5-7_cleanup_exit.md。

**阶段五之后**：

- Q79：标题只对有大纲的文档计分；
- Q80：依赖升级，段落由版面检测器分；
- Q81：Agent 先读懂全文再定标题（`skim` 工具，structure Skill 改为通用阅读方法）；修正再次调用 `process` 覆盖 Agent 结构决定的缺陷。
  交 Agent 的 10 篇有大纲文档，角色 F1 0.680–0.690（此前 0.664），heading_f1 持平。见 eval_reports/2026-09-26_skim_reading_method.md。
- Q82：指标 2.3（标题层级允许整篇统一差一级）；Q83：不加编号同级信号；real_doc01、patent01 标注修订。
- Q85：工具包按第一性原理重新设计为四个工具；证据存入状态；旧工具删除，探索模式退役；结构操作按结果判定层级。质量与之前持平，工具调用少三分之一以上。见 eval_reports/2026-09-27_four_tools.md。
- Q86：接口定型——请求模型是唯一契约（中文说明，命令行参数与工具参考由它生成）；一套词表（`set_role {role: H1…}`、`join`/`unjoin`）；信封精简；`read_draft` 的 `changes` 视图；自己的函数调用循环（`runtimes/loop.py`）。5 篇上循环 + gpt-6-sol 与 Codex 质量相当、用时少三成；gpt-6-luna 也能完成，标题较弱、费用低 20 倍。见 eval_reports/2026-09-27_q86_interface.md。
- Q87、Q88（外部评审之后）：Agent 从校对员升为能重新解释局部的编辑（理解记录 `note`、凭证据的例外 `override`、区域重读替换与 `unadopt`、拆开合并的表）；自己的循环有了中立对话记录与适配器（`responses`、`chat`，缓存标记）、少而成批的清理、截止前请 Agent 交稿。自己看图质量相当、费用约 2.5 倍，默认仍经视觉模型；换模型验证用了 `.env` 里 `_C` 组的 qwen3.6-plus——那是几个月前的旧配置，用户要求以后不再使用它，需要换模型或服务商时先问用户。见 eval_reports/2026-09-27_q87_q88.md、docs/v2_agent_design.md。
- 表格的分与合（T1–T7，Q89–Q92，docs/v2_tables_plan.md）：扫描表跨页只比左边、有线表恢复合并单元格、外框里的几张表拆开、三线表补回表头并移出表题、图中示意图的"表"改为文字；全语料表格 F1 0.866 → 0.914。Agent（Codex）会修看得见的表格问题，发现不了结构性的。见 eval_reports/2026-09-27_tables.md。改标注前先把原件加进 git。
- DeepSeek（`.env` 的 `_D` 组，deepseek-flash）驱动自己的循环（d1）：9 篇上与 Codex 质量相当，按标价费用约四分之一，用时多三分之一；它发现了文字层的康熙部首码位（⽤→用，4 篇 180 字），应由程序通用修正。默认仍是 Codex（包月）。见 eval_reports/2026-09-27_deepseek.md。
- 文字层的部首码位（Q96–Q99，docs/v2_radicals_plan.md）：原生 PDF 与 DOCX 读出时把康熙部首与 CJK 部首补充的码位换成 Unicode 规定的等价统一汉字（对照表原样放在 `parserx/content/data/`），每块写 Decision 列出原字符；全语料输出中的部首 180 → 0。两个冻结 run 重新冻结（`2026-09-27_radicals_fixed_full`、`2026-09-27_radicals_v2_toolkit`）。见 eval_reports/2026-09-27_radicals.md。
- 表格的待办信号（docs/v2_table_signal_plan.md，G1–G5，Q93–Q95）：本地读数在表格区域里看到、格子里没有的行挂到那张表上并写明修法；Codex 按它补回了 text_table_word 的表头和 unseen_scan_form01 的漏行；复核门槛两处误拒（下标数字、合并当成补入）已修正。见 eval_reports/2026-09-27_table_signal.md。
- 模型能力比较（docs/v2_model_comparison_plan.md，Q100 决定一、Q101–Q105）：配置有了 `models` 条目与 `use`，`check_services.py --model` 探测模型接受的参数。服务模型三者分数相当，保持 luna；Agent 按量付费时 DeepSeek 最好（接近 Codex，每次约 $0.08），GLM 相近但贵一些，luna 便宜但公式待办多时放弃。图片描述是否编造待用户看对照页判断。见 eval_reports/2026-09-28_model_comparison.md。
- 独立发布（docs/v2_release_plan.md，R1–R7，Q107–Q115）：配置分四层（包内默认 `parserx/config/defaults.yaml` → `./parserx.yaml` → 个人 `~/.config/parserx/config.yaml` → `--config`），key 只在个人配置，仓库的 `parserx.yaml` 与模板已删；`parse --agent codex|<模型> / --no-agent / --vlm <模型>`；`parserx check`（`--model` 探测、`--offline`）；开始前说清缺什么；版面模型下载到 `~/.cache/parserx/models`（校验 SHA-256）；开发命令收进 `parserx dev`；README 写清安装四步，已在干净环境从 wheel 装起走通。
- 输入与输出（docs/v2_io_plan.md，IO1–IO5，Q116–Q125）：默认只交 Markdown 与它引用的图片（摘要 `--report`、sidecar `--sidecar` 按需）；识别失败处留一行"〔未识别〕"；图片说明改为类型 + 一两句话（中位数 127 → 47 字，单张用时 4.1 → 1.9 s），`--lang zh|en`；输入可以是网址、`-r` 递归目录、图片文件，同名加 `-2`。两个冻结 run 重新冻结（`2026-09-28_io_fixed_full`、`2026-09-28_io_v2_toolkit`）：29 篇分数不变，text_pic02 与 unseen_word_spec01 因截图的文字改由扫描引擎转写而变（Q125）。
- 图片按用途处理（docs/v2_io_plan.md §7，IO6，Q125–Q129）：服务模型先写说明、判断用途，再决定是否转写。
  - 内容类（票据、证照、文件页、表格、公式）：转写，放在以"〔图片识别〕"开头的引用块里。本地读数核对完整、或 Agent 看图后关闭了漏字待办时，不显示原图；含公式的核对不了，原图保留。
  - 图画类（界面截图、图表、示意图、照片）：原图加一两句说明，图里的字不转写；说明里的数字与本地读数核对。
  - 前后的注释 `<!-- parserx:image-text src=… -->` 供下游追溯；扫描页标 `<!-- PAGE n scanned -->`。
  - text_pic02 的标注删去截图里的界面文字（Q128）。冻结 run 为 `2026-09-28_io6_fixed_full` 与 `2026-09-28_io6_v2_toolkit`：全语料只有三篇变化，都是变好。
  - 待办：本地读数的低置信行（Q129），等全语料有更多内容类图片再看。

## 先做四件事

任一不通过先处理，不要绕过：

1. 阅读：
   - 指导 §0、§2、§3、§5、§9.5、§12、§14，尤其 Q13、Q40、Q56、Q72、Q78、Q81、Q85–Q88；
   - 阶段五退出报告全文；
   - eval_reports/2026-09-26_p5-2_headings.md、2026-09-26_headings_discussion.md、2026-09-26_skim_reading_method.md；
   - docs/annotation_changes.md。
2. 运行 `uv run parserx check`：扫描引擎与服务模型两项都要 ✓（配置分层，key 在个人配置 `~/.config/parserx/config.yaml`，不再读 `.env`）。
3. 运行 L0：`uv run pytest -q --ignore=tests/test_live_e2e.py`。预期 695 通过，无已知失败（其中 `tests/test_tool_eval.py` 3 个；另一个会话在改工具对比，提交后数目会变）。
4. 运行 L1 与两个冻结 run 的回放，都要 PASS：
   - `uv run python scripts/regression_test.py --core --repeat 2`
   - `uv run python scripts/regression_test.py --replay eval_runs/2026-09-28_io6_v2_toolkit`
   - `uv run python scripts/regression_test.py --gt-dir ground_truth --gt-dir ground_truth_public --replay eval_runs/2026-09-28_io6_fixed_full`

## 然后

请我从下面的清单（退出报告 §4）选定本轮主题，再写分解（事实、工作项、顺序、退出条件、待决问题），请我确认后再动代码：

1. ~~Agent 的标题修改~~（Q81）、~~绝对层级~~（Q82，指标 2.3）、~~页眉里的部分名~~（排除）、~~real_doc01 标注~~、~~编号同级信号~~（Q83 不加）、~~`process` 的设计~~（Q85 四个工具）。剩下：
   - **画在表格里的节名**（表单式文档整页是表格，unseen_scan_form01、unseen_pdf_tables01）：输出作为表格行保留，标注把它们提成标题。
2. ~~只有一种证据的标题~~：Q83。
3. **公式待核对项多时逐项交 Agent，成本高**：paper_chn02 本次 $1.56。
4. **扫描 PDF 上不可见的 OCR 文字层可作为独立读数**。
5. **DOCX 页面层**（Q69）。
6. **Agent 运行时**（Q62–Q64、Q88）：自己的循环已能用，可换模型，但按 API 计费——用户要求在找到更好的付费方案之前，Agent 一律用 Codex（包月），循环只做离线开发（2026-09-27）；还缺对 Agent 请求的响应缓存与回放（进 L1）、是否默认改用循环、便宜模型做哪些文档、按对象返回能力信息。
9. **表格结构的待办信号**：把 Agent 引到可疑表格（表格区域里有本地读数看到、却不在任何单元格里的文字；列数远多于实际使用的表），区域重读替换才会被用上（T7：8 次运行一次也没用）。
10. **截图里的界面表格是否输出**（Q92）。
7. ~~依赖升级~~：已完成（openai 3.19；pymupdf 1.28，段落改由版面检测器分，Q80）。
8. **跨栏续接**：段落在一栏底部没说完、接到下一栏顶部，目前只在跨页时续接（阶段五之后的段落分析发现 8 处）。

## 约束

- **环境**：只用 uv（`uv run python …`、`uv add …`）。
- **标注**：
  - 标注本身由工具生成。偏离最合适的结果、不如我们的解析时，改标注，并记入 docs/annotation_changes.md（Q78）；
  - 以页面原文为证据，不以任一方的输出为证据；
  - 标注对、我们的解析差的，记录差距，不迁就；
  - ground_truth/ 多数文件不入 git，改前先备份。
- **规则要谨慎**：
  - 不为单篇文档加规则或阈值；
  - 不加关键词表；
  - 带魔法数字的规则尤其要谨慎，能与文档自身比较的就不用绝对阈值。
- **信号与判定**（Q56）：
  - 信号只指路，由 Agent 看图判断；
  - 信号来自正确输出必须满足的性质；
  - 新信号或改动要在全语料上衡量；
  - 门只放行程序能核实的证据。
- **定位**：遇到某篇文档处理得不好，先按指导 §9.5 定位是哪一层的问题。
- **优先级**：信息保全与使用体验 > 标题层级 > 其他指标。
- **测试**：新模块先写测试，用合成输入，不用标注内容。
- **远程调用**：
  - 只走 ServiceGateway；
  - 并发结果按任务顺序生效；
  - 同一输入，输出逐字节相同。
- **两类模型分开**（Q40）：
  - 主 Agent：Codex gpt-6-sol，medium，在命令行上显式指定；
  - 服务层 VLM：gpt-6-luna。
- **Agent 运行的卫生**：
  - 真实运行走 `scripts/agent_explore.py`：先 snapshot（干净的 HEAD），再 parse；
  - 实验根目录在仓库外（~/parserx-exp/）；
  - 服务密钥不写进实验目录；
  - 不读取、不复制 ~/.codex 下的任何凭据。
- **冻结 run**：
  - 改动影响验收文档的输出时，以空缓存重新冻结，并用 `--replay` 验证；
  - 被取代的冻结 run 改名保留，不删除；
  - 不删 eval_runs/、eval_reports/、ground_truth*/ 与响应缓存。
- **从简**：典型文档要能正常处理，新文档出问题时再打磨。

## 每完成一项

- 报告 L0 的数字，并确认 L1 PASS；
- 更新指导 §12、§15，新的决策写入 §14；
- 提交一次（不推送）；
- 评测报告写入 eval_reports/，文件名含日期和阶段号；
- 然后向我汇报并继续。
