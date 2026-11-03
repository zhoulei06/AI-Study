"""AG-UI inputs to server-side model messages and approval decisions."""

import json
from typing import Any
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from ag_ui.core import (
    AssistantMessage,
    ReasoningMessage,
    RunAgentInput,
    ToolMessage as AGUIToolMessage,
    UserMessage,
)


def _approvals(run_input: RunAgentInput) -> dict[str, bool]:
    """resume 里的每一条对应一次工具确认。取消或未接受都视为拒绝。"""
    found: dict[str, bool] = {}
    for entry in run_input.resume or []:
        payload = entry.payload if isinstance(entry.payload, dict) else {}
        tool_call_id = str(payload.get("toolCallId") or "")
        if not tool_call_id:
            continue
        found[tool_call_id] = entry.status != "cancelled" and bool(
            payload.get("approved")
        )
    return found


def _to_agent_messages(run_input: RunAgentInput) -> list[BaseMessage]:
    messages: list[BaseMessage] = []
    # 带工具的思考模式要求下一轮把 reasoning_content 放回对应的 assistant。
    pending_reasoning: list[str] = []
    for message in run_input.messages:
        if isinstance(message, ReasoningMessage):
            text = message.content or ""
            if (
                messages
                and isinstance(messages[-1], AIMessage)
                and "reasoning_content" not in messages[-1].additional_kwargs
            ):
                messages[-1].additional_kwargs["reasoning_content"] = text
            else:
                pending_reasoning.append(text)
        elif isinstance(message, UserMessage):
            messages.append(HumanMessage(id=message.id, content=_text(message.content)))
        elif isinstance(message, AssistantMessage):
            tool_calls = []
            for call in message.tool_calls or []:
                raw = call.function.arguments or "{}"
                try:
                    args = json.loads(raw)
                except json.JSONDecodeError:
                    args = {}
                if not isinstance(args, dict):
                    args = {}
                tool_calls.append(
                    {"id": call.id, "name": call.function.name, "args": args}
                )
            extra: dict[str, Any] = {}
            if pending_reasoning:
                extra["reasoning_content"] = "\n".join(pending_reasoning)
                pending_reasoning.clear()
            messages.append(
                AIMessage(
                    content=message.content or "",
                    tool_calls=tool_calls,
                    additional_kwargs=extra,
                )
            )
        elif isinstance(message, AGUIToolMessage):
            messages.append(
                ToolMessage(
                    content=_text(message.content),
                    tool_call_id=message.tool_call_id,
                )
            )
    if not any(isinstance(item, HumanMessage) for item in messages):
        raise ValueError("没有可处理的用户消息")
    return messages


def _text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for part in content:
        text = getattr(part, "text", None)
        if isinstance(text, str) and text:
            parts.append(text)
    return "\n".join(parts)
