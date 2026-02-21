"""Tests for plate assembly and 3MF export."""

from pathlib import Path

import trimesh

from fabprint.arrange import arrange
from fabprint.plate import build_plate, export_plate

FIXTURES = Path(__file__).parent / "fixtures"


def test_build_and_export_3mf(tmp_path):
    m1 = trimesh.load(str(FIXTURES / "cube_10mm.stl"))
    m2 = trimesh.load(str(FIXTURES / "cylinder_5x20mm.stl"))

    placements = arrange([m1, m2], ["cube", "cylinder"], plate_size=(256, 256))
    scene = build_plate(placements)

    assert len(scene.geometry) == 2

    out = tmp_path / "test_plate.3mf"
    export_plate(scene, out)
    assert out.exists()
    assert out.stat().st_size > 0

    # Reload and verify
    reloaded = trimesh.load(str(out))
    assert isinstance(reloaded, trimesh.Scene)
    assert len(reloaded.geometry) == 2
