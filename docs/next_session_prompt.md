# 下一轮会话启动提示词（2026-09-23 生成）

复制下面整段作为新会话的第一条消息。

---

你在 /Users/xuyun/Projects/ParserX 工作。这是一个把含扫描图片的 DOCX/PDF 转成适合大模型消费的 Markdown 的工具。v1 已冻结（提交见 git log 最新一条），v2 的设计与研发指导在 docs/redesign_guide.md（v1.0），所有决策以它为准。本轮会话的目标：把设计落成可执行的研发计划，并开始阶段零。

先做三件事，任一不通过先处理，不要绕过：
1. 读 docs/redesign_guide.md 的 §0、§2、§3、§5、§7、§12、§14；§4、§6、§8、§9 在实现对应部分时再读。
2. 运行 `uv run python scripts/check_services.py`，OCR、LLM、VLM 三项都要 OK。
3. 运行 `uv run pytest -q --ignore=tests/test_live_e2e.py`，预期 432 通过、4 个已知失败（指导 §0.1 有说明，不要修它们）。

然后产出：
- 阶段零剩余任务（指导 §12）的工作分解：验收工具修复（§9.2 的六项）、回归分层 `--core`（读 configs/regression_core.txt）、缓存层（覆盖 OCR 与 VLM）、关闭 LLM 兜底的回归配置、用修好的指标冻结两份 v1 基线（gpt-5.4-mini、gpt-6-luna）并划出隔离验证集。每项给出改动文件、测试、验收方式和顺序。
- 阶段一"文档工具包 v1"的模块与接口草案（§4、§5、§11.3）：先给 IR 五个概念、TableGrid 和七个工具的 pydantic 模型与 JSON CLI 签名，不写实现。
- 需要我拍板的问题：Q4、Q8（§14），以及你在分解时发现的新问题，集中列出一次问。

约束：
- 只用 uv（`uv run python …`、`uv add …`）。
- 不改 ground_truth/ 下的任何文件；不为单篇文档加规则或阈值；新模块先写测试（§13）。
- 修了验收指标就必须重算基线，不沿用 best_scores.json 的旧分数。
- 本轮不做阶段二的 Agent 探索，不引入任何 Agent 框架依赖。
- 每完成一项：跑 L0（pytest）汇报结果，更新指导 §12 状态列与 §15 变更记录；评测报告写入 eval_reports/，文件名含日期和阶段号。

分解和接口草案经我确认后，直接开始实现阶段零第一项（验收工具修复），完成一项汇报一项。
