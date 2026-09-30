import math
from pathlib import Path

import numpy as np
import trimesh


def orient_stl(
    input_path: Path,
    output_path: Path,
    x: float = 0,
    y: float = 0,
    z: float = 0,
) -> dict:
    """
    Rotate an STL model using Three.js XYZ Euler
    rotation convention.

    After rotation:
    - Centre the model in X and Y.
    - Place its lowest point at Z = 0.
    - Export the transformed STL.
    """

    input_path = Path(input_path)
    output_path = Path(output_path)

    # Validate rotation values
    angles = (x, y, z)

    if not all(math.isfinite(value) for value in angles):
        raise ValueError(
            "Rotation angles must be finite numbers."
        )

    if not input_path.is_file():
        raise FileNotFoundError(
            f"STL file not found: {input_path}"
        )

    # Load original geometry
    mesh = trimesh.load(
        input_path,
        file_type="stl",
        force="mesh",
    )

    if mesh.is_empty:
        raise ValueError(
            "The STL contains no geometry."
        )

    # Convert degrees to radians
    rx, ry, rz = np.radians(
        [x, y, z]
    )

    # Rotation around X
    rotation_x = trimesh.transformations.rotation_matrix(
        rx,
        [1, 0, 0],
    )

    # Rotation around Y
    rotation_y = trimesh.transformations.rotation_matrix(
        ry,
        [0, 1, 0],
    )

    # Rotation around Z
    rotation_z = trimesh.transformations.rotation_matrix(
        rz,
        [0, 0, 1],
    )

    # Match Three.js Euler order XYZ.
    #
    # Matrix multiplication is intentionally ordered
    # this way to match the frontend.
    rotation_matrix = (
        rotation_x
        @ rotation_y
        @ rotation_z
    )

    mesh.apply_transform(
        rotation_matrix
    )

    # Calculate rotated bounding box
    bounds = mesh.bounds

    min_x, min_y, min_z = bounds[0]
    max_x, max_y, max_z = bounds[1]

    # Centre the model in X and Y
    center_x = (
        min_x + max_x
    ) / 2

    center_y = (
        min_y + max_y
    ) / 2

    # Place the lowest point on the bed (Z = 0)
    mesh.apply_translation([
        -center_x,
        -center_y,
        -min_z,
    ])

    # Final dimensions
    dimensions = mesh.extents

    # Save the oriented STL
    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    mesh.export(
        output_path,
        file_type="stl",
    )

    return {
        "status": "oriented",

        "rotation_degrees": {
            "x": x,
            "y": y,
            "z": z,
        },

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

        "bed_position": {
            "minimum_z_mm": round(
                float(mesh.bounds[0][2]),
                6,
            ),
        },

        "output_file": str(output_path),
    }