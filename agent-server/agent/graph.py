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
from .errors import ErrorCode, ApplicationError, make_error
from .runtime.context import LimitStop, current_run
from .runtime.models import configure_model
from .runtime.retry import (
    RetryPolicy,
    call_with_retry,
    acall_with_retry,
    current_attempt,
    is_transient,
    reserve_model_attempt,
)
from .memory.short_term import SessionState

Log = Callable[[str], None]


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
    """Stateless middleware: each graph invocation reads its own RunContext."""

    def before_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        context = current_run()
        if context is not None:
            context.check()
        return None

    async def abefore_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        return self.before_model(state, runtime)

    def _request(self, request: ModelRequest) -> ModelRequest:
        context = current_run()
        settings = {}
        if context is not None:
            context.check()
            size = sum(
                len(str(message.content))
                + len(
                    json.dumps(getattr(message, "tool_calls", []), ensure_ascii=False)
                )
                for message in request.messages
            )
            size += len(str(getattr(request.system_message, "content", "")))
            size += sum(len(str(getattr(tool, "args", tool))) for tool in request.tools)
            remaining = (
                context.tracker.limits.token_budget - context.tracker.total_tokens
            )
            if size >= remaining:
                raise LimitStop(
                    "token_budget", "输入超过本次运行剩余上下文预算，请缩短输入"
                )
            if hasattr(request.model, "request_timeout"):
                settings["max_tokens"] = min(
                    getattr(request.model, "max_tokens", None) or remaining,
                    remaining - size,
                )
        attempt = current_attempt()
        timeout = attempt.timeout if attempt is not None else 15
        return request.override(
            model=configure_model(request.model, timeout=timeout, **settings)
        )

    def _retry_decision(self, holder, exc):
        request = holder[0]
        if _thinking_unsupported(exc) and getattr(request.model, "extra_body", None):
            holder[0] = request.override(
                model=request.model.model_copy(update={"extra_body": None})
            )
            context = current_run()
            if context is not None:
                context.log("思考模式不可用，下一次尝试使用普通模式")
            return True
        return is_transient(exc)

    def _after(self, response: Any) -> Any:
        context = current_run()
        if context is not None:
            _record_usage(response, context.tracker)
            context.check()
        return response

    def wrap_model_call(self, request: ModelRequest, handler: Callable) -> Any:
        holder = [request]
        response = call_with_retry(
            lambda: handler(self._request(holder[0])),
            policy=RetryPolicy.configured("model"),
            label="model",
            before_attempt=reserve_model_attempt,
            retry_if=lambda exc: self._retry_decision(holder, exc),
        )
        return self._after(response)

    async def awrap_model_call(self, request: ModelRequest, handler: Callable) -> Any:
        holder = [request]
        response = await acall_with_retry(
            lambda: handler(self._request(holder[0])),
            policy=RetryPolicy.configured("model"),
            label="model",
            before_attempt=reserve_model_attempt,
            retry_if=lambda exc: self._retry_decision(holder, exc),
        )
        return self._after(response)

    def _before_tool(self) -> None:
        context = current_run()
        if context is not None:
            context.check()
            reason = context.tracker.start_tool()
            if reason:
                raise LimitStop(reason, f"执行工具前达到限制：{reason}")

    def wrap_tool_call(self, request: Any, handler: Callable) -> Any:
        self._before_tool()
        # A completed tool must be checkpointed even if cancellation arrives
        # during the call. The next boundary checks cancellation.
        return handler(request)

    async def awrap_tool_call(self, request: Any, handler: Callable) -> Any:
        self._before_tool()
        return await handler(request)


def build_model(*, thinking: bool, timeout: float) -> ReasoningDeepSeek:
    api_key = (
        os.getenv("DEEPSEEK_API_KEY", "").strip()
        or os.getenv("OPENAI_API_KEY", "").strip()
    )
    if not api_key:
        raise ApplicationError(make_error(ErrorCode.CONFIGURATION_ERROR, source="model"))
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
        max_retries=0,
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
    planning: AgentMiddleware | None = None,
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
        # Inspect the final prompt after plan/memory additions at the budget boundary.
        middleware=([planning, *memory_middleware, budget, confirm] if planning
                    else [budget, *memory_middleware, confirm]),
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


def _record_usage(
    response: ModelResponse | AIMessage | BaseMessage, tracker: LimitTracker | None
) -> None:
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
    return (
        "thinking" in text or "reasoning_effort" in text or "reasoning_content" in text
    )
