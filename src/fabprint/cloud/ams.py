"""AMS tray parsing, mapping, and 3MF helper utilities."""

from __future__ import annotations

import io
import json
import logging
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

log = logging.getLogger(__name__)


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


def _build_ams_mapping(
    threemf_path: Path, plate_index: int = 1, ams_trays: list[dict] | None = None
) -> dict:
    """Parse 3MF to build amsDetailMapping, amsMapping, amsMapping2, filamentSettingIds.

    Returns a dict with all AMS-related task body fields, matching BambuConnect's format.
    Uses the total filament slot count from project_settings.config (not just plate filaments).
    """
    result: dict[str, list] = {
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
                plate_el: ET.Element | None = None
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
    except (zipfile.BadZipFile, KeyError, ET.ParseError, json.JSONDecodeError, OSError) as e:
        log.warning("Failed to parse 3MF for AMS mapping: %s", e)
        return result

    if not filament_by_id:
        return result

    log.debug(
        "3MF filament slots: plate=%s, total=%d, settings=%s",
        list(filament_by_id.keys()),
        total_slots,
        filament_setting_ids,
    )

    # Physical slot assignment: use live AMS state when available, else sequential.
    phys_by_id = _build_ams_mapping_from_state(filament_by_id, total_slots, ams_trays or [])

    # Build lookup from physical slot -> AMS tray for targetColor resolution
    tray_by_phys = {t["phys_slot"]: t for t in (ams_trays or [])}

    # All arrays are full-length (one entry per virtual slot), matching BambuConnect's format.
    # Unused slots get sentinel -1 / {255,255} / "" — matching BC's captured payload.
    detail = []
    mapping = []
    mapping2 = []
    setting_ids = []
    for slot_idx in range(total_slots):
        filament_id = slot_idx + 1
        fil_el = filament_by_id.get(filament_id)
        if fil_el is not None:
            source_color = fil_el.get("color", "#000000").lstrip("#").upper() + "FF"
            fil_type = fil_el.get("type", "")
            tray_idx = fil_el.get("tray_info_idx", "")
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
            mapping2.append({"ams_id": phys_slot // 4, "slot_id": phys_slot % 4})
            setting_ids.append(tray_idx)
        else:
            # Unused slot — use -1 sentinel matching BambuConnect's format.
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
            mapping.append(-1)
            mapping2.append({"ams_id": 255, "slot_id": 255})
            setting_ids.append("")

    result["amsDetailMapping"] = detail
    result["amsMapping"] = mapping
    result["amsMapping2"] = mapping2
    result["filamentSettingIds"] = setting_ids
    return result


def _build_ams_mapping_from_state(
    filament_by_id: dict,
    total_slots: int,
    ams_trays: list[dict],
) -> list[int]:
    """Match virtual filament slots to physical AMS trays.

    Returns ams_mapping list of length total_slots (-1 for unused slots).
    Matches first by filament type, then by color if multiple candidates.
    Falls back to sequential slot 0, 1, 2... if no AMS state available.
    """
    am = [-1] * total_slots
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
    except (zipfile.BadZipFile, KeyError, OSError) as e:
        log.debug("Could not read config 3MF for color patching: %s", e)
        return

    if "Metadata/slice_info.config" not in file_data:
        return

    try:
        root = ET.fromstring(file_data["Metadata/slice_info.config"])
    except ET.ParseError as e:
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
                        "Patching filament %d color: %s -> %s (AMS slot %d %s)",
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
        except (json.JSONDecodeError, KeyError, TypeError) as e:
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
