import asyncio
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage

from agent.cancellation import RunCancelled
from agent.limits import AgentLimits, LimitTracker
from agent.memory.short_term import ShortTermMemory
from agent.mcp_client import McpConnection
from agent.runtime.context import (
    RunContext,
    bind_run,
    current_run,
    invoke_auxiliary,
    LimitStop,
)
from agent.adapters.agui.streaming import EventChannel, SlowSubscriber


class ReliabilityTests(unittest.TestCase):
    def test_summary_sees_long_message_tail_and_tool_arguments(self):
        seen = []
        memory = ShortTermMemory(max_tokens=1600, keep_messages=1)
        long = "a" * 5000 + "重要的结尾"
        messages = [
            HumanMessage(id="old", content=long),
            AIMessage(
                id="tool",
                content="",
                tool_calls=[dict(id="call", name="save", args={"data": "x" * 2000})],
            ),
            HumanMessage(id="new", content="下一轮"),
        ]
        update = memory.plan(
            messages, "", lambda summary, older: seen.extend(older) or "摘要"
        )
        self.assertEqual(update["summary"], "摘要")
        self.assertTrue(seen[0].content.endswith("重要的结尾"))
        self.assertEqual(len(update["messages"]), 2)

    def test_parallel_tool_reservations_cannot_exceed_budget(self):
        tracker = LimitTracker(AgentLimits(max_tool_calls=1), "test")
        with ThreadPoolExecutor(max_workers=8) as pool:
            reasons = list(pool.map(lambda _: tracker.start_tool(), range(20)))
        self.assertEqual(reasons.count(None), 1)
        self.assertEqual(tracker.tool_calls, 1)

    def test_auxiliary_calls_count_usage_and_respect_budget(self):
        model = SimpleNamespace(
            invoke=lambda *args, **kwargs: AIMessage(
                content="摘要",
                usage_metadata={
                    "input_tokens": 12,
                    "output_tokens": 3,
                    "total_tokens": 15,
                },
            )
        )
        tracker = LimitTracker(AgentLimits(max_iterations=1), "deepseek-chat")
        with bind_run(RunContext(tracker, "summary")):
            invoke_auxiliary(model, [], purpose="摘要")
            self.assertEqual(tracker.total_tokens, 15)
            with self.assertRaises(LimitStop):
                invoke_auxiliary(model, [], purpose="提取")
        self.assertIsNone(current_run())

    def test_context_does_not_leak_between_threads(self):
        barrier = threading.Barrier(2)

        def execute(owner):
            with bind_run(
                RunContext(LimitTracker(AgentLimits(), "test"), owner, owner=owner)
            ):
                barrier.wait(timeout=3)
                return current_run().owner

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(execute, owner) for owner in ("alice", "bob")]
            self.assertEqual([f.result() for f in futures], ["alice", "bob"])
        self.assertIsNone(current_run())


class McpTimeoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_slow_subscriber_has_bounded_memory_and_is_disconnected(self):
        channel = EventChannel(maxsize=1)
        channel.publish("first", 1)
        channel.publish("second", 2)
        await asyncio.sleep(0)
        self.assertFalse(channel.connected)
        self.assertEqual(channel.queue.qsize(), 1)
        _, error = await channel.queue.get()
        self.assertIsInstance(error, SlowSubscriber)

    async def test_timeout_cancels_background_coroutine(self):
        connection = McpConnection.__new__(McpConnection)
        connection._loop = asyncio.get_running_loop()
        ended = asyncio.Event()

        async def operation():
            try:
                await asyncio.sleep(30)
            finally:
                ended.set()

        with self.assertRaises(TimeoutError):
            await asyncio.to_thread(connection._run, operation(), timeout=0.05)
        await asyncio.wait_for(ended.wait(), timeout=1)

    async def test_cancel_interrupts_waiting_mcp_call(self):
        connection = McpConnection.__new__(McpConnection)
        connection._loop = asyncio.get_running_loop()
        cancelled = threading.Event()
        ended = asyncio.Event()

        async def operation():
            try:
                cancelled.set()
                await asyncio.sleep(30)
            finally:
                ended.set()

        with bind_run(
            RunContext(LimitTracker(AgentLimits(), "test"), "mcp", cancel=cancelled)
        ):
            with self.assertRaises(RunCancelled):
                await asyncio.to_thread(connection._run, operation())
        await asyncio.wait_for(ended.wait(), timeout=1)
