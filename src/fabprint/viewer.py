"""Visualize the arranged build plate."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

import trimesh

log = logging.getLogger(__name__)


def show_plate(meshes: list[trimesh.Trimesh], names: list[str] | None = None) -> None:
    """Display arranged meshes, trying ocp_vscode first, then trimesh viewer."""
    if _try_ocp(meshes, names):
        return
    _try_trimesh(meshes, names)


def _try_ocp(meshes: list[trimesh.Trimesh], names: list[str] | None) -> bool:
    """Try displaying via ocp_vscode. Returns True if successful."""
    try:
        from build123d import import_stl
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

    log.info("Showing plate in OCP viewer (%d parts)", len(solids))
    show(*solids)
    return True


def _try_trimesh(meshes: list[trimesh.Trimesh], names: list[str] | None) -> None:
    """Fallback: show via trimesh's built-in viewer."""
    scene = trimesh.Scene()
    for i, mesh in enumerate(meshes):
        name = names[i] if names else f"part_{i}"
        scene.add_geometry(mesh, node_name=name)
    log.info("Showing plate in trimesh viewer (%d parts)", len(meshes))
    scene.show()
