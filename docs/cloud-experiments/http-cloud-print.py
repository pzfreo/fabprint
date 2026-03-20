"""Cloud printing via pure Python HTTP (direct REST calls to Bambu Lab API)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any

import base64
import hashlib
import json
import logging
import time
import uuid
from pathlib import Path

from fabprint import require_file
from fabprint.cloud.ams import _build_ams_mapping, _strip_gcode_from_3mf

log = logging.getLogger(__name__)

BASE_URL = "https://api.bambulab.com"

# Task polling defaults
TASK_POLL_MAX_ATTEMPTS = 12
TASK_POLL_INTERVAL = 5

# 3MF processing poll limits
PROCESSING_POLL_MAX_ATTEMPTS = 15
PROCESSING_POLL_INTERVAL = 2

# BambuConnect X.509 certificate ID and private key for signing print tasks.
# The server passes this signature to the printer via MQTT; without it the
# printer rejects the command ("MQTT Command verification failed").
# Ref: https://hackaday.com/2025/01/19/bambu-connects-authentication-x-509-certificate-and-private-key-extracted/
BAMBU_CERT_ID = "CN=GLOF3813734089.bambulab.com:f9332ab780a6ffe6664db61be42b04ee"

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

_bambu_private_key = None


def _get_private_key():
    """Lazily load the BambuConnect private key (requires cryptography package)."""
    global _bambu_private_key
    if _bambu_private_key is None:
        from cryptography.hazmat.primitives.serialization import load_pem_private_key

        _bambu_private_key = load_pem_private_key(BAMBU_PRIVATE_KEY_PEM.encode(), password=None)
    return _bambu_private_key


def _sign_task_body(body_bytes: bytes) -> str:
    """Sign the POST /my/task request body with the BambuConnect X.509 private key.

    Returns a Base64-encoded RSA-SHA256 signature.
    """
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    key = _get_private_key()
    signature = key.sign(
        body_bytes,
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    return base64.b64encode(signature).decode("ascii")


def cloud_list_devices(token_file: Path) -> list[dict]:
    """List bound printers via Bambu Cloud REST API.

    Returns a list of device dicts with keys like dev_id, name, online,
    dev_product_name, dev_model_name.
    """
    import requests

    token_data = json.loads(token_file.read_text())
    token = token_data.get("token") or token_data.get("accessToken")
    if not token:
        raise ValueError("No token found in token file")

    resp = requests.get(
        f"{BASE_URL}/v1/iot-service/api/user/bind",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("devices", [])


def _poll_task_status(
    session: Any,  # requests.Session — imported inside cloud_print_http()
    task_id: int,
    device_id: str = "",
    *,
    max_polls: int = TASK_POLL_MAX_ATTEMPTS,
    interval: int = TASK_POLL_INTERVAL,
) -> dict:
    """Poll task status until dispatched or times out.

    Checks both the task API (status 1=pending, 2=running, 3=complete, 4=failed)
    and the device bind API (print_status: IDLE, PREPARE, RUNNING, FINISH, FAILED).
    The device status updates faster than the task API for newly dispatched tasks.
    """
    for attempt in range(max_polls):
        # Check task API
        task_status = -1
        try:
            r = session.get(f"{BASE_URL}/v1/user-service/my/task/{task_id}")
            if r.ok:
                task = r.json()
                task_status = task.get("status", -1)
                if task_status != 1:  # No longer pending
                    log.info(
                        "Task %s status changed to %s (failedType=%s)",
                        task_id,
                        task_status,
                        task.get("failedType", 0),
                    )
                    return task
        except (OSError, ValueError) as e:
            log.debug("Task poll error: %s", e)

        # Check device status (updates faster than task API)
        if device_id:
            try:
                r = session.get(f"{BASE_URL}/v1/iot-service/api/user/bind")
                if r.ok:
                    for dev in r.json().get("devices", []):
                        if dev.get("dev_id") == device_id:
                            ps = dev.get("print_status", "")
                            pj = dev.get("print_job", "")
                            log.debug(
                                "Task %s poll %d/%d: task_status=%s, printer=%s (job=%s)",
                                task_id,
                                attempt + 1,
                                max_polls,
                                task_status,
                                ps,
                                pj,
                            )
                            if str(pj) == str(task_id) and ps in ("PREPARE", "RUNNING"):
                                log.info(
                                    "Task %s dispatched! Printer is %s",
                                    task_id,
                                    ps,
                                )
                                return {
                                    "status": 2 if ps == "RUNNING" else 1,
                                    "id": task_id,
                                    "print_status": ps,
                                }
                            break
            except (OSError, ValueError) as e:
                log.debug("Device poll error: %s", e)
        else:
            log.debug(
                "Task %s poll %d/%d: task_status=%s",
                task_id,
                attempt + 1,
                max_polls,
                task_status,
            )

        if attempt < max_polls - 1:
            time.sleep(interval)

    log.warning("Task %s still pending after %ds", task_id, max_polls * interval)
    return {"status": 1, "id": task_id}


def cloud_print_http(
    threemf_path: Path,
    device_id: str,
    token_file: Path,
    *,
    project_name: str = "fabprint",
    plate_index: int = 1,
    bed_type: str = "textured_plate",
    use_ams: bool = True,
    ams_mapping: list[int] | None = None,
    timelapse: bool = False,
    bed_leveling: bool = True,
    verbose: bool = False,
) -> dict:
    """Start a cloud print job via pure Python HTTP (no C++ bridge needed).

    Uses BambuConnect client headers to call Bambu Lab's REST API directly.
    Requires 'requests': pip install fabprint[cloud]

    Args:
        threemf_path: Path to the sliced .gcode.3mf file
        device_id: Printer serial number
        token_file: Path to JSON file with {"token": "...", "email": "..."}
        project_name: Title shown in Bambu Handy app
        plate_index: Plate number to print (usually 1)
        bed_type: Bed surface type ("auto", "textured_plate", "smooth_plate", etc.)
        use_ams: Whether to use AMS filament system
        ams_mapping: AMS slot mapping list, e.g. [0,1,2,3]. Defaults to [0,1,2,3].
        timelapse: Enable timelapse recording
        bed_leveling: Enable auto bed leveling
        verbose: Log extra debug info

    Returns:
        dict with keys: result, task_id, project_id, model_id

    Raises:
        RuntimeError: On HTTP errors or missing 'requests' library
        FileNotFoundError: If 3mf or token file doesn't exist
    """
    try:
        import requests
    except ImportError:
        raise RuntimeError(
            "Pure Python cloud print requires 'requests'. Install with: pip install fabprint[cloud]"
        )

    require_file(threemf_path, "3MF file")
    require_file(token_file, "Token file")

    token_data = json.loads(token_file.read_text())
    token = token_data["token"]

    session = requests.Session()
    session.headers.update(
        {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "x-bbl-client-name": "BambuConnect",
            "x-bbl-client-type": "connect",
            "x-bbl-client-version": "v2.2.1-beta.2",
            "x-bbl-device-id": str(uuid.uuid4()),
            "x-bbl-language": "en-GB",
        }
    )

    def _check(resp: "requests.Response", step: str) -> dict:
        if not resp.ok:
            raise RuntimeError(f"Cloud HTTP {step} failed ({resp.status_code}): {resp.text[:300]}")
        return resp.json()

    # Step 1: Create project
    log.debug("Creating project for %s", threemf_path.name)
    data = _check(
        session.post(
            f"{BASE_URL}/v1/iot-service/api/user/project", json={"name": threemf_path.name}
        ),
        "create project",
    )
    project_id = data["project_id"]
    model_id = data["model_id"]
    profile_id = int(data["profile_id"])
    upload_url = data["upload_url"]
    upload_ticket = data["upload_ticket"]
    log.debug("Project created: project_id=%s model_id=%s", project_id, model_id)

    # Step 2: Upload config-only 3MF (no gcode) to presigned S3 URL.
    # BC uploads a small config 3MF first. This keeps the task's gcode field empty
    # (matching BC format). If the full 3MF is uploaded here, the server sets gcode.name
    # but leaves gcode.url EMPTY, causing "MQTT Command verification failed".
    config_3mf_bytes = _strip_gcode_from_3mf(threemf_path)
    log.debug("Uploading config-only 3MF to S3 (%d bytes)", len(config_3mf_bytes))
    resp = requests.put(upload_url, data=config_3mf_bytes, headers={})
    if not resp.ok:
        raise RuntimeError(f"S3 upload failed ({resp.status_code}): {resp.text[:200]}")
    log.debug("Config upload complete")

    # Step 3: Notify server upload is complete
    _check(
        session.put(
            f"{BASE_URL}/v1/iot-service/api/user/notification",
            json={
                "action": "upload",
                "upload": {"ticket": upload_ticket, "origin_file_name": "connect_config.3mf"},
            },
        ),
        "notification",
    )

    # Step 4: Poll GET /notification until processing is done (not "running")
    log.debug("Waiting for server to process 3MF...")
    for attempt in range(PROCESSING_POLL_MAX_ATTEMPTS):
        r = session.get(
            f"{BASE_URL}/v1/iot-service/api/user/notification",
            params={"action": "upload", "ticket": upload_ticket},
        )
        msg = r.json().get("message", "")
        if msg != "running":
            log.debug("Processing done after %d poll(s): %s", attempt + 1, msg)
            break
        if verbose:
            log.debug("Poll %d/%d: still processing", attempt + 1, PROCESSING_POLL_MAX_ATTEMPTS)
        time.sleep(PROCESSING_POLL_INTERVAL)
    else:
        raise RuntimeError(f"3MF processing timed out for project {project_id}")

    # Step 5: Upload full gcode.3mf to printer-accessible storage (second S3 upload).
    # BambuConnect uses a separate presigned URL for this (different bucket/path from step 2).
    # We compute the MD5 here because PATCH must reference this URL + MD5 of this file.
    log.debug("Getting gcode upload URL for %s_%s_1.3mf", model_id, profile_id)
    upload_info = _check(
        session.get(
            f"{BASE_URL}/v1/iot-service/api/user/upload",
            params={"models": f"{model_id}_{profile_id}_1.3mf"},
        ),
        "get gcode upload url",
    )
    gcode_upload_url = upload_info["urls"][0]["url"]
    gcode_bytes = threemf_path.read_bytes()
    gcode_md5 = hashlib.md5(gcode_bytes).hexdigest().upper()
    log.debug("Uploading full 3MF to gcode storage (%d bytes, md5=%s)", len(gcode_bytes), gcode_md5)
    resp = requests.put(gcode_upload_url, data=gcode_bytes, headers={})
    if not resp.ok:
        raise RuntimeError(f"Gcode S3 upload failed ({resp.status_code}): {resp.text[:200]}")
    log.debug("Gcode upload complete")

    # Step 6: PATCH project with the full presigned S3 upload URL + MD5.
    # BC uses the presigned URL (with AWSAccessKeyId, Expires, Signature query params),
    # NOT a dualstack or profile URL. MD5 was already computed in step 5.
    log.debug("PATCH profile_print_3mf URL: %s", gcode_upload_url)
    _check(
        session.patch(
            f"{BASE_URL}/v1/iot-service/api/user/project/{project_id}",
            json={
                "profile_id": str(profile_id),
                "profile_print_3mf": [
                    {
                        "comments": "no_ips",
                        "md5": gcode_md5,
                        "plate_idx": plate_index,
                        "url": gcode_upload_url,
                    }
                ],
            },
        ),
        "patch project",
    )

    # Step 7: Build AMS mapping from 3MF filament metadata
    ams_data = _build_ams_mapping(threemf_path, plate_index)
    if ams_mapping is not None:
        # Caller provided explicit mapping — override computed
        ams_data["amsMapping"] = ams_mapping

    log.debug("AMS mapping: %s", ams_data["amsMapping"])
    if verbose:
        log.debug("AMS detail: %s", json.dumps(ams_data["amsDetailMapping"], indent=2))

    # Step 8: Create print task (body matches BambuConnect v2.2.1 capture)
    task_body = {
        "amsDetailMapping": ams_data["amsDetailMapping"],
        "amsMapping": ams_data["amsMapping"],
        "amsMapping2": ams_data["amsMapping2"],
        "bedType": bed_type,
        "cover": "",
        "deviceId": device_id,
        "filamentSettingIds": ams_data["filamentSettingIds"],
        "isPublicProfile": False,
        "jobType": 1,
        "layerInspect": True,
        "mode": "cloud_file",
        "modelId": model_id,
        "plateIndex": plate_index,
        "profileId": profile_id,
        "title": threemf_path.name,
        "useAms": use_ams,
        "timelapse": timelapse,
        "bedLeveling": bed_leveling,
        "flowCali": False,
        "extrudeCaliManualMode": 1,
        "autoBedLeveling": 2,
        "extrudeCaliFlag": 2,
        "nozzleOffsetCali": 2,
        "nozzleInfos": [],
        "primeVolumeMode": "Default",
    }
    if verbose:
        log.info("Task body: %s", json.dumps(task_body, indent=2))

    # POST /my/task — sent unsigned (correct signing key not yet available).
    # BC signs this request with its X.509 private key; the server includes the
    # signature in the MQTT command to the printer. Without it the printer rejects
    # the command ("MQTT Command verification failed"). The Hackaday-extracted key
    # does not work with BC v2.2.1-beta.2 (server returns 403 on wrong signature).
    task_data = _check(
        session.post(
            f"{BASE_URL}/v1/user-service/my/task",
            json=task_body,
        ),
        "create task",
    )
    task_id = task_data["id"]
    log.info("Task created: task_id=%s — polling for dispatch...", task_id)

    # Step 9: Poll task status to confirm dispatch
    final_status = _poll_task_status(session, task_id, device_id)
    status_code = final_status.get("status", -1)
    print_status = final_status.get("print_status", "")
    status_names = {1: "pending", 2: "running", 3: "complete", 4: "failed"}
    status_name = status_names.get(status_code, f"unknown({status_code})")

    # If device shows PREPARE/RUNNING, the task dispatched even if API still says pending
    if print_status in ("PREPARE", "RUNNING"):
        status_name = print_status.lower()

    log.info("Task %s final status: %s", task_id, status_name)

    if status_code == 4:
        log.error("Task FAILED: failedType=%s", final_status.get("failedType", "unknown"))

    return {
        "result": "success" if status_code in (1, 2, 3) else "failed",
        "task_id": task_id,
        "task_status": status_name,
        "project_id": project_id,
        "model_id": model_id,
    }
