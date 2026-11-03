"""Retry failure injection: no real model/network requests or long sleeps."""

import asyncio
import json
import os
import ssl
import threading
import unittest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from langchain_deepseek import ChatDeepSeek

from agent.cancellation import RunCancelled
from agent.limits import AgentLimits, LimitTracker
from agent.mcp_client import McpConnection
from agent.runtime.context import RunContext, bind_run, LimitStop
from agent.runtime.models import configure_model
from agent.runtime.retry import (
    RetryPolicy,
    call_with_retry,
    acall_with_retry,
    current_attempt,
    is_transient,
    mark_model_output,
    reserve_model_attempt,
    retry_after,
)
from agent.tools import Tool, ToolRegistry


def status_error(status, headers=None):
    request = httpx.Request("POST", "https://test.invalid")
    response = httpx.Response(status, request=request, headers=headers)
    return httpx.HTTPStatusError("injected", request=request, response=response)


class RetryTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(
            os.environ, {"RETRY_BASE_DELAY": "0", "RETRY_MAX_DELAY": "0"}
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.policy = RetryPolicy(base_delay=0, max_delay=0)

    def test_transient_failures_count_every_model_attempt(self):
        calls = []

        def operation():
            calls.append(current_attempt().timeout)
            if len(calls) < 3:
                raise status_error(503)
            return "ok"

        tracker = LimitTracker(AgentLimits(), "test")
        with bind_run(RunContext(tracker, "retry")):
            self.assertEqual(
                call_with_retry(
                    operation,
                    policy=self.policy,
                    label="test",
                    before_attempt=reserve_model_attempt,
                ),
                "ok",
            )
        self.assertEqual(tracker.iterations, 3)
        self.assertEqual(tracker.retries, 2)
        self.assertTrue(all(0 < timeout <= 15 for timeout in calls))

    def test_permanent_http_and_argument_errors_are_not_retried(self):
        for error in [status_error(n) for n in (400, 401, 403, 404, 409, 422, 501)] + [
            ValueError(),
            TypeError(),
        ]:
            with self.subTest(error=error):
                calls = []

                def operation():
                    calls.append(1)
                    raise error

                with self.assertRaises(type(error)):
                    call_with_retry(operation, policy=self.policy, label="test")
                self.assertEqual(len(calls), 1)

    def test_exhaustion_returns_original_error(self):
        error = ConnectionError("injected")
        calls = []

        def operation():
            calls.append(1)
            raise error

        with self.assertRaises(ConnectionError) as raised:
            call_with_retry(operation, policy=self.policy, label="test")
        self.assertIs(raised.exception, error)
        self.assertEqual(len(calls), 3)

    def test_partial_stream_never_restarts(self):
        calls = []

        def operation():
            calls.append(1)
            mark_model_output()
            raise ConnectionError()

        with self.assertRaises(ConnectionError):
            call_with_retry(operation, policy=self.policy, label="stream")
        self.assertEqual(len(calls), 1)

    def test_retry_after_larger_than_remaining_budget_does_not_sleep_or_retry(self):
        calls = []

        def operation():
            calls.append(1)
            raise status_error(429, {"retry-after": "3600"})

        with self.assertRaises(httpx.HTTPStatusError):
            call_with_retry(operation, policy=self.policy, label="rate-limit")
        self.assertEqual(len(calls), 1)

    def test_retry_after_parses_seconds_milliseconds_and_date(self):
        self.assertEqual(retry_after(status_error(429, {"retry-after": "2"})), 2)
        self.assertEqual(
            retry_after(status_error(429, {"retry-after-ms": "1500"})), 1.5
        )
        stamp = format_datetime(
            datetime.now(timezone.utc) + timedelta(seconds=30), usegmt=True
        )
        self.assertTrue(
            28 <= retry_after(status_error(429, {"retry-after": stamp})) <= 30
        )
        self.assertIsNone(retry_after(status_error(429, {"retry-after": "invalid"})))

    def test_cancel_during_backoff_stops_immediately(self):
        cancel = threading.Event()

        def operation():
            raise ConnectionError()

        def retry_if(error):
            cancel.set()
            return True

        with bind_run(
            RunContext(LimitTracker(AgentLimits(), "test"), "cancel", cancel=cancel)
        ):
            with self.assertRaises(RunCancelled):
                call_with_retry(
                    operation,
                    policy=RetryPolicy(base_delay=5),
                    label="cancel",
                    retry_if=retry_if,
                )

    def test_iteration_budget_stops_additional_model_attempts(self):
        calls = []

        def operation():
            calls.append(1)
            raise ConnectionError()

        tracker = LimitTracker(AgentLimits(max_iterations=1), "test")
        with bind_run(RunContext(tracker, "budget")):
            with self.assertRaises(LimitStop):
                call_with_retry(
                    operation,
                    policy=self.policy,
                    label="budget",
                    before_attempt=reserve_model_attempt,
                )
        self.assertEqual(len(calls), 1)

    def test_wrapped_tls_error_is_permanent(self):
        cert = ssl.SSLCertVerificationError("untrusted certificate")
        inner = RuntimeError()
        inner.__cause__ = cert
        outer = httpx.ConnectError("certificate failure")
        outer.__cause__ = inner
        self.assertFalse(is_transient(outer))

    def test_unknown_and_write_tools_are_not_replayed(self):
        calls = []

        def write(**kwargs):
            calls.append(1)
            raise TimeoutError()

        tool = Tool("write", "write", {"type": "object"}, write)
        registry = ToolRegistry([tool])
        with bind_run(RunContext(LimitTracker(AgentLimits(), "test"), "writes")):
            self.assertEqual(
                json.loads(registry.execute("write", "{}"))["error"]["code"],
                "OPERATION_OUTCOME_UNKNOWN",
            )
            self.assertEqual(
                json.loads(registry.execute("write", "{}"))["error"]["code"],
                "OPERATION_OUTCOME_UNKNOWN",
            )
        self.assertEqual(len(calls), 1)

    def test_read_tool_retries_within_tool_budget(self):
        calls = []

        def read():
            calls.append(1)
            if len(calls) < 3:
                raise TimeoutError()
            return "value"

        registry = ToolRegistry(
            [Tool("read", "read", {"type": "object"}, read, read_only=True)]
        )
        tracker = LimitTracker(AgentLimits(max_tool_calls=3), "test")
        tracker.start_tool()
        with bind_run(RunContext(tracker, "read")):
            self.assertEqual(registry.execute("read", "{}"), "value")
        self.assertEqual(tracker.tool_calls, 3)
        self.assertEqual(tracker.retries, 2)

    def test_mcp_and_tool_wrapper_do_not_multiply_retries(self):
        connection = McpConnection.__new__(McpConnection)
        connection.remote_tools = [
            SimpleNamespace(
                name="read",
                description="read",
                input_schema={"type": "object"},
                annotations=SimpleNamespace(read_only_hint=True),
            )
        ]
        connection._bridge_tools = lambda: []

        async def call_tool(*args):
            return None

        connection._client = SimpleNamespace(call_tool=call_tool)
        calls = []

        def run(coro, **kwargs):
            coro.close()
            calls.append(1)
            if len(calls) < 3:
                raise ConnectionError()
            return SimpleNamespace(
                content=[SimpleNamespace(text="value")], is_error=False
            )

        connection._run = run
        registry = ToolRegistry(connection.agent_tools())
        self.assertEqual(registry.execute("read", "{}"), "value")
        self.assertEqual(len(calls), 3)

    def test_sdk_clients_really_disable_retries_and_use_attempt_timeout(self):
        calls = []

        def respond(request):
            calls.append(request.extensions["timeout"])
            return httpx.Response(
                503, json={"error": {"message": "injected", "type": "server_error"}}
            )

        client = httpx.Client(transport=httpx.MockTransport(respond))
        model = ChatDeepSeek(
            model="deepseek-chat",
            api_key="test-only-placeholder",
            max_retries=1,
            http_client=client,
        )
        configured = configure_model(model, timeout=2)
        try:
            self.assertEqual(configured.root_client.max_retries, 0)
            self.assertEqual(configured.root_async_client.max_retries, 0)
            with self.assertRaises(Exception):
                configured.invoke("test")
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["read"], 2)
        finally:
            client.close()
            asyncio.run(model.root_async_client.close())


class AsyncRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_sdk_and_application_retries_do_not_multiply(self):
        calls = []

        def respond(request):
            calls.append(1)
            return httpx.Response(
                503, json={"error": {"message": "injected", "type": "server_error"}}
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        model = ChatDeepSeek(
            model="deepseek-chat",
            api_key="test-only-placeholder",
            max_retries=1,
            http_async_client=client,
        )
        configured = configure_model(model, timeout=2)
        try:
            with self.assertRaises(Exception):
                await acall_with_retry(
                    lambda: configured.ainvoke("test"),
                    policy=RetryPolicy(base_delay=0, max_delay=0),
                    label="async-sdk",
                )
            self.assertEqual(len(calls), 3)
        finally:
            await client.aclose()
            model.root_client.close()

    async def test_async_recovers_before_output_but_not_after_output(self):
        calls = []

        async def operation():
            calls.append(1)
            if len(calls) < 3:
                raise ConnectionError()
            return "ok"

        policy = RetryPolicy(base_delay=0, max_delay=0)
        self.assertEqual(
            await acall_with_retry(operation, policy=policy, label="async"), "ok"
        )
        calls.clear()

        async def partial():
            calls.append(1)
            mark_model_output()
            raise ConnectionError()

        with self.assertRaises(ConnectionError):
            await acall_with_retry(partial, policy=policy, label="async-partial")
        self.assertEqual(len(calls), 1)

    async def test_task_cancellation_does_not_start_another_attempt(self):
        started = asyncio.Event()
        calls = []

        async def operation():
            calls.append(1)
            started.set()
            await asyncio.sleep(30)

        task = asyncio.create_task(
            acall_with_retry(operation, policy=RetryPolicy(), label="cancel")
        )
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(len(calls), 1)
