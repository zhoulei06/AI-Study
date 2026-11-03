"""Request-local state; ContextVars propagate into LangGraph worker threads."""

from __future__ import annotations

import threading
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
from typing import Any, Callable

from ..cancellation import check_cancelled
from ..limits import LimitTracker


class LimitStop(Exception):
    def __init__(self, reason: str, hint: str):
        super().__init__(hint)
        self.reason, self.hint = reason, hint


@dataclass
class RunContext:
    tracker: LimitTracker
    thread_id: str
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    owner: str = "local"
    cancel: threading.Event | None = None
    log: Callable[[str], None] = lambda _line: None
    protocol_sink: Callable[[Any], None] | None = None
    streamed_text: bool = False
    model_max_attempts: int | None = None
    uncertain_writes: set[str] = field(default_factory=set)
    tool_lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def check(self) -> None:
        check_cancelled(self.cancel)
        reason = self.tracker.after_usage()
        if reason:
            raise LimitStop(reason, f"运行达到限制：{reason}")


_current: ContextVar[RunContext | None] = ContextVar("agent_run_context", default=None)


def current_run() -> RunContext | None:
    return _current.get()


@contextmanager
def bind_run(context: RunContext):
    token = _current.set(context)
    try:
        yield context
    finally:
        _current.reset(token)


def isolated_run(method):
    """Bind one context around all paths, including cancellation continuation."""

    @wraps(method)
    def wrapped(self, *args, **kwargs):
        tracker = kwargs.get("tracker")
        if tracker is None:
            tracker = (
                self.tracker
                if method.__name__ == "chat"
                else LimitTracker(self.limits, self.model)
            )
        if method.__name__ != "chat":
            kwargs["tracker"] = tracker
        context = RunContext(
            tracker=tracker,
            thread_id=kwargs.get("thread_id", getattr(self, "thread_id", "")),
            run_id=kwargs.get("run_id") or str(uuid.uuid4()),
            owner=kwargs.get("owner", "local"),
            request_id=kwargs.get("request_id") or str(uuid.uuid4()),
            cancel=kwargs.get("cancel_event"),
            log=self._log,
            protocol_sink=kwargs.get("on_protocol_event"),
        )
        with bind_run(context):
            return method(self, *args, **kwargs)

    return wrapped


def invoke_auxiliary(model: Any, messages: list, *, purpose: str) -> Any:
    """Account for summary/extraction calls and keep them out of the UI stream."""
    from ..cancellation import CancellationCallback
    from .retry import (
        RetryPolicy,
        call_with_retry,
        current_attempt,
        reserve_model_attempt,
    )
    from .models import configure_model

    context = current_run()

    def operation():
        attempt = current_attempt()
        configured = configure_model(model, timeout=attempt.timeout)
        return configured.invoke(
            messages,
            config={
                "tags": ["agent:internal"],
                "callbacks": [CancellationCallback(context.cancel, context.tracker)]
                if context is not None
                else [],
            },
            timeout=attempt.timeout,
        )

    response = call_with_retry(
        operation,
        policy=RetryPolicy.configured("model"),
        label=purpose,
        before_attempt=reserve_model_attempt,
    )
    if context is not None:
        usage = getattr(response, "usage_metadata", None) or {}
        context.tracker.record_usage(
            usage.get("input_tokens", 0), usage.get("output_tokens", 0)
        )
        context.check()
    return response
