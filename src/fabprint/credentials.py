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
    path = _credentials_path()
    existing = _load_raw()

    printers = existing.get("printers", {})
    if printers:
        print(f"Existing printers in {path}:")
        for name, creds in printers.items():
            ptype = creds.get("type", "unknown")
            print(f"  {name} ({ptype})")
        print()

    name = input("Printer name (e.g. 'workshop'): ").strip()
    if not name:
        print("Aborted — printer name is required.")
        return

    # Choose printer type
    print("\nPrinter types:")
    type_list = list(PRINTER_TYPES.keys())
    for i, t in enumerate(type_list, 1):
        info = PRINTER_TYPES[t]
        print(f"  [{i}] {t} — {info['description']}")

    while True:
        raw = input("Choose type [1]: ").strip()
        if not raw:
            ptype = type_list[0]
            break
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(type_list):
                ptype = type_list[idx]
                break
        except ValueError:
            pass
        print(f"  Enter a number 1-{len(type_list)}")

    print(f"\nSetting up '{name}' ({ptype})")

    entry: dict[str, str] = {"type": ptype}
    type_info = PRINTER_TYPES[ptype]

    # Collect required fields
    for field in type_info["required"]:
        while True:
            val = input(f"  {field}: ").strip()
            if val:
                entry[field] = val
                break
            print(f"    {field} is required")

    # Collect optional fields
    for field in type_info["optional"]:
        val = input(f"  {field} (optional): ").strip()
        if val:
            entry[field] = val

    # Handle cloud login for bambu-cloud type
    if ptype == "bambu-cloud":
        cloud = existing.get("cloud")
        if cloud and cloud.get("token"):
            print(f"\n  Cloud login already configured ({cloud.get('email', 'unknown')})")
        else:
            print("\n  Cloud printing requires a Bambu Lab account login.")
            do_login = input("  Log in now? [Y/n]: ").strip().lower()
            if do_login != "n":
                _cloud_login_flow(existing)

    # Merge into existing config
    if "printers" not in existing:
        existing["printers"] = {}
    existing["printers"][name] = entry
    _write_credentials(existing)

    print(f"\nWrote {path} (mode 600)")
    print("Reference this printer in fabprint.toml with:")
    print("  [printer]")
    print(f'  name = "{name}"')


def _cloud_login_flow(existing: dict) -> None:
    """Run the Bambu Cloud login flow and save credentials."""
    import os

    from fabprint.auth import _get_user_profile, _login, _show_devices

    # Check for existing valid token
    cloud = existing.get("cloud")
    if cloud and cloud.get("token"):
        try:
            profile = _get_user_profile(cloud["token"])
            print(f"  Cached token is valid ({profile.get('name') or profile['uid']})")
            _show_devices(cloud["token"])
            refresh = input("  Re-login anyway? [y/N]: ").strip().lower()
            if refresh != "y":
                return
        except Exception:
            print("  Cached token is invalid or expired.")

    # Accept env vars or prompt
    email = os.environ.get("BAMBU_EMAIL") or input("  Email: ").strip()
    password = os.environ.get("BAMBU_PASSWORD") or input("  Password: ").strip()
    if not email or not password:
        print("  Skipped — email and password required.")
        return

    print("\n  Logging in...")
    token, refresh_token = _login(email, password)
    profile = _get_user_profile(token)

    existing["cloud"] = {
        "token": token,
        "refresh_token": refresh_token,
        "email": email,
        "uid": profile["uid"],
    }

    print(f"\n  Login successful! User: {profile.get('name') or profile['uid']}")
    _show_devices(token)
