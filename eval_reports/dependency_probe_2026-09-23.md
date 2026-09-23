# 外部依赖探测记录（2026-09-23）

配套文档：[docs/redesign_guide.md](../docs/redesign_guide.md) §3。本文件只记录原始探测结果，结论见指导文档。

## OCR（PaddleOCR-VL-1.5，AI Studio 部署）

| 探测 | 结果 |
|---|---|
| `OPTIONS` 无 token | 401，0.5 s（主机存活） |
| `POST /layout-parsing` 带 token，空文件 | 503，`{"errorCode":10010,"errorMsg":"任务提交队列已满，请稍后重试"}`，0.08 s |
| 45 s 后重试 | 同上 |
| 服务类 `recognize()` / `recognize_pdf()`（5 次退避 + 关闭版面检测回退） | 33 s 后全部失败 |

## LLM / VLM

探测输入：ocr01 第 1 页渲染图（100 dpi，827×1170）；示意图 `paper_chn01/p4_img109.png`。
charF1 基于 `normalize_for_comparison` 后的字符多重集，对照 `ground_truth/ocr01/expected.md` 第 1 页（去掉描述句和复选框）。

### 主中转端点（`OPENAI_BASE_URL`）

| 模型 | 文本 | 视觉 |
|---|---|---|
| gpt-5.4-mini | 400 "not supported when using Codex with a ChatGPT account" | 429 "All available accounts are currently rate-limited" |
| gpt-5.5 / gpt-5.6 / gpt-6 | 正常，2–3 s，Responses API | 正常，2–5 s |

该端点接受 `temperature`，官方端点对同系列模型拒绝该参数，说明中转端点会静默丢弃参数。

### 官方端点（`api.openai.com`）

| 模型 | 任务 | 参数 | 耗时 | in/out token | 结果 |
|---|---|---|---|---|---|
| gpt-5.4-mini | 文本 + 视觉（服务类，带 temperature） | | 1.9 / 2.3 s | | 正常 |
| gpt-5.6-luna, gpt-6-luna, gpt-5.6-terra | 任意（服务类，带 temperature） | | | | 400 "Unsupported parameter: 'temperature'" |
| gpt-5.6-luna | 视觉 + json_schema | max_out 200 | 5.0 s | 1226 / 200 | 输出为空（推理耗尽预算） |
| gpt-5.6-luna | 视觉 + json_schema | max_out 1000, effort low | 4.2 s | 1226 / 30 | 正常 |
| gpt-6-luna | 视觉 + json_schema | max_out 1000, effort low | 5.5 s | 1226 / 432 | 正常 |
| gpt-5.4-mini | 视觉 + json_schema | max_out 1000, effort low | 2.2 s | 1226 / 102 | 正常 |
| gpt-5.6-luna | 整页转 Markdown | effort low | 8.5 s | 1192 / 718 | charF1 0.924，P 0.984，R 0.872 |
| gpt-6-luna | 整页转 Markdown | effort low | 6.6 s | 1192 / 723 | charF1 0.927，P 0.997，R 0.866 |
| gpt-5.4-mini | 整页转 Markdown | effort low | 3.1 s | 1192 / 394 | charF1 0.926，P 0.987，R 0.872 |
| gpt-5.6-luna | 整页转 Markdown | effort none | 3.5 s | 1178 / 244（推理 0） | 331 字符 |
| gpt-5.6-luna | 整页转 Markdown | effort minimal | | | 400 不支持 |
| gpt-5.6-luna | 整页转 Markdown | effort medium | 6.8 s | 1178 / 571（推理 325） | 332 字符 |
| gpt-6-luna | 整页转 Markdown | effort none | 2.9 s | 1178 / 248（推理 0） | 331 字符 |
| gpt-6-luna | 整页转 Markdown | effort medium | 15.3 s | 1178 / 1665（推理 1419） | 331 字符 |
| gpt-5.6-luna | 按区域转录（本地版面 14 区域，json_schema） | effort low | 8.3 s | 1664 / 750 | 14/14 区域，charF1 0.912，P 0.99 |
| gpt-6-luna | 同上 | effort low | 8.3 s | 1664 / 925 | 14/14，charF1 0.915，P 0.997 |
| gpt-5.4-mini | 同上 | effort low | 5.3 s | 1664 / 808 | 14/14，charF1 0.916，P 0.993 |
| gpt-5.6-luna | 示意图语义（diagram schema） | effort low | 6.6 s | 861 / 475 | diagram，7 节点，10 边 |
| gpt-6-luna | 同上 | effort low | 6.4 s | 861 / 505 | diagram，12 节点，4 边 |
| gpt-5.4-mini | 同上 | effort low | 3.2 s | 861 / 306 | diagram，7 节点，1 边 |
| 三款模型 | 6 路并发小视觉请求 | | 2.3–3.1 s | | 全部成功，无 429 |

召回率上限说明：ground truth 第 1 页把脚注句记录了两次（图片 OCR 块一次、原生文本一次），
三款模型的缺失字符集合正是这句话的第二份，实际转录无遗漏。

### DashScope（`qwen3.6-plus`，compatible-mode）

| 任务 | 耗时 | in/out token | 结果 |
|---|---|---|---|
| 文本 + 视觉（服务类） | 5.3 / 4.6 s | | 正常 |
| 视觉 + json_schema strict | 38.2 s | 1023 / 1997 | 返回 ```json 围栏且字段名自拟，strict 未生效 |
| 整页转 Markdown（effort low） | 12.9 s | 1041 / 660 | charF1 0.928，P 0.997，R 0.869 |
| 示意图语义（json_schema） | | | JSON 解析失败（围栏） |
| 6 路并发 | 6.8 s | | 全部成功 |

## 其他

| 项 | 结果 |
|---|---|
| LlamaCloud key | `GET /api/v1/projects` 200 |
| Python 包 `llama-parse`、`pypdf` | 无代码引用 |
| `pdfplumber` | 仅 `parserx/tool_eval/adapters.py` |
| LibreOffice | 26.8.0.3 |
| tesseract | 5.5.3，无代码引用；仓库根目录 `eng.traineddata` 为遗留文件 |
| Node / npm | 26.8.2；`node_modules` 缺失 |
| 本地版面检测（rapid-layout 1.2.1，ONNX Runtime，CPU） | pp_doc_layoutv3：加载 4.9 s（含 72 MB 下载），单页 0.22 s，14 区域；doclayout_docstructbench：加载 22.9 s，单页 0.22 s，11 区域 |

## 复核（2026-09-23，jobs API 接入之后）

另一会话已把 `services/ocr.py` 改为 AI Studio jobs API（PaddleOCR-VL-1.6）。用当前配置重新探测：

| 检查 | 结果 |
|---|---|
| OCR `recognize()`，ocr01 第 1 页 100 dpi | OK，5.5 s，14 块（image 5 / text 7 / paragraph_title 2） |
| OCR `recognize_pdf()`，ocr01 全部 9 页单次提交 | OK，20.4 s，返回 9 页，块数 [14, 20, 6, 1, 1, 1, 6, 1, 1] |
| VLM `describe_image()`，gpt-5.4-mini @ api.openai.com | OK，2.6 s |
| LLM `complete()`，gpt-5.4-mini @ api.openai.com | OK，1.5 s |
| `tests/test_ocr_service.py` + `tests/test_config.py` | 18 passed |

结论：OCR、LLM、VLM 三个外部依赖均已确定并可用；详见 `docs/redesign_guide.md` §3。

## llm.py 适配后（2026-09-23，VLM 切到 gpt-6-luna）

`services/llm.py` 改为：后端 400 "Unsupported parameter/value" → 去掉或改名该参数并重试一次，结果记在服务实例上；
`ServiceConfig` 新增 `reasoning_effort` / `send_temperature` / `min_output_tokens`；`parserx.yaml` vlm 与 llm 均 `reasoning_effort: none`。

| 检查 | 结果 |
|---|---|
| `check_services.py`，VLM=gpt-6-luna | ocr / llm / vlm 全部 OK（0.4 / 1.1 / 2.2 s） |
| 单元测试（离线） | 432 通过，4 个既有失败不变 |
| receipt 回归，gpt-6-luna 两次 | char_f1 0.962 / 0.954，edit 0.083 / 0.099 |
| receipt 回归，gpt-5.4-mini 两次 | char_f1 0.971 / 0.971（完全一致） |
| text_report01 回归，gpt-6-luna 两次 | char_f1 0.938 / 0.948，heading_f1 0.500 / 0.556；上次 gpt-5.4-mini 全量报告为 0.905 / 0.526 |

结论：接口层可用；luna 系列不能设 temperature，输出逐次有差异，回归复现性要靠缓存层。
