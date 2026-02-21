"""Assemble arranged parts into a 3MF file."""

from __future__ import annotations

import logging
from pathlib import Path

import trimesh

from fabprint.arrange import Placement

log = logging.getLogger(__name__)


def build_plate(
    placements: list[Placement],
    plate_size: tuple[float, float] = (256.0, 256.0),
) -> trimesh.Scene:
    """Build a trimesh Scene from placed meshes.

    Meshes are shifted so the plate center is at origin (0,0),
    matching slicer bed coordinate conventions.
    """
    cx, cy = plate_size[0] / 2, plate_size[1] / 2
    scene = trimesh.Scene()
    for p in placements:
        mesh = p.mesh.copy()
        mesh.apply_translation([-cx, -cy, 0])
        mesh.metadata["name"] = p.name
        scene.add_geometry(mesh, node_name=p.name)
    return scene


def export_plate(scene: trimesh.Scene, output: Path) -> Path:
    """Export a plate scene to 3MF."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    scene.export(str(output))
    log.info("Exported plate to %s", output)
    return output
