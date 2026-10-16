"""一个最小的学习笔记 MCP server。

三种原语放在同一个进程里，方便对照：

- Tool：模型发起的动作（记笔记、搜索）
- Resource：客户端来读的数据（目录、单条笔记）
- Prompt：可复用的提示词模板（总结、出题）

用官方 Python SDK 的 MCPServer，走 Streamable HTTP。这是一个长期运行的
HTTP 服务，agent 连过来，不会由 agent 拉起子进程。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ResourceNotFoundError, ToolError
from pydantic import BaseModel

mcp = MCPServer(
    "study-notes",
    instructions=(
        "这是学习笔记服务。记笔记、删除和搜索用 tool；"
        "删除时 server 会向 client 发起确认；"
        "读目录用 notes://catalog，读单条用 notes://{id}；"
        "总结用 prompt summarize_notes，出题用 prompt quiz_from_notes。"
    ),
    log_level="WARNING",
)


@dataclass
class Note:
    id: str
    title: str
    body: str


_notes: dict[str, Note] = {
    "n1": Note("n1", "Tool", "Tool 是模型可以发起的动作，例如 add_note、search_notes。"),
    "n2": Note("n2", "Resource", "Resource 是可读取的数据。目录是 notes://catalog，单条是 notes://n2。"),
    "n3": Note("n3", "Prompt", "Prompt 是可复用的提示词模板，例如 summarize_notes、quiz_from_notes。"),
}
_seq = len(_notes)


def _require_text(value: str, field: str) -> str:
    text = value.strip()
    if not text:
        raise ToolError(f"{field} 不能为空")
    return text


@mcp.tool(structured_output=False)
def add_note(title: str, body: str) -> str:
    """新增一条学习笔记，返回笔记 id 和读取地址。"""
    global _seq
    title = _require_text(title, "title")
    body = _require_text(body, "body")
    _seq += 1
    note_id = f"n{_seq}"
    _notes[note_id] = Note(note_id, title, body)
    return f"已记下 {note_id}：{title}\n读取地址：notes://{note_id}"


class DeleteDecision(BaseModel):
    """删除确认没有额外字段。用户接受或拒绝这次 elicitation 即可。"""


@mcp.tool(structured_output=False)
async def delete_note(note_id: str, ctx: Context) -> str:
    """删除一条笔记。执行过程中会向 MCP client 询问用户是否确认。"""
    note_id = _require_text(note_id, "note_id")
    note = _notes.get(note_id)
    if note is None:
        raise ToolError(f"没有笔记 {note_id}")
    result = await ctx.elicit(
        message=f"MCP server 请求确认：删除笔记 {note.id}「{note.title}」？删除后无法恢复。",
        schema=DeleteDecision,
    )
    if result.action != "accept":
        return f"用户未确认，笔记 {note.id}「{note.title}」仍保留。"
    del _notes[note_id]
    return f"已删除 {note.id}：{note.title}"


@mcp.tool(structured_output=False)
def search_notes(query: str) -> str:
    """按标题或正文搜索笔记。query 为空时返回全部笔记。"""
    keyword = query.strip().lower()
    matched = [
        note
        for note in _notes.values()
        if not keyword or keyword in note.title.lower() or keyword in note.body.lower()
    ]
    if not matched:
        return f"没有找到包含「{query.strip()}」的笔记"
    lines = [f"{note.id}\t{note.title}\tnotes://{note.id}\n{note.body}" for note in matched]
    return "\n\n".join(lines)


@mcp.resource(
    "notes://catalog",
    name="notes-catalog",
    description="全部学习笔记的目录",
    mime_type="text/plain",
)
def notes_catalog() -> str:
    """列出全部笔记的 id、标题和读取地址。"""
    if not _notes:
        return "笔记库是空的"
    lines = [f"{note.id}\t{note.title}\tnotes://{note.id}" for note in _notes.values()]
    return "\n".join(lines)


@mcp.resource(
    "notes://{note_id}",
    name="note",
    description="按 id 读取一条笔记，例如 notes://n1",
    mime_type="text/plain",
)
def note_by_id(note_id: str) -> str:
    """读取一条笔记。"""
    note = _notes.get(note_id)
    if note is None:
        raise ResourceNotFoundError(f"没有笔记 {note_id}")
    return f"# {note.title}\n\n{note.body}"


@mcp.prompt(title="总结笔记", description="生成一段要求总结笔记库的提示词")
def summarize_notes() -> str:
    """让模型先读目录和每条笔记，再给出简体中文总结。"""
    return (
        "请总结学习笔记库。"
        "先用 read_mcp_resource 读取 notes://catalog，再按目录里的地址逐条读取。"
        "用简体中文列出每条笔记的标题和一句话要点，不要编造目录里没有的笔记。"
    )


@mcp.prompt(title="按笔记出题", description="围绕一个主题生成练习题提示词")
def quiz_from_notes(topic: str) -> str:
    """根据笔记库，围绕 topic 出一道简答题。"""
    return (
        f"请围绕「{topic}」出一道简答题。"
        "先用 search_notes 或 read_mcp_resource 查看相关笔记，题目和参考答案都只能依据笔记，"
        "用简体中文回答，并在最后单独给出参考答案。"
    )


def main() -> None:
    host = os.getenv("MCP_HOST", "127.0.0.1")
    port = int(os.getenv("MCP_PORT", "8000"))
    print(f"study-notes MCP server: http://{host}:{port}/mcp", flush=True)
    mcp.run(transport="streamable-http", host=host, port=port)
