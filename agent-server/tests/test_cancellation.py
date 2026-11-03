"""Exercise real graph checkpoints with a deterministic model and local tools."""
import threading
import unittest
import time
from types import SimpleNamespace

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langchain_core.outputs import ChatGenerationChunk
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from agent.cancellation import RunCancelled
from agent.continuation import PartialAnswer
from agent.graph import BudgetMiddleware, compile_agent
from agent.limits import AgentLimits
from agent.loop import Agent
from agent.mcp_client import McpConnection


class FakeToolModel(GenericFakeChatModel):
    received: list = []
    def bind_tools(self, tools, **kwargs):
        return self

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        self.received.append(messages)
        message = next(self.messages)
        chunks = [AIMessageChunk(content="", tool_calls=message.tool_calls)] if message.tool_calls else [
            AIMessageChunk(content=token) for token in message.content.splitlines(keepends=True)
        ]
        for message_chunk in chunks:
            time.sleep(0.02)  # Let the consumer observe a token before the next one.
            chunk = ChatGenerationChunk(message=message_chunk)
            if run_manager:
                run_manager.on_llm_new_token(str(message_chunk.content), chunk=chunk)
            yield chunk


def make_agent(responses, tools=(), checkpointer=None, budget=None):
    agent = Agent.__new__(Agent)
    agent.limits = AgentLimits()
    agent.model = "test-model"
    agent.verbose = False
    agent._budget = budget or BudgetMiddleware()
    agent.system_prompt = "Test"
    agent.chat_model = FakeToolModel(messages=iter(responses))
    agent.graph = compile_agent(
        model=agent.chat_model, tools=list(tools),
        system_prompt="Test", budget=agent._budget,
        checkpointer=checkpointer or InMemorySaver(), store=InMemoryStore(), memory_middleware=[],
    )
    agent._remember = lambda *args: None
    return agent


class GraphCancellationTests(unittest.TestCase):
    def test_continue_recovers_checkpoint_suffix_without_calling_model(self):
        agent = make_agent([])
        users = [HumanMessage(id="u1", content="解释")]
        agent.graph.update_state(agent._config("recover-suffix"), {
            "messages": [*users, AIMessage(id="answer", content="已有内容和遗漏后缀")],
        }, as_node="model")
        events = []
        result = agent.run_messages(
            users, thread_id="recover-suffix", resume_cancelled=True,
            partial=PartialAnswer("answer", "已有内容"),
            on_event=lambda kind, payload: events.append((kind, payload)),
        )
        self.assertEqual(result.text, "已有内容和遗漏后缀")
        self.assertEqual("".join(p["delta"] for k, p in events if k == "text_delta"), "和遗漏后缀")
        self.assertEqual([p["id"] for k, p in events if k == "text_start"], ["answer"])
        self.assertEqual(agent.chat_model.received, [])

    def test_continue_appends_to_same_message_and_persists_combined_answer(self):
        cancel = threading.Event()
        agent = make_agent([AIMessage(content="第一步\n未完成"), AIMessage(content="第二步")])
        users = [HumanMessage(id="u1", content="分步骤解释")]
        events = []

        def observe(kind, payload):
            events.append((kind, payload))
            if kind == "text_delta":
                cancel.set()

        with self.assertRaises(RunCancelled):
            agent.run_messages(users, thread_id="append", on_event=observe, cancel_event=cancel)
        answer_id = next(p["id"] for k, p in events if k == "text_start")
        prefix = "".join(p["delta"] for k, p in events if k == "text_delta")
        cancel.clear()
        continued = []
        result = agent.run_messages(users, thread_id="append", on_event=lambda k, p: continued.append((k, p)),
                                    cancel_event=cancel, resume_cancelled=True,
                                    partial=PartialAnswer(answer_id, prefix))
        self.assertEqual(result.text, prefix + "第二步")
        self.assertEqual([p["id"] for k, p in continued if k == "text_start"], [answer_id])
        self.assertEqual("".join(p["delta"] for k, p in continued if k == "text_delta"), "第二步")
        state = agent.graph.get_state(agent._config("append"))
        self.assertEqual(sum(isinstance(m, HumanMessage) for m in state.values["messages"]), 1)
        self.assertEqual(state.values["messages"][-1].id, answer_id)
        prompt = agent.chat_model.received[-1]
        self.assertEqual(prompt[-2].content, prefix)
        self.assertIn("不要重复已有内容", prompt[-1].content)

    def test_cancel_continuation_then_continue_again(self):
        agent = make_agent([AIMessage(content="乙\n丙"), AIMessage(content="丁")])
        users = [HumanMessage(id="u1", content="解释")]
        agent.graph.update_state(agent._config("again"), {"messages": users}, as_node="__start__")
        cancel = threading.Event()
        events = []

        def observe(kind, payload):
            events.append((kind, payload))
            if kind == "text_delta":
                cancel.set()

        with self.assertRaises(RunCancelled):
            agent.run_messages(users, thread_id="again", on_event=observe, cancel_event=cancel,
                                resume_cancelled=True, partial=PartialAnswer("answer", "甲"))
        prefix = "甲" + "".join(p["delta"] for k, p in events if k == "text_delta")
        cancel.clear()
        result = agent.run_messages(users, thread_id="again", on_event=lambda *args: None,
                                    resume_cancelled=True, partial=PartialAnswer("answer", prefix))
        self.assertEqual(result.text, prefix + "丁")
        self.assertEqual(agent.graph.get_state(agent._config("again")).values["messages"][-1].id, "answer")

    def test_new_question_discards_unexecuted_tools_after_cancel(self):
        cancel = threading.Event()
        calls = []

        @tool
        def save_first() -> str:
            """Save the first item."""
            calls.append("first")
            cancel.set()
            return "saved"

        @tool
        def save_second() -> str:
            """Save the second item."""
            calls.append("second")
            return "saved"

        class WaitForFirstTool(BudgetMiddleware):
            def wrap_tool_call(self, request, handler):
                if request.tool_call["name"] == "save_second":
                    cancel.wait(timeout=2)
                return super().wrap_tool_call(request, handler)

        agent = make_agent([AIMessage(content="", tool_calls=[
            dict(id="one", name="save_first", args={}), dict(id="two", name="save_second", args={}),
        ]), AIMessage(content="新回答")], [save_first, save_second], budget=WaitForFirstTool())
        users = [HumanMessage(id="u1", content="保存")]
        with self.assertRaises(RunCancelled):
            agent.run_messages(users, thread_id="supersede", on_event=lambda *args: None, cancel_event=cancel)
        result = agent.run_messages([*users, HumanMessage(id="u2", content="换个问题")], thread_id="supersede",
                                    on_event=lambda *args: None, abandon_cancelled=True)
        self.assertEqual(result.text, "新回答")
        self.assertEqual(calls, ["first"])

    def test_new_question_preserves_partial_and_does_not_resume_old_answer(self):
        cancel = threading.Event()
        agent = make_agent([AIMessage(content="旧回答\n后续"), AIMessage(content="新回答")])
        first = HumanMessage(id="u1", content="相同问题")
        events = []

        def observe(kind, payload):
            events.append((kind, payload))
            if kind == "text_delta":
                cancel.set()

        with self.assertRaises(RunCancelled):
            agent.run_messages([first], thread_id="new", on_event=observe, cancel_event=cancel)
        answer_id = next(p["id"] for k, p in events if k == "text_start")
        prefix = "".join(p["delta"] for k, p in events if k == "text_delta")
        result = agent.run_messages([first, HumanMessage(id="u2", content="相同问题")], thread_id="new",
                                    on_event=lambda *args: None, abandon_cancelled=True,
                                    partial=PartialAnswer(answer_id, prefix))
        self.assertEqual(result.text, "新回答")
        messages = agent.graph.get_state(agent._config("new")).values["messages"]
        self.assertEqual([m.id for m in messages if isinstance(m, HumanMessage)], ["u1", "u2"])
        self.assertEqual(next(m.content for m in messages if m.id == answer_id), prefix)

    def test_cancel_partial_generation_then_resume_without_new_user_message(self):
        cancel = threading.Event()
        agent = make_agent([AIMessage(content="partial\nanswer"), AIMessage(content="finished answer")])
        users = [HumanMessage(content="hello")]
        events = []

        def observe(kind, payload):
            events.append((kind, payload))
            if kind == "text_delta":
                cancel.set()

        with self.assertRaises(RunCancelled):
            agent.run_messages(users, thread_id="partial", on_event=observe, cancel_event=cancel)
        self.assertTrue(agent.graph.get_state(agent._config("partial")).next)
        cancel.clear()
        result = agent.run_messages(users, thread_id="partial", on_event=lambda *args: None,
                                    cancel_event=cancel, resume_cancelled=True)
        self.assertEqual(result.text, "finished answer")
        state = agent.graph.get_state(agent._config("partial"))
        self.assertEqual(sum(isinstance(m, HumanMessage) for m in state.values["messages"]), 1)

    def test_completed_tool_is_not_repeated_on_resume(self):
        cancel = threading.Event()
        calls = []

        @tool
        def save_once(value: str) -> str:
            """Save a value once."""
            calls.append(value)
            cancel.set()  # User cancels while a tool is completing.
            return "saved"

        agent = make_agent([
            AIMessage(content="", tool_calls=[dict(id="tool-1", name="save_once", args={"value": "note"})]),
            AIMessage(content="saved successfully"),
        ], [save_once])
        users = [HumanMessage(content="save note")]
        with self.assertRaises(RunCancelled):
            agent.run_messages(users, thread_id="tools", on_event=lambda *args: None, cancel_event=cancel)
        self.assertEqual(calls, ["note"])
        cancel.clear()
        result = agent.run_messages(users, thread_id="tools", on_event=lambda *args: None,
                                    cancel_event=cancel, resume_cancelled=True)
        self.assertEqual(result.text, "saved successfully")
        self.assertEqual(calls, ["note"])

    def test_cancel_between_parallel_tools_preserves_successful_task(self):
        cancel = threading.Event()
        calls = []

        class WaitForFirstTool(BudgetMiddleware):
            def wrap_tool_call(self, request, handler):
                if request.tool_call["name"] == "save_second":
                    cancel.wait(timeout=2)
                return super().wrap_tool_call(request, handler)

        @tool
        def save_first() -> str:
            """Save the first item."""
            calls.append("first")
            cancel.set()
            return "first saved"

        @tool
        def save_second() -> str:
            """Save the second item."""
            calls.append("second")
            return "second saved"

        agent = make_agent([
            AIMessage(content="", tool_calls=[
                dict(id="one", name="save_first", args={}),
                dict(id="two", name="save_second", args={}),
            ]), AIMessage(content="both saved"),
        ], [save_first, save_second], budget=WaitForFirstTool())
        users = [HumanMessage(content="save both")]
        with self.assertRaises(RunCancelled):
            agent.run_messages(users, thread_id="parallel", on_event=lambda *args: None, cancel_event=cancel)
        self.assertEqual(calls, ["first"])
        cancel.clear()
        result = agent.run_messages(users, thread_id="parallel", on_event=lambda *args: None,
                                    cancel_event=cancel, resume_cancelled=True)
        self.assertEqual(result.text, "both saved")
        self.assertEqual(calls, ["first", "second"])


class McpCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_before_and_during_confirmation_setup(self):
        connection = McpConnection.__new__(McpConnection)
        connection._elicit_waiters = {}
        cancel = threading.Event()
        calls = []

        def handler(elicit_id, message, phase):
            calls.append(phase)
            if phase == "start":
                cancel.set()

        connection.set_elicit_ui(handler, cancel_event=cancel)
        params = SimpleNamespace(mode="form", message="delete?")
        result = await connection._on_elicitation(None, params)
        self.assertEqual(result.action, "cancel")
        self.assertEqual(calls, ["start", "end"])
        self.assertEqual(connection._elicit_waiters, {})
        calls.clear()
        result = await connection._on_elicitation(None, params)
        self.assertEqual(result.action, "cancel")
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
