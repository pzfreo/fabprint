"""Cloud printing for Bambu Lab printers (bridge and HTTP modes)."""

from fabprint.cloud.ams import (
    _build_ams_mapping,
    _build_ams_mapping_from_state,
    _patch_config_3mf_ams_colors,
    _strip_gcode_from_3mf,
    parse_ams_trays,
)
from fabprint.cloud.bridge import (
    PersistentBridge,
    _find_bridge,
    _record_pull,
    _run_bridge,
    _should_pull_image,
    cloud_cancel,
    cloud_print,
    cloud_status,
    cloud_tasks,
)
from fabprint.cloud.http import (
    cloud_list_devices,
    cloud_print_http,
)

__all__ = [
    "PersistentBridge",
    "_build_ams_mapping",
    "_find_bridge",
    "_record_pull",
    "_run_bridge",
    "_should_pull_image",
    "_build_ams_mapping_from_state",
    "_patch_config_3mf_ams_colors",
    "_strip_gcode_from_3mf",
    "cloud_cancel",
    "cloud_list_devices",
    "cloud_print",
    "cloud_print_http",
    "cloud_status",
    "cloud_tasks",
    "parse_ams_trays",
]
