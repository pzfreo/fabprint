# Bambu Lab Cloud Print API Research

## Overview

This document captures all findings from reverse-engineering the Bambu Lab cloud print API,
with the goal of triggering a cloud print from third-party code (not BambuStudio or Bambu Connect).

**Printer:** Bambu Lab P1S, serial `01P00A451601106`, with 4-slot AMS
**Branch:** `cloud-print-test`
**Test script:** `scripts/test_cloud_print.py`

---

## The Cloud Print Flow

Reconstructed from BambuStudio source code error codes in `bambu_networking.hpp`:

| Step | Error Code | Description | Status |
|------|-----------|-------------|--------|
| 1 | -2010 | Create project (POST /v1/iot-service/api/user/project) | Working |
| 2 | -2110 | Upload 3mf to S3 (PUT signed URL) | Working |
| 3 | -2030 | Upload 3mf config to OSS | Unknown (may be implicit) |
| 4 | -2050 | PUT notification (upload complete) | Working |
| 5 | -2060 | GET notification (poll confirmation) | Working |
| 6 | -2080 | PATCH project | Working (200) |
| 7 | -2090 | GET my/setting | Working (200) |
| 8 | -2140 | get_user_upload | Unknown |
| 9 | -2120 | POST task (POST /v1/user-service/my/task) | **BLOCKED — returns 400** |
| 10 | -3130 | Wait for printer ACK via MQTT | Not reached |

The actual cloud print logic lives in the **proprietary `libbambu_networking.so`** — BambuStudio's
open-source code only shows wrapper interfaces. The error codes above are the best clues to the
internal flow.

---

## API Endpoints

### Working Endpoints

| Method | Endpoint | Purpose | Notes |
|--------|----------|---------|-------|
| POST | /v1/user-service/user/login | Login | Returns accessToken |
| GET | /v1/design-user-service/my/preference | Get user ID | Returns uid |
| GET | /v1/iot-service/api/user/bind | List devices | Returns devices array |
| POST | /v1/iot-service/api/user/project | Create project | Returns project_id, model_id, profile_id, upload_url, upload_ticket |
| PUT | (signed S3 URL) | Upload 3mf to S3 | Standard S3 PUT |
| PUT | /v1/iot-service/api/user/notification | Notify upload complete | Needs {upload: {ticket, origin_file_name}} |
| GET | /v1/iot-service/api/user/notification | Poll upload status | With action=upload&ticket=... |
| GET | /v1/iot-service/api/user/project/{id} | Get project detail | Returns profiles with context, plates, configs |
| PATCH | /v1/iot-service/api/user/project/{id} | Update project | Works with {name, profile_id(string)} |
| GET | /v1/user-service/my/setting | Get user settings | Returns notification preferences |
| GET | /v1/iot-service/api/user/print | Get device print status | Returns device info + access code |
| GET | /v1/iot-service/api/user/upload | Get signed upload URL | For thumbnails and files |

### Blocked Endpoints

| Method | Endpoint | Response | Notes |
|--------|----------|----------|-------|
| **POST** | **/v1/user-service/my/task** | **400** | **The main blocker — see detailed analysis below** |
| POST | /v1/iot-service/api/user/print | 405 | GET-only endpoint despite docs claiming POST |
| GET | /v1/iot-service/api/user/files | 404 | Endpoint doesn't exist |

---

## Task Creation Deep Dive (POST /v1/user-service/my/task)

### What We Know About Required Fields

Discovered by sending minimal payloads and reading error messages:

| Field | Required? | Type | Evidence |
|-------|-----------|------|----------|
| modelId | Yes | string | Error: "field modelId is not set" when using snake_case |
| cover | Yes | string (URL) | Error: "field cover is not set" |
| plateIndex | Yes | int | Error: "field plateIndex is not set" |
| profileId | Yes | int | Error: "type mismatch for profileId" when sent as string |
| deviceId | Yes | string | No error (always included) |
| title | Yes | string | No error (always included) |

### The designId Problem

`designId` behaves inconsistently:

| Value sent | Response |
|-----------|----------|
| Omitted | Empty 400 |
| `null` | Empty 400 |
| `0` (int) | "type mismatch for field designId" |
| `"0"` (string) | "type mismatch for field designId" |
| `0.0` (float) | "strconv.ParseInt: parsing '0.0': invalid syntax" |
| `false` (bool) | "type mismatch for field designId" |
| `[]` (list) | "type mismatch for field designId" |

The `strconv.ParseInt` error reveals the **backend is written in Go** and expects to parse an integer.
But JSON int `0` causes "type mismatch" — this is paradoxical.

**From GitHub research:** Every source (Rust `u64`, C# `int`, TypeScript `number`, BambuStudio test data)
confirms `designId` should be integer `0` for non-MakerWorld prints. The type mismatch error with `0`
may have been caused by another field in the same payload being wrong (the API stops at the first error
and gives empty 400 for some validation failures).

### The Empty 400 Problem

With `{modelId, title, deviceId, profileId(int), cover, plateIndex}` — all confirmed-correct fields
and types — the API returns 400 with an **empty body**. This happens regardless of:

- Cover URL format (original, bare, dualstack-rewritten)
- Cover URL value (even dummy URLs like `https://example.com/test.png`)
- profileId value (real value or `0`)
- modelId value (real value or `"0"`)
- Additional fields (bedType, jobType, amsDetailMapping, taskUseAms, etc.)

**Conclusion (Feb 2026):** The empty 400 is a **deliberate server-side rejection** of third-party
clients. The proprietary `libbambu_networking.so` likely includes an undocumented signature, client
certificate, or challenge-response mechanism. **No third-party project has ever successfully called
this endpoint** — not OrcaSlicer (uses the same proprietary DLL), not coelacant1/Bambu-Lab-Cloud-API
(lists "print job submission" as "Not Yet Implemented"), not KITT (bypasses it entirely).

Evidence:
- All JSON parsing works (type mismatch errors fire correctly)
- All required field validation works ("field X is not set")
- Empty 400 has `Content-Length: 0` and NO `Content-Type` header — different code path from parsed errors
- Same result regardless of: Client-Name header, session cookies, request ordering, precise payload structure
- Same result with OrcaSlicer, BambuStudio, and Bambu Connect client headers

### Fields From GET /my/tasks Response

The task object returned by GET has ALL these fields (from actual successful prints):

```json
{
    "id": 775084413,
    "designId": 0,
    "designTitle": "",
    "designTitleTranslated": "",
    "instanceId": 0,
    "modelId": "US...",
    "title": "filename.3mf",
    "cover": "https://...",
    "status": 2,
    "failedType": 0,
    "feedbackStatus": 2,
    "startTime": "2026-02-26T21:10:57Z",
    "endTime": "2026-02-26T21:11:16Z",
    "weight": 0.31,
    "length": 10,
    "costTime": 562,
    "profileId": 634845344,
    "plateIndex": 1,
    "plateName": "",
    "deviceId": "...",
    "amsDetailMapping": [{"ams": 0, "sourceColor": "FCECD6FF", "targetColor": "FCECD6FF",
        "filamentId": "GFL99", "filamentType": "PLA", "targetFilamentType": "",
        "weight": 0.31, "nozzleId": 1, "amsId": 0, "slotId": 0}],
    "mode": "cloud_file",
    "isPublicProfile": false,
    "isPrintable": true,
    "isDelete": false,
    "deviceModel": "P1S",
    "deviceName": "bambu p1s",
    "bedType": "textured_plate",
    "jobType": 1,
    "material": {"id": "", "name": ""},
    "platform": "",
    "stepSummary": [],
    "nozzleInfos": [],
    "nozzleMapping": null,
    "snapShot": "",
    "extention": {"modelInfo": {"configs": [...], "compatibility": {...}}}
}
```

### All Discovered Fields (32 total)

Every field the API recognizes was discovered through type-probing (sending wrong types to trigger
"type mismatch" errors). All 32 fields with correct types in a single payload still returns empty 400.

**Required (confirmed by "field X is not set" errors):**
`modelId` (string), `cover` (string/URL), `plateIndex` (int), `profileId` (int), `deviceId` (string), `title` (string)

**Recognized (confirmed by "type mismatch" errors):**
`designId` (int), `designTitle` (string), `instanceId` (int), `weight` (float), `length` (int),
`costTime` (int), `plateName` (string), `deviceModel` (string), `deviceName` (string),
`amsDetailMapping` (array), `mode` (string), `isPublicProfile` (bool), `isPrintable` (bool),
`bedType` (string), `jobType` (int), `bedLeveling` (bool), `useAms` (bool), `layerInspect` (bool),
`timelapse` (bool), `amsMapping` (JSON string), `nozzleMapping` (JSON string), `flowCali` (bool),
`vibrationCali` (bool), `nozzleDiameter` (float), `status` (int), `feedbackStatus` (int)

**NOT recognized (no type error, no effect):** `projectId`, `subtaskId`, `fileUrl`, `md5`,
`configUrl`, `ossKey`, `signature`, `nonce`, `source`, `config`, `context`, `metadata`, etc.

### Key Differences from Successful Tasks

Compared to real successful tasks from GET /my/tasks:
- `bedType` should be `"textured_plate"` not `"auto"` (but making it match doesn't help)
- `jobType` should be `1` not `0` (but making it match doesn't help)
- `amsDetailMapping` should have actual entries with `sourceColor`, `filamentType`, etc.
- `length` is in centimeters (used_m × 100): 13.12m → 1312
- `deviceModel: "P1S"`, `deviceName: "bambu p1s"` (not empty strings)

---

## Project Detail Structure

After upload + PATCH, the project detail (`GET /v1/iot-service/api/user/project/{id}`) returns:

```
project
├── project_id: "666266555"
├── user_id: "1939415276"
├── model_id: "USb7b76c7521e40c"
├── status: "ACTIVE"
├── name: "plate_sliced.gcode.3mf"
├── profile_id: "636077332"
└── profiles[0]
    ├── profile_id: "636077332"
    ├── model_id: "USb7b76c7521e40c"
    ├── status: "ACTIVE"
    ├── url: (often empty — not always populated)
    ├── md5: "b19be138aaee..."
    └── context
        ├── compatibility: {dev_model_name: "C12", dev_product_name: "P1S", nozzle_diameter: 0.4}
        ├── configs[]: [{name: "plate_1.json", dir: "Metadata", url: "https://s3..."}]
        ├── plates[0]
        │   ├── index: 1
        │   ├── thumbnail: {name: "plate_1.png", url: "https://s3..."}
        │   ├── gcode: {name: "plate_1.gcode", url: "https://s3..."}
        │   ├── prediction: 9043 (seconds)
        │   ├── weight: 39.45 (grams)
        │   ├── filaments: [{id: "3", type: "PETG-CF", color: "#F2754E", used_m: "13.12", used_g: "39.45"}]
        │   ├── objects: [{identify_id: "8"}, {identify_id: "16"}, ...]
        │   └── warning: [{msg: "bed_temperature_too_high_than_filament", ...}]
        ├── materials[]: [{color: "F2754E", material: "PLA", filament_id: "GFL99"}, ...]
        └── flush_volumes_matrix: null
```

**Key insight:** The server extracts the 3mf and creates individual S3 objects for each file
(gcode, configs, thumbnails). The `plates[0].gcode.url` is a direct signed URL to `plate_1.gcode`.

---

## MQTT Observations

### Cloud MQTT (us.mqtt.bambulab.com:8883)

- Auth: username `u_{user_id}`, password = access token
- Commands require X.509 RSA-SHA256 signing with the extracted Bambu Connect private key
- Topic: `device/{device_id}/request` (publish) and `device/{device_id}/report` (subscribe)
- The `project_file` command is echoed back by the printer
- Printer goes to `gcode_state: FAILED` with `upload: {status: idle}` — it receives the command
  but does not download the file
- This happens with both `task_id: "0"` and fake task IDs
- The printer **validates task_id with the Bambu server in real time** before downloading
- **err_code 84033545** (0x5024009) = invalid/unrecognized task_id
- Tested task_id values that ALL fail with 84033545:
  - `"0"` (default)
  - `project_id` (e.g., "666425410")
  - UUID4 (e.g., "fed892ed-4cfe-4be2-81ec-7f860d2aca76")
  - Previous successful task_id (e.g., "775084413") — already consumed/completed
- Tested URL schemes that ALL fail:
  - HTTPS S3 URL (project download URL)
  - `cloud://private/{model_id}/{profile_id}/origin/filename.3mf`
- **There is no way to use cloud MQTT without a valid task_id from POST /my/task**

### X.509 Signing

Commands are signed with the Bambu Connect private key (publicly extracted Jan 2025):
- Cert ID: `GLOF3813734089-524a37c80000c6a6a274a47b3281`
- Algorithm: RSA-SHA256 with PKCS1v15 padding
- The signature wraps the command JSON in a `header` object

### LAN MQTT (printer_ip:8883)

- Auth: username `bblp`, password = access code (e.g., `19236776`)
- No signing required
- Uses `ftp://filename` URL format after FTPS upload to port 990
- All IDs set to `"0"` — no task/project/profile needed
- Implemented but not tested

---

## HTTP Headers

All requests use:
```
Authorization: Bearer {access_token}
Content-Type: application/json
X-BBL-Client-Name: OrcaSlicer
X-BBL-Client-Type: slicer
X-BBL-Client-Version: 02.03.01.00
User-Agent: bambu_network_agent/02.03.01.00
```

---

## Authentication

- Login: POST /v1/user-service/user/login with {account, password}
- May require email verification code or 2FA
- Token cached at `~/.bambu_cloud_token` as `{token, email}`
- Token used as Bearer auth for all API calls and as MQTT password
- User ID obtained from GET /v1/design-user-service/my/preference

---

## Alternative Approach: Slicer/Upload (KITT Method)

Discovered from the [KITT project](https://github.com/Jmi2020/KITT) (`services/fabrication/src/fabrication/drivers/bambu_cloud.py`).
This approach **bypasses** the entire project/task creation flow and uses a different upload endpoint.

### Flow

| Step | Action | Details |
|------|--------|---------|
| 1 | POST /v1/iot-service/api/slicer/upload | `{name, size, md5}` → `{url, osskey, file_url}` |
| 2 | PUT (presigned URL) | Upload 3mf binary with `Content-Type: application/octet-stream` |
| 3 | MQTT project_file | `url: cloud://{osskey}` or `file_url`, all IDs = "0", task_id = uuid4 |

### Key Differences from BambuStudio Flow

- Uses `/slicer/upload` instead of `/user/project` — no project_id, model_id, profile_id
- No upload notification, no PATCH, no task creation
- URL scheme: `cloud://{osskey}` instead of S3 HTTPS URL
- task_id is a locally-generated UUID, not server-validated
- No X.509 signing in KITT's implementation (may only work on older firmware)

### Test Results (Feb 2026)

- `/v1/iot-service/api/slicer/upload` → **404 Not Found**
- `/v1/iot-service/api/user/slicer/upload` → **404 Not Found**
- `/v1/cloud/file/upload` → **404 Route Not Found**
- `/v1/iot-service/api/user/file/upload` → **404 Not Found**

**Conclusion:** The slicer/upload endpoint has been **removed or was never real**. KITT's cloud
print code appears to be aspirational/untested — their print executor only uses local drivers
(MoonrakerDriver, BambuMqttDriver), not the cloud path.

---

## BambuStudio Source Code Analysis (Feb 2026)

### The Missing Config 3MF Upload (Step -3030)

Deep analysis of BambuStudio source code (`PrintJob.cpp`, `Plater.cpp`, `bambu_networking.hpp`)
revealed a **separate config-only 3MF** that BambuStudio uploads before the main file:

```
export_config_3mf() with:
  SaveStrategy::SkipModel | SaveStrategy::WithSliceInfo | SaveStrategy::SkipAuxiliary
```

This creates a metadata-only 3MF (no model geometry, no gcode, no images) containing:
- `Metadata/slice_info.config`
- `Metadata/plate_1.json`
- `Metadata/model_settings.config`
- `Metadata/project_settings.config`

The full BambuStudio cloud print flow (SP error codes):

| Step | Error Code | Description | Our Status |
|------|-----------|-------------|------------|
| 1 | -3010 | Create project | Working |
| 2 | -3020 | Check MD5 | Skipped |
| **3** | **-3030** | **Upload config 3MF to OSS** | **Implemented but doesn't fix POST /my/task** |
| 4 | -3040 | PUT notification | Working |
| 5 | -3050/-3060 | GET notification | Working |
| 6 | -3070 | File existence check | Skipped |
| 7 | -3080 | get_user_upload | Working (GET /user/upload) |
| 8 | -3090 | File over size check | Skipped |
| 9 | -3100 | Upload 3MF to OSS | Working |
| 10 | -3110 | PATCH project | Working |
| 11 | -3120 | POST task | **BLOCKED — empty 400** |
| 12 | -3130 | Wait printer ACK | Not reached |
| 13 | -3140 | ENC flag not ready | Not reached |

### Config Upload Implementation

The config 3MF is uploaded via `GET /v1/iot-service/api/user/upload` which accepts arbitrary params:
- `filename` → S3 URL for the file itself
- `size` → S3 URL for size metadata
- `model_id` → S3 URL linking upload to model
- `profile_id` → S3 URL linking upload to profile
- `project_id` → S3 URL linking upload to project
- `md5` → S3 URL for MD5 metadata

Each param creates a separate S3 object at `users/{uid}/{param_type}/{timestamp}/{value}`.
The server uses these to associate uploads with projects.

**Result:** Config 3MF upload succeeds (200 OK), but POST /my/task still returns empty 400.
The config upload is **not the missing piece** — the blocker is in the authentication/signing layer.

### Key Parameters from BambuStudio PrintParams

```cpp
params.config_filename      = job_data._3mf_config_path.string();  // separate config 3mf
params.filename             = job_data._3mf_path.string();          // main 3mf
params.origin_profile_id    = stoi(origin_profile_id);
params.origin_model_id      = origin_model_id;
params.preset_name          = profile_name;
params.project_name         = mall_model_name;
params.stl_design_id        = stl_design_id;
params.connection_type      = this->connection_type;
params.print_type           = this->m_print_type;
params.auto_offset_cali     = this->auto_offset_cali;
params.extruder_cali_manual_mode = this->extruder_cali_manual_mode;
params.task_ext_change_assist = this->task_ext_change_assist;
params.try_emmc_print       = this->could_emmc_print;
```

All parameters are passed to `m_agent->start_print(params, ...)` which delegates to the
**proprietary `libbambu_networking.so`** — the actual HTTP request construction is opaque.

---

## Full Task Payload Attempt

Added `cloud_create_task_full()` to try ALL fields from the GET /my/tasks response:

```json
{
    "designId": 0,
    "designTitle": "",
    "instanceId": 0,
    "modelId": "...",
    "title": "filename.3mf",
    "cover": "https://...",
    "status": 0,
    "feedbackStatus": 0,
    "weight": 39.45,
    "costTime": 9043,
    "profileId": 636077332,
    "plateIndex": 1,
    "plateName": "",
    "deviceId": "...",
    "deviceModel": "",
    "deviceName": "",
    "amsDetailMapping": [],
    "mode": "cloud_file",
    "isPublicProfile": false,
    "isPrintable": true,
    "bedType": "auto"
}
```

Pulls `weight` and `costTime` from project detail `plates[0]`. Also tries variants
without `designId` in case the type mismatch was masking other errors.

**Status:** Added to test script (Feb 2026). Results pending first test run.

---

## Key Files

- `/app/workspaces/pzfreo/fabprint/scripts/test_cloud_print.py` — main test script
- `/app/workspaces/pzfreo/fabprint/examples/gib-tuners-c13-10/output/plate_sliced.gcode.3mf` — test 3mf file (3.2MB, sliced for P1S, uses PETG-CF filament)
- `/tmp/bambu_studio_src/src/slic3r/GUI/Jobs/PrintJob.cpp` — BambuStudio cloud print flow
- `/tmp/bambu_studio_src/src/slic3r/Utils/bambu_networking.hpp` — Error codes, PrintParams struct

---

## External References

- [OpenBambuAPI cloud-http.md](https://github.com/Doridian/OpenBambuAPI/blob/main/cloud-http.md)
- [coelacant1/Bambu-Lab-Cloud-API](https://github.com/coelacant1/Bambu-Lab-Cloud-API)
- [BambuStudio source](https://github.com/bambulab/BambuStudio)
- [Bambu Connect key extraction](https://hackaday.com/2025/01/19/bambu-connects-authentication-x-509-certificate-and-private-key-extracted/)
- Task struct definitions: [Bambu.NET](https://github.com/ColdThunder11/Bambu.NET), [bambulab-rs](https://github.com/m1guelpf/bambulab-rs), [bambulab-dashboard](https://github.com/mohamedhadrami/bambulab-dashboard)
- [KITT project](https://github.com/Jmi2020/KITT) — working cloud print via slicer/upload + MQTT (bypasses POST /my/task)

---

## Conclusion and Recommended Path (Feb 2026)

### Cloud Print: Blocked

After exhaustive testing (32 discovered fields, 100+ API calls, BambuStudio source analysis,
config 3MF upload implementation, multiple MQTT task_id strategies), the conclusion is clear:

**POST /v1/user-service/my/task is deliberately restricted to the proprietary `libbambu_networking.so`.**

The endpoint works — BambuStudio successfully creates tasks through it every time. But it includes
an undocumented authentication mechanism (possibly a client certificate, HMAC signature, or
challenge-response token) that cannot be replicated without the proprietary library.

No third-party project has EVER successfully called this endpoint:
- **OrcaSlicer** → uses the same proprietary DLL (stubs for its own implementation)
- **coelacant1/Bambu-Lab-Cloud-API** → lists print submission as "Not Yet Implemented"
- **KITT** → bypasses task creation entirely (but its slicer/upload endpoint returns 404)
- **ha-bambulab** → read-only for cloud mode, uses LAN for printing
- **SimplyPrint** → switched to LAN-only after "Bambu Lab removed cloud API access" in late 2024

### Recommended: LAN Mode

LAN mode (FTPS + local MQTT) is the **proven working path** used by ALL successful third-party tools:

1. Upload 3MF via implicit FTPS (port 990, user `bblp`, password = access code)
2. Send MQTT `project_file` command via local broker (printer_ip:8883)
3. No signing required, no task creation, no cloud dependency

**Requirements:**
- Printer and control host must be on the same local network
- Developer Mode or LAN Mode enabled on the printer
- Printer IP address (discoverable via mDNS or cloud API)
- Access code: `19236776` (from GET /user/print)

For fabprint's architecture, this means running a **local print agent** on the same network
as the printer, which receives print jobs from the cloud scheduler and executes them via LAN mode.

### Alternative: Wrap libbambu_networking.so

The nuclear option: extract `libbambu_networking.so` from a BambuStudio or OrcaSlicer installation,
load it via Python ctypes, and call its `start_print()` function directly. This would work for
cloud printing but adds a proprietary binary dependency and is fragile across versions.

---

## Printer Details

- Model: P1S (dev_model_name: C12)
- Serial: 01P00A451601106
- LAN access code: 19236776 (from GET /user/print)
- AMS: 4-slot, gcode uses slots 1 and 2
- Online: yes
