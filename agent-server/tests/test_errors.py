"""Public error contracts across HTTP, graph adapters, tool results and history."""

import json
import os
import threading
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from agent.cancellation import RunRegistry
from agent.errors import ErrorCode, MESSAGES, make_error, normalize_error
from agent.graph import compile_agent
from agent.limits import AgentLimits
from agent.loop import AgentResult
from agent.mcp_client import McpConnection
from agent.server import app
from agent.tools import Tool, ToolRegistry
from test_cancellation import make_agent, FakeToolModel
import test_persistence as persistence

SECRET = "sk-private-value-not-for-clients"


class PublicErrorTests(unittest.TestCase):
    def test_every_code_has_a_safe_message(self):
        self.assertEqual(set(MESSAGES), set(ErrorCode))
        for code in ErrorCode:
            error = make_error(code, source="test").public()
            self.assertEqual(error["version"], 1)
            self.assertEqual(error["code"], code.value)
            self.assertTrue(error["message"])

    def test_original_exception_text_is_never_public(self):
        for exc in (RuntimeError(SECRET), ValueError(SECRET), ConnectionError(SECRET)):
            error = normalize_error(exc, source="tool").public()
            self.assertNotIn(SECRET, json.dumps(error))

    def test_tool_failure_is_a_machine_readable_envelope(self):
        def failed():
            raise ValueError(SECRET)

        registry = ToolRegistry(
            [Tool("read", "read", {"type": "object"}, failed, read_only=True)]
        )
        result = json.loads(registry.execute("read", "{}"))
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "INVALID_ARGUMENT")
        self.assertNotIn(SECRET, json.dumps(result))
        self.assertEqual(
            json.loads(registry.execute("missing", "{}"))["error"]["code"],
            "TOOL_NOT_FOUND",
        )

    def test_mcp_business_error_does_not_leak_remote_error_text(self):
        connection = McpConnection.__new__(McpConnection)
        connection._call = lambda *args, **kwargs: SimpleNamespace(
            content=[SimpleNamespace(text=SECRET)], is_error=True
        )
        result = json.loads(connection._tool_handler("write")())
        self.assertEqual(result["error"]["code"], "TOOL_REJECTED")
        self.assertNotIn(SECRET, json.dumps(result))


class ErrorApiTests(unittest.TestCase):
    _drop_schema = persistence.PersistenceTests._drop_schema
    tearDown = persistence.PersistenceTests.tearDown

    def setUp(self):
        persistence.PersistenceTests.setUp(self)
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(
            patch.dict(
                os.environ,
                {
                    "AGENT_HISTORY_OWNER": self.owner,
                    "RETRY_BASE_DELAY": "0",
                    "RETRY_MAX_DELAY": "0",
                    "MODEL_MAX_ATTEMPTS": "2",
                },
            )
        )
        values = dict(
            history=self.history,
            agent=SimpleNamespace(mcp=None, limits=AgentLimits(), model="test"),
            runs=RunRegistry(),
            lock=threading.Lock(),
            workers=set(),
            agent_status={"status": "ready"},
        )
        for key, value in values.items():
            stack.enter_context(patch.object(app.state, key, value, create=True))
        self.client = TestClient(app, raise_server_exceptions=False)
        self.addCleanup(self.client.close)

    def run_body(self):
        tid = self.client.post("/agent/conversations").json()["id"]
        return dict(
            threadId=tid,
            runId="structured-run",
            state={},
            tools=[],
            context=[],
            forwardedProps={},
            messages=[dict(id="user", role="user", content="测试")],
        )

    def events(self, response):
        return [
            json.loads(line[6:])
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]

    def test_http_validation_and_not_found_share_the_error_contract(self):
        for response, code in [
            (self.client.post("/agent", json={"secret": SECRET}), "INVALID_ARGUMENT"),
            (self.client.get("/agent/conversations/missing"), "NOT_FOUND"),
        ]:
            error = response.json()["error"]
            self.assertEqual(error["code"], code)
            self.assertEqual(response.headers["x-request-id"], error["requestId"])
            self.assertEqual(response.json()["detail"], error["message"])
            self.assertNotIn(SECRET, response.text)

    def install_failure_model(self, *, partial=False):
        class FailingModel(FakeToolModel):
            def _stream(self, messages, stop=None, run_manager=None, **kwargs):
                if partial:
                    chunk = ChatGenerationChunk(
                        message=AIMessageChunk(content="保留的内容")
                    )
                    if run_manager:
                        run_manager.on_llm_new_token("保留的内容", chunk=chunk)
                    yield chunk
                request = httpx.Request("POST", "https://test.invalid")
                raise httpx.HTTPStatusError(
                    SECRET,
                    request=request,
                    response=httpx.Response(429, request=request),
                )

        agent = make_agent([])
        agent.mcp = None
        agent.chat_model = FailingModel(messages=iter([]))
        agent.graph = compile_agent(
            model=agent.chat_model,
            tools=[],
            system_prompt="test",
            budget=agent._budget,
            checkpointer=InMemorySaver(),
            store=InMemoryStore(),
            memory_middleware=[],
        )
        app.state.agent = agent

    def test_model_retry_error_survives_sse_persistence_and_replay(self):
        self.install_failure_model()
        body = self.run_body()
        with self.assertLogs("ag_ui_langgraph", level="ERROR"):
            response = self.client.post("/agent", json=body)
        events = self.events(response)
        error_event = next(e for e in events if e["type"] == "RUN_ERROR")
        error = error_event["metadata"]["error"]
        self.assertEqual(error_event["code"], "RATE_LIMITED")
        self.assertEqual(error["attempts"], 2)
        self.assertTrue(error["retryExhausted"])
        self.assertEqual(error["requestId"], response.headers["x-request-id"])
        self.assertEqual(error["runId"], body["runId"])
        self.assertFalse(any(e["type"] == "RUN_FINISHED" for e in events))
        self.assertNotIn(SECRET, response.text)
        detail = self.client.get(f"/agent/conversations/{body['threadId']}").json()
        self.assertEqual(detail["status"], "failed")
        self.assertEqual(detail["errorInfo"], error)
        replay = self.client.get(
            f"/agent/conversations/{body['threadId']}/runs/{body['runId']}/events"
        )
        self.assertEqual(
            next(e for e in self.events(replay) if e["type"] == "RUN_ERROR"),
            error_event,
        )

    def test_partial_failure_retains_text_and_disables_retry(self):
        self.install_failure_model(partial=True)
        body = self.run_body()
        with self.assertLogs("ag_ui_langgraph", level="ERROR"):
            response = self.client.post("/agent", json=body)
        error = next(e for e in self.events(response) if e["type"] == "RUN_ERROR")[
            "metadata"
        ]["error"]
        self.assertEqual(error["code"], "STREAM_INTERRUPTED")
        self.assertEqual(error["causeCode"], "RATE_LIMITED")
        self.assertTrue(error["partial"])
        self.assertFalse(error["retryable"])
        self.assertEqual(error["attempts"], 1)
        detail = self.client.get(f"/agent/conversations/{body['threadId']}").json()
        self.assertEqual(detail["entries"][-1]["text"], "保留的内容")

    def test_budget_stop_is_an_error_terminal_not_success(self):
        app.state.agent.run_messages = lambda *args, **kwargs: AgentResult(
            text="停止",
            stop_reason="max_iterations",
            usage={},
            error=make_error(ErrorCode.LIMIT_EXCEEDED, limit="max_iterations"),
        )
        body = self.run_body()
        response = self.client.post("/agent", json=body)
        events = self.events(response)
        self.assertFalse(any(e["type"] == "RUN_FINISHED" for e in events))
        error = next(e for e in events if e["type"] == "RUN_ERROR")["metadata"]["error"]
        self.assertEqual(error["code"], "LIMIT_EXCEEDED")
        self.assertEqual(error["limit"], "max_iterations")
        self.assertEqual(error["requestId"], response.headers["x-request-id"])
        self.assertEqual(
            self.client.get(f"/agent/conversations/{body['threadId']}").json()[
                "status"
            ],
            "failed",
        )

    def test_unexpected_http_error_uses_safe_envelope(self):
        with patch.object(self.history, "list", side_effect=RuntimeError(SECRET)):
            response = self.client.get("/agent/conversations")
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["error"]["code"], "INTERNAL_ERROR")
        self.assertEqual(
            response.headers["x-request-id"], response.json()["error"]["requestId"]
        )
        self.assertNotIn(SECRET, response.text)

    def test_persistence_failure_ends_stream_and_releases_admission_lock(self):
        body = self.run_body()
        app.state.agent.run_messages = lambda *args, **kwargs: SimpleNamespace(
            approval=None
        )
        with patch.object(self.history, "record", side_effect=RuntimeError(SECRET)):
            response = self.client.post("/agent", json=body)
        error = next(e for e in self.events(response) if e["type"] == "RUN_ERROR")[
            "metadata"
        ]["error"]
        self.assertEqual(error["code"], "PERSISTENCE_FAILED")
        self.assertNotIn(SECRET, response.text)
        self.assertTrue(app.state.lock.acquire(blocking=False))
        app.state.lock.release()

    def test_expired_memory_attempt_limit_has_structured_error(self):
        self.jobs.enqueue(self.owner, "expired", "问题", "回答")
        self.jobs.claim(self.owner)
        with self.history.pool.connection() as conn:
            conn.execute(
                "UPDATE agent_memory_jobs SET attempts=3,lease_until=now()-interval '1 second' WHERE user_id=%s",
                (self.owner,),
            )
        self.assertIsNone(self.jobs.claim(self.owner))
        with self.history.pool.connection() as conn:
            row = conn.execute(
                "SELECT status,error_info FROM agent_memory_jobs WHERE user_id=%s",
                (self.owner,),
            ).fetchone()
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["error_info"]["code"], "LIMIT_EXCEEDED")
        self.assertTrue(row["error_info"]["retryExhausted"])
