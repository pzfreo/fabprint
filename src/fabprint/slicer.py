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


def _has_docker(image: str) -> bool:
    """Check if Docker is available and the given image exists."""
    try:
        r = subprocess.run(
            ["docker", "image", "inspect", image],
            capture_output=True,
            timeout=10,
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

    cmd.extend(
        [
            "--slice",
            "0",
            "--export-3mf",
            "plate_sliced.gcode.3mf",
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
        for i, f in enumerate(filaments):
            data = resolve_profile_data(f, engine, "filament", project_dir)
            path = _write_tmp_profile(data, tmp_dir, f"filament_{i}")
            resolved.append(str(path))
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


def _generate_plate_thumbnail(width: int = 256, height: int = 256) -> bytes:
    """Generate a minimal plate thumbnail PNG using pure Python (no Pillow).

    Returns PNG bytes for a dark plate graphic with 'fabprint' branding.
    """
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
    scale = 2
    tx = (width - text_w * scale) // 2
    ty = height // 2 - (char_h * scale) // 2

    rows = []
    for y in range(height):
        row = bytearray(width * 3)
        for x in range(width):
            # Plate rectangle
            mx, my = 20, 40
            if mx <= x < width - mx and my <= y < height - my:
                if y <= my + 2:
                    r, g, b = accent
                else:
                    r, g, b = plate_c
            else:
                r, g, b = bg

            # Text overlay
            sx = (x - tx) // scale
            sy = (y - ty) // scale
            if 0 <= sy < char_h and 0 <= sx < text_w:
                ci = sx // (char_w + spacing)
                cx = sx % (char_w + spacing)
                if ci < len(text) and cx < char_w:
                    ch = text[ci]
                    if ch in _font:
                        row_bits = _font[ch][sy]
                        if row_bits & (0x80 >> cx):
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


def _fix_sliced_3mf(path: Path) -> None:
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

        # Generate placeholder thumbnails
        thumb = _generate_plate_thumbnail(256, 256)
        thumb_small = _generate_plate_thumbnail(128, 128)
        existing_files = set(zin.namelist())

        # Rewrite the zip
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                if item.filename == "Metadata/project_settings.config":
                    zout.writestr(item, json.dumps(ps, indent=4))
                elif item.filename == "Metadata/model_settings.config" and ms_patched:
                    zout.writestr(item, ms_patched)
                else:
                    zout.writestr(item, zin.read(item.filename))

            # Add thumbnails if not already present
            if "Metadata/plate_1.png" not in existing_files:
                zout.writestr("Metadata/plate_1.png", thumb)
            if "Metadata/plate_1_small.png" not in existing_files:
                zout.writestr("Metadata/plate_1_small.png", thumb_small)

    path.write_bytes(buf.getvalue())
    log.info("Patched sliced 3mf for Bambu Connect compatibility")


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
    required_version: str | None = None,
) -> Path:
    """Slice a 3MF file using BambuStudio or OrcaSlicer CLI.

    Profile names are resolved via profiles.resolve_profile_data().
    If overrides are provided, they are patched into the process profile.

    Docker modes:
      docker=True          - use Docker with default image
      docker_version="X"   - use Docker with fabprint:orca-X image
      neither + no local   - fallback to Docker with default image

    If required_version is set (from config), the slicer version is checked
    and must match exactly. For Docker, the image tag is used as the version.

    Returns the output directory containing the sliced gcode.
    """
    # If config specifies a version and no explicit docker_version was given,
    # use it as the docker_version for Docker-based slicing.
    if required_version and not docker_version:
        docker_version = required_version
        docker = True

    use_docker = docker or docker_version is not None
    image = _docker_image(docker_version)

    if not use_docker:
        try:
            slicer = find_slicer(engine)
        except FileNotFoundError:
            if _has_docker(image):
                log.info("Slicer not found locally, falling back to Docker (%s)", image)
                use_docker = True
            else:
                raise

    if use_docker and not _has_docker(image):
        raise FileNotFoundError(
            f"Docker image '{image}' not found. "
            f"Build it with: docker build --build-arg "
            f"ORCA_VERSION={docker_version or 'X.Y.Z'} -t {image} ."
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

    print(f"Slicer: OrcaSlicer {detected_version or 'unknown'}{' (Docker)' if use_docker else ''}")

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
            _fix_sliced_3mf(result_dir / "plate_sliced.gcode.3mf")
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

        cmd.extend(
            [
                "--slice",
                "0",
                "--export-3mf",
                "plate_sliced.gcode.3mf",
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
        _fix_sliced_3mf(output_dir / "plate_sliced.gcode.3mf")
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
        elif m := re.match(r";\s*estimated printing time.*?=\s*(.+)", line):
            stats["print_time"] = m.group(1).strip()

    # Scan tail for filament stats
    for line in lines[-50:]:
        if m := re.match(r";\s*(?:total )?filament used \[g\]\s*=\s*([\d.]+)", line):
            stats["filament_g"] = float(m.group(1))
        elif m := re.match(r";\s*(?:total )?filament used \[cm3\]\s*=\s*([\d.]+)", line):
            stats["filament_cm3"] = float(m.group(1))

    return stats
