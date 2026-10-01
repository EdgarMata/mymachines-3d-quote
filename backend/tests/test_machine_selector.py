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

        self.assertEqual(
            result["status"],
            "ready",
        )

        self.assertEqual(
            result["selected_machine"]["id"],
            "prusa_mk3s_04",
        )

        self.assertEqual(
            result["quoting_candidates_count"],
            2,
        )

    def test_machine_breakdown_uses_alternative_machine(self):
        """A compatible operational machine must replace an unavailable printer."""

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
            "ready",
        )

        self.assertEqual(
            result["selected_machine"]["id"],
            "my_cloner_04",
        )

        self.assertEqual(
            result["quoting_candidates_count"],
            1,
        )

    def test_all_compatible_machines_unavailable(self):
        """Technical review is required when no compatible printer is available."""

        config = deepcopy(load_printing_config())

        config["machines"]["prusa_mk3s_04"]["operational"] = False
        config["machines"]["my_cloner_04"]["operational"] = False

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

    def test_my_cloner_selected_when_prusa_is_too_small(self):
        """MyCloner must be selected when the model does not fit the Prusa."""

        result = select_machine({
            "x": 220,
            "y": 220,
            "z": 100,
        })

        self.assertEqual(
            result["status"],
            "ready",
        )

        self.assertEqual(
            result["selected_machine"]["id"],
            "my_cloner_04",
        )

        self.assertEqual(
            result["quoting_candidates_count"],
            1,
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
            10.61,
            places=2,
        )
    
    def test_failure_exposed_cost_excludes_labour(self):
        config = load_pricing_config(
            machine_id="prusa_mk3s_04"
        )

        filament_cost_eur = 0.38
        print_time_seconds = 7830
        quantity = 1

        result = calculate_pricing(
            filament_cost_eur=filament_cost_eur,
            print_time_seconds=print_time_seconds,
            quantity=quantity,
            config=config,
        )

        total_print_hours = (
            print_time_seconds / 3600
        ) * quantity

        depreciable_value = (
            config.machine_purchase_cost_eur
            - config.machine_residual_value_eur
        )

        depreciation_cost = (
            depreciable_value
            / config.machine_lifetime_hours
            * total_print_hours
        )

        maintenance_cost = (
            config.maintenance_cost_per_hour_eur
            * total_print_hours
        )

        electricity_cost = (
            config.average_power_kw
            * config.electricity_cost_per_kwh_eur
            * total_print_hours
        )

        expected_failure_exposed_cost = (
            filament_cost_eur * quantity
            + depreciation_cost
            + maintenance_cost
            + electricity_cost
        )

        self.assertAlmostEqual(
            result["internal"]["failure_exposed_cost_eur"],
            expected_failure_exposed_cost,
            places=2,
        )

    def test_fixed_labour_contains_setup_and_postprocess_only(self):
        config = load_pricing_config(
            machine_id="prusa_mk3s_04"
        )

        result = calculate_pricing(
            filament_cost_eur=0.38,
            print_time_seconds=7830,
            quantity=1,
            config=config,
        )

        expected_fixed_labour_cost = (
            result["costs"]["setup_labour_eur"]
            + result["costs"]["postprocess_labour_eur"]
        )

        self.assertAlmostEqual(
            result["internal"]["fixed_labour_cost_eur"],
            expected_fixed_labour_cost,
            places=2,
        )

if __name__ == "__main__":
    unittest.main()