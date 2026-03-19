"""Tests for mesh loading."""

import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest
import trimesh

from fabprint.loader import extract_paint_colors, load_3mf_objects, load_mesh

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


# --- load_3mf_objects ---


def _make_multi_object_3mf(path, objects, transforms=None):
    """Create a 3MF with multiple named objects.

    objects: list of (name, trimesh.Trimesh)
    transforms: optional dict of name → 12-float transform string
    """
    ns = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
    ET.register_namespace("", ns)

    model = ET.Element(f"{{{ns}}}model", attrib={"unit": "millimeter"})
    resources = ET.SubElement(model, f"{{{ns}}}resources")
    build = ET.SubElement(model, f"{{{ns}}}build")

    for i, (name, mesh) in enumerate(objects, start=1):
        obj = ET.SubElement(resources, f"{{{ns}}}object", id=str(i), name=name, type="model")
        mesh_elem = ET.SubElement(obj, f"{{{ns}}}mesh")
        verts_elem = ET.SubElement(mesh_elem, f"{{{ns}}}vertices")
        for v in mesh.vertices:
            ET.SubElement(verts_elem, f"{{{ns}}}vertex", x=str(v[0]), y=str(v[1]), z=str(v[2]))
        tris_elem = ET.SubElement(mesh_elem, f"{{{ns}}}triangles")
        for f in mesh.faces:
            ET.SubElement(tris_elem, f"{{{ns}}}triangle", v1=str(f[0]), v2=str(f[1]), v3=str(f[2]))
        item_attrib = {"objectid": str(i)}
        if transforms and name in transforms:
            item_attrib["transform"] = transforms[name]
        ET.SubElement(build, f"{{{ns}}}item", **item_attrib)

    xml_str = ET.tostring(model, encoding="unicode", xml_declaration=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("3D/3dmodel.model", xml_str)


def test_load_3mf_objects_two_objects(tmp_path):
    """Loads two named objects from a multi-object 3MF."""
    path = tmp_path / "multi.3mf"
    box1 = trimesh.creation.box(extents=[10, 10, 2])  # thin inlay
    box2 = trimesh.creation.box(extents=[20, 20, 10])  # body
    _make_multi_object_3mf(path, [("inlay", box1), ("body", box2)])

    objects = load_3mf_objects(path)
    assert len(objects) == 2
    assert objects[0][0] == "inlay"
    assert objects[1][0] == "body"
    # Inlay should be ~10x10x2
    assert abs(objects[0][1].extents[0] - 10.0) < 0.1
    assert abs(objects[0][1].extents[2] - 2.0) < 0.1
    # Body should be ~20x20x10
    assert abs(objects[1][1].extents[0] - 20.0) < 0.1
    assert abs(objects[1][1].extents[2] - 10.0) < 0.1


def test_load_3mf_objects_preserves_position(tmp_path):
    """Objects maintain their coordinate positions."""
    path = tmp_path / "positioned.3mf"
    box1 = trimesh.creation.box(extents=[5, 5, 1])
    # Offset box2 by 10mm in X
    box2 = trimesh.creation.box(extents=[5, 5, 5])
    box2.apply_translation([10, 0, 0])
    _make_multi_object_3mf(path, [("a", box1), ("b", box2)])

    objects = load_3mf_objects(path)
    # box2 center should be at x=10
    b_center_x = (objects[1][1].bounds[0][0] + objects[1][1].bounds[1][0]) / 2
    assert abs(b_center_x - 10.0) < 0.1


def test_load_3mf_objects_nonexistent(tmp_path):
    """Raises FileNotFoundError for missing file."""
    with pytest.raises(FileNotFoundError):
        load_3mf_objects(tmp_path / "nope.3mf")


def test_load_3mf_objects_with_transform(tmp_path):
    """Build-section transforms are applied to objects."""
    path = tmp_path / "transformed.3mf"
    box = trimesh.creation.box(extents=[10, 10, 10])
    # Transform: identity rotation, translate by (50, 0, 0)
    transform = "1 0 0 0 1 0 0 0 1 50 0 0"
    _make_multi_object_3mf(path, [("box", box)], transforms={"box": transform})

    objects = load_3mf_objects(path)
    center_x = (objects[0][1].bounds[0][0] + objects[0][1].bounds[1][0]) / 2
    assert abs(center_x - 50.0) < 0.1


# --- Additional tests for improved coverage ---


class TestLoadMeshSceneAndErrors:
    """Test load_mesh with Scene (multiple geometries) and non-Trimesh error."""

    def test_multi_geometry_3mf_merged(self, tmp_path):
        """A 3MF with multiple geometries is merged into a single Trimesh."""
        path = tmp_path / "multi_geom.3mf"
        box1 = trimesh.creation.box(extents=[10, 10, 10])
        box2 = trimesh.creation.box(extents=[5, 5, 5])
        box2.apply_translation([20, 0, 0])
        scene = trimesh.Scene()
        scene.add_geometry(box1, node_name="box1")
        scene.add_geometry(box2, node_name="box2")
        scene.export(str(path))

        mesh = load_mesh(path)
        assert isinstance(mesh, trimesh.Trimesh)
        # Merged mesh should span from box1 to box2
        assert mesh.extents[0] > 15.0  # at least wide enough for both

    def test_empty_scene_raises(self, tmp_path, monkeypatch):
        """A 3MF that loads as an empty Scene raises ValueError."""
        path = tmp_path / "empty.3mf"
        path.write_bytes(b"dummy")  # file must exist for the existence check

        # Mock trimesh.load to return an empty Scene
        monkeypatch.setattr("trimesh.load", lambda *a, **kw: trimesh.Scene())

        with pytest.raises(ValueError, match="No geometry found"):
            load_mesh(path)

    def test_step_extension_without_build123d(self, tmp_path, monkeypatch):
        """STEP files raise ImportError when build123d is not installed."""
        path = tmp_path / "model.step"
        path.write_text("dummy step content")

        # Mock the import to fail
        import builtins

        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "build123d":
                raise ImportError("No module named 'build123d'")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)

        with pytest.raises(ImportError, match="build123d is required"):
            load_mesh(path)


class TestExtractPaintColorsEdgeCases:
    """Test extract_paint_colors with non-3MF and edge cases."""

    def test_non_3mf_returns_none(self):
        """Non-3MF files return None."""
        assert extract_paint_colors(Path("/tmp/model.stl")) is None
        assert extract_paint_colors(Path("/tmp/model.obj")) is None

    def test_bad_zip_returns_none(self, tmp_path):
        """A corrupted 3MF file returns None."""
        path = tmp_path / "corrupt.3mf"
        path.write_bytes(b"not a zip file at all")
        assert extract_paint_colors(path) is None

    def test_3mf_without_model_files(self, tmp_path):
        """A 3MF zip without model XML files returns None."""
        path = tmp_path / "nomodel.3mf"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("readme.txt", "no model here")
        assert extract_paint_colors(path) is None


class TestLoad3mfObjectsEdgeCases:
    """Test load_3mf_objects with mesh_elem None, missing obj_id, build is None."""

    def test_object_without_mesh_skipped(self, tmp_path):
        """Objects without a mesh element are skipped."""
        ns = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
        ET.register_namespace("", ns)

        model = ET.Element(f"{{{ns}}}model", attrib={"unit": "millimeter"})
        resources = ET.SubElement(model, f"{{{ns}}}resources")
        build = ET.SubElement(model, f"{{{ns}}}build")

        # Object 1: no mesh element (should be skipped)
        ET.SubElement(resources, f"{{{ns}}}object", id="1", name="no_mesh", type="model")

        # Object 2: valid mesh
        box = trimesh.creation.box(extents=[10, 10, 10])
        obj2 = ET.SubElement(resources, f"{{{ns}}}object", id="2", name="valid", type="model")
        mesh_elem = ET.SubElement(obj2, f"{{{ns}}}mesh")
        verts_elem = ET.SubElement(mesh_elem, f"{{{ns}}}vertices")
        for v in box.vertices:
            ET.SubElement(verts_elem, f"{{{ns}}}vertex", x=str(v[0]), y=str(v[1]), z=str(v[2]))
        tris_elem = ET.SubElement(mesh_elem, f"{{{ns}}}triangles")
        for f in box.faces:
            ET.SubElement(tris_elem, f"{{{ns}}}triangle", v1=str(f[0]), v2=str(f[1]), v3=str(f[2]))

        # Only object 2 in build
        ET.SubElement(build, f"{{{ns}}}item", objectid="2")

        path = tmp_path / "skip_nomesh.3mf"
        xml_str = ET.tostring(model, encoding="unicode", xml_declaration=True)
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("3D/3dmodel.model", xml_str)

        objects = load_3mf_objects(path)
        assert len(objects) == 1
        assert objects[0][0] == "valid"

    def test_build_item_missing_obj_id_skipped(self, tmp_path):
        """Build items referencing non-existent object IDs are skipped."""
        ns = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
        ET.register_namespace("", ns)

        model = ET.Element(f"{{{ns}}}model", attrib={"unit": "millimeter"})
        resources = ET.SubElement(model, f"{{{ns}}}resources")
        build = ET.SubElement(model, f"{{{ns}}}build")

        box = trimesh.creation.box(extents=[10, 10, 10])
        obj = ET.SubElement(resources, f"{{{ns}}}object", id="1", name="box", type="model")
        mesh_elem = ET.SubElement(obj, f"{{{ns}}}mesh")
        verts_elem = ET.SubElement(mesh_elem, f"{{{ns}}}vertices")
        for v in box.vertices:
            ET.SubElement(verts_elem, f"{{{ns}}}vertex", x=str(v[0]), y=str(v[1]), z=str(v[2]))
        tris_elem = ET.SubElement(mesh_elem, f"{{{ns}}}triangles")
        for f in box.faces:
            ET.SubElement(tris_elem, f"{{{ns}}}triangle", v1=str(f[0]), v2=str(f[1]), v3=str(f[2]))

        # Build references obj 1 (exists) and obj 99 (doesn't exist)
        ET.SubElement(build, f"{{{ns}}}item", objectid="1")
        ET.SubElement(build, f"{{{ns}}}item", objectid="99")

        path = tmp_path / "missing_ref.3mf"
        xml_str = ET.tostring(model, encoding="unicode", xml_declaration=True)
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("3D/3dmodel.model", xml_str)

        objects = load_3mf_objects(path)
        assert len(objects) == 1
        assert objects[0][0] == "box"

    def test_no_build_section_uses_all_objects(self, tmp_path):
        """When build section is absent, all parsed objects are returned."""
        ns = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
        ET.register_namespace("", ns)

        model = ET.Element(f"{{{ns}}}model", attrib={"unit": "millimeter"})
        resources = ET.SubElement(model, f"{{{ns}}}resources")
        # No <build> element

        for i, (name, extents) in enumerate([("a", [10, 10, 10]), ("b", [5, 5, 5])], 1):
            box = trimesh.creation.box(extents=extents)
            obj = ET.SubElement(resources, f"{{{ns}}}object", id=str(i), name=name, type="model")
            mesh_elem = ET.SubElement(obj, f"{{{ns}}}mesh")
            verts_elem = ET.SubElement(mesh_elem, f"{{{ns}}}vertices")
            for v in box.vertices:
                ET.SubElement(verts_elem, f"{{{ns}}}vertex", x=str(v[0]), y=str(v[1]), z=str(v[2]))
            tris_elem = ET.SubElement(mesh_elem, f"{{{ns}}}triangles")
            for f in box.faces:
                ET.SubElement(
                    tris_elem, f"{{{ns}}}triangle", v1=str(f[0]), v2=str(f[1]), v3=str(f[2])
                )

        path = tmp_path / "no_build.3mf"
        xml_str = ET.tostring(model, encoding="unicode", xml_declaration=True)
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("3D/3dmodel.model", xml_str)

        objects = load_3mf_objects(path)
        assert len(objects) == 2
        names = [n for n, _ in objects]
        assert "a" in names
        assert "b" in names

    def test_no_objects_at_all_raises(self, tmp_path):
        """A 3MF with no mesh objects raises ValueError."""
        ns = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
        ET.register_namespace("", ns)

        model = ET.Element(f"{{{ns}}}model", attrib={"unit": "millimeter"})
        ET.SubElement(model, f"{{{ns}}}resources")
        ET.SubElement(model, f"{{{ns}}}build")

        path = tmp_path / "empty_objects.3mf"
        xml_str = ET.tostring(model, encoding="unicode", xml_declaration=True)
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("3D/3dmodel.model", xml_str)

        with pytest.raises(ValueError, match="No mesh objects found"):
            load_3mf_objects(path)

    def test_object_default_name(self, tmp_path):
        """Objects without a name attribute get a default name."""
        ns = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
        ET.register_namespace("", ns)

        model = ET.Element(f"{{{ns}}}model", attrib={"unit": "millimeter"})
        resources = ET.SubElement(model, f"{{{ns}}}resources")
        build = ET.SubElement(model, f"{{{ns}}}build")

        box = trimesh.creation.box(extents=[10, 10, 10])
        # No name attribute on the object
        obj = ET.SubElement(resources, f"{{{ns}}}object", id="7", type="model")
        mesh_elem = ET.SubElement(obj, f"{{{ns}}}mesh")
        verts_elem = ET.SubElement(mesh_elem, f"{{{ns}}}vertices")
        for v in box.vertices:
            ET.SubElement(verts_elem, f"{{{ns}}}vertex", x=str(v[0]), y=str(v[1]), z=str(v[2]))
        tris_elem = ET.SubElement(mesh_elem, f"{{{ns}}}triangles")
        for f in box.faces:
            ET.SubElement(tris_elem, f"{{{ns}}}triangle", v1=str(f[0]), v2=str(f[1]), v3=str(f[2]))
        ET.SubElement(build, f"{{{ns}}}item", objectid="7")

        path = tmp_path / "noname.3mf"
        xml_str = ET.tostring(model, encoding="unicode", xml_declaration=True)
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("3D/3dmodel.model", xml_str)

        objects = load_3mf_objects(path)
        assert len(objects) == 1
        assert objects[0][0] == "object_7"
