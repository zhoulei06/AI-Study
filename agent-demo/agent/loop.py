"""手写 Agent Loop。

核心就这一段：

    while True:
        response = llm(messages, tools=tools)
        if not response.tool_calls:
            return response            # 模型认为可以给最终答案了
        for call in response.tool_calls:
            result = execute_tool(call)
            messages.append(result)    # 把工具结果塞回对话，继续循环

其余代码都是在给这个循环加「该停的时候停」。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from openai import OpenAI

from .limits import AgentLimits, LimitTracker
from .mcp_client import McpConnection
from .tools import ToolRegistry, default_tools

SYSTEM_PROMPT = """你是一个会用工具的助手。
计算、查时间、查天气用本地工具。笔记相关的事用 MCP 工具。
不要自己编造数字、天气或笔记内容。
工具返回结果后，用简体中文给出最终回答。
"""


@dataclass
class AgentResult:
    text: str
    stop_reason: str
    usage: dict[str, str]


class Agent:
    def __init__(
        self,
        *,
        model: str | None = None,
        limits: AgentLimits | None = None,
        tools: ToolRegistry | None = None,
        verbose: bool = True,
        use_mcp: bool = True,
    ):
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError(
                "没有找到 OPENAI_API_KEY。请在 agent-demo/.env 里填入 DeepSeek 的 key。"
            )

        base_url = os.getenv("OPENAI_BASE_URL", "").strip() or "https://api.deepseek.com"
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model or os.getenv("OPENAI_MODEL", "deepseek-chat")
        self.limits = limits or AgentLimits()
        self.tools = tools or default_tools()
        self.verbose = verbose
        self.mcp: McpConnection | None = None
        system_prompt = SYSTEM_PROMPT
        if use_mcp:
            self.mcp = McpConnection()
            self.tools.extend(self.mcp.openai_tools())
            system_prompt = f"{SYSTEM_PROMPT}\n{self.mcp.model_instructions()}"
            self._log(self.mcp.startup_summary())
        self.messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt}
        ]
        self.tracker = LimitTracker(self.limits, self.model)

    def close(self) -> None:
        if self.mcp is not None:
            self.mcp.close()

    def chat(self, user_input: str) -> AgentResult:
        """处理一轮用户输入。多轮聊天反复调用这个方法即可。"""
        stop = self.tracker.begin_turn()
        if stop:
            return self._stopped(stop, "已达到 max_turns，不再接受新的用户消息。")

        self.messages.append({"role": "user", "content": user_input})
        self._log(f"\n========== turn {self.tracker.turns} ==========")

        while True:
            # 每次准备调模型前，先看还没超限
            stop = self.tracker.before_llm()
            if stop:
                return self._stopped(stop, f"循环在调用模型前停下：{stop}")

            self.tracker.record_iteration()
            self._log(f"→ iteration {self.tracker.iterations}")

            response = self.client.chat.completions.create(
                model=self.model,
                messages=self.messages,
                tools=self.tools.schemas(),
                tool_choice="auto",
                timeout=self.tracker.remaining_seconds(),
            )

            usage = response.usage
            if usage is not None:
                self.tracker.record_usage(
                    prompt_tokens=usage.prompt_tokens or 0,
                    completion_tokens=usage.completion_tokens or 0,
                )

            message = response.choices[0].message
            self.messages.append(_assistant_to_dict(message))

            # 没有 tool_calls = 模型给出了最终回复，循环结束
            if not message.tool_calls:
                text = (message.content or "").strip() or "(模型没有返回文本)"
                self._log("  最终回答")
                return AgentResult(
                    text=text,
                    stop_reason="completed",
                    usage=self.tracker.snapshot(),
                )

            self._log(f"  模型请求 {len(message.tool_calls)} 个工具")

            # 一次回复里可能有多个 tool_calls（并行调用）。
            # 演示里按顺序执行，结果全部追加后再进入下一轮 LLM。
            for call in message.tool_calls:
                stop = self.tracker.before_tool()
                if stop:
                    return self._stopped(stop, f"循环在执行工具前停下：{stop}")

                result = self.tools.execute(call.function.name, call.function.arguments)
                self.tracker.record_tool_call()
                self._log(
                    f"  ✓ {call.function.name}({call.function.arguments}) => {result}"
                )

                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": result,
                    }
                )

            stop = self.tracker.after_usage()
            if stop:
                return self._stopped(stop, f"本轮工具执行完后超限：{stop}")

    def _stopped(self, reason: str, hint: str) -> AgentResult:
        text = f"[stopped: {reason}] {hint}"
        self._log(f"  {text}")
        return AgentResult(text=text, stop_reason=reason, usage=self.tracker.snapshot())

    def _log(self, line: str) -> None:
        if self.verbose:
            print(line)


def _assistant_to_dict(message: Any) -> dict[str, Any]:
    """把 SDK 对象变成普通 dict，方便看清 messages 里到底存了什么。"""
    item: dict[str, Any] = {
        "role": "assistant",
        "content": message.content,
    }
    if message.tool_calls:
        item["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.function.name,
                    "arguments": call.function.arguments,
                },
            }
            for call in message.tool_calls
        ]
    return item
