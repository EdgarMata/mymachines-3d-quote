
import re
import subprocess
import tempfile
from pathlib import Path
from app.services.printing_config import (
    get_filament_profile,
    get_process_profile,
)


PROJECT_ROOT = Path.home() / "Projetos" / "mymachines-3d-quote"

ORCA_APP_ID = "com.orcaslicer.OrcaSlicer"

ORCA_PROCESS_PROFILE = (
    Path.home()
    / ".var/app/com.orcaslicer.OrcaSlicer/config/OrcaSlicer/system/Prusa/process"
    / "0.20mm Standard @MK3S 0.4.json"
)

ORCA_PRINTER_PROFILES = {
    "prusa_mk3s_04": (
        Path.home()
        / ".var/app/com.orcaslicer.OrcaSlicer/config/OrcaSlicer/system/Prusa/machine"
        / "Prusa MK3S 0.4 nozzle.json"
    ),
    "my_cloner_04": (
        PROJECT_ROOT
        / "profiles/printers/my_cloner_04.json"
    ),
}

def parse_print_time(value: str) -> int:
    """
    Convert values such as:
        2h 36m 6s
        45m 12s
        38s

    into total seconds.
    """

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

    patterns = {
        "filament_mm": (
            r"; filament used \[mm\] = ([0-9.]+)"
        ),
        "filament_cm3": (
            r"; filament used \[cm3\] = ([0-9.]+)"
        ),
        "filament_g": (
            r"; total filament used \[g\] = ([0-9.]+)"
        ),
        "material_cost_eur": (
            r"; total filament cost = ([0-9.]+)"
        ),
        "layers": (
            r"; total layers count = (\d+)"
        ),
        "print_time": (
            r"; estimated printing time "
            r"\(normal mode\) = (.+)"
        ),
    }

    result = {}

    for key, pattern in patterns.items():
        match = re.search(pattern, text)

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
    material = material.upper()
    quality = quality.lower()

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
            "flatpak",
            "run",
            ORCA_APP_ID,

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

        stats = extract_gcode_stats(
            gcode_path
        )

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
