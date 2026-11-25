import asyncio
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import trimesh
from fastapi import HTTPException, UploadFile
from fastapi.testclient import TestClient

from app.main import app
from app.services import upload_validation as validation


class TestUploadValidation(unittest.IsolatedAsyncioTestCase):
    async def test_bounded_read_without_size_metadata(self):
        file = UploadFile(filename="part.STL", file=io.BytesIO(b"1234"))
        with patch.object(validation, "MAX_UPLOAD_SIZE_BYTES", 3):
            with self.assertRaises(HTTPException) as error:
                await validation.validate_stl_upload(file)
        self.assertEqual(error.exception.status_code, 413)
        self.assertEqual(file.file.tell(), 4)
        await file.close()

    async def test_declared_oversize_is_rejected_without_reading(self):
        file = UploadFile(filename="part.stl", file=io.BytesIO(), size=4)
        file.read = AsyncMock()
        with patch.object(validation, "MAX_UPLOAD_SIZE_BYTES", 3):
            with self.assertRaises(HTTPException) as error:
                await validation.validate_stl_upload(file)
        self.assertEqual(error.exception.status_code, 413)
        file.read.assert_not_awaited()
        await file.close()

    async def test_limit_is_inclusive(self):
        file = UploadFile(filename="part.STL", file=io.BytesIO(b"123"))
        with patch.object(validation, "MAX_UPLOAD_SIZE_BYTES", 3):
            self.assertEqual(
                await validation.validate_stl_upload(file), ("part.STL", b"123")
            )
        await file.close()

    async def test_paths_and_control_characters_are_removed(self):
        for name in ("../../part.stl", "/tmp/part.stl", "C:\\temp\\part.stl", "part\x00.stl"):
            with self.subTest(name=name):
                file = UploadFile(filename=name, file=io.BytesIO(b"123"))
                filename, _ = await validation.validate_stl_upload(file)
                self.assertEqual(filename, "part.stl")
                await file.close()

    def test_invalid_configuration_fails(self):
        for value in ("0", "-1", "abc", "", "1.5"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validation.parse_upload_limit(value)
        self.assertEqual(validation.parse_upload_limit("50"), 50)


class TestUploadEndpoints(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.stl = trimesh.creation.box(extents=[10, 20, 30]).export(file_type="stl")
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        patcher = patch("app.main.PROJECT_ROOT", Path(self.directory.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_all_endpoints_reject_invalid_uploads_before_processing(self):
        cases = [("part.obj", b"123", 400), ("part.stl", b"", 400),
                 ("part.stl", b"1234", 413), ("", b"123", 422)]
        with patch.object(validation, "MAX_UPLOAD_SIZE_BYTES", 3), patch("app.main.slice_stl") as slicer:
            for endpoint in ("/upload", "/slice", "/analyze", "/estimate"):
                for name, content, status in cases:
                    with self.subTest(endpoint=endpoint, name=name, content=content):
                        response = self.client.post(endpoint, files={"file": (name, content, "model/stl")})
                        self.assertEqual(response.status_code, status, response.text)
            slicer.assert_not_called()
        self.assertEqual(list(Path(self.directory.name).iterdir()), [])

    def test_valid_geometry_at_exact_limit_on_all_endpoints(self):
        slicing = {"material_cost_eur": 0.38, "print_time_seconds": 7830, "filament_g": 23.75}
        with patch.object(validation, "MAX_UPLOAD_SIZE_BYTES", len(self.stl)), patch("app.main.slice_stl", return_value=slicing):
            for endpoint in ("/upload", "/slice", "/analyze", "/estimate"):
                with self.subTest(endpoint=endpoint):
                    response = self.client.post(endpoint, files={"file": ("part.STL", self.stl, "model/stl")})
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.json()["filename"], "part.STL")

    def test_slice_uses_owned_path_and_cleans_up(self):
        paths = []

        def slice_file(path):
            paths.append(path)
            self.assertEqual(path.name, "original.stl")
            self.assertTrue(path.is_relative_to(Path(self.directory.name)))
            self.assertEqual(path.read_bytes(), self.stl)
            return {}

        with patch("app.main.slice_stl", side_effect=slice_file):
            for name in ("../../outside.stl", "/tmp/outside.stl", "C:\\temp\\outside.stl"):
                response = self.client.post("/slice", files={"file": (name, self.stl, "model/stl")})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["filename"], "outside.stl")
        self.assertTrue(paths)
        self.assertTrue(all(not path.exists() for path in paths))

    def test_declared_request_limit_rejects_before_parser(self):
        with patch.object(validation, "MAX_REQUEST_SIZE_BYTES", 100):
            response = self.client.post("/upload", content=b"x", headers={"content-length": "101"})
        self.assertEqual(response.status_code, 413)

    def test_chunked_multipart_limit_rejects_before_endpoint(self):
        # Feed separate ASGI messages so the parser has created a temporary file
        # before a later chunk exceeds the limit. A fake small Content-Length
        # must not bypass the streaming guard either.
        async def run(endpoint, declared_length):
            first = (b'--boundary\r\nContent-Disposition: form-data; name="file"; filename="part.stl"\r\n'
                     b'Content-Type: model/stl\r\n\r\n' + b'x' * 32)
            chunks = iter([first, b'x' * 1024, b'\r\n--boundary--\r\n'])
            receive = AsyncMock(side_effect=lambda: {
                "type": "http.request", "body": next(chunks), "more_body": True,
            })
            sent = []
            files = []
            spool = tempfile.SpooledTemporaryFile

            def track_file(*args, **kwargs):
                file = spool(*args, **kwargs)
                files.append(file)
                return file

            async def send(message):
                sent.append(message)

            headers = [(b"content-type", b"multipart/form-data; boundary=boundary")]
            if declared_length:
                headers.append((b"content-length", b"1"))
            scope = {
                "type": "http", "http_version": "1.1", "method": "POST",
                "scheme": "http", "path": endpoint, "raw_path": endpoint.encode(),
                "query_string": b"", "headers": headers,
                "client": ("127.0.0.1", 1234), "server": ("testserver", 80),
            }
            with patch.object(validation, "MAX_REQUEST_SIZE_BYTES", 200), patch(
                "starlette.formparsers.SpooledTemporaryFile", side_effect=track_file
            ), patch("app.main.slice_stl") as slicer:
                await app(scope, receive, send)
                slicer.assert_not_called()
            self.assertEqual(sent[0]["status"], 413)
            body = b"".join(m.get("body", b"") for m in sent)
            self.assertEqual(json.loads(body), {"detail": "Request body exceeds the upload limit."})
            self.assertEqual(receive.await_count, 2)
            self.assertTrue(files)
            self.assertTrue(all(file.closed for file in files))

        for endpoint in ("/upload", "/slice", "/analyze", "/estimate"):
            for declared_length in (False, True):
                with self.subTest(endpoint=endpoint, declared_length=declared_length):
                    asyncio.run(run(endpoint, declared_length))

    def test_stream_guard_counts_separate_messages_and_stops_receiving(self):
        async def run():
            messages = iter([
                {"type": "http.request", "body": b"123", "more_body": True},
                {"type": "http.request", "body": b"456", "more_body": True},
            ])
            receive = AsyncMock(side_effect=lambda: next(messages))

            async def consumer(scope, receive, send):
                await receive()
                await receive()

            middleware = validation.UploadBodyLimitMiddleware(consumer)
            with patch.object(validation, "MAX_REQUEST_SIZE_BYTES", 5):
                with self.assertRaises(HTTPException) as error:
                    await middleware({"type": "http", "headers": []}, receive, AsyncMock())
            self.assertEqual(error.exception.status_code, 413)
            self.assertEqual(receive.await_count, 2)

        asyncio.run(run())
