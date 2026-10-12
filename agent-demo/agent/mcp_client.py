"""把 mcp-server 接到手写 Agent 上。

Agent 仍走 OpenAI tools 协议。这里做的是 MCP client：

- stdio 拉起 mcp-server
- 把 MCP tool 转成 Agent 的 function tool
- resource / prompt 不是模型直接调用的原语，所以各包一层桥接工具
"""

from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path
from typing import Any

from mcp import Client, Implementation, StdioServerParameters

from .tools import Tool

DEFAULT_SERVER_DIR = Path(__file__).resolve().parents[2] / "mcp-server"


def _block_text(block: Any) -> str:
    text = getattr(block, "text", None)
    if isinstance(text, str):
        return text
    return str(block)


class McpConnection:
    def __init__(self, server_dir: Path | None = None):
        self.server_dir = (server_dir or DEFAULT_SERVER_DIR).resolve()
        if not (self.server_dir / "pyproject.toml").is_file():
            raise RuntimeError(f"找不到 MCP server：{self.server_dir}")

        self._closed = False
        self._client: Client | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._thread_main, name="mcp-client", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=5):
            raise RuntimeError("MCP 客户端事件循环没有启动")

        try:
            self._run(self._connect(), timeout=90)
        except Exception:
            self.close()
            raise

    def openai_tools(self) -> list[Tool]:
        tools: list[Tool] = []
        for spec in self.remote_tools:
            parameters = dict(spec.input_schema)
            parameters.setdefault("type", "object")
            tools.append(
                Tool(
                    name=spec.name,
                    description=spec.description or spec.title or spec.name,
                    parameters=parameters,
                    handler=self._tool_handler(spec.name),
                )
            )
        tools.extend(self._bridge_tools())
        return tools

    def startup_summary(self) -> str:
        tool_names = ", ".join(tool.name for tool in self.remote_tools) or "(无)"
        resources = ", ".join(item.uri for item in self.resources) or "(无)"
        templates = ", ".join(item.uri_template for item in self.templates) or "(无)"
        prompts = ", ".join(_prompt_signature(item) for item in self.prompts) or "(无)"
        name = self.server_name or "mcp-server"
        return (
            f"MCP {name} 已连接\n"
            f"  tools: {tool_names}\n"
            f"  resources: {resources}\n"
            f"  templates: {templates}\n"
            f"  prompts: {prompts}"
        )

    def model_instructions(self) -> str:
        resources = "\n".join(
            f"- {item.uri}：{item.description or item.name}" for item in self.resources
        ) or "- （无）"
        templates = "\n".join(
            f"- {item.uri_template}：{item.description or item.name}" for item in self.templates
        ) or "- （无）"
        prompts = "\n".join(
            f"- {_prompt_signature(item)}：{item.description or item.title or item.name}"
            for item in self.prompts
        ) or "- （无）"
        server_text = f"\n服务说明：{self.instructions}" if self.instructions else ""
        return f"""已通过 stdio 连接 MCP server「{self.server_name or "mcp-server"}」。{server_text}
MCP tool 已作为函数提供，直接调用。
读 resource 时调用 read_mcp_resource。已知资源：
{resources}
资源模板：
{templates}
取 prompt 模板时调用 get_mcp_prompt，然后按返回的文本继续完成任务。已知模板：
{prompts}"""

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        loop = self._loop
        if loop is None or not loop.is_running():
            return
        if self._client is not None:
            try:
                self._run(self._shutdown(), timeout=15)
            except Exception:
                pass
        loop.call_soon_threadsafe(loop.stop)
        self._thread.join(timeout=5)

    def _tool_handler(self, name: str):
        def handler(**arguments: Any) -> str:
            result = self._run(self._require_client().call_tool(name, arguments or None))
            parts = [_block_text(block) for block in result.content]
            if not parts and result.structured_content is not None:
                parts.append(str(result.structured_content))
            text = "\n".join(parts).strip() or "(工具没有返回内容)"
            if result.is_error:
                return f"Error: {text}"
            return text

        return handler

    def read_resource(self, uri: str) -> str:
        result = self._run(self._require_client().read_resource(uri))
        parts = [_block_text(block) for block in result.contents]
        return "\n".join(parts).strip() or "(资源是空的)"

    def get_mcp_prompt(self, name: str, arguments: dict[str, Any] | str | None = None) -> str:
        payload = _prompt_arguments(arguments)
        result = self._run(self._require_client().get_prompt(name, payload or None))
        lines = []
        if result.description:
            lines.append(result.description)
        for message in result.messages:
            lines.append(f"[{message.role}] {_block_text(message.content)}")
        return "\n".join(lines).strip() or "(prompt 是空的)"

    def _bridge_tools(self) -> list[Tool]:
        resource_hint = ", ".join(item.uri for item in self.resources) or "notes://catalog"
        template_hint = ", ".join(item.uri_template for item in self.templates)
        prompt_hint = ", ".join(_prompt_signature(item) for item in self.prompts)
        uri_description = f"资源地址，例如 {resource_hint}"
        if template_hint:
            uri_description += f"。也可以按模板拼地址：{template_hint}"
        return [
            Tool(
                name="read_mcp_resource",
                description=f"读取已连接 MCP server 的 resource。{uri_description}",
                parameters={
                    "type": "object",
                    "properties": {
                        "uri": {"type": "string", "description": uri_description},
                    },
                    "required": ["uri"],
                },
                handler=self.read_resource,
            ),
            Tool(
                name="get_mcp_prompt",
                description=f"获取 MCP prompt 模板。已知：{prompt_hint}。拿到模板后按其中的要求继续。",
                parameters={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "prompt 名称"},
                        "arguments": {
                            "type": "object",
                            "description": "模板参数。没有参数时省略，或传空对象",
                            "additionalProperties": {"type": "string"},
                        },
                    },
                    "required": ["name"],
                },
                handler=self.get_mcp_prompt,
            ),
        ]

    async def _connect(self) -> None:
        client = Client(
            StdioServerParameters(
                command="uv",
                args=["run", "--directory", str(self.server_dir), "python", "-m", "mcp_server"],
                cwd=self.server_dir,
                env={"PYTHONUNBUFFERED": "1"},
            ),
            client_info=Implementation(name="agent-demo", version="0.1.0"),
            cache=None,
            read_timeout_seconds=60,
        )
        await client.__aenter__()
        self._client = client
        info = client.server_info
        self.server_name = info.name if info is not None else None
        self.instructions = client.instructions
        self.remote_tools = await _collect(client.list_tools, "tools")
        self.resources = await _collect(client.list_resources, "resources")
        self.templates = await _collect(client.list_resource_templates, "resource_templates")
        self.prompts = await _collect(client.list_prompts, "prompts")

    async def _shutdown(self) -> None:
        client = self._client
        self._client = None
        if client is not None:
            await client.__aexit__(None, None, None)

    def _require_client(self) -> Client:
        if self._client is None:
            raise RuntimeError("MCP 连接已经关闭")
        return self._client

    def _run(self, coro: Any, timeout: float = 60) -> Any:
        loop = self._loop
        if loop is None:
            raise RuntimeError("MCP 客户端事件循环没有启动")
        return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=timeout)

    def _thread_main(self) -> None:
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        self._loop.run_forever()
        self._loop.close()


async def _collect(list_page: Any, field: str) -> list[Any]:
    items: list[Any] = []
    cursor = None
    for _ in range(10):
        page = await list_page(cursor=cursor)
        items.extend(getattr(page, field))
        cursor = page.next_cursor
        if not cursor:
            return items
    return items


def _prompt_signature(prompt: Any) -> str:
    if not prompt.arguments:
        return prompt.name
    parts = [arg.name if arg.required else f"{arg.name}?" for arg in prompt.arguments]
    return f"{prompt.name}({', '.join(parts)})"


def _prompt_arguments(arguments: dict[str, Any] | str | None) -> dict[str, str]:
    if arguments is None or arguments == "":
        return {}
    if isinstance(arguments, str):
        import json

        parsed = json.loads(arguments or "{}")
        if not isinstance(parsed, dict):
            raise ValueError("prompt arguments 必须是 JSON 对象")
        arguments = parsed
    return {str(key): str(value) for key, value in arguments.items()}
