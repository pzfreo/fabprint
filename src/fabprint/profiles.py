"""Discover, resolve, and pin slicer profiles."""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from fabprint import FabprintError

log = logging.getLogger(__name__)

CATEGORIES = ("machine", "process", "filament")


def _system_dirs() -> dict[str, Path]:
    """Return slicer system profile directories for the current platform."""
    if sys.platform == "darwin":
        return {
            "orca": Path.home() / "Library/Application Support/OrcaSlicer/system/BBL",
        }
    elif sys.platform == "win32":
        appdata = Path.home() / "AppData/Roaming"
        return {
            "orca": appdata / "OrcaSlicer/system/BBL",
        }
    else:  # Linux and other Unix
        config = Path.home() / ".config"
        return {
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

    # Expected JSON "type" field for each category.
    # machine_model profiles (e.g. "Bambu Lab P1S") define the printer
    # but cannot be passed to the slicer — only "machine" profiles
    # (e.g. "Bambu Lab P1S 0.4 nozzle") are valid for slicing.
    _VALID_TYPES = {
        "machine": "machine",
        "process": "process",
        "filament": "filament",
    }

    result: dict[str, dict[str, Path]] = {}
    for category in CATEGORIES:
        cat_dir = base / category
        profiles: dict[str, Path] = {}
        expected_type = _VALID_TYPES[category]
        if cat_dir.is_dir():
            for f in sorted(cat_dir.glob("*.json")):
                name = f.stem
                # Skip internal/template files
                if "template" in name or name.startswith("fdm_"):
                    continue
                # Only include profiles with the correct type
                try:
                    with open(f) as fh:
                        data = json.load(fh)
                    if data.get("type") != expected_type:
                        continue
                except (json.JSONDecodeError, OSError):
                    continue
                profiles[name] = f
        result[category] = profiles

    return result


_BUNDLED_DIR = Path(__file__).parent / "data"


def load_bundled_profiles(engine: str, version: str | None = None) -> dict[str, list[str]]:
    """Load profile names bundled with the package.

    Looks for ``src/fabprint/data/profiles.<engine>.<version>.json``.
    Falls back to the highest available version if the requested one is missing.

    Returns a dict of ``{category: [name, ...]}`` or an empty dict if none found.
    """
    if version:
        exact = _BUNDLED_DIR / f"profiles.{engine}.{version}.json"
        if exact.exists():
            with open(exact) as f:
                data = json.load(f)
            return {cat: data.get(cat, []) for cat in CATEGORIES}

    # Fall back to highest bundled version
    candidates = sorted(_BUNDLED_DIR.glob(f"profiles.{engine}.*.json"))
    if candidates:
        with open(candidates[-1]) as f:
            data = json.load(f)
        return {cat: data.get(cat, []) for cat in CATEGORIES}

    return {}


def discover_profile_names(
    engine: str,
    version: str | None = None,
    project_dir: Path | None = None,
) -> tuple[dict[str, list[str]], str]:
    """Discover profile names with full fallback chain.

    Priority:
    1. System profiles (local OrcaSlicer install)
    2. Pinned profiles in the project repo (``./profiles/``)
    3. Bundled profiles shipped with the package

    Returns ``(names_dict, source)`` where *source* is one of
    ``"system"``, ``"pinned"``, ``"bundled"``, or ``"none"``.
    """
    # 1. System profiles
    try:
        system = discover_profiles(engine)
    except ValueError:
        system = {}
    if any(system.values()):
        return {cat: sorted(system.get(cat, {}).keys()) for cat in CATEGORIES}, "system"

    # 2. Pinned profiles in repo
    if project_dir:
        pinned: dict[str, list[str]] = {}
        for cat in CATEGORIES:
            cat_dir = project_dir / "profiles" / cat
            if cat_dir.is_dir():
                pinned[cat] = sorted(f.stem for f in cat_dir.glob("*.json") if f.is_file())
        if any(pinned.values()):
            return pinned, "pinned"

    # 3. Bundled profiles
    bundled = load_bundled_profiles(engine, version)
    if any(bundled.values()):
        return bundled, "bundled"

    return {cat: [] for cat in CATEGORIES}, "none"


# ---------------------------------------------------------------------------
# Docker profile extraction
# ---------------------------------------------------------------------------

# Profile path inside the OrcaSlicer Docker container
_DOCKER_PROFILE_ROOT = "/opt/orca-slicer/resources/profiles/BBL"


def _docker_image_for_version(version: str | None) -> str:
    """Build the Docker image name for a given OrcaSlicer version."""
    from fabprint.slicer import DOCKERHUB_REPO

    if version:
        return f"{DOCKERHUB_REPO}:orca-{version}"
    return f"{DOCKERHUB_REPO}:latest"


def extract_docker_profiles(
    version: str | None = None,
    image: str | None = None,
) -> Path:
    """Extract OrcaSlicer profiles from a Docker image to a temp directory.

    Uses ``docker create`` + ``docker cp`` + ``docker rm`` to avoid
    starting the container (no Xvfb needed).

    Returns a Path to a temporary directory structured as
    ``<tmpdir>/{machine,process,filament}/*.json``.
    The caller is responsible for cleanup.
    """
    if not image:
        image = _docker_image_for_version(version)

    # Ensure image is available
    from fabprint.slicer import _ensure_docker_image

    if not _ensure_docker_image(image):
        raise FabprintError(
            f"Docker image {image} is not available and could not be pulled. "
            "Install OrcaSlicer locally or check your Docker setup."
        )

    tmp_dir = Path(tempfile.mkdtemp(prefix="fabprint_profiles_"))
    container_id = None
    try:
        # Create a stopped container (does not start it)
        result = subprocess.run(
            ["docker", "create", "--platform", "linux/amd64", image, "true"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise FabprintError(f"docker create failed: {result.stderr.strip()}")
        container_id = result.stdout.strip()

        # Copy profile directories out
        for category in CATEGORIES:
            src = f"{container_id}:{_DOCKER_PROFILE_ROOT}/{category}"
            dest = tmp_dir / category
            cp_result = subprocess.run(
                ["docker", "cp", src, str(dest)],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if cp_result.returncode != 0:
                log.debug("docker cp %s failed: %s", category, cp_result.stderr.strip())

    finally:
        # Clean up the container
        if container_id:
            subprocess.run(
                ["docker", "rm", container_id],
                capture_output=True,
                timeout=10,
            )

    return tmp_dir


def _resolve_profile_data_from_dir(
    name: str,
    category: str,
    base_dir: Path,
) -> dict:
    """Resolve and flatten a profile from a directory, walking the inheritance chain."""
    profile_path = base_dir / category / f"{name}.json"
    if not profile_path.exists():
        raise FileNotFoundError(f"Profile '{name}' not found in {base_dir / category}")

    chain = []
    current = profile_path
    seen: set[str] = set()
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
        parent = current.parent / f"{parent_name}.json"
        if parent.exists():
            current = parent
        else:
            break

    # Merge root-first so leaf values override parents
    merged: dict = {}
    for data in reversed(chain):
        merged.update(data)
    merged.pop("inherits", None)
    return merged


# ---------------------------------------------------------------------------
# Profile resolution
# ---------------------------------------------------------------------------


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
        if ".." in Path(name_or_path).parts:
            raise ValueError(f"Profile path must not contain '..': {name_or_path}")
        path = Path(name_or_path).resolve()
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
    seen: set[str] = set()
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
    merged: dict = {}
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
    docker_version: str | None = None,
) -> list[Path]:
    """Flatten and save profiles into <project_dir>/profiles/ for reproducibility.

    Profiles are fully resolved (inheritance chain merged, 'inherits' removed)
    so builds are independent of the installed slicer version.

    When a profile is not found locally, falls back to extracting it from
    the Docker image (if ``docker_version`` is provided).

    Returns list of pinned file paths.
    """
    pinned: list[Path] = []

    items: list[tuple[str, str]] = []
    if printer:
        items.append(("machine", printer))
    if process:
        items.append(("process", process))
    for f in filaments:
        items.append(("filament", f))

    # Try local resolution first; collect failures for Docker fallback
    docker_needed: list[tuple[str, str]] = []
    for category, name in items:
        if _is_path(name):
            log.info("Skipping '%s' (already a path)", name)
            continue

        dest_dir = project_dir / "profiles" / category
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{name}.json"

        try:
            data = resolve_profile_data(name, engine, category, project_dir)
            with open(dest, "w") as fh:
                json.dump(data, fh, indent=4)
            log.info("Pinned %s → %s (flattened)", name, dest)
            pinned.append(dest)
        except FileNotFoundError:
            docker_needed.append((category, name))

    # Docker fallback for profiles not found locally
    if docker_needed:
        if not docker_version:
            names = ", ".join(f"'{n}'" for _, n in docker_needed)
            raise FabprintError(
                f"Profile(s) {names} not found locally. Set slicer.version in your "
                "config or install OrcaSlicer to access profiles."
            )

        import shutil

        docker_dir = extract_docker_profiles(docker_version)
        try:
            for category, name in docker_needed:
                dest_dir = project_dir / "profiles" / category
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest = dest_dir / f"{name}.json"

                data = _resolve_profile_data_from_dir(name, category, docker_dir)
                with open(dest, "w") as fh:
                    json.dump(data, fh, indent=4)
                log.info("Pinned %s → %s (from Docker, flattened)", name, dest)
                pinned.append(dest)
        finally:
            shutil.rmtree(docker_dir, ignore_errors=True)

    return pinned


# ---------------------------------------------------------------------------
# Profile import (add)
# ---------------------------------------------------------------------------

# Keys commonly found in each profile category
_CATEGORY_KEYS: dict[str, set[str]] = {
    "machine": {"printer_model", "machine_start_gcode", "printable_area"},
    "process": {"layer_height", "wall_loops", "sparse_infill_density"},
    "filament": {"filament_type", "filament_density", "nozzle_temperature"},
}


def detect_category(data: dict) -> str | None:
    """Guess the profile category from its JSON keys."""
    scores: dict[str, int] = {}
    keys = set(data.keys())
    for cat, markers in _CATEGORY_KEYS.items():
        scores[cat] = len(keys & markers)
    best = max(scores, key=lambda c: scores[c])
    return best if scores[best] > 0 else None


def add_profile(
    source: str,
    project_dir: Path,
    category: str | None = None,
    name: str | None = None,
) -> Path:
    """Import a profile JSON file into the project's profiles directory.

    Args:
        source: A local file path or URL (http/https).
        project_dir: The project root containing ``profiles/``.
        category: Profile category (machine/process/filament).
            Auto-detected from JSON content if not provided.
        name: Profile name (default: filename stem or JSON ``"name"`` field).

    Returns:
        Path to the imported profile file.
    """
    # Load the JSON from source
    if source.startswith(("http://", "https://")):
        try:
            with urlopen(source, timeout=30) as resp:  # noqa: S310
                raw = resp.read()
        except (URLError, OSError) as e:
            raise FabprintError(f"Failed to download profile from {source}: {e}") from e
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise FabprintError(f"Invalid JSON from {source}: {e}") from e
        default_name = Path(source.split("/")[-1]).stem
    else:
        path = Path(source)
        if not path.exists():
            raise FabprintError(f"Profile file not found: {source}")
        with open(path) as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError as e:
                raise FabprintError(f"Invalid JSON in {source}: {e}") from e
        default_name = path.stem

    if not isinstance(data, dict):
        raise FabprintError(f"Profile must be a JSON object, got {type(data).__name__}")

    # Determine category
    if not category:
        category = detect_category(data)
        if not category:
            raise FabprintError(
                f"Cannot auto-detect profile category for {source}. "
                "Use --category to specify machine, process, or filament."
            )

    if category not in CATEGORIES:
        raise FabprintError(
            f"Invalid category '{category}'. Must be one of: {', '.join(CATEGORIES)}"
        )

    # Determine name
    profile_name = name or data.get("name") or default_name

    # Write to profiles directory
    dest_dir = project_dir / "profiles" / category
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{profile_name}.json"

    with open(dest, "w") as fh:
        json.dump(data, fh, indent=4)

    # Warn about unresolved inheritance
    if data.get("inherits"):
        parent = data["inherits"]
        parent_path = dest_dir / f"{parent}.json"
        if not parent_path.exists():
            log.warning(
                "Profile '%s' inherits from '%s' which is not in %s. "
                "Add the parent profile or use 'fabprint profiles pin' to flatten.",
                profile_name,
                parent,
                dest_dir,
            )

    return dest
