"""Tests for profile discovery, resolution, and pinning."""

import json

import pytest

from fabprint.profiles import (
    SYSTEM_DIRS,
    discover_profiles,
    pin_profiles,
    resolve_profile,
    resolve_profile_data,
)

ORCA_SYSTEM = SYSTEM_DIRS.get("orca")


def _has_orca():
    return ORCA_SYSTEM is not None and ORCA_SYSTEM.is_dir()


@pytest.mark.skipif(not _has_orca(), reason="OrcaSlicer not installed")
def test_discover_orca():
    profiles = discover_profiles("orca")
    assert "machine" in profiles
    assert "process" in profiles
    assert "filament" in profiles
    assert "Bambu Lab P1S 0.4 nozzle" in profiles["machine"]
    assert "0.20mm Standard @BBL X1C" in profiles["process"]


@pytest.mark.skipif(not _has_orca(), reason="OrcaSlicer not installed")
def test_resolve_from_system():
    path = resolve_profile("Bambu Lab P1S 0.4 nozzle", "orca", "machine")
    assert path.exists()
    assert path.name == "Bambu Lab P1S 0.4 nozzle.json"


def test_resolve_path_directly(tmp_path):
    profile = tmp_path / "custom.json"
    profile.write_text("{}")
    path = resolve_profile(str(profile), "orca", "machine")
    assert path == profile


def test_resolve_pinned_first(tmp_path):
    """Pinned profiles should take precedence over system profiles."""
    pinned_dir = tmp_path / "profiles" / "machine"
    pinned_dir.mkdir(parents=True)
    pinned = pinned_dir / "MyProfile.json"
    pinned.write_text("{}")
    path = resolve_profile("MyProfile", "orca", "machine", project_dir=tmp_path)
    assert path == pinned


def test_resolve_not_found():
    with pytest.raises(FileNotFoundError, match="not found"):
        resolve_profile("Nonexistent Printer", "orca", "machine")


def test_discover_unknown_engine():
    with pytest.raises(ValueError, match="Unknown engine"):
        discover_profiles("cura")


@pytest.mark.skipif(not _has_orca(), reason="OrcaSlicer not installed")
def test_pin_profiles(tmp_path):
    pinned = pin_profiles(
        engine="orca",
        printer="Bambu Lab P1S 0.4 nozzle",
        process="0.20mm Standard @BBL X1C",
        filaments=["Generic PLA @base"],
        project_dir=tmp_path,
    )
    assert len(pinned) == 3
    assert (tmp_path / "profiles" / "machine" / "Bambu Lab P1S 0.4 nozzle.json").exists()
    assert (tmp_path / "profiles" / "process" / "0.20mm Standard @BBL X1C.json").exists()
    assert (tmp_path / "profiles" / "filament" / "Generic PLA @base.json").exists()

    # Verify pinned files are valid JSON
    for p in pinned:
        json.loads(p.read_text())


def test_resolve_profile_data_flattens_inheritance(tmp_path):
    """Verify resolve_profile_data merges the full inheritance chain."""
    # Create a 2-level inheritance chain in a fake system dir
    cat_dir = tmp_path / "profiles" / "process"
    cat_dir.mkdir(parents=True)

    root = {"from": "system", "wall_loops": 2, "enable_support": 0, "infill": "grid"}
    cat_dir.joinpath("root.json").write_text(json.dumps(root))

    child = {"from": "system", "inherits": "root", "wall_loops": 3}
    cat_dir.joinpath("child.json").write_text(json.dumps(child))

    data = resolve_profile_data(str(cat_dir / "child.json"), "orca", "process", tmp_path)
    # Child overrides wall_loops, inherits enable_support and infill from root
    assert data["wall_loops"] == 3
    assert data["enable_support"] == 0
    assert data["infill"] == "grid"
    # inherits key must be stripped
    assert "inherits" not in data


@pytest.mark.skipif(not _has_orca(), reason="OrcaSlicer not installed")
def test_resolve_profile_data_real_process():
    """Verify real OrcaSlicer process profile resolves enable_support."""
    data = resolve_profile_data("0.20mm Standard @BBL X1C", "orca", "process")
    # Must have enable_support from the root of the chain
    assert "enable_support" in data
    # Must not have inherits (fully flattened)
    assert "inherits" not in data
    # Should have many keys from the full chain
    assert len(data) > 50
