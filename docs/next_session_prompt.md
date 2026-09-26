# 下一轮会话启动提示词（2026-09-26 生成，阶段五之后）

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

## 先做四件事

任一不通过先处理，不要绕过：

1. 阅读：
   - 指导 §0、§2、§3、§9.5、§12、§14，尤其 Q13、Q40、Q56、Q72、Q78；
   - 阶段五退出报告全文；
   - eval_reports/2026-09-26_p5-2_headings.md；
   - docs/annotation_changes.md。
2. 运行 `uv run python scripts/check_services.py`：扫描引擎与 VLM 两项都要 OK。
3. 运行 L0：`uv run pytest -q --ignore=tests/test_live_e2e.py`。预期 581 通过，无已知失败。
4. 运行 L1 与两个冻结 run 的回放，都要 PASS：
   - `uv run python scripts/regression_test.py --core --repeat 2`
   - `uv run python scripts/regression_test.py --replay eval_runs/2026-09-26_p5-2_v2_toolkit`
   - `uv run python scripts/regression_test.py --gt-dir ground_truth --gt-dir ground_truth_public --replay eval_runs/2026-09-26_p5-7_fixed_full`

## 然后

请我从下面的清单（退出报告 §4）选定本轮主题，再写分解（事实、工作项、顺序、退出条件、待决问题），请我确认后再动代码：

1. **Agent 的标题修改有得有失**：混合方案的标题与固定流水线持平（0.697 / 0.699），Agent 在 4 篇上改好、3 篇上改坏。同类问题是 Agent 定层级不稳定。
2. **只有一种证据的标题**：编号或排版之一，固定流水线不采用，Agent 也少补。例如 real_doc01 的"三、采购文件的发售："、receipt 的"账单与付款"。
3. **公式待核对项多时逐项交 Agent，成本高**：paper_chn02 本次 $1.56。
4. **扫描 PDF 上不可见的 OCR 文字层可作为独立读数**。
5. **DOCX 页面层**（Q69）。
6. **第三方 Agent 框架**（Q62–Q64）。
7. **依赖升级**：openai 3.x；pymupdf 1.28（Q76 推迟：文字层分块方式改变）。

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
