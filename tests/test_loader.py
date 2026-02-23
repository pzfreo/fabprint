"""Tests for mesh loading."""

import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest
import trimesh

from fabprint.loader import extract_paint_colors, load_mesh

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


# --- extract_paint_colors ---


def _make_painted_3mf(path, paint_colors):
    """Create a minimal 3MF file with paint_color attributes on triangles."""
    # Export a simple mesh via trimesh to get valid 3MF structure
    mesh = trimesh.creation.box(extents=[10, 10, 10])
    scene = trimesh.Scene()
    scene.add_geometry(mesh, node_name="painted_part")
    scene.export(str(path))

    # Post-process to add paint_color attributes
    ns = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
    ET.register_namespace("", ns)
    with zipfile.ZipFile(path, "r") as zf:
        model_xml = zf.read("3D/3dmodel.model")
        other_files = {n: zf.read(n) for n in zf.namelist() if n != "3D/3dmodel.model"}

    root = ET.fromstring(model_xml)
    triangles = list(root.iter(f"{{{ns}}}triangle"))
    for i, tri in enumerate(triangles):
        if i < len(paint_colors) and paint_colors[i] is not None:
            tri.set("paint_color", paint_colors[i])

    new_xml = ET.tostring(root, encoding="unicode", xml_declaration=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("3D/3dmodel.model", new_xml)
        for name, data in other_files.items():
            zf.writestr(name, data)


def test_extract_paint_colors_painted_3mf(tmp_path):
    """Extracts paint_color values from a painted 3MF."""
    path = tmp_path / "painted.3mf"
    _make_painted_3mf(path, ["4", "8", "4", "8"])
    colors = extract_paint_colors(path)
    assert colors is not None
    # First 4 triangles should have our paint values
    assert colors[0] == "4"
    assert colors[1] == "8"
    assert colors[2] == "4"
    assert colors[3] == "8"


def test_extract_paint_colors_unpainted_3mf(tmp_path):
    """Returns None for a 3MF without paint_color."""
    path = tmp_path / "plain.3mf"
    mesh = trimesh.creation.box(extents=[10, 10, 10])
    scene = trimesh.Scene()
    scene.add_geometry(mesh)
    scene.export(str(path))
    assert extract_paint_colors(path) is None


def test_extract_paint_colors_stl():
    """Returns None for non-3MF files."""
    assert extract_paint_colors(FIXTURES / "cube_10mm.stl") is None


def test_extract_paint_colors_nonexistent(tmp_path):
    """Returns None for non-existent file."""
    assert extract_paint_colors(tmp_path / "nope.3mf") is None
