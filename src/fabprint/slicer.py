"""Shell out to BambuStudio or OrcaSlicer CLI for slicing."""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from fabprint.profiles import resolve_profile_data

log = logging.getLogger(__name__)

DEFAULT_DOCKER_IMAGE = "fabprint:latest"


def _slicer_paths() -> dict[str, Path]:
    """Return default slicer executable paths for the current platform."""
    if sys.platform == "darwin":
        return {
            "bambu": Path("/Applications/BambuStudio.app/Contents/MacOS/BambuStudio"),
            "orca": Path("/Applications/OrcaSlicer.app/Contents/MacOS/OrcaSlicer"),
        }
    elif sys.platform == "win32":
        pf = Path("C:/Program Files")
        return {
            "bambu": pf / "BambuStudio/bambu-studio.exe",
            "orca": pf / "OrcaSlicer/orca-slicer.exe",
        }
    else:  # Linux and other Unix
        return {
            "bambu": Path("/usr/bin/bambu-studio"),
            "orca": Path("/usr/bin/orca-slicer"),
        }


SLICER_PATHS = _slicer_paths()


def _docker_image(version: str | None = None) -> str:
    """Return the Docker image name for a given OrcaSlicer version."""
    if version:
        return f"fabprint:orca-{version}"
    return DEFAULT_DOCKER_IMAGE


def find_slicer(engine: str) -> Path:
    """Find the slicer executable for the given engine.

    Checks the platform-specific default path first, then falls back
    to searching PATH (useful on Linux or custom installs).
    """
    if engine not in SLICER_PATHS:
        raise ValueError(f"Unknown slicer engine: '{engine}'. Supported: {list(SLICER_PATHS)}")

    path = SLICER_PATHS[engine]
    if path.exists():
        return path

    # Fall back to PATH lookup (handles AppImage, Flatpak, AUR, custom installs)
    exe_names = {
        "bambu": ["bambu-studio", "BambuStudio", "BambuStudio.AppImage"],
        "orca": ["orca-slicer", "OrcaSlicer", "OrcaSlicer.AppImage"],
    }
    for name in exe_names.get(engine, []):
        found = shutil.which(name)
        if found:
            return Path(found)

    app_name = "BambuStudio" if engine == "bambu" else "OrcaSlicer"
    raise FileNotFoundError(
        f"{engine} slicer not found at {path} or on PATH. Is {app_name} installed?"
    )


def _write_tmp_profile(data: dict, tmp_dir: Path, name: str) -> Path:
    """Write a profile dict to a JSON file in the given temp directory."""
    path = tmp_dir / f"{name}.json"
    path.write_text(json.dumps(data, indent=4))
    return path


def _apply_overrides(data: dict, overrides: dict[str, object], name: str) -> dict:
    """Apply overrides to resolved profile data, returning the modified dict."""
    applied = []
    for key, value in overrides.items():
        old = data.get(key, "<unset>")
        # Slicer profiles store all values as strings
        data[key] = str(value)
        applied.append(f"  {key}: {old} → {value}")

    log.info(
        "Applied %d override(s) to %s:\n%s",
        len(applied), name, "\n".join(applied),
    )
    return data


def _has_docker(image: str) -> bool:
    """Check if Docker is available and the given image exists."""
    try:
        r = subprocess.run(
            ["docker", "image", "inspect", image],
            capture_output=True, timeout=10,
        )
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _slice_via_docker(
    input_3mf: Path,
    output_dir: Path,
    profile_dir: Path,
    settings_arg: str | None,
    filament_arg: str | None,
    image: str,
) -> Path:
    """Run the slicer inside the fabprint Docker container.

    Profile files live under output_dir/.profiles/ so they're accessible
    via the same volume mount as the output directory. No separate mount
    needed (avoids macOS Docker temp-dir visibility issues).
    """
    input_3mf = input_3mf.resolve()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    # Profile dir is under output_dir, so rewrite paths relative to /work/output
    host_prefix = str(profile_dir)
    container_prefix = "/work/output/" + profile_dir.name

    cmd = [
        "docker", "run", "--rm",
        "--platform", "linux/amd64",
        "-v", f"{input_3mf}:/work/input.3mf:ro",
        "-v", f"{output_dir}:/work/output",
        "--entrypoint", "orca-slicer",
        image,
    ]

    if settings_arg:
        rewritten = settings_arg.replace(host_prefix, container_prefix)
        cmd.extend(["--load-settings", rewritten])
    if filament_arg:
        rewritten = filament_arg.replace(host_prefix, container_prefix)
        cmd.extend(["--load-filaments", rewritten])

    cmd.extend([
        "--slice", "0",
        "--outputdir", "/work/output",
        "/work/input.3mf",
    ])

    log.info("Slicing via Docker (%s): %s", image, " ".join(cmd))

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

    if result.returncode != 0:
        log.error("Docker slicer stderr:\n%s", result.stderr)
        raise RuntimeError(
            f"Docker slicer failed (exit code {result.returncode}):\n"
            f"{result.stderr[:500]}"
        )

    log.info("Docker slicer stdout:\n%s", result.stdout)
    log.info("Slicing complete. Output in %s", output_dir)
    return output_dir


def _resolve_profiles(
    engine: str,
    printer: str | None,
    process: str | None,
    filaments: list[str] | None,
    overrides: dict[str, object] | None,
    project_dir: Path | None,
    tmp_dir: Path,
) -> tuple[str | None, str | None]:
    """Resolve and flatten all profiles into tmp_dir.

    Returns (settings_arg, filament_arg) — semicolon-separated paths
    suitable for --load-settings and --load-filaments.
    """
    settings = []
    if printer:
        data = resolve_profile_data(printer, engine, "machine", project_dir)
        path = _write_tmp_profile(data, tmp_dir, "machine")
        settings.append(str(path))
    if process:
        data = resolve_profile_data(process, engine, "process", project_dir)
        if overrides:
            data = _apply_overrides(data, overrides, process)
        path = _write_tmp_profile(data, tmp_dir, "process")
        settings.append(str(path))

    filament_arg = None
    if filaments:
        resolved = []
        for i, f in enumerate(filaments):
            data = resolve_profile_data(f, engine, "filament", project_dir)
            path = _write_tmp_profile(data, tmp_dir, f"filament_{i}")
            resolved.append(str(path))
        filament_arg = ";".join(resolved)

    settings_arg = ";".join(settings) if settings else None
    return settings_arg, filament_arg


def slice_plate(
    input_3mf: Path,
    engine: str = "bambu",
    output_dir: Path | None = None,
    printer: str | None = None,
    process: str | None = None,
    filaments: list[str] | None = None,
    filament_ids: list[int] | None = None,
    overrides: dict[str, object] | None = None,
    project_dir: Path | None = None,
    docker: bool = False,
    docker_version: str | None = None,
) -> Path:
    """Slice a 3MF file using BambuStudio or OrcaSlicer CLI.

    Profile names are resolved via profiles.resolve_profile_data().
    If overrides are provided, they are patched into the process profile.

    Docker modes:
      docker=True          - use Docker with default image
      docker_version="X"   - use Docker with fabprint:orca-X image
      neither + no local   - fallback to Docker with default image

    Returns the output directory containing the sliced gcode.
    """
    use_docker = docker or docker_version is not None
    image = _docker_image(docker_version)

    if not use_docker:
        try:
            slicer = find_slicer(engine)
        except FileNotFoundError:
            if _has_docker(image):
                log.info(
                    "Slicer not found locally, falling back to Docker (%s)", image
                )
                use_docker = True
            else:
                raise

    if use_docker and not _has_docker(image):
        raise FileNotFoundError(
            f"Docker image '{image}' not found. "
            f"Build it with: docker build --build-arg "
            f"ORCA_VERSION={docker_version or 'X.Y.Z'} -t {image} ."
        )

    input_3mf = input_3mf.resolve()
    if not input_3mf.exists():
        raise FileNotFoundError(f"Input file not found: {input_3mf}")

    if output_dir is None:
        output_dir = input_3mf.parent / "output"
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    # For Docker: write profiles under output_dir so they share the same mount.
    # For local: use system temp (faster, auto-cleaned).
    if use_docker:
        tmp_dir = output_dir / ".profiles"
        tmp_dir.mkdir(exist_ok=True)
    else:
        tmp_dir = Path(tempfile.mkdtemp(prefix="fabprint_"))

    try:
        settings_arg, filament_arg = _resolve_profiles(
            engine, printer, process, filaments, overrides, project_dir, tmp_dir,
        )

        if use_docker:
            return _slice_via_docker(
                input_3mf, output_dir, tmp_dir,
                settings_arg, filament_arg, image,
            )

        # Local slicer path
        cmd = [str(slicer)]
        if settings_arg:
            cmd.extend(["--load-settings", settings_arg])
        if filament_arg:
            cmd.extend(["--load-filaments", filament_arg])

        # --load-filament-ids only works with STL inputs, not 3MF
        if filament_ids and not str(input_3mf).endswith(".3mf"):
            cmd.extend(["--load-filament-ids", ",".join(str(i) for i in filament_ids)])

        cmd.extend([
            "--slice", "0",
            "--outputdir", str(output_dir),
            str(input_3mf),
        ])

        log.info("Slicing with %s: %s", engine, " ".join(cmd))

        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300,
        )

        if result.returncode != 0:
            log.error("Slicer stderr:\n%s", result.stderr)
            raise RuntimeError(
                f"Slicer failed (exit code {result.returncode}):\n"
                f"{result.stderr[:500]}"
            )

        log.info("Slicer stdout:\n%s", result.stdout)
        log.info("Slicing complete. Output in %s", output_dir)
        return output_dir

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def parse_gcode_stats(output_dir: Path) -> dict[str, str | float]:
    """Parse filament usage and print time from gcode comments.

    Scans header (first 300 lines) for print time, and tail (last 50 lines)
    for filament usage. Handles multiple OrcaSlicer/BambuStudio formats.
    Returns dict with 'filament_g' and/or 'filament_cm3' and/or 'print_time'.
    """
    gcode_files = list(output_dir.glob("*.gcode"))
    if not gcode_files:
        return {}

    stats: dict[str, str | float] = {}
    lines = gcode_files[0].read_text().splitlines()

    # Scan header for print time
    for line in lines[:300]:
        if m := re.search(r"total estimated time:\s*(.+?)(?:;|$)", line):
            stats["print_time"] = m.group(1).strip()
        elif m := re.match(
            r";\s*estimated printing time.*?=\s*(.+)", line
        ):
            stats["print_time"] = m.group(1).strip()

    # Scan tail for filament stats
    for line in lines[-50:]:
        if m := re.match(
            r";\s*(?:total )?filament used \[g\]\s*=\s*([\d.]+)", line
        ):
            stats["filament_g"] = float(m.group(1))
        elif m := re.match(
            r";\s*(?:total )?filament used \[cm3\]\s*=\s*([\d.]+)", line
        ):
            stats["filament_cm3"] = float(m.group(1))

    return stats
