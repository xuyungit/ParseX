"""Console text of ``parserx parse`` in both interface languages (Q60: Chinese by default, ``--lang en``).

Commands, options and JSON field names stay English in both.  Every message is here, side by side, keyed by the
codes the runtime's events carry (``runtimes/events.py``).
"""

from __future__ import annotations

LANGS = ("zh", "en")

MESSAGES: dict[str, dict[str, str]] = {
    # header and stages
    "header": {"zh": "ParserX · {source}{pages}", "en": "ParserX · {source}{pages}"},
    "header_pages": {"zh": "（{pages} 页）", "en": " ({pages} pages)"},
    "header_pages_scanned": {"zh": "（{pages} 页，其中 {scanned} 页扫描）", "en": " ({pages} pages, {scanned} scanned)"},
    "header_docx": {"zh": "（DOCX）", "en": " (DOCX)"},
    "resumed": {"zh": "接着上次的工作区继续", "en": "continuing from the previous workspace"},
    "stage.read": {"zh": "读取文档", "en": "Read document"},
    "stage.process": {"zh": "标准处理", "en": "Standard processing"},
    "stage.agent": {"zh": "Agent 复核", "en": "Agent review"},
    "stage.agent_with": {"zh": "Agent 复核（{engine} · {model}）", "en": "Agent review ({engine} · {model})"},
    "stage.export": {"zh": "导出", "en": "Export"},
    # steps of the standard processing
    "step.recognize": {"zh": "识别扫描页 {total} 页", "en": "scanning {total} pages"},
    "step.layout": {"zh": "版面检测 {total} 页", "en": "layout of {total} pages"},
    "step.layout_figures": {"zh": "图片分类 {figures} 张", "en": "classifying {figures} images"},
    "step.reading": {"zh": "本地读数 {done}/{total} 页", "en": "local reading {done}/{total} pages"},
    "step.formulas": {"zh": "公式识别为 LaTeX {total} 处", "en": "formulas as LaTeX {total}"},
    "step.transcribe": {"zh": "识别图片中的文字 {total} 张", "en": "reading text in {total} images"},
    "step.describe": {"zh": "图片描述 {total} 张", "en": "describing {total} figures"},
    "step.structure": {"zh": "标题与结构", "en": "titles and structure"},
    "step.check": {"zh": "检查", "en": "check"},
    "wait.pending": {"zh": "服务排队中 {seconds}", "en": "queued at the service {seconds}"},
    "wait.running": {"zh": "服务识别中 {seconds}", "en": "service working {seconds}"},
    "wait.other": {"zh": "等待服务 {seconds}", "en": "waiting for the service {seconds}"},
    "service.ocr": {"zh": "扫描引擎", "en": "scan engine"},
    "review": {"zh": "待核对 {n} 项：{kinds}", "en": "{n} items to review: {kinds}"},
    "review_none": {"zh": "待核对 0 项", "en": "nothing to review"},
    # review item kinds (tools/envelope.py UnresolvedKind)
    "kind.page_pending": {"zh": "待识别的页", "en": "pages not read"},
    "kind.block_failed": {"zh": "识别失败的内容", "en": "failed blocks"},
    "kind.table_uncertain": {"zh": "结构不确定的表格", "en": "uncertain tables"},
    "kind.table_merge_candidate": {"zh": "可能的跨页续表", "en": "possible table continuations"},
    "kind.table_arithmetic": {"zh": "表格算术不一致", "en": "table arithmetic"},
    "kind.text_suspicious": {"zh": "可疑字符", "en": "suspicious characters"},
    "kind.text_unaccounted": {"zh": "页面上有而输出里没有的文字", "en": "text on the page but not in the output"},
    "kind.text_not_seen": {"zh": "输出里有而页面上读不到的文字", "en": "output text not seen on the page"},
    "kind.figure_without_content": {"zh": "没有描述也没有文字的图片", "en": "images without description or text"},
    "kind.title_candidate": {"zh": "标题候选", "en": "title candidates"},
    "kind.evidence_conflict": {"zh": "证据冲突", "en": "conflicting evidence"},
    "kind.structure_pending": {"zh": "待定的标题层级", "en": "undecided title levels"},
    "kind.budget_skipped": {"zh": "因预算跳过", "en": "skipped for budget"},
    "kind.asset_missing": {"zh": "缺失的图片文件", "en": "missing image files"},
    # the agent's actions
    "page": {"zh": "第 {page} 页", "en": "page {page}"},
    "pages": {"zh": "第 {pages} 页", "en": "pages {pages}"},
    "act.look": {"zh": "看图", "en": "look"},
    "act.edit": {"zh": "修改", "en": "edit"},
    "act.add": {"zh": "补入", "en": "add"},
    "act.set_title": {"zh": "设为标题", "en": "set title"},
    "act.set_level": {"zh": "改标题层级", "en": "set level"},
    "act.set_role": {"zh": "改类型", "en": "set kind"},
    "act.split": {"zh": "拆分", "en": "split"},
    "act.merge": {"zh": "合并续表", "en": "merge table"},
    "act.join": {"zh": "接续段落", "en": "join paragraph"},
    "act.exclude": {"zh": "不输出", "en": "exclude"},
    "act.restore": {"zh": "恢复", "en": "restore"},
    "act.move_after": {"zh": "调整顺序", "en": "move"},
    "act.close": {"zh": "关闭", "en": "close"},
    "act.recognize": {"zh": "重新识别", "en": "re-read"},
    "act.describe": {"zh": "描述图片", "en": "describe"},
    "act.table_fix": {"zh": "修正表格", "en": "fix table"},
    "act.rejected": {"zh": "未采用", "en": "not accepted"},
    "count": {"zh": "{n} 处", "en": "{n} places"},
    "rejected_why": {"zh": "修改未通过程序核对", "en": "the change did not pass the program's checks"},
    "role.text": {"zh": "正文", "en": "body text"},
    "role.list": {"zh": "列表", "en": "list"},
    "role.caption": {"zh": "图表标题", "en": "caption"},
    "role.footnote": {"zh": "脚注", "en": "footnote"},
    "role.header": {"zh": "页眉", "en": "header"},
    "role.footer": {"zh": "页脚", "en": "footer"},
    "role.page_number": {"zh": "页码", "en": "page number"},
    "role.other": {"zh": "其他", "en": "other"},
    "role_to": {"zh": "→ {role}", "en": "→ {role}"},
    "level": {"zh": "→ {level} 级", "en": "→ level {level}"},
    "agent_done": {"zh": "{seconds} · 修改 {changes} 处 · 补入 {added} 处 · 关闭 {closed} 项 · 剩余 {open} 项",
                   "en": "{seconds} · {changes} changes · {added} added · {closed} closed · {open} left"},
    # why the agent did not run or its work was not used
    "skip.no_review_items": {"zh": "待核对 0 项，跳过", "en": "nothing to review, skipped"},
    "skip.mode_fixed": {"zh": "只用固定流水线（--runtime fixed），跳过", "en": "fixed runtime only (--runtime fixed), skipped"},
    "why.codex_not_found": {"zh": "Agent 未运行：未找到 Codex CLI（安装并运行 `codex login` 后可用）",
                            "en": "Agent not run: Codex CLI not found (install it and run `codex login`)"},
    "why.codex_not_logged_in": {"zh": "Agent 未运行：Codex CLI 未登录（运行 `codex login` 后可用）",
                                "en": "Agent not run: Codex CLI is not logged in (run `codex login`)"},
    "why.codex_not_working": {"zh": "Agent 未运行：Codex CLI 无法启动", "en": "Agent not run: Codex CLI does not start"},
    "why.agent_failed": {"zh": "Agent 失败（{detail}），用时 {seconds}", "en": "Agent failed ({detail}) after {seconds}"},
    "why.agent_timeout": {"zh": "Agent 超过截止时间（{detail}），已停止", "en": "Agent passed its deadline ({detail}), stopped"},
    "why.workspace_tampered": {"zh": "Agent 绕过工具改动了工作区，它的修改全部不采用",
                               "en": "The agent changed the workspace outside the tools: none of its changes are used"},
    "why.export_failed": {"zh": "Agent 处理后无法导出（{detail}）", "en": "Export after the agent failed ({detail})"},
    "fallback": {"zh": "已输出标准处理的结果{open}", "en": "The standard processing result was written{open}"},
    "fallback_open": {"zh": "，另有 {n} 项待核对，见 {file} 的 review", "en": "; {n} items to review, see review in {file}"},
    "fallback_resume": {"zh": "再次运行同一命令会接着交给 Agent 处理", "en": "Run the same command again to continue with the agent"},
    # notices
    "notice.tool_failures": {"zh": "{targets}：{what}（{retry}）", "en": "{targets}: {what} ({retry})"},
    "retryable": {"zh": "可重试", "en": "retryable"},
    "not_retryable": {"zh": "不可重试", "en": "not retryable"},
    "failure.service_error": {"zh": "服务错误", "en": "service error"},
    "failure.timeout": {"zh": "服务超时", "en": "service timeout"},
    "failure.budget_exhausted": {"zh": "预算用尽", "en": "budget exhausted"},
    "failure.cache_miss_offline": {"zh": "离线回放缺少记录", "en": "no recorded response (offline)"},
    "failure.other": {"zh": "处理失败", "en": "failed"},
    "notice.agent_audit": {"zh": "Agent 访问了工作目录以外的路径：{hits}", "en": "The agent named paths outside its directory: {hits}"},
    "notice.config_defaults": {"zh": "没有找到配置文件，使用内置默认配置", "en": "No config file found; using the built-in defaults"},
    # result
    "done": {"zh": "完成", "en": "Done"},
    "partial": {"zh": "部分完成", "en": "Partial"},
    "failed": {"zh": "失败", "en": "Failed"},
    "result_line": {"zh": "状态 {status} · {pages} 页 · 表格 {tables} · 图片 {images} · 标题 {titles} · 待核对 {open} 项",
                    "en": "status {status} · {pages} pages · {tables} tables · {images} images · {titles} titles · "
                          "{open} to review"},
    "result_line_docx": {"zh": "状态 {status} · 表格 {tables} · 图片 {images} · 标题 {titles} · 待核对 {open} 项",
                         "en": "status {status} · {tables} tables · {images} images · {titles} titles · {open} to review"},
    "missing_line": {"zh": "缺失 {n} 处：{items}", "en": "{n} missing: {items}"},
    "time_cost": {"zh": "用时 {seconds} · 费用约 {cost}", "en": "time {seconds} · cost about {cost}"},
    "time_only": {"zh": "用时 {seconds}", "en": "time {seconds}"},
    "cost_split": {"zh": "{total}（服务 {service} + Agent 按标价 {agent}）",
                   "en": "{total} (services {service} + agent at list price {agent})"},
    "cost_service": {"zh": "{total}（服务）", "en": "{total} (services)"},
    "other_files": {"zh": "其余文件：{summary}（摘要）· {blocks}{images}", "en": "other files: {summary} (summary) · {blocks}{images}"},
    "interrupted": {"zh": "已中断，工作区保留在 {work}；再次运行同一命令会从中断处继续",
                    "en": "Interrupted; the workspace is kept in {work}; run the same command again to continue"},
    "error.unreadable": {"zh": "无法读取：{message}", "en": "cannot be read: {message}"},
    "error.process_failed": {"zh": "标准处理失败：{message}", "en": "standard processing failed: {message}"},
    "error.export_failed": {"zh": "导出失败：{message}", "en": "export failed: {message}"},
    "error.other": {"zh": "内部错误：{message}（-v 显示详情）", "en": "internal error: {message} (-v for details)"},
    "total": {"zh": "合计 {n} 篇：完成 {complete} · 部分 {partial} · 失败 {failed} · 用时 {seconds} · 费用约 {cost}",
              "en": "{n} documents: {complete} complete · {partial} partial · {failed} failed · time {seconds} · "
                    "cost about {cost}"},
    "compact_line": {"zh": "{mark} {source}  {status} · {pages} 页 · 待核对 {open} 项 · {seconds} · {cost}{note}  → {path}",
                     "en": "{mark} {source}  {status} · {pages} pages · {open} to review · {seconds} · {cost}{note}"
                           "  → {path}"},
    "cost_unknown": {"zh": "未知", "en": "unknown"},
    "no_inputs": {"zh": "没有找到可处理的文件（PDF、DOCX、DOC）：{paths}", "en": "No PDF, DOCX or DOC files found: {paths}"},
}


def t(lang: str, key: str, **values) -> str:
    entry = MESSAGES[key]
    return entry.get(lang, entry["en"]).format(**values)


def duration(lang: str, seconds: float) -> str:
    if seconds < 10:
        return f"{seconds:.1f} s"
    if seconds < 60:
        return f"{seconds:.0f} s"
    m, s = divmod(int(round(seconds)), 60)
    if m < 60:
        return f"{m} 分 {s:02d} 秒" if lang == "zh" else f"{m} min {s:02d} s"
    h, m = divmod(m, 60)
    return f"{h} 小时 {m:02d} 分" if lang == "zh" else f"{h} h {m:02d} min"


def money(value: float | None, lang: str) -> str:
    if value is None:
        return t(lang, "cost_unknown")
    return f"${value:.2f}" if value >= 0.01 or value == 0 else "<$0.01"
