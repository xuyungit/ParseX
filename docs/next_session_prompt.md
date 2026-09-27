# 下一轮会话启动提示词（2026-09-26 生成，阶段五之后；Q79–Q81 之后更新）

复制下面整段作为新会话的第一条消息。

---

你在 /Users/xuyun/Projects/ParserX 工作。这是一个把 PDF / DOCX（含扫描件与图片）转成适合大模型使用的 Markdown 的工具。设计与研发指导在 docs/redesign_guide.md，所有决策以它为准。

**当前**：v2 重建的阶段零至五全部完成。只剩一条流水线：工作区 + 工具包 + 程序约束。

- `parserx parse` 默认走混合方案：先跑固定流水线，有待核对项时交给 Codex Agent；
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

## 先做四件事

任一不通过先处理，不要绕过：

1. 阅读：
   - 指导 §0、§2、§3、§9.5、§12、§14，尤其 Q13、Q40、Q56、Q72、Q78、Q81；
   - 阶段五退出报告全文；
   - eval_reports/2026-09-26_p5-2_headings.md、2026-09-26_headings_discussion.md、2026-09-26_skim_reading_method.md；
   - docs/annotation_changes.md。
2. 运行 `uv run python scripts/check_services.py`：扫描引擎与 VLM 两项都要 OK。
3. 运行 L0：`uv run pytest -q --ignore=tests/test_live_e2e.py`。预期 600 通过，无已知失败。
4. 运行 L1 与两个冻结 run 的回放，都要 PASS：
   - `uv run python scripts/regression_test.py --core --repeat 2`
   - `uv run python scripts/regression_test.py --replay eval_runs/2026-09-26_q80_v2_toolkit`
   - `uv run python scripts/regression_test.py --gt-dir ground_truth --gt-dir ground_truth_public --replay eval_runs/2026-09-26_q80_fixed_full`

## 然后

请我从下面的清单（退出报告 §4）选定本轮主题，再写分解（事实、工作项、顺序、退出条件、待决问题），请我确认后再动代码：

1. ~~Agent 的标题修改~~（Q81）、~~绝对层级~~（Q82，指标 2.3）、~~页眉里的部分名~~（排除）、~~real_doc01 标注~~、~~编号同级信号~~（Q83 不加）。剩下：
   - **Q84 `process` 的设计**：产品中 Agent 不再拿到 `process`，待办清单由 `overview` 返回——待用户确认；
   - **画在表格里的节名**（表单式文档整页是表格，unseen_scan_form01、unseen_pdf_tables01）：输出作为表格行保留，标注把它们提成标题。
2. ~~只有一种证据的标题~~：Q83。
3. **公式待核对项多时逐项交 Agent，成本高**：paper_chn02 本次 $1.56。
4. **扫描 PDF 上不可见的 OCR 文字层可作为独立读数**。
5. **DOCX 页面层**（Q69）。
6. **第三方 Agent 框架**（Q62–Q64）。
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
