"""Exercise admission on the real ASGI app and cancellation of running threads."""

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import trimesh

from app.main import app
from app.services import processing
from app.services.input_validation import InputValidationError, positive_integer_setting

VALID_STATS = {"material_cost_eur": 0.38, "print_time_seconds": 7830, "filament_g": 23.75}
STL = trimesh.creation.box(extents=[10, 20, 30]).export(file_type="stl")
BODY = (b'--boundary\r\nContent-Disposition: form-data; name="file"; filename="part.stl"\r\n'
        b'Content-Type: model/stl\r\n\r\n' + STL + b'\r\n--boundary--\r\n')


def scope_for(path, method="POST"):
    return {
        "type": "http", "http_version": "1.1", "method": method, "scheme": "http",
        "path": path, "raw_path": path.encode(), "query_string": b"",
        "headers": [(b"content-type", b"multipart/form-data; boundary=boundary")],
        "client": ("127.0.0.1", 1234), "server": ("testserver", 80),
    }


async def request(path, method="POST", receive=None):
    messages = []

    async def default_receive():
        return {"type": "http.request", "body": BODY if method == "POST" else b"", "more_body": False}

    async def send(message):
        messages.append(message)

    await app(scope_for(path, method), receive or default_receive, send)
    return messages[0]["status"], dict(messages[0]["headers"]), json.loads(
        b"".join(message.get("body", b"") for message in messages)
    )


async def eventually_available():
    for _ in range(200):
        lease = processing.processing_capacity.admit()
        if lease is not None:
            lease.finish_request()
            return
        await asyncio.sleep(0.01)
    raise AssertionError("Processing admission was not released after worker completion.")


class TestProcessing(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        patcher = patch("app.main.PROJECT_ROOT", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_busy_before_body_on_all_endpoints_and_trailing_slash(self):
        lease = processing.processing_capacity.admit()
        self.assertIsNotNone(lease)
        try:
            for path in processing.PROCESSING_PATHS | {"/estimate/"}:
                receive = AsyncMock(side_effect=AssertionError("Busy request body was consumed."))
                status, headers, result = await request(path, receive=receive)
                self.assertEqual(status, 503)
                self.assertEqual(headers[b"retry-after"], b"5")
                self.assertIn("busy", result["detail"])
                receive.assert_not_awaited()
        finally:
            lease.finish_request()
        self.assertEqual((await request("/upload"))[0], 200)

    async def test_heavy_endpoints_offload_once_and_keep_health_options_responsive(self):
        import app.main as main
        original_loader = main.load_validated_stl
        loop_thread = threading.get_ident()
        for path in processing.PROCESSING_PATHS:
            started, finish = threading.Event(), threading.Event()
            worker_threads = []

            def blocked_loader(contents):
                worker_threads.append(threading.get_ident())
                started.set()
                if not finish.wait(3):
                    raise AssertionError("Test did not release worker.")
                return original_loader(contents)

            with self.subTest(path=path), patch("app.main.load_validated_stl", side_effect=blocked_loader), patch(
                "app.main.slice_stl", return_value=VALID_STATS
            ):
                task = asyncio.create_task(request(path))
                try:
                    self.assertTrue(await asyncio.to_thread(started.wait, 1))
                    for light_path in ("/health", "/options"):
                        self.assertEqual((await asyncio.wait_for(request(light_path, "GET"), 0.5))[0], 200)
                    self.assertEqual((await request("/upload"))[0], 503)
                finally:
                    finish.set()
                    response = await task
                self.assertEqual(response[0], 200, response[2])
                self.assertEqual(len(worker_threads), 1)
                self.assertNotEqual(worker_threads[0], loop_thread)
        self.assertEqual((await request("/upload"))[0], 200)

    async def test_admission_released_after_validation_and_processing_exceptions(self):
        for path in processing.PROCESSING_PATHS:
            with self.subTest(path=path), patch(
                "app.main.load_validated_stl", side_effect=InputValidationError("Invalid model.")
            ):
                self.assertEqual((await request(path))[0], 400)
            self.assertEqual((await request("/upload"))[0], 200)
        with patch("app.main.orient_mesh", side_effect=RuntimeError("private exception")):
            self.assertEqual((await request("/analyze"))[0], 500)
        self.assertEqual((await request("/upload"))[0], 200)

    async def test_cancellation_during_body_releases_admission(self):
        started = asyncio.Event()

        async def receive():
            started.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(request("/upload", receive=receive))
        await asyncio.wait_for(started.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual((await request("/upload"))[0], 200)

    async def test_cancelled_request_keeps_slot_and_files_until_actual_worker_end(self):
        for fail in (False, True):
            started, finish = threading.Event(), threading.Event()
            paths = []

            def blocked_slice(path, **kwargs):
                paths.append(path)
                started.set()
                if not finish.wait(3):
                    raise AssertionError("Test did not release worker.")
                if fail:
                    raise RuntimeError("Late failure after client cancellation.")
                return VALID_STATS

            with self.subTest(fail=fail), patch("app.main.slice_stl", side_effect=blocked_slice):
                task = asyncio.create_task(request("/estimate"))
                try:
                    self.assertTrue(await asyncio.to_thread(started.wait, 1))
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                    self.assertTrue(paths[0].exists())
                    self.assertEqual((await request("/upload"))[0], 503)
                    self.assertEqual((await request("/health", "GET"))[0], 200)
                finally:
                    finish.set()
                    await eventually_available()
                self.assertFalse(paths[0].exists())
            self.assertEqual((await request("/upload"))[0], 200)

    async def test_configurable_capacity_has_no_waiting_queue(self):
        capacity = processing.ProcessingCapacity(2)
        try:
            first, second = capacity.admit(), capacity.admit()
            self.assertIsNotNone(first)
            self.assertIsNotNone(second)
            self.assertIsNone(capacity.admit())
            first.finish_request()
            replacement = capacity.admit()
            self.assertIsNotNone(replacement)
            self.assertIsNone(capacity.admit())
            replacement.finish_request()
            second.finish_request()
        finally:
            capacity.executor.shutdown(wait=True)

    async def test_lease_rejects_double_submission(self):
        lease = processing.processing_capacity.admit()
        try:
            self.assertEqual(await asyncio.wrap_future(lease.submit(lambda: 42)), 42)
            with self.assertRaises(RuntimeError):
                lease.submit(lambda: 43)
        finally:
            lease.finish_request()

    def test_invalid_processing_settings(self):
        for name in ("MAX_CONCURRENT_PROCESSING_JOBS", "SLICER_TIMEOUT_SECONDS"):
            for value in ("0", "-1", "NaN", "Infinity", "1.5", ""):
                with self.subTest(name=name, value=value), patch.dict("os.environ", {name: value}):
                    with self.assertRaises(ValueError):
                        positive_integer_setting(name, 1)


    def test_invalid_configuration_prevents_app_import(self):
        for name in ("MAX_CONCURRENT_PROCESSING_JOBS", "SLICER_TIMEOUT_SECONDS"):
            environment = {**os.environ, name: "0"}
            result = subprocess.run(
                [sys.executable, "-c", "import app.main"], env=environment,
                capture_output=True, text=True, timeout=10,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(name + " must be a positive integer", result.stderr)


    def test_admission_and_release_are_atomic_under_parallel_calls(self):
        from concurrent.futures import ThreadPoolExecutor
        capacity = processing.ProcessingCapacity(2)
        barrier = threading.Barrier(8)

        def admit_together():
            barrier.wait(timeout=3)
            return capacity.admit()

        try:
            with ThreadPoolExecutor(max_workers=8) as callers:
                attempts = [callers.submit(admit_together) for _ in range(8)]
                leases = [future.result(timeout=3) for future in attempts]
            accepted = [lease for lease in leases if lease is not None]
            self.assertEqual(len(accepted), 2)
            for lease in accepted:
                lease.finish_request()
                lease.finish_request()
            first, second = capacity.admit(), capacity.admit()
            self.assertIsNotNone(first)
            self.assertIsNotNone(second)
            self.assertIsNone(capacity.admit())
            first.finish_request()
            second.finish_request()
        finally:
            capacity.executor.shutdown(wait=True)

    def test_simultaneous_worker_completion_and_request_finish_release_only_once(self):
        from concurrent.futures import ThreadPoolExecutor
        capacity = processing.ProcessingCapacity(1)
        try:
            with ThreadPoolExecutor(max_workers=1) as callers:
                for _ in range(50):
                    lease = capacity.admit()
                    self.assertIsNotNone(lease)
                    barrier = threading.Barrier(2)

                    def finish_request():
                        barrier.wait(timeout=3)
                        lease.finish_request()

                    worker = lease.submit(lambda: barrier.wait(timeout=3))
                    request_done = callers.submit(finish_request)
                    worker.result(timeout=3)
                    request_done.result(timeout=3)
                    lease.finish_request()
                    replacement = capacity.admit()
                    self.assertIsNotNone(replacement)
                    self.assertIsNone(capacity.admit())
                    replacement.finish_request()
        finally:
            capacity.executor.shutdown(wait=True)

    async def test_multipart_parse_failure_closes_files_and_releases_capacity(self):
        files = []
        spool = tempfile.SpooledTemporaryFile

        def track_file(*args, **kwargs):
            file = spool(*args, **kwargs)
            files.append(file)
            return file

        # A valid first file followed by a part without a form field name.
        malformed = BODY.replace(b"--boundary--\r\n", (
            b'--boundary\r\nContent-Disposition: form-data\r\n\r\nx\r\n--boundary--\r\n'
        ))
        receive = AsyncMock(return_value={"type": "http.request", "body": malformed, "more_body": False})
        with patch("starlette.formparsers.SpooledTemporaryFile", side_effect=track_file):
            self.assertEqual((await request("/upload", receive=receive))[0], 400)
        self.assertTrue(files)
        self.assertTrue(all(file.closed for file in files))
        self.assertEqual((await request("/upload"))[0], 200)

    async def test_cleanup_exception_does_not_leak_admission(self):
        from contextlib import contextmanager
        original = tempfile.TemporaryDirectory

        @contextmanager
        def failing_cleanup(*args, **kwargs):
            with original(*args, **kwargs) as directory:
                yield directory
            raise OSError("Synthetic temporary cleanup failure.")

        with patch("app.main.tempfile.TemporaryDirectory", side_effect=failing_cleanup), patch(
            "app.main.slice_stl", return_value=VALID_STATS
        ):
            self.assertEqual((await request("/estimate"))[0], 500)
        self.assertFalse(any(self.root.rglob("*.stl")))
        self.assertEqual((await request("/upload"))[0], 200)


    async def test_client_disconnect_does_not_release_a_running_worker(self):
        started, finish = threading.Event(), threading.Event()
        disconnected = False

        def blocked_slice(path, **kwargs):
            started.set()
            if not finish.wait(3):
                raise AssertionError("Test did not release worker.")
            return VALID_STATS

        async def receive():
            if disconnected:
                return {"type": "http.disconnect"}
            return {"type": "http.request", "body": BODY, "more_body": False}

        async def send(message):
            if disconnected:
                raise ConnectionResetError("Synthetic client disconnect.")

        with patch("app.main.slice_stl", side_effect=blocked_slice):
            task = asyncio.create_task(app(scope_for("/estimate"), receive, send))
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 1))
                disconnected = True
                self.assertEqual((await request("/upload"))[0], 503)
                self.assertEqual((await request("/options", "GET"))[0], 200)
            finally:
                finish.set()
                with self.assertRaises(ConnectionResetError):
                    await task
                await eventually_available()
        self.assertEqual((await request("/upload"))[0], 200)
