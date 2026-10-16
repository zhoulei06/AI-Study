# 启动说明

网页聊天要同时开三个进程，顺序不能反：先笔记服务，再 Agent 接口，最后页面。命令行聊天只需要前两个，Agent 接口可以不启。

| 顺序 | 目录 | 做什么 | 地址 |
| --- | --- | --- | --- |
| 1 | `mcp-server` | 学习笔记 MCP 服务，Streamable HTTP | `http://127.0.0.1:8000/mcp` |
| 2 | `agent-demo` | Agent 循环。命令行直接跑，网页走 FastAPI | 接口 `http://127.0.0.1:8010/agent` |
| 3 | `front` | React 聊天页，用 AG-UI 调上面的接口 | `http://127.0.0.1:5173` |

每个服务一个终端，开着不要关。

## 第一次准备

```powershell
cd "D:\work\AI Study\mcp-server"
uv sync

cd "D:\work\AI Study\agent-demo"
uv sync

cd "D:\work\AI Study\front"
pnpm install
```

在 `agent-demo/.env` 里填入 [DeepSeek](https://platform.deepseek.com/api_keys) 的 key：

```env
OPENAI_API_KEY=sk-...
OPENAI_MODEL=deepseek-chat
OPENAI_BASE_URL=https://api.deepseek.com
```

前端用 pnpm。本机的 Node 是 vfox 里的 `node.exe`，没有单独的 `npm`。

## 1. mcp-server

笔记在这个进程的内存里。关掉进程后，笔记回到最初的三条。

```powershell
cd "D:\work\AI Study\mcp-server"
uv run python -m mcp_server
```

看到 `study-notes MCP server: http://127.0.0.1:8000/mcp` 就可以进行下一步。

换端口时在这个终端里设环境变量，Agent 那边要改成同一个地址：

```powershell
$env:MCP_HOST = "127.0.0.1"
$env:MCP_PORT = "8000"
uv run python -m mcp_server
```

## 2. agent-demo

这个目录有两种启动方式，都要等 mcp-server 已经在听。

网页聊天用 FastAPI。它会去连 MCP，连上后停在 `http://127.0.0.1:8010`：

```powershell
cd "D:\work\AI Study\agent-demo"
uv run python -m agent.server
```

命令行聊天不经过 8010，直接在终端里问：

```powershell
cd "D:\work\AI Study\agent-demo"
uv run python main.py
```

单次提问：

```powershell
uv run python main.py "现在几点了？北京天气怎么样？再帮我算 23*17"
```

MCP 不在默认地址时，在 `agent-demo/.env` 里加：

```env
MCP_URL=http://127.0.0.1:8000/mcp
```

Agent 接口换端口：

```powershell
$env:AGENT_HOST = "127.0.0.1"
$env:AGENT_PORT = "8010"
uv run python -m agent.server
```

`8010` 改掉以后，前端也要指向新地址，见下一节的 `VITE_AGENT_URL`。

## 3. front

页面启动前，8010 上的 Agent 接口要已经起来。

```powershell
cd "D:\work\AI Study\front"
pnpm dev
```

浏览器打开 `http://127.0.0.1:5173`。页面默认请求 `http://127.0.0.1:8010/agent`。

Agent 接口不是这个地址时，启动前加上：

```powershell
$env:VITE_AGENT_URL = "http://127.0.0.1:8010/agent"
pnpm dev
```
