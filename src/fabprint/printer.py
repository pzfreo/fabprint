"""Send gcode to a Bambu Lab printer via LAN or Bambu Connect."""

from __future__ import annotations

import hashlib
import logging
import os
import zipfile
from pathlib import Path

from fabprint.config import PrinterConfig
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


def _resolve_credentials(config: PrinterConfig) -> dict[str, str | None]:
    """Merge config values with env var overrides.

    Env vars take precedence over config file values.
    """
    return {
        "mode": config.mode,
        "ip": os.environ.get("BAMBU_PRINTER_IP", config.ip),
        "access_code": os.environ.get("BAMBU_ACCESS_CODE", config.access_code),
        "serial": os.environ.get("BAMBU_SERIAL", config.serial),
        "email": os.environ.get("BAMBU_EMAIL"),
        "password": os.environ.get("BAMBU_PASSWORD"),
    }


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


def _send_cloud(
    gcode_path: Path,
    dry_run: bool = False,
) -> None:
    """Send gcode to printer via Bambu Cloud API (experimental).

    Uses bambu-lab-cloud-api to upload gcode and start a print via
    the HTTP + MQTT cloud APIs. This is experimental — Bambu's cloud
    broker currently rejects third-party MQTT print commands with
    "MQTT Command verification failed".
    """
    try:
        from bambu_lab_cloud_api import BambuClient
    except ImportError:
        raise ImportError(
            "bambu-lab-cloud-api is required for cloud printing. "
            "Install with: pip install fabprint[cloud]"
        ) from None

    email = os.environ.get("BAMBU_EMAIL")
    password = os.environ.get("BAMBU_PASSWORD")
    if not email or not password:
        raise ValueError("bambu-cloud mode requires BAMBU_EMAIL and BAMBU_PASSWORD env vars.")

    print(f"Sending {gcode_path.name} via Bambu Cloud (experimental)")

    if dry_run:
        print(f"  [dry-run] Would upload {gcode_path.name} via cloud API")
        return

    client = BambuClient(email, password)
    client.login()
    log.info("Logged in to Bambu Cloud")

    devices = client.get_devices()
    if not devices:
        raise RuntimeError("No printers found in Bambu Cloud account")

    device = devices[0]
    device_id = device["dev_id"]
    device_name = device.get("name", device_id)
    print(f"  Printer: {device_name} ({device_id})")

    file_url = client.upload_file(str(gcode_path))
    log.info("Uploaded to %s", file_url)
    print(f"  Uploaded {gcode_path.name}")

    # Attempt to start print via cloud API — currently returns 405 or
    # MQTT command verification failure. Kept for future compatibility.
    try:
        client.start_print_job(device_id, gcode_path.name, file_url)
        print("  Print started via cloud")
    except (RuntimeError, OSError, ValueError) as e:
        print(f"  Warning: Could not start print via cloud API: {e}")
        print("  File uploaded — start manually from Bambu Handy or printer touchscreen")


def _send_bambu_connect(
    gcode_path: Path,
    dry_run: bool = False,
) -> None:
    """Send gcode to printer via Bambu Connect.

    Wraps gcode in a .gcode.3mf and opens it in Bambu Connect using
    the bambu-connect:// URL scheme. The user confirms and starts the
    print from the Bambu Connect UI.
    """
    import subprocess
    import sys
    from urllib.parse import quote

    # Prefer the slicer's --min-save export (has proper project_settings).
    # Fall back to wrap_gcode_3mf for pre-sliced gcode not produced by fabprint.
    sliced_3mf = gcode_path.parent / "plate_sliced.gcode.3mf"
    if sliced_3mf.exists():
        threemf_path = sliced_3mf
        print(f"  Using {threemf_path.name}")
    else:
        threemf_path = wrap_gcode_3mf(gcode_path)
        print(f"  Wrapped as {threemf_path.name}")

    if dry_run:
        print(f"  [dry-run] Would open {threemf_path.name} in Bambu Connect")
        return

    # Ensure Bambu Connect is running before sending the URL
    import time

    if sys.platform == "darwin":
        subprocess.run(["open", "-a", "Bambu Connect"], check=True)
    elif sys.platform == "win32":
        os.startfile("Bambu Connect")  # noqa: S606
    print("  Waiting for Bambu Connect...")
    time.sleep(5)

    encoded_path = quote(str(threemf_path), safe="")
    encoded_name = quote(gcode_path.stem, safe="")
    url = f"bambu-connect://import-file?path={encoded_path}&name={encoded_name}&version=1.0.0"

    if sys.platform == "darwin":
        subprocess.run(["open", url], check=True)
    elif sys.platform == "win32":
        os.startfile(url)  # noqa: S606
    else:
        subprocess.run(["xdg-open", url], check=True)

    print("  Opened in Bambu Connect — confirm and print from there")


def send_print(
    gcode_path: Path,
    config: PrinterConfig,
    dry_run: bool = False,
    upload_only: bool = False,
) -> None:
    """Send gcode to a Bambu Lab printer.

    Dispatches to LAN or cloud mode based on config.
    Env vars override config values (see _resolve_credentials).
    If upload_only is True, uploads without starting the print.
    """
    creds = _resolve_credentials(config)

    if creds["mode"] in ("bambu-lan", "lan"):
        for field in ("ip", "access_code", "serial"):
            if not creds[field]:
                env_var = f"BAMBU_{field.upper()}" if field != "ip" else "BAMBU_PRINTER_IP"
                raise ValueError(
                    f"LAN mode requires {field}. Set in [printer] config or {env_var} env var."
                )
        _send_lan(
            gcode_path,
            ip=creds["ip"],
            access_code=creds["access_code"],
            serial=creds["serial"],
            dry_run=dry_run,
            upload_only=upload_only,
        )

    elif creds["mode"] in ("bambu-connect", "cloud"):
        _send_bambu_connect(
            gcode_path,
            dry_run=dry_run,
        )

    elif creds["mode"] == "bambu-cloud":
        _send_cloud(
            gcode_path,
            dry_run=dry_run,
        )
