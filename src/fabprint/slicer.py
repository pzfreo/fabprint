"""Shell out to BambuStudio or OrcaSlicer CLI for slicing."""

from __future__ import annotations

import json
import logging
import re
import subprocess
import tempfile
from pathlib import Path

from fabprint.profiles import resolve_profile

log = logging.getLogger(__name__)

SLICER_PATHS = {
    "bambu": Path("/Applications/BambuStudio.app/Contents/MacOS/BambuStudio"),
    "orca": Path("/Applications/OrcaSlicer.app/Contents/MacOS/OrcaSlicer"),
}


def find_slicer(engine: str) -> Path:
    """Find the slicer executable for the given engine."""
    path = SLICER_PATHS.get(engine)
    if path is None:
        raise ValueError(f"Unknown slicer engine: '{engine}'. Supported: {list(SLICER_PATHS)}")
    if not path.exists():
        raise FileNotFoundError(
            f"{engine} slicer not found at {path}. "
            f"Is {'BambuStudio' if engine == 'bambu' else 'OrcaSlicer'} installed?"
        )
    return path


def _apply_overrides(profile_path: Path, overrides: dict[str, object]) -> Path:
    """Create a temp copy of a profile JSON with overrides applied."""
    with open(profile_path) as f:
        data = json.load(f)

    applied = []
    for key, value in overrides.items():
        old = data.get(key, "<unset>")
        data[key] = value
        applied.append(f"  {key}: {old} → {value}")

    log.info(
        "Applied %d override(s) to %s:\n%s",
        len(applied), profile_path.name, "\n".join(applied),
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
        settings = []
        if printer:
            path = resolve_profile(printer, engine, "machine", project_dir)
            settings.append(str(path))
        if process:
            path = resolve_profile(process, engine, "process", project_dir)
            if overrides:
                path = _apply_overrides(path, overrides)
                tmp_files.append(path)
            settings.append(str(path))
        if settings:
            cmd.extend(["--load-settings", ";".join(settings)])

        if filaments:
            resolved = []
            for f in filaments:
                path = resolve_profile(f, engine, "filament", project_dir)
                resolved.append(str(path))
            cmd.extend(["--load-filaments", ";".join(resolved)])

        if filament_ids:
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
    """Parse filament usage and print time from gcode header comments.

    Looks for OrcaSlicer/BambuStudio comment lines like:
      ; filament used [g] = 42.94
      ; total filament used [g] = 42.94
      ; estimated printing time (normal mode) = 1h 33m 15s
    Returns dict with 'filament_g' (float) and/or 'print_time' (str).
    """
    gcode_files = list(output_dir.glob("*.gcode"))
    if not gcode_files:
        return {}

    stats: dict[str, str | float] = {}
    with open(gcode_files[0]) as f:
        for i, line in enumerate(f):
            if i > 300:
                break
            if m := re.match(
                r";\s*(?:total )?filament used \[g\]\s*=\s*([\d.]+)", line
            ):
                stats["filament_g"] = float(m.group(1))
            elif m := re.match(
                r";\s*estimated printing time.*?=\s*(.+)", line
            ):
                stats["print_time"] = m.group(1).strip()
    return stats
