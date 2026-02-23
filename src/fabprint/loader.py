"""Load mesh files (STL, 3MF, STEP) into trimesh."""

from __future__ import annotations

import logging
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import trimesh

log = logging.getLogger(__name__)

NS_3MF = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"

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


def _load_step(path: Path) -> trimesh.Trimesh:
    """Load a STEP file via build123d → STL round-trip."""
    try:
        from build123d import export_stl, import_step
    except ImportError:
        raise ImportError(
            "build123d is required to load STEP files. "
            "Install with: uv pip install 'fabprint[step]'"
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
