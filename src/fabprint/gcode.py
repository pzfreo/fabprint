"""Shared gcode metadata parsing utilities."""

from __future__ import annotations

import re
from pathlib import Path


def parse_gcode_metadata(gcode_path: Path) -> dict[str, str | float | int]:
    """Extract print time and filament stats from gcode comments.

    Scans header (first 300 lines) for print time, and tail (last 50 lines)
    for filament usage. Handles multiple OrcaSlicer/BambuStudio formats.

    Returns dict with keys like 'print_time', 'print_time_secs',
    'filament_g', and/or 'filament_cm3'.
    """
    lines = gcode_path.read_text().splitlines()
    stats: dict[str, str | float | int] = {}

    # Scan header for print time
    for line in lines[:300]:
        if m := re.search(r"total estimated time:\s*(.+?)(?:;|$)", line):
            stats["print_time"] = m.group(1).strip()
        elif m := re.match(r";\s*estimated printing time.*?=\s*(.+)", line):
            stats["print_time"] = m.group(1).strip()

    # Scan tail for filament stats. OrcaSlicer emits one line per slot
    # (including 0.00 for unused slots) then sometimes a total line.
    # Sum all values to get the true total; a single total line gives itself.
    filament_g_total = 0.0
    filament_cm3_total = 0.0
    for line in lines[-50:]:
        if m := re.match(r";\s*(?:total )?filament used \[g\]\s*=\s*([\d.]+)", line):
            filament_g_total += float(m.group(1))
        elif m := re.match(r";\s*(?:total )?filament used \[cm3\]\s*=\s*([\d.]+)", line):
            filament_cm3_total += float(m.group(1))
    if filament_g_total > 0:
        stats["filament_g"] = filament_g_total
    if filament_cm3_total > 0:
        stats["filament_cm3"] = filament_cm3_total

    # Convert time string like "1h 7m 32s" to seconds
    if "print_time" in stats:
        t = str(stats["print_time"])
        secs = 0
        if hm := re.search(r"(\d+)h", t):
            secs += int(hm.group(1)) * 3600
        if mm := re.search(r"(\d+)m", t):
            secs += int(mm.group(1)) * 60
        if sm := re.search(r"(\d+)s", t):
            secs += int(sm.group(1))
        if secs > 0:
            stats["print_time_secs"] = secs

    return stats
