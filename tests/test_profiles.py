"""Tests for profile discovery, resolution, and pinning."""

import json
from pathlib import Path

import pytest

from fabprint.profiles import (
    discover_profiles,
    pin_profiles,
    resolve_profile,
)

ORCA_SYSTEM = Path.home() / "Library/Application Support/OrcaSlicer/system/BBL"


def _has_orca():
    return ORCA_SYSTEM.is_dir()


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
