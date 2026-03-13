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
    assert _encode_paint_color(0) == "4"  # filament 1
    assert _encode_paint_color(1) == "8"  # filament 2
    assert _encode_paint_color(2) == "C"  # filament 3
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


def test_export_injects_extruder_metadata(tmp_path):
    """Per-object extruder metadata is injected into model_settings.config."""
    m1 = trimesh.creation.box(extents=[10, 10, 10])
    m2 = trimesh.creation.box(extents=[10, 10, 10])
    m1.metadata["filament_id"] = 1
    m2.metadata["filament_id"] = 3

    placements = arrange([m1, m2], ["part_a", "part_b"], plate_size=(256, 256))
    scene = build_plate(placements)

    out = tmp_path / "plate.3mf"
    export_plate(scene, out)

    # model_settings.config should exist with per-object extruder
    with zipfile.ZipFile(out) as zf:
        ms = zf.read("Metadata/model_settings.config").decode()

    ms_root = ET.fromstring(ms)
    objects = ms_root.findall("object")
    assert len(objects) == 2

    # First object: extruder 1
    meta0 = {m.get("key"): m.get("value") for m in objects[0].findall("metadata")}
    assert meta0["extruder"] == "1"

    # Second object: extruder 3
    meta1 = {m.get("key"): m.get("value") for m in objects[1].findall("metadata")}
    assert meta1["extruder"] == "3"

    # Object IDs should match the 3dmodel.model
    model_root = _read_3mf_xml(out)
    model_objects = model_root.findall(f".//{{{NS_3MF}}}object")
    assert objects[0].get("id") == model_objects[0].get("id")
    assert objects[1].get("id") == model_objects[1].get("id")


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


# --- multi-object groups ---


def test_build_plate_expands_group(tmp_path):
    """Grouped placement (multi-object 3MF) expands into individual objects."""
    # Create two sub-meshes at different positions
    inlay = trimesh.creation.box(extents=[10, 10, 1])
    body = trimesh.creation.box(extents=[20, 20, 10])
    body.apply_translation([0, 0, 5])  # body sits on top of inlay
    inlay.metadata["filament_id"] = 2
    body.metadata["filament_id"] = 1

    # Combine for packing
    combined = trimesh.util.concatenate([inlay, body])
    combined.metadata["filament_id"] = 1
    combined.metadata["group_objects"] = [("inlay", inlay), ("body", body)]
    combined.metadata["original_bounds_min"] = combined.bounds[0][:2].copy()

    placements = arrange([combined], ["widget"], plate_size=(256, 256))
    scene = build_plate(placements, plate_size=(256, 256))

    # Scene should have 2 geometries (expanded from group), not 1
    assert len(scene.geometry) == 2
    # Check filament_ids are preserved
    geoms = list(scene.geometry.values())
    fil_ids = {g.metadata.get("filament_id") for g in geoms}
    assert fil_ids == {1, 2}

    # Export and verify 3MF has 2 objects with correct extruders
    out = tmp_path / "plate.3mf"
    export_plate(scene, out)
    with zipfile.ZipFile(out) as zf:
        ms = zf.read("Metadata/model_settings.config").decode()
    ms_root = ET.fromstring(ms)
    objects = ms_root.findall("object")
    assert len(objects) == 2
    extruders = {m.get("value") for o in objects for m in o.findall("metadata")}
    assert extruders == {"1", "2"}


def test_build_plate_sequence_filter():
    """Grouped objects can be filtered by sequence for sequential printing."""
    inlay = trimesh.creation.box(extents=[10, 10, 1])
    body = trimesh.creation.box(extents=[20, 20, 10])
    body.apply_translation([0, 0, 5])
    inlay.metadata["filament_id"] = 2
    inlay.metadata["sequence"] = 1
    body.metadata["filament_id"] = 1
    body.metadata["sequence"] = 2

    # Build full group
    combined = trimesh.util.concatenate([inlay, body])
    combined.metadata["filament_id"] = 1
    combined.metadata["group_objects"] = [("inlay", inlay), ("body", body)]
    combined.metadata["original_bounds_min"] = combined.bounds[0][:2].copy()

    placements = arrange([combined], ["widget"], plate_size=(256, 256))

    # Filter to sequence 1 only (inlay)
    seq1_objects = [
        (n, m)
        for n, m in placements[0].mesh.metadata["group_objects"]
        if m.metadata.get("sequence") == 1
    ]
    seq1_mesh = trimesh.util.concatenate([m for _, m in seq1_objects])
    seq1_mesh.metadata["filament_id"] = seq1_objects[0][1].metadata["filament_id"]
    seq1_mesh.metadata["group_objects"] = seq1_objects
    seq1_mesh.metadata["original_bounds_min"] = placements[0].mesh.metadata["original_bounds_min"]

    from fabprint.arrange import Placement

    seq1_placement = Placement(mesh=seq1_mesh, name="widget", x=placements[0].x, y=placements[0].y)
    scene1 = build_plate([seq1_placement], plate_size=(256, 256))
    assert len(scene1.geometry) == 1
    geom = list(scene1.geometry.values())[0]
    assert geom.metadata.get("filament_id") == 2

    # Filter to sequence 2 only (body)
    seq2_objects = [
        (n, m)
        for n, m in placements[0].mesh.metadata["group_objects"]
        if m.metadata.get("sequence") == 2
    ]
    seq2_mesh = trimesh.util.concatenate([m for _, m in seq2_objects])
    seq2_mesh.metadata["filament_id"] = seq2_objects[0][1].metadata["filament_id"]
    seq2_mesh.metadata["group_objects"] = seq2_objects
    seq2_mesh.metadata["original_bounds_min"] = placements[0].mesh.metadata["original_bounds_min"]

    seq2_placement = Placement(mesh=seq2_mesh, name="widget", x=placements[0].x, y=placements[0].y)
    scene2 = build_plate([seq2_placement], plate_size=(256, 256))
    assert len(scene2.geometry) == 1
    geom = list(scene2.geometry.values())[0]
    assert geom.metadata.get("filament_id") == 1
