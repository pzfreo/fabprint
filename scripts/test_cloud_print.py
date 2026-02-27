#!/usr/bin/env python3
"""Standalone test for Bambu Lab cloud printing via MQTT.

Zero third-party dependencies beyond paho-mqtt and requests.
Tests the full cloud print lifecycle:
  1. Login (email/password → access token + user ID)
  2. List devices
  3. Upload a .3mf file to Bambu Cloud (S3)
  4. Start print via MQTT project_file command
  5. Pause print
  6. Resume print
  7. Stop print

Login flow:
    First login requires an email verification code (sent to your inbox).
    The token is cached in ~/.bambu_cloud_token for subsequent runs.

Usage:
    export BAMBU_EMAIL="your@email.com"
    export BAMBU_PASSWORD="your_password"

    # Dry run — login, list devices, but don't actually print
    python scripts/test_cloud_print.py

    # Upload and start a print
    python scripts/test_cloud_print.py path/to/file.gcode.3mf

    # Interactive mode — send pause/resume/stop after print starts
    python scripts/test_cloud_print.py path/to/file.gcode.3mf --interactive

Requirements:
    pip install paho-mqtt requests cryptography
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import ssl
import sys
import threading
import time
import zipfile
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

import ftplib
import re
import subprocess

import paho.mqtt.client as mqtt
import requests

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Bambu Cloud HTTP API
# ---------------------------------------------------------------------------

API_BASE = "https://api.bambulab.com"

# Headers that mimic OrcaSlicer (the cloud API expects these)
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


def cloud_login(email: str, password: str) -> dict:
    """Login to Bambu Cloud and return token + user info.

    Handles three login flows:
    1. Direct password login (if account allows it)
    2. Verification code login (code sent to email)
    3. Two-factor authentication (TFA key returned)

    Returns dict with keys: access_token, user_id
    """
    # Try password login first
    resp = requests.post(
        f"{API_BASE}/v1/user-service/user/login",
        headers=SLICER_HEADERS,
        json={"account": email, "password": password, "apiError": ""},
    )
    resp.raise_for_status()
    data = resp.json()

    token = data.get("accessToken")
    login_type = data.get("loginType", "")

    # Handle verification code flow
    if not token and login_type == "verifyCode":
        print("  Account requires email verification code")
        _request_verification_code(email)
        code = input("  Enter verification code from email: ").strip()

        resp = requests.post(
            f"{API_BASE}/v1/user-service/user/login",
            headers=SLICER_HEADERS,
            json={"account": email, "code": code},
        )
        resp.raise_for_status()
        data = resp.json()
        token = data.get("accessToken")

    # Handle TFA flow
    if not token and data.get("tfaKey"):
        tfa_key = data["tfaKey"]
        print("  Account requires two-factor authentication")
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
        raise RuntimeError(f"Login failed: {data}")

    # Save token for reuse
    token_file = Path.home() / ".bambu_cloud_token"
    token_file.write_text(json.dumps({"token": token, "email": email}))
    token_file.chmod(0o600)

    return _get_user_info(token)


def cloud_login_with_cache(email: str, password: str) -> dict:
    """Login using cached token if available, falling back to fresh login."""
    token_file = Path.home() / ".bambu_cloud_token"
    if token_file.exists():
        try:
            cached = json.loads(token_file.read_text())
            if cached.get("email") == email and cached.get("token"):
                info = _get_user_info(cached["token"])
                return info
        except Exception:
            pass  # Token expired or invalid, fall through to fresh login

    return cloud_login(email, password)


def _get_user_info(token: str) -> dict:
    """Get user ID from token for MQTT username."""
    profile_resp = requests.get(
        f"{API_BASE}/v1/design-user-service/my/preference",
        headers={**SLICER_HEADERS, "Authorization": f"Bearer {token}"},
    )
    profile_resp.raise_for_status()
    uid = profile_resp.json().get("uid")
    if not uid:
        raise RuntimeError(f"Could not get user ID from profile: {profile_resp.json()}")

    return {"access_token": token, "user_id": str(uid)}


def cloud_get_devices(token: str) -> list[dict]:
    """List printers bound to the account."""
    resp = requests.get(
        f"{API_BASE}/v1/iot-service/api/user/bind",
        headers={**SLICER_HEADERS, "Authorization": f"Bearer {token}"},
    )
    resp.raise_for_status()
    data = resp.json()

    devices = data.get("devices", [])
    return devices


def cloud_create_project(token: str, filename: str) -> dict:
    """Create a cloud project to get project_id, model_id, and upload_ticket.

    This is the first step in the cloud print flow — it registers
    a project on Bambu's server before the file is uploaded.
    """
    auth_headers = {**SLICER_HEADERS, "Authorization": f"Bearer {token}"}

    resp = requests.post(
        f"{API_BASE}/v1/iot-service/api/user/project",
        headers=auth_headers,
        json={"name": filename},
    )
    if resp.ok:
        return resp.json()

    # Fallback: use most recent existing project
    resp = requests.get(f"{API_BASE}/v1/iot-service/api/user/project", headers=auth_headers)
    if resp.ok:
        data = resp.json()
        projects = data.get("projects", data if isinstance(data, list) else [])
        if projects:
            proj = projects[-1]
            pid = proj.get("project_id", "")
            detail = requests.get(f"{API_BASE}/v1/iot-service/api/user/project/{pid}", headers=auth_headers)
            return detail.json() if detail.ok else proj
    return {}


def cloud_notify_upload(token: str, upload_ticket: str, filename: str = "") -> dict:
    """Notify Bambu's server that the S3 upload is complete and poll for confirmation."""
    auth_headers = {**SLICER_HEADERS, "Authorization": f"Bearer {token}"}
    notify_url = f"{API_BASE}/v1/iot-service/api/user/notification"

    # The "upload" field is a struct requiring at minimum:
    #   - origin_file_name (required)
    #   - ticket (required — variant 4 showed "upload.ticket is not set")
    # We try with all known fields, then progressively add more if needed.
    upload_struct = {
        "ticket": upload_ticket,
        "origin_file_name": filename,
    }

    put_resp = requests.put(
        notify_url,
        headers=auth_headers,
        json={"upload": upload_struct},
    )

    if not put_resp.ok:
        upload_struct_v2 = {
            "ticket": upload_ticket,
            "origin_file_name": filename,
            "status": "complete",
            "file_size": 0,
        }
        put_resp = requests.put(
            notify_url,
            headers=auth_headers,
            json={"upload": upload_struct_v2},
        )

    # Poll GET for confirmation
    for attempt in range(3):
        time.sleep(2)
        resp = requests.get(
            notify_url,
            headers=auth_headers,
            params={"action": "upload", "ticket": upload_ticket},
        )
        if resp.ok:
            return resp.json()
    return {}


def cloud_upload_cover(token: str, file_path: Path, plate_index: int = 1) -> str:
    """Extract plate thumbnail from 3mf and upload it to get a cover URL.

    3mf files are zip archives containing plate thumbnails at
    Metadata/plate_N.png or similar paths.
    """
    auth_headers = {**SLICER_HEADERS, "Authorization": f"Bearer {token}"}

    # Try to extract thumbnail from 3mf
    thumbnail_data = None
    thumbnail_name = None
    try:
        with zipfile.ZipFile(file_path, "r") as zf:
            # Try common thumbnail paths
            candidates = [
                f"Metadata/plate_{plate_index}.png",
                f"Metadata/top_{plate_index}.png",
                "Metadata/plate_1.png",
                "Metadata/top_1.png",
            ]
            # Also check for any png in Metadata/
            for name in zf.namelist():
                if name.startswith("Metadata/") and name.endswith(".png"):
                    if name not in candidates:
                        candidates.append(name)

            for candidate in candidates:
                if candidate in zf.namelist():
                    thumbnail_data = zf.read(candidate)
                    thumbnail_name = candidate.split("/")[-1]
                    print(f"  Extracted thumbnail: {candidate} ({len(thumbnail_data)} bytes)")
                    break
    except (zipfile.BadZipFile, KeyError):
        pass

    if not thumbnail_data:
        print("  No thumbnail found in 3mf")
        # Return a minimal 1x1 PNG data URI — probably won't work but worth trying
        return ""

    # Upload thumbnail using generic upload endpoint
    resp = requests.get(
        f"{API_BASE}/v1/iot-service/api/user/upload",
        headers=auth_headers,
        params={"filename": thumbnail_name, "size": len(thumbnail_data)},
    )
    if not resp.ok:
        print(f"  Failed to get thumbnail upload URL: {resp.status_code}")
        return ""

    upload_data = resp.json()
    urls = upload_data.get("urls", [])
    thumb_upload_url = None
    for entry in urls:
        if entry.get("type") == "filename":
            thumb_upload_url = entry.get("url")

    if not thumb_upload_url:
        print("  No thumbnail upload URL returned")
        return ""

    put_resp = requests.put(thumb_upload_url, data=thumbnail_data, headers={}, timeout=60)
    if put_resp.ok:
        # Return full signed URL — the server may need query params for access
        print(f"  Uploaded thumbnail: {thumb_upload_url[:120]}...")
        return thumb_upload_url

    print(f"  Thumbnail upload failed: {put_resp.status_code}")
    return ""


def cloud_create_task(
    token: str,
    device_id: str,
    filename: str,
    model_id: str,
    profile_id: str = "0",
    cover_url: str = "",
) -> dict:
    """Create a cloud print task. Returns task info including task_id and subtask_id."""
    from urllib.parse import urlparse, urlunparse

    # profileId must be an integer, not a string
    try:
        profile_id_int = int(profile_id)
    except (ValueError, TypeError):
        profile_id_int = 0

    task_headers = {**SLICER_HEADERS, "Authorization": f"Bearer {token}"}
    task_url = f"{API_BASE}/v1/user-service/my/task"

    # Try multiple cover URL formats.
    # Existing successful tasks use virtual-hosted S3 URL style:
    #   https://or-cloud-model-prod.s3.dualstack.us-west-2.amazonaws.com/private/...
    # But the profile endpoint returns path-style:
    #   https://s3.us-west-2.amazonaws.com/or-cloud-model-prod/private/...
    # Convert to the virtual-hosted format that the task endpoint expects.
    cover_variants = []
    if cover_url:
        # Convert path-style S3 URL to virtual-hosted dualstack format
        parsed = urlparse(cover_url)
        rewritten = cover_url
        # Match: https://s3.{region}.amazonaws.com/{bucket}/{key}
        path_style = re.match(
            r"https://s3\.([^.]+)\.amazonaws\.com/([^/]+)(/.*)",
            cover_url,
        )
        if path_style:
            region = path_style.group(1)
            bucket = path_style.group(2)
            key_and_params = path_style.group(3)
            # Reconstruct as virtual-hosted dualstack
            rewritten = f"https://{bucket}.s3.dualstack.{region}.amazonaws.com{key_and_params}"
            cover_variants.append(("dualstack", rewritten))

        # Also try the original signed URL and bare URL
        cover_variants.append(("signed", cover_url))
        parsed = urlparse(cover_url)
        bare_url = urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
        if bare_url != cover_url:
            cover_variants.append(("bare", bare_url))
        # Also try dualstack bare
        if path_style:
            parsed_rw = urlparse(rewritten)
            bare_rw = urlunparse((parsed_rw.scheme, parsed_rw.netloc, parsed_rw.path, "", "", ""))
            cover_variants.append(("dualstack-bare", bare_rw))

    if not cover_variants:
        cover_variants.append(("empty", ""))

    # Use the first (best) cover variant
    primary_cover = cover_variants[0][1] if cover_variants else ""

    # Two payload variants: one with extra fields from BambuStudio PrintParams,
    # one minimal matching the OpenBambuAPI docs.
    payloads = [
        # Variant A: Full payload with fields from BambuStudio PrintParams
        # (print_type, connection_type, bed_type, ams_mapping, etc.)
        {
            "deviceId": device_id,
            "title": filename,
            "modelId": model_id,
            "profileId": profile_id_int,
            "plateIndex": 1,
            "designId": 0,
            "cover": primary_cover,
            "amsDetailMapping": [],
            "mode": "cloud_file",
            "bedType": "auto",
            "jobType": 1,
            # Fields from BambuStudio PrintParams that we weren't sending before
            "printType": "from_normal",
            "connectionType": "cloud",
            "taskBedLeveling": True,
            "taskFlowCali": True,
            "taskVibrationCali": True,
            "taskLayerInspect": False,
            "taskRecordTimelapse": False,
            "taskUseAms": True,
            "taskBedType": "auto",
        },
        # Variant B: Minimal — just what OpenBambuAPI docs say
        {
            "modelId": model_id,
            "title": filename,
            "deviceId": device_id,
            "profileId": profile_id_int,
        },
        # Variant C: snake_case variant (some Bambu APIs use snake_case)
        {
            "device_id": device_id,
            "title": filename,
            "model_id": model_id,
            "profile_id": profile_id_int,
            "plate_index": 1,
            "design_id": 0,
            "cover": primary_cover,
            "print_type": "from_normal",
            "connection_type": "cloud",
        },
    ]

    for i, payload in enumerate(payloads):
        label = ["full+PrintParams", "minimal", "snake_case"][i]
        print(f"  POST {task_url} (variant {label})")
        print(f"  cover: {primary_cover[:100]}")
        print(f"  payload keys: {list(payload.keys())}")
        resp = requests.post(task_url, headers=task_headers, json=payload)
        body = resp.text[:500] if resp.text else "(empty)"
        print(f"  -> {resp.status_code}: {body}")
        # Show response headers for debugging
        for hdr in ["X-Request-Id", "X-Trace-Id"]:
            if resp.headers.get(hdr):
                print(f"  {hdr}: {resp.headers[hdr]}")

        if resp.ok:
            data = resp.json()
            print(f"  Task created: {json.dumps(data, indent=2)[:500]}")
            return data

    return {}


def cloud_upload_file(token: str, file_path: Path) -> str:
    """Upload a file to Bambu Cloud (S3) and return the file URL.

    Gets a signed S3 upload URL, PUTs the file data, and uploads
    the size metadata.
    """
    file_size = file_path.stat().st_size
    filename = file_path.name

    upload_endpoint = f"{API_BASE}/v1/iot-service/api/user/upload"
    auth_headers = {**SLICER_HEADERS, "Authorization": f"Bearer {token}"}
    params = {"filename": filename, "size": file_size}

    resp = requests.get(upload_endpoint, headers=auth_headers, params=params)
    resp.raise_for_status()
    upload_data = resp.json()

    # Response has a urls array with filename and size entries
    upload_url = upload_data.get("upload_url")
    size_url = None

    if not upload_url:
        urls = upload_data.get("urls", [])
        for entry in urls:
            if entry.get("type") == "filename":
                upload_url = entry.get("url")
            elif entry.get("type") == "size":
                size_url = entry.get("url")

    if not upload_url:
        raise RuntimeError(f"No upload URL returned: {upload_data}")

    # PUT the file to S3 (empty headers — signed URLs can fail
    # if extra headers are included that weren't part of the signature)
    file_content = file_path.read_bytes()
    put_resp = requests.put(upload_url, data=file_content, headers={}, timeout=300)
    put_resp.raise_for_status()

    # Upload size metadata if a size URL was provided
    if size_url:
        requests.put(
            size_url,
            data=str(file_size).encode(),
            headers={"Content-Type": "text/plain"},
            timeout=30,
        )

    return upload_url


# ---------------------------------------------------------------------------
# Bambu Cloud MQTT
# ---------------------------------------------------------------------------

MQTT_BROKER = "us.mqtt.bambulab.com"
MQTT_PORT = 8883

# ---------------------------------------------------------------------------
# X.509 Command Signing
#
# Bambu's cloud MQTT broker requires critical commands (print start, pause,
# resume, stop) to be signed with the Bambu Connect app's private key.
# The key was publicly extracted in January 2025 and is embedded in every
# copy of the Bambu Connect desktop app.
#
# Reference: https://hackaday.com/2025/01/19/bambu-connects-authentication-x-509-certificate-and-private-key-extracted/
# Implementation based on: https://github.com/schwarztim/bambu-mcp
# ---------------------------------------------------------------------------

BAMBU_CERT_ID = "GLOF3813734089-524a37c80000c6a6a274a47b3281"

BAMBU_PRIVATE_KEY_PEM = """\
-----BEGIN PRIVATE KEY-----
MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQDQNp2NfkajwcWH
PIqosa08P1ZwETPr1veZCMqieQxWtYw97wp+JCxX4yBrBcAwid7o7PHI9KQVzPRM
f0uXspaDUdSljrfJ/YwGEz7+GJz4+ml1UbWXBePyzXW1+N2hIGGn7BcNuA0v8rMY
uvVgiIIQNjLErgGcCWmMHLwsMMQ7LNprUZZKsSNB4HaQDH7cQZmYBN/O45np6l+K
VuLdzXdDpZcOM7bNO6smev822WPGDuKBo1iVfQbUe10X4dCNwkBR3QGpScVvg8gg
tRYZDYue/qc4Xaj806RZPttknWfxdvfZgoOmAiwnyQ5K3+mzNYHgQZAOC2ydkK4J
s+ZizK3lAgMBAAECggEAKwEcyXyrWmdLRQNcIDuSbD8ouzzSXIOp4BHQyH337nDQ
5nnY0PTns79VksU9TMktIS7PQZJF0brjOmmQU2SvcbAVG5y+mRmlMhwHhrPOuB4A
ahrWRrsQubV1+n/MRttJUEWS/WJmVuDp3NHAnI+VTYPkOHs4GeJXynik5PutjAr3
tYmr3kaw0Wo/hYAXTKsI/R5aenC7jH8ZSyVcZ/j+bOSH5sT5/JY122AYmkQOFE7s
JA0EfYJaJEwiuBWKOfRLQVEHhOFodUBZdGQcWeW3uFb88aYKN8QcKTO8/f6e4r8w
QojgK3QMj1zmfS7xid6XCOVa17ary2hZHAEPnjcigQKBgQDQnm4TlbVTsM+CbFUS
1rOIJRzPdnH3Y7x3IcmVKZt81eNktsdu56A4U6NEkFQqk4tVTT4TYja/hwgXmm6w
J+w0WwZd445Bxj8PmaEr6Z/NSMYbCsi8pRelKWmlIMwD2YhtY/1xXD37zpOgN8oQ
ryTKZR2gljbPxdfhKS7YerLp2wKBgQD/gJt3Ds69j1gMDLnnPctjmhsPRXh7PQ0e
E9lqgFkx/vNuCuyRs6ymic2rBZmkdlpjsTJFmz1bwOzIvSRoH6kp0Mfyo6why5kr
upDf7zz+hlvaFewme8aDeV3ex9Wvt73D66nwAy5ABOgn+66vZJeo0Iq/tnCwK3a/
evTL9BOzPwKBgEUi7AnziEc3Bl4Lttnqa08INZcPgs9grzmv6dVUF6J0Y8qhxFAd
1Pw1w5raVfpSMU/QrGzSFKC+iFECLgKVCHOFYwPEgQWNRKLP4BjkcMAgiP63QTU7
ZS2oHsnJp7Ly6YKPK5Pg5O3JVSU4t+91i7TDc+EfRwTuZQ/KjSrS5u4XAoGBAP06
v9reSDVELuWyb0Yqzrxm7k7ScbjjJ28aCTAvCTguEaKNHS7DP2jHx5mrMT35N1j7
NHIcjFG2AnhqTf0M9CJHlQR9B4tvON5ISHJJsNAq5jpd4/G4V2XTEiBNOxKvL1tQ
5NrGrD4zHs0R+25GarGcDwg3j7RrP4REHv9NZ4ENAoGAY7Nuz6xKu2XUwuZtJP7O
kjsoDS7bjP95ddrtsRq5vcVjJ04avnjsr+Se9WDA//t7+eSeHjm5eXD7u0NtdqZo
WtSm8pmWySOPXMn9QQmdzKHg1NOxer//f1KySVunX1vftTStjsZH7dRCtBEePcqg
z5Av6MmEFDojtwTqvEZuhBM=
-----END PRIVATE KEY-----"""

_private_key = serialization.load_pem_private_key(
    BAMBU_PRIVATE_KEY_PEM.encode(), password=None
)


def sign_command(command: dict) -> dict:
    """Sign an MQTT command with the Bambu Connect X.509 private key.

    Wraps the command with a header containing an RSA-SHA256 signature.
    The cloud broker validates this signature for critical operations
    (print start, pause, resume, stop).
    """
    message_bytes = json.dumps(command).encode("utf-8")

    signature = _private_key.sign(
        message_bytes,
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    signature_b64 = base64.b64encode(signature).decode("ascii")

    return {
        **command,
        "header": {
            "sign_ver": "v1.0",
            "sign_alg": "RSA_SHA256",
            "sign_string": signature_b64,
            "cert_id": BAMBU_CERT_ID,
            "payload_len": len(message_bytes),
        },
    }


class BambuCloudMQTT:
    """Minimal MQTT client for Bambu Cloud printer commands."""

    def __init__(self, user_id: str, access_token: str, device_id: str):
        self.user_id = user_id
        self.access_token = access_token
        self.device_id = device_id
        self._seq = 0
        self._connected = threading.Event()
        self._responses: list[dict] = []

        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"fabprint-test-{device_id[:8]}",
        )
        self.client.username_pw_set(f"u_{user_id}", access_token)
        self.client.tls_set(cert_reqs=ssl.CERT_REQUIRED, tls_version=ssl.PROTOCOL_TLS)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_disconnect = self._on_disconnect

    @property
    def request_topic(self) -> str:
        return f"device/{self.device_id}/request"

    @property
    def report_topic(self) -> str:
        return f"device/{self.device_id}/report"

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        if rc == 0:
            client.subscribe(self.report_topic)
            self._connected.set()
        else:
            print(f"  MQTT connection failed: rc={rc}")

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload)
            self._responses.append(payload)

            # Log ALL responses at debug level
            log.debug("<< %s", json.dumps(payload, indent=2)[:2000])

            # Log interesting responses
            if "print" in payload:
                p = payload["print"]
                cmd = p.get("command", "")
                result = p.get("result", "")
                reason = p.get("reason", "")

                # Always show project_file responses (even without result)
                if cmd == "project_file":
                    print(f"  << project_file response: {json.dumps(p)[:500]}")

                if result:
                    status = f"result={result}"
                    if reason:
                        status += f" reason={reason}"
                    print(f"  << {cmd}: {status}")
                # Show print progress
                mc_percent = p.get("mc_percent")
                gcode_state = p.get("gcode_state")
                if gcode_state:
                    extra = f" ({mc_percent}%)" if mc_percent is not None else ""
                    print(f"  << state: {gcode_state}{extra}")
                # Show upload progress (printer downloading the file)
                upload = p.get("upload")
                if upload:
                    print(f"  << upload: {upload}")
        except json.JSONDecodeError:
            pass

    def _on_disconnect(self, client, userdata, flags, rc, properties=None):
        self._connected.clear()
        if rc != 0:
            print(f"  MQTT disconnected unexpectedly: rc={rc}")

    def connect(self, timeout: float = 10.0):
        """Connect to the cloud MQTT broker."""
        self.client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
        self.client.loop_start()
        if not self._connected.wait(timeout):
            raise TimeoutError("MQTT connection timed out")

    def disconnect(self):
        self.client.loop_stop()
        self.client.disconnect()

    def _next_seq(self) -> str:
        self._seq += 1
        return str(self._seq)

    def _publish(self, command: dict):
        """Sign and publish a command."""
        signed = sign_command(command)
        payload = json.dumps(signed)
        log.debug(">> %s", payload)
        self.client.publish(self.request_topic, payload)

    # -- The four commands --------------------------------------------------

    def start_print(
        self,
        file_url: str,
        filename: str,
        task_id: str = "0",
        subtask_id: str = "0",
        project_id: str = "0",
        profile_id: str = "0",
        plate_index: int = 1,
        use_ams: bool = True,
        bed_levelling: bool = True,
        flow_cali: bool = True,
        vibration_cali: bool = True,
        timelapse: bool = False,
        md5: str = "",
    ):
        """Send project_file command to start a print."""
        cmd = {
            "print": {
                "sequence_id": self._next_seq(),
                "command": "project_file",
                "param": f"Metadata/plate_{plate_index}.gcode",
                "project_id": project_id,
                "profile_id": profile_id,
                "task_id": task_id,
                "subtask_id": subtask_id,
                "subtask_name": filename,
                "file": "",
                "url": file_url,
                "md5": md5,
                "timelapse": timelapse,
                "bed_type": "auto",
                "bed_levelling": bed_levelling,
                "flow_cali": flow_cali,
                "vibration_cali": vibration_cali,
                "layer_inspect": False,
                "ams_mapping": [0, 1, 2, 3],
                "use_ams": use_ams,
            }
        }
        print(f"  >> project_file: task={task_id} url={file_url[:80]}...")
        self._publish(cmd)

    def pause_print(self):
        """Pause the current print."""
        cmd = {"print": {"sequence_id": self._next_seq(), "command": "pause", "param": ""}}
        print("  >> pause")
        self._publish(cmd)

    def resume_print(self):
        """Resume a paused print."""
        cmd = {"print": {"sequence_id": self._next_seq(), "command": "resume", "param": ""}}
        print("  >> resume")
        self._publish(cmd)

    def stop_print(self):
        """Stop/cancel the current print."""
        cmd = {"print": {"sequence_id": self._next_seq(), "command": "stop", "param": ""}}
        print("  >> stop")
        self._publish(cmd)

    def request_status(self):
        """Request full printer status (pushall)."""
        cmd = {"pushing": {"sequence_id": self._next_seq(), "command": "pushall", "version": 1, "push_target": 1}}
        self._publish(cmd)


# ---------------------------------------------------------------------------
# LAN Mode (FTPS + Local MQTT)
#
# Uses implicit FTPS (port 990) to upload .3mf to the printer's SD card,
# then sends a project_file MQTT command over the local broker to start
# the print. This is the proven approach used by all successful third-party
# tools (Home Assistant integration, bambu-ftp-and-print, bambu-mcp, etc.)
#
# Requirements:
#   - Printer IP address (BAMBU_PRINTER_IP or --printer-ip)
#   - LAN access code (BAMBU_ACCESS_CODE or --access-code)
#   - Printer serial number (BAMBU_SERIAL or --serial)
#   - Developer Mode or LAN Mode enabled on the printer
# ---------------------------------------------------------------------------


class ImplicitFTPS(ftplib.FTP_TLS):
    """FTP_TLS subclass that uses implicit TLS (port 990).

    Standard FTP_TLS does explicit TLS (AUTH TLS after connect).
    Bambu printers use implicit TLS where the connection starts encrypted.
    Based on the pybambu/Home Assistant integration approach.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def connect(self, host="", port=0, timeout=-999, source_address=None):
        """Connect with implicit TLS — wrap socket in SSL before FTP handshake."""
        if host:
            self.host = host
        if port:
            self.port = port
        if timeout != -999:
            self.timeout = timeout
        if source_address is not None:
            self.source_address = source_address

        import socket
        self.sock = socket.create_connection(
            (self.host, self.port), self.timeout, self.source_address
        )
        self.af = self.sock.family
        # Wrap in SSL immediately (implicit TLS)
        self.sock = self.context.wrap_socket(self.sock, server_hostname=self.host)
        self.file = self.sock.makefile("r", encoding=self.encoding)
        self.welcome = self.getresp()
        return self.welcome


def lan_upload_file(ip: str, access_code: str, file_path: Path) -> str:
    """Upload a .3mf file to the printer via implicit FTPS (port 990).

    Returns the remote filename on the printer's SD card.
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # Printer uses self-signed cert

    ftp = ImplicitFTPS(context=ctx)
    ftp.connect(host=ip, port=990, timeout=30)
    ftp.login(user="bblp", passwd=access_code)

    # Enable data connection protection
    ftp.prot_p()

    remote_filename = file_path.name
    print(f"  Uploading {remote_filename} ({file_path.stat().st_size} bytes)...")

    with open(file_path, "rb") as f:
        ftp.storbinary(f"STOR {remote_filename}", f)

    print(f"  Upload complete: {remote_filename}")
    ftp.quit()
    return remote_filename


class BambuLanMQTT:
    """MQTT client for local Bambu printer control."""

    def __init__(self, ip: str, access_code: str, serial: str):
        self.ip = ip
        self.access_code = access_code
        self.serial = serial
        self._seq = 0
        self._connected = threading.Event()
        self._responses: list[dict] = []

        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"fabprint-lan-{serial[:8]}",
        )
        self.client.username_pw_set("bblp", access_code)

        # TLS with no cert verification (printer uses self-signed cert)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        self.client.tls_set_context(ctx)

        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_disconnect = self._on_disconnect

    @property
    def request_topic(self) -> str:
        return f"device/{self.serial}/request"

    @property
    def report_topic(self) -> str:
        return f"device/{self.serial}/report"

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        if rc == 0:
            print(f"  MQTT connected to {self.ip}")
            client.subscribe(self.report_topic)
            self._connected.set()
        else:
            print(f"  MQTT connection failed: rc={rc}")

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload)
            self._responses.append(payload)

            if "print" in payload:
                p = payload["print"]
                cmd = p.get("command", "")
                result = p.get("result", "")
                reason = p.get("reason", "")

                if cmd == "project_file":
                    print(f"  << project_file response: {json.dumps(p)[:500]}")

                if result:
                    status = f"result={result}"
                    if reason:
                        status += f" reason={reason}"
                    print(f"  << {cmd}: {status}")

                mc_percent = p.get("mc_percent")
                gcode_state = p.get("gcode_state")
                if gcode_state:
                    extra = f" ({mc_percent}%)" if mc_percent is not None else ""
                    print(f"  << state: {gcode_state}{extra}")
        except json.JSONDecodeError:
            pass

    def _on_disconnect(self, client, userdata, flags, rc, properties=None):
        self._connected.clear()
        if rc != 0:
            print(f"  MQTT disconnected unexpectedly: rc={rc}")

    def connect(self, timeout: float = 10.0):
        print(f"  Connecting MQTT to {self.ip}:8883...")
        self.client.connect(self.ip, 8883, keepalive=60)
        self.client.loop_start()
        if not self._connected.wait(timeout):
            raise TimeoutError("MQTT connection timed out")

    def disconnect(self):
        self.client.loop_stop()
        self.client.disconnect()
        print("  MQTT disconnected")

    def _next_seq(self) -> str:
        self._seq += 1
        return str(self._seq)

    def _publish(self, command: dict):
        """Publish a command (no signing needed for LAN mode)."""
        payload = json.dumps(command)
        log.debug(">> %s", payload)
        self.client.publish(self.request_topic, payload)

    def start_print(
        self,
        filename: str,
        plate_index: int = 1,
        use_ams: bool = True,
        bed_levelling: bool = True,
        flow_cali: bool = True,
        vibration_cali: bool = True,
        timelapse: bool = False,
    ):
        """Send project_file to start a print from the printer's SD card."""
        cmd = {
            "print": {
                "sequence_id": self._next_seq(),
                "command": "project_file",
                "param": f"Metadata/plate_{plate_index}.gcode",
                "project_id": "0",
                "profile_id": "0",
                "task_id": "0",
                "subtask_id": "0",
                "subtask_name": filename,
                "file": "",
                "url": f"ftp://{filename}",
                "md5": "",
                "timelapse": timelapse,
                "bed_type": "auto",
                "bed_levelling": bed_levelling,
                "flow_cali": flow_cali,
                "vibration_cali": vibration_cali,
                "layer_inspect": True,
                "ams_mapping": [0, 1, 2, 3],
                "use_ams": use_ams,
            }
        }
        print(f"  >> start_print: {filename}")
        print(f"  >> url: ftp://{filename}")
        self._publish(cmd)

    def pause_print(self):
        cmd = {"print": {"sequence_id": self._next_seq(), "command": "pause", "param": ""}}
        print("  >> pause")
        self._publish(cmd)

    def resume_print(self):
        cmd = {"print": {"sequence_id": self._next_seq(), "command": "resume", "param": ""}}
        print("  >> resume")
        self._publish(cmd)

    def stop_print(self):
        cmd = {"print": {"sequence_id": self._next_seq(), "command": "stop", "param": ""}}
        print("  >> stop")
        self._publish(cmd)

    def request_status(self):
        cmd = {"pushing": {"sequence_id": self._next_seq(), "command": "pushall", "version": 1, "push_target": 1}}
        print("  >> pushall (request status)")
        self._publish(cmd)


def lan_main(args):
    """LAN mode: FTPS upload + local MQTT print trigger."""
    printer_ip = args.printer_ip or os.environ.get("BAMBU_PRINTER_IP")
    access_code = args.access_code or os.environ.get("BAMBU_ACCESS_CODE")
    serial = args.serial or os.environ.get("BAMBU_SERIAL")

    if not printer_ip or not access_code or not serial:
        print("Error: LAN mode requires --printer-ip, --access-code, --serial")
        print("  (or set BAMBU_PRINTER_IP, BAMBU_ACCESS_CODE, BAMBU_SERIAL env vars)")
        sys.exit(1)

    print(f"\n=== LAN Mode ===")
    print(f"  Printer: {printer_ip}")
    print(f"  Serial: {serial}")

    if args.status_only:
        print(f"\n[1] Connecting MQTT to printer...")
        mqttc = BambuLanMQTT(printer_ip, access_code, serial)
        try:
            mqttc.connect()
            print(f"\n[2] Requesting status...")
            mqttc.request_status()
            time.sleep(5)
        finally:
            mqttc.disconnect()
        return

    if not args.file:
        print("Error: Specify a .3mf file to print")
        sys.exit(1)

    file_path = Path(args.file)
    if not file_path.exists():
        print(f"Error: File not found: {file_path}")
        sys.exit(1)

    # Step 1: Upload via FTPS
    print(f"\n[1] Uploading {file_path.name} via FTPS...")
    remote_filename = lan_upload_file(printer_ip, access_code, file_path)

    # Step 2: Connect local MQTT and start print
    print(f"\n[2] Connecting local MQTT...")
    mqttc = BambuLanMQTT(printer_ip, access_code, serial)
    try:
        mqttc.connect()

        print(f"\n[3] Starting print via local MQTT...")
        mqttc.start_print(
            filename=remote_filename,
            use_ams=args.use_ams,
        )

        print("  Waiting for response (10s)...")
        time.sleep(10)

        mqttc.request_status()
        time.sleep(5)

        if args.interactive:
            print("\n--- Interactive mode ---")
            print("Commands: [p]ause  [r]esume  [s]top  [t]atus  [q]uit\n")
            while True:
                try:
                    cmd = input("> ").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    break
                if cmd in ("p", "pause"):
                    mqttc.pause_print()
                elif cmd in ("r", "resume"):
                    mqttc.resume_print()
                elif cmd in ("s", "stop"):
                    mqttc.stop_print()
                elif cmd in ("t", "status"):
                    mqttc.request_status()
                elif cmd in ("q", "quit"):
                    break
                else:
                    print("Unknown command. Use p/r/s/t/q")
                time.sleep(1)
    except KeyboardInterrupt:
        print("\n  Interrupted")
    finally:
        mqttc.disconnect()

    print("\nDone.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def select_device(devices: list[dict]) -> dict:
    """Let user pick a device if multiple are bound."""
    if len(devices) == 1:
        return devices[0]

    print("\nAvailable printers:")
    for i, d in enumerate(devices):
        name = d.get("name", "unnamed")
        dev_id = d.get("dev_id", "?")
        online = "online" if d.get("online") else "offline"
        model = d.get("dev_product_name", d.get("dev_model_name", "?"))
        print(f"  [{i}] {name} ({model}) — {dev_id} [{online}]")

    while True:
        try:
            idx = int(input("\nSelect printer [0]: ") or "0")
            return devices[idx]
        except (ValueError, IndexError):
            print("Invalid selection, try again")


def interactive_loop(mqttc: BambuCloudMQTT):
    """Interactive command loop for pause/resume/stop."""
    print("\n--- Interactive mode ---")
    print("Commands: [p]ause  [r]esume  [s]top  [t]atus  [q]uit\n")

    while True:
        try:
            cmd = input("> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            break

        if cmd in ("p", "pause"):
            mqttc.pause_print()
        elif cmd in ("r", "resume"):
            mqttc.resume_print()
        elif cmd in ("s", "stop"):
            mqttc.stop_print()
        elif cmd in ("t", "status"):
            mqttc.request_status()
        elif cmd in ("q", "quit"):
            break
        else:
            print("Unknown command. Use p/r/s/t/q")

        # Give MQTT time to receive response
        time.sleep(1)


def main():
    parser = argparse.ArgumentParser(description="Test Bambu Cloud print via MQTT")
    parser.add_argument(
        "file",
        nargs="?",
        help="Path to .gcode.3mf or .3mf file to print",
    )
    parser.add_argument(
        "--interactive", "-i",
        action="store_true",
        help="Enter interactive mode after starting print (pause/resume/stop)",
    )
    parser.add_argument(
        "--status-only",
        action="store_true",
        help="Just connect and request printer status, don't print",
    )
    parser.add_argument(
        "--no-upload",
        action="store_true",
        help="Skip upload — use file_url from a previous upload",
    )
    parser.add_argument(
        "--file-url",
        help="Pre-existing cloud file URL (skip upload)",
    )
    parser.add_argument(
        "--use-ams",
        action="store_true",
        help="Enable AMS filament system",
    )
    parser.add_argument(
        "--lan",
        action="store_true",
        help="Use LAN mode (FTPS upload + local MQTT) instead of cloud",
    )
    parser.add_argument(
        "--printer-ip",
        help="Printer IP for LAN mode (or set BAMBU_PRINTER_IP env var)",
    )
    parser.add_argument(
        "--access-code",
        help="Printer access code for LAN mode (or set BAMBU_ACCESS_CODE env var)",
    )
    parser.add_argument(
        "--serial",
        help="Printer serial for LAN mode (or set BAMBU_SERIAL env var)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO, format="%(message)s")

    # --- LAN mode ---
    if args.lan:
        lan_main(args)
        return

    # --- Credentials ---
    email = os.environ.get("BAMBU_EMAIL")
    password = os.environ.get("BAMBU_PASSWORD")
    if not email or not password:
        print("Error: Set BAMBU_EMAIL and BAMBU_PASSWORD environment variables")
        sys.exit(1)

    # --- Step 1: Login ---
    print("[1] Logging in...", end=" ")
    auth = cloud_login_with_cache(email, password)
    print(f"OK (user {auth['user_id']})")

    # --- Step 2: List devices ---
    print("[2] Fetching devices...", end=" ")
    devices = cloud_get_devices(auth["access_token"])
    if not devices:
        print("FAIL — no printers found!")
        sys.exit(1)

    device = select_device(devices)
    device_id = device["dev_id"]
    device_name = device.get("name", device_id)
    online = "online" if device.get("online") else "offline"
    print(f"{device_name} ({device_id}) [{online}]")

    # --- Step 3: Create project + Upload file ---
    file_url = args.file_url
    filename = "unknown.3mf"
    model_id = "0"
    profile_id = "0"
    project_id = "0"
    cover_url = ""
    download_md5 = ""
    task_id = "0"
    subtask_id = "0"

    if args.file and not args.no_upload and not file_url:
        file_path = Path(args.file)
        if not file_path.exists():
            print(f"  Error: File not found: {file_path}")
            sys.exit(1)
        filename = file_path.name

        # Step 3a: Create a project to get model_id and upload_ticket
        print(f"[3a] Creating project...", end=" ")
        project_data = cloud_create_project(auth["access_token"], filename)
        model_id = str(project_data.get("model_id", "0"))
        upload_ticket = project_data.get("upload_ticket", "")
        project_id = str(project_data.get("project_id", "0"))
        project_upload_url = project_data.get("upload_url", "")
        if project_data.get("profile_id"):
            profile_id = str(project_data["profile_id"])
        print(f"project={project_id} model={model_id} profile={profile_id}")

        # Step 3b: Upload file to S3
        print(f"[3b] Uploading {filename}...", end=" ")
        if project_upload_url:
            file_content = file_path.read_bytes()
            put_resp = requests.put(project_upload_url, data=file_content, headers={}, timeout=300)
            put_resp.raise_for_status()
            file_url = project_upload_url
            print(f"OK ({len(file_content)} bytes)")
        else:
            file_url = cloud_upload_file(auth["access_token"], file_path)
            print("OK")

        # Step 3c: Notify server that upload is complete
        print(f"[3c] Upload notification...", end=" ")
        if upload_ticket:
            notify_data = cloud_notify_upload(auth["access_token"], upload_ticket, filename)
            if notify_data.get("model_id"):
                model_id = str(notify_data["model_id"])
            print("OK")
        else:
            print("skipped (no ticket)")
            time.sleep(3)

        # Step 3d: Poll project details / fetch profile
        print(f"[3d] Waiting for server processing...", end=" ", flush=True)
        auth_headers = {**SLICER_HEADERS, "Authorization": f"Bearer {auth['access_token']}"}
        cover_url = ""
        download_url = ""
        download_md5 = ""
        profile_id_from_server = ""

        for poll in range(15):
            proj_resp = requests.get(
                f"{API_BASE}/v1/iot-service/api/user/project/{project_id}",
                headers=auth_headers,
            )
            if proj_resp.ok:
                profiles = proj_resp.json().get("profiles", []) or []
                if profiles:
                    prof = profiles[0]
                    if prof.get("url"):
                        download_url = prof["url"]
                        download_md5 = prof.get("md5", "")
                        if prof.get("profile_id"):
                            profile_id_from_server = str(prof["profile_id"])
                        context = prof.get("context", {}) or {}
                        plates = context.get("plates", []) or []
                        if plates:
                            cover_url = (plates[0].get("thumbnail", {}) or {}).get("url", "")
                        break
            print(".", end="", flush=True)
            time.sleep(2)

        if profile_id_from_server:
            profile_id = profile_id_from_server

        # Fallback: profile endpoint directly
        if not download_url and profile_id != "0" and model_id != "0":
            prof_resp = requests.get(
                f"{API_BASE}/v1/iot-service/api/user/profile/{profile_id}",
                headers=auth_headers,
                params={"model_id": model_id},
            )
            if prof_resp.ok:
                prof_data = prof_resp.json()
                download_url = prof_data.get("url") or ""
                download_md5 = prof_data.get("md5") or ""
                context = prof_data.get("context", {}) or {}
                plates = context.get("plates", []) or []
                if plates and not cover_url:
                    cover_url = (plates[0].get("thumbnail", {}) or {}).get("url", "")

        if download_url:
            print(f" OK (profile={profile_id}, md5={download_md5[:12]}...)")
        else:
            print(f" no download URL found")

        # Step 3e: PATCH project (discovered from BambuStudio error codes)
        # The proprietary bambu_networking library PATCHes the project after
        # upload notification. This may be required before task creation.
        print(f"\n[3e] PATCH project (update after upload)...")
        patch_payloads = [
            # Variant 1: minimal — just mark status
            {"name": filename, "status": "uploaded"},
            # Variant 2: with profile info
            {"name": filename, "profile_id": int(profile_id) if profile_id.isdigit() else 0},
            # Variant 3: with model_id
            {"model_id": model_id, "name": filename},
        ]
        for i, patch_payload in enumerate(patch_payloads):
            resp = requests.patch(
                f"{API_BASE}/v1/iot-service/api/user/project/{project_id}",
                headers=auth_headers,
                json=patch_payload,
            )
            body = resp.text[:500] if resp.text else "(empty)"
            print(f"  PATCH variant {i+1}: {resp.status_code} — {body}")
            if resp.ok:
                print(f"  PATCH succeeded with variant {i+1}!")
                break

        # Step 3f: Get my settings (error code -2090 shows this step exists)
        print(f"\n[3f] GET my/setting...")
        resp = requests.get(
            f"{API_BASE}/v1/user-service/my/setting",
            headers=auth_headers,
        )
        body = resp.text[:500] if resp.text else "(empty)"
        print(f"  -> {resp.status_code}: {body}")

        # Step 3g: List cloud files to get correct file_id
        # The coelacant1 library shows file_id != model_id
        print(f"\n[3g] Listing cloud files to get file_id...")
        file_id = ""
        # Try files endpoint
        resp = requests.get(
            f"{API_BASE}/v1/iot-service/api/user/files",
            headers=auth_headers,
        )
        body = resp.text[:1000] if resp.text else "(empty)"
        print(f"  GET /user/files: {resp.status_code} — {body}")
        if resp.ok:
            try:
                files_data = resp.json()
                files_list = files_data if isinstance(files_data, list) else files_data.get("files", [])
                for f in files_list:
                    f_name = f.get("name", "") or f.get("file_name", "")
                    f_id = f.get("file_id", "") or f.get("id", "")
                    if f_name == filename and f_id:
                        file_id = str(f_id)
                        print(f"  Found file_id={file_id} for {filename}")
                        break
            except Exception as e:
                print(f"  Error parsing files: {e}")

        # Step 3h: Try to trigger cloud print via multiple endpoints
        print(f"\n[3h] Trying to trigger cloud print...")

        # Use profile cover URL for task creation attempts
        task_cover = cover_url if cover_url else ""

        # Attempt 1: POST /v1/user-service/my/task with print_type field
        # BambuStudio sets params.print_type = "from_normal" for cloud prints
        print(f"\n  [1] POST /v1/user-service/my/task (with print_type)")
        task_data = cloud_create_task(
            auth["access_token"], device_id, filename, model_id, profile_id, task_cover
        )
        if task_data.get("id"):
            task_id = str(task_data["id"])
            subtask_id = str(task_data.get("subtask_id", "0"))
            print(f"  SUCCESS! task_id={task_id}, subtask_id={subtask_id}")

        # Attempt 2: POST /v1/iot-service/api/user/print (known 405 — kept for retry)
        if task_id == "0":
            print_url = f"{API_BASE}/v1/iot-service/api/user/print"
            print_file_url = download_url or file_url or ""

            for variant_label, payload in [
                ("snake_case", {"device_id": device_id, "file_id": model_id,
                                "file_name": filename, "file_url": print_file_url, "settings": {}}),
                ("camelCase", {"deviceId": device_id, "fileId": model_id,
                               "fileName": filename, "fileUrl": print_file_url}),
                ("full", {"device_id": device_id, "file_id": model_id, "file_name": filename,
                          "file_url": print_file_url, "model_id": model_id,
                          "profile_id": int(profile_id) if profile_id.isdigit() else 0,
                          "project_id": project_id, "plate_index": 1}),
            ]:
                if task_id != "0":
                    break
                resp = requests.post(print_url, headers=auth_headers, json=payload)
                if resp.ok:
                    data = resp.json()
                    job_id = data.get("data", {}).get("job_id") or data.get("job_id") or data.get("id")
                    if job_id:
                        task_id = str(job_id)
                        print(f"  POST /user/print ({variant_label}): SUCCESS! task_id={task_id}")
            else:
                print(f"  POST /user/print: {resp.status_code} (all 3 variants)")

        # GET /user/print — check device status
        resp = requests.get(
            f"{API_BASE}/v1/iot-service/api/user/print",
            headers=auth_headers,
            params={"device_id": device_id},
        )
        if resp.ok:
            print(f"  GET /user/print: {resp.status_code} (device info OK)")

        # Use the profile download URL for MQTT (not the S3 upload URL)
        # Rewrite to dualstack virtual-hosted format if it's path-style
        if download_url:
            m = re.match(
                r"https://s3\.([^.]+)\.amazonaws\.com/([^/]+)(/.*)",
                download_url,
            )
            if m:
                region, bucket, key_params = m.group(1), m.group(2), m.group(3)
                download_url = f"https://{bucket}.s3.dualstack.{region}.amazonaws.com{key_params}"
            file_url = download_url

    elif file_url:
        filename = Path(file_url).name
        print(f"\n[3] Using existing URL: {file_url}")
    elif not args.status_only:
        print("\n[3] No file specified — skipping upload")

    # --- Step 4: Connect MQTT ---
    print(f"[4] MQTT...", end=" ")
    mqttc = BambuCloudMQTT(auth["user_id"], auth["access_token"], device_id)
    try:
        mqttc.connect()
        print("connected")

        if args.status_only:
            mqttc.request_status()
            time.sleep(5)
            return

        if file_url:
            # --- Step 5: MQTT project_file commands ---
            print(f"[5] MQTT project_file (task={task_id}, project={project_id}, profile={profile_id})")
            mqttc.start_print(
                file_url=file_url,
                filename=filename,
                task_id=task_id,
                subtask_id=subtask_id,
                project_id=project_id,
                profile_id=profile_id,
                use_ams=args.use_ams,
                md5=download_md5,
            )
            time.sleep(10)
            mqttc.request_status()
            time.sleep(5)

            # Retry with fake task_id if real one wasn't obtained
            if task_id == "0":
                import random
                fake_task_id = str(random.randint(100000000, 999999999))
                print(f"[5b] MQTT retry (fake task_id={fake_task_id})")
                mqttc.start_print(
                    file_url=file_url,
                    filename=filename,
                    task_id=fake_task_id,
                    subtask_id=fake_task_id,
                    project_id=project_id,
                    profile_id=profile_id,
                    use_ams=args.use_ams,
                    md5=download_md5,
                )
                time.sleep(10)
                mqttc.request_status()
                time.sleep(5)

            if args.interactive:
                interactive_loop(mqttc)
        else:
            print("[5] No file to print.")

    except KeyboardInterrupt:
        print("\n  Interrupted")
    finally:
        mqttc.disconnect()

    print("\nDone.")


if __name__ == "__main__":
    main()
