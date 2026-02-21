"""Tests for slicer module."""

from pathlib import Path

import pytest

from fabprint.slicer import find_slicer

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
