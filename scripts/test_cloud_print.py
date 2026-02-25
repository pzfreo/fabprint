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

    print(f"  Login successful (token: {token[:20]}...)")

    # Save token for reuse
    token_file = Path.home() / ".bambu_cloud_token"
    token_file.write_text(json.dumps({"token": token, "email": email}))
    token_file.chmod(0o600)
    print(f"  Token saved to {token_file}")

    return _get_user_info(token)


def cloud_login_with_cache(email: str, password: str) -> dict:
    """Login using cached token if available, falling back to fresh login."""
    token_file = Path.home() / ".bambu_cloud_token"
    if token_file.exists():
        try:
            cached = json.loads(token_file.read_text())
            if cached.get("email") == email and cached.get("token"):
                print("  Trying cached token...")
                info = _get_user_info(cached["token"])
                print(f"  Cached token valid (user {info['user_id']})")
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

    # Try creating a new project
    resp = requests.post(
        f"{API_BASE}/v1/iot-service/api/user/project",
        headers=auth_headers,
        json={"name": filename},
    )
    print(f"  Create project response: {resp.status_code}")
    print(f"  Body: {resp.text[:1000]}")

    if resp.ok:
        data = resp.json()
        project_id = data.get("project_id", "")
        model_id = data.get("model_id", "")
        upload_ticket = data.get("upload_ticket", "")
        print(f"  project_id={project_id}, model_id={model_id}, upload_ticket={upload_ticket}")
        return data

    # If POST isn't supported, try listing existing projects
    print("  Falling back to project listing...")
    resp = requests.get(
        f"{API_BASE}/v1/iot-service/api/user/project",
        headers=auth_headers,
    )
    print(f"  List projects response: {resp.status_code}")
    print(f"  Body: {resp.text[:1000]}")
    if resp.ok:
        data = resp.json()
        projects = data.get("projects", [])
        if isinstance(data, list):
            projects = data
        if projects:
            # Use the most recent project
            proj = projects[-1]
            project_id = proj.get("project_id", "")
            print(f"  Using existing project: {project_id}")
            # Fetch full project details for upload_ticket
            detail_resp = requests.get(
                f"{API_BASE}/v1/iot-service/api/user/project/{project_id}",
                headers=auth_headers,
            )
            if detail_resp.ok:
                return detail_resp.json()
            return proj
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
    print(f"  PUT notification: {put_resp.status_code} — {put_resp.text[:500]}")

    if not put_resp.ok:
        # If that didn't work, try adding more fields
        upload_struct_v2 = {
            "ticket": upload_ticket,
            "origin_file_name": filename,
            "status": "complete",
            "file_size": 0,
        }
        put_resp2 = requests.put(
            notify_url,
            headers=auth_headers,
            json={"upload": upload_struct_v2},
        )
        print(f"  PUT v2: {put_resp2.status_code} — {put_resp2.text[:500]}")

    # Poll GET for confirmation
    for attempt in range(3):
        time.sleep(2)
        resp = requests.get(
            notify_url,
            headers=auth_headers,
            params={"action": "upload", "ticket": upload_ticket},
        )
        print(f"  GET poll {attempt + 1}/3: {resp.status_code} — {resp.text[:300]}")

        if resp.ok:
            data = resp.json()
            return data

    print("  Notification not confirmed — continuing to task creation anyway")
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
    # profileId must be an integer, not a string
    try:
        profile_id_int = int(profile_id)
    except (ValueError, TypeError):
        profile_id_int = 0

    task_headers = {**SLICER_HEADERS, "Authorization": f"Bearer {token}"}
    task_url = f"{API_BASE}/v1/user-service/my/task"

    payload = {
        "deviceId": device_id,
        "title": filename,
        "modelId": model_id,
        "profileId": profile_id_int,
        "plateIndex": 1,
        "designId": 0,
        "cover": cover_url,
        "amsDetailMapping": [],
        "mode": "cloud_file",
    }

    print(f"  POST {task_url}")
    print(f"  Payload: {json.dumps(payload)[:500]}")
    resp = requests.post(task_url, headers=task_headers, json=payload)
    status = resp.status_code
    body = resp.text[:500] if resp.text else "(empty)"
    print(f"  -> {status}: {body}")

    if resp.ok:
        data = resp.json()
        print(f"  Task created: {json.dumps(data, indent=2)[:500]}")
        return data

    # If it fails, also try without designId (it may not be needed)
    print("  Retrying without designId...")
    payload2 = {k: v for k, v in payload.items() if k != "designId"}
    resp2 = requests.post(task_url, headers=task_headers, json=payload2)
    body2 = resp2.text[:500] if resp2.text else "(empty)"
    print(f"  -> {resp2.status_code}: {body2}")
    if resp2.ok:
        return resp2.json()

    return {}


def cloud_upload_file(token: str, file_path: Path) -> str:
    """Upload a file to Bambu Cloud (S3) and return the file URL.

    Gets a signed S3 upload URL, PUTs the file data, and uploads
    the size metadata.
    """
    file_size = file_path.stat().st_size
    filename = file_path.name

    # Get a signed upload URL
    upload_endpoint = f"{API_BASE}/v1/iot-service/api/user/upload"
    auth_headers = {**SLICER_HEADERS, "Authorization": f"Bearer {token}"}
    params = {"filename": filename, "size": file_size}
    print(f"  GET {upload_endpoint}")
    print(f"  params: {params}")

    resp = requests.get(upload_endpoint, headers=auth_headers, params=params)
    print(f"  Response: {resp.status_code}")
    if resp.status_code != 200:
        print(f"  Body: {resp.text[:500]}")
    resp.raise_for_status()
    upload_data = resp.json()
    print(f"  Upload response: {json.dumps(upload_data, indent=2)[:1000]}")

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

    print(f"  Uploaded {filename} ({file_size} bytes)")

    # Return the full URL including query params
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
            print(f"  MQTT connected to {MQTT_BROKER}")
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
        print(f"  Connecting MQTT to {MQTT_BROKER}:{MQTT_PORT}...")
        self.client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
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
        use_ams: bool = False,
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
                "ams_mapping": "",
                "use_ams": use_ams,
            }
        }
        print(f"  >> start_print: {filename}")
        print(f"  >> url: {file_url[:100]}...")
        print(f"  >> task_id={task_id}, subtask_id={subtask_id}")
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
        print("  >> pushall (request status)")
        self._publish(cmd)


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
        "--verbose", "-v",
        action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO, format="%(message)s")

    # --- Credentials ---
    email = os.environ.get("BAMBU_EMAIL")
    password = os.environ.get("BAMBU_PASSWORD")
    if not email or not password:
        print("Error: Set BAMBU_EMAIL and BAMBU_PASSWORD environment variables")
        sys.exit(1)

    # --- Step 1: Login ---
    print("\n[1] Logging in...")
    auth = cloud_login_with_cache(email, password)
    print(f"  Logged in as user {auth['user_id']}")

    # --- Step 2: List devices ---
    print("\n[2] Fetching devices...")
    devices = cloud_get_devices(auth["access_token"])
    if not devices:
        print("  No printers found on this account!")
        sys.exit(1)

    for d in devices:
        name = d.get("name", "unnamed")
        dev_id = d.get("dev_id", "?")
        online = "online" if d.get("online") else "offline"
        model = d.get("dev_product_name", d.get("dev_model_name", "?"))
        print(f"  {name} ({model}) — {dev_id} [{online}]")

    device = select_device(devices)
    device_id = device["dev_id"]
    device_name = device.get("name", device_id)
    print(f"  Selected: {device_name}")

    # --- Step 3: Create project + Upload file ---
    file_url = args.file_url
    filename = "unknown.3mf"
    model_id = "0"
    profile_id = "0"
    project_id = "0"
    cover_url = ""
    download_md5 = ""

    if args.file and not args.no_upload and not file_url:
        file_path = Path(args.file)
        if not file_path.exists():
            print(f"  Error: File not found: {file_path}")
            sys.exit(1)
        filename = file_path.name

        # Step 3a: Create a project to get model_id and upload_ticket
        print(f"\n[3a] Creating cloud project for {filename}...")
        project_data = cloud_create_project(auth["access_token"], filename)
        model_id = str(project_data.get("model_id", "0"))
        upload_ticket = project_data.get("upload_ticket", "")
        project_id = str(project_data.get("project_id", "0"))
        project_upload_url = project_data.get("upload_url", "")
        if project_data.get("profile_id"):
            profile_id = str(project_data["profile_id"])
        print(f"  model_id={model_id}, project_id={project_id}, profile_id={profile_id}")
        print(f"  ticket={upload_ticket}")
        if project_upload_url:
            print(f"  project upload_url: {project_upload_url[:120]}...")

        # Step 3b: Upload file to S3
        # Use the project's upload_url if available (uploads to models/ path),
        # otherwise fall back to the generic upload endpoint (filename/ path)
        if project_upload_url:
            print(f"\n[3b] Uploading {filename} to project S3 URL...")
            file_content = file_path.read_bytes()
            put_resp = requests.put(project_upload_url, data=file_content, headers={}, timeout=300)
            put_resp.raise_for_status()
            file_url = project_upload_url
            print(f"  Uploaded {filename} ({len(file_content)} bytes) to project URL")
        else:
            print(f"\n[3b] Uploading {filename} to S3 (generic)...")
            file_url = cloud_upload_file(auth["access_token"], file_path)
        print(f"  URL: {file_url[:120]}...")

        # Step 3c: Notify server that upload is complete
        if upload_ticket:
            print(f"\n[3c] Notifying server of upload completion (ticket={upload_ticket})...")
            notify_data = cloud_notify_upload(auth["access_token"], upload_ticket, filename)
            if notify_data.get("model_id"):
                model_id = str(notify_data["model_id"])
                print(f"  Updated model_id from notification: {model_id}")
        else:
            print(f"\n[3c] No upload_ticket — skipping notification")
            time.sleep(3)

        # Step 3d: Poll project details until profiles are populated
        # The server needs time to process the 3MF after upload.
        # We poll until profiles[0].url is available (the download URL).
        print(f"\n[3d] Polling project details (waiting for server processing)...")
        auth_headers = {**SLICER_HEADERS, "Authorization": f"Bearer {auth['access_token']}"}
        cover_url = ""
        download_url = ""
        download_md5 = ""
        profile_id_from_server = ""

        for poll in range(15):  # up to ~30 seconds
            proj_resp = requests.get(
                f"{API_BASE}/v1/iot-service/api/user/project/{project_id}",
                headers=auth_headers,
            )
            if not proj_resp.ok:
                print(f"  Poll {poll+1}: HTTP {proj_resp.status_code}")
                time.sleep(2)
                continue

            proj_detail = proj_resp.json()
            profiles = proj_detail.get("profiles", []) or []

            if profiles:
                prof = profiles[0]
                prof_url = prof.get("url") or ""
                prof_md5 = prof.get("md5") or ""
                prof_id = prof.get("profile_id") or ""

                if prof_url:
                    download_url = prof_url
                    download_md5 = prof_md5
                    if prof_id:
                        profile_id_from_server = str(prof_id)
                    print(f"  Poll {poll+1}: Profile ready!")
                    print(f"  profile_id: {profile_id_from_server}")
                    print(f"  url: {download_url[:120]}...")
                    if download_md5:
                        print(f"  md5: {download_md5}")

                    # Extract cover from plates[0].thumbnail.url
                    context = prof.get("context", {}) or {}
                    plates = context.get("plates", []) or []
                    if plates:
                        thumb = plates[0].get("thumbnail", {}) or {}
                        thumb_url = thumb.get("url") or ""
                        if thumb_url:
                            cover_url = thumb_url
                            print(f"  cover: {cover_url[:120]}...")

                    # If no thumbnail in plates, try deriving from configs
                    if not cover_url:
                        configs = context.get("configs", []) or []
                        for cfg in configs:
                            cfg_url = cfg.get("url", "")
                            cfg_name = cfg.get("name", "")
                            if cfg_url and "plate_1" in cfg_name:
                                cover_url = cfg_url.rsplit(".", 1)[0] + ".png"
                                print(f"  cover (derived): {cover_url[:120]}...")
                                break

                    break
                else:
                    print(f"  Poll {poll+1}: Profile exists but url not ready yet...")
            else:
                print(f"  Poll {poll+1}: No profiles yet...")

            time.sleep(2)

        # Update profile_id if server gave us one
        if profile_id_from_server:
            profile_id = profile_id_from_server

        # Also try the profile details endpoint directly
        if not download_url and profile_id != "0" and model_id != "0":
            print(f"\n  Trying profile endpoint: profile_id={profile_id}, model_id={model_id}")
            prof_resp = requests.get(
                f"{API_BASE}/v1/iot-service/api/user/profile/{profile_id}",
                headers=auth_headers,
                params={"model_id": model_id},
            )
            print(f"  Profile endpoint: {prof_resp.status_code}")
            if prof_resp.ok:
                prof_data = prof_resp.json()
                download_url = prof_data.get("url") or ""
                download_md5 = prof_data.get("md5") or ""
                if download_url:
                    print(f"  url: {download_url[:120]}...")

                context = prof_data.get("context", {}) or {}
                plates = context.get("plates", []) or []
                if plates and not cover_url:
                    thumb = plates[0].get("thumbnail", {}) or {}
                    cover_url = thumb.get("url") or ""
                    if cover_url:
                        print(f"  cover: {cover_url[:120]}...")

        # Use the profile download URL for MQTT (not the S3 upload URL)
        if download_url:
            print(f"  Using profile download_url for print: {download_url[:120]}...")
            file_url = download_url

    elif file_url:
        filename = Path(file_url).name
        print(f"\n[3] Using existing URL: {file_url}")
    elif not args.status_only:
        print("\n[3] No file specified — skipping upload")

    # --- Step 4: Connect MQTT ---
    print(f"\n[4] Connecting MQTT...")
    mqttc = BambuCloudMQTT(auth["user_id"], auth["access_token"], device_id)
    try:
        mqttc.connect()

        if args.status_only:
            print("\n[5] Requesting printer status...")
            mqttc.request_status()
            print("  Waiting for status (5s)...")
            time.sleep(5)
            print("\n  Done.")
            return

        if file_url:
            # --- Step 5: Create task (must happen BEFORE MQTT) ---
            task_id = "0"
            subtask_id = "0"

            if cover_url:
                print(f"\n[5] Creating cloud print task...")
                task_data = cloud_create_task(
                    auth["access_token"], device_id, filename, model_id, profile_id, cover_url
                )
                if task_data.get("id"):
                    task_id = str(task_data["id"])
                    subtask_id = str(task_data.get("subtask_id", "0"))
                    print(f"  task_id={task_id}, subtask_id={subtask_id}")
                else:
                    print("  Task creation failed — trying MQTT with task_id=0")
            else:
                print(f"\n[5] No cover URL — skipping task creation, using task_id=0")

            # --- Step 6: Start print via MQTT ---
            print(f"\n[6] Starting print via MQTT")
            print(f"  project_id={project_id}, profile_id={profile_id}")
            print(f"  task_id={task_id}, subtask_id={subtask_id}")
            print(f"  url: {file_url[:120]}...")
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

            # Wait for response
            print("  Waiting for response (15s)...")
            time.sleep(15)

            # Request status to see if print started
            mqttc.request_status()
            time.sleep(5)

            if args.interactive:
                interactive_loop(mqttc)
        else:
            print("\n[5] No file to print. Use --status-only to check printer status.")

    except KeyboardInterrupt:
        print("\n  Interrupted")
    finally:
        mqttc.disconnect()

    print("\nDone.")


if __name__ == "__main__":
    main()
