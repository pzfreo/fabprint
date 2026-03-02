"""Cloud printing — C++ bridge and pure Python HTTP implementations.

Two approaches:

1. C++ bridge (cloud-bridge mode): wraps the compiled bambu_cloud_bridge binary,
   which uses Bambu Lab's proprietary libbambu_networking.so.

   The bridge binary must be available either:
     - In PATH as 'bambu_cloud_bridge'
     - At the path specified by BAMBU_BRIDGE_PATH env var
     - Via Docker: fabprint/cloud-bridge image

2. Pure Python HTTP (cloud-http mode): direct REST calls to Bambu Lab's API
   using BambuConnect client headers. Requires 'requests' (pip install fabprint[cloud]).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

log = logging.getLogger(__name__)

BRIDGE_NAME = "bambu_cloud_bridge"
DOCKER_IMAGE = "fabprint/cloud-bridge"
BASE_URL = "https://api.bambulab.com"


def _find_bridge() -> str | None:
    """Find the bridge binary. Returns path or None."""
    env_path = os.environ.get("BAMBU_BRIDGE_PATH")
    if env_path and Path(env_path).is_file():
        return env_path

    found = shutil.which(BRIDGE_NAME)
    if found:
        return found

    # Check common locations
    for candidate in [
        Path(__file__).parent.parent.parent / "scripts" / BRIDGE_NAME,
        Path.home() / ".local" / "bin" / BRIDGE_NAME,
        Path("/usr/local/bin") / BRIDGE_NAME,
    ]:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)

    return None


def _run_bridge(
    args: list[str],
    *,
    timeout: int = 300,
    verbose: bool = False,
) -> subprocess.CompletedProcess:
    """Run the bridge binary with given arguments.

    Returns CompletedProcess. Raises RuntimeError if bridge not found.
    """
    bridge = _find_bridge()
    use_docker = bridge is None

    if use_docker:
        # Fall back to Docker
        cmd = [
            "docker",
            "run",
            "--rm",
            "--platform",
            "linux/amd64",
        ]
        # Mount any file paths that appear in args
        for i, arg in enumerate(args):
            if os.path.exists(arg):
                abs_path = os.path.abspath(arg)
                cmd.extend(["-v", f"{abs_path}:{abs_path}:ro"])
        cmd.append(DOCKER_IMAGE)
        cmd.extend(args)
    else:
        cmd = [bridge] + args

    if verbose:
        cmd.append("-v")

    log.debug("Running: %s", " ".join(cmd))
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result


def cloud_print(
    threemf_path: Path,
    device_id: str,
    token_file: Path,
    *,
    config_3mf: Path | None = None,
    project_name: str = "fabprint",
    timeout: int = 180,
    verbose: bool = False,
) -> dict:
    """Start a cloud print job.

    Args:
        threemf_path: Path to the sliced .3mf file
        device_id: Printer serial number
        token_file: Path to JSON file with Bambu Cloud credentials
        config_3mf: Optional config-only 3MF file
        project_name: Project name shown in Bambu Cloud
        timeout: Seconds to wait for print to start
        verbose: Enable debug logging

    Returns:
        dict with keys: result, return_code, print_result, device_id, file

    Raises:
        RuntimeError: If bridge binary not found and Docker not available
        FileNotFoundError: If 3mf file or token file doesn't exist
    """
    if not threemf_path.exists():
        raise FileNotFoundError(f"3MF file not found: {threemf_path}")
    if not token_file.exists():
        raise FileNotFoundError(f"Token file not found: {token_file}")

    args = [
        "print",
        str(threemf_path.resolve()),
        device_id,
        str(token_file.resolve()),
        "--project",
        project_name,
        "--timeout",
        str(timeout),
    ]
    if config_3mf and config_3mf.exists():
        args.extend(["--config-3mf", str(config_3mf.resolve())])

    result = _run_bridge(args, timeout=timeout + 60, verbose=verbose)

    if result.stderr:
        log.debug("Bridge stderr: %s", result.stderr.strip())

    try:
        return json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        raise RuntimeError(
            f"Bridge returned non-JSON output (exit {result.returncode}): "
            f"{result.stdout[:200]} | {result.stderr[:200]}"
        )


def cloud_status(
    device_id: str,
    token_file: Path,
    *,
    verbose: bool = False,
) -> dict:
    """Query live printer status via MQTT.

    Returns the printer's status as a dict (the 'print' key from the MQTT message).
    """
    if not token_file.exists():
        raise FileNotFoundError(f"Token file not found: {token_file}")

    args = ["status", device_id, str(token_file.resolve())]
    result = _run_bridge(args, timeout=60, verbose=verbose)

    try:
        data = json.loads(result.stdout.strip())
        return data.get("print", data)
    except json.JSONDecodeError:
        if result.returncode == 2:
            raise RuntimeError(f"No status received from printer {device_id}")
        raise RuntimeError(
            f"Bridge returned non-JSON output (exit {result.returncode}): {result.stdout[:200]}"
        )


def cloud_tasks(
    token_file: Path,
    *,
    limit: int = 10,
) -> list[dict]:
    """List recent cloud print tasks (REST API, no MQTT needed).

    Returns list of task dicts.
    """
    if not token_file.exists():
        raise FileNotFoundError(f"Token file not found: {token_file}")

    args = ["tasks", str(token_file.resolve()), "--limit", str(limit)]
    result = _run_bridge(args, timeout=30)

    try:
        data = json.loads(result.stdout.strip())
        return data.get("hits", [])
    except json.JSONDecodeError:
        raise RuntimeError(
            f"Bridge returned non-JSON output (exit {result.returncode}): {result.stdout[:200]}"
        )


def cloud_cancel(
    device_id: str,
    token_file: Path,
    *,
    verbose: bool = False,
) -> dict:
    """Cancel the current print on a printer.

    Returns dict with command confirmation.
    """
    if not token_file.exists():
        raise FileNotFoundError(f"Token file not found: {token_file}")

    args = ["cancel", device_id, str(token_file.resolve())]
    result = _run_bridge(args, timeout=30, verbose=verbose)

    try:
        return json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        raise RuntimeError(
            f"Bridge returned non-JSON output (exit {result.returncode}): {result.stdout[:200]}"
        )


def cloud_print_http(
    threemf_path: Path,
    device_id: str,
    token_file: Path,
    *,
    project_name: str = "fabprint",
    plate_index: int = 1,
    bed_type: str = "auto",
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
            "Pure Python cloud print requires 'requests'. "
            "Install with: pip install fabprint[cloud]"
        )

    if not threemf_path.exists():
        raise FileNotFoundError(f"3MF file not found: {threemf_path}")
    if not token_file.exists():
        raise FileNotFoundError(f"Token file not found: {token_file}")

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
            raise RuntimeError(
                f"Cloud HTTP {step} failed ({resp.status_code}): {resp.text[:300]}"
            )
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

    # Step 2: Upload 3MF to presigned S3 URL (no Content-Type header)
    log.debug("Uploading %s to S3", threemf_path.name)
    with open(threemf_path, "rb") as f:
        resp = requests.put(upload_url, data=f, headers={})
    if not resp.ok:
        raise RuntimeError(f"S3 upload failed ({resp.status_code}): {resp.text[:200]}")
    log.debug("Upload complete")

    # Step 3: Notify server upload is complete
    _check(
        session.put(
            f"{BASE_URL}/v1/iot-service/api/user/notification",
            json={
                "action": "upload",
                "upload": {"ticket": upload_ticket, "origin_file_name": threemf_path.name},
            },
        ),
        "notification",
    )

    # Step 4: Poll GET /notification until processing is done (not "running")
    log.debug("Waiting for server to process 3MF...")
    for attempt in range(15):
        r = session.get(
            f"{BASE_URL}/v1/iot-service/api/user/notification",
            params={"action": "upload", "ticket": upload_ticket},
        )
        msg = r.json().get("message", "")
        if msg != "running":
            log.debug("Processing done after %d poll(s): %s", attempt + 1, msg)
            break
        if verbose:
            log.debug("Poll %d/15: still processing", attempt + 1)
        time.sleep(2)
    else:
        raise RuntimeError(f"3MF processing timed out for project {project_id}")

    # Step 5: Get profile S3 URL + MD5, patch project with profile_print_3mf
    prof = _check(
        session.get(
            f"{BASE_URL}/v1/iot-service/api/user/profile/{profile_id}",
            params={"model_id": model_id},
        ),
        "get profile",
    )
    profile_url = prof["url"]
    profile_md5 = prof["md5"].upper()
    _check(
        session.patch(
            f"{BASE_URL}/v1/iot-service/api/user/project/{project_id}",
            json={
                "profile_id": str(profile_id),
                "profile_print_3mf": [
                    {
                        "comments": "no_ips",
                        "md5": profile_md5,
                        "plate_idx": plate_index,
                        "url": profile_url,
                    }
                ],
            },
        ),
        "patch project",
    )

    # Step 6: Create print task
    task_body = {
        "deviceId": device_id,
        "modelId": model_id,
        "profileId": profile_id,
        "plateIndex": plate_index,
        "title": project_name,
        "cover": "",
        "mode": "cloud_file",
        "bedType": bed_type,
        "useAms": use_ams,
        "amsMapping": ams_mapping if ams_mapping is not None else [0, 1, 2, 3],
        "timelapse": timelapse,
        "bedLeveling": bed_leveling,
        "jobType": 1,
        "isPublicProfile": False,
    }
    log.debug("Creating print task for device %s", device_id)
    task_data = _check(
        session.post(f"{BASE_URL}/v1/user-service/my/task", json=task_body),
        "create task",
    )
    task_id = task_data["id"]
    log.debug("Task created: task_id=%s", task_id)

    return {
        "result": "success",
        "task_id": task_id,
        "project_id": project_id,
        "model_id": model_id,
    }
