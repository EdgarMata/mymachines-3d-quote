"""Fail-fast admission before multipart parsing; leases outlive cancelled awaits."""

import asyncio
from concurrent.futures import Future, ThreadPoolExecutor
from functools import partial
from threading import BoundedSemaphore, Lock

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.services.input_validation import positive_integer_setting

MAX_CONCURRENT_PROCESSING_JOBS = positive_integer_setting("MAX_CONCURRENT_PROCESSING_JOBS", 1)
PROCESSING_PATHS = frozenset({"/upload", "/analyze", "/slice", "/estimate"})


class ProcessingCapacity:
    def __init__(self, maximum: int):
        self._slots = BoundedSemaphore(maximum)
        self.executor = ThreadPoolExecutor(max_workers=maximum, thread_name_prefix="quote-processing")

    def admit(self):
        if self._slots.acquire(blocking=False):
            return ProcessingLease(self)
        return None


class ProcessingLease:
    def __init__(self, capacity: ProcessingCapacity):
        self._capacity = capacity
        self._lock = Lock()
        self._future: Future | None = None
        self._request_finished = False
        self._released = False

    def submit(self, function, *args, **kwargs) -> Future:
        with self._lock:
            if self._future is not None or self._request_finished:
                raise RuntimeError("A processing lease can run only one pipeline.")
            self._future = self._capacity.executor.submit(partial(function, *args, **kwargs))
            future = self._future
        # Register outside the lock: a completed future invokes callbacks inline.
        future.add_done_callback(lambda _: self._release_if_finished())
        return future

    def finish_request(self):
        with self._lock:
            self._request_finished = True
        self._release_if_finished()

    def _release_if_finished(self):
        with self._lock:
            if (self._request_finished and not self._released
                    and (self._future is None or self._future.done())):
                self._released = True
                self._capacity._slots.release()


processing_capacity = ProcessingCapacity(MAX_CONCURRENT_PROCESSING_JOBS)


class ProcessingAdmissionMiddleware:
    def __init__(self, app: ASGIApp, capacity: ProcessingCapacity | None = None):
        self.app = app
        self.capacity = capacity if capacity is not None else processing_capacity

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if (scope["type"] != "http" or scope["method"] != "POST"
                or scope["path"].rstrip("/") not in PROCESSING_PATHS):
            return await self.app(scope, receive, send)
        lease = self.capacity.admit()
        if lease is None:
            response = JSONResponse(
                {"detail": "The server is busy processing another model. Please try again shortly."},
                status_code=503, headers={"Retry-After": "5"},
            )
            return await response(scope, receive, send)
        scope["processing_lease"] = lease
        try:
            await self.app(scope, receive, send)
        finally:
            lease.finish_request()


async def run_processing(request, function, *args, **kwargs):
    lease = request.scope["processing_lease"]
    future = asyncio.wrap_future(lease.submit(function, *args, **kwargs))
    # The concurrent Future (not the asyncio await) determines when work ends.
    # Observe exceptions even when the request has gone away.
    future.add_done_callback(lambda completed: None if completed.cancelled() else completed.exception())
    return await asyncio.shield(future)
