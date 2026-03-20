"""Load and validate fabprint.toml configuration."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from fabprint import FabprintError

VALID_ORIENTS = {"flat", "upright", "side"}


@dataclass
class PlateConfig:
    size: tuple[float, float] = (256.0, 256.0)
    padding: float = 5.0


@dataclass
class SlicerConfig:
    engine: str = "orca"
    version: str | None = None  # required OrcaSlicer version (e.g. "2.3.1")
    printer: str | None = None
    process: str | None = None
    filaments: list[str] = field(default_factory=list)
    slots: dict[int, str] = field(default_factory=dict)  # slot (1-indexed) → profile name
    overrides: dict[str, object] = field(default_factory=dict)


@dataclass
class PartConfig:
    file: Path
    copies: int = 1
    orient: str = "flat"
    rotate: list[float] | None = None  # [rx, ry, rz] in degrees, overrides orient
    filament: int = 1  # slicer filament slot (1-indexed), resolved from name or int
    scale: float = 1.0  # uniform scale factor
    object_filaments: dict[str, int] = field(default_factory=dict)  # 3MF object → slot
    object: str | None = None  # select named object from multi-object 3MF
    sequence: int = 1  # print order for sequential printing


@dataclass
class PrinterConfig:
    name: str  # references a printer in ~/.config/fabprint/credentials.toml


DEFAULT_STAGES = ["load", "arrange", "plate", "slice"]


@dataclass
class PipelineConfig:
    stages: list[str] = field(default_factory=lambda: list(DEFAULT_STAGES))


@dataclass
class FabprintConfig:
    plate: PlateConfig
    slicer: SlicerConfig
    parts: list[PartConfig]
    base_dir: Path  # directory containing the toml file
    name: str | None = None  # optional project name, used to prefix output filenames
    printer: PrinterConfig | None = None
    pipeline: PipelineConfig = field(default_factory=PipelineConfig)


def _resolve_filaments(
    parts: list[PartConfig],
    slicer: SlicerConfig,
    raw_filaments: list[int | str],
    raw_obj_filaments: list[dict[str, int | str]],
) -> None:
    """Resolve filament names/indices and mutate parts in place.

    Sets ``.filament`` and ``.object_filaments`` on each part.
    May also populate ``slicer.filaments`` when auto-deriving from string refs.
    """
    # Collect all raw filament values (part defaults + per-object overrides)
    all_raw_filaments: list[int | str] = list(raw_filaments)
    for obj_fils in raw_obj_filaments:
        all_raw_filaments.extend(obj_fils.values())

    # Resolve filament names → slot indices
    has_string_filaments = any(isinstance(f, str) for f in all_raw_filaments)
    has_int_filaments = any(isinstance(f, int) for f in all_raw_filaments)

    if has_string_filaments and has_int_filaments and not slicer.filaments and not slicer.slots:
        raise FabprintError(
            "Cannot mix filament names and indices without [slicer].filaments or [slicer.slots]"
        )

    if has_int_filaments and not has_string_filaments and not slicer.slots:
        # All integers, no slots map — backward compatible, no resolution needed
        for i, raw_fil in enumerate(raw_filaments):
            if not isinstance(raw_fil, int):  # pragma: no cover
                raise FabprintError(f"parts[{i}]: expected int filament, got {type(raw_fil)}")
            parts[i].filament = raw_fil
            for obj_name, obj_fil in raw_obj_filaments[i].items():
                if not isinstance(obj_fil, int):  # pragma: no cover
                    raise FabprintError(
                        f"parts[{i}].filaments.{obj_name}: expected int, got {type(obj_fil)}"
                    )
                parts[i].object_filaments[obj_name] = obj_fil
    else:
        if not slicer.filaments:
            # Auto-derive filaments list from string refs + slots map
            # Seed with slots map entries (slot → profile)
            slot_to_name: dict[int, str] = dict(slicer.slots)
            used_slots: set[int] = set(slot_to_name.keys())

            # Collect unique string filament names from parts (default + per-object)
            unique_names: list[str] = []
            for raw_fil in all_raw_filaments:
                if isinstance(raw_fil, str) and raw_fil not in unique_names:
                    unique_names.append(raw_fil)

            # Auto-assign string filaments not already pinned via slots
            next_slot = 1
            for name in unique_names:
                if name not in slot_to_name.values():
                    while next_slot in used_slots:
                        next_slot += 1
                    slot_to_name[next_slot] = name
                    used_slots.add(next_slot)
                    next_slot += 1

            # Build the filaments list — use empty string for unused gap slots
            max_slot = max(slot_to_name.keys())
            slicer.filaments = [slot_to_name.get(s, "") for s in range(1, max_slot + 1)]

        # Build name → index lookup (first occurrence for name-based refs)
        fil_index: dict[str, int] = {}
        for idx, name in enumerate(slicer.filaments):
            if name not in fil_index:
                fil_index[name] = idx + 1

        for i, raw_fil in enumerate(raw_filaments):
            if isinstance(raw_fil, str):
                if raw_fil not in fil_index:
                    raise FabprintError(
                        f"parts[{i}]: filament '{raw_fil}' not in "
                        f"[slicer].filaments {slicer.filaments}"
                    )
                parts[i].filament = fil_index[raw_fil]
            else:
                # Integer slot ref — validate against slots map if present
                if slicer.slots and raw_fil not in slicer.slots:
                    raise FabprintError(
                        f"parts[{i}]: filament slot {raw_fil} not defined in [slicer.slots]"
                    )
                parts[i].filament = raw_fil

            # Resolve per-object filament overrides for this part
            for obj_name, obj_fil in raw_obj_filaments[i].items():
                if isinstance(obj_fil, str):
                    if obj_fil not in fil_index:
                        raise FabprintError(
                            f"parts[{i}].filaments.{obj_name}: '{obj_fil}' not in "
                            f"[slicer].filaments {slicer.filaments}"
                        )
                    parts[i].object_filaments[obj_name] = fil_index[obj_fil]
                else:
                    if slicer.slots and obj_fil not in slicer.slots:
                        raise FabprintError(
                            f"parts[{i}].filaments.{obj_name}: slot {obj_fil} "
                            f"not defined in [slicer.slots]"
                        )
                    parts[i].object_filaments[obj_name] = obj_fil


def load_config(path: Path) -> FabprintConfig:
    """Load and validate a fabprint.toml file."""
    path = path.resolve()
    if not path.exists():
        raise FabprintError(f"Config file not found: {path}")

    with open(path, "rb") as f:
        raw = tomllib.load(f)

    base_dir = path.parent

    # Plate config
    plate_raw = raw.get("plate", {})
    size = tuple(plate_raw.get("size", [256.0, 256.0]))
    if len(size) != 2 or any(s <= 0 for s in size):
        raise FabprintError(f"plate.size must be two positive numbers, got {size}")
    plate = PlateConfig(size=size, padding=float(plate_raw.get("padding", 5.0)))

    # Slicer config
    slicer_raw = raw.get("slicer", {})
    slots_parsed: dict[int, str] = {}
    for key, profile in slicer_raw.get("slots", {}).items():
        try:
            slot_num = int(key)
        except (TypeError, ValueError):
            raise FabprintError(f"slicer.slots: key '{key}' must be an integer slot number")
        if slot_num < 1:
            raise FabprintError(f"slicer.slots: slot must be >= 1, got {slot_num}")
        if not isinstance(profile, str) or not profile.strip():
            raise FabprintError(
                f"slicer.slots[{slot_num}]: profile name must be a non-empty string"
            )
        slots_parsed[slot_num] = profile
    slicer = SlicerConfig(
        engine=slicer_raw.get("engine", "orca"),
        version=slicer_raw.get("version"),
        printer=slicer_raw.get("printer"),
        process=slicer_raw.get("process"),
        filaments=slicer_raw.get("filaments", []),
        slots=slots_parsed,
        overrides=slicer_raw.get("overrides", {}),
    )
    if slicer.engine != "orca":
        raise FabprintError(f"slicer.engine must be 'orca', got '{slicer.engine}'")

    # Parts — first pass: parse everything except filament resolution
    parts_raw = raw.get("parts", [])
    if not parts_raw:
        raise FabprintError("At least one [[parts]] entry is required")

    parts = []
    raw_filaments: list[int | str] = []  # preserve raw filament values for resolution
    raw_obj_filaments: list[dict[str, int | str]] = []  # per-part object filament overrides
    for i, p in enumerate(parts_raw):
        if "file" not in p:
            raise FabprintError(f"parts[{i}]: 'file' is required")
        orient = p.get("orient", "flat")
        if orient not in VALID_ORIENTS:
            raise FabprintError(
                f"parts[{i}]: orient must be one of {VALID_ORIENTS}, got '{orient}'"
            )
        file_path = base_dir / p["file"]
        if not file_path.exists():
            raise FabprintError(f"parts[{i}]: file not found: {file_path}")
        copies = int(p.get("copies", 1))
        if copies < 1:
            raise FabprintError(f"parts[{i}]: copies must be >= 1, got {copies}")
        raw_fil = p.get("filament", 1)
        if isinstance(raw_fil, str):
            if not raw_fil.strip():
                raise FabprintError(f"parts[{i}]: filament name must not be empty")
        else:
            raw_fil = int(raw_fil)
            if raw_fil < 1:
                raise FabprintError(f"parts[{i}]: filament must be >= 1, got {raw_fil}")
        raw_filaments.append(raw_fil)

        # Per-object filament overrides for multi-object 3MF files
        obj_fils_raw: dict[str, int | str] = {}
        for obj_name, obj_fil in p.get("filaments", {}).items():
            if isinstance(obj_fil, str):
                if not obj_fil.strip():
                    raise FabprintError(
                        f"parts[{i}].filaments.{obj_name}: filament name must not be empty"
                    )
            else:
                obj_fil = int(obj_fil)
                if obj_fil < 1:
                    raise FabprintError(
                        f"parts[{i}].filaments.{obj_name}: filament must be >= 1, got {obj_fil}"
                    )
            obj_fils_raw[obj_name] = obj_fil
        raw_obj_filaments.append(obj_fils_raw)

        rotate = p.get("rotate")
        if rotate is not None:
            if not isinstance(rotate, list) or len(rotate) != 3:
                raise FabprintError(f"parts[{i}]: rotate must be [rx, ry, rz], got {rotate}")
            rotate = [float(r) for r in rotate]
        scale = float(p.get("scale", 1.0))
        if scale <= 0:
            raise FabprintError(f"parts[{i}]: scale must be > 0, got {scale}")
        obj_name = p.get("object")
        if obj_name is not None:
            if not isinstance(obj_name, str) or not obj_name.strip():
                raise FabprintError(f"parts[{i}]: object must be a non-empty string")
            if obj_fils_raw:
                raise FabprintError(f"parts[{i}]: cannot use both 'object' and [parts.filaments]")
        sequence = int(p.get("sequence", 1))
        if sequence < 1:
            raise FabprintError(f"parts[{i}]: sequence must be >= 1, got {sequence}")
        parts.append(
            PartConfig(
                file=file_path,
                copies=copies,
                orient=orient,
                rotate=rotate,
                filament=1,  # placeholder, resolved below
                scale=scale,
                object=obj_name,
                sequence=sequence,
            )
        )

    _resolve_filaments(parts, slicer, raw_filaments, raw_obj_filaments)

    # Pipeline config (optional)
    from fabprint.pipeline import STAGE_OUTPUTS

    pipeline_raw = raw.get("pipeline", {})
    pipeline_stages = pipeline_raw.get("stages", list(DEFAULT_STAGES))
    if not isinstance(pipeline_stages, list):
        raise FabprintError("pipeline.stages must be a list of stage names")
    for s in pipeline_stages:
        if not isinstance(s, str) or not s.strip():
            raise FabprintError(
                f"pipeline.stages: each stage must be a non-empty string, got {s!r}"
            )
        if s not in STAGE_OUTPUTS:
            raise FabprintError(
                f"pipeline.stages: unknown stage '{s}'. Valid stages: {sorted(STAGE_OUTPUTS)}"
            )
    pipeline = PipelineConfig(stages=pipeline_stages)

    # Printer config (optional)
    printer = None
    printer_raw = raw.get("printer")
    if printer_raw is not None:
        # Reject secrets in project TOML — they belong in credentials.toml
        for secret_field in ("ip", "access_code", "serial", "mode"):
            if secret_field in printer_raw:
                raise FabprintError(
                    f"printer.{secret_field} should not be in project config. "
                    f"Use 'fabprint setup' to configure printers in credentials.toml."
                )
        name = printer_raw.get("name")
        if not name:
            raise FabprintError("printer.name is required — it references credentials.toml")
        printer = PrinterConfig(name=name)

    # Top-level project name (optional)
    project_name: str | None = raw.get("name")
    if project_name is not None:
        if not isinstance(project_name, str) or not project_name.strip():
            raise FabprintError("name must be a non-empty string")
        project_name = project_name.strip()

    return FabprintConfig(
        plate=plate,
        slicer=slicer,
        parts=parts,
        base_dir=base_dir,
        name=project_name,
        printer=printer,
        pipeline=pipeline,
    )
