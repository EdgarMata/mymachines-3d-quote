"""Public HTTP boundaries with synthetic secrets, geometry and native slicing regressions."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import app.main as main
from app.api_config import load_api_settings
from app.api_models import ErrorResponse, HealthResponse, RootResponse
from app.services.printing_config import load_printing_config
from test_processing import STL

SECRET = "SYNTHETIC_PRIVATE_SENTINEL /srv/private/model.stl G1 X99 token=example"
FORBIDDEN = {"material_cost_eur", "cost_per_kg", "costs", "rates", "internal", "summary",
             "margin", "production", "printer_profile", "process_profile", "filament_profile",
             "output_file", "command", "gcode", "stdout", "stderr", "private_future_field",
             "operational", "profile_ready", "automatic_quoting_ready", "quoting_candidates_count"}
STATS = {"status": "success", "slicer": "OrcaSlicer", "machine_id": "prusa_mk3s_04",
         "material": "PLA", "quality": "standard", "filament_g": 23.75, "layers": 150,
         "print_time": "2h 10m 30s", "print_time_seconds": 7830, "material_cost_eur": 0.38,
         "printer_profile": SECRET, "process_profile": SECRET, "filament_profile": SECRET,
         "command": SECRET, "gcode": SECRET, "stdout": SECRET, "stderr": SECRET}


def poison(value):
    if isinstance(value, dict):
        return {**{key: poison(item) for key, item in value.items()}, "private_future_field": SECRET}
    if isinstance(value, list):
        return [poison(item) for item in value]
    return value


class TestPublicContracts(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        patcher = patch.object(main, "PROJECT_ROOT", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(main.app)

    def post(self, endpoint, **kwargs):
        return self.client.post(endpoint, files={"file": ("customer.stl", STL, "model/stl")}, **kwargs)

    def assert_public(self, value):
        if isinstance(value, dict):
            self.assertFalse(FORBIDDEN.intersection(value), value.keys())
            for item in value.values():
                self.assert_public(item)
        elif isinstance(value, list):
            for item in value:
                self.assert_public(item)
        elif isinstance(value, str):
            self.assertNotIn(SECRET, value)
            self.assertNotIn(str(self.root), value)

    def test_root_and_health_contracts_are_allowlists(self):
        for endpoint, model, expected in (("/", RootResponse, {"name", "status", "version"}),
                                          ("/health", HealthResponse, {"status"})):
            response = self.client.get(endpoint)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(set(response.json()), expected)
            self.assertEqual(set(model.model_validate(poison(response.json())).model_dump()), expected)

    def test_options_filters_private_configuration_recursively(self):
        config = deepcopy(load_printing_config())
        config["private_future_field"] = SECRET
        for group in ("machines", "materials", "qualities"):
            for item in config[group].values():
                item["private_future_field"] = SECRET
                item["cost_per_kg"] = SECRET
                if "build_volume_mm" in item:
                    item["build_volume_mm"]["private_future_field"] = SECRET
        with patch.object(main, "load_printing_config", return_value=config):
            response = self.client.get("/options")
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(set(result), {"defaults", "materials", "qualities", "machines"})
        self.assert_public(result)
        self.assertEqual(result["defaults"]["material"], "PLA")
        self.assertEqual(set(result["machines"]["prusa_mk3s_04"]),
                         {"label", "build_volume_mm", "nozzle_diameter_mm", "quoting_enabled"})

    def test_all_heavy_endpoints_drop_future_fields_at_every_nested_level(self):
        for endpoint, function in (("/upload", "_upload_model"), ("/analyze", "_analyze_model"),
                                   ("/slice", "_slice_model"), ("/estimate", "_estimate_model")):
            original = getattr(main, function)

            def with_internal_fields(*args, **kwargs):
                return poison(original(*args, **kwargs))

            with self.subTest(endpoint=endpoint), patch.object(main, function, side_effect=with_internal_fields), patch.object(
                main, "slice_stl", return_value=STATS
            ):
                response = self.post(endpoint)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["filename"], "customer.stl")
            self.assert_public(response.json())

    def test_slicing_cost_is_used_for_pricing_but_never_returned(self):
        with patch.object(main, "slice_stl", return_value=STATS):
            result = self.post("/estimate").json()
        self.assertEqual(result["status"], "estimated")
        self.assertEqual(result["pricing"]["selling_price_eur"], 10.61)
        self.assertEqual(result["machine_selection"]["selected_machine"]["id"], "prusa_mk3s_04")
        self.assertEqual(set(result["pricing"]), {"machine", "quantity", "selling_price_eur", "minimum_order_applied"})
        self.assertEqual(result["slicing"]["filament_g"], 23.75)
        self.assertEqual(result["slicing"]["print_time"], "2h 10m 30s")
        self.assertEqual(result["slicing"]["layers"], 150)
        self.assert_public(result)

    def test_analyze_preserves_frontend_display_and_orientation(self):
        response = self.post("/analyze", params={"rotation_x": 90})
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result["status"], "ready")
        self.assertEqual(set(result["display_machine"]), {"id", "label", "build_volume_mm", "quoting_enabled"})
        self.assertEqual(result["orientation"]["rotation_degrees"]["x"], 90)
        self.assertEqual(result["orientation"]["bed_position"]["minimum_z_mm"], 0)
        self.assert_public(result)

    def test_review_and_incompatibility_preserve_null_quote_and_public_machines(self):
        from trimesh.creation import box
        for size, status in ((300, "technical_review_required"), (1100, "model_exceeds_build_volume")):
            stl = box(extents=[size] * 3).export(file_type="stl")
            for endpoint in ("/analyze", "/estimate"):
                with self.subTest(size=size, endpoint=endpoint), patch.object(main, "slice_stl") as slicer:
                    response = self.client.post(endpoint, files={"file": ("part.stl", stl)})
                self.assertEqual(response.status_code, 200, response.text)
                result = response.json()
                self.assertEqual(result["status"], status)
                self.assert_public(result)
                if endpoint == "/estimate":
                    self.assertIsNone(result["slicing"])
                    self.assertIsNone(result["pricing"])
                    self.assertIsInstance(result["message"], str)
                slicer.assert_not_called()

    def test_validation_errors_never_echo_values_uploads_or_large_inputs(self):
        for endpoint, params in (("/estimate", {"quantity": SECRET * 100}),
                                 ("/estimate", {"rotation_z": SECRET}), ("/analyze", {"rotation_x": SECRET})):
            response = self.post(endpoint, params=params)
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.json(), {"detail": "Invalid request parameters or missing STL file."})
            self.assertLess(len(response.content), 150)
        response = self.client.post("/estimate", data={"file": SECRET})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn(SECRET, response.text)
        self.assertEqual(set(response.json()), {"detail"})

    def test_missing_file_and_unknown_routes_have_controlled_errors(self):
        for method, endpoint, status in (("post", "/estimate", 422), ("get", "/not-a-route", 404),
                                         ("get", "/estimate", 405)):
            response = getattr(self.client, method)(endpoint)
            self.assertEqual(response.status_code, status)
            ErrorResponse.model_validate(response.json())

    def test_framework_http_errors_cannot_echo_arbitrary_details_or_headers(self):
        with patch.object(main, "validate_stl_upload", side_effect=HTTPException(
            400, detail={"internal": SECRET}, headers={"X-Private": SECRET}
        )):
            response = self.post("/upload")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"detail": "Invalid request."})
        self.assertNotIn("x-private", response.headers)

    def test_all_registered_business_routes_declare_explicit_response_models(self):
        # FastAPI keeps included routers deferred; inspect their declared routes
        # too, and confirm registration using the generated schema.
        routes = {route.path: route for route in (*main.app.routes, *main.legacy.routes) if isinstance(route, APIRoute)}
        self.assertEqual(set(routes), {"/", "/health", "/options", "/upload", "/slice", "/analyze", "/estimate"})
        self.assertEqual(set(main.app.openapi()["paths"]), set(routes))
        for route in routes.values():
            self.assertIsNotNone(route.response_model, route.path)
        # Direct JSONResponse paths are reviewed errors, not raw service results.
        response = self.client.post("/estimate")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(set(response.json()), {"detail"})

    def test_all_http_error_statuses_are_safe_and_503_keeps_retry_after(self):
        for status in (400, 413, 422, 503, 504, 500):
            with self.subTest(status=status), patch.object(main, "validate_stl_upload", side_effect=HTTPException(
                status, detail={"internal": SECRET}, headers={"Retry-After": SECRET, "X-Private": SECRET}
            )):
                response = self.post("/upload")
            self.assertEqual(response.status_code, status)
            self.assertEqual(set(response.json()), {"detail"})
            self.assertIsInstance(response.json()["detail"], str)
            self.assertNotIn(SECRET, response.text + str(response.headers))
            if status == 503:
                self.assertEqual(response.headers["retry-after"], "5")

    def test_nested_review_lists_nulls_and_omitted_statistics_remain_public(self):
        from trimesh.creation import box
        original = main._estimate_model

        def future_fields(*args, **kwargs):
            return poison(original(*args, **kwargs))

        with patch.object(main, "_estimate_model", side_effect=future_fields), patch.object(main, "slice_stl", return_value=STATS) as slicer:
            for size in (300, 1100):
                response = self.client.post("/estimate", files={"file": ("part.stl", box(extents=[size] * 3).export(file_type="stl"))})
                self.assertEqual(response.status_code, 200, response.text)
                result = response.json()
                self.assertIsNone(result["slicing"])
                self.assertIsNone(result["pricing"])
                self.assertIsNone(result["machine_selection"]["selected_machine"])
                self.assert_public(result)
            slicer.assert_not_called()
            slicer.return_value = {"filament_g": 1.0, "print_time_seconds": 60, "material_cost_eur": 0.02}
            response = self.post("/estimate")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertNotIn("layers", response.json()["slicing"])
            self.assertNotIn("print_time", response.json()["slicing"])
            self.assert_public(response.json())

    def test_unexpected_errors_are_json_and_diagnostics_omit_exception_payload(self):
        for endpoint, function in (("/options", "load_printing_config"), ("/upload", "geometry_summary")):
            with self.subTest(endpoint=endpoint), patch.object(main, function, side_effect=RuntimeError(SECRET)), self.assertLogs(
                "app.api_errors", level="ERROR"
            ) as logs:
                response = self.client.get(endpoint) if endpoint == "/options" else self.post(endpoint)
            self.assertEqual(response.status_code, 500)
            self.assertEqual(response.json(), {"detail": "The request could not be processed."})
            self.assertNotIn(SECRET, " ".join(logs.output))
            self.assertIn("RuntimeError", " ".join(logs.output))
            self.assertIn(endpoint, " ".join(logs.output))

    def test_invalid_internal_response_fails_closed_without_serializing_it(self):
        with patch.object(main, "slice_stl", return_value=STATS):
            base = main._estimate_model("customer.stl", STL, 1, "PLA", "standard", 0, 0, 0)
        nonfinite_price, nonfinite_geometry = deepcopy(base), deepcopy(base)
        nonfinite_price["pricing"]["selling_price_eur"] = float("nan")
        nonfinite_geometry["geometry"]["dimensions_mm"]["x"] = float("inf")
        for invalid in ({"private": SECRET}, {"filename": SECRET, "status": "estimated", "pricing": float("nan")},
                        nonfinite_price, nonfinite_geometry):
            with patch.object(main, "_estimate_model", return_value=invalid), self.assertLogs("app.api_errors", level="ERROR") as logs:
                response = self.post("/estimate")
            self.assertEqual(response.status_code, 500)
            self.assertNotIn(SECRET, response.text + " ".join(logs.output))
            self.assertEqual(set(response.json()), {"detail"})

    def test_configured_domain_errors_do_not_echo_unknown_material_or_quality(self):
        for name in ("material", "quality"):
            response = self.post("/estimate", params={name: SECRET})
            self.assertEqual(response.status_code, 400)
            self.assertNotIn(SECRET, response.text)

    def test_cors_local_development_and_same_origin(self):
        response = self.client.get("/options")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("access-control-allow-origin", response.headers)
        response = self.client.options("/estimate", headers={"Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["access-control-allow-origin"], "http://localhost:5173")
        self.assertNotIn("access-control-allow-credentials", response.headers)
        response = self.client.options("/estimate", headers={"Origin": "https://untrusted.example",
            "Access-Control-Request-Method": "POST"})
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("access-control-allow-origin", response.headers)

    def test_development_openapi_describes_public_models_and_safe_validation(self):
        for endpoint in ("/docs", "/redoc", "/docs/oauth2-redirect"):
            self.assertEqual(self.client.get(endpoint).status_code, 200)
        schema = self.client.get("/openapi.json").json()
        self.assertEqual(set(schema["paths"]), {"/", "/health", "/options", "/upload", "/slice", "/analyze", "/estimate"})
        self.assertEqual(schema["paths"]["/estimate"]["post"]["responses"]["422"]["content"]["application/json"]["schema"],
                         {"$ref": "#/components/schemas/ErrorResponse"})
        for component in schema["components"]["schemas"].values():
            self.assertFalse(FORBIDDEN.intersection(component.get("properties", {})))


class TestPublicSurfaceSettings(unittest.TestCase):
    def settings_environment(self, **overrides):
        result = dict(os.environ)
        for name in ("APP_ENV", "ENABLE_LEGACY_ENDPOINTS", "ENABLE_API_DOCS", "CORS_ALLOWED_ORIGINS"):
            result.pop(name, None)
        return {**result, **overrides}

    def probe(self, **overrides):
        code = """
import json
from fastapi.testclient import TestClient
from app.main import app
from app.services.processing import processing_capacity
client = TestClient(app)
paths = ('/docs', '/redoc', '/openapi.json', '/docs/oauth2-redirect')
result = {path: client.get(path).status_code for path in paths}
for path in ('/upload', '/slice'):
    response = client.post(path)
    result[path] = response.status_code
result['options'] = client.get('/options').status_code
response = client.get('/options', headers={'Origin': 'http://localhost:5173'})
result['cors'] = response.headers.get('access-control-allow-origin')
result['credentials'] = response.headers.get('access-control-allow-credentials')
result['paths'] = list(app.openapi()['paths'])
lease = processing_capacity.admit()
try:
    result['busy_legacy'] = {path: client.post(path).status_code for path in ('/upload', '/slice')}
    result['busy_estimate'] = client.post('/estimate').status_code
finally:
    lease.finish_request()
print(json.dumps(result))
"""
        process = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                                 env=self.settings_environment(**overrides), timeout=10, check=True)
        return json.loads(process.stdout)

    def test_production_defaults_disable_legacy_docs_and_cross_origin_access(self):
        result = self.probe(APP_ENV="production")
        for path in ("/upload", "/slice", "/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"):
            self.assertEqual(result[path], 404)
        self.assertEqual(result["options"], 200)
        self.assertIsNone(result["cors"])
        self.assertIsNone(result["credentials"])
        self.assertNotIn("/upload", result["paths"])
        self.assertNotIn("/slice", result["paths"])
        self.assertEqual(result["busy_legacy"], {"/upload": 404, "/slice": 404})
        self.assertEqual(result["busy_estimate"], 503)

    def test_explicit_production_overrides_preserve_development_utilities(self):
        result = self.probe(APP_ENV="production", ENABLE_API_DOCS="true", ENABLE_LEGACY_ENDPOINTS="true",
                            CORS_ALLOWED_ORIGINS="http://localhost:5173")
        for path in ("/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"):
            self.assertEqual(result[path], 200)
        for path in ("/upload", "/slice"):
            self.assertEqual(result[path], 422)  # Route exists, but no STL was supplied.
        self.assertEqual(result["cors"], "http://localhost:5173")
        self.assertIsNone(result["credentials"])

    def test_invalid_surface_settings_fail_without_echoing_configured_values(self):
        cases = (("APP_ENV", SECRET), ("ENABLE_API_DOCS", SECRET), ("ENABLE_LEGACY_ENDPOINTS", "1"),
                 ("CORS_ALLOWED_ORIGINS", "*"), ("CORS_ALLOWED_ORIGINS", "https://user:password@example.com"),
                 ("CORS_ALLOWED_ORIGINS", "https://@example.com"),
                 ("CORS_ALLOWED_ORIGINS", "https://example.com/path"),
                 ("CORS_ALLOWED_ORIGINS", "https://example.com:bad"))
        for name, value in cases:
            with self.subTest(name=name, value=value), patch.dict(os.environ, self.settings_environment(**{name: value}), clear=True):
                with self.assertRaises(ValueError) as error:
                    load_api_settings()
                self.assertNotIn(value, str(error.exception))

    def test_invalid_settings_prevent_app_import(self):
        process = subprocess.run([sys.executable, "-c", "import app.main"], capture_output=True, text=True,
                                 env=self.settings_environment(APP_ENV="production", ENABLE_API_DOCS=SECRET), timeout=10)
        self.assertNotEqual(process.returncode, 0)
        self.assertIn("ENABLE_API_DOCS must be true or false", process.stderr)
        self.assertNotIn(SECRET, process.stderr)
