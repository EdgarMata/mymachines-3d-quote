
import re
import math
import os
import shlex
import subprocess
import tempfile
from pathlib import Path
from app.services.input_validation import InputValidationError, validate_printing_options
from app.services.printing_config import (
    get_filament_profile,
    get_process_profile,
)


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

ORCA_APP_ID = "com.orcaslicer.OrcaSlicer"

ORCA_COMMAND = shlex.split(
    os.getenv(
        "ORCA_COMMAND",
        (
            "flatpak-spawn --host flatpak run "
            f"{ORCA_APP_ID}"
        ),
    )
)

DEFAULT_PRUSA_PRINTER_PROFILE = (
    Path.home()
    / ".var/app/com.orcaslicer.OrcaSlicer/config/OrcaSlicer/system/Prusa/machine"
    / "Prusa MK3S 0.4 nozzle.json"
)

PRUSA_PRINTER_PROFILE = Path(
    os.getenv(
        "ORCA_PRUSA_PRINTER_PROFILE",
        str(DEFAULT_PRUSA_PRINTER_PROFILE),
    )
)

ORCA_PRINTER_PROFILES = {
    "prusa_mk3s_04": PRUSA_PRINTER_PROFILE,
    
    "my_cloner_04": (
        PROJECT_ROOT
        / "profiles/printers/my_cloner_04.json"
    ),
}

def validate_slicing_result(result: dict) -> None:
    """Do not turn missing/empty or non-finite slicing output into a quote."""
    def valid_number(key: str, positive: bool = True) -> bool:
        value = result.get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return False
        try:
            return math.isfinite(value) and (value > 0 if positive else value >= 0)
        except OverflowError:
            return False

    # Mass is the canonical material-consumption field returned by OrcaSlicer.
    valid = all(valid_number(key) for key in ("print_time_seconds", "filament_g"))
    valid = valid and valid_number("material_cost_eur", positive=False)
    for key in ("filament_mm", "filament_cm3", "layers"):
        if key in result:
            valid = valid and valid_number(key)
    if not valid:
        raise InputValidationError(
            "The model did not produce a usable print with valid time and material consumption.",
            status_code=422,
        )


def parse_print_time(value: str) -> int:
    """
    Convert values such as:
        2h 36m 6s
        45m 12s
        38s

    into total seconds.
    """

    if not re.fullmatch(r"\s*(?:(?:[0-9]+)h\s*)?(?:(?:[0-9]+)m\s*)?(?:(?:[0-9]+)s\s*)?", value):
        return 0

    hours = 0
    minutes = 0
    seconds = 0

    hour_match = re.search(r"(\d+)h", value)
    minute_match = re.search(r"(\d+)m", value)
    second_match = re.search(r"(\d+)s", value)

    if hour_match:
        hours = int(hour_match.group(1))

    if minute_match:
        minutes = int(minute_match.group(1))

    if second_match:
        seconds = int(second_match.group(1))

    return (
        hours * 3600
        + minutes * 60
        + seconds
    )


def extract_gcode_stats(gcode_path: Path) -> dict:
    text = gcode_path.read_text(
        encoding="utf-8",
        errors="ignore",
    )

    number = r"([^\s]+)[ \t]*\r?$"
    patterns = {
        "filament_mm": (
            r"; filament used \[mm\] = " + number
        ),
        "filament_cm3": (
            r"; filament used \[cm3\] = " + number
        ),
        "filament_g": (
            r"; total filament used \[g\] = " + number
        ),
        "material_cost_eur": (
            r"; total filament cost = " + number
        ),
        "layers": (
            r"; total layers count = " + number
        ),
        "print_time": (
            r"; estimated printing time "
            r"\(normal mode\) = (.+)"
        ),
    }

    result = {}

    for key, pattern in patterns.items():
        match = re.search(pattern, text, flags=re.MULTILINE)

        if match:
            result[key] = match.group(1)

    if "filament_mm" in result:
        result["filament_mm"] = float(
            result["filament_mm"]
        )

    if "filament_cm3" in result:
        result["filament_cm3"] = float(
            result["filament_cm3"]
        )

    if "filament_g" in result:
        result["filament_g"] = float(
            result["filament_g"]
        )

    if "material_cost_eur" in result:
        result["material_cost_eur"] = float(
            result["material_cost_eur"]
        )

    if "layers" in result:
        result["layers"] = int(
            result["layers"]
        )

    if "print_time" in result:
        result["print_time_seconds"] = (
            parse_print_time(
                result["print_time"]
            )
        )

    return result


def slice_stl(
    stl_path: Path,
    material: str = "PLA",
    quality: str = "standard",
    machine_id: str = "prusa_mk3s_04",
) -> dict:
    stl_path = Path(stl_path).resolve()
    material, quality = validate_printing_options(material, quality)

    if machine_id not in ORCA_PRINTER_PROFILES:
        raise ValueError(
            f"No OrcaSlicer profile configured for machine: {machine_id}"
        )

    printer_profile = ORCA_PRINTER_PROFILES[machine_id]

    if not printer_profile.is_file():
        raise FileNotFoundError(
            f"Printer profile not found: {printer_profile}"
        )

    filament_profile = get_filament_profile(
        material
    )

    process_profile = get_process_profile(
        quality
    )

    if not stl_path.exists():
        raise FileNotFoundError(
            f"STL file not found: {stl_path}"
        )

    temp_root = PROJECT_ROOT / "tmp" / "slicer"
    temp_root.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(
        dir=temp_root
    ) as tmp_dir:
        output_dir = Path(tmp_dir)

        command = [
            *ORCA_COMMAND,

            "--load-settings",
            (
                f"{process_profile};"
                f"{printer_profile}"
            ),

            "--load-filaments",
            str(filament_profile),

            "--slice",
            "0",

            "--outputdir",
            str(output_dir),

            str(stl_path),
        ]

        process = subprocess.run(
            command,
            capture_output=True,
            text=True,
        )

        if process.returncode != 0:
            raise RuntimeError(
                "OrcaSlicer failed.\n"
                f"Return code: {process.returncode}\n"
                f"STDOUT:\n{process.stdout}\n"
                f"STDERR:\n{process.stderr}"
            )

        gcode_files = list(
            output_dir.glob("*.gcode")
        )

        if not gcode_files:
            raise RuntimeError(
                "OrcaSlicer finished but no G-code file was generated.\n"
                f"Output directory: {output_dir}\n"
                f"STDOUT:\n{process.stdout}\n"
                f"STDERR:\n{process.stderr}"
            )

        gcode_path = gcode_files[0]

        try:
            stats = extract_gcode_stats(gcode_path)
        except (ValueError, OverflowError) as exc:
            raise InputValidationError(
                "The model did not produce valid print statistics.", status_code=422
            ) from exc
        validate_slicing_result(stats)

        return {
            "status": "success",
            "slicer": "OrcaSlicer",
            "machine_id": machine_id,
            "printer_profile": printer_profile.name,
            "process_profile": (
                process_profile.name
            ),
            "filament_profile": (
                filament_profile.name
            ),
            "material": material,
            "quality": quality,
            **stats,
        }
