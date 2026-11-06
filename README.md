# 启动说明

页面已更名为 **Nexus Agent**，支持结构化规划、逐步执行、验证和有限重新规划。配置与实现边界见 [规划执行架构](docs/planner-workflow.md)。

项目架构、目录职责和主要调用链见 [项目架构与目录说明](项目架构与目录说明.md)。

网页聊天要同时开四个进程，顺序不能反：先 Keycloak，再笔记服务，再 Agent 接口，最后页面。命令行聊天可以不启页面。Agent 启动时会打开浏览器，登录完成后才继续。

| 顺序 | 目录 | 做什么 | 地址 |
| --- | --- | --- | --- |
| 1 | `keycloak` | 登录和角色。alice 可写，bob 只读 | `http://localhost:8080` |
| 2 | `mcp-server` | 学习笔记 MCP 服务，校验 Keycloak 的 token | `http://127.0.0.1:8000/mcp` |
| 3 | `agent-server` | Agent 循环。命令行直接跑，网页走 FastAPI | 接口 `http://127.0.0.1:8010/agent` |
| 4 | `front` | React 聊天页，用 AG-UI 调上面的接口 | `http://127.0.0.1:5173` |

每个服务一个终端，开着不要关。

## 第一次准备

```powershell
cd "D:\work\AI Study\mcp-server"
uv sync

cd "D:\work\AI Study\agent-server"
uv sync

cd "D:\work\AI Study\front"
pnpm install
```

在 `agent-server/.env` 里填入 [DeepSeek](https://platform.deepseek.com/api_keys) 的 key：

```env
DEEPSEEK_API_KEY=sk-...
DEEPSEEK_MODEL=deepseek-chat
DEEPSEEK_BASE_URL=https://api.deepseek.com
```

前端用 pnpm。本机的 Node 是 vfox 里的 `node.exe`，没有单独的 `npm`。

## 1. Keycloak

本机演示账号，只供这个仓库使用：

| 用户 | 密码 | 权限 |
| --- | --- | --- |
| alice | alice | 读取、新增、删除自己的笔记 |
| bob | bob | 只能读取自己的笔记 |
| admin | admin | Keycloak 管理台，不用于笔记 |

```powershell
cd "D:\work\AI Study\keycloak"
docker compose up
```

看到 Keycloak 监听 `8080` 后，浏览器能打开 `http://localhost:8080` 再进行下一步。登录页请用 `localhost`，不要改成 `127.0.0.1`。管理台用 admin / admin，realm 选 `study`。

改过 `study-realm.json` 之后要重建一次，否则还是旧配置：

```powershell
docker compose down
docker compose up
```

## 2. mcp-server

笔记在这个进程的内存里，并且按登录用户分开。关掉进程后，每个人的笔记回到最初的三条。

```powershell
cd "D:\work\AI Study\mcp-server"
uv run python -m mcp_server
```

看到 `study-notes MCP server: http://127.0.0.1:8000/mcp` 就可以进行下一步。

换端口时在这个终端里设环境变量，Agent 那边要改成同一个地址。token 的受众默认是 `http://127.0.0.1:8000/mcp`，和 Keycloak 里的 Audience 一致；地址改了的话，两边都要改。

```powershell
$env:MCP_HOST = "127.0.0.1"
$env:MCP_PORT = "8000"
uv run python -m mcp_server
```

## 3. agent-server

会话 checkpoint 和长期记忆都用 PostgreSQL，长期记忆另有 pgvector。Agent 启动前先把数据库拉起来，容器可以一直开着：

```powershell
cd "D:\work\AI Study\agent-server"
docker compose up -d
```

这个目录有两种启动方式，都要等 Keycloak、mcp-server 和上面的数据库已经在听。启动后浏览器会打开 Keycloak 登录页，登录成功 Agent 才算连上笔记服务。

网页聊天用 FastAPI。它会去连 MCP，连上后停在 `http://127.0.0.1:8010`：

```powershell
cd "D:\work\AI Study\agent-server"
uv run python -m agent.server
```

命令行聊天不经过 8010，直接在终端里问：

```powershell
cd "D:\work\AI Study\agent-server"
uv run python main.py
```

单次提问：

```powershell
uv run python main.py "现在几点了？北京天气怎么样？再帮我算 23*17"
```

MCP 不在默认地址时，在 `agent-server/.env` 里加：

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

## 4. front

页面启动前，8010 上的 Agent 接口要已经起来，也就是 Keycloak 登录已经完成。

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
