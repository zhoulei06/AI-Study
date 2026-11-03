"""AG-UI HTTP 入口。

前端 HttpAgent 向 POST /agent 提交 RunAgentInput，响应是 SSE。
LangGraph 在线程里跑；图里的工具和回答通过队列变成 AG-UI 事件。

MCP server 占着 8000，这个接口默认 8010。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import threading

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
import uuid
from contextlib import asynccontextmanager
from typing import Any, Literal

from ag_ui.core import (
    Interrupt, RunAgentInput, RunFinishedEvent,
    RunFinishedInterruptOutcome, RunFinishedSuccessOutcome, RunStartedEvent, UserMessage,
)
from ag_ui.encoder import EventEncoder
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from .cancellation import RunCancelled, RunRegistry, check_cancelled
from .loop import Agent
from .history import ConversationHistory, HistoryRecorder
from .mcp_client import McpConnection
from .application.runs import RunService
from .adapters.agui.display import build_event_handlers
from .adapters.agui.messages import _approvals, _to_agent_messages, _text
from .adapters.agui.streaming import EventChannel, SlowSubscriber
from .runtime.retry import RetryPolicy, call_with_retry
from .errors import ApplicationError, ErrorCode, make_error, normalize_error
from .api.errors import install_error_handlers
from .adapters.agui.errors import error_event

load_dotenv()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.agent = None
    app.state.agent_status = {"status": "initializing", "error": None}
    app.state.lock = threading.Lock()
    app.state.runs = RunRegistry()
    app.state.workers = set()
    app.state.history = ConversationHistory()
    app.state.history.recover_interrupted(_owner_from_env())
    app.state.mcp_status = {"status": "connecting", "username": None, "error": None}
    app.state.shutting_down = False
    stopped = threading.Event()
    agent_ready = threading.Event()

    def initialize_agent() -> None:
        agent = None
        try:
            agent = Agent(verbose=True, thinking=True, use_mcp=False)
            with app.state.lock:
                if stopped.is_set():
                    agent.close()
                    return
                app.state.agent = agent
                app.state.agent_status = {"status": "ready", "error": None}
        except Exception as exc:
            error = normalize_error(exc, source="model")
            app.state.agent_status = {"status": "unavailable", "error": error.message, "errorInfo": error.public()}
            logger.error("Agent initialization failed request_id=%s code=%s", error.request_id, error.code.value)
        finally:
            agent_ready.set()

    threading.Thread(target=initialize_agent, name="agent-init", daemon=True).start()

    def connect_mcp() -> None:
        connection: McpConnection | None = None
        try:
            connection = call_with_retry(McpConnection,
                policy=RetryPolicy.configured("mcp_connect"), label="mcp_connect", stop_event=stopped)
            # Login can open immediately, even while embeddings are loading.
            agent_ready.wait()
            if stopped.is_set():
                connection.close()
                return
            with app.state.lock:
                if stopped.is_set():
                    connection.close()
                    return
                agent = app.state.agent
                if agent is None:
                    raise RuntimeError("Agent 初始化失败，无法挂载 MCP 工具")
                agent.attach_mcp(connection)
            app.state.mcp_status = {"status": "connected", "username": connection.username, "error": None}
        except RunCancelled:
            if connection is not None:
                connection.close()
        except Exception as exc:
            if connection is not None:
                connection.close()
            error = normalize_error(exc, source="mcp")
            app.state.mcp_status = {"status": "unavailable", "username": None, "error": error.message, "errorInfo": error.public()}
            logger.error("MCP initialization failed request_id=%s code=%s", error.request_id, error.code.value)

    mcp_worker = None
    if os.getenv("MCP_ENABLED", "true").strip().lower() not in {"0", "false", "no"}:
        mcp_worker = threading.Thread(target=connect_mcp, name="mcp-login", daemon=True)
        mcp_worker.start()
    else:
        app.state.mcp_status = {"status": "disabled", "username": None, "error": None}
    try:
        yield
    finally:
        app.state.shutting_down = True
        stopped.set()
        agent_ready.set()
        with app.state.runs.lock:
            for active in app.state.runs.active.values():
                active.cancel.set()
        agent = app.state.agent
        if agent is not None and agent.mcp is not None:
            agent.mcp.cancel_pending_elicit()
        if app.state.workers:
            await asyncio.gather(*app.state.workers, return_exceptions=True)
        app.state.history.close()
        if agent is not None:
            agent.close()


app = FastAPI(title="agent-server", lifespan=lifespan)
install_error_handlers(app)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ],
    allow_methods=["POST", "GET", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)


class ElicitAnswer(BaseModel):
    id: str
    action: Literal["accept", "decline", "cancel"]


class CancelRun(BaseModel):
    runId: str


@app.post("/agent/conversations/{thread_id}/cancel")
def cancel_conversation(thread_id: str, body: CancelRun, request: Request) -> dict[str, Any]:
    owner = _owner(request)
    history: ConversationHistory = request.app.state.history
    registry: RunRegistry = request.app.state.runs
    pending_elicit = None
    with registry.lock:
        detail = history.get(owner, thread_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="会话不存在")
        if detail.get("runId") != body.runId:
            raise HTTPException(status_code=409, detail="运行已变化，请刷新会话状态")
        active = registry.active.get((owner, thread_id))
        if active is not None and detail["status"] == "running":
            history.record(owner, thread_id, [{"type": "CANCEL_REQUESTED"}])
            active.cancel.set()
            pending_elicit = (detail.get("mcpConfirm") or {}).get("id")
        result = history.get(owner, thread_id)
    # The MCP callback also records events. Never wait for its loop while
    # holding registry.lock, and never cancel an unrelated run's confirmation.
    agent = request.app.state.agent
    if pending_elicit and agent is not None and agent.mcp is not None:
        agent.mcp.resolve_elicit(pending_elicit, "cancel")
    return result


@app.post("/agent/elicit")
def answer_elicit(body: ElicitAnswer, request: Request) -> dict[str, bool]:
    """MCP elicitation 还停在同一次工具调用里，这里把用户的接受或拒绝送回去。"""
    agent: Agent = request.app.state.agent
    if agent is None or agent.mcp is None or not agent.mcp.resolve_elicit(body.id, body.action):
        raise HTTPException(status_code=404, detail="没有等待中的 MCP 确认")
    return {"ok": True}


@app.get("/health")
def health(request: Request) -> dict[str, Any]:
    return {"status": "ok", "agent": getattr(request.app.state, "agent_status", None),
            "mcp": getattr(request.app.state, "mcp_status", None)}


@app.post("/agent")
async def agent_endpoint(run_input: RunAgentInput, request: Request) -> StreamingResponse:
    encoder = EventEncoder(accept=request.headers.get("accept"))
    agent: Agent = request.app.state.agent
    if agent is None:
        state = request.app.state.agent_status
        message = "Agent 正在初始化模型，请稍后重试；会话历史可正常使用"
        if state["status"] == "unavailable":
            message = "Agent 初始化失败，请检查服务端模型加载日志；会话历史可正常使用"
        raise HTTPException(status_code=503, detail=message)
    lock: threading.Lock = request.app.state.lock
    history: ConversationHistory = request.app.state.history
    owner = _owner(request)
    history.create(owner, run_input.thread_id)
    if history.get(owner, run_input.thread_id) is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    try:
        _to_agent_messages(run_input)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    registry: RunRegistry = request.app.state.runs
    adapter = os.getenv("AGUI_ADAPTER", "langgraph").strip().lower()
    if adapter not in {"langgraph", "legacy"}:
        raise ApplicationError(make_error(ErrorCode.CONFIGURATION_ERROR, source="api",
                                          request_id=request.state.request_id))
    forwarded = run_input.forwarded_props or {}
    resume_of = forwarded.get("resumeOf") if forwarded.get("action") == "continue" else None
    if forwarded.get("action") == "continue" and not isinstance(resume_of, str):
        raise HTTPException(status_code=400, detail="继续请求缺少上次运行 ID")
    service = RunService(history, registry, lock)
    try:
        claim = await asyncio.to_thread(service.claim, owner, run_input.thread_id, run_input.run_id,
            resume_of=resume_of, request_id=request.state.request_id, users=[
                {"id": message.id, "role": "user", "content": _text(message.content)}
                for message in run_input.messages if isinstance(message, UserMessage)
            ])
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    active, run_context = claim.active, claim.persisted
    channel = EventChannel()
    recorder = HistoryRecorder(history, owner, run_input.thread_id, run_input.run_id, flush_each=True)

    partial_output = False

    def emit(event: Any) -> None:
        nonlocal partial_output
        if event is not None and event.type == "TEXT_MESSAGE_CONTENT" and event.delta:
            partial_output = True
        if event is not None:
            with registry.lock:
                recorder.emit(event)
        channel.publish(event, recorder.last_sequence)

    observe, on_elicit, protocol_event = build_event_handlers(emit)

    def work() -> None:
        try:
            emit(RunStartedEvent(thread_id=run_input.thread_id, run_id=run_input.run_id))
            check_cancelled(active.cancel)
            # The persisted conversation owns continuation text and messages;
            # a browser cannot replace them with an out-of-date local prefix.
            persisted_input = RunAgentInput.model_validate({
                **run_input.model_dump(mode="json", by_alias=True), "messages": run_context["messages"],
            })
            messages = _to_agent_messages(persisted_input)
            result = service.execute(
                claim, agent, messages, approvals=_approvals(run_input),
                observe=observe, protocol_sink=protocol_event if adapter == "langgraph" else None, on_elicit=on_elicit,
                continuing=resume_of is not None,
            )
            if getattr(result, "error", None) is not None:
                emit(error_event(normalize_error(ApplicationError(result.error),
                    request_id=request.state.request_id, run_id=run_input.run_id,
                    thread_id=run_input.thread_id, partial=partial_output)))
            elif result.approval is not None:
                check_cancelled(active.cancel)
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
        except RunCancelled:
            emit(RunFinishedEvent(
                thread_id=run_input.thread_id, run_id=run_input.run_id,
                outcome=RunFinishedInterruptOutcome(type="interrupt", interrupts=[
                    Interrupt(id=run_input.run_id, reason="user_cancelled", message="已取消，可从检查点继续")
                ]),
            ))
        except Exception as exc:
            error = normalize_error(exc, source="runtime", request_id=request.state.request_id,
                                    run_id=run_input.run_id, thread_id=run_input.thread_id, partial=partial_output)
            logger.error("Run failed request_id=%s run_id=%s code=%s type=%s", error.request_id,
                         run_input.run_id, error.code.value, type(exc).__name__)
            try:
                emit(error_event(error))
            except Exception:
                # If persistence itself failed, terminate the subscription too;
                # never leave the caller waiting on a lost worker.
                channel.publish(error_event(make_error(ErrorCode.PERSISTENCE_FAILED, source="history",
                    request_id=request.state.request_id, run_id=run_input.run_id, thread_id=run_input.thread_id)))
                logger.error("Run %s could not persist failure (%s)", run_input.run_id, type(exc).__name__)
        finally:
            try:
                recorder.flush()
            finally:
                try:
                    service.release(claim, agent)
                finally:
                    channel.publish(None)


    worker = asyncio.create_task(asyncio.to_thread(work))
    # Keep a strong reference until completion even if SSE disconnects.
    request.app.state.workers.add(worker)
    def worker_finished(task):
        request.app.state.workers.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.error("Run %s worker failed (%s)", run_input.run_id, type(task.exception()).__name__)
    worker.add_done_callback(worker_finished)

    async def event_stream():
        try:
            while True:
                try:
                    sequence, event = await asyncio.wait_for(channel.queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if event is None:
                    break
                if isinstance(event, SlowSubscriber):
                    yield encoder.encode(error_event(make_error(ErrorCode.STREAM_INTERRUPTED, source="transport",
                        request_id=request.state.request_id, run_id=run_input.run_id, thread_id=run_input.thread_id, partial=True)))
                    break
                yield f"id: {run_input.run_id}:{sequence}\n" + encoder.encode(event)
        finally:
            channel.close()

    return StreamingResponse(event_stream(), media_type=encoder.get_content_type())


@app.get("/agent/conversations/{thread_id}/runs/{run_id}/events")
async def replay_events(thread_id: str, run_id: str, request: Request, after: int = 0):
    """Replay committed events, then follow a running generation using SSE ids."""
    if after < 0:
        raise HTTPException(400, "after 必须为非负整数")
    last_id = request.headers.get("last-event-id")
    if last_id:
        try:
            generation, sequence = last_id.rsplit(":", 1)
            if generation != run_id or int(sequence) < 0:
                raise ValueError()
            after = max(after, int(sequence))
        except ValueError:
            raise HTTPException(400, "Last-Event-ID 与当前运行不匹配")
    history = request.app.state.history
    owner = _owner(request)
    detail = await asyncio.to_thread(history.get, owner, thread_id)
    if detail is None:
        raise HTTPException(404, "会话不存在")
    if run_id != detail.get("runId") and run_id not in detail.get("runIds", []):
        raise HTTPException(404, "运行不存在")

    async def stream():
        cursor = after
        while not await request.is_disconnected():
            rows = await asyncio.to_thread(history.events, owner, thread_id, run_id, cursor)
            for row in rows:
                cursor = row["seq"]
                yield f"id: {run_id}:{cursor}\ndata: {json.dumps(row['event'], ensure_ascii=False)}\n\n"
            if rows:
                continue
            current = await asyncio.to_thread(history.get, owner, thread_id)
            if current is None or current.get("runId") != run_id or current["status"] != "running":
                # Recheck after observing terminal state to close the read race.
                tail = await asyncio.to_thread(history.events, owner, thread_id, run_id, cursor)
                for row in tail:
                    cursor = row["seq"]
                    yield f"id: {run_id}:{cursor}\ndata: {json.dumps(row['event'], ensure_ascii=False)}\n\n"
                return
            await asyncio.sleep(0.25)
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


def _owner(request: Request) -> str:
    return _owner_from_env()


def _owner_from_env() -> str:
    # This demo has no application login. Keep browser history identity stable
    # when MCP authentication is pending or changes between users.
    return os.getenv("AGENT_HISTORY_OWNER", "local").strip() or "local"


@app.get("/agent/mcp/status")
def mcp_status(request: Request) -> dict[str, Any]:
    return request.app.state.mcp_status


@app.get("/agent/conversations")
def list_conversations(request: Request) -> list[dict[str, Any]]:
    return request.app.state.history.list(_owner(request))


@app.post("/agent/conversations")
def create_conversation(request: Request) -> dict[str, Any]:
    thread_id = str(uuid.uuid4())
    history: ConversationHistory = request.app.state.history
    history.create(_owner(request), thread_id)
    return history.get(_owner(request), thread_id)


@app.get("/agent/conversations/{thread_id}")
def get_conversation(thread_id: str, request: Request) -> dict[str, Any]:
    result = request.app.state.history.get(_owner(request), thread_id)
    if result is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return result


@app.delete("/agent/conversations/{thread_id}")
def delete_conversation(thread_id: str, request: Request) -> dict[str, bool]:
    try:
        deleted = request.app.state.history.delete(_owner(request), thread_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"ok": True}


def main() -> None:
    import uvicorn

    host = os.getenv("AGENT_HOST", "127.0.0.1")
    port = int(os.getenv("AGENT_PORT", "8010"))
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
