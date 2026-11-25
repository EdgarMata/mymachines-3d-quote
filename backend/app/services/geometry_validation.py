"""Bound STL complexity and validate raw input before Trimesh processing."""

import io
import math
import re
import struct

import numpy as np
import trimesh

from app.services.input_validation import (
    InputValidationError,
    positive_integer_setting,
)

MAX_STL_TRIANGLES = positive_integer_setting("MAX_STL_TRIANGLES", 1_000_000)

# STL binary records: normal, three vertices, attribute byte count (50 bytes).
ASCII_NUMBER = re.compile(rb"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")

BINARY_FACET = np.dtype([
    ("normal", "<f4", (3,)), ("vertices", "<f4", (3, 3)), ("attribute", "<u2"),
])


def check_triangle_count(count: int) -> None:
    if count > MAX_STL_TRIANGLES:
        raise InputValidationError(
            f"STL exceeds the maximum of {MAX_STL_TRIANGLES} triangles.", 413
        )
    if count == 0:
        raise InputValidationError("The STL contains no usable geometry.")


def _finite_vector(tokens: list[bytes]) -> None:
    if len(tokens) != 3:
        raise InputValidationError("Invalid or corrupted STL file.")
    try:
        finite = all(math.isfinite(float(token)) for token in tokens)
    except ValueError as exc:
        raise InputValidationError("Invalid or corrupted STL file.") from exc
    if not finite:
        raise InputValidationError("STL coordinates and normals must be finite numbers.")
    if not all(ASCII_NUMBER.fullmatch(token) for token in tokens):
        raise InputValidationError("Invalid or corrupted STL file.")


def _check_ascii(contents: bytes) -> int:
    """Scan conventional line-based ASCII STL without allocating geometry arrays.

    Require complete facets and closed solid blocks, including multi-solid STL.
    Count each facet before its vertices; stop immediately above the limit.
    """
    state, count, solids = "solid", 0, 0
    stream = io.BytesIO(contents)
    while line := stream.readline(4097):
        # Bound token allocation even when an attacker omits all line breaks.
        if len(line) > 4096:
            raise InputValidationError("Invalid or corrupted STL file.")
        tokens = line.split()
        if not tokens:
            continue
        if state == "solid" and tokens[0] == b"solid":
            state, solids = "facet", solids + 1
        elif state == "facet" and tokens[0] == b"endsolid":
            state = "solid"
        elif state == "facet" and tokens[:2] == [b"facet", b"normal"]:
            count += 1
            check_triangle_count(count)
            _finite_vector(tokens[2:])
            state = "outer"
        elif state == "outer" and tokens == [b"outer", b"loop"]:
            state = "vertex1"
        elif state in ("vertex1", "vertex2", "vertex3") and tokens[0] == b"vertex":
            _finite_vector(tokens[1:])
            state = {"vertex1": "vertex2", "vertex2": "vertex3", "vertex3": "endloop"}[state]
        elif state == "endloop" and tokens == [b"endloop"]:
            state = "endfacet"
        elif state == "endfacet" and tokens == [b"endfacet"]:
            state = "facet"
        else:
            raise InputValidationError("Invalid or corrupted STL file.")
    if state != "solid" or not solids:
        raise InputValidationError("Invalid or corrupted STL file.")
    check_triangle_count(count)
    return count


def preflight_stl(contents: bytes) -> int:
    """Disambiguate by binary length, since binary headers may start with solid."""
    if len(contents) >= 84:
        count = struct.unpack_from("<I", contents, 80)[0]
        if len(contents) == 84 + count * 50:
            check_triangle_count(count)
            facets = np.frombuffer(contents, dtype=BINARY_FACET, offset=84, count=count)
            if not np.isfinite(facets["vertices"]).all() or not np.isfinite(facets["normal"]).all():
                raise InputValidationError("STL coordinates and normals must be finite numbers.")
            return count
    if contents.lstrip().startswith(b"solid"):
        return _check_ascii(contents)
    raise InputValidationError("Invalid or corrupted STL file.")


def validate_mesh(mesh: trimesh.Trimesh) -> None:
    """Allow open shells and some degenerate faces, but require usable surface."""
    if not isinstance(mesh, trimesh.Trimesh) or mesh.is_empty:
        raise InputValidationError("The STL contains no usable geometry.")
    check_triangle_count(len(mesh.faces))
    # Both transformed export and Orca's binary input must remain representable.
    if (not np.isfinite(mesh.vertices).all()
            or (np.abs(mesh.vertices) > np.finfo(np.float32).max).any()):
        raise InputValidationError("STL coordinates must be finite and representable in binary STL.")
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        finite = (np.isfinite(mesh.bounds).all() and np.isfinite(mesh.extents).all()
                  and np.isfinite(mesh.area_faces).all() and math.isfinite(mesh.area)
                  and math.isfinite(mesh.volume))
    if not finite:
        raise InputValidationError("STL dimensions, area and volume must be finite numbers.")
    if mesh.area <= 0:
        raise InputValidationError("The STL contains no usable geometry.")


def _process_mesh(mesh: trimesh.Trimesh) -> None:
    # Trimesh's normal merge quantizes positions to int64 at tol.merge precision.
    # Beyond that numeric range use exact duplicates instead of overflowing or
    # introducing a physical size restriction. Keep the normal path unchanged.
    digits = trimesh.util.decimal_to_digits(trimesh.constants.tol.merge)
    safe_integer = np.nextafter(float(np.iinfo(np.int64).max), 0.0)
    if np.max(np.abs(mesh.vertices)) * (10 ** digits) < safe_integer:
        mesh.process()
    else:
        _, indices, inverse = np.unique(
            np.asarray(mesh.vertices), axis=0, return_index=True, return_inverse=True
        )
        mesh.update_vertices(indices, inverse=inverse)


def load_validated_stl(contents: bytes) -> trimesh.Trimesh:
    count = preflight_stl(contents)
    try:
        # Validate before Trimesh can silently discard non-finite vertices.
        mesh = trimesh.load(
            io.BytesIO(contents), file_type="stl", force="mesh", process=False
        )
        if not isinstance(mesh, trimesh.Trimesh) or len(mesh.faces) != count:
            raise InputValidationError("Invalid or corrupted STL file.")
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            validate_mesh(mesh)
            _process_mesh(mesh)
            validate_mesh(mesh)
        return mesh
    except InputValidationError:
        raise
    except Exception as exc:
        raise InputValidationError("Invalid or corrupted STL file.") from exc


def geometry_summary(mesh: trimesh.Trimesh) -> dict:
    """Preserve the existing public geometry fields and rounding."""
    return {
        "dimensions_mm": {
            axis: round(float(value), 3)
            for axis, value in zip(("x", "y", "z"), mesh.extents)
        },
        "volume_mm3": round(float(abs(mesh.volume)), 3),
        "surface_area_mm2": round(float(mesh.area), 3),
        "vertices": int(len(mesh.vertices)),
        "faces": int(len(mesh.faces)),
        "watertight": bool(mesh.is_watertight),
    }
