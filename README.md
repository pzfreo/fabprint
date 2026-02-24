# fabprint

Headless 3D print pipeline: arrange parts on a build plate, slice to gcode, and send to a Bambu Lab printer — all from a TOML config file.

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
pip install .
```

Or with [uv](https://docs.astral.sh/uv/):

```bash
uv sync
```

Optional extras:

```bash
pip install ".[lan]"    # LAN printing (bambulabs-api)
pip install ".[cloud]"  # Bambu Cloud API (experimental)
pip install ".[step]"   # STEP file support (build123d)
pip install ".[all]"    # Everything
pip install ".[dev]"    # pytest + ruff
```

## Quick start

1. Create a `fabprint.toml` config:

```toml
[printer]
mode = "bambu-connect"

[plate]
size = [256, 256]
padding = 5.0

[slicer]
engine = "orca"
printer = "Bambu Lab P1S 0.4 nozzle"
process = "0.20mm Standard @BBL X1C"
filaments = ["Generic PLA @base", "Generic PLA @base", "Generic PETG-CF @base"]

[slicer.overrides]
enable_support = 1
curr_bed_type = "Textured PEI Plate"

[[parts]]
file = "frame.stl"
copies = 1
filament = 3           # PETG-CF in AMS slot 3

[[parts]]
file = "wheel.stl"
copies = 5
orient = "upright"
filament = 3
```

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
| `filaments` | `[string]` | —         | Filament profiles (one per AMS slot)   |

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
| `filament` | `int`      | `1`          | AMS filament slot (1-indexed)        |
| `scale`    | `float`    | `1.0`        | Uniform scale factor                 |

## CLI commands

```
fabprint plate <config>           # Arrange and export 3MF
fabprint plate <config> --view    # Preview in viewer first
fabprint slice <config>           # Arrange, export, and slice to gcode
fabprint print <config>           # Arrange, slice, and send to printer
fabprint print <config> --dry-run # Do everything except send to printer
fabprint print <config> --gcode output/plate_1.gcode  # Send pre-sliced gcode
fabprint print <config> --upload-only  # Upload without starting print
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
