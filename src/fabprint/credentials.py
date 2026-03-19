"""Load and manage printer credentials from ~/.config/fabprint/credentials.toml."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import tomllib
from contextlib import contextmanager
from pathlib import Path

from fabprint import FabprintError


def mask_serial(serial: str) -> str:
    """Mask a printer serial, keeping only the last 4 characters visible."""
    if len(serial) <= 4:
        return serial
    return "*" * (len(serial) - 4) + serial[-4:]


# Valid printer types and their required/optional fields
PRINTER_TYPES = {
    "bambu-lan": {
        "required": ["ip", "access_code", "serial"],
        "optional": [],
        "description": "Bambu Lab printer via LAN (direct connection)",
    },
    "bambu-cloud": {
        "required": ["serial"],
        "optional": [],
        "description": "Bambu Lab printer via cloud (requires cloud login)",
    },
    "moonraker": {
        "required": ["url"],
        "optional": ["api_key"],
        "description": "Klipper/Moonraker printer via REST API (experimental)",
    },
}


def _credentials_path() -> Path:
    """Return the path to the credentials file."""
    env = os.environ.get("FABPRINT_CREDENTIALS")
    if env:
        return Path(env)
    if sys.platform == "win32":
        return Path.home() / "AppData/Roaming/fabprint/credentials.toml"
    return Path.home() / ".config/fabprint/credentials.toml"


def _load_raw() -> dict:
    """Load the raw credentials TOML, or return empty dict if not found."""
    path = _credentials_path()
    if not path.exists():
        return {}
    with open(path, "rb") as f:
        return tomllib.load(f)


def _write_credentials(data: dict) -> None:
    """Write credentials dict to TOML file with 0o600 permissions.

    Manual TOML writer (tomllib is read-only, no tomli_w dependency).
    """
    path = _credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        # Write [cloud] section
        cloud = data.get("cloud", {})
        if cloud:
            f.write("[cloud]\n")
            for key, val in cloud.items():
                f.write(f'{key} = "{val}"\n')
            f.write("\n")

        # Write [printers.*] sections
        for printer_name, creds in data.get("printers", {}).items():
            f.write(f"[printers.{printer_name}]\n")
            for key, val in creds.items():
                f.write(f'{key} = "{val}"\n')
            f.write("\n")

    path.chmod(0o600)


def load_printer_credentials(name: str | None) -> dict[str, str | None]:
    """Load credentials for a named printer.

    Resolution order for each field:
    1. Environment variables (BAMBU_PRINTER_IP, BAMBU_ACCESS_CODE, BAMBU_SERIAL)
    2. Named printer entry in credentials.toml
    3. None

    Returns dict with 'type' key indicating printer type.
    """
    file_creds: dict[str, str | None] = {}

    if name is not None:
        path = _credentials_path()
        if not path.exists():
            raise FabprintError(
                f"Credentials file not found: {path}\nRun 'fabprint setup' to create it."
            )
        with open(path, "rb") as f:
            raw = tomllib.load(f)
        printers = raw.get("printers", {})
        if name not in printers:
            available = list(printers.keys())
            raise FabprintError(f"Printer '{name}' not found in {path}. Available: {available}")
        file_creds = printers[name]

    return {
        "type": file_creds.get("type"),
        "ip": os.environ.get("BAMBU_PRINTER_IP", file_creds.get("ip")),
        "access_code": os.environ.get("BAMBU_ACCESS_CODE", file_creds.get("access_code")),
        "serial": os.environ.get("BAMBU_SERIAL", file_creds.get("serial")),
        "url": file_creds.get("url"),
        "api_key": file_creds.get("api_key"),
    }


def list_printers() -> dict[str, dict[str, str]]:
    """Return all configured printers from credentials.toml.

    Returns dict mapping printer name → credentials dict (including 'type').
    """
    raw = _load_raw()
    return raw.get("printers", {})


def load_cloud_credentials() -> dict[str, str] | None:
    """Load cloud credentials from the [cloud] section.

    Returns dict with token, refresh_token, email, uid, or None if not set.
    """
    raw = _load_raw()
    cloud = raw.get("cloud")
    if not cloud or not cloud.get("token"):
        return None
    return cloud


def save_cloud_credentials(
    token: str, refresh_token: str, email: str, uid: str, **extra: str
) -> None:
    """Save cloud credentials to the [cloud] section of credentials.toml."""
    raw = _load_raw()
    raw["cloud"] = {
        "token": token,
        "refresh_token": refresh_token,
        "email": email,
        "uid": uid,
        **extra,
    }
    _write_credentials(raw)


@contextmanager
def cloud_token_json():
    """Context manager that yields a temp JSON file path for the C++ bridge.

    The bridge binary expects a JSON file with token, refreshToken, email, uid fields.
    Creates a temp file from credentials.toml [cloud] data, cleans up on exit.
    """
    cloud = load_cloud_credentials()
    if not cloud:
        raise FabprintError(
            "No cloud credentials found.\n"
            "Run 'fabprint setup' and choose 'bambu-cloud' type to log in."
        )

    # Bridge expects camelCase keys
    bridge_data = {
        "token": cloud["token"],
        "refreshToken": cloud.get("refresh_token", ""),
        "email": cloud.get("email", ""),
        "uid": cloud.get("uid", ""),
    }

    # Use ~/.cache so Docker Desktop on macOS can mount the file (/var/folders is not shared)
    cache_dir = Path.home() / ".cache" / "fabprint"
    cache_dir.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", prefix="bambu_token_", dir=cache_dir, delete=False
    )
    try:
        json.dump(bridge_data, tmp)
        tmp.close()
        Path(tmp.name).chmod(0o600)
        yield Path(tmp.name)
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


def setup_printer() -> None:
    """Interactive wizard to add or update a printer in credentials.toml."""
    from fabprint import ui

    path = _credentials_path()
    existing = _load_raw()

    ui.heading("Printer Setup")

    printers = existing.get("printers", {})
    if printers:
        ui.console.print()
        items = [(n, c.get("type", "unknown")) for n, c in printers.items()]
        ui.choice_table(items, ["Name", "Type"])
        ui.console.print()

    name = ui.prompt_str("Printer name (e.g. 'workshop')")
    if not name:
        ui.warn("Aborted — printer name is required.")
        return

    # Choose printer type
    ui.console.print()
    type_list = list(PRINTER_TYPES.keys())
    items = [(t, PRINTER_TYPES[t]["description"]) for t in type_list]
    ui.choice_table(items, ["Type", "Description"])

    while True:
        pick = ui.prompt_int("Choose type", 1)
        if 1 <= pick <= len(type_list):
            ptype = type_list[pick - 1]
            break
        ui.console.print(f"  Enter a number 1-{len(type_list)}")

    ui.console.print()
    ui.info(f"Setting up [bold]{name}[/bold] ({ptype})")

    entry: dict[str, str] = {"type": ptype}
    type_info = PRINTER_TYPES[ptype]

    # For bambu-cloud: login first, then offer printer selection from the account
    if ptype == "bambu-cloud":
        cloud = existing.get("cloud")
        if cloud and cloud.get("token"):
            ui.success(f"Cloud login already configured ({cloud.get('email', 'unknown')})")
        else:
            ui.console.print()
            ui.info("Cloud printing requires a Bambu Lab account login.")
            if ui.prompt_yn("Log in now?"):
                _cloud_login_flow(existing)

        # Try to list bound printers so user can pick instead of typing serial
        picked = _pick_cloud_printer(existing.get("cloud"))
        if picked:
            entry["serial"] = picked

    # Collect any remaining required fields not yet filled
    for field in type_info["required"]:
        if field in entry:
            continue
        while True:
            val = ui.prompt_str(field)
            if val:
                entry[field] = val
                break
            ui.warn(f"{field} is required")

    # Collect optional fields
    for field in type_info["optional"]:
        val = ui.prompt_str(f"{field} (optional)")
        if val:
            entry[field] = val

    # Merge into existing config
    if "printers" not in existing:
        existing["printers"] = {}
    existing["printers"][name] = entry
    _write_credentials(existing)

    ui.console.print()
    ui.success(f"Wrote {path} (mode 600)")
    toml_ref = f'[printer]\nname = "{name}"'
    from rich.syntax import Syntax

    ui.console.print(
        "  Reference this printer in fabprint.toml with:",
    )
    ui.console.print(Syntax(toml_ref, "toml", theme="monokai", line_numbers=False))


def _pick_cloud_printer(cloud: dict | None) -> str | None:
    """If we have a valid cloud token, list bound printers and let user pick one.

    Returns the serial number of the chosen printer, or None.
    """
    from fabprint import ui

    if not cloud or not cloud.get("token"):
        return None
    try:
        from fabprint.auth import _get_devices

        devices = _get_devices(cloud["token"])
    except Exception:
        return None
    if not devices:
        return None

    ui.console.print()
    items = []
    for d in devices:
        dname = d.get("name", "unnamed")
        model = d.get("dev_product_name", d.get("dev_model_name", "?"))
        serial = d.get("dev_id", "?")
        online_str = "[green]online[/green]" if d.get("online") else "[dim]offline[/dim]"
        items.append((dname, model, mask_serial(serial), online_str))
    ui.choice_table(items, ["Name", "Model", "Serial", "Status"])

    while True:
        pick = ui.prompt_int("Pick a printer", 1)
        idx = pick - 1
        if 0 <= idx < len(devices):
            break
        ui.console.print(f"  Enter a number 1-{len(devices)}")

    chosen = devices[idx]
    serial = chosen.get("dev_id", "")
    dname = chosen.get("name", "")
    ui.success(f"Selected: {dname} (serial: {mask_serial(serial)})")
    return serial


def _cloud_login_flow(existing: dict) -> None:
    """Run the Bambu Cloud login flow and save credentials."""
    import os

    from fabprint import ui
    from fabprint.auth import _get_user_profile, _login, _show_devices

    # Check for existing valid token
    cloud = existing.get("cloud")
    if cloud and cloud.get("token"):
        try:
            profile = _get_user_profile(cloud["token"])
            ui.success(f"Cached token is valid ({profile.get('name') or profile['uid']})")
            _show_devices(cloud["token"])
            if not ui.prompt_yn("Re-login anyway?", default=False):
                return
        except Exception:
            ui.warn("Cached token is invalid or expired.")

    # Accept env vars or prompt
    email = os.environ.get("BAMBU_EMAIL") or ui.prompt_str("Email")
    password = os.environ.get("BAMBU_PASSWORD") or ui.prompt_password("Password")
    if not email or not password:
        ui.warn("Skipped — email and password required.")
        return

    ui.info("Logging in...")
    token, refresh_token = _login(email, password)
    profile = _get_user_profile(token)

    existing["cloud"] = {
        "token": token,
        "refresh_token": refresh_token,
        "email": email,
        "uid": profile["uid"],
    }

    ui.success(f"Login successful! User: {profile.get('name') or profile['uid']}")
    _show_devices(token)
