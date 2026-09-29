import io
import tempfile
from pathlib import Path

import trimesh
from fastapi import FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.services.slicer import slice_stl


app = FastAPI(
    title="MyMachines 3D Quote API",
    version="0.2.0",
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


class QuoteRequest(BaseModel):
    volume_mm3: float = Field(gt=0)
    surface_area_mm2: float = Field(gt=0)

    material: str = "PLA"
    quantity: int = Field(default=1, ge=1)

    infill_percent: float = Field(default=20, ge=0, le=100)

    nozzle_diameter_mm: float = Field(default=0.4, gt=0)
    wall_count: int = Field(default=3, ge=1)

    waste_percent: float = Field(default=5, ge=0, le=100)


@app.get("/")
def root():
    return {
        "name": "MyMachines 3D Quote API",
        "status": "ok",
        "version": "0.2.0",
    }


@app.get("/health")
def health():
    return {
        "status": "healthy"
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

@app.post("/quote")
def calculate_quote(request: QuoteRequest):
    material_name = request.material.upper()

    if material_name not in MATERIALS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported material: {request.material}",
        )

    material = MATERIALS[material_name]

    density = material["density_g_cm3"]
    cost_per_kg = material["cost_per_kg"]

    wall_thickness_mm = (
        request.nozzle_diameter_mm * request.wall_count
    )

    shell_volume_mm3 = (
        request.surface_area_mm2 * wall_thickness_mm
    )

    shell_volume_mm3 = min(
        shell_volume_mm3,
        request.volume_mm3,
    )

    internal_volume_mm3 = max(
        request.volume_mm3 - shell_volume_mm3,
        0,
    )

    infill_ratio = request.infill_percent / 100

    infill_volume_mm3 = (
        internal_volume_mm3 * infill_ratio
    )

    estimated_material_volume_mm3 = (
        shell_volume_mm3 + infill_volume_mm3
    )

    material_volume_cm3 = (
        estimated_material_volume_mm3 / 1000
    )

    material_weight_g = (
        material_volume_cm3 * density
    )

    waste_multiplier = (
        1 + request.waste_percent / 100
    )

    material_weight_with_waste_g = (
        material_weight_g * waste_multiplier
    )

    total_weight_g = (
        material_weight_with_waste_g
        * request.quantity
    )

    material_cost = (
        total_weight_g / 1000
    ) * cost_per_kg

    return {
        "material": material_name,
        "quantity": request.quantity,
        "print_parameters": {
            "infill_percent": request.infill_percent,
            "nozzle_diameter_mm": request.nozzle_diameter_mm,
            "wall_count": request.wall_count,
            "wall_thickness_mm": round(
                wall_thickness_mm,
                3,
            ),
            "waste_percent": request.waste_percent,
        },
        "estimate": {
            "shell_volume_mm3": round(
                shell_volume_mm3,
                3,
            ),
            "infill_volume_mm3": round(
                infill_volume_mm3,
                3,
            ),
            "material_volume_mm3": round(
                estimated_material_volume_mm3,
                3,
            ),
            "material_weight_g_each": round(
                material_weight_with_waste_g,
                2,
            ),
            "total_weight_g": round(
                total_weight_g,
                2,
            ),
            "material_cost_eur": round(
                material_cost,
                2,
            ),
        },
        "status": "estimated",
    }