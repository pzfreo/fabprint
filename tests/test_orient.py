"""Tests for mesh orientation."""

from pathlib import Path

import pytest
import trimesh

from fabprint.orient import orient_mesh

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> trimesh.Trimesh:
    return trimesh.load(str(FIXTURES / name))


def test_upright_drops_to_z0():
    mesh = _load("cylinder_5x20mm.stl")
    mesh.apply_translation([0, 0, 50])  # lift off plate
    result = orient_mesh(mesh, "upright")
    assert abs(result.bounds[0][2]) < 1e-6  # min Z ~ 0


def test_upright_preserves_shape():
    mesh = _load("cube_10mm.stl")
    original_extents = mesh.extents.copy()
    result = orient_mesh(mesh, "upright")
    for i in range(3):
        assert abs(result.extents[i] - original_extents[i]) < 0.1


def test_side_rotates_90_around_x():
    mesh = _load("cylinder_5x20mm.stl")
    result = orient_mesh(mesh, "side")
    # Cylinder is 20mm tall, 10mm diameter. After side rotation,
    # height (Z) should be ~10mm (diameter), Y should be ~20mm
    assert abs(result.bounds[0][2]) < 1e-6  # on plate
    assert result.extents[2] < result.extents[1] or result.extents[2] < result.extents[0]


def test_flat_minimizes_z():
    mesh = _load("cylinder_5x20mm.stl")
    result = orient_mesh(mesh, "flat")
    # Flat orientation should minimize Z extent
    assert abs(result.bounds[0][2]) < 1e-6  # on plate
    # Z should be the smallest or close to smallest dimension
    assert result.extents[2] <= max(result.extents[0], result.extents[1]) + 0.5


def test_flat_cube_stays_cube():
    mesh = _load("cube_10mm.stl")
    result = orient_mesh(mesh, "flat")
    # Cube is symmetric — all extents should still be ~10mm
    for dim in result.extents:
        assert abs(dim - 10.0) < 0.5


def test_orient_returns_copy():
    mesh = _load("cube_10mm.stl")
    result = orient_mesh(mesh, "upright")
    # Original should not be modified
    assert result is not mesh


def test_unknown_strategy():
    mesh = _load("cube_10mm.stl")
    with pytest.raises(ValueError, match="Unknown"):
        orient_mesh(mesh, "diagonal")


def test_custom_rotate_90_around_x():
    mesh = _load("cylinder_5x20mm.stl")
    original_z = mesh.extents[2]
    result = orient_mesh(mesh, "upright", rotate=[90, 0, 0])
    # Rotating 90 around X should change the Z extent
    assert abs(result.bounds[0][2]) < 1e-6  # on plate
    assert abs(result.extents[2] - original_z) > 1.0  # Z changed


def test_custom_rotate_overrides_strategy():
    mesh = _load("cube_10mm.stl")
    # With rotate provided, strategy is ignored (even if invalid would normally raise)
    result = orient_mesh(mesh, "flat", rotate=[0, 0, 45])
    assert result is not mesh
    assert abs(result.bounds[0][2]) < 1e-6  # on plate


def test_custom_rotate_zero_is_noop():
    mesh = _load("cube_10mm.stl")
    original_extents = mesh.extents.copy()
    result = orient_mesh(mesh, "upright", rotate=[0, 0, 0])
    for i in range(3):
        assert abs(result.extents[i] - original_extents[i]) < 0.1
