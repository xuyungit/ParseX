# 下一轮会话启动提示词（2026-09-26 生成，阶段五）

复制下面整段作为新会话的第一条消息。

---

你在 /Users/xuyun/Projects/ParserX 工作。这是一个把 PDF / DOCX（含扫描件与图片）转成适合大模型使用的 Markdown 的工具。

- **当前**：v2 重建已完成阶段零至四。设计与研发指导在 docs/redesign_guide.md，所有决策以它为准。
- **产品现状**：`parserx parse` 默认走混合方案（Q13）：先跑固定流水线；文档摘要里有待核对项或状态不是 complete，就交给 Agent（Codex）处理。阶段四全语料运行 B：混合方案在文字、表格、标题上的平均值都高于 v1（eval_reports/2026-09-26_p4-7_full_run_b.md）。
- **本轮目标**：阶段五"清理"（指导 §12）：删除死代码与死依赖（§11.4），§11.5 的正确性要求全部落到 v2 的测试，README 反映真实状态。

**先做五件事**，任一不通过先处理，不要绕过：

1. 读指导 §0、§2、§3、§9.5、§10.4、§11、§12、§14（尤其 Q13、Q40、Q56、Q59、Q69–Q71），再读 eval_reports/2026-09-26_p4-7_full_run_b.md 与 docs/annotation_changes.md。
2. 运行 `uv run python scripts/check_services.py`，OCR、LLM、VLM 三项都要 OK。
3. 运行 L0：`uv run pytest -q --ignore=tests/test_live_e2e.py`。预期 918 通过、4 个已知失败（v1 的旧测试，不要修；阶段五随模块删除，要求已登记在 §11.5）。
4. 运行两个 L1，都要 PASS：
   - `uv run python scripts/regression_test.py --core --repeat 2`
   - `uv run python scripts/regression_test.py --core --config configs/regression_v2.yaml --repeat 2`
5. 回放两个冻结 run，都要 PASS：
   - `uv run python scripts/regression_test.py --replay eval_runs/2026-09-23_p0_v1_gpt-6-luna`
   - `uv run python scripts/regression_test.py --config configs/regression_v2.yaml --replay eval_runs/2026-09-25_p4_v2_toolkit`

**然后**：

- 先写阶段五的分解 docs/v2_phase5_plan.md（格式同前几个阶段：事实、工作项、退出条件、待决问题），请我确认后再动代码。必须回答：
  - v2 的标题仍依赖 adapter:v1（Q59）：删除 v1 之前，是迁移它依赖的排版判断，还是由 v2 自有路径替代；无论哪种，都要在全语料上验证不降低；
  - `pipeline: v1` 开关、v1 的 L1 与 v1 冻结 run 的去留；
  - §11.4 删除清单与依赖清单（§10.4）逐项核对；
  - Q71（被遮挡的文字）等待决问题。
- 需要我拍板的问题集中列出一次问，附上建议。

**约束**：

- **环境**：只用 uv（`uv run python …`、`uv add …`）。
- **标注**：改 ground_truth/ 下的任何文件，都要先经我同意；有明确证据证明原标注不合理、而我们的解析更合理时，要改标注，并记入 docs/annotation_changes.md。
- **规则要谨慎**：
  - 不为单篇文档加规则或阈值；
  - 带魔法数字的规则尤其要谨慎：能与文档自身比较的就不用绝对阈值，非用不可的写明是测量容差，并登记理由。
- **信号与判定**（Q56）：
  - 信号只指路，由 Agent 看图判断。信号来自正确输出必须满足的性质（守恒、独立读数一致、文档自身一致、结构与版面一致），不来自见过的错误样子。
  - 新信号或改动要在全语料上衡量：错误被指到的比例上升，指到处确有错误的比例不明显下降。
  - 直接改输出的判定，只在证据强、并在全语料上验证不降低时才做。
  - Agent 按需读取（看图、再识别）是它的核心优势，工具与门要让有独立证据的修正通过；门只放行程序能核实的证据，不采信 Agent 的说法。
- **定位**：遇到某篇文档处理得不好，先按指导 §9.5 定位是哪一层的问题（内容获取与算法、待核对信号、接受门、工具能力、Agent 判断），再决定修哪里。
- **优先级**：信息保全与使用体验 > 标题层级 > 其他指标。
- **测试**：新模块先写测试（§13），测试用合成输入，不用标注内容。
- **远程调用**：只走 ServiceGateway；并发结果按任务顺序生效；只对传输错误和无法解析的响应重试；同一输入输出逐字节相同。
- **两类模型分开**（Q40）：主 Agent 用 Codex gpt-6-sol、推理强度 medium，每次在命令行上显式指定；服务层 LLM / VLM 保持 gpt-6-luna。
- **Agent 运行的卫生**：
  - 真实的 Agent 运行（验证用）沿用实验装置的规则：实验根目录在仓库外，Agent 看不到 expected.md、评分或仓库，服务密钥不写进实验目录，每次运行后做路径审计与完整性检查；
  - 产品里的混合运行时也不把服务密钥传给 Agent 的进程；
  - 不读取、不复制 ~/.codex 下的任何凭据。
- **v1 与冻结 run**：
  - v1 代码只在修复明确缺陷时改动；v1 与 v2 的 L1 必须一直 PASS；
  - 改动影响验收文档的输出时，以空缓存重新冻结，并用 `--replay` 验证。
- **从简**：典型文档要能正常处理，新文档出问题时再打磨；不做计划之外的扩展。第三方 Agent 框架（Pi、自研薄循环或其他，见 docs/v2_agent_runtime_research.md 与 Q62–Q64）是以后单独的议题，本轮只保证 Agent 运行时接口可替换。

**每完成一项**：

- 报告 L0 的数字，并确认两个 L1 PASS；
- 更新指导 §12、§15，新的决策写入 §14；
- 提交一次（不推送）；
- 评测报告写入 eval_reports/，文件名含日期和阶段号；
- 然后向我汇报并继续。
