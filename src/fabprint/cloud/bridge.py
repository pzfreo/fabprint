"""Cloud printing via the C++ bridge binary (wraps libbambu_networking.so)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any

import json
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from fabprint import require_file
from fabprint.cloud.ams import (
    _build_ams_mapping,
    _patch_config_3mf_ams_colors,
    _strip_gcode_from_3mf,
)

log = logging.getLogger(__name__)

BRIDGE_NAME = "bambu_cloud_bridge"
DOCKER_IMAGE = "fabprint/cloud-bridge"

# Timeouts (seconds)
BRIDGE_TIMEOUT = 300
BRIDGE_STATUS_TIMEOUT = 60
BRIDGE_TASK_LIST_TIMEOUT = 30
BRIDGE_CANCEL_TIMEOUT = 30


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
    timeout: int = BRIDGE_TIMEOUT,
    verbose: bool = False,
) -> subprocess.CompletedProcess:
    """Run the bridge binary with given arguments.

    Returns CompletedProcess. Raises RuntimeError if bridge not found.
    """
    import platform

    bridge = _find_bridge()
    # On macOS the bridge binary can't load the Linux .so — always use Docker
    use_docker = bridge is None or platform.system() == "Darwin"

    if use_docker:
        # Check Docker is available
        try:
            subprocess.run(
                ["docker", "info"],
                capture_output=True,
                timeout=10,
            )
        except FileNotFoundError:
            raise RuntimeError(
                "Docker is required for Bambu Cloud printing but is not installed. "
                "Install Docker Desktop from https://www.docker.com/products/docker-desktop/"
            ) from None

        # Pull latest image quietly — only log on actual update.
        pull = subprocess.run(
            ["docker", "pull", DOCKER_IMAGE],
            capture_output=True,
            text=True,
            check=False,
        )
        if pull.returncode == 0 and "Downloaded newer image" in pull.stdout:
            log.info("Updated Docker image %s", DOCKER_IMAGE)

        # Mount each input file individually using its realpath.
        # Directory mounts on macOS/Docker Desktop have persistent symlink and
        # permission issues; individual file mounts via /Users (which Docker
        # Desktop always shares) are more reliable.
        cmd = [
            "docker",
            "run",
            "--rm",
            "--platform",
            "linux/amd64",
        ]
        docker_args = []
        for arg in args:
            if os.path.exists(arg):
                real = os.path.realpath(arg)
                container_path = f"/input/{os.path.basename(real)}"
                cmd.extend(["-v", f"{real}:{container_path}:ro"])
                docker_args.append(container_path)
            else:
                docker_args.append(arg)

        cmd.append(DOCKER_IMAGE)
        cmd.extend(docker_args)

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
    else:
        assert bridge is not None  # guaranteed by use_docker check above
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


class PersistentBridge:
    """Keep a Docker container running for repeated bridge commands.

    Usage::

        with PersistentBridge(token_file) as bridge:
            status = bridge.status(device_id)
    """

    def __init__(self, token_file: Path) -> None:
        self._token_file = token_file.resolve()
        self._container_id: str | None = None

    def __enter__(self) -> PersistentBridge:
        real_token = str(self._token_file)
        cmd = [
            "docker",
            "run",
            "-d",
            "--platform",
            "linux/amd64",
            "-v",
            f"{real_token}:/input/token.json:ro",
            "--entrypoint",
            "sleep",
            DOCKER_IMAGE,
            "infinity",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        self._container_id = result.stdout.strip()[:12]
        log.debug("Started persistent bridge container: %s", self._container_id)
        return self

    def __exit__(self, *exc: Any) -> None:
        if self._container_id:
            subprocess.run(
                ["docker", "rm", "-f", self._container_id],
                capture_output=True,
                check=False,
            )
            log.debug("Stopped persistent bridge container: %s", self._container_id)
            self._container_id = None

    def status(self, device_id: str, *, timeout: int = BRIDGE_STATUS_TIMEOUT) -> dict:
        """Query printer status via the running container.

        The bridge binary outputs JSON but may not exit afterwards, so we
        read the first line of stdout and kill the process.
        """
        import selectors

        assert self._container_id is not None
        cmd = [
            "docker",
            "exec",
            self._container_id,
            "bambu_cloud_bridge",
            "status",
            device_id,
            "/input/token.json",
        ]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        assert proc.stdout is not None
        try:
            sel = selectors.DefaultSelector()
            sel.register(proc.stdout, selectors.EVENT_READ)
            ready = sel.select(timeout=timeout)
            sel.close()
            if not ready:
                raise RuntimeError(f"Status query timed out for printer {device_id}")
            line = proc.stdout.readline()
        finally:
            proc.kill()
            proc.wait()
        try:
            data = json.loads(line.strip())
            return data.get("print", data)
        except json.JSONDecodeError:
            raise RuntimeError(f"Bridge returned non-JSON (exit {proc.returncode}): {line[:200]}")


def cloud_print(
    threemf_path: Path,
    device_id: str,
    token_file: Path,
    *,
    config_3mf: Path | None = None,
    project_name: str = "fabprint",
    timeout: int = 180,
    verbose: bool = False,
    ams_trays: list[dict] | None = None,
    skip_ams_mapping: bool = False,
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
    require_file(threemf_path, "3MF file")
    require_file(token_file, "Token file")

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

    # Build explicit AMS slot mapping so the printer doesn't show the
    # "Failed to get AMS mapping table" dialog. Without this the bridge
    # defaults to [0,1,2,3] (identity) which is wrong when AMS tray order
    # differs from gcode filament order.
    if ams_trays and not skip_ams_mapping:
        ams_data = _build_ams_mapping(threemf_path, ams_trays=ams_trays)
        raw = ams_data["amsMapping"]
        if raw and any(v >= 0 for v in raw):
            args.extend(["--ams-mapping", json.dumps(raw)])
            log.debug("AMS slot mapping: %s", raw)
        raw2 = ams_data["amsMapping2"]
        if raw2:
            args.extend(["--ams-mapping2", json.dumps(raw2)])
            log.debug("AMS slot mapping2: %s", raw2)
    elif skip_ams_mapping:
        log.info("AMS mapping skipped (--no-ams-mapping), using bridge default [0,1,2,3]")

    # Auto-generate config-only 3MF if not provided.
    # The v02.05 library requires a separate config_filename (3MF without gcode).
    tmp_config = None
    if config_3mf and config_3mf.exists():
        args.extend(["--config-3mf", str(config_3mf.resolve())])
    else:
        config_bytes = _strip_gcode_from_3mf(threemf_path)
        # Create alongside the source 3MF so it's under /Users — macOS
        # /var/folders temp files cause statx() ENOSYS inside Docker/Rosetta.
        tmp_config = tempfile.NamedTemporaryFile(
            suffix=".3mf", delete=False, dir=threemf_path.parent
        )
        tmp_config.write(config_bytes)
        tmp_config.close()
        if ams_trays:
            _patch_config_3mf_ams_colors(Path(tmp_config.name), threemf_path, ams_trays)
        args.extend(["--config-3mf", tmp_config.name])
        log.debug("Auto-generated config 3MF: %s (%d bytes)", tmp_config.name, len(config_bytes))

    try:
        result = _run_bridge(args, timeout=timeout + 60, verbose=verbose)
    finally:
        if tmp_config:
            try:
                os.unlink(tmp_config.name)
            except OSError:
                pass

    try:
        data = json.loads(result.stdout.strip())
        # Only warn on stderr when the result is actually an error (not "success"/"sent")
        if result.stderr:
            if data.get("result") not in ("success", "sent"):
                log.warning("Bridge stderr:\n%s", result.stderr.strip())
            else:
                log.debug("Bridge stderr:\n%s", result.stderr.strip())
        return data
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
    require_file(token_file, "Token file")

    args = ["status", device_id, str(token_file.resolve())]
    result = _run_bridge(args, timeout=BRIDGE_STATUS_TIMEOUT, verbose=verbose)

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
    require_file(token_file, "Token file")

    args = ["tasks", str(token_file.resolve()), "--limit", str(limit)]
    result = _run_bridge(args, timeout=BRIDGE_TASK_LIST_TIMEOUT)

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
    require_file(token_file, "Token file")

    args = ["cancel", device_id, str(token_file.resolve())]
    result = _run_bridge(args, timeout=BRIDGE_CANCEL_TIMEOUT, verbose=verbose)

    try:
        return json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        raise RuntimeError(
            f"Bridge returned non-JSON output (exit {result.returncode}): {result.stdout[:200]}"
        )
