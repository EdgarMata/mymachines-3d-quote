import os
import tempfile
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse

from app.services.upload_validation import (
    UploadBodyLimitMiddleware,
    validate_stl_upload,
)
from app.services.processing import ProcessingAdmissionMiddleware, run_processing
from app.services.temp_storage import cleanup_lifecycle, workspace
from app.services.slicer_process import SlicerTimeoutError
from app.services.orientation import orient_mesh
from app.services.geometry_validation import load_validated_stl, geometry_summary
from app.services.input_validation import (
    InputValidationError,
    validate_printing_options,
    validate_quantity,
    validate_rotations,
)
from app.services.machine_selector import select_machine
from app.services.pricing import calculate_pricing, load_pricing_config
from app.services.slicer import slice_stl, validate_slicing_result
from app.services.printing_config import (
    load_printing_config,
)


@asynccontextmanager
async def lifespan(app):
    async with cleanup_lifecycle(PROJECT_ROOT):
        yield


app = FastAPI(
    title="MyMachines 3D Quote API",
    version="0.3.0",
    lifespan=lifespan,
)


@app.exception_handler(InputValidationError)
async def invalid_input_handler(request, exc: InputValidationError):
    return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})


@app.exception_handler(SlicerTimeoutError)
async def slicer_timeout_handler(request, exc: SlicerTimeoutError):
    return JSONResponse(
        status_code=504,
        content={"detail": "Slicing exceeded the allowed processing time. Please try again."},
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

app.add_middleware(UploadBodyLimitMiddleware)
app.add_middleware(ProcessingAdmissionMiddleware)
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
async def upload_model(request: Request, file: UploadFile = File(...)):
    filename, contents = await validate_stl_upload(file)
    return await run_processing(request, _upload_model, filename, contents, file.content_type)


def _upload_model(filename, contents, content_type):
    mesh = load_validated_stl(contents)

    return {
        "filename": filename,
        "content_type": content_type,
        "size_bytes": len(contents),
        "geometry": geometry_summary(mesh),
        "status": "analysed",
    }


@app.post("/slice")
async def slice_model(request: Request, file: UploadFile = File(...)):
    filename, contents = await validate_stl_upload(file)
    return await run_processing(request, _slice_model, filename, contents)


def _slice_model(filename, contents):
    validate_printing_options("PLA", "standard")
    load_validated_stl(contents)

    try:
        with workspace(PROJECT_ROOT, "uploads", directory_factory=tempfile.TemporaryDirectory) as tmp_dir:
            tmp_path = Path(tmp_dir)

            stl_path = tmp_path / "original.stl"
            stl_path.write_bytes(contents)

            result = slice_stl(stl_path)

    except (InputValidationError, SlicerTimeoutError):
        raise

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="Slicing failed.",
        ) from exc

    return {
        "filename": filename,
        "status": "sliced",
        "slicing": result,
    }

@app.post("/analyze")
async def analyze_model(
    request: Request,
    file: UploadFile = File(...),
    rotation_x: float = 0,
    rotation_y: float = 0,
    rotation_z: float = 0,
):
    """Analyze orientation and machine compatibility without slicing or pricing."""
    validate_rotations(rotation_x, rotation_y, rotation_z)
    filename, contents = await validate_stl_upload(file)
    return await run_processing(
        request, _analyze_model, filename, contents, rotation_x, rotation_y, rotation_z
    )


def _analyze_model(filename, contents, rotation_x, rotation_y, rotation_z):
    mesh = load_validated_stl(contents)

    try:
        with workspace(PROJECT_ROOT, "analysis", directory_factory=tempfile.TemporaryDirectory) as tmp_dir:
            tmp_path = Path(tmp_dir)

            oriented_stl = tmp_path / "oriented.stl"

            # Apply the orientation selected in the viewer.
            orientation_result = orient_mesh(
                mesh=mesh,
                output_path=oriented_stl,
                x=rotation_x,
                y=rotation_y,
                z=rotation_z,
            )

            # Identify machines using the oriented dimensions.
            machine_selection = select_machine(
                orientation_result["dimensions_mm"]
            )

    except (InputValidationError, SlicerTimeoutError):
        raise

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="Model analysis failed.",
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
    request: Request,
    file: UploadFile = File(...),
    quantity: int = 1,
    material: str = "PLA",
    quality: str = "standard",
    rotation_x: float = 0,
    rotation_y: float = 0,
    rotation_z: float = 0,
):
    validate_quantity(quantity)
    validate_rotations(rotation_x, rotation_y, rotation_z)
    filename, contents = await validate_stl_upload(file)
    return await run_processing(
        request, _estimate_model, filename, contents, quantity, material, quality,
        rotation_x, rotation_y, rotation_z,
    )


def _estimate_model(filename, contents, quantity, material, quality, rotation_x, rotation_y, rotation_z):
    material, quality = validate_printing_options(material, quality)
    mesh = load_validated_stl(contents)
    geometry = geometry_summary(mesh)

    # ---------------------------------------------
    # Slicing
    # ---------------------------------------------

    try:
        with workspace(PROJECT_ROOT, "uploads", directory_factory=tempfile.TemporaryDirectory) as tmp_dir:
            tmp_path = Path(tmp_dir)

            # Prepare oriented STL
            oriented_stl = (
                tmp_path / "oriented.stl"
            )

            orientation_result = orient_mesh(
                mesh=mesh,
                output_path=oriented_stl,
                x=rotation_x,
                y=rotation_y,
                z=rotation_z,
            )

            orientation_result.pop("output_file", None)

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
            validate_slicing_result(slicing)

    except (InputValidationError, SlicerTimeoutError):
        raise

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="Slicing failed.",
        ) from exc

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

    except (InputValidationError, SlicerTimeoutError):
        raise

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="Pricing failed.",
        ) from exc

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
