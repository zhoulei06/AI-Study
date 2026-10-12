# 手写 OpenAI Agent Loop Demo

对照学习路线 Phase 3 / Phase 5：自己写循环，不引入 LangGraph / Agents SDK。

```text
User
  ↓
LLM（DeepSeek，走 OpenAI 兼容的 Chat Completions + tools）
  ↓
有 tool_calls？
  /          \
是            否
  ↓            ↓
执行工具      最终回答
  ↓
把 role=tool 的结果追加进 messages
  ↓
再调 LLM
```

## 1. 准备

依赖由 [uv](https://docs.astral.sh/uv/) 管理：`pyproject.toml` 声明依赖，`uv.lock` 锁定版本。

```powershell
cd "D:\work\AI Study\agent-demo"
uv sync
```

在 `.env` 里填入 [DeepSeek](https://platform.deepseek.com/api_keys) 的 key。变量名仍叫 `OPENAI_*`，因为客户端用的是 OpenAI SDK，只是把请求发到 DeepSeek：

```env
OPENAI_API_KEY=sk-...
OPENAI_MODEL=deepseek-chat
OPENAI_BASE_URL=https://api.deepseek.com
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

1. `agent/tools.py` — 工具 schema + 本地执行
2. `agent/limits.py` — 六个安全阀
3. `agent/loop.py` — 真正的 `while True`
4. `main.py` — 怎么把一轮用户输入丢进 loop
5. `agent/mcp_client.py` — 用官方 MCP Python SDK 连接旁边的 `mcp-server`

## 4. MCP

`../mcp-server` 是一个学习笔记 MCP server，包含 tool、resource、prompt。Agent 启动时用 stdio 拉起它：

- MCP tool（`add_note`、`search_notes`）直接变成模型可调用的函数
- resource 由客户端读取，模型通过 `read_mcp_resource` 发起
- prompt 由客户端取模板，模型通过 `get_mcp_prompt` 发起，再按模板继续

```powershell
uv run python main.py "记一条笔记，标题是 Loop，正文是 Agent 会反复调用工具。然后读取 notes://catalog，告诉我有哪些标题"
```

## 5. 六个限制分别卡什么

| 限制 | 单位 | 含义 |
|---|---|---|
| `max_turns` | 用户消息 | 用户说一句、Agent 给出最终回答算 1 轮 |
| `max_iterations` | LLM 调用 | 一轮里可能多次调模型（工具结果还要再问一遍） |
| `max_tool_calls` | 工具执行次数 | 模型想调多少次工具，程序最多执行多少次 |
| `timeout` | 秒 | 从 Agent 启动开始的墙钟时间 |
| `token_budget` | token | `prompt_tokens + completion_tokens` 累计 |
| `cost_budget` | 美元 | 用一张近似价格表估算，只为演示 |

`loop.py` 里每次「调模型前 / 执行工具前」都会问 `LimitTracker`：还能继续吗？不能就带上 `stop_reason` 退出。
