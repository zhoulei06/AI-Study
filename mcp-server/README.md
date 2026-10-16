# mcp-server

用官方 Python SDK（`mcp.server.mcpserver.MCPServer`）写的学习笔记服务。Streamable HTTP 传输，默认 `http://127.0.0.1:8000/mcp`。

| 原语 | 名字 | 作用 |
|---|---|---|
| Tool | `add_note` / `search_notes` | 记笔记、搜索 |
| Resource | `notes://catalog` | 笔记目录 |
| Resource 模板 | `notes://{note_id}` | 读一条笔记，例如 `notes://n1` |
| Prompt | `summarize_notes` | 总结笔记库的提示词 |
| Prompt | `quiz_from_notes` | 按主题出题的提示词 |

笔记在进程内存里，服务退出后恢复成三条种子笔记。

```powershell
cd "D:\work\AI Study\mcp-server"
uv sync
uv run python -m mcp_server
```

单独运行后停在 HTTP 上等待客户端。`agent-demo` 不会拉起这个进程，要先在这里启动，再去跑 agent。

地址可用环境变量改：`MCP_HOST`（默认 `127.0.0.1`）、`MCP_PORT`（默认 `8000`）。客户端对应的是 `MCP_URL`，默认 `http://127.0.0.1:8000/mcp`。
