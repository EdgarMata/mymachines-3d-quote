import os

from fastapi import HTTPException, UploadFile
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


def parse_upload_limit(value: str) -> int:
    """Fail at startup rather than silently disable upload protection."""
    try:
        limit = int(value)
    except ValueError as exc:
        raise ValueError("MAX_UPLOAD_SIZE_MB must be a positive integer.") from exc
    if limit <= 0:
        raise ValueError("MAX_UPLOAD_SIZE_MB must be a positive integer.")
    return limit


MAX_UPLOAD_SIZE_MB = parse_upload_limit(os.getenv("MAX_UPLOAD_SIZE_MB", "50"))

MAX_UPLOAD_SIZE_BYTES = (
    MAX_UPLOAD_SIZE_MB * 1024 * 1024
)


# Allow multipart headers and form fields without accepting an unbounded body.
MAX_REQUEST_SIZE_BYTES = MAX_UPLOAD_SIZE_BYTES + 1024 * 1024


class UploadBodyLimitMiddleware:
    """Bound HTTP bodies before multipart parsing, including chunked requests."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        for name, value in scope.get("headers", []):
            if name.lower() == b"content-length":
                try:
                    too_large = int(value) > MAX_REQUEST_SIZE_BYTES
                except ValueError:
                    too_large = False
                if too_large:
                    response = JSONResponse(
                        {"detail": "Request body exceeds the upload limit."},
                        status_code=413,
                    )
                    return await response(scope, receive, send)

        received = 0

        async def bounded_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > MAX_REQUEST_SIZE_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail="Request body exceeds the upload limit.",
                    )
            return message

        await self.app(scope, bounded_receive, send)


async def validate_stl_upload(
    file: UploadFile,
) -> tuple[str, bytes]:
    """Validate an STL upload and read its bounded content."""

    # Treat both POSIX and Windows paths as untrusted display metadata.
    filename = (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    filename = "".join(char for char in filename if char.isprintable())

    if not filename.lower().endswith(".stl"):
        raise HTTPException(
            status_code=400,
            detail="Only STL files are supported.",
        )

    if file.size is not None:
        if file.size > MAX_UPLOAD_SIZE_BYTES:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"File exceeds the maximum upload size "
                    f"of {MAX_UPLOAD_SIZE_MB} MiB."
                ),
            )

    contents = await file.read(
        MAX_UPLOAD_SIZE_BYTES + 1
    )

    if len(contents) > MAX_UPLOAD_SIZE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"File exceeds the maximum upload size "
                f"of {MAX_UPLOAD_SIZE_MB} MiB."
            ),
        )

    if not contents:
        raise HTTPException(
            status_code=400,
            detail="The uploaded file is empty.",
        )

    return filename, contents
