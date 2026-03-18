"""Tests for the Hamilton pipeline DAG."""

from pathlib import Path

import pytest

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


# --- display_results tests ---


def test_display_results_plate(capsys):
    """_display_results shows plate and preview paths."""
    from fabprint.cli import _display_results

    _display_results(
        {
            "part_summary": "Parts:\n  cube x2",
            "plate_3mf_path": Path("/tmp/plate.3mf"),
            "preview_path": Path("/tmp/plate_preview.3mf"),
        }
    )
    out = capsys.readouterr().out
    assert "cube x2" in out
    assert "plate.3mf" in out
    assert "Preview:" in out


def test_display_results_gcode_stats(capsys):
    """_display_results shows gcode stats."""
    from fabprint.cli import _display_results

    _display_results(
        {
            "gcode_stats": {"filament_g": 12.5, "print_time": "1h 30m"},
        }
    )
    out = capsys.readouterr().out
    assert "12.5g" in out
    assert "1h 30m" in out


def test_display_results_empty(capsys):
    """_display_results with empty dict produces no output."""
    from fabprint.cli import _display_results

    _display_results({})
    assert capsys.readouterr().out == ""
