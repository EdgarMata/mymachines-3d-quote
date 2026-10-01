import io
import tempfile
from pathlib import Path

import trimesh
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from app.services.orientation import orient_stl
from app.services.machine_selector import select_machine
from app.services.pricing import calculate_pricing, load_pricing_config
from app.services.slicer import slice_stl
from app.services.printing_config import (
    get_available_materials,
    get_available_qualities,
    load_printing_config,
)

app = FastAPI(
    title="MyMachines 3D Quote API",
    version="0.3.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

MATERIALS = {
    "PLA": {
        "density_g_cm3": 1.24,
        "cost_per_kg": 16.00,
    },
    "PETG": {
        "density_g_cm3": 1.27,
        "cost_per_kg": 22.00,
    },
    "ASA": {
        "density_g_cm3": 1.07,
        "cost_per_kg": 25.00,
    },
}


@app.get("/")
def root():
    return {
        "name": "MyMachines 3D Quote API",
        "status": "ok",
        "version": "0.3.0",
    }


@app.get("/health")
def health():
    return {
        "status": "healthy"
    }

@app.get("/options")
def get_options():
    config = load_printing_config()

    materials = {}

    for material_name, material_data in config[
        "materials"
    ].items():
        materials[material_name] = {
            "label": material_data["label"],
            "qualities": material_data[
                "qualities"
            ],
        }

    qualities = {}

    for quality_name, quality_data in config[
        "qualities"
    ].items():
        qualities[quality_name] = {
            "label": quality_data["label"],
            "layer_height_mm": quality_data[
                "layer_height_mm"
            ],
        }

    machines = {}

    for machine_name, machine_data in config["machines"].items():
        machines[machine_name] = {
            "label": machine_data["label"],
            "nozzle_diameter_mm": machine_data["nozzle_diameter_mm"],
            "build_volume_mm": {
                "x": machine_data["build_volume_mm"]["x"],
                "y": machine_data["build_volume_mm"]["y"],
                "z": machine_data["build_volume_mm"]["z"],
            },
            "quoting_enabled": machine_data["quoting_enabled"],
        }

    return {
        "defaults": {
            "machine": config[
                "default_machine"
            ],
            "material": config[
                "default_material"
            ],
            "quality": config[
                "default_quality"
            ],
        },
        "materials": materials,
        "qualities": qualities,
        "machines": machines,
    }

@app.post("/upload")
async def upload_model(file: UploadFile = File(...)):
    filename = file.filename or ""

    if not filename.lower().endswith(".stl"):
        raise HTTPException(
            status_code=400,
            detail="Only STL files are supported at this stage.",
        )

    contents = await file.read()

    if not contents:
        raise HTTPException(
            status_code=400,
            detail="The uploaded file is empty.",
        )

    try:
        mesh = trimesh.load(
            io.BytesIO(contents),
            file_type="stl",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Unable to read STL file: {exc}",
        )

    if isinstance(mesh, trimesh.Scene):
        if not mesh.geometry:
            raise HTTPException(
                status_code=400,
                detail="The STL contains no geometry.",
            )

        mesh = trimesh.util.concatenate(
            tuple(mesh.geometry.values())
        )

    dimensions = mesh.extents

    return {
        "filename": filename,
        "content_type": file.content_type,
        "size_bytes": len(contents),
        "geometry": {
            "dimensions_mm": {
                "x": round(float(dimensions[0]), 3),
                "y": round(float(dimensions[1]), 3),
                "z": round(float(dimensions[2]), 3),
            },
            "volume_mm3": round(float(abs(mesh.volume)), 3),
            "surface_area_mm2": round(float(mesh.area), 3),
            "vertices": int(len(mesh.vertices)),
            "faces": int(len(mesh.faces)),
            "watertight": bool(mesh.is_watertight),
        },
        "status": "analysed",
    }


@app.post("/slice")
async def slice_model(file: UploadFile = File(...)):
    filename = file.filename or ""

    if not filename.lower().endswith(".stl"):
        raise HTTPException(
            status_code=400,
            detail="Only STL files are supported at this stage.",
        )

    contents = await file.read()

    if not contents:
        raise HTTPException(
            status_code=400,
            detail="The uploaded file is empty.",
        )

    try:
        project_root = (
            Path.home()
            / "Projetos"
            / "mymachines-3d-quote"
        )

        temp_root = (
            project_root
            / "tmp"
            / "uploads"
        )

        temp_root.mkdir(
            parents=True,
            exist_ok=True,
        )

        with tempfile.TemporaryDirectory(
            dir=temp_root
        ) as tmp_dir:
            tmp_path = Path(tmp_dir)

            stl_path = tmp_path / filename
            stl_path.write_bytes(contents)

            result = slice_stl(stl_path)

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Slicing failed: {exc}",
        )

    return {
        "filename": filename,
        "status": "sliced",
        "slicing": result,
    }

@app.post("/analyze")
async def analyze_model(
    file: UploadFile = File(...),
    rotation_x: float = 0,
    rotation_y: float = 0,
    rotation_z: float = 0,
):
    """
    Analyze the oriented model and identify compatible machines.

    This endpoint does not perform slicing or calculate pricing.
    """

    filename = file.filename or ""

    if not filename.lower().endswith(".stl"):
        raise HTTPException(
            status_code=400,
            detail="Only STL files are supported.",
        )

    contents = await file.read()

    if not contents:
        raise HTTPException(
            status_code=400,
            detail="The uploaded file is empty.",
        )

    project_root = (
        Path.home()
        / "Projetos"
        / "mymachines-3d-quote"
    )

    temp_root = project_root / "tmp" / "analysis"

    temp_root.mkdir(
        parents=True,
        exist_ok=True,
    )

    try:
        with tempfile.TemporaryDirectory(dir=temp_root) as tmp_dir:
            tmp_path = Path(tmp_dir)

            original_stl = tmp_path / "original.stl"
            oriented_stl = tmp_path / "oriented.stl"

            original_stl.write_bytes(contents)

            # Apply the orientation selected in the viewer.
            orientation_result = orient_stl(
                input_path=original_stl,
                output_path=oriented_stl,
                x=rotation_x,
                y=rotation_y,
                z=rotation_z,
            )

            # Identify machines using the oriented dimensions.
            machine_selection = select_machine(
                orientation_result["dimensions_mm"]
            )

    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Model analysis failed: {exc}",
        ) from exc

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Model analysis failed: {exc}",
        ) from exc

    # The temporary STL has already been deleted.
    # Do not expose its former filesystem path.
    orientation_result.pop("output_file", None)

    # Determine which machine should be shown in the viewer.
    selected_machine = machine_selection["selected_machine"]

    if selected_machine is None:
        compatible = machine_selection["compatible_machines"]

        if compatible:
            # Use the first physically compatible machine
            # when automatic quoting is not yet available.
            selected_machine = compatible[0]

        else:
            # No machine fits: display the largest configured
            # build volume as a visual reference.
            config = load_printing_config()

            largest_id, largest_data = max(
                config["machines"].items(),
                key=lambda item: (
                    item[1]["build_volume_mm"]["x"]
                    * item[1]["build_volume_mm"]["y"]
                    * item[1]["build_volume_mm"]["z"]
                ),
            )

            selected_machine = {
                "id": largest_id,
                "label": largest_data["label"],
                "build_volume_mm": largest_data["build_volume_mm"],
                "quoting_enabled": largest_data["quoting_enabled"],
            }

    return {
        "filename": filename,
        "status": machine_selection["status"],
        "orientation": orientation_result,
        "machine_selection": machine_selection,
        "display_machine": selected_machine,
    }

@app.post("/estimate")
async def estimate_model(
    file: UploadFile = File(...),
    quantity: int = 1,
    material: str = "PLA",
    quality: str = "standard",
    rotation_x: float = 0,
    rotation_y: float = 0,
    rotation_z: float = 0,
):
    filename = file.filename or ""

    if not filename.lower().endswith(".stl"):
        raise HTTPException(
            status_code=400,
            detail="Only STL files are supported at this stage.",
        )

    material = material.upper()
    quality = quality.lower()

    available_materials = get_available_materials()

    if material not in available_materials:
        raise HTTPException(
            status_code=400,
            detail=(
                "Unsupported material. "
                f"Available materials: "
                f"{', '.join(available_materials)}."
            ),
        )

    available_qualities = get_available_qualities()

    if quality not in available_qualities:
        raise HTTPException(
            status_code=400,
            detail=(
                "Unsupported quality. "
                f"Available qualities: "
                f"{', '.join(available_qualities)}."
            ),
        )

    contents = await file.read()

    if not contents:
        raise HTTPException(
            status_code=400,
            detail="The uploaded file is empty.",
        )

    # ---------------------------------------------
    # Geometry analysis
    # ---------------------------------------------

    try:
        mesh = trimesh.load(
            io.BytesIO(contents),
            file_type="stl",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Unable to read STL file: {exc}",
        )

    if isinstance(mesh, trimesh.Scene):
        if not mesh.geometry:
            raise HTTPException(
                status_code=400,
                detail="The STL contains no geometry.",
            )

        mesh = trimesh.util.concatenate(
            tuple(mesh.geometry.values())
        )

    dimensions = mesh.extents

    geometry = {
        "dimensions_mm": {
            "x": round(
                float(dimensions[0]),
                3,
            ),
            "y": round(
                float(dimensions[1]),
                3,
            ),
            "z": round(
                float(dimensions[2]),
                3,
            ),
        },
        "volume_mm3": round(
            float(abs(mesh.volume)),
            3,
        ),
        "surface_area_mm2": round(
            float(mesh.area),
            3,
        ),
        "vertices": int(
            len(mesh.vertices)
        ),
        "faces": int(
            len(mesh.faces)
        ),
        "watertight": bool(
            mesh.is_watertight
        ),
    }

    # ---------------------------------------------
    # Slicing
    # ---------------------------------------------

    try:
        project_root = (
            Path.home()
            / "Projetos"
            / "mymachines-3d-quote"
        )

        temp_root = (
            project_root
            / "tmp"
            / "uploads"
        )

        temp_root.mkdir(
            parents=True,
            exist_ok=True,
        )

        with tempfile.TemporaryDirectory(
            dir=temp_root
        ) as tmp_dir:
            tmp_path = Path(tmp_dir)

            # Save original STL
            original_stl = (
                tmp_path / "original.stl"
            )

            original_stl.write_bytes(
                contents
            )

            # Prepare oriented STL
            oriented_stl = (
                tmp_path / "oriented.stl"
            )

            orientation_result = orient_stl(
                input_path=original_stl,
                output_path=oriented_stl,
                x=rotation_x,
                y=rotation_y,
                z=rotation_z,
            )

            # ---------------------------------------------
            # Machine selection
            # ---------------------------------------------

            machine_selection = select_machine(
                orientation_result["dimensions_mm"]
            )

            # No machine is currently eligible for
            # an automatic quotation.
            if machine_selection["status"] != "ready":
                if machine_selection["status"] == "technical_review_required":
                    message = (
                        "This model fits one or more machines, "
                        "but requires a technical review before "
                        "an automatic quote can be provided."
                    )
                else:
                    message = (
                        "The model exceeds the build volume "
                        "of all configured machines in its "
                        "current orientation."
                    )

                return {
                    "filename": filename,
                    "quantity": quantity,
                    "material": material,
                    "quality": quality,
                    "status": machine_selection["status"],
                    "message": message,
                    "geometry": geometry,
                    "orientation": orientation_result,
                    "machine_selection": machine_selection,
                    "slicing": None,
                    "pricing": None,
                }

            # ---------------------------------------------
            # Slicing
            # ---------------------------------------------

            selected_machine = machine_selection["selected_machine"]
            selected_machine_id = selected_machine["id"]

            slicing = slice_stl(
                oriented_stl,
                material=material,
                quality=quality,
                machine_id=selected_machine_id,
            )

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Slicing failed: {exc}",
        )

    # ---------------------------------------------
    # Pricing
    # ---------------------------------------------

    try:
        # Get the machine selected by the compatibility engine.
        selected_machine = machine_selection["selected_machine"]

        if selected_machine is None:
            raise ValueError(
                "No machine selected for automatic quotation."
            )

        machine_id = selected_machine["id"]

        # Load the individual costs of the selected machine.
        machine_pricing_config = load_pricing_config(
            machine_id=machine_id
        )

        # Calculate the quotation using those costs.
        pricing = calculate_pricing(
            filament_cost_eur=slicing["material_cost_eur"],
            print_time_seconds=slicing["print_time_seconds"],
            quantity=quantity,
            config=machine_pricing_config,
        )

        public_pricing = {
            "machine": pricing["machine"],
            "quantity": pricing["quantity"],
            "selling_price_eur": (
                pricing["summary"]["selling_price_eur"]
            ),
            "minimum_order_applied": (
                pricing["summary"]["minimum_order_applied"]
            ),
        }

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Pricing failed: {exc}",
        )

    # ---------------------------------------------
    # Final result
    # ---------------------------------------------

    return {
        "filename": filename,
        "quantity": quantity,
        "material": material,
        "quality": quality,
        "status": "estimated",

        "geometry": geometry,

        "orientation": orientation_result,

        "machine_selection": machine_selection,

        "slicing": slicing,

        "pricing": public_pricing,
}
