"""Tests for plate assembly and 3MF export."""

import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import trimesh

from fabprint.arrange import arrange
from fabprint.plate import _encode_paint_color, build_plate, export_plate

NS_3MF = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"

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


# --- paint_color encoding ---


def test_encode_paint_color():
    assert _encode_paint_color(0) == "4"   # filament 1
    assert _encode_paint_color(1) == "8"   # filament 2
    assert _encode_paint_color(2) == "C"   # filament 3
    assert _encode_paint_color(3) == "10"  # filament 4


# --- multi-filament paint injection ---


def _read_3mf_xml(path):
    """Read and parse the model XML from a 3MF file."""
    with zipfile.ZipFile(path, "r") as zf:
        return ET.fromstring(zf.read("3D/3dmodel.model"))


def test_export_filament_id_no_paint(tmp_path):
    """Config-assigned filament_id does NOT inject paint_color (OrcaSlicer bug)."""
    m1 = trimesh.creation.box(extents=[10, 10, 10])
    m2 = trimesh.creation.box(extents=[10, 10, 10])
    m1.metadata["filament_id"] = 1
    m2.metadata["filament_id"] = 3

    placements = arrange([m1, m2], ["part_a", "part_b"], plate_size=(256, 256))
    scene = build_plate(placements)

    out = tmp_path / "plate.3mf"
    export_plate(scene, out)

    root = _read_3mf_xml(out)
    tris = root.findall(f".//{{{NS_3MF}}}triangle")
    # No paint_color should be injected for config-assigned filaments
    assert all(t.get("paint_color") is None for t in tris)
    # No BambuStudio metadata
    meta = root.findall(f".//{{{NS_3MF}}}metadata[@name='BambuStudio:MmPaintingVersion']")
    assert len(meta) == 0


def test_export_preserves_paint_colors(tmp_path):
    """Pre-painted meshes (paint_colors in metadata) are preserved in export."""
    mesh = trimesh.creation.box(extents=[10, 10, 10])
    num_faces = len(mesh.faces)
    # Alternate paint_color per face
    paint_colors = ["4" if i % 2 == 0 else "8" for i in range(num_faces)]
    mesh.metadata["paint_colors"] = paint_colors
    mesh.metadata["filament_id"] = 1

    placements = arrange([mesh], ["painted"], plate_size=(256, 256))
    scene = build_plate(placements)

    out = tmp_path / "plate.3mf"
    export_plate(scene, out)

    root = _read_3mf_xml(out)
    objects = root.findall(f".//{{{NS_3MF}}}object")
    tris = objects[0].findall(f".//{{{NS_3MF}}}triangle")

    # All triangles should have paint_color matching our input
    assert len(tris) == num_faces
    for i, tri in enumerate(tris):
        expected = "4" if i % 2 == 0 else "8"
        assert tri.get("paint_color") == expected


def test_export_no_paint_skips_postprocess(tmp_path):
    """When all parts use filament 1 with no paint, no post-processing occurs."""
    mesh = trimesh.creation.box(extents=[10, 10, 10])
    mesh.metadata["filament_id"] = 1

    placements = arrange([mesh], ["plain"], plate_size=(256, 256))
    scene = build_plate(placements)

    out = tmp_path / "plate.3mf"
    export_plate(scene, out)

    root = _read_3mf_xml(out)
    tris = root.findall(f".//{{{NS_3MF}}}triangle")
    # No paint_color attributes should be present
    assert all(t.get("paint_color") is None for t in tris)
    # No BambuStudio metadata
    meta = root.findall(f".//{{{NS_3MF}}}metadata[@name='BambuStudio:MmPaintingVersion']")
    assert len(meta) == 0
