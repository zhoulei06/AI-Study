"""Cooperative cancellation at model/tool boundaries (one HTTP worker process)."""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from langchain_core.callbacks import BaseCallbackHandler


class RunCancelled(BaseException):
    """Do not let tool error handlers turn cancellation into a tool result."""


class ModelCallCancelled(Exception):
    """LangChain async model aggregation requires an Exception, not BaseException."""


class ModelDeadlineExceeded(Exception):
    pass


@dataclass
class ActiveRun:
    run_id: str
    cancel: threading.Event = field(default_factory=threading.Event)


class RunRegistry:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.active: dict[tuple[str, str], ActiveRun] = {}


def check_cancelled(event: threading.Event | None) -> None:
    if event is not None and event.is_set():
        raise RunCancelled()


class CancellationCallback(BaseCallbackHandler):
    """Stop the producer itself, rather than just closing its event consumer."""
    raise_error = True

    def __init__(self, event: threading.Event | None, tracker=None) -> None:
        self.event = event
        self.tracker = tracker

    def on_llm_new_token(self, token: str, **kwargs) -> None:
        from .runtime.retry import mark_model_output
        mark_model_output()
        if self.event is not None and self.event.is_set():
            raise ModelCallCancelled("模型生成已取消")
        if self.tracker is not None and self.tracker.elapsed >= self.tracker.limits.timeout:
            raise ModelDeadlineExceeded("模型调用达到运行期限")
