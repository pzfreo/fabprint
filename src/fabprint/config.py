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
    version: str | None = None  # required OrcaSlicer version (e.g. "2.3.1")
    printer: str | None = None
    process: str | None = None
    filaments: list[str] = field(default_factory=list)
    overrides: dict[str, object] = field(default_factory=dict)


@dataclass
class PartConfig:
    file: Path
    copies: int = 1
    orient: str = "flat"
    rotate: list[float] | None = None  # [rx, ry, rz] in degrees, overrides orient
    filament: int = 1  # AMS slot (1-indexed)
    scale: float = 1.0  # uniform scale factor


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
        version=slicer_raw.get("version"),
        printer=slicer_raw.get("printer"),
        process=slicer_raw.get("process"),
        filaments=slicer_raw.get("filaments", []),
        overrides=slicer_raw.get("overrides", {}),
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
        copies = int(p.get("copies", 1))
        if copies < 1:
            raise ValueError(f"parts[{i}]: copies must be >= 1, got {copies}")
        filament = int(p.get("filament", 1))
        if filament < 1:
            raise ValueError(f"parts[{i}]: filament must be >= 1, got {filament}")
        rotate = p.get("rotate")
        if rotate is not None:
            if not isinstance(rotate, list) or len(rotate) != 3:
                raise ValueError(f"parts[{i}]: rotate must be [rx, ry, rz], got {rotate}")
            rotate = [float(r) for r in rotate]
        scale = float(p.get("scale", 1.0))
        if scale <= 0:
            raise ValueError(f"parts[{i}]: scale must be > 0, got {scale}")
        parts.append(PartConfig(
            file=file_path,
            copies=copies,
            orient=orient,
            rotate=rotate,
            filament=filament,
            scale=scale,
        ))

    return FabprintConfig(plate=plate, slicer=slicer, parts=parts, base_dir=base_dir)
