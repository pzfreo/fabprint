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


def _generate_plate(args: argparse.Namespace, output: Path) -> FabprintConfig:
    """Shared logic: load config, orient, arrange, optionally view, export 3MF."""
    cfg = load_config(args.config)

    meshes = []
    names = []
    for part in cfg.parts:
        base_mesh = load_mesh(part.file)
        oriented = orient_mesh(base_mesh, part.orient)
        for i in range(part.copies):
            meshes.append(oriented.copy())
            suffix = f"_{i + 1}" if part.copies > 1 else ""
            names.append(f"{part.file.stem}{suffix}")

    logging.info("Loaded %d parts (%d unique)", len(meshes), len(cfg.parts))

    placements = arrange(meshes, names, cfg.plate.size, cfg.plate.padding)

    if getattr(args, "view", False):
        from fabprint.viewer import show_plate

        show_plate([p.mesh for p in placements], [p.name for p in placements])

    scene = build_plate(placements)
    export_plate(scene, output)
    return cfg


def _cmd_plate(args: argparse.Namespace) -> None:
    output = args.output or Path("plate.3mf")
    _generate_plate(args, output)
    print(f"Plate exported to {output}")


def _cmd_slice(args: argparse.Namespace) -> None:
    from fabprint.slicer import slice_plate

    plate_3mf = Path("plate.3mf")
    cfg = _generate_plate(args, plate_3mf)
    print(f"Plate exported to {plate_3mf}")

    output_dir = slice_plate(
        input_3mf=plate_3mf,
        engine=cfg.slicer.engine,
        output_dir=args.output_dir,
        printer=cfg.slicer.printer,
        process=cfg.slicer.process,
        filaments=cfg.slicer.filaments,
        project_dir=cfg.base_dir,
    )
    print(f"Sliced gcode in {output_dir}")


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
