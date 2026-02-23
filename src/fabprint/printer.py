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
    email: str,
    password: str,
    serial: str | None = None,
    dry_run: bool = False,
    upload_only: bool = False,
) -> None:
    """Send gcode to printer via Bambu cloud API.

    Uses BambuAuthenticator for token auth, BambuClient for HTTP API
    (device listing, file upload), and MQTTClient to send the print
    command. Tokens are cached in ~/.bambu_token.
    """
    try:
        from bambulab import BambuClient
        from bambulab.auth import BambuAuthenticator
        from bambulab.mqtt import MQTTClient
    except ImportError:
        raise ImportError(
            "bambu-lab-cloud-api is required for cloud printing. "
            "Install with: pip install fabprint[cloud]"
        ) from None

    print(f"Sending {gcode_path.name} via Bambu cloud")

    if dry_run:
        action = "upload" if upload_only else "upload and start cloud print"
        print(f"  [dry-run] Would {action} {gcode_path.name}")
        return

    # Authenticate (uses cached token if valid, else logs in)
    auth = BambuAuthenticator()
    token = auth.get_or_create_token(username=email, password=password)
    client = BambuClient(token=token)

    devices = client.get_devices()
    if not devices:
        raise RuntimeError("No printers found on Bambu cloud account")

    if serial:
        device = next((d for d in devices if d["dev_id"] == serial), None)
        if not device:
            available = ", ".join(d["dev_id"] for d in devices)
            raise RuntimeError(f"Printer {serial} not found. Available: {available}")
    else:
        device = devices[0]
        log.info("Using first available printer: %s (%s)", device["name"], device["dev_id"])

    device_id = device["dev_id"]
    print(f"  Printer: {device['name']} ({device_id})")

    result = client.upload_file(str(gcode_path))
    upload_url = result["upload_url"]
    log.info("Cloud upload result: %s", result)
    print(f"  Uploaded {gcode_path.name}")

    if upload_only:
        return

    # Get user ID for MQTT auth
    user_info = client.get_user_info()
    uid = str(user_info.get("uid", user_info.get("user_id", "")))
    if not uid:
        raise RuntimeError("Could not determine user ID for MQTT connection")

    # Connect to cloud MQTT broker and send print command
    import time

    mqtt_client = MQTTClient(
        username=uid,
        access_token=token,
        device_id=device_id,
    )
    mqtt_client.connect(blocking=False)

    # Wait for MQTT connection
    for _ in range(50):
        if mqtt_client.connected:
            break
        time.sleep(0.1)
    if not mqtt_client.connected:
        mqtt_client.disconnect()
        raise RuntimeError("Failed to connect to Bambu cloud MQTT broker")

    try:
        mqtt_client.publish(
            {
                "print": {
                    "sequence_id": "0",
                    "command": "gcode_file",
                    "param": upload_url,
                    "subtask_name": gcode_path.stem,
                    "url": upload_url,
                }
            }
        )
        # Give broker time to deliver the message
        time.sleep(1)
        print("  Print started via cloud")
    finally:
        mqtt_client.disconnect()


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
            upload_only=upload_only,
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
            upload_only=upload_only,
        )
