"""AG-UI HTTP 入口。

前端 HttpAgent 向 POST /agent 提交 RunAgentInput，响应是 SSE。
Agent 循环仍是同步的，放在线程里跑；循环里的工具和回答通过队列变成 AG-UI 事件。

MCP server 占着 8000，这个接口默认 8010。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
import uuid
from contextlib import asynccontextmanager
from typing import Any

from ag_ui.core import (
    ActivitySnapshotEvent,
    AssistantMessage,
    Interrupt,
    ReasoningEndEvent,
    ReasoningMessage,
    ReasoningMessageContentEvent,
    ReasoningMessageEndEvent,
    ReasoningMessageStartEvent,
    ReasoningStartEvent,
    RunAgentInput,
    RunErrorEvent,
    RunFinishedEvent,
    RunFinishedInterruptOutcome,
    RunFinishedSuccessOutcome,
    RunStartedEvent,
    StepFinishedEvent,
    StepStartedEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
    ToolMessage,
    UserMessage,
)
from ag_ui.encoder import EventEncoder
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from .limits import LimitTracker
from .loop import Agent

load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):
    agent = Agent(verbose=True)
    app.state.agent = agent
    app.state.lock = threading.Lock()
    try:
        yield
    finally:
        agent.close()


app = FastAPI(title="agent-demo", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ],
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["*"],
)


class ElicitAnswer(BaseModel):
    id: str
    action: str


@app.post("/agent/elicit")
def answer_elicit(body: ElicitAnswer, request: Request) -> dict[str, bool]:
    """MCP elicitation 还停在同一次工具调用里，这里把用户的接受或拒绝送回去。"""
    agent: Agent = request.app.state.agent
    if agent.mcp is None or not agent.mcp.resolve_elicit(body.id, body.action):
        raise HTTPException(status_code=404, detail="没有等待中的 MCP 确认")
    return {"ok": True}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/agent")
async def agent_endpoint(run_input: RunAgentInput, request: Request) -> StreamingResponse:
    encoder = EventEncoder(accept=request.headers.get("accept"))
    agent: Agent = request.app.state.agent
    lock: threading.Lock = request.app.state.lock
    try:
        messages = _to_openai_messages(run_input, agent.system_prompt)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    async def event_stream():
        yield encoder.encode(
            RunStartedEvent(thread_id=run_input.thread_id, run_id=run_input.run_id)
        )
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Any] = asyncio.Queue()

        def emit(event: Any) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, event)

        def observe(kind: str, payload: dict[str, str]) -> None:
            if kind == "text_start":
                emit(TextMessageStartEvent(message_id=payload["id"], role="assistant"))
            elif kind == "text_delta" and payload.get("delta"):
                emit(TextMessageContentEvent(message_id=payload["id"], delta=payload["delta"]))
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

        def work() -> None:
            if agent.mcp is not None:
                agent.mcp.set_elicit_ui(on_elicit)
            try:
                with lock:
                    result = agent.run_messages(
                        messages,
                        tracker=LimitTracker(agent.limits, agent.model),
                        on_event=observe,
                        approvals=_approvals(run_input),
                    )
                if result.approval is not None:
                    approval = result.approval
                    emit(
                        RunFinishedEvent(
                            thread_id=run_input.thread_id,
                            run_id=run_input.run_id,
                            outcome=RunFinishedInterruptOutcome(
                                type="interrupt",
                                interrupts=[
                                    Interrupt(
                                        id=approval.interrupt_id,
                                        reason="tool_approval",
                                        message=approval.message,
                                        tool_call_id=approval.tool_call_id,
                                    )
                                ],
                            ),
                        )
                    )
                else:
                    emit(
                        RunFinishedEvent(
                            thread_id=run_input.thread_id,
                            run_id=run_input.run_id,
                            outcome=RunFinishedSuccessOutcome(type="success"),
                        )
                    )
            except Exception as exc:
                emit(RunErrorEvent(message=str(exc)))
            finally:
                if agent.mcp is not None:
                    agent.mcp.set_elicit_ui(None)
                emit(None)

        worker = asyncio.create_task(asyncio.to_thread(work))
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield encoder.encode(event)
        finally:
            if agent.mcp is not None:
                agent.mcp.cancel_pending_elicit()
            await worker

    return StreamingResponse(event_stream(), media_type=encoder.get_content_type())


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


def _approvals(run_input: RunAgentInput) -> dict[str, bool]:
    """resume 里的每一条对应一次工具确认。取消或未接受都视为拒绝。"""
    found: dict[str, bool] = {}
    for entry in run_input.resume or []:
        payload = entry.payload if isinstance(entry.payload, dict) else {}
        tool_call_id = str(payload.get("toolCallId") or "")
        if not tool_call_id:
            continue
        found[tool_call_id] = entry.status != "cancelled" and bool(payload.get("approved"))
    return found


def _to_openai_messages(run_input: RunAgentInput, system_prompt: str) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    # 带工具的思考模式要求下一轮把 reasoning_content 放回对应的 assistant。
    pending_reasoning: list[str] = []
    for message in run_input.messages:
        if isinstance(message, ReasoningMessage):
            text = message.content or ""
            if messages and messages[-1]["role"] == "assistant" and "reasoning_content" not in messages[-1]:
                messages[-1]["reasoning_content"] = text
            else:
                pending_reasoning.append(text)
        elif isinstance(message, UserMessage):
            messages.append({"role": "user", "content": _text(message.content)})
        elif isinstance(message, AssistantMessage):
            item: dict[str, Any] = {"role": "assistant", "content": message.content or None}
            if message.tool_calls:
                item["tool_calls"] = [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.function.name,
                            "arguments": call.function.arguments or "{}",
                        },
                    }
                    for call in message.tool_calls
                ]
            if pending_reasoning:
                item["reasoning_content"] = "\n".join(pending_reasoning)
                pending_reasoning.clear()
            messages.append(item)
        elif isinstance(message, ToolMessage):
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": message.tool_call_id,
                    "content": _text(message.content),
                }
            )
    if len(messages) == 1:
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


def main() -> None:
    import uvicorn

    host = os.getenv("AGENT_HOST", "127.0.0.1")
    port = int(os.getenv("AGENT_PORT", "8010"))
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
