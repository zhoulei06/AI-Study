"""History round-trips against the configured demo PostgreSQL (no model calls)."""
import unittest
import uuid
import threading
import os
from types import SimpleNamespace
from unittest.mock import patch
import time

from ag_ui.core import RunAgentInput
from fastapi.testclient import TestClient
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.base import empty_checkpoint

from agent.history import ConversationHistory
from agent.limits import AgentLimits
from agent.cancellation import RunCancelled, RunRegistry
from agent.continuation import PartialAnswer
from agent.server import app
import agent.server as server_module


class HistoryIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.history = ConversationHistory()
        self.owner = f"history-test-{uuid.uuid4()}"
        self.owner_env = patch.dict(os.environ, {"AGENT_HISTORY_OWNER": self.owner})
        self.owner_env.start()
        app.state.agent = SimpleNamespace(mcp=SimpleNamespace(username=self.owner))
        app.state.history = self.history
        app.state.runs = RunRegistry()
        app.state.workers = set()
        app.state.lock = threading.Lock()
        # No lifespan: this exercises the API without loading embeddings or a model.
        self.client = TestClient(app)

    def tearDown(self):
        with self.history.pool.connection() as conn:
            conn.execute("DELETE FROM chat_conversations WHERE owner=%s", (self.owner,))
        self.client.close()
        self.history.close()
        self.owner_env.stop()

    def test_history_survives_reload_and_approval(self):
        created = self.client.post("/agent/conversations")
        self.assertEqual(created.status_code, 200)
        tid = created.json()["id"]
        user = dict(id="u1", role="user", content="保存一条笔记")
        events = [
            dict(type="RUN_STARTED"),
            dict(type="TEXT_MESSAGE_START", messageId="a1"),
            dict(type="TEXT_MESSAGE_CONTENT", messageId="a1", delta="正在"),
            dict(type="TEXT_MESSAGE_CONTENT", messageId="a1", delta="保存"),
            dict(type="TOOL_CALL_START", toolCallId="tc1", toolCallName="add_note", parentMessageId="a1"),
            dict(type="TOOL_CALL_ARGS", toolCallId="tc1", delta='{"title":"笔记"}'),
            dict(type="RUN_FINISHED", outcome=dict(type="interrupt", interrupts=[dict(id="i1", toolCallId="tc1", message="确认保存？")])),
        ]
        self.history.record(self.owner, tid, events, users=[user])
        # Repeated user input on approval/resume must not duplicate history.
        self.history.record(self.owner, tid, [], users=[user])
        detail = self.client.get(f"/agent/conversations/{tid}").json()
        self.assertEqual(detail["confirm"]["interruptId"], "i1")
        self.assertEqual(detail["status"], "waiting")
        self.assertEqual(detail["entries"][1]["text"], "正在保存")
        self.assertEqual(sum(x["kind"] == "user" for x in detail["entries"]), 1)
        # Restored messages must be accepted by the actual AG-UI protocol schema.
        RunAgentInput(thread_id=tid, run_id="r2", state={}, messages=detail["messages"], tools=[], context=[], forwarded_props={})
        self.history.record(self.owner, tid, [
            dict(type="RUN_STARTED"),
            dict(type="TOOL_CALL_RESULT", messageId="tr1", toolCallId="tc1", content="已保存"),
            dict(type="ACTIVITY_SNAPSHOT", messageId="card1", activityType="card", content=dict(title="北京", subtitle="晴", fields=[])),
            dict(type="RUN_FINISHED", outcome=dict(type="success")),
        ])
        other_connection = ConversationHistory()
        try:
            persisted = other_connection.get(self.owner, tid)
            self.assertEqual(persisted["entries"][2]["result"], "已保存")
            self.assertEqual(persisted["entries"][-1]["kind"], "card")
            self.assertIsNone(persisted["confirm"])
            self.assertEqual(persisted["status"], "completed")
        finally:
            other_connection.close()
        listing = self.client.get("/agent/conversations").json()
        self.assertEqual(listing[0]["title"], "保存一条笔记")
        self.assertEqual(self.client.get("/agent/conversations/missing").status_code, 404)
        app.state.agent.mcp.username = "another-owner"
        self.assertEqual(self.client.get(f"/agent/conversations/{tid}").status_code, 200)
        self.assertEqual(len(self.client.get("/agent/conversations").json()), 1)

    def test_delete_cleans_checkpoints_and_checks_owner_and_running_state(self):
        tid = self.client.post("/agent/conversations").json()["id"]
        config = {"configurable": {"thread_id": tid, "checkpoint_ns": ""}}
        saver = PostgresSaver(self.history.pool)
        saver.setup()
        saver.put(config, empty_checkpoint(), {}, {})
        self.assertIsNotNone(saver.get_tuple(config))
        app.state.agent.mcp.username = "other-owner"
        # History ownership is independent of whichever MCP account logged in.
        self.assertEqual(self.client.get(f"/agent/conversations/{tid}").status_code, 200)
        self.history.record(self.owner, tid, [dict(type="RUN_STARTED")])
        self.assertEqual(self.client.delete(f"/agent/conversations/{tid}").status_code, 409)
        self.assertIsNotNone(saver.get_tuple(config))
        self.history.record(self.owner, tid, [dict(type="RUN_FINISHED", outcome=dict(type="success"))])
        self.assertEqual(self.client.delete(f"/agent/conversations/{tid}").status_code, 200)
        self.assertIsNone(saver.get_tuple(config))
        self.assertEqual(self.client.get(f"/agent/conversations/{tid}").status_code, 404)
        self.assertEqual(self.client.get("/agent/conversations").json(), [])
        self.assertEqual(self.client.delete(f"/agent/conversations/{tid}").status_code, 404)

    def test_delete_cors_preflight(self):
        response = self.client.options("/agent/conversations/test", headers={
            "Origin": "http://127.0.0.1:5173", "Access-Control-Request-Method": "DELETE",
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn("DELETE", response.headers["access-control-allow-methods"])

    def test_continuation_history_appends_and_ignores_old_generation(self):
        tid = self.client.post("/agent/conversations").json()["id"]
        user = dict(id="u1", role="user", content="解释")
        self.history.begin_run(self.owner, tid, "g1", resume_of=None, users=[user])
        self.history.record(self.owner, tid, [
            dict(type="TEXT_MESSAGE_START", messageId="a1"),
            dict(type="TEXT_MESSAGE_CONTENT", messageId="a1", delta="已有内容"),
            dict(type="RUN_FINISHED", outcome=dict(type="interrupt", interrupts=[dict(id="g1", reason="user_cancelled")])),
        ], generation_id="g1")
        claim = self.history.begin_run(self.owner, tid, "g2", resume_of="g1", users=[
            dict(id="injected", role="user", content="不应作为新问题写入"),
        ])
        self.assertEqual(claim["partial"], dict(message_id="a1", text="已有内容"))
        self.history.record(self.owner, tid, [dict(type="RUN_ERROR", message="迟到错误")], generation_id="g1")
        self.history.record(self.owner, tid, [
            dict(type="TEXT_MESSAGE_START", messageId="a1"),
            dict(type="TEXT_MESSAGE_CONTENT", messageId="a1", delta="追加内容"),
            dict(type="RUN_FINISHED", outcome=dict(type="success")),
        ], generation_id="g2")
        detail = self.client.get(f"/agent/conversations/{tid}").json()
        self.assertEqual(detail["status"], "completed")
        self.assertEqual(detail["error"], "")
        assistants = [e for e in detail["entries"] if e["kind"] == "assistant"]
        self.assertEqual(len(assistants), 1)
        self.assertEqual(assistants[0]["text"], "已有内容追加内容")
        self.assertEqual(assistants[0]["status"], "completed")
        self.assertIsNone(assistants[0]["activeGenerationId"])
        self.assertEqual(len([m for m in detail["messages"] if m["role"] == "user"]), 1)
        self.assertEqual(next(m["content"] for m in detail["messages"] if m["id"] == "a1"), "已有内容追加内容")

    def test_cancel_continue_and_reject_stale_or_duplicate_runs(self):
        app.state.agent.mcp = None
        app.state.agent.limits = AgentLimits()
        app.state.agent.model = "test-model"
        started, release = threading.Event(), threading.Event()
        calls = []

        def run_messages(messages, *, cancel_event, resume_cancelled, on_event, **kwargs):
            calls.append(resume_cancelled)
            if not resume_cancelled:
                on_event("text_start", {"id": "partial"})
                on_event("text_delta", {"id": "partial", "delta": "部分回答"})
                started.set()
                if not cancel_event.wait(timeout=5):
                    raise RuntimeError("cancel did not arrive")
                release.wait(timeout=5)
                raise RunCancelled()
            on_event("text_start", {"id": "continued"})
            on_event("text_delta", {"id": "continued", "delta": "继续完成"})
            on_event("text_end", {"id": "continued"})
            return SimpleNamespace(approval=None)

        app.state.agent.run_messages = run_messages
        tid = self.client.post("/agent/conversations").json()["id"]
        body = dict(threadId=tid, runId="first", state={}, tools=[], context=[], forwardedProps={},
                    messages=[dict(id="user", role="user", content="请回答")])
        responses, errors = [], []

        def post_run():
            try:
                responses.append(self.client.post("/agent", json=body))
            except BaseException as exc:
                errors.append(exc)

        thread = threading.Thread(target=post_run)
        thread.start()
        try:
            self.assertTrue(started.wait(timeout=5))
            # A second request is rejected before an SSE stream is opened.
            self.assertEqual(self.client.post("/agent", json=body).status_code, 409)
            self.assertEqual(self.client.post(f"/agent/conversations/{tid}/cancel", json={"runId": "stale"}).status_code, 409)
            for _ in range(2):
                cancelled = self.client.post(f"/agent/conversations/{tid}/cancel", json={"runId": "first"})
                self.assertEqual(cancelled.status_code, 200)
                self.assertTrue(cancelled.json()["cancelRequested"])
            self.assertEqual(self.client.delete(f"/agent/conversations/{tid}").status_code, 409)
        finally:
            release.set()
            thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertIn("user_cancelled", responses[0].text)
        detail = self.client.get(f"/agent/conversations/{tid}").json()
        self.assertEqual(detail["status"], "cancelled")
        self.assertEqual(detail["entries"][-1]["text"], "部分回答")
        self.assertEqual(self.client.post(f"/agent/conversations/{tid}/cancel", json={"runId": "first"}).json()["status"], "cancelled")
        # Resume uses a new run ID, retaining the original user message ID.
        body.update(runId="second", forwardedProps={"action": "continue", "resumeOf": "first"})
        resumed = self.client.post("/agent", json=body)
        self.assertEqual(resumed.status_code, 200)
        self.assertEqual(calls, [False, True])
        detail = self.client.get(f"/agent/conversations/{tid}").json()
        self.assertEqual(detail["status"], "completed")
        self.assertEqual(sum(e["kind"] == "user" for e in detail["entries"]), 1)
        self.assertEqual(self.client.post("/agent", json=body).status_code, 409)
        body.update(runId="third")
        self.assertEqual(self.client.post("/agent", json=body).status_code, 409)
        self.assertEqual(self.client.post(f"/agent/conversations/{tid}/cancel", json={"runId": "first"}).status_code, 409)
        self.assertEqual(self.client.post("/agent/conversations/missing/cancel", json={"runId": "first"}).status_code, 404)

    def test_history_available_while_embeddings_load_or_fail(self):
        release_model = threading.Event()
        model_finished = threading.Event()
        login_started = threading.Event()

        def slow_agent(**kwargs):
            release_model.wait(timeout=5)
            model_finished.set()
            raise RuntimeError("Hugging Face SSL connection failed")

        class FakeMcp:
            username = "alice"

            def __init__(self):
                login_started.set()

            def close(self):
                pass

        with patch.object(server_module, "Agent", slow_agent), \
             patch.object(server_module, "McpConnection", FakeMcp), \
             patch.dict(os.environ, {"MCP_ENABLED": "true"}):
            try:
                with TestClient(app) as client:
                    self.assertTrue(login_started.wait(timeout=1))
                    self.assertEqual(client.get("/health").json()["agent"]["status"], "initializing")
                    tid = client.post("/agent/conversations").json()["id"]
                    self.assertEqual(client.get(f"/agent/conversations/{tid}").status_code, 200)
                    body = dict(threadId=tid, runId="loading", state={}, messages=[], tools=[], context=[], forwardedProps={})
                    self.assertEqual(client.post("/agent", json=body).status_code, 503)
                    release_model.set()
                    self.assertTrue(model_finished.wait(timeout=1))
                    for _ in range(100):
                        if app.state.agent_status["status"] == "unavailable":
                            break
                        time.sleep(0.01)
                    self.assertEqual(app.state.agent_status["status"], "unavailable")
                    self.assertEqual(client.get("/agent/conversations").status_code, 200)
                    self.assertEqual(client.post("/agent", json=body).status_code, 503)
            finally:
                release_model.set()

    def test_history_api_starts_while_mcp_login_is_pending(self):
        release_login = threading.Event()

        class FakeAgent:
            def __init__(self, **kwargs):
                self.mcp = None

            def attach_mcp(self, connection):
                self.mcp = connection

            def close(self):
                pass

        class SlowMcpLogin:
            def __init__(self):
                release_login.wait(timeout=3)
                self.username = "alice"

            def close(self):
                pass

            def cancel_pending_elicit(self):
                pass

        with patch.object(server_module, "Agent", FakeAgent), \
             patch.object(server_module, "McpConnection", SlowMcpLogin), \
             patch.dict(os.environ, {"MCP_ENABLED": "true", "AGENT_HISTORY_OWNER": self.owner}):
            try:
                with TestClient(app) as live_client:
                    self.assertEqual(app.state.mcp_status["status"], "connecting")
                    started = time.monotonic()
                    self.assertEqual(live_client.get("/health").status_code, 200)
                    self.assertLess(time.monotonic() - started, 1)
                    self.assertEqual(live_client.get("/agent/conversations").status_code, 200)
                    release_login.set()
                    for _ in range(100):
                        if app.state.mcp_status["status"] == "connected":
                            break
                        time.sleep(0.01)
                    self.assertEqual(app.state.mcp_status["status"], "connected")
            finally:
                release_login.set()

    def test_restart_preserves_partial_answer_and_clears_transient_confirmation(self):
        tid = str(uuid.uuid4())
        self.history.create(self.owner, tid)
        self.history.record(self.owner, tid, [
            dict(type="RUN_STARTED"),
            dict(type="TEXT_MESSAGE_START", messageId="a"),
            dict(type="TEXT_MESSAGE_CONTENT", messageId="a", delta="部分回答"),
            dict(type="ACTIVITY_SNAPSHOT", messageId="e", activityType="mcp-confirm", content=dict(id="e", message="删除？")),
        ])
        self.history.recover_interrupted(self.owner)
        detail = self.history.get(self.owner, tid)
        self.assertEqual(detail["status"], "interrupted")
        self.assertEqual(detail["runId"], f"recovered:{tid}")
        self.assertIsNone(detail["mcpConfirm"])
        self.assertEqual(detail["entries"][0]["text"], "部分回答")

    def test_resume_after_recreating_graph_from_postgres_checkpoint(self):
        from langchain_core.messages import AIMessage, HumanMessage
        from test_cancellation import make_agent

        tid = self.client.post("/agent/conversations").json()["id"]
        saver = PostgresSaver(self.history.pool)
        saver.setup()
        cancel = threading.Event()
        users = [HumanMessage(content="hello")]
        agent = make_agent([AIMessage(content="partial\nanswer")], checkpointer=saver)
        events = []

        def observe(kind, payload):
            events.append((kind, payload))
            if kind == "text_delta":
                cancel.set()

        try:
            with self.assertRaises(RunCancelled):
                agent.run_messages(users, thread_id=tid, on_event=observe, cancel_event=cancel)
            restored = make_agent([AIMessage(content="recovered answer")], checkpointer=saver)
            answer_id = next(p["id"] for k, p in events if k == "text_start")
            prefix = "".join(p["delta"] for k, p in events if k == "text_delta")
            result = restored.run_messages(users, thread_id=tid, on_event=lambda *args: None,
                                           resume_cancelled=True, cancel_event=threading.Event(),
                                           partial=PartialAnswer(answer_id, prefix))
            self.assertEqual(result.text, prefix + "recovered answer")
            state = restored.graph.get_state(restored._config(tid))
            self.assertEqual(sum(isinstance(m, HumanMessage) for m in state.values["messages"]), 1)
        finally:
            saver.delete_thread(tid)

    def test_stream_endpoint_persists_events_before_finishing(self):
        app.state.agent.mcp = None
        # /agent uses the same demo owner when MCP is disabled.
        original_owner = self.owner
        self.owner = "local"
        tid = self.client.post("/agent/conversations").json()["id"]
        app.state.agent.limits = AgentLimits()
        app.state.agent.model = "deepseek-chat"
        app.state.lock = threading.Lock()

        def run_messages(messages, *, on_event, **kwargs):
            on_event("text_start", {"id": "a1"})
            on_event("text_delta", {"id": "a1", "delta": "你好"})
            on_event("text_end", {"id": "a1"})
            return SimpleNamespace(approval=None)

        app.state.agent.run_messages = run_messages
        try:
            response = self.client.post("/agent", json=dict(
                threadId=tid, runId="r1", state={}, tools=[], context=[], forwardedProps={},
                messages=[dict(id="u1", role="user", content="你好")],
            ))
            self.assertEqual(response.status_code, 200)
            self.assertIn("RUN_FINISHED", response.text)
            detail = self.client.get(f"/agent/conversations/{tid}").json()
            self.assertEqual(detail["status"], "completed")
            self.assertEqual(detail["entries"][-1]["text"], "你好")
        finally:
            # Never remove an existing user's conversations in test cleanup.
            with self.history.pool.connection() as conn:
                conn.execute("DELETE FROM chat_conversations WHERE thread_id=%s", (tid,))
            self.owner = original_owner


if __name__ == "__main__":
    unittest.main()
