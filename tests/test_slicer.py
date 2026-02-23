"""Tests for slicer module."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fabprint.slicer import (
    SLICER_PATHS,
    _apply_overrides,
    _docker_image,
    _has_docker,
    _slice_via_docker,
    find_slicer,
    parse_gcode_stats,
    slice_plate,
)

# --- find_slicer ---


def test_find_bambu():
    if not SLICER_PATHS["bambu"].exists():
        pytest.skip("BambuStudio not installed")
    assert find_slicer("bambu") == SLICER_PATHS["bambu"]


def test_find_orca():
    if not SLICER_PATHS["orca"].exists():
        pytest.skip("OrcaSlicer not installed")
    assert find_slicer("orca") == SLICER_PATHS["orca"]


def test_find_unknown():
    with pytest.raises(ValueError, match="Unknown slicer"):
        find_slicer("cura")


def test_find_slicer_path_fallback():
    """Falls back to shutil.which when default path doesn't exist."""
    with patch.dict("fabprint.slicer.SLICER_PATHS", {"orca": Path("/nonexistent/orca")}):
        with patch("fabprint.slicer.shutil.which", return_value="/usr/local/bin/orca-slicer"):
            result = find_slicer("orca")
            assert result == Path("/usr/local/bin/orca-slicer")


def test_find_slicer_not_found():
    """Raises FileNotFoundError when slicer not at default path or on PATH."""
    with patch.dict("fabprint.slicer.SLICER_PATHS", {"orca": Path("/nonexistent/orca")}):
        with patch("fabprint.slicer.shutil.which", return_value=None):
            with pytest.raises(FileNotFoundError, match="not found"):
                find_slicer("orca")


# --- _apply_overrides ---


def test_apply_overrides():
    data = {
        "wall_loops": "2",
        "sparse_infill_density": "15%",
        "other_setting": "keep",
    }
    result = _apply_overrides(
        data,
        {
            "wall_loops": 4,
            "sparse_infill_density": "25%",
        },
        "test_profile",
    )

    assert result["wall_loops"] == "4"
    assert result["sparse_infill_density"] == "25%"
    assert result["other_setting"] == "keep"
    assert result is data  # modifies in place


# --- _docker_image ---


def test_docker_image_default():
    assert _docker_image() == "fabprint:latest"


def test_docker_image_versioned():
    assert _docker_image("2.3.1") == "fabprint:orca-2.3.1"
    assert _docker_image("2.3.2") == "fabprint:orca-2.3.2"


# --- _has_docker ---


def test_has_docker_true():
    mock_result = MagicMock(returncode=0)
    with patch("fabprint.slicer.subprocess.run", return_value=mock_result) as mock_run:
        assert _has_docker("fabprint:orca-2.3.1") is True
        mock_run.assert_called_once_with(
            ["docker", "image", "inspect", "fabprint:orca-2.3.1"],
            capture_output=True,
            timeout=10,
        )


def test_has_docker_false_no_image():
    mock_result = MagicMock(returncode=1)
    with patch("fabprint.slicer.subprocess.run", return_value=mock_result):
        assert _has_docker("fabprint:orca-2.3.1") is False


def test_has_docker_false_no_docker():
    with patch("fabprint.slicer.subprocess.run", side_effect=FileNotFoundError):
        assert _has_docker("fabprint:orca-2.3.1") is False


# --- _slice_via_docker ---


def test_slice_via_docker_command(tmp_path):
    """Verify Docker command is built correctly with profile path rewriting."""
    input_3mf = tmp_path / "plate.3mf"
    input_3mf.write_text("fake")
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    profile_dir = output_dir / ".profiles"
    profile_dir.mkdir()
    (profile_dir / "machine.json").write_text("{}")
    (profile_dir / "process.json").write_text("{}")

    settings_arg = f"{profile_dir}/machine.json;{profile_dir}/process.json"
    filament_arg = None

    mock_result = MagicMock(returncode=0, stdout="", stderr="")
    with patch("fabprint.slicer.subprocess.run", return_value=mock_result) as mock_run:
        _slice_via_docker(
            input_3mf,
            output_dir,
            profile_dir,
            settings_arg,
            filament_arg,
            "fabprint:orca-2.3.1",
        )

    cmd = mock_run.call_args[0][0]
    assert "docker" == cmd[0]
    assert "--platform" in cmd
    assert "linux/amd64" in cmd
    assert "fabprint:orca-2.3.1" in cmd
    assert "--entrypoint" in cmd
    assert "orca-slicer" in cmd
    # Verify profile paths rewritten to container paths under /work/output/
    settings_idx = cmd.index("--load-settings") + 1
    assert "/work/output/.profiles/machine.json" in cmd[settings_idx]
    assert "/work/output/.profiles/process.json" in cmd[settings_idx]
    assert str(profile_dir) not in cmd[settings_idx]


def test_slice_via_docker_failure(tmp_path):
    """Verify Docker slicer failure raises RuntimeError."""
    input_3mf = tmp_path / "plate.3mf"
    input_3mf.write_text("fake")
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    profile_dir = output_dir / ".profiles"
    profile_dir.mkdir()

    mock_result = MagicMock(returncode=1, stdout="", stderr="some error")
    with patch("fabprint.slicer.subprocess.run", return_value=mock_result):
        with pytest.raises(RuntimeError, match="Docker slicer failed"):
            _slice_via_docker(
                input_3mf,
                output_dir,
                profile_dir,
                None,
                None,
                "fabprint:latest",
            )


# --- slice_plate integration ---


def _mock_resolve(name, engine, category, project_dir=None):
    """Return fake profile data for testing."""
    return {"name": name, "from": "test"}


def test_slice_plate_local_command(tmp_path):
    """Verify local slicer builds correct CLI command."""
    input_3mf = tmp_path / "plate.3mf"
    input_3mf.write_text("fake")
    output_dir = tmp_path / "output"

    mock_result = MagicMock(returncode=0, stdout="", stderr="")
    slicer_path = Path("/usr/bin/orca-slicer")

    with (
        patch("fabprint.slicer.find_slicer", return_value=slicer_path),
        patch("fabprint.slicer.resolve_profile_data", side_effect=_mock_resolve),
        patch("fabprint.slicer.subprocess.run", return_value=mock_result) as mock_run,
    ):
        slice_plate(
            input_3mf,
            engine="orca",
            output_dir=output_dir,
            printer="My Printer",
            process="My Process",
            filaments=["PLA"],
            overrides={"wall_loops": 4},
        )

    cmd = mock_run.call_args[0][0]
    assert cmd[0] == str(slicer_path)
    assert "--load-settings" in cmd
    assert "--load-filaments" in cmd
    assert "--slice" in cmd
    assert str(output_dir) in cmd


def test_slice_plate_docker_fallback(tmp_path):
    """When local slicer not found, falls back to Docker."""
    input_3mf = tmp_path / "plate.3mf"
    input_3mf.write_text("fake")
    output_dir = tmp_path / "output"

    mock_result = MagicMock(returncode=0, stdout="", stderr="")

    with (
        patch("fabprint.slicer.find_slicer", side_effect=FileNotFoundError("not found")),
        patch("fabprint.slicer._has_docker", return_value=True),
        patch("fabprint.slicer.resolve_profile_data", side_effect=_mock_resolve),
        patch("fabprint.slicer.subprocess.run", return_value=mock_result) as mock_run,
    ):
        slice_plate(
            input_3mf,
            engine="orca",
            output_dir=output_dir,
            printer="My Printer",
        )

    cmd = mock_run.call_args[0][0]
    assert cmd[0] == "docker"
    assert "fabprint:latest" in cmd


def test_slice_plate_docker_explicit(tmp_path):
    """docker=True forces Docker even if local slicer exists."""
    input_3mf = tmp_path / "plate.3mf"
    input_3mf.write_text("fake")
    output_dir = tmp_path / "output"

    mock_result = MagicMock(returncode=0, stdout="", stderr="")

    with (
        patch("fabprint.slicer._has_docker", return_value=True),
        patch("fabprint.slicer.resolve_profile_data", side_effect=_mock_resolve),
        patch("fabprint.slicer.subprocess.run", return_value=mock_result) as mock_run,
    ):
        slice_plate(
            input_3mf,
            engine="orca",
            output_dir=output_dir,
            printer="My Printer",
            docker=True,
        )

    cmd = mock_run.call_args[0][0]
    assert cmd[0] == "docker"
    assert "fabprint:latest" in cmd


def test_slice_plate_docker_version(tmp_path):
    """docker_version selects a versioned image."""
    input_3mf = tmp_path / "plate.3mf"
    input_3mf.write_text("fake")
    output_dir = tmp_path / "output"

    mock_result = MagicMock(returncode=0, stdout="", stderr="")

    with (
        patch("fabprint.slicer._has_docker", return_value=True),
        patch("fabprint.slicer.resolve_profile_data", side_effect=_mock_resolve),
        patch("fabprint.slicer.subprocess.run", return_value=mock_result) as mock_run,
    ):
        slice_plate(
            input_3mf,
            engine="orca",
            output_dir=output_dir,
            printer="My Printer",
            docker_version="2.3.1",
        )

    cmd = mock_run.call_args[0][0]
    assert cmd[0] == "docker"
    assert "fabprint:orca-2.3.1" in cmd


def test_slice_plate_docker_image_missing(tmp_path):
    """Raises FileNotFoundError when Docker image doesn't exist."""
    input_3mf = tmp_path / "plate.3mf"
    input_3mf.write_text("fake")

    with (
        patch("fabprint.slicer._has_docker", return_value=False),
    ):
        with pytest.raises(FileNotFoundError, match="Docker image.*not found"):
            slice_plate(
                input_3mf,
                engine="orca",
                docker_version="9.9.9",
            )


# --- parse_gcode_stats ---


def test_parse_gcode_stats_full(tmp_path):
    gcode = tmp_path / "plate.gcode"
    gcode.write_text(
        "; HEADER_BLOCK_START\n"
        "; generated by OrcaSlicer\n"
        "; estimated printing time (normal mode) = 1h 33m 15s\n"
        "; HEADER_BLOCK_END\n"
        "G28 ; home\n"
        "G1 X10 Y10\n"
        "; filament used [mm] = 14395.62\n"
        "; filament used [cm3] = 34.63\n"
        "; filament used [g] = 42.94\n"
        "; total filament used [g] = 42.94\n"
    )
    stats = parse_gcode_stats(tmp_path)
    assert stats["filament_g"] == 42.94
    assert stats["print_time"] == "1h 33m 15s"


def test_parse_gcode_stats_orca_format(tmp_path):
    """OrcaSlicer 2.3+ format: time in header, cm3 in footer, no grams."""
    gcode = tmp_path / "plate.gcode"
    gcode.write_text(
        "; model printing time: 4m 23s; total estimated time: 10m 52s\n"
        "; filament_density: 0\n"
        "G28 ; home\n"
        "G1 X10 Y10\n"
        "; filament used [mm] = 101.51\n"
        "; filament used [cm3] = 0.24\n"
    )
    stats = parse_gcode_stats(tmp_path)
    assert stats["filament_cm3"] == 0.24
    assert stats["print_time"] == "10m 52s"
    assert "filament_g" not in stats


def test_parse_gcode_stats_empty(tmp_path):
    assert parse_gcode_stats(tmp_path) == {}


def test_parse_gcode_stats_no_metadata(tmp_path):
    gcode = tmp_path / "plate.gcode"
    gcode.write_text("G28 ; home\nG1 X10 Y10\n")
    assert parse_gcode_stats(tmp_path) == {}
