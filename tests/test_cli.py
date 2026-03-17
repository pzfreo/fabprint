"""Tests for CLI entry point."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from packaging.version import Version

from fabprint.cli import main
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
    assert (tmp_path / "output" / "plate.3mf").exists()


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
    assert (tmp_path / "output" / "plate.3mf").exists()


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
