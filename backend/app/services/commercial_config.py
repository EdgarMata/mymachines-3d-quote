"""Validated, process-local commercial snapshot; never serialize it to HTTP."""

from functools import lru_cache
import json
import math
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.api_config import load_api_settings
from app.services.printing_config import PROJECT_ROOT, load_printing_config


class CommercialConfigurationError(ValueError):
    """Operator-safe error: no paths, submitted keys, values or parser details."""


class SettingsModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)


class Energy(SettingsModel):
    electricity_cost_per_kwh_eur: float = Field(ge=0)


class Labour(SettingsModel):
    cost_per_hour_eur: float = Field(ge=0)


class Risk(SettingsModel):
    failure_rate_percent: float = Field(ge=0, lt=100)


class Commercial(SettingsModel):
    target_margin_percent: float = Field(ge=0, lt=100)
    minimum_order_eur: float = Field(ge=0)


class MachineCosts(SettingsModel):
    purchase_cost_eur: float = Field(ge=0)
    residual_value_eur: float = Field(ge=0)
    lifetime_hours: float = Field(gt=0)
    maintenance_cost_per_hour_eur: float = Field(ge=0)
    average_power_kw: float = Field(ge=0)
    setup_minutes: float = Field(ge=0)
    postprocess_minutes_per_piece: float = Field(ge=0)


class MaterialCosts(SettingsModel):
    cost_per_kg_eur: float = Field(ge=0)


class CommercialConfig(SettingsModel):
    schema_version: int = Field(ge=1, le=1)
    configuration_kind: Literal["demonstration", "private"]
    currency: Literal["EUR"]
    energy: Energy
    labour: Labour
    risk: Risk
    commercial: Commercial
    machines: dict[str, MachineCosts]
    materials: dict[str, MaterialCosts]


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CommercialConfigurationError("Commercial configuration contains duplicate fields.")
        result[key] = value
    return result


def _invalid_constant(value):
    raise CommercialConfigurationError("Commercial configuration requires finite JSON numbers.")


def read_commercial_config(path: Path, *, production: bool = False) -> CommercialConfig:
    """Validate a candidate without installing it or echoing its contents."""
    try:
        path = Path(path)
        if not path.is_absolute() or not path.is_file():
            raise CommercialConfigurationError("Commercial configuration must be a readable absolute file.")
        if production:
            resolved = path.resolve()
            # Exclude bundled configuration and application source, including symlinks.
            roots = (PROJECT_ROOT.resolve(), Path(__file__).resolve().parents[2])
            if any(resolved.is_relative_to(root) for root in roots):
                raise CommercialConfigurationError("Production commercial configuration must be outside application roots.")
        config = CommercialConfig.model_validate(json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object,
            parse_constant=_invalid_constant,
        ))
        if production and config.configuration_kind != "private":
            raise CommercialConfigurationError("Production requires configuration_kind=private; demonstration configuration is disabled.")
        catalogue = load_printing_config()
        if set(config.machines) != set(catalogue["machines"]) or set(config.materials) != set(catalogue["materials"]):
            raise CommercialConfigurationError("Commercial configuration must cover exactly the configured machines and materials.")
        if not all(isinstance(machine.get("pricing_name"), str) and machine["pricing_name"].strip()
                   for machine in catalogue["machines"].values()):
            raise CommercialConfigurationError("Technical catalogue requires a public pricing_name for every machine.")
        # Check actual unit-rate operations without inventing currency/size caps.
        for machine in config.machines.values():
            rates = (
                max(machine.purchase_cost_eur - machine.residual_value_eur, 0) / machine.lifetime_hours,
                machine.average_power_kw * config.energy.electricity_cost_per_kwh_eur,
                machine.setup_minutes / 60 * config.labour.cost_per_hour_eur,
                machine.postprocess_minutes_per_piece / 60 * config.labour.cost_per_hour_eur,
            )
            if not all(math.isfinite(rate) for rate in rates):
                raise CommercialConfigurationError("Commercial configuration produces non-finite unit costs.")
        return config
    except CommercialConfigurationError:
        raise
    except OSError:
        raise CommercialConfigurationError("Commercial configuration file could not be read.") from None
    except ValidationError:
        raise CommercialConfigurationError(
            "Commercial configuration violates the schema; check required/unknown fields, numeric types and ranges."
        ) from None
    except (ValueError, TypeError, KeyError, OverflowError):
        raise CommercialConfigurationError(
            "Invalid or unreadable commercial configuration; check required fields, numeric types and units."
        ) from None


@lru_cache(maxsize=1)
def get_commercial_config() -> CommercialConfig:
    """Load once before serving; no request-time fallback or dynamic reload."""
    production = load_api_settings().environment == "production"
    configured = os.getenv("PRICING_CONFIG_PATH")
    if configured is None:
        if production:
            raise CommercialConfigurationError("Production requires an explicit external PRICING_CONFIG_PATH.")
        path = PROJECT_ROOT / "config/pricing.demo.json"
    else:
        path = Path(configured)
    return read_commercial_config(path, production=production)


if __name__ == "__main__":
    # Use exactly the startup validation, without printing the private snapshot.
    try:
        get_commercial_config()
    except CommercialConfigurationError as error:
        raise SystemExit(str(error)) from None
    print("Commercial configuration is valid.")
