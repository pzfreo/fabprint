"""Discover, resolve, and pin slicer profiles."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

log = logging.getLogger(__name__)

CATEGORIES = ("machine", "process", "filament")


def _system_dirs() -> dict[str, Path]:
    """Return slicer system profile directories for the current platform."""
    if sys.platform == "darwin":
        return {
            "bambu": Path.home() / "Library/Application Support/BambuStudio/system/BBL",
            "orca": Path.home() / "Library/Application Support/OrcaSlicer/system/BBL",
        }
    elif sys.platform == "win32":
        appdata = Path.home() / "AppData/Roaming"
        return {
            "bambu": appdata / "BambuStudio/system/BBL",
            "orca": appdata / "OrcaSlicer/system/BBL",
        }
    else:  # Linux and other Unix
        config = Path.home() / ".config"
        return {
            "bambu": config / "BambuStudio/system/BBL",
            "orca": config / "OrcaSlicer/system/BBL",
        }


SYSTEM_DIRS = _system_dirs()


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
        path = Path(name_or_path).resolve()
        if ".." in path.parts:
            raise ValueError(f"Profile path must not contain '..': {name_or_path}")
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


def resolve_profile_data(
    name_or_path: str,
    engine: str,
    category: str,
    project_dir: Path | None = None,
) -> dict:
    """Resolve a profile and flatten its full inheritance chain.

    Returns a merged dict with all inherited values resolved,
    with the 'inherits' key removed so the slicer uses it as-is.
    """
    path = resolve_profile(name_or_path, engine, category, project_dir)
    base = SYSTEM_DIRS.get(engine)

    chain = []
    current = path
    seen = set()
    while current:
        if str(current) in seen:
            break
        seen.add(str(current))
        with open(current) as f:
            data = json.load(f)
        chain.append(data)
        parent_name = data.get("inherits")
        if not parent_name:
            break
        # Check sibling directory first, then system dir
        sibling = current.parent / f"{parent_name}.json"
        system = (base / category / f"{parent_name}.json") if base else None
        if sibling.exists():
            current = sibling
        elif system and system.exists():
            current = system
        else:
            break

    # Merge root-first so leaf values override parents
    merged = {}
    for data in reversed(chain):
        merged.update(data)
    merged.pop("inherits", None)
    return merged


def pin_profiles(
    engine: str,
    printer: str | None,
    process: str | None,
    filaments: list[str],
    project_dir: Path,
) -> list[Path]:
    """Flatten and save profiles into <project_dir>/profiles/ for reproducibility.

    Profiles are fully resolved (inheritance chain merged, 'inherits' removed)
    so builds are independent of the installed slicer version.
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

        dest_dir = project_dir / "profiles" / category
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{name}.json"

        # Always re-flatten to pick up any slicer updates
        data = resolve_profile_data(name, engine, category)
        with open(dest, "w") as f:
            json.dump(data, f, indent=4)
        log.info("Pinned %s → %s (flattened)", name, dest)
        pinned.append(dest)

    return pinned
