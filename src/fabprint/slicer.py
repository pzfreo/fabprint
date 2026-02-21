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


def _write_tmp_profile(data: dict) -> Path:
    """Write a profile dict to a temp JSON file."""
    tmp = tempfile.NamedTemporaryFile(
        suffix=".json", prefix="fabprint_", delete=False, mode="w"
    )
    json.dump(data, tmp, indent=4)
    tmp.close()
    return Path(tmp.name)


def _apply_overrides(data: dict, overrides: dict[str, object], name: str) -> Path:
    """Create a temp profile JSON with overrides applied to resolved data."""

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

    tmp = tempfile.NamedTemporaryFile(
        suffix=".json", prefix="fabprint_", delete=False, mode="w"
    )
    json.dump(data, tmp, indent=4)
    tmp.close()
    return Path(tmp.name)


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
) -> Path:
    """Slice a 3MF file using BambuStudio or OrcaSlicer CLI.

    Profile names are resolved via profiles.resolve_profile().
    If overrides are provided, they are patched into the process profile.
    Returns the output directory containing the sliced gcode.
    """
    slicer = find_slicer(engine)
    input_3mf = input_3mf.resolve()

    if not input_3mf.exists():
        raise FileNotFoundError(f"Input file not found: {input_3mf}")

    if output_dir is None:
        output_dir = input_3mf.parent / "output"
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    tmp_files = []
    cmd = [str(slicer)]

    try:
        # Resolve and load settings (machine + process)
        # Profiles are flattened (inheritance resolved) to avoid issues
        # with the slicer not finding parent profiles from temp files.
        settings = []
        if printer:
            data = resolve_profile_data(printer, engine, "machine", project_dir)
            path = _write_tmp_profile(data)
            tmp_files.append(path)
            settings.append(str(path))
        if process:
            data = resolve_profile_data(process, engine, "process", project_dir)
            if overrides:
                path = _apply_overrides(data, overrides, process)
            else:
                path = _write_tmp_profile(data)
            tmp_files.append(path)
            settings.append(str(path))
        if settings:
            cmd.extend(["--load-settings", ";".join(settings)])

        if filaments:
            resolved = []
            for f in filaments:
                data = resolve_profile_data(f, engine, "filament", project_dir)
                path = _write_tmp_profile(data)
                tmp_files.append(path)
                resolved.append(str(path))
            cmd.extend(["--load-filaments", ";".join(resolved)])

        # Note: --load-filament-ids is only supported with STL inputs, not 3MF.
        # For 3MF, filament assignment must be embedded in the file itself.
        if filament_ids and not str(input_3mf).endswith(".3mf"):
            cmd.extend(["--load-filament-ids", ",".join(str(i) for i in filament_ids)])

        cmd.extend([
            "--slice", "0",
            "--outputdir", str(output_dir),
            str(input_3mf),
        ])

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
        log.info("Slicing complete. Output in %s", output_dir)
        return output_dir

    finally:
        for tmp in tmp_files:
            tmp.unlink(missing_ok=True)


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
