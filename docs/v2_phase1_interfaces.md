# 阶段一"文档工具包 v1"：模块与接口草案

> 状态：**已确认**（2026-09-23），2026-09-24 按阶段一分解修订（标 **〔v1.9〕**，见 [v2_phase1_plan.md](v2_phase1_plan.md) §2）。依据 [redesign_guide.md](redesign_guide.md) §4、§5、§11.3。本文只给数据模型与接口签名，不含实现。标 **〔增补〕** / **〔偏离〕** 的地方已一并确认，并回写到指导 v1.1。字段级定义以本文为准。
>
> `TableGrid`（§3）会在阶段零 P0-1 提前落地，因为评测要用到它。

## 1. 模块划分（阶段一范围）

| 模块 | 阶段一职责 | 主要接口 |
|---|---|---|
| `parserx/ir/` | 五个概念 + Decision + 枚举；序列化与 schema 导出 | §2 的模型；`ir.schema.export_json_schema()` |
| `parserx/tables/` | `TableGrid` 构建（HTML / GFM / 字符归属结果）、GFM / HTML 渲染、结构校验；跨页合并只产生候选 | `TableGrid.from_html/from_gfm/to_gfm/to_html`、`merge_candidate(a, b) -> MergeCandidate \| None` |
| `parserx/workspace/` | 工作区持久化、事务、版本号、调用日志；概况与读取 | `Workspace.create/open`、`Workspace.txn(actor)` |
| `parserx/content/`（最小形态） | 原生 PDF 提取（阶段一按 PyMuPDF 文本块组织，版面归属在阶段四决定）；paddleocr 扫描页引擎适配为 Observation；**〔v1.9〕DOCX 直接读取 OOXML**（正文段落、表格、图片、修订最终视图、域代码结果、分页分节、样式与编号证据），不再经 Docling 适配，因为后者给不出节点路径，也无法记账；选择步骤与接受门 | `extract_pdf(doc) -> Extraction`、`extract_docx(path) -> Extraction`、`scan_pages(pages, engine) -> list[Observation]`、`select(block) -> Decision` |
| `parserx/scheduling/` | 在阶段零的 `ServiceGateway` 上加入预算（截止时间、并发、请求数、费用，请求前预留、完成后结算）、token 用量与费用、可重试错误分类（SDK `max_retries=0`）、OCR job_id 恢复与页数校验；**〔v1.9〕`run_ordered`：并发请求，结果按任务顺序生效** | `ServiceGateway.call(...)`、`run_ordered(tasks, fetch, apply) -> list[TaskOutcome]` |
| `parserx/cache/` | 阶段零已建；增加 `derived/` 后处理缓存 | `ResponseCache.get/put` |

〔P1-3〕调度层实现要点：
- 每篇文档一个 `ServiceGateway.from_config(meter, cache, config.scheduling)`，OCR、VLM、LLM 共用，共享预算、重试策略与价格表；`gateway.budget.reset()` 在每篇开始时调用。
- `call(service, material, fetch, *, pages=0, parse=None, parse_retries=1)`：缓存命中直接返回（不占预算）；未命中先 `budget.reserve`（不足则抛 `BudgetExhausted`，计 `skipped_budget`），再发送并做传输重试（`RetryPolicy`，只重试 `is_retryable` 为真的错误：网络、5xx、429、`TransientError`），结束后按真实费用结算。`parse` 抛 `UnparseableResponse` 时以 `parse_retry: n` 作为新请求再发一次，回放时按同样顺序命中缓存；已解析的响应不再重试。
- 服务通过钩子报告：`attempt_hook`（每次网络发送，含参数降级重发）与 `usage_hook(model, input, cached_input, output)`；网关用 context variable 把它们归到当前请求，并发请求互不混淆。没有报告发送次数的服务按每次 fetch 一次尝试计。OpenAI SDK `max_retries=0`，`ServiceConfig.max_retries` 已删除，重试次数在 `scheduling.retry`。
- 费用：`scheduling.prices`（美元/百万 token，按模型名）；`cost_usd` 只含 LLM/VLM 的 token 费用，OCR 按页计费不在其中；有未定价的用量时 `cost_usd=None`（未知，不是免费）。
- `run_ordered(tasks, fetch, apply, max_workers=)`：fetch 并发、不得修改共享状态；apply 在调用线程按任务顺序执行、只对成功的任务执行；结果为 `TaskOutcome(status=ok|failed|skipped_budget|cache_miss, retryable)`。
- OCR：`JobStore` 按请求键记住已提交的 job_id（有缓存目录时存在 `<cache>/jobs/ocr/`），重试或进程重启后恢复轮询而不是重新提交，任务失败或过期（404）才重新提交；返回页数与提交页数不符抛 `PageCountMismatch`（不重试、不缓存）。
- 配置指纹只覆盖能改变回放输出的设置：不含 `cache`、`scheduling.retry`、`scheduling.prices`，含 `scheduling.budget`。`load_record` 读取冻结 run 时按今天的函数从 manifest 重新计算其指纹，此后新增的配置字段（取默认值）不会让旧的冻结 run 无法回放。
| `parserx/render/` 〔v1.9〕 | §4.5 的 Markdown 契约与 sidecar 导出（v1 的 `assembly/` 不动） | `render_markdown(state) -> str`、`export_sidecar(state) -> dict` |
| `parserx/hierarchy/legality.py` 〔v1.9〕 | `apply_structure` 的合法性检查（§6.8 的完整实现在阶段四） | `check_changes(state, changes) -> list[Rejection]` |
| `parserx/prompts/` 〔v1.9〕 | 工具内部 VLM 任务的提示词文件（内容哈希计入缓存键）；与面向运行时的 Skill 分开 | `load_prompt(name) -> (text, sha256)` |
| `parserx/accounting/` | 去向账目与检查 | `check(state) -> CheckResult` |
| `parserx/tools/` | 七个工具、统一返回信封、JSON CLI | §4、§5 |
| `parserx/skills/` | 三份 Skill（Markdown）+ 内容哈希 | `load_skill(name) -> SkillText(text, sha256)` |
| `parserx/layout/` + `parserx/routing/image.py` | 检测器封装、标签映射、面积统计、图片路由；**影子运行**：只写 Decision，不影响输出 | `Detector.detect(asset) -> list[Observation]`、`labels.to_kind(source, label)`、`route(asset, regions) -> RouteResult` |
| `parserx/runtimes/pipeline.py` | 固定序列（流水线运行时的最小形态），全部通过工具函数完成；`pipeline: v1 \| v2` 开关，v2 返回同样的 `ParseResult` | `run(input, ws, config) -> ExportResult` |
| `parserx/runtimes/v1_structure.py` 〔v1.9，临时〕 | 原生 PDF 的标题来源：复用 v1 不调用服务的元数据构建与章节处理，经 `apply_structure` 写入（actor `adapter:v1`）；阶段四删除（Q24） | `propose_structure(pdf_path, state) -> list[StructureChange]` |
| `parserx/hierarchy/engine_titles.py` 〔Q33〕 | 扫描页的标题来源：扫描页引擎的 `doc_title` / `paragraph_title` 标签按 `layout/labels.py` 的 `TITLE_RANK` 给出建议层级（1 / 2），带点号编号每多一段深一级；`adapter:v1` 此后只匹配原生文字块 | `engine_titles(state) -> list[(block, text, level, evidence)]`，actor `program:hierarchy.engine_titles` |

新旧路径由 `pipeline: v1 | v2` 开关切换（§12）。

## 2. IR：五个概念

### 2.1 公共约定

```python
# parserx/ir/base.py
class IRModel(BaseModel):
    # 禁止自由字典和标志位（§4.2）：出现未知字段直接报错
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

BBox = tuple[float, float, float, float]                 # x0, y0, x1, y1
Affine = tuple[float, float, float, float, float, float] # a b c d e f，PDF 矩阵约定：
                                                         # x' = a·x + c·y + e,  y' = b·x + d·y + f
```

**〔增补〕ID 规则**：ID 由确定性规则生成，不用随机数。同一输入得到同一组 ID，这是缓存回放和任务级重跑（§8.1"按块 id、任务类型、输入哈希寻址"）的前提。

| 对象 | 格式 | 例子 |
|---|---|---|
| Block | `b-p{page:03d}-{seq:04d}`（PDF）/ `b-d{seq:05d}`（DOCX） | `b-p003-0012` |
| Observation | `o-{block 或 region}-{engine}-{n}` | `o-b-p003-0012-paddleocr-1` |
| Relation | `r-{kind}-{src}-{dst}` | |
| Asset | `a-{sha256[:16]}` | |
| 账目条目 | `i-p{page:03d}-{seq:05d}` / `i-d{seq:05d}` | |

### 2.2 枚举

```python
# parserx/ir/enums.py
class BlockKind(StrEnum):
    TITLE="title"; TEXT="text"; LIST="list"; TABLE="table"; FIGURE="figure"; FORMULA="formula"
    CAPTION="caption"; HEADER="header"; FOOTER="footer"; PAGE_NUMBER="page_number"
    FOOTNOTE="footnote"; SCAN="scan"; OTHER="other"

class BlockStatus(StrEnum):
    OK="ok"; DEGRADED="degraded"; FAILED="failed"; EXCLUDED="excluded"; MERGED="merged"; DUPLICATE="duplicate"

class ObservationStatus(StrEnum):
    OK="ok"; EMPTY="empty"; FAILED="failed"; SKIPPED_BUDGET="skipped_budget"

class RelationKind(StrEnum):
    CONTAINS="contains"; FOLLOWS="follows"; CONTINUES="continues"; CAPTIONS="captions"
    FOOTNOTES="footnotes"; BELONGS_TO_SECTION="belongs_to_section"; DUPLICATE_OF="duplicate_of"

class DecisionStage(StrEnum):
    IMAGE_ROUTE="image_route"; CONTENT_SOURCE="content_source"; REVIEW_ACCEPT="review_accept"
    HEADING_ROLE="heading_role"; HEADING_LEVEL="heading_level"; EXCLUDE="exclude"; BUDGET="budget"

class TaskKind(StrEnum):   # 〔增补〕Observation 属于哪类任务，对应 §6.1 与 §8.1
    EXTRACT="extract"; LAYOUT="layout"; RECOGNIZE="recognize"; REVIEW="review"
    DESCRIBE="describe"; EXPLAIN="explain"

class EvidenceLevel(StrEnum):
    VISIBLE="visible"; ESTIMATED="estimated"; INFERRED="inferred"; UNKNOWN="unknown"

class ImageRoute(StrEnum):
    SCAN="SCAN"; FIGURE="FIGURE"; MIXED="MIXED"; UNCERTAIN="UNCERTAIN"; DECORATIVE="DECORATIVE"

class PageStatus(StrEnum):      # §4.5 的 done/partial/failed/skipped，加上工作中的 pending
    PENDING="pending"; DONE="done"; PARTIAL="partial"; FAILED="failed"; SKIPPED="skipped"

class DocumentStatus(StrEnum):  # §4.5 的三种，加上工作中的 in_progress
    IN_PROGRESS="in_progress"; COMPLETE="complete"; PARTIAL="partial"; FAILED="failed"
```

### 2.3 SourceAnchor

**〔偏离〕** §4.1 只列了 PDF 和 DOCX 两种来源。但嵌入图片内的区域（PDF 和 DOCX 都有）只能用父图片的像素坐标表示，这里单独列为第三种 `AssetAnchor`。原图属于哪一页或哪个 OOXML 节点，记在 Asset 的 `source` 字段上。

```python
# parserx/ir/anchor.py
class PdfAnchor(IRModel):
    type: Literal["pdf"] = "pdf"
    page: int                                   # 物理页码，从 1 开始
    bbox: BBox
    coord_space: Literal["page_pt", "image_px"] # image_px 用于页面渲染图上的检测或 OCR 框
    image_size: tuple[int, int] | None = None   # coord_space=image_px 时必填（有校验器）
    transform: Affine | None = None             # image_px → page_pt

class DocxAnchor(IRModel):
    type: Literal["docx"] = "docx"
    part: str                                   # "word/document.xml"、"word/footnotes.xml"……
    node_path: str                              # 类 XPath，如 "/w:body/w:tbl[3]/w:tr[2]/w:tc[1]/w:p[1]"
    run_range: tuple[int, int] | None = None    # [start, end) 的 run 下标
    segment: int | None = None                  # 〔P1-2〕所在的 docx_segment（显式分页 / 分节切出的段），位置属性，不随重排改变

class AssetAnchor(IRModel):
    type: Literal["asset"] = "asset"
    asset: str                                  # Asset id
    bbox: BBox                                  # 该资源的像素坐标
    coord_space: Literal["image_px"] = "image_px"
    image_size: tuple[int, int]
    transform: Affine | None = None             # 资源像素 → 父坐标系（父为 PDF 页时是 page_pt）；DOCX 没有几何父坐标，为 None

SourceAnchor = Annotated[PdfAnchor | DocxAnchor | AssetAnchor, Field(discriminator="type")]
```

### 2.4 Observation

```python
# parserx/ir/observation.py
class Observation(IRModel):
    id: str
    engine: str                     # "native_pdf" | "docx" | "paddleocr" | "vlm" | "layout"
    engine_version: str             # "PaddleOCR-VL-1.6"、"gpt-6-luna"、"pp_doc_layoutv3@rapid-layout-1.2.1"
    task: TaskKind                  # 〔增补〕
    anchor: SourceAnchor
    raw_ref: str | None = None      # 原始响应的缓存键（.parserx_cache/raw）
    label: str | None = None        # 〔增补〕引擎原始标签（映射前），映射只在 layout/labels.py
    text: str | None = None
    cells: TableGrid | None = None
    style: TextStyle | None = None  # 〔v1.9〕结构证据，见下
    det_confidence: float | None = None
    rec_confidence: float | None = None   # None 表示"未知"，不表示"可信"（§4.2）
    status: ObservationStatus
    error: str | None = None        # 〔增补〕status=failed 时的原因
```

**〔v1.9〕TextStyle**：结构判断用到的排版证据（§6.8）。用类型化字段表示，不用自由字典（§4.2）。只记录来源里实际存在的值，缺失时为 None。

```python
class Numbering(IRModel):
    num_id: str; level: int          # numbering.xml 中的编号定义与层级（这是编号层级，不是标题层级）
    text: str | None = None          # 渲染出来的编号文字，如 "1.2.3"

class TextStyle(IRModel):
    font_size: float | None = None   # pt
    bold: bool | None = None
    style_name: str | None = None    # DOCX 样式名（沿 basedOn 链解析后的显示名）
    outline_level: int | None = None # w:outlineLvl（含样式继承），0 起
    numbering: Numbering | None = None
```

### 2.5 Relation

```python
class Relation(IRModel):
    id: str
    kind: RelationKind
    src: str                        # Block id
    dst: str                        # Block id
    confidence: float | None = None
```

### 2.6 Asset

```python
class Asset(IRModel):
    id: str                         # a-{sha256[:16]}
    sha256: str
    path: str                       # 相对工作区根目录
    media_type: str                 # 〔增补〕"image/png" …
    width: int
    height: int
    role: Literal["original", "crop", "render"]   # 〔增补〕
    derived_from: str | None = None               # 父 Asset id（裁剪图、渲染图）
    source: PdfAnchor | DocxAnchor | None = None  # 〔增补〕原图在源文件中的位置
    transform: Affine | None = None               # 本资源像素 → derived_from 的坐标系
    dpi: float | None = None                      # 〔增补〕仅渲染图
```

### 2.7 Decision

```python
class Decision(IRModel):
    stage: DecisionStage
    choice: str
    reason: str
    evidence: dict[str, float | int | str | bool]
    actor: str                  # 〔增补〕"program:content.select"、"pipeline"、"agent"、"tool:apply_structure"
    refs: list[str] = []        # 〔增补〕作为证据的 Observation / Relation / Block id
```

§4.4 的 Decision 回答不了"这句话是谁改的、依据是什么"（§1.2）。`actor` 和 `refs` 就是为此增加的。

### 2.8 Block

```python
class Block(IRModel):
    id: str
    kind: BlockKind
    order: int
    status: BlockStatus = BlockStatus.OK
    anchors: list[SourceAnchor]                 # 至少一个；合并块保留全部来源（§11.5）
    observations: list[Observation] = []
    chosen_observation: str | None = None       # 必须是 observations 中的某个 id（有校验器）
    text: str = ""                              # 由 chosen_observation 派生；表格块为空
    cells: TableGrid | None = None              # 仅 table
    level: int | None = None                    # 仅 title，1..6（有校验器）
    semantic: FigureSemantic | None = None      # 仅 figure
    decisions: list[Decision] = []
```

校验器只做结构层面的检查：`level` 只允许出现在 title 上；`cells` 只允许出现在 table 上；`chosen_observation` 必须能在 `observations` 中找到。合法性检查中涉及跨块的部分（跳级、编号一致性）不放在这里，放在 `hierarchy/`。

### 2.9 图片语义（§6.7，阶段一只定形状）

```python
class Evidenced(IRModel):
    value: str | float | None
    level: EvidenceLevel

class ChartSemantic(IRModel):
    type: Literal["chart"] = "chart"
    chart_type: Evidenced; title: Evidenced | None = None
    x_axis: Evidenced | None = None; y_axis: Evidenced | None = None
    series: list[Series] = []          # Series(name: Evidenced, values: list[Evidenced])
    unit: Evidenced | None = None; axis_scale: Evidenced | None = None

class DiagramSemantic(IRModel):
    type: Literal["diagram"] = "diagram"
    diagram_type: Evidenced
    nodes: list[Evidenced] = []
    edges: list[Edge] = []             # Edge(src, dst, label: Evidenced | None, direction: Literal["forward","backward","both","unknown"])

class GenericSemantic(IRModel):        # photo / seal / other
    type: Literal["photo", "seal", "other"]
    summary: Evidenced
    visible_text: list[Evidenced] = []

FigureSemantic = Annotated[ChartSemantic | DiagramSemantic | GenericSemantic, Field(discriminator="type")]
```

### 2.10 工作区状态与 sidecar

"五个概念"之外，工作区还需要以下模型。sidecar（§4.5）就是 `DocumentState` 的导出形式。

```python
class PageState(IRModel):
    n: int
    unit: Literal["pdf_page", "docx_segment"]   # DOCX 按显式分页 / 分节切成段，用于跟踪进度
    status: PageStatus
    size_pt: tuple[float, float] | None = None
    render: str | None = None                   # Asset id

class LedgerEntry(IRModel):                     # 去向账目的最小单位（见下方说明）
    item: str
    unit: LedgerUnit                            # 〔P1-1〕条目是什么：native_line / pdf_image / ocr_block /
                                                # docx_paragraph / docx_table / docx_image / docx_deleted / docx_unsupported
    source: SourceAnchor
    chars: int
    disposition: Literal["output", "merged", "duplicate", "excluded", "failed"] | None = None
    block: str | None = None                    # 承载内容、或记录其去向 Decision 的块

class ImageRecord(IRModel):
    id: str; route: ImageRoute; shown: bool
    t: float | None = None; f: float | None = None
    regions: int = 0; complete: bool | None = None

class Stats(IRModel):              # 〔P1-1〕sidecar 中唯一允许在新请求与回放之间不同的部分
    requests: dict[str, int]; attempts: dict[str, int]; cache_hits: dict[str, int]
    tokens: dict[str, TokenUsage]   # TokenUsage(input, cached_input, output)，按服务
    cost_usd: float | None; wall_time_s: float

class DocumentState(IRModel):
    schema_version: Literal[1] = 1  # 〔P1-1〕
    id: str; source: str; source_sha256: str; format: Literal["pdf", "docx"]
    status: DocumentStatus
    engines: dict[str, str]; prompt_hashes: dict[str, str]
    pages: list[PageState]
    blocks: list[Block]; relations: list[Relation]; assets: list[Asset]
    images: list[ImageRecord]
    ledger: list[LedgerEntry]
    missing: list[Missing]          # Missing(block, reason)
    stats: Stats                    # requests / attempts / cache_hits / tokens / cost_usd / wall_time_s
    warnings: list[str]
    version: int                    # 每个事务 +1

class Sidecar(DocumentState):       # 〔P1-1〕导出形式 = DocumentState + 由账目算出的汇总
    accounting: AccountingSummary   # discovered / output / merged / duplicate / excluded / failed / unassigned
```

〔P1-1〕实现说明：模型在 `parserx/ir/` 各子模块中，按子模块导入（包的 `__init__` 只导出 anchor 与 base，避免与 `tables/grid.py` 循环导入）。sidecar 的 JSON Schema 由 `ir.schema.sidecar_json_schema()` 生成（序列化模式，所有字段必填、`additionalProperties: false`），`validate_sidecar()` 用标准 JSON Schema 校验器（`jsonschema`，已显式加入依赖），外部消费者不需要导入 ParserX。结构校验器另有：`status=failed` 的 Observation 必须有 `error`；`role=crop` 的 Asset 必须有 `derived_from`，`dpi` 只用于渲染图；表格块的 `text` 必须为空；`semantic` 只能出现在 figure 上。跨块规则不放在模型里，由 `accounting/` 与 `hierarchy/` 报告。

〔P1-4〕`PageState` 增加 `starts_with: "page_break" | "section_break" | None`（DOCX 段由什么开始，渲染 PAGE-BREAK / SECTION 锚点用）。

〔P1-4〕内容获取的实现要点（`parserx/content/`）：
- `extract_pdf` / `extract_docx` 返回 `Extraction`（pages、blocks、relations、ledger、assets 与其字节、engines、warnings、missing），`to_state()` 生成初始 `DocumentState`；`Asset.from_bytes` 是纯函数，`Workspace.create(files=)` 在写状态前写入资源字节。
- 原生 PDF：每个 PyMuPDF 文本行一个 `native_line` 账目条目；文本块按位置排序（行内同一行从左到右；〔Q33〕先找分栏：`content/order.py` 的递归 XY 切分——区域内找没有栏宽块穿过的竖向空白，穿过它的少数居中块（标题、公式）与通栏块成为分节，节内先左后右；没有分栏时按横向空白切成带、相邻带合起来仍能分栏就合并，最后按行排序；两侧必须都够宽、够高、并排，逐行配对的两侧（表单、无框表格）还需像正文栏：每侧至少三块、两侧等宽、多数块至少占本侧一半宽），块内的行按行排序（`row_order`）；〔Q34〕页面装饰：`content/furniture.py` 的 `mark_furniture` 在原生层判定通过的页上做跨页重复检测——页面上下各 10% 区域内（单独的页码且是本页最外侧的块时可到 15%）、高度不超过页高 4% 的文字块，去掉空白、数字串与单独的罗马数字记为 `#` 后的形状若在另一页同一区域、相近高度（页高 3% 以内）出现，就成为 HEADER / FOOTER / PAGE_NUMBER 块（没有字母的形状为页码），`excluded` 并写 `exclude` Decision（actor `program:content.furniture`），账目 excluded，文字留在 sidecar；`find_tables` 的规则表格成为 TABLE 块，表内的行记到表格块；每个块至少一条 `content_source` Decision。原生层质量判定新增**不可见文字**信号（渲染模式 3，可检索扫描件的 OCR 层）：语料 24 个 PDF 中只有 ocr_scan_jtg3362 含不可见文字（每页 100%），其封面被切成 7 张图片，v1 的主图判据漏掉了它。判定失败的页上**所有**嵌入图片都是 SCAN 块（扫描页引擎读取整页渲染图）。
- 扫描页：按 v1 相同的方式组装子 PDF（`no_new_id`，字节稳定，可命中既有缓存）；引擎阅读顺序为主，`block_order=None` 的区域（表格、图注、页眉页码）按位置插入（〔Q34〕上下相接的未排序区域（图注与其表格）作为一组，放在起点在其上方、横向与其重叠的区域中序列最靠后者之后——右栏的表跟随右栏、通栏的表跟在两栏之后；没有这样的区域时放在第一个起点在其下方的区域之前）；页眉、页脚、页码为 `excluded`；图片从整页渲染图裁剪（渲染图与裁剪图都是 Asset，带 `derived_from` 与 `transform`），图中 OCR 文字只作证据；无法转换的表格 HTML 保留为 OTHER 块文字（`degraded`）。未知标签映射为 OTHER 并记 warning。
- 选择步骤（`content/select.py`）：`integrate_scan_page` 把原生块标 `duplicate`（`duplicate_of` 指向重叠最多的新块）、扫描图标 `merged`，账目随之更新并重排 `order`；`mark_scan_failed` 保留原生文字（`degraded`）与扫描图并记 `missing`。接受门 `review_table` / `review_text` 返回 `GateCheck` 列表：图像证据（候选来自区域图像、有 `raw_ref`）；数字（原生证据的数字一律不得改；OCR 证据只允许在被要求核查的单元格里改数字；文本候选不得改动证据中的数字）；结构（网格合法，未被要求核查的单元格内容不得丢失）。
- DOCX：修订被删除的文字成为 `excluded` 块（`exclude` / `revision_deleted` Decision，账目 `docx_deleted`），被删除的段落标记按最终视图合并段落（账目 `merged`）；`mc:AlternateContent` 只读第一个 Choice；列表编号按 numbering.xml 渲染并写入段落文字（Word 显示的样子），同时记入 `TextStyle.numbering`；页眉页脚为 `excluded`；脚注尾注、批注、链接图片为 `failed` 块（〔Q44〕文本框的每个段落成为紧跟在锚定段落之后的正文块，账目 `docx_paragraph` output，锚点为 `…//w:txbxContent[k]/w:p[i]`，不带样式证据）（文字保留在 sidecar，账目 `docx_unsupported`，`missing` 列出）。样式名按 styles.xml 原样记录（内置样式为小写，如 `heading 1`）。

**修订记录 〔v1.9〕**：DOCX 按"接受全部修订"后的最终视图提取（Q26）。`w:ins` 与 `w:moveTo` 的文字进入正文；`w:del` 与 `w:moveFrom` 的文字作为账目条目，去向为 `excluded`，并配一条 `stage=exclude`、`choice=revision_deleted` 的 Decision。sidecar 的 `warnings` 注明文档含修订。`DocxAnchor.node_path` 在阶段一只到段落或单元格一级，`run_range` 可以为空。

**账目单位 〔增补〕**：指导 §2.3 原则 1 所说的"已发现内容"落到这些条目：原生 PDF 的文本行、OOXML 的段落 / 表格 / 图片节点、检测区域、OCR 块。每个条目只能有一个去向，`disposition=None` 就是未归属。`check` 要求 `discovered = output + merged + duplicate + excluded + failed` 且没有未归属条目。以行而不是字符为单位，是为了让账目规模可控，同时仍能发现整行丢失。行内丢字交给评测的字符指标发现。

**持久化 〔增补〕**：`<ws>/state.json`（`DocumentState`）+ `<ws>/assets/` + `<ws>/calls.jsonl`（每次工具调用的请求、信封、diff 与证据引用，§7.5）+ 文件锁。〔P1-2〕另存 `<ws>/source.<ext>`（输入文件副本，工具不依赖原路径，实验目录只需工作区本身）；`Workspace.create` 算作第一次提交（version 1）；`txn(actor, expect_version=, timeout=)` 在锁内读取最新状态、提交前整体重新校验（列表修改绕过赋值校验）、失败不写；`calls.jsonl` 同时记录提交（`type=txn`，actor 与新版本号）与工具调用（`type=call`）；资源按内容寻址（`assets/<asset id>.<ext>`，重复写入幂等）。查询函数在 `workspace/queries.py`（阅读顺序、邻近块、按页 / 段取块、标题树）。〔P2-1〕完整性：每次提交把写入的 `state.json` 字节的 SHA-256 记在 `head.json`（`version`、`state_sha256`）与该条 `txn` 记录里；事务开始前若 `state.json` 与 `head.json` 不符即抛 `WorkspaceTampered`，后续提交因此无法把工具之外的改写带进新版本；每条 `call` 记录带 `txns`——该调用的 `Workspace` 实例自上一条调用记录以来提交的版本号。`workspace/integrity.py` 的 `verify_workspace(root)` 做完整核对：`state.json` 与 `head.json`、最后一条事务一致；事务版本 1…N 连续；每个版本恰好被一条调用记录认领（没人认领的提交来自工具以外，例如脚本直接调用工作区接口）；资源文件与输入副本的摘要不变（.doc 输入的副本是转换后的 .docx，不比）。先用 JSON 文件；几百页文档的 state.json 若超过约 50 MB，或写入明显拖慢速度，再换 SQLite，届时只改 `workspace/` 内部。

## 3. TableGrid（§4.3，阶段零 P0-1 落地）

```python
# parserx/tables/grid.py
class Cell(IRModel):
    row: int; col: int
    rowspan: int = 1; colspan: int = 1
    content: str
    is_header: bool = False
    anchors: list[SourceAnchor] = []
    rec_confidence: float | None = None

class TableGrid(IRModel):
    n_rows: int; n_cols: int
    cells: list[Cell]               # 只存起始单元格；被跨度覆盖的位置不重复存
    header_rows: int = 0

    # 校验器：跨度不越界、不重叠；header_rows <= n_rows
    @classmethod
    def from_html(cls, html: str) -> "TableGrid": ...     # 复用 _collect_rows + _build_table_grid
    @classmethod
    def from_gfm(cls, lines: list[str]) -> "TableGrid": ...
    def to_gfm(self) -> str: ...        # 阶段一；有合并单元格时抛 ValueError
    def to_html(self) -> str: ...       # 阶段一
    @property
    def has_spans(self) -> bool: ...
    def slot(self, row: int, col: int) -> Cell | None: ...  # 返回覆盖该位置的单元格

def find_tables(markdown: str) -> list[TableSpan]: ...     # TableSpan(start, end, fmt: "gfm"|"html", grid)
```

渲染规则（§4.5）：没有合并单元格输出 GFM，有则输出 HTML。〔P1-1〕GFM 只能表达一行表头，所以判据是 `needs_html = has_spans or header_rows > 1`，`to_gfm()` 在 `needs_html` 时抛 ValueError，避免丢掉表头结构；没有标记表头的网格按 GFM 要求以第一行作表头（与 v1 相同；补一行空表头会让整张表的行位置错位）。`to_html()` 表头行用 `<th>`，未被任何单元格覆盖的位置输出空 `<td>`；〔P1-11〕没有标记表头的网格与 `to_gfm` 一样以第一行作表头（扫描页引擎的 HTML 表格全是 `<td>`，此前 HTML 输出因此没有表头，jtg3362 的表头关联正确率从 v1 的 0.733 降到 0.067）。

## 4. 工具返回信封（§5.2）

```python
# parserx/tools/envelope.py
class DocText(IRModel):
    """来自文档的文字，是数据不是指令（§3.3 注入隔离）。
    JSON 形如 {"doc_text": "..."}；运行时适配层把它放进数据围栏，不拼进指令。"""
    doc_text: str

class Cost(IRModel):
    requests: dict[str, int] = {}       # 真实网络请求：ocr / vlm / llm
    attempts: dict[str, int] = {}
    cache_hits: dict[str, int] = {}
    tokens_in: int = 0; tokens_out: int = 0
    usd: float | None = None
    wall_s: float = 0.0
    budget_left: BudgetLeft | None = None   # 文档级剩余：requests / usd / seconds

class FailureCode(StrEnum):
    INVALID_REQUEST="invalid_request"; NOT_FOUND="not_found"; BUDGET_EXHAUSTED="budget_exhausted"
    SERVICE_ERROR="service_error"; TIMEOUT="timeout"; CACHE_MISS_OFFLINE="cache_miss_offline"
    CHECK_FAILED="check_failed"; VERSION_CONFLICT="version_conflict"
    WORKSPACE_TAMPERED="workspace_tampered"   # 〔P2-1〕state.json 被工具以外的方式改写；所有工具拒绝，不能导出

class Failure(IRModel):
    code: FailureCode
    message: str
    retryable: bool
    targets: list[str] = []             # 受影响的页或块

class Change(IRModel):                  # diff 条目
    target: str; field: str
    before: JsonValue; after: JsonValue

class UnresolvedKind(StrEnum):
    PAGE_PENDING="page_pending"; BLOCK_FAILED="block_failed"; TABLE_UNCERTAIN="table_uncertain"
    EVIDENCE_CONFLICT="evidence_conflict"; STRUCTURE_PENDING="structure_pending"
    BUDGET_SKIPPED="budget_skipped"; ASSET_MISSING="asset_missing"
    TABLE_MERGE_CANDIDATE="table_merge_candidate"   # 〔Q33〕未合并的跨页续表候选（见 §5.7）

class Unresolved(IRModel):
    target: str; kind: UnresolvedKind; detail: str

class Envelope(IRModel, Generic[R]):
    tool: str
    doc: str
    ws_version: int                     # 调用结束后的工作区版本
    ok: bool                            # 没有致命失败；批量调用中部分目标失败时仍为 True，失败写在 failures 里
    result: R | None
    cost: Cost
    failures: list[Failure] = []        # 〔偏离〕§5.2 写的是单数 failure；批量语义需要列表
    diff: list[Change] = []
    unresolved: list[Unresolved] = []
```

## 5. 七个工具

### 5.1 CLI 公共约定（§5.3）

```
parserx tool <name> --ws <工作区目录> [参数…] --json
  --request <file.json | ->   用完整请求模型（pydantic）代替命令行参数
  --expect-version N          工作区版本不符时返回 version_conflict，避免在过期状态上写入
stdout 只输出一个 Envelope JSON；日志写 stderr。
退出码：0 = 已返回信封（看 ok 与 failures）；2 = 请求不合法（同时仍输出带 invalid_request 的信封）；1 = 内部错误。
parserx tool schema <name>    输出该工具请求与结果的 JSON Schema（供运行时适配层、codex --output-schema 使用）
```

创建工作区不属于七个工具，由运行时启动时调用：

```
parserx workspace init <input.pdf|docx> --ws <dir> [--config cfg.yaml] --json
    → Envelope[InitResult]：原生提取、账目初始化、页面状态全部 pending
```

### 5.2 overview

```
parserx tool overview --ws <dir> --json
```

```python
class OverviewResult(IRModel):
    doc: DocInfo                        # source 文件名、format、页数 / 段数、status、version
    native_chars: int
    images: int; tables: int
    styles: list[StyleSummary] | None   # DOCX：样式名、大纲级别、basedOn 链的根、使用次数
    numbering: list[NumberingSummary] | None   # DOCX：numId / 层级 / 格式 / 使用次数
    pages: list[PageRow]                # n、unit、status、native_chars、blocks、unresolved 数
    blocks_by_kind: dict[BlockKind, int]
    blocks_by_status: dict[BlockStatus, int]
    outline: list[OutlineNode]          # 当前标题树：block、level、text: DocText（截断预览）
    unresolved: dict[UnresolvedKind, int]
```

### 5.3 read

```
parserx tool read --ws <dir> (--page N | --block ID) [--image none|page|crop] [--context K]
                  [--dpi 150] [--pad-pt 6] [--observations] --json
```

```python
class ReadRequest(IRModel):
    page: int | None = None; block: str | None = None   # 二选一（有校验器）
    image: Literal["none", "page", "crop"] = "none"
    context: int = 0                  # 前后各取 K 个邻近块
    dpi: int = 150; pad_pt: float = 6.0
    observations: bool = False

class ReadResult(IRModel):
    image: ImageRef | None            # asset id、绝对路径、宽高、coord_space、到页面坐标的 transform
    blocks: list[BlockView]           # 目标块及其邻近块：id / kind / order / status / level / text: DocText / cells / anchors / chosen_observation
    observations: list[ObservationView] | None
```

图片只返回文件路径，不返回 base64。§7.3 要求验证图片是否真正进入了主模型的视觉上下文，这由运行时适配层负责。

### 5.4 recognize

```
parserx tool recognize --ws <dir> (--pages 1,3-5 | --blocks ID,… | --regions regions.json)
                       --engine paddleocr|vlm|native|layout [--force] --json
```

```python
class RegionRef(IRModel):
    anchor: PdfAnchor | AssetAnchor
    hint: BlockKind | None = None

class RecognizeRequest(IRModel):
    pages: list[int] = []; blocks: list[str] = []; regions: list[RegionRef] = []
    engine: Literal["paddleocr", "vlm", "native", "layout"]
    force: bool = False               # 已有同引擎、同输入的 ok 结果时仍再识别一次（仍会走缓存）

class RecognizeResult(IRModel):
    observations: list[ObservationView]   # 新写入的 Observation
    pages: list[PageRow]                  # 页面状态变化
    selections: list[SelectionOutcome]    # 程序选择步骤的结果（Q20）
```

批量、分批、并发、重试、页数校验都在工具内部完成（§5.2）。部分页失败时 `ok=True`，失败页写在 `failures` 里，页面状态记为 failed。

### 5.5 review_table

```
parserx tool review_table --ws <dir> --block ID --issues issues.json [--context table|table+caption|page] --json
```

```python
class TableIssue(IRModel):
    kind: Literal["char", "structure"]    # §6.6：只处理字符错误和结构错误两类，含义解释是另一个任务
    cells: list[tuple[int, int]] | None = None
    note: str

class ReviewTableRequest(IRModel):
    block: str
    issues: list[TableIssue]
    context: Literal["table", "table+caption", "page"] = "table"

class GateCheck(IRModel):
    name: Literal["image_evidence", "numeric_consistency", "structure_valid"]
    passed: bool; detail: str

class ReviewTableResult(IRModel):
    candidate: str                        # 新写入的候选 Observation id
    grid: TableGrid
    cell_diff: list[CellChange]           # row、col、before: DocText、after: DocText、kind
    undetermined: list[tuple[int, int]]
    gate: list[GateCheck]                 # 接受门逐项结果（程序执行）
    adopted: bool                         # 接受门通过后由程序同步采用（Q20）
```

每块最多复核 1 次（§8.1）。没有新证据时再次调用，返回 `invalid_request`，原因写明"无新证据"。

### 5.6 describe_figure

```
parserx tool describe_figure --ws <dir> --block ID [--schema auto|chart|diagram|photo|seal|other] --json
```

```python
class DescribeFigureRequest(IRModel):
    block: str
    schema_: Literal["auto", "chart", "diagram", "photo", "seal", "other"] = Field("auto", alias="schema")

class DescribeFigureResult(IRModel):
    semantic: FigureSemantic
    table_block: str | None = None        # 图片形式的表：返回 TableGrid，并建立或更新对应的表格块（§6.7）
```

描述失败时，`semantic` 缺省、图片保留，不影响正文（§6.1）。

〔P1-6〕渲染实现（`parserx/render/`）：`render_markdown(state, image_dir="images")`、`export_sidecar(state)`（DocumentState + 由 `check` 算出的账目汇总，通过 schema 校验）、`sidecar_json`（同一状态逐字节相同，只有 `stats` 随运行变化）、`write_export(state, ws_root, out_dir, name)`（写 `<name>.md`、`<name>.blocks.json`，只复制渲染出来的图片到 `images/`）。规则：级别待定的标题渲染为普通段落（渲染器不臆造结构）；段落内换行按 pandoc `east_asian_line_breaks` 约定合并（两侧都是宽字符时直接相连，否则一个空格）；段首 `#`、`>` 转义；公式没有定界符时包成 `$$…$$`；图片替代文字只写类型与标题（`chart: 标题`、`diagram: 类型`、`photo`，无语义时 `图片`），不重复描述；识别失败的扫描图保持可见（`扫描图像`）；excluded / merged / duplicate / failed 块不渲染。

**〔v1.9〕渲染格式**：语义块必须是紧跟在图片行之后的引用块，首行为 `> [图片语义]`，然后每项一行并标出证据层级。评测的规范化步骤（`parserx/eval/normalize.py`）会把这种结构当作图片描述剔除；换成其他写法，描述会被计入正文分数。

```
![<一句话>](images/<file>)

> [图片语义] chart · 可见
> 标题：2024 年各月产量（可见）
> 系列 产量：1 月 120，2 月 135（可见）；3 月约 150（估读）
```

### 5.7 apply_structure

```
parserx tool apply_structure --ws <dir> --changes changes.json|- [--atomic] --json
```

```python
StructuralKind = Literal["title", "text", "list", "caption", "footnote", "header", "footer", "page_number", "other"]
# table / figure / formula / scan 不能通过结构变更改成别的类型：内容表示不同，改了会丢数据

class SetRole(IRModel):      op: Literal["set_role"];   block: str; kind: StructuralKind; reason: str; evidence: dict[str, float | int | str | bool] = {}
class SetLevel(IRModel):     op: Literal["set_level"];  block: str; level: int | None;     reason: str; evidence: dict[str, float | int | str | bool] = {}
class MoveAfter(IRModel):    op: Literal["move_after"]; block: str; after: str | None;    reason: str
class AddRelation(IRModel):  op: Literal["add_relation"]; kind: RelationKind; src: str; dst: str; confidence: float | None = None
class RemoveRelation(IRModel): op: Literal["remove_relation"]; relation: str
class MarkPending(IRModel):  op: Literal["mark_pending"]; block: str; reason: str   # 证据不足：保留正文，块记为 degraded，结构待定
class MergeTables(IRModel):  op: Literal["merge_tables"]; first: str; second: str; drop_rows: int = 0; reason: str; evidence: dict[str, float | int | str | bool] = {}   # 〔Q33〕跨页续表

StructureChange = Annotated[SetRole | SetLevel | MoveAfter | AddRelation | RemoveRelation | MarkPending | MergeTables, Field(discriminator="op")]

class ApplyStructureRequest(IRModel):
    changes: list[StructureChange]
    atomic: bool = False                  # True：任一条被拒则全部不生效

class LegalityRule(StrEnum):
    UNKNOWN_BLOCK="unknown_block"; KIND_NOT_STRUCTURAL="kind_not_structural"
    LEVEL_ON_NON_TITLE="level_on_non_title"; LEVEL_SKIP="level_skip"
    NUMBERING_LEVEL_INCONSISTENT="numbering_level_inconsistent"
    ORDER_CYCLE="order_cycle"; DUPLICATE_RELATION="duplicate_relation"
    NOT_MERGE_CANDIDATE="not_merge_candidate"; ROWS_NOT_DUPLICATE="rows_not_duplicate"   # 〔Q33〕

class Rejection(IRModel):
    index: int; rule: LegalityRule; detail: str

class ApplyStructureResult(IRModel):
    accepted: list[int]
    rejected: list[Rejection]
    outline_after: list[OutlineNode]
```

请求模型里根本没有文本字段，"结构变更永不改原文"因此由 schema 保证，不需要运行时检查。每条被接受的变更都会在对应块上写一条 Decision（heading_role / heading_level，actor = 调用方）。

〔Q33〕跨页续表（`parserx/tables/merge.py`，指导 §6.9）：`merge_candidate(state, a, b) -> MergeCandidate | None`、`merge_candidates(state)`、`propose_merges(state)`。**候选**＝两个可见表格列数相同、b 在 a 最后一页的下一页、阅读顺序中两者之间只有页面装饰（页眉页脚页码类块；原生页还没有装饰标签，所以页面上下 12% 区域内、高度不超过页高 3% 的短行，若是单独的页码或去掉数字后在另一页同一区域重复出现，也按装饰跳过——只用于判断相邻，不隐藏）。**确认**＝左右边缘与页宽之比的偏差都不超过 0.05，且 b 没有与 a 不同的自有表头；b 开头若逐字重复 a 的表头行（空白除外），合并时去掉（`drop_rows`）。`merge_tables` 的合法性：必须是候选（`not_merge_candidate`），只能去掉逐字重复表头的行（`rows_not_duplicate`）。应用：a 追加 b 的行、保留两者全部锚点，b 标 `merged`，写 `continues` 关系与 `structure` Decision（`merge_table` / `merged_into`），b 的 output 账目条目改为 merged 到 a。固定序列运行时在标题之前以 actor `program:tables.merge` 合并已确认的候选；未确认或未合并的候选在 `unresolved` 中以 `table_merge_candidate` 列出，留给能看图的运行时。`apply_structure` 的 diff 增加表格行数（字段 `rows`）。

### 5.8 check / export

```
parserx tool check  --ws <dir> --json
parserx tool export --ws <dir> --out <dir> --json
```

```python
class CheckResult(IRModel):
    accounting: AccountingSummary         # discovered / output / merged / duplicate / excluded / failed
    unassigned: list[str]                 # 未归属的账目条目：非空即为缺陷
    illegal_refs: list[IllegalRef]        # 指向不存在的块或资源的引用
    missing_assets: list[str]
    pages: list[PageRow]
    document_status: DocumentStatus
    missing: list[Missing]
    exportable: bool                      # 没有未归属条目、没有非法引用、没有 pending 页

class ExportResult(IRModel):
    markdown: str                         # 路径
    sidecar: str                          # 路径（.blocks.json，可通过 schema 校验）
    status: DocumentStatus
    missing: list[Missing]
```

〔P1-5〕实现（`parserx/accounting/check.py`，`check(state, root=None)`）：`CheckResult` 增加 `mismatched`——去向与块状态不一致的账目条目（例如标为 output 的条目指向 duplicate / excluded 块，即静默丢失）；去向允许的块状态：output→ok/degraded，merged→ok/degraded/merged，duplicate→duplicate，excluded→excluded，failed→failed 或可见的回退块。非法引用覆盖：重复 id、账目指向的块、Relation 两端、AssetAnchor、Decision.refs、`derived_from`、页面渲染图、ImageRecord、missing。`missing` = 已记录的缺失 + 尚未记录的 failed 块（原因取其最后一条 Decision）。文档状态：有 pending 页为 in_progress；没有任何条目进入输出（output / merged）为 failed（§4.5"提取本身失败"）；有缺失、失败或跳过的页、failed 条目为 partial；否则 complete。`exportable` = 无未归属、无不一致、无非法引用、无缺失资源文件、无 pending 页。`PageRow` 定义在 `workspace/views.py`（overview 与 check 共用）。

`export` 在 `exportable=False` 时返回 `check_failed`，不写文件。预算耗尽导致的失败或跳过，只要已经记入账目，仍可导出，状态为 `partial`。这样区分了"已知的缺失"和"静默丢失"两种情况。

### 5.9 〔P1-7〕实现与偏离

- 入口：`parserx.tools.call_tool(name, ws, request, config=)`（进程内，固定序列运行时用）与 `parserx tool <name> … --json`、`parserx workspace init`、`parserx tool schema <name>`（CLI）。每次调用写一条 `calls.jsonl`（请求、信封）。
- **文档文字一律在 `DocText` 中**：块文字、观察文字、表格单元格（`TableView` / `CellView.content`）、大纲预览、复核的单元格差异，以及 `describe_figure` 的结果——结果的 `semantic` 是渲染后的 `> [图片语义]` 文本（`DocText`），另给 `type`；类型化的语义在 sidecar 的块上（〔偏离〕接口原写 `semantic: FigureSemantic`：图中可见文字的转录也是文档文字）。
- 失败码增加 `internal_error`（退出码 1）。请求校验失败退出码 2；找不到工作区、块或页为 `not_found`。
- 预算与统计跨调用持续：`state.stats` 保存累计请求、token、费用与耗时，每次调用的上下文据此预载文档预算，调用结束后把本次用量加回。
- `read` 生成的页面渲染图与裁剪图写在 `<ws>/renders/`（按内容命名），**不登记到状态**，读取不改变工作区版本；DOCX 没有页面图，只有图片块能取图。
- `recognize`：`paddleocr` 只识别整页，只处理原生层判定失败（pending / failed / skipped）的页；原生层通过的页返回非致命的 `invalid_request`（选择步骤会保留原生文字）；`force` 重新识别已由扫描页引擎识别的页；`native` 只返回已有的原生观察；`layout` 见 P1-9；`vlm` 在阶段四。结果最多 50 条观察视图，另给 `observations_total`，保持 JSON 长度有界。
- `review_table`：只支持有页面几何的表格（DOCX 表格来自原生 XML，没有图像证据）；裁剪图登记为 Asset，候选 Observation 的锚点指向它；"没有新证据"＝同一问题针对同一份识别结果（最近一条非复核的 Observation）再次提出；接受门见 §2 与 `content/select.py`。
- `describe_figure`：已有描述时直接返回（`cached=true`，不发请求）；描述失败时图片保持原样，失败写在 `failures`。
- `apply_structure`：请求增加 `actor`（写入每条 Decision）；`DecisionStage` 增加 `structure`（移动、待定）。所有被拒或 atomic 下有被拒时不开事务、不改版本。
- 〔P1-7b，试用后〕`read` 默认只返回当前采用的内容（隐藏 duplicate / merged / excluded 块），`include_hidden` 与 `geometry`（锚点与坐标）按需；`recognize` 默认不返回观察视图（`observations=true` 时返回）；VLM 回答在 JSON 之后附带内容时取第一个完整 JSON；两次都无法解析、或被端点内容策略拒绝时，失败信息写明下一步；复核未采用时写一条 `table_uncertain` 未解决项（附未通过的检查）。
- 配置新增 `tools`（描述与复核的 reasoning effort、max tokens、`read_dpi`、`crop_pad_pt`、`scan_batch_pages`）；提示词在 `parserx/prompts/`（`describe_figure.md`、`review_table.md`），内容哈希计入缓存键与 `state.prompt_hashes`。

- 〔P2-1〕每次调用先核对工作区完整性（`Workspace.tampered`，在锁内比较 `state.json` 与 `head.json`）；不一致时所有工具（包括 `check`、`export`）返回 `workspace_tampered`（`retryable: false`），不提交、不导出，调用仍记入 `calls.jsonl`。

### 5.10 〔P1-9〕版面检测与图片路由（影子运行）

- 检测器：`layout/detector.py`，rapid-layout 1.2.1 的 pp_doc_layoutv3（CPU，单页约 0.24 s，进程内只加载一次）；标签与 PaddleOCR-VL 同为 25 类，映射在 `layout/labels.py`（`LAYOUT`；另有 `DOCX` 元素到 BlockKind 的映射，DOCX 读取器的块类型经它取得）。检测结果是本地计算，存入 `.parserx_cache/derived/layout/`（键＝模型版本 + 图片字节），离线回放不加载模型。单元测试用假检测器；真实模型的用例标 `live_layout`，默认不跑。
- 面积：`layout/area.py` 的 `coverage` 在整图上求并集面积；文字类（text/title/list/table/caption/footnote/formula 与页眉页脚页码）为 t，图形类（image/chart/seal）为 f；落在图形框内（面积 ≥ 90%）的文字框只算 f。
- 路由：`routing/image.py`，廉价过滤（短边 < 30 px、像素标准差 < 1、长宽比 ≥ 12、**以及 v1 的"琐碎图"：面积 ≤ 12000 px² 且长边 ≤ 160 px**，见 Q31）→ DECORATIVE；否则按 §6.5 表格；检出区域置信度全低或 t、f 都 < 0.2 → UNCERTAIN。阈值在配置 `routing`。
- 影子运行＝`recognize --engine layout`：`pages` 对页面渲染图（`layout.page_dpi`，默认 100）检测，每个区域作为 `layout` Observation 挂到重叠最多的块（被取代的 duplicate / merged 块除外，页眉页脚仍可挂），无块可挂的区域计入 warnings；`blocks` 对图片块路由，写 ImageRecord（`shown` 为实际是否渲染）与 `image_route` Decision，区域写成图片像素坐标的 Observation。**只有 DECORATIVE 生效**（块 excluded、账目 excluded、不渲染、不描述）；其余路由的 Decision 以 "shadow" 开头，不改变任何块。已处理的页与图片不重复，`force` 重做（检测仍来自派生缓存）。

### 5.11 〔P1-10〕固定序列运行时

- `parserx/runtimes/pipeline.py`：`run(input, ws, out, config)` 依次调用 workspace init → recognize（paddleocr，只对 pending 页）→ recognize（layout 影子：PDF 全部页面 + 可见图片块）→ describe_figure（可见且未描述的图片，预算耗尽即停）→ 〔Q33〕已确认的跨页续表（`tables/merge.py`，actor `program:tables.merge`）→ 结构（DOCX：`hierarchy/docx_styles.py`；PDF：`runtimes/v1_structure.py` 的原生文字标题（actor `adapter:v1`，临时）与 `hierarchy/engine_titles.py` 的扫描页标题（〔Q33〕），两者按阅读顺序一起做层级统一，再分两次调用写入；只因依赖另一来源的标题而被判跳级的层级，在两者都写入后再发一次）→ apply_structure → check → export。一篇文档的所有工具调用共用一个上下文（一个计数器、一个预算）；信封里的 `cost` 与写回 `state.stats` 的是本次调用的增量。
- `pipeline: v1 | v2`（配置顶层）；`Pipeline.parse_result` 在 v2 时转给 `runtimes.pipeline.parse_result`，返回同样的 `ParseResult`，另带 `sidecar_json` 与 `document_status`；离线回放缺响应时返回带 `cache_misses` 的结果（评测记为未执行，不记为失败）。`configs/regression_v2.yaml` 继承回归配置并设 `pipeline: v2`；冻结 run 另存 `outputs/<doc>.blocks.json`。工作区默认在临时目录，`runtime.workspace_root` 可保留。
- DOCX 确定性结构：大纲级别（直接或样式继承，9 为正文）或 `heading N` / `标题 N` 样式 → 标题；`Title` / `标题` 样式为文档标题（H1），存在时其余标题下移一级；带编号而无标题证据的段落 → list。
- 层级统一（`hierarchy/levels.py`，§6.8 的文档级统一）：同一编号模式取多数层级（并列取浅），再按阅读顺序使每个标题最多比前一个标题深一级；只移动层级，不增删标题。
- PDF 临时适配器：运行 v1 不调用服务的 provider → metadata → reading order → header/footer → code block → chapter（关闭 LLM 兜底），把与 v2 块文字相同（忽略空白）的标题作为结构变更提交；v1 标题落在更大的 v2 块里时不应用（结构变更不拆分文字）。

### 5.12 〔P2-5〕process、批量描述、Agent 修改与排除

- `process`（`tools/process.py`）：一次调用完成标准处理——待识别页的扫描页引擎、版面影子运行、未描述图片的批量描述、已确认的跨页续表、标题（DOCX 样式；PDF 的 `adapter:v1` 与引擎标题统一层级，因依赖另一来源而被判跳级的层级在两者都写入后再发一次）、`check`。各步直接在本次调用的上下文里运行，只有一条调用记录、请求只计一次；已完成的步骤跳过，再次调用不发请求。结果是摘要：`steps`（执行了的步骤与简述）、`pages`、`blocks_by_kind`、`figures`（described / failed / not_described）、`titles`（with_level / pending）、`check`（exportable、状态、账目），以及 `worklist`——未解决项（最多 200 条，另给 `worklist_total`），Agent 据此处理需要判断的地方，不必逐页通读。固定序列运行时改为 `workspace init → process → export`（v2 冻结 run 回放逐字节一致）。
- `describe_figure --blocks a,b,…`（请求字段 `blocks`，与 `block` 二选一）：请求并发（`services.vlm.max_concurrent`），结果按给出的块顺序在一次事务中生效；某一块的问题（不是图片、没有图像、格式不能发送）只作为该块的失败；有请求数预算时按块顺序截断，结果不随完成顺序变化。结果增加 `items`（每块的类型、渲染后的描述、是否已有描述）。`--schema` 指定类型时，JSON Schema 只允许该类型。

- `correct`（`tools/correct.py`，Q30）：主 Agent 看图读出的修改直接提交，不再让服务层 VLM 重读。请求：`block`、`image`（`read --image` 为该块或其所在页返回的图像 id）、`reason`，以及 `edits`（正文：`{find, replace}`，`find` 须在当前文字中恰好出现一次）或 `cells`（表格：`{row, col, content}`，网格内的空位可以补填，Q45）。接受门（`content/select.py::correct`）：图像证据——该图确由本工作区的 `read` 为这个块或它所在的页生成（从 `calls.jsonl` 核对）；数字——原生文字层的数字不得改，OCR 文字的修改限于声明的片段；结构——结果非空。修改成为新的 Observation（`task=correct`、`engine=agent`），旧的识别结果保留为证据，写 `review_accept` Decision（证据含图像 id）；未采用时返回 `evidence_conflict` 未解决项。DOCX 正文来自 XML、没有页面图像，不能这样修改。
- `apply_structure` 新增：`set_role` 为 title 时可带 `level`（一条变更完成角色与层级，层级照常检查）；`exclude`（必须写 reason；块 excluded，账目中 output / merged 的条目改为 excluded，文字留在 sidecar，写 `exclude` Decision；规则 `reason_required`、`not_visible`）；`restore`（撤销页面装饰、装饰图或此前的 `exclude`，账目恢复为 output；修订删除的文字不可恢复，Q26；规则 `not_excluded`、`not_restorable`）。

- 〔Q43〕编号一致性的作用范围：两个同一编号模式的标题只有在同一范围内才要求同级；它们之间若有一个不属于该编号体系（1 / 1.1 / 1.1.1 为一体系，附录的 C / C.1 为一体系，其余每种写法各自一体系）、且层级不深于两者中较浅者的标题（例如"附件 3 检测报告"），就是不同范围（`hierarchy/legality.py::_same_section`）。
- `ask_image`（`tools/ask_image.py`）：看图的第二种方式——主 Agent 不自己看图，向服务层 VLM 提问：`block`（该块的裁剪图）或 `page`（整页图）加 `question`，VLM 只依图作答（提示词 `prompts/ask_image.md`：看不清就说看不清、转录照原样），答案作为 `DocText` 返回，另给所读图像的 id；不改工作区。这张图可作为之后 `correct` 的图像证据（该块，或该页上的任一块）。配置 `tools.ask_reasoning_effort`（默认 low）、`tools.ask_max_tokens`。

### 5.13 〔P2-1〕Agent 实验装置

- `runtimes/codex.py`：`exec_command` 生成 `codex exec` 命令行——模型与推理强度显式传入；`--sandbox workspace-write` 并允许网络；`--ephemeral --ignore-user-config --ignore-rules`，关闭 memories、插件、应用、浏览器、电脑操作、图片生成、子 Agent、目标、hooks 与网页搜索，只留下沙箱里的 shell；`usage_from_events` 从 `--json` 事件流读轮数、各类条目数、命令数（失败数）与 token（输入 / 缓存 / 输出 / 推理）；`audit_events` 是卫生审计：命令里出现的绝对路径、`..` 与 `~` 路径必须在实验目录或系统位置（`/tmp`、`/usr` 等），出现答案关键词（`expected.md`、`ground_truth`、`eval_runs` …）、仓库、实验根目录（其他文档、工具快照）或 `~/.codex` / `~/.claude` / `~/.config` 为 forbidden，其余目录外路径为 outside；shell 以外的工具条目（网页搜索、MCP、子 Agent）与直接编辑 `ws/`、`out/` 的文件修改也使运行作废；未知条目类型只记为待查。
- `runtimes/px.py`：实验目录中 `./px` 的实现。只允许 `workspace init`、`tool <七个工具之一>`、`tool schema` 与 `python`（快照的 Python，用于只读分析）；配置固定（拒绝 `--config`，自动加实验目录的 `parserx.yaml`）；服务密钥从实验目录以外的 env 文件读入，只存在于工具进程，`px python` 的环境里去掉这些变量与名字像密钥的变量。
- `runtimes/experiment.py`：`doc_config`（响应缓存放在实验目录内，`${VAR}` 保持占位）、`prepare_doc_dir`（只写 `input.<ext>`、`px`、`parserx.yaml`、`AGENTS.md`、`skills/`，返回各文件摘要）、`listing_problems`、`config_problems`（不得含仓库路径与密钥值）、`render_task`（`{{#rN}}…{{/rN}}` 按轮次取舍、`{{name}}` 必须都有值）、`verify_run`（在快照里运行：工作区完整性、导出核对——最后一次写入 `out/` 的成功导出、其 Markdown 是否等于最终状态的渲染、`out/` 中不是导出写的文件——、`check` 摘要与未解决项、各工具调用次数与失败码、服务请求与费用）。任务模板在 `runtimes/agent_task.md`。
- 〔P2-5〕任务模板除轮次块外还有选项块：`{{#vision_agent}}`（Agent 自己看图，`read --image`）与 `{{#vision_tool}}`（经 `ask_image` 看图）；装置 `run --vision agent|tool`，tool 模式下 Codex 另加 `--disable view_image`，主 Agent 相当于纯文本模型。运行记录写入所用方式。
- `scripts/agent_explore.py`：`snapshot`（干净的 HEAD 经 `git archive` 构建 wheel，按 `uv.lock` 装进仓库外的虚拟环境并预编译，复制版面模型，生成 `px-run`；记录提交、wheel 与依赖摘要、Codex 版本、主 Agent 与服务层模型、配置指纹、Skill 与模板摘要）、`run`（每篇新建实验目录并做事前检查；Codex 进程的环境去掉 `.env` 中的变量与名字像密钥的变量；截止时间按 Q39；一轮之内 Codex 版本与模型必须与快照一致；`--rerun` 把旧目录改名为 `.void<N>` 并记入人工介入）、`verify`（写 `run/record.json`：运行条件、主 Agent 用量、工具用量、结果、评分（有标注时，指标版本同冻结 run）、卫生结论、人工介入）、`summary`。

## 6. 阶段一测试清单（先写测试）

| 测试文件 | 覆盖 |
|---|---|
| `test_ir_models.py` | 五个概念 + Decision：JSON 往返；`extra="forbid"` 拒绝未知字段；anchor 判别；Block 的结构校验器 |
| `test_table_grid.py` | 阶段零已建；阶段一补 GFM / HTML 渲染与往返 |
| `test_workspace.py` | 创建 / 打开 / 事务原子性 / 版本号 / 调用日志 |
| `test_accounting.py` | 平衡、未归属、非法引用、partial 与 check_failed 的区分 |
| `test_tools_contract.py` | 每个工具：信封 schema；文档文字只出现在 DocText 中；各失败码；apply_structure 的每条合法性规则 |
| `test_scheduling.py` | 预算预留与结算、可重试错误分类、超限时标记 skipped_budget |
| `test_layout_labels.py` | 三套标签的映射表是完整的 |

合成输入都是 Block 级的（几行文本、一张生成的图片、手写的区域列表），不依赖整篇文档（§13）。
