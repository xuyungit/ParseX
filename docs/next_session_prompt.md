# 下一轮会话启动提示词（2026-09-26 生成，阶段五）

复制下面整段作为新会话的第一条消息。

---

你在 /Users/xuyun/Projects/ParserX 工作。这是一个把 PDF / DOCX（含扫描件与图片）转成适合大模型使用的 Markdown 的工具。

**当前**：v2 重建已完成阶段零至四。设计与研发指导在 docs/redesign_guide.md，所有决策以它为准。

**产品现状**：

- `parserx parse` 默认走混合方案（Q13）：先跑固定流水线；有待核对项或状态不是 complete，就交给 Agent（Codex gpt-6-sol）处理。
- 控制台默认中文，`--lang en` 切换英文；另有 `--json`、中断后续跑。
- 阶段四全语料运行 B：混合方案 char_f1 0.947、表格 F1 0.875、heading_f1 0.756、角色 F1 0.868；v1 分别为 0.890 / 0.775 / 0.494 / 0.614。报告：eval_reports/2026-09-26_p4-7_full_run_b.md。

**本轮目标**：阶段五"清理"（指导 §12）：

- 删除死代码与死依赖（§11.4、§10.4）；
- §11.5 的正确性要求全部落到 v2 的测试；
- README 与 `parserx --help` 反映真实状态。

## 先做五件事

任一不通过先处理，不要绕过：

1. 阅读：
   - 指导 §0、§2、§3、§9.5、§10.4、§11、§12、§14，尤其 Q13、Q40、Q56、Q59、Q69–Q71；
   - 运行 B 报告全文与 docs/annotation_changes.md；
   - 标题相关的数据见 eval_reports/2026-09-25_p4-3_headings.md。
2. 运行 `uv run python scripts/check_services.py`，OCR、LLM、VLM 三项都要 OK。
3. 运行 L0：`uv run pytest -q --ignore=tests/test_live_e2e.py`。预期 919 通过、4 个已知失败。这 4 个是 v1 处理器的旧测试，不要修；它们的要求已登记在 §11.5，阶段五迁移后随模块删除。
4. 运行两个 L1，都要 PASS：
   - `uv run python scripts/regression_test.py --core --repeat 2`
   - `uv run python scripts/regression_test.py --core --config configs/regression_v2.yaml --repeat 2`
5. 回放两个冻结 run，都要 PASS：
   - `uv run python scripts/regression_test.py --replay eval_runs/2026-09-23_p0_v1_gpt-6-luna`
   - `uv run python scripts/regression_test.py --config configs/regression_v2.yaml --replay eval_runs/2026-09-25_p4_v2_toolkit`

## 阶段五开工前已知的事实（请先核实，再写进计划）

1. **v2 的标题仍依赖 v1 的代码（Q59）**：`parserx/runtimes/v1_structure.py`（adapter:v1）调用：
   - PDF：v1 的 `PDFProvider`、`ReadingOrderBuilder`、`MetadataBuilder`、`ChapterProcessor`、`CodeBlockProcessor`、`HeaderFooterProcessor`；
   - DOCX（Q48）：v1 的 `DOCXProvider`，经 docling。

   不用 adapter:v1 时，固定流水线的 heading_f1 只有 0.249（用时 0.537，v1 0.504）：PDF 原生页和手工排版的 DOCX 的标题都靠它的排版判断。所以不能直接删除 v1。
2. **docling 还不能删**：§10.4 写的是"阶段四移除"，实际仍经 adapter:v1 在 DOCX 标题中使用。
3. **v1 的其他用处**：
   - `pipeline: v1` 开关（`parserx parse --pipeline v1`、`configs/regression.yaml`）；
   - v1 的 L1；
   - v1 冻结 run：阶段四所有比较的基线，被 `scripts/phase4_compare.py` 读取。
4. **§11.5 正确性要求**：7 条，每条都要找到或补上 v2 中对应的测试，再删旧测试。

## 然后

1. 先写阶段五的分解 docs/v2_phase5_plan.md，格式同前几个阶段：事实、工作项（改动文件、测试、验收）、顺序、退出条件、待决问题。请我确认后再动代码。计划必须回答：
   - **adapter:v1 的出路**：
     - 候选一：把它依赖的排版判断迁到 v2 的 `hierarchy/` 与 `content/`，并去掉 docling；
     - 候选二：由 v2 自有路径（版面检测器的标题、字号字重、编号）替代。
     - 无论哪种，都要在全语料上验证固定流水线与混合方案的 heading_f1、角色 F1 不降低。附上建议与测量方案。
   - **v1 的去留**：`pipeline: v1` 开关、v1 的 L1、v1 冻结 run（删除 v1 后不能再回放，但输出仍可作为比较基线）。
   - **删除清单**：§11.4 与 §10.4 逐项核对，确认每个文件与依赖真的没有引用；删除顺序；每一步怎样验证。
   - **§11.5**：7 条要求各落到哪个 v2 测试。
2. 阶段四留下的以下几项不在阶段五范围内。只列进计划的"以后"一节，由我决定是否纳入：
   - Agent 定标题层级不稳定；
   - 公式待核对项多时逐项交 Agent，成本高：paper_chn02 有 68 项，花了 $1.01；
   - 扫描 PDF 上不可见的 OCR 文字层可作为独立读数；
   - DOCX 页面层（Q69 推迟）；
   - 第三方 Agent 框架（Q62–Q64）。
3. 需要我拍板的问题集中列出一次问，附上建议。

## 约束

- **环境**：只用 uv（`uv run python …`、`uv add …`）。
- **标注**：
  - 改 ground_truth/ 下的任何文件，都要先经我同意；
  - 有明确证据证明原标注不合理、而我们的解析更合理时，要改标注，并记入 docs/annotation_changes.md；
  - 两种写法都说得通的（风格选择）不改。
- **删除要稳**：
  - 先让测试覆盖要保留的行为，再删除；
  - 每删一批就跑 L0 与 L1；
  - 删除只经 git 提交，便于回退；
  - 不删 eval_runs/、eval_reports/、ground_truth*/ 与响应缓存。
- **规则要谨慎**：
  - 不为单篇文档加规则或阈值；
  - 带魔法数字的规则尤其要谨慎：能与文档自身比较的就不用绝对阈值；非用不可的，写明是测量容差，并登记理由。
- **信号与判定**（Q56）：
  - 信号只指路，由 Agent 看图判断。信号来自正确输出必须满足的性质（守恒、独立读数一致、文档自身一致、结构与版面一致），不来自见过的错误样子。
  - 新信号或改动要在全语料上衡量。
  - 直接改输出的判定，只在证据强、并在全语料上验证不降低时才做。
  - 门只放行程序能核实的证据，不采信 Agent 的说法。
- **定位**：遇到某篇文档处理得不好，先按指导 §9.5 定位是哪一层的问题，再决定修哪里。
- **优先级**：信息保全与使用体验 > 标题层级 > 其他指标。
- **测试**：新模块先写测试（§13），测试用合成输入，不用标注内容。
- **远程调用**：
  - 只走 ServiceGateway；
  - 并发结果按任务顺序生效；
  - 只对传输错误和无法解析的响应重试；
  - 同一输入，输出逐字节相同。
- **两类模型分开**（Q40）：主 Agent 用 Codex gpt-6-sol、推理强度 medium，每次在命令行上显式指定；服务层 LLM / VLM 保持 gpt-6-luna。
- **Agent 运行的卫生**：
  - 真实的 Agent 运行（验证用）走 `scripts/agent_explore.py`：先 `snapshot`（基于干净的 HEAD），再 `parse`。实验根目录在仓库外（~/parserx-exp/），Agent 看不到 expected.md、评分或仓库；服务密钥不写进实验目录；每次运行后做路径审计与完整性检查；
  - 产品里的混合运行时也不把服务密钥传给 Agent 的进程；
  - 不读取、不复制 ~/.codex 下的任何凭据。
- **冻结 run**：
  - 改动影响验收文档的输出时，以空缓存重新冻结，并用 `--replay` 验证；
  - 全语料比较用 `scripts/phase4_compare.py`，信息类容差 0.005。
- **从简**：典型文档要能正常处理，新文档出问题时再打磨；不做计划之外的扩展。

## 每完成一项

- 报告 L0 的数字，并确认两个 L1 PASS；
- 更新指导 §12、§15，新的决策写入 §14；
- 提交一次（不推送）；
- 评测报告写入 eval_reports/，文件名含日期和阶段号；
- 然后向我汇报并继续。
