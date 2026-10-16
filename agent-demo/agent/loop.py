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

import json
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
记笔记直接调用 add_note，不要先询问要不要保存；系统会请用户确认。
删除笔记直接调用 delete_note。确认由 MCP server 在这次调用里向 client 发起，不要自己先问。
工具返回结果后，用简体中文给出最终回答。
回答用 Markdown：分点用列表，代码放进代码块，需要强调时用加粗。
"""


# 会改 MCP 数据的工具。网页上先暂停，等用户接受或拒绝。
_CONFIRM_TOOLS = frozenset({"add_note"})


@dataclass
class PendingApproval:
    interrupt_id: str
    tool_call_id: str
    name: str
    arguments: str
    message: str


@dataclass
class AgentResult:
    text: str
    stop_reason: str
    usage: dict[str, str]
    approval: PendingApproval | None = None


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
        approvals: dict[str, bool] | None = None,
    ) -> AgentResult:
        """跑一轮已经包含用户消息的对话。HTTP 入口每个请求用自己的 tracker。"""
        active = tracker or LimitTracker(self.limits, self.model)
        stop = active.begin_turn()
        if stop:
            return self._stopped(active, stop, "已达到 max_turns，不再接受新的用户消息。", on_event)
        self._log(f"\n========== turn {active.turns} ==========")
        return self._loop(messages, active, on_event, approvals)

    def _loop(
        self,
        messages: list[dict[str, Any]],
        tracker: LimitTracker,
        on_event: Callable[[str, dict[str, str]], None] | None,
        approvals: dict[str, bool] | None = None,
    ) -> AgentResult:
        decisions = dict(approvals or {})
        while True:
            # 上一轮停在确认上时，消息里会留下还没有结果的 tool call。
            paused = self._apply_pending(messages, tracker, on_event, decisions)
            if paused is not None:
                return paused

            # 每次准备调模型前，先看还没超限
            stop = tracker.before_llm()
            if stop:
                return self._stopped(tracker, stop, f"循环在调用模型前停下：{stop}", on_event)

            tracker.record_iteration()
            self._log(f"→ iteration {tracker.iterations}")

            # 网页入口带着 on_event，模型输出按 token 推出去。
            # 命令行没有监听者，仍然等这一次调用完整返回。
            if on_event is not None:
                self._emit(on_event, "step_start", name="调用模型")
            try:
                turn = (
                    self._complete_stream(messages, tracker, on_event)
                    if on_event is not None
                    else self._complete(messages, tracker)
                )
            finally:
                if on_event is not None:
                    self._emit(on_event, "step_end", name="调用模型")
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

                arguments = call.arguments or "{}"
                if self._needs_confirm(call.name, on_event) and call.id not in decisions:
                    self._emit(
                        on_event,
                        "tool_start",
                        id=call.id,
                        name=call.name,
                        arguments=arguments,
                        parent=turn.message_id,
                    )
                    self._log(f"  等待确认 {call.name}")
                    return self._approval(tracker, call)

                if call.name in _CONFIRM_TOOLS and on_event is None and call.id not in decisions:
                    decisions[call.id] = _cli_approved(call.name, arguments)

                step_name = f"执行 {call.name or '工具'}"
                self._emit(on_event, "step_start", name=step_name)
                self._emit(
                    on_event,
                    "tool_start",
                    id=call.id,
                    name=call.name,
                    arguments=arguments,
                    parent=turn.message_id,
                )
                try:
                    if call.name in _CONFIRM_TOOLS and not decisions.get(call.id, True):
                        result = _rejected(call.name)
                    else:
                        result = self.tools.execute(call.name, call.arguments)
                except Exception:
                    self._emit(on_event, "step_end", name=step_name)
                    raise
                tracker.record_tool_call()
                self._log(f"  ok {call.name}({call.arguments}) => {result}")
                self._emit(
                    on_event,
                    "tool_result",
                    id=call.id,
                    name=call.name,
                    result=result,
                )
                self._emit(on_event, "step_end", name=step_name)

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

    def _apply_pending(
        self,
        messages: list[dict[str, Any]],
        tracker: LimitTracker,
        on_event: Callable[[str, dict[str, str]], None] | None,
        decisions: dict[str, bool],
    ) -> AgentResult | None:
        """把还没有结果的工具调用补完。需要确认且没有答复时，停在这里。"""
        for call in _unanswered_calls(messages):
            if self._needs_confirm(call.name, on_event) and call.id not in decisions:
                self._log(f"  仍在等待确认 {call.name}")
                return self._approval(tracker, call)

            approved = decisions.pop(call.id, call.name not in _CONFIRM_TOOLS)
            if not approved:
                result = _rejected(call.name)
            else:
                step_name = f"执行 {call.name or '工具'}"
                self._emit(on_event, "step_start", name=step_name)
                try:
                    result = self.tools.execute(call.name, call.arguments)
                except Exception:
                    self._emit(on_event, "step_end", name=step_name)
                    raise
                self._emit(on_event, "step_end", name=step_name)
            tracker.record_tool_call()
            self._log(f"  ok {call.name}({call.arguments}) => {result}")
            self._emit(
                on_event,
                "tool_result",
                id=call.id,
                name=call.name,
                result=result,
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": result,
                }
            )
        return None

    def _needs_confirm(
        self,
        name: str,
        on_event: Callable[[str, dict[str, str]], None] | None,
    ) -> bool:
        return name in _CONFIRM_TOOLS and on_event is not None

    def _approval(self, tracker: LimitTracker, call: "_ToolCall") -> AgentResult:
        arguments = call.arguments or "{}"
        return AgentResult(
            text="",
            stop_reason="interrupt",
            usage=tracker.snapshot(),
            approval=PendingApproval(
                interrupt_id=str(uuid.uuid4()),
                tool_call_id=call.id,
                name=call.name,
                arguments=arguments,
                message=_approval_message(call.name, arguments),
            ),
        )

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

    def _create_stream(
        self,
        messages: list[dict[str, Any]],
        tracker: LimitTracker,
        *,
        thinking: bool,
    ) -> Any:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "tools": self.tools.schemas(),
            "tool_choice": "auto",
            "timeout": tracker.remaining_seconds(),
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        # DeepSeek 把思考过程放在 reasoning_content，和正文分开。
        # 带 tools 的后续请求必须把这段原样带回，否则接口会 400。
        if thinking:
            kwargs["extra_body"] = {
                "thinking": {"type": "enabled"},
                "reasoning_effort": "low",
            }
        return self.client.chat.completions.create(**kwargs)

    def _complete_stream(
        self,
        messages: list[dict[str, Any]],
        tracker: LimitTracker,
        on_event: Callable[[str, dict[str, str]], None],
    ) -> "_ModelTurn":
        stream = self._create_stream(messages, tracker, thinking=True)
        try:
            iterator = iter(stream)
            first = next(iterator, _STREAM_END)
        except Exception as exc:
            if not _thinking_unsupported(exc):
                raise
            self._log(f"  思考模式不可用：{exc}")
            stream = self._create_stream(messages, tracker, thinking=False)
            return self._consume_stream(stream, tracker, on_event, thinking=False)
        return self._consume_stream(
            _Prepended(first, iterator),
            tracker,
            on_event,
            thinking=True,
        )

    def _consume_stream(
        self,
        stream: Any,
        tracker: LimitTracker,
        on_event: Callable[[str, dict[str, str]], None],
        *,
        thinking: bool,
    ) -> "_ModelTurn":
        parts: list[str] = []
        reasoning_parts: list[str] = []
        calls: dict[int, _ToolCall] = {}
        message_id = str(uuid.uuid4())
        reasoning_id: str | None = None
        text_open = False
        answer_step = False

        def close_reasoning() -> None:
            nonlocal reasoning_id
            if reasoning_id is None:
                return
            self._emit(on_event, "reasoning_end", id=reasoning_id)
            reasoning_id = None

        try:
            for chunk in stream:
                if chunk is _STREAM_END:
                    continue
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
                thought = _reasoning_text(delta)
                if thought:
                    if reasoning_id is None:
                        reasoning_id = str(uuid.uuid4())
                        self._emit(on_event, "reasoning_start", id=reasoning_id)
                    self._emit(on_event, "reasoning_delta", id=reasoning_id, delta=thought)
                    reasoning_parts.append(thought)
                if delta.content:
                    close_reasoning()
                    if not text_open:
                        self._emit(on_event, "step_start", name="生成回答")
                        answer_step = True
                        self._emit(on_event, "text_start", id=message_id)
                        text_open = True
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
        finally:
            if thinking and reasoning_id is None and not reasoning_parts:
                reasoning_id = str(uuid.uuid4())
                self._emit(on_event, "reasoning_start", id=reasoning_id)
            close_reasoning()
            if text_open:
                self._emit(on_event, "text_end", id=message_id)
            if answer_step:
                self._emit(on_event, "step_end", name="生成回答")
            if reasoning_parts:
                self._log(f"  思考 {len(''.join(reasoning_parts))} 字")
            elif thinking:
                self._log("  模型没有返回思考文本")

        ordered = [calls[index] for index in sorted(calls)]
        for call in ordered:
            if not call.id:
                call.id = str(uuid.uuid4())
        return _ModelTurn(
            content="".join(parts),
            tool_calls=ordered,
            reasoning="".join(reasoning_parts),
            message_id=message_id,
            thinking=thinking,
        )

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
    reasoning: str = ""
    message_id: str = ""
    thinking: bool = False


_STREAM_END = object()


class _Prepended:
    """把已经取出的第一个 chunk 放回流，后面继续迭代。"""

    def __init__(self, first: Any, rest: Any) -> None:
        self._items = (first, rest)

    def __iter__(self) -> Any:
        first, rest = self._items
        if first is not _STREAM_END:
            yield first
        yield from rest


def _reasoning_text(delta: Any) -> str:
    value = getattr(delta, "reasoning_content", None)
    if not isinstance(value, str) or not value:
        extra = getattr(delta, "model_extra", None) or {}
        if isinstance(extra, dict):
            value = extra.get("reasoning_content")
    return value if isinstance(value, str) else ""


def _thinking_unsupported(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    if status not in (400, 404, 422):
        return False
    text = str(exc).lower()
    return "thinking" in text or "reasoning_effort" in text or "reasoning_content" in text


def _turn_to_message(turn: _ModelTurn) -> dict[str, Any]:
    """把这一次模型回复收成 messages 里的一条 assistant。"""
    item: dict[str, Any] = {
        "role": "assistant",
        "content": turn.content or None,
    }
    if turn.thinking:
        item["reasoning_content"] = turn.reasoning
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


def _unanswered_calls(messages: list[dict[str, Any]]) -> list[_ToolCall]:
    answered = {
        message.get("tool_call_id")
        for message in messages
        if message.get("role") == "tool" and message.get("tool_call_id")
    }
    pending: list[_ToolCall] = []
    for message in messages:
        if message.get("role") != "assistant":
            continue
        for raw in message.get("tool_calls") or []:
            if not isinstance(raw, dict):
                continue
            call_id = str(raw.get("id") or "")
            if not call_id or call_id in answered:
                continue
            function = raw.get("function") or {}
            if not isinstance(function, dict):
                function = {}
            pending.append(
                _ToolCall(
                    id=call_id,
                    name=str(function.get("name") or ""),
                    arguments=str(function.get("arguments") or ""),
                )
            )
            answered.add(call_id)
    return pending


def _approval_message(name: str, arguments: str) -> str:
    if name != "add_note":
        return f"即将调用 {name}：{arguments}"
    try:
        data = json.loads(arguments or "{}")
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    title = str(data.get("title") or "").strip() or "（无标题）"
    body = str(data.get("body") or "").strip() or "（无正文）"
    return f"MCP 工具 add_note 会写入一条笔记。\n标题：{title}\n正文：{body}"


def _rejected(name: str) -> str:
    return f"用户拒绝了 {name}，没有执行。"


def _cli_approved(name: str, arguments: str) -> bool:
    print(f"\n{_approval_message(name, arguments)}", flush=True)
    try:
        answer = input("接受这次调用？[y/N] ").strip().lower()
    except EOFError:
        return False
    return answer in {"y", "yes", "接受"}
