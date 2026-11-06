"""Versioned public errors. Never serialize provider exceptions or request input."""

from __future__ import annotations

import json
import re
import ssl
import uuid
from dataclasses import dataclass, replace
from enum import Enum


class ErrorCode(str, Enum):
    INTERNAL_ERROR = "INTERNAL_ERROR"
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    NOT_FOUND = "NOT_FOUND"
    RUN_CONFLICT = "RUN_CONFLICT"
    AGENT_NOT_READY = "AGENT_NOT_READY"
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    RATE_LIMITED = "RATE_LIMITED"
    NETWORK_ERROR = "NETWORK_ERROR"
    UPSTREAM_TIMEOUT = "UPSTREAM_TIMEOUT"
    UPSTREAM_UNAVAILABLE = "UPSTREAM_UNAVAILABLE"
    UPSTREAM_REJECTED = "UPSTREAM_REJECTED"
    TLS_ERROR = "TLS_ERROR"
    TOOL_EXECUTION_FAILED = "TOOL_EXECUTION_FAILED"
    TOOL_NOT_FOUND = "TOOL_NOT_FOUND"
    TOOL_REJECTED = "TOOL_REJECTED"
    OPERATION_OUTCOME_UNKNOWN = "OPERATION_OUTCOME_UNKNOWN"
    LIMIT_EXCEEDED = "LIMIT_EXCEEDED"
    RUN_CANCELLED = "RUN_CANCELLED"
    STREAM_INTERRUPTED = "STREAM_INTERRUPTED"
    PERSISTENCE_FAILED = "PERSISTENCE_FAILED"
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"
    PLANNING_FAILED = "PLANNING_FAILED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"


MESSAGES = {
    ErrorCode.INTERNAL_ERROR: "服务处理失败，请稍后重试或联系维护人员。",
    ErrorCode.INVALID_ARGUMENT: "请求参数不合法，请检查输入。",
    ErrorCode.NOT_FOUND: "请求的资源不存在或不可访问。",
    ErrorCode.RUN_CONFLICT: "会话状态已变化或已有运行，请刷新后重试。",
    ErrorCode.AGENT_NOT_READY: "Agent 正在初始化或暂不可用，请稍后重试。",
    ErrorCode.AUTHENTICATION_FAILED: "服务认证失败，请检查登录或服务端凭据配置。",
    ErrorCode.PERMISSION_DENIED: "当前身份没有执行此操作的权限。",
    ErrorCode.RATE_LIMITED: "上游服务请求过于频繁，请稍后重试。",
    ErrorCode.NETWORK_ERROR: "连接上游服务失败，请检查服务和网络状态。",
    ErrorCode.UPSTREAM_TIMEOUT: "上游服务响应超时，请稍后重试。",
    ErrorCode.UPSTREAM_UNAVAILABLE: "上游服务暂不可用，请稍后重试。",
    ErrorCode.UPSTREAM_REJECTED: "上游服务拒绝了请求，请检查服务配置。",
    ErrorCode.TLS_ERROR: "服务证书校验失败，请检查证书配置。",
    ErrorCode.TOOL_EXECUTION_FAILED: "工具执行失败，请检查输入或联系维护人员。",
    ErrorCode.TOOL_NOT_FOUND: "请求的工具不存在。",
    ErrorCode.TOOL_REJECTED: "工具服务未能完成操作，请检查参数和权限。",
    ErrorCode.OPERATION_OUTCOME_UNKNOWN: "写操作结果未知，远端可能已经执行。请先核对结果，不要重复提交。",
    ErrorCode.LIMIT_EXCEEDED: "运行已达到时间、次数或用量限制，已停止执行。",
    ErrorCode.RUN_CANCELLED: "运行已取消。",
    ErrorCode.STREAM_INTERRUPTED: "回答生成途中发生错误，已有内容已保留，请重新发起请求。",
    ErrorCode.PERSISTENCE_FAILED: "运行记录保存失败，请重新加载会话。",
    ErrorCode.CONFIGURATION_ERROR: "服务配置不正确，请联系维护人员检查。",
    ErrorCode.PLANNING_FAILED: "未能生成有效的执行计划，请补充目标或缩小任务范围。",
    ErrorCode.VERIFICATION_FAILED: "任务步骤未通过验证，已停止自动执行。请检查工具结果或调整目标。",
}


@dataclass(frozen=True)
class ErrorInfo:
    code: ErrorCode
    source: str
    request_id: str
    operation: str | None = None
    run_id: str | None = None
    thread_id: str | None = None
    retryable: bool = False
    attempts: int = 0
    max_attempts: int = 0
    retry_exhausted: bool = False
    partial: bool = False
    outcome_unknown: bool = False
    cause_code: ErrorCode | None = None
    limit: str | None = None

    @property
    def message(self):
        return MESSAGES[self.code]

    def public(self):
        result = {
            "version": 1,
            "code": self.code.value,
            "message": self.message,
            "source": self.source,
            "requestId": self.request_id,
            "retryable": self.retryable,
            "attempts": self.attempts,
            "maxAttempts": self.max_attempts,
            "retryExhausted": self.retry_exhausted,
            "partial": self.partial,
            "outcomeUnknown": self.outcome_unknown,
        }
        optional = {
            "operation": self.operation,
            "runId": self.run_id,
            "threadId": self.thread_id,
            "causeCode": self.cause_code.value if self.cause_code else None,
            "limit": self.limit,
        }
        result.update(
            {key: value for key, value in optional.items() if value is not None}
        )
        return result

    def tool_result(self):
        return json.dumps({"ok": False, "error": self.public()}, ensure_ascii=False)


class ApplicationError(RuntimeError):
    def __init__(self, error: ErrorInfo):
        super().__init__(error.message)
        self.error = error


def make_error(code: ErrorCode, *, source="runtime", **kwargs):
    from .runtime.context import current_run

    context = current_run()
    values = {
        "request_id": context.request_id if context else str(uuid.uuid4()),
        "run_id": context.run_id if context else None,
        "thread_id": context.thread_id if context else None,
    }
    values.update(kwargs)
    operation = values.get("operation")
    if operation is not None and not re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", operation):
        values["operation"] = "operation"
    return ErrorInfo(code=code, source=source, **values)


def _chain(exc):
    pending, seen = [exc], set()
    while pending:
        item = pending.pop(0)
        if id(item) in seen:
            continue
        seen.add(id(item))
        yield item
        if item.__cause__ is not None:
            pending.append(item.__cause__)
        pending.extend(getattr(item, "exceptions", ()) or ())


def normalize_error(exc, *, source="runtime", operation=None, **overrides):
    from .runtime.context import LimitStop, current_run
    from .cancellation import RunCancelled, ModelCallCancelled, ModelDeadlineExceeded
    from .runtime.retry import is_transient

    known = (
        getattr(exc, "error", None)
        if isinstance(exc, ApplicationError)
        else getattr(exc, "agent_error", None)
    )
    if isinstance(known, ErrorInfo):
        # Outer transports must preserve the original classified source/cause.
        result = replace(known, **overrides) if overrides else known
        if result.partial and result.code in {
            ErrorCode.NETWORK_ERROR,
            ErrorCode.UPSTREAM_TIMEOUT,
            ErrorCode.UPSTREAM_UNAVAILABLE,
            ErrorCode.RATE_LIMITED,
        }:
            result = replace(
                result,
                code=ErrorCode.STREAM_INTERRUPTED,
                cause_code=result.code,
                retryable=False,
            )
        return result
    chain = list(_chain(exc))
    code = ErrorCode.INTERNAL_ERROR
    limit = None
    if any(isinstance(e, (RunCancelled, ModelCallCancelled)) for e in chain):
        code = ErrorCode.RUN_CANCELLED
    elif any(isinstance(e, LimitStop) for e in chain):
        code = ErrorCode.LIMIT_EXCEEDED
        limit = next(e.reason for e in chain if isinstance(e, LimitStop))
    elif any(isinstance(e, ModelDeadlineExceeded) for e in chain):
        code, limit = ErrorCode.LIMIT_EXCEEDED, "timeout"
    elif any(isinstance(e, ssl.SSLCertVerificationError) for e in chain):
        code = ErrorCode.TLS_ERROR
    elif any(
        type(e).__name__
        in {"OAuthFlowError", "OAuthTokenError", "OAuthRegistrationError"}
        for e in chain
    ):
        code = ErrorCode.AUTHENTICATION_FAILED
    else:
        statuses = [
            getattr(e, "status_code", None)
            or getattr(getattr(e, "response", None), "status_code", None)
            for e in chain
        ]
        status = next((s for s in statuses if isinstance(s, int)), None)
        if status is not None:
            code = {
                400: ErrorCode.UPSTREAM_REJECTED,
                401: ErrorCode.AUTHENTICATION_FAILED,
                403: ErrorCode.PERMISSION_DENIED,
                404: ErrorCode.NOT_FOUND,
                408: ErrorCode.UPSTREAM_TIMEOUT,
                429: ErrorCode.RATE_LIMITED,
            }.get(
                status,
                ErrorCode.UPSTREAM_UNAVAILABLE
                if status >= 500
                else ErrorCode.UPSTREAM_REJECTED,
            )
        elif any(isinstance(e, PermissionError) for e in chain):
            code = ErrorCode.PERMISSION_DENIED
        elif any(
            isinstance(e, TimeoutError) or "Timeout" in type(e).__name__ for e in chain
        ):
            code = ErrorCode.UPSTREAM_TIMEOUT
        elif is_transient(exc):
            code = ErrorCode.NETWORK_ERROR
        elif isinstance(exc, (ValueError, TypeError)):
            code = (
                ErrorCode.INVALID_ARGUMENT
                if source in {"api", "tool", "mcp"}
                else ErrorCode.CONFIGURATION_ERROR
            )
        elif source == "tool":
            code = ErrorCode.TOOL_EXECUTION_FAILED
    context = current_run()
    partial = overrides.pop("partial", bool(context and context.streamed_text))
    cause_code = None
    if partial and code in {
        ErrorCode.NETWORK_ERROR,
        ErrorCode.UPSTREAM_TIMEOUT,
        ErrorCode.UPSTREAM_UNAVAILABLE,
        ErrorCode.RATE_LIMITED,
    }:
        cause_code, code = code, ErrorCode.STREAM_INTERRUPTED
    return make_error(
        code,
        source=source,
        operation=operation,
        retryable=is_transient(exc) and not partial,
        partial=partial,
        cause_code=cause_code,
        limit=limit,
        **overrides,
    )


def annotate_failure(exc, *, label, attempts, max_attempts, partial=False):
    from .runtime.retry import is_transient

    source = (
        "mcp"
        if label.startswith("mcp")
        else ("tool" if label.startswith("tool:") else "model")
    )
    error = normalize_error(
        exc,
        source=source,
        operation=label,
        attempts=attempts,
        max_attempts=max_attempts,
        retry_exhausted=attempts >= max_attempts and is_transient(exc),
        partial=partial,
    )
    try:
        exc.agent_error = error
    except (AttributeError, TypeError):
        pass
    return error
