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
    path = _write_toml(
        tmp_path,
        """
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
""",
        create_files=["cube.stl", "cyl.stl"],
    )

    cfg = load_config(path)
    assert cfg.plate.size == (200, 200)
    assert cfg.plate.padding == 3.0
    assert cfg.slicer.engine == "orca"
    assert cfg.slicer.printer == "Bambu Lab P1S 0.4 nozzle"
    assert cfg.slicer.process == "0.20mm Standard @BBL X1C"
    assert cfg.slicer.filaments == ["Generic PLA @base"]
    assert cfg.slicer.version is None
    assert len(cfg.parts) == 2
    assert cfg.parts[0].copies == 2
    assert cfg.parts[0].orient == "flat"
    assert cfg.parts[0].filament == 1
    assert cfg.parts[1].orient == "upright"
    assert cfg.parts[1].filament == 2


def test_defaults(tmp_path):
    path = _write_toml(
        tmp_path,
        """
[[parts]]
file = "cube.stl"
""",
        create_files=["cube.stl"],
    )

    cfg = load_config(path)
    assert cfg.plate.size == (256.0, 256.0)
    assert cfg.plate.padding == 5.0
    assert cfg.slicer.engine == "bambu"
    assert cfg.parts[0].copies == 1
    assert cfg.parts[0].orient == "flat"


def test_missing_parts(tmp_path):
    path = _write_toml(
        tmp_path,
        """
[plate]
size = [200, 200]
""",
    )
    with pytest.raises(ValueError, match="At least one"):
        load_config(path)


def test_bad_orient(tmp_path):
    path = _write_toml(
        tmp_path,
        """
[[parts]]
file = "cube.stl"
orient = "diagonal"
""",
        create_files=["cube.stl"],
    )
    with pytest.raises(ValueError, match="orient"):
        load_config(path)


def test_bad_plate_size(tmp_path):
    path = _write_toml(
        tmp_path,
        """
[plate]
size = [-1, 200]

[[parts]]
file = "cube.stl"
""",
        create_files=["cube.stl"],
    )
    with pytest.raises(ValueError, match="plate.size"):
        load_config(path)


def test_missing_file(tmp_path):
    path = _write_toml(
        tmp_path,
        """
[[parts]]
file = "nonexistent.stl"
""",
    )
    with pytest.raises(FileNotFoundError, match="nonexistent.stl"):
        load_config(path)


def test_filament_defaults_to_1(tmp_path):
    path = _write_toml(
        tmp_path,
        """
[[parts]]
file = "cube.stl"
""",
        create_files=["cube.stl"],
    )
    cfg = load_config(path)
    assert cfg.parts[0].filament == 1


def test_bad_filament(tmp_path):
    path = _write_toml(
        tmp_path,
        """
[[parts]]
file = "cube.stl"
filament = 0
""",
        create_files=["cube.stl"],
    )
    with pytest.raises(ValueError, match="filament"):
        load_config(path)


def test_bad_copies(tmp_path):
    path = _write_toml(
        tmp_path,
        """
[[parts]]
file = "cube.stl"
copies = 0
""",
        create_files=["cube.stl"],
    )
    with pytest.raises(ValueError, match="copies"):
        load_config(path)


def test_bad_engine(tmp_path):
    path = _write_toml(
        tmp_path,
        """
[slicer]
engine = "cura"

[[parts]]
file = "cube.stl"
""",
        create_files=["cube.stl"],
    )
    with pytest.raises(ValueError, match="engine"):
        load_config(path)


def test_scale(tmp_path):
    path = _write_toml(
        tmp_path,
        """
[[parts]]
file = "cube.stl"
scale = 2.0
""",
        create_files=["cube.stl"],
    )
    cfg = load_config(path)
    assert cfg.parts[0].scale == 2.0


def test_scale_default(tmp_path):
    path = _write_toml(
        tmp_path,
        """
[[parts]]
file = "cube.stl"
""",
        create_files=["cube.stl"],
    )
    cfg = load_config(path)
    assert cfg.parts[0].scale == 1.0


def test_bad_scale(tmp_path):
    path = _write_toml(
        tmp_path,
        """
[[parts]]
file = "cube.stl"
scale = 0
""",
        create_files=["cube.stl"],
    )
    with pytest.raises(ValueError, match="scale"):
        load_config(path)


def test_overrides(tmp_path):
    path = _write_toml(
        tmp_path,
        """
[slicer]
engine = "orca"

[slicer.overrides]
sparse_infill_density = "25%"
wall_loops = 3

[[parts]]
file = "cube.stl"
""",
        create_files=["cube.stl"],
    )
    cfg = load_config(path)
    assert cfg.slicer.overrides == {
        "sparse_infill_density": "25%",
        "wall_loops": 3,
    }


def test_overrides_default_empty(tmp_path):
    path = _write_toml(
        tmp_path,
        """
[[parts]]
file = "cube.stl"
""",
        create_files=["cube.stl"],
    )
    cfg = load_config(path)
    assert cfg.slicer.overrides == {}


def test_version(tmp_path):
    path = _write_toml(
        tmp_path,
        """
[slicer]
engine = "orca"
version = "2.3.1"

[[parts]]
file = "cube.stl"
""",
        create_files=["cube.stl"],
    )
    cfg = load_config(path)
    assert cfg.slicer.version == "2.3.1"


def test_printer_config(tmp_path):
    path = _write_toml(
        tmp_path,
        """
[printer]
mode = "lan"
ip = "192.168.1.100"
access_code = "12345678"
serial = "01P00A000000"

[[parts]]
file = "cube.stl"
""",
        create_files=["cube.stl"],
    )
    cfg = load_config(path)
    assert cfg.printer is not None
    assert cfg.printer.mode == "lan"
    assert cfg.printer.ip == "192.168.1.100"
    assert cfg.printer.access_code == "12345678"
    assert cfg.printer.serial == "01P00A000000"


def test_printer_config_cloud(tmp_path):
    path = _write_toml(
        tmp_path,
        """
[printer]
mode = "cloud"

[[parts]]
file = "cube.stl"
""",
        create_files=["cube.stl"],
    )
    cfg = load_config(path)
    assert cfg.printer is not None
    assert cfg.printer.mode == "cloud"
    assert cfg.printer.ip is None


def test_printer_config_absent(tmp_path):
    path = _write_toml(
        tmp_path,
        """
[[parts]]
file = "cube.stl"
""",
        create_files=["cube.stl"],
    )
    cfg = load_config(path)
    assert cfg.printer is None


def test_printer_bad_mode(tmp_path):
    path = _write_toml(
        tmp_path,
        """
[printer]
mode = "usb"

[[parts]]
file = "cube.stl"
""",
        create_files=["cube.stl"],
    )
    with pytest.raises(ValueError, match="printer.mode"):
        load_config(path)


def test_rotate_valid(tmp_path):
    path = _write_toml(
        tmp_path,
        """
[[parts]]
file = "cube.stl"
rotate = [90, 0, 45]
""",
        create_files=["cube.stl"],
    )
    cfg = load_config(path)
    assert cfg.parts[0].rotate == [90.0, 0.0, 45.0]


def test_rotate_bad_length(tmp_path):
    path = _write_toml(
        tmp_path,
        """
[[parts]]
file = "cube.stl"
rotate = [90, 0]
""",
        create_files=["cube.stl"],
    )
    with pytest.raises(ValueError, match="rotate"):
        load_config(path)


def test_rotate_not_list(tmp_path):
    path = _write_toml(
        tmp_path,
        """
[[parts]]
file = "cube.stl"
rotate = 45
""",
        create_files=["cube.stl"],
    )
    with pytest.raises(ValueError, match="rotate"):
        load_config(path)


def test_negative_scale(tmp_path):
    path = _write_toml(
        tmp_path,
        """
[[parts]]
file = "cube.stl"
scale = -1.0
""",
        create_files=["cube.stl"],
    )
    with pytest.raises(ValueError, match="scale"):
        load_config(path)


# --- filament by name ---


def test_filament_by_name_auto_derive(tmp_path):
    """String filament, no explicit list -> auto-derived."""
    path = _write_toml(
        tmp_path,
        """
[[parts]]
file = "cube.stl"
filament = "Generic PETG-CF @base"
""",
        create_files=["cube.stl"],
    )
    cfg = load_config(path)
    assert cfg.slicer.filaments == ["Generic PETG-CF @base"]
    assert cfg.parts[0].filament == 1


def test_filament_by_name_multi_material(tmp_path):
    """Multiple parts with different filaments -> correct ordering."""
    path = _write_toml(
        tmp_path,
        """
[[parts]]
file = "frame.stl"
filament = "Generic PETG-CF @base"

[[parts]]
file = "cover.stl"
filament = "Generic PLA @base"
""",
        create_files=["frame.stl", "cover.stl"],
    )
    cfg = load_config(path)
    assert cfg.slicer.filaments == ["Generic PETG-CF @base", "Generic PLA @base"]
    assert cfg.parts[0].filament == 1
    assert cfg.parts[1].filament == 2


def test_filament_by_name_dedup(tmp_path):
    """Multiple parts, same filament name -> single entry in derived list."""
    path = _write_toml(
        tmp_path,
        """
[[parts]]
file = "a.stl"
filament = "Generic PLA @base"

[[parts]]
file = "b.stl"
filament = "Generic PLA @base"
""",
        create_files=["a.stl", "b.stl"],
    )
    cfg = load_config(path)
    assert cfg.slicer.filaments == ["Generic PLA @base"]
    assert cfg.parts[0].filament == 1
    assert cfg.parts[1].filament == 1


def test_filament_by_name_explicit_list(tmp_path):
    """String filament with explicit list -> resolved to index."""
    path = _write_toml(
        tmp_path,
        """
[slicer]
filaments = ["Generic PLA @base", "Generic PETG-CF @base"]

[[parts]]
file = "cube.stl"
filament = "Generic PETG-CF @base"
""",
        create_files=["cube.stl"],
    )
    cfg = load_config(path)
    assert cfg.parts[0].filament == 2


def test_filament_by_name_not_in_list(tmp_path):
    """String filament not in explicit list -> error."""
    path = _write_toml(
        tmp_path,
        """
[slicer]
filaments = ["Generic PLA @base"]

[[parts]]
file = "cube.stl"
filament = "Generic ABS @base"
""",
        create_files=["cube.stl"],
    )
    with pytest.raises(ValueError, match="not in"):
        load_config(path)


def test_filament_mixed_int_string_no_list(tmp_path):
    """Mixed int + string without explicit list -> error."""
    path = _write_toml(
        tmp_path,
        """
[[parts]]
file = "a.stl"
filament = 1

[[parts]]
file = "b.stl"
filament = "Generic PLA @base"
""",
        create_files=["a.stl", "b.stl"],
    )
    with pytest.raises(ValueError, match="Cannot mix"):
        load_config(path)


def test_filament_int_backward_compat(tmp_path):
    """Integer filament with explicit list -> works as before."""
    path = _write_toml(
        tmp_path,
        """
[slicer]
filaments = ["Generic PLA @base", "Generic PETG-CF @base"]

[[parts]]
file = "cube.stl"
filament = 2
""",
        create_files=["cube.stl"],
    )
    cfg = load_config(path)
    assert cfg.parts[0].filament == 2


def test_filament_empty_name(tmp_path):
    """Empty filament name -> error."""
    path = _write_toml(
        tmp_path,
        """
[[parts]]
file = "cube.stl"
filament = ""
""",
        create_files=["cube.stl"],
    )
    with pytest.raises(ValueError, match="empty"):
        load_config(path)


# --- slicer.slots (slot → profile) ---


def test_slots_direct_feed(tmp_path):
    """Slot map: TPU on slot 5 (direct feed), PLA auto-assigned to slot 1."""
    path = _write_toml(
        tmp_path,
        """
[slicer.slots]
5 = "Generic TPU @base"

[[parts]]
file = "frame.stl"
filament = "Generic PLA @base"

[[parts]]
file = "insert.stl"
filament = "Generic TPU @base"
""",
        create_files=["frame.stl", "insert.stl"],
    )
    cfg = load_config(path)
    assert cfg.parts[0].filament == 1  # PLA auto-assigned
    assert cfg.parts[1].filament == 5  # TPU from slots map
    assert cfg.slicer.filaments[0] == "Generic PLA @base"
    assert cfg.slicer.filaments[4] == "Generic TPU @base"
    assert len(cfg.slicer.filaments) == 5


def test_slots_int_ref_with_map(tmp_path):
    """Integer filament ref resolved via slots map (case 1: 'use slot 3')."""
    path = _write_toml(
        tmp_path,
        """
[slicer.slots]
1 = "Generic PLA @base"
3 = "Generic PETG-CF @base"

[[parts]]
file = "frame.stl"
filament = 3
""",
        create_files=["frame.stl"],
    )
    cfg = load_config(path)
    assert cfg.parts[0].filament == 3
    assert cfg.slicer.filaments[0] == "Generic PLA @base"
    assert cfg.slicer.filaments[2] == "Generic PETG-CF @base"


def test_slots_mixed_int_and_string(tmp_path):
    """Mix int + string refs when slots map covers the int refs."""
    path = _write_toml(
        tmp_path,
        """
[slicer.slots]
3 = "Generic PETG-CF @base"
5 = "Generic TPU @base"

[[parts]]
file = "frame.stl"
filament = 3

[[parts]]
file = "insert.stl"
filament = "Generic TPU @base"
""",
        create_files=["frame.stl", "insert.stl"],
    )
    cfg = load_config(path)
    assert cfg.parts[0].filament == 3
    assert cfg.parts[1].filament == 5


def test_slots_int_ref_not_in_map(tmp_path):
    """Integer ref to a slot not in the map -> error."""
    path = _write_toml(
        tmp_path,
        """
[slicer.slots]
1 = "Generic PLA @base"

[[parts]]
file = "frame.stl"
filament = 3
""",
        create_files=["frame.stl"],
    )
    with pytest.raises(ValueError, match="slot 3 not defined"):
        load_config(path)


def test_slots_bad_slot_number(tmp_path):
    """Slot number must be >= 1."""
    path = _write_toml(
        tmp_path,
        """
[slicer.slots]
0 = "Generic PLA @base"

[[parts]]
file = "cube.stl"
filament = "Generic PLA @base"
""",
        create_files=["cube.stl"],
    )
    with pytest.raises(ValueError, match="slot must be >= 1"):
        load_config(path)


def test_slots_duplicate_profile(tmp_path):
    """Same profile in two slots — parts can target specific slot by int."""
    path = _write_toml(
        tmp_path,
        """
[slicer.slots]
1 = "Generic PETG-CF @base"
3 = "Generic PETG-CF @base"

[[parts]]
file = "frame.stl"
filament = 1

[[parts]]
file = "cover.stl"
filament = 3
""",
        create_files=["frame.stl", "cover.stl"],
    )
    cfg = load_config(path)
    assert cfg.parts[0].filament == 1
    assert cfg.parts[1].filament == 3
    assert cfg.slicer.filaments[0] == "Generic PETG-CF @base"
    assert cfg.slicer.filaments[2] == "Generic PETG-CF @base"


# --- object_filaments (multi-object 3MF) ---


def test_object_filaments_by_name(tmp_path):
    """Per-object filament overrides resolved by name."""
    path = _write_toml(
        tmp_path,
        """
[[parts]]
file = "widget.3mf"
filament = "Generic PETG-CF @base"

[parts.filaments]
inlay = "Bambu PLA Basic @BBL X1C"
""",
        create_files=["widget.3mf"],
    )
    cfg = load_config(path)
    assert cfg.slicer.filaments == ["Generic PETG-CF @base", "Bambu PLA Basic @BBL X1C"]
    assert cfg.parts[0].filament == 1  # default
    assert cfg.parts[0].object_filaments == {"inlay": 2}


def test_object_filaments_auto_derive_includes_objects(tmp_path):
    """Object filament names included in auto-derived filaments list."""
    path = _write_toml(
        tmp_path,
        """
[[parts]]
file = "a.3mf"
filament = "Generic PLA @base"

[parts.filaments]
logo = "Generic PETG-CF @base"
text = "Generic TPU @base"
""",
        create_files=["a.3mf"],
    )
    cfg = load_config(path)
    assert "Generic PLA @base" in cfg.slicer.filaments
    assert "Generic PETG-CF @base" in cfg.slicer.filaments
    assert "Generic TPU @base" in cfg.slicer.filaments
    assert (
        cfg.parts[0].object_filaments["logo"]
        == cfg.slicer.filaments.index("Generic PETG-CF @base") + 1
    )
    assert (
        cfg.parts[0].object_filaments["text"] == cfg.slicer.filaments.index("Generic TPU @base") + 1
    )


def test_object_filaments_with_explicit_list(tmp_path):
    """Per-object filaments resolved against explicit slicer.filaments."""
    path = _write_toml(
        tmp_path,
        """
[slicer]
filaments = ["Generic PETG-CF @base", "Bambu PLA Basic @BBL X1C"]

[[parts]]
file = "widget.3mf"
filament = "Generic PETG-CF @base"

[parts.filaments]
inlay = "Bambu PLA Basic @BBL X1C"
""",
        create_files=["widget.3mf"],
    )
    cfg = load_config(path)
    assert cfg.parts[0].filament == 1
    assert cfg.parts[0].object_filaments == {"inlay": 2}


def test_object_filaments_not_in_list(tmp_path):
    """Object filament name not in explicit list -> error."""
    path = _write_toml(
        tmp_path,
        """
[slicer]
filaments = ["Generic PLA @base"]

[[parts]]
file = "widget.3mf"
filament = "Generic PLA @base"

[parts.filaments]
inlay = "Generic ABS @base"
""",
        create_files=["widget.3mf"],
    )
    with pytest.raises(ValueError, match="inlay.*not in"):
        load_config(path)


def test_object_filaments_empty(tmp_path):
    """No object_filaments -> empty dict (backward compat)."""
    path = _write_toml(
        tmp_path,
        """
[[parts]]
file = "cube.stl"
filament = "Generic PLA @base"
""",
        create_files=["cube.stl"],
    )
    cfg = load_config(path)
    assert cfg.parts[0].object_filaments == {}
