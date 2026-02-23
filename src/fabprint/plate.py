"""Assemble arranged parts into a 3MF file."""

from __future__ import annotations

import io
import logging
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import trimesh

from fabprint.arrange import Placement

log = logging.getLogger(__name__)

NS_3MF = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"


def _encode_paint_color(extruder_idx: int) -> str:
    """Encode a 0-based extruder index as a paint_color hex string.

    BambuStudio/OrcaSlicer TriangleSelector bitstream format:
      Extruder 0 → "4", Extruder 1 → "8", Extruder 2 → "C"
    """
    state = extruder_idx + 1
    return format(state << 2, "X")


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
    """Export a plate scene to 3MF, injecting paint_color if needed."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    scene.export(str(output))
    _inject_paint_data(output, scene)
    log.info("Exported plate to %s", output)
    return output


def _inject_paint_data(output: Path, scene: trimesh.Scene) -> None:
    """Post-process a 3MF to add paint_color attributes from mesh metadata.

    Reads filament_id and paint_colors from each geometry's metadata.
    Objects in the 3MF XML appear in the same order as scene.geometry
    (insertion order), so we match by index.

    Uses ET for element traversal but preserves the original XML header
    (including all namespace declarations) to avoid dropping namespaces
    that OrcaSlicer expects.
    """
    geometries = list(scene.geometry.values())

    # Only post-process if any geometry has pre-painted data.
    # Config-assigned filament_id is NOT injected as paint_color because
    # OrcaSlicer 2.3.1 CLI segfaults on paint_color + --load-filaments.
    if not any(geom.metadata.get("paint_colors") for geom in geometries):
        return

    # Read the 3MF ZIP
    with zipfile.ZipFile(output, "r") as zf:
        model_xml = zf.read("3D/3dmodel.model")
        other_files = {}
        for name in zf.namelist():
            if name != "3D/3dmodel.model":
                other_files[name] = zf.read(name)

    # Re-register all namespaces from the original XML so ET preserves
    # prefixes (avoids ns0: renames). Note: ET.tostring still drops
    # unused namespace declarations from the root element.
    for _event, (prefix, uri) in ET.iterparse(io.BytesIO(model_xml), events=["start-ns"]):
        ET.register_namespace(prefix, uri)

    root = ET.fromstring(model_xml)

    # Find all object elements (in document order = insertion order)
    objects = root.findall(f".//{{{NS_3MF}}}object")

    for i, obj in enumerate(objects):
        if i >= len(geometries):
            break

        geom = geometries[i]
        paint_colors = geom.metadata.get("paint_colors")
        if not paint_colors:
            continue

        triangles = obj.findall(f".//{{{NS_3MF}}}triangle")
        for j, tri in enumerate(triangles):
            if j < len(paint_colors) and paint_colors[j] is not None:
                tri.set("paint_color", paint_colors[j])

    # Add BambuStudio painting version metadata
    meta = ET.SubElement(root, f"{{{NS_3MF}}}metadata")
    meta.set("name", "BambuStudio:MmPaintingVersion")
    meta.text = "0"

    new_xml = ET.tostring(root, encoding="unicode", xml_declaration=True)

    # ET.tostring drops unused namespace declarations from <model>.
    # Restore the original opening tag (with all xmlns:* attrs) so
    # OrcaSlicer doesn't crash on missing namespaces.
    orig_str = model_xml.decode("utf-8")
    orig_open = re.match(r"<\?xml[^?]*\?>\s*(<model[^>]*>)", orig_str)
    new_open = re.match(r"<\?xml[^?]*\?>\s*(<model[^>]*>)", new_xml)
    if orig_open and new_open:
        new_xml = new_xml.replace(new_open.group(1), orig_open.group(1), 1)

    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zout:
        zout.writestr("3D/3dmodel.model", new_xml)
        for name, data in other_files.items():
            zout.writestr(name, data)
