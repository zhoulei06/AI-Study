"""Claim, execute and release runs; transport code only streams the result."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..cancellation import ActiveRun
from ..continuation import PartialAnswer
from ..limits import LimitTracker


@dataclass
class RunClaim:
    owner: str
    thread_id: str
    run_id: str
    active: ActiveRun
    persisted: dict[str, Any]
    request_id: str | None = None


class RunService:
    def __init__(self, history, registry, execution_lock):
        self.history, self.registry, self.execution_lock = (
            history,
            registry,
            execution_lock,
        )

    def claim(self, owner, thread_id, run_id, *, resume_of, users, request_id=None) -> RunClaim:
        # MCP currently represents one authenticated user and one elicitation
        # channel. Keep admission serialized until delegated MCP sessions exist.
        if not self.execution_lock.acquire(blocking=False):
            raise ValueError("Agent 正在处理其他请求，请稍后重试")
        try:
            with self.registry.lock:
                persisted = self.history.begin_run(
                    owner, thread_id, run_id, resume_of=resume_of, users=users
                )
                active = ActiveRun(run_id)
                self.registry.active[(owner, thread_id)] = active
            return RunClaim(owner, thread_id, run_id, active, persisted, request_id)
        except BaseException:
            self.execution_lock.release()
            raise

    def execute(
        self,
        claim: RunClaim,
        agent,
        messages,
        *,
        approvals,
        observe,
        protocol_sink,
        on_elicit,
        continuing,
    ):
        if agent.mcp is not None:
            agent.mcp.set_elicit_ui(on_elicit, cancel_event=claim.active.cancel)
        return agent.run_messages(
            messages,
            tracker=LimitTracker(agent.limits, agent.model),
            on_event=observe,
            approvals=approvals,
            thread_id=claim.thread_id,
            cancel_event=claim.active.cancel,
            resume_cancelled=continuing,
            partial=PartialAnswer(**claim.persisted["partial"])
            if claim.persisted["partial"]
            else None,
            abandon_cancelled=claim.persisted["abandon"],
            on_protocol_event=protocol_sink,
            run_id=claim.run_id,
            owner=claim.owner,
            request_id=claim.request_id,
        )

    def release(self, claim: RunClaim, agent) -> None:
        try:
            if agent.mcp is not None:
                agent.mcp.set_elicit_ui(None)
        finally:
            with self.registry.lock:
                self.registry.active.pop((claim.owner, claim.thread_id), None)
            self.execution_lock.release()
