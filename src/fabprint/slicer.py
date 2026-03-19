"""Shell out to BambuStudio or OrcaSlicer CLI for slicing."""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from fabprint import require_file
from fabprint.gcode import parse_gcode_metadata
from fabprint.profiles import resolve_profile_data
from fabprint.thumbnails import generate_plate_thumbnail

log = logging.getLogger(__name__)

DOCKERHUB_REPO = "fabprint/fabprint"


def _slicer_paths() -> dict[str, Path]:
    """Return default slicer executable paths for the current platform."""
    if sys.platform == "darwin":
        return {
            "orca": Path("/Applications/OrcaSlicer.app/Contents/MacOS/OrcaSlicer"),
        }
    elif sys.platform == "win32":
        pf = Path("C:/Program Files")
        return {
            "orca": pf / "OrcaSlicer/orca-slicer.exe",
        }
    else:  # Linux and other Unix
        return {
            "orca": Path("/usr/bin/orca-slicer"),
        }


SLICER_PATHS = _slicer_paths()


def _docker_image(version: str | None = None) -> str:
    """Return the Docker image name for a given OrcaSlicer version."""
    if version:
        return f"{DOCKERHUB_REPO}:orca-{version}"
    return f"{DOCKERHUB_REPO}:latest"


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
    for name in ("orca-slicer", "OrcaSlicer", "OrcaSlicer.AppImage"):
        found = shutil.which(name)
        if found:
            return Path(found)

    raise FileNotFoundError(f"OrcaSlicer not found at {path} or on PATH. Is OrcaSlicer installed?")


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
        len(applied),
        name,
        "\n".join(applied),
    )
    return data


def _detect_slicer_version(slicer: Path) -> str | None:
    """Detect the version of a local slicer by parsing --help output."""
    try:
        r = subprocess.run(
            [str(slicer), "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        # OrcaSlicer prints "OrcaSlicer-2.3.1:" on the first few lines
        for line in (r.stdout + r.stderr).splitlines()[:5]:
            m = re.search(r"OrcaSlicer[- ]([\d][^\s:]+)", line)
            if m:
                return m.group(1)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def _check_slicer_version(
    actual: str | None,
    required: str,
    source: str,
) -> None:
    """Raise if the detected slicer version doesn't match the required one."""
    if actual is None:
        raise RuntimeError(
            f"Could not detect {source} slicer version; config requires version {required}"
        )
    if actual != required:
        raise RuntimeError(
            f"{source} slicer version {actual} does not match config-required version {required}"
        )


def _has_docker_image(image: str) -> bool:
    """Check if the given Docker image exists locally."""
    try:
        r = subprocess.run(
            ["docker", "image", "inspect", image],
            capture_output=True,
            timeout=10,
        )
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _pull_docker_image(image: str) -> bool:
    """Pull a Docker image from the registry. Returns True on success."""
    log.info("Pulling Docker image %s ...", image)
    try:
        r = subprocess.run(
            ["docker", "pull", image],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if r.returncode == 0:
            return True
        log.debug("docker pull failed: %s", r.stderr.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return False


def _ensure_docker_image(image: str) -> bool:
    """Ensure a Docker image is available locally, pulling if needed."""
    if _has_docker_image(image):
        return True
    return _pull_docker_image(image)


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
        "docker",
        "run",
        "--rm",
        "--platform",
        "linux/amd64",
        "-v",
        f"{input_3mf}:/work/input.3mf:ro",
        "-v",
        f"{output_dir}:/work/output",
        "--entrypoint",
        "orca-slicer",
        image,
    ]

    if settings_arg:
        rewritten = settings_arg.replace(host_prefix, container_prefix)
        cmd.extend(["--load-settings", rewritten])
    if filament_arg:
        rewritten = filament_arg.replace(host_prefix, container_prefix)
        cmd.extend(["--load-filaments", rewritten])

    sliced_3mf_name = input_3mf.stem + "_sliced.gcode.3mf"
    cmd.extend(
        [
            "--slice",
            "0",
            "--export-3mf",
            sliced_3mf_name,
            "--min-save",
            "--outputdir",
            "/work/output",
            "/work/input.3mf",
        ]
    )

    log.info("Slicing via Docker (%s): %s", image, " ".join(cmd))

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

    if result.returncode != 0:
        log.error("Docker slicer stderr:\n%s", result.stderr)
        raise RuntimeError(
            f"Docker slicer failed (exit code {result.returncode}):\n{result.stderr[:500]}"
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
        # Resolve real profiles; reuse the first for gap (empty) slots
        first_path: str | None = None
        for i, f in enumerate(filaments):
            if f:
                data = resolve_profile_data(f, engine, "filament", project_dir)
                path = _write_tmp_profile(data, tmp_dir, f"filament_{i}")
                resolved.append(str(path))
                if first_path is None:
                    first_path = str(path)
            else:
                resolved.append("")
        # Fill gap slots with the first resolved profile (same file, no re-resolve)
        if first_path:
            resolved = [p if p else first_path for p in resolved]
        filament_arg = ";".join(resolved)

    settings_arg = ";".join(settings) if settings else None
    return settings_arg, filament_arg


# Keys that OrcaSlicer CLI --min-save omits but Bambu Connect requires.
_BC_DEFAULT_KEYS: dict[str, object] = {
    "bbl_use_printhost": "1",
    "default_bed_type": "",
    "filament_retract_lift_above": ["0"],
    "filament_retract_lift_below": ["0"],
    "filament_retract_lift_enforce": [""],
    "host_type": "octoprint",
    "pellet_flow_coefficient": "0",
    "pellet_modded_printer": "0",
    "printhost_authorization_type": "key",
    "printhost_ssl_ignore_revoke": "0",
    "thumbnails_format": "BTT_TFT",
}

# Minimum array length for filament-related settings in project_settings.
# Bambu Connect rejects files where these arrays are shorter than the
# printer's AMS slot count. 5 covers P1S (4-slot AMS + external spool).
_MIN_FILAMENT_SLOTS = 5


def _fix_sliced_3mf(path: Path, plate_3mf: Path | None = None) -> None:
    """Post-process a --min-save 3mf so Bambu Connect accepts it.

    OrcaSlicer CLI's --min-save export needs three fixes:
    1. project_settings.config — short filament arrays and missing keys
    2. model_settings.config — filament_maps padding + thumbnail references
    3. Thumbnail PNGs — add placeholder images
    """
    import io
    import re as _re

    if not path.exists():
        return

    with zipfile.ZipFile(path, "r") as zin:
        try:
            ps_raw = zin.read("Metadata/project_settings.config")
        except KeyError:
            return  # No project_settings — nothing to fix

        # --- Fix project_settings.config ---
        ps = json.loads(ps_raw)
        for key, default in _BC_DEFAULT_KEYS.items():
            if key not in ps:
                ps[key] = default
        for key, val in ps.items():
            if isinstance(val, list) and 0 < len(val) < _MIN_FILAMENT_SLOTS:
                while len(val) < _MIN_FILAMENT_SLOTS:
                    val.append(val[-1])

        # --- Fix model_settings.config ---
        try:
            ms_raw = zin.read("Metadata/model_settings.config").decode()
        except KeyError:
            ms_raw = None

        ms_patched = None
        if ms_raw:
            # Pad filament_maps value (e.g. "1" -> "1 1 1 1 1")
            def _pad_filament_maps(m: _re.Match) -> str:
                val = m.group(1)
                parts = val.split()
                while len(parts) < _MIN_FILAMENT_SLOTS:
                    parts.append(parts[-1] if parts else "1")
                return f'key="filament_maps" value="{" ".join(parts)}"'

            ms_patched = _re.sub(
                r'key="filament_maps" value="([^"]*)"',
                _pad_filament_maps,
                ms_raw,
            )

            # Add missing metadata keys that Bambu Connect requires.
            # Thumbnail/bbox references are needed even if files don't exist.
            extra_keys = {
                "thumbnail_file": "Metadata/plate_1.png",
                "thumbnail_no_light_file": "Metadata/plate_no_light_1.png",
                "top_file": "Metadata/top_1.png",
                "pick_file": "Metadata/pick_1.png",
                "pattern_bbox_file": "Metadata/plate_1.json",
            }
            for key, val in extra_keys.items():
                if f'key="{key}"' not in ms_patched:
                    ms_patched = ms_patched.replace(
                        "  </plate>",
                        f'    <metadata key="{key}" value="{val}"/>\n  </plate>',
                    )

        # Check if OrcaSlicer generated valid thumbnails (requires Xvfb).
        # A valid PNG is > 1KB; broken headless ones are empty or tiny.
        _THUMB_MIN_SIZE = 1024
        thumbnail_overrides: dict[str, bytes] = {}
        thumb_files = {
            "Metadata/plate_1.png": (256, 256),
            "Metadata/plate_no_light_1.png": (256, 256),
            "Metadata/plate_1_small.png": (128, 128),
        }
        for fname, (w, h) in thumb_files.items():
            try:
                existing = zin.read(fname)
                if len(existing) >= _THUMB_MIN_SIZE:
                    continue  # OrcaSlicer generated a valid thumbnail
            except KeyError:
                pass
            thumbnail_overrides[fname] = generate_plate_thumbnail(w, h, plate_3mf)

        # Rewrite the zip
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                if item.filename in thumbnail_overrides:
                    pass  # replaced below
                elif item.filename == "Metadata/project_settings.config":
                    zout.writestr(item, json.dumps(ps, indent=4))
                elif item.filename == "Metadata/model_settings.config" and ms_patched:
                    zout.writestr(item, ms_patched)
                else:
                    zout.writestr(item, zin.read(item.filename))

            # Always write generated thumbnails (replace OrcaSlicer's broken ones)
            for fname, data in thumbnail_overrides.items():
                zout.writestr(fname, data)

    path.write_bytes(buf.getvalue())
    log.info("Patched sliced 3mf for Bambu Connect compatibility")


def slice_plate(
    input_3mf: Path,
    engine: str = "orca",
    output_dir: Path | None = None,
    printer: str | None = None,
    process: str | None = None,
    filaments: list[str] | None = None,
    filament_ids: list[int] | None = None,
    overrides: dict[str, object] | None = None,
    project_dir: Path | None = None,
    local: bool = False,
    docker_version: str | None = None,
    required_version: str | None = None,
) -> Path:
    """Slice a 3MF file using BambuStudio or OrcaSlicer CLI.

    Profile names are resolved via profiles.resolve_profile_data().
    If overrides are provided, they are patched into the process profile.

    Slicer selection:
      local=True           - force local slicer, fail if not installed
      docker_version="X"   - force Docker with fabprint:orca-X image
      neither (default)    - try Docker first, fall back to local

    If required_version is set (from config), the slicer version is checked
    and must match exactly. For Docker, the image tag is used as the version.

    Returns the output directory containing the sliced gcode.
    """
    # If config specifies a version and no explicit docker_version was given,
    # use it as the docker_version for Docker-based slicing.
    if required_version and not docker_version:
        docker_version = required_version

    if not docker_version and not local:
        print(
            "  \033[33mWarning: No slicer.version set in config. "
            'Pin a version (e.g. version = "2.3.1") for reproducible builds.\033[0m'
        )

    image = _docker_image(docker_version)

    if local:
        # Force local — no Docker fallback
        use_docker = False
        slicer = find_slicer(engine)
    elif docker_version is not None:
        # Explicit Docker version requested
        use_docker = True
        if not _ensure_docker_image(image):
            raise FileNotFoundError(
                f"Docker image '{image}' not found locally or on Docker Hub. "
                f"Check your Docker login or build locally with: docker build "
                f"--build-arg ORCA_VERSION={docker_version or 'X.Y.Z'} -t {image} ."
            )
    else:
        # Default: try Docker first, fall back to local
        if _ensure_docker_image(image):
            use_docker = True
        else:
            try:
                slicer = find_slicer(engine)
                use_docker = False
                print(
                    "  \033[33mWarning: Docker not available, using local slicer. "
                    "Builds may not be reproducible across machines.\033[0m"
                )
            except FileNotFoundError:
                raise FileNotFoundError(
                    "No slicer available. Install OrcaSlicer locally or "
                    "pull a Docker image: docker pull fabprint/fabprint:orca-2.3.1"
                )

    # Detect and verify slicer version
    if use_docker:
        detected_version = docker_version
    else:
        detected_version = _detect_slicer_version(slicer)

    if required_version:
        _check_slicer_version(
            detected_version, required_version, "Docker" if use_docker else "local"
        )

    docker_str = " (Docker)" if use_docker else ""
    log.debug("Slicer: OrcaSlicer %s%s", detected_version or "unknown", docker_str)

    input_3mf = input_3mf.resolve()
    require_file(input_3mf, "Input 3MF file")

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
            engine,
            printer,
            process,
            filaments,
            overrides,
            project_dir,
            tmp_dir,
        )

        if use_docker:
            result_dir = _slice_via_docker(
                input_3mf,
                output_dir,
                tmp_dir,
                settings_arg,
                filament_arg,
                image,
            )
            _fix_sliced_3mf(result_dir / (input_3mf.stem + "_sliced.gcode.3mf"), input_3mf)
            return result_dir

        # Local slicer path
        cmd = [str(slicer)]
        if settings_arg:
            cmd.extend(["--load-settings", settings_arg])
        if filament_arg:
            cmd.extend(["--load-filaments", filament_arg])

        # --load-filament-ids only works with STL inputs, not 3MF
        if filament_ids and not str(input_3mf).endswith(".3mf"):
            cmd.extend(["--load-filament-ids", ",".join(str(i) for i in filament_ids)])

        sliced_3mf_name = input_3mf.stem + "_sliced.gcode.3mf"
        cmd.extend(
            [
                "--slice",
                "0",
                "--export-3mf",
                sliced_3mf_name,
                "--min-save",
                "--outputdir",
                str(output_dir),
                str(input_3mf),
            ]
        )

        log.info("Slicing with %s: %s", engine, " ".join(cmd))

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
        )

        if result.returncode != 0:
            log.error("Slicer stderr:\n%s", result.stderr)
            raise RuntimeError(
                f"Slicer failed (exit code {result.returncode}):\n{result.stderr[:500]}"
            )

        log.info("Slicer stdout:\n%s", result.stdout)
        _fix_sliced_3mf(output_dir / sliced_3mf_name, input_3mf)
        log.info("Slicing complete. Output in %s", output_dir)
        return output_dir

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def parse_gcode_stats(output_dir: Path) -> dict[str, str | float | int]:
    """Parse filament usage and print time from gcode in an output directory.

    Finds the first .gcode file and delegates to gcode.parse_gcode_metadata().
    Returns dict with 'filament_g' and/or 'filament_cm3' and/or 'print_time'.
    """
    gcode_files = list(output_dir.glob("*.gcode"))
    if not gcode_files:
        return {}

    return parse_gcode_metadata(gcode_files[0])
