"""Tests for bin packing arrangement."""

from pathlib import Path

import pytest
import trimesh

from fabprint.arrange import arrange

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> trimesh.Trimesh:
    return trimesh.load(str(FIXTURES / name))


def test_arrange_two_cubes():
    m1 = _load("cube_10mm.stl")
    m2 = _load("cube_10mm.stl")
    placements = arrange([m1, m2], ["a", "b"], plate_size=(256, 256), padding=5.0)
    assert len(placements) == 2

    # All parts should be within plate bounds
    for p in placements:
        assert p.mesh.bounds[0][0] >= 0  # min X >= 0
        assert p.mesh.bounds[0][1] >= 0  # min Y >= 0
        assert p.mesh.bounds[1][0] <= 256  # max X <= plate width
        assert p.mesh.bounds[1][1] <= 256  # max Y <= plate depth


def test_arrange_no_overlap():
    meshes = [_load("cube_10mm.stl") for _ in range(4)]
    names = [f"cube_{i}" for i in range(4)]
    placements = arrange(meshes, names, plate_size=(256, 256), padding=5.0)

    # Check no XY overlap between any pair of placed meshes
    boxes = []
    for p in placements:
        xmin, ymin = p.mesh.bounds[0][0], p.mesh.bounds[0][1]
        xmax, ymax = p.mesh.bounds[1][0], p.mesh.bounds[1][1]
        boxes.append((xmin, ymin, xmax, ymax))

    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            a, b = boxes[i], boxes[j]
            overlap_x = a[0] < b[2] and a[2] > b[0]
            overlap_y = a[1] < b[3] and a[3] > b[1]
            assert not (overlap_x and overlap_y), f"Parts {i} and {j} overlap"


def test_arrange_overflow():
    # Try to fit a 200mm cube on a 100mm plate — should fail
    mesh = trimesh.creation.box(extents=[200, 200, 10])
    with pytest.raises(ValueError, match="fit"):
        arrange([mesh], ["big"], plate_size=(100, 100), padding=5.0)
