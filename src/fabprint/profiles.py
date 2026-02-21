"""Discover, resolve, and pin slicer profiles."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

log = logging.getLogger(__name__)

CATEGORIES = ("machine", "process", "filament")

SYSTEM_DIRS: dict[str, Path] = {
    "bambu": Path.home() / "Library/Application Support/BambuStudio/system/BBL",
    "orca": Path.home() / "Library/Application Support/OrcaSlicer/system/BBL",
}


def _is_path(value: str) -> bool:
    """Check if a value looks like a file path rather than a profile name."""
    return "/" in value or "\\" in value


def discover_profiles(engine: str) -> dict[str, dict[str, Path]]:
    """Scan system directories for available profiles.

    Returns {"machine": {"Name": Path, ...}, "process": {...}, "filament": {...}}
    """
    base = SYSTEM_DIRS.get(engine)
    if base is None:
        raise ValueError(f"Unknown engine: '{engine}'. Supported: {list(SYSTEM_DIRS)}")

    result: dict[str, dict[str, Path]] = {}
    for category in CATEGORIES:
        cat_dir = base / category
        profiles: dict[str, Path] = {}
        if cat_dir.is_dir():
            for f in sorted(cat_dir.glob("*.json")):
                name = f.stem
                # Skip internal/template files
                if "template" in name or name.startswith("fdm_"):
                    continue
                profiles[name] = f
        result[category] = profiles

    return result


def resolve_profile(
    name_or_path: str,
    engine: str,
    category: str,
    project_dir: Path | None = None,
) -> Path:
    """Resolve a profile name or path to an absolute file path.

    Resolution order:
    1. If it looks like a path, use it directly
    2. Check <project_dir>/profiles/<category>/<name>.json
    3. Check slicer system directory
    """
    if _is_path(name_or_path):
        path = Path(name_or_path)
        if not path.exists():
            raise FileNotFoundError(f"Profile path not found: {path}")
        return path

    # Check local pinned profiles
    if project_dir:
        local = project_dir / "profiles" / category / f"{name_or_path}.json"
        if local.exists():
            log.debug("Resolved '%s' from pinned profiles: %s", name_or_path, local)
            return local

    # Check system directory
    base = SYSTEM_DIRS.get(engine)
    if base:
        system = base / category / f"{name_or_path}.json"
        if system.exists():
            log.debug("Resolved '%s' from system profiles: %s", name_or_path, system)
            return system

    raise FileNotFoundError(
        f"Profile '{name_or_path}' not found in category '{category}' "
        f"for engine '{engine}'. Run 'fabprint profiles list' to see available profiles."
    )


def pin_profiles(
    engine: str,
    printer: str | None,
    process: str | None,
    filaments: list[str],
    project_dir: Path,
) -> list[Path]:
    """Copy referenced profiles into <project_dir>/profiles/ for reproducibility.

    Returns list of pinned file paths.
    """
    pinned = []

    items = []
    if printer:
        items.append(("machine", printer))
    if process:
        items.append(("process", process))
    for f in filaments:
        items.append(("filament", f))

    for category, name in items:
        if _is_path(name):
            log.info("Skipping '%s' (already a path)", name)
            continue

        source = resolve_profile(name, engine, category, project_dir)
        dest_dir = project_dir / "profiles" / category
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{name}.json"

        if dest.exists():
            log.info("Already pinned: %s", dest)
        else:
            shutil.copy2(source, dest)
            log.info("Pinned %s → %s", source.name, dest)
        pinned.append(dest)

    return pinned
