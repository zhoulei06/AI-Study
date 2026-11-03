import json
import os
import threading
import unittest
import uuid
from unittest.mock import patch

import httpx

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.store.memory import InMemoryStore
from psycopg import sql
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from agent.history import ConversationHistory
from agent.memory.extractor import MemoryExtractor
from agent.memory.jobs import MemoryJobs, MemoryWorker
from agent.memory.policy import PolicyEngine
from agent.memory.resolver import MemoryResolver
from agent.memory.postgres import database_url, open_pool
from agent.persistence.checkpoints import ThreadedPostgresSaver
from agent.server import app
from test_cancellation import make_agent


class FakeMemoryModel:
    model_name = "deepseek-chat"

    def model_copy(self, **kwargs):
        return self

    def invoke(self, *args, **kwargs):
        return AIMessage(
            content=json.dumps(
                [
                    dict(
                        text="用户喜欢简短回答",
                        kind="preference",
                        subject="preference.reply_style",
                        confidence=0.9,
                    )
                ]
            ),
            usage_metadata={
                "input_tokens": 20,
                "output_tokens": 10,
                "total_tokens": 30,
            },
        )


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        # Owner isolation is insufficient when a live worker polls all jobs.
        self.schema = "agent_test_" + uuid.uuid4().hex
        self.admin_pool = open_pool(database_url(), min_size=1, max_size=1)
        with self.admin_pool.connection() as conn:
            conn.execute(
                sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema))
            )
        pool = ConnectionPool(
            conninfo=database_url(),
            min_size=1,
            max_size=4,
            open=False,
            kwargs={
                "autocommit": True,
                "prepare_threshold": 0,
                "row_factory": dict_row,
                "options": f"-csearch_path={self.schema}",
            },
        )
        try:
            pool.open(wait=True, timeout=10)
            with patch("agent.history.open_pool", return_value=pool):
                self.history = ConversationHistory()
        except BaseException:
            pool.close()
            self._drop_schema()
            raise
        self.owner = "production-test-" + str(uuid.uuid4())
        self.jobs = MemoryJobs(self.history.pool)

    def tearDown(self):
        self.history.close()
        self._drop_schema()

    def _drop_schema(self):
        assert self.schema.startswith("agent_test_") and len(self.schema) == 43
        try:
            with self.admin_pool.connection() as conn:
                conn.execute(
                    sql.SQL("DROP SCHEMA {} CASCADE").format(
                        sql.Identifier(self.schema)
                    )
                )
        finally:
            self.admin_pool.close()

    def test_events_replay_with_cursor_and_owner_isolation(self):
        tid = str(uuid.uuid4())
        self.history.create(self.owner, tid)
        self.history.begin_run(self.owner, tid, "run", resume_of=None, users=[])
        self.history.record(
            self.owner,
            tid,
            [
                dict(type="TEXT_MESSAGE_START", messageId="answer"),
                dict(type="TEXT_MESSAGE_CONTENT", messageId="answer", delta="你好"),
                dict(type="RUN_FINISHED"),
            ],
            generation_id="run",
        )
        self.assertEqual(
            [row["seq"] for row in self.history.events(self.owner, tid, "run")],
            [1, 2, 3],
        )
        self.assertEqual(self.history.events("other", tid, "run"), [])
        with (
            patch.object(app.state, "history", self.history, create=True),
            patch.dict(os.environ, {"AGENT_HISTORY_OWNER": self.owner}),
        ):
            client = TestClient(
                app
            )  # No lifespan: never load embeddings or open a login page.
            try:
                response = client.get(
                    f"/agent/conversations/{tid}/runs/run/events",
                    headers={"Last-Event-ID": "run:1"},
                )
                self.assertEqual(response.status_code, 200)
                self.assertNotIn("id: run:1\n", response.text)
                self.assertIn("id: run:2\n", response.text)
                self.assertIn("RUN_FINISHED", response.text)
                self.assertEqual(
                    client.get(
                        f"/agent/conversations/{tid}/runs/run/events",
                        headers={"Last-Event-ID": "other:1"},
                    ).status_code,
                    400,
                )
                with patch.dict(os.environ, {"AGENT_HISTORY_OWNER": "other"}):
                    self.assertEqual(
                        client.get(
                            f"/agent/conversations/{tid}/runs/run/events"
                        ).status_code,
                        404,
                    )
            finally:
                client.close()

    def test_event_log_and_projection_roll_back_together(self):
        tid = str(uuid.uuid4())
        self.history.create(self.owner, tid)
        self.history.begin_run(self.owner, tid, "rollback", resume_of=None, users=[])
        with self.assertRaises(TypeError):
            self.history.record(
                self.owner,
                tid,
                [
                    dict(type="TEXT_MESSAGE_START", messageId="a"),
                    dict(type="TEXT_MESSAGE_CONTENT", messageId="a", delta=None),
                ],
                generation_id="rollback",
            )
        self.assertEqual(self.history.events(self.owner, tid, "rollback"), [])
        self.assertEqual(self.history.get(self.owner, tid)["entries"], [])

    def test_restart_adds_replayable_terminal_event(self):
        tid = str(uuid.uuid4())
        self.history.create(self.owner, tid)
        self.history.begin_run(self.owner, tid, "restart", resume_of=None, users=[])
        self.history.recover_interrupted(self.owner)
        self.assertEqual(self.history.get(self.owner, tid)["status"], "interrupted")
        event = self.history.events(self.owner, tid, "restart")[-1]["event"]
        self.assertEqual(event["type"], "RUN_FINISHED")
        self.assertEqual(
            event["outcome"]["interrupts"][0]["reason"], "service_restarted"
        )

    def test_memory_job_deduplicates_and_tracks_independent_usage(self):
        self.jobs.enqueue(self.owner, "run", "请记住我喜欢简短回答", "好的")
        self.jobs.enqueue(self.owner, "run", "重复", "重复")
        job = self.jobs.claim(self.owner)
        self.assertEqual(job["user_text"], "请记住我喜欢简短回答")
        worker = MemoryWorker.__new__(MemoryWorker)
        worker.jobs, worker.store = self.jobs, InMemoryStore()
        worker.extractor = MemoryExtractor(FakeMemoryModel())
        worker.policy, worker.resolver = PolicyEngine(), MemoryResolver()
        worker.stop = threading.Event()
        worker.process(job)
        self.assertIsNone(self.jobs.claim(self.owner))
        with self.history.pool.connection() as conn:
            row = conn.execute(
                "SELECT status,usage FROM agent_memory_jobs WHERE user_id=%s",
                (self.owner,),
            ).fetchone()
        self.assertEqual(row["status"], "completed")
        self.assertTrue(row["usage"]["tokens"].startswith("30/"))
        self.assertEqual(len(worker.store.search(("study", self.owner))), 1)
        # Re-applying a recovered job cannot create a second memory record.
        worker.process(job)
        self.assertEqual(len(worker.store.search(("study", self.owner))), 1)

    def test_memory_jobs_retry_transient_errors_without_nested_model_retries(self):
        class TransientModel(FakeMemoryModel):
            calls = 0

            def invoke(self, *args, **kwargs):
                self.calls += 1
                if self.calls <= 2:
                    request = httpx.Request("POST", "https://test.invalid")
                    raise httpx.HTTPStatusError(
                        "unavailable", request=request, response=httpx.Response(503)
                    )
                return super().invoke(*args, **kwargs)

        model = TransientModel()
        self.jobs.enqueue(self.owner, "retry-job", "问题", "回答")
        worker = MemoryWorker.__new__(MemoryWorker)
        worker.jobs, worker.store = self.jobs, InMemoryStore()
        worker.extractor, worker.policy, worker.resolver = (
            MemoryExtractor(model),
            PolicyEngine(),
            MemoryResolver(),
        )
        worker.stop = threading.Event()
        for attempt in range(1, 4):
            job = self.jobs.claim(self.owner)
            self.assertEqual(job["attempts"], attempt)
            worker.process(job)
            self.assertEqual(model.calls, attempt)
            with self.history.pool.connection() as conn:
                row = conn.execute(
                    "SELECT status,usage FROM agent_memory_jobs WHERE user_id=%s",
                    (self.owner,),
                ).fetchone()
                self.assertEqual(
                    row["status"], "pending" if attempt < 3 else "completed"
                )
                self.assertTrue(row["usage"]["iterations"].startswith("1/"))
                conn.execute(
                    "UPDATE agent_memory_jobs SET available_at=now() WHERE user_id=%s",
                    (self.owner,),
                )

    def test_memory_auth_failure_is_final_after_one_attempt(self):
        class AuthModel(FakeMemoryModel):
            def invoke(self, *args, **kwargs):
                request = httpx.Request("POST", "https://test.invalid")
                raise httpx.HTTPStatusError(
                    "unauthorized", request=request, response=httpx.Response(401)
                )

        self.jobs.enqueue(self.owner, "auth-job", "问题", "回答")
        worker = MemoryWorker.__new__(MemoryWorker)
        worker.jobs, worker.store = self.jobs, InMemoryStore()
        worker.extractor, worker.policy, worker.resolver = (
            MemoryExtractor(AuthModel()),
            PolicyEngine(),
            MemoryResolver(),
        )
        worker.stop = threading.Event()
        worker.process(self.jobs.claim(self.owner))
        self.assertIsNone(self.jobs.claim(self.owner))
        with self.history.pool.connection() as conn:
            row = conn.execute(
                "SELECT status,attempts,error_info FROM agent_memory_jobs WHERE user_id=%s",
                (self.owner,),
            ).fetchone()
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["attempts"], 1)
        self.assertEqual(row["error_info"]["code"], "AUTHENTICATION_FAILED")
        self.assertFalse(row["error_info"]["retryable"])

    def test_expired_job_lease_can_be_reclaimed_but_old_worker_cannot_finish(self):
        self.jobs.enqueue(self.owner, "lease", "问题", "回答")
        old = self.jobs.claim(self.owner)
        with self.history.pool.connection() as conn:
            conn.execute(
                "UPDATE agent_memory_jobs SET lease_until=now()-interval '1 second' WHERE user_id=%s",
                (self.owner,),
            )
        new = self.jobs.claim(self.owner)
        self.assertNotEqual(old["lease_token"], new["lease_token"])
        self.jobs.finish(old, usage={})
        self.assertTrue(self.jobs.owns(new))
        self.jobs.finish(new, usage={})

    def test_official_adapter_with_real_postgres_checkpoints(self):
        saver = ThreadedPostgresSaver(self.history.pool)
        saver.setup()
        tid = str(uuid.uuid4())
        try:
            agent = make_agent([AIMessage(content="持久回答")], checkpointer=saver)
            events = []
            result = agent.run_messages(
                [HumanMessage(id="user", content="测试")],
                thread_id=tid,
                on_event=lambda *args: None,
                on_protocol_event=events.append,
            )
            self.assertEqual(result.text, "持久回答")
            self.assertEqual(
                saver.get_tuple(agent._config(tid))
                .checkpoint["channel_values"]["messages"][-1]
                .content,
                "持久回答",
            )
        finally:
            saver.delete_thread(tid)
