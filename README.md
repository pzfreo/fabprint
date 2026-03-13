# fabprint

Headless 3D print pipeline: arrange parts on a build plate, slice to gcode, and send to a Bambu Lab printer — all from a TOML config file.

## Why fabprint

Code-CAD tools like [build123d](https://github.com/gumyr/build123d) let you define physical parts in Python — parametric, testable, and version-controlled. But the moment you need to print, the workflow breaks: open a slicer GUI, drag in your files, fiddle with settings, hit print. None of that is reproducible or tracked.

fabprint closes that gap. A TOML file declares your parts, filaments, and slicer settings. The CLI arranges, slices, and sends to the printer. Everything is text, everything goes in git, and the same config produces the same print every time.

Software engineering spent decades borrowing rigour from physical manufacturing — assembly lines, quality gates, repeatable builds. fabprint brings those ideas full circle: version-controlled configs, reproducible builds, and command-line deployments for the physical world.

## Features

- **Multi-format input** — STL, 3MF, and STEP files
- **Automatic orientation** — flat, upright, side, or custom rotations
- **Bin packing** — efficient 2D arrangement with configurable padding
- **Part scaling** — uniform scale factor per part
- **Multi-filament** — AMS slot assignment per part with correct extruder mapping
- **Slicer integration** — BambuStudio and OrcaSlicer CLI support
- **Profile management** — discover, pin, and override slicer profiles
- **Print delivery** — send to printer via LAN, Bambu Connect, or cloud API
- **Cross-platform** — macOS, Linux, and Windows

## Installation

Requires Python 3.11+.

```bash
pip install fabprint
```

Or from source with [uv](https://docs.astral.sh/uv/):

```bash
uv sync
```

Optional extras:

```bash
pip install "fabprint[lan]"    # LAN printing (bambulabs-api)
pip install "fabprint[cloud]"  # Bambu Cloud API (experimental)
pip install "fabprint[step]"   # STEP file support (build123d)
pip install "fabprint[all]"    # Everything
```

## Quick start

1. Create a `fabprint.toml` config:

```toml
[printer]
mode = "cloud-bridge"
serial = "01P00A..."

[plate]
size = [256, 256]
padding = 5.0

[slicer]
engine = "orca"
printer = "Bambu Lab P1S 0.4 nozzle"
process = "0.20mm Standard @BBL X1C"

[slicer.overrides]
enable_support = 1
curr_bed_type = "Textured PEI Plate"

[[parts]]
file = "frame.stl"
copies = 1
filament = "Generic PETG-CF @base"

[[parts]]
file = "wheel.stl"
copies = 5
orient = "upright"
filament = "Generic PETG-CF @base"
```

Parts reference filament profiles by name — no need to manually number AMS slots. The filament list is auto-derived from what parts use. For multi-material prints with explicit slot ordering, you can still set `[slicer].filaments` directly.

2. Generate a build plate:

```bash
fabprint plate fabprint.toml -o plate.3mf
```

3. Slice to gcode:

```bash
fabprint slice fabprint.toml
```

4. Slice and send to printer:

```bash
fabprint print fabprint.toml
```

## Config reference

### `[printer]`

| Key           | Type     | Default       | Description                            |
|---------------|----------|---------------|----------------------------------------|
| `mode`        | `string` | `"bambu-lan"` | `"bambu-lan"`, `"bambu-connect"`, or `"bambu-cloud"` |
| `ip`          | `string` | —             | Printer IP address (LAN mode)          |
| `access_code` | `string` | —             | Printer access code (LAN mode)         |
| `serial`      | `string` | —             | Printer serial number (LAN mode)       |

Printer modes:

- **`bambu-lan`** — direct LAN connection via MQTT + FTP. Requires `ip`, `access_code`, and `serial`. Fastest, works offline.
- **`bambu-connect`** — sends sliced `.gcode.3mf` to [Bambu Connect](https://wiki.bambulab.com/en/software/bambu-connect) app. No credentials needed. You confirm and start the print from Bambu Connect.
- **`bambu-cloud`** — experimental cloud API. Requires `BAMBU_EMAIL` and `BAMBU_PASSWORD` env vars.

Credentials can also be set via environment variables, which override config values:

| Env var            | Overrides         |
|--------------------|-------------------|
| `BAMBU_PRINTER_IP` | `printer.ip`      |
| `BAMBU_ACCESS_CODE`| `printer.access_code` |
| `BAMBU_SERIAL`     | `printer.serial`  |
| `BAMBU_EMAIL`      | Cloud login email  |
| `BAMBU_PASSWORD`   | Cloud login password |

### `[plate]`

| Key       | Type         | Default | Description              |
|-----------|--------------|---------|--------------------------|
| `size`    | `[w, h]`    | —       | Build plate size in mm   |
| `padding` | `float`      | `5.0`   | Gap between parts in mm  |

### `[slicer]`

| Key         | Type       | Default   | Description                            |
|-------------|------------|-----------|----------------------------------------|
| `engine`    | `string`   | `"orca"`  | `"orca"` or `"bambu"`                  |
| `version`   | `string`   | —         | Required OrcaSlicer version (e.g. `"2.3.1"`) |
| `printer`   | `string`   | —         | Printer profile name                   |
| `process`   | `string`   | —         | Process profile name                   |
| `filaments` | `[string]` | —         | Filament profiles (auto-derived from parts if omitted) |

### `[slicer.slots]`

Map slot numbers to filament profiles. Useful when you need specific slot placement (e.g. direct feed) or when parts reference slots by number:

```toml
[slicer.slots]
1 = "Generic PLA @base"
3 = "Generic PETG-CF @base"
5 = "Generic TPU @base"        # direct feed (bypass AMS)
```

Parts can then use `filament = 3` to target a specific slot, or `filament = "Generic PLA @base"` to let the slicer pick. String-referenced filaments not in the slots map are auto-assigned to the next free slot.

### `[slicer.overrides]`

Key-value pairs applied on top of the process profile:

```toml
[slicer.overrides]
enable_support = 1
wall_loops = 4
curr_bed_type = "Textured PEI Plate"
```

Common bed types: `"Cool Plate"`, `"Engineering Plate"`, `"High Temp Plate"`, `"Textured PEI Plate"`.

### `[[parts]]`

| Key        | Type       | Default      | Description                          |
|------------|------------|--------------|--------------------------------------|
| `file`     | `string`   | —            | Path to mesh file (STL/3MF/STEP)     |
| `copies`   | `int`      | `1`          | Number of copies                     |
| `orient`   | `string`   | `"flat"`     | `"flat"`, `"upright"`, or `"side"`   |
| `rotate`   | `[x,y,z]`  | —            | Custom rotation in degrees (overrides `orient`) |
| `filament` | `int\|string` | `1`       | Filament profile name or slot index  |
| `scale`    | `float`    | `1.0`        | Uniform scale factor                 |

### `[parts.filaments]`

Per-object filament overrides for multi-object 3MF files. When a 3MF contains multiple named objects (e.g. exported from build123d), each object can be assigned a different filament while preserving their relative positions:

```toml
[[parts]]
file = "widget.3mf"
filament = "Generic PETG-CF @base"       # default for objects not listed

[parts.filaments]
inlay = "Bambu PLA Basic @BBL X1C"       # override for object named "inlay"
```

Objects in the 3MF are grouped as a single unit for bin packing. Orientation is skipped for grouped parts — the objects are used as-is from the 3MF.

## CLI commands

```
fabprint plate <config>           # Arrange and export 3MF
fabprint plate <config> --view    # Preview in viewer first
fabprint slice <config>           # Arrange, export, and slice to gcode
fabprint print <config>           # Arrange, slice, and send to printer
fabprint print <config> --dry-run # Do everything except send to printer
fabprint print <config> --gcode output/plate_1.gcode  # Send pre-sliced gcode
fabprint print <config> --upload-only  # Upload without starting print
fabprint gcode-info output/plate_1.gcode  # Analyze extruder usage per layer
fabprint login                    # Login to Bambu Cloud and cache token
fabprint watch                    # Live dashboard for all printers
fabprint status                   # Query status of all printers
fabprint status --serial 01P...  # Query a specific printer
fabprint profiles list            # List available slicer profiles
fabprint profiles pin <config>    # Pin profiles for reproducible builds
```

## Profile management

fabprint resolves slicer profiles in this order:

1. Direct file path (if the name contains `/` or `\`)
2. Pinned profiles in `<project>/profiles/<category>/`
3. Slicer system directory

Pin profiles to lock your build against slicer updates:

```bash
fabprint profiles pin fabprint.toml
```

## Platform support

fabprint auto-detects slicer paths per platform:

| Platform | BambuStudio | OrcaSlicer |
|----------|-------------|------------|
| macOS    | `/Applications/BambuStudio.app/...` | `/Applications/OrcaSlicer.app/...` |
| Linux    | `/usr/bin/bambu-studio` | `/usr/bin/orca-slicer` |
| Windows  | `C:\Program Files\BambuStudio\...` | `C:\Program Files\OrcaSlicer\...` |

Slicers on PATH are also detected (Flatpak, Snap, custom installs).

Profile directories follow platform conventions (`~/Library/Application Support/` on macOS, `~/.config/` on Linux, `%APPDATA%` on Windows).

## Docker

Pre-built Docker images with OrcaSlicer are available on [Docker Hub](https://hub.docker.com/r/fabprint/fabprint):

```bash
docker pull fabprint/fabprint:orca-2.3.1
```

### Run from your project directory

```bash
docker run --rm -v "$PWD:/project" fabprint/fabprint:orca-2.3.1 slice fabprint.toml
docker run --rm -v "$PWD:/project" fabprint/fabprint:orca-2.3.1 plate fabprint.toml -o plate.3mf
docker run --rm fabprint/fabprint:orca-2.3.1 profiles list
```

### Slicing via Docker from the CLI

Use `--docker` to force Docker slicing, or `--docker-version` to pick a specific OrcaSlicer version:

```bash
# Use default fabprint/fabprint:latest image
fabprint slice fabprint.toml --docker

# Use a specific OrcaSlicer version
fabprint slice fabprint.toml --docker-version 2.3.1
```

If OrcaSlicer isn't installed locally, `fabprint slice` automatically falls back to Docker.

### Building your own image

To build locally or for a different OrcaSlicer version:

```bash
./scripts/build-docker.sh 2.3.2          # build only
./scripts/build-docker.sh 2.3.2 --push   # build and push to Docker Hub
```

### Reproducible builds

Pin both profiles and OrcaSlicer version for fully reproducible slicing:

```bash
# Pin profiles into your project
fabprint profiles pin fabprint.toml

# Slice with a pinned OrcaSlicer version
fabprint slice fabprint.toml --docker-version 2.3.1
```

Commit the `profiles/` directory to git so slicing results are identical across machines.

## How it works

fabprint handles the full pipeline from STL to printer:

1. **Arrange** — loads meshes, orients them, and bin-packs onto the build plate
2. **Export** — writes a 3MF with per-object extruder metadata so OrcaSlicer knows which AMS slot each part uses
3. **Slice** — calls OrcaSlicer CLI with `--export-3mf` and `--min-save` to produce a Bambu Connect-compatible `.gcode.3mf`
4. **Post-process** — patches the sliced 3MF to fix metadata issues that Bambu Connect requires (see [docs/gcode-3mf-format.md](docs/gcode-3mf-format.md))
5. **Send** — delivers to the printer via LAN, Bambu Connect, or cloud API

For details on the `.gcode.3mf` format and the post-processing fixes, see [The .gcode.3mf Format](docs/gcode-3mf-format.md).

## License

Apache 2.0
