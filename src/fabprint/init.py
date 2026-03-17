"""fabprint init and validate commands."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fabprint.config import DEFAULT_STAGES, VALID_ORIENTS

# ---------------------------------------------------------------------------
# Template
# ---------------------------------------------------------------------------

_TEMPLATE = """\
# fabprint.toml — reproducible 3D print pipeline config
# Docs: https://github.com/pzfreo/fabprint/blob/main/docs/config.md

[pipeline]
# Stages to run: load, arrange, plate, slice, print
stages = ["load", "arrange", "plate", "slice"]

[plate]
size = [256, 256]       # bed size in mm [x, y]
padding = 5.0           # gap between parts in mm

[slicer]
engine = "orca"
# version = "2.3.1"                        # pin OrcaSlicer version for reproducibility
printer = "Bambu Lab P1S 0.4 nozzle"       # machine profile name
process = "0.20mm Standard @BBL X1C"       # process/quality profile
filaments = ["Generic PLA @base"]          # filament profiles (one per AMS slot)

# Per-slot filament mapping (alternative to filaments list):
# [slicer.slots]
# 1 = "Generic PLA @base"
# 2 = "Generic PETG @base"

# Slicer setting overrides:
# [slicer.overrides]
# sparse_infill_density = "25%"
# wall_loops = 3

[[parts]]
file = "my-part.stl"          # path relative to this file
copies = 1                     # number of copies
orient = "flat"                # flat, upright, or side
# filament = "Generic PLA @base"  # filament name or slot number (default: 1)
# scale = 1.0                     # uniform scale factor
# rotate = [0, 0, 45]            # [rx, ry, rz] in degrees (overrides orient)
# sequence = 1                    # print order for sequential printing

# Add more parts:
# [[parts]]
# file = "another-part.step"
# copies = 2
# orient = "upright"

# Printer connection (optional — requires credentials.toml):
# [printer]
# name = "my-printer"           # references [printers.my-printer] in credentials.toml
#                                # Run 'fabprint setup' to configure printers
"""


def dump_template() -> str:
    """Return the commented TOML template string."""
    return _TEMPLATE


# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------


def validate_config(path: Path) -> list[str]:
    """Validate a fabprint.toml and return a list of warnings.

    Raises FabprintError for hard errors (via load_config).
    Returns a list of actionable warning strings for soft issues.
    """
    from fabprint.config import load_config
    from fabprint.pipeline import STAGE_OUTPUTS
    from fabprint.profiles import discover_profiles

    cfg = load_config(path)
    warnings: list[str] = []

    # Check slicer version pinning
    if not cfg.slicer.version:
        warnings.append(
            'slicer.version is not set — pin it for reproducible builds (e.g. version = "2.3.1")'
        )

    # Check profile availability
    try:
        profiles = discover_profiles(cfg.slicer.engine)
    except ValueError:
        profiles = {}

    if profiles:
        if cfg.slicer.printer:
            machines = profiles.get("machine", {})
            if cfg.slicer.printer not in machines:
                close = _closest_match(cfg.slicer.printer, list(machines.keys()))
                hint = f" Did you mean '{close}'?" if close else ""
                warnings.append(
                    f"slicer.printer '{cfg.slicer.printer}' not found in "
                    f"installed {cfg.slicer.engine} profiles.{hint}"
                )

        if cfg.slicer.process:
            processes = profiles.get("process", {})
            if cfg.slicer.process not in processes:
                close = _closest_match(cfg.slicer.process, list(processes.keys()))
                hint = f" Did you mean '{close}'?" if close else ""
                warnings.append(
                    f"slicer.process '{cfg.slicer.process}' not found in "
                    f"installed {cfg.slicer.engine} profiles.{hint}"
                )

        filament_profiles = profiles.get("filament", {})
        for fil in cfg.slicer.filaments:
            if fil not in filament_profiles:
                close = _closest_match(fil, list(filament_profiles.keys()))
                hint = f" Did you mean '{close}'?" if close else ""
                warnings.append(
                    f"slicer filament '{fil}' not found in "
                    f"installed {cfg.slicer.engine} profiles.{hint}"
                )

    # Check printer credentials reference
    if cfg.printer:
        from fabprint.credentials import _credentials_path

        cred_path = _credentials_path()
        if not cred_path.exists():
            warnings.append(
                f"printer.name = '{cfg.printer.name}' but credentials file not found: {cred_path}. "
                "Run 'fabprint setup' to configure."
            )
        else:
            import tomllib

            with open(cred_path, "rb") as f:
                creds = tomllib.load(f)
            printers = creds.get("printers", {})
            if cfg.printer.name not in printers:
                available = list(printers.keys())
                warnings.append(
                    f"printer '{cfg.printer.name}' not found in {cred_path}. Available: {available}"
                )

    # Check for absolute part paths
    for i, part in enumerate(cfg.parts):
        if part.file.is_absolute():
            warnings.append(
                f"parts[{i}].file is an absolute path — consider making it relative for portability"
            )

    # Check pipeline stages
    for stage in cfg.pipeline.stages:
        if stage not in STAGE_OUTPUTS:
            warnings.append(f"pipeline stage '{stage}' is unknown")

    return warnings


def _closest_match(name: str, candidates: list[str]) -> str | None:
    """Return the closest matching string from candidates, or None."""
    if not candidates:
        return None
    name_lower = name.lower()
    # Try substring match first
    for c in candidates:
        if name_lower in c.lower() or c.lower() in name_lower:
            return c
    # Simple prefix match
    for c in candidates:
        if c.lower().startswith(name_lower[:8]):
            return c
    return None


# ---------------------------------------------------------------------------
# Interactive wizard
# ---------------------------------------------------------------------------


@dataclass
class _MachineInfo:
    """Information extracted from a machine profile."""

    plate_size: tuple[int, int] | None = None
    multi_material: bool = False


def _read_machine_info(profile_name: str, engine: str) -> _MachineInfo:
    """Extract build plate size and AMS/multi-material capability from a machine profile."""
    info = _MachineInfo()
    try:
        from fabprint.profiles import resolve_profile_data

        data = resolve_profile_data(profile_name, engine, "machine")

        # Plate size from printable_area polygon (e.g. ["0x0", "256x0", "256x256", "0x256"])
        area = data.get("printable_area")
        if area and isinstance(area, list):
            max_x = max_y = 0
            for pt in area:
                parts = str(pt).split("x")
                if len(parts) == 2:
                    max_x = max(max_x, int(float(parts[0])))
                    max_y = max(max_y, int(float(parts[1])))
            if max_x > 0 and max_y > 0:
                info.plate_size = (max_x, max_y)

        # AMS / multi-material support
        if data.get("single_extruder_multi_material"):
            info.multi_material = True
    except Exception:
        pass
    return info


def _list_configured_printers() -> dict[str, dict]:
    """Return configured printers from credentials.toml, or empty dict."""
    try:
        from fabprint.credentials import list_printers

        return list_printers() or {}
    except Exception:
        return {}


def _query_ams_trays(configured: dict[str, dict]) -> list[dict]:
    """Query a configured cloud printer for AMS tray info.

    Returns list of tray dicts with 'type', 'color', 'phys_slot' keys,
    or empty list if unavailable.
    """
    # Find the first bambu-cloud printer
    for name, creds in configured.items():
        if creds.get("type") != "bambu-cloud":
            continue
        serial = creds.get("serial")
        if not serial:
            continue
        try:
            from fabprint.cloud import cloud_status, parse_ams_trays
            from fabprint.credentials import cloud_token_json

            with cloud_token_json() as token_file:
                status = cloud_status(serial, token_file)
            return parse_ams_trays(status)
        except Exception:
            return []
    return []


def _match_filament_profile(tray_type: str, profile_names: list[str]) -> str | None:
    """Best-effort match an AMS tray type (e.g. 'PLA') to a slicer profile name.

    Looks for 'Generic <type>' first, then any profile containing the type string.
    """
    tray_upper = tray_type.upper()
    # Prefer "Generic PLA @base" style
    for name in profile_names:
        if name.upper().startswith(f"GENERIC {tray_upper}") and "@base" in name.lower():
            return name
    for name in profile_names:
        if name.upper().startswith(f"GENERIC {tray_upper}"):
            return name
    # Fallback: any profile containing the type
    for name in profile_names:
        if tray_upper in name.upper():
            return name
    return None


def _detect_orca_version() -> str | None:
    """Try to detect the installed OrcaSlicer version."""
    try:
        from fabprint.slicer import SLICER_PATHS, _detect_slicer_version

        slicer = SLICER_PATHS.get("orca")
        if slicer and slicer.exists():
            return _detect_slicer_version(slicer)
    except Exception:
        pass
    return None


_FILTER_THRESHOLD = 10  # show search prompt when list exceeds this size


def _prompt_choice(prompt: str, options: list[str], allow_multi: bool = False) -> list[int]:
    """Show a numbered list and return selected indices.

    For long lists (>20 items), prompts user to type a search term first,
    then shows only matching entries.
    """
    filtered = options
    filter_indices = list(range(len(options)))

    if len(options) > _FILTER_THRESHOLD:
        filtered, filter_indices = _search_filter(options)

    for i, opt in enumerate(filtered, 1):
        print(f"  [{i}] {opt}")

    while True:
        raw = input(prompt).strip()
        if not raw:
            continue
        # Allow re-searching from the selection prompt
        if not raw[0].isdigit() and raw.lower() != "all":
            filtered, filter_indices = _search_filter(options, raw)
            for i, opt in enumerate(filtered, 1):
                print(f"  [{i}] {opt}")
            continue
        if allow_multi and raw.lower() == "all":
            return list(filter_indices)
        try:
            if allow_multi:
                picks = [int(x.strip()) - 1 for x in raw.split(",")]
            else:
                picks = [int(raw) - 1]
            if all(0 <= p < len(filtered) for p in picks):
                return [filter_indices[p] for p in picks]
        except ValueError:
            pass
        print(
            f"  Enter a number 1-{len(filtered)}"
            + (" (comma-separated, 'all', or type to search)" if allow_multi else "")
            + (" or type to search" if not allow_multi and len(options) > _FILTER_THRESHOLD else "")
        )


def _search_filter(options: list[str], query: str | None = None) -> tuple[list[str], list[int]]:
    """Prompt for a search term and return matching options with original indices."""
    while True:
        if query is None:
            # Show first few examples so the user can see the naming format
            preview = options[:10]
            for ex in preview:
                print(f"    {ex}")
            if len(options) > len(preview):
                print(f"    ... and {len(options) - len(preview)} more")
            query = input(f"  Search ({len(options)} available, type to filter): ").strip()
        if not query:
            query = None
            continue
        q = query.lower()
        matches = [(i, o) for i, o in enumerate(options) if q in o.lower()]
        if matches:
            print(f"  {len(matches)} match(es) for '{query}':")
            indices = [i for i, _ in matches]
            names = [o for _, o in matches]
            return names, indices
        print(f"  No matches for '{query}'. Try again.")
        query = None


def _prompt_str(prompt: str, default: str | None = None) -> str:
    """Prompt for a string value with optional default."""
    suffix = f" [{default}]" if default else ""
    raw = input(f"{prompt}{suffix}: ").strip()
    return raw or (default or "")


def _prompt_int(prompt: str, default: int) -> int:
    """Prompt for an integer with a default."""
    raw = input(f"{prompt} [{default}]: ").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        print(f"  Using default: {default}")
        return default


def _prompt_yn(prompt: str, default: bool = True) -> bool:
    """Prompt yes/no with a default."""
    hint = "Y/n" if default else "y/N"
    raw = input(f"{prompt} [{hint}]: ").strip().lower()
    if not raw:
        return default
    return raw.startswith("y")


def run_wizard(output: Path | None = None) -> str:
    """Run the interactive init wizard and return generated TOML."""
    from fabprint.profiles import discover_profiles

    print("fabprint init — interactive config wizard\n")

    engine = "orca"

    # --- Step 0: Check for configured printers ---
    configured = _list_configured_printers()
    if not configured:
        print("No printers configured yet.")
        if _prompt_yn("Run 'fabprint setup' to add a printer first?"):
            from fabprint.credentials import setup_printer

            print()
            setup_printer()
            print()
            # Refresh after setup
            configured = _list_configured_printers()
        else:
            print("  Continuing without printer setup.\n")

    # --- Query AMS trays from configured printer ---
    ams_trays: list[dict] = []
    if configured:
        print("Checking printer AMS status...")
        ams_trays = _query_ams_trays(configured)
        if ams_trays:
            print(f"  Found {len(ams_trays)} loaded AMS slot(s):")
            for t in ams_trays:
                c = t["color"]
                r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
                swatch = f"\033[48;2;{r};{g};{b}m  \033[0m"
                print(f"    Slot {t['phys_slot'] + 1}: {t['type']}  {swatch}")
        else:
            print("  Could not read AMS trays (printer may be offline).")
        print()

    # --- Step 1: Discover profiles ---
    try:
        profiles = discover_profiles(engine)
    except ValueError:
        profiles = {"machine": {}, "process": {}, "filament": {}}

    # --- Step 3: Pick printer profile ---
    printer_profile = None
    machine_info = _MachineInfo()
    machines = sorted(profiles.get("machine", {}).keys())
    if machines:
        print("Printer profiles:")
        chosen = _prompt_choice("Pick a printer profile: ", machines)
        printer_profile = machines[chosen[0]]
        machine_info = _read_machine_info(printer_profile, engine)
        print()
    else:
        printer_profile = _prompt_str("Printer profile name (e.g. 'Bambu Lab P1S 0.4 nozzle')")
        print()

    # --- Step 4: Pick process profile ---
    process_profile = None
    processes = sorted(profiles.get("process", {}).keys())
    if processes:
        print("Process profiles:")
        chosen = _prompt_choice("Pick a process profile: ", processes)
        process_profile = processes[chosen[0]]
        print()
    else:
        process_profile = _prompt_str("Process profile name (e.g. '0.20mm Standard @BBL X1C')")
        print()

    # --- Step 5: Pick filament(s) ---
    filament_names: list[str] = []
    filament_options = sorted(profiles.get("filament", {}).keys())

    # Try to pre-populate from AMS trays
    ams_suggestions: list[str | None] = []
    if ams_trays and filament_options:
        ams_suggestions = [_match_filament_profile(t["type"], filament_options) for t in ams_trays]

    if ams_suggestions and any(ams_suggestions):
        # Show what we matched from AMS and let user confirm/edit
        print("Filament profiles (matched from AMS):")
        for i, (tray, suggestion) in enumerate(zip(ams_trays, ams_suggestions)):
            label = suggestion or "? (no match)"
            print(f"  Slot {tray['phys_slot'] + 1}: {tray['type']} → {label}")
        if _prompt_yn("Use these filaments?"):
            filament_names = [s for s in ams_suggestions if s]
        print()

    if not filament_names and filament_options:
        if machine_info.multi_material:
            print("Printer supports multi-material (AMS). Pick a filament for each slot.")
            slot = 1
            while True:
                print(f"Slot {slot} filament:")
                chosen = _prompt_choice(f"  Pick filament for slot {slot}: ", filament_options)
                filament_names.append(filament_options[chosen[0]])
                print(f"  Slot {slot}: {filament_names[-1]}")
                if slot >= 4:
                    break
                if not _prompt_yn(f"Add slot {slot + 1}?", default=slot < 2):
                    break
                slot += 1
        else:
            print("Filament profile:")
            chosen = _prompt_choice("Pick a filament: ", filament_options)
            filament_names.append(filament_options[chosen[0]])
        print()

    if not filament_names:
        fil = _prompt_str("Filament profile name", "Generic PLA @base")
        filament_names = [fil]
        print()

    # --- Step 6: Discover CAD files ---
    cwd = Path.cwd()
    candidates = sorted(
        p for ext in ("*.stl", "*.3mf", "*.step", "*.STL", "*.3MF", "*.STEP") for p in cwd.glob(ext)
    )
    parts_config: list[dict] = []
    if candidates:
        print(f"Found {len(candidates)} CAD file(s) in current directory:")
        names = [p.name for p in candidates]
        chosen = _prompt_choice(
            "Select files to include (comma-separated or 'all'): ",
            names,
            allow_multi=True,
        )
        print()
        for idx in chosen:
            f = candidates[idx]
            copies = _prompt_int(f"  {f.name} — copies?", 1)
            print("  Orient options: flat, upright, side")
            orient = _prompt_str(f"  {f.name} — orient?", "flat")
            if orient not in VALID_ORIENTS:
                orient = "flat"
            fil_slot = 1
            if len(filament_names) > 1:
                fil_slot = _prompt_int(f"  {f.name} — filament slot (1-{len(filament_names)})?", 1)
            parts_config.append(
                {
                    "file": f.name,
                    "copies": copies,
                    "orient": orient,
                    "filament": fil_slot,
                }
            )
        print()

    if not parts_config:
        # No CAD files found or selected — add a placeholder
        file_name = _prompt_str("Part file path (relative to this directory)", "my-part.stl")
        parts_config.append({"file": file_name, "copies": 1, "orient": "flat", "filament": 1})
        print()

    # --- Step 7: Plate size (default from printer profile if available) ---
    default_plate = (256, 256)
    if machine_info.plate_size:
        default_plate = machine_info.plate_size
        print(f"Detected plate size from printer profile: {default_plate[0]}x{default_plate[1]}mm")
    plate_x = _prompt_int("Plate width (mm)?", default_plate[0])
    plate_y = _prompt_int("Plate depth (mm)?", default_plate[1])
    print()

    # --- Step 8: Slicer version ---
    detected_version = _detect_orca_version()
    if detected_version:
        slicer_version = _prompt_str(
            "OrcaSlicer version to pin (leave blank to skip)", detected_version
        )
    else:
        slicer_version = _prompt_str("OrcaSlicer version to pin (leave blank to skip)")
    print()

    # --- Step 9: Pipeline stages ---
    stages = list(DEFAULT_STAGES)
    if not _prompt_yn("Include print stage in pipeline?"):
        stages = [s for s in stages if s != "print"]
    print()

    # --- Step 10: Printer connection ---
    printer_name = None
    if "print" in stages:
        configured = _list_configured_printers()
        if configured:
            names = list(configured.keys())
            print("Configured printers:")
            for i, (n, creds) in enumerate(configured.items(), 1):
                ptype = creds.get("type", "unknown")
                print(f"  [{i}] {n} ({ptype})")
            print(f"  [{len(names) + 1}] Skip")
            chosen = _prompt_choice("Pick a printer: ", [*names, "Skip (configure later)"])
            pick = names[chosen[0]] if chosen[0] < len(names) else None
            printer_name = pick
        elif _prompt_yn("Configure printer connection?", default=False):
            printer_name = _prompt_str("  Printer name (from 'fabprint setup')", "")
            if not printer_name:
                printer_name = None
        print()

    # --- Build TOML ---
    toml = _build_toml(
        engine=engine,
        printer_profile=printer_profile,
        process_profile=process_profile,
        filament_names=filament_names,
        parts=parts_config,
        plate_size=(plate_x, plate_y),
        slicer_version=slicer_version or None,
        stages=stages,
        printer_name=printer_name,
    )

    # --- Preview and confirm ---
    print("--- Generated fabprint.toml ---")
    print(toml)
    print("-------------------------------")

    dest = output or Path("fabprint.toml")
    if dest.exists():
        if not _prompt_yn(f"{dest} already exists. Overwrite?", default=False):
            print("Aborted.")
            return toml

    if _prompt_yn(f"Write to {dest}?"):
        dest.write_text(toml)
        print(f"Wrote {dest}")
    else:
        print("Not written. Copy the output above to create your config.")

    return toml


def _build_toml(
    *,
    engine: str,
    printer_profile: str | None,
    process_profile: str | None,
    filament_names: list[str],
    parts: list[dict],
    plate_size: tuple[int, int],
    slicer_version: str | None,
    stages: list[str],
    printer_name: str | None,
) -> str:
    """Build a TOML string from wizard answers."""
    lines: list[str] = []

    # Pipeline
    stage_list = ", ".join(f'"{s}"' for s in stages)
    lines.append("[pipeline]")
    lines.append(f"stages = [{stage_list}]")
    lines.append("")

    # Plate
    lines.append("[plate]")
    lines.append(f"size = [{plate_size[0]}, {plate_size[1]}]")
    lines.append("padding = 5.0")
    lines.append("")

    # Slicer
    lines.append("[slicer]")
    lines.append(f'engine = "{engine}"')
    if slicer_version:
        lines.append(f'version = "{slicer_version}"')
    if printer_profile:
        lines.append(f'printer = "{printer_profile}"')
    if process_profile:
        lines.append(f'process = "{process_profile}"')
    if filament_names:
        fil_list = ", ".join(f'"{f}"' for f in filament_names)
        lines.append(f"filaments = [{fil_list}]")
    lines.append("")

    # Parts
    for p in parts:
        lines.append("[[parts]]")
        lines.append(f'file = "{p["file"]}"')
        if p.get("copies", 1) != 1:
            lines.append(f"copies = {p['copies']}")
        if p.get("orient", "flat") != "flat":
            lines.append(f'orient = "{p["orient"]}"')
        if p.get("filament", 1) != 1:
            lines.append(f"filament = {p['filament']}")
        lines.append("")

    # Printer
    if printer_name:
        lines.append("[printer]")
        lines.append(f'name = "{printer_name}"')
        lines.append("")

    return "\n".join(lines)
