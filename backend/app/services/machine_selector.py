import math

from app.services.printing_config import load_printing_config


def select_machine(
    dimensions_mm: dict,
) -> dict:
    """
    Find machines compatible with the oriented model dimensions.

    Only machines with quoting_enabled=True can be selected
    for automatic quoting.

    The default machine is preferred when compatible.
    """

    config = load_printing_config()

    default_machine = config["default_machine"]

    # Validate model dimensions
    required_axes = ("x", "y", "z")

    dimensions = {}

    for axis in required_axes:
        try:
            value = float(dimensions_mm[axis])
        except (KeyError, TypeError, ValueError):
            raise ValueError(
                f"Invalid model dimension: {axis}"
            )

        if not math.isfinite(value) or value <= 0:
            raise ValueError(
                f"Model dimension {axis} must be a finite "
                "number greater than zero."
            )

        dimensions[axis] = value

    compatible_machines = []

    for machine_id, machine_data in config["machines"].items():
        volume = machine_data["build_volume_mm"]

        fits = all(
            dimensions[axis] <= float(volume[axis])
            for axis in required_axes
        )

        if not fits:
            continue

        quoting_enabled = machine_data["quoting_enabled"]
        profile_ready = machine_data["profile_ready"]
        operational = machine_data["operational"]

        automatic_quoting_ready = (
            quoting_enabled
            and profile_ready
            and operational
        )

        compatible_machines.append(
            {
                "id": machine_id,
                "label": machine_data["label"],
                "build_volume_mm": volume,
                "quoting_enabled": quoting_enabled,
                "profile_ready": profile_ready,
                "operational": operational,
                "automatic_quoting_ready": automatic_quoting_ready,
            }
        )

    # Machines ready for automatic quotations
    quoting_candidates = [
        machine
        for machine in compatible_machines
        if machine["automatic_quoting_ready"]
    ]

    selected_machine = None

    # Prefer the default machine when possible
    for machine in quoting_candidates:
        if machine["id"] == default_machine:
            selected_machine = machine
            break

    # Otherwise use the first configured eligible machine
    if selected_machine is None and quoting_candidates:
        selected_machine = quoting_candidates[0]

    if selected_machine:
        status = "ready"

    elif compatible_machines:
        status = "technical_review_required"

    else:
        status = "model_exceeds_build_volume"

    return {
        "status": status,
        "model_dimensions_mm": dimensions,
        "default_machine": default_machine,
        "selected_machine": selected_machine,
        "compatible_machines": compatible_machines,
        "compatible_count": len(compatible_machines),
        "quoting_candidates_count": len(quoting_candidates),
    }