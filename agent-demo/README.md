# LangGraph Agent Demo

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

记忆分成三段，串在同一次调用里：

```text
短期  同一条 thread 的 checkpoint 写在 PostgreSQL
      消息过长时摘要压缩，摘要写在 state.summary
长期  一轮结束后 Extractor 抽候选
      Policy 打分并拒绝密钥
      Resolver 按主题去重或更新
      Store 按用户分开保存，PostgreSQL + pgvector 检索
上下文  每次调模型前，Context Builder 只注入
        和当前问题相近、并且放得进 token 预算的记忆
```

会话和长期记忆都在 PostgreSQL 里，进程关掉还在。先把数据库拉起来：

```powershell
cd "D:\work\AI Study\agent-demo"
docker compose up -d
```

默认连 `postgresql://study:study@127.0.0.1:5432/study`。换地址时在 `.env` 里写 `MEMORY_DATABASE_URL`。

长期记忆的向量用 LangChain Embeddings，默认本机模型 `huggingface:BAAI/bge-small-zh-v1.5`。DeepSeek 只负责对话。换成别的嵌入模型时改 `EMBEDDING_MODEL`，维度变了会重建 `store_vectors` 并重新嵌入已有记忆。

## 1. 准备

依赖由 [uv](https://docs.astral.sh/uv/) 管理：`pyproject.toml` 声明依赖，`uv.lock` 锁定版本。

```powershell
cd "D:\work\AI Study\agent-demo"
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

默认地址是 `http://127.0.0.1:8000/mcp`。换地址时在 `agent-demo/.env` 里加 `MCP_URL=http://127.0.0.1:8000/mcp`。

然后再开一个终端跑 agent：

- MCP tool（`add_note`、`search_notes`）直接变成模型可调用的函数
- resource 由客户端读取，模型通过 `read_mcp_resource` 发起
- prompt 由客户端取模板，模型通过 `get_mcp_prompt` 发起，再按模板继续

```powershell
cd "D:\work\AI Study\agent-demo"
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
cd "D:\work\AI Study\agent-demo"
uv run python -m agent.server
```

```powershell
cd "D:\work\AI Study\front"
pnpm install
pnpm dev
```

Agent 接口默认 `http://127.0.0.1:8010/agent`（`AGENT_PORT` 可改）。页面开发服务器把 `/agent` 代理过去，浏览器打开 `http://127.0.0.1:5173`。一次网页请求使用一份新的限额，不和命令行共用计数。
