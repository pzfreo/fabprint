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

This suggests either:
1. There's a required field we haven't discovered that the API doesn't name in its error
2. The empty 400 is actually the `designId: 0` type mismatch (API gives empty body for type errors on some fields)
3. Server-side validation fails (e.g., project not in correct state, or auth issue)

### Fields From GET /my/tasks Response

The task object returned by GET has these fields — some may be required for POST:

```json
{
    "id": 0,
    "designId": 0,
    "designTitle": "",
    "instanceId": 0,
    "modelId": "US...",
    "title": "filename.3mf",
    "cover": "https://...",
    "status": 2,
    "feedbackStatus": 0,
    "startTime": "2022-11-22T01:58:10Z",
    "endTime": "2022-11-22T02:54:12Z",
    "weight": 12.6,
    "length": 0,
    "costTime": 3348,
    "profileId": 12345678,
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

### Fields NOT Yet Tried

These fields appear in the GET response but we haven't tried sending them:
- `weight` (float, from plates[0].weight — e.g., 39.45)
- `costTime` (int, from plates[0].prediction — e.g., 9043 seconds)
- `status` (int, probably 0 for new)
- `feedbackStatus` (int, probably 0)
- `isPrintable` (bool, probably true)
- `isPublicProfile` (bool, probably false)
- `plateName` (string, probably empty)
- `mode` (string, "cloud_file")
- `designTitle` (string)
- `instanceId` (int)
- `length` (int)

### Next Steps for Task Creation

1. **Try the full GET /my/tasks response structure** — include weight, costTime, status, feedbackStatus,
   isPrintable, isPublicProfile, plateName, mode, and designId: 0 all together
2. **Pull weight/costTime from project detail** plates[0].weight and plates[0].prediction
3. **Try `designId: 0` again** but with all other fields correct — the earlier "type mismatch" may
   have been masked by another field error
4. **Check if project state matters** — maybe the server needs the project to be in a specific state
   before task creation will work

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
- The printer likely needs a **server-validated task_id** before it will download from S3

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

---

## Printer Details

- Model: P1S (dev_model_name: C12)
- Serial: 01P00A451601106
- LAN access code: 19236776 (from GET /user/print)
- AMS: 4-slot, gcode uses slots 1 and 2
- Online: yes
