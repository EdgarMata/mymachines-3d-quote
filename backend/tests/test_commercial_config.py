"""MM-007 configuration boundaries, synthetic data and native cost regressions."""

from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
import app.main as main
from app.services.commercial_config import (
    CommercialConfigurationError, get_commercial_config, read_commercial_config,
)
from app.services.pricing import calculate_pricing, load_pricing_config
from app.services.printing_config import PROJECT_ROOT, get_filament_profile
from app.services.slicer_process import SlicerTimeoutError
import app.services.slicer as slicer
from commercial_fixtures import demo_config, write_external_config
from test_processing import STL


SENTINEL = "SYNTHETIC_PRIVATE_MM007_DO_NOT_ECHO"


class TestCommercialConfiguration(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="mm007-")
        self.addCleanup(self.directory.cleanup)
        self.path = write_external_config(self.directory.name)
        get_commercial_config.cache_clear()
        self.addCleanup(get_commercial_config.cache_clear)

    def environment(self, **overrides):
        env = dict(os.environ)
        for key in ("APP_ENV", "PRICING_CONFIG_PATH", "ENABLE_API_DOCS", "ENABLE_LEGACY_ENDPOINTS", "CORS_ALLOWED_ORIGINS"):
            env.pop(key, None)
        return {**env, **overrides}

    def write(self, data):
        self.path.write_text(json.dumps(data), encoding="utf-8")

    def assert_invalid(self, data):
        self.write(data)
        with self.assertRaises(CommercialConfigurationError) as error:
            read_commercial_config(self.path)
        self.assertNotIn(SENTINEL, str(error.exception))
        self.assertNotIn(str(self.path), str(error.exception))

    def test_development_demo_preserves_reference_price_and_active_material_costs(self):
        with patch.dict(os.environ, self.environment(), clear=True):
            config = get_commercial_config()
            self.assertEqual(config.configuration_kind, "demonstration")
            self.assertEqual(config.materials["PLA"].cost_per_kg_eur, 16)
            self.assertEqual(config.materials["PETG"].cost_per_kg_eur, 20)
            result = calculate_pricing(filament_cost_eur=0.38, print_time_seconds=7830)
            self.assertEqual(result["summary"]["selling_price_eur"], 10.61)
            self.assertEqual(result["machine"], "Prusa MK3S")
            with self.assertRaises(ValueError):
                load_pricing_config(machine_id="")

    def test_external_labour_change_has_predictable_price_without_rebuild(self):
        data = demo_config()
        data["labour"]["cost_per_hour_eur"] = 40
        write_external_config(self.directory.name, data)
        with patch.dict(os.environ, self.environment(PRICING_CONFIG_PATH=str(self.path)), clear=True):
            result = calculate_pricing(filament_cost_eur=0.38, print_time_seconds=7830)
        self.assertEqual(result["costs"]["setup_labour_eur"], 6.67)
        self.assertEqual(result["costs"]["postprocess_labour_eur"], 3.33)
        self.assertEqual(result["summary"]["selling_price_eur"], 18.94)

    def test_snapshot_is_not_reloaded_during_requests(self):
        with patch.dict(os.environ, self.environment(PRICING_CONFIG_PATH=str(self.path)), clear=True):
            original = get_commercial_config()
            self.path.write_text("INVALID " + SENTINEL)
            self.assertIs(get_commercial_config(), original)
            self.assertEqual(load_pricing_config().labour_cost_per_hour_eur, 20)

    def test_production_requires_explicit_external_path(self):
        with patch.dict(os.environ, self.environment(APP_ENV="production"), clear=True):
            with self.assertRaisesRegex(CommercialConfigurationError, "PRICING_CONFIG_PATH"):
                get_commercial_config()

    def test_valid_external_configuration_is_accepted_in_production(self):
        with patch.dict(os.environ, self.environment(APP_ENV="production", PRICING_CONFIG_PATH=str(self.path)), clear=True):
            self.assertEqual(get_commercial_config().configuration_kind, "private")

    def test_production_rejects_bundled_demo_symlink_and_copied_demo(self):
        demo = PROJECT_ROOT / "config/pricing.demo.json"
        link = Path(self.directory.name) / "linked.json"
        link.symlink_to(demo)
        for path in (demo, link):
            with self.assertRaises(CommercialConfigurationError):
                read_commercial_config(path, production=True)
        self.write(demo_config())
        with self.assertRaisesRegex(CommercialConfigurationError, "configuration_kind"):
            read_commercial_config(self.path, production=True)

    def test_missing_empty_relative_and_directory_paths_never_fall_back(self):
        for path in ("", "relative.json", str(self.path) + ".missing", self.directory.name):
            with patch.dict(os.environ, self.environment(PRICING_CONFIG_PATH=path), clear=True):
                get_commercial_config.cache_clear()
                with self.assertRaises(CommercialConfigurationError):
                    get_commercial_config()

    def test_unreadable_file_has_safe_error(self):
        # Containers run as root; mode 000 is not an unreadability simulation.
        with patch.object(Path, "read_text", side_effect=PermissionError(SENTINEL)):
            with self.assertRaises(CommercialConfigurationError) as error:
                read_commercial_config(self.path)
        self.assertNotIn(SENTINEL, str(error.exception))

    def test_malformed_duplicate_and_non_utf8_json_are_rejected(self):
        for data in (b'{"schema_version":1,"schema_version":1}', b'{"bad":', b'\xff', b'[]', b'null'):
            self.path.write_bytes(data)
            with self.assertRaises(CommercialConfigurationError):
                read_commercial_config(self.path)

    def test_required_fields_and_complete_catalogue_entries(self):
        data = demo_config()
        for key in data:
            invalid = deepcopy(data)
            del invalid[key]
            self.assert_invalid(invalid)
        for key in data["machines"]["prusa_mk3s_04"]:
            invalid = deepcopy(data)
            del invalid["machines"]["prusa_mk3s_04"][key]
            self.assert_invalid(invalid)

    def test_unknown_root_and_nested_fields_are_rejected_without_echo(self):
        for target in ((), ("energy",), ("machines", "prusa_mk3s_04"), ("materials", "PLA")):
            data = demo_config()
            node = data
            for key in target:
                node = node[key]
            node[SENTINEL] = SENTINEL
            self.assert_invalid(data)

    def test_strict_numeric_types_do_not_accept_booleans_strings_or_null(self):
        for value in (True, "20", None, [], {}):
            data = demo_config()
            data["labour"]["cost_per_hour_eur"] = value
            self.assert_invalid(data)
        for value in (True, 1.0, "1", 2):
            data = demo_config()
            data["schema_version"] = value
            self.assert_invalid(data)

    def test_nan_infinity_and_numeric_overflow_are_rejected(self):
        for value in (float("nan"), float("inf"), float("-inf"), "NaN", "Infinity"):
            data = demo_config()
            data["materials"]["PLA"]["cost_per_kg_eur"] = value
            self.assert_invalid(data)
        self.path.write_text(json.dumps(demo_config()).replace('0.25', '1e9999', 1))
        with self.assertRaises(CommercialConfigurationError):
            read_commercial_config(self.path)

    def test_negative_costs_rates_and_times_are_rejected(self):
        for group in ("energy", "labour", "risk", "commercial"):
            for key in demo_config()[group]:
                data = demo_config()
                data[group][key] = -1
                self.assert_invalid(data)
        for key in demo_config()["machines"]["prusa_mk3s_04"]:
            data = demo_config()
            data["machines"]["prusa_mk3s_04"][key] = -1
            self.assert_invalid(data)
        data = demo_config()
        data["materials"]["PLA"]["cost_per_kg_eur"] = -1
        self.assert_invalid(data)

    def test_percentage_boundaries_and_zero_lifetime(self):
        for group, field in (("risk", "failure_rate_percent"), ("commercial", "target_margin_percent")):
            for value in (0, 99.999):
                data = demo_config()
                data[group][field] = value
                self.write(data)
                read_commercial_config(self.path)
            data[group][field] = 100
            self.assert_invalid(data)
        data = demo_config()
        data["machines"]["prusa_mk3s_04"]["lifetime_hours"] = 0
        self.assert_invalid(data)

    def test_zero_costs_are_allowed_and_existing_depreciation_floor_is_preserved(self):
        data = demo_config()
        data["energy"]["electricity_cost_per_kwh_eur"] = 0
        data["materials"]["PLA"]["cost_per_kg_eur"] = 0
        data["machines"]["prusa_mk3s_04"]["residual_value_eur"] = 1000
        self.write(data)
        read_commercial_config(self.path)

    def test_unknown_and_missing_machine_material_references(self):
        for group in ("machines", "materials"):
            data = demo_config()
            first = next(iter(data[group]))
            data[group][SENTINEL] = data[group].pop(first)
            self.assert_invalid(data)
            data = demo_config()
            del data[group][first]
            self.assert_invalid(data)
        from app.services.printing_config import load_printing_config
        catalogue = load_printing_config()
        del catalogue["machines"]["prusa_mk3s_04"]["pricing_name"]
        write_external_config(self.directory.name)
        with patch("app.services.commercial_config.load_printing_config", return_value=catalogue):
            with self.assertRaisesRegex(CommercialConfigurationError, "pricing_name"):
                read_commercial_config(self.path)

    def test_currency_kind_and_nonfinite_unit_costs(self):
        for key, value in (("currency", "USD"), ("configuration_kind", SENTINEL)):
            data = demo_config()
            data[key] = value
            self.assert_invalid(data)
        data = demo_config()
        data["energy"]["electricity_cost_per_kwh_eur"] = 1e308
        data["machines"]["prusa_mk3s_04"]["average_power_kw"] = 1e308
        self.assert_invalid(data)

    def test_nested_duplicate_fields_are_rejected(self):
        self.path.write_text(json.dumps(demo_config()).replace(
            '"cost_per_kg_eur": 16', '"cost_per_kg_eur": 16, "cost_per_kg_eur": 17'))
        with self.assertRaisesRegex(CommercialConfigurationError, "duplicate"):
            read_commercial_config(self.path)

    def test_finite_unit_costs_cannot_overflow_a_quantity_quote(self):
        data = demo_config()
        data["labour"]["cost_per_hour_eur"] = 1e308
        self.write(data)
        read_commercial_config(self.path)  # Valid unit rates; no arbitrary currency cap.
        with patch.dict(os.environ, self.environment(PRICING_CONFIG_PATH=str(self.path)), clear=True):
            with self.assertRaisesRegex(ValueError, "non-finite"):
                calculate_pricing(filament_cost_eur=0.38, print_time_seconds=7830, quantity=100)

    def test_zero_labour_does_not_mask_overflow_of_postprocess_hours(self):
        data = demo_config()
        data["labour"]["cost_per_hour_eur"] = 0
        data["machines"]["prusa_mk3s_04"]["postprocess_minutes_per_piece"] = 1e308
        self.write(data)
        read_commercial_config(self.path)
        with patch.dict(os.environ, self.environment(PRICING_CONFIG_PATH=str(self.path)), clear=True):
            with self.assertRaisesRegex(ValueError, "non-finite"):
                calculate_pricing(filament_cost_eur=0.38, print_time_seconds=7830, quantity=100)

    def test_large_representable_prices_are_allowed(self):
        data = demo_config()
        data["commercial"]["minimum_order_eur"] = 1e300
        self.write(data)
        with patch.dict(os.environ, self.environment(PRICING_CONFIG_PATH=str(self.path)), clear=True):
            result = calculate_pricing(filament_cost_eur=0.38, print_time_seconds=7830)
        self.assertEqual(result["summary"]["selling_price_eur"], 1e300)

    def test_overflow_cannot_be_returned_as_a_successful_public_quote(self):
        data = demo_config()
        data["labour"]["cost_per_hour_eur"] = 1e308
        self.write(data)
        stats = {"material_cost_eur": 0.38, "print_time_seconds": 7830, "filament_g": 23.75}
        with patch.dict(os.environ, self.environment(PRICING_CONFIG_PATH=str(self.path)), clear=True), \
                patch.object(main, "slice_stl", return_value=stats):
            response = TestClient(app).post("/estimate", params={"quantity": 100},
                files={"file": ("synthetic.stl", STL)})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"detail": "Pricing failed."})

    def test_original_profiles_and_runtime_cleanup_survive_errors_and_timeouts(self):
        model = Path(self.directory.name) / "synthetic.stl"
        model.write_bytes(STL)
        for material in ("PLA", "PETG"):
            original = get_filament_profile(material).read_bytes()
            for failure in (RuntimeError("Synthetic failure"), SlicerTimeoutError()):
                with self.subTest(material=material, failure=type(failure).__name__):
                    paths = []

                    def fail(command, **kwargs):
                        runtime = Path(command[command.index("--load-filaments") + 1])
                        paths.append(runtime)
                        self.assertTrue(runtime.is_file())
                        raise failure

                    with patch.object(slicer, "run_slicer_process", side_effect=fail):
                        with self.assertRaises(type(failure)):
                            slicer.slice_stl(model, material=material)
                    self.assertTrue(paths)
                    self.assertFalse(any(path.exists() for path in paths))
                    self.assertEqual(get_filament_profile(material).read_bytes(), original)

    def test_real_slicer_uses_private_material_cost_and_cleans_runtime_profile(self):
        model = Path(self.directory.name) / "synthetic.stl"
        model.write_bytes(STL)
        original = get_filament_profile("PLA").read_bytes()
        baseline = slicer.slice_stl(model)
        self.assertGreater(baseline["material_cost_eur"], 0)
        data = demo_config()
        data["materials"]["PLA"]["cost_per_kg_eur"] = 0
        write_external_config(self.directory.name, data)
        get_commercial_config.cache_clear()
        paths = []
        native = slicer.run_slicer_process

        def inspect(command, **kwargs):
            runtime = Path(command[command.index("--load-filaments") + 1])
            paths.append(runtime)
            profile = json.loads(runtime.read_text())
            expected = json.loads(original)
            expected["filament_cost"] = ["0.0"]
            self.assertEqual(profile, expected)
            return native(command, **kwargs)

        with patch.dict(os.environ, self.environment(PRICING_CONFIG_PATH=str(self.path)), clear=True), \
                patch.object(slicer, "run_slicer_process", side_effect=inspect):
            result = slicer.slice_stl(model)
        self.assertEqual(result["material_cost_eur"], 0)
        self.assertEqual(result["print_time_seconds"], baseline["print_time_seconds"])
        self.assertEqual(result["filament_g"], baseline["filament_g"])
        self.assertTrue(paths)
        self.assertFalse(any(path.exists() for path in paths))
        self.assertEqual(get_filament_profile("PLA").read_bytes(), original)

    def test_public_and_legacy_responses_never_expose_external_costs(self):
        data = demo_config()
        data["energy"]["electricity_cost_per_kwh_eur"] = 97.123456789
        write_external_config(self.directory.name, data)
        stats = {"material_cost_eur": 97.123456789, "print_time_seconds": 7830, "filament_g": 23.75}
        with patch.dict(os.environ, self.environment(PRICING_CONFIG_PATH=str(self.path)), clear=True), \
                patch.object(main, "slice_stl", return_value=stats):
            client = TestClient(app)
            for route in ("/options", "/upload", "/analyze", "/slice", "/estimate"):
                response = (client.get(route) if route == "/options" else
                            client.post(route, files={"file": ("synthetic.stl", STL)}))
                self.assertEqual(response.status_code, 200)
                self.assertNotIn("97.123456789", response.text)
                self.assertNotIn(str(self.path), response.text)
                self.assertNotIn("cost_per_kg_eur", response.text)
                self.assertNotIn("electricity_cost_per_kwh_eur", response.text)

    def test_production_startup_health_and_invalid_startup_are_safe(self):
        code = ("from app.main import app; from fastapi.testclient import TestClient; "
                "print(TestClient(app).get('/health').json())")
        process = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
            env=self.environment(APP_ENV="production", PRICING_CONFIG_PATH=str(self.path)), timeout=10)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertIn("healthy", process.stdout)
        self.path.write_text('{"' + SENTINEL + '": "' + SENTINEL + '"}')
        process = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
            env=self.environment(APP_ENV="production", PRICING_CONFIG_PATH=str(self.path)), timeout=10)
        self.assertNotEqual(process.returncode, 0)
        self.assertNotIn(SENTINEL, process.stderr + process.stdout)
        self.assertNotIn(str(self.path), process.stderr + process.stdout)
