"""fabprint init and validate commands."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Union

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
    from fabprint.profiles import discover_profile_names

    cfg = load_config(path)
    warnings: list[str] = []

    # Check slicer version pinning
    if not cfg.slicer.version:
        warnings.append(
            'slicer.version is not set — pin it for reproducible builds (e.g. version = "2.3.1")'
        )

    # Check profile names — system → pinned → bundled
    profiles, source = discover_profile_names(
        cfg.slicer.engine,
        version=cfg.slicer.version,
        project_dir=cfg.base_dir,
    )

    if source != "none":
        if cfg.slicer.printer:
            machines = profiles.get("machine", [])
            if machines and cfg.slicer.printer not in machines:
                close = _closest_match(cfg.slicer.printer, machines)
                hint = f" Did you mean '{close}'?" if close else ""
                warnings.append(
                    f"slicer.printer '{cfg.slicer.printer}' not found in "
                    f"{cfg.slicer.engine} profiles ({source}).{hint}"
                )

        if cfg.slicer.process:
            processes = profiles.get("process", [])
            if processes and cfg.slicer.process not in processes:
                close = _closest_match(cfg.slicer.process, processes)
                hint = f" Did you mean '{close}'?" if close else ""
                warnings.append(
                    f"slicer.process '{cfg.slicer.process}' not found in "
                    f"{cfg.slicer.engine} profiles ({source}).{hint}"
                )

        filaments = profiles.get("filament", [])
        if filaments:
            for fil in cfg.slicer.filaments:
                if fil not in filaments:
                    close = _closest_match(fil, filaments)
                    hint = f" Did you mean '{close}'?" if close else ""
                    warnings.append(
                        f"slicer filament '{fil}' not found in "
                        f"{cfg.slicer.engine} profiles ({source}).{hint}"
                    )
    else:
        warnings.append(
            f"slicer profile names could not be validated — {cfg.slicer.engine} is not "
            "installed locally, no pinned profiles found, and no bundled profile list available. "
            "Run 'fabprint profiles pin' or "
            f"'python scripts/extract_profiles.py {cfg.slicer.version or '<version>'}' to add one."
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

    # Check for absolute part paths (check raw TOML value, not resolved path)
    import tomllib

    with open(path, "rb") as f:
        raw = tomllib.load(f)
    for i, p in enumerate(raw.get("parts", [])):
        raw_file = p.get("file", "")
        if raw_file and Path(raw_file).is_absolute():
            warnings.append(
                f"parts[{i}].file is an absolute path — consider making it relative for portability"
            )

    # Check part files: readability, extension, duplicates
    # (existence and orient are already hard errors in load_config)
    _SUPPORTED_EXTENSIONS = {".stl", ".3mf", ".step", ".stp", ".obj"}
    seen_files: set[str] = set()
    for i, part in enumerate(cfg.parts):
        part_path = part.file

        # Check readability (file exists at this point — load_config validated that)
        if not os.access(part_path, os.R_OK):
            warnings.append(f"parts[{i}].file '{part_path}' is not readable")

        # Check file extension
        ext = part_path.suffix.lower()
        if ext and ext not in _SUPPORTED_EXTENSIONS:
            warnings.append(
                f"parts[{i}].file has unsupported extension '{ext}' "
                f"— expected one of {sorted(_SUPPORTED_EXTENSIONS)}"
            )

        # Check for duplicate files
        canon = str(part_path)
        if canon in seen_files:
            warnings.append(f"parts[{i}].file '{part_path.name}' appears more than once")
        seen_files.add(canon)

    # Check plate dimensions
    width, depth = cfg.plate.size
    if width < 50 or depth < 50:
        warnings.append(
            f"plate.size [{width}, {depth}] seems very small — most beds are at least 100mm"
        )
    if width > 1000 or depth > 1000:
        warnings.append(f"plate.size [{width}, {depth}] seems very large — check units are in mm")

    # Check pipeline stages
    for stage in cfg.pipeline.stages:
        if stage not in STAGE_OUTPUTS:
            warnings.append(f"pipeline stage '{stage}' is unknown")

    # Check pipeline stage ordering
    stage_order = list(STAGE_OUTPUTS.keys())
    prev_idx = -1
    for stage in cfg.pipeline.stages:
        if stage in stage_order:
            idx = stage_order.index(stage)
            if idx < prev_idx:
                warnings.append(
                    f"pipeline stage '{stage}' is out of order — expected after "
                    f"'{stage_order[prev_idx]}'"
                )
            prev_idx = idx

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


def _fetch_available_versions() -> list[str]:
    """Fetch OrcaSlicer versions available as Docker images on DockerHub."""
    import requests

    from fabprint.slicer import DOCKERHUB_REPO

    try:
        url = f"https://hub.docker.com/v2/repositories/{DOCKERHUB_REPO}/tags"
        resp = requests.get(url, params={"page_size": 100}, timeout=5)
        resp.raise_for_status()
        tags = [t["name"] for t in resp.json().get("results", [])]
        # Extract versions from orca-X.Y.Z tags
        versions = []
        for tag in tags:
            if tag.startswith("orca-"):
                versions.append(tag[5:])  # strip "orca-" prefix
        return sorted(versions, reverse=True)
    except Exception:
        return []


def _prompt_slicer_version() -> str | None:
    """Prompt for OrcaSlicer version, offering available Docker image versions."""
    from fabprint import ui

    detected = _detect_orca_version()
    available = _fetch_available_versions()

    if available:
        options = list(available) + ["Skip (don't pin version)"]
        # Pre-select detected version if it's in the list
        default_idx = 1
        if detected and detected in available:
            default_idx = available.index(detected) + 1

        ui.choice_table(
            [(v,) for v in options],
            ["Available versions"],
        )
        pick = _prompt_int("Pick version", default_idx)
        idx = pick - 1
        if 0 <= idx < len(available):
            version = available[idx]
            ui.success(f"OrcaSlicer {version}")
            return version
        return None

    # Fallback: no Docker images found, prompt manually
    if detected:
        version = _prompt_str("OrcaSlicer version to pin (leave blank to skip)", detected)
    else:
        version = _prompt_str("OrcaSlicer version to pin (leave blank to skip)")
    return version or None


def _prompt_choice(prompt: str, options: list[str], allow_multi: bool = False) -> list[int]:
    """Interactive picker — delegates to ``ui.pick``."""
    from fabprint import ui

    return ui.pick(options, prompt=prompt, allow_multi=allow_multi)


def _prompt_str(prompt: str, default: str | None = None) -> str:
    """Prompt for a string value with optional default."""
    from fabprint import ui

    return ui.prompt_str(prompt, default)


def _prompt_int(prompt: str, default: int) -> int:
    """Prompt for an integer with a default."""
    from fabprint import ui

    return ui.prompt_int(prompt, default)


def _prompt_yn(prompt: str, default: bool = True) -> bool:
    """Prompt yes/no with a default."""
    from fabprint import ui

    return ui.prompt_yn(prompt, default)


# ---------------------------------------------------------------------------
# Common slicer overrides
# ---------------------------------------------------------------------------

# Each entry: (display_name, slicer_key, value_spec)
# value_spec is either ("text", "hint string") or ("choice", [...options])
OverrideSpec = tuple[str, str, Union[tuple[str, str], tuple[str, list[str]]]]

COMMON_OVERRIDES: list[OverrideSpec] = [
    ("Infill density", "sparse_infill_density", ("text", "e.g. 15%, 25%, 50%")),
    (
        "Infill pattern",
        "sparse_infill_pattern",
        (
            "choice",
            [
                "grid",
                "gyroid",
                "honeycomb",
                "line",
                "cubic",
                "triangles",
                "concentric",
                "lightning",
            ],
        ),
    ),
    ("Wall loops", "wall_loops", ("text", "e.g. 2, 3, 4")),
    ("Layer height", "layer_height", ("text", "e.g. 0.12, 0.16, 0.20, 0.28")),
    (
        "Enable support",
        "enable_support",
        ("choice", ["0 (off)", "1 (on)"]),
    ),
    (
        "Support type",
        "support_type",
        ("choice", ["normal", "tree", "hybrid"]),
    ),
    ("Top shell layers", "top_shell_layers", ("text", "e.g. 3, 5")),
    ("Bottom shell layers", "bottom_shell_layers", ("text", "e.g. 3, 5")),
    (
        "Brim type",
        "brim_type",
        ("choice", ["no_brim", "outer_only", "inner_only", "outer_and_inner"]),
    ),
    (
        "Seam position",
        "seam_position",
        ("choice", ["nearest", "aligned", "back", "random"]),
    ),
]


def _prompt_overrides() -> dict[str, str]:
    """Prompt user to add slicer overrides, returning key→value dict."""
    from fabprint import ui

    if not _prompt_yn("Add slicer overrides?", default=False):
        return {}

    overrides: dict[str, str] = {}
    while True:
        # Build display list: common overrides + custom option
        items = [(name, key) for name, key, _ in COMMON_OVERRIDES]
        items.append(("Custom key...", ""))
        ui.choice_table(items, ["Name", "Slicer key"])

        pick = _prompt_int("Pick override", 1)
        idx = pick - 1
        if idx < 0 or idx > len(COMMON_OVERRIDES):
            ui.warn(f"Enter 1-{len(COMMON_OVERRIDES) + 1}")
            continue

        if idx == len(COMMON_OVERRIDES):
            # Custom key
            key = _prompt_str("Slicer key name")
            if not key:
                continue
            value = _prompt_str(f"Value for {key}")
            if value:
                overrides[key] = value
                ui.success(f'{key} = "{value}"')
        else:
            name, key, spec = COMMON_OVERRIDES[idx]
            ui.success(name)

            if spec[0] == "choice":
                choices = spec[1]
                assert isinstance(choices, list)
                ui.choice_table([(c,) for c in choices], ["Option"])
                cpick = _prompt_int("Pick value", 1)
                cidx = cpick - 1
                if 0 <= cidx < len(choices):
                    raw = choices[cidx]
                    # Strip parenthetical hints like "0 (off)" → "0"
                    value = raw.split(" (")[0] if " (" in raw else raw
                    overrides[key] = value
                    ui.success(f'{key} = "{value}"')
                else:
                    ui.warn(f"Enter 1-{len(choices)}")
                    continue
            else:
                hint = spec[1]
                value = _prompt_str(f"Value for {key} ({hint})")
                if value:
                    overrides[key] = value
                    ui.success(f'{key} = "{value}"')

        ui.console.print()
        if not _prompt_yn("Add another override?", default=False):
            break

    return overrides


def run_wizard(output: Path | None = None) -> str:
    """Run the interactive init wizard and return generated TOML."""
    from fabprint import ui
    from fabprint.profiles import discover_profile_names

    ui.heading("fabprint init")
    ui.console.print()

    engine = "orca"

    # --- Step 0: Check for configured printers ---
    configured = _list_configured_printers()
    if not configured:
        ui.warn("No printers configured yet.")
        if _prompt_yn("Run 'fabprint setup' to add a printer first?"):
            from fabprint.credentials import setup_printer

            ui.console.print()
            setup_printer()
            ui.console.print()
            # Refresh after setup
            configured = _list_configured_printers()
        else:
            ui.info("Continuing without printer setup.")
            ui.console.print()

    # --- Query AMS trays in background while we ask other questions ---
    ams_future = None
    if configured:
        from concurrent.futures import ThreadPoolExecutor

        _ams_pool = ThreadPoolExecutor(max_workers=1)
        ams_future = _ams_pool.submit(_query_ams_trays, configured)

    # --- Step 1: Discover profiles (system → pinned → bundled) ---
    profiles, profile_source = discover_profile_names(engine)
    if profile_source == "bundled":
        ui.info("Using bundled profile list — install OrcaSlicer locally for full access")
        ui.console.print()
    elif profile_source == "none":
        ui.warn("No profiles found — profile names will need to be entered manually")
        ui.console.print()

    # --- Step 3: Pick printer profile ---
    printer_profile = None
    machine_info = _MachineInfo()
    machines = sorted(profiles.get("machine", []))
    if machines:
        ui.heading("Printer Profile")
        chosen = _prompt_choice("Pick a printer profile", machines)
        printer_profile = machines[chosen[0]]
        machine_info = _read_machine_info(printer_profile, engine)
        ui.console.print()
    else:
        printer_profile = _prompt_str("Printer profile name (e.g. 'Bambu Lab P1S 0.4 nozzle')")
        ui.console.print()

    # --- Step 4: Pick process profile ---
    process_profile = None
    processes = sorted(profiles.get("process", []))
    if processes:
        ui.heading("Process Profile")
        chosen = _prompt_choice("Pick a process profile", processes)
        process_profile = processes[chosen[0]]
        ui.console.print()
    else:
        process_profile = _prompt_str("Process profile name (e.g. '0.20mm Standard @BBL X1C')")
        ui.console.print()

    # --- Step 4b: Slicer overrides ---
    ui.heading("Slicer Overrides")
    overrides = _prompt_overrides()
    ui.console.print()

    # --- Collect AMS results (should be done by now) ---
    ams_trays: list[dict] = []
    if ams_future is not None:
        try:
            ams_trays = ams_future.result(timeout=10)
        except Exception:
            pass
        if ams_trays:
            ui.info(f"AMS detected ({len(ams_trays)} slot(s))")

    # --- Step 5: Pick filament(s) ---
    filament_names: list[str] = []
    filament_options = sorted(profiles.get("filament", []))

    # Try to pre-populate from AMS trays
    ams_suggestions: list[str | None] = []
    if ams_trays and filament_options:
        ams_suggestions = [_match_filament_profile(t["type"], filament_options) for t in ams_trays]

    if ams_suggestions and any(ams_suggestions):
        # Show what we matched from AMS and let user confirm/edit
        ui.heading("Filament Profiles (matched from AMS)")
        for i, (tray, suggestion) in enumerate(zip(ams_trays, ams_suggestions)):
            label = suggestion or "[dim]? (no match)[/dim]"
            swatch = ui.color_swatch(tray["color"])
            slot_num = tray["phys_slot"] + 1
            ui.console.print(f"  Slot {slot_num}: {tray['type']} {swatch} \u2192 {label}")
        if _prompt_yn("Use these filaments?"):
            filament_names = [s for s in ams_suggestions if s]
        ui.console.print()

    if not filament_names and filament_options:
        ui.heading("Filament Profile")
        if machine_info.multi_material:
            ui.info("Printer supports multi-material (AMS). Pick a filament for each slot.")
            slot = 1
            while True:
                chosen = _prompt_choice(f"Pick filament for slot {slot}", filament_options)
                filament_names.append(filament_options[chosen[0]])
                ui.success(f"Slot {slot}: {filament_names[-1]}")
                if slot >= 4:
                    break
                if not _prompt_yn(f"Add slot {slot + 1}?", default=slot < 2):
                    break
                slot += 1
        else:
            chosen = _prompt_choice("Pick a filament", filament_options)
            filament_names.append(filament_options[chosen[0]])
        ui.console.print()

    if not filament_names:
        fil = _prompt_str("Filament profile name", "Generic PLA @base")
        filament_names = [fil]
        ui.console.print()

    # --- Step 6: Discover CAD files ---
    ui.heading("CAD Files")
    cwd = Path.cwd()
    candidates = sorted(
        p for ext in ("*.stl", "*.3mf", "*.step", "*.STL", "*.3MF", "*.STEP") for p in cwd.glob(ext)
    )
    parts_config: list[dict] = []
    if candidates:
        ui.info(f"Found {len(candidates)} CAD file(s) in current directory")
        names = [p.name for p in candidates]
        chosen = _prompt_choice(
            "Select files (comma-separated or 'all')",
            names,
            allow_multi=True,
        )
        ui.console.print()
        for idx in chosen:
            f = candidates[idx]
            copies = _prompt_int(f"{f.name} — copies?", 1)
            ui.info("Orient options: flat, upright, side")
            orient = _prompt_str(f"{f.name} — orient?", "flat")
            if orient not in VALID_ORIENTS:
                orient = "flat"
            fil_slot = 1
            if len(filament_names) > 1:
                fil_slot = _prompt_int(f"{f.name} — filament slot (1-{len(filament_names)})?", 1)
            parts_config.append(
                {
                    "file": f.name,
                    "copies": copies,
                    "orient": orient,
                    "filament": fil_slot,
                }
            )
        ui.console.print()

    if not parts_config:
        # No CAD files found or selected — add a placeholder
        file_name = _prompt_str("Part file path (relative to this directory)", "my-part.stl")
        parts_config.append({"file": file_name, "copies": 1, "orient": "flat", "filament": 1})
        ui.console.print()

    # --- Step 7: Plate size (default from printer profile if available) ---
    ui.heading("Build Plate")
    default_plate = (256, 256)
    if machine_info.plate_size:
        default_plate = machine_info.plate_size
        w, d = default_plate
        ui.success(f"Detected plate size from printer profile: {w}x{d}mm")
    plate_x = _prompt_int("Plate width (mm)?", default_plate[0])
    plate_y = _prompt_int("Plate depth (mm)?", default_plate[1])
    ui.console.print()

    # --- Step 8: Slicer version ---
    ui.heading("Slicer Version")
    slicer_version = _prompt_slicer_version()
    ui.console.print()

    # --- Step 9: Pipeline stages (always include print) ---
    stages = list(DEFAULT_STAGES)

    # --- Step 10: Printer connection ---
    printer_name = None
    if "print" in stages:
        ui.heading("Printer Connection")
        configured = _list_configured_printers()
        if configured:
            names = list(configured.keys())
            chosen = _prompt_choice("Pick a printer", [*names, "Skip (configure later)"])
            pick = names[chosen[0]] if chosen[0] < len(names) else None
            printer_name = pick
        elif _prompt_yn("Configure printer connection?", default=False):
            printer_name = _prompt_str("Printer name (from 'fabprint setup')", "")
            if not printer_name:
                printer_name = None
        ui.console.print()

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
        overrides=overrides,
    )

    # --- Preview and confirm ---
    ui.heading("Preview")
    ui.preview_toml(toml)

    dest = output or Path("fabprint.toml")
    if dest.exists():
        if not _prompt_yn(f"{dest} already exists. Overwrite?", default=False):
            ui.warn("Aborted.")
            return toml

    if _prompt_yn(f"Write to {dest}?"):
        dest.write_text(toml)
        ui.success(f"Wrote {dest}")
    else:
        ui.info("Not written. Copy the output above to create your config.")

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
    overrides: dict[str, str] | None = None,
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

    # Slicer overrides
    if overrides:
        lines.append("[slicer.overrides]")
        for key, value in overrides.items():
            # Try to emit numeric values without quotes
            try:
                float(value)
                lines.append(f"{key} = {value}")
            except ValueError:
                lines.append(f'{key} = "{value}"')
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
