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
| `parserx/render/` 〔v1.9〕 | §4.5 的 Markdown 契约与 sidecar 导出（v1 的 `assembly/` 不动） | `render_markdown(state) -> str`、`export_sidecar(state) -> dict` |
| `parserx/hierarchy/legality.py` 〔v1.9〕 | `apply_structure` 的合法性检查（§6.8 的完整实现在阶段四） | `check_changes(state, changes) -> list[Rejection]` |
| `parserx/prompts/` 〔v1.9〕 | 工具内部 VLM 任务的提示词文件（内容哈希计入缓存键）；与面向运行时的 Skill 分开 | `load_prompt(name) -> (text, sha256)` |
| `parserx/accounting/` | 去向账目与检查 | `check(state) -> CheckResult` |
| `parserx/tools/` | 七个工具、统一返回信封、JSON CLI | §4、§5 |
| `parserx/skills/` | 三份 Skill（Markdown）+ 内容哈希 | `load_skill(name) -> SkillText(text, sha256)` |
| `parserx/layout/` + `parserx/routing/image.py` | 检测器封装、标签映射、面积统计、图片路由；**影子运行**：只写 Decision，不影响输出 | `Detector.detect(asset) -> list[Observation]`、`labels.to_kind(source, label)`、`route(asset, regions) -> RouteResult` |
| `parserx/runtimes/pipeline.py` | 固定序列（流水线运行时的最小形态），全部通过工具函数完成；`pipeline: v1 \| v2` 开关，v2 返回同样的 `ParseResult` | `run(input, ws, config) -> ExportResult` |
| `parserx/runtimes/v1_structure.py` 〔v1.9，临时〕 | 原生 PDF 的标题来源：复用 v1 不调用服务的元数据构建与章节处理，经 `apply_structure` 写入（actor `adapter:v1`）；阶段四删除（Q24） | `propose_structure(pdf_path, state) -> list[StructureChange]` |

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

**修订记录 〔v1.9〕**：DOCX 按"接受全部修订"后的最终视图提取（Q26）。`w:ins` 与 `w:moveTo` 的文字进入正文；`w:del` 与 `w:moveFrom` 的文字作为账目条目，去向为 `excluded`，并配一条 `stage=exclude`、`choice=revision_deleted` 的 Decision。sidecar 的 `warnings` 注明文档含修订。`DocxAnchor.node_path` 在阶段一只到段落或单元格一级，`run_range` 可以为空。

**账目单位 〔增补〕**：指导 §2.3 原则 1 所说的"已发现内容"落到这些条目：原生 PDF 的文本行、OOXML 的段落 / 表格 / 图片节点、检测区域、OCR 块。每个条目只能有一个去向，`disposition=None` 就是未归属。`check` 要求 `discovered = output + merged + duplicate + excluded + failed` 且没有未归属条目。以行而不是字符为单位，是为了让账目规模可控，同时仍能发现整行丢失。行内丢字交给评测的字符指标发现。

**持久化 〔增补〕**：`<ws>/state.json`（`DocumentState`）+ `<ws>/assets/` + `<ws>/calls.jsonl`（每次工具调用的请求、信封、diff 与证据引用，§7.5）+ 文件锁。〔P1-2〕另存 `<ws>/source.<ext>`（输入文件副本，工具不依赖原路径，实验目录只需工作区本身）；`Workspace.create` 算作第一次提交（version 1）；`txn(actor, expect_version=, timeout=)` 在锁内读取最新状态、提交前整体重新校验（列表修改绕过赋值校验）、失败不写；`calls.jsonl` 同时记录提交（`type=txn`，actor 与新版本号）与工具调用（`type=call`）；资源按内容寻址（`assets/<asset id>.<ext>`，重复写入幂等）。查询函数在 `workspace/queries.py`（阅读顺序、邻近块、按页 / 段取块、标题树）。先用 JSON 文件；几百页文档的 state.json 若超过约 50 MB，或写入明显拖慢速度，再换 SQLite，届时只改 `workspace/` 内部。

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

渲染规则（§4.5）：没有合并单元格输出 GFM，有则输出 HTML。〔P1-1〕GFM 只能表达一行表头，所以判据是 `needs_html = has_spans or header_rows > 1`，`to_gfm()` 在 `needs_html` 时抛 ValueError，避免丢掉表头结构；没有标记表头的网格按 GFM 要求以第一行作表头（与 v1 相同；补一行空表头会让整张表的行位置错位）。`to_html()` 表头行用 `<th>`，未被任何单元格覆盖的位置输出空 `<td>`。

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

StructureChange = Annotated[SetRole | SetLevel | MoveAfter | AddRelation | RemoveRelation | MarkPending, Field(discriminator="op")]

class ApplyStructureRequest(IRModel):
    changes: list[StructureChange]
    atomic: bool = False                  # True：任一条被拒则全部不生效

class LegalityRule(StrEnum):
    UNKNOWN_BLOCK="unknown_block"; KIND_NOT_STRUCTURAL="kind_not_structural"
    LEVEL_ON_NON_TITLE="level_on_non_title"; LEVEL_SKIP="level_skip"
    NUMBERING_LEVEL_INCONSISTENT="numbering_level_inconsistent"
    ORDER_CYCLE="order_cycle"; DUPLICATE_RELATION="duplicate_relation"

class Rejection(IRModel):
    index: int; rule: LegalityRule; detail: str

class ApplyStructureResult(IRModel):
    accepted: list[int]
    rejected: list[Rejection]
    outline_after: list[OutlineNode]
```

请求模型里根本没有文本字段，"结构变更永不改原文"因此由 schema 保证，不需要运行时检查。每条被接受的变更都会在对应块上写一条 Decision（heading_role / heading_level，actor = 调用方）。

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

`export` 在 `exportable=False` 时返回 `check_failed`，不写文件。预算耗尽导致的失败或跳过，只要已经记入账目，仍可导出，状态为 `partial`。这样区分了"已知的缺失"和"静默丢失"两种情况。

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
