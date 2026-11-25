"""Final-review regressions: unusable slicing, extreme coordinates and counts."""

import struct
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest.mock import patch

import numpy as np
import trimesh
from fastapi.testclient import TestClient

from app.main import app
from app.services import geometry_validation as geometry
from app.services.input_validation import InputValidationError
from app.services.orientation import orient_mesh
from app.services.slicer import extract_gcode_stats, parse_print_time, slice_stl, validate_slicing_result

VALID_STATS = {"print_time_seconds": 60, "filament_g": 1.0, "material_cost_eur": 0.02}


def box_ascii(scale=1.0, offset=0.0):
    # Generate large coordinates as text without asking Trimesh to merge them.
    text = trimesh.creation.box(extents=[2, 2, 2]).export(file_type="stl_ascii")
    lines = []
    for line in text.splitlines():
        if line.strip().startswith("vertex "):
            xyz = [float(value) * scale + offset for value in line.split()[1:]]
            line = "vertex " + " ".join(str(value) for value in xyz)
        lines.append(line)
    return "\n".join(lines).encode()


class TestFinalGeometryBoundaries(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        patcher = patch("app.main.PROJECT_ROOT", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(app)
        self.plane = trimesh.Trimesh(
            vertices=[[0, 0, 0], [10, 0, 0], [0, 10, 0]], faces=[[0, 1, 2]], process=False
        ).export(file_type="stl")

    def post(self, endpoint, contents):
        return self.client.post(endpoint, files={"file": ("part.stl", contents, "model/stl")})

    def test_zero_volume_analysis_allowed(self):
        response = self.post("/upload", self.plane)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["geometry"]["volume_mm3"], 0)
        self.assertGreater(response.json()["geometry"]["surface_area_mm2"], 0)
        response = self.post("/analyze", self.plane)
        self.assertEqual(response.status_code, 200, response.text)

    def test_invalid_slice_statistics_never_reach_pricing(self):
        cases = [{}]
        for key in ("print_time_seconds", "filament_g", "material_cost_eur"):
            for value in (None, -1, float("nan"), float("inf"), "1", True):
                cases.append({**VALID_STATS, key: value})
            cases.append({k: v for k, v in VALID_STATS.items() if k != key})
        for key in ("print_time_seconds", "filament_g", "filament_mm", "filament_cm3", "layers"):
            cases.append({**VALID_STATS, key: 0})
        for contents in (self.plane, box_ascii()):
            for stats in cases:
                with self.subTest(stats=stats, plane=contents is self.plane), patch(
                    "app.main.slice_stl", return_value=stats
                ), patch("app.main.calculate_pricing") as pricing:
                    response = self.post("/estimate", contents)
                    self.assertEqual(response.status_code, 422, response.text)
                    self.assertNotIn("pricing", response.json())
                    pricing.assert_not_called()

    def test_rounded_zero_material_cost_does_not_reject_positive_consumption(self):
        # Cost can round to zero; require positive physical mass/time, not a price floor.
        validate_slicing_result({**VALID_STATS, "material_cost_eur": 0})
        with self.assertRaises(InputValidationError):
            validate_slicing_result({**VALID_STATS, "print_time_seconds": 10 ** 1000})

    def test_zero_volume_real_orca_does_not_create_empty_quote(self):
        with patch("app.main.calculate_pricing") as pricing:
            response = self.post("/estimate", self.plane)
        self.assertIn(response.status_code, (422, 500), response.text)
        self.assertNotIn("pricing", response.json())
        pricing.assert_not_called()

    def test_extreme_finite_geometry_and_export_without_numeric_warnings(self):
        # Safe large geometry is still valid: it receives the existing oversize status.
        with warnings.catch_warnings():
            warnings.simplefilter("error", RuntimeWarning)
            for scale, offset in ((1e12, 0), (1e35, 0), (1e30, 1e35)):
                contents = box_ascii(scale, offset)
                mesh = geometry.load_validated_stl(contents)
                self.assertEqual(len(mesh.faces), 12)
                result = orient_mesh(mesh, self.root / "oriented.stl", x=30, y=-45, z=120)
                self.assertTrue(np.isfinite(list(result["dimensions_mm"].values())).all())
                self.assertEqual(result["bed_position"]["minimum_z_mm"], 0)
                reloaded = geometry.load_validated_stl((self.root / "oriented.stl").read_bytes())
                self.assertTrue(np.isfinite(reloaded.vertices).all())
                self.assertGreater(reloaded.area, 0)
                with patch("app.main.slice_stl") as slicer:
                    response = self.post("/estimate", contents)
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.json()["status"], "model_exceeds_build_volume")
                    slicer.assert_not_called()

    def test_unrepresentable_input_and_transformed_binary_output_rejected(self):
        with warnings.catch_warnings():
            warnings.simplefilter("error", RuntimeWarning)
            for scale in (1e40, 1e200, 1e308):
                # Multiplication is Python arithmetic on a unit box, no input Inf.
                contents = box_ascii(scale)
                response = self.post("/analyze", contents)
                self.assertEqual(response.status_code, 400, response.text)
            # Input vertices fit float32; placing minimum Z on the bed no longer fits.
            contents = box_ascii(2e38)
            self.assertEqual(self.post("/upload", contents).status_code, 200)
            for endpoint in ("/analyze", "/estimate"):
                with patch("app.main.slice_stl") as slicer:
                    response = self.post(endpoint, contents)
                    self.assertEqual(response.status_code, 400, response.text)
                    slicer.assert_not_called()
            self.assertFalse(any(self.root.rglob("oriented.stl")))

    def test_forged_binary_count_cannot_bypass_limit(self):
        contents = trimesh.creation.box().export(file_type="stl")
        for header in (b"binary".ljust(80, b" "), b"solid fake".ljust(80, b" ")):
            for count in (0, 1, 11, 12, 13, 2 ** 32 - 1):
                forged = header + struct.pack("<I", count) + contents[84:]
                with self.subTest(count=count, header=header[:10]), patch.object(
                    geometry, "MAX_STL_TRIANGLES", 11
                ), patch.object(geometry.trimesh, "load") as loader:
                    with self.assertRaises(InputValidationError):
                        geometry.load_validated_stl(forged)
                    loader.assert_not_called()

    def test_ascii_triangle_limit_counts_across_solid_blocks(self):
        contents = box_ascii()
        with patch.object(geometry, "MAX_STL_TRIANGLES", 12), patch.object(geometry.trimesh, "load") as loader:
            with self.assertRaises(InputValidationError) as error:
                geometry.load_validated_stl(contents + b"\n" + contents)
            self.assertEqual(error.exception.status_code, 413)
            loader.assert_not_called()

    def test_stats_parser_rejects_partial_invalid_tokens(self):
        gcode = self.root / "test.gcode"
        for value in ("0", "-1", "NaN", "Infinity", "1e999", "1.2.3"):
            gcode.write_text(
                f"; total filament used [g] = {value}\n"
                "; total filament cost = 0.02\n"
                "; estimated printing time (normal mode) = 1m 0s\n"
            )
            with self.subTest(value=value), self.assertRaises((InputValidationError, ValueError)):
                validate_slicing_result(extract_gcode_stats(gcode))
        self.assertEqual(parse_print_time("-1h 10m"), 0)
        self.assertEqual(parse_print_time("1h trailing"), 0)
        self.assertEqual(parse_print_time("2h 36m 6s"), 9366)

    def test_slicer_service_rejects_gcode_without_positive_statistics(self):
        source = self.root / "source.stl"
        source.write_bytes(self.plane)

        def fake_orca(command, **kwargs):
            output = Path(command[command.index("--outputdir") + 1])
            (output / "empty.gcode").write_text("; no useful print statistics\n")
            from subprocess import CompletedProcess
            return CompletedProcess(command, 0, "", "")

        with patch("app.services.slicer.PROJECT_ROOT", self.root), patch(
            "app.services.slicer.run_slicer_process", side_effect=fake_orca
        ):
            with self.assertRaises(InputValidationError) as error:
                slice_stl(source)
        self.assertEqual(error.exception.status_code, 422)
        self.assertFalse(any((self.root / "tmp" / "slicer").rglob("*.gcode")))
