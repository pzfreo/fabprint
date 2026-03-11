"""Bambu Cloud authentication — login, token caching, and device discovery."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests

API_BASE = "https://api.bambulab.com"
TOKEN_FILE = Path.home() / ".bambu_cloud_token"

SLICER_HEADERS = {
    "X-BBL-Client-Name": "OrcaSlicer",
    "X-BBL-Client-Type": "slicer",
    "X-BBL-Client-Version": "02.03.01.00",
    "User-Agent": "bambu_network_agent/02.03.01.00",
    "Content-Type": "application/json",
}


def _request_verification_code(email: str) -> None:
    """Request a verification code be sent to the user's email."""
    resp = requests.post(
        f"{API_BASE}/v1/user-service/user/sendemail/code",
        headers=SLICER_HEADERS,
        json={"email": email, "type": "codeLogin"},
    )
    resp.raise_for_status()
    print(f"  Verification code sent to {email}")


def _login(email: str, password: str) -> tuple[str, str]:
    """Login and return (access_token, refresh_token). Handles all auth flows."""

    # Step 1: Try password login
    print("  Attempting password login...")
    resp = requests.post(
        f"{API_BASE}/v1/user-service/user/login",
        headers=SLICER_HEADERS,
        json={"account": email, "password": password, "apiError": ""},
    )
    resp.raise_for_status()
    data = resp.json()

    token = data.get("accessToken")
    refresh_token = data.get("refreshToken", "")
    login_type = data.get("loginType", "")

    # Step 2: Handle verification code flow
    if not token and login_type == "verifyCode":
        print("  Account requires email verification code.")
        already = input("  Did you already receive a code? [y/N]: ").strip().lower()
        if already != "y":
            _request_verification_code(email)

        code = input("  Enter verification code: ").strip()
        resp = requests.post(
            f"{API_BASE}/v1/user-service/user/login",
            headers=SLICER_HEADERS,
            json={"account": email, "code": code},
        )
        resp.raise_for_status()
        data = resp.json()
        token = data.get("accessToken")
        refresh_token = data.get("refreshToken", "")

    # Step 3: Handle TFA flow
    if not token and data.get("tfaKey"):
        tfa_key = data["tfaKey"]
        print("  Account requires two-factor authentication.")
        tfa_code = input("  Enter 2FA code: ").strip()
        resp = requests.post(
            f"{API_BASE}/v1/user-service/user/tfa",
            headers=SLICER_HEADERS,
            json={"tfaKey": tfa_key, "tfaCode": tfa_code},
        )
        resp.raise_for_status()
        data = resp.json()
        token = data.get("accessToken")
        refresh_token = data.get("refreshToken", "")

    if not token:
        print(f"\n  Login failed. Response: {json.dumps(data, indent=2)}")
        sys.exit(1)

    return token, refresh_token


def _get_user_profile(token: str) -> dict:
    """Fetch user profile (uid, name, avatar)."""
    resp = requests.get(
        f"{API_BASE}/v1/design-user-service/my/preference",
        headers={**SLICER_HEADERS, "Authorization": f"Bearer {token}"},
    )
    resp.raise_for_status()
    data = resp.json()
    return {
        "uid": str(data.get("uid", "")),
        "name": data.get("name", ""),
        "avatar": data.get("avatar", ""),
    }


def _get_devices(token: str) -> list[dict]:
    """List printers bound to the account."""
    resp = requests.get(
        f"{API_BASE}/v1/iot-service/api/user/bind",
        headers={**SLICER_HEADERS, "Authorization": f"Bearer {token}"},
    )
    resp.raise_for_status()
    return resp.json().get("devices", [])


def cloud_login(email: str | None = None, password: str | None = None) -> None:
    """Interactive Bambu Cloud login. Saves token to ~/.bambu_cloud_token."""
    email = email or os.environ.get("BAMBU_EMAIL")
    password = password or os.environ.get("BAMBU_PASSWORD")
    if not email or not password:
        print("Set BAMBU_EMAIL and BAMBU_PASSWORD environment variables,")
        print("or pass --email and --password.")
        sys.exit(1)

    print("\nBambu Cloud Login")
    print(f"  Account: {email}")

    # Check for existing token
    if TOKEN_FILE.exists():
        try:
            cached = json.loads(TOKEN_FILE.read_text())
            if cached.get("email") == email and cached.get("token"):
                print(f"\n  Found cached token in {TOKEN_FILE}")
                profile = _get_user_profile(cached["token"])
                print(f"  Token is valid! User: {profile['name'] or profile['uid']}")

                refresh = input("  Refresh token anyway? [y/N]: ").strip().lower()
                if refresh != "y":
                    _show_devices(cached["token"])
                    return
        except Exception:
            pass

    # Fresh login
    print("\n  Logging in...")
    token, refresh_token = _login(email, password)
    profile = _get_user_profile(token)

    # Save token
    TOKEN_FILE.write_text(
        json.dumps(
            {
                "token": token,
                "refreshToken": refresh_token,
                "email": email,
                "uid": profile["uid"],
                "name": profile["name"],
                "avatar": profile["avatar"],
            }
        )
    )
    TOKEN_FILE.chmod(0o600)

    print("\n  Login successful!")
    print(f"  User: {profile['name'] or profile['uid']}")
    print(f"  Token saved to: {TOKEN_FILE}")

    _show_devices(token)


def _show_devices(token: str) -> None:
    """Print bound printers."""
    print("\n  Printers:")
    devices = _get_devices(token)
    if devices:
        for d in devices:
            name = d.get("name", "unnamed")
            dev_id = d.get("dev_id", "?")
            online = "online" if d.get("online") else "offline"
            model = d.get("dev_product_name", d.get("dev_model_name", "?"))
            print(f"    {name} ({model}) — {dev_id} [{online}]")
    else:
        print("    No printers found")
