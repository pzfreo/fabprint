"""CLI entry point for fabprint."""

import logging
import sys
from pathlib import Path
from typing import Annotated, Optional

import click
import typer

from fabprint import FabprintError, __version__
from fabprint.config import load_config

log = logging.getLogger(__name__)

app = typer.Typer(
    name="fabprint",
    help="Immutable 3D print pipeline: arrange, slice, and print.",
    no_args_is_help=True,
)

profiles_app = typer.Typer(help="List or pin slicer profiles.")
app.add_typer(profiles_app, name="profiles")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _version_callback(value: bool) -> None:
    if value:
        print(f"fabprint {__version__}")
        raise typer.Exit()


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )


def _resolve_config_path(config: Optional[Path]) -> Path:
    """Resolve config path, defaulting to ./fabprint.toml."""
    if config is not None:
        return config
    candidate = Path("fabprint.toml")
    if not candidate.exists():
        raise FabprintError(
            "No config file specified and no fabprint.toml found in the current directory.\n"
            "Usage: fabprint <command> [config.toml]"
        )
    return candidate


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


def _gather_inputs(
    *,
    config: Path,
    output_dir: Optional[Path],
    output_3mf: Path,
    scale: Optional[float],
    local: bool,
    docker_version: Optional[str],
    filament_type: Optional[str],
    filament_slot: int,
    dry_run: bool,
    upload_only: bool,
    experimental: bool,
    no_ams_mapping: bool,
) -> dict:
    """Build the full set of Hamilton driver inputs."""
    return {
        "config_path": config,
        "global_scale": scale,
        "output_3mf": output_3mf,
        "output_dir": output_dir or Path("output"),
        "slicer_local": local,
        "docker_version": docker_version,
        "filament_type_override": filament_type,
        "filament_slot_override": filament_slot,
        "dry_run": dry_run,
        "upload_only": upload_only,
        "experimental": experimental,
        "skip_ams_mapping": no_ams_mapping,
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
# Version callback (top-level)
# ---------------------------------------------------------------------------


@app.callback()
def _app_callback(
    version: Annotated[
        bool, typer.Option("--version", callback=_version_callback, is_eager=True)
    ] = False,
) -> None:
    pass


# ---------------------------------------------------------------------------
# run command
# ---------------------------------------------------------------------------


@app.command()
def run(
    config: Annotated[Optional[Path], typer.Argument(help="Path to config file")] = None,
    output_dir: Annotated[
        Optional[Path], typer.Option("-o", "--output-dir", help="Output directory")
    ] = None,
    until: Annotated[
        Optional[str], typer.Option(help="Run pipeline up to and including this stage")
    ] = None,
    only: Annotated[
        Optional[str],
        typer.Option(help="Run only this stage (fail if required artifacts don't exist)"),
    ] = None,
    scale: Annotated[
        Optional[float],
        typer.Option(help="Scale all parts by this factor (multiplies per-part scale)"),
    ] = None,
    local: Annotated[
        bool, typer.Option("--local", help="Force local slicer (fail if not installed)")
    ] = False,
    docker_version: Annotated[
        Optional[str],
        typer.Option(help="Use a specific OrcaSlicer Docker image version (e.g. 2.3.1)"),
    ] = None,
    filament_type: Annotated[
        Optional[str],
        typer.Option(help="Override filament profile name (e.g. 'Generic PLA @base')"),
    ] = None,
    filament_slot: Annotated[int, typer.Option(help="AMS slot for --filament-type")] = 1,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Do everything except send to printer")
    ] = False,
    upload_only: Annotated[
        bool, typer.Option("--upload-only", help="Upload gcode but don't start printing")
    ] = False,
    experimental: Annotated[
        bool, typer.Option("--experimental", help="Enable experimental printer modes")
    ] = False,
    no_ams_mapping: Annotated[
        bool,
        typer.Option(
            "--no-ams-mapping",
            help="Skip AMS mapping and use bridge default [0,1,2,3] (diagnostic)",
        ),
    ] = False,
    verbose: Annotated[bool, typer.Option("-v", "--verbose", help="Enable debug logging")] = False,
) -> None:
    """Run the pipeline defined in fabprint.toml."""
    _setup_logging(verbose)
    from fabprint.pipeline import resolve_outputs, resolve_overrides

    if until and only:
        raise ValueError("Cannot use both --until and --only")

    resolved_config = _resolve_config_path(config)
    cfg = load_config(resolved_config)
    stages = cfg.pipeline.stages

    out_dir = output_dir or Path("output")
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"{cfg.name}-" if cfg.name else ""
    output_3mf = out_dir / f"{prefix}plate.3mf"

    outputs = resolve_outputs(stages, until=until, only=only)

    overrides = {}
    if only:
        overrides = resolve_overrides(only, out_dir)

    dr = _build_driver(verbose=verbose)
    inputs = _gather_inputs(
        config=resolved_config,
        output_dir=output_dir,
        output_3mf=output_3mf,
        scale=scale,
        local=local,
        docker_version=docker_version,
        filament_type=filament_type,
        filament_slot=filament_slot,
        dry_run=dry_run,
        upload_only=upload_only,
        experimental=experimental,
        no_ams_mapping=no_ams_mapping,
    )

    result = dr.execute(outputs, inputs=inputs, overrides=overrides)
    _display_results(result)


# ---------------------------------------------------------------------------
# init / validate / setup commands
# ---------------------------------------------------------------------------


@app.command()
def init(
    template: Annotated[
        bool, typer.Option("--template", help="Dump a commented template to stdout (no wizard)")
    ] = False,
    output: Annotated[
        Optional[Path],
        typer.Option("-o", "--output", help="Output file path (default: ./fabprint.toml)"),
    ] = None,
    verbose: Annotated[bool, typer.Option("-v", "--verbose", help="Enable debug logging")] = False,
) -> None:
    """Create a new fabprint.toml config file."""
    _setup_logging(verbose)
    from fabprint.init import dump_template, run_wizard

    if template:
        print(dump_template(), end="")
    else:
        run_wizard(output=output)


@app.command()
def validate(
    config: Annotated[Optional[Path], typer.Argument(help="Path to config file")] = None,
    verbose: Annotated[bool, typer.Option("-v", "--verbose", help="Enable debug logging")] = False,
) -> None:
    """Check a fabprint.toml for issues."""
    _setup_logging(verbose)
    from fabprint.init import validate_config

    resolved_config = _resolve_config_path(config)
    warnings = validate_config(resolved_config)
    if warnings:
        for w in warnings:
            print(f"  warning: {w}")
        print(f"\n{len(warnings)} warning(s) found.")
    else:
        print("Config OK \u2014 no issues found.")


@app.command()
def setup(
    verbose: Annotated[bool, typer.Option("-v", "--verbose", help="Enable debug logging")] = False,
) -> None:
    """Set up a printer (credentials, cloud login, connection type)."""
    _setup_logging(verbose)
    from fabprint.credentials import setup_printer

    setup_printer()


# ---------------------------------------------------------------------------
# status / watch commands
# ---------------------------------------------------------------------------


def _resolve_status_printers(
    printer_name: Optional[str], serial: Optional[str], list_printers_fn, load_creds_fn
):
    """Build list of (name, creds) tuples for status/watch commands."""
    if printer_name:
        creds = load_creds_fn(printer_name)
        return [(printer_name, creds)]

    if serial:
        return [(serial, {"type": "bambu-cloud", "serial": serial})]

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


@app.command()
def status(
    printer: Annotated[
        Optional[str], typer.Option(help="Printer name from credentials.toml")
    ] = None,
    serial: Annotated[
        Optional[str], typer.Option(help="Bambu printer serial (cloud only, legacy)")
    ] = None,
    verbose: Annotated[bool, typer.Option("-v", "--verbose", help="Enable debug logging")] = False,
) -> None:
    """Query printer status (all configured or by name)."""
    _setup_logging(verbose)
    from fabprint.credentials import list_printers, load_printer_credentials

    printers = _resolve_status_printers(printer, serial, list_printers, load_printer_credentials)

    for name, creds in printers:
        ptype = creds.get("type", "unknown")
        print(f"\033[1m{name}\033[0m  ({ptype})")
        try:
            st = _query_printer_status(name, creds)
            for line in _render_printer(st, name, creds.get("serial", "")):
                print(line)
        except Exception as e:
            print(f"  \033[31merror: {e}\033[0m")
        print()


@app.command()
def watch(
    printer: Annotated[
        Optional[str], typer.Option(help="Printer name from credentials.toml")
    ] = None,
    interval: Annotated[int, typer.Option(help="Refresh interval in seconds")] = 10,
    verbose: Annotated[bool, typer.Option("-v", "--verbose", help="Enable debug logging")] = False,
) -> None:
    """Live dashboard for all configured printers."""
    _setup_logging(verbose)
    import time

    from fabprint.credentials import list_printers, load_printer_credentials

    printers = _resolve_status_printers(printer, None, list_printers, load_printer_credentials)
    print(f"Watching {len(printers)} printer(s): {', '.join(n for n, _ in printers)}")

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
                        st = bridge_ctx.status(creds["serial"])
                    else:
                        st = _query_printer_status(name, creds)
                    output_lines.extend(_render_printer(st, name, creds.get("serial", "")))
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


# ---------------------------------------------------------------------------
# profiles subcommands
# ---------------------------------------------------------------------------


@profiles_app.command("list")
def profiles_list(
    engine: Annotated[str, typer.Option(help="Slicer engine")] = "orca",
    category: Annotated[
        Optional[str], typer.Option(help="Filter by category (machine, process, filament)")
    ] = None,
    verbose: Annotated[bool, typer.Option("-v", "--verbose", help="Enable debug logging")] = False,
) -> None:
    """List available profiles."""
    _setup_logging(verbose)
    from fabprint.profiles import CATEGORIES, discover_profiles

    profiles = discover_profiles(engine)
    categories = [category] if category else list(CATEGORIES)
    for cat in categories:
        names = profiles.get(cat, {})
        print(f"\n{cat} ({len(names)} profiles):")
        for name in names:
            print(f"  {name}")


@profiles_app.command("pin")
def profiles_pin(
    config: Annotated[Optional[Path], typer.Argument(help="Path to config file")] = None,
    verbose: Annotated[bool, typer.Option("-v", "--verbose", help="Enable debug logging")] = False,
) -> None:
    """Pin profiles from config into local profiles/ dir."""
    _setup_logging(verbose)
    from fabprint.profiles import pin_profiles

    resolved_config = _resolve_config_path(config)
    cfg = load_config(resolved_config)
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


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    """Entry point for fabprint CLI."""
    try:
        app(argv, standalone_mode=False)
    except click.exceptions.NoArgsIsHelpError:
        sys.exit(1)
    except SystemExit as e:
        if e.code:
            sys.exit(e.code)
    except FabprintError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    except (ValueError, FileNotFoundError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
