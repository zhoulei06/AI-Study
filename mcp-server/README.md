# mcp-server

用官方 Python SDK（`mcp.server.mcpserver.MCPServer`）写的学习笔记服务。stdio 传输。

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

单独运行会停在 stdio 上等待客户端。给 `agent-demo` 用时不用手动启动，agent 会自己拉起这个进程。
