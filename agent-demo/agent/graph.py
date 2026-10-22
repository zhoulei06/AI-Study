"""LangGraph agent：模型节点和工具节点，外加确认与限额。

`create_agent` 编出来的图就是原来手写的那个循环：

    model → 有 tool_calls？ → tools → model
                      ↘ 没有 → 结束

`add_note` 在工具执行前打断，等用户接受或拒绝后再继续。
限额在每次调模型、每次执行工具之前检查。
"""

from __future__ import annotations

import json
import os
from typing import Any, Callable

from langchain.agents import create_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    HumanInTheLoopMiddleware,
    ModelRequest,
    ModelResponse,
)
from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.tools import BaseTool
from langchain_deepseek import ChatDeepSeek
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.store.base import BaseStore

from .limits import LimitTracker
from .memory.short_term import SessionState

Log = Callable[[str], None]


class LimitStop(Exception):
    """限额用完。图停在这里，由调用方写成一条停止说明。"""

    def __init__(self, reason: str, hint: str) -> None:
        super().__init__(hint)
        self.reason = reason
        self.hint = hint


class ReasoningDeepSeek(ChatDeepSeek):
    """把思考文本带回下一轮请求。

    DeepSeek 开了思考模式之后，带工具的后续请求必须带上 reasoning_content，
    否则接口会 400。官方集成只把它留在 additional_kwargs 里，这里补回请求体。
    """

    def _get_request_payload(
        self,
        input_: LanguageModelInput,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        assistants = [
            message
            for message in self._convert_input(input_).to_messages()
            if isinstance(message, AIMessage)
        ]
        index = 0
        for raw in payload.get("messages") or []:
            if raw.get("role") != "assistant":
                continue
            if index >= len(assistants):
                break
            message = assistants[index]
            index += 1
            reasoning = _reasoning_of(message)
            if reasoning:
                raw["reasoning_content"] = reasoning
        return payload


class BudgetMiddleware(AgentMiddleware):
    """六个限额。每次 run 之前换上那一轮的 LimitTracker。"""

    def __init__(self) -> None:
        self.tracker: LimitTracker | None = None
        self.log: Log = lambda _line: None

    def before_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        tracker = self.tracker
        if tracker is None:
            return None
        stop = tracker.before_llm()
        if stop:
            raise LimitStop(stop, f"循环在调用模型前停下：{stop}")
        tracker.record_iteration()
        self.log(f"→ iteration {tracker.iterations}")
        return None

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse | AIMessage:
        try:
            response = handler(request)
        except LimitStop:
            raise
        except Exception as exc:
            if not _thinking_unsupported(exc) or request.model.extra_body is None:
                raise
            self.log(f"  思考模式不可用：{exc}")
            plain = request.model.model_copy(update={"extra_body": None})
            response = handler(request.override(model=plain))
        _record_usage(response, self.tracker)
        return response

    def wrap_tool_call(self, request: Any, handler: Callable[..., Any]) -> Any:
        tracker = self.tracker
        if tracker is not None:
            stop = tracker.before_tool()
            if stop:
                raise LimitStop(stop, f"循环在执行工具前停下：{stop}")
        result = handler(request)
        if tracker is not None:
            tracker.record_tool_call()
            name = request.tool_call.get("name") or "工具"
            self.log(f"  ok {name}({json.dumps(request.tool_call.get('args') or {}, ensure_ascii=False)})")
        return result


def build_model(*, thinking: bool, timeout: float) -> ReasoningDeepSeek:
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip() or os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "没有找到 DEEPSEEK_API_KEY。请在 agent-demo/.env 里填入 DeepSeek 的 key。"
        )
    model_name = (
        os.getenv("DEEPSEEK_MODEL", "").strip()
        or os.getenv("OPENAI_MODEL", "").strip()
        or "deepseek-chat"
    )
    base_url = (
        os.getenv("DEEPSEEK_BASE_URL", "").strip()
        or os.getenv("OPENAI_BASE_URL", "").strip()
        or "https://api.deepseek.com"
    )
    extra_body = None
    if thinking:
        extra_body = {"thinking": {"type": "enabled"}, "reasoning_effort": "low"}
    return ReasoningDeepSeek(
        model=model_name,
        api_key=api_key,
        api_base=base_url,
        timeout=timeout,
        max_retries=1,
        stream_usage=True,
        extra_body=extra_body,
    )


def compile_agent(
    *,
    model: ChatDeepSeek,
    tools: list[BaseTool],
    system_prompt: str,
    budget: BudgetMiddleware,
    checkpointer: BaseCheckpointSaver,
    store: BaseStore,
    memory_middleware: list[AgentMiddleware],
) -> CompiledStateGraph:
    confirm = HumanInTheLoopMiddleware(
        interrupt_on={
            "add_note": {
                "allowed_decisions": ["approve", "reject"],
                "description": _describe_add_note,
            }
        }
    )
    return create_agent(
        model=model,
        tools=tools,
        system_prompt=system_prompt,
        middleware=[budget, *memory_middleware, confirm],
        checkpointer=checkpointer,
        store=store,
        state_schema=SessionState,
    )


def approval_message(name: str, arguments: str) -> str:
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


def rejected_message(name: str) -> str:
    return f"用户拒绝了 {name}，没有执行。"


def _describe_add_note(tool_call: dict[str, Any], _state: Any, _runtime: Any) -> str:
    arguments = json.dumps(tool_call.get("args") or {}, ensure_ascii=False)
    return approval_message(str(tool_call.get("name") or "add_note"), arguments)


def _reasoning_of(message: AIMessage) -> str:
    extra = message.additional_kwargs or {}
    value = extra.get("reasoning_content")
    if isinstance(value, str) and value:
        return value
    content = message.content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "reasoning":
            text = block.get("reasoning") or block.get("text") or ""
            if isinstance(text, str) and text:
                parts.append(text)
    return "".join(parts)


def _record_usage(response: ModelResponse | AIMessage | BaseMessage, tracker: LimitTracker | None) -> None:
    if tracker is None:
        return
    messages: list[Any]
    if isinstance(response, ModelResponse):
        messages = list(response.result)
    else:
        messages = [response]
    for message in messages:
        usage = getattr(message, "usage_metadata", None) or {}
        prompt = int(usage.get("input_tokens") or 0)
        completion = int(usage.get("output_tokens") or 0)
        if prompt or completion:
            tracker.record_usage(prompt_tokens=prompt, completion_tokens=completion)


def _thinking_unsupported(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    if status not in (400, 404, 422):
        return False
    text = str(exc).lower()
    return "thinking" in text or "reasoning_effort" in text or "reasoning_content" in text
