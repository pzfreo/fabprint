"""Load and validate fabprint.toml configuration."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

VALID_ORIENTS = {"flat", "upright", "side"}


@dataclass
class PlateConfig:
    size: tuple[float, float] = (256.0, 256.0)
    padding: float = 5.0


@dataclass
class SlicerConfig:
    engine: str = "bambu"
    printer: str | None = None
    process: str | None = None
    filaments: list[str] = field(default_factory=list)


@dataclass
class PartConfig:
    file: Path
    copies: int = 1
    orient: str = "flat"


@dataclass
class FabprintConfig:
    plate: PlateConfig
    slicer: SlicerConfig
    parts: list[PartConfig]
    base_dir: Path  # directory containing the toml file


def load_config(path: Path) -> FabprintConfig:
    """Load and validate a fabprint.toml file."""
    path = path.resolve()
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(path, "rb") as f:
        raw = tomllib.load(f)

    base_dir = path.parent

    # Plate config
    plate_raw = raw.get("plate", {})
    size = tuple(plate_raw.get("size", [256.0, 256.0]))
    if len(size) != 2 or any(s <= 0 for s in size):
        raise ValueError(f"plate.size must be two positive numbers, got {size}")
    plate = PlateConfig(size=size, padding=float(plate_raw.get("padding", 5.0)))

    # Slicer config
    slicer_raw = raw.get("slicer", {})
    slicer = SlicerConfig(
        engine=slicer_raw.get("engine", "bambu"),
        printer=slicer_raw.get("printer"),
        process=slicer_raw.get("process"),
        filaments=slicer_raw.get("filaments", []),
    )
    if slicer.engine not in ("bambu", "orca"):
        raise ValueError(f"slicer.engine must be 'bambu' or 'orca', got '{slicer.engine}'")

    # Parts
    parts_raw = raw.get("parts", [])
    if not parts_raw:
        raise ValueError("At least one [[parts]] entry is required")

    parts = []
    for i, p in enumerate(parts_raw):
        if "file" not in p:
            raise ValueError(f"parts[{i}]: 'file' is required")
        orient = p.get("orient", "flat")
        if orient not in VALID_ORIENTS:
            raise ValueError(
                f"parts[{i}]: orient must be one of {VALID_ORIENTS}, got '{orient}'"
            )
        file_path = base_dir / p["file"]
        if not file_path.exists():
            raise FileNotFoundError(f"parts[{i}]: file not found: {file_path}")
        parts.append(PartConfig(
            file=file_path,
            copies=int(p.get("copies", 1)),
            orient=orient,
        ))

    return FabprintConfig(plate=plate, slicer=slicer, parts=parts, base_dir=base_dir)
