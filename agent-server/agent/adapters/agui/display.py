"""Business display events and compatibility events for text continuation."""

import json
import uuid
from typing import Any
from ag_ui.core import (
    ActivitySnapshotEvent,
    ReasoningEndEvent,
    ReasoningMessageContentEvent,
    ReasoningMessageEndEvent,
    ReasoningMessageStartEvent,
    ReasoningStartEvent,
    StepFinishedEvent,
    StepStartedEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
)


def build_event_handlers(emit):
    def observe(kind: str, payload: dict[str, str]) -> None:
        if kind == "text_start":
            emit(TextMessageStartEvent(message_id=payload["id"], role="assistant"))
        elif kind == "text_delta" and payload.get("delta"):
            emit(
                TextMessageContentEvent(
                    message_id=payload["id"], delta=payload["delta"]
                )
            )
        elif kind == "text_end":
            emit(TextMessageEndEvent(message_id=payload["id"]))
        elif kind == "assistant_text":
            _emit_text(emit, payload["text"])
        elif kind == "step_start":
            emit(StepStartedEvent(step_name=payload["name"]))
        elif kind == "step_end":
            emit(StepFinishedEvent(step_name=payload["name"]))
        elif kind == "reasoning_start":
            emit(ReasoningStartEvent(message_id=payload["id"]))
            emit(ReasoningMessageStartEvent(message_id=payload["id"], role="reasoning"))
        elif kind == "reasoning_delta" and payload.get("delta"):
            emit(
                ReasoningMessageContentEvent(
                    message_id=payload["id"],
                    delta=payload["delta"],
                )
            )
        elif kind == "reasoning_end":
            emit(ReasoningMessageEndEvent(message_id=payload["id"]))
            emit(ReasoningEndEvent(message_id=payload["id"]))
        elif kind == "mcp_elicit":
            emit(
                ActivitySnapshotEvent(
                    message_id=payload["id"],
                    activity_type="mcp-confirm",
                    content={"id": payload["id"], "message": payload["message"]},
                )
            )
        elif kind == "tool_start":
            _emit_tool_start(emit, payload)
        elif kind == "tool_result":
            _emit_tool_result(emit, payload)
            card = _weather_card(payload.get("name", ""), payload.get("result", ""))
            if card is not None:
                emit(
                    ActivitySnapshotEvent(
                        message_id=str(uuid.uuid4()),
                        activity_type="card",
                        content=card,
                    )
                )

    def on_elicit(elicit_id: str, message: str, phase: str) -> None:
        if phase == "start":
            observe("step_start", {"name": "MCP 询问确认"})
            observe("mcp_elicit", {"id": elicit_id, "message": message})
        else:
            observe("step_end", {"name": "MCP 询问确认"})

    protocol_tools: dict[str, str] = {}

    def protocol_event(event: Any) -> None:
        emit(event)
        if event.type == "TOOL_CALL_START":
            protocol_tools[event.tool_call_id] = event.tool_call_name
        elif event.type == "TOOL_CALL_RESULT":
            card = _weather_card(
                protocol_tools.get(event.tool_call_id, ""), event.content
            )
            if card is not None:
                emit(
                    ActivitySnapshotEvent(
                        message_id=str(uuid.uuid4()), activity_type="card", content=card
                    )
                )

    return observe, on_elicit, protocol_event


def _weather_card(name: str, result: str) -> dict[str, Any] | None:
    """约定 activityType=card。天气工具的真实返回值变成卡片内容。"""
    if name != "get_weather":
        return None
    try:
        data = json.loads(result)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or data.get("error") or "temp_c" not in data:
        return None
    title = str(data.get("display") or data.get("city") or "天气")
    condition = str(data.get("condition") or "")
    return {
        "title": title,
        "subtitle": condition,
        "fields": [{"label": "温度", "value": f"{data['temp_c']}°C"}],
    }


def _emit_text(emit: Any, text: str) -> None:
    if not text:
        return
    message_id = str(uuid.uuid4())
    emit(TextMessageStartEvent(message_id=message_id, role="assistant"))
    emit(TextMessageContentEvent(message_id=message_id, delta=text))
    emit(TextMessageEndEvent(message_id=message_id))


def _emit_tool_start(emit: Any, payload: dict[str, str]) -> None:
    tool_call_id = payload["id"]
    parent = payload.get("parent") or None
    emit(
        ToolCallStartEvent(
            tool_call_id=tool_call_id,
            tool_call_name=payload["name"],
            parent_message_id=parent,
        )
    )
    emit(
        ToolCallArgsEvent(
            tool_call_id=tool_call_id,
            delta=payload.get("arguments") or "{}",
        )
    )
    emit(ToolCallEndEvent(tool_call_id=tool_call_id))


def _emit_tool_result(emit: Any, payload: dict[str, str]) -> None:
    emit(
        ToolCallResultEvent(
            message_id=str(uuid.uuid4()),
            tool_call_id=payload["id"],
            content=payload.get("result") or "",
            role="tool",
        )
    )
