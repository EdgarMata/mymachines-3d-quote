"""Shared, public-safe validation errors and printing parameter checks."""

import math
import os

from app.services.printing_config import load_printing_config


class InputValidationError(ValueError):
    """Only application-defined messages may cross the public API boundary."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def positive_integer_setting(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer.") from exc
    if value < 1:
        raise ValueError(f"{name} must be a positive integer.")
    return value


MAX_QUANTITY = positive_integer_setting("MAX_QUANTITY", 100)


def validate_rotations(x: float, y: float, z: float) -> None:
    if not all(math.isfinite(value) for value in (x, y, z)):
        raise InputValidationError("Rotation angles must be finite numbers.")


def validate_quantity(quantity: int) -> None:
    if not 1 <= quantity <= MAX_QUANTITY:
        raise InputValidationError(
            f"Quantity must be between 1 and {MAX_QUANTITY}."
        )


def validate_printing_options(material: str, quality: str) -> tuple[str, str]:
    material, quality = material.upper(), quality.lower()
    config = load_printing_config()
    if material not in config["materials"]:
        raise InputValidationError("Unsupported material.")
    if quality not in config["qualities"]:
        raise InputValidationError("Unsupported print quality.")
    if quality not in config["materials"][material]["qualities"]:
        raise InputValidationError("The selected quality is not available for this material.")
    return material, quality
