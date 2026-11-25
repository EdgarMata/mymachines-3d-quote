import struct
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import numpy as np
import trimesh
from fastapi.testclient import TestClient

from app.main import app
from app.services import geometry_validation as geometry
from app.services import input_validation as inputs
from app.services.orientation import orient_mesh, orient_stl
from app.services.printing_config import load_printing_config

ENDPOINTS = ("/upload", "/analyze", "/slice", "/estimate")
SLICING = {"material_cost_eur": 0.38, "print_time_seconds": 7830, "filament_g": 23.75}


class TestGeometryValidation(unittest.TestCase):
    def setUp(self):
        self.box = trimesh.creation.box(extents=[10, 20, 30])
        self.binary = self.box.export(file_type="stl")
        self.ascii = self.box.export(file_type="stl_ascii").encode("ascii")

    def test_valid_binary_and_ascii(self):
        for contents in (self.binary, self.ascii):
            with self.subTest(binary=contents is self.binary):
                mesh = geometry.load_validated_stl(contents)
                self.assertEqual(len(mesh.faces), 12)
                self.assertEqual(geometry.geometry_summary(mesh)["dimensions_mm"],
                                 {"x": 10.0, "y": 20.0, "z": 30.0})
                self.assertAlmostEqual(mesh.volume, 6000)
                self.assertTrue(mesh.is_watertight)

    def test_binary_header_starting_with_solid(self):
        contents = b"solid binary".ljust(80, b" ") + self.binary[80:]
        self.assertEqual(len(geometry.load_validated_stl(contents).faces), 12)

    def test_multiple_ascii_solids(self):
        self.assertEqual(len(geometry.load_validated_stl(self.ascii + b"\n" + self.ascii).faces), 24)

    def test_triangle_boundary_and_preflight_before_trimesh(self):
        for contents in (self.binary, self.ascii):
            for limit in (11, 12, 13):
                with self.subTest(limit=limit, binary=contents is self.binary), patch.object(
                    geometry, "MAX_STL_TRIANGLES", limit
                ), patch.object(geometry.trimesh, "load", wraps=trimesh.load) as loader:
                    if limit < 12:
                        with self.assertRaises(inputs.InputValidationError) as error:
                            geometry.load_validated_stl(contents)
                        self.assertEqual(error.exception.status_code, 413)
                        loader.assert_not_called()
                    else:
                        self.assertEqual(len(geometry.load_validated_stl(contents).faces), 12)
                        loader.assert_called_once()

    def test_corrupted_and_empty_stl_rejected_before_loader(self):
        cases = (b"", b"garbage", self.binary[:-1], self.binary + b"trailing",
                 self.binary[:80] + struct.pack("<I", 13) + self.binary[84:],
                 b"\0" * 84, b"solid empty\nendsolid empty\n",
                 self.ascii.replace(b"endfacet", b"invalid", 1),
                 self.ascii.replace(b"outer loop", b"outer", 1),
                 self.ascii.replace(b"facet normal", b"facet normal 1_0", 1),
                 self.ascii.replace(b"endsolid", b"unfinished", 1),
                 self.ascii + b"trailing junk", b"solid " + b"x" * 4096)
        for contents in cases:
            with self.subTest(prefix=contents[:30]), patch.object(geometry.trimesh, "load") as loader:
                with self.assertRaises(inputs.InputValidationError):
                    geometry.load_validated_stl(contents)
                loader.assert_not_called()

    def test_nonfinite_binary_vertices_and_normals_before_loader(self):
        for number in (float("nan"), float("inf"), -float("inf")):
            for offset in (84, 96):  # facet normal and first vertex
                contents = bytearray(self.binary)
                struct.pack_into("<f", contents, offset, number)
                with self.subTest(number=number, offset=offset), patch.object(geometry.trimesh, "load") as loader:
                    with self.assertRaises(inputs.InputValidationError):
                        geometry.load_validated_stl(bytes(contents))
                    loader.assert_not_called()

    def test_nonfinite_ascii_vertices_and_normals_before_loader(self):
        for number in (b"NaN", b"Infinity", b"-Infinity", b"1e999"):
            for keyword in (b"vertex", b"facet normal"):
                lines = self.ascii.splitlines()
                index = next(i for i, line in enumerate(lines) if line.strip().startswith(keyword))
                lines[index] = keyword + b" " + number + b" 0 0"
                with self.subTest(number=number, keyword=keyword), patch.object(geometry.trimesh, "load") as loader:
                    with self.assertRaises(inputs.InputValidationError):
                        geometry.load_validated_stl(b"\n".join(lines))
                    loader.assert_not_called()

    def test_degenerate_geometry_and_unrepresentable_coordinates(self):
        cases = [trimesh.Trimesh(vertices=np.zeros((3, 3)), faces=[[0, 1, 2]], process=False)]
        huge = self.box.copy()
        huge.apply_scale(1e40)
        cases.append(huge)
        for mesh in cases:
            with self.subTest(extents=mesh.extents), self.assertRaises(inputs.InputValidationError):
                geometry.load_validated_stl(mesh.export(file_type="stl_ascii").encode())

    def test_open_shell_and_partial_degeneracy_allowed(self):
        open_mesh = trimesh.Trimesh(vertices=self.box.vertices, faces=self.box.faces[:-1], process=False)
        for mesh in (open_mesh, trimesh.util.concatenate([
            open_mesh, trimesh.Trimesh(vertices=np.zeros((3, 3)), faces=[[0, 1, 2]], process=False)
        ])):
            result = geometry.load_validated_stl(mesh.export(file_type="stl"))
            self.assertFalse(result.is_watertight)
            self.assertGreater(result.area, 0)

    def test_finite_derived_geometry_required(self):
        mesh = geometry.load_validated_stl(self.binary)
        for key, value in (("extents", np.array([np.inf, 1, 1])), ("area", np.inf), ("volume", np.nan)):
            with self.subTest(key=key), patch.object(type(mesh), key, new=property(lambda self, v=value: v)):
                with self.assertRaises(inputs.InputValidationError):
                    geometry.validate_mesh(mesh)

    def test_orientation_xyz_and_path_wrapper(self):
        mesh = geometry.load_validated_stl(self.binary)
        with tempfile.TemporaryDirectory() as directory:
            original, output = Path(directory) / "original.stl", Path(directory) / "oriented.stl"
            original.write_bytes(self.binary)
            result = orient_stl(original, output, x=90, y=0, z=0)
            self.assertEqual(result["dimensions_mm"], {"x": 10, "y": 30, "z": 20})
            self.assertEqual(result["bed_position"]["minimum_z_mm"], 0)
            # Match the existing Three.js XYZ matrix multiplication for mixed angles.
            expected = mesh.copy()
            rotations = [trimesh.transformations.rotation_matrix(np.radians(a), axis)
                         for a, axis in zip((30, -45, 120), np.eye(3))]
            expected.apply_transform(rotations[0] @ rotations[1] @ rotations[2])
            result = orient_mesh(mesh, output, x=30, y=-45, z=120)
            np.testing.assert_allclose(list(result["dimensions_mm"].values()), expected.extents, atol=0.001)
            np.testing.assert_allclose(mesh.extents, [10, 20, 30])
            result = orient_mesh(mesh, output, x=810, y=-720, z=0)
            self.assertEqual(result["dimensions_mm"], {"x": 10, "y": 30, "z": 20})

    def test_positive_integer_configuration(self):
        for name in ("MAX_STL_TRIANGLES", "MAX_QUANTITY"):
            for value in ("0", "-1", "abc", "1.5", ""):
                with self.subTest(name=name, value=value), patch.dict("os.environ", {name: value}):
                    with self.assertRaises(ValueError):
                        inputs.positive_integer_setting(name, 100)
            with patch.dict("os.environ", {name: "25"}):
                self.assertEqual(inputs.positive_integer_setting(name, 100), 25)


class TestGeometryEndpoints(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        patcher = patch("app.main.PROJECT_ROOT", Path(self.directory.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.mesh = trimesh.creation.box(extents=[10, 20, 30])
        self.binary = self.mesh.export(file_type="stl")
        self.ascii = self.mesh.export(file_type="stl_ascii").encode()

    def post(self, endpoint, contents=None, **kwargs):
        return self.client.post(endpoint, files={"file": ("part.stl", self.binary if contents is None else contents, "model/stl")}, **kwargs)

    def test_binary_and_ascii_all_endpoints_load_once(self):
        for contents in (self.binary, self.ascii):
            for endpoint in ENDPOINTS:
                with self.subTest(endpoint=endpoint, binary=contents is self.binary), patch(
                    "app.main.slice_stl", return_value=SLICING
                ), patch.object(geometry.trimesh, "load", wraps=trimesh.load) as loader:
                    response = self.post(endpoint, contents)
                    self.assertEqual(response.status_code, 200, response.text)
                    loader.assert_called_once()
                    if "geometry" in response.json():
                        self.assertEqual(response.json()["geometry"]["faces"], 12)

    def test_corrupt_empty_and_degenerate_geometry_all_endpoints(self):
        degenerate = trimesh.Trimesh(vertices=np.zeros((3, 3)), faces=[[0, 1, 2]], process=False).export(file_type="stl")
        for contents in (b"invalid geometry", self.binary[:-1], b"\0" * 84,
                         b"solid empty\nendsolid empty", degenerate):
            for endpoint in ENDPOINTS:
                with self.subTest(endpoint=endpoint, contents=contents[:20]), patch("app.main.slice_stl") as slicer:
                    response = self.post(endpoint, contents)
                    self.assertEqual(response.status_code, 400, response.text)
                    self.assertIn("detail", response.json())
                    self.assertNotIn(self.directory.name, response.text)
                    slicer.assert_not_called()
        self.assertEqual(list(Path(self.directory.name).iterdir()), [])

    def test_nonfinite_coordinates_all_endpoints(self):
        for number in (float("nan"), float("inf"), -float("inf")):
            contents = bytearray(self.binary)
            struct.pack_into("<f", contents, 96, number)
            for endpoint in ENDPOINTS:
                with self.subTest(endpoint=endpoint, number=number), patch("app.main.slice_stl") as slicer:
                    response = self.post(endpoint, bytes(contents))
                    self.assertEqual(response.status_code, 400, response.text)
                    self.assertEqual(response.json()["detail"], "STL coordinates and normals must be finite numbers.")
                    slicer.assert_not_called()

    def test_triangle_limits_all_endpoints(self):
        for contents in (self.binary, self.ascii):
            for endpoint in ENDPOINTS:
                for limit, status in ((11, 413), (12, 200), (13, 200)):
                    with self.subTest(endpoint=endpoint, limit=limit), patch.object(
                        geometry, "MAX_STL_TRIANGLES", limit
                    ), patch("app.main.slice_stl", return_value=SLICING) as slicer:
                        response = self.post(endpoint, contents)
                        self.assertEqual(response.status_code, status, response.text)
                        if status == 413:
                            slicer.assert_not_called()

    def test_invalid_rotations_before_geometry_or_slicing(self):
        for endpoint in ("/analyze", "/estimate"):
            for axis in ("rotation_x", "rotation_y", "rotation_z"):
                for value in ("NaN", "Infinity", "-Infinity", "1e999", "not-a-number"):
                    with self.subTest(endpoint=endpoint, axis=axis, value=value), patch(
                        "app.main.load_validated_stl"
                    ) as loader, patch("app.main.slice_stl") as slicer:
                        response = self.post(endpoint, params={axis: value})
                        self.assertEqual(response.status_code, 422 if value == "not-a-number" else 400, response.text)
                        loader.assert_not_called()
                        slicer.assert_not_called()

    def test_finite_rotations_have_no_arbitrary_range_limit(self):
        for endpoint in ("/analyze", "/estimate"):
            for angle in (-810, 810, 1e300):
                with self.subTest(endpoint=endpoint, angle=angle), patch("app.main.slice_stl", return_value=SLICING):
                    response = self.post(endpoint, params={"rotation_x": angle})
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.json()["orientation"]["rotation_degrees"]["x"], angle)

    def test_invalid_quantities_before_geometry(self):
        for quantity in (-1, 0, 101, "1.5", "abc"):
            with self.subTest(quantity=quantity), patch("app.main.load_validated_stl") as loader, patch("app.main.slice_stl") as slicer:
                response = self.post("/estimate", params={"quantity": quantity})
                self.assertEqual(response.status_code, 400 if quantity in (-1, 0, 101) else 422, response.text)
                loader.assert_not_called()
                slicer.assert_not_called()

    def test_quantity_boundaries_and_override_preserve_quote(self):
        for maximum in (100, 5):
            with patch.object(inputs, "MAX_QUANTITY", maximum), patch("app.main.slice_stl", return_value=SLICING):
                for quantity in (1, maximum):
                    response = self.post("/estimate", params={"quantity": quantity})
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.json()["pricing"]["quantity"], quantity)
                response = self.post("/estimate", params={"quantity": maximum + 1})
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(response.json()["detail"], f"Quantity must be between 1 and {maximum}.")

    def test_material_quality_membership_and_combination(self):
        config = deepcopy(load_printing_config())
        config["materials"]["PETG"]["qualities"] = ["standard"]
        for material, quality in (("ASA", "standard"), ("PLA", "unknown"), ("PETG", "fine")):
            with self.subTest(material=material, quality=quality), patch(
                "app.services.input_validation.load_printing_config", return_value=config
            ), patch("app.main.load_validated_stl") as loader, patch("app.main.slice_stl") as slicer:
                response = self.post("/estimate", params={"material": material, "quality": quality})
                self.assertEqual(response.status_code, 400, response.text)
                loader.assert_not_called()
                slicer.assert_not_called()
        with patch("app.main.slice_stl", return_value=SLICING) as slicer:
            response = self.post("/estimate", params={"material": "petg", "quality": "STANDARD"})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["material"], "PETG")
            self.assertEqual(slicer.call_args.kwargs["quality"], "standard")

    def test_legacy_slice_validates_default_combination(self):
        config = deepcopy(load_printing_config())
        config["materials"]["PLA"]["qualities"] = ["fine"]
        with patch("app.services.input_validation.load_printing_config", return_value=config), patch("app.main.slice_stl") as slicer:
            response = self.post("/slice")
            self.assertEqual(response.status_code, 400)
            slicer.assert_not_called()

    def test_open_shell_is_accepted_by_all_endpoints(self):
        mesh = trimesh.Trimesh(vertices=self.mesh.vertices, faces=self.mesh.faces[:-1], process=False)
        with patch("app.main.slice_stl", return_value=SLICING):
            for endpoint in ENDPOINTS:
                response = self.post(endpoint, mesh.export(file_type="stl"))
                self.assertEqual(response.status_code, 200, response.text)
                if "geometry" in response.json():
                    self.assertFalse(response.json()["geometry"]["watertight"])

    def test_build_volume_and_technical_review_statuses_preserved(self):
        for size, expected in ((1100, "model_exceeds_build_volume"), (300, "technical_review_required")):
            contents = trimesh.creation.box(extents=[size] * 3).export(file_type="stl")
            for endpoint in ("/analyze", "/estimate"):
                with self.subTest(endpoint=endpoint, size=size), patch("app.main.slice_stl") as slicer:
                    response = self.post(endpoint, contents)
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.json()["status"], expected)
                    self.assertNotIn("output_file", response.json()["orientation"])
                    if endpoint == "/estimate":
                        self.assertIsNone(response.json()["pricing"])
                    slicer.assert_not_called()

    def test_machine_and_reference_price_regression(self):
        with patch("app.main.slice_stl", return_value=SLICING):
            response = self.post("/estimate")
        result = response.json()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(result["machine_selection"]["selected_machine"]["id"], "prusa_mk3s_04")
        self.assertEqual(result["pricing"]["selling_price_eur"], 10.61)
        self.assertNotIn("output_file", result["orientation"])

    def test_processing_errors_do_not_expose_internal_details(self):
        for endpoint, dependency in (("/slice", "slice_stl"), ("/analyze", "orient_mesh"), ("/estimate", "slice_stl"), ("/estimate", "calculate_pricing")):
            with self.subTest(endpoint=endpoint, dependency=dependency), patch("app.main.slice_stl", return_value=SLICING), patch(
                "app.main." + dependency, side_effect=RuntimeError("private /server/path and internal output")
            ):
                response = self.post(endpoint)
                self.assertEqual(response.status_code, 500, response.text)
                self.assertNotIn("private", response.text)
                self.assertNotIn("/server/path", response.text)


    def test_ascii_real_orca_estimate(self):
        response = self.post("/estimate", self.ascii)
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["status"], "estimated")
        self.assertEqual(result["machine_selection"]["selected_machine"]["id"], "prusa_mk3s_04")
        self.assertGreater(result["slicing"]["print_time_seconds"], 0)
        self.assertGreater(result["pricing"]["selling_price_eur"], 0)

    def test_slicer_service_rejects_invalid_combination_before_subprocess(self):
        from app.services.slicer import slice_stl

        config = deepcopy(load_printing_config())
        config["materials"]["PETG"]["qualities"] = ["standard"]
        with patch("app.services.input_validation.load_printing_config", return_value=config), patch(
            "app.services.slicer.run_slicer_process"
        ) as subprocess:
            with self.assertRaises(inputs.InputValidationError):
                slice_stl(Path(self.directory.name) / "part.stl", material="PETG", quality="fine")
            subprocess.assert_not_called()
