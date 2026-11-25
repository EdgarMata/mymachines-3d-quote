"""Bounded public errors and diagnostics that never serialize exception payloads."""

import logging

from fastapi.exceptions import RequestValidationError, ResponseValidationError
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

from app.services.upload_validation import MAX_UPLOAD_SIZE_MB

logger = logging.getLogger(__name__)


def log_failure(scope, error):
    route = scope.get("route")
    # Only a registered route template and exception class, never request URLs,
    # validation input/context, command output, exception text or tracebacks.
    logger.error("API operation failed: route=%s error_type=%s",
                 getattr(route, "path", "unmatched"), type(error).__name__)


async def validation_error(request, exc: RequestValidationError):
    return JSONResponse({"detail": "Invalid request parameters or missing STL file."}, status_code=422)


async def response_error(request, exc: ResponseValidationError):
    log_failure(request.scope, exc)
    return JSONResponse({"detail": "The request could not be processed."}, status_code=500)


async def http_error(request, exc: HTTPException):
    # Only reviewed application messages can pass through. Framework/parser or
    # future diagnostic details are replaced, preserving the HTTP status.
    messages = {
        400: {"Only STL files are supported.", "The uploaded file is empty."},
        413: {"Request body exceeds the upload limit.",
              f"File exceeds the maximum upload size of {MAX_UPLOAD_SIZE_MB} MiB."},
        500: {"Slicing failed.", "Model analysis failed.", "Pricing failed."},
    }
    defaults = {400: "Invalid request.", 404: "Not found.", 405: "Method not allowed.",
                413: "Request body exceeds the upload limit.", 422: "Invalid request parameters.",
                503: "The server is busy. Please try again shortly.",
                504: "Processing took too long. Please try again."}
    detail = exc.detail if isinstance(exc.detail, str) and exc.detail in messages.get(exc.status_code, ()) else (
        defaults.get(exc.status_code, "The request could not be processed.")
    )
    if exc.status_code >= 500:
        log_failure(request.scope, exc.__cause__ or exc)
    # Do not forward arbitrary exception headers. Method availability is public.
    headers = {"Allow": exc.headers["Allow"]} if exc.status_code == 405 and exc.headers and "Allow" in exc.headers else None
    if exc.status_code == 503:
        # Match admission policy without reflecting arbitrary exception headers.
        headers = {"Retry-After": "5"}
    return JSONResponse({"detail": detail}, status_code=exc.status_code, headers=headers)


class PublicErrorMiddleware:
    """Catch unexpected failures before headers, without ServerError traceback logs.

    A pure ASGI wrapper preserves multipart/cancellation and worker ownership.
    Lifespan configuration errors and disconnected sends still propagate.
    """
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        started = False

        async def track_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, track_send)
        except Exception as exc:
            if started:
                raise
            log_failure(scope, exc)
            await JSONResponse({"detail": "The request could not be processed."}, status_code=500)(scope, receive, send)
