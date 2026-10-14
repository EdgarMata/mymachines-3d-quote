import json
import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_PROJECT_ROOT = (
    Path.home()
    / "Projetos"
    / "mymachines-3d-quote"
)

PROJECT_ROOT = Path(
    os.getenv(
        "MYMACHINES_PROJECT_ROOT",
        str(DEFAULT_PROJECT_ROOT),
    )
)

PRICING_CONFIG_FILE = (
    PROJECT_ROOT
    / "config"
    / "pricing.json"
)

MACHINE_COSTS_FILE = (
    PROJECT_ROOT
    / "config"
    / "machine_costs.json"
)

@dataclass
class PricingConfig:
    machine_name: str

    machine_purchase_cost_eur: float
    machine_residual_value_eur: float
    machine_lifetime_hours: float

    maintenance_cost_per_hour_eur: float
    average_power_kw: float
    electricity_cost_per_kwh_eur: float

    labour_cost_per_hour_eur: float
    setup_minutes: float
    postprocess_minutes_per_piece: float

    failure_rate_percent: float
    target_margin_percent: float

    minimum_order_eur: float


def load_pricing_config(
    machine_id: str | None = None,
) -> PricingConfig:

    if not PRICING_CONFIG_FILE.exists():
        raise FileNotFoundError(
            f"Pricing config not found: {PRICING_CONFIG_FILE}"
        )

    if not MACHINE_COSTS_FILE.exists():
        raise FileNotFoundError(
            f"Machine costs config not found: {MACHINE_COSTS_FILE}"
        )

    # Load general company pricing settings
    data = json.loads(
        PRICING_CONFIG_FILE.read_text(
            encoding="utf-8"
        )
    )

    # Load individual machine costs
    machine_costs = json.loads(
        MACHINE_COSTS_FILE.read_text(
            encoding="utf-8"
        )
    )

    # Preserve the configured default machine
    if machine_id is None:
        from app.services.printing_config import load_printing_config

        printing_config = load_printing_config()

        machine_id = printing_config["default_machine"]

    # Validate machine
    if machine_id not in machine_costs["machines"]:
        raise ValueError(
            f"Machine costs not configured: {machine_id}"
        )

    machine = machine_costs["machines"][machine_id]

    energy = data["energy"]
    labour = data["labour"]
    risk = data["risk"]
    commercial = data["commercial"]

    return PricingConfig(
        machine_name=machine["name"],

        machine_purchase_cost_eur=(
            machine["purchase_cost_eur"]
        ),

        machine_residual_value_eur=(
            machine["residual_value_eur"]
        ),

        machine_lifetime_hours=(
            machine["lifetime_hours"]
        ),

        maintenance_cost_per_hour_eur=(
            machine["maintenance_cost_per_hour_eur"]
        ),

        average_power_kw=(
            machine["average_power_kw"]
        ),

        electricity_cost_per_kwh_eur=(
            energy["electricity_cost_per_kwh_eur"]
        ),

        labour_cost_per_hour_eur=(
            labour["cost_per_hour_eur"]
        ),

        setup_minutes=(
            machine["setup_minutes"]
        ),

        postprocess_minutes_per_piece=(
            machine["postprocess_minutes_per_piece"]
        ),

        failure_rate_percent=(
            risk["failure_rate_percent"]
        ),

        target_margin_percent=(
            commercial["target_margin_percent"]
        ),

        minimum_order_eur=(
            commercial["minimum_order_eur"]
        ),
    )

def calculate_pricing(
    *,
    filament_cost_eur: float,
    print_time_seconds: int,
    quantity: int = 1,
    config: PricingConfig | None = None,
) -> dict:

    if quantity < 1:
        raise ValueError(
            "Quantity must be at least 1."
        )

    if config is None:
        config = load_pricing_config()

    print_hours_each = (
        print_time_seconds / 3600
    )

    total_print_hours = (
        print_hours_each * quantity
    )

    # Material
    total_material_cost = (
        filament_cost_eur * quantity
    )

    # Machine depreciation
    depreciable_value = max(
        config.machine_purchase_cost_eur
        - config.machine_residual_value_eur,
        0,
    )

    depreciation_per_hour = (
        depreciable_value
        / config.machine_lifetime_hours
    )

    depreciation_cost = (
        depreciation_per_hour
        * total_print_hours
    )

    # Maintenance
    maintenance_cost = (
        config.maintenance_cost_per_hour_eur
        * total_print_hours
    )

    # Electricity
    electricity_cost_per_hour = (
        config.average_power_kw
        * config.electricity_cost_per_kwh_eur
    )

    electricity_cost = (
        electricity_cost_per_hour
        * total_print_hours
    )

    # Setup labour
    setup_hours = (
        config.setup_minutes / 60
    )

    setup_cost = (
        setup_hours
        * config.labour_cost_per_hour_eur
    )

    # Post-processing
    postprocess_hours = (
        config.postprocess_minutes_per_piece
        * quantity
        / 60
    )

    postprocess_cost = (
        postprocess_hours
        * config.labour_cost_per_hour_eur
    )

    # Costs exposed to print failure
    failure_exposed_cost = (
        total_material_cost
        + depreciation_cost
        + maintenance_cost
        + electricity_cost
    )

    # Labour not repeated automatically on print failure
    fixed_labour_cost = (
        setup_cost
        + postprocess_cost
    )

    # Direct production cost
    production_cost = (
        failure_exposed_cost
        + fixed_labour_cost
    )

    # Failure / risk
    failure_rate = (
        config.failure_rate_percent / 100
    )

    if failure_rate >= 1:
        raise ValueError(
            "Failure rate must be lower than 100%."
        )

    expected_failure_exposed_cost = (
        failure_exposed_cost
        / (1 - failure_rate)
    )

    risk_cost = (
        expected_failure_exposed_cost
        - failure_exposed_cost
    )

    expected_cost = (
        expected_failure_exposed_cost
        + fixed_labour_cost
    )

    # Margin
    margin = (
        config.target_margin_percent / 100
    )

    if margin >= 1:
        raise ValueError(
            "Target margin must be lower than 100%."
        )

    selling_price_before_minimum = (
        expected_cost
        / (1 - margin)
    )

    selling_price = max(
        selling_price_before_minimum,
        config.minimum_order_eur,
    )

    minimum_order_applied = (
        selling_price
        > selling_price_before_minimum
    )

    return {
        "machine": config.machine_name,

        "quantity": quantity,

        "production": {
            "print_hours_each": round(
                print_hours_each,
                3,
            ),
            "total_print_hours": round(
                total_print_hours,
                3,
            ),
        },

        "costs": {
            "material_eur": round(
                total_material_cost,
                2,
            ),
            "machine_depreciation_eur": round(
                depreciation_cost,
                2,
            ),
            "maintenance_eur": round(
                maintenance_cost,
                2,
            ),
            "electricity_eur": round(
                electricity_cost,
                2,
            ),
            "setup_labour_eur": round(
                setup_cost,
                2,
            ),
            "postprocess_labour_eur": round(
                postprocess_cost,
                2,
            ),
            "risk_eur": round(
                risk_cost,
                2,
            ),
        },

        "internal": {
            "failure_exposed_cost_eur": round(
                failure_exposed_cost,
                2,
            ),
            "fixed_labour_cost_eur": round(
                fixed_labour_cost,
                2,
            ),
        },

        "summary": {
            "production_cost_eur": round(
                production_cost,
                2,
            ),
            "expected_cost_eur": round(
                expected_cost,
                2,
            ),
            "selling_price_before_minimum_eur": round(
                selling_price_before_minimum,
                2,
            ),
            "minimum_order_applied": (
                minimum_order_applied
            ),
            "selling_price_eur": round(
                selling_price,
                2,
            ),
        },

        "rates": {
            "depreciation_per_hour_eur": round(
                depreciation_per_hour,
                4,
            ),
            "maintenance_per_hour_eur": (
                config.maintenance_cost_per_hour_eur
            ),
            "electricity_per_hour_eur": round(
                electricity_cost_per_hour,
                4,
            ),
            "labour_per_hour_eur": (
                config.labour_cost_per_hour_eur
            ),
            "failure_rate_percent": (
                config.failure_rate_percent
            ),
            "target_margin_percent": (
                config.target_margin_percent
            ),
        },
    }