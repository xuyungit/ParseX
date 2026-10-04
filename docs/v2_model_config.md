# 模型接入与配置文件（Q100，2026-09-27；决定一、决定二都已实施，2026-09-28）

用户基本同意方向，要求先写成文档、不实施。2026-09-28 为模型能力比较实施了决定一（§2，见 §6）；决定二（§3）以后做（Q101），§5 的待定项届时再定。

## 1. 起因与事实

很多服务商都提供与 OpenAI 兼容的接口，但每个模型需要的参数不完全一样。现在接入一个新模型，要同时改 `.env` 和 YAML 两个文件，而且接口差异要到请求失败时才会暴露出来。

### 1.1 实测的接口差异（2026-09-27）

用 `services/llm.py` 的 `OpenAICompatibleService` 对每组配置做了三项检查：列出模型、文本请求、图片请求。

| 模型 | 端点 | 接口 | 参数差异 |
|---|---|---|---|
| gpt-6-luna | sub2api 中转（`.env` 无后缀组）、官方（`_B`） | Responses | 拒绝 `temperature`；effort 接受 none/low/medium（§10.3） |
| deepseek-flash | api.deepseek.com（`_D`） | Responses 与 Chat 都能用，自己的循环用 Chat | 思考内容以 `reasoning_content` 返回，工具循环里要交回给它（4075fa6） |
| glm-5.3-flashx | open.bigmodel.cn（`_G`） | 只有 Chat（Responses 返回 404，服务自动回退） | effort 只接受 low/high/max，传 none、minimal、medium 都返回 400；不传 effort 时按接近 max 的强度思考；`top_p`、`thinking` 要经 `extra_body` 传 |

GLM 拒绝 effort 时的报错是中文："该模型始终思考，不支持关闭思考；请使用 low、high 或 max。"它不匹配 `_UNSUPPORTED_RE`（只认 OpenAI 的 `Unsupported parameter/value: '…'`），所以服务不会自动去掉这个参数。如果 GLM 用 `parserx.yaml` 给 gpt-6-luna 写的 `reasoning_effort: none`，每个请求都会失败。

另外，中转站原来写的地址是 `https://…:2086/`，TLS 握手失败。2086 通常是 Cloudflare 的明文 HTTP 端口；改用 `http://…:2086` 连得上，但返回的是空内容的 200。用户改成 `https://sub2api.wddgxs.com`（443）后三项都正常，末尾加不加 `/v1` 都可以。这类问题现在要到第一次处理文档时才会发现。

### 1.2 GLM 用推荐设置与我们的设置对比（2026-09-27）

用两张真实图片，每种设置各跑 2 次：
- **抄录**：`text_pic02` 的界面截图，核对 26 处文字。
- **描述**：`paper_chn02` 的力学示意图，用 `describe_figure` 提示词，把 schema 写在提示里。

| 设置 | 抄录 | 描述：耗时 / 输出 token（其中思考 token） |
|---|---|---|
| temperature 0，不传 effort | 26/26，约 4 s | 约 18 s / 约 2400（约 1500） |
| temperature 0，effort=low（项目描述任务的实际设置） | 26/26，约 3 s | 约 4 s / 约 530（0） |
| 官方推荐：temperature 1、top_p 0.95、effort=max、`thinking.enabled` | 26/26，约 4 s | 约 20 s / 约 2800（约 1850） |

- **准确度**：三种设置的抄录都全对。描述都没有实质错误，不传 effort 和推荐设置列得更细，low 粗一些。
- **成本**：推荐设置的描述任务耗时和 token 都约为 low 的 5 倍。
- **预算**：不传 effort 时约 2400–2900 个输出 token，超过 `describe_max_tokens` 的 2048。沿用服务级 effort 的表格复审（`review_reasoning_effort: None`）也会这样思考，容易被截断。

结论：官方推荐的是做难推理题的设置。服务层的任务应显式设 `low`（Q40：经济、速度优先）。

### 1.3 配置现状

- **一个模型的配置拆在两个文件里。** `parserx.yaml` 写 `endpoint: ${OPENAI_BASE_URL_B}`，地址、密钥和模型名在 `.env` 里，靠后缀（无后缀、`_B`、`_C`、`_D`、`_G`）区分。`configs/vlm_b.yaml`、`configs/vlm_c.yaml` 又各列了一遍。自己的循环（`runtime.agent`）还有一套 `endpoint`、`api_key`、`api`、`extra_body`。
- **配置文件二选一，不叠加。** 当前目录有 `parserx.yaml` 就只用它，否则用 `~/.config/parserx/config.yaml`，都没有就用代码里的默认值（`schema.py::load_config_with_result`）。`.env` 从当前目录和 `~/.config/parserx/.env` 读。
- **生产设置复制了两份。** 在仓库外运行时读不到仓库的 `parserx.yaml`，所以 Q67 让 `parserx init` 写出的全局配置（`parserx/config/template.yaml`）完整复制一份生产设置，并用测试保证与 `parserx.yaml` 一致。
- **密钥为什么放在 `.env`**：`parserx.yaml` 进 git，`.env` 被 git 忽略。这是两个文件并存唯一实际的理由。

## 2. 决定一：接口差异写在配置里，按模型写，不按服务商写

### 2.1 为什么不为每家服务商写一层封装

- **差异跟着模型走。** 同一个官方端点下，gpt-6-luna 拒绝 `temperature`，别的模型不拒。GLM-5.3-flash 的 effort 取值未必与 GLM 的其他版本相同。中转站上挂的是 OpenAI 的模型，按服务商分类说不清它属于哪家。按服务商写成类，里面迟早会出现按模型名分支的判断。
- **这些规则变得快。** 每个新模型都可能改变可接受的参数值。写在代码里，每次都要改代码。写在配置里，改一行就行。
- **1.1 里所有差异都在参数层面**：用哪套接口、接不接受某个参数、参数能取哪些值、要不要额外字段。没有一处非写代码不可。

### 2.2 代码只按协议分

- **现有的两种协议适配器**：服务层 `OpenAICompatibleService` 里的 Responses 与 Chat 两条路，以及自己的循环里的 `ResponsesModel` 与 `ChatModel`（Q88）。
- **以后什么时候加新适配器**：只在协议本身不同时加一个，例如 Anthropic Messages 或 Gemini 原生接口。
- **结构上的行为按响应内容通用处理**，不认服务商的名字。例如响应里有 `reasoning_content`，就在同一接口的工具循环里交回（4075fa6 的做法）。

### 2.3 模型条目

配置里新增 `models`：一个模型一个条目，写清怎样和这个模型对话。

```yaml
models:
  gpt-6-luna:
    endpoint: https://api.openai.com/v1
    model: gpt-6-luna
    api_style: responses
    send_temperature: false
    efforts: [none, low, medium]
  glm-flashx:
    endpoint: https://open.bigmodel.cn/api/paas/v4/
    model: glm-5.3-flashx
    api_style: chat
    efforts: [low, high, max]
  deepseek-flash:
    endpoint: https://api.deepseek.com
    model: deepseek-flash
    api_style: chat
```

- **可写的字段**：`endpoint`、`api_key`、`model`、`api_style`、`send_temperature`、`efforts`、`min_output_tokens`、`extra_body`、`user_agent`。除了新增的 `efforts`，都是 `ServiceConfig` 已有的字段。
- **用的地方按名字选，写在旁边的字段覆盖条目里的值。** 用的地方是 `services.vlm`、`services.llm` 和 `runtime.agent`（自己的循环）：

  ```yaml
  services:
    vlm: {use: glm-flashx, reasoning_effort: low, max_concurrent: 6, timeout: 180}
  runtime:
    agent: {engine: loop, use: deepseek-flash, effort: high}
  ```
- **加载时就把 `use` 展开**成现在的 `ServiceConfig` 与 `AgentConfig` 字段，服务和循环的代码只看到展开后的结果。`models` 段不算进配置指纹（加进 `_NOT_PROCESSING`），因为指纹只该反映实际用到的设置。展开后与现在相同的配置，指纹不变，冻结 run 照常回放。
- **价格**仍按模型名放在 `scheduling.prices`，不在这次改动之内。

### 2.4 思考强度的换算

各任务按用途写 effort：转录与复核 `none`，图片描述与看图回答 `low`（`tools.*_reasoning_effort`）。模型条目的 `efforts` 按从低到高列出这个模型接受的取值。发请求时：

- 请求的取值在列表里：原样发。
- 不在列表里：按 `none < minimal < low < medium < high < xhigh < max` 的顺序换成最近的一个；两边一样近时取低的（Q40：服务层经济优先）。例如 GLM 把 `none` 换成 `low`，把 `medium` 也换成 `low`。
- 条目没写 `efforts`：原样发，和现在一样。

这样换模型时不用逐个任务改 effort，代码里也不出现服务商的名字。

### 2.5 400 探测保留，只作兜底

碰到 OpenAI 格式的 "Unsupported parameter/value" 报错时，去掉或改名参数后重试，这个机制保留，只用来兜底。

- **不为认出各家的报错写专门代码。** 那样做等于从后门走回了按服务商封装。
- **以配置为准。** 被拒时把服务商的原话报出来，让人去改配置。

§10.3 原来写的是"不按模型名维护能力表，靠 400 探测"。这一条改为：模型的特性写在配置里，由用户维护；代码里仍然没有按模型名写的表。

### 2.6 接入新模型时先验证

`scripts/check_services.py` 加 `--model <名字>`：对一个模型条目做 1.1 那样的检查，包括列出模型（看条目里的 `model` 在不在）、文本请求、图片请求，以及 `efforts` 里的每个取值是否被接受。1.1 里的中转站地址错误和 GLM 的 effort 限制，都是这样查出来的。

## 3. 决定二：只用 YAML，按"是不是个人的"分层，不再用 `.env`

### 3.1 三层叠加，后面的覆盖前面的

| 层 | 位置 | 进 git | 内容 |
|---|---|---|---|
| 内置默认 | 包内 `parserx/config/defaults.yaml` | 进 | 生产设置（现在 `parserx.yaml` 的内容），加上已知模型的条目（不含密钥） |
| 项目 | 当前目录的 `parserx.yaml`（可选） | 由项目决定 | 这个项目自己要改的设置 |
| 个人 | `~/.config/parserx/config.yaml` | 不进 | 密钥、个人的端点（如中转站）、各角色用哪个模型 |

- **显式的 `--config` 文件优先级最高**，叠在个人层之上，它自己的 `extends` 链照旧展开。评测配置（`configs/regression*.yaml`）显式写 `use`，所以个人层对评测只提供地址和密钥，不会悄悄换掉评测用的模型。
- **生产设置放进包里，作为内置默认层。** 这样在仓库外运行也能用到，Q67 为此复制的 `template.yaml` 和"两份一致"的测试就可以删掉。仓库根目录的 `parserx.yaml` 只在需要项目级覆盖时保留。

个人文件大致是这样：

```yaml
models:
  gpt-6-luna: {endpoint: https://sub2api.wddgxs.com, api_key: ...}   # 走中转站
  glm-flashx: {api_key: ...}
  deepseek-flash: {api_key: ...}
builders:
  ocr: {token: ...}
services:
  vlm: {use: gpt-6-luna}
```

- **已知模型只补密钥或个人端点。** 内置层里有的模型，个人文件里只写密钥（需要时加上端点），条目按名字深度合并。官方端点和中转站都要用时，另起一个条目。
- **新模型整段写在个人文件里。** 值得共享时，再把它的特性挪进内置层。

### 3.2 密钥

- **不再读 `.env` 文件。** `${VAR}` 写法保留，在服务器或 CI 上仍可以写 `api_key: ${SOME_KEY}`，从真实的环境变量取值。
- **个人文件在 `~/.config/parserx/`。** 这个目录本来就在配置的查找顺序里，Agent 审计也已把它列为禁区（`codex_agent` 的 `forbidden`）。
- **Q65 ④ 不受影响。** Agent 的配置由生效配置导出，按字段名把凭据换成 `${PARSERX_SECRET_n}`，与密钥来自哪个文件无关。Codex 的环境照旧去掉像密钥的变量名和值等于密钥的变量；专门读 `.env` 变量名的 `_dotenv_names()` 删去。
- **缓存键含端点身份（主机 + 路径），这一点不变。** 把评测用的端点改到别的地址（哪怕是同一个模型），回放缓存就对不上，要重新录制。

## 4. 实施时的改动（待确认后做）

1. **`parserx/config/schema.py`**
   - 新增 `ModelProfile`、`ParserXConfig.models`，`ServiceConfig` 与 `AgentConfig` 加 `use`，加载时展开 `use`。
   - 加载改为三层叠加加显式文件。
   - 删去读 `.env`。
   - `models` 进 `_NOT_PROCESSING`。
2. **`parserx/services/llm.py`**：按 `efforts` 换算思考强度（2.4）。
3. **`parserx/runtimes/loop.py`、`hybrid.py`**：循环经 `use` 取模型；删去 `_dotenv_names()`。
4. **包内 `defaults.yaml`**：接替 `parserx.yaml` 的生产设置和 `template.yaml`。`parserx init` 只写个人文件的骨架（已知模型各留一个空的 `api_key`，加上 `use`）。`.env.example` 换成个人文件的示例。
5. **配置文件**
   - `configs/vlm_b.yaml`、`regression*.yaml` 改用 `use`。
   - `configs/vlm_c.yaml` 删去（`_C` 组按用户要求不再使用）。
   - `tool_eval/adapters.py` 的 `LLAMA_CLOUD_API_KEY` 改为从配置读。
6. **`scripts/check_services.py --model`**（2.6）。
7. **测试与验收**
   - L0 覆盖叠加顺序、`use` 展开与覆盖、effort 换算、密钥不进 Agent 配置。
   - L1 PASS。
   - 两个冻结 run 回放 PASS：生效配置不变，指纹也不变。

## 5. 待定

1. **个人文件放哪里。** 建议放 `~/.config/parserx/config.yaml`：在任何目录运行都能读到，也已在 Agent 审计的禁区里。另一个选择是项目目录下一个被 git 忽略的 `parserx.local.yaml`。
2. **仓库根目录的 `parserx.yaml` 还要不要。** 建议删去，生产设置全在包内默认层。开发实验用 `--config`。
3. **effort 两边一样近时取哪边。** 建议取低的（2.4）。

## 6. 决定一的实施（2026-09-28，模型能力比较的 M1）

与 §2 的差别：
- **结构化输出**：条目另有 `structured_output`（json_schema / json_object / off），服务的结构化输出从这一级开始往下退（Q105）。
- **Agent 的思考强度**：两边一样近时取高的，服务层取低的（Q103）。
- **不发思考强度**：`efforts: []` 表示这个模型不接受思考强度参数，一律不发。
- **缓存键**：记实际发送的思考强度；只在条目限定了结构化输出时，才把它记入缓存键。没有限定的配置，缓存键不变。
- **Agent 的工具配置**：导出时去掉 `models` 和 `use`（使用处已经展开），别的模型的密钥不进 Agent 一侧。
- **密钥**：仍从 `.env` 引用（决定二以后做）。
- **`parserx.yaml`**：写入三个条目，另补 GLM 的价格（Q102）。
- **gpt-6-luna 的条目**：不列 `efforts` 和 `structured_output`。它接受任务发出的所有取值，所以请求、缓存键和配置指纹都与原来相同。

### 6.1 探测结果（`scripts/check_services.py --model`，2026-09-28）

| | gpt-6-luna（官方） | deepseek-flash | glm-5.3-flashx |
|---|---|---|---|
| 列在端点的模型里 | 是 | 是 | 是 |
| 文字、图片 | 可以 | 可以 | 可以 |
| temperature | 拒绝 | 接受 | 接受 |
| 思考强度 | none、low、medium、high、xhigh、max（只拒 minimal） | 七个都接受 | low、high、max |
| json_schema | 遵守 | 拒绝（"unavailable now"） | **接受但不遵守**：返回带 \`\`\`json 围栏的文字，不报错 |
| json_object | 遵守 | 遵守 | 遵守 |
| 返回 `reasoning_content` | 否（Responses 的推理是加密项） | 是 | 是 |

- **条目已按此写好**，三个都通过核对。
- **luna 的强度**：§10.3 原来写 luna 只接受 none、low、medium，已经过时。
- **GLM 的 json_schema**：它接受却不遵守，也不报错，服务的退级机制等不到报错，只能在条目里写明。

**配置指纹因此改为按实际发送来算**：
- 思考强度记换算后的取值，不记条目列出的范围；结构化输出没写时按 json_schema 记。
- 所以给 luna 补写条目，不改变任何请求，指纹也不变。
- 缓存键同理：只有条目把结构化输出限到 json_schema 以下时，才把它记进去。

## 7. 决定二的实施（2026-09-28，独立发布 R1，Q107–Q109）

按 §3 实施。
- **四层**：包内默认 `parserx/config/defaults.yaml` → `./parserx.yaml` → 个人 `~/.config/parserx/config.yaml`（`PARSERX_CONFIG_DIR` 可改位置）→ `--config`，后面的深度合并到前面的上。
- **`.env`**：不再读取，`${VAR}` 照旧读环境变量。
- **删去的文件**：仓库根目录的 `parserx.yaml`（Q108）、`template.yaml` 及"两份一致"的测试。
- **评测配置**（`configs/regression.yaml`）：写明 `use` 与缓存目录，个人配置只提供 key。
- **`parserx init`**：写个人配置骨架（0600）；旧 v1 配置另存为 `config.yaml.v1.bak`；旧 `.env` 的值一次性搬入。
- **本机**：仓库 `.env` 的各组已改写成个人配置里的模型条目（Q109）：`_B` 给 gpt-6-luna 与 gpt-6-sol，无后缀的中转站另起条目 `gpt-6-luna-relay`，`_D`、`_G` 各给自己的条目，`_C` 不用。
- **Agent 的 px**：不读个人配置。
- **结果**：两个冻结 run 的指纹与回放不变。

## 8. 接入 qwen3.8-flash（2026-10-02，Q145）

用户要把阿里云百炼的 qwen3.8-flash 接成与 gpt-6-luna、deepseek-flash、glm-5.3-flashx 并列的工作模型，并先定接入方式：OpenAI 兼容，还是 DashScope 自有接口。依据是官方的两篇文档（[OpenAI 兼容 Chat](https://docs.bailian.console.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions)、[DashScope 原生](https://docs.bailian.console.aliyun.com/zh/model-studio/qwen-api-via-dashscope)）和 [qwen3.8-flash 的模型页](https://docs.bailian.console.aliyun.com/zh/model-studio/qwen3-8-flash)。

### 8.1 接入方式：OpenAI 兼容的 Chat Completions

| | OpenAI 兼容（compatible-mode） | DashScope 原生 |
|---|---|---|
| 地址 | `…/compatible-mode/v1`，一个端点 | `…/api/v1/services/aigc/text-generation/generation` 与 `…/multimodal-generation/generation`，文字和图片是两个端点 |
| 请求体 | `messages`，图片是 `image_url` 的 Base64 Data URL——与我们现在发的一样 | `input.messages`，图片写 `{"image": …}`，参数放 `parameters` |
| 思考 | `reasoning_effort` 是标准参数；`enable_thinking`、`thinking_budget`、`preserve_thinking`、`vl_high_resolution_images` 经 `extra_body` | 同名参数放 `parameters` |
| 只有它有的 | — | 显式缓存 `cache_control`、`fileid://` 引用已上传文件、联网搜索策略、视频 `max_frames` |
| 我们的代码 | 现有的 Chat / Responses 两条路直接可用 | 要新写一个协议适配器 |

**结论：走 OpenAI 兼容。** 原生接口多出的能力我们一样都不用；按 §2.2，只在协议本身不同、而且需要时才加适配器。兼容模式下 Chat 与 Responses 两条路都通（8.3），条目写 `api_style: chat`：思考内容只在 Chat 以 `reasoning_content` 返回，而 qwen3.8-flash 的 `preserve_thinking` 默认开启，官方要求多轮里把历史的 `reasoning_content` 完整交回——循环对 DeepSeek 已经这样做（`runtimes/models.py`），同一机制直接适用。

### 8.2 地址与 key 怎么配

- **地址**：`https://dashscope.aliyuncs.com/compatible-mode/v1`（北京地域的通用域名；官方说"现有域名仍可正常使用"，本机 key 在它上面能列出 261 个模型，含 qwen3.8-flash）。官方建议迁到业务空间专属域名 `https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1`，WorkspaceId 在百炼控制台"业务空间详情"页看；要用时在个人配置的条目下写 `endpoint:` 覆盖即可。新加坡地域的 key 对应 `https://dashscope-intl.aliyuncs.com/compatible-mode/v1`（或 `{WorkspaceId}.ap-southeast-1.maas.aliyuncs.com`）。旧 `.env` `_C` 组写的 `…/api/v2/apps/protocols/compatible-mode/v1` 现在返回 404，不再用。
- **key**：按决定二，只写在个人配置 `~/.config/parserx/config.yaml`（条目的端点和参数都在内置默认里）：

  ```yaml
  models:
    qwen3.8-flash:
      api_key: sk-...
      # endpoint: https://<WorkspaceId>.cn-beijing.maas.aliyuncs.com/compatible-mode/v1   # 可选：业务空间专属域名
  ```

- **验证与使用**：`uv run parserx check --model qwen3.8-flash`；`parserx parse x.pdf --vlm qwen3.8-flash`（服务模型）、`--agent qwen3.8-flash`（自己的循环）；评测用 `configs/vlm_qwen.yaml`；读图摸底 `scripts/model_probe.py --model qwen3.8-flash --effort low`。

### 8.3 模型特性与探测（2026-10-02）

官方文档：
- 多模态：输入图片、文字、视频，输出文字；上下文 100 万，最大输出 131 072，思维链最长 262 144。
- 思考强度：原生只有 low / medium / xhigh（默认 xhigh）；`max`、`high` 映射为 xhigh，`minimal` 映射为 low，`none` 映射为关闭思考；其他值报错。七个都能发，条目不列 `efforts`。**两者都不发时按 xhigh、预算 131 072 思考**——我们的任务都发强度（服务默认 `none`，描述与看图 `low`），循环也发，但条目里写明了。`reasoning_effort` 与 `thinking_budget` 不能同时设。
- `max_tokens` 只算回答、不含思维链（与 DeepSeek 不同），所以不需要 `min_output_tokens`。
- 图片像素上限默认 2 621 440（约 1620²），200 dpi 的整页会被缩小；`extra_body: {vl_high_resolution_images: true}` 提到 16 777 216，输入 token 随之增加。先不开，读图摸底时再定。
- 价格（北京，标价）：输入 0.8 元、缓存命中 0.1 元、输出 2.7 元 / 百万 token；按 6.75 折成 0.12 / 0.015 / 0.40 美元写进 `scheduling.prices`。与 luna（0.10 / 0.01 / 0.50）同一量级，比 GLM（0.30 / 0.084 / 1.04）便宜。

探测（`check --model`，Responses 路）：

| | qwen3.8-flash |
|---|---|
| 列在端点的模型里 | 是（261 个） |
| 文字、图片 | 可以（读出样图的 12345） |
| temperature | 接受 |
| 思考强度 | none、minimal、low、medium、high、xhigh、max 七个都接受 |
| json_schema / json_object | 都遵守 |
| 返回 `reasoning_content` | Responses 路不返回；Chat 路按文档返回 |

Chat 路（条目写的那条）的探测还没跑：那次命令从 `.env` 里取 key，被权限检查拦下；key 写进个人配置后用 8.2 的命令跑一遍，条目与模型应一致。

### 8.4 改动

`parserx/config/defaults.yaml` 条目与价格；`parserx/config/personal.yaml` 骨架留空 key；`configs/vlm_qwen.yaml`；`scripts/model_probe.py` 的帮助文字；README 的个人配置示例。代码没改：现有的 Chat 适配器、effort 换算、结构化输出退级都直接适用。
