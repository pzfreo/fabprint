"""CLI entry point for fabprint."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from fabprint import __version__
from fabprint.arrange import arrange
from fabprint.config import FabprintConfig, load_config
from fabprint.loader import extract_paint_colors, load_mesh
from fabprint.orient import orient_mesh
from fabprint.plate import build_plate, export_plate


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="fabprint",
        description="Headless 3D print pipeline: arrange, slice, and print",
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
    slice_cmd.add_argument(
        "--docker",
        action="store_true",
        help="Force slicing via Docker (even if local slicer is available)",
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
        "print", parents=[common], help="Arrange, slice, and send to printer"
    )
    print_cmd.add_argument("config", type=Path, help="Path to fabprint.toml")
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
        "--view", action="store_true", help="Show plate in viewer before slicing"
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
        "--docker",
        action="store_true",
        help="Force slicing via Docker (even if local slicer is available)",
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
    pin_cmd.add_argument("config", type=Path, help="Path to fabprint.toml")

    # status subcommand
    status_cmd = sub.add_parser("status", parents=[common], help="Query live printer status")
    status_cmd.add_argument("config", type=Path, help="Path to fabprint.toml")

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

    if args.command == "plate":
        _cmd_plate(args)
    elif args.command == "slice":
        _cmd_slice(args)
    elif args.command == "print":
        _cmd_print(args)
    elif args.command == "status":
        _cmd_status(args)
    elif args.command == "watch":
        _cmd_watch(args)
    elif args.command == "profiles":
        _cmd_profiles(args)


def _generate_plate(
    args: argparse.Namespace, output: Path
) -> tuple[FabprintConfig, list[int], bool]:
    """Shared logic: load config, orient, arrange, optionally view, export 3MF.

    Returns (config, filament_ids, has_paint_colors).
    """
    cfg = load_config(args.config)

    meshes = []
    names = []
    filament_ids = []
    has_paint_colors = False
    part_info = []  # (name, copies, filament, scale, w, d, h) per unique part
    global_scale = getattr(args, "scale", None)
    for part in cfg.parts:
        base_mesh = load_mesh(part.file)
        oriented = orient_mesh(base_mesh, part.orient, part.rotate)
        scale = part.scale * global_scale if global_scale else part.scale
        if scale != 1.0:
            oriented.apply_scale(scale)
        # Store paint data in metadata (survives copy/transform)
        oriented.metadata["filament_id"] = part.filament
        paint_colors = extract_paint_colors(part.file)
        if paint_colors:
            oriented.metadata["paint_colors"] = paint_colors
            has_paint_colors = True
        w, d, h = oriented.extents
        part_info.append((part.file.stem, part.copies, part.filament, scale, w, d, h))
        for i in range(part.copies):
            meshes.append(oriented.copy())
            suffix = f"_{i + 1}" if part.copies > 1 else ""
            names.append(f"{part.file.stem}{suffix}")
            filament_ids.append(part.filament)

    _print_summary(part_info, len(meshes), cfg.plate.size)

    placements = arrange(meshes, names, cfg.plate.size, cfg.plate.padding)

    if getattr(args, "view", False):
        from fabprint.viewer import show_plate

        show_plate([p.mesh for p in placements], [p.name for p in placements], cfg.plate.size)

    scene = build_plate(placements, cfg.plate.size)
    export_plate(scene, output)
    return cfg, filament_ids, has_paint_colors


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


def _cmd_plate(args: argparse.Namespace) -> None:
    output = args.output or Path("plate.3mf")
    _generate_plate(args, output)
    print(f"Plate exported to {output}")


def _do_slice(args: argparse.Namespace) -> Path:
    """Arrange, export, and slice. Returns the output directory."""
    from fabprint.slicer import parse_gcode_stats, slice_plate

    plate_3mf = Path("plate.3mf")
    cfg, filament_ids, has_paint_colors = _generate_plate(args, plate_3mf)
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
        docker=args.docker or args.docker_version is not None,
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


def _cmd_slice(args: argparse.Namespace) -> None:
    _do_slice(args)


def _cmd_print(args: argparse.Namespace) -> None:
    from fabprint.printer import send_print

    cfg = load_config(args.config)

    if cfg.printer is None:
        raise ValueError("No [printer] section in config. Required for printing.")

    if args.gcode:
        # Send pre-sliced gcode directly
        gcode_path = args.gcode.resolve()
        if not gcode_path.exists():
            raise FileNotFoundError(f"Gcode file not found: {gcode_path}")
    else:
        # Full pipeline: arrange → slice → find gcode
        output_dir = _do_slice(args)
        gcode_files = list(output_dir.glob("*.gcode"))
        if not gcode_files:
            raise RuntimeError(f"No gcode files found in {output_dir}")
        gcode_path = gcode_files[0]

    send_print(
        gcode_path,
        cfg.printer,
        dry_run=args.dry_run,
        upload_only=args.upload_only,
        experimental=getattr(args, "experimental", False),
        skip_ams_mapping=getattr(args, "no_ams_mapping", False),
    )


def _cmd_status(args: argparse.Namespace) -> None:
    from fabprint.cloud import parse_ams_trays
    from fabprint.printer import get_printer_status

    cfg = load_config(args.config)
    if cfg.printer is None:
        raise ValueError("No [printer] section in config.")

    serial = cfg.printer.serial or os.environ.get("BAMBU_SERIAL")
    if not serial:
        raise ValueError("No serial in [printer] config or BAMBU_SERIAL env var.")

    print(f"Querying printer {serial}...")
    status = get_printer_status(serial)

    state = status.get("gcode_state", "unknown")
    percent = status.get("mc_percent", 0)
    layer = status.get("layer_num", 0)
    total_layers = status.get("total_layer_num", 0)
    remaining = status.get("mc_remaining_time", 0)

    # Print stage descriptions (from Bambu firmware mc_print_stage values)
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

    print(f"  State:    {state}")

    task_name = status.get("subtask_name", "")
    if task_name:
        print(f"  Task:     {task_name}")

    if state not in ("IDLE", "FINISH", "FAILED", ""):
        stage_id = str(status.get("mc_print_stage", ""))
        if layer and int(layer) > 0:
            stage = "printing"
        else:
            stage = _PRINT_STAGES.get(stage_id, "")
        if stage:
            print(f"  Stage:    {stage}")
        print(f"  Progress: {percent}%", end="")
        if total_layers:
            print(f" (layer {layer}/{total_layers})", end="")
        print()
        if remaining:
            h, m = divmod(int(remaining), 60)
            print(f"  Remaining: {h}h {m}m" if h else f"  Remaining: {m}m")

    # Temperatures
    nozzle = status.get("nozzle_temper", 0)
    nozzle_target = status.get("nozzle_target_temper", 0)
    bed = status.get("bed_temper", 0)
    bed_target = status.get("bed_target_temper", 0)
    nozzle_str = f"{nozzle:.0f}°C"
    if nozzle_target:
        nozzle_str += f" → {nozzle_target:.0f}°C"
    bed_str = f"{bed:.0f}°C"
    if bed_target:
        bed_str += f" → {bed_target:.0f}°C"
    print(f"  Nozzle:   {nozzle_str}")
    print(f"  Bed:      {bed_str}")

    ams_trays = parse_ams_trays(status)
    if ams_trays:
        tray_now_raw = int(status.get("ams", {}).get("tray_now", 255))
        print("  AMS:")
        for t in ams_trays:
            active = " <-- printing" if t["phys_slot"] == tray_now_raw else ""
            c = t["color"]
            r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
            swatch = f"\033[48;2;{r};{g};{b}m  \033[0m"
            print(f"    slot {t['phys_slot'] + 1}  {t['type']:<12}  {swatch} #{c}{active}")


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
        percent = status.get("mc_percent", 0)
        layer = status.get("layer_num", 0)
        total_layers = status.get("total_layer_num", 0)
        progress = f"  Progress: {percent}%"
        if total_layers:
            progress += f" (layer {layer}/{total_layers})"
        lines.append(progress)
        remaining = status.get("mc_remaining_time", 0)
        if remaining:
            h, m = divmod(int(remaining), 60)
            lines.append(f"  Remaining: {h}h {m}m" if h else f"  Remaining: {m}m")

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

    from fabprint.cloud import cloud_list_devices, cloud_status

    token_file_str = os.environ.get("BAMBU_TOKEN_FILE")
    token_file = Path(token_file_str) if token_file_str else Path.home() / ".bambu_cloud_token"
    if not token_file.exists():
        print(f"Token file not found: {token_file}")
        print("Run 'python scripts/bambu_cloud_login.py' first, or set BAMBU_TOKEN_FILE.")
        sys.exit(1)
    interval = args.interval

    print("Discovering printers...")
    devices = cloud_list_devices(token_file)
    if not devices:
        print("No printers found.")
        return

    printer_names = {d["dev_id"]: d.get("name", d["dev_id"]) for d in devices}
    serials = [d["dev_id"] for d in devices]
    print(f"Found {len(serials)} printer(s): {', '.join(printer_names.values())}")

    try:
        while True:
            output_lines = []
            now = time.strftime("%H:%M:%S")
            output_lines.append(
                f"fabprint watch  {now}  (refresh every {interval}s, Ctrl-C to quit)"
            )
            output_lines.append("")

            for serial in serials:
                name = printer_names[serial]
                output_lines.append(f"\033[1m{name}\033[0m  ({serial})")
                try:
                    status = cloud_status(serial, token_file)
                    output_lines.extend(_render_printer(status, name, serial))
                except Exception as e:
                    output_lines.append(f"  \033[31merror: {e}\033[0m")
                output_lines.append("")

            # Clear screen and print
            sys.stdout.write("\033[2J\033[H")
            sys.stdout.write("\n".join(output_lines))
            sys.stdout.flush()

            time.sleep(interval)
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
