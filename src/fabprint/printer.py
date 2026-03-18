"""Send gcode to a printer — dispatches by printer type from credentials.toml."""

from __future__ import annotations

import hashlib
import logging
import zipfile
from pathlib import Path

from fabprint import FabprintError
from fabprint.config import PrinterConfig
from fabprint.credentials import cloud_token_json, load_printer_credentials
from fabprint.gcode import parse_gcode_metadata

log = logging.getLogger(__name__)


def wrap_gcode_3mf(gcode_path: Path, output_path: Path | None = None) -> Path:
    """Wrap a gcode file into a .gcode.3mf for Bambu Connect.

    Creates a minimal but valid .gcode.3mf archive that Bambu Connect
    can import and send to a printer.
    """
    if output_path is None:
        output_path = gcode_path.parent / f"{gcode_path.stem}.gcode.3mf"

    gcode_bytes = gcode_path.read_bytes()
    md5 = hashlib.md5(gcode_bytes).hexdigest()
    stats = parse_gcode_metadata(gcode_path)

    prediction = int(stats.get("print_time_secs", 0))
    weight = f"{stats.get('filament_g', 0):.2f}"

    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
        ' <Default Extension="rels"'
        ' ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
        ' <Default Extension="model"'
        ' ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>\n'
        ' <Default Extension="gcode" ContentType="text/x.gcode"/>\n'
        "</Types>"
    )

    rels = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<Relationships xmlns="
        '"http://schemas.openxmlformats.org/package/2006/relationships">\n'
        ' <Relationship Target="/3D/3dmodel.model" Id="rel-1"'
        ' Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>\n'
        "</Relationships>"
    )

    model = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<model unit="millimeter" xml:lang="en-US"'
        ' xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"'
        ' xmlns:BambuStudio="http://schemas.bambulab.com/package/2021"'
        ' xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06"'
        ' requiredextensions="p">\n'
        ' <metadata name="Application">OrcaSlicer</metadata>\n'
        ' <metadata name="BambuStudio:3mfVersion">1</metadata>\n'
        " <resources/>\n"
        " <build/>\n"
        "</model>"
    )

    model_settings = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<config>\n"
        "  <plate>\n"
        '    <metadata key="plater_id" value="1"/>\n'
        '    <metadata key="plater_name" value=""/>\n'
        '    <metadata key="locked" value="false"/>\n'
        '    <metadata key="gcode_file" value="Metadata/plate_1.gcode"/>\n'
        "  </plate>\n"
        "</config>"
    )

    model_settings_rels = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<Relationships xmlns="
        '"http://schemas.openxmlformats.org/package/2006/relationships">\n'
        ' <Relationship Target="/Metadata/plate_1.gcode" Id="rel-1"'
        ' Type="http://schemas.bambulab.com/package/2021/gcode"/>\n'
        "</Relationships>"
    )

    slice_info = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<config>\n"
        "  <header>\n"
        '    <header_item key="X-BBL-Client-Type" value="slicer"/>\n'
        '    <header_item key="X-BBL-Client-Version" value="02.03.01.00"/>\n'
        "  </header>\n"
        "  <plate>\n"
        f'    <metadata key="index" value="1"/>\n'
        f'    <metadata key="prediction" value="{prediction}"/>\n'
        f'    <metadata key="weight" value="{weight}"/>\n'
        "  </plate>\n"
        "</config>"
    )

    cut_info = '<?xml version="1.0" encoding="utf-8"?>\n<objects/>'

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("3D/3dmodel.model", model)
        zf.writestr("Metadata/plate_1.gcode", gcode_bytes)
        zf.writestr("Metadata/plate_1.gcode.md5", md5)
        zf.writestr("Metadata/model_settings.config", model_settings)
        zf.writestr("Metadata/_rels/model_settings.config.rels", model_settings_rels)
        zf.writestr("Metadata/slice_info.config", slice_info)
        zf.writestr("Metadata/cut_information.xml", cut_info)

    return output_path


def _send_lan(
    gcode_path: Path,
    ip: str,
    access_code: str,
    serial: str,
    dry_run: bool = False,
    upload_only: bool = False,
) -> None:
    """Send gcode to printer via LAN using bambulabs-api."""
    try:
        from bambulabs_api import Printer
    except ImportError:
        raise ImportError(
            "bambulabs-api is required for LAN printing. Install with: pip install fabprint[lan]"
        ) from None

    print(f"Sending {gcode_path.name} to printer at {ip}")

    if dry_run:
        action = "upload" if upload_only else "upload and start print"
        print(f"  [dry-run] Would {action} {gcode_path.name}")
        return

    printer = Printer(ip_address=ip, access_code=access_code, serial=serial)
    try:
        printer.connect()
        log.info("Connected to printer at %s", ip)

        with open(gcode_path, "rb") as f:
            remote_path = printer.upload_file(f, filename=gcode_path.name)
        log.info("Uploaded to %s", remote_path)
        print(f"  Uploaded {gcode_path.name}")

        if upload_only:
            print("  File ready on printer — start from touchscreen when ready")
        else:
            printer.start_print(filename=remote_path, plate_number=1)
            print("  Print started")
    finally:
        printer.disconnect()
        log.info("Disconnected from printer")


def get_printer_status(serial: str) -> dict:
    """Query live printer status via the cloud bridge.

    Returns a dict with keys like gcode_state, mc_percent, layer_num, etc.
    Raises RuntimeError if the bridge fails.
    """
    from fabprint.cloud import cloud_status

    with cloud_token_json() as token_file:
        return cloud_status(serial, token_file)


def get_lan_status(ip: str, access_code: str, serial: str) -> dict:
    """Query printer status via LAN (MQTT).

    Returns a normalised status dict compatible with the cloud status format.
    """
    try:
        from bambulabs_api import Printer
    except ImportError:
        raise ImportError(
            "bambulabs-api is required for LAN status. Install with: pip install fabprint[lan]"
        ) from None

    import time

    printer = Printer(ip_address=ip, access_code=access_code, serial=serial)
    try:
        printer.connect()
        # Give MQTT a moment to receive the first status push
        time.sleep(3)
        status = printer.get_device_status() or {}  # type: ignore[attr-defined]
        return status
    finally:
        printer.disconnect()


def get_moonraker_status(url: str, api_key: str | None = None) -> dict:
    """Query printer status via Moonraker REST API (experimental).

    Returns a normalised status dict with keys matching the rendering format.
    Not yet tested against a real Moonraker instance.
    """
    try:
        import requests
    except ImportError:
        raise ImportError(
            "requests is required for Moonraker status. Install with: pip install requests"
        ) from None

    base = url.rstrip("/")
    headers = {}
    if api_key:
        headers["X-Api-Key"] = api_key

    # Query print stats and temperatures in one call
    resp = requests.get(
        f"{base}/printer/objects/query?print_stats&heater_bed&extruder&display_status",
        headers=headers,
        timeout=10,
    )
    resp.raise_for_status()
    result = resp.json().get("result", {}).get("status", {})

    ps = result.get("print_stats", {})
    extruder = result.get("extruder", {})
    bed = result.get("heater_bed", {})
    display = result.get("display_status", {})

    # Map Moonraker state to Bambu-style state
    state_map = {
        "standby": "IDLE",
        "printing": "RUNNING",
        "paused": "PAUSE",
        "complete": "FINISH",
        "cancelled": "IDLE",
        "error": "FAILED",
    }
    klipper_state = ps.get("state", "standby")

    return {
        "gcode_state": state_map.get(klipper_state, klipper_state.upper()),
        "subtask_name": ps.get("filename", ""),
        "mc_percent": int((display.get("progress", 0)) * 100),
        "nozzle_temper": extruder.get("temperature", 0),
        "nozzle_target_temper": extruder.get("target", 0),
        "bed_temper": bed.get("temperature", 0),
        "bed_target_temper": bed.get("target", 0),
        "layer_num": ps.get("info", {}).get("current_layer", 0),
        "total_layer_num": ps.get("info", {}).get("total_layer", 0),
    }


def _send_cloud_bridge(
    gcode_path: Path,
    serial: str,
    dry_run: bool = False,
    verbose: bool = False,
    skip_ams_mapping: bool = False,
) -> None:
    """Send gcode to printer via the bambu_cloud_bridge binary."""
    from fabprint.cloud import cloud_print

    # Check printer availability before sending, and capture AMS state for mapping
    ams_trays = None
    if not dry_run:
        try:
            from fabprint.cloud import parse_ams_trays

            status = get_printer_status(serial)
            gcode_state = status.get("gcode_state", "")
            if gcode_state not in ("IDLE", "FINISH", "FAILED", ""):
                raise RuntimeError(
                    f"Printer is not ready (state: {gcode_state}). "
                    "Wait for current job to finish or cancel it first."
                )
            if gcode_state:
                print(f"  Printer ready (state: {gcode_state})")
            ams_trays = parse_ams_trays(status)
            if ams_trays:
                log.debug(
                    "AMS trays: %s",
                    ", ".join(f"slot{t['phys_slot']}={t['type']}" for t in ams_trays),
                )
        except RuntimeError as e:
            if "not ready" in str(e):
                raise
            log.debug("Status check failed (printer may be offline): %s", e)

    # Use the slicer's .gcode.3mf if available, otherwise wrap the gcode
    sliced_3mfs = list(gcode_path.parent.glob("*_sliced.gcode.3mf"))
    if sliced_3mfs:
        threemf_path = sliced_3mfs[0]
    else:
        threemf_path = wrap_gcode_3mf(gcode_path)

    print(f"Sending {threemf_path.name} via cloud bridge")

    if dry_run:
        print(f"  [dry-run] Would upload {threemf_path.name} to printer {serial}")
        return

    with cloud_token_json() as token_file:
        result = cloud_print(
            threemf_path=threemf_path,
            device_id=serial,
            token_file=token_file,
            project_name=gcode_path.stem,
            ams_trays=ams_trays,
            verbose=verbose,
            skip_ams_mapping=skip_ams_mapping,
        )

    status = result.get("result", "unknown")
    if status in ("success", "sent"):
        print(f"  Print job sent to {serial}")
        print(
            "  If the printer shows 'Failed to get AMS mapping table',"
            " press Resume on the touchscreen."
        )
    else:
        raise RuntimeError(f"Cloud print failed: {result}")


def _send_moonraker(
    gcode_path: Path,
    url: str,
    api_key: str | None = None,
    dry_run: bool = False,
    upload_only: bool = False,
) -> None:
    """Send gcode to a Klipper/Moonraker printer via REST API (experimental)."""
    try:
        import requests
    except ImportError:
        raise ImportError(
            "requests is required for Moonraker printing. Install with: pip install requests"
        ) from None

    base = url.rstrip("/")
    print(f"Sending {gcode_path.name} to Moonraker at {base}")

    if dry_run:
        action = "upload" if upload_only else "upload and start print"
        print(f"  [dry-run] Would {action} {gcode_path.name}")
        return

    headers = {}
    if api_key:
        headers["X-Api-Key"] = api_key

    # Upload file
    with open(gcode_path, "rb") as f:
        resp = requests.post(
            f"{base}/server/files/upload",
            files={"file": (gcode_path.name, f)},
            headers=headers,
        )
    resp.raise_for_status()
    print(f"  Uploaded {gcode_path.name}")

    if upload_only:
        print("  File ready — start from Mainsail/Fluidd or printer screen")
    else:
        resp = requests.post(
            f"{base}/printer/print/start",
            json={"filename": gcode_path.name},
            headers=headers,
        )
        resp.raise_for_status()
        print("  Print started")


def send_print(
    gcode_path: Path,
    config: PrinterConfig,
    dry_run: bool = False,
    upload_only: bool = False,
    experimental: bool = False,
    skip_ams_mapping: bool = False,
) -> None:
    """Send gcode to a printer.

    Dispatches based on printer type from credentials.toml.
    Env vars override credential values (see load_printer_credentials).
    """
    creds = load_printer_credentials(config.name)
    ptype = creds.get("type")

    if not ptype:
        raise FabprintError(
            f"Printer '{config.name}' has no 'type' in credentials.toml. "
            "Run 'fabprint setup' to configure it."
        )

    if ptype == "bambu-lan":
        ip = creds.get("ip") or ""
        access_code = creds.get("access_code") or ""
        serial = creds.get("serial") or ""
        for field_name, field_val in [("ip", ip), ("access_code", access_code), ("serial", serial)]:
            if not field_val:
                raise FabprintError(
                    f"bambu-lan printer '{config.name}' requires {field_name}. "
                    "Run 'fabprint setup' to configure it."
                )
        _send_lan(
            gcode_path,
            ip=ip,
            access_code=access_code,
            serial=serial,
            dry_run=dry_run,
            upload_only=upload_only,
        )

    elif ptype == "bambu-cloud":
        if not creds["serial"]:
            raise FabprintError(
                f"bambu-cloud printer '{config.name}' requires serial. "
                "Run 'fabprint setup' to configure it."
            )
        _send_cloud_bridge(
            gcode_path,
            serial=creds["serial"],
            dry_run=dry_run,
            verbose=log.isEnabledFor(logging.DEBUG),
            skip_ams_mapping=skip_ams_mapping,
        )

    elif ptype == "moonraker":
        if not creds["url"]:
            raise FabprintError(
                f"moonraker printer '{config.name}' requires url. "
                "Run 'fabprint setup' to configure it."
            )
        _send_moonraker(
            gcode_path,
            url=creds["url"],
            api_key=creds.get("api_key"),
            dry_run=dry_run,
            upload_only=upload_only,
        )

    else:
        raise FabprintError(
            f"Unknown printer type '{ptype}' for printer '{config.name}'. "
            f"Valid types: bambu-lan, bambu-cloud, moonraker"
        )
