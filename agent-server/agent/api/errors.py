"""A single HTTP envelope, compatible with old clients reading detail."""

import logging
import uuid

from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from ..errors import ApplicationError, ErrorCode, make_error, normalize_error

logger = logging.getLogger(__name__)


def install_error_handlers(app):
    @app.middleware("http")
    async def request_identity(request, call_next):
        request.state.request_id = str(uuid.uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    def response(request, error, status, headers=None):
        return JSONResponse(
            status_code=status,
            content={"error": error.public(), "detail": error.message},
            headers={**(headers or {}), "X-Request-ID": error.request_id},
        )

    def request_id(request):
        return getattr(request.state, "request_id", str(uuid.uuid4()))

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        code = {
            400: ErrorCode.INVALID_ARGUMENT,
            401: ErrorCode.AUTHENTICATION_FAILED,
            403: ErrorCode.PERMISSION_DENIED,
            404: ErrorCode.NOT_FOUND,
            409: ErrorCode.RUN_CONFLICT,
            422: ErrorCode.INVALID_ARGUMENT,
            429: ErrorCode.RATE_LIMITED,
            503: ErrorCode.AGENT_NOT_READY,
        }.get(exc.status_code, ErrorCode.INTERNAL_ERROR)
        error = make_error(
            code,
            source="api",
            request_id=request_id(request),
            retryable=exc.status_code in {429, 503},
        )
        return response(request, error, exc.status_code, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return response(
            request,
            make_error(
                ErrorCode.INVALID_ARGUMENT, source="api", request_id=request_id(request)
            ),
            422,
        )

    @app.exception_handler(ApplicationError)
    async def application_error(request, exc):
        return response(request, exc.error, 500)

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        error = normalize_error(exc, source="api", request_id=request_id(request))
        logger.error(
            "HTTP request failed request_id=%s code=%s type=%s",
            error.request_id,
            error.code.value,
            type(exc).__name__,
        )
        return response(request, error, 500)
