"""Tests for the viewer module."""

from unittest.mock import patch

import trimesh

from fabprint.viewer import _make_plate_outline, _try_trimesh, show_plate


def test_make_plate_outline_dimensions():
    plate = _make_plate_outline((200, 300))
    # Should be a thin box centered on the plate
    assert abs(plate.extents[0] - 200) < 0.1
    assert abs(plate.extents[1] - 300) < 0.1
    assert abs(plate.extents[2] - 0.5) < 0.1


def test_make_plate_outline_position():
    plate = _make_plate_outline((256, 256))
    # Center should be at (128, 128)
    center = plate.centroid
    assert abs(center[0] - 128) < 0.1
    assert abs(center[1] - 128) < 0.1


def test_try_trimesh_builds_scene():
    mesh = trimesh.creation.box(extents=[10, 10, 10])
    with patch.object(trimesh.Scene, "show") as mock_show:
        _try_trimesh([mesh], ["cube"], (256, 256))
        mock_show.assert_called_once()


def test_try_trimesh_multiple_parts():
    meshes = [trimesh.creation.box(extents=[10, 10, 10]) for _ in range(3)]
    names = ["a", "b", "c"]
    with patch.object(trimesh.Scene, "show") as mock_show:
        _try_trimesh(meshes, names, (256, 256))
        mock_show.assert_called_once()


def test_show_plate_falls_through_to_trimesh():
    mesh = trimesh.creation.box(extents=[10, 10, 10])
    with (
        patch("fabprint.viewer._try_ocp", return_value=False) as mock_ocp,
        patch("fabprint.viewer._try_trimesh") as mock_trimesh,
    ):
        show_plate([mesh], ["cube"], (256, 256))
        mock_ocp.assert_called_once()
        mock_trimesh.assert_called_once()


def test_show_plate_ocp_success_skips_trimesh():
    mesh = trimesh.creation.box(extents=[10, 10, 10])
    with (
        patch("fabprint.viewer._try_ocp", return_value=True) as mock_ocp,
        patch("fabprint.viewer._try_trimesh") as mock_trimesh,
    ):
        show_plate([mesh], ["cube"], (256, 256))
        mock_ocp.assert_called_once()
        mock_trimesh.assert_not_called()
