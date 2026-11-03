# mcp-server

用官方 Python SDK（`mcp.server.mcpserver.MCPServer`）写的学习笔记服务。Streamable HTTP 传输，默认 `http://127.0.0.1:8000/mcp`。

| 原语 | 名字 | 作用 |
|---|---|---|
| Tool | `add_note` / `delete_note` / `search_notes` | 记笔记、删除、搜索。删除和新增需要写权限 |
| Resource | `notes://catalog` | 笔记目录 |
| Resource 模板 | `notes://{note_id}` | 读一条笔记，例如 `notes://n1` |
| Prompt | `summarize_notes` | 总结笔记库的提示词 |
| Prompt | `quiz_from_notes` | 按主题出题的提示词 |

登录由 Keycloak 签发 access token，这个进程只校验 token。笔记按用户名分开，存在进程内存里；服务退出后，每个人恢复成三条种子笔记。alice 可以新增和删除，bob 只有读取权限。

先启动 Keycloak，见仓库根目录的启动说明。然后：

```powershell
cd "D:\work\AI Study\mcp-server"
uv sync
uv run python -m mcp_server
```

单独运行后停在 HTTP 上等待客户端。`agent-server` 不会拉起这个进程，要先在这里启动，再去跑 agent。

地址可用环境变量改：`MCP_HOST`（默认 `127.0.0.1`）、`MCP_PORT`（默认 `8000`）。客户端对应的是 `MCP_URL`，默认 `http://127.0.0.1:8000/mcp`。
