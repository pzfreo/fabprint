# Cloud HTTP Mode — Handoff Notes

Branch: `cloud-print-test`

## What's implemented

`cloud_print_http()` in `src/fabprint/cloud.py` — pure Python cloud print, no C++ bridge needed.
Uses `mode = "cloud-http"` in `fabprint.toml`.

**7-step flow:**
1. `POST /v1/iot-service/api/user/project` → project_id, model_id, profile_id, upload_url, upload_ticket
2. `PUT upload_url` — upload **config-only 3MF** (gcode stripped via `_strip_gcode_from_3mf()`)
3. `PUT /v1/iot-service/api/user/notification` — notify upload complete
4. `GET /notification` poll until `message != "running"` (up to 15×2s)
5. `GET /v1/iot-service/api/user/upload?models={model_id}_{profile_id}_1.3mf` → second S3 URL;
   `PUT` full gcode.3mf there
6. `GET /v1/iot-service/api/user/profile/{profile_id}` → url + md5;
   `PATCH /v1/iot-service/api/user/project/{project_id}` with profile_print_3mf
7. `POST /v1/user-service/my/task` → task_id

## Bugs fixed (all in this branch)

| Bug | Fix |
|-----|-----|
| Response fields nested under `"project"` key | Top-level — `data["project_id"]` |
| `profile_id` from project creation is a string | `int(data["profile_id"])` |
| PATCH before server finishes processing → "Wrong file format" | Added GET /notification poll loop |
| Simple `{"profile_id":...}` PATCH fails | Must include `profile_print_3mf` with url+md5 from GET /profile |
| Only one S3 upload (missing second) | Added GET /user/upload + PUT full gcode |
| First upload is full gcode.3mf → "MQTT command verification failed" | Strip gcode from first upload (`_strip_gcode_from_3mf`) |

## Key discovery: two different files

BambuConnect uploads **two different files**:

| Upload | Content | Size | Where |
|--------|---------|------|-------|
| First (upload_url from project creation) | Config-only 3MF (no gcode) | ~14–50KB | `or-cloud-upload-prod/models/{model_id}/profiles/{profile_id}/` |
| Second (url from GET /user/upload) | Full gcode.3mf | ~1–12MB | `or-cloud-upload-prod/models/{timestamp}/{model_id}_{profile_id}_1.3mf` |

When the first upload **contains gcode**, Bambu's server extracts gcode metadata into model storage
(`or-cloud-model-prod`) but **leaves the URL empty**. The MQTT command to the printer then
contains an empty gcode URL → "MQTT command verification failed".

Confirmed via `GET /v1/iot-service/api/user/task/{task_id}`:
- ❌ Old (gcode in first upload): `"gcode": {"name": "plate_1.gcode", "url": "", "bucket": "or-cloud-model-prod"}`
- ✅ New (config-only first upload): `"gcode": {"name": "", "url": "", "bucket": ""}` — matches BC

## Current status (task 782446211)

After the gcode-strip fix was applied:
- Task created successfully, task_id = 782446211
- Task API gcode format now matches BC (all empty — correct)
- `status: 1` (pending) — Bambu has the task but printer is reporting idle
- **Not yet confirmed working** — printer was idle when checked, no error on display

## What to try next

### 1. Wait and re-check
`status: 1` may persist for a while before the MQTT is delivered. Poll `/v1/user-service/my/task/782446211` and watch for `status: 2` (running) or `status: 4` (failed).

### 2. If still stuck — monitor printer via LAN MQTT
The printer connects to Bambu's cloud via MQTT. You can also subscribe to the printer's
**local** MQTT (port 8883) with bambulabs-api to see what messages arrive from the cloud:

```python
# Rough sketch
from bambulabs_api import Printer
p = Printer(ip="...", access_code="...", serial="01P00A451601106")
p.start()
# Watch p.get_all_info() for changes
```

### 3. AMS mapping
BC uses `amsDetailMapping` (array of objects with amsId/slotId/filamentType) but we use
`amsMapping: [0,1,2,3]`. The server auto-generates amsDetailMapping from our filament data
and slots it to `amsId:0, slotId:0` regardless of our requested slot. This won't cause
verification failure but may cause a wrong-slot print.

For `mode = "cloud-http"`, need to pass the correct AMS slot info. The `ams_mapping` param
currently does `amsMapping: [0,1,2,3]` — might need to change to `amsDetailMapping` format.

### 4. Try a simpler test
Try a single-filament print with `use_ams=False` to eliminate AMS complexity.

### 5. Check the cover URL
BC fetches a thumbnail from `or-cloud-model-prod` after upload and uses it as the `cover`
in POST /my/task. We set `"cover": ""`. This shouldn't cause verification failure but may
matter for display.

## Evidence files

- `docs/bc-tls-capture.log` — sanitised full TLS traffic from BambuConnect (two prints captured)
- `docs/cloud-print-research.md` — full API analysis and history

## Quick test command

```bash
fabprint print examples/gib-tuners-c13-10/fabprint.toml --verbose
```

Token file: `~/.bambu_cloud_token` (JSON with `{"token": "...", "email": "..."}`)
