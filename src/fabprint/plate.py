"""Assemble arranged parts into a 3MF file."""

from __future__ import annotations

import logging
from pathlib import Path

import trimesh

from fabprint.arrange import Placement

log = logging.getLogger(__name__)


def build_plate(placements: list[Placement]) -> trimesh.Scene:
    """Build a trimesh Scene from placed meshes."""
    scene = trimesh.Scene()
    for p in placements:
        p.mesh.metadata["name"] = p.name
        scene.add_geometry(p.mesh, node_name=p.name)
    return scene


def export_plate(scene: trimesh.Scene, output: Path) -> Path:
    """Export a plate scene to 3MF."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    scene.export(str(output))
    log.info("Exported plate to %s", output)
    return output
