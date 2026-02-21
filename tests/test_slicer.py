"""Tests for slicer module."""

import json
from pathlib import Path

import pytest

from fabprint.slicer import _apply_overrides, find_slicer

BAMBU_PATH = Path("/Applications/BambuStudio.app/Contents/MacOS/BambuStudio")
ORCA_PATH = Path("/Applications/OrcaSlicer.app/Contents/MacOS/OrcaSlicer")


def test_find_bambu():
    if not BAMBU_PATH.exists():
        pytest.skip("BambuStudio not installed")
    assert find_slicer("bambu") == BAMBU_PATH


def test_find_orca():
    if not ORCA_PATH.exists():
        pytest.skip("OrcaSlicer not installed")
    assert find_slicer("orca") == ORCA_PATH


def test_find_unknown():
    with pytest.raises(ValueError, match="Unknown slicer"):
        find_slicer("cura")


def test_apply_overrides(tmp_path):
    profile = tmp_path / "process.json"
    profile.write_text(json.dumps({
        "wall_loops": 2,
        "sparse_infill_density": "15%",
        "other_setting": "keep",
    }))

    result = _apply_overrides(profile, {
        "wall_loops": 4,
        "sparse_infill_density": "25%",
    })

    try:
        data = json.loads(result.read_text())
        assert data["wall_loops"] == 4
        assert data["sparse_infill_density"] == "25%"
        assert data["other_setting"] == "keep"
    finally:
        result.unlink(missing_ok=True)
