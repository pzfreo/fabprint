"""Thumbnail generation for 3MF plate images."""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)


def generate_plate_thumbnail(
    width: int = 256, height: int = 256, plate_3mf: Path | None = None
) -> bytes:
    """Render an isometric thumbnail of the plate using trimesh + Pillow.

    Falls back to a branded placeholder if rendering fails.
    """
    if plate_3mf is not None:
        try:
            return _render_plate_thumbnail(width, height, plate_3mf)
        except Exception:
            log.debug("Thumbnail rendering failed, using placeholder", exc_info=True)
    return placeholder_thumbnail(width, height)


def _render_plate_thumbnail(width: int, height: int, plate_3mf: Path) -> bytes:
    """Render an isometric view of the plate meshes with bed plate."""
    import io as _io

    import numpy as np
    import trimesh
    from PIL import Image, ImageDraw

    scene = trimesh.load(str(plate_3mf), force="scene")
    meshes = list(scene.geometry.values())  # type: ignore[attr-defined]
    if not meshes:
        raise ValueError("No geometry in plate 3MF")

    # Bed is the full plate size (256x256 by convention — parts are centered at origin)
    # Read plate size from the part bounds symmetry (parts centered at 0,0)
    combined_parts = trimesh.util.concatenate(meshes)
    part_bounds = combined_parts.bounds
    # Plate is symmetric about origin; infer size from the max extent
    plate_half = max(
        abs(part_bounds[0][0]),
        abs(part_bounds[1][0]),
        abs(part_bounds[0][1]),
        abs(part_bounds[1][1]),
    )
    # Round up to nearest common plate size, minimum the part extent + padding
    bed_half = max(plate_half + 10, 64)

    # Isometric view: Rx(-30) @ Rz(45) — looking down from front-right
    cos45 = np.cos(np.radians(45))
    sin45 = np.sin(np.radians(45))
    cos35 = np.cos(np.radians(30))
    sin35 = np.sin(np.radians(30))

    # Rx(-a) @ Rz(b): screen_x = cos(b)*x - sin(b)*y
    #                  screen_y = cos(a)*sin(b)*x + cos(a)*cos(b)*y + sin(a)*z
    #                  depth    = -sin(a)*sin(b)*x - sin(a)*cos(b)*y + cos(a)*z
    rot = np.array(
        [
            [cos45, -sin45, 0],
            [cos35 * sin45, cos35 * cos45, sin35],
            [-sin35 * sin45, -sin35 * cos45, cos35],
        ]
    )

    # Light direction (from upper-right, normalized)
    light_dir = np.array([0.3, -0.3, 0.9])
    light_dir = light_dir / np.linalg.norm(light_dir)

    # Project all part meshes for bounding box
    all_projected = []
    for mesh in meshes:
        projected = mesh.vertices @ rot.T
        all_projected.append(projected)

    # Also project bed corners for bounding box
    bed_corners_3d = np.array(
        [
            [-bed_half, -bed_half, 0],
            [bed_half, -bed_half, 0],
            [bed_half, bed_half, 0],
            [-bed_half, bed_half, 0],
        ]
    )
    bed_corners_proj = bed_corners_3d @ rot.T

    all_pts = np.vstack(all_projected + [bed_corners_proj])
    px_min, py_min = all_pts[:, 0].min(), all_pts[:, 1].min()
    px_max, py_max = all_pts[:, 0].max(), all_pts[:, 1].max()

    # Fit into image with margin
    margin = 16
    draw_w = width - 2 * margin
    draw_h = height - 2 * margin
    proj_w = px_max - px_min
    proj_h = py_max - py_min
    scale = min(draw_w / max(proj_w, 1), draw_h / max(proj_h, 1))
    offset_x = margin + draw_w / 2 - (px_min + px_max) / 2 * scale
    offset_y = margin + draw_h / 2 + (py_min + py_max) / 2 * scale  # flip Y

    def to_pixel(pt: np.ndarray) -> tuple[float, float]:
        return (offset_x + pt[0] * scale, offset_y - pt[1] * scale)

    # Image setup
    bg = (25, 25, 30)
    img = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(img)

    # Colors for different filament slots
    palette = [
        (0, 150, 136),  # teal
        (76, 175, 80),  # green
        (255, 152, 0),  # orange
        (33, 150, 243),  # blue
        (244, 67, 54),  # red
        (156, 39, 176),  # purple
    ]

    # Draw bed first as a simple projected quad
    bed_pts = [to_pixel(c) for c in bed_corners_proj]
    draw.polygon(bed_pts, fill=(55, 58, 65))
    draw.line(bed_pts + [bed_pts[0]], fill=(75, 78, 85), width=1)

    # Collect part faces with depth for painter's algorithm
    face_list = []  # (depth, pixel_pts, color)
    for mesh_idx, mesh in enumerate(meshes):
        fil_id = mesh.metadata.get("filament_id", 1)
        base_color = np.array(palette[(fil_id - 1) % len(palette)], dtype=float)

        projected = all_projected[mesh_idx]
        normals = mesh.face_normals

        for fi, face in enumerate(mesh.faces):
            verts_proj = projected[face]
            depth = verts_proj[:, 2].mean()

            n = normals[fi]
            brightness = max(0.3, float(np.dot(n, light_dir)))
            color = tuple(int(min(255, c * brightness)) for c in base_color)

            pts = [to_pixel(verts_proj[i]) for i in range(3)]
            face_list.append((depth, pts, color))

    # Sort back-to-front: lowest depth = furthest from camera = draw first
    face_list.sort(key=lambda f: f[0])

    for _, pts, color in face_list:
        draw.polygon(pts, fill=color)

    buf = _io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def placeholder_thumbnail(width: int = 256, height: int = 256) -> bytes:
    """Generate a minimal branded placeholder PNG (no mesh data needed)."""
    import struct
    import zlib as _zlib

    # Colors (RGB)
    bg = (25, 25, 30)
    plate_c = (50, 52, 58)
    accent = (0, 150, 136)  # teal

    # Simple 5x7 pixel font for "fabprint"
    _font: dict[str, list[int]] = {
        "f": [0x7C, 0x40, 0x78, 0x40, 0x40, 0x40, 0x40],
        "a": [0x38, 0x44, 0x44, 0x7C, 0x44, 0x44, 0x44],
        "b": [0x78, 0x44, 0x44, 0x78, 0x44, 0x44, 0x78],
        "p": [0x78, 0x44, 0x44, 0x78, 0x40, 0x40, 0x40],
        "r": [0x78, 0x44, 0x44, 0x78, 0x50, 0x48, 0x44],
        "i": [0x38, 0x10, 0x10, 0x10, 0x10, 0x10, 0x38],
        "n": [0x44, 0x64, 0x54, 0x4C, 0x44, 0x44, 0x44],
        "t": [0x7C, 0x10, 0x10, 0x10, 0x10, 0x10, 0x10],
    }
    text = "fabprint"
    char_w, char_h, spacing = 7, 7, 1
    text_w = len(text) * (char_w + spacing) - spacing
    font_scale = 2
    tx = (width - text_w * font_scale) // 2
    ty = height // 2 - (char_h * font_scale) // 2

    rows = []
    for y in range(height):
        row = bytearray(width * 3)
        for x in range(width):
            mx, my = 20, 40
            if mx <= x < width - mx and my <= y < height - my:
                if y <= my + 2:
                    r, g, b = accent
                else:
                    r, g, b = plate_c
            else:
                r, g, b = bg

            sx = (x - tx) // font_scale
            sy = (y - ty) // font_scale
            if 0 <= sy < char_h and 0 <= sx < text_w:
                ci = sx // (char_w + spacing)
                fcx = sx % (char_w + spacing)
                if ci < len(text) and fcx < char_w:
                    ch = text[ci]
                    if ch in _font:
                        row_bits = _font[ch][sy]
                        if row_bits & (0x80 >> fcx):
                            r, g, b = accent

            off = x * 3
            row[off] = r
            row[off + 1] = g
            row[off + 2] = b
        rows.append(bytes([0]) + bytes(row))

    raw = b"".join(rows)
    compressed = _zlib.compress(raw)

    def _chunk(ctype: bytes, data: bytes) -> bytes:
        c = ctype + data
        crc = _zlib.crc32(c) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + c + struct.pack(">I", crc)

    png = b"\x89PNG\r\n\x1a\n"
    png += _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += _chunk(b"IDAT", compressed)
    png += _chunk(b"IEND", b"")
    return png
