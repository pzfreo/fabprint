"""Visualize the arranged build plate."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

import trimesh

log = logging.getLogger(__name__)


def show_plate(
    meshes: list[trimesh.Trimesh],
    names: list[str] | None = None,
    plate_size: tuple[float, float] = (256, 256),
) -> None:
    """Display arranged meshes, trying ocp_vscode first, then trimesh viewer."""
    if _try_ocp(meshes, names, plate_size):
        return
    _try_trimesh(meshes, names, plate_size)


def _make_plate_outline(plate_size: tuple[float, float]) -> trimesh.Trimesh:
    """Create a thin transparent rectangle representing the build plate."""
    w, h = plate_size
    plate = trimesh.creation.box(extents=[w, h, 0.5])
    plate.apply_translation([w / 2, h / 2, -0.25])
    return plate


def _try_ocp(
    meshes: list[trimesh.Trimesh],
    names: list[str] | None,
    plate_size: tuple[float, float],
) -> bool:
    """Try displaying via ocp_vscode. Returns True if successful."""
    try:
        from build123d import Box, Color, Location, import_stl
        from ocp_vscode import show
    except ImportError:
        log.debug("ocp_vscode or build123d not available, skipping OCP viewer")
        return False

    solids = []
    for i, mesh in enumerate(meshes):
        with tempfile.NamedTemporaryFile(suffix=".stl", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            mesh.export(str(tmp_path))
            solid = import_stl(str(tmp_path))
            solids.append(solid)
        finally:
            tmp_path.unlink(missing_ok=True)

    # Ghost build plate
    w, h = plate_size
    plate_box = Location((w / 2, h / 2, -0.25)) * Box(w, h, 0.5)
    plate_box.color = Color(0.8, 0.8, 0.8, 0.3)

    log.info("Showing plate in OCP viewer (%d parts)", len(solids))
    show(*solids, plate_box)
    return True


def _try_trimesh(
    meshes: list[trimesh.Trimesh],
    names: list[str] | None,
    plate_size: tuple[float, float],
) -> None:
    """Fallback: show via trimesh's built-in viewer."""
    scene = trimesh.Scene()

    # Add ghost plate
    plate = _make_plate_outline(plate_size)
    plate.visual.face_colors = [200, 200, 200, 80]
    scene.add_geometry(plate, node_name="build_plate")

    for i, mesh in enumerate(meshes):
        name = names[i] if names else f"part_{i}"
        scene.add_geometry(mesh, node_name=name)
    log.info("Showing plate in trimesh viewer (%d parts)", len(meshes))
    scene.show()
