"""CLI entry point for fabprint."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from fabprint.arrange import arrange
from fabprint.config import load_config
from fabprint.loader import load_mesh
from fabprint.orient import orient_mesh
from fabprint.plate import build_plate, export_plate


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="fabprint",
        description="Headless 3D print pipeline: arrange, slice, and print",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")
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
    slice_cmd.add_argument("-o", "--output-dir", type=Path, default=None, help="Output directory")
    slice_cmd.add_argument(
        "--view", action="store_true", help="Show plate in viewer before slicing"
    )

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    if args.command == "plate":
        _cmd_plate(args)
    elif args.command == "slice":
        _cmd_slice(args)


def _generate_plate(args: argparse.Namespace, output: Path) -> None:
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

    cfg = load_config(args.config)

    # Generate plate 3MF to a temp file if no explicit output
    plate_3mf = Path("plate.3mf")
    _generate_plate(args, plate_3mf)
    print(f"Plate exported to {plate_3mf}")

    output_dir = slice_plate(
        input_3mf=plate_3mf,
        engine=cfg.slicer.engine,
        output_dir=args.output_dir,
        print_profile=cfg.slicer.print_profile,
        filaments=cfg.slicer.filaments,
        printer_profile=cfg.slicer.printer_profile,
    )
    print(f"Sliced gcode in {output_dir}")
