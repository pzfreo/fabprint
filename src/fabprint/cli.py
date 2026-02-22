"""CLI entry point for fabprint."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from fabprint.arrange import arrange
from fabprint.config import FabprintConfig, load_config
from fabprint.loader import load_mesh
from fabprint.orient import orient_mesh
from fabprint.plate import build_plate, export_plate


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="fabprint",
        description="Headless 3D print pipeline: arrange, slice, and print",
    )
    sub = parser.add_subparsers(dest="command")

    # Shared args for subcommands
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")

    # plate subcommand
    plate_cmd = sub.add_parser(
        "plate", parents=[common], help="Arrange parts and export a 3MF plate"
    )
    plate_cmd.add_argument("config", type=Path, help="Path to fabprint.toml")
    plate_cmd.add_argument("-o", "--output", type=Path, default=None, help="Output 3MF path")
    plate_cmd.add_argument("--view", action="store_true", help="Show plate in viewer")

    # slice subcommand
    slice_cmd = sub.add_parser(
        "slice", parents=[common], help="Arrange, export, and slice to gcode"
    )
    slice_cmd.add_argument("config", type=Path, help="Path to fabprint.toml")
    slice_cmd.add_argument(
        "-o", "--output-dir", type=Path, default=None, help="Output directory"
    )
    slice_cmd.add_argument(
        "--view", action="store_true", help="Show plate in viewer before slicing"
    )
    slice_cmd.add_argument(
        "--docker", action="store_true",
        help="Force slicing via Docker (even if local slicer is available)",
    )
    slice_cmd.add_argument(
        "--docker-version", type=str, default=None,
        help="Use a specific OrcaSlicer Docker image version (e.g. 2.3.1)",
    )

    # profiles subcommand
    profiles_cmd = sub.add_parser(
        "profiles", parents=[common], help="List or pin slicer profiles"
    )
    profiles_sub = profiles_cmd.add_subparsers(dest="profiles_command")

    list_cmd = profiles_sub.add_parser("list", help="List available profiles")
    list_cmd.add_argument(
        "--engine", default="orca", choices=["orca", "bambu"],
        help="Slicer engine (default: orca)",
    )
    list_cmd.add_argument(
        "--category", default=None, choices=["machine", "process", "filament"],
        help="Filter by category",
    )

    pin_cmd = profiles_sub.add_parser(
        "pin", help="Pin profiles from config into local profiles/ dir"
    )
    pin_cmd.add_argument("config", type=Path, help="Path to fabprint.toml")

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    logging.basicConfig(
        level=logging.DEBUG if getattr(args, "verbose", False) else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    if args.command == "plate":
        _cmd_plate(args)
    elif args.command == "slice":
        _cmd_slice(args)
    elif args.command == "profiles":
        _cmd_profiles(args)


def _generate_plate(
    args: argparse.Namespace, output: Path
) -> tuple[FabprintConfig, list[int]]:
    """Shared logic: load config, orient, arrange, optionally view, export 3MF."""
    cfg = load_config(args.config)

    meshes = []
    names = []
    filament_ids = []
    part_info = []  # (name, copies, filament, scale, w, d, h) per unique part
    for part in cfg.parts:
        base_mesh = load_mesh(part.file)
        oriented = orient_mesh(base_mesh, part.orient, part.rotate)
        if part.scale != 1.0:
            oriented.apply_scale(part.scale)
        w, d, h = oriented.extents
        part_info.append((part.file.stem, part.copies, part.filament, part.scale, w, d, h))
        for i in range(part.copies):
            meshes.append(oriented.copy())
            suffix = f"_{i + 1}" if part.copies > 1 else ""
            names.append(f"{part.file.stem}{suffix}")
            filament_ids.append(part.filament)

    _print_summary(part_info, len(meshes), cfg.plate.size)

    placements = arrange(meshes, names, cfg.plate.size, cfg.plate.padding)

    if getattr(args, "view", False):
        from fabprint.viewer import show_plate

        show_plate(
            [p.mesh for p in placements], [p.name for p in placements], cfg.plate.size
        )

    scene = build_plate(placements, cfg.plate.size)
    export_plate(scene, output)
    return cfg, filament_ids


def _print_summary(
    part_info: list[tuple], total: int, plate_size: tuple[float, float],
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


def _cmd_plate(args: argparse.Namespace) -> None:
    output = args.output or Path("plate.3mf")
    cfg, _filament_ids = _generate_plate(args, output)
    print(f"Plate exported to {output}")


def _cmd_slice(args: argparse.Namespace) -> None:
    from fabprint.slicer import parse_gcode_stats, slice_plate

    plate_3mf = Path("plate.3mf")
    cfg, filament_ids = _generate_plate(args, plate_3mf)
    print(f"Plate exported to {plate_3mf}")

    output_dir = slice_plate(
        input_3mf=plate_3mf,
        engine=cfg.slicer.engine,
        output_dir=args.output_dir,
        printer=cfg.slicer.printer,
        process=cfg.slicer.process,
        filaments=cfg.slicer.filaments,
        filament_ids=filament_ids,
        overrides=cfg.slicer.overrides or None,
        project_dir=cfg.base_dir,
        docker=args.docker or args.docker_version is not None,
        docker_version=args.docker_version,
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
