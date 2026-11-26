import math
from pathlib import Path

import numpy as np
import trimesh

from app.services.geometry_validation import load_validated_stl, validate_mesh
from app.services.input_validation import validate_rotations


def orient_stl(
    input_path: Path,
    output_path: Path,
    x: float = 0,
    y: float = 0,
    z: float = 0,
) -> dict:
    """Compatibility wrapper for internal callers that start from a path."""
    validate_rotations(x, y, z)
    mesh = load_validated_stl(Path(input_path).read_bytes())
    return orient_mesh(mesh, output_path, x, y, z)


def orient_mesh(
    mesh: trimesh.Trimesh,
    output_path: Path,
    x: float = 0,
    y: float = 0,
    z: float = 0,
) -> dict:
    """Orient a validated mesh using the viewer's Three.js XYZ convention.

    Copy the in-memory mesh instead of reparsing the STL. Preserve the original
    mesh for the public geometry summary; centre XY and place minimum Z at zero.
    """
    validate_rotations(x, y, z)
    mesh = mesh.copy()
    output_path = Path(output_path)

    # Convert degrees to radians
    rx, ry, rz = np.radians(
        [math.remainder(angle, 360.0) for angle in (x, y, z)]
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

    validate_mesh(mesh)

    # Save the oriented STL
    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    # Own the descriptor: a failed third-party filename export can otherwise
    # keep an unlinked STL consuming disk space until cyclic garbage collection.
    with output_path.open("wb") as stream:
        mesh.export(
            stream,
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
