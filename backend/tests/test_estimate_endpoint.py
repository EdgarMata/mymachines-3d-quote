import unittest
from pathlib import Path

import trimesh
from fastapi.testclient import TestClient

from app.main import app


class TestEstimateEndpoint(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

        cls.test_file = Path(
            "/tmp/mycloner-estimate-endpoint-test.stl"
        )

        outer = 220
        bar = 10
        height = 5

        top = trimesh.creation.box(
            extents=[outer, bar, height]
        )
        top.apply_translation(
            [0, (outer - bar) / 2, height / 2]
        )

        bottom = trimesh.creation.box(
            extents=[outer, bar, height]
        )
        bottom.apply_translation(
            [0, -(outer - bar) / 2, height / 2]
        )

        left = trimesh.creation.box(
            extents=[
                bar,
                outer - 2 * bar,
                height,
            ]
        )
        left.apply_translation(
            [-(outer - bar) / 2, 0, height / 2]
        )

        right = trimesh.creation.box(
            extents=[
                bar,
                outer - 2 * bar,
                height,
            ]
        )
        right.apply_translation(
            [(outer - bar) / 2, 0, height / 2]
        )

        mesh = trimesh.util.concatenate(
            [top, bottom, left, right]
        )

        mesh.export(cls.test_file)

    @classmethod
    def tearDownClass(cls):
        if cls.test_file.exists():
            cls.test_file.unlink()

    def test_estimate_uses_my_cloner_end_to_end(self):
        with self.test_file.open("rb") as stl_file:
            response = self.client.post(
                (
                    "/estimate"
                    "?quantity=1"
                    "&material=PLA"
                    "&quality=standard"
                    "&rotation_x=0"
                    "&rotation_y=0"
                    "&rotation_z=0"
                ),
                files={
                    "file": (
                        self.test_file.name,
                        stl_file,
                        "model/stl",
                    )
                },
            )

        self.assertEqual(
            response.status_code,
            200,
        )

        result = response.json()

        self.assertEqual(
            result["status"],
            "estimated",
        )

        self.assertEqual(
            result["machine_selection"]
            ["selected_machine"]
            ["id"],
            "my_cloner_04",
        )

        self.assertEqual(
            result["slicing"]["machine_id"],
            "my_cloner_04",
        )

        self.assertEqual(
            result["slicing"]["printer_profile"],
            "my_cloner_04.json",
        )

        self.assertEqual(
            result["pricing"]["machine"],
            "MyCloner",
        )


if __name__ == "__main__":
    unittest.main()