"""Load printer credentials from ~/.config/fabprint/credentials.toml."""

from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path

from fabprint import FabprintError


def _credentials_path() -> Path:
    """Return the path to the credentials file."""
    env = os.environ.get("FABPRINT_CREDENTIALS")
    if env:
        return Path(env)
    if sys.platform == "win32":
        return Path.home() / "AppData/Roaming/fabprint/credentials.toml"
    return Path.home() / ".config/fabprint/credentials.toml"


def load_printer_credentials(name: str | None) -> dict[str, str | None]:
    """Load credentials for a named printer.

    Resolution order for each field:
    1. Environment variables (BAMBU_PRINTER_IP, BAMBU_ACCESS_CODE, BAMBU_SERIAL,
       BAMBU_EMAIL, BAMBU_PASSWORD)
    2. Named printer entry in credentials.toml
    3. None

    If name is None and no env vars are set, returns all-None values.
    """
    file_creds: dict[str, str | None] = {}

    if name is not None:
        path = _credentials_path()
        if not path.exists():
            raise FabprintError(
                f"Credentials file not found: {path}\n"
                f"Create it with a [printers.{name}] section, or set env vars instead."
            )
        with open(path, "rb") as f:
            raw = tomllib.load(f)
        printers = raw.get("printers", {})
        if name not in printers:
            available = list(printers.keys())
            raise FabprintError(f"Printer '{name}' not found in {path}. Available: {available}")
        file_creds = printers[name]

    return {
        "ip": os.environ.get("BAMBU_PRINTER_IP", file_creds.get("ip")),
        "access_code": os.environ.get("BAMBU_ACCESS_CODE", file_creds.get("access_code")),
        "serial": os.environ.get("BAMBU_SERIAL", file_creds.get("serial")),
        "email": os.environ.get("BAMBU_EMAIL", file_creds.get("email")),
        "password": os.environ.get("BAMBU_PASSWORD", file_creds.get("password")),
    }
