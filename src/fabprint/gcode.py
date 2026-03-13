"""Shared gcode metadata parsing utilities."""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
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
    # (including 0.00 for unused slots) and sometimes a separate total line.
    # Prefer explicit "total" lines; fall back to summing per-slot lines.
    filament_g_slots: list[float] = []
    filament_g_total: float | None = None
    filament_cm3_slots: list[float] = []
    filament_cm3_total: float | None = None
    for line in lines[-50:]:
        if m := re.match(r";\s*total filament used \[g\]\s*=\s*([\d.]+)", line):
            filament_g_total = float(m.group(1))
        elif m := re.match(r";\s*filament used \[g\]\s*=\s*([\d.]+)", line):
            filament_g_slots.append(float(m.group(1)))
        elif m := re.match(r";\s*total filament used \[cm3\]\s*=\s*([\d.]+)", line):
            filament_cm3_total = float(m.group(1))
        elif m := re.match(r";\s*filament used \[cm3\]\s*=\s*([\d.]+)", line):
            filament_cm3_slots.append(float(m.group(1)))
    g = filament_g_total if filament_g_total is not None else sum(filament_g_slots)
    cm3 = filament_cm3_total if filament_cm3_total is not None else sum(filament_cm3_slots)
    if g > 0:
        stats["filament_g"] = g
    if cm3 > 0:
        stats["filament_cm3"] = cm3

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


@dataclass
class LayerSpan:
    """A contiguous range of layers using the same extruder."""

    start_layer: int
    end_layer: int
    extruder: int  # 0-indexed
    start_z: float
    end_z: float


@dataclass
class GcodeInfo:
    """Parsed gcode analysis result."""

    filament_types: list[str] = field(default_factory=list)  # per-slot filament types
    layer_count: int = 0
    spans: list[LayerSpan] = field(default_factory=list)
    filament_changes: int = 0
    filament_usage_g: list[float] = field(default_factory=list)  # per-slot grams
    print_time: str = ""


def read_gcode(path: Path) -> str:
    """Read gcode from a .gcode file or from inside a .gcode.3mf zip."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    if path.suffix == ".3mf" or path.name.endswith(".gcode.3mf"):
        with zipfile.ZipFile(path, "r") as zf:
            gcode_names = [n for n in zf.namelist() if n.endswith(".gcode")]
            if not gcode_names:
                raise ValueError(f"No .gcode file found inside {path}")
            return zf.read(gcode_names[0]).decode("utf-8", errors="replace")

    return path.read_text()


def analyze_gcode(path: Path) -> GcodeInfo:
    """Analyze gcode for extruder usage per layer.

    Parses layer boundaries (CHANGE_LAYER), tool changes (T{n}),
    filament types, and per-slot usage from OrcaSlicer/BambuStudio gcode.
    """
    text = read_gcode(path)
    lines = text.splitlines()
    info = GcodeInfo()

    # Parse filament types from header
    for line in lines[:300]:
        if m := re.match(r";\s*filament_type\s*=\s*(.+)", line):
            info.filament_types = [t.strip() for t in m.group(1).split(";")]
        elif m := re.search(r"total estimated time:\s*(.+?)(?:;|$)", line):
            info.print_time = m.group(1).strip()
        elif m := re.match(r";\s*estimated printing time.*?=\s*(.+)", line):
            info.print_time = m.group(1).strip()

    # Parse per-slot filament usage from tail
    for line in lines[-50:]:
        if m := re.match(r";\s*filament used \[g\]\s*=\s*(.+)", line):
            info.filament_usage_g = [float(v.strip()) for v in m.group(1).split(",")]

    # Walk gcode for layers and tool changes.
    # Z_HEIGHT appears immediately after CHANGE_LAYER, so we track
    # per-layer z values and resolve spans at the end.
    current_layer = 0
    current_z = 0.0
    current_extruder = 0  # 0-indexed
    layer_z: dict[int, float] = {}  # layer number → z height

    # (layer, extruder) pairs recording each tool change point
    tool_events: list[tuple[int, int]] = []  # (layer_at_change, new_extruder)

    for line in lines:
        if line.startswith("; CHANGE_LAYER"):
            current_layer += 1
        elif m := re.match(r"; Z_HEIGHT:\s*([\d.]+)", line):
            current_z = float(m.group(1))
            layer_z[current_layer] = current_z
        elif m := re.match(r"T(\d+)$", line):
            tool = int(m.group(1))
            # Skip special tool numbers (T1000 = initial load, T255 = unload)
            if tool >= 255:
                continue
            if current_layer == 0:
                # Pre-print tool select — set initial extruder, not a change
                current_extruder = tool
            elif tool != current_extruder:
                if not tool_events:
                    # Record initial extruder span starting at layer 1
                    tool_events.append((1, current_extruder))
                tool_events.append((current_layer, tool))
                info.filament_changes += 1
                current_extruder = tool

    # If no tool events recorded, the initial extruder was used throughout
    if not tool_events and current_layer > 0:
        tool_events.append((1, current_extruder))

    # Build spans from tool events
    for i, (start_layer, extruder) in enumerate(tool_events):
        if i + 1 < len(tool_events):
            end_layer = tool_events[i + 1][0]
        else:
            end_layer = current_layer
        info.spans.append(
            LayerSpan(
                start_layer=start_layer,
                end_layer=end_layer,
                extruder=extruder,
                start_z=layer_z.get(start_layer, 0.0),
                end_z=layer_z.get(end_layer, current_z),
            )
        )

    info.layer_count = current_layer
    return info
