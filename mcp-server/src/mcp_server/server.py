"""一个最小的学习笔记 MCP server。

三种原语放在同一个进程里，方便对照：

- Tool：模型发起的动作（记笔记、搜索）
- Resource：客户端来读的数据（目录、单条笔记）
- Prompt：可复用的提示词模板（总结、出题）

用官方 Python SDK 的 MCPServer，走 Streamable HTTP。登录交给 Keycloak。
这个进程只校验 access token，并按用户分开笔记。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Annotated

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import (
    AcceptedElicitation,
    Elicit,
    ElicitationResult,
    MCPServer,
    Resolve,
)
from mcp.server.mcpserver.exceptions import ResourceNotFoundError, ToolError
from mcp.types import ToolAnnotations
from pydantic import AnyHttpUrl, BaseModel

from .auth import READ_SCOPE, WRITE_SCOPE, KeycloakTokenVerifier

DEFAULT_ISSUER = "http://localhost:8080/realms/study"
DEFAULT_RESOURCE_URL = "http://127.0.0.1:8000/mcp"

_issuer = os.getenv("KEYCLOAK_ISSUER", "").strip() or DEFAULT_ISSUER
_resource_url = os.getenv("MCP_RESOURCE_URL", "").strip() or DEFAULT_RESOURCE_URL

mcp = MCPServer(
    "study-notes",
    instructions=(
        "这是学习笔记服务。笔记按当前登录用户隔离。"
        "记笔记、删除和搜索用 tool；删除时 server 会向 client 发起确认；"
        "没有 notes:write 权限时不要新增或删除，告诉用户只有读取权限。"
        "读目录用 notes://catalog，读单条用 notes://{id}；"
        "总结用 prompt summarize_notes，出题用 prompt quiz_from_notes。"
    ),
    log_level="WARNING",
    token_verifier=KeycloakTokenVerifier(_issuer, _resource_url),
    auth=AuthSettings(
        issuer_url=AnyHttpUrl(_issuer),
        resource_server_url=AnyHttpUrl(_resource_url),
        required_scopes=[READ_SCOPE],
        validate_token_resource=True,
    ),
)


@dataclass
class Note:
    id: str
    title: str
    body: str


_SEED = (
    Note("n1", "Tool", "Tool 是模型可以发起的动作，例如 add_note、search_notes。"),
    Note("n2", "Resource", "Resource 是可读取的数据。目录是 notes://catalog，单条是 notes://n2。"),
    Note("n3", "Prompt", "Prompt 是可复用的提示词模板，例如 summarize_notes、quiz_from_notes。"),
)
_libraries: dict[str, dict[str, Note]] = {}


def _current_user() -> AccessToken:
    token = get_access_token()
    if token is None or not token.subject:
        raise ToolError("未登录")
    return token


def _library(token: AccessToken) -> dict[str, Note]:
    assert token.subject is not None
    library = _libraries.get(token.subject)
    if library is None:
        library = {note.id: Note(note.id, note.title, note.body) for note in _SEED}
        _libraries[token.subject] = library
    return library


def _require_write(token: AccessToken) -> None:
    if WRITE_SCOPE not in token.scopes:
        raise ToolError(f"用户 {token.subject} 只有读取权限，不能修改笔记")


def _require_text(value: str, field: str) -> str:
    text = value.strip()
    if not text:
        raise ToolError(f"{field} 不能为空")
    return text


def _next_id(library: dict[str, Note]) -> str:
    numbers = [int(note_id[1:]) for note_id in library if note_id.startswith("n") and note_id[1:].isdigit()]
    return f"n{max(numbers, default=0) + 1}"


@mcp.tool(structured_output=False, annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False))
def add_note(title: str, body: str) -> str:
    """新增一条学习笔记，返回笔记 id 和读取地址。需要 notes:write。"""
    token = _current_user()
    _require_write(token)
    title = _require_text(title, "title")
    body = _require_text(body, "body")
    library = _library(token)
    note_id = _next_id(library)
    library[note_id] = Note(note_id, title, body)
    return f"已记下 {note_id}：{title}（用户 {token.subject}）\n读取地址：notes://{note_id}"


class DeleteDecision(BaseModel):
    """删除确认没有额外字段。用户接受或拒绝这次 elicitation 即可。"""


def _confirm_delete(note_id: str) -> Elicit[DeleteDecision]:
    """2026 协议：先返回问题，client 带回答重试后才执行删除。"""
    token = _current_user()
    _require_write(token)
    note_id = _require_text(note_id, "note_id")
    note = _library(token).get(note_id)
    if note is None:
        raise ToolError(f"没有笔记 {note_id}")
    return Elicit(
        message=f"MCP server 请求确认：删除笔记 {note.id}「{note.title}」？删除后无法恢复。",
        schema=DeleteDecision,
    )


@mcp.tool(structured_output=False, annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True))
def delete_note(
    note_id: str,
    decision: Annotated[ElicitationResult[DeleteDecision], Resolve(_confirm_delete)],
) -> str:
    """删除一条笔记。server 先向 client 要确认，用户接受后才删除。需要 notes:write。"""
    token = _current_user()
    _require_write(token)
    note_id = _require_text(note_id, "note_id")
    library = _library(token)
    note = library.get(note_id)
    if note is None:
        raise ToolError(f"没有笔记 {note_id}")
    if not isinstance(decision, AcceptedElicitation):
        return f"用户未确认，笔记 {note.id}「{note.title}」仍保留。"
    del library[note_id]
    return f"已删除 {note.id}：{note.title}（用户 {token.subject}）"


@mcp.tool(structured_output=False, annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False))
def search_notes(query: str) -> str:
    """按标题或正文搜索当前用户的笔记。query 为空时返回该用户的全部笔记。"""
    token = _current_user()
    library = _library(token)
    keyword = query.strip().lower()
    matched = [
        note
        for note in library.values()
        if not keyword or keyword in note.title.lower() or keyword in note.body.lower()
    ]
    if not matched:
        return f"用户 {token.subject} 的笔记里没有找到包含「{query.strip()}」的内容"
    lines = [f"{note.id}\t{note.title}\tnotes://{note.id}\n{note.body}" for note in matched]
    return f"用户 {token.subject} 的笔记\n\n" + "\n\n".join(lines)


@mcp.resource(
    "notes://catalog",
    name="notes-catalog",
    description="当前登录用户的学习笔记目录",
    mime_type="text/plain",
)
def notes_catalog() -> str:
    """列出当前用户全部笔记的 id、标题和读取地址。"""
    token = _current_user()
    library = _library(token)
    if not library:
        return f"用户 {token.subject} 的笔记库是空的"
    lines = [f"{note.id}\t{note.title}\tnotes://{note.id}" for note in library.values()]
    return f"用户 {token.subject}\n" + "\n".join(lines)


@mcp.resource(
    "notes://{note_id}",
    name="note",
    description="按 id 读取当前用户的一条笔记，例如 notes://n1",
    mime_type="text/plain",
)
def note_by_id(note_id: str) -> str:
    """读取当前用户的一条笔记。"""
    token = _current_user()
    note = _library(token).get(note_id)
    if note is None:
        raise ResourceNotFoundError(f"没有笔记 {note_id}")
    return f"# {note.title}\n\n{note.body}"


@mcp.prompt(title="总结笔记", description="生成一段要求总结笔记库的提示词")
def summarize_notes() -> str:
    """让模型先读目录和每条笔记，再给出简体中文总结。"""
    return (
        "请总结当前登录用户的学习笔记库。"
        "先用 read_mcp_resource 读取 notes://catalog，再按目录里的地址逐条读取。"
        "用简体中文列出每条笔记的标题和一句话要点，不要编造目录里没有的笔记。"
    )


@mcp.prompt(title="按笔记出题", description="围绕一个主题生成练习题提示词")
def quiz_from_notes(topic: str) -> str:
    """根据笔记库，围绕 topic 出一道简答题。"""
    return (
        f"请围绕「{topic}」出一道简答题。"
        "先用 search_notes 或 read_mcp_resource 查看当前用户的相关笔记，题目和参考答案都只能依据笔记，"
        "用简体中文回答，并在最后单独给出参考答案。"
    )


def main() -> None:
    host = os.getenv("MCP_HOST", "127.0.0.1")
    port = int(os.getenv("MCP_PORT", "8000"))
    print(f"study-notes MCP server: http://{host}:{port}/mcp", flush=True)
    print(f"Keycloak issuer: {_issuer}", flush=True)
    print(f"token audience: {_resource_url}", flush=True)
    mcp.run(transport="streamable-http", host=host, port=port)
