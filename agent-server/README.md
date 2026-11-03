# LangGraph Agent Server

目录已由 `agent-demo` 改为 `agent-server`。生产化重构计划见 [架构方案](../docs/agent-production-plan.md)。

兼容说明：Keycloak 的 OAuth client ID 继续使用已注册的 `agent-demo`；Compose 项目名也固定为 `agent-demo`，以复用已有 PostgreSQL 数据卷。这两个标识独立于目录和 Python 项目名称。

模型用 DeepSeek。循环交给 LangGraph：`create_agent` 编出 model 节点和 tools 节点，`add_note` 在执行前打断等确认。

```text
User
  ↓
LangGraph
  model（DeepSeek）
  ↓
有 tool_calls？
  /          \
是            否
  ↓            ↓
tools 节点    最终回答
  ↓
add_note 先 interrupt，接受后再执行
  ↓
回到 model
```

记忆分成三段，长期提取通过持久后台任务执行：

```text
短期  同一条 thread 的 checkpoint 写在 PostgreSQL
      消息过长时摘要压缩，摘要写在 state.summary
长期  一轮结束后先写 PostgreSQL 任务，后台 Extractor 抽候选
      Policy 打分并拒绝密钥
      Resolver 按主题去重或更新
      Store 按用户分开保存，PostgreSQL + pgvector 检索
上下文  每次调模型前，Context Builder 只注入
        和当前问题相近、并且放得进 token 预算的记忆
```

会话和长期记忆都在 PostgreSQL 里，进程关掉还在。先把数据库拉起来：

```powershell
cd "D:\work\AI Study\agent-server"
docker compose up -d
```

默认连 `postgresql://study:study@127.0.0.1:5432/study`。换地址时在 `.env` 里写 `MEMORY_DATABASE_URL`。

长期记忆的向量用 LangChain Embeddings，默认本机模型 `huggingface:BAAI/bge-small-zh-v1.5`。DeepSeek 只负责对话。启动会登记模型标识和维度；已有索引与模型不匹配时停止初始化，不会自动删表或重建。同维度更换模型也需要显式迁移。第一次升级旧数据库时以当前配置登记基线，请确保仍使用生成原索引的模型。可用 `EMBEDDING_REVISION` 固定 Hugging Face revision。

## 1. 准备

依赖由 [uv](https://docs.astral.sh/uv/) 管理：`pyproject.toml` 声明依赖，`uv.lock` 锁定版本。

```powershell
cd "D:\work\AI Study\agent-server"
uv sync
```

在 `.env` 里填入 [DeepSeek](https://platform.deepseek.com/api_keys) 的 key：

```env
DEEPSEEK_API_KEY=sk-...
DEEPSEEK_MODEL=deepseek-chat
DEEPSEEK_BASE_URL=https://api.deepseek.com
```

`deepseek-chat` 支持这个 demo 里的工具调用。换成 `deepseek-reasoner` 会走思考模式，工具调用不如 chat 稳定。

## 2. 运行

单次提问（容易触发工具）：

```powershell
uv run python main.py "现在几点了？北京天气怎么样？再帮我算 23*17"
```

多轮聊天：

```powershell
uv run python main.py
```

把限制调得很小，观察循环怎么停：

```powershell
uv run python main.py --max-iterations 1 "北京天气和 12*8"
```

## 3. 建议阅读顺序

1. `agent/tools.py` — 工具定义，转成 LangChain tool
2. `agent/limits.py` — 六个安全阀
3. `agent/graph.py` — `create_agent`、确认、限额
4. `agent/memory/` — 短期摘要、长期提取 / 审核 / 去重、上下文组装
5. `agent/loop.py` — 把一轮用户输入送进图，并把流变成事件
6. `main.py` — 命令行入口
7. `agent/mcp_client.py` — 用官方 MCP Python SDK，以 Streamable HTTP 连接旁边的 `mcp-server`

## 4. MCP

`../mcp-server` 是一个学习笔记 MCP server，包含 tool、resource、prompt。它自己作为 Streamable HTTP 服务运行，Agent 只负责连接，不会拉起子进程。Agent 循环保持同步：MCP SDK 是异步的，调用放在 `mcp_client.py` 的后台事件循环里。

笔记服务要求 Keycloak 登录。先按仓库根目录的说明启动 Keycloak，再用 alice / alice 或 bob / bob 登录。笔记按用户分开，存在 mcp-server 进程的内存里，关掉就回到该用户的三条种子笔记。

先启动 server：

```powershell
cd "D:\work\AI Study\mcp-server"
uv run python -m mcp_server
```

默认地址是 `http://127.0.0.1:8000/mcp`。换地址时在 `agent-server/.env` 里加 `MCP_URL=http://127.0.0.1:8000/mcp`。

然后再开一个终端跑 agent：

- MCP tool（`add_note`、`search_notes`）直接变成模型可调用的函数
- resource 由客户端读取，模型通过 `read_mcp_resource` 发起
- prompt 由客户端取模板，模型通过 `get_mcp_prompt` 发起，再按模板继续

```powershell
cd "D:\work\AI Study\agent-server"
uv run python main.py "记一条笔记，标题是 Loop，正文是 Agent 会反复调用工具。然后读取 notes://catalog，告诉我有哪些标题"
```

## 5. 六个限制分别卡什么

| 限制             | 单位         | 含义                                         |
| ---------------- | ------------ | -------------------------------------------- |
| `max_turns`      | 用户消息     | 用户说一句、Agent 给出最终回答算 1 轮        |
| `max_iterations` | LLM 调用     | 一轮里可能多次调模型（工具结果还要再问一遍） |
| `max_tool_calls` | 工具执行次数 | 模型想调多少次工具，程序最多执行多少次       |
| `timeout`        | 秒           | 从 Agent 启动开始的墙钟时间                  |
| `token_budget`   | token        | `prompt_tokens + completion_tokens` 累计     |
| `cost_budget`    | 美元         | 用一张近似价格表估算，只为演示               |

`graph.py` 的 `BudgetMiddleware` 在每次调模型前、执行工具前问 `LimitTracker`：还能继续吗？不能就带上 `stop_reason` 退出。

## 6. 网页聊天

`../front` 是 React 页面，用 `@ag-ui/client` 的 `HttpAgent` 和 Agent 通信。协议是 AG-UI：浏览器 `POST /agent`，响应是 `text/event-stream`。FastAPI 只做这层传输，对话由图来跑。同一条 `thread_id` 的历史写在 PostgreSQL 的 checkpoint 表里，服务重启后还能接着这条会话。

三个终端，先 MCP，再 Agent 接口，再页面：

```powershell
cd "D:\work\AI Study\mcp-server"
uv run python -m mcp_server
```

```powershell
cd "D:\work\AI Study\agent-server"
uv run python -m agent.server
```

```powershell
cd "D:\work\AI Study\front"
pnpm install
pnpm dev
```

Agent 接口默认 `http://127.0.0.1:8010/agent`（`AGENT_PORT` 可改）。页面开发服务器把 `/agent` 代理过去，浏览器打开 `http://127.0.0.1:5173`。一次网页请求使用一份新的限额，不和命令行共用计数。

### 会话列表与历史恢复

页面左侧可以新建、切换会话，标题取第一条用户消息。浏览器只在 localStorage 保存当前会话 ID；消息历史保存在同一个 PostgreSQL 的 `chat_conversations` 表中，服务启动时自动建表。刷新后会恢复上次打开的会话，包括回答、思考文本、工具参数和结果、天气卡片以及 `add_note` 待审批状态。历史独立于 checkpoint，摘要压缩不会删除页面消息。

- `GET /agent/conversations`：按最近更新时间列出会话。
- `POST /agent/conversations`：新建会话。
- `GET /agent/conversations/{thread_id}`：读取显示历史、AG-UI 消息和待审批状态。
- `DELETE /agent/conversations/{thread_id}`：删除当前用户的会话历史及 checkpoint；执行中返回 409，不存在或不属于当前用户时返回 404。

会话列表中的“删除”会要求确认。删除当前会话后切换到最近的其他会话；删除最后一个会话后自动新建空会话。历史和 checkpoint 在同一个数据库事务中清理。已创建的 MCP 笔记和用户级长期记忆不随会话删除。

生成时每隔约 0.5 秒（有新事件时）批量保存，结束时立即保存。刷新后若后台仍在运行，页面每 2 秒同步历史，暂时禁用发送，但仍可取消该次运行。

### 取消与继续

- 点击“取消”会请求服务端停止当前运行，页面显示“取消中…”，直到后端到达安全停止点。关闭页面只断开事件流，不会隐式取消后台任务。
- `POST /agent/conversations/{thread_id}/cancel`，请求体为 `{"runId":"当前运行 ID"}`。重复取消同一运行不会启动新任务；过期的运行 ID 返回 409，避免取消后续运行。
- 取消后保留已收到的内容，显示“继续回答”。继续使用原会话、新的 `runId`，通过 `POST /agent` 的 `forwardedProps: {"action":"continue","resumeOf":"被取消的运行 ID"}` 发起，不添加新的用户消息。
- 回答和生成请求分开记录：助手回答沿用同一个 `messageId`，每次生成使用新的 `runId`（generation ID）。回答记录 `status` 和 `activeGenerationId`。续写读取服务端已保存的回答前缀，带上下文重新请求模型，只把新生成的后缀追加到原回答；临时续写指令不会写成用户消息。完整回答回写 LangGraph 检查点，之后提问能看到完整内容。
- 没有部分文字时仍使用 LangGraph 同步持久化检查点恢复工具和审批流程。已完成且保存的工具任务不会再次提交；正在执行的工具允许先完成，MCP 等待中的确认则发送取消。文字续写使用未绑定工具的模型，避免重新执行已有工具。
- 取消后也可输入并发送新问题：保留旧回答前缀作为上下文，创建新的用户消息和助手回答，不会执行旧任务中尚未完成的工具调用。即使新问题文字相同，也按不同消息 ID 区分。
- 前后端按当前运行 ID 隔离事件，迟到的旧生成事件不能更新新一轮状态。流式开始事件重用回答 ID 时保留已有内容，与 AG-UI 客户端的追加行为保持一致。
- 续写不是恢复模型内部 KV cache，也不是断线重放：不保证和未取消时的文字完全一致，模型仍可能重复前缀或产生衔接差异。该版本不提供按事件序号重连与补发。
- 取消与继续的状态保存在 PostgreSQL，刷新后仍能继续；服务重启将遗留的运行状态标记为 `interrupted`，同样可从检查点继续。恢复到审批节点时仍需明确接受或拒绝。
- 同一会话不能同时执行两次，请求的 `runId` 不可复用。预算、取消信号和流式状态已通过 RunContext 隔离。当前 MCP 仍使用进程级登录与确认通道，因此入场控制仍限制全服务同时执行一轮，其他运行请求返回 409；取消请求和历史读取不受此限制。运行注册表按单进程实现，部署保持单 worker。

检查点不能对外部副作用提供跨崩溃的“恰好一次”保证：如果进程在外部写入完成、检查点落库前崩溃，生产环境仍需工具端按稳定业务幂等键去重。

Agent 服务启动时只等待会话历史数据库就绪，模型初始化和 MCP/Keycloak 登录分别在后台启动。向量模型下载缓慢、SSL 重试、模型初始化失败或 MCP 尚未登录都不阻塞历史接口。模型尚未就绪时，生成接口返回 503；模型就绪后本地工具可用，MCP 工具在登录成功后热加载。`GET /health` 可查看 Agent 初始化状态和 MCP 状态；`GET /agent/mcp/status` 返回 `connecting`、`connected`、`unavailable` 或 `disabled`。

如果日志出现 Hugging Face `SSL: UNEXPECTED_EOF_WHILE_READING`，表示下载/检查向量模型时 TLS 连接被中断，请检查网络、代理和证书配置。模型已完整缓存时，可在启动前设置 `HF_HUB_OFFLINE=1` 避免联网检查；首次下载或缓存不完整时仍需要先通过可用网络下载模型。不要通过关闭 TLS 校验解决。

会话历史使用稳定的应用级身份 `AGENT_HISTORY_OWNER`（默认 `local`），不会跟随 MCP 登录状态变化；已登录的 MCP 用户身份仍用于隔离长期记忆和笔记工具。此 demo 没有独立的浏览器账号认证，所以同一部署的浏览器共享会话历史。生产多用户部署应在历史 API 前接入应用认证，并从已验证的登录主体生成 owner。新增功能前已存在的、按 MCP 用户名保存的历史不会自动迁移；新历史从 `local` 命名空间开始记录。

验证（需要先启动 demo PostgreSQL，不调用模型）：

```powershell
uv run python -m unittest discover -s tests -v
```

实现参考：

- [AI SDK：Chatbot Message Persistence](https://ai-sdk.dev/docs/ai-sdk-ui/chatbot-message-persistence)：持久化对话、稳定消息 ID、服务端处理断开后的生成。
- [LangGraph：Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)：线程检查点、故障恢复及 pending writes。

以上是组合实现的设计参考，不依赖 AI SDK，也不代表这些框架提供跨供应商的精确 token 续写。


## 生产化重构：当前已实现

- `application/runs.py`：认领、执行和释放运行；`runtime/context.py`：每次运行独立的预算、取消和事件出口。
- HTTP 标准图事件由锁定的 `ag-ui-langgraph==0.0.47` 转换；`adapters/agui/` 保留业务卡片、MCP 确认和文本续写扩展。设置 `AGUI_ADAPTER=legacy` 可临时回退同步转换器。
- 后台摘要调用带内部标记，不会作为助手回答发给浏览器；摘要和主回答都计入当前运行预算。
- 长期记忆存入 `agent_memory_jobs` 后即可完成回答。后台任务使用独立 30 秒、8000 token 估算上限和费用预算，用量存入任务记录；临时错误最多尝试三次，认证、参数或预算错误立即终止，进程重启后过期租约可重新认领。
- `agent_run_events` 保存每次运行的事件，与会话展示投影同事务提交。当前采用逐事件提交来保证发送前持久化；高吞吐场景应在此边界批量合并 delta，而非取消持久化保证。
- SSE 带 `id: runId:seq`。`GET /agent/conversations/{thread_id}/runs/{run_id}/events?after=序号` 可补发并跟随运行，也支持 `Last-Event-ID`。这是供客户端接入的重放接口，现有页面断线恢复仍通过会话快照轮询。
- 慢客户端使用有界队列，超限断开订阅，执行继续保存；原始图 state 和内部摘要不对浏览器公开。
- 应用表使用 `agent/persistence/migrations/*.sql` 版本化增量迁移。启动时自动执行幂等迁移；部署时也可提前执行 `uv run python -m agent.persistence.migrate`。

### 验证

先启动本目录的 PostgreSQL，再执行：

```powershell
uv sync --locked
uv run ruff check agent tests
uv run python -m unittest discover -s tests -v
```

测试使用确定性模型、HTTP MockTransport 和独立测试会话；持久任务测试另建隔离 schema，避免被开发服务的 worker 领取。CI 使用单独的 PostgreSQL 容器验证后端，另行构建前端。

### 目前的部署边界

保持单 HTTP worker 和单应用身份。运行级数据库租约、浏览器 JWT 认证、按用户委托的 MCP 会话、持久化 MCP RPC 审批恢复及远端写工具幂等仍须继续实现，不能直接当作多租户服务部署。

记忆任务属于最终一致性，新一轮对话不保证立即检索到上一轮刚提交的记忆。取消本地 MCP 协程也不保证撤回远端已完成的写操作。详细阶段与后续验收见 [生产化方案](../docs/agent-production-plan.md)。

## 调用失败与重试

统一错误码、HTTP/SSE/工具结果结构、历史字段和后台任务错误详情见 [结构化错误协议](../docs/error-contract.md)。

统一策略位于 `agent/runtime/retry.py`。配置示例见 `.env.example`；修改实际 `.env` 后重启服务生效。

- 模型主回答、摘要和续写：默认总共最多 3 次尝试。只重试连接错误、网络超时及 408/429/500/502/503/504；参数、认证、权限和证书错误不重试。思考模式不兼容时可回退普通模式，也占用这 3 次额度。
- SDK 初始化及每次调用的真实同步/异步客户端均关闭内部重试，避免 SDK 与应用层重试相乘。每次尝试重新设置 SDK 客户端超时；HTTP 异步模型链路同时受本次尝试的墙钟期限限制。
- 模型一旦产生任何流式 chunk（包括思考和工具参数），失败后不会自动从头重试；保留已有内容并终止该次运行。用户主动点击“继续生成”属于独立操作。
- 明确只读的本地工具、MCP 工具、resource/prompt 读取：临时故障最多尝试 3 次；MCP 的重试由连接封装负责，工具适配层不会额外再重试。
- MCP 工具只在 `read_only_hint=True` 时视为可安全重试；未知注解默认按写操作处理。内置笔记服务已为搜索、新增和删除提供注解。业务错误结果（`is_error=True` 或错误文本）不根据内容猜测为临时故障。
- 写操作不做网络失败自动重放。发生超时/连接失败时返回“结果未知”，并阻止同一运行里再次提交相同工具及参数。该保护不是跨运行幂等，用户仍须先核对远端结果。
- MCP 协议为收集用户确认而继续同一工具调用的交互流程保持不变，它不属于故障重试。
- MCP 启动连接对临时网络故障最多尝试 3 次；授权失败不反复打开登录。运行中永久断开的会话目前不自动重建，需要恢复 MCP 后重启 Agent。
- 长期记忆重试只由持久任务队列负责，每次任务执行的模型调用只尝试一次，避免“3 次任务 × 3 次模型重试”。任务使用指数退避与抖动，最多执行 3 次；认证、参数、预算等确定性错误立即标记失败。

重试不会重置运行总超时、模型次数、工具次数或 token/费用预算。默认基础退避 0.5 秒、普通退避上限 5 秒；服务器给出的 Retry-After 优先，若超出剩余运行期限则直接结束，不提前重试。重试日志只记录操作名、次数、等待时长和错误类型，不记录参数或密钥；运行用量快照增加 `retries` 计数。

模型默认每次尝试最多 60 秒，只读工具的网络尝试默认最多 15 秒，均受当前运行剩余时间约束。写 MCP 调用保留等待用户确认所需的剩余运行时间。普通同步 Python 工具无法安全强制终止，新增工具必须自行将超时传给底层 I/O；控制层不会用遗留后台线程来假装取消执行。
