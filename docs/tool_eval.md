# 对标工具比较：怎么跑

背景、规则和工作项见 [v2_benchmark_plan.md](v2_benchmark_plan.md)。这里只讲命令。

## 工具

| `--tools` 里的名字 | 结果目录 | 是什么 | 凭据（`.env`） |
|---|---|---|---|
| `parserx` | `parserx-fixed` | ParserX 固定流水线，取冻结 run 的输出（`--parserx-run`，默认 `eval_runs/2026-09-28_io6_fixed_full`）；图片从它的缓存离线导出，不发请求 | 无 |
| `parserx-hybrid` | `parserx-hybrid` | ParserX 产品默认（混合方案，有待办时交 Agent，默认 Codex），用当前代码真实运行，配置同回归（`configs/regression.yaml`）；摘要、sidecar、Agent 工作目录存在 `raw/` | 个人配置（Codex） |
| `parserx-agent` | `parserx-agent` | 同上，但每篇都交 Agent 通读（`runtime.agent_when: always`，Q135），看 Agent 在没有待办的文档上能发现并修好多少 | 个人配置（Codex） |
| `llamaparse` | `llamaparse-agentic` | LlamaParse Parse API v2，agentic 档；表格输出 HTML、跨页续表合并 | `LLAMA_CLOUD_API_KEY` |
| `mineru` | `mineru-vlm` | MinerU 在线 API v4（mineru.net），`vlm` 模型 | `MINER_U_API_KEY` |
| `datalab` | `datalab-accurate` | Datalab（marker 的托管版），accurate 模式；多页文档打开跨页合并（按篇另收费） | `DATALAB_API_KEY` |
| `paddleocr` | `paddleocr-vl` | PaddleOCR-VL（AI Studio），扫描引擎的参数加整页重组；Word 先由 LibreOffice 转 PDF | `PADDLE_OCR_ENDPOINT`、`PADDLE_OCR_TOKEN` |

## 运行

```bash
uv run parserx dev tool-eval run --docs-file configs/bench_round1.txt
uv run parserx dev tool-eval run --tools mineru,datalab --docs receipt,text_table01
```

- 默认跑 `parserx,llamaparse,mineru,datalab,paddleocr`，文档默认是所有有标注的。
- 结果写在 `eval_runs/bench/<工具>/<文档>/`：
  - `output.md`：工具给的 Markdown，原样保存；
  - 图片：放在 Markdown 引用的位置；
  - `raw/`：原始响应；
  - `meta.json`：状态、用时、费用、参数、说明，失败时写原因。
- **已经成功的结果不再请求**，重跑不花钱。失败的下次会重试；`--force` 强制重做。
- 每次运行结束都会重新打分。

## 打分

```bash
uv run parserx dev tool-eval score
```

写出 `eval_runs/bench/scores.json` 和 `report.md`：
- 指标与回归相同（`evaluate_markdown`）；
- 标题 F1 只算有大纲的文档；
- 按标注来源分组：独立标注、LlamaParse 初稿改出的、参考过 ParserX 结果的。标注来源可在文档的 `meta.json` 里用 `annotation_origin` 指定；
- 平均只取各工具都有结果的文档。

## 对照页

```bash
uv run parserx dev tool-eval view        # http://127.0.0.1:8765/
```

- **选来源**：选 2–4 个来源左右并排，按点选的顺序排栏。可选：原件（页面图）、标注（expected.md）、各工具的输出。
- **看内容**：
  - Markdown 渲染后显示，图片、HTML 表格、公式都显示出来；
  - 标题前标出 H1/H2 等层级；
  - 勾"源码"看原始 Markdown；
  - "同步滚动"按比例对齐各栏。
- **评分**：
  - 每栏上方是自动评分：表格、文字、标题、用时、费用；
  - 下面是手工评分：信息完整、标题层级、表格、可读性、总体，各 1–5 分，加一行备注；
  - 点下去就保存到 `eval_runs/bench/manual_scores.json`。
- **总表**：列出每篇、每个来源的自动分和手工总体分。
- **翻页**：左右方向键切换文档。

## 其他

- LlamaParse 的 `scripts/llamaparse_to_markdown.ts`（Node SDK）仍用于给新文档生成标注初稿（[evaluation_workflow.md](evaluation_workflow.md) §2），与这里的比较无关。
