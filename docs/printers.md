# Printer support

fabprint supports three printer connection types. All are configured via `fabprint setup` and stored in `~/.config/fabprint/credentials.toml`.

## bambu-lan (experimental)

Direct LAN connection to Bambu Lab printers using the `bambulabs_api` library.

| Feature        | Status |
|----------------|--------|
| Send gcode     | Supported |
| Upload only    | Supported |
| Status         | Supported |
| Watch          | Supported |

**Credentials:** `ip`, `access_code`, `serial`

**Dependencies:** `bambulabs_api` (optional install)

**Tested against:** Not yet tested against real hardware.

## bambu-cloud

Cloud connection to Bambu Lab printers via the Bambu Connect bridge binary (`bambu_cloud_bridge`).

| Feature           | Status |
|--------------------|--------|
| Send gcode (.3mf)  | Supported |
| AMS filament mapping | Supported |
| Status             | Supported (via cloud bridge) |
| Watch              | Supported |

**Credentials:** `serial` (plus cloud login via `fabprint login`)

**Dependencies:** `bambu_cloud_bridge` binary, cloud auth token

**Tested against:** Bambu Lab P1S via Bambu Cloud.

## moonraker

REST API connection to Klipper/Moonraker printers. Works with any printer running Klipper + Moonraker (Voron, Ender with Klipper, etc.).

| Feature        | Status |
|----------------|--------|
| Send gcode     | Supported |
| Upload only    | Supported |
| Status         | Supported |
| Watch          | Supported |

**Credentials:** `url` (required), `api_key` (optional, for authenticated instances)

**Dependencies:** `requests` (optional install)

### API endpoints used

| Operation    | Method | Endpoint |
|-------------|--------|----------|
| Upload file  | POST   | `/server/files/upload` |
| Start print  | POST   | `/printer/print/start` |
| Query status | GET    | `/printer/objects/query?print_stats&heater_bed&extruder&display_status` |

### State mapping

Klipper states are mapped to the normalised fabprint states:

| Klipper state | fabprint state |
|---------------|----------------|
| standby       | IDLE           |
| printing      | RUNNING        |
| paused        | PAUSE          |
| complete      | FINISH         |
| cancelled     | IDLE           |
| error         | FAILED         |

### Testing with a virtual printer

Moonraker support has been tested against the [mainsail-crew/virtual-klipper-printer](https://github.com/mainsail-crew/virtual-klipper-printer) Docker image, which runs Klipper with simulavr + Moonraker without real hardware.

```bash
# Clone and start the virtual printer
git clone https://github.com/mainsail-crew/virtual-klipper-printer.git
cd virtual-klipper-printer
docker run -d --name virtual-klipper \
  -v "$(pwd)/printer_data:/home/printer/printer_data" \
  -p 7125:7125 -p 8110:8080 \
  --tmpfs /tmp:noexec \
  --tmpfs /home/printer/printer_data/comms:noexec \
  ghcr.io/mainsail-crew/virtual-klipper-printer:latest

# Wait for Klipper to connect to simulavr (~15-30s)
# Check readiness:
curl -s http://localhost:7125/printer/info | python3 -m json.tool

# Configure fabprint
fabprint setup  # choose moonraker, url = http://localhost:7125

# Test
fabprint status --printer <name>
fabprint status --printer <name> --watch
```

**Verified operations (2026-03-18):**

- `get_moonraker_status()` — state, temperatures, progress, layer info
- `_send_moonraker()` upload-only — file appears in Moonraker file list
- `_send_moonraker()` upload + start — print runs to completion
- `fabprint status` — renders state, task name, temperatures
- `fabprint status --watch` — live dashboard with polling

**Note:** The simulavr virtual printer executes gcode nearly instantly, so `RUNNING` state is brief. On real hardware, progress and layer tracking will update over time.

### Not yet tested

- API key authentication (`X-Api-Key` header)
- Real Klipper hardware (Voron, Ender, etc.)
