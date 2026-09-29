import io
import tempfile
from pathlib import Path

import trimesh
from fastapi import FastAPI, File, HTTPException, UploadFile

from app.services.pricing import calculate_pricing
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

    return {
        "defaults": {
            "machine": config["default_machine"],
            "material": config["default_material"],
            "quality": config["default_quality"],
        },
        "materials": config["materials"],
        "qualities": config["qualities"],
        "machines": config["machines"],
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


@app.post("/estimate")
async def estimate_model(
    file: UploadFile = File(...),
    quantity: int = 1,
    material: str = "PLA",
    quality: str = "standard",
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

            stl_path = (
                tmp_path
                / filename
            )

            stl_path.write_bytes(
                contents
            )

            slicing = slice_stl(
                stl_path,
                material=material,
                quality=quality,
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
        pricing = calculate_pricing(
            filament_cost_eur=(
                slicing["material_cost_eur"]
            ),
            print_time_seconds=(
                slicing["print_time_seconds"]
            ),
            quantity=quantity,
        )

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
        "slicing": slicing,
        "pricing": pricing,
    }