"""Real compiled graphs, deterministic models, no external model API calls."""

import json
import threading
import unittest
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from pydantic import ValidationError

from agent.cancellation import RunCancelled
from agent.continuation import PartialAnswer
from agent.errors import ApplicationError, ErrorCode
from agent.graph import compile_agent
from agent.history import apply_event
from agent.limits import AgentLimits
from agent.planning import PlanningMiddleware
from agent.planning.schemas import Plan
from agent.persistence.checkpoints import ThreadedPostgresSaver
from test_cancellation import make_agent
import test_persistence as persistence


def plan(*steps):
    return AIMessage(
        content=json.dumps(
            {
                "mode": "planned",
                "goal": "完成任务",
                "steps": [
                    {
                        "id": f"s{i}",
                        "task": task,
                        "depends_on": [f"s{i - 1}"] if i > 1 else [],
                        "tools": tools,
                        "success_criteria": ["结果有证据"],
                    }
                    for i, (task, tools) in enumerate(steps, 1)
                ],
            },
            ensure_ascii=False,
        )
    )


def verification(passed=True):
    return AIMessage(
        content=json.dumps(
            {"passed": passed, "reason": "满足条件" if passed else "缺少证据"}
        )
    )


def build(responses, tools=(), *, checkpointer=None, **settings):
    agent = make_agent(responses, tools)
    agent.limits = AgentLimits(max_iterations=30, token_budget=100000)
    middleware = PlanningMiddleware(agent.chat_model, list(tools), **settings)
    agent.graph = compile_agent(
        model=agent.chat_model,
        tools=list(tools),
        system_prompt="test",
        budget=agent._budget,
        checkpointer=checkpointer or InMemorySaver(),
        store=InMemoryStore(),
        memory_middleware=[],
        planning=middleware,
    )
    return agent, middleware


def run(agent, thread="p", **kwargs):
    events = []
    result = agent.run_messages(
        [HumanMessage(id="u1", content="完成任务")],
        thread_id=thread,
        on_event=lambda *args: None,
        on_protocol_event=events.append,
        **kwargs,
    )
    return result, events


class PlanningTests(unittest.TestCase):
    def test_plan_rejects_cycles_duplicate_ids_and_unknown_fields(self):
        valid = json.loads(plan(("第一步", []), ("第二步", [])).content)
        for changed in [
            {**valid, "extra": True},
            {**valid, "steps": [valid["steps"][1], valid["steps"][0]]},
            {**valid, "steps": [valid["steps"][0], valid["steps"][0]]},
        ]:
            with self.assertRaises(ValidationError):
                Plan.model_validate(changed)

    def test_two_steps_are_verified_and_only_final_answer_is_streamed(self):
        agent, _ = build(
            [
                plan(("查询", []), ("分析", [])),
                AIMessage(content="内部步骤一"),
                verification(),
                AIMessage(content="内部步骤二"),
                verification(),
                AIMessage(content="最终结果"),
            ]
        )
        result, events = run(agent)
        self.assertEqual(result.text, "最终结果")
        output = "".join(e.delta for e in events if e.type == "TEXT_MESSAGE_CONTENT")
        self.assertEqual(output, "最终结果")
        task = agent.graph.get_state(agent._config("p")).values["task"]
        self.assertEqual(task["phase"], "completed")
        self.assertEqual(
            [s["status"] for s in task["steps"]], ["succeeded", "succeeded"]
        )
        self.assertEqual(result.usage["iterations"], "6/30")
        progress = [e for e in events if e.type == "ACTIVITY_SNAPSHOT"]
        self.assertTrue(progress)
        self.assertNotIn(
            "内部步骤", json.dumps([e.content for e in progress], ensure_ascii=False)
        )

    def test_direct_route_does_not_verify_or_create_plan_card(self):
        agent, _ = build(
            [
                AIMessage(content='{"mode":"direct","goal":"你好","steps":[]}'),
                AIMessage(content="你好"),
            ]
        )
        result, events = run(agent)
        self.assertEqual(result.text, "你好")
        self.assertFalse(any(e.type == "ACTIVITY_SNAPSHOT" for e in events))

    def test_invalid_json_and_unknown_tools_fail_closed(self):
        for response in [
            AIMessage(content="secret not json"),
            plan(("查询", ["nonexistent"])),
        ]:
            agent, _ = build([response])
            with self.assertRaises(ApplicationError) as raised:
                run(agent)
            self.assertEqual(raised.exception.error.code, ErrorCode.PLANNING_FAILED)
            self.assertNotIn("secret", str(raised.exception))

    def test_executor_cannot_call_tools_outside_current_step(self):
        calls = []

        @tool
        def forbidden() -> str:
            """Must never execute."""
            calls.append(True)
            return "done"

        agent, _ = build(
            [
                plan(("查询", [])),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "forbidden",
                            "args": {},
                            "id": "c1",
                            "type": "tool_call",
                        }
                    ],
                ),
            ],
            [forbidden],
        )
        with self.assertRaises(ApplicationError):
            run(agent)
        self.assertEqual(calls, [])

    def test_failed_verification_replans_only_remaining_work(self):
        agent, _ = build(
            [
                plan(("查询", [])),
                AIMessage(content="证据不足"),
                verification(False),
                plan(("补充证据", [])),
                AIMessage(content="补充后的结果"),
                verification(),
                AIMessage(content="完成"),
            ]
        )
        result, _ = run(agent)
        task = agent.graph.get_state(agent._config("p")).values["task"]
        self.assertEqual(result.text, "完成")
        self.assertEqual(task["version"], 2)
        self.assertEqual(task["replans"], 1)

    def test_replanning_is_bounded(self):
        agent, _ = build(
            [plan(("查询", [])), AIMessage(content="不足"), verification(False)],
            max_replans=0,
        )
        with self.assertRaises(ApplicationError) as raised:
            run(agent)
        self.assertEqual(raised.exception.error.code, ErrorCode.VERIFICATION_FAILED)
        self.assertEqual(
            agent.graph.get_state(agent._config("p")).values["task"]["phase"], "failed"
        )

    def test_completed_write_is_not_repeated_when_verification_is_cancelled(self):
        calls = []

        @tool
        def save_record() -> str:
            """Save record."""
            calls.append(True)
            return '{"saved":true}'

        agent, middleware = build(
            [
                plan(("保存", ["save_record"])),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "save_record",
                            "args": {},
                            "id": "c1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="已保存"),
                verification(),
                AIMessage(content="完成"),
            ],
            [save_record],
        )
        with patch.object(middleware.service, "verify", side_effect=RunCancelled()):
            with self.assertRaises(RunCancelled):
                run(agent)
        self.assertEqual(calls, [True])
        result, _ = run(agent, resume_cancelled=True)
        self.assertEqual(result.text, "完成")
        self.assertEqual(calls, [True])

    def test_write_failure_is_final_and_never_replanned(self):
        calls = []

        @tool
        def save_record() -> str:
            """Save record."""
            calls.append(True)
            return '{"ok":false,"error":{"code":"OPERATION_OUTCOME_UNKNOWN"}}'

        agent, _ = build(
            [
                plan(("保存", ["save_record"])),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "save_record",
                            "args": {},
                            "id": "c1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="保存失败"),
            ],
            [save_record],
        )
        with self.assertRaises(ApplicationError):
            run(agent)
        self.assertEqual(calls, [True])

    def test_all_model_calls_share_the_run_budget(self):
        agent, _ = build(
            [plan(("查询", [])), AIMessage(content="结果"), verification()]
        )
        agent.limits = AgentLimits(max_iterations=2)
        with self.assertRaises(ApplicationError) as raised:
            run(agent)
        self.assertEqual(raised.exception.error.code, ErrorCode.LIMIT_EXCEEDED)

    def test_plan_activity_is_updated_in_history_instead_of_duplicated(self):
        doc = {"entries": [], "messages": []}
        for phase in ["executing", "completed"]:
            apply_event(
                doc,
                {
                    "type": "ACTIVITY_SNAPSHOT",
                    "messageId": "plan-1",
                    "activityType": "plan",
                    "content": {"phase": phase},
                },
            )
        self.assertEqual(len(doc["entries"]), 1)
        self.assertEqual(doc["entries"][0]["plan"]["phase"], "completed")

    def test_human_approval_resumes_the_same_plan(self):
        calls = []

        @tool
        def add_note(title: str, body: str) -> str:
            """Save note with confirmation."""
            calls.append(title)
            return '{"id":"n1"}'

        agent, _ = build(
            [
                plan(("保存笔记", ["add_note"])),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "add_note",
                            "args": {"title": "目标", "body": "内容"},
                            "id": "approval-call",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="已保存"),
                verification(),
                AIMessage(content="任务完成"),
            ],
            [add_note],
        )
        first, _ = run(agent)
        self.assertIsNotNone(first.approval)
        self.assertEqual(calls, [])
        task_id = agent.graph.get_state(agent._config("p")).values["task"]["id"]
        second, events = run(agent, approvals={"approval-call": True})
        self.assertEqual(second.text, "任务完成")
        self.assertEqual(calls, ["目标"])
        self.assertEqual(
            agent.graph.get_state(agent._config("p")).values["task"]["id"], task_id
        )
        self.assertTrue(any(e.type == "TOOL_CALL_RESULT" for e in events))

    def test_replan_cannot_introduce_write_tools(self):
        @tool
        def write_record() -> str:
            """Write record."""
            self.fail("Unexpected write")

        agent, _ = build(
            [
                plan(("分析", [])),
                AIMessage(content="不足"),
                verification(False),
                plan(("改为写入", ["write_record"])),
            ],
            [write_record],
        )
        with self.assertRaises(ApplicationError) as raised:
            run(agent)
        self.assertEqual(raised.exception.error.code, ErrorCode.VERIFICATION_FAILED)

    def test_new_question_gets_a_new_task_in_the_same_thread(self):
        direct = AIMessage(content='{"mode":"direct","goal":"问答","steps":[]}')
        agent, _ = build(
            [direct, AIMessage(content="第一次"), direct, AIMessage(content="第二次")]
        )
        run(agent)
        task_id = agent.graph.get_state(agent._config("p")).values["task"]["id"]
        result = agent.run_messages(
            [HumanMessage(id="u2", content="另一个问题")],
            thread_id="p",
            on_event=lambda *args: None,
            on_protocol_event=lambda _: None,
        )
        self.assertEqual(result.text, "第二次")
        self.assertNotEqual(
            agent.graph.get_state(agent._config("p")).values["task"]["id"], task_id
        )

    def test_executor_has_a_per_step_call_limit(self):
        @tool
        def lookup() -> str:
            """Read a record."""
            return "data"

        lookup.metadata = {"read_only": True}
        agent, _ = build(
            [
                plan(("查询", ["lookup"])),
                AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "lookup", "args": {}, "id": "c1", "type": "tool_call"}
                    ],
                ),
            ],
            [lookup],
            max_step_calls=1,
        )
        with self.assertRaises(ApplicationError) as raised:
            run(agent)
        self.assertEqual(raised.exception.error.code, ErrorCode.LIMIT_EXCEEDED)
        self.assertEqual(raised.exception.error.limit, "step_model_calls")

    def test_cli_observer_does_not_stream_internal_step_drafts(self):
        agent, _ = build(
            [
                plan(("分析", [])),
                AIMessage(content="内部草稿"),
                verification(),
                AIMessage(content="最终回答"),
            ]
        )
        events = []
        result = agent.run_messages(
            [HumanMessage(id="u", content="分析")],
            thread_id="cli",
            on_event=lambda kind, value: events.append((kind, value)),
        )
        output = "".join(v["delta"] for k, v in events if k == "text_delta")
        self.assertEqual(result.text, "最终回答")
        self.assertEqual(output, "最终回答")

    def test_cancelled_final_synthesis_continues_without_replanning(self):
        agent, _ = build(
            [
                plan(("分析", [])),
                AIMessage(content="步骤结果"),
                verification(),
                AIMessage(content="已有内容\n未完成"),
                AIMessage(content="继续内容"),
            ]
        )
        cancel = threading.Event()
        events = []

        def observe(event):
            events.append(event)
            if event.type == "TEXT_MESSAGE_CONTENT":
                cancel.set()

        with self.assertRaises(RunCancelled):
            agent.run_messages(
                [HumanMessage(id="u1", content="完成任务")],
                thread_id="p",
                cancel_event=cancel,
                on_event=lambda *args: None,
                on_protocol_event=observe,
            )
        delta = next(e for e in events if e.type == "TEXT_MESSAGE_CONTENT")
        task_id = agent.graph.get_state(agent._config("p")).values["task"]["id"]
        result, _ = run(
            agent,
            resume_cancelled=True,
            partial=PartialAnswer(delta.message_id, delta.delta),
        )
        self.assertEqual(result.text, delta.delta + "继续内容")
        state = agent.graph.get_state(agent._config("p")).values
        self.assertEqual(state["task"]["id"], task_id)
        self.assertEqual(state["task"]["phase"], "completed")


class PlanningPersistenceTests(unittest.TestCase):
    setUp = persistence.PersistenceTests.setUp
    tearDown = persistence.PersistenceTests.tearDown
    _drop_schema = persistence.PersistenceTests._drop_schema

    def test_rebuilt_graph_recovers_plan_and_does_not_repeat_completed_write(self):
        calls = []

        @tool
        def save_record() -> str:
            """Write record."""
            calls.append(True)
            return '{"saved":true}'

        saver = ThreadedPostgresSaver(self.history.pool)
        saver.setup()
        agent, middleware = build(
            [
                plan(("保存", ["save_record"]), ("汇总", [])),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "save_record",
                            "args": {},
                            "id": "c1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="已保存"),
                verification(),
                AIMessage(content="汇总结果"),
            ],
            [save_record],
            checkpointer=saver,
        )
        original = middleware.service.verify

        def cancel_second(task, step, evidence):
            if step.id == "s2":
                raise RunCancelled()
            return original(task, step, evidence)

        with patch.object(middleware.service, "verify", side_effect=cancel_second):
            with self.assertRaises(RunCancelled):
                run(agent)
        self.assertEqual(calls, [True])
        task_id = agent.graph.get_state(agent._config("p")).values["task"]["id"]
        rebuilt, _ = build(
            [verification(), AIMessage(content="最终结果")],
            [save_record],
            checkpointer=ThreadedPostgresSaver(self.history.pool),
        )
        result, events = run(rebuilt, resume_cancelled=True)
        self.assertEqual(result.text, "最终结果")
        self.assertEqual(calls, [True])
        task = rebuilt.graph.get_state(rebuilt._config("p")).values["task"]
        self.assertEqual(task["id"], task_id)
        self.assertEqual(
            [s["status"] for s in task["steps"]], ["succeeded", "succeeded"]
        )
        self.history.create(self.owner, "p")
        self.history.begin_run(self.owner, "p", "resume", resume_of=None, users=[])
        self.history.record(
            self.owner,
            "p",
            [
                e.model_dump(by_alias=True, mode="json")
                for e in events
                if e.type == "ACTIVITY_SNAPSHOT"
            ],
            generation_id="resume",
        )
        restored = self.history.get(self.owner, "p")
        self.assertEqual(restored["entries"][0]["plan"]["phase"], "completed")


if __name__ == "__main__":
    unittest.main()
