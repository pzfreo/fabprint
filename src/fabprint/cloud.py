"""Cloud printing via the bambu_cloud_bridge C++ binary.

This module wraps the compiled bambu_cloud_bridge binary, which uses
Bambu Lab's proprietary libbambu_networking.so to upload 3MF files
and start cloud print jobs.

The bridge binary must be available either:
  - In PATH as 'bambu_cloud_bridge'
  - At the path specified by BAMBU_BRIDGE_PATH env var
  - Via Docker: fabprint/cloud-bridge image
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

BRIDGE_NAME = "bambu_cloud_bridge"
DOCKER_IMAGE = "fabprint/cloud-bridge"


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
