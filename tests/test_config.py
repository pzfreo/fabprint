"""Tests for config loading and validation."""

from pathlib import Path

import pytest

from fabprint.config import load_config

FIXTURES = Path(__file__).parent / "fixtures"


def _write_toml(tmp_path: Path, content: str, create_files: list[str] | None = None) -> Path:
    """Write a toml file and optionally create referenced part files."""
    toml_path = tmp_path / "fabprint.toml"
    toml_path.write_text(content)
    for f in create_files or []:
        (tmp_path / f).touch()
    return toml_path


def test_valid_config(tmp_path):
    path = _write_toml(tmp_path, """
[plate]
size = [200, 200]
padding = 3.0

[slicer]
engine = "orca"
printer = "Bambu Lab P1S 0.4 nozzle"
process = "0.20mm Standard @BBL X1C"
filaments = ["Generic PLA @base"]

[[parts]]
file = "cube.stl"
copies = 2
orient = "flat"
filament = 1

[[parts]]
file = "cyl.stl"
orient = "upright"
filament = 2
""", create_files=["cube.stl", "cyl.stl"])

    cfg = load_config(path)
    assert cfg.plate.size == (200, 200)
    assert cfg.plate.padding == 3.0
    assert cfg.slicer.engine == "orca"
    assert cfg.slicer.printer == "Bambu Lab P1S 0.4 nozzle"
    assert cfg.slicer.process == "0.20mm Standard @BBL X1C"
    assert cfg.slicer.filaments == ["Generic PLA @base"]
    assert len(cfg.parts) == 2
    assert cfg.parts[0].copies == 2
    assert cfg.parts[0].orient == "flat"
    assert cfg.parts[0].filament == 1
    assert cfg.parts[1].orient == "upright"
    assert cfg.parts[1].filament == 2


def test_defaults(tmp_path):
    path = _write_toml(tmp_path, """
[[parts]]
file = "cube.stl"
""", create_files=["cube.stl"])

    cfg = load_config(path)
    assert cfg.plate.size == (256.0, 256.0)
    assert cfg.plate.padding == 5.0
    assert cfg.slicer.engine == "bambu"
    assert cfg.parts[0].copies == 1
    assert cfg.parts[0].orient == "flat"


def test_missing_parts(tmp_path):
    path = _write_toml(tmp_path, """
[plate]
size = [200, 200]
""")
    with pytest.raises(ValueError, match="At least one"):
        load_config(path)


def test_bad_orient(tmp_path):
    path = _write_toml(tmp_path, """
[[parts]]
file = "cube.stl"
orient = "diagonal"
""", create_files=["cube.stl"])
    with pytest.raises(ValueError, match="orient"):
        load_config(path)


def test_bad_plate_size(tmp_path):
    path = _write_toml(tmp_path, """
[plate]
size = [-1, 200]

[[parts]]
file = "cube.stl"
""", create_files=["cube.stl"])
    with pytest.raises(ValueError, match="plate.size"):
        load_config(path)


def test_missing_file(tmp_path):
    path = _write_toml(tmp_path, """
[[parts]]
file = "nonexistent.stl"
""")
    with pytest.raises(FileNotFoundError, match="nonexistent.stl"):
        load_config(path)


def test_filament_defaults_to_1(tmp_path):
    path = _write_toml(tmp_path, """
[[parts]]
file = "cube.stl"
""", create_files=["cube.stl"])
    cfg = load_config(path)
    assert cfg.parts[0].filament == 1


def test_bad_filament(tmp_path):
    path = _write_toml(tmp_path, """
[[parts]]
file = "cube.stl"
filament = 0
""", create_files=["cube.stl"])
    with pytest.raises(ValueError, match="filament"):
        load_config(path)


def test_bad_copies(tmp_path):
    path = _write_toml(tmp_path, """
[[parts]]
file = "cube.stl"
copies = 0
""", create_files=["cube.stl"])
    with pytest.raises(ValueError, match="copies"):
        load_config(path)


def test_bad_engine(tmp_path):
    path = _write_toml(tmp_path, """
[slicer]
engine = "cura"

[[parts]]
file = "cube.stl"
""", create_files=["cube.stl"])
    with pytest.raises(ValueError, match="engine"):
        load_config(path)


def test_scale(tmp_path):
    path = _write_toml(tmp_path, """
[[parts]]
file = "cube.stl"
scale = 2.0
""", create_files=["cube.stl"])
    cfg = load_config(path)
    assert cfg.parts[0].scale == 2.0


def test_scale_default(tmp_path):
    path = _write_toml(tmp_path, """
[[parts]]
file = "cube.stl"
""", create_files=["cube.stl"])
    cfg = load_config(path)
    assert cfg.parts[0].scale == 1.0


def test_bad_scale(tmp_path):
    path = _write_toml(tmp_path, """
[[parts]]
file = "cube.stl"
scale = 0
""", create_files=["cube.stl"])
    with pytest.raises(ValueError, match="scale"):
        load_config(path)


def test_overrides(tmp_path):
    path = _write_toml(tmp_path, """
[slicer]
engine = "orca"

[slicer.overrides]
sparse_infill_density = "25%"
wall_loops = 3

[[parts]]
file = "cube.stl"
""", create_files=["cube.stl"])
    cfg = load_config(path)
    assert cfg.slicer.overrides == {
        "sparse_infill_density": "25%",
        "wall_loops": 3,
    }


def test_overrides_default_empty(tmp_path):
    path = _write_toml(tmp_path, """
[[parts]]
file = "cube.stl"
""", create_files=["cube.stl"])
    cfg = load_config(path)
    assert cfg.slicer.overrides == {}
