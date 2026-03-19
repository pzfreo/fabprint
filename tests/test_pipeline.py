"""Tests for the Hamilton pipeline DAG."""

from pathlib import Path

import pytest
import trimesh

from fabprint.config import load_config
from fabprint.pipeline import LoadedParts, ResolvedFilaments, format_summary, load_parts

FIXTURES = Path(__file__).parent / "fixtures"


def _posix(p: Path) -> str:
    return p.as_posix()


def _write_config(tmp_path: Path) -> Path:
    toml = tmp_path / "fabprint.toml"
    toml.write_text(f"""
[plate]
size = [256, 256]
padding = 5.0

[slicer]
engine = "orca"

[[parts]]
file = "{_posix(FIXTURES / "cube_10mm.stl")}"
copies = 2
orient = "flat"

[[parts]]
file = "{_posix(FIXTURES / "cylinder_5x20mm.stl")}"
orient = "upright"
""")
    return toml


# --- Unit tests for pipeline helpers ---


def test_load_parts(tmp_path):
    cfg = load_config(_write_config(tmp_path))
    parts = load_parts(cfg)
    assert isinstance(parts, LoadedParts)
    assert len(parts.meshes) == 3  # 2 cubes + 1 cylinder
    assert len(parts.names) == 3
    assert len(parts.filament_ids) == 3
    assert not parts.has_paint_colors


def test_load_parts_with_scale(tmp_path):
    cfg = load_config(_write_config(tmp_path))
    parts = load_parts(cfg, global_scale=2.0)
    assert len(parts.meshes) == 3
    # Scaled cube should be ~20mm instead of ~10mm
    cube_extent = max(parts.meshes[0].extents)
    assert cube_extent > 15.0  # roughly 20mm after scaling


def test_format_summary(tmp_path):
    cfg = load_config(_write_config(tmp_path))
    parts = load_parts(cfg)
    summary = format_summary(parts, cfg.plate.size)
    assert "Parts:" in summary
    assert "cube_10mm" in summary
    assert "cylinder_5x20mm" in summary
    assert "256x256" in summary


def test_resolved_filaments_default():
    """ResolvedFilaments can be created with defaults."""
    rf = ResolvedFilaments()
    assert rf.filaments is None
    assert rf.filament_ids == []


# --- Hamilton DAG tests ---


def test_dag_builds():
    """Hamilton driver should build without errors."""
    from hamilton import driver

    from fabprint import pipeline

    dr = driver.Builder().with_modules(pipeline).build()
    variables = dr.list_available_variables()
    node_names = {v.name for v in variables}
    assert "config" in node_names
    assert "loaded_parts" in node_names
    assert "placements" in node_names
    assert "plate_3mf_path" in node_names
    assert "preview_path" in node_names
    assert "sliced_output_dir" in node_names
    assert "gcode_path" in node_names
    assert "print_result" in node_names


def test_dag_plate_execution(tmp_path):
    """Execute the DAG up to plate_3mf_path."""
    from hamilton import driver

    from fabprint import pipeline

    dr = driver.Builder().with_modules(pipeline).build()
    config_path = _write_config(tmp_path)
    output_3mf = tmp_path / "plate.3mf"

    result = dr.execute(
        ["plate_3mf_path", "preview_path", "part_summary"],
        inputs={
            "config_path": config_path,
            "global_scale": None,
            "output_3mf": output_3mf,
        },
    )

    assert result["plate_3mf_path"] == output_3mf
    assert output_3mf.exists()
    assert output_3mf.stat().st_size > 0
    assert result["preview_path"].exists()
    assert "cube_10mm" in result["part_summary"]


def test_dag_with_timing_adapter(tmp_path):
    """TimingAdapter should not break execution."""
    from hamilton import driver

    from fabprint import adapters, pipeline

    dr = driver.Builder().with_modules(pipeline).with_adapters(adapters.TimingAdapter()).build()
    config_path = _write_config(tmp_path)
    output_3mf = tmp_path / "plate.3mf"

    dr.execute(
        ["plate_3mf_path"],
        inputs={
            "config_path": config_path,
            "global_scale": None,
            "output_3mf": output_3mf,
        },
    )
    assert output_3mf.exists()


def test_cli_run_until_plate(tmp_path):
    """CLI run --until plate should work with the Hamilton pipeline."""
    from fabprint.cli import main

    config_path = _write_config(tmp_path)
    output_dir = tmp_path / "output"
    main(["run", str(config_path), "-o", str(output_dir), "--until", "plate"])
    assert (output_dir / "plate.3mf").exists()
    assert (output_dir / "plate.3mf").stat().st_size > 0


def test_resolve_outputs_full():
    """resolve_outputs with no flags returns all stage outputs."""
    from fabprint.pipeline import resolve_outputs

    stages = ["load", "arrange", "plate", "slice", "print"]
    outputs = resolve_outputs(stages)
    assert "loaded_parts" in outputs
    assert "plate_3mf_path" in outputs
    assert "sliced_output_dir" in outputs
    assert "print_result" in outputs


def test_resolve_outputs_until():
    """resolve_outputs with until stops at the right stage."""
    from fabprint.pipeline import resolve_outputs

    stages = ["load", "arrange", "plate", "slice", "print"]
    outputs = resolve_outputs(stages, until="plate")
    assert "plate_3mf_path" in outputs
    assert "sliced_output_dir" not in outputs
    assert "print_result" not in outputs


def test_resolve_outputs_only():
    """resolve_outputs with only returns just that stage's outputs."""
    from fabprint.pipeline import resolve_outputs

    stages = ["load", "arrange", "plate", "slice", "print"]
    outputs = resolve_outputs(stages, only="slice")
    assert outputs == ["sliced_output_dir", "gcode_stats"]


def test_resolve_outputs_unknown_stage():
    """resolve_outputs with unknown only stage raises."""
    from fabprint.pipeline import resolve_outputs

    with pytest.raises(ValueError, match="Unknown stage"):
        resolve_outputs(["load"], only="foobar")


def test_resolve_outputs_until_not_in_stages():
    """resolve_outputs with until stage not in pipeline raises."""
    from fabprint.pipeline import resolve_outputs

    with pytest.raises(ValueError, match="not in pipeline stages"):
        resolve_outputs(["load", "arrange"], until="print")


def test_resolve_outputs_only_gcode_info():
    """resolve_outputs --only gcode-info returns gcode_stats."""
    from fabprint.pipeline import resolve_outputs

    stages = ["load", "arrange", "plate", "slice", "gcode-info"]
    outputs = resolve_outputs(stages, only="gcode-info")
    assert outputs == ["gcode_stats"]


# --- resolve_overrides tests ---


def test_resolve_overrides_no_requirements():
    """Stages with no prerequisites return empty overrides."""
    from fabprint.pipeline import resolve_overrides

    overrides = resolve_overrides("plate", Path("/tmp/unused"))
    assert overrides == {}


def test_resolve_overrides_slice_finds_plate(tmp_path):
    """--only slice resolves plate_3mf_path from disk."""
    from fabprint.pipeline import resolve_overrides

    plate = tmp_path / "plate.3mf"
    plate.write_bytes(b"fake 3mf")
    overrides = resolve_overrides("slice", tmp_path)
    assert overrides["plate_3mf_path"] == plate


def test_resolve_overrides_slice_missing_plate(tmp_path):
    """--only slice raises when plate.3mf doesn't exist."""
    from fabprint.pipeline import resolve_overrides

    with pytest.raises(FileNotFoundError, match="plate 3MF file"):
        resolve_overrides("slice", tmp_path)


def test_resolve_overrides_gcode_info_finds_dir(tmp_path):
    """--only gcode-info resolves sliced_output_dir from disk."""
    from fabprint.pipeline import resolve_overrides

    overrides = resolve_overrides("gcode-info", tmp_path)
    assert overrides["sliced_output_dir"] == tmp_path


def test_resolve_overrides_print_finds_gcode(tmp_path):
    """--only print resolves gcode_path from disk."""
    from fabprint.pipeline import resolve_overrides

    gcode = tmp_path / "plate.gcode"
    gcode.write_text("G28\n")
    overrides = resolve_overrides("print", tmp_path)
    assert overrides["gcode_path"] == gcode


def test_resolve_overrides_print_missing_gcode(tmp_path):
    """--only print raises when no gcode files exist."""
    from fabprint.pipeline import resolve_overrides

    with pytest.raises(FileNotFoundError, match="sliced gcode file"):
        resolve_overrides("print", tmp_path)


# --- gcode_path node tests ---


def test_gcode_path_finds_file(tmp_path):
    """gcode_path returns the gcode file from the output dir."""
    from fabprint.pipeline import gcode_path

    gcode = tmp_path / "model.gcode"
    gcode.write_text("G28\n")
    assert gcode_path(tmp_path) == gcode


def test_gcode_path_no_files(tmp_path):
    """gcode_path raises when no gcode files exist."""
    from fabprint.pipeline import gcode_path

    with pytest.raises(RuntimeError, match="No gcode files"):
        gcode_path(tmp_path)


# --- resolved_filaments node tests ---


def test_resolved_filaments_with_override(tmp_path):
    """Filament type override should produce single-filament config."""
    from fabprint.pipeline import resolved_filaments

    cfg = load_config(_write_config(tmp_path))
    parts = load_parts(cfg)
    rf = resolved_filaments(
        cfg, parts, filament_type_override="Generic PETG @base", filament_slot_override=2
    )
    assert rf.filaments == ["Generic PETG @base"]
    assert all(slot == 2 for slot in rf.filament_ids)


def test_resolved_filaments_from_config(tmp_path):
    """Without override, filaments come from config."""
    from fabprint.pipeline import resolved_filaments

    cfg = load_config(_write_config(tmp_path))
    parts = load_parts(cfg)
    rf = resolved_filaments(cfg, parts, filament_type_override=None, filament_slot_override=1)
    assert rf.filament_ids == parts.filament_ids


# --- format_summary edge case ---


def test_format_summary_empty():
    """format_summary with empty parts returns empty string."""
    parts = LoadedParts()
    assert format_summary(parts, (256, 256)) == ""


# --- Additional tests for improved coverage ---


class TestResolveOutputsAllPaths:
    """Test resolve_outputs for only, until, and default/None paths."""

    def test_only_returns_single_stage_outputs(self):
        from fabprint.pipeline import resolve_outputs

        outputs = resolve_outputs(["load", "arrange", "plate"], only="load")
        assert outputs == ["loaded_parts", "part_summary"]

    def test_only_print_returns_print_result(self):
        from fabprint.pipeline import resolve_outputs

        outputs = resolve_outputs(["load"], only="print")
        assert outputs == ["print_result"]

    def test_until_load(self):
        from fabprint.pipeline import resolve_outputs

        stages = ["load", "arrange", "plate", "slice", "print"]
        outputs = resolve_outputs(stages, until="load")
        assert "loaded_parts" in outputs
        assert "part_summary" in outputs
        assert "placements" not in outputs

    def test_default_none_returns_all(self):
        from fabprint.pipeline import resolve_outputs

        stages = ["load", "arrange", "plate"]
        outputs = resolve_outputs(stages, until=None, only=None)
        assert "loaded_parts" in outputs
        assert "placements" in outputs
        assert "plate_3mf_path" in outputs
        assert "preview_path" in outputs


class TestResolveOverridesArtifacts:
    """Test resolve_overrides for slice/gcode-info/print artifact discovery."""

    def test_slice_excludes_preview_plate(self, tmp_path):
        """Preview plate files should be excluded when resolving slice."""
        from fabprint.pipeline import resolve_overrides

        (tmp_path / "plate_preview.3mf").write_bytes(b"preview")
        (tmp_path / "plate.3mf").write_bytes(b"real")
        overrides = resolve_overrides("slice", tmp_path)
        assert "preview" not in overrides["plate_3mf_path"].name

    def test_gcode_info_missing_dir(self, tmp_path):
        """--only gcode-info raises when output dir doesn't exist."""
        from fabprint.pipeline import resolve_overrides

        missing = tmp_path / "nonexistent_dir"
        with pytest.raises(FileNotFoundError, match="slicer output directory"):
            resolve_overrides("gcode-info", missing)

    def test_gcode_info_existing_dir(self, tmp_path):
        """--only gcode-info resolves existing dir."""
        from fabprint.pipeline import resolve_overrides

        overrides = resolve_overrides("gcode-info", tmp_path)
        assert overrides["sliced_output_dir"] == tmp_path

    def test_print_finds_first_gcode(self, tmp_path):
        """--only print picks a gcode file from the directory."""
        from fabprint.pipeline import resolve_overrides

        (tmp_path / "a.gcode").write_text("G28\n")
        (tmp_path / "b.gcode").write_text("G28\n")
        overrides = resolve_overrides("print", tmp_path)
        assert overrides["gcode_path"].suffix == ".gcode"

    def test_unknown_stage_returns_empty(self):
        """Stages with no requirements return empty overrides."""
        from fabprint.pipeline import resolve_overrides

        overrides = resolve_overrides("load", Path("/tmp/unused"))
        assert overrides == {}


class TestLoadPartsFileGroups:
    """Test load_parts with file groups, object-by-object filament, paint colors."""

    def test_object_filaments_assignment(self, tmp_path):
        """Parts with object_filaments assign per-object filament IDs."""
        # Create a multi-object 3MF
        import xml.etree.ElementTree as ET
        import zipfile

        ns = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
        ET.register_namespace("", ns)

        model = ET.Element(f"{{{ns}}}model", attrib={"unit": "millimeter"})
        resources = ET.SubElement(model, f"{{{ns}}}resources")
        build = ET.SubElement(model, f"{{{ns}}}build")

        for i, (name, extents) in enumerate([("body", [20, 20, 10]), ("inlay", [10, 10, 2])], 1):
            mesh = trimesh.creation.box(extents=extents)
            obj = ET.SubElement(resources, f"{{{ns}}}object", id=str(i), name=name, type="model")
            mesh_elem = ET.SubElement(obj, f"{{{ns}}}mesh")
            verts_elem = ET.SubElement(mesh_elem, f"{{{ns}}}vertices")
            for v in mesh.vertices:
                ET.SubElement(verts_elem, f"{{{ns}}}vertex", x=str(v[0]), y=str(v[1]), z=str(v[2]))
            tris_elem = ET.SubElement(mesh_elem, f"{{{ns}}}triangles")
            for f in mesh.faces:
                ET.SubElement(
                    tris_elem, f"{{{ns}}}triangle", v1=str(f[0]), v2=str(f[1]), v3=str(f[2])
                )
            ET.SubElement(build, f"{{{ns}}}item", objectid=str(i))

        path_3mf = tmp_path / "multi.3mf"
        xml_str = ET.tostring(model, encoding="unicode", xml_declaration=True)
        with zipfile.ZipFile(path_3mf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("3D/3dmodel.model", xml_str)

        toml = tmp_path / "fabprint.toml"
        toml.write_text(f"""
[plate]
size = [256, 256]
padding = 5.0

[slicer]
engine = "orca"

[[parts]]
file = "{_posix(path_3mf)}"
orient = "flat"
filament = 1
filaments = {{ body = 1, inlay = 2 }}
""")
        cfg = load_config(toml)
        parts = load_parts(cfg)
        assert len(parts.meshes) == 1
        # The combined mesh should have group_objects metadata
        assert "group_objects" in parts.meshes[0].metadata

    def test_paint_colors_detected(self, tmp_path):
        """Parts with paint colors set has_paint_colors flag."""
        import xml.etree.ElementTree as ET
        import zipfile

        # Create a painted 3MF
        mesh = trimesh.creation.box(extents=[10, 10, 10])
        scene = trimesh.Scene()
        scene.add_geometry(mesh, node_name="painted")
        path_3mf = tmp_path / "painted.3mf"
        scene.export(str(path_3mf))

        ns = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
        ET.register_namespace("", ns)
        with zipfile.ZipFile(path_3mf, "r") as zf:
            model_xml = zf.read("3D/3dmodel.model")
            other_files = {n: zf.read(n) for n in zf.namelist() if n != "3D/3dmodel.model"}

        root = ET.fromstring(model_xml)
        for tri in root.iter(f"{{{ns}}}triangle"):
            tri.set("paint_color", "#FF0000")

        new_xml = ET.tostring(root, encoding="unicode", xml_declaration=True)
        with zipfile.ZipFile(path_3mf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("3D/3dmodel.model", new_xml)
            for name, data in other_files.items():
                zf.writestr(name, data)

        toml = tmp_path / "fabprint.toml"
        toml.write_text(f"""
[plate]
size = [256, 256]
padding = 5.0

[slicer]
engine = "orca"

[[parts]]
file = "{_posix(path_3mf)}"
orient = "flat"
""")
        cfg = load_config(toml)
        parts = load_parts(cfg)
        assert parts.has_paint_colors

    def test_grouped_object_parts(self, tmp_path):
        """Multiple parts referencing objects from the same 3MF file are grouped."""
        import xml.etree.ElementTree as ET
        import zipfile

        ns = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
        ET.register_namespace("", ns)

        model = ET.Element(f"{{{ns}}}model", attrib={"unit": "millimeter"})
        resources = ET.SubElement(model, f"{{{ns}}}resources")
        build = ET.SubElement(model, f"{{{ns}}}build")

        for i, (name, extents) in enumerate([("body", [20, 20, 10]), ("inlay", [10, 10, 2])], 1):
            mesh = trimesh.creation.box(extents=extents)
            obj = ET.SubElement(resources, f"{{{ns}}}object", id=str(i), name=name, type="model")
            mesh_elem = ET.SubElement(obj, f"{{{ns}}}mesh")
            verts_elem = ET.SubElement(mesh_elem, f"{{{ns}}}vertices")
            for v in mesh.vertices:
                ET.SubElement(verts_elem, f"{{{ns}}}vertex", x=str(v[0]), y=str(v[1]), z=str(v[2]))
            tris_elem = ET.SubElement(mesh_elem, f"{{{ns}}}triangles")
            for f in mesh.faces:
                ET.SubElement(
                    tris_elem, f"{{{ns}}}triangle", v1=str(f[0]), v2=str(f[1]), v3=str(f[2])
                )
            ET.SubElement(build, f"{{{ns}}}item", objectid=str(i))

        path_3mf = tmp_path / "multi.3mf"
        xml_str = ET.tostring(model, encoding="unicode", xml_declaration=True)
        with zipfile.ZipFile(path_3mf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("3D/3dmodel.model", xml_str)

        toml = tmp_path / "fabprint.toml"
        toml.write_text(f"""
[plate]
size = [256, 256]
padding = 5.0

[slicer]
engine = "orca"

[[parts]]
file = "{_posix(path_3mf)}"
object = "body"
filament = 1

[[parts]]
file = "{_posix(path_3mf)}"
object = "inlay"
filament = 2
""")
        cfg = load_config(toml)
        parts = load_parts(cfg)
        # Two objects from same file should be grouped into one mesh
        assert len(parts.meshes) == 1
        assert "group_objects" in parts.meshes[0].metadata

    def test_single_object_part(self, tmp_path):
        """A single object-selection part (non-grouped) loads correctly."""
        import xml.etree.ElementTree as ET
        import zipfile

        ns = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
        ET.register_namespace("", ns)

        model = ET.Element(f"{{{ns}}}model", attrib={"unit": "millimeter"})
        resources = ET.SubElement(model, f"{{{ns}}}resources")
        build = ET.SubElement(model, f"{{{ns}}}build")

        mesh = trimesh.creation.box(extents=[10, 10, 10])
        obj = ET.SubElement(resources, f"{{{ns}}}object", id="1", name="only_obj", type="model")
        mesh_elem = ET.SubElement(obj, f"{{{ns}}}mesh")
        verts_elem = ET.SubElement(mesh_elem, f"{{{ns}}}vertices")
        for v in mesh.vertices:
            ET.SubElement(verts_elem, f"{{{ns}}}vertex", x=str(v[0]), y=str(v[1]), z=str(v[2]))
        tris_elem = ET.SubElement(mesh_elem, f"{{{ns}}}triangles")
        for f in mesh.faces:
            ET.SubElement(tris_elem, f"{{{ns}}}triangle", v1=str(f[0]), v2=str(f[1]), v3=str(f[2]))
        ET.SubElement(build, f"{{{ns}}}item", objectid="1")

        path_3mf = tmp_path / "single_obj.3mf"
        xml_str = ET.tostring(model, encoding="unicode", xml_declaration=True)
        with zipfile.ZipFile(path_3mf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("3D/3dmodel.model", xml_str)

        toml = tmp_path / "fabprint.toml"
        toml.write_text(f"""
[plate]
size = [256, 256]
padding = 5.0

[slicer]
engine = "orca"

[[parts]]
file = "{_posix(path_3mf)}"
object = "only_obj"
filament = 1
""")
        cfg = load_config(toml)
        parts = load_parts(cfg)
        assert len(parts.meshes) == 1
        assert parts.names[0] == "single_obj"
        assert parts.filament_ids[0] == 1

    def test_object_not_found_raises(self, tmp_path):
        """Referencing a nonexistent object name raises ValueError."""
        import xml.etree.ElementTree as ET
        import zipfile

        ns = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
        ET.register_namespace("", ns)

        model = ET.Element(f"{{{ns}}}model", attrib={"unit": "millimeter"})
        resources = ET.SubElement(model, f"{{{ns}}}resources")
        build = ET.SubElement(model, f"{{{ns}}}build")

        mesh = trimesh.creation.box(extents=[10, 10, 10])
        obj = ET.SubElement(resources, f"{{{ns}}}object", id="1", name="real_obj", type="model")
        mesh_elem = ET.SubElement(obj, f"{{{ns}}}mesh")
        verts_elem = ET.SubElement(mesh_elem, f"{{{ns}}}vertices")
        for v in mesh.vertices:
            ET.SubElement(verts_elem, f"{{{ns}}}vertex", x=str(v[0]), y=str(v[1]), z=str(v[2]))
        tris_elem = ET.SubElement(mesh_elem, f"{{{ns}}}triangles")
        for f in mesh.faces:
            ET.SubElement(tris_elem, f"{{{ns}}}triangle", v1=str(f[0]), v2=str(f[1]), v3=str(f[2]))
        ET.SubElement(build, f"{{{ns}}}item", objectid="1")

        path_3mf = tmp_path / "obj.3mf"
        xml_str = ET.tostring(model, encoding="unicode", xml_declaration=True)
        with zipfile.ZipFile(path_3mf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("3D/3dmodel.model", xml_str)

        toml = tmp_path / "fabprint.toml"
        toml.write_text(f"""
[plate]
size = [256, 256]
padding = 5.0

[slicer]
engine = "orca"

[[parts]]
file = "{_posix(path_3mf)}"
object = "nonexistent_obj"
filament = 1
""")
        cfg = load_config(toml)
        with pytest.raises(ValueError, match="Object 'nonexistent_obj' not found"):
            load_parts(cfg)


class TestFormatSummaryOutput:
    """Test format_summary output formatting."""

    def test_scale_shown_when_not_one(self):
        parts = LoadedParts(
            meshes=[trimesh.creation.box(extents=[10, 10, 10])],
            names=["cube"],
            filament_ids=[1],
            part_info=[("cube", 1, 1, 2.0, 20.0, 20.0, 20.0)],
        )
        summary = format_summary(parts, (256, 256))
        assert "2.0x" in summary

    def test_scale_hidden_when_one(self):
        parts = LoadedParts(
            meshes=[trimesh.creation.box(extents=[10, 10, 10])],
            names=["cube"],
            filament_ids=[1],
            part_info=[("cube", 1, 1, 1.0, 10.0, 10.0, 10.0)],
        )
        summary = format_summary(parts, (256, 256))
        assert "1.0x" not in summary

    def test_multiple_parts_formatted(self):
        parts = LoadedParts(
            meshes=[trimesh.creation.box(extents=[10, 10, 10])] * 3,
            names=["cube_1", "cube_2", "cyl"],
            filament_ids=[1, 1, 2],
            part_info=[
                ("cube", 2, 1, 1.0, 10.0, 10.0, 10.0),
                ("cyl", 1, 2, 1.0, 5.0, 5.0, 20.0),
            ],
        )
        summary = format_summary(parts, (200, 200))
        assert "cube" in summary
        assert "cyl" in summary
        assert "3 parts" in summary
        assert "200x200" in summary

    def test_plate_size_in_summary(self):
        parts = LoadedParts(
            meshes=[trimesh.creation.box(extents=[10, 10, 10])],
            names=["a"],
            filament_ids=[1],
            part_info=[("a", 1, 1, 1.0, 10.0, 10.0, 10.0)],
        )
        summary = format_summary(parts, (180, 180))
        assert "180x180" in summary


class TestResolvedFilamentsPaintColors:
    """Test resolved_filaments with paint colors."""

    def test_paint_colors_returns_none_filaments(self, tmp_path):
        from fabprint.pipeline import resolved_filaments

        cfg = load_config(_write_config(tmp_path))
        parts = LoadedParts(
            meshes=[],
            names=[],
            filament_ids=[1, 2],
            has_paint_colors=True,
            part_info=[],
        )
        rf = resolved_filaments(cfg, parts, filament_type_override=None, filament_slot_override=1)
        assert rf.filaments is None
        assert rf.filament_ids == [1, 2]


class TestPrintResultNode:
    """Test print_result node error handling."""

    def test_no_printer_config_raises(self, tmp_path):
        from fabprint.pipeline import print_result

        toml = tmp_path / "fabprint.toml"
        stl = tmp_path / "dummy.stl"
        # Create a minimal STL so config loader doesn't fail
        mesh = trimesh.creation.box(extents=[10, 10, 10])
        mesh.export(str(stl))

        toml.write_text(f"""
[plate]
size = [256, 256]
padding = 5.0

[slicer]
engine = "orca"

[[parts]]
file = "{_posix(stl)}"
""")
        cfg = load_config(toml)
        assert cfg.printer is None

        with pytest.raises(ValueError, match="No \\[printer\\] section"):
            print_result(
                gcode_path=Path("/tmp/fake.gcode"),
                config=cfg,
                dry_run=False,
                upload_only=False,
                experimental=False,
                skip_ams_mapping=False,
            )
