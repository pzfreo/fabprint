#!/usr/bin/env python3
"""Login to Bambu Cloud and cache the access token.

Run this once before using test_cloud_print.py. Handles:
  - Password login (if the account allows it)
  - Email verification code login (most accounts)
  - Two-factor authentication (if enabled)

The token is saved to ~/.bambu_cloud_token and reused by test_cloud_print.py.
Tokens are valid for ~3 months.

Usage:
    export BAMBU_EMAIL="your@email.com"
    export BAMBU_PASSWORD="your_password"
    python scripts/bambu_cloud_login.py

Requirements:
    pip install requests
"""

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


def request_verification_code(email: str) -> None:
    """Request a verification code be sent to the user's email."""
    resp = requests.post(
        f"{API_BASE}/v1/user-service/user/sendemail/code",
        headers=SLICER_HEADERS,
        json={"email": email, "type": "codeLogin"},
    )
    resp.raise_for_status()
    print(f"  Verification code sent to {email}")


def login(email: str, password: str) -> str:
    """Login and return an access token. Handles all auth flows."""

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
    login_type = data.get("loginType", "")

    # Step 2: Handle verification code flow
    if not token and login_type == "verifyCode":
        print("  Account requires email verification code.")

        # The password attempt may have already triggered a code.
        # Ask user if they already got one, otherwise request one.
        already = input("  Did you already receive a code? [y/N]: ").strip().lower()
        if already != "y":
            request_verification_code(email)

        code = input("  Enter verification code: ").strip()

        resp = requests.post(
            f"{API_BASE}/v1/user-service/user/login",
            headers=SLICER_HEADERS,
            json={"account": email, "code": code},
        )
        resp.raise_for_status()
        data = resp.json()
        token = data.get("accessToken")

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

    if not token:
        print(f"\n  Login failed. Response: {json.dumps(data, indent=2)}")
        sys.exit(1)

    return token


def get_user_id(token: str) -> str:
    """Fetch user ID (needed for MQTT username)."""
    resp = requests.get(
        f"{API_BASE}/v1/design-user-service/my/preference",
        headers={**SLICER_HEADERS, "Authorization": f"Bearer {token}"},
    )
    resp.raise_for_status()
    uid = resp.json().get("uid")
    if not uid:
        print(f"  Warning: Could not get user ID from profile")
        return "unknown"
    return str(uid)


def get_devices(token: str) -> list[dict]:
    """List printers bound to the account."""
    resp = requests.get(
        f"{API_BASE}/v1/iot-service/api/user/bind",
        headers={**SLICER_HEADERS, "Authorization": f"Bearer {token}"},
    )
    resp.raise_for_status()
    return resp.json().get("devices", [])


def main():
    email = os.environ.get("BAMBU_EMAIL")
    password = os.environ.get("BAMBU_PASSWORD")
    if not email or not password:
        print("Error: Set BAMBU_EMAIL and BAMBU_PASSWORD environment variables")
        sys.exit(1)

    print(f"\nBambu Cloud Login")
    print(f"  Account: {email}")

    # Check for existing token
    if TOKEN_FILE.exists():
        try:
            cached = json.loads(TOKEN_FILE.read_text())
            if cached.get("email") == email and cached.get("token"):
                print(f"\n  Found cached token in {TOKEN_FILE}")
                uid = get_user_id(cached["token"])
                print(f"  Token is valid! User ID: {uid}")

                refresh = input("  Refresh token anyway? [y/N]: ").strip().lower()
                if refresh != "y":
                    print("\n  Using existing token. You're good to go!")
                    print(f"  Run: python scripts/test_cloud_print.py --status-only")
                    return
        except Exception:
            pass

    # Fresh login
    print(f"\n  Logging in...")
    token = login(email, password)
    uid = get_user_id(token)

    # Save token
    TOKEN_FILE.write_text(json.dumps({"token": token, "email": email}))
    TOKEN_FILE.chmod(0o600)

    print(f"\n  Login successful!")
    print(f"  User ID: {uid}")
    print(f"  Token saved to: {TOKEN_FILE}")

    # Show devices as a bonus
    print(f"\n  Fetching printers...")
    devices = get_devices(token)
    if devices:
        for d in devices:
            name = d.get("name", "unnamed")
            dev_id = d.get("dev_id", "?")
            online = "online" if d.get("online") else "offline"
            model = d.get("dev_product_name", d.get("dev_model_name", "?"))
            print(f"    {name} ({model}) — {dev_id} [{online}]")
    else:
        print("    No printers found")

    print(f"\n  You're ready! Next run:")
    print(f"    python scripts/test_cloud_print.py --status-only")


if __name__ == "__main__":
    main()
