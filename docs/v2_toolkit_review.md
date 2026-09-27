# 工具包梳理（2026-09-27，草稿，待用户确认）

用户意见（2026-09-27）：工具名不在同一层级上，有的太笼统（check、read、close、process），有的功能重叠（与图片相关的有好几个），容易产生歧义、显得冗余；工具应当更原子化，便于 Agent 自由组合。

## 1. 现状：13 个工具

"用量"指 s1、s2 两轮 42 次 Agent 运行里的调用次数。

| 工具 | 实际做什么 | 读/写 | 用量 |
|---|---|---|---|
| `process` | 整条流水线：识别、版面、本地读数、公式、图片描述、续表、标题、段落续接、核对；返回摘要与完整待办 | 写（很多） | 67 |
| `overview` | 各页状态、块统计、标题大纲、Word 样式与编号、待办**计数** | 读 | 44 |
| `skim` | 按阅读顺序看文字：翻看、一页、查找、排版类别、大纲 | 读 | 274 |
| `read` | 一个块或一页的细节：表格单元格、状态、识别来源；`--image` 取图片文件路径 | 读 | 461 |
| `ask_image` | 让视觉模型看图回答问题（块、整页、跨页接缝、表格的几行） | 读（有服务费用） | 149 |
| `recognize` | 用某个引擎重新识别页、块或区域 | 写 | **0** |
| `review_table` | 列出表格问题，视觉模型重建结构，经接受门采用 | 写 | 12 |
| `correct` | 按图改正文、改表格单元格、补入漏掉的文字 | 写 | 246 |
| `close` | 关闭一个看过图、无需修改的待办项 | 写 | 163 |
| `describe_figure` | 视觉模型为图片写描述 | 写 | 2 |
| `apply_structure` | 角色、层级、顺序、关系、续表合并、拆块、排除与恢复、待定 | 写 | 124 |
| `check` | 核对账目、是否可导出；返回完整待办 | 读 | 71 |
| `export` | 导出 Markdown 与 sidecar | 写文件 | 0（产品中由程序导出） |

## 2. 问题

1. **一个工具身兼两职**：
   - `process` 既是"跑流水线"，又是"查待办"（Q84）；
   - `check` 也返回完整待办，与 `process` 重复；
   - `overview` 却只给待办的计数。

   Agent 想知道"还剩什么"，有三个入口，各给一部分。
2. **名字不在同一层级**：
   - `process`、`check`、`read`、`close`、`correct` 是泛泛的动词，看名字不知道作用于什么；
   - `review_table`、`describe_figure`、`apply_structure` 是"动词 + 对象"；
   - `close` 尤其含糊：关的是待办项，不是文件或工作区。
3. **看图的工具分散、边界不清**：
   - `ask_image` 问视觉模型；
   - `read --image` 取图片文件（只在 Agent 自己能看图的模式下有用）；
   - `describe_figure` 让视觉模型写描述并存入文档；
   - `recognize` 也可以用视觉模型重新识别。

   前两个是"取证"，后两个是"改内容"，却都挂着"图片"的名义。
4. **读文字的两个工具分工靠参数**：`skim` 是读者看到的文字流，`read` 是块的内部细节。但 `read --page` 也能读一页文字，与 `skim --page` 重叠；`overview` 的大纲又与 `skim --outline` 重叠。
5. **表格的修改分在两个工具里**：单元格内容在 `correct --cells`，行列结构在 `review_table`。
6. **不用的工具与参数**：
   - `recognize` 42 次运行里 0 次；
   - `describe_figure` 2 次；
   - 每个写工具都有 `actor` 参数，只供程序内部使用，Agent 不该看到。
7. **待办项没有编号**：`close` 要照抄 `target` 和 `kind` 两个字段才能指明是哪一项。

## 3. 建议：按"看 / 取证 / 修改 / 收尾"四类，统一用"动词_对象"命名

| 类 | 新名 | 取代 | 做什么 |
|---|---|---|---|
| 看（只读，无费用） | `overview` | `overview`（去掉大纲）＋ `process`、`check` 返回的待办 | 各页状态、块统计、Word 样式与编号，以及**完整的待办清单**（每项有编号） |
| | `read_text` | `skim`、`read --page` | 读者看到的文字：翻看、一页、查找、排版类别、大纲 |
| | `inspect_block` | `read --block`（不含取图） | 一个块的细节：表格单元格、状态、各来源的识别结果、坐标 |
| 取证（看图） | `ask_image` | `ask_image` | 视觉模型看图作答（Agent 不能直接看图时） |
| | `view_image` | `read --image` | 返回图片文件，Agent 自己看（Agent 能直接看图时） |
| 修改（都经程序的门） | `edit_text` | `correct`（正文、补入漏字） | 改正文、补入图上有而文档里没有的文字 |
| | `edit_table` | `correct --cells` ＋ `review_table` | 改单元格；列出结构问题，由视觉模型重建、经接受门采用 |
| | `edit_structure` | `apply_structure` | 角色、层级、顺序、关系、续表合并、拆块、排除与恢复、待定 |
| | `describe_image` | `describe_figure` | 为图片写描述 |
| | `rerun_recognition` | `recognize` | 对失败的页或块重新识别（流水线已做过基础识别） |
| | `dismiss_item` | `close` | 按编号关闭一个看过图、无需修改的待办项，写明理由 |
| 收尾 | `validate` | `check` | 核对账目与能否导出；只报告结果，不再附带待办清单 |
| | `export` | `export` | 导出（产品中由程序在 Agent 结束后执行） |
| 不给 Agent | `run_pipeline` | `process` | 固定流水线的入口；产品与实验装置都在 Agent 开始前运行它 |

- **每次运行给 Agent 的工具**：
  - 12 个：`overview`、`read_text`、`inspect_block`，`ask_image` 或 `view_image` 二选一（按看图模式），`edit_text`、`edit_table`、`edit_structure`、`describe_image`、`rerun_recognition`、`dismiss_item`，`validate`，外加实验模式的 `export`；
  - 现在是 13 个，另有一个身兼两职的 `process`。
- **命名规则**：
  - 除 `overview`、`validate`、`export` 这类整体动作外，都是"动词_对象"；
  - 动词只用几个：read、inspect、ask、view、edit、describe、rerun、dismiss；
  - 同一动词的工具作用于不同对象（`edit_text`、`edit_table`、`edit_structure`），一看就知道该用哪个。
- **参数统一**：
  - 单个用 `block`、`page`，多个用 `blocks`、`pages`；
  - 待办项用编号 `item`；
  - `actor` 从 Agent 可见的请求里去掉（Agent 的调用由运行时记作 agent）。
- **不做的**：流水线里的"提议"步骤（标题、续表、段落续接）暂不拆成单独工具。它们在 Agent 开始前已经应用，需要判断的地方都在待办清单里，Agent 用 `edit_structure` 调整；以后发现需要再拆。

## 4. 改动与验证

- **改动范围**：
  - 工具注册与命令行：`tools/__init__.py`、`tools/cli.py`；
  - `read` 拆成两个；`correct` 与 `review_table` 重组成 `edit_text`、`edit_table`；`process` 与 `check` 不再返回待办清单；待办项编号；
  - 任务说明 `agent_task.md` 与三份 Skill；
  - 混合运行时与固定运行时（在 Agent 前跑流水线，实验模式与产品一致）；
  - 审计的工具名、测试、README。
- **不保留旧名的别名**：只有我们自己在用，一次改干净。
- **验证**：
  - L0、L1；固定流水线的冻结 run 回放应逐字节相同（流水线的步骤不变，只是入口改名）；
  - 然后在交 Agent 的 10 篇上各跑一次混合方案（约 $3），与 s2 比较质量、工具调用次数与费用。

## 5. 待确认

1. 以上分类与命名是否采用？名字可以再调。
2. `export` 是否留给 Agent？产品中由程序导出，只在实验模式有用。
3. `rerun_recognition` 是否保留？42 次运行里没用过，但扫描页识别失败时它是唯一的补救手段。
