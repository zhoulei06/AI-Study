"""Durable display history, independent of compacted graph checkpoints."""
from __future__ import annotations

import threading
import time
from typing import Any

from psycopg.types.json import Jsonb
from langgraph.checkpoint.postgres import PostgresSaver

from .memory.postgres import database_url, open_pool
from .persistence.migrate import migrate


def finish_answer(doc: dict[str, Any], status: str) -> None:
    for entry in doc["entries"]:
        if entry["kind"] == "assistant" and entry.get("activeGenerationId") == doc.get("runId"):
            entry.update(status=status, activeGenerationId=None, streaming=False)


def partial_answer(doc: dict[str, Any]) -> dict[str, str] | None:
    """Only the interrupted answer from the latest user turn can be continued."""
    for entry in reversed(doc["entries"]):
        if entry["kind"] in {"user", "tool"}:
            return None
        if entry["kind"] == "assistant" and entry.get("text"):
            if entry.get("status", "cancelled") in {"cancelled", "generating"}:
                return {"message_id": entry["id"], "text": entry["text"]}
            return None
    return None


def apply_event(doc: dict[str, Any], event: dict[str, Any]) -> None:
    generation = event.get("generationId")
    if generation is not None and generation != doc.get("runId"):
        return
    kind = event["type"]
    entries, messages = doc["entries"], doc["messages"]
    mid = event.get("messageId")
    entry = next((x for x in entries if x["id"] == mid), None)
    message = next((x for x in messages if x["id"] == mid), None)
    if kind == "RUN_STARTED":
        doc.update(status="running", confirm=None, mcpConfirm=None, error="", errorInfo=None, cancelRequested=False)
    elif kind == "CANCEL_REQUESTED":
        doc["cancelRequested"] = True
    elif kind == "TEXT_MESSAGE_START":
        if entry is None:
            entry = dict(kind="assistant", id=mid, text="", streaming=False)
            entries.append(entry)
        entry.update(status="generating", activeGenerationId=generation or doc.get("runId"))
        if message is None:
            messages.append(dict(id=mid, role="assistant", content=""))
    elif kind == "TEXT_MESSAGE_CONTENT":
        if entry is not None:
            entry["text"] += event["delta"]
        if message is not None:
            message["content"] += event["delta"]
    elif kind == "REASONING_MESSAGE_CONTENT":
        if entry is None:
            entry = dict(kind="reasoning", id=mid, text="", streaming=False)
            entries.append(entry)
        entry["text"] += event["delta"]
    elif kind == "TOOL_CALL_START":
        tid = event["toolCallId"]
        if not any(x["id"] == tid for x in entries):
            entries.append(dict(kind="tool", id=tid, name=event["toolCallName"], args="", result=""))
        else:
            next(x for x in entries if x["id"] == tid)["args"] = ""
        parent = event.get("parentMessageId") or f"tool-parent-{tid}"
        assistant = next((x for x in messages if x["id"] == parent), None)
        if assistant is None:
            assistant = dict(id=parent, role="assistant", content="")
            messages.append(assistant)
        calls = assistant.setdefault("toolCalls", [])
        if not any(x["id"] == tid for x in calls):
            calls.append(dict(id=tid, type="function", function=dict(name=event["toolCallName"], arguments="")))
        else:
            next(x for x in calls if x["id"] == tid)["function"]["arguments"] = ""
    elif kind == "TOOL_CALL_ARGS":
        tid = event["toolCallId"]
        tool = next((x for x in entries if x["id"] == tid), None)
        if tool is not None:
            tool["args"] += event["delta"]
        for item in messages:
            for call in item.get("toolCalls", []):
                if call["id"] == tid:
                    call["function"]["arguments"] += event["delta"]
    elif kind == "TOOL_CALL_RESULT":
        tool = next((x for x in entries if x["id"] == event["toolCallId"]), None)
        if tool is not None:
            tool["result"] = event["content"]
        if message is None:
            messages.append(dict(id=mid, role="tool", toolCallId=event["toolCallId"], content=event["content"]))
    elif kind == "ACTIVITY_SNAPSHOT":
        if event["activityType"] == "plan":
            if entry is None:
                entries.append(dict(kind="plan", id=mid, plan=event["content"]))
            else:
                entry["plan"] = event["content"]
        elif event["activityType"] == "card" and entry is None:
            entries.append(dict(kind="card", id=mid, **event["content"]))
        elif event["activityType"] == "mcp-confirm":
            doc["mcpConfirm"] = event["content"]
    elif kind == "STEP_FINISHED" and event["stepName"] == "MCP 询问确认":
        doc["mcpConfirm"] = None
    elif kind == "RUN_FINISHED":
        outcome = event.get("outcome") or {}
        doc.update(status="completed", mcpConfirm=None, cancelRequested=False)
        if outcome.get("type") == "interrupt":
            item = outcome["interrupts"][0]
            if item.get("reason") == "user_cancelled":
                finish_answer(doc, "cancelled")
                doc.update(status="cancelled", confirm=None, cancelRequested=False)
                return
            doc["status"] = "waiting"
            doc["confirm"] = dict(interruptId=item["id"], toolCallId=item.get("toolCallId"), message=item.get("message", "需要确认这次调用"))
        finish_answer(doc, "completed")
    elif kind == "RUN_ERROR":
        finish_answer(doc, "failed")
        doc.update(status="failed", mcpConfirm=None, cancelRequested=False, error=event["message"])
        doc["errorInfo"] = (event.get("metadata") or {}).get("error")


class ConversationHistory:
    def __init__(self) -> None:
        self.pool = open_pool(database_url(), min_size=1, max_size=4)
        try:
            migrate(self.pool)
        except BaseException:
            self.pool.close()
            raise

    def close(self) -> None:
        self.pool.close()

    def recover_interrupted(self, owner: str) -> None:
        """The demo has one worker process; its old in-flight runs cannot survive restart."""
        with self.pool.connection() as conn, conn.transaction():
            rows = conn.execute("""SELECT thread_id, document FROM chat_conversations
                WHERE owner=%s AND (document->>'status'='running' OR
                    (document->>'status'='interrupted' AND NOT (document ? 'runId'))) FOR UPDATE""", (owner,)).fetchall()
            for row in rows:
                doc = row["document"]
                finish_answer(doc, "cancelled")
                doc.update(status="interrupted", mcpConfirm=None, cancelRequested=False,
                           runId=doc.get("runId") or f"recovered:{row['thread_id']}",
                           error="服务已重启，上次执行已中断，可以继续生成或发送新问题。")
                doc["eventSeq"] = doc.get("eventSeq", 0) + 1
                event = dict(type="RUN_FINISHED", threadId=row["thread_id"], runId=doc["runId"],
                    outcome=dict(type="interrupt", interrupts=[dict(id=doc["runId"], reason="service_restarted",
                        message=doc["error"])]))
                conn.execute("""INSERT INTO agent_run_events(owner,thread_id,run_id,seq,event)
                    VALUES (%s,%s,%s,%s,%s)""", (owner, row["thread_id"], doc["runId"], doc["eventSeq"], Jsonb(event)))
                conn.execute("UPDATE chat_conversations SET document=%s WHERE owner=%s AND thread_id=%s",
                             (Jsonb(doc), owner, row["thread_id"]))

    def create(self, owner: str, thread_id: str) -> None:
        doc = dict(entries=[], messages=[], status="idle", confirm=None, error="")
        with self.pool.connection() as conn:
            conn.execute("INSERT INTO chat_conversations(thread_id, owner, document) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING", (thread_id, owner, Jsonb(doc)))

    def list(self, owner: str) -> list[dict[str, Any]]:
        with self.pool.connection() as conn:
            return conn.execute("SELECT thread_id AS id, title, created_at, updated_at, document->>'status' AS status FROM chat_conversations WHERE owner=%s ORDER BY updated_at DESC", (owner,)).fetchall()

    def get(self, owner: str, thread_id: str) -> dict[str, Any] | None:
        with self.pool.connection() as conn:
            row = conn.execute("SELECT thread_id AS id, title, document FROM chat_conversations WHERE owner=%s AND thread_id=%s", (owner, thread_id)).fetchone()
        return dict(id=row["id"], title=row["title"], **row["document"]) if row else None

    def delete(self, owner: str, thread_id: str) -> bool:
        # Lock the display row so a run cannot mark itself running during deletion.
        # Checkpoints and display history commit or roll back together.
        with self.pool.connection() as conn, conn.transaction():
            row = conn.execute(
                "SELECT document->>'status' AS status FROM chat_conversations WHERE owner=%s AND thread_id=%s FOR UPDATE",
                (owner, thread_id),
            ).fetchone()
            if row is None:
                return False
            if row["status"] == "running":
                raise ValueError("会话仍在执行，请等待执行结束后再删除")
            PostgresSaver(conn).delete_thread(thread_id)
            conn.execute("DELETE FROM chat_conversations WHERE owner=%s AND thread_id=%s", (owner, thread_id))
        return True

    def begin_run(self, owner: str, thread_id: str, run_id: str, *, resume_of: str | None,
                  users: list[dict[str, Any]]) -> dict[str, Any]:
        """Claim the conversation before opening SSE; never replay a run ID."""
        with self.pool.connection() as conn, conn.transaction():
            row = conn.execute(
                "SELECT document FROM chat_conversations WHERE owner=%s AND thread_id=%s FOR UPDATE",
                (owner, thread_id),
            ).fetchone()
            if row is None:
                raise ValueError("会话不存在")
            doc = row["document"]
            if run_id in doc.get("runIds", []) or doc["status"] == "running":
                raise ValueError("该请求已执行或会话仍在执行，请刷新会话状态")
            paused = doc["status"] in {"cancelled", "interrupted"}
            if paused and resume_of is None and not any(
                not any(m["id"] == u["id"] for m in doc["messages"]) for u in users
            ):
                raise ValueError("请继续回答或输入新的问题")
            if resume_of is not None and (doc["status"] not in {"cancelled", "interrupted"}
                                          or doc.get("runId") != resume_of):
                raise ValueError("上次运行状态已变化，请刷新后再继续")
            context = dict(partial=partial_answer(doc) if paused else None,
                           abandon=paused and resume_of is None)
            doc["eventSeq"] = 0
            doc["runId"] = run_id
            doc.setdefault("runIds", []).append(run_id)
            for user in users if resume_of is None else []:
                if not any(x["id"] == user["id"] for x in doc["messages"]):
                    doc["messages"].append(user)
                    doc["entries"].append(dict(kind="user", id=user["id"], text=user["content"]))
            apply_event(doc, {"type": "RUN_STARTED"})
            first = next((x for x in doc["entries"] if x["kind"] == "user"), None)
            title = first["text"].strip().replace("\n", " ")[:40] if first else "新会话"
            conn.execute("UPDATE chat_conversations SET title=%s, document=%s, updated_at=clock_timestamp() WHERE owner=%s AND thread_id=%s",
                         (title, Jsonb(doc), owner, thread_id))
            context["messages"] = doc["messages"]
            return context

    def record(self, owner: str, thread_id: str, events: list[dict[str, Any]], users: list[dict[str, Any]] | None = None,
               *, generation_id: str | None = None) -> list[int]:
        with self.pool.connection() as conn, conn.transaction():
            row = conn.execute("SELECT title, document FROM chat_conversations WHERE owner=%s AND thread_id=%s FOR UPDATE", (owner, thread_id)).fetchone()
            if row is None:
                raise ValueError("会话不存在")
            doc = row["document"]
            if generation_id is not None and doc.get("runId") != generation_id:
                return []
            sequences = []
            for user in users or []:
                if not any(x["id"] == user["id"] for x in doc["messages"]):
                    doc["messages"].append(user)
                    doc["entries"].append(dict(kind="user", id=user["id"], text=user["content"]))
            for event in events:
                if generation_id is not None:
                    doc["eventSeq"] = doc.get("eventSeq", 0) + 1
                    conn.execute("""INSERT INTO agent_run_events(owner,thread_id,run_id,seq,event)
                        VALUES (%s,%s,%s,%s,%s)""",
                        (owner, thread_id, generation_id, doc["eventSeq"], Jsonb(event)))
                    sequences.append(doc["eventSeq"])
                apply_event(doc, {**event, **({"generationId": generation_id} if generation_id is not None else {})})
            first = next((x for x in doc["entries"] if x["kind"] == "user"), None)
            title = first["text"].strip().replace("\n", " ")[:40] if first else row["title"]
            conn.execute("UPDATE chat_conversations SET title=%s, document=%s, updated_at=clock_timestamp() WHERE owner=%s AND thread_id=%s", (title, Jsonb(doc), owner, thread_id))
            return sequences

    def events(self, owner: str, thread_id: str, run_id: str, after: int = 0, limit: int = 500):
        with self.pool.connection() as conn:
            return conn.execute("""SELECT seq,event FROM agent_run_events
                WHERE owner=%s AND thread_id=%s AND run_id=%s AND seq>%s ORDER BY seq LIMIT %s""",
                (owner, thread_id, run_id, after, min(max(limit, 1), 1000))).fetchall()


class HistoryRecorder:
    def __init__(self, history: ConversationHistory, owner: str, thread_id: str, run_id: str | None = None,
                 *, flush_each: bool = False) -> None:
        self.history, self.owner, self.thread_id = history, owner, thread_id
        self.run_id = run_id
        self.pending: list[dict[str, Any]] = []
        self.lock = threading.RLock()
        self.last_flush = time.monotonic()
        self.flush_each = flush_each
        self.last_sequence = 0

    def emit(self, event: Any) -> None:
        with self.lock:
            self.pending.append(event.model_dump(mode="json", by_alias=True, exclude_none=True))
            if self.flush_each or time.monotonic() - self.last_flush >= 0.5 or event.type in ("RUN_FINISHED", "RUN_ERROR", "ACTIVITY_SNAPSHOT"):
                self.flush()

    def flush(self) -> None:
        with self.lock:
            if self.pending:
                sequences = self.history.record(self.owner, self.thread_id, self.pending, generation_id=self.run_id)
                if sequences:
                    self.last_sequence = sequences[-1]
                self.pending.clear()
            self.last_flush = time.monotonic()
