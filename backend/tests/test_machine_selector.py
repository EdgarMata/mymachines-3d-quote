import unittest
from copy import deepcopy
from unittest.mock import patch

from app.services.machine_selector import select_machine
from app.services.printing_config import load_printing_config
from app.services.pricing import (
    calculate_pricing,
    load_pricing_config,
)


class TestMachineSelector(unittest.TestCase):

    def test_default_machine_selection(self):
        """The Prusa should be selected for a compatible small model."""

        result = select_machine({
            "x": 130,
            "y": 72,
            "z": 20,
        })

        self.assertEqual(result["status"], "ready")

        self.assertEqual(
            result["selected_machine"]["id"],
            "prusa_mk3s_04",
        )

        self.assertEqual(
            result["quoting_candidates_count"],
            1,
        )

    def test_machine_breakdown(self):
        """An unavailable printer must not receive automatic quotes."""

        config = deepcopy(load_printing_config())

        config["machines"]["prusa_mk3s_04"]["operational"] = False

        with patch(
            "app.services.machine_selector.load_printing_config",
            return_value=config,
        ):
            result = select_machine({
                "x": 130,
                "y": 72,
                "z": 20,
            })

        self.assertEqual(
            result["status"],
            "technical_review_required",
        )

        self.assertIsNone(
            result["selected_machine"]
        )

        self.assertEqual(
            result["quoting_candidates_count"],
            0,
        )

    def test_model_exceeds_all_build_volumes(self):
        """An oversized component must not receive an automatic quote."""

        result = select_machine({
            "x": 1100,
            "y": 1100,
            "z": 1100,
        })

        self.assertEqual(
            result["status"],
            "model_exceeds_build_volume",
        )

        self.assertEqual(
            result["compatible_count"],
            0,
        )

        self.assertIsNone(
            result["selected_machine"]
        )

    def test_prusa_pricing_regression(self):
        """Preserve the validated LCD Housing reference quotation."""

        config = load_pricing_config(
            machine_id="prusa_mk3s_04"
        )

        result = calculate_pricing(
            filament_cost_eur=0.38,
            print_time_seconds=7830,
            quantity=1,
            config=config,
        )

        self.assertEqual(
            result["machine"],
            "Prusa MK3S",
        )

        self.assertAlmostEqual(
            result["summary"]["selling_price_eur"],
            11.34,
            places=2,
        )


if __name__ == "__main__":
    unittest.main()