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

    log.info("Packed %d parts onto plate", len(placements))
    return placements
