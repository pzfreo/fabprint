"""Orientation presets for mesh parts."""

from __future__ import annotations

import math

import trimesh
from trimesh.transformations import rotation_matrix


def orient_mesh(mesh: trimesh.Trimesh, strategy: str) -> trimesh.Trimesh:
    """Orient a mesh according to a named strategy. Returns a copy."""
    mesh = mesh.copy()

    if strategy == "flat":
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
    """Rotate mesh so its largest flat face is on the XY plane."""
    try:
        _, transform = trimesh.bounds.oriented_bounds(mesh)
        mesh.apply_transform(transform)
    except Exception:
        # Fallback: use principal inertia axes
        transform = mesh.principal_inertia_transform
        mesh.apply_transform(transform)

    # oriented_bounds may flip the mesh — ensure Z extent is minimized
    extents = mesh.extents
    if extents[2] > extents[0] or extents[2] > extents[1]:
        # Rotate 90° around X to lay the tallest axis flat
        mesh.apply_transform(rotation_matrix(math.radians(90), [1, 0, 0]))


def _drop_to_z0(mesh: trimesh.Trimesh) -> None:
    """Translate mesh so its bottom sits on Z=0."""
    min_z = mesh.bounds[0][2]
    if abs(min_z) > 1e-6:
        mesh.apply_translation([0, 0, -min_z])
