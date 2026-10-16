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
import sys
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from openai import OpenAI

from .limits import AgentLimits, LimitTracker
from .mcp_client import McpConnection
from .tools import ToolRegistry, default_tools

SYSTEM_PROMPT = """你是一个会用工具的助手。
计算、查时间、查天气用本地工具。笔记相关的事用 MCP 工具。
不要自己编造数字、天气或笔记内容。
工具返回结果后，用简体中文给出最终回答。
回答用 Markdown：分点用列表，代码放进代码块，需要强调时用加粗。
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
        self.system_prompt = system_prompt
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
            return self._stopped(self.tracker, stop, "已达到 max_turns，不再接受新的用户消息。")

        self.messages.append({"role": "user", "content": user_input})
        self._log(f"\n========== turn {self.tracker.turns} ==========")
        return self._loop(self.messages, self.tracker, None)

    def run_messages(
        self,
        messages: list[dict[str, Any]],
        *,
        tracker: LimitTracker | None = None,
        on_event: Callable[[str, dict[str, str]], None] | None = None,
    ) -> AgentResult:
        """跑一轮已经包含用户消息的对话。HTTP 入口每个请求用自己的 tracker。"""
        active = tracker or LimitTracker(self.limits, self.model)
        stop = active.begin_turn()
        if stop:
            return self._stopped(active, stop, "已达到 max_turns，不再接受新的用户消息。", on_event)
        self._log(f"\n========== turn {active.turns} ==========")
        return self._loop(messages, active, on_event)

    def _loop(
        self,
        messages: list[dict[str, Any]],
        tracker: LimitTracker,
        on_event: Callable[[str, dict[str, str]], None] | None,
    ) -> AgentResult:
        while True:
            # 每次准备调模型前，先看还没超限
            stop = tracker.before_llm()
            if stop:
                return self._stopped(tracker, stop, f"循环在调用模型前停下：{stop}", on_event)

            tracker.record_iteration()
            self._log(f"→ iteration {tracker.iterations}")

            # 网页入口带着 on_event，模型输出按 token 推出去。
            # 命令行没有监听者，仍然等这一次调用完整返回。
            turn = (
                self._complete_stream(messages, tracker, on_event)
                if on_event is not None
                else self._complete(messages, tracker)
            )
            messages.append(_turn_to_message(turn))

            # 没有 tool_calls = 模型给出了最终回复，循环结束
            if not turn.tool_calls:
                text = turn.content.strip() or "(模型没有返回文本)"
                self._log("  最终回答")
                if on_event is None or not turn.content.strip():
                    self._emit(on_event, "assistant_text", text=text)
                return AgentResult(
                    text=text,
                    stop_reason="completed",
                    usage=tracker.snapshot(),
                )

            self._log(f"  模型请求 {len(turn.tool_calls)} 个工具")

            # 一次回复里可能有多个 tool_calls（并行调用）。
            # 演示里按顺序执行，结果全部追加后再进入下一轮 LLM。
            for call in turn.tool_calls:
                stop = tracker.before_tool()
                if stop:
                    return self._stopped(tracker, stop, f"循环在执行工具前停下：{stop}", on_event)

                result = self.tools.execute(call.name, call.arguments)
                tracker.record_tool_call()
                self._log(f"  ok {call.name}({call.arguments}) => {result}")
                self._emit(
                    on_event,
                    "tool",
                    id=call.id,
                    name=call.name,
                    arguments=call.arguments or "{}",
                    result=result,
                )

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": result,
                    }
                )

            stop = tracker.after_usage()
            if stop:
                return self._stopped(tracker, stop, f"本轮工具执行完后超限：{stop}", on_event)

    def _stopped(
        self,
        tracker: LimitTracker,
        reason: str,
        hint: str,
        on_event: Callable[[str, dict[str, str]], None] | None = None,
    ) -> AgentResult:
        text = f"[stopped: {reason}] {hint}"
        self._log(f"  {text}")
        self._emit(on_event, "assistant_text", text=text)
        return AgentResult(text=text, stop_reason=reason, usage=tracker.snapshot())

    def _complete(self, messages: list[dict[str, Any]], tracker: LimitTracker) -> "_ModelTurn":
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=self.tools.schemas(),
            tool_choice="auto",
            timeout=tracker.remaining_seconds(),
        )
        usage = response.usage
        if usage is not None:
            tracker.record_usage(
                prompt_tokens=usage.prompt_tokens or 0,
                completion_tokens=usage.completion_tokens or 0,
            )
        message = response.choices[0].message
        calls = [
            _ToolCall(
                id=call.id,
                name=call.function.name,
                arguments=call.function.arguments or "",
            )
            for call in message.tool_calls or []
        ]
        return _ModelTurn(content=message.content or "", tool_calls=calls)

    def _complete_stream(
        self,
        messages: list[dict[str, Any]],
        tracker: LimitTracker,
        on_event: Callable[[str, dict[str, str]], None],
    ) -> "_ModelTurn":
        stream = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=self.tools.schemas(),
            tool_choice="auto",
            timeout=tracker.remaining_seconds(),
            stream=True,
            stream_options={"include_usage": True},
        )
        parts: list[str] = []
        calls: dict[int, _ToolCall] = {}
        message_id = str(uuid.uuid4())
        started = False
        for chunk in stream:
            usage = chunk.usage
            if usage is not None:
                tracker.record_usage(
                    prompt_tokens=usage.prompt_tokens or 0,
                    completion_tokens=usage.completion_tokens or 0,
                )
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta is None:
                continue
            if delta.content:
                if not started:
                    self._emit(on_event, "text_start", id=message_id)
                    started = True
                self._emit(on_event, "text_delta", id=message_id, delta=delta.content)
                parts.append(delta.content)
            for piece in delta.tool_calls or []:
                index = piece.index if piece.index is not None else 0
                current = calls.get(index)
                if current is None:
                    current = _ToolCall(id=piece.id or "", name="", arguments="")
                    calls[index] = current
                if piece.id:
                    current.id = piece.id
                function = piece.function
                if function is None:
                    continue
                if function.name:
                    current.name += function.name
                if function.arguments:
                    current.arguments += function.arguments
        if started:
            self._emit(on_event, "text_end", id=message_id)
        ordered = [calls[index] for index in sorted(calls)]
        for call in ordered:
            if not call.id:
                call.id = str(uuid.uuid4())
        return _ModelTurn(content="".join(parts), tool_calls=ordered)

    def _emit(
        self,
        on_event: Callable[[str, dict[str, str]], None] | None,
        kind: str,
        **payload: str,
    ) -> None:
        if on_event is not None:
            on_event(kind, payload)

    def _log(self, line: str) -> None:
        if not self.verbose:
            return
        try:
            print(line, flush=True)
        except UnicodeEncodeError:
            encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
            safe = line.encode(encoding, errors="replace").decode(encoding, errors="replace")
            print(safe, flush=True)


@dataclass
class _ToolCall:
    id: str
    name: str
    arguments: str


@dataclass
class _ModelTurn:
    content: str
    tool_calls: list[_ToolCall]


def _turn_to_message(turn: _ModelTurn) -> dict[str, Any]:
    """把这一次模型回复收成 messages 里的一条 assistant。"""
    item: dict[str, Any] = {
        "role": "assistant",
        "content": turn.content or None,
    }
    if turn.tool_calls:
        item["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": call.arguments,
                },
            }
            for call in turn.tool_calls
        ]
    return item
