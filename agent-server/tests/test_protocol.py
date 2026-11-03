"""Real official adapter, deterministic model, no external model requests."""

import threading
import unittest

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langchain_core.outputs import ChatGenerationChunk
from langchain_core.tools import tool

from agent.cancellation import RunCancelled
from agent.continuation import PartialAnswer
from agent.graph import compile_agent
from agent.memory.short_term import ShortTermMemory, ShortTermMiddleware
from test_cancellation import make_agent, FakeToolModel


class ProtocolTests(unittest.TestCase):
    def test_deepseek_reasoning_is_separate_from_answer(self):
        from langgraph.checkpoint.memory import InMemorySaver
        from langgraph.store.memory import InMemoryStore

        class ReasoningModel(FakeToolModel):
            def _stream(self, messages, stop=None, run_manager=None, **kwargs):
                for message in [
                    AIMessageChunk(
                        content="", additional_kwargs={"reasoning_content": "思考内容"}
                    ),
                    AIMessageChunk(content="最终回答"),
                ]:
                    chunk = ChatGenerationChunk(message=message)
                    if run_manager:
                        run_manager.on_llm_new_token(str(message.content), chunk=chunk)
                    yield chunk

        agent = make_agent([])
        agent.chat_model = ReasoningModel(messages=iter([]))
        agent.graph = compile_agent(
            model=agent.chat_model,
            tools=[],
            system_prompt="test",
            budget=agent._budget,
            checkpointer=InMemorySaver(),
            store=InMemoryStore(),
            memory_middleware=[],
        )
        events = []
        agent.run_messages(
            [HumanMessage(id="u", content="测试")],
            thread_id="reasoning",
            on_event=lambda *args: None,
            on_protocol_event=events.append,
        )
        self.assertEqual(
            "".join(e.delta for e in events if e.type == "TEXT_MESSAGE_CONTENT"),
            "最终回答",
        )
        self.assertEqual(
            "".join(e.delta for e in events if e.type == "REASONING_MESSAGE_CONTENT"),
            "思考内容",
        )

    def test_internal_summary_is_not_streamed_as_an_answer(self):
        from langgraph.checkpoint.memory import InMemorySaver
        from langgraph.store.memory import InMemoryStore

        agent = make_agent(
            [AIMessage(content="内部摘要，不给用户看"), AIMessage(content="对外回答")]
        )
        agent.graph = compile_agent(
            model=agent.chat_model,
            tools=[],
            system_prompt="test",
            budget=agent._budget,
            checkpointer=InMemorySaver(),
            store=InMemoryStore(),
            memory_middleware=[
                ShortTermMiddleware(
                    agent.chat_model,
                    ShortTermMemory(max_tokens=1, keep_messages=1),
                    lambda _: None,
                )
            ],
        )
        events = []
        agent.run_messages(
            [
                HumanMessage(id="old", content="以前的问题"),
                AIMessage(id="a", content="以前的回答"),
                HumanMessage(id="new", content="新问题"),
            ],
            thread_id="summary",
            on_event=lambda *args: None,
            on_protocol_event=events.append,
        )
        text = "".join(e.delta for e in events if e.type == "TEXT_MESSAGE_CONTENT")
        self.assertEqual(text, "对外回答")

    def test_text_and_tool_events_without_exposing_graph_state(self):
        @tool
        def calculate() -> str:
            """Return a deterministic result."""
            return "42"

        agent = make_agent(
            [
                AIMessage(
                    content="", tool_calls=[dict(id="call", name="calculate", args={})]
                ),
                AIMessage(content="结果为42"),
            ],
            [calculate],
        )
        events = []
        result = agent.run_messages(
            [HumanMessage(id="user", content="计算")],
            thread_id="protocol",
            on_event=lambda *args: None,
            on_protocol_event=events.append,
        )
        self.assertEqual(result.text, "结果为42")
        kinds = [e.type for e in events]
        self.assertIn("TOOL_CALL_START", kinds)
        self.assertIn("TOOL_CALL_RESULT", kinds)
        self.assertEqual(
            "".join(e.delta for e in events if e.type == "TEXT_MESSAGE_CONTENT"),
            "结果为42",
        )
        self.assertFalse(
            set(kinds) & {"STATE_SNAPSHOT", "MESSAGES_SNAPSHOT", "RUN_FINISHED", "RAW"}
        )

    def test_approval_reject_does_not_execute_tool(self):
        calls = []

        @tool
        def add_note(title: str, body: str) -> str:
            """Write a note after user approval."""
            calls.append(title)
            return "saved"

        agent = make_agent(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        dict(
                            id="write",
                            name="add_note",
                            args={"title": "a", "body": "b"},
                        )
                    ],
                ),
                AIMessage(content="已拒绝"),
            ],
            [add_note],
        )
        messages = [HumanMessage(id="u", content="记笔记")]
        result = agent.run_messages(
            messages,
            thread_id="approve",
            on_event=lambda *args: None,
            on_protocol_event=lambda event: None,
        )
        self.assertIsNotNone(result.approval)
        self.assertEqual(calls, [])
        result = agent.run_messages(
            messages,
            thread_id="approve",
            approvals={"write": False},
            on_event=lambda *args: None,
            on_protocol_event=lambda event: None,
        )
        self.assertEqual(result.text, "已拒绝")
        self.assertEqual(calls, [])

    def test_cancel_and_continue_keeps_the_same_answer(self):
        agent = make_agent(
            [AIMessage(content="第一段\n第二段"), AIMessage(content="第二段")]
        )
        cancel = threading.Event()
        events = []

        def receive(event):
            events.append(event)
            if event.type == "TEXT_MESSAGE_CONTENT":
                cancel.set()

        messages = [HumanMessage(id="u", content="解释")]
        with self.assertRaises(RunCancelled):
            agent.run_messages(
                messages,
                thread_id="cancel-protocol",
                on_event=lambda *args: None,
                on_protocol_event=receive,
                cancel_event=cancel,
            )
        prefix = "".join(e.delta for e in events if e.type == "TEXT_MESSAGE_CONTENT")
        mid = next(e.message_id for e in events if e.type == "TEXT_MESSAGE_START")
        cancel.clear()
        result = agent.run_messages(
            messages,
            thread_id="cancel-protocol",
            resume_cancelled=True,
            partial=PartialAnswer(mid, prefix),
            cancel_event=cancel,
            on_event=lambda *args: None,
            on_protocol_event=lambda event: None,
        )
        self.assertEqual(result.text, "第一段\n第二段")
