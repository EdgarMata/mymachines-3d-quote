import json
from pathlib import Path


PROJECT_ROOT = (
    Path.home()
    / "Projetos"
    / "mymachines-3d-quote"
)

PRINTING_CONFIG_FILE = (
    PROJECT_ROOT
    / "config"
    / "printing.json"
)


def load_printing_config() -> dict:
    if not PRINTING_CONFIG_FILE.exists():
        raise FileNotFoundError(
            f"Printing config not found: "
            f"{PRINTING_CONFIG_FILE}"
        )

    return json.loads(
        PRINTING_CONFIG_FILE.read_text(
            encoding="utf-8"
        )
    )


def get_available_materials() -> list[str]:
    config = load_printing_config()

    return list(
        config["materials"].keys()
    )


def get_available_qualities() -> list[str]:
    config = load_printing_config()

    return list(
        config["qualities"].keys()
    )


def get_filament_profile(
    material: str,
) -> Path:
    config = load_printing_config()

    material = material.upper()

    if material not in config["materials"]:
        raise ValueError(
            f"Unsupported material: {material}"
        )

    filename = (
        config["materials"][material][
            "filament_profile"
        ]
    )

    return (
        PROJECT_ROOT
        / "profiles"
        / "filaments"
        / filename
    )


def get_process_profile(
    quality: str,
) -> Path:
    config = load_printing_config()

    quality = quality.lower()

    if quality not in config["qualities"]:
        raise ValueError(
            f"Unsupported quality: {quality}"
        )

    filename = (
        config["qualities"][quality][
            "process_profile"
        ]
    )

    return (
        PROJECT_ROOT
        / "profiles"
        / "processes"
        / filename
    )