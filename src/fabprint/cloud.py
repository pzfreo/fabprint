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

import base64
import hashlib
import io
import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

log = logging.getLogger(__name__)

BRIDGE_NAME = "bambu_cloud_bridge"
DOCKER_IMAGE = "pzfreo/fabprint-cloud-bridge"
BASE_URL = "https://api.bambulab.com"

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
    import platform

    bridge = _find_bridge()
    # On macOS the bridge binary can't load the Linux .so — always use Docker
    use_docker = bridge is None or platform.system() == "Darwin"

    if use_docker:
        # Pull latest image first so progress is visible (not swallowed by capture_output).
        print("  Checking for Docker image updates...", flush=True)
        subprocess.run(
            ["docker", "pull", DOCKER_IMAGE],
            check=False,
        )

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
    ams_trays: list[dict] | None = None,
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

    # Build explicit AMS slot mapping so the printer doesn't show the
    # "Failed to get AMS mapping table" dialog. Without this the bridge
    # defaults to [0,1,2,3] (identity) which is wrong when AMS tray order
    # differs from gcode filament order.
    if ams_trays:
        ams_data = _build_ams_mapping(threemf_path, ams_trays=ams_trays)
        raw = ams_data["amsMapping"]
        if raw:
            # Strip trailing 255s (unused slots) to keep the array compact,
            # but keep leading 255s so slot indices stay correct.
            bridge_mapping = raw[:]
            while bridge_mapping and bridge_mapping[-1] == 255:
                bridge_mapping.pop()
            if bridge_mapping and any(v != 255 for v in bridge_mapping):
                args.extend(["--ams-mapping", json.dumps(bridge_mapping)])
                log.debug("AMS slot mapping: %s", bridge_mapping)

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


def _patch_config_3mf_ams_colors(
    config_path: Path,
    source_3mf: Path,
    ams_trays: list[dict],
) -> None:
    """Patch filament colors in the config 3MF to match actual AMS tray colors.

    The library matches virtual filament slots to AMS trays by type+color.
    If the gcode was sliced with a default/generic filament color that doesn't
    match the AMS tray color, the library's matching fails and the printer
    shows "Failed to get AMS mapping table". This patches slice_info.config
    and project_settings.config so colors match the physical AMS trays.
    """
    try:
        with zipfile.ZipFile(config_path, "r") as z:
            file_data = {name: z.read(name) for name in z.namelist()}
    except Exception as e:
        log.debug("Could not read config 3MF for color patching: %s", e)
        return

    if "Metadata/slice_info.config" not in file_data:
        return

    try:
        root = ET.fromstring(file_data["Metadata/slice_info.config"])
    except Exception as e:
        log.debug("Could not parse slice_info.config: %s", e)
        return

    plate_el = root.find("plate")
    if plate_el is None:
        return

    filament_by_id = {}
    for f in plate_el.findall("filament"):
        fid = int(f.get("id", "1"))
        filament_by_id[fid] = f

    if not filament_by_id:
        return

    total_slots = max(filament_by_id.keys())
    phys_by_id = _build_ams_mapping_from_state(filament_by_id, total_slots, ams_trays)
    tray_by_phys = {t["phys_slot"]: t for t in ams_trays}

    # Patch color attributes in slice_info.config
    changed = False
    for f in plate_el.findall("filament"):
        fid = int(f.get("id", "1"))
        if fid - 1 < len(phys_by_id):
            phys_slot = phys_by_id[fid - 1]
            tray = tray_by_phys.get(phys_slot)
            if tray and phys_slot != 255:
                new_color = "#" + tray["color"]
                if f.get("color", "") != new_color:
                    log.debug(
                        "Patching filament %d color: %s → %s (AMS slot %d %s)",
                        fid,
                        f.get("color", ""),
                        new_color,
                        phys_slot,
                        tray["type"],
                    )
                    f.set("color", new_color)
                    changed = True

    if not changed:
        return

    file_data["Metadata/slice_info.config"] = ET.tostring(root, encoding="unicode").encode()

    # Also patch project_settings.config filament_colour array
    if "Metadata/project_settings.config" in file_data:
        try:
            ps = json.loads(file_data["Metadata/project_settings.config"])
            colours = list(ps.get("filament_colour", []))
            for fid in sorted(filament_by_id.keys()):
                idx = fid - 1
                if idx < len(colours) and idx < len(phys_by_id):
                    phys_slot = phys_by_id[idx]
                    tray = tray_by_phys.get(phys_slot)
                    if tray and phys_slot != 255:
                        colours[idx] = "#" + tray["color"]
            ps["filament_colour"] = colours
            file_data["Metadata/project_settings.config"] = json.dumps(ps).encode()
        except Exception as e:
            log.debug("Could not patch project_settings.config colours: %s", e)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
        for name, data in file_data.items():
            zout.writestr(name, data)
    config_path.write_bytes(buf.getvalue())
    log.debug("Patched config 3MF filament colors to match AMS trays")


def _strip_gcode_from_3mf(path: Path) -> bytes:
    """Return a config-only 3MF matching BambuConnect's first upload.

    BambuConnect uploads a small config-only 3MF as the first file. It contains
    ONLY metadata — no model geometry, no gcode, no images, no gcode MD5.
    Including extra files (especially .gcode.md5 or model geometry) causes the
    server to set up gcode references incorrectly, leading to
    "MQTT Command verification failed" on the printer.

    Allowed files (from BambuStudio export_config_3mf):
      - [Content_Types].xml
      - _rels/.rels
      - Metadata/slice_info.config
      - Metadata/model_settings.config
      - Metadata/project_settings.config
      - Metadata/_rels/model_settings.config.rels
      - Metadata/plate_*.json
    """
    ALLOWED_FILES = {
        "[Content_Types].xml",
        "_rels/.rels",
        "Metadata/slice_info.config",
        "Metadata/model_settings.config",
        "Metadata/project_settings.config",
        "Metadata/_rels/model_settings.config.rels",
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(path, "r") as zin, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            name = item.filename
            # Keep explicitly allowed files + plate JSON files
            if name in ALLOWED_FILES or (
                name.startswith("Metadata/plate_") and name.endswith(".json")
            ):
                zout.writestr(item, zin.read(name))
    return buf.getvalue()


def _build_ams_mapping(
    threemf_path: Path, plate_index: int = 1, ams_trays: list[dict] | None = None
) -> dict:
    """Parse 3MF to build amsDetailMapping, amsMapping, amsMapping2, filamentSettingIds.

    Returns a dict with all AMS-related task body fields, matching BambuConnect's format.
    Uses the total filament slot count from project_settings.config (not just plate filaments).
    """
    result = {
        "amsDetailMapping": [],
        "amsMapping": [],
        "amsMapping2": [],
        "filamentSettingIds": [],
    }

    try:
        with zipfile.ZipFile(threemf_path, "r") as z:
            # Get total filament count from project_settings.config
            total_slots = 0
            filament_setting_ids = []
            if "Metadata/project_settings.config" in z.namelist():
                ps = json.loads(z.read("Metadata/project_settings.config"))
                filament_colour = ps.get("filament_colour", [])
                total_slots = len(filament_colour)
                filament_setting_ids = ps.get("filament_settings_id", [])

            # Get plate filament usage from slice_info.config
            filament_by_id = {}
            if "Metadata/slice_info.config" in z.namelist():
                root = ET.fromstring(z.read("Metadata/slice_info.config"))
                plate_el = None
                for plate in root.findall("plate"):
                    idx_meta = plate.find("metadata[@key='index']")
                    if idx_meta is not None and idx_meta.get("value") == str(plate_index):
                        plate_el = plate
                        break
                if plate_el is None:
                    plate_el = root.find("plate")
                if plate_el is not None:
                    for f in plate_el.findall("filament"):
                        fid = int(f.get("id", "1"))
                        filament_by_id[fid] = f
                    if not total_slots and filament_by_id:
                        total_slots = max(filament_by_id.keys())
    except Exception as e:
        log.warning("Failed to parse 3MF for AMS mapping: %s", e)
        return result

    if not filament_by_id:
        return result

    # Cap at highest used filament id — OrcaSlicer may define extra default
    # slots (e.g. 5 for P1S) that duplicate the last loaded filament.
    max_loaded = max(filament_by_id.keys())
    if total_slots > max_loaded:
        total_slots = max_loaded

    log.debug(
        "3MF filament slots: plate=%s, total=%d, settings=%s",
        list(filament_by_id.keys()),
        total_slots,
        filament_setting_ids,
    )

    # Physical slot assignment: use live AMS state when available, else sequential.
    phys_by_id = _build_ams_mapping_from_state(filament_by_id, total_slots, ams_trays or [])

    # Build lookups from physical slot and filament type → AMS tray
    tray_by_phys = {t["phys_slot"]: t for t in (ams_trays or [])}
    # Group AMS trays by type for matching against filament setting names
    tray_by_type: dict[str, list[dict]] = {}
    for t in ams_trays or []:
        typ = t.get("type", "")
        if typ:
            tray_by_type.setdefault(typ, []).append(t)
    # Sort type keys longest-first so "PETG-CF" matches before "PLA" etc.
    type_keys_sorted = sorted(tray_by_type.keys(), key=len, reverse=True)

    # All arrays are full-length (one entry per virtual slot), matching BambuConnect's format.
    # Unused slots get sentinel values: -1 / {255,255} / "" — not just the used filaments.
    detail = []
    mapping = []
    mapping2 = []
    setting_ids = []
    for slot_idx in range(total_slots):
        filament_id = slot_idx + 1
        f = filament_by_id.get(filament_id)
        if f is not None:
            source_color = f.get("color", "#000000").lstrip("#").upper() + "FF"
            fil_type = f.get("type", "")
            tray_idx = f.get("tray_info_idx", "")
            phys_slot = phys_by_id[slot_idx]  # 0-based physical slot
            # targetColor = actual AMS color; falls back to sourceColor if unknown
            actual_tray = tray_by_phys.get(phys_slot)
            target_color = (actual_tray["color"] + "FF") if actual_tray else source_color
            detail.append(
                {
                    "ams": phys_slot,
                    "amsId": phys_slot // 4,
                    "slotId": phys_slot % 4,
                    "nozzleId": 0,
                    "sourceColor": source_color,
                    "targetColor": target_color,
                    "filamentType": fil_type,
                    "targetFilamentType": fil_type,
                    "filamentId": tray_idx,
                }
            )
            mapping.append(phys_slot)
            mapping2.append({"amsId": phys_slot // 4, "slotId": phys_slot % 4})
            setting_ids.append(tray_idx)
        else:
            # Slot not used on this plate — try matching via filament_settings_id
            # (e.g. "Generic ABS @base") so loaded-but-unused filaments still get
            # correct physical slots. Without this, single-material prints produce
            # an incomplete mapping that triggers "Failed to get AMS mapping table".
            n = len(filament_setting_ids)
            setting_id = filament_setting_ids[slot_idx] if slot_idx < n else ""
            # Match by finding which AMS tray type appears in the setting name
            tray = None
            if setting_id:
                for typ in type_keys_sorted:
                    if typ in setting_id:
                        candidates = tray_by_type[typ]
                        tray = candidates[0]
                        break
            if tray:
                phys_slot = tray["phys_slot"]
                target_color = tray["color"] + "FF"
                detail.append(
                    {
                        "ams": phys_slot,
                        "amsId": phys_slot // 4,
                        "slotId": phys_slot % 4,
                        "nozzleId": 0,
                        "sourceColor": target_color,
                        "targetColor": target_color,
                        "filamentType": tray.get("type", ""),
                        "targetFilamentType": tray.get("type", ""),
                        "filamentId": setting_id,
                    }
                )
                mapping.append(phys_slot)
                mapping2.append({"amsId": phys_slot // 4, "slotId": phys_slot % 4})
                setting_ids.append(setting_id)
            else:
                detail.append(
                    {
                        "ams": -1,
                        "amsId": 255,
                        "slotId": 255,
                        "filamentId": "",
                        "filamentType": "",
                        "targetColor": "",
                    }
                )
                mapping.append(255)
                mapping2.append({"amsId": 255, "slotId": 255})
                setting_ids.append("")

    result["amsDetailMapping"] = detail
    result["amsMapping"] = mapping
    result["amsMapping2"] = mapping2
    result["filamentSettingIds"] = setting_ids
    return result


def parse_ams_trays(status: dict) -> list[dict]:
    """Extract physical AMS tray info from a printer status dict.

    Returns a list of dicts (one per loaded tray) with keys:
        phys_slot  — global slot index (amsId * 4 + slotId)
        ams_id     — AMS unit index (0-based)
        slot_id    — tray within AMS unit (0-based)
        type       — filament type string, e.g. "PETG-CF"
        color      — 6-char hex color without alpha, e.g. "F2754E"
        tray_info_idx — Bambu filament ID, e.g. "GFG98"
    """
    trays = []
    ams_data = status.get("ams", {})
    for unit in ams_data.get("ams", []):
        ams_id = int(unit.get("id", 0))
        for tray in unit.get("tray", []):
            slot_id = int(tray.get("id", 0))
            fil_type = tray.get("tray_type", "")
            if not fil_type:
                continue  # empty tray
            color_raw = tray.get("tray_color", "")
            color = color_raw[:6] if len(color_raw) >= 6 else color_raw
            trays.append(
                {
                    "phys_slot": ams_id * 4 + slot_id,
                    "ams_id": ams_id,
                    "slot_id": slot_id,
                    "type": fil_type,
                    "color": color,
                    "tray_info_idx": tray.get("tray_info_idx", ""),
                }
            )
    return trays


def _build_ams_mapping_from_state(
    filament_by_id: dict,
    total_slots: int,
    ams_trays: list[dict],
) -> list[int]:
    """Match virtual filament slots to physical AMS trays.

    Returns ams_mapping list of length total_slots (255 for unused slots).
    Matches first by filament type, then by color if multiple candidates.
    Falls back to sequential slot 0, 1, 2… if no AMS state available.
    """
    am = [255] * total_slots
    used = set()  # physical slots already assigned

    for seq_idx, filament_id in enumerate(sorted(filament_by_id.keys())):
        f = filament_by_id[filament_id]
        fil_type = f.get("type", "")
        color = f.get("color", "").lstrip("#").upper()

        best = None
        if ams_trays:
            # Score candidates: 2 pts for type match, 1 pt for color match
            candidates = [
                (
                    (2 if t["type"] == fil_type else 0) + (1 if t["color"] == color else 0),
                    t,
                )
                for t in ams_trays
                if t["phys_slot"] not in used
            ]
            candidates.sort(key=lambda x: -x[0])
            if candidates and candidates[0][0] > 0:
                best = candidates[0][1]

        phys_slot = best["phys_slot"] if best else seq_idx
        if best:
            used.add(phys_slot)
        am[filament_id - 1] = phys_slot

    return am


def _poll_task_status(
    session: requests.Session,  # noqa: F821
    task_id: int,
    device_id: str = "",
    *,
    max_polls: int = 12,
    interval: int = 5,
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
        except Exception as e:
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
            except Exception as e:
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
