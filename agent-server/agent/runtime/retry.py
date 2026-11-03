"""One retry owner per operation, with deadlines, cancellation and stream guards."""

from __future__ import annotations

import asyncio
import logging
import math
import os
import random
import ssl
import threading
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx
import httpx2

from ..cancellation import check_cancelled, ModelCallCancelled, ModelDeadlineExceeded
from .context import current_run, LimitStop

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3  # Total attempts, including the first request.
    base_delay: float = 0.5
    max_delay: float = 5.0
    attempt_timeout: float = 15.0
    total_timeout: float = 60.0

    def __post_init__(self):
        if not 1 <= self.max_attempts <= 10:
            raise ValueError("retry max_attempts must be between 1 and 10")
        values = (
            self.base_delay,
            self.max_delay,
            self.attempt_timeout,
            self.total_timeout,
        )
        if not all(math.isfinite(v) for v in values) or min(values) < 0:
            raise ValueError("retry durations must be finite and non-negative")
        if min(self.attempt_timeout, self.total_timeout) <= 0:
            raise ValueError("retry timeouts must be positive")

    @classmethod
    def configured(cls, operation: str, *, safe: bool = True):
        attempts = (
            int(os.getenv(f"{operation.upper()}_MAX_ATTEMPTS", "3")) if safe else 1
        )
        context = current_run()
        if (
            operation == "model"
            and context is not None
            and context.model_max_attempts is not None
        ):
            attempts = context.model_max_attempts
        startup = operation == "mcp_connect"
        single_mcp = operation == "mcp_read" and not safe
        timeout = (
            float(os.getenv("MODEL_ATTEMPT_TIMEOUT", "60"))
            if operation == "model"
            else float(os.getenv("RETRY_ATTEMPT_TIMEOUT", "15"))
        )
        return cls(
            max_attempts=attempts,
            base_delay=float(os.getenv("RETRY_BASE_DELAY", "0.5")),
            max_delay=float(os.getenv("RETRY_MAX_DELAY", "5")),
            attempt_timeout=330 if startup else (180 if single_mcp else timeout),
            total_timeout=360 if startup else (180 if single_mcp else 60),
        )


@dataclass
class Attempt:
    number: int
    timeout: float
    output_started: bool = False
    stop_event: threading.Event | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def mark_output(self):
        with self._lock:
            self.output_started = True


_attempt: ContextVar[Attempt | None] = ContextVar("retry_attempt", default=None)


def current_attempt() -> Attempt | None:
    return _attempt.get()


def mark_model_output():
    attempt = current_attempt()
    if attempt is not None:
        attempt.mark_output()


def _has_permanent_cause(exc):
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, ssl.SSLCertVerificationError) or type(exc).__name__ in {
            "OAuthFlowError",
            "OAuthTokenError",
            "OAuthRegistrationError",
        }:
            return True
        exc = exc.__cause__
    return False


def is_transient(exc: BaseException, _seen=None) -> bool:
    """Positive allowlist. Business errors, auth, cancellation and TLS stay final."""
    seen = set() if _seen is None else _seen
    if id(exc) in seen:
        return False
    seen.add(id(exc))
    if _has_permanent_cause(exc):
        return False
    if not isinstance(exc, Exception) or isinstance(
        exc,
        (
            LimitStop,
            ModelCallCancelled,
            ModelDeadlineExceeded,
            ValueError,
            TypeError,
            PermissionError,
            ssl.SSLCertVerificationError,
        ),
    ):
        return False
    if type(exc).__name__ in {
        "OAuthFlowError",
        "OAuthTokenError",
        "OAuthRegistrationError",
    }:
        return False
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    if status is not None:
        return status in {408, 429, 500, 502, 503, 504}
    children = getattr(exc, "exceptions", None)
    if children:
        return all(is_transient(child, seen.copy()) for child in children)
    cause = exc.__cause__
    if cause is not None and isinstance(cause, ssl.SSLCertVerificationError):
        return False
    network_types = (
        ConnectionError,
        TimeoutError,
        httpx.TimeoutException,
        httpx.NetworkError,
        httpx.RemoteProtocolError,
        httpx2.TimeoutException,
        httpx2.NetworkError,
        httpx2.RemoteProtocolError,
    )
    if isinstance(exc, network_types):
        return True
    # OpenAI SDK wraps transport errors; keep the concrete HTTP error as source.
    if type(exc).__name__ in {"APIConnectionError", "APITimeoutError"}:
        return is_transient(cause, seen) if cause is not None else True
    sqlstate = getattr(exc, "sqlstate", None)
    if isinstance(sqlstate, str):
        return sqlstate.startswith("08") or sqlstate in {
            "40001",
            "40P01",
            "53300",
            "57P01",
        }
    return is_transient(cause, seen) if cause is not None else False


def retry_after(exc: BaseException) -> float | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", {})
    value = headers.get("retry-after-ms")
    try:
        if value is not None:
            seconds = float(value) / 1000
        elif headers.get("retry-after") is not None:
            value = headers["retry-after"]
            try:
                seconds = float(value)
            except ValueError:
                stamp = parsedate_to_datetime(value)
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=timezone.utc)
                seconds = (stamp - datetime.now(timezone.utc)).total_seconds()
        else:
            return retry_after(exc.__cause__) if exc.__cause__ is not None else None
        return max(0.0, seconds) if math.isfinite(seconds) else None
    except (ValueError, TypeError, OverflowError):
        return None


def retry_delay(policy: RetryPolicy, attempt: int, exc: BaseException) -> float:
    hint = retry_after(exc)
    if hint is not None:
        return hint  # Never retry sooner than the server's requested delay.
    ceiling = min(policy.max_delay, policy.base_delay * 2 ** min(attempt - 1, 10))
    return random.uniform(ceiling / 2, ceiling)


def _check(stop_event=None):
    check_cancelled(stop_event)
    context = current_run()
    if context is not None:
        context.check()


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    context = current_run()
    if context is not None:
        remaining = min(
            remaining, context.tracker.limits.timeout - context.tracker.elapsed
        )
    return remaining


def _prepare(policy, number, deadline, before_attempt, stop_event):
    _check(stop_event)
    remaining = _remaining(deadline)
    if remaining <= 0:
        raise TimeoutError("操作重试总期限已用尽")
    if before_attempt is not None:
        before_attempt(number)
    context = current_run()
    if number > 1 and context is not None:
        context.tracker.record_retry()
    return Attempt(
        number, min(policy.attempt_timeout, remaining), stop_event=stop_event
    )


def _should_wait(policy, number, state, exc, deadline, retry_if, label):
    if number >= policy.max_attempts or state.output_started:
        return None
    if not (retry_if(exc) if retry_if is not None else is_transient(exc)):
        return None
    delay = retry_delay(policy, number, exc)
    if delay >= _remaining(deadline):
        return None
    message = f"{label}: retry {number + 1}/{policy.max_attempts}, delay={delay:.2f}s, error={type(exc).__name__}"
    context = current_run()
    if context is not None:
        context.log(message)
    else:
        logger.info(message)
    return delay


def call_with_retry(
    operation, *, policy, label, before_attempt=None, retry_if=None, stop_event=None
):
    deadline = time.monotonic() + policy.total_timeout
    for number in range(1, policy.max_attempts + 1):
        state = _prepare(policy, number, deadline, before_attempt, stop_event)
        token = _attempt.set(state)
        try:
            return operation()
        except Exception as exc:
            _check(stop_event)
            delay = _should_wait(policy, number, state, exc, deadline, retry_if, label)
            if delay is None:
                from ..errors import annotate_failure
                annotate_failure(exc, label=label, attempts=number,
                                 max_attempts=policy.max_attempts, partial=state.output_started)
                raise
        finally:
            _attempt.reset(token)
        end = time.monotonic() + delay
        while time.monotonic() < end:
            _check(stop_event)
            wait = min(0.05, end - time.monotonic())
            if stop_event is not None:
                stop_event.wait(max(0, wait))
            else:
                context = current_run()
                if context is not None and context.cancel is not None:
                    context.cancel.wait(max(0, wait))
                else:
                    time.sleep(max(0, wait))


async def acall_with_retry(
    operation, *, policy, label, before_attempt=None, retry_if=None, stop_event=None
):
    deadline = time.monotonic() + policy.total_timeout
    for number in range(1, policy.max_attempts + 1):
        state = _prepare(policy, number, deadline, before_attempt, stop_event)
        token = _attempt.set(state)
        try:
            return await asyncio.wait_for(operation(), timeout=state.timeout)
        except Exception as exc:
            _check(stop_event)
            delay = _should_wait(policy, number, state, exc, deadline, retry_if, label)
            if delay is None:
                from ..errors import annotate_failure
                annotate_failure(exc, label=label, attempts=number,
                                 max_attempts=policy.max_attempts, partial=state.output_started)
                raise
        finally:
            _attempt.reset(token)
        end = time.monotonic() + delay
        while time.monotonic() < end:
            _check(stop_event)
            await asyncio.sleep(max(0, min(0.05, end - time.monotonic())))


def reserve_model_attempt(_number):
    context = current_run()
    if context is not None:
        reason = context.tracker.start_model()
        if reason:
            raise LimitStop(reason, f"模型调用前达到限制：{reason}")


def reserve_tool_retry(number):
    # Initial dispatch is reserved by BudgetMiddleware (or CLI settlement).
    context = current_run()
    if number > 1 and context is not None:
        reason = context.tracker.start_tool()
        if reason:
            raise LimitStop(reason, f"工具重试前达到限制：{reason}")
