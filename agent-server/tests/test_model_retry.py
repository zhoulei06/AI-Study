"""Exercise retries through actual LangGraph streams, not just the retry helper."""

import os
import unittest
from unittest.mock import patch

import httpx
import openai

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langchain_core.outputs import ChatGenerationChunk
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from agent.graph import compile_agent
from agent.limits import AgentLimits, LimitTracker
from agent.continuation import PartialAnswer
from test_cancellation import make_agent, FakeToolModel


class FailingStreamModel(FakeToolModel):
    calls: list = []
    failure_mode: str = "before"

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls.append(1)
        if len(self.calls) == 1:
            if self.failure_mode != "before":
                chunk = {
                    "text": AIMessageChunk(content="部分回答"),
                    "reasoning": AIMessageChunk(
                        content="", additional_kwargs={"reasoning_content": "部分思考"}
                    ),
                    "tool": AIMessageChunk(
                        content="",
                        tool_call_chunks=[
                            dict(name="calculator", args="{", id="call", index=0)
                        ],
                    ),
                }[self.failure_mode]
                generation = ChatGenerationChunk(message=chunk)
                if run_manager:
                    run_manager.on_llm_new_token(str(chunk.content), chunk=generation)
                yield generation
            raise ConnectionError("injected stream failure")
        yield from super()._stream(
            messages, stop=stop, run_manager=run_manager, **kwargs
        )


def agent_with_model(mode):
    agent = make_agent([])
    agent.chat_model = FailingStreamModel(
        messages=iter([AIMessage(content="成功回答")]), failure_mode=mode
    )
    agent.graph = compile_agent(
        model=agent.chat_model,
        tools=[],
        system_prompt="test",
        budget=agent._budget,
        checkpointer=InMemorySaver(),
        store=InMemoryStore(),
        memory_middleware=[],
    )
    return agent


class ModelRetryIntegrationTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"RETRY_BASE_DELAY": "0", "RETRY_MAX_DELAY": "0"})
        env.start()
        self.addCleanup(env.stop)

    def test_official_adapter_retries_before_any_output(self):
        agent = agent_with_model("before")
        events = []
        tracker = LimitTracker(AgentLimits(), "test")
        result = agent.run_messages(
            [HumanMessage(id="u", content="测试")],
            thread_id="retry-before",
            tracker=tracker,
            on_event=lambda *args: None,
            on_protocol_event=events.append,
        )
        self.assertEqual(result.text, "成功回答")
        self.assertEqual(
            "".join(e.delta for e in events if e.type == "TEXT_MESSAGE_CONTENT"),
            "成功回答",
        )
        self.assertEqual(len(agent.chat_model.calls), 2)
        self.assertEqual(sum(e.type == "TEXT_MESSAGE_START" for e in events), 1)
        self.assertEqual(tracker.iterations, 2)
        self.assertEqual(tracker.retries, 1)

    def test_thinking_fallback_counts_as_an_attempt(self):
        class ThinkingModel(FakeToolModel):
            extra_body: dict | None = {"thinking": {"type": "enabled"}}
            calls: list = []

            def _stream(self, messages, stop=None, run_manager=None, **kwargs):
                self.calls.append(self.extra_body)
                if self.extra_body is not None:
                    response = httpx.Response(
                        400, request=httpx.Request("POST", "https://test.invalid")
                    )
                    raise openai.BadRequestError(
                        "thinking unsupported", response=response, body={}
                    )
                yield from super()._stream(
                    messages, stop=stop, run_manager=run_manager, **kwargs
                )

        agent = make_agent([])
        agent.chat_model = ThinkingModel(
            messages=iter([AIMessage(content="普通模式回答")])
        )
        agent.graph = compile_agent(
            model=agent.chat_model,
            tools=[],
            system_prompt="test",
            budget=agent._budget,
            checkpointer=InMemorySaver(),
            store=InMemoryStore(),
            memory_middleware=[],
        )
        tracker = LimitTracker(AgentLimits(), "test")
        result = agent.run_messages(
            [HumanMessage(id="u", content="测试")],
            thread_id="thinking-fallback",
            tracker=tracker,
            on_event=lambda *args: None,
        )
        self.assertEqual(result.text, "普通模式回答")
        self.assertEqual(
            agent.chat_model.calls, [{"thinking": {"type": "enabled"}}, None]
        )
        self.assertEqual(tracker.iterations, 2)

    def test_partial_text_reasoning_or_tool_arguments_prevent_automatic_replay(self):
        for mode in ("text", "reasoning", "tool"):
            with self.subTest(mode=mode):
                agent = agent_with_model(mode)
                with (
                    self.assertLogs("ag_ui_langgraph", level="ERROR"),
                    self.assertRaises(RuntimeError),
                ):
                    agent.run_messages(
                        [HumanMessage(id="u", content="测试")],
                        thread_id=f"partial-{mode}",
                        on_event=lambda *args: None,
                        on_protocol_event=lambda event: None,
                    )
                self.assertEqual(len(agent.chat_model.calls), 1)

    def test_continuation_retries_only_before_new_suffix(self):
        agent = agent_with_model("before")
        users = [HumanMessage(id="u", content="继续")]
        agent.graph.update_state(
            agent._config("continue-retry"), {"messages": users}, as_node="model"
        )
        events = []
        result = agent.run_messages(
            users,
            thread_id="continue-retry",
            resume_cancelled=True,
            partial=PartialAnswer("answer", "已有内容"),
            on_event=lambda *args: events.append(args),
        )
        self.assertEqual(result.text, "已有内容成功回答")
        self.assertEqual(len(agent.chat_model.calls), 2)
        self.assertEqual(
            "".join(
                payload["delta"] for kind, payload in events if kind == "text_delta"
            ),
            "成功回答",
        )
