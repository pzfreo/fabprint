"""2D bin packing of parts onto a build plate via rectpack."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import rectpack
import trimesh

log = logging.getLogger(__name__)


@dataclass
class Placement:
    mesh: trimesh.Trimesh
    name: str
    x: float
    y: float


def arrange(
    meshes: list[trimesh.Trimesh],
    names: list[str],
    plate_size: tuple[float, float],
    padding: float = 5.0,
) -> list[Placement]:
    """Pack oriented meshes onto a build plate.

    Returns a list of Placement objects with meshes translated to their packed positions.
    Raises ValueError if not all parts fit on the plate.
    """
    if len(meshes) != len(names):
        raise ValueError("meshes and names must have the same length")

    # Compute padded XY bounding boxes (use integer mm for rectpack)
    rects = []
    for i, mesh in enumerate(meshes):
        w = mesh.extents[0] + padding
        h = mesh.extents[1] + padding
        rects.append((i, int(w + 1), int(h + 1)))  # ceil to int

    packer = rectpack.newPacker(
        mode=rectpack.PackingMode.Offline,
        pack_algo=rectpack.MaxRectsBssf,
        rotation=False,
    )
    packer.add_bin(int(plate_size[0]), int(plate_size[1]))
    for idx, w, h in rects:
        packer.add_rect(w, h, rid=idx)

    packer.pack()

    packed = packer.rect_list()
    if len(packed) != len(meshes):
        raise ValueError(
            f"Only {len(packed)}/{len(meshes)} parts fit on the "
            f"{plate_size[0]}x{plate_size[1]}mm plate"
        )

    placements = []
    for _bin_id, x, y, _w, _h, rid in packed:
        mesh = meshes[rid].copy()
        # Translate mesh: move min XY to the packed position (+ half padding offset)
        min_x, min_y = mesh.bounds[0][0], mesh.bounds[0][1]
        offset_x = x + padding / 2 - min_x
        offset_y = y + padding / 2 - min_y
        mesh.apply_translation([offset_x, offset_y, 0])
        placements.append(Placement(mesh=mesh, name=names[rid], x=x, y=y))

    # Center the packed group on the plate
    _center_on_plate(placements, plate_size)

    log.info("Packed %d parts onto plate", len(placements))
    return placements


def _center_on_plate(placements: list[Placement], plate_size: tuple[float, float]) -> None:
    """Translate all placements so the group is centered on the plate."""
    if not placements:
        return

    # Find bounding box of all placed meshes
    all_min_x = min(p.mesh.bounds[0][0] for p in placements)
    all_max_x = max(p.mesh.bounds[1][0] for p in placements)
    all_min_y = min(p.mesh.bounds[0][1] for p in placements)
    all_max_y = max(p.mesh.bounds[1][1] for p in placements)

    group_cx = (all_min_x + all_max_x) / 2
    group_cy = (all_min_y + all_max_y) / 2
    plate_cx = plate_size[0] / 2
    plate_cy = plate_size[1] / 2

    dx = plate_cx - group_cx
    dy = plate_cy - group_cy

    for p in placements:
        p.mesh.apply_translation([dx, dy, 0])
        p.x += dx
        p.y += dy
