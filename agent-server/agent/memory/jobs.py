"""Durable memory extraction with bounded retries and independent usage budgets."""

from __future__ import annotations

import logging
import threading
import uuid

from psycopg.types.json import Jsonb

from ..cancellation import RunCancelled
from ..limits import AgentLimits, LimitTracker
from ..runtime.context import RunContext, bind_run
from ..runtime.retry import RetryPolicy, is_transient, retry_delay
from ..errors import ErrorCode, make_error, normalize_error
from .long_term import MemoryRepository

logger = logging.getLogger(__name__)


class MemoryJobs:
    def __init__(self, pool):
        self.pool = pool

    def enqueue(self, user_id, run_id, user_text, assistant_text):
        with self.pool.connection() as conn:
            conn.execute(
                """INSERT INTO agent_memory_jobs(user_id,run_id,user_text,assistant_text)
                VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                (user_id, run_id, user_text, assistant_text),
            )

    def claim(self, user_id=None):
        token = str(uuid.uuid4())
        with self.pool.connection() as conn, conn.transaction():
            # A process crash leaves a lease, not an indefinitely 'running' job.
            exhausted = conn.execute(
                """UPDATE agent_memory_jobs SET status='failed', error='LIMIT_EXCEEDED'
                WHERE status='running' AND lease_until < now() AND attempts >= 3
                AND (%s::text IS NULL OR user_id=%s) RETURNING user_id,run_id,attempts""",
                (user_id, user_id),
            ).fetchall()
            for row in exhausted:
                error = make_error(
                    ErrorCode.LIMIT_EXCEEDED,
                    source="memory",
                    run_id=row["run_id"],
                    attempts=row["attempts"],
                    max_attempts=3,
                    retry_exhausted=True,
                    limit="memory_attempts",
                )
                conn.execute(
                    "UPDATE agent_memory_jobs SET error_info=%s WHERE user_id=%s AND run_id=%s",
                    (Jsonb(error.public()), row["user_id"], row["run_id"]),
                )
            row = conn.execute(
                """SELECT * FROM agent_memory_jobs
                WHERE attempts < 3 AND (%s::text IS NULL OR user_id=%s)
                  AND ((status='pending' AND available_at <= now())
                    OR (status='running' AND lease_until < now()))
                ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1""",
                (user_id, user_id),
            ).fetchone()
            if row is None:
                return None
            return conn.execute(
                """UPDATE agent_memory_jobs SET status='running', attempts=attempts+1,
                lease_token=%s,lease_until=now()+interval '5 minutes',updated_at=now()
                WHERE user_id=%s AND run_id=%s RETURNING *""",
                (token, row["user_id"], row["run_id"]),
            ).fetchone()

    def owns(self, job):
        with self.pool.connection() as conn:
            return (
                conn.execute(
                    """SELECT 1 FROM agent_memory_jobs WHERE user_id=%s AND run_id=%s
                AND lease_token=%s AND status='running' AND lease_until > now()""",
                    (job["user_id"], job["run_id"], job["lease_token"]),
                ).fetchone()
                is not None
            )

    def finish(
        self, job, *, usage, error=None, retryable=True, delay=10, error_info=None
    ):
        if delay > 86400:
            retryable = False  # Do not shorten a server's long Retry-After hint.
        status = (
            "completed"
            if error is None
            else ("failed" if job["attempts"] >= 3 or not retryable else "pending")
        )
        with self.pool.connection() as conn:
            conn.execute(
                """UPDATE agent_memory_jobs SET status=%s,error=%s,usage=%s,error_info=%s,
                lease_until=NULL,available_at=now()+(%s * interval '1 second'),updated_at=now()
                WHERE user_id=%s AND run_id=%s AND lease_token=%s AND status='running'""",
                (
                    status,
                    error,
                    Jsonb(usage),
                    Jsonb(error_info) if error_info is not None else None,
                    min(max(delay, 0), 86400),
                    job["user_id"],
                    job["run_id"],
                    job["lease_token"],
                ),
            )


class MemoryWorker:
    def __init__(self, jobs, store, extractor, policy, resolver):
        self.jobs, self.store = jobs, store
        self.extractor, self.policy, self.resolver = extractor, policy, resolver
        self.stop = threading.Event()
        self.thread = threading.Thread(
            target=self._loop, name="memory-worker", daemon=True
        )
        self.thread.start()

    def close(self):
        self.stop.set()
        self.thread.join(timeout=40)
        if self.thread.is_alive():
            raise RuntimeError("长期记忆任务未能在关闭期限内停止")

    def process(self, job):
        if not self.jobs.owns(job):
            return
        limits = AgentLimits(
            max_iterations=2, timeout=30, token_budget=8000, cost_budget=0.01
        )
        tracker = LimitTracker(
            limits, getattr(self.extractor.model, "model_name", "deepseek-chat")
        )
        error = None
        error_info = None
        retryable = True
        delay = 0
        try:
            if len(job["user_text"]) + len(job["assistant_text"]) > limits.token_budget:
                raise ValueError("记忆输入超过独立任务预算")
            with bind_run(
                RunContext(
                    tracker,
                    thread_id="memory",
                    run_id=job["run_id"],
                    owner=job["user_id"],
                    cancel=self.stop,
                    model_max_attempts=1,  # The durable queue owns all retries.
                )
            ):
                candidates = self.extractor.extract(
                    job["user_text"], job["assistant_text"]
                )
                repository = MemoryRepository(self.store, job["user_id"])
                for candidate in candidates:
                    if self.stop.is_set() or not self.jobs.owns(job):
                        raise RunCancelled()
                    review = self.policy.review(candidate)
                    if review.allowed:
                        self.resolver.apply(repository, candidate, review.score)
        except RunCancelled:
            info = make_error(
                ErrorCode.RUN_CANCELLED, source="memory", run_id=job["run_id"]
            )
            error, error_info = info.code.value, info.public()
        except Exception as exc:
            # Store a category, not provider errors that may contain secrets.
            retryable = is_transient(exc)
            info = normalize_error(
                exc,
                source="memory",
                run_id=job["run_id"],
                attempts=job["attempts"],
                max_attempts=3,
                retry_exhausted=job["attempts"] >= 3 and retryable,
            )
            error, error_info = info.code.value, info.public()
            delay = retry_delay(
                RetryPolicy(base_delay=10, max_delay=60), job["attempts"], exc
            )
            logger.warning(
                "Memory job %s failed request_id=%s code=%s type=%s",
                job["run_id"],
                info.request_id,
                error,
                type(exc).__name__,
            )
        self.jobs.finish(
            job,
            usage=tracker.snapshot(),
            error=error,
            retryable=retryable,
            delay=delay,
            error_info=error_info,
        )

    def _loop(self):
        while not self.stop.is_set():
            try:
                job = self.jobs.claim()
                if job is not None:
                    self.process(job)
                    continue
            except Exception as exc:
                error = normalize_error(exc, source="history")
                logger.error(
                    "Memory worker storage failure request_id=%s code=%s type=%s",
                    error.request_id,
                    error.code.value,
                    type(exc).__name__,
                )
            self.stop.wait(0.5)
