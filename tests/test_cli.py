"""Tests for CLI entry point."""

from pathlib import Path

import pytest

from fabprint.cli import main

FIXTURES = Path(__file__).parent / "fixtures"


def _write_config(tmp_path: Path, engine: str = "orca") -> Path:
    toml = tmp_path / "fabprint.toml"
    toml.write_text(f"""
[plate]
size = [256, 256]
padding = 5.0

[slicer]
engine = "{engine}"

[[parts]]
file = "{FIXTURES / 'cube_10mm.stl'}"
copies = 2
orient = "flat"

[[parts]]
file = "{FIXTURES / 'cylinder_5x20mm.stl'}"
orient = "upright"
""")
    return toml


def test_plate_subcommand(tmp_path):
    config = _write_config(tmp_path)
    output = tmp_path / "out.3mf"
    main(["plate", str(config), "-o", str(output)])
    assert output.exists()
    assert output.stat().st_size > 0


def test_plate_default_output(tmp_path, monkeypatch):
    config = _write_config(tmp_path)
    monkeypatch.chdir(tmp_path)
    main(["plate", str(config)])
    assert (tmp_path / "plate.3mf").exists()


def test_plate_verbose(tmp_path):
    config = _write_config(tmp_path)
    output = tmp_path / "out.3mf"
    main(["plate", str(config), "-o", str(output), "-v"])
    assert output.exists()


def test_no_subcommand():
    with pytest.raises(SystemExit) as exc_info:
        main([])
    assert exc_info.value.code == 1


def test_profiles_list():
    """Profiles list should run without error."""
    # Just verify it doesn't crash — output goes to stdout
    main(["profiles", "list", "--engine", "orca", "--category", "machine"])


def test_profiles_pin(tmp_path):
    config = tmp_path / "fabprint.toml"
    config.write_text(f"""
[slicer]
engine = "orca"
printer = "Bambu Lab P1S 0.4 nozzle"
process = "0.20mm Standard @BBL X1C"
filaments = ["Generic PLA @base"]

[[parts]]
file = "{FIXTURES / 'cube_10mm.stl'}"
""")
    main(["profiles", "pin", str(config)])
    assert (tmp_path / "profiles" / "machine" / "Bambu Lab P1S 0.4 nozzle.json").exists()
    assert (tmp_path / "profiles" / "process" / "0.20mm Standard @BBL X1C.json").exists()
    assert (tmp_path / "profiles" / "filament" / "Generic PLA @base.json").exists()
