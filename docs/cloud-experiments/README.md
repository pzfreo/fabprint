# Cloud Printing Experiments

This directory documents the research and experimental implementations
explored while building Bambu Lab cloud printing support for fabprint.

## Background

Bambu Lab cloud printing requires:
1. REST API calls to create a project, upload 3MF files to S3, and create a print task
2. RSA-2048 signing of the task body — the server includes the signature in the MQTT command to the printer, which verifies it before accepting the job

The signing requirement is the critical obstacle. Each Bambu Connect
installation generates its own RSA-2048 key pair, stored encrypted
in `BambuNetworkEngine.conf`. The key is never exposed in standard
PEM/DER format — extraction attempts via Frida, mitmproxy, and memory
scanning all confirmed it's not accessible.

## Approaches tried

### 1. Pure Python HTTP (`http-cloud-print.py`)

A complete implementation of the Bambu Cloud REST API flow in pure Python:
- Session management with BambuConnect client headers
- Project creation, S3 upload (config + gcode 3MF), server notification
- Processing poll loop
- AMS mapping from 3MF metadata
- Task creation and dispatch polling

**Status:** Steps 1–7 work perfectly. Step 8 (task creation) succeeds
(HTTP 200) but the printer rejects the MQTT command because the task
isn't signed with the installation's private key. The publicly extracted
Hackaday key doesn't work with current API versions (returns 403).

This file is preserved as reference for the complete API flow.

### 2. C++ bridge (`cloud_print()` in `cloud/bridge.py`) — ACTIVE

Wraps Bambu Lab's `libbambu_networking.so` shared library via a C++
bridge binary. The library handles authentication, MQTT, and crucially
the RSA signing with its internal key pair.

**Status:** Working. This is the active implementation used by fabprint.
The bridge runs in Docker (`fabprint/cloud-bridge` image) and
communicates via JSON stdin/stdout.

See `scripts/bambu_cloud_bridge.cpp` for the bridge source.

## Files

- `README.md` — this file
- `cloud-print-research.md` — detailed reverse-engineering notes,
  API captures, signing analysis, and Frida/mitmproxy findings
- `http-cloud-print.py` — complete pure-Python HTTP implementation
  (extracted from `src/fabprint/cloud/http.py` before removal)
