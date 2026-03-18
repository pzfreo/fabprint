"""Hamilton DAG nodes for the fabprint pipeline.

Each public function is a node in the pipeline DAG.  Hamilton auto-wires
dependencies by matching *parameter names* to *function names* (or to
values provided at execution time via ``driver.execute(inputs=...)``.

Typical execution::

    from hamilton import driver
    from fabprint import pipeline, adapters

    dr = driver.Builder().with_modules(pipeline).build()

    # Only plate generation (Hamilton computes the minimum subgraph):
    result = dr.execute(["plate_3mf_path"], inputs={...})

    # Full pipeline through printing:
    result = dr.execute(["print_result"], inputs={...})
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import trimesh

from fabprint.arrange import Placement, arrange
from fabprint.config import FabprintConfig, load_config
from fabprint.loader import extract_paint_colors, load_3mf_objects, load_mesh
from fabprint.orient import orient_mesh
from fabprint.plate import build_plate, export_plate

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Stage registry
# ---------------------------------------------------------------------------

# Maps stage names (used in [pipeline].stages) to Hamilton output node names.
STAGE_OUTPUTS: dict[str, list[str]] = {
    "load": ["loaded_parts", "part_summary"],
    "arrange": ["placements"],
    "plate": ["plate_3mf_path", "preview_path"],
    "slice": ["sliced_output_dir", "gcode_stats"],
    "gcode-info": ["gcode_stats"],  # kept for backward compat
    "print": ["print_result"],
}

# For --only mode: maps stage names to the Hamilton node overrides that must
# be resolved from disk artifacts.  Each value is (node_name, description).
STAGE_REQUIRES: dict[str, list[tuple[str, str]]] = {
    "slice": [("plate_3mf_path", "plate 3MF file")],
    "gcode-info": [("sliced_output_dir", "slicer output directory")],
    "print": [("gcode_path", "sliced gcode file")],
}


def resolve_outputs(
    stages: list[str],
    until: Optional[str] = None,
    only: Optional[str] = None,
) -> list[str]:
    """Resolve the list of Hamilton output nodes to request.

    Args:
        stages: The full ordered pipeline from config.
        until: If set, include stages up to and including this one.
        only: If set, include only this single stage's outputs.

    Returns:
        List of Hamilton node names to pass to ``driver.execute()``.
    """
    if only:
        if only not in STAGE_OUTPUTS:
            raise ValueError(f"Unknown stage '{only}'. Valid stages: {sorted(STAGE_OUTPUTS)}")
        return list(STAGE_OUTPUTS[only])

    if until:
        if until not in stages:
            raise ValueError(f"Stage '{until}' not in pipeline stages {stages}")
        cut = stages[: stages.index(until) + 1]
    else:
        cut = stages

    outputs: list[str] = []
    for stage in cut:
        outputs.extend(STAGE_OUTPUTS[stage])
    return outputs


def resolve_overrides(
    only: str,
    output_dir: Path,
) -> dict[str, object]:
    """Build Hamilton overrides for --only mode.

    Locates required artifacts on disk and returns them as override values
    so Hamilton skips upstream computation.  Raises FileNotFoundError if
    any required artifact is missing.
    """
    requirements = STAGE_REQUIRES.get(only, [])
    overrides: dict[str, object] = {}

    for node_name, description in requirements:
        if node_name == "plate_3mf_path":
            plate_files = list(output_dir.glob("*plate.3mf"))
            # Exclude preview files
            plate_files = [p for p in plate_files if "preview" not in p.name]
            if not plate_files:
                raise FileNotFoundError(f"--only {only}: requires {description} in {output_dir}")
            overrides[node_name] = plate_files[0]
        elif node_name == "sliced_output_dir":
            if not output_dir.exists():
                raise FileNotFoundError(f"--only {only}: requires {description} at {output_dir}")
            overrides[node_name] = output_dir
        elif node_name == "gcode_path":
            gcode_files = list(output_dir.glob("*.gcode"))
            if not gcode_files:
                raise FileNotFoundError(f"--only {only}: requires {description} in {output_dir}")
            overrides[node_name] = gcode_files[0]

    return overrides


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------


@dataclass
class LoadedParts:
    """Result of loading and orienting all meshes from config."""

    meshes: list = field(default_factory=list)
    names: list[str] = field(default_factory=list)
    filament_ids: list[int] = field(default_factory=list)
    has_paint_colors: bool = False
    part_info: list[tuple] = field(default_factory=list)
    part_sequences: list[int] = field(default_factory=list)


@dataclass
class ResolvedFilaments:
    """Filament profiles and IDs ready for the slicer."""

    filaments: Optional[list[str]] = None
    filament_ids: list[int] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Public helper (also used by cli.py for sequential printing)
# ---------------------------------------------------------------------------


def load_parts(cfg: FabprintConfig, global_scale: Optional[float] = None) -> LoadedParts:
    """Load and prepare meshes from config parts.

    For parts with 'object' set, loads a specific named object from a
    multi-object 3MF.  Parts from the same file are grouped so that objects
    maintain their relative positions during arrangement.
    """
    meshes: list = []
    names: list[str] = []
    filament_ids: list[int] = []
    part_sequences: list[int] = []
    has_paint_colors = False
    part_info: list[tuple] = []

    # Group parts by source file for object-selection parts
    file_groups: dict[Path, list[int]] = {}
    for i, part in enumerate(cfg.parts):
        if part.object:
            file_groups.setdefault(part.file, []).append(i)

    grouped_parts: set[int] = set()
    for indices in file_groups.values():
        if len(indices) > 1:
            grouped_parts.update(indices)

    # Process file groups (multiple object-selection parts from the same 3MF)
    processed_files: set[Path] = set()
    for file_path, part_indices in file_groups.items():
        if len(part_indices) < 2 or file_path in processed_files:
            continue
        processed_files.add(file_path)

        all_objects = dict(load_3mf_objects(file_path))
        group_parts = [cfg.parts[i] for i in part_indices]
        scale = group_parts[0].scale * global_scale if global_scale else group_parts[0].scale

        sub_meshes = []
        for part in group_parts:
            if part.object not in all_objects:
                raise ValueError(
                    f"Object '{part.object}' not found in {file_path}. "
                    f"Available: {list(all_objects.keys())}"
                )
            obj_mesh = all_objects[part.object].copy()
            if scale != 1.0:
                obj_mesh.apply_scale(scale)
            obj_mesh.metadata["filament_id"] = part.filament
            obj_mesh.metadata["sequence"] = part.sequence
            sub_meshes.append((part.object, obj_mesh))

        combined = trimesh.util.concatenate([m for _, m in sub_meshes])
        combined.metadata["filament_id"] = group_parts[0].filament
        combined.metadata["group_objects"] = sub_meshes
        combined.metadata["original_bounds_min"] = combined.bounds[0][:2].copy()
        w, d, h = combined.extents

        copies = group_parts[0].copies
        part_info.append((file_path.stem, copies, group_parts[0].filament, scale, w, d, h))
        for i in range(copies):
            meshes.append(combined.copy())
            suffix = f"_{i + 1}" if copies > 1 else ""
            names.append(f"{file_path.stem}{suffix}")
            filament_ids.append(group_parts[0].filament)
            part_sequences.append(0)

    # Process remaining parts (non-grouped)
    for i, part in enumerate(cfg.parts):
        if i in grouped_parts:
            continue

        scale = part.scale * global_scale if global_scale else part.scale

        if part.object:
            all_objects = dict(load_3mf_objects(part.file))
            if part.object not in all_objects:
                raise ValueError(
                    f"Object '{part.object}' not found in {part.file}. "
                    f"Available: {list(all_objects.keys())}"
                )
            mesh = all_objects[part.object].copy()
            if scale != 1.0:
                mesh.apply_scale(scale)
            mesh.metadata["filament_id"] = part.filament
        elif part.object_filaments:
            objects = load_3mf_objects(part.file)
            sub_meshes = []
            for obj_name, obj_mesh in objects:
                if scale != 1.0:
                    obj_mesh.apply_scale(scale)
                fil_id = part.object_filaments.get(obj_name, part.filament)
                obj_mesh.metadata["filament_id"] = fil_id
                sub_meshes.append((obj_name, obj_mesh))

            mesh = trimesh.util.concatenate([m for _, m in sub_meshes])
            mesh.metadata["filament_id"] = part.filament
            mesh.metadata["group_objects"] = sub_meshes
            mesh.metadata["original_bounds_min"] = mesh.bounds[0][:2].copy()
        else:
            base_mesh = load_mesh(part.file)
            mesh = orient_mesh(base_mesh, part.orient, part.rotate)
            if scale != 1.0:
                mesh.apply_scale(scale)
            mesh.metadata["filament_id"] = part.filament
            paint_colors = extract_paint_colors(part.file)
            if paint_colors:
                mesh.metadata["paint_colors"] = paint_colors
                has_paint_colors = True

        w, d, h = mesh.extents
        part_info.append((part.file.stem, part.copies, part.filament, scale, w, d, h))
        for c in range(part.copies):
            meshes.append(mesh.copy())
            suffix = f"_{c + 1}" if part.copies > 1 else ""
            names.append(f"{part.file.stem}{suffix}")
            filament_ids.append(part.filament)
            part_sequences.append(part.sequence)

    return LoadedParts(
        meshes=meshes,
        names=names,
        filament_ids=filament_ids,
        has_paint_colors=has_paint_colors,
        part_info=part_info,
        part_sequences=part_sequences,
    )


def format_summary(loaded_parts: LoadedParts, plate_size: tuple[float, float]) -> str:
    """Build a human-readable summary of parts on the plate."""
    lines: list[str] = []
    part_info = loaded_parts.part_info
    if not part_info:
        return ""
    name_width = max(len(name) for name, *_ in part_info)
    lines.append("\nParts:")
    for name, copies, filament, scale, w, d, h in part_info:
        scale_str = f"  {scale}x" if scale != 1.0 else ""
        lines.append(
            f"  {name:<{name_width}}  x{copies}  slot {filament}"
            f"{scale_str}  {w:.0f}x{d:.0f}x{h:.0f}mm"
        )
    total = len(loaded_parts.meshes)
    lines.append(f"\nPlate: {total} parts on {plate_size[0]:.0f}x{plate_size[1]:.0f}mm")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Hamilton DAG nodes
# ---------------------------------------------------------------------------


def config(config_path: Path) -> FabprintConfig:
    """Load and validate the fabprint TOML config."""
    return load_config(config_path)


def loaded_parts(config: FabprintConfig, global_scale: Optional[float]) -> LoadedParts:
    """Load, orient, and scale all meshes from the config."""
    return load_parts(config, global_scale)


def part_summary(loaded_parts: LoadedParts, config: FabprintConfig) -> str:
    """Human-readable build summary."""
    return format_summary(loaded_parts, config.plate.size)


def placements(loaded_parts: LoadedParts, config: FabprintConfig) -> list[Placement]:
    """Arrange parts on the build plate via 2D bin-packing."""
    return arrange(
        loaded_parts.meshes,
        loaded_parts.names,
        config.plate.size,
        config.plate.padding,
    )


def plate_scene(placements: list[Placement], config: FabprintConfig) -> trimesh.Scene:
    """Assemble a trimesh Scene from placements."""
    return build_plate(placements, config.plate.size)


def plate_3mf_path(plate_scene: trimesh.Scene, loaded_parts: LoadedParts, output_3mf: Path) -> Path:
    """Export the plate scene to a 3MF file."""
    export_plate(plate_scene, output_3mf)
    log.info("Plate exported to %s", output_3mf)
    return output_3mf


def preview_path(placements: list[Placement], config: FabprintConfig, output_3mf: Path) -> Path:
    """Export a preview 3MF with bed outline."""
    preview_scene = build_plate(placements, config.plate.size, include_bed=True)
    out = output_3mf.with_stem(output_3mf.stem + "_preview")
    export_plate(preview_scene, out)
    log.info("Preview exported to %s", out)
    return out


def resolved_filaments(
    config: FabprintConfig,
    loaded_parts: LoadedParts,
    filament_type_override: Optional[str],
    filament_slot_override: int,
) -> ResolvedFilaments:
    """Resolve filament profiles and IDs, applying CLI overrides."""
    if filament_type_override:
        return ResolvedFilaments(
            filaments=[filament_type_override],
            filament_ids=[filament_slot_override] * len(loaded_parts.filament_ids),
        )
    elif loaded_parts.has_paint_colors:
        return ResolvedFilaments(filaments=None, filament_ids=loaded_parts.filament_ids)
    else:
        return ResolvedFilaments(
            filaments=config.slicer.filaments or None,
            filament_ids=loaded_parts.filament_ids,
        )


def sliced_output_dir(
    plate_3mf_path: Path,
    config: FabprintConfig,
    resolved_filaments: ResolvedFilaments,
    output_dir: Path,
    slicer_local: bool,
    docker_version: Optional[str],
) -> Path:
    """Slice the plate 3MF via OrcaSlicer/BambuStudio."""
    from fabprint.slicer import slice_plate

    return slice_plate(
        input_3mf=plate_3mf_path,
        engine=config.slicer.engine,
        output_dir=output_dir,
        printer=config.slicer.printer,
        process=config.slicer.process,
        filaments=resolved_filaments.filaments,
        filament_ids=resolved_filaments.filament_ids,
        overrides=config.slicer.overrides or None,
        project_dir=config.base_dir,
        local=slicer_local,
        docker_version=docker_version,
        required_version=config.slicer.version,
    )


def gcode_stats(sliced_output_dir: Path) -> dict:
    """Parse print time and filament usage from sliced gcode."""
    from fabprint.slicer import parse_gcode_stats

    return parse_gcode_stats(sliced_output_dir)


def gcode_path(sliced_output_dir: Path) -> Path:
    """Find the gcode file in the slicer output directory."""
    gcode_files = list(sliced_output_dir.glob("*.gcode"))
    if not gcode_files:
        raise RuntimeError(f"No gcode files found in {sliced_output_dir}")
    return gcode_files[0]


def print_result(
    gcode_path: Path,
    config: FabprintConfig,
    dry_run: bool,
    upload_only: bool,
    experimental: bool,
    skip_ams_mapping: bool,
) -> None:
    """Send sliced gcode to the printer."""
    from fabprint.printer import send_print

    if config.printer is None:
        raise ValueError("No [printer] section in config. Required for printing.")
    send_print(
        gcode_path,
        config.printer,
        dry_run=dry_run,
        upload_only=upload_only,
        experimental=experimental,
        skip_ams_mapping=skip_ams_mapping,
    )
