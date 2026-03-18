"""Load mesh files (STL, 3MF, STEP) into trimesh."""

from __future__ import annotations

import logging
import tempfile
import zipfile
from pathlib import Path

import trimesh
from defusedxml import ElementTree as ET

from fabprint.constants import NS_3MF

log = logging.getLogger(__name__)

MESH_EXTENSIONS = {".stl", ".3mf"}
STEP_EXTENSIONS = {".step", ".stp"}
SUPPORTED_EXTENSIONS = MESH_EXTENSIONS | STEP_EXTENSIONS


def load_mesh(path: Path) -> trimesh.Trimesh:
    """Load a mesh file and return a single trimesh.Trimesh."""
    path = Path(path)
    suffix = path.suffix.lower()

    if suffix not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported file type '{suffix}'. Supported: {SUPPORTED_EXTENSIONS}")

    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    if suffix in STEP_EXTENSIONS:
        return _load_step(path)

    result = trimesh.load(str(path))

    # trimesh.load may return a Scene for multi-body files; merge into single mesh
    if isinstance(result, trimesh.Scene):
        if len(result.geometry) == 0:
            raise ValueError(f"No geometry found in {path}")
        meshes = list(result.geometry.values())
        result = trimesh.util.concatenate(meshes)

    if not isinstance(result, trimesh.Trimesh):
        raise ValueError(f"Could not load {path} as a single mesh")

    return result


def extract_paint_colors(path: Path) -> list[str] | None:
    """Extract per-triangle paint_color attributes from a 3MF file.

    Handles both simple 3MF (geometry in 3D/3dmodel.model) and BambuStudio
    project 3MF (geometry in 3D/Objects/*.model sub-files).

    Returns a list of paint_color hex strings (one per face), or None if
    the file has no paint data or isn't a 3MF.
    """
    path = Path(path)
    if path.suffix.lower() != ".3mf":
        return None

    try:
        with zipfile.ZipFile(path, "r") as zf:
            # Collect all model XML files (root + sub-objects)
            model_files = []
            if "3D/3dmodel.model" in zf.namelist():
                model_files.append("3D/3dmodel.model")
            model_files.extend(
                n for n in zf.namelist() if n.startswith("3D/Objects/") and n.endswith(".model")
            )
            if not model_files:
                return None

            colors = []
            has_paint = False
            for mf in model_files:
                root = ET.fromstring(zf.read(mf))
                for tri in root.iter(f"{{{NS_3MF}}}triangle"):
                    pc = tri.get("paint_color")
                    if pc is not None:
                        has_paint = True
                    colors.append(pc)
    except (zipfile.BadZipFile, FileNotFoundError):
        return None

    if not has_paint:
        return None

    return colors


def load_3mf_objects(path: Path) -> list[tuple[str, trimesh.Trimesh]]:
    """Load named objects from a multi-object 3MF file.

    Returns list of (object_name, mesh) tuples with coordinate positions preserved.
    Build-section transforms are applied if present.
    """
    import numpy as np

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    with zipfile.ZipFile(path, "r") as zf:
        model_xml = zf.read("3D/3dmodel.model")

    root = ET.fromstring(model_xml)
    ns = NS_3MF

    # Parse all objects by id
    obj_map: dict[str, tuple[str, trimesh.Trimesh]] = {}
    for obj_elem in root.findall(f".//{{{ns}}}object"):
        obj_id = obj_elem.get("id")
        name = obj_elem.get("name", f"object_{obj_id}")

        mesh_elem = obj_elem.find(f"{{{ns}}}mesh")
        if mesh_elem is None:
            continue

        vertices = []
        for v in mesh_elem.findall(f".//{{{ns}}}vertex"):
            vertices.append([float(v.get("x")), float(v.get("y")), float(v.get("z"))])

        faces = []
        for t in mesh_elem.findall(f".//{{{ns}}}triangle"):
            faces.append([int(t.get("v1")), int(t.get("v2")), int(t.get("v3"))])

        mesh = trimesh.Trimesh(vertices=vertices, faces=faces)
        obj_map[obj_id] = (name, mesh)

    # Walk build items, applying transforms if present
    results = []
    build = root.find(f"{{{ns}}}build")
    if build is not None:
        for item in build.findall(f"{{{ns}}}item"):
            obj_id = item.get("objectid")
            if obj_id not in obj_map:
                continue
            name, mesh = obj_map[obj_id]

            transform_str = item.get("transform")
            if transform_str:
                vals = [float(v) for v in transform_str.split()]
                if len(vals) == 12:
                    # 3MF affine: m00 m01 m02 m10 m11 m12 m20 m21 m22 tx ty tz
                    matrix = np.eye(4)
                    matrix[:3, :3] = np.array(vals[:9]).reshape(3, 3).T
                    matrix[:3, 3] = vals[9:12]
                    mesh = mesh.copy()
                    mesh.apply_transform(matrix)

            results.append((name, mesh))
    else:
        results = list(obj_map.values())

    if not results:
        raise ValueError(f"No mesh objects found in {path}")

    log.info("Loaded %d objects from %s: %s", len(results), path, [n for n, _ in results])
    return results


def _load_step(path: Path) -> trimesh.Trimesh:
    """Load a STEP file via build123d → STL round-trip."""
    try:
        from build123d import export_stl, import_step
    except ImportError:
        raise ImportError(
            "build123d is required to load STEP files. Install with: pip install build123d"
        ) from None

    log.info("Loading STEP file via build123d: %s", path)
    shape = import_step(str(path))

    with tempfile.NamedTemporaryFile(suffix=".stl", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        export_stl(shape, str(tmp_path))
        mesh = trimesh.load(str(tmp_path))
    finally:
        tmp_path.unlink(missing_ok=True)

    if not isinstance(mesh, trimesh.Trimesh):
        raise ValueError(f"Failed to convert STEP to mesh: {path}")

    return mesh
