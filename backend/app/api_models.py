"""Public response allowlists; internal service dictionaries are never contracts."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat


class PublicModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Dimensions(PublicModel):
    x: FiniteFloat
    y: FiniteFloat
    z: FiniteFloat


class RootResponse(PublicModel):
    name: str
    status: Literal["ok"]
    version: str


class HealthResponse(PublicModel):
    status: Literal["healthy"]


class Defaults(PublicModel):
    machine: str
    material: str
    quality: str


class MaterialOption(PublicModel):
    label: str
    qualities: list[str]


class QualityOption(PublicModel):
    label: str
    layer_height_mm: FiniteFloat


class MachineOption(PublicModel):
    label: str
    nozzle_diameter_mm: FiniteFloat
    build_volume_mm: Dimensions
    quoting_enabled: bool


class OptionsResponse(PublicModel):
    defaults: Defaults
    materials: dict[str, MaterialOption]
    qualities: dict[str, QualityOption]
    machines: dict[str, MachineOption]


class Geometry(PublicModel):
    dimensions_mm: Dimensions
    volume_mm3: FiniteFloat
    surface_area_mm2: FiniteFloat
    vertices: int
    faces: int
    watertight: bool


class UploadResponse(PublicModel):
    filename: str
    size_bytes: int
    geometry: Geometry
    status: Literal["analysed"]


class BedPosition(PublicModel):
    minimum_z_mm: FiniteFloat


class Orientation(PublicModel):
    status: Literal["oriented"]
    rotation_degrees: Dimensions
    dimensions_mm: Dimensions
    bed_position: BedPosition


MachineStatus = Literal["ready", "technical_review_required", "model_exceeds_build_volume"]


class Machine(PublicModel):
    id: str
    label: str
    build_volume_mm: Dimensions
    quoting_enabled: bool


class MachineSelection(PublicModel):
    status: MachineStatus
    model_dimensions_mm: Dimensions
    default_machine: str
    selected_machine: Machine | None
    compatible_machines: list[Machine]
    compatible_count: int


class AnalyzeResponse(PublicModel):
    filename: str
    status: MachineStatus
    orientation: Orientation
    machine_selection: MachineSelection
    display_machine: Machine


class Slicing(PublicModel):
    # Optional statistics preserve service behavior when Orca omits a field.
    status: Literal["success"] | None = None
    slicer: Literal["OrcaSlicer"] | None = None
    machine_id: str | None = None
    material: str | None = None
    quality: str | None = None
    filament_mm: FiniteFloat | None = None
    filament_cm3: FiniteFloat | None = None
    filament_g: FiniteFloat
    layers: int | None = None
    print_time: str | None = None
    print_time_seconds: FiniteFloat


class SliceResponse(PublicModel):
    filename: str
    status: Literal["sliced"]
    slicing: Slicing


class Pricing(PublicModel):
    machine: str
    quantity: int
    selling_price_eur: FiniteFloat
    minimum_order_applied: bool


class EstimateBase(PublicModel):
    filename: str
    quantity: int
    material: str
    quality: str
    geometry: Geometry
    orientation: Orientation
    machine_selection: MachineSelection


class SuccessfulEstimate(EstimateBase):
    status: Literal["estimated"]
    slicing: Slicing
    pricing: Pricing


class ReviewEstimate(EstimateBase):
    status: Literal["technical_review_required", "model_exceeds_build_volume"]
    message: str
    slicing: None
    pricing: None


EstimateResponse = Annotated[SuccessfulEstimate | ReviewEstimate, Field(discriminator="status")]


class ErrorResponse(PublicModel):
    detail: str
