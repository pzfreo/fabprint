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
    slots: dict[str, int] = field(default_factory=dict)  # filament name → slot (1-indexed)
    overrides: dict[str, object] = field(default_factory=dict)


@dataclass
class PartConfig:
    file: Path
    copies: int = 1
    orient: str = "flat"
    rotate: list[float] | None = None  # [rx, ry, rz] in degrees, overrides orient
    filament: int = 1  # slicer filament slot (1-indexed), resolved from name or int
    scale: float = 1.0  # uniform scale factor


@dataclass
class PrinterConfig:
    mode: str = "bambu-lan"  # "bambu-lan", "bambu-connect", "bambu-cloud", or legacy "lan"/"cloud"
    ip: str | None = None
    access_code: str | None = None
    serial: str | None = None


@dataclass
class FabprintConfig:
    plate: PlateConfig
    slicer: SlicerConfig
    parts: list[PartConfig]
    base_dir: Path  # directory containing the toml file
    printer: PrinterConfig | None = None


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
    slots_raw = slicer_raw.get("slots", {})
    for name, slot in slots_raw.items():
        if not isinstance(slot, int) or slot < 1:
            raise ValueError(f"slicer.slots['{name}']: slot must be a positive integer, got {slot}")
    slicer = SlicerConfig(
        engine=slicer_raw.get("engine", "bambu"),
        version=slicer_raw.get("version"),
        printer=slicer_raw.get("printer"),
        process=slicer_raw.get("process"),
        filaments=slicer_raw.get("filaments", []),
        slots=slots_raw,
        overrides=slicer_raw.get("overrides", {}),
    )
    if slicer.engine not in ("bambu", "orca"):
        raise ValueError(f"slicer.engine must be 'bambu' or 'orca', got '{slicer.engine}'")

    # Parts — first pass: parse everything except filament resolution
    parts_raw = raw.get("parts", [])
    if not parts_raw:
        raise ValueError("At least one [[parts]] entry is required")

    parts = []
    raw_filaments: list[int | str] = []  # preserve raw filament values for resolution
    for i, p in enumerate(parts_raw):
        if "file" not in p:
            raise ValueError(f"parts[{i}]: 'file' is required")
        orient = p.get("orient", "flat")
        if orient not in VALID_ORIENTS:
            raise ValueError(f"parts[{i}]: orient must be one of {VALID_ORIENTS}, got '{orient}'")
        file_path = base_dir / p["file"]
        if not file_path.exists():
            raise FileNotFoundError(f"parts[{i}]: file not found: {file_path}")
        copies = int(p.get("copies", 1))
        if copies < 1:
            raise ValueError(f"parts[{i}]: copies must be >= 1, got {copies}")
        raw_fil = p.get("filament", 1)
        if isinstance(raw_fil, str):
            if not raw_fil.strip():
                raise ValueError(f"parts[{i}]: filament name must not be empty")
        else:
            raw_fil = int(raw_fil)
            if raw_fil < 1:
                raise ValueError(f"parts[{i}]: filament must be >= 1, got {raw_fil}")
        raw_filaments.append(raw_fil)
        rotate = p.get("rotate")
        if rotate is not None:
            if not isinstance(rotate, list) or len(rotate) != 3:
                raise ValueError(f"parts[{i}]: rotate must be [rx, ry, rz], got {rotate}")
            rotate = [float(r) for r in rotate]
        scale = float(p.get("scale", 1.0))
        if scale <= 0:
            raise ValueError(f"parts[{i}]: scale must be > 0, got {scale}")
        parts.append(
            PartConfig(
                file=file_path,
                copies=copies,
                orient=orient,
                rotate=rotate,
                filament=1,  # placeholder, resolved below
                scale=scale,
            )
        )

    # Resolve filament names → slot indices
    has_string_filaments = any(isinstance(f, str) for f in raw_filaments)
    has_int_filaments = any(isinstance(f, int) for f in raw_filaments)

    if has_string_filaments and has_int_filaments and not slicer.filaments:
        raise ValueError(
            "Cannot mix filament names and indices without an explicit [slicer].filaments list"
        )

    if has_int_filaments and not has_string_filaments:
        # All integers — backward compatible, no resolution needed
        for i, raw_fil in enumerate(raw_filaments):
            parts[i].filament = raw_fil
    else:
        # String filament references — resolve to indices
        if not slicer.filaments:
            # Auto-derive filaments list, respecting [slicer.slots] pinning
            unique_names: list[str] = []
            for raw_fil in raw_filaments:
                if isinstance(raw_fil, str) and raw_fil not in unique_names:
                    unique_names.append(raw_fil)

            if slicer.slots:
                # Place pinned filaments at their slots, auto-assign the rest
                pinned_slots: set[int] = set()
                fil_to_slot: dict[str, int] = {}
                for name, slot in slicer.slots.items():
                    if name not in unique_names:
                        raise ValueError(f"slicer.slots['{name}']: filament not used by any part")
                    fil_to_slot[name] = slot
                    pinned_slots.add(slot)

                # Auto-assign unpinned filaments to next free slot
                next_slot = 1
                for name in unique_names:
                    if name not in fil_to_slot:
                        while next_slot in pinned_slots:
                            next_slot += 1
                        fil_to_slot[name] = next_slot
                        pinned_slots.add(next_slot)
                        next_slot += 1

                # Build the filaments list, filling gaps with the first filament
                max_slot = max(fil_to_slot.values())
                slot_to_name = {v: k for k, v in fil_to_slot.items()}
                first_name = unique_names[0]
                slicer.filaments = [slot_to_name.get(s, first_name) for s in range(1, max_slot + 1)]
            else:
                slicer.filaments = unique_names

        # Build name → index lookup (use slot mapping if available,
        # otherwise first occurrence — avoids gap-filler duplicates)
        if slicer.slots:
            fil_index = fil_to_slot
        else:
            fil_index = {}
            for idx, name in enumerate(slicer.filaments):
                if name not in fil_index:
                    fil_index[name] = idx + 1

        for i, raw_fil in enumerate(raw_filaments):
            if isinstance(raw_fil, str):
                if raw_fil not in fil_index:
                    raise ValueError(
                        f"parts[{i}]: filament '{raw_fil}' not in "
                        f"[slicer].filaments {slicer.filaments}"
                    )
                parts[i].filament = fil_index[raw_fil]
            else:
                parts[i].filament = raw_fil

    # Printer config (optional)
    printer = None
    printer_raw = raw.get("printer")
    if printer_raw:
        mode = printer_raw.get("mode", "bambu-lan")
        valid_modes = (
            "bambu-lan",
            "bambu-connect",
            "bambu-cloud",
            "cloud-bridge",
            "cloud-http",
            "lan",
            "cloud",
        )
        if mode not in valid_modes:
            raise ValueError(f"printer.mode must be one of {valid_modes}, got '{mode}'")
        printer = PrinterConfig(
            mode=mode,
            ip=printer_raw.get("ip"),
            access_code=printer_raw.get("access_code"),
            serial=printer_raw.get("serial"),
        )

    return FabprintConfig(
        plate=plate, slicer=slicer, parts=parts, base_dir=base_dir, printer=printer
    )
