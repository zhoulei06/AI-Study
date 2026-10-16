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
    RunAgentInput,
    RunErrorEvent,
    RunFinishedEvent,
    RunFinishedSuccessOutcome,
    RunStartedEvent,
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
            elif kind == "tool":
                _emit_tool(emit, payload)
                card = _weather_card(payload.get("name", ""), payload.get("result", ""))
                if card is not None:
                    emit(
                        ActivitySnapshotEvent(
                            message_id=str(uuid.uuid4()),
                            activity_type="card",
                            content=card,
                        )
                    )

        def work() -> None:
            try:
                with lock:
                    agent.run_messages(
                        messages,
                        tracker=LimitTracker(agent.limits, agent.model),
                        on_event=observe,
                    )
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
                emit(None)

        worker = asyncio.create_task(asyncio.to_thread(work))
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield encoder.encode(event)
        finally:
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


def _emit_tool(emit: Any, payload: dict[str, str]) -> None:
    tool_call_id = payload["id"]
    emit(ToolCallStartEvent(tool_call_id=tool_call_id, tool_call_name=payload["name"]))
    arguments = payload.get("arguments") or "{}"
    emit(ToolCallArgsEvent(tool_call_id=tool_call_id, delta=arguments))
    emit(ToolCallEndEvent(tool_call_id=tool_call_id))
    emit(
        ToolCallResultEvent(
            message_id=str(uuid.uuid4()),
            tool_call_id=tool_call_id,
            content=payload.get("result") or "",
            role="tool",
        )
    )


def _to_openai_messages(run_input: RunAgentInput, system_prompt: str) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    for message in run_input.messages:
        if isinstance(message, UserMessage):
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
