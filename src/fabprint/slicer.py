"""Shell out to BambuStudio or OrcaSlicer CLI for slicing."""

from __future__ import annotations

import logging
import subprocess
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


def slice_plate(
    input_3mf: Path,
    engine: str = "bambu",
    output_dir: Path | None = None,
    printer: str | None = None,
    process: str | None = None,
    filaments: list[str] | None = None,
    filament_ids: list[int] | None = None,
    project_dir: Path | None = None,
) -> Path:
    """Slice a 3MF file using BambuStudio or OrcaSlicer CLI.

    Profile names are resolved via profiles.resolve_profile().
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

    cmd = [str(slicer)]

    # Resolve and load settings (machine + process)
    settings = []
    if printer:
        path = resolve_profile(printer, engine, "machine", project_dir)
        settings.append(str(path))
    if process:
        path = resolve_profile(process, engine, "process", project_dir)
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
