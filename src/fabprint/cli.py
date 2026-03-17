"""CLI entry point for fabprint."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from fabprint import FabprintError, __version__
from fabprint.config import load_config

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Hamilton driver helpers
# ---------------------------------------------------------------------------


def _build_driver(verbose: bool = False):
    """Build a Hamilton driver wired to the fabprint pipeline."""
    import os

    # Disable Hamilton telemetry before first import
    os.environ["HAMILTON_TELEMETRY_ENABLED"] = "false"

    # Silence all Hamilton loggers (pandera warnings, tracebacks, error boxes)
    logging.getLogger("hamilton").setLevel(logging.CRITICAL + 1)

    from hamilton import driver

    from fabprint import adapters, pipeline

    builder = driver.Builder().with_modules(pipeline)
    if verbose:
        builder = builder.with_adapters(adapters.TimingAdapter())
    return builder.build()


def _gather_inputs(args: argparse.Namespace, output_3mf: Path) -> dict:
    """Build the full set of Hamilton driver inputs from CLI args."""
    output_dir = getattr(args, "output_dir", None) or Path("output")
    return {
        "config_path": args.config,
        "global_scale": getattr(args, "scale", None),
        "output_3mf": output_3mf,
        "output_dir": output_dir,
        "slicer_local": getattr(args, "local", False),
        "docker_version": getattr(args, "docker_version", None),
        "filament_type_override": getattr(args, "filament_type", None),
        "filament_slot_override": getattr(args, "filament_slot", 1),
        "dry_run": getattr(args, "dry_run", False),
        "upload_only": getattr(args, "upload_only", False),
        "experimental": getattr(args, "experimental", False),
        "skip_ams_mapping": getattr(args, "no_ams_mapping", False),
    }


def _display_results(result: dict) -> None:
    """Print human-readable output from pipeline results."""
    if "part_summary" in result:
        print(result["part_summary"])
    if "plate_3mf_path" in result:
        print(f"Plate exported to {result['plate_3mf_path']}")
    if "preview_path" in result:
        print(f"Preview: {result['preview_path']}")
    if "sliced_output_dir" in result:
        print(f"Sliced gcode in {result['sliced_output_dir']}")
    if "gcode_stats" in result:
        stats = result["gcode_stats"]
        parts = []
        if "filament_g" in stats:
            parts.append(f"{stats['filament_g']:.1f}g filament")
        elif "filament_cm3" in stats:
            parts.append(f"{stats['filament_cm3']:.1f}cm3 filament")
        if "print_time" in stats:
            parts.append(f"estimated {stats['print_time']}")
        if parts:
            print(f"  {', '.join(parts)}")


# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------


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

    # --- run subcommand (the pipeline) ---
    run_cmd = sub.add_parser(
        "run", parents=[common], help="Run the pipeline defined in fabprint.toml"
    )
    run_cmd.add_argument(
        "config",
        nargs="?",
        type=Path,
        default=_CONFIG_DEFAULT,
        help="Path to config file (default: ./fabprint.toml)",
    )
    run_cmd.add_argument("-o", "--output-dir", type=Path, default=None, help="Output directory")
    run_cmd.add_argument(
        "--until",
        type=str,
        default=None,
        metavar="STAGE",
        help="Run pipeline up to and including this stage",
    )
    run_cmd.add_argument(
        "--only",
        type=str,
        default=None,
        metavar="STAGE",
        help="Run only this stage (fail if required artifacts don't exist)",
    )
    run_cmd.add_argument(
        "--scale",
        type=float,
        default=None,
        help="Scale all parts by this factor (multiplies per-part scale)",
    )
    run_cmd.add_argument(
        "--local",
        action="store_true",
        help="Force local slicer (fail if not installed)",
    )
    run_cmd.add_argument(
        "--docker-version",
        type=str,
        default=None,
        help="Use a specific OrcaSlicer Docker image version (e.g. 2.3.1)",
    )
    run_cmd.add_argument(
        "--filament-type",
        type=str,
        default=None,
        help="Override filament profile name (e.g. 'Generic PLA @base')",
    )
    run_cmd.add_argument(
        "--filament-slot",
        type=int,
        default=1,
        help="AMS slot for --filament-type (default: 1)",
    )
    run_cmd.add_argument(
        "--dry-run", action="store_true", help="Do everything except send to printer"
    )
    run_cmd.add_argument(
        "--upload-only",
        action="store_true",
        help="Upload gcode but don't start printing",
    )
    run_cmd.add_argument(
        "--experimental",
        action="store_true",
        help="Enable experimental printer modes",
    )
    run_cmd.add_argument(
        "--no-ams-mapping",
        action="store_true",
        help="Skip AMS mapping and use bridge default [0,1,2,3] (diagnostic)",
    )

    # --- profiles subcommand ---
    profiles_cmd = sub.add_parser("profiles", parents=[common], help="List or pin slicer profiles")
    profiles_sub = profiles_cmd.add_subparsers(dest="profiles_command")

    list_cmd = profiles_sub.add_parser("list", help="List available profiles")
    list_cmd.add_argument(
        "--engine",
        default="orca",
        choices=["orca"],
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

    # --- init subcommand ---
    init_cmd = sub.add_parser(
        "init", parents=[common], help="Create a new fabprint.toml config file"
    )
    init_cmd.add_argument(
        "--template",
        action="store_true",
        help="Dump a commented template to stdout (no wizard)",
    )
    init_cmd.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output file path (default: ./fabprint.toml)",
    )

    # --- validate subcommand ---
    validate_cmd = sub.add_parser(
        "validate", parents=[common], help="Check a fabprint.toml for issues"
    )
    validate_cmd.add_argument(
        "config",
        nargs="?",
        type=Path,
        default=_CONFIG_DEFAULT,
        help="Path to config file (default: ./fabprint.toml)",
    )

    # --- setup subcommand ---
    sub.add_parser(
        "setup",
        parents=[common],
        help="Set up a printer (credentials, cloud login, connection type)",
    )

    # --- login subcommand ---
    login_cmd = sub.add_parser(
        "login", parents=[common], help="Login to Bambu Cloud and cache token"
    )
    login_cmd.add_argument("--email", type=str, default=None, help="Bambu account email")
    login_cmd.add_argument("--password", type=str, default=None, help="Bambu account password")

    # --- status subcommand ---
    status_cmd = sub.add_parser(
        "status", parents=[common], help="Query printer status (all configured or by name)"
    )
    status_cmd.add_argument(
        "--printer", type=str, default=None, help="Printer name from credentials.toml"
    )
    status_cmd.add_argument(
        "--serial", type=str, default=None, help="Bambu printer serial (cloud only, legacy)"
    )

    # --- watch subcommand ---
    watch_cmd = sub.add_parser(
        "watch", parents=[common], help="Live dashboard for all configured printers"
    )
    watch_cmd.add_argument(
        "--printer", type=str, default=None, help="Printer name from credentials.toml"
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

        if args.command == "run":
            _cmd_run(args)
        elif args.command == "init":
            _cmd_init(args)
        elif args.command == "validate":
            _cmd_validate(args)
        elif args.command == "setup":
            _cmd_setup(args)
        elif args.command == "login":
            _cmd_login(args)
        elif args.command == "status":
            _cmd_status(args)
        elif args.command == "watch":
            _cmd_watch(args)
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


# ---------------------------------------------------------------------------
# run command
# ---------------------------------------------------------------------------


def _cmd_run(args: argparse.Namespace) -> None:
    from fabprint.pipeline import resolve_outputs, resolve_overrides

    if args.until and args.only:
        raise ValueError("Cannot use both --until and --only")

    cfg = load_config(args.config)
    stages = cfg.pipeline.stages

    output_dir = args.output_dir or Path("output")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_3mf = output_dir / "plate.3mf"

    # Determine which Hamilton nodes to request
    outputs = resolve_outputs(stages, until=args.until, only=args.only)

    # Build overrides for --only mode
    overrides = {}
    if args.only:
        overrides = resolve_overrides(args.only, output_dir)

    verbose = getattr(args, "verbose", False)
    dr = _build_driver(verbose=verbose)
    inputs = _gather_inputs(args, output_3mf)

    result = dr.execute(outputs, inputs=inputs, overrides=overrides)
    _display_results(result)


# ---------------------------------------------------------------------------
# init / validate commands
# ---------------------------------------------------------------------------


def _cmd_init(args: argparse.Namespace) -> None:
    from fabprint.init import dump_template, run_wizard

    if args.template:
        print(dump_template(), end="")
    else:
        run_wizard(output=args.output)


def _cmd_setup(args: argparse.Namespace) -> None:
    from fabprint.credentials import setup_printer

    setup_printer()


def _cmd_validate(args: argparse.Namespace) -> None:
    from fabprint.init import validate_config

    warnings = validate_config(args.config)
    if warnings:
        for w in warnings:
            print(f"  warning: {w}")
        print(f"\n{len(warnings)} warning(s) found.")
    else:
        print("Config OK — no issues found.")


# ---------------------------------------------------------------------------
# Non-pipeline subcommand handlers
# ---------------------------------------------------------------------------


def _cmd_login(args: argparse.Namespace) -> None:
    from fabprint.auth import cloud_login

    cloud_login(email=args.email, password=args.password)


def _cmd_status(args: argparse.Namespace) -> None:
    from fabprint.credentials import list_printers, load_printer_credentials

    printers = _resolve_status_printers(args, list_printers, load_printer_credentials)

    for name, creds in printers:
        ptype = creds.get("type", "unknown")
        print(f"\033[1m{name}\033[0m  ({ptype})")
        try:
            status = _query_printer_status(name, creds)
            for line in _render_printer(status, name, creds.get("serial", "")):
                print(line)
        except Exception as e:
            print(f"  \033[31merror: {e}\033[0m")
        print()


def _resolve_status_printers(args, list_printers_fn, load_creds_fn):
    """Build list of (name, creds) tuples for status/watch commands."""
    # --printer flag: single named printer
    if getattr(args, "printer", None):
        creds = load_creds_fn(args.printer)
        return [(args.printer, creds)]

    # --serial flag (legacy cloud-only): query by serial directly
    if getattr(args, "serial", None):
        return [(args.serial, {"type": "bambu-cloud", "serial": args.serial})]

    # Default: all configured printers
    all_printers = list_printers_fn()
    if not all_printers:
        raise FabprintError("No printers configured.\nRun 'fabprint setup' to add a printer.")
    return [(name, {**creds}) for name, creds in all_printers.items()]


def _query_printer_status(name: str, creds: dict) -> dict:
    """Query a single printer's status, dispatching by type."""
    ptype = creds.get("type")

    if ptype == "bambu-cloud":
        from fabprint.cloud import cloud_status
        from fabprint.credentials import cloud_token_json

        serial = creds.get("serial")
        if not serial:
            raise ValueError(f"Printer '{name}' has no serial")
        with cloud_token_json() as token_file:
            return cloud_status(serial, token_file)

    elif ptype == "bambu-lan":
        from fabprint.printer import get_lan_status

        ip = creds.get("ip") or ""
        access_code = creds.get("access_code") or ""
        serial = creds.get("serial") or ""
        if not all([ip, access_code, serial]):
            raise ValueError(f"bambu-lan printer '{name}' requires ip, access_code, serial")
        return get_lan_status(ip, access_code, serial)

    elif ptype == "moonraker":
        from fabprint.printer import get_moonraker_status

        url = creds.get("url")
        if not url:
            raise ValueError(f"moonraker printer '{name}' requires url")
        return get_moonraker_status(url, creds.get("api_key"))

    else:
        raise ValueError(f"Unknown printer type '{ptype}' for '{name}'")


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
        if layer and int(layer) > 0:
            stage = "printing"
        else:
            stage = _PRINT_STAGES.get(stage_id, "")
        if stage:
            lines.append(f"  Stage:    {stage}")
        percent = int(status.get("mc_percent", 0))
        layer = status.get("layer_num", 0)
        total_layers = status.get("total_layer_num", 0)

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
    """Live dashboard showing all configured printers."""
    import time

    from fabprint.credentials import list_printers, load_printer_credentials

    interval = args.interval

    printers = _resolve_status_printers(args, list_printers, load_printer_credentials)
    print(f"Watching {len(printers)} printer(s): {', '.join(n for n, _ in printers)}")

    # For cloud printers, keep a persistent bridge container to avoid Docker overhead
    cloud_printers = [(n, c) for n, c in printers if c.get("type") == "bambu-cloud"]
    bridge_ctx = None
    if cloud_printers:
        from fabprint.cloud import PersistentBridge
        from fabprint.credentials import cloud_token_json

        token_ctx = cloud_token_json()
        token_file = token_ctx.__enter__()
        bridge_ctx = PersistentBridge(token_file)
        bridge_ctx.__enter__()

    try:
        while True:
            t0 = time.monotonic()
            output_lines = []

            for name, creds in printers:
                ptype = creds.get("type", "unknown")
                output_lines.append(f"\033[1m{name}\033[0m  ({ptype})")
                try:
                    if ptype == "bambu-cloud" and bridge_ctx is not None:
                        status = bridge_ctx.status(creds["serial"])
                    else:
                        status = _query_printer_status(name, creds)
                    output_lines.extend(_render_printer(status, name, creds.get("serial", "")))
                except Exception as e:
                    output_lines.append(f"  \033[31merror: {e}\033[0m")
                output_lines.append("")

            elapsed = time.monotonic() - t0
            now = time.strftime("%H:%M:%S")
            header = f"fabprint watch  {now}  (polled in {elapsed:.1f}s, Ctrl-C to quit)"

            sys.stdout.write("\033[2J\033[H")
            sys.stdout.write(header + "\n\n" + "\n".join(output_lines))
            sys.stdout.flush()

            sleep_time = max(1, interval - elapsed)
            time.sleep(sleep_time)
    except KeyboardInterrupt:
        print("\n")
    finally:
        if bridge_ctx is not None:
            bridge_ctx.__exit__(None, None, None)
            token_ctx.__exit__(None, None, None)


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
