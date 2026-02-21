"""Load mesh files (STL, 3MF, STEP) into trimesh."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

import trimesh

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
