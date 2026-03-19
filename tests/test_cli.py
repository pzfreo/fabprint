"""Tests for CLI entry point."""

import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from packaging.version import Version

from fabprint import FabprintError, __version__
from fabprint.cli import (
    _query_printer_status,
    _render_printer,
    _resolve_config_path,
    _resolve_status_printers,
    _version_callback,
    main,
)
from fabprint.profiles import SYSTEM_DIRS

FIXTURES = Path(__file__).parent / "fixtures"

_orca_system = SYSTEM_DIRS.get("orca")
_has_orca = _orca_system is not None and _orca_system.is_dir()


def _docker_orca_version() -> str | None:
    """Return the OrcaSlicer Docker image version to test with.

    Uses FABPRINT_TEST_ORCA_VERSION env var if set, otherwise auto-detects
    the first available fabprint:orca-* image.
    """
    env_ver = os.environ.get("FABPRINT_TEST_ORCA_VERSION")
    if env_ver:
        return env_ver
    try:
        r = subprocess.run(
            ["docker", "image", "ls", "--format", "{{.Repository}}:{{.Tag}}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        versions = []
        for line in r.stdout.splitlines():
            if line.startswith("fabprint:orca-"):
                versions.append(line.split("fabprint:orca-", 1)[1])
        # Prefer stable releases over pre-releases, sorted by version number
        stable = [v for v in versions if not Version(v).is_prerelease]
        if stable:
            return max(stable, key=Version)
        if versions:
            return max(versions, key=Version)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


_docker_version = _docker_orca_version()
skip_no_docker = pytest.mark.skipif(
    _docker_version is None, reason="Docker not running or no fabprint:orca-* image"
)


def _posix(p: Path) -> str:
    """Return a forward-slash path string (avoids TOML backslash escaping on Windows)."""
    return p.as_posix()


def _write_config(tmp_path: Path, engine: str = "orca") -> Path:
    toml = tmp_path / "fabprint.toml"
    toml.write_text(f"""
[plate]
size = [256, 256]
padding = 5.0

[slicer]
engine = "{engine}"

[[parts]]
file = "{_posix(FIXTURES / "cube_10mm.stl")}"
copies = 2
orient = "flat"

[[parts]]
file = "{_posix(FIXTURES / "cylinder_5x20mm.stl")}"
orient = "upright"
""")
    return toml


def test_run_until_plate(tmp_path):
    config = _write_config(tmp_path)
    output_dir = tmp_path / "output"
    main(["run", str(config), "-o", str(output_dir), "--until", "plate"])
    assert (output_dir / "plate.3mf").exists()
    assert (output_dir / "plate.3mf").stat().st_size > 0


def test_run_default_output(tmp_path, monkeypatch):
    config = _write_config(tmp_path)
    monkeypatch.chdir(tmp_path)
    main(["run", str(config), "--until", "plate"])
    assert (tmp_path / "fabprint_output" / "plate.3mf").exists()


def test_run_verbose(tmp_path):
    config = _write_config(tmp_path)
    output_dir = tmp_path / "output"
    main(["run", str(config), "-o", str(output_dir), "--until", "plate", "-v"])
    assert (output_dir / "plate.3mf").exists()


def test_no_subcommand():
    with pytest.raises(SystemExit) as exc_info:
        main([])
    assert exc_info.value.code == 1


def test_run_auto_discover_config(tmp_path, monkeypatch):
    """Running without config arg should find ./fabprint.toml."""
    _write_config(tmp_path)
    monkeypatch.chdir(tmp_path)
    main(["run", "--until", "plate"])
    assert (tmp_path / "fabprint_output" / "plate.3mf").exists()


def test_run_missing_config_no_traceback(tmp_path, monkeypatch, capsys):
    """Missing config should print a clean error, not a traceback."""
    monkeypatch.chdir(tmp_path)  # no fabprint.toml here
    with pytest.raises(SystemExit) as exc_info:
        main(["run"])
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "error:" in captured.err
    assert "fabprint.toml" in captured.err


def test_run_until_and_only_conflict(tmp_path, capsys):
    config = _write_config(tmp_path)
    with pytest.raises(SystemExit) as exc_info:
        main(["run", str(config), "--until", "plate", "--only", "slice"])
    assert exc_info.value.code == 1
    assert "Cannot use both" in capsys.readouterr().err


def test_run_only_plate(tmp_path):
    """--only plate should work without any pre-existing artifacts."""
    config = _write_config(tmp_path)
    output_dir = tmp_path / "output"
    main(["run", str(config), "-o", str(output_dir), "--only", "plate"])
    assert (output_dir / "plate.3mf").exists()


def test_run_only_slice_fails_without_plate(tmp_path, capsys):
    """--only slice should fail when plate.3mf doesn't exist."""
    config = _write_config(tmp_path)
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    with pytest.raises(SystemExit) as exc_info:
        main(["run", str(config), "-o", str(output_dir), "--only", "slice"])
    assert exc_info.value.code == 1
    assert "plate 3MF file" in capsys.readouterr().err


def test_run_custom_stages(tmp_path):
    """Config with custom pipeline stages should be respected."""
    toml = tmp_path / "fabprint.toml"
    toml.write_text(f"""
[plate]
size = [256, 256]
padding = 5.0

[slicer]
engine = "orca"

[pipeline]
stages = ["load", "arrange", "plate"]

[[parts]]
file = "{_posix(FIXTURES / "cube_10mm.stl")}"
""")
    output_dir = tmp_path / "output"
    # Running all stages (only up to plate since that's all that's configured)
    main(["run", str(toml), "-o", str(output_dir)])
    assert (output_dir / "plate.3mf").exists()


def test_run_name_output_dir(tmp_path, monkeypatch):
    """Project name should create a subdirectory under fabprint_output/."""
    toml = tmp_path / "fabprint.toml"
    toml.write_text(f"""
name = "benchy"

[plate]
size = [256, 256]

[slicer]
engine = "orca"

[[parts]]
file = "{_posix(FIXTURES / "cube_10mm.stl")}"
""")
    monkeypatch.chdir(tmp_path)
    main(["run", str(toml), "--until", "plate"])
    assert (tmp_path / "fabprint_output" / "benchy" / "plate.3mf").exists()


def test_run_name_explicit_output_dir(tmp_path):
    """Explicit -o should override name-based default."""
    toml = tmp_path / "fabprint.toml"
    toml.write_text(f"""
name = "benchy"

[plate]
size = [256, 256]

[slicer]
engine = "orca"

[[parts]]
file = "{_posix(FIXTURES / "cube_10mm.stl")}"
""")
    output_dir = tmp_path / "custom"
    main(["run", str(toml), "-o", str(output_dir), "--until", "plate"])
    assert (output_dir / "plate.3mf").exists()


def test_run_invalid_stage_in_config(tmp_path):
    """Unknown stage in config should raise at parse time."""
    toml = tmp_path / "fabprint.toml"
    toml.write_text(f"""
[plate]
size = [256, 256]

[slicer]
engine = "orca"

[pipeline]
stages = ["load", "arrange", "foobar"]

[[parts]]
file = "{_posix(FIXTURES / "cube_10mm.stl")}"
""")
    with pytest.raises(SystemExit) as exc_info:
        main(["run", str(toml)])
    assert exc_info.value.code == 1


def test_profiles_list(capsys):
    """Profiles list should run and produce output."""
    main(["profiles", "list", "--engine", "orca", "--category", "machine"])
    captured = capsys.readouterr()
    assert "machine" in captured.out.lower()


@pytest.mark.skipif(not _has_orca, reason="OrcaSlicer not installed")
def test_profiles_pin(tmp_path):
    config = tmp_path / "fabprint.toml"
    config.write_text(f"""
[slicer]
engine = "orca"
printer = "Bambu Lab P1S 0.4 nozzle"
process = "0.20mm Standard @BBL X1C"
filaments = ["Generic PLA @base"]

[[parts]]
file = "{_posix(FIXTURES / "cube_10mm.stl")}"
""")
    main(["profiles", "pin", str(config)])
    assert (tmp_path / "profiles" / "machine" / "Bambu Lab P1S 0.4 nozzle.json").exists()
    assert (tmp_path / "profiles" / "process" / "0.20mm Standard @BBL X1C.json").exists()
    assert (tmp_path / "profiles" / "filament" / "Generic PLA @base.json").exists()


# --- Docker-based slicing integration tests ---


def _docker_work_dir(suffix: str = "") -> Path:
    """Return a work directory suitable for Docker volume mounts."""
    work_dir = Path.home() / ".cache" / f"fabprint-test{suffix}"
    work_dir.mkdir(parents=True, exist_ok=True)
    return work_dir


def _write_slice_config(
    tmp_path: Path,
    filaments: list[str] | None = None,
    version: str | None = None,
) -> Path:
    """Write a fabprint.toml for slicing tests."""
    filaments = filaments or ["Generic PLA @base"]
    filament_toml = ", ".join(f'"{f}"' for f in filaments)
    version_line = f'version = "{version}"' if version else ""
    toml = tmp_path / "fabprint.toml"
    toml.write_text(f"""
[plate]
size = [256, 256]
padding = 5.0

[slicer]
engine = "orca"
{version_line}
printer = "Bambu Lab P1S 0.4 nozzle"
process = "0.20mm Standard @BBL X1C"
filaments = [{filament_toml}]

[[parts]]
file = "{_posix(FIXTURES / "cube_10mm.stl")}"
copies = 1
""")
    return toml


@skip_no_docker
def test_slice_docker(monkeypatch):
    """Slice a single cube via Docker using version from config."""
    work_dir = _docker_work_dir()
    config = _write_slice_config(work_dir, version=_docker_version)
    output_dir = work_dir / "output"
    monkeypatch.chdir(work_dir)
    try:
        main(
            [
                "run",
                str(config),
                "-o",
                str(output_dir),
                "--until",
                "slice",
            ]
        )
        gcode_files = list(output_dir.glob("*.gcode"))
        assert len(gcode_files) >= 1, "Expected at least one gcode file"
        assert gcode_files[0].stat().st_size > 0
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


@skip_no_docker
def test_slice_docker_filament_override(monkeypatch):
    """--filament-type overrides config filaments with a single filament."""
    work_dir = _docker_work_dir("-override")
    config = _write_slice_config(
        work_dir,
        filaments=["Generic PLA @base", "Generic PLA @base"],
        version=_docker_version,
    )
    output_dir = work_dir / "output"
    monkeypatch.chdir(work_dir)
    try:
        main(
            [
                "run",
                str(config),
                "-o",
                str(output_dir),
                "--until",
                "slice",
                "--filament-type",
                "Generic PLA @base",
                "--filament-slot",
                "1",
            ]
        )
        gcode_files = list(output_dir.glob("*.gcode"))
        assert len(gcode_files) >= 1, "Expected at least one gcode file"
        assert gcode_files[0].stat().st_size > 0
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


@skip_no_docker
def test_slice_docker_version_mismatch(monkeypatch):
    """Config version that doesn't match any Docker image should fail."""
    work_dir = _docker_work_dir("-mismatch")
    config = _write_slice_config(work_dir, version="99.99.99")
    monkeypatch.chdir(work_dir)
    try:
        with pytest.raises(SystemExit) as exc_info:
            main(
                [
                    "run",
                    str(config),
                    "-o",
                    str(work_dir / "output"),
                    "--until",
                    "slice",
                ]
            )
        assert exc_info.value.code == 1
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Unit tests for CLI helpers and commands (mocked, no I/O)
# ---------------------------------------------------------------------------


# --- _version_callback ---


def test_version_callback_prints_version(capsys):
    """_version_callback should print version and raise typer.Exit."""
    import typer

    with pytest.raises(typer.Exit):
        _version_callback(True)
    assert __version__ in capsys.readouterr().out


def test_version_callback_noop_when_false():
    """_version_callback should do nothing when value is False."""
    _version_callback(False)  # should not raise


def test_version_flag_via_main(capsys):
    """--version flag via main() should print version and exit cleanly."""
    main(["--version"])
    assert __version__ in capsys.readouterr().out


# --- _resolve_config_path ---


def test_resolve_config_path_explicit(tmp_path):
    """Explicit path is returned as-is."""
    p = tmp_path / "custom.toml"
    assert _resolve_config_path(p) == p


def test_resolve_config_path_fallback(tmp_path, monkeypatch):
    """Falls back to ./fabprint.toml when it exists."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "fabprint.toml").write_text("[slicer]\n")
    result = _resolve_config_path(None)
    assert result == Path("fabprint.toml")


def test_resolve_config_path_missing(tmp_path, monkeypatch):
    """Raises FabprintError when no config and no fabprint.toml."""
    monkeypatch.chdir(tmp_path)
    with pytest.raises(FabprintError, match="No config file specified"):
        _resolve_config_path(None)


# --- _resolve_status_printers ---


def test_resolve_status_printers_by_name():
    load_fn = MagicMock(return_value={"type": "bambu-lan", "ip": "1.2.3.4"})
    result = _resolve_status_printers("my-printer", None, MagicMock(), load_fn)
    assert result == [("my-printer", {"type": "bambu-lan", "ip": "1.2.3.4"})]
    load_fn.assert_called_once_with("my-printer")


def test_resolve_status_printers_by_serial():
    result = _resolve_status_printers(None, "ABC123", MagicMock(), MagicMock())
    assert result == [("ABC123", {"type": "bambu-cloud", "serial": "ABC123"})]


def test_resolve_status_printers_all():
    list_fn = MagicMock(return_value={"p1": {"type": "bambu-lan"}, "p2": {"type": "moonraker"}})
    result = _resolve_status_printers(None, None, list_fn, MagicMock())
    assert len(result) == 2
    assert result[0][0] == "p1"
    assert result[1][0] == "p2"


def test_resolve_status_printers_none_configured():
    list_fn = MagicMock(return_value={})
    with pytest.raises(FabprintError, match="No printers configured"):
        _resolve_status_printers(None, None, list_fn, MagicMock())


# --- _query_printer_status ---


@patch("fabprint.cli.cloud_status", create=True)
def test_query_printer_status_bambu_cloud(mock_cs):
    """bambu-cloud dispatches to cloud_status with token."""
    with (
        patch("fabprint.cloud.cloud_status", return_value={"gcode_state": "IDLE"}) as m_cs,
        patch("fabprint.credentials.cloud_token_json") as m_tok,
    ):
        # Set up the context manager mock
        m_tok.return_value.__enter__ = MagicMock(return_value=Path("/tmp/token.json"))
        m_tok.return_value.__exit__ = MagicMock(return_value=False)
        result = _query_printer_status("test", {"type": "bambu-cloud", "serial": "SN123"})
        assert result == {"gcode_state": "IDLE"}
        m_cs.assert_called_once_with("SN123", Path("/tmp/token.json"))


def test_query_printer_status_bambu_cloud_no_serial():
    with pytest.raises(ValueError, match="no serial"):
        _query_printer_status("test", {"type": "bambu-cloud"})


@patch("fabprint.printer.get_lan_status", return_value={"gcode_state": "RUNNING"})
def test_query_printer_status_bambu_lan(mock_lan):
    creds = {"type": "bambu-lan", "ip": "10.0.0.1", "access_code": "abc", "serial": "SN1"}
    result = _query_printer_status("test", creds)
    assert result == {"gcode_state": "RUNNING"}
    mock_lan.assert_called_once_with("10.0.0.1", "abc", "SN1")


def test_query_printer_status_bambu_lan_missing_fields():
    with pytest.raises(ValueError, match="requires ip, access_code, serial"):
        _query_printer_status("test", {"type": "bambu-lan", "ip": "10.0.0.1"})


@patch("fabprint.printer.get_moonraker_status", return_value={"gcode_state": "IDLE"})
def test_query_printer_status_moonraker(mock_mr):
    result = _query_printer_status("test", {"type": "moonraker", "url": "http://k1:7125"})
    assert result == {"gcode_state": "IDLE"}
    mock_mr.assert_called_once_with("http://k1:7125", None)


@patch("fabprint.printer.get_moonraker_status", return_value={"gcode_state": "IDLE"})
def test_query_printer_status_moonraker_with_key(mock_mr):
    creds = {"type": "moonraker", "url": "http://k1:7125", "api_key": "secret"}
    _query_printer_status("test", creds)
    mock_mr.assert_called_once_with("http://k1:7125", "secret")


def test_query_printer_status_moonraker_no_url():
    with pytest.raises(ValueError, match="requires url"):
        _query_printer_status("test", {"type": "moonraker"})


def test_query_printer_status_unknown_type():
    with pytest.raises(ValueError, match="Unknown printer type"):
        _query_printer_status("test", {"type": "foobar"})


# --- _render_printer ---


@patch("fabprint.cloud.parse_ams_trays", return_value=[])
def test_render_printer_idle(mock_ams):
    status = {"gcode_state": "IDLE", "nozzle_temper": 25.0, "bed_temper": 22.0}
    lines = _render_printer(status, "p1", "SN1")
    text = "\n".join(lines)
    assert "IDLE" in text
    assert "25" in text
    assert "22" in text
    # Should NOT have progress bar for IDLE
    assert "Progress" not in text


@patch("fabprint.cloud.parse_ams_trays", return_value=[])
def test_render_printer_printing_with_progress(mock_ams):
    status = {
        "gcode_state": "RUNNING",
        "subtask_name": "my_model",
        "mc_percent": 50,
        "layer_num": 10,
        "total_layer_num": 20,
        "mc_remaining_time": 30,
        "mc_print_stage": "0",
        "nozzle_temper": 210.0,
        "nozzle_target_temper": 220.0,
        "bed_temper": 55.0,
        "bed_target_temper": 60.0,
    }
    lines = _render_printer(status, "p1", "SN1")
    text = "\n".join(lines)
    assert "RUNNING" in text
    assert "my_model" in text
    assert "50%" in text
    assert "layer 10/20" in text
    assert "210" in text
    assert "220" in text
    assert "55" in text
    assert "60" in text
    assert "Progress" in text
    assert "ETA" in text


@patch("fabprint.cloud.parse_ams_trays", return_value=[])
def test_render_printer_no_target_temps(mock_ams):
    """When target temps are 0, no arrow should appear."""
    status = {
        "gcode_state": "IDLE",
        "nozzle_temper": 25.0,
        "nozzle_target_temper": 0,
        "bed_temper": 22.0,
        "bed_target_temper": 0,
    }
    lines = _render_printer(status, "p1", "SN1")
    text = "\n".join(lines)
    assert "\u2192" not in text


@patch("fabprint.cloud.parse_ams_trays")
def test_render_printer_with_ams(mock_ams):
    mock_ams.return_value = [
        {"phys_slot": 0, "type": "PLA", "color": "FF0000"},
        {"phys_slot": 1, "type": "PETG", "color": "00FF00"},
    ]
    status = {
        "gcode_state": "RUNNING",
        "mc_percent": 10,
        "mc_print_stage": "0",
        "layer_num": 1,
        "total_layer_num": 100,
        "nozzle_temper": 200.0,
        "bed_temper": 60.0,
        "ams": {"tray_now": 0},
    }
    lines = _render_printer(status, "p1", "SN1")
    text = "\n".join(lines)
    assert "AMS:" in text
    assert "PLA" in text
    assert "PETG" in text
    assert "<-- printing" in text


@patch("fabprint.cloud.parse_ams_trays", return_value=[])
def test_render_printer_stage_lookup(mock_ams):
    """Non-printing stage should show human-readable label."""
    status = {
        "gcode_state": "RUNNING",
        "mc_percent": 0,
        "mc_print_stage": "2",
        "layer_num": 0,
        "total_layer_num": 100,
        "nozzle_temper": 100.0,
        "bed_temper": 40.0,
    }
    lines = _render_printer(status, "p1", "SN1")
    text = "\n".join(lines)
    assert "heatbed preheating" in text


# --- validate command ---


@patch("fabprint.init.validate_config")
@patch("fabprint.cli.load_config")
def test_validate_no_warnings(mock_load, mock_validate, tmp_path, capsys):
    """validate with no warnings should print 'All checks passed'."""
    from fabprint.init import ValidationResult

    config = tmp_path / "fabprint.toml"
    config.write_text("[slicer]\n")
    mock_validate.return_value = ValidationResult(
        passes=["Slicer version pinned: 2.3.1"],
        warnings=[],
    )
    main(["validate", str(config)])
    out = capsys.readouterr().out
    assert "All checks passed" in out


@patch("fabprint.init.validate_config")
@patch("fabprint.cli.load_config")
def test_validate_with_warnings(mock_load, mock_validate, tmp_path, capsys):
    """validate with warnings should show warning count."""
    from fabprint.init import ValidationResult

    config = tmp_path / "fabprint.toml"
    config.write_text("[slicer]\n")
    mock_validate.return_value = ValidationResult(
        passes=["OK thing"],
        warnings=["slicer.version is not set", "some other issue"],
    )
    main(["validate", str(config)])
    out = capsys.readouterr().out
    assert "2" in out
    assert "warning" in out


# --- main() error handling ---


def test_main_fabprint_error(capsys):
    """FabprintError should print clean error and exit 1."""
    with patch("fabprint.cli.app", side_effect=FabprintError("boom")):
        with pytest.raises(SystemExit) as exc_info:
            main(["run"])
        assert exc_info.value.code == 1
    assert "boom" in capsys.readouterr().err


def test_main_value_error(capsys):
    """ValueError should print clean error and exit 1."""
    with patch("fabprint.cli.app", side_effect=ValueError("bad value")):
        with pytest.raises(SystemExit) as exc_info:
            main(["run"])
        assert exc_info.value.code == 1
    assert "bad value" in capsys.readouterr().err


def test_main_file_not_found_error(capsys):
    """FileNotFoundError should print clean error and exit 1."""
    with patch("fabprint.cli.app", side_effect=FileNotFoundError("no such file")):
        with pytest.raises(SystemExit) as exc_info:
            main(["run"])
        assert exc_info.value.code == 1
    assert "no such file" in capsys.readouterr().err


def test_main_system_exit_with_code():
    """SystemExit with non-zero code should propagate."""
    with patch("fabprint.cli.app", side_effect=SystemExit(42)):
        with pytest.raises(SystemExit) as exc_info:
            main(["run"])
        assert exc_info.value.code == 42


def test_main_system_exit_zero():
    """SystemExit(0) should not re-raise."""
    with patch("fabprint.cli.app", side_effect=SystemExit(0)):
        main(["run"])  # should not raise


def test_main_keyboard_interrupt():
    """KeyboardInterrupt should exit 130."""
    with patch("fabprint.cli.app", side_effect=KeyboardInterrupt()):
        with pytest.raises(SystemExit) as exc_info:
            main(["run"])
        assert exc_info.value.code == 130


# --- profiles list ---


@patch("fabprint.profiles.discover_profiles")
def test_profiles_list_command(mock_discover, capsys):
    """profiles list should show category and profile names."""
    mock_discover.return_value = {
        "machine": {"Bambu P1S": Path("/fake")},
        "process": {},
        "filament": {},
    }
    main(["profiles", "list", "--engine", "orca", "--category", "machine"])
    out = capsys.readouterr().out
    assert "machine" in out
    assert "Bambu P1S" in out


# --- profiles pin ---


@patch("fabprint.profiles.pin_profiles", return_value=[Path("/a/b.json"), Path("/c/d.json")])
@patch("fabprint.cli.load_config")
def test_profiles_pin_command(mock_load, mock_pin, tmp_path, capsys):
    """profiles pin should call pin_profiles and report count."""
    config = tmp_path / "fabprint.toml"
    config.write_text("[slicer]\n")

    # Create a mock config object with necessary attributes
    mock_cfg = MagicMock()
    mock_cfg.slicer.engine = "orca"
    mock_cfg.slicer.printer = "Bambu Lab P1S 0.4 nozzle"
    mock_cfg.slicer.process = "0.20mm Standard @BBL X1C"
    mock_cfg.slicer.filaments = ["Generic PLA @base"]
    mock_cfg.slicer.version = None
    mock_cfg.base_dir = tmp_path
    mock_load.return_value = mock_cfg

    main(["profiles", "pin", str(config)])
    out = capsys.readouterr().out
    assert "Pinned 2 profile(s)" in out
    mock_pin.assert_called_once()
