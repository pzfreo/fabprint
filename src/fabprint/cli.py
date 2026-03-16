"""CLI entry point for fabprint."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from fabprint import FabprintError, __version__
from fabprint.arrange import arrange
from fabprint.config import FabprintConfig, load_config
from fabprint.loader import extract_paint_colors, load_3mf_objects, load_mesh
from fabprint.orient import orient_mesh
from fabprint.plate import build_plate, export_plate


def _resolve_config(args: argparse.Namespace) -> None:
    """Default config to ./fabprint.toml when not provided."""
    if not hasattr(args, "config") or args.config is None:
        return
    # argparse stores the Path from nargs="?" default; if it's the sentinel
    # value we set, resolve to ./fabprint.toml
    if args.config == _CONFIG_DEFAULT:
        candidate = Path("fabprint.toml")
        if not candidate.exists():
            raise FabprintError(
                "No config file specified and no fabprint.toml found in the current directory.\n"
                "Usage: fabprint <command> [config.toml]"
            )
        args.config = candidate


# Sentinel so we can distinguish "user didn't pass config" from "user passed a path"
_CONFIG_DEFAULT = Path("\x00")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="fabprint",
        description="Immutable 3D print pipeline: arrange, slice, and print.",
        epilog="Run 'fabprint <command> --help' for command-specific options.",
    )
    parser.add_argument("--version", action="version", version=f"fabprint {__version__}")
    sub = parser.add_subparsers(dest="command")

    # Shared args for subcommands
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")
    common.add_argument(
        "--scale",
        type=float,
        default=None,
        help="Scale all parts by this factor (multiplies per-part scale)",
    )

    # plate subcommand
    plate_cmd = sub.add_parser(
        "plate",
        parents=[common],
        help="Arrange parts and export a 3MF build plate",
    )
    plate_cmd.add_argument(
        "config",
        nargs="?",
        type=Path,
        default=_CONFIG_DEFAULT,
        help="Path to config file (default: ./fabprint.toml)",
    )
    plate_cmd.add_argument("-o", "--output", type=Path, default=None, help="Output 3MF path")

    # slice subcommand
    slice_cmd = sub.add_parser(
        "slice",
        parents=[common],
        help="Arrange, export, and slice to gcode",
    )
    slice_cmd.add_argument(
        "config",
        nargs="?",
        type=Path,
        default=_CONFIG_DEFAULT,
        help="Path to config file (default: ./fabprint.toml)",
    )
    slice_cmd.add_argument("-o", "--output-dir", type=Path, default=None, help="Output directory")
    slice_cmd.add_argument(
        "--local",
        action="store_true",
        help="Force local slicer (fail if not installed)",
    )
    slice_cmd.add_argument(
        "--docker-version",
        type=str,
        default=None,
        help="Use a specific OrcaSlicer Docker image version (e.g. 2.3.1)",
    )
    slice_cmd.add_argument(
        "--filament-type",
        type=str,
        default=None,
        help="Override filament profile name (e.g. 'Generic PLA @base')",
    )
    slice_cmd.add_argument(
        "--filament-slot",
        type=int,
        default=1,
        help="AMS slot for --filament-type (default: 1)",
    )

    # print subcommand
    print_cmd = sub.add_parser(
        "print",
        parents=[common],
        help="Arrange, slice, and send to printer",
    )
    print_cmd.add_argument(
        "config",
        nargs="?",
        type=Path,
        default=_CONFIG_DEFAULT,
        help="Path to config file (default: ./fabprint.toml)",
    )
    print_cmd.add_argument(
        "--gcode", type=Path, default=None, help="Send pre-sliced gcode (skip arrange/slice)"
    )
    print_cmd.add_argument("-o", "--output-dir", type=Path, default=None, help="Output directory")
    print_cmd.add_argument(
        "--dry-run", action="store_true", help="Do everything except send to printer"
    )
    print_cmd.add_argument(
        "--upload-only",
        action="store_true",
        help="Upload gcode but don't start printing (start from touchscreen/app)",
    )
    print_cmd.add_argument(
        "--experimental",
        action="store_true",
        help="Enable experimental printer modes (e.g. cloud-http, which lacks request signing)",
    )
    print_cmd.add_argument(
        "--no-ams-mapping",
        action="store_true",
        help="Skip AMS mapping and use bridge default [0,1,2,3] (diagnostic)",
    )
    print_cmd.add_argument(
        "--local",
        action="store_true",
        help="Force local slicer (fail if not installed)",
    )
    print_cmd.add_argument(
        "--docker-version",
        type=str,
        default=None,
        help="Use a specific OrcaSlicer Docker image version (e.g. 2.3.1)",
    )
    print_cmd.add_argument(
        "--filament-type",
        type=str,
        default=None,
        help="Override filament profile name (e.g. 'Generic PLA @base')",
    )
    print_cmd.add_argument(
        "--filament-slot",
        type=int,
        default=1,
        help="AMS slot for --filament-type (default: 1)",
    )
    print_cmd.add_argument(
        "--sequence",
        type=int,
        default=None,
        help="Print only this sequence number (for sequential printing configs)",
    )

    # profiles subcommand
    profiles_cmd = sub.add_parser("profiles", parents=[common], help="List or pin slicer profiles")
    profiles_sub = profiles_cmd.add_subparsers(dest="profiles_command")

    list_cmd = profiles_sub.add_parser("list", help="List available profiles")
    list_cmd.add_argument(
        "--engine",
        default="orca",
        choices=["orca", "bambu"],
        help="Slicer engine (default: orca)",
    )
    list_cmd.add_argument(
        "--category",
        default=None,
        choices=["machine", "process", "filament"],
        help="Filter by category",
    )

    pin_cmd = profiles_sub.add_parser(
        "pin", help="Pin profiles from config into local profiles/ dir"
    )
    pin_cmd.add_argument(
        "config",
        nargs="?",
        type=Path,
        default=_CONFIG_DEFAULT,
        help="Path to config file (default: ./fabprint.toml)",
    )

    # login subcommand
    login_cmd = sub.add_parser(
        "login", parents=[common], help="Login to Bambu Cloud and cache token"
    )
    login_cmd.add_argument("--email", type=str, default=None, help="Bambu account email")
    login_cmd.add_argument("--password", type=str, default=None, help="Bambu account password")

    # status subcommand
    status_cmd = sub.add_parser(
        "status", parents=[common], help="Query printer status (all or by serial)"
    )
    status_cmd.add_argument(
        "--serial", type=str, default=None, help="Printer serial (default: all printers)"
    )

    # gcode-info subcommand
    gcode_info_cmd = sub.add_parser(
        "gcode-info", parents=[common], help="Analyze sliced gcode (extruders, layers, usage)"
    )
    gcode_info_cmd.add_argument("gcode", type=Path, help="Path to .gcode or .gcode.3mf file")

    # watch subcommand
    watch_cmd = sub.add_parser(
        "watch", parents=[common], help="Live dashboard for all printers (no config needed)"
    )
    watch_cmd.add_argument(
        "--interval", type=int, default=10, help="Refresh interval in seconds (default: 10)"
    )

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    logging.basicConfig(
        level=logging.DEBUG if getattr(args, "verbose", False) else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    try:
        _resolve_config(args)

        if args.command == "plate":
            _cmd_plate(args)
        elif args.command == "slice":
            _cmd_slice(args)
        elif args.command == "print":
            _cmd_print(args)
        elif args.command == "login":
            _cmd_login(args)
        elif args.command == "status":
            _cmd_status(args)
        elif args.command == "watch":
            _cmd_watch(args)
        elif args.command == "gcode-info":
            _cmd_gcode_info(args)
        elif args.command == "profiles":
            _cmd_profiles(args)
    except FabprintError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    except (ValueError, FileNotFoundError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)


def _load_parts(cfg: FabprintConfig, global_scale: float | None = None):
    """Load and prepare meshes from config parts.

    For parts with 'object' set, loads a specific named object from a multi-object
    3MF. Parts from the same file are grouped: a combined mesh is used for
    arrangement so that objects maintain their relative positions.

    Returns (meshes, names, filament_ids, has_paint_colors, part_info, part_sequences).
    part_sequences is a parallel list of sequence numbers for each mesh.
    """
    import trimesh as _trimesh

    meshes = []
    names = []
    filament_ids = []
    part_sequences = []
    has_paint_colors = False
    part_info = []

    # Group parts by source file for object-selection parts
    # so they share the same arrangement position
    file_groups: dict[Path, list[int]] = {}  # file → list of part indices
    for i, part in enumerate(cfg.parts):
        if part.object:
            file_groups.setdefault(part.file, []).append(i)

    # Track which parts are handled via file groups
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

        # Load all objects from the 3MF
        all_objects = dict(load_3mf_objects(file_path))
        group_parts = [cfg.parts[i] for i in part_indices]
        scale = group_parts[0].scale * global_scale if global_scale else group_parts[0].scale

        # Build sub-meshes for each part in the group, tagged with sequence
        sub_meshes = []
        for part in group_parts:
            if part.object not in all_objects:
                raise FabprintError(
                    f"Object '{part.object}' not found in {file_path}. "
                    f"Available: {list(all_objects.keys())}"
                )
            obj_mesh = all_objects[part.object].copy()
            if scale != 1.0:
                obj_mesh.apply_scale(scale)
            obj_mesh.metadata["filament_id"] = part.filament
            obj_mesh.metadata["sequence"] = part.sequence
            sub_meshes.append((part.object, obj_mesh))

        # Combine for arrangement
        combined = _trimesh.util.concatenate([m for _, m in sub_meshes])
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
            part_sequences.append(0)  # 0 = group with mixed sequences, handled in build

    # Process remaining parts (non-grouped)
    for i, part in enumerate(cfg.parts):
        if i in grouped_parts:
            continue

        scale = part.scale * global_scale if global_scale else part.scale

        if part.object:
            # Single object selection (not grouped with other parts)
            all_objects = dict(load_3mf_objects(part.file))
            if part.object not in all_objects:
                raise FabprintError(
                    f"Object '{part.object}' not found in {part.file}. "
                    f"Available: {list(all_objects.keys())}"
                )
            mesh = all_objects[part.object].copy()
            if scale != 1.0:
                mesh.apply_scale(scale)
            mesh.metadata["filament_id"] = part.filament
        elif part.object_filaments:
            # Multi-object 3MF with per-object filaments (multi-material)
            objects = load_3mf_objects(part.file)
            sub_meshes = []
            for obj_name, obj_mesh in objects:
                if scale != 1.0:
                    obj_mesh.apply_scale(scale)
                fil_id = part.object_filaments.get(obj_name, part.filament)
                obj_mesh.metadata["filament_id"] = fil_id
                sub_meshes.append((obj_name, obj_mesh))

            mesh = _trimesh.util.concatenate([m for _, m in sub_meshes])
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

    return meshes, names, filament_ids, has_paint_colors, part_info, part_sequences


def _generate_plate(
    args: argparse.Namespace, output: Path
) -> tuple[FabprintConfig, list[int], bool]:
    """Shared logic: load config, orient, arrange, optionally view, export 3MF.

    Returns (config, filament_ids, has_paint_colors).
    """
    cfg = load_config(args.config)
    global_scale = getattr(args, "scale", None)
    meshes, names, filament_ids, has_paint_colors, part_info, _ = _load_parts(cfg, global_scale)

    _print_summary(part_info, len(meshes), cfg.plate.size)

    placements = arrange(meshes, names, cfg.plate.size, cfg.plate.padding)

    scene = build_plate(placements, cfg.plate.size)
    export_plate(scene, output)

    preview_scene = build_plate(placements, cfg.plate.size, include_bed=True)
    preview_path = output.with_stem(output.stem + "_preview")
    export_plate(preview_scene, preview_path)
    print(f"Preview: {preview_path}")

    return cfg, filament_ids, has_paint_colors, placements


def _generate_sequential_plates(
    args: argparse.Namespace, output_prefix: Path
) -> list[tuple[int, Path, FabprintConfig, list[int], bool]]:
    """Generate one plate per sequence, all sharing the same arrangement.

    Returns list of (seq_num, plate_path, config, filament_ids, has_paint_colors).
    """
    cfg = load_config(args.config)
    global_scale = getattr(args, "scale", None)
    meshes, names, filament_ids, has_paint_colors, part_info, part_sequences = _load_parts(
        cfg, global_scale
    )

    _print_summary(part_info, len(meshes), cfg.plate.size)

    placements = arrange(meshes, names, cfg.plate.size, cfg.plate.padding)

    sequences = sorted(set(s for s in part_sequences if s > 0))
    # Also collect sequences from group sub-objects
    for p in placements:
        group = p.mesh.metadata.get("group_objects")
        if group:
            for _, obj_mesh in group:
                seq = obj_mesh.metadata.get("sequence", 1)
                if seq not in sequences:
                    sequences.append(seq)
    sequences = sorted(set(sequences))

    results = []
    for seq_num in sequences:
        plate_path = output_prefix.parent / f"{output_prefix.stem}_seq{seq_num}.3mf"

        # Filter placements for this sequence
        seq_placements = []
        for pi, p in enumerate(placements):
            group = p.mesh.metadata.get("group_objects")
            if group:
                # Filter group to only this sequence's objects
                seq_objects = [(n, m) for n, m in group if m.metadata.get("sequence", 1) == seq_num]
                if seq_objects:
                    import trimesh as _trimesh

                    # Create a new combined mesh with only this sequence's objects
                    combined = _trimesh.util.concatenate([m for _, m in seq_objects])
                    combined.metadata["filament_id"] = seq_objects[0][1].metadata["filament_id"]
                    combined.metadata["group_objects"] = seq_objects
                    combined.metadata["original_bounds_min"] = p.mesh.metadata.get(
                        "original_bounds_min"
                    )

                    from fabprint.arrange import Placement

                    seq_placements.append(Placement(mesh=combined, name=p.name, x=p.x, y=p.y))
            else:
                # Non-grouped part: include if sequence matches
                if part_sequences[pi] == seq_num:
                    seq_placements.append(p)

        if not seq_placements:
            continue

        scene = build_plate(seq_placements, cfg.plate.size)
        export_plate(scene, plate_path)

        seq_fil_ids = []
        for p in seq_placements:
            seq_fil_ids.append(p.mesh.metadata.get("filament_id", 1))

        results.append((seq_num, plate_path, cfg, seq_fil_ids, has_paint_colors))

    # Preview with all objects and bed outline
    preview_scene = build_plate(placements, cfg.plate.size, include_bed=True)
    preview_path = output_prefix.parent / f"{output_prefix.stem}_preview.3mf"
    export_plate(preview_scene, preview_path)
    print(f"Preview: {preview_path}")

    return results


def _print_summary(
    part_info: list[tuple],
    total: int,
    plate_size: tuple[float, float],
) -> None:
    """Print a build summary table."""
    name_width = max(len(name) for name, *_ in part_info)
    print("\nParts:")
    for name, copies, filament, scale, w, d, h in part_info:
        scale_str = f"  {scale}x" if scale != 1.0 else ""
        print(
            f"  {name:<{name_width}}  x{copies}  slot {filament}"
            f"{scale_str}  {w:.0f}x{d:.0f}x{h:.0f}mm"
        )
    print(f"\nPlate: {total} parts on {plate_size[0]:.0f}x{plate_size[1]:.0f}mm")


def _get_token_file() -> Path:
    """Return the Bambu Cloud token file path, raising if it doesn't exist."""
    token_file_str = os.environ.get("BAMBU_TOKEN_FILE")
    token_file = Path(token_file_str) if token_file_str else Path.home() / ".bambu_cloud_token"
    if not token_file.exists():
        raise FabprintError(f"Token file not found: {token_file}\nRun 'fabprint login' first.")
    return token_file


def _has_sequences(cfg: FabprintConfig) -> bool:
    """Check if config uses sequential printing (multiple distinct sequences)."""
    sequences = {p.sequence for p in cfg.parts}
    return len(sequences) > 1


def _cmd_plate(args: argparse.Namespace) -> None:
    cfg = load_config(args.config)
    if _has_sequences(cfg):
        output = args.output or Path("plate.3mf")
        results = _generate_sequential_plates(args, output)
        for seq_num, plate_path, *_ in results:
            print(f"Sequence {seq_num}: {plate_path}")
    else:
        output = args.output or Path("plate.3mf")
        _generate_plate(args, output)
        print(f"Plate exported to {output}")


def _do_slice(args: argparse.Namespace) -> Path:
    """Arrange, export, and slice. Returns the output directory."""
    from fabprint.slicer import parse_gcode_stats, slice_plate

    output_dir = args.output_dir or Path("output")
    output_dir.mkdir(parents=True, exist_ok=True)
    plate_3mf = output_dir / "plate.3mf"
    cfg, filament_ids, has_paint_colors, _ = _generate_plate(args, plate_3mf)
    print(f"Plate exported to {plate_3mf}")

    # CLI --filament-type overrides the config's filament list with a single
    # filament in the specified AMS slot.
    if args.filament_type:
        filaments = [args.filament_type]
        # Override all parts to use the specified slot
        filament_ids = [args.filament_slot] * len(filament_ids)
    elif has_paint_colors:
        # OrcaSlicer 2.3.1 CLI segfaults on paint_color + --load-filaments.
        # Skip filament profiles when paint_color data is present — the
        # paint_color attributes already encode which extruder to use.
        filaments = None
    else:
        filaments = cfg.slicer.filaments

    if not cfg.slicer.version:
        print(
            "warning: no slicer.version in config — slices are not reproducible. "
            'Add version = "X.Y.Z" to [slicer] to pin the OrcaSlicer version.',
            file=sys.stderr,
        )

    output_dir = slice_plate(
        input_3mf=plate_3mf,
        engine=cfg.slicer.engine,
        output_dir=args.output_dir,
        printer=cfg.slicer.printer,
        process=cfg.slicer.process,
        filaments=filaments,
        filament_ids=filament_ids,
        overrides=cfg.slicer.overrides or None,
        project_dir=cfg.base_dir,
        local=args.local,
        docker_version=args.docker_version,
        required_version=cfg.slicer.version,
    )
    print(f"Sliced gcode in {output_dir}")

    stats = parse_gcode_stats(output_dir)
    parts = []
    if "filament_g" in stats:
        parts.append(f"{stats['filament_g']:.1f}g filament")
    elif "filament_cm3" in stats:
        parts.append(f"{stats['filament_cm3']:.1f}cm3 filament")
    if "print_time" in stats:
        parts.append(f"estimated {stats['print_time']}")
    if parts:
        print(f"  {', '.join(parts)}")

    return output_dir


def _do_slice_sequential(args: argparse.Namespace) -> list[tuple[int, Path]]:
    """Slice each sequence separately. Returns list of (seq_num, output_dir)."""
    from fabprint.slicer import parse_gcode_stats, slice_plate

    output_dir = args.output_dir or Path("output")
    output_dir.mkdir(parents=True, exist_ok=True)
    plate_3mf = output_dir / "plate.3mf"
    results = _generate_sequential_plates(args, plate_3mf)

    sliced = []
    for seq_num, plate_path, cfg, filament_ids, has_paint_colors in results:
        if args.filament_type:
            filaments = [args.filament_type]
            filament_ids = [args.filament_slot] * len(filament_ids)
        elif has_paint_colors:
            filaments = None
        else:
            filaments = cfg.slicer.filaments

        seq_output_dir = (args.output_dir or Path("output")) / f"seq{seq_num}"
        output_dir = slice_plate(
            input_3mf=plate_path,
            engine=cfg.slicer.engine,
            output_dir=seq_output_dir,
            printer=cfg.slicer.printer,
            process=cfg.slicer.process,
            filaments=filaments,
            filament_ids=filament_ids,
            overrides=cfg.slicer.overrides or None,
            project_dir=cfg.base_dir,
            local=args.local,
            docker_version=args.docker_version,
            required_version=cfg.slicer.version,
        )
        print(f"Sequence {seq_num}: sliced to {output_dir}")

        stats = parse_gcode_stats(output_dir)
        parts = []
        if "filament_g" in stats:
            parts.append(f"{stats['filament_g']:.1f}g filament")
        elif "filament_cm3" in stats:
            parts.append(f"{stats['filament_cm3']:.1f}cm3 filament")
        if "print_time" in stats:
            parts.append(f"estimated {stats['print_time']}")
        if parts:
            print(f"  {', '.join(parts)}")

        sliced.append((seq_num, output_dir))

    return sliced


def _cmd_slice(args: argparse.Namespace) -> None:
    cfg = load_config(args.config)
    if _has_sequences(cfg):
        _do_slice_sequential(args)
    else:
        _do_slice(args)


def _cmd_print(args: argparse.Namespace) -> None:
    from fabprint.printer import send_print

    cfg = load_config(args.config)

    if cfg.printer is None:
        raise FabprintError("No [printer] section in config. Required for printing.")

    if args.gcode:
        # Send pre-sliced gcode directly
        gcode_path = args.gcode.resolve()
        if not gcode_path.exists():
            raise FabprintError(f"Gcode file not found: {gcode_path}")
        send_print(
            gcode_path,
            cfg.printer,
            dry_run=args.dry_run,
            upload_only=args.upload_only,
            experimental=getattr(args, "experimental", False),
            skip_ams_mapping=getattr(args, "no_ams_mapping", False),
        )
    elif _has_sequences(cfg):
        # Sequential printing: slice all sequences, send only the requested one
        seq = args.sequence
        if seq is None:
            raise FabprintError(
                "Config has multiple sequences. Use --sequence N to print one at a time."
            )
        sliced = _do_slice_sequential(args)
        match = [d for s, d in sliced if s == seq]
        if not match:
            available = [s for s, _ in sliced]
            raise FabprintError(f"Sequence {seq} not found. Available: {available}")
        gcode_files = list(match[0].glob("*.gcode"))
        if not gcode_files:
            raise FabprintError(f"No gcode files found in {match[0]} for sequence {seq}")
        gcode_path = gcode_files[0]
        print(f"\nSending sequence {seq} to printer...")
        send_print(
            gcode_path,
            cfg.printer,
            dry_run=args.dry_run,
            upload_only=args.upload_only,
            experimental=getattr(args, "experimental", False),
            skip_ams_mapping=getattr(args, "no_ams_mapping", False),
        )
    else:
        # Full pipeline: arrange → slice → find gcode
        output_dir = _do_slice(args)
        gcode_files = list(output_dir.glob("*.gcode"))
        if not gcode_files:
            raise FabprintError(f"No gcode files found in {output_dir}")
        gcode_path = gcode_files[0]
        send_print(
            gcode_path,
            cfg.printer,
            dry_run=args.dry_run,
            upload_only=args.upload_only,
            experimental=getattr(args, "experimental", False),
            skip_ams_mapping=getattr(args, "no_ams_mapping", False),
        )


def _cmd_gcode_info(args: argparse.Namespace) -> None:
    from fabprint.gcode import analyze_gcode

    info = analyze_gcode(args.gcode)

    if not info.layer_count:
        print("No layer data found in gcode.")
        return

    print(f"\nFile: {args.gcode.name}")
    if info.print_time:
        print(f"Print time: {info.print_time}")
    print(f"Layers: {info.layer_count}")
    print(f"Filament changes: {info.filament_changes}")

    if info.spans:
        print("\nExtruder usage by layer:")
        for span in info.spans:
            extruder = span.extruder + 1  # display as 1-indexed
            fil_type = ""
            if span.extruder < len(info.filament_types):
                fil_type = f"  ({info.filament_types[span.extruder]})"
            if span.start_layer == span.end_layer:
                layer_str = f"Layer {span.start_layer}"
            else:
                layer_str = f"Layer {span.start_layer}-{span.end_layer}"
            print(
                f"  {layer_str:>16}  z={span.start_z:.1f}-{span.end_z:.1f}mm"
                f"  extruder {extruder}{fil_type}"
            )

    if info.filament_usage_g:
        used = [
            (i + 1, g, info.filament_types[i] if i < len(info.filament_types) else "")
            for i, g in enumerate(info.filament_usage_g)
            if g > 0
        ]
        if used:
            print("\nFilament usage:")
            for slot, grams, fil_type in used:
                type_str = f"  ({fil_type})" if fil_type else ""
                print(f"  Slot {slot}: {grams:.1f}g{type_str}")


def _cmd_login(args: argparse.Namespace) -> None:
    from fabprint.auth import cloud_login

    cloud_login(email=args.email, password=args.password)


def _cmd_status(args: argparse.Namespace) -> None:
    from fabprint.cloud import cloud_list_devices, cloud_status

    token_file = _get_token_file()

    if args.serial:
        serials = [(args.serial, args.serial)]
    else:
        devices = cloud_list_devices(token_file)
        if not devices:
            print("No printers found.")
            return
        serials = [(d["dev_id"], d.get("name", d["dev_id"])) for d in devices]

    for serial, name in serials:
        print(f"\033[1m{name}\033[0m  ({serial})")
        try:
            status = cloud_status(serial, token_file)
            for line in _render_printer(status, name, serial):
                print(line)
        except Exception as e:
            print(f"  \033[31merror: {e}\033[0m")
        print()


def _render_printer(status: dict, name: str, serial: str) -> list[str]:
    """Render a single printer's status as lines of text."""
    from fabprint.cloud import parse_ams_trays

    _PRINT_STAGES = {
        "0": "printing",
        "1": "auto bed leveling",
        "2": "heatbed preheating",
        "3": "sweeping XY mech mode",
        "4": "changing filament",
        "5": "M400 pause",
        "6": "filament runout pause",
        "7": "heating hotend",
        "8": "calibrating extrusion",
        "9": "scanning bed surface",
        "10": "inspecting first layer",
        "11": "identifying build plate type",
        "12": "calibrating micro lidar",
        "13": "homing toolhead",
        "14": "cleaning nozzle tip",
        "17": "calibrating extrusion flow",
        "18": "vibration compensation",
        "19": "motor noise calibration",
    }

    lines: list[str] = []
    state = status.get("gcode_state", "unknown")
    lines.append(f"  State:    {state}")

    task_name = status.get("subtask_name", "")
    if task_name:
        lines.append(f"  Task:     {task_name}")

    if state not in ("IDLE", "FINISH", "FAILED", ""):
        layer = status.get("layer_num", 0)
        stage_id = str(status.get("mc_print_stage", ""))
        # mc_print_stage doesn't update reliably during printing —
        # override with "printing" when we have layer progress.
        if layer and int(layer) > 0:
            stage = "printing"
        else:
            stage = _PRINT_STAGES.get(stage_id, "")
        if stage:
            lines.append(f"  Stage:    {stage}")
        percent = int(status.get("mc_percent", 0))
        layer = status.get("layer_num", 0)
        total_layers = status.get("total_layer_num", 0)

        # Progress bar
        bar_width = 30
        filled = int(bar_width * percent / 100)
        bar = "\u2588" * filled + "\u2591" * (bar_width - filled)
        progress = f"  Progress: [{bar}] {percent}%"
        if total_layers:
            progress += f" (layer {layer}/{total_layers})"
        lines.append(progress)

        remaining = int(status.get("mc_remaining_time", 0))
        if remaining:
            import time as _time

            h, m = divmod(remaining, 60)
            eta = _time.strftime("%H:%M", _time.localtime(_time.time() + remaining * 60))
            time_str = f"{h}h {m}m" if h else f"{m}m"
            lines.append(f"  ETA:      {time_str} remaining (done ~{eta})")

    nozzle = status.get("nozzle_temper", 0)
    nozzle_target = status.get("nozzle_target_temper", 0)
    bed = status.get("bed_temper", 0)
    bed_target = status.get("bed_target_temper", 0)
    nozzle_str = f"{nozzle:.0f}\u00b0C"
    if nozzle_target:
        nozzle_str += f" \u2192 {nozzle_target:.0f}\u00b0C"
    bed_str = f"{bed:.0f}\u00b0C"
    if bed_target:
        bed_str += f" \u2192 {bed_target:.0f}\u00b0C"
    lines.append(f"  Nozzle:   {nozzle_str}")
    lines.append(f"  Bed:      {bed_str}")

    ams_trays = parse_ams_trays(status)
    if ams_trays:
        tray_now_raw = int(status.get("ams", {}).get("tray_now", 255))
        lines.append("  AMS:")
        for t in ams_trays:
            active = " <-- printing" if t["phys_slot"] == tray_now_raw else ""
            c = t["color"]
            r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
            swatch = f"\033[48;2;{r};{g};{b}m  \033[0m"
            lines.append(f"    slot {t['phys_slot'] + 1}  {t['type']:<12}  {swatch} #{c}{active}")

    return lines


def _cmd_watch(args: argparse.Namespace) -> None:
    """Live dashboard showing all bound printers."""
    import time

    from fabprint.cloud import PersistentBridge, cloud_list_devices

    token_file = _get_token_file()
    interval = args.interval

    print("Discovering printers...")
    devices = cloud_list_devices(token_file)
    if not devices:
        print("No printers found.")
        return

    printer_names = {d["dev_id"]: d.get("name", d["dev_id"]) for d in devices}
    serials = [d["dev_id"] for d in devices]
    print(f"Found {len(serials)} printer(s): {', '.join(printer_names.values())}")

    with PersistentBridge(token_file) as bridge:
        try:
            while True:
                t0 = time.monotonic()
                output_lines = []

                for serial in serials:
                    name = printer_names[serial]
                    output_lines.append(f"\033[1m{name}\033[0m  ({serial})")
                    try:
                        status = bridge.status(serial)
                        output_lines.extend(_render_printer(status, name, serial))
                    except Exception as e:
                        output_lines.append(f"  \033[31merror: {e}\033[0m")
                    output_lines.append("")

                elapsed = time.monotonic() - t0
                now = time.strftime("%H:%M:%S")
                header = f"fabprint watch  {now}  (polled in {elapsed:.1f}s, Ctrl-C to quit)"

                # Clear screen and print
                sys.stdout.write("\033[2J\033[H")
                sys.stdout.write(header + "\n\n" + "\n".join(output_lines))
                sys.stdout.flush()

                sleep_time = max(1, interval - elapsed)
                time.sleep(sleep_time)
        except KeyboardInterrupt:
            print("\n")


def _cmd_profiles(args: argparse.Namespace) -> None:
    from fabprint.profiles import CATEGORIES, discover_profiles, pin_profiles

    if args.profiles_command == "list":
        profiles = discover_profiles(args.engine)
        categories = [args.category] if args.category else list(CATEGORIES)
        for cat in categories:
            names = profiles.get(cat, {})
            print(f"\n{cat} ({len(names)} profiles):")
            for name in names:
                print(f"  {name}")

    elif args.profiles_command == "pin":
        cfg = load_config(args.config)
        pinned = pin_profiles(
            engine=cfg.slicer.engine,
            printer=cfg.slicer.printer,
            process=cfg.slicer.process,
            filaments=cfg.slicer.filaments,
            project_dir=cfg.base_dir,
        )
        print(f"Pinned {len(pinned)} profile(s)")
        for p in pinned:
            print(f"  {p}")

    else:
        print("Usage: fabprint profiles {list|pin}")
        sys.exit(1)
