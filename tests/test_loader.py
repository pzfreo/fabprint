"""Tests for mesh loading."""

from pathlib import Path

import pytest
import trimesh

from fabprint.loader import load_mesh

FIXTURES = Path(__file__).parent / "fixtures"


def test_load_stl_cube():
    mesh = load_mesh(FIXTURES / "cube_10mm.stl")
    assert isinstance(mesh, trimesh.Trimesh)
    assert mesh.vertices.shape[0] > 0
    # Bounding box should be ~10mm in each dimension
    extents = mesh.extents
    for dim in extents:
        assert abs(dim - 10.0) < 0.1


def test_load_stl_cylinder():
    mesh = load_mesh(FIXTURES / "cylinder_5x20mm.stl")
    assert isinstance(mesh, trimesh.Trimesh)
    # Height ~20mm (Z), diameter ~10mm (X and Y)
    extents = mesh.extents
    assert abs(extents[2] - 20.0) < 0.5  # height
    assert abs(extents[0] - 10.0) < 0.5  # diameter X
    assert abs(extents[1] - 10.0) < 0.5  # diameter Y


def test_load_nonexistent():
    with pytest.raises(FileNotFoundError):
        load_mesh(Path("/nonexistent/cube.stl"))


def test_load_unsupported():
    with pytest.raises(ValueError, match="Unsupported"):
        load_mesh(Path("model.obj"))
