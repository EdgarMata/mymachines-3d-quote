"""Opt-in HTTP checks against a local production-mode frontend/Nginx instance.

Run separately from discovery: python /app/tests/nginx_smoke.py http://frontend
Requires the existing backend dependencies; all model data is synthetic/in-memory.
"""

import http.client
import json
import re
import sys
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlencode, urlsplit
from urllib.request import ProxyHandler, Request, build_opener
from urllib.error import HTTPError

import trimesh
from pydantic import TypeAdapter

from app.api_models import AnalyzeResponse, EstimateResponse, HealthResponse, OptionsResponse, RootResponse

BASE_URL = sys.argv.pop(1).rstrip("/") if len(sys.argv) > 1 else "http://frontend"
STL = trimesh.creation.box(extents=[10, 20, 30]).export(file_type="stl")


def request(path, *, contents=None, filename="synthetic.stl", params=None, rate_retries=3):
    url = BASE_URL + path + ("?" + urlencode(params) if params else "")
    headers, body = {}, None
    if contents is not None:
        body = (b'--mm005-boundary\r\nContent-Disposition: form-data; name="file"; filename="'
                + filename.encode() + b'"\r\nContent-Type: model/stl\r\n\r\n'
                + contents + b'\r\n--mm005-boundary--\r\n')
        headers["Content-Type"] = "multipart/form-data; boundary=mm005-boundary"
    for attempt in range(rate_retries + 1):
        try:
            response = build_opener(ProxyHandler({})).open(Request(url, data=body, headers=headers), timeout=90)
        except HTTPError as error:
            response = error
        with response:
            payload = response.read()
            value = json.loads(payload) if "application/json" in response.headers.get("Content-Type", "") else payload.decode()
            result = response.status, response.headers, value
        if result[0] != 429 or attempt == rate_retries:
            return result
        # Test-driver pacing for MM-006's 10/minute estimate pool. The product
        # UI still retries only manually; this bounded loop is not app behavior.
        time.sleep(6.2)


class TestNginxPublicAPI(unittest.TestCase):
    def assert_contract(self, value, model):
        # Round-trip equality catches unknown fields at every nested level.
        self.assertEqual(TypeAdapter(model).validate_python(value).model_dump(exclude_unset=True), value)

    def test_application_and_static_bundle(self):
        status, _, html = request("/")
        self.assertEqual(status, 200)
        self.assertIn('<div id="root">', html)
        bundle = re.search(r'src="([^"]+\.js)"', html)
        self.assertIsNotNone(bundle)
        status, _, javascript = request(bundle[1])
        self.assertEqual(status, 200)
        self.assertIn("Calculate quote", javascript)

    def test_public_static_assets_are_readable(self):
        for path, marker in (("/favicon.svg", "<svg"), ("/icons.svg", "<svg"),
                             ("/LICENSE", "GNU AFFERO GENERAL PUBLIC LICENSE"),
                             ("/THIRD_PARTY_NOTICES.md", "Third-Party")):
            with self.subTest(path=path):
                status, _, contents = request(path, rate_retries=0)
                self.assertEqual(status, 200, path)
                self.assertIn(marker, contents, path)
                # A missing asset served through the SPA fallback is not success.
                self.assertNotIn('<div id="root">', contents, path)

    def test_options_and_health(self):
        for path, model in (("/api/", RootResponse), ("/api/health", HealthResponse), ("/api/options", OptionsResponse)):
            status, _, result = request(path)
            self.assertEqual(status, 200)
            self.assert_contract(result, model)

    def test_analysis_and_manual_orientation(self):
        status, _, result = request("/api/analyze", contents=STL, params={"rotation_x": 90})
        self.assertEqual(status, 200)
        self.assert_contract(result, AnalyzeResponse)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["orientation"]["rotation_degrees"]["x"], 90)
        self.assertEqual(result["orientation"]["dimensions_mm"], {"x": 10, "y": 30, "z": 20})
        self.assertEqual(result["display_machine"]["id"], "prusa_mk3s_04")

    def test_default_real_orca_estimate(self):
        status, _, result = request("/api/estimate", contents=STL)
        self.assertEqual(status, 200, result)
        self.assert_contract(result, EstimateResponse)
        self.assertEqual(result["status"], "estimated")
        self.assertEqual(result["material"], "PLA")
        self.assertGreater(result["pricing"]["selling_price_eur"], 0)
        self.assertGreater(result["slicing"]["filament_g"], 0)

    def test_material_quality_quantity_and_rotation(self):
        status, _, result = request("/api/estimate", contents=STL, params={
            "material": "PETG", "quality": "fine", "quantity": 2, "rotation_y": 90})
        self.assertEqual(status, 200, result)
        self.assert_contract(result, EstimateResponse)
        self.assertEqual((result["material"], result["quality"], result["quantity"]), ("PETG", "fine", 2))
        self.assertEqual(result["pricing"]["quantity"], 2)
        self.assertEqual(result["orientation"]["dimensions_mm"], {"x": 30, "y": 20, "z": 10})

    def test_review_and_incompatibility(self):
        for size, expected in ((300, "technical_review_required"), (1100, "model_exceeds_build_volume")):
            contents = trimesh.creation.box(extents=[size] * 3).export(file_type="stl")
            for path, model in (("/api/analyze", AnalyzeResponse), ("/api/estimate", EstimateResponse)):
                status, _, result = request(path, contents=contents)
                self.assertEqual(status, 200, result)
                self.assert_contract(result, model)
                self.assertEqual(result["status"], expected)
                if path.endswith("estimate"):
                    self.assertIsNone(result["slicing"])
                    self.assertIsNone(result["pricing"])

    def test_controlled_errors_and_proxy_upload_limit(self):
        # Nginx and ASGI must not decode a hidden endpoint at different depths.
        for path in ("/api/%2565stimate", "/api/%2561nalyze", "/api/estimate%252f"):
            status, _, _ = request(path, contents=b"")
            self.assertEqual(status, 404, path)
        marker = "SYNTHETIC_PRIVATE_VALUE"
        for params, filename, expected in (({}, "part.obj", 400), ({"quantity": 0}, "part.stl", 400),
                                          ({"quantity": marker * 100}, "part.stl", 422),
                                          ({"rotation_x": "NaN"}, "part.stl", 400)):
            status, _, result = request("/api/estimate", contents=STL, filename=filename, params=params)
            self.assertEqual(status, expected)
            self.assertEqual(set(result), {"detail"})
            self.assertNotIn(marker, result["detail"])
            self.assertLess(len(result["detail"]), 150)
        target = urlsplit(BASE_URL)
        connection = http.client.HTTPConnection(target.hostname, target.port or 80, timeout=10)
        try:
            connection.putrequest("POST", "/api/estimate")
            connection.putheader("Content-Length", str(51 * 1024 * 1024 + 1))
            connection.endheaders()  # Nginx rejects the length without reading 51 MiB.
            self.assertEqual(connection.getresponse().status, 413)
        finally:
            connection.close()

    def test_busy_retry_after_and_light_endpoints(self):
        barrier = threading.Barrier(6)

        def quote():
            barrier.wait(timeout=5)
            return request("/api/estimate", contents=STL, rate_retries=0)

        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = [executor.submit(quote) for _ in range(6)]
            self.assertEqual(request("/api/health")[0], 200)
            self.assertEqual(request("/api/options")[0], 200)
            results = [future.result(timeout=90) for future in futures]
        self.assertIn(200, [status for status, _, _ in results])
        self.assertIn(503, [status for status, _, _ in results])
        for status, headers, result in results:
            self.assertIn(status, (200, 429, 503))
            if status == 503:
                self.assertEqual(headers["Retry-After"], "5")
                self.assertEqual(set(result), {"detail"})
            elif status == 429:
                self.assertEqual(set(result), {"detail"})
            else:
                self.assert_contract(result, EstimateResponse)

    def test_production_surface(self):
        for path in ("/api/docs", "/api/docs/oauth2-redirect", "/api/redoc", "/api/openapi.json"):
            self.assertEqual(request(path)[0], 404)
        for path in ("/api/upload", "/api/slice"):
            self.assertEqual(request(path, contents=STL)[0], 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)
