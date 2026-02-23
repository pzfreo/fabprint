"""Send gcode to a Bambu Lab printer via LAN or cloud."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fabprint.config import PrinterConfig

log = logging.getLogger(__name__)


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
        print(f"  [dry-run] Would upload {gcode_path.name} and start print")
        return

    printer = Printer(ip_address=ip, access_code=access_code, serial=serial)
    try:
        printer.connect()
        log.info("Connected to printer at %s", ip)

        with open(gcode_path, "rb") as f:
            remote_path = printer.upload_file(f, filename=gcode_path.name)
        log.info("Uploaded to %s", remote_path)
        print(f"  Uploaded {gcode_path.name}")

        printer.start_print(filename=remote_path, plate_number=1)
        print("  Print started")
    finally:
        printer.disconnect()
        log.info("Disconnected from printer")


def _send_cloud(
    gcode_path: Path,
    email: str,
    password: str,
    serial: str | None = None,
    dry_run: bool = False,
) -> None:
    """Send gcode to printer via Bambu cloud API.

    Uses BambuAuthenticator to get/cache a token, then BambuClient
    for API calls. Tokens are cached in ~/.bambu_token.
    """
    try:
        from bambulab import BambuClient
        from bambulab.auth import BambuAuthenticator
    except ImportError:
        raise ImportError(
            "bambu-lab-cloud-api is required for cloud printing. "
            "Install with: pip install fabprint[cloud]"
        ) from None

    print(f"Sending {gcode_path.name} via Bambu cloud")

    if dry_run:
        print(f"  [dry-run] Would upload {gcode_path.name} and start cloud print")
        return

    # Authenticate (uses cached token if valid, else logs in)
    auth = BambuAuthenticator()
    token = auth.get_or_create_token(username=email, password=password)
    client = BambuClient(token=token)

    devices = client.get_devices()
    if not devices:
        raise RuntimeError("No printers found on Bambu cloud account")

    if serial:
        device = next((d for d in devices if d.serial == serial), None)
        if not device:
            available = ", ".join(d.serial for d in devices)
            raise RuntimeError(f"Printer {serial} not found. Available: {available}")
    else:
        device = devices[0]
        log.info("Using first available printer: %s (%s)", device.name, device.serial)

    print(f"  Printer: {device.name} ({device.serial})")

    result = client.upload_file(str(gcode_path))
    log.info("Cloud upload result: %s", result)
    print(f"  Uploaded {gcode_path.name}")
    print("  Print started via cloud")


def send_print(
    gcode_path: Path,
    config: PrinterConfig,
    dry_run: bool = False,
) -> None:
    """Send gcode to a Bambu Lab printer.

    Dispatches to LAN or cloud mode based on config.
    Env vars override config values (see _resolve_credentials).
    """
    creds = _resolve_credentials(config)

    if creds["mode"] == "lan":
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
        )

    elif creds["mode"] == "cloud":
        if not creds["email"] or not creds["password"]:
            raise ValueError("Cloud mode requires BAMBU_EMAIL and BAMBU_PASSWORD env vars.")
        _send_cloud(
            gcode_path,
            email=creds["email"],
            password=creds["password"],
            serial=creds.get("serial"),
            dry_run=dry_run,
        )
