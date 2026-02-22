"""Orientation presets for mesh parts."""

from __future__ import annotations

import math

import trimesh
from trimesh.transformations import rotation_matrix


def orient_mesh(
    mesh: trimesh.Trimesh,
    strategy: str,
    rotate: list[float] | None = None,
) -> trimesh.Trimesh:
    """Orient a mesh according to a named strategy or custom rotation. Returns a copy.

    If rotate is provided, it should be [rx, ry, rz] in degrees.
    """
    mesh = mesh.copy()

    if rotate:
        rx, ry, rz = rotate
        if rx:
            mesh.apply_transform(rotation_matrix(math.radians(rx), [1, 0, 0]))
        if ry:
            mesh.apply_transform(rotation_matrix(math.radians(ry), [0, 1, 0]))
        if rz:
            mesh.apply_transform(rotation_matrix(math.radians(rz), [0, 0, 1]))
    elif strategy == "flat":
        _orient_flat(mesh)
    elif strategy == "upright":
        pass  # keep as-is
    elif strategy == "side":
        mesh.apply_transform(rotation_matrix(math.radians(90), [1, 0, 0]))
    else:
        raise ValueError(f"Unknown orientation strategy: '{strategy}'")

    _drop_to_z0(mesh)
    return mesh


def _orient_flat(mesh: trimesh.Trimesh) -> None:
    """Rotate mesh so its smallest extent is along Z (flat on the bed).

    Skips reorientation if Z is already the smallest extent.
    """
    extents = mesh.extents
    if extents[2] <= extents[0] and extents[2] <= extents[1]:
        return  # already flat

    try:
        _, transform = trimesh.bounds.oriented_bounds(mesh)
        mesh.apply_transform(transform)
    except Exception:
        transform = mesh.principal_inertia_transform
        mesh.apply_transform(transform)

    # oriented_bounds puts extents in sorted order (smallest first).
    # Rotate so the smallest extent ends up along Z.
    extents = mesh.extents
    if extents[2] > extents[0] or extents[2] > extents[1]:
        if extents[0] <= extents[1]:
            # X is smallest — rotate 90° around Y to move X→Z
            mesh.apply_transform(rotation_matrix(math.radians(90), [0, 1, 0]))
        else:
            # Y is smallest — rotate 90° around X to move Y→Z
            mesh.apply_transform(rotation_matrix(math.radians(90), [1, 0, 0]))


def _drop_to_z0(mesh: trimesh.Trimesh) -> None:
    """Translate mesh so its bottom sits on Z=0."""
    min_z = mesh.bounds[0][2]
    if abs(min_z) > 1e-6:
        mesh.apply_translation([0, 0, -min_z])
