# Bambu Lab Cloud Print API Research

## Overview

This document captures all findings from reverse-engineering the Bambu Lab cloud print API,
with the goal of triggering a cloud print from third-party code (not BambuStudio or Bambu Connect).

**Printer:** Bambu Lab P1S, serial `<DEVICE_ID>`, with 4-slot AMS
**Branch:** `cloud-print-test`
**Working solution:** `scripts/bambu_cloud_bridge.cpp` — C++ bridge to `libbambu_networking.so`
**Python wrapper:** `src/fabprint/cloud.py`
**Docker image:** `Dockerfile.cloud-bridge`

**Status: CLOUD PRINT WORKING** via C++ bridge wrapping `libbambu_networking.so`.

The bridge handles the full flow including RSA-2048 signing of `POST /my/task` via
the library's internal per-installation certificate. Pure Python handles steps 1–7
(project creation, upload, patch) but step 8 (task creation) requires RSA signing
that only the library can provide. Without signing, the task is created (HTTP 200)
but the printer rejects the MQTT command ("verification failed").

**Key architectural finding (Mar 2026):** Each installation gets its own RSA-2048 key pair.
The private key is stored encrypted in `BambuNetworkEngine.conf` and is never exposed in
standard PEM/DER or OpenSSL RSA structures — even during active signing. Exhaustive extraction
attempts via Frida, mitmproxy, and memory scanning all confirmed the key is not extractable.

---

## The Cloud Print Flow

Reconstructed from BambuStudio source code error codes in `bambu_networking.hpp`:

| Step | Error Code | Description | Status |
|------|-----------|-------------|--------|
| 1 | -3010 | Create project | Working |
| 2 | -3020 | Check MD5 | Skipped (library handles) |
| 3 | -3030 | Upload config 3MF to S3 | Working |
| 4 | -3040 | PUT notification (upload complete) | Working |
| 5 | -3050/-3060 | GET notification (poll confirmation) | Working |
| 6 | -3070 | File existence check | Working |
| 7 | -3080 | get_user_upload | Working |
| 8 | -3090 | File over size check | Working |
| 9 | -3100 | Upload main 3MF to S3 (0%→100%) | Working |
| 10 | -3110 | PATCH project | Working |
| 11 | -3120 | POST task (POST /v1/user-service/my/task) | **Working** (was 403, fixed by headers + CA) |
| 12 | -3130 | Wait for printer ACK via MQTT | Working |
| 13 | -3140 | Enc flag check | Working (retry with pushall) |

All steps are handled internally by `libbambu_networking.so` when called via our C++ bridge.
The `-3xxx` error codes are from BambuStudio's `start_print()` flow (different numbering from
the `-2xxx` series used in the older `start_send_gcode_to_sdcard()` flow).

---

## API Endpoints

### Working Endpoints

| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | /v1/user-service/user/login | Login (see Authentication section) |
| POST | /v1/user-service/user/sendemail/code | Send email verification code |
| POST | /v1/user-service/user/tfa | Two-factor authentication |
| GET | /v1/design-user-service/my/preference | Get user ID (uid) |
| GET | /v1/iot-service/api/user/bind | List bound devices |
| POST | /v1/iot-service/api/user/project | Create project |
| GET | /v1/iot-service/api/user/project | List projects |
| GET | /v1/iot-service/api/user/project/{id} | Get project detail |
| PATCH | /v1/iot-service/api/user/project/{id} | Update project |
| GET | /v1/iot-service/api/user/profile/{id} | Get profile detail |
| GET | /v1/iot-service/api/user/upload | Get signed upload URLs |
| PUT | (signed S3 URL) | Upload file to S3 |
| PUT | /v1/iot-service/api/user/notification | Notify upload complete |
| GET | /v1/iot-service/api/user/notification | Poll upload status |
| GET | /v1/user-service/my/setting | Get user settings |
| GET | /v1/iot-service/api/user/print | Get device print status + access code |
| GET | /v1/user-service/my/tasks | List recent print tasks |

### Endpoint Details

#### GET /v1/iot-service/api/user/bind (List Devices)

```
Response: {"devices": [...]}

Device object:
{
    "dev_id": "<DEVICE_ID>",
    "name": "bambu p1s",
    "online": true,
    "dev_product_name": "P1S",
    "dev_model_name": "C12"
}
```

#### POST /v1/iot-service/api/user/project (Create Project)

```
Request: {"name": "filename.3mf"}

Response:
{
    "project_id": "666266555",
    "model_id": "USb7b76c7521e40c",
    "profile_id": "636077332",
    "upload_url": "https://s3...",       // Presigned S3 PUT URL
    "upload_ticket": "abc123..."         // Required for notification step
}
```

Fallback: if creation fails, `GET /v1/iot-service/api/user/project` returns
`{"projects": [...]}`. Use the most recent project's `project_id` to fetch detail.

#### GET /v1/iot-service/api/user/upload (Get Signed Upload URLs)

```
Query params: ?filename=name.3mf&size=1234567
              (optional: &model_id=X&profile_id=Y&project_id=Z&md5=abc)

Response:
{
    "urls": [
        {"type": "filename", "url": "https://s3..."},   // PUT file binary here
        {"type": "size", "url": "https://s3..."},        // PUT file size as text/plain
        {"type": "md5", "url": "https://s3..."},         // PUT MD5 hash as text/plain
        {"type": "model_id", "url": "https://s3..."},    // PUT model_id as text/plain
        {"type": "profile_id", "url": "https://s3..."},  // PUT profile_id as text/plain
        {"type": "project_id", "url": "https://s3..."}   // PUT project_id as text/plain
    ]
}
```

Each `type` URL receives different content — the `filename` URL gets the binary file,
all others get text metadata. The metadata URLs link the upload to the correct project.

S3 URLs are at path `users/{uid}/{type}/{timestamp}/{value}`.

**Important:** When PUTting to presigned S3 URLs, send with **empty headers** (`headers={}`).
Adding extra headers can break the S3 signature validation.

#### PUT /v1/iot-service/api/user/notification (Upload Complete)

```
# Captured from BambuConnect (Mar 2026) — use this format:
{"action": "upload", "upload": {"ticket": "abc123", "origin_file_name": "connect_config.3mf"}}

# Extended payload (fallback):
{"action": "upload", "upload": {"ticket": "abc123", "origin_file_name": "file.3mf",
            "status": "complete", "file_size": 0}}
```

Note: The `"action": "upload"` top-level key is required — captured from BC traffic.
Earlier tests used the nested-only format and it also worked, but include `action` to match BC exactly.

#### GET /v1/iot-service/api/user/notification (Poll Upload)

```
Query params: ?action=upload&ticket=abc123
Response: (confirmation JSON, varies)
```

Poll with 2-second intervals, up to 3 attempts.

#### GET /v1/iot-service/api/user/project/{id} (Project Detail)

Requires polling — profile URL may be empty initially while server processes the 3MF.
Poll up to 15 times with 2-second delays until `profiles[0].url` is populated.

(See "Project Detail Structure" section below for full response format.)

#### GET /v1/iot-service/api/user/profile/{id} (Profile Detail)

```
Query params: ?model_id=USb7b76c7521e40c

Response:
{
    "url": "https://s3.us-west-2.amazonaws.com/...",
    "md5": "b19be138aaee...",
    "context": {
        "plates": [{
            "thumbnail": {"url": "https://s3..."},
            ...
        }]
    }
}
```

Fallback when project detail URL isn't immediately available.

#### PATCH /v1/iot-service/api/user/project/{id} (Update Project)

Multiple payload variants work. **BambuConnect (Mar 2026) uses:**

```json
{
  "profile_id": "639766066",
  "profile_print_3mf": [
    {"comments": "no_ips", "md5": "<MD5_OF_3MF>", "plate_idx": 1, "url": "https://s3..."}
  ]
}
```

Simpler variants that also work:
1. `{"name": "file.3mf", "profile_id": "636077332"}` (profile_id as string)
2. `{"name": "file.3mf", "status": "uploaded"}`
3. `{"model_id": "USb7b76c7521e40c", "name": "file.3mf"}`
4. `{"name": "file.3mf", "profile_id": "636077332", "model_id": "USb7b76c7521e40c"}`

### Previously Blocked Endpoints (now working via library)

| Method | Endpoint | Direct HTTP | Via Library | Notes |
|--------|----------|-------------|-------------|-------|
| **POST** | **/v1/user-service/my/task** | **Working** ✅ | **Working** | Requires BambuConnect headers (`x-bbl-client-type: connect`) — slicer headers fail |
| POST | /v1/iot-service/api/user/print | 405 | N/A | GET-only endpoint |
| GET | /v1/iot-service/api/user/files | 404 | N/A | Endpoint doesn't exist |

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

## MQTT Protocol

### Cloud MQTT Broker

- **Broker:** `us.mqtt.bambulab.com:8883` (TLS required)
- **Username:** `u_{user_id}` (user_id from `/my/preference`, integer as string)
- **Password:** Access token (from login)
- **Client ID:** Any unique string (e.g., `fabprint-test-{device_id[:8]}`)
- **Keepalive:** 60 seconds
- **TLS:** `ssl.CERT_REQUIRED`, `ssl.PROTOCOL_TLS`
- **Publish topic:** `device/{device_id}/request`
- **Subscribe topic:** `device/{device_id}/report`
- Commands require X.509 RSA-SHA256 signing (see below)

### LAN MQTT Broker

- **Broker:** `{printer_ip}:8883` (TLS, self-signed cert)
- **Username:** `bblp` (hardcoded)
- **Password:** Access code (from GET /user/print, e.g., `19236776`)
- **Client ID:** Any unique string (e.g., `fabprint-lan-{serial[:8]}`)
- **Keepalive:** 60 seconds
- **TLS:** `ssl.CERT_NONE`, `check_hostname = False` (printer uses self-signed cert)
- **Topics:** Same format as cloud but uses serial instead of dev_id
- **No signing required** — commands are published as plain JSON

### MQTT Commands

#### project_file (Start Print)

**Cloud variant:**
```json
{
  "print": {
    "sequence_id": "1",
    "command": "project_file",
    "param": "Metadata/plate_1.gcode",
    "project_id": "666425410",
    "profile_id": "636077332",
    "task_id": "775084413",
    "subtask_id": "0",
    "subtask_name": "filename.3mf",
    "file": "",
    "url": "https://bucket.s3.dualstack.us-west-2.amazonaws.com/private/...",
    "md5": "b19be138aaee...",
    "timelapse": false,
    "bed_type": "auto",
    "bed_levelling": true,
    "flow_cali": true,
    "vibration_cali": true,
    "layer_inspect": false,
    "ams_mapping": [0, 1, 2, 3],
    "use_ams": true
  }
}
```

**LAN variant:**
```json
{
  "print": {
    "sequence_id": "1",
    "command": "project_file",
    "param": "Metadata/plate_1.gcode",
    "project_id": "0",
    "profile_id": "0",
    "task_id": "0",
    "subtask_id": "0",
    "subtask_name": "filename.3mf",
    "file": "",
    "url": "ftp://filename.3mf",
    "md5": "",
    "timelapse": false,
    "bed_type": "auto",
    "bed_levelling": true,
    "flow_cali": true,
    "vibration_cali": true,
    "layer_inspect": true,
    "ams_mapping": [0, 1, 2, 3],
    "use_ams": true
  }
}
```

Key differences: LAN uses `ftp://filename` URL, all IDs set to `"0"`, no signing.

#### pushall (Request Full Status)

```json
{
  "pushing": {
    "sequence_id": "1",
    "command": "pushall",
    "version": 1,
    "push_target": 1
  }
}
```

No signing required even on cloud MQTT.

#### pause / resume / stop

```json
{"print": {"sequence_id": "1", "command": "pause", "param": ""}}
{"print": {"sequence_id": "1", "command": "resume", "param": ""}}
{"print": {"sequence_id": "1", "command": "stop", "param": ""}}
```

All three require X.509 signing on cloud MQTT.

### MQTT Report Messages (Printer → Client)

Messages on the report topic contain a `print` object with state:

```json
{
  "print": {
    "command": "project_file",
    "result": "ok",
    "reason": "",
    "mc_percent": 45,
    "gcode_state": "RUNNING",
    "upload": {
      "status": "idle",
      "progress": 0
    }
  }
}
```

**Key fields:**
- `gcode_state`: `IDLE`, `RUNNING`, `PAUSED`, `FAILED`, `FINISH`
- `mc_percent`: Print progress (0-100)
- `upload.status`: File download progress (`idle`, `downloading`)
- `result`: Command response (`ok` or error)
- `reason`: Error description

**Known error code:** `84033545` (0x5024009) = invalid/unrecognized task_id.
The printer validates task_id with the Bambu server in real time before downloading.

### X.509 Command Signing (Cloud Only)

Commands on cloud MQTT must be signed with the Bambu Connect private key
(publicly extracted Jan 2025).

**Signing process:**
1. Serialize the command dict to JSON bytes
2. Sign with RSA-SHA256 (PKCS1v15 padding) using the private key
3. Base64-encode the signature
4. Add a `header` object to the command

**Signed message structure:**
```json
{
  "print": { ... command payload ... },
  "header": {
    "sign_ver": "v1.0",
    "sign_alg": "RSA_SHA256",
    "sign_string": "base64_encoded_signature",
    "cert_id": "GLOF3813734089-524a37c80000c6a6a274a47b3281",
    "payload_len": 456
  }
}
```

- `payload_len` = byte length of the command JSON (without the header)
- `cert_id` = `GLOF3813734089-524a37c80000c6a6a274a47b3281`
- Private key: Embedded in every copy of the Bambu Connect app (see `test_cloud_print.py`)

### Cloud MQTT task_id Validation

The printer validates `task_id` with the Bambu server before downloading. All of these fail
with error 84033545:
- `"0"` (default)
- `project_id` (e.g., "666425410")
- UUID4 (random)
- Previously consumed task_id
- `cloud://private/{model_id}/{profile_id}/origin/filename.3mf`

**There is no way to use cloud MQTT without a valid task_id from POST /my/task.**
This is why the library bridge is necessary — it handles task creation internally.

---

## S3 URL Format Conversions

The API returns S3 URLs in **path-style** format, but MQTT commands may require
**virtual-hosted dualstack** format. The conversion is critical for cloud printing.

**Path-style (from API):**
```
https://s3.us-west-2.amazonaws.com/or-cloud-model-prod/private/user/123/file.3mf?X-Amz-...
```

**Virtual-hosted dualstack (for MQTT):**
```
https://or-cloud-model-prod.s3.dualstack.us-west-2.amazonaws.com/private/user/123/file.3mf?X-Amz-...
```

**Conversion regex:**
```python
match = re.match(r"https://s3\.([^.]+)\.amazonaws\.com/([^/]+)(/.*)", url)
if match:
    region, bucket, key_params = match.groups()
    url = f"https://{bucket}.s3.dualstack.{region}.amazonaws.com{key_params}"
```

The dualstack format is the most reliable for cloud MQTT `project_file` commands.
Bare URLs (without query string) and path-style URLs sometimes fail.

---

## FTPS Upload (LAN Mode)

For LAN printing, files are uploaded to the printer via implicit FTPS:

- **Protocol:** Implicit TLS (connection starts encrypted, NOT explicit AUTH TLS)
- **Port:** 990
- **Username:** `bblp`
- **Password:** Access code (from GET /user/print)
- **TLS:** Self-signed cert, `verify_mode = ssl.CERT_NONE`
- **Data protection:** Must call `prot_p()` after login
- **Upload command:** `STOR {filename}` (uploads to printer's SD card root)
- **No explicit folder path** — files go to the default location

Python implementation requires a custom `FTP_TLS` subclass that wraps the socket in SSL
before the FTP handshake (standard `FTP_TLS` uses explicit TLS with AUTH TLS after connect).

---

## Config 3MF Upload

BambuStudio uploads a separate **config-only 3MF** before the main file (step -3030).
This 3MF contains only metadata — no model geometry, gcode, or images.

### Contents

Files to include:
- `[Content_Types].xml`
- `_rels/.rels`
- `Metadata/slice_info.config`
- `Metadata/model_settings.config`
- `Metadata/project_settings.config`
- `Metadata/_rels/model_settings.config.rels`
- `Metadata/plate_*.json` (all plate JSON files)

Files to exclude: model geometry, gcode, images, auxiliary files.

### Upload Process

Uses the same `GET /user/upload` endpoint as the main file, but with additional
metadata-linking parameters (`model_id`, `profile_id`, `project_id`).

Each returned URL type receives different content:
- `filename`: Binary config 3MF data
- `size`: File size as `text/plain`
- `md5`: MD5 hash as `text/plain`
- `model_id`, `profile_id`, `project_id`: ID values as `text/plain`

Corresponds to BambuStudio's `export_config_3mf()` with:
```
SaveStrategy::SkipModel | SaveStrategy::WithSliceInfo | SaveStrategy::SkipAuxiliary
```

---

## Authentication

### Base URL

All API endpoints use: `https://api.bambulab.com`

### Login Flow

Three distinct authentication flows, determined by the server response to the initial login:

**1. Direct Password Login**

```
POST /v1/user-service/user/login
Body: {"account": "user@email.com", "password": "...", "apiError": ""}
Response: {"accessToken": "eyJ..."}
```

If `accessToken` is present in the response, login is complete.

**2. Email Verification Code Flow**

Triggered when the response contains `"loginType": "verifyCode"` and no `accessToken`:

```
# Step 1: Request verification code
POST /v1/user-service/user/sendemail/code
Body: {"email": "user@email.com", "type": "codeLogin"}
Response: 200 OK (code sent to email)

# Step 2: Login with code
POST /v1/user-service/user/login
Body: {"account": "user@email.com", "code": "123456"}
Response: {"accessToken": "eyJ..."}
```

**3. Two-Factor Authentication (2FA)**

Triggered when the response contains `"tfaKey": "..."` and no `accessToken`:

```
POST /v1/user-service/user/tfa
Body: {"tfaKey": "...", "tfaCode": "123456"}
Response: {"accessToken": "eyJ..."}
```

### Token Caching

- Stored in `~/.config/fabprint/credentials.toml` `[cloud]` section (via `fabprint login` or `fabprint setup`)
- Legacy location: `~/.bambu_cloud_token` (used by standalone test scripts only)
- Format in credentials.toml: `token`, `refresh_token`, `email`, `uid` fields
- Validation: attempt `GET /my/preference` with cached token; fall back to fresh login on failure

### User ID Resolution

```
GET /v1/design-user-service/my/preference
Headers: Authorization: Bearer {access_token}
Response: {"uid": 1939415276, ...}
```

- `uid` is an integer, must be converted to string for MQTT: `u_{uid}`
- Token is used as Bearer auth for all API calls AND as MQTT password

### HTTP Headers for Direct API Calls

```
Authorization: Bearer {access_token}
Content-Type: application/json
X-BBL-Client-Name: OrcaSlicer
X-BBL-Client-Type: slicer
X-BBL-Client-Version: 02.03.01.00
User-Agent: bambu_network_agent/02.03.01.00
```

Note: These headers work for REST API calls. For cloud printing via the library bridge,
different headers are required (see "Required HTTP Headers" in the Solution section).

### Response Headers (Useful for Debugging)

- `X-Request-Id`: Client request tracking ID
- `X-Trace-Id`: Server-side trace ID (useful for correlating with 400/403 errors)

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
| **3** | **-3030** | **Upload config 3MF to OSS** | **Working** (via library; direct HTTP alone insufficient) |
| 4 | -3040 | PUT notification | Working |
| 5 | -3050/-3060 | GET notification | Working |
| 6 | -3070 | File existence check | Working (via library) |
| 7 | -3080 | get_user_upload | Working (GET /user/upload) |
| 8 | -3090 | File over size check | Working (via library) |
| 9 | -3100 | Upload 3MF to OSS | Working |
| 10 | -3110 | PATCH project | Working |
| 11 | -3120 | POST task | **Working** (via library; direct HTTP returns 400/403) |
| 12 | -3130 | Wait printer ACK | Working |
| 13 | -3140 | ENC flag not ready | Working (retry with pushall) |

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

**Result:** All 32+ fields with correct types still returns empty 400 when called via
direct HTTP. This confirms the blocker is authentication/signing, not missing fields.
Solved by using the library bridge instead of direct HTTP.

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

## Solution: C++ Bridge to libbambu_networking.so (WORKING)

### Background

After exhaustive testing of direct HTTP calls (32 discovered fields, 100+ API calls,
BambuStudio source analysis, config 3MF upload implementation, multiple MQTT task_id
strategies), we confirmed that **POST /v1/user-service/my/task cannot be called via
plain HTTP** — the proprietary `libbambu_networking.so` includes an undocumented
authentication mechanism (likely HMAC signatures or client certificates embedded in the
binary).

The solution: **wrap the proprietary library in a C++ bridge** that loads it via `dlopen()`
and calls its functions directly. This is the same approach BambuStudio and OrcaSlicer use.

### Architecture

```
fabprint pipeline
    → cloud.py (Python wrapper)
        → bambu_cloud_bridge (C++ CLI binary)
            → libbambu_networking.so (proprietary, loaded via dlopen)
                → Bambu Lab cloud API (HTTP + MQTT)
                    → Printer
```

For Mac/cross-platform use, the bridge is packaged in a Docker container
(`Dockerfile.cloud-bridge`, `--platform linux/amd64`) since the library is x86_64 Linux only.

### The 403 "Internal Blocking" Fix

The initial bridge attempt got through steps 1-10 (project creation, S3 upload, project
patching) but failed at step 11 (POST /my/task) with HTTP 403 "internal blocking".

**Root cause — two issues:**

1. **Wrong HTTP headers.** The bridge was identifying as `bambu_connect`/`device` instead of
   `BambuStudio`/`slicer`. Additionally, four required headers were missing:
   `X-BBL-OS-Type`, `X-BBL-OS-Version`, `X-BBL-Device-ID`, `X-BBL-Language`.

2. **Missing CA certificate bundle.** The library's embedded curl could not verify SSL
   certificates. Fix: `setenv("CURL_CA_BUNDLE", "/etc/ssl/certs/ca-certificates.crt", 0)`.

### Required HTTP Headers (7 total)

These headers must be set via `set_extra_http_header()` **AFTER** `bambu_network_start()`
but **BEFORE** `change_user()`:

```
X-BBL-Client-Type:    slicer
X-BBL-Client-Name:    BambuStudio
X-BBL-Client-Version: 02.05.01.52
X-BBL-OS-Type:        linux
X-BBL-OS-Version:     6.8.0
X-BBL-Device-ID:      <unique-id>      (e.g. "fabprint-headless-001")
X-BBL-Language:       en
```

**Critical:** Using `bambu_connect` as Client-Name or `device` as Client-Type will cause
the POST /my/task to return 403. The library probably passes these headers to the API, and
the server uses them for access control.

### Critical Init Sequence

The order of operations matters. Getting it wrong causes SSL errors, auth failures, or
MQTT disconnects:

```
 1. setenv("CURL_CA_BUNDLE", "/etc/ssl/certs/ca-certificates.crt")  ← before anything
 2. bambu_network_create_agent("/tmp/bambu_agent/log")
 3. bambu_network_init_log()
 4. bambu_network_set_config_dir("/tmp/bambu_agent/config")
 5. bambu_network_set_cert_file("/tmp/bambu_agent/cert", "slicer_base64.cer")
 6. bambu_network_set_country_code("US")
 7. bambu_network_start()
 8. bambu_network_set_extra_http_header({...7 headers...})     ← AFTER start()
 9. Set all callbacks (server_connected, message, http_error, etc.)
10. bambu_network_change_user(user_json)                       ← BEFORE connect_server
11. bambu_network_connect_server()  → wait for server_connected callback rc=0
12. bambu_network_set_user_selected_machine(device_id)
13. bambu_network_start_subscribe("device")
14. sleep(3s)  ← must wait for subscription to establish
15. bambu_network_send_message(pushall)  → wait ~20s for enc flag
16. bambu_network_start_print(params, callbacks)
```

### PrintParams Struct (Key Fields)

```cpp
params.dev_id           = device_id;
params.filename         = "/path/to/file.3mf";     // MUST be .3mf (not .gcode.3mf)
params.config_filename  = "/path/to/config.3mf";   // optional config-only 3MF
params.plate_index      = 1;
params.connection_type  = "cloud";
params.print_type       = "from_normal";
params.ams_mapping      = "[0,1,2,3]";
params.task_use_ams     = true;
params.task_bed_type    = "auto";
params.use_ssl_for_mqtt = true;
params.ftp_folder       = "sdcard/";
// Most other fields can be empty/default
```

### Library Function Signatures

```cpp
// Agent lifecycle
void*  create_agent(string log_dir);
int    destroy_agent(void* agent);      // WARNING: can hang on MQTT threads
int    init_log(void* agent);
int    set_config_dir(void* agent, string dir);
int    set_cert_file(void* agent, string dir, string filename);
int    set_country_code(void* agent, string code);
int    start(void* agent);

// Auth & connection
int    change_user(void* agent, string user_json);
bool   is_user_login(void* agent);
int    connect_server(void* agent);
bool   is_server_connected(void* agent);

// Device & messaging
int    set_user_selected_machine(void* agent, string dev_id);
int    start_subscribe(void* agent, string module);  // "device"
int    send_message(void* agent, string dev_id, string json, int qos);  // 4 params!
int    send_message_to_printer(void* agent, string dev_id, string json, int qos, int flag);

// Printing
int    start_print(void* agent, PrintParams params,
                   OnUpdateStatusFn, WasCancelledFn, OnWaitFn);

// HTTP headers
int    set_extra_http_header(void* agent, map<string,string> headers);

// Callbacks (all set via set_on_*_fn)
typedef function<void(int rc, int reason)>          OnServerConnectedFn;
typedef function<void(string dev_id, string msg)>   OnMessageFn;
typedef function<void(unsigned code, string body)>  OnHttpErrorFn;
typedef function<void(string topic)>                OnPrinterConnectedFn;
typedef function<void(int online, bool login)>      OnUserLoginFn;
typedef function<string()>                          GetCountryCodeFn;
```

### Error Codes

| Code | Constant | Meaning | Solution |
|------|----------|---------|----------|
| -3140 | ENC_FLAG_NOT_READY | Encryption flag not available | Send pushall, wait 20s, retry |
| -3120 | POST_TASK_FAILED | Task creation failed (was 403) | Fix headers + CA bundle |
| -3070 | FILE_NOT_EXIST | File path wrong | Use .3mf extension, not .gcode.3mf |
| -3010 | REQUEST_PROJECT_FAILED | SSL cert verification failed | Set CURL_CA_BUNDLE env var |
| -1 | Generic error | Printer busy / timeout | Printer may be printing already |
| 0 | Success | Print started | — |

### Known Gotchas

1. **stdout noise:** The library prints `use_count = 4` to stdout from background MQTT
   threads. Must redirect stdout to `/dev/null` via `dup2()` during library calls, and
   restore only for JSON output.

2. **Process hanging on exit:** `destroy_agent()` and `dlclose()` hang waiting for MQTT
   threads to close. Use `_exit()` instead of normal return to force immediate exit.

3. **send_message signatures:** There are two versions — `send_message` (4 params: agent,
   dev_id, json, qos) and `send_message_to_printer` (5 params: + flag). The legacy 4-param
   version works for pushall. Using the wrong signature causes crashes or -4 returns.

4. **Pushall timing:** Must wait 3 seconds after `start_subscribe()` before sending pushall,
   and wait ~20 seconds after pushall for the encryption flag to arrive.

5. **Token JSON format:** The `change_user()` function expects a specific JSON wrapper:
   ```json
   {"data":{"token":"...","refresh_token":"...","expires_in":"7200",
    "refresh_expires_in":"2592000","user":{"uid":"...","name":"...",
    "account":"...","avatar":"..."}}}
   ```

6. **Cert file:** `slicer_base64.cer` is a DigiCert wildcard cert for MQTT TLS, downloaded
   from BambuStudio's GitHub repo. Must be at the path passed to `set_cert_file()`.

7. **Library source:** Download from Bambu CDN:
   `https://public-cdn.bambulab.com/upgrade/studio/plugins/01.10.02.89/linux_01.10.02.89.zip`

### CLI Tool

The bridge is packaged as a CLI with 4 subcommands:

```
bambu_cloud_bridge print  <3mf> <device_id> <token_file> [options]
bambu_cloud_bridge status <device_id> <token_file> [-v]
bambu_cloud_bridge tasks  <token_file> [--limit N]
bambu_cloud_bridge cancel <device_id> <token_file> [-v]
```

All commands produce JSON on stdout, logs go to stderr (`-v` for verbose).

---

## Historical: Direct HTTP Attempts (Failed)

The sections above document the working solution. The sections below are preserved as a
record of the direct HTTP approach that was ultimately unsuccessful.

### Task Creation Deep Dive (POST /v1/user-service/my/task) — Direct HTTP

Direct HTTP calls to POST /my/task always fail:
- With correct fields and types: returns empty 400 (no body)
- The 400 has `Content-Length: 0` and no `Content-Type` — a different code path from
  field validation errors, indicating server-side rejection of the client identity
- The library adds undocumented auth (signatures/certificates) that we cannot replicate

No third-party project has ever successfully called this endpoint via direct HTTP:
- **OrcaSlicer** → uses the same proprietary DLL
- **coelacant1/Bambu-Lab-Cloud-API** → "Not Yet Implemented"
- **KITT** → bypasses it entirely (slicer/upload endpoint returns 404 now)
- **ha-bambulab** → read-only for cloud, uses LAN for printing
- **SimplyPrint** → switched to LAN-only

---

## Alternative Approaches

### LAN Mode (FTPS + Local MQTT)

Still a viable option for printers on the same network:

1. Upload 3MF via implicit FTPS (port 990, user `bblp`, password = access code)
2. Send MQTT `project_file` command via local broker (printer_ip:8883)
3. No signing, no task creation, no cloud dependency

**Requirements:** Same network, Developer/LAN Mode enabled, printer IP address.

### Cloud Bridge (Current Solution)

Wrap `libbambu_networking.so` via C++ `dlopen()` — this is what we implemented and it works.
See the "Solution" section above.

### Pure Python / Direct HTTP (NEW — Feasible!)

**Discovery:** The Bambu Lab cloud API CAN be called directly via standard HTTP
without the proprietary library. No request signing, HMAC, or special authentication
is required beyond the Bearer token and X-BBL-* headers.

#### What Works via Pure HTTP

| Endpoint | Method | Status |
|----------|--------|--------|
| /v1/iot-service/api/user/bind | GET | **Working** — lists devices |
| /v1/iot-service/api/user/print | GET | **Working** — device status + access code |
| /v1/design-user-service/my/preference | GET | **Working** — user profile |
| /v1/user-service/my/tasks | GET | **Working** — list print tasks |
| /v1/iot-service/api/user/project | POST | **Working** — create project, returns S3 URL |
| /v1/iot-service/api/user/project/{id} | GET | **Working** — project details |
| /v1/iot-service/api/user/project/{id} | PATCH | **Partial** — only `profile_id` accepted |
| S3 presigned URL | PUT | **Working** — file upload (no Content-Type header!) |
| /v1/user-service/my/task | POST | **Working** — creates task (see required fields below) |

#### Required Headers

**For POST /my/task (new models) — use BambuConnect headers (captured Mar 2026):**
```
Content-Type: application/json
Authorization: Bearer <token>
x-bbl-client-name: BambuConnect
x-bbl-client-type: connect
x-bbl-client-version: v2.2.1-beta.2
x-bbl-device-id: <unique UUID, e.g. generated once and reused>
x-bbl-language: en-GB
```

**For all other endpoints — slicer headers also work:**
```
Content-Type: application/json
Authorization: Bearer <token>
X-BBL-Client-Type: slicer
X-BBL-Client-Name: BambuStudio
X-BBL-Client-Version: 02.05.01.52
X-BBL-OS-Type: linux
X-BBL-OS-Version: 6.8.0
X-BBL-Device-ID: <any unique hex string>
X-BBL-Language: en
```

No special User-Agent is required for REST endpoints (unlike the library's
`bambu_network_agent/01.09.05.01`).

**Key finding:** `POST /my/task` for HTTP-created models requires `x-bbl-client-type: connect`.
Slicer headers return 400 empty for new HTTP-created models.

#### Task Creation — POST /v1/user-service/my/task

**Required fields** (server returns error message if missing):
- `deviceId` (string) — printer serial number
- `modelId` (string) — from project creation response
- `profileId` (integer) — from project creation response
- `plateIndex` (integer) — plate number (usually 1)
- `title` (string) — task display name
- `cover` (string) — cover image URL

**Critical required field** (server returns empty 400 if missing, NO error message):
- `mode` (string) — must be `"cloud_file"` for cloud prints

**Optional fields:**
- `designId` (integer) — default 0
- `amsDetailMapping` (array) — AMS filament mapping, default `[]`
- `bedType` (string) — `"auto"` or specific bed type
- `timelapse` (boolean) — enable timelapse recording
- `useAms` (boolean) — use AMS filament system
- `flowCalibration` (boolean) — auto flow calibration
- `bedLevelling` (boolean) — auto bed leveling
- `vibrationCalibration` (boolean) — vibration compensation
- `layerInspect` (boolean) — first layer inspection

**Example request:**
```json
{
    "deviceId": "<DEVICE_ID>",
    "modelId": "USf9309334c60f9f",
    "profileId": 636989901,
    "plateIndex": 1,
    "title": "my_print",
    "cover": "https://public-cdn.bambulab.cn/default/task.jpg",
    "mode": "cloud_file"
}
```

**Response:** `{"id": <task_id>}` (HTTP 200)

#### Complete Task Body (Captured from BambuConnect v2.2.1, Mar 2026)

This is the exact payload BC sent when triggering a real print (5-filament AMS config):

```json
{
  "amsDetailMapping": [
    {"ams": -1, "amsId": 255, "slotId": 255, "filamentId": "", "filamentType": "", "targetColor": ""},
    {"ams": -1, "amsId": 255, "slotId": 255, "filamentId": "", "filamentType": "", "targetColor": ""},
    {"ams": 2, "amsId": 0, "slotId": 2, "nozzleId": 0, "sourceColor": "F2754EFF", "targetColor": "2850E0FF", "filamentType": "PETG-CF", "targetFilamentType": "PETG-CF"},
    {"ams": -1, "amsId": 255, "slotId": 255, "filamentId": "", "filamentType": "", "targetColor": ""},
    {"ams": -1, "amsId": 255, "slotId": 255, "filamentId": "", "filamentType": "", "targetColor": ""}
  ],
  "amsMapping": [-1, -1, 2, -1, -1],
  "amsMapping2": [
    {"amsId": 255, "slotId": 255},
    {"amsId": 255, "slotId": 255},
    {"amsId": 0, "slotId": 2},
    {"amsId": 255, "slotId": 255},
    {"amsId": 255, "slotId": 255}
  ],
  "bedType": "textured_plate",
  "cover": "",
  "deviceId": "<DEVICE_ID>",
  "filamentSettingIds": ["", "", "GFSG50", "", ""],
  "isPublicProfile": false,
  "jobType": 1,
  "layerInspect": true,
  "mode": "cloud_file",
  "modelId": "USbf276844668d16",
  "plateIndex": 1,
  "profileId": 639766066,
  "title": "plate_sliced.gcode.3mf",
  "useAms": true,
  "timelapse": false,
  "bedLeveling": true,
  "flowCali": false,
  "extrudeCaliManualMode": 1,
  "autoBedLeveling": 2,
  "extrudeCaliFlag": 2,
  "nozzleOffsetCali": 2,
  "nozzleInfos": [],
  "primeVolumeMode": "Default"
}
```

Notes:
- `amsId: 255` / `slotId: 255` = unused slot (no filament)
- `amsId: 0` / `slotId: 2` = AMS unit 0, slot 2 (0-indexed)
- `profileId` is integer here; PATCH endpoint uses string form
- `cover: ""` is fine — empty string works (BC uses thumbnail URL when available)
- `filamentSettingIds` indexed by filament slot (empty string for unused slots)

#### S3 Upload

The presigned URL from project creation response should be used with a simple PUT.
**Do NOT set Content-Type header** — S3 signature validation will fail.

```python
with open('file.3mf', 'rb') as f:
    requests.put(upload_url, data=f)  # No headers!
```

#### Pure Python Cloud Print (8-step flow)

**Status: TASK CREATED BUT PRINTER REJECTS — task appears in Bambu Handy but printer does not start
(MQTT command rejected: "MQTT Command verification failed"). Signing is required — see below.**

**Implemented as `mode = "cloud-http"` (experimental) in fabprint.**

**Flow (8 steps, REST-only — captured from BambuConnect v2.2.1 TLS traffic, Mar 2026):**

1. **Create project** — `POST /v1/iot-service/api/user/project` with `{"name": "file.3mf"}`
   Returns: `project_id`, `model_id`, `profile_id` (string), `upload_url`, `upload_ticket`.

2. **Upload config-only 3MF** — `PUT upload_url` with config-only 3MF bytes (gcode stripped),
   **NO Content-Type header**. BC strips gcode from the 3MF before this upload — it does NOT
   upload the full gcode.3mf here. See `_strip_gcode_from_3mf()` in `cloud.py`.
   The `origin_file_name` in the notification should be `"connect_config.3mf"`.

3. **Notify** — `PUT /v1/iot-service/api/user/notification` with:
   `{"action": "upload", "upload": {"ticket": upload_ticket, "origin_file_name": "connect_config.3mf"}}`.
   Note: `origin_file_name` must be `"connect_config.3mf"`, not the actual filename.

4. **Poll `GET /notification`** — `GET /v1/iot-service/api/user/notification?action=upload&ticket=...`
   Wait until `message != "running"`. Returns `"success"` when done (~2-4 seconds, 1-2 polls).
   **This step is required** — proceeding before processing completes causes PATCH to fail
   with `code 11: "Wrong file format"`.

5. **Get gcode upload URL** — `GET /v1/iot-service/api/user/upload?model_id=X&profile_id=Y&project_id=Z&filename=file.gcode.3mf&md5=<MD5>&size=<size>`
   Returns presigned S3 URL for the full gcode.3mf.

6. **Upload full gcode.3mf** — `PUT <gcode_upload_url>` with the full `.gcode.3mf` bytes,
   **NO Content-Type header**.

7. **PATCH project** — `PATCH /v1/iot-service/api/user/project/{id}` with:
   ```json
   {"profile_id": "640212109",
    "profile_print_3mf": [{"comments": "no_ips", "md5": "B19B...", "plate_idx": 1, "url": "<gcode_upload_url>"}]}
   ```
   `profile_id` as string, `md5` = uppercase MD5 of the full gcode.3mf bytes.
   **Use the `gcode_upload_url` from step 5 (NOT a GET /profile URL) and the gcode.3mf MD5
   (NOT the config 3MF MD5).** This is the exact format captured from BC v2.2.1 TLS traffic.

8. **Create task** — `POST /v1/user-service/my/task` with **BambuConnect headers**.
   `profileId` must be integer (not string). Include `jobType: 1`.
   Returns `{"id": <task_id>}` (HTTP 200). Task appears in Bambu Handy history, but the
   printer rejects the MQTT command because the request lacks RSA-2048 signing.

**Key discoveries:**
- **BambuConnect headers required for POST /my/task** — use `x-bbl-client-type: connect`,
  `x-bbl-client-name: BambuConnect`. Slicer headers cause 400 empty for HTTP-created models.
- **Notification poll (GET) required** — must wait for `message: "success"` before PATCH.
  Proceeding too early causes `code 11: "Wrong file format"` on PATCH.
- **Two-upload pattern** — step 2 uploads config-only 3MF to `upload_url` (from POST /project),
  step 6 uploads full gcode.3mf to a separate URL from GET /user/upload.
- **PATCH uses gcode upload URL + gcode MD5** — NOT the GET /profile URL or config 3MF MD5.
  Using the wrong URL/MD5 causes the task to be created but the printer ignores it.
- **origin_file_name must be `"connect_config.3mf"`** — using the actual filename here
  causes BC-side errors (confirmed from captured traffic).
- **profileId type mismatch** — project creation returns profile_id as string; POST /my/task
  requires it as integer. Convert with `int(profile_id)`.
- **`mode: "cloud_file"` required** — empty 400 (no error message) if missing.
- **POST /my/task requires signing** — `x-bbl-device-security-sign` (RSA-2048 signature),
  `x-bbl-app-certification-id` (cert CN + fingerprint). Without it: task created (200) but
  printer rejects MQTT command with "MQTT Command verification failed". See signing section.

**Alternative (MQTT path, no POST /my/task):**

Steps 1-4 same as above. Step 5 replaced with:

`MQTT project_file` — Connect to `us.mqtt.bambulab.com:8883` with `u_{uid}` / token.
Send signed `project_file` command to `device/{device_id}/request`:
```json
{"print": {"command": "project_file", "param": "Metadata/plate_1.gcode",
  "project_id": "...", "profile_id": "...", "task_id": "0", "subtask_id": "0",
  "subtask_name": "file.3mf", "url": "<dualstack S3 URL>", "md5": "...",
  "bed_type": "auto", "use_ams": true, ...}}
```
Sign with Bambu Connect X.509 RSA-SHA256 key (publicly extracted, cert_id known).
Downside: does NOT create a task record in Bambu Handy app history.

**Script:** `/tmp/test_pure_python_cloud_print.py`

#### POST /my/task Analysis

`POST /my/task` has server-side validation beyond field checking:

| Model source | Headers | Result |
|---|---|---|
| Library-created model + matching profile | any | 200 OK ✅ |
| HTTP-created model + matching profile | **BambuConnect** (`x-bbl-client-type: connect`) | **200 OK ✅** |
| HTTP-created model + matching profile | slicer (`x-bbl-client-type: slicer`) | 400 empty ❌ |
| Any model + mismatched profile | any | 403 ❌ |
| Any model + profileId=0 | any | 400 empty ❌ |

**Key finding (Mar 2026):** The 400 empty error for HTTP-created models was caused by using
slicer headers. Switching to BambuConnect headers (`x-bbl-client-type: connect`,
`x-bbl-client-name: BambuConnect`) makes POST /my/task work for all HTTP-created models.
Captured from live BambuConnect v2.2.1 traffic during a real print trigger.

#### Library Anti-Debug Analysis

The proprietary `libbambu_networking.so` uses multiple anti-debugging techniques:

1. **Static networking:** ALL networking (curl, OpenSSL, DNS resolver) is statically
   linked inside VMProtect-obfuscated code. `nm -D` shows ZERO networking symbols.
   LD_PRELOAD hooks on connect/socket/getaddrinfo are completely ineffective.

2. **Anti-debug checks:**
   - Reads `/proc/self/status` to check `TracerPid` field
   - Reads `/proc/<parent_pid>/cmdline` to identify tracer process
   - Calls `ptrace(PTRACE_TRACEME)` as secondary check
   - Crashes with SIGSEGV if any check detects debugging

3. **Bypass method (for research):** A ptrace-based stealth tracer can:
   - Intercept `read()` on `/proc/self/status` and patch `TracerPid: <pid>` → `TracerPid: 0`
   - Intercept `read()` on `/proc/<tracer>/cmdline` and fake the content
   - Replace `ptrace(TRACEME)` syscall with `getpid()` and fake return value 0
   - Successfully redirect `connect()` syscalls from port 443 to a local proxy
   - See `/tmp/ptrace_stealth.c` for implementation

4. **TLS interception blocked:** Even with connection redirection working,
   the library's statically-linked TLS rejects mitmproxy certificates.
   The embedded curl respects `CURL_CA_BUNDLE` for connecting to the real API,
   but adding the mitmproxy CA to the bundle causes initialization failures
   under ptrace (timing-sensitive behavior).

---

## Private Key Extraction Attempts (Feb 2026)

### Goal

Extract the RSA private key used by `libbambu_networking.so` for signing MQTT `project_file`
commands, enabling pure Python cloud printing without the proprietary library.

### Background

The library successfully signs and sends MQTT commands during `start_print()`. The signing
uses RSA-SHA256 with an X.509 certificate. The device certificate
(CN=`GLOF3813734089-b04ef6650000`, RSA 2048-bit, valid 2025-02-09 to 2026-08-14) was found
in memory, but its **private key** is needed to sign messages independently.

Key distinction:
- `send_message_to_printer()` with `MSG_SIGN=1` signs pushall/get_accessories (rc=0)
- `send_message_to_printer()` returns **-4** for `project_file` and `gcode_line` — the
  library gates these command types at the application level, BEFORE signing
- `start_print()` works end-to-end because it uses an internal code path that bypasses
  `send_message_to_printer()`

### Approach 1: PEM/DER Memory Scan (FAILED)

**Tool:** `/tmp/self_scan_bridge.cpp` — self-scanning bridge with SIGSEGV/SIGBUS signal
handlers using `sigsetjmp`/`siglongjmp` for safe memory reading.

**Method:** After loading the library and logging in (waiting 8s for key decryption),
scanned all readable memory regions matching `libbambu`, `[heap]`, or anonymous writable.
Searched for PEM markers (`BEGIN PRIVATE KEY`, `BEGIN RSA PRIVATE KEY`) and DER PKCS#8
headers (`30 82 XX XX 02 01 00`).

**Result:** Found 200 items — ALL certificates, ZERO private keys.

6 unique certificates found:
| CN | Type | Notes |
|----|------|-------|
| GLOF3813734089-b04ef6650000 | Device cert | Library-generated, RSA 2048, used for MQTT TLS |
| GLOF3813734089.bambulab.com | Printer cert | Issued by application_root |
| application_root.bambulab.com | Intermediate CA | |
| BBL CA | Root CA | 2022–2032 |
| BBL CA2 RSA | New root CA | 2025–2035 |
| BBL CA2 ECC | New ECC root | 2025–2035 |

**Conclusion:** Private key is NOT stored in PEM or DER format in memory. OpenSSL 3.x
stores decrypted keys internally as BIGNUM structures (raw word arrays), not serialized.

### Approach 2: LD_PRELOAD OpenSSL Hook (FAILED)

**Tool:** `/tmp/hook_sign.c` — shared library intercepting `EVP_DigestSignFinal`,
`EVP_PKEY_sign`, and `EVP_SignFinal`.

**Method:** Compiled as shared object, ran bridge with `LD_PRELOAD=/tmp/hook_sign.so`.

**Result:** Zero keys captured. No hook functions were called.

**Root cause:** Library **statically links OpenSSL**. `ldd` shows only basic system
libraries (libpthread, libstdc++, libdl, libc, libz, libm, libgcc_s) — no libssl or
libcrypto. LD_PRELOAD only intercepts dynamically-linked functions.

### Approach 3: External Process Memory Scan (FAILED)

**Tool:** `/tmp/bg_bridge.cpp` (background bridge) + `/tmp/scan_pid.py` (external scanner).

**Method:** Started the bridge as a background process (PID 13239, login=0), then tried
to read `/proc/PID/mem` from a separate Python script.

**Result:** `Permission denied` on `/proc/PID/mem`.

**Root cause:** `ptrace_scope=1` on this Linux kernel. External process memory reading
requires parent-child relationship. Cannot be fixed without root.

### Approach 4: Library Export Functions (FAILED)

**Tool:** `/tmp/get_cert2.cpp`, `/tmp/get_token.cpp`

**Method:** Tried calling various `bambu_network_get_*` exports to find a key getter.

**Results:**
- `bambu_network_get_device_cert` → NULL (function doesn't exist)
- `bambu_network_get_user_id` → "1939415276" ✅
- `bambu_network_get_user_name` → "paul@fremantle.org" ✅
- `bambu_network_get_my_token` → segfault (wrong calling convention?)

**Conclusion:** No exported function exposes the private key.

### Approach 5: BIGNUM Memory Scan via Frida (FAILED)

**Tool:** Frida v17.7.3 with custom JavaScript instrumentation scripts (`/tmp/frida_trace_rsa.js`)

**Method:** Since OpenSSL stores keys as BIGNUM structures (arrays of `uint64_t` in
little-endian word order on x86_64), used Frida `Memory.scanSync()` to search for the
known RSA modulus from the installation's certificate in both:
1. Big-endian format (as in certificate DER encoding)
2. BIGNUM LE word-reversed format (as in OpenSSL's internal structs)

Then followed pointer chains: modulus data → BIGNUM struct → RSA_KEY struct → private
exponent BIGNUM.

**Results (from Frida hooking `start_print`):**
- **5 big-endian modulus hits** — DER-encoded certificate copies in heap
- **2 little-endian BIGNUM hits** — OpenSSL BIGNUM data at known addresses
- Traced from BIGNUM data → found pointers to BIGNUM structs (top=32, dmax validated)
- Traced from BIGNUM structs → found RSA structures with matching `e=65537`
- **ALL RSA structures have `d = NULL`** — public key only!
- No PEM or DER private key patterns found anywhere in process memory
- No PKCS#1 or PKCS#8 private key headers (`30 82 XX XX 02 01 00`) found

**Conclusion:** The library stores the private key in a custom internal format, NOT in
standard OpenSSL RSA structures. Even during active signing (hooked `start_print` on both
entry and exit), the private exponent `d` is NULL in all RSA structures. The library
likely uses its own crypto implementation or stores key material in obfuscated form.

### Approach 6: Binary Disassembly (PARTIAL)

**Method:** Disassembled `send_message_to_printer` and `start_print` exports.

**Findings:** All exported functions use the same anti-debug trampoline pattern:
```
push rcx; push rdx; push rdi; push rsi; pushf
call 0x116730          ; anti-debug check
popf; pop rsi; pop rdi; pop rdx; pop rcx
jmp <real_implementation>  ; obfuscated code
```

The real implementations jump to heavily obfuscated code (VMProtect-style: stack
manipulation, `xchg` tricks, `push/pop` chains). Makes binary patching impractical.

Key addresses (in `/tmp/bambu_plugin/libbambu_networking.so`):
| Export | Address | Real impl | Size |
|--------|---------|-----------|------|
| `send_message_to_printer` | 0x33c4c0 | 0x8a6130 | 0x159 |
| `start_print` | 0x351600 | 0x8a6890 | 0x1f4 |
| `start_publish` | 0x077120 | 0x8a4460 | 0x321 |
| anti-debug check | 0x116730 | — | — |

`start_publish` is for MakerWorld model publishing, NOT MQTT publish.

### Encrypted Config File

The library stores key material in `/tmp/bambu_agent/config/BambuNetworkEngine.conf`
(688 bytes, 7.66 bits/byte entropy — fully encrypted). Encrypted at rest, decrypted at runtime.

- KEK from cloud API (`efc30d7df1f956d7a9a5220b48ba68db`) does NOT decrypt it
- Encryption scheme unknown (not standard AES-CBC/GCM with known IV patterns)
- Backup at `BambuNetworkEngine.conf.bak`

### Approach 7: mitmproxy HTTPS Interception (Mar 2026)

**Tool:** mitmproxy v11.1.3 with custom addon (`/tmp/mitm_capture2.py`)

**Method:** Ran the bridge with `HTTPS_PROXY` pointing to mitmproxy. Appended mitmproxy's
CA cert to the system CA bundle and to `slicer_base64.cer` so the library's static
libcurl would trust it. Captured all HTTP traffic during a successful `start_print()`.

**Results — Successfully captured signing headers from POST /my/task:**
```
x-bbl-app-certification-id: CN=GLOF3813734089.bambulab.com:a4e8faaa1a38e3650a0ea590d192383f
x-bbl-device-security-sign: IlN4Ym0I6sPF...KhsCKg== (344 chars base64, 256 bytes = RSA-2048)
```
- **`x-connect-api-acton` header NOT sent** by library (BambuConnect sends `sign`)
- Server returned HTTP 200 with `{"id": 782951694}` — task created, printer started PREPARE
- POST body: 500 bytes JSON with deviceId, modelId, profileId, amsMapping, etc.

**Also captured certificate download:**
```
GET /v1/iot-service/api/user/applications/{app_id}/cert?aes256=<RSA-encrypted-AES-key>
```
Response contained:
- `cert`: Full PEM chain (3 certs — leaf, intermediate CA, root CA)
- `key`: 940 chars base64 (704 bytes) — AES-256-encrypted private key
- `crl`: Certificate revocation list

**Leaf cert:** CN=GLOF3813734089-836763c70000, RSA 2048-bit, valid 2025-12-21 to 2027-06-25
**Intermediate CA:** CN=GLOF3813734089.bambulab.com (account-level, signs all installation certs)
**Root CA:** CN=application_root.bambulab.com (signed by BBL CA)

Custom X.509 OIDs in leaf cert:
- `1.3.6.1.4.1.1666250441.1` = "v1" (version)
- `1.3.6.1.4.1.1666250441.2` = "false" (revoked flag)
- `1.3.6.1.4.1.1666250441.1.1` = "3DP" (product type)
- `1.3.6.1.4.1.1666250441.1.2` = "Studio" (client type)

**Key exchange flow:**
1. Library generates random AES-256 key
2. RSA-encrypts AES key with server's public key (embedded in library binary)
3. Sends encrypted AES key as `aes256` URL parameter
4. Server decrypts AES key, generates RSA-2048 key pair, signs cert
5. Server AES-256-encrypts private key with the AES key
6. Returns cert chain + encrypted private key
7. Library decrypts private key with its AES key, stores in BambuNetworkEngine.conf

The `key` field cannot be decrypted without the AES key, which is only known to
the library (generated internally) and the server. The server's RSA public key
(used to encrypt the `aes256` parameter) is embedded in the library binary.

### Approach 8: Frida RSA Structure Tracing (Mar 2026, FAILED)

**Tool:** Frida with `/tmp/frida_trace_rsa.js` — comprehensive pointer chain analysis

**Method:** Three-phase extraction during `start_print()`:
1. Find LE modulus BIGNUM data in memory (pattern: last 8 bytes reversed)
2. Search for pointers TO the data → identify BIGNUM structs (validate top=32)
3. Search for pointers TO BIGNUM structs → identify RSA structures (validate e=65537)
4. Read d, p, q, dp, dq, qinv from RSA structure offsets

Tested all possible offsets (0–128 in steps of 8) for where `n` sits in the RSA struct.

**Results:**
- Phase 1: Found 2 LE modulus data locations
- Phase 2: Found 2 valid BIGNUM(n) structs with top=32
- Phase 3: Found RSA structures at multiple offsets — **ALL have d=NULL**
- Scanned on both `start_print` entry AND exit — same result
- Repeated for both BIGNUM addresses — same result

**Definitive conclusion:** The library does NOT use standard OpenSSL RSA structures for
private key storage. The key is held in a custom obfuscated format. The RSA-2048 signing
works through an internal implementation that never populates the standard `d` field.

### Approach 9: BambuConnect macOS CDP JavaScript API Hooking (Mar 2026, FAILED)

**Tool:** Node.js Inspector via `kill -SIGUSR1 <BC PID>` (SIGUSR1 enables inspector on
port 9229; `--inspect` flag has no effect on this BC installation)

**Method:** Attached Chrome DevTools Protocol (CDP) to the BambuConnect main process and
injected hook scripts into both the main process and renderer.

**Hooks attempted:**

| Hook target | Method | Result |
|---|---|---|
| `require('crypto').createSign` | Replace with instrumented version in main process | Nothing captured |
| `Sign.prototype.sign` | Patch native binding via `_handle` protocol | Nothing captured |
| `require('crypto').sign` (one-shot) | Replace with instrumented version | Nothing captured |
| `webcrypto.subtle.sign` | Replace in main process | Nothing captured |
| `fetch`, `XMLHttpRequest`, `subtle.sign` | Injected via `Runtime.evaluate` into renderer | Nothing captured |

**Root cause:** BC's `main.jsc` (compiled V8 bytecode via `main-loader.cjs`) captures
references to crypto function objects at startup. By the time hooks are installed via CDP,
the closures inside main.jsc already hold direct references to the original functions.
Patching `Sign.prototype.sign` or `crypto.createSign` after the fact does not intercept
calls from pre-captured references.

**IPC discovery (side effect):**
- Found `bridge` IPC channel firing continuously for `publishMqttMessage` (printer polls)
- Enumerated all `ipcMain` handler registrations; found: `uploadFileToDevice`,
  `connectAndSubscribeLanDevice`, `getCloudDeviceTicket`, `publishMqttMessage`
- The `bridge` channel is BC's primary renderer↔main transport; all print actions go through it
- Calling IPC handlers with a fake event object crashed BC (SIGUSR1 to restart)

**Storage enumeration:**

| Storage location | Contents | Key material? |
|---|---|---|
| LevelDB (Chromium Local Storage) | `token`, `device_id` in plaintext | No |
| IndexedDB (renderer context) | 3 databases: filament profiles, HMS actions, OTA updates | No |
| `app.getPath('userData')` safeStorage scan | No `v10`/`v11` encrypted value prefixes | No |

**Conclusion:** The BC macOS private key is NOT in Local Storage or IndexedDB. The encrypted
key material is stored elsewhere (likely fetched and held in memory only, or stored in macOS
Keychain). With V8 bytecode closures capturing references at startup and no extractable
storage, CDP-based extraction is not viable for this version of BambuConnect.

**Hackaday key test (Mar 2026):**

The publicly-extracted BambuConnect v1.0.4 private key (Jan 2025) was wired into
`_sign_task_body()` and tested against POST /my/task. Result: **HTTP 403 Forbidden** from
the server (not just printer rejection). This confirms the server validates the signature
against the registered cert for that specific installation — a per-installation key from
one specific build does not work for any other installation.

### MQTT app_cert_list (from captures)

The printer broadcasts registered cert IDs via MQTT `security` topic:
```json
{
  "command": "app_cert_list",
  "cert_ids": [
    "a4e8faaa1a38e3650a0ea590d192383fCN=GLOF3813734089.bambulab.com",
    "f9332ab780a6ffe6664db61be42b04ee"
  ]
}
```
- First entry: our library's current cert (used for signing)
- Second entry: old cert from Jan 2025 extraction (still registered but key rotated)

### Old Bambu Connect Key (Jan 2025)

The Bambu Connect v1.0.4 private key (publicly extracted Jan 2025) was tested with all
9 cert_id format variants against 3 printer-registered cert_ids. ALL attempts fail with
error `84033545` (0x05024009 = "signature verification failed").

**Conclusion:** The key is **per-installation**, not global (see BC macOS findings below).
Each installation generates its own RSA key pair and certificate.

### Bambu Connect macOS Analysis (Feb 28, 2026)

Analysis of the Bambu Connect macOS app (Electron-based) revealed the certificate
architecture:

**Found in compiled bytecode:**
```
Subject/OU: GLOF3813734089-524a37c80000
Issuer:     GLOF3813734089.bambulab.com
Valid:      Dec 2024 – Dec 2025 (EXPIRED)
Key usage:  Digital Signature, Key Encipherment, Key Agreement
```

**Certificate comparison across installations:**

| Installation | Cert CN suffix | Valid until | Key storage |
|---|---|---|---|
| BC macOS (Electron) | `524a37c80000` | Dec 2025 (expired) | AES-256 in IndexedDB |
| Linux library (our instance) | `b04ef6650000` | Aug 2026 | Encrypted in BambuNetworkEngine.conf |
| BC v1.0.4 (Jan 2025 leak) | (unknown) | (unknown) | Extracted from memory |

**Critical insight: Per-installation key pairs.** Each Bambu Connect / library installation:
1. Generates its own RSA 2048-bit key pair via `install_device_cert(dev_id, lan_only)`
2. Gets a unique certificate signed by `GLOF3813734089.bambulab.com` (account-level CA)
3. Uses a unique suffix (e.g., `524a37c80000`, `b04ef6650000`) as the installation ID
4. The `GLOF3813734089` prefix is the **account/printer identifier**, shared across all installations

This means:
- The Jan 2025 "leaked" key was never a global key — it was from ONE specific installation
- Our Linux library has its OWN private key (for the `b04ef6650000` cert)
- The BC mac private key wouldn't work for us even if extracted (different cert, and expired)
- To use pure Python, we must extract OUR library's `b04ef6650000` private key specifically

**BC macOS key storage:**
- Private key encrypted with AES-256
- Key derivation code is in compiled V8 bytecode (.jsc files) — not readable
- Strings found: `encAppKey`, `random_key`, `aes256`
- Stored in IndexedDB or fetched via `app_cert_install` API endpoint
- NOT in the app bundle or Local Storage

**Potential new approach:** Call `bambu_network_install_device_cert()` to generate a FRESH
certificate, then immediately scan memory for the newly-generated key before it gets
encrypted. The key would be in plaintext BIGNUM form during the CSR construction phase.

### Remaining Approaches (Likely Infeasible)

All practical key extraction approaches have been exhausted. The key is protected by:
- Custom internal crypto (not standard OpenSSL RSA structures)
- VMProtect-style code obfuscation
- Anti-debug trampolines on all exports
- Encrypted config file with unknown key derivation
- Per-installation certificates (no global key to find)

Theoretical remaining approaches:
1. **GDB hardware breakpoints** on memory writes during cert decryption
2. **Binary patching** of the obfuscated signing function (extremely difficult)
3. **Full binary reverse engineering** of the stripped, obfuscated 4.6MB library

### Current Status (Mar 2026)

**Working:** C++ bridge (`scripts/bambu_cloud_bridge.cpp`) wrapping `libbambu_networking.so`
for cloud printing. This is the **recommended production solution** (`mode = "cloud-bridge"`).
The bridge handles the full flow including RSA-2048 signing via the library's internal
per-installation certificate. The `cloud_print()` function auto-generates the required
config-only 3MF from the input file.

**Partially working (experimental):** Pure Python HTTP (`cloud_print_http()`,
`mode = "cloud-http"`) handles all 8 steps in pure Python. POST /my/task is sent unsigned
— the task is created (HTTP 200) but the printer rejects the MQTT command without the
RSA signature ("MQTT Command verification failed"). **Gated behind `--experimental` flag**
in the CLI — running without it prints an error and points to cloud-bridge. Use
cloud-bridge mode instead.

**Private key extraction: DEFINITIVELY FAILED.** After 9 different approaches including
Frida dynamic instrumentation, mitmproxy HTTPS interception, LD_PRELOAD hooks, memory
scanning, RSA structure tracing, and BambuConnect CDP-based JavaScript API hooking, the
key cannot be extracted. It is stored in a custom obfuscated format, never in standard
OpenSSL structures or PEM/DER encoding. See "Approach 9" below for the latest attempts.

**Pure Python flow (steps 1–7 work, step 8 creates task but printer rejects):**
1. POST /v1/iot-service/api/user/project — create project, get upload URL
2. PUT S3 presigned URL — upload config-only 3MF (gcode stripped, no Content-Type header)
3. PUT /v1/iot-service/api/user/notification — notify upload complete (`origin_file_name: "connect_config.3mf"`)
4. GET /v1/iot-service/api/user/notification — poll until processed
5. GET /v1/iot-service/api/user/upload — get gcode.3mf upload URL
6. PUT gcode S3 URL — upload full .gcode.3mf (no Content-Type header)
7. PATCH /v1/iot-service/api/user/project/{id} — set **gcode upload URL** + **gcode.3mf MD5**
8. POST /v1/user-service/my/task — create task (**requires RSA signing to avoid printer rejection**)

**Implementation:** `src/fabprint/cloud.py` provides both `cloud_print()` (bridge-based,
recommended) and `cloud_print_http()` (pure Python, unsigned). Callable via
`mode = "cloud-bridge"` (default) or `mode = "cloud-http" --experimental` in fabprint.

---

## Printer Details

- Model: P1S (dev_model_name: C12)
- Serial: `<DEVICE_ID>`
- LAN access code: `<ACCESS_CODE>` (from GET /user/print)
- AMS: 4-slot, gcode uses slots 1 and 2
- Online: yes
