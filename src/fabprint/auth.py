"""Bambu Cloud authentication — login, token caching, and device discovery."""

from __future__ import annotations

import json
import sys

import requests

API_BASE = "https://api.bambulab.com"

SLICER_HEADERS = {
    "X-BBL-Client-Name": "OrcaSlicer",
    "X-BBL-Client-Type": "slicer",
    "X-BBL-Client-Version": "02.03.01.00",
    "User-Agent": "bambu_network_agent/02.03.01.00",
    "Content-Type": "application/json",
}


def _request_verification_code(email: str) -> None:
    """Request a verification code be sent to the user's email."""
    from fabprint import ui

    resp = requests.post(
        f"{API_BASE}/v1/user-service/user/sendemail/code",
        headers=SLICER_HEADERS,
        json={"email": email, "type": "codeLogin"},
    )
    resp.raise_for_status()
    ui.success(f"Verification code sent to {email}")


def _login(email: str, password: str) -> tuple[str, str]:
    """Login and return (access_token, refresh_token). Handles all auth flows."""

    from fabprint import ui

    # Step 1: Try password login
    ui.info("Attempting password login...")
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
        ui.info("Account requires email verification code.")
        if not ui.prompt_yn("Did you already receive a code?", default=False):
            _request_verification_code(email)

        code = ui.prompt_str("Enter verification code")
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
        ui.info("Account requires two-factor authentication.")
        tfa_code = ui.prompt_str("Enter 2FA code")
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
        ui.error(f"Login failed. Response: {json.dumps(data, indent=2)}")
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


def _show_devices(token: str) -> None:
    """Print bound printers."""
    from fabprint import ui
    from fabprint.credentials import mask_serial

    devices = _get_devices(token)
    if devices:
        ui.console.print()
        items = []
        for d in devices:
            name = d.get("name", "unnamed")
            dev_id = d.get("dev_id", "?")
            model = d.get("dev_product_name", d.get("dev_model_name", "?"))
            online_str = "[green]online[/green]" if d.get("online") else "[dim]offline[/dim]"
            items.append((name, model, mask_serial(dev_id), online_str))
        ui.choice_table(items, ["Name", "Model", "Serial", "Status"])
    else:
        ui.info("No printers found")
