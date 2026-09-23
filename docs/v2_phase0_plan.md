# 阶段零剩余任务分解

> 状态：**已确认**（2026-09-23）。依据 [redesign_guide.md](redesign_guide.md) §8.3、§9、§12；拍板结果见指导 §14 的 Q4、Q8、Q15–Q21。按本文顺序实施，每完成一项更新指导 §12 与 §15。

## 0. 前置检查结果（2026-09-23）

- `scripts/check_services.py`：ocr（PaddleOCR-VL-1.6，0.5 s）、llm（gpt-5.4-mini，1.7 s）、vlm（gpt-6-luna，1.6 s）三项 OK。
- `pytest -q --ignore=tests/test_live_e2e.py`：432 通过、4 个已知失败、4 跳过，9.1 s。

## 1. 读代码时发现的事实（影响分解）

| 事实 | 位置 | 影响 |
|---|---|---|
| `best_scores.json` 在 `ground_truth/` 下，`--update-baseline` 会写它 | `scripts/regression_test.py` | 与"不改 ground_truth/"冲突；基线改为冻结 run，放在 `ground_truth/` 之外 |
| `ground_truth/` 整体被 gitignore（内部文档） | `.gitignore` | 已决定（Q15）：只有代码与文档入 git；`eval_runs/`、`.parserx_cache/`、新的评测报告都不入 git |
| paper01、paper_chn02 的 expected.md 用 HTML 表格（共 11 张） | ground truth | 反例 3 不是假想问题：这两篇的 table_f1 现在恒为 0 |
| 批量 OCR 提交的 PDF 字节每次不同 | `builders/ocr.py` 两处 `temp_pdf.tobytes()` | 实测默认生成新 `/ID`；`tobytes(no_new_id=True)` 字节稳定。按字节做缓存键之前必须先修 |
| 逐页渲染的 PNG 字节稳定 | 同上 `_ocr_page` | 可直接按字节做键 |
| 调用数由元素推算：OCR 按页计数、VLM 按"有描述的图"计数 | `pipeline._collect_api_calls` | 与真实请求不符（批量 OCR 一次请求覆盖多页），§9.2 ⑤ |
| LLM 用途有四处开关 + 一处质量检查 | chapter / header_footer（`llm_fallback: true`）、line_unwrap / content_value（已为 false）、`builders.quality_check`（LLM 判断公式碎片，默认开） | 已决定（Q18）：全部关闭，回归配置不调用 LLM |
| 配置已支持 `extends:` | `config/schema.py` | 回归配置写成对 `parserx.yaml` 的覆盖，不复制全文 |
| 编辑距离是纯 Python Levenshtein，超过 5000 字符按块近似 | `text_utils.compute_edit_distance` | 已决定引入 `rapidfuzz`（Q21）。`text_utils` 也被 v1 处理器用来判阈值，保持不动；评测另建 `parserx/eval/normalize.py` |
| 表格 HTML 解析已有可复用实现 | `builders/ocr.py` `_collect_rows` + `_build_table_grid` | 迁到 `parserx/tables/`，v1 反向导入，行为不变 |
| `parserx eval`、`parserx compare`、`tool_eval` 共用同一套指标 | `parserx/eval/`、`parserx/tool_eval/` | 改指标会同时影响三者，测试一起更新 |

## 2. 顺序与依赖

```
P0-1 验收工具修复 ──┬─> P0-2 回归配置（关 LLM 兜底）──┐
                   └─> P0-3 缓存层 ──> P0-4 回归分层 --core ──> P0-5 冻结基线 + 隔离验证集
```

P0-1 先做：之后所有数字都用新指标。P0-2 很小，放在缓存之前，这样缓存里不会混入以后用不到的 LLM 响应。P0-4 的"离线回放"依赖缓存。P0-5 依赖前四项全部完成。

## 3. 各项分解

### P0-1 验收工具修复（§9.2 ①–⑤，⑥ 只定格式）

| 子项 | 内容 | 改动文件 | 测试 |
|---|---|---|---|
| 1a TableGrid | 按 §4.3 定义 `Cell`、`TableGrid`（pydantic，`extra="forbid"`，校验跨度不重叠、不越界）；`TableGrid.from_html()`、`TableGrid.from_gfm()`；`find_tables(markdown)` 同时找 GFM 管道表和 `<table>…</table>`，返回字符区间。HTML 解析从 `builders/ocr.py` 迁入，v1 反向导入 | 新增 `parserx/tables/{__init__,grid,html}.py`；`builders/ocr.py` 改为导入 | `tests/test_table_grid.py`：rowspan/colspan、`th` 表头、GFM 转义管道符、重叠跨度报错、GFM 与 HTML 同一张表得到相同网格 |
| 1b 表格结构指标 | 表格配对：按单元格内容相似度贪心配对，同分按顺序。行列对齐：对行、列分别做序列对齐（DP），一行插入或删除只影响该行，不会让整表错位归零（思路同 GriTS）。单元格位置 F1 按 (row, col, rowspan, colspan, content) 计算。**未配对的表进入分母**（漏表的单元格计入 FN，多余表计入 FP）。另报表头关联正确率、合并单元格正确率、漏表数、多余表数 | 新增 `parserx/eval/tables.py`；`metrics.py` 的 `TableMetrics` 扩字段 | 反例 1（甲乙数值互换）< 1.0；反例 2（两表只出一张）F1 < 1 且漏表 = 1；反例 3（HTML 输出）能检出并得满分；插入一行只扣该行 |
| 1c 文本指标 | 比较前先规范化两侧：表格替换为按行展开的单元格文本（GFM 和 HTML 得到相同文本）；`> [图片] …` 与 `![…](…)` 剔除，只报告图片占位数（Q17）；NFKC 归一（康熙部首、全角字符与常规字符视为同一字符），比较时忽略空白。`char_f1` 改为基于 LCS 的有序 F1；原字符频次版本保留为 `char_bag_f1`，只作诊断。`edit_distance` 改用 rapidfuzz 精确计算 | 新增 `parserx/eval/normalize.py`；`parserx/eval/metrics.py` | 反例 4（交换甲乙主体）`char_f1` < 1；同一张表 GFM 和 HTML 两种写法文本分数相同；旧用例按新定义更新 |
| 1d 阅读顺序 | 期望文本按空行切块（表格整块）。每块取两侧都只出现一次的字符 8-gram 作锚点，按锚点在输出中的中位位置给块定位；然后计算成对逆序率和 Kendall tau（= 1 − 2·逆序率），另报覆盖率（能定位的块占比）。不受换行差异影响，也能发现块移动 | 新增 `parserx/eval/order.py` | 段落互换后 tau < 1；完全相同 tau = 1；缺块只影响覆盖率、不影响 tau |
| 1e 关键内容错误 | 从两侧按顺序抽取四类记号：数字（全角转半角、去千分位）、数字加单位、否定词、日期（`2025年11月24日` 与 `2025-11-24` 归一为同一形式）。每类在记号序列上做 LCS，分别报告缺失数和多余数 | 新增 `parserx/eval/key_content.py` | `100万元`→`999万元` 被计入；日期不同写法不算错；删掉"不"被计入 |
| 1f 真实请求计数 | `RequestMeter`：线程安全计数器，按服务记录逻辑请求数、尝试次数、OCR 提交页数，P0-3 起再记缓存命中数。在服务边界包一层，协议不变。`Pipeline` 构造服务时套上，`ParseResult.api_calls` 改用计数器，删除 `_collect_api_calls` 的推算。尝试次数来自 `ocr._run_with_retries` 和 `llm._create` 的参数降级重试；OpenAI SDK 内部的重试看不到，阶段一由调度层接管（SDK `max_retries=0`） | 新增 `parserx/scheduling/{__init__,meter}.py`（阶段一的调度层在此基础上扩展）；`pipeline.py`；`services/ocr.py`、`services/llm.py` 加尝试回调 | `tests/test_request_meter.py`：假服务计数、并发计数、批量 OCR 算 1 次请求 N 页 |
| 1g 硬检查与退出码 | 记录失败文档、未执行文档（请求了但没有输入或 expected.md）、每篇的漏表数和多余表数。退出码：0 通过，1 指标回退，2 硬检查失败。失败文档和未执行文档按绝对数；漏表和多余表与基线比较，增加才算失败，没有基线时只记录（Q16）。删除 `--update-baseline`（不再写 `ground_truth/`），改由 P0-5 的 `--freeze` 产出基线。`--baseline <结果 JSON 或 run 目录>` 与基线比较 | `scripts/regression_test.py`、`parserx/eval/runner.py` | 反例 5（全部失败）退出码 2；请求的文档不存在也是 2；把退出码逻辑写成可单测的函数 |
| 1h 报告 | 终端和 Markdown 报告按 §9.3 的固定顺序输出：硬检查 → 表格结构 F1 → char_f1 与编辑距离 → 阅读顺序 → heading_f1 → 关键内容错误 → 真实请求数 → 耗时 → 费用（费用在阶段一记录 token 用量之前显示"—"）。结果 JSON 带 `metric_version: "2.0"`，指标版本不同的结果拒绝比较。`parserx eval`、`compare`、`tool_eval` 同步更新 | `parserx/eval/runner.py`、`compare.py`、`reporting.py`、`tool_eval/runner.py`、`docs/evaluation.md` | `tests/test_eval.py` 更新报告格式用例 |

新增依赖：`uv add rapidfuzz`（Q21）。

**验收**
- 五个反例写成 `tests/test_eval_counterexamples.py`，每个都被指标或硬检查捕获（阶段零退出条件之一）。
- L0：除 4 个已知失败外全部通过，耗时仍 < 15 s。
- 在确定性文档（deepseek、text_table01、pdf_text01_tables、text_table_libreoffice）和两篇 HTML 表格文档（paper01、paper_chn02）上各跑一次，报告新旧指标对照，确认新指标的行为能解释。报告：`eval_reports/2026-09-23_p0-1_metric_fix.md`（日期取实际完成日）。

### P0-2 回归配置：关闭 LLM 兜底

- 新增 `configs/regression.yaml`：`extends: ../parserx.yaml`，覆盖 `processors.chapter.llm_fallback: false`、`processors.header_footer.llm_fallback: false`、`builders.quality_check.enabled: false`（Q18）；line_unwrap 和 content_value 显式写 false，防止默认值以后变化。回归配置不调用 LLM。
- 只做 VLM gpt-6-luna 一份基线（Q19），不再建 gpt-5.4-mini 变体。
- `scripts/regression_test.py` 不传 `--config` 时默认用 `configs/regression.yaml`；报告元数据和结果 JSON 记录配置指纹（解析后配置去掉密钥后的哈希），与基线指纹不同时给出提示。（实施时发现：`header_footer.llm_fallback` 在 v1 代码中没有被读取，v1 调用 LLM 的只有质量检查、chapter、line_unwrap、content_value 四处。）
- 测试：`tests/test_config.py` 增加用例，加载 regression.yaml 后覆盖项生效、其余与 parserx.yaml 一致。
- 验收：核心集跑一次，LLM 真实请求数为 0。结果只在终端打印（L1 级别，不写报告）。

### P0-3 缓存层（OCR、VLM；LLM 用同一机制顺带覆盖）

| 子项 | 内容 | 改动文件 |
|---|---|---|
| 3a 存储 | `ResponseCache(root)`：`.parserx_cache/raw/<service>/<k[:2]>/<k>.json`，`derived/` 预留给阶段一的后处理缓存。临时文件 + `os.replace` 原子写入，线程安全。模式：`off`、`read_write`、`read_only`（未命中抛 `CacheMiss`）、`refresh`（只写不读） | 新增 `parserx/cache/{__init__,store}.py` |
| 3b 缓存键 | 对请求语义的规范 JSON 取 sha256，覆盖 §8.3 列出的全部字段：服务类型、端点身份（host + path，不含密钥）、模型、方法、system/user/prompt/context 原文、各图片字节的 sha256、structured_output_mode、json_schema 的哈希与名称、temperature、max_tokens、reasoning_effort、min_output_tokens、extra_body；OCR 为文件字节 sha256、mime、模型、`_OPTIONS`。另加 `CACHE_SCHEMA_VERSION`。临时文件路径不进入键 | 新增 `parserx/cache/keys.py` |
| 3c 接入点 | LLM/VLM 在公开方法边界缓存（`complete`、`describe_image`、`describe_images`），缓存最终字符串，内部的结构化输出降级和接口风格回退不受影响。OCR 在 `_run_with_retries` 这一唯一出口缓存合并后的原始 JSON，响应解析照常执行。命中时计 `cache_hits`，未命中且实际请求时计 `requests`（与 P0-1 的计数器合在同一个包装层） | `services/ocr.py`、新增 `parserx/cache/services.py`、`pipeline.py` |
| 3d 确定性 | 两处 `temp_pdf.tobytes()` 改为 `tobytes(no_new_id=True)`（提交内容不变，只是不再生成随机 `/ID`）。设置 `PARSERX_CACHE_DEBUG=1` 时，未命中会记录键的各组成部分，用来查找其他不稳定字段 | `builders/ocr.py` |
| 3e 配置 | `cache: {mode, dir}` 加入 schema，默认 `read_write`；`.parserx_cache/` 加入 `.gitignore`；CLI 和回归脚本加 `--cache-mode` | `config/schema.py`、`parserx.yaml`、`cli.py`、`.gitignore` |

- 实施说明（2026-09-23）：计数与缓存合并为 `parserx/scheduling/gateway.py` 的 `ServiceGateway`；LLM/VLM 由 `MeteredService` 在公开方法处接入，OCR 由客户端的 `gateway` 属性在 `_run_with_retries` 接入（OCR 不再套 `MeteredService`）。schema 默认 `off`（库调用和单元测试不落盘），`parserx.yaml` 设为 `read_write`。离线回放未命中时计入 `cache_misses`，评测把该文档记为未执行，不对降级输出打分。回归脚本增加 `--cache-mode`、`--cache-dir`、`--outputs-dir`，结果记录带每篇 `output_sha256`；配置指纹不含缓存设置。
- 测试 `tests/test_cache.py`：键对每个语义字段敏感，对 api_key 和临时路径不敏感；原子写入；`read_only` 未命中抛异常；命中时不调用内层服务；并发写同一个键不会损坏文件。
- 验收：核心集先 `read_write` 跑一次，再 `read_only` 跑一次：第二次真实请求 0 次，六篇输出的 Markdown 字节一致。报告：`eval_reports/<日期>_p0-3_cache.md`（命中率、耗时对比、发现并修复的不稳定字段）。

### P0-4 回归分层

- `scripts/regression_test.py`：
  - `--core` 读取 `configs/regression_core.txt`（忽略注释）；`--list FILE` 读任意清单。
  - `--core` 默认离线（缓存 `read_only`）。缓存未命中的文档记为"未执行：缓存未命中"，属于硬检查失败，并提示加 `--allow-calls`（切到 `read_write`）。
  - `--repeat 2`：同一进程跑两遍并比较每篇输出的哈希，用来验证"核心集回放两次一致"。
  - `--gt-dir` 可重复，L2 同时覆盖 `ground_truth/` 和 `ground_truth_public/`。
  - `--deterministic-only` 依赖 `best_scores.json` 的 `requires_services`，标为弃用，由 `--core` 代替。
- `parserx eval` 增加 `--cache-mode`，文档清单沿用 `--include-list`。
- 测试：清单解析（注释、空行、未知文档计入未执行）；缓存未命中时的退出码。
- 验收：`uv run python scripts/regression_test.py --core --repeat 2` 在缓存命中时 < 1 min，两遍一致，真实请求 0。L1 只在终端打印，不写报告。
- 实施结果（2026-09-23）：逻辑放在 `parserx/eval/suite.py`（清单读取、多目录、重复比对），脚本只做参数与输出。首次离线回放暴露 v1 图片处理的竞态（并发 VLM 任务互相影响重叠证据），修正并加测试后，两个进程各回放两次一致，整条命令 4.2 s；空缓存目录下 4 篇需服务的文档记为未执行并提示 `--allow-calls`，退出码 2。

### P0-5 冻结 v1 基线并划出隔离验证集

- **冻结 run 目录** `eval_runs/<日期>_p0_v1_gpt-6-luna/`（整个 `eval_runs/` 不入 git，Q15）：
  - `manifest.json`：run_id、日期、阶段、git commit 与工作区是否有未提交改动（有则记录 diff 哈希）、去掉密钥后的解析配置及其哈希、`metric_version`、`CACHE_SCHEMA_VERSION`、每篇文档的输入 sha256 / expected.md sha256 / 所属集合（tune / isolation / public）、服务端点 host 与模型名。
  - `metrics.json`：逐篇完整指标、硬检查、真实请求数、耗时。
  - `outputs/<doc>.md`、`cache/`（本次用到的原始响应）。
- `scripts/regression_test.py --freeze <label>`：硬检查全部通过才写入（失败或未执行文档必须为 0）；使用空的、本次 run 专用的缓存目录，保证 run 自包含、可离线回放。`--baseline eval_runs/<id>` 与之比较。
- **隔离验证集**：新增 `configs/isolation_set.txt`：patent01、paper01、text_pic02（Q8，按来源划分；DOCX 以后补标注）。L1 永不包含它；L2 报告中单独成节，不计入调参集平均值。
- **一次运行**：`configs/regression.yaml`（VLM gpt-6-luna），一次完整 L2（真实调用，约 20 min）。
- 测试：manifest 字段完整；`--freeze` 在硬检查失败时拒绝；冻结 run 离线回放能复现 `metrics.json`。
- **验收**：冻结 run 存档；离线回放后 `metrics.json` 完全一致；报告 `eval_reports/<日期>_p0-5_v1_frozen_baseline.md` 按 §9.3 顺序输出，隔离集单列。满足阶段零三条退出条件后，§12 阶段零标 ✅。

## 4. 每项完成时的固定动作

1. 跑 L0 并汇报结果（通过数 / 已知失败 / 耗时）。
2. 更新指导 §12 状态列与 §15 变更记录；有新决策时写入 §14。
3. 需要写报告的项目，报告写入 `eval_reports/`，文件名含日期和阶段号（如 `2026-09-24_p0-1_metric_fix.md`）。
4. 汇报后再开始下一项。
