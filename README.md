# fabprint

[![PyPI version](https://img.shields.io/pypi/v/fabprint)](https://pypi.org/project/fabprint/)
[![CI](https://github.com/pzfreo/fabprint/actions/workflows/ci.yml/badge.svg)](https://github.com/pzfreo/fabprint/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/pypi/pyversions/fabprint)](https://pypi.org/project/fabprint/)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)

**Immutable 3D print pipeline**: arrange parts on a build plate, slice to gcode, and send to a Bambu Lab printer — all defined in a single TOML config file.

![fabprint pipeline](docs/images/pipeline.png)

## Why fabprint?

Code-CAD tools like [build123d](https://github.com/gumyr/build123d), [OpenSCAD](https://openscad.org) and [cadquery](https://github.com/cadquery/cadquery) let you define physical parts in code — making designs parametric, testable, and version-controlled. But the moment you need to print, that workflow breaks: open a slicer GUI, drag in files, fiddle with settings, hit print. Hard to reproduce, no diffs, the only thing to track are binary project files.

fabprint is aiming to close that gap. Define your objects, filaments, slicer setting and printer targets in a straightforward TOML file. Use a CLI or CI pipeline to arrange, slice and send to the printer. Pin filament and printer profiles to capture the exact settings. Slicing happens in Docker for platform-agnostic reproducible builds. Everything is text, everything goes in git, and the same config produces the same print every time.

## Quick start

### 1. Install

```bash
pip install fabprint
```

### 2. Create `fabprint.toml`

```toml
[plate]
size = [256, 256]       # build plate dimensions in mm
padding = 5.0

[slicer]
engine = "orca"
printer = "Bambu Lab P1S 0.4 nozzle"
process = "0.20mm Standard @BBL X1C"

[[parts]]
file = "frame.stl"
filament = "Generic PETG-CF @base"

[[parts]]
file = "wheel.stl"
copies = 5
orient = "upright"
filament = "Generic PETG-CF @base"
```

Parts reference filament profiles by name — no need to manually number AMS slots.

### 3. Arrange, slice, print

```bash
fabprint run fabprint.toml --until plate     # arrange parts onto a build plate
fabprint run fabprint.toml --until slice     # arrange and slice to gcode
fabprint run fabprint.toml                   # arrange, slice and send to printer
fabprint run fabprint.toml --dry-run         # full pipeline without sending to printer
```

The plate stage also generates a `plate_preview.3mf` with a bed outline — open it in any 3MF viewer to review placement:

![plate preview](docs/images/plate_preview.png)

## Features

- **Multi-format input** — STL, 3MF, and STEP files
- **Automatic orientation** — flat, upright, side, or custom rotations
- **Bin packing** — efficient 2D arrangement with configurable padding
- **Part scaling** — uniform scale factor per part
- **Multi-filament** — AMS slot assignment per part with correct extruder mapping
- **Slicer integration** — OrcaSlicer support
- **Profile management** — discover, pin, and override slicer profiles
- **Print delivery** — LAN, Bambu Connect, or cloud API
- **Docker support** — pre-built images with OrcaSlicer for reproducible CI/CD slicing
- **Cross-platform** — macOS, Linux, and Windows

## Installation

Requires Python 3.11+.

```bash
pip install fabprint
```

Or with [uv](https://docs.astral.sh/uv/):

```bash
uv pip install fabprint
```

Includes LAN printing, cloud API, and STEP file support out of the box.

## CLI overview

fabprint uses a single `run` command with `--until` and `--only` flags to control how far the pipeline runs:

```bash
fabprint run fabprint.toml                    # full pipeline (arrange → slice → print)
fabprint run fabprint.toml --until plate      # stop after plating
fabprint run fabprint.toml --only slice       # run just the slice stage
fabprint run fabprint.toml --dry-run          # everything except sending to printer
fabprint login                                # log in to Bambu Cloud
fabprint watch                                # live dashboard for all printers
fabprint status                               # query printer status
fabprint profiles list                        # list available slicer profiles
fabprint profiles pin fabprint.toml           # pin profiles for reproducible builds
```

![fabprint watch](docs/images/watch.png)

See [docs/cli.md](docs/cli.md) for the full CLI reference with all flags and options.

See [docs/config.md](docs/config.md) for the complete TOML configuration reference.

## Docker

Pre-built images with OrcaSlicer are available on [Docker Hub](https://hub.docker.com/r/fabprint/fabprint):

```bash
docker pull fabprint/fabprint:orca-2.3.1
```

By default, fabprint uses Docker if available, falling back to a local slicer install:

```bash
fabprint run fabprint.toml --until slice                    # Docker first, local fallback
fabprint run fabprint.toml --until slice --local            # Force local slicer
fabprint run fabprint.toml --until slice --docker-version 2.3.1  # Pin Docker image version
```

For fully reproducible builds, pin both profiles and the OrcaSlicer version in your config:

```toml
[slicer]
engine = "orca"
version = "2.3.1"
```

To build your own image:

```bash
./scripts/build-docker.sh 2.3.2          # build only
./scripts/build-docker.sh 2.3.2 --push   # build and push
```

## Platform support

fabprint auto-detects slicer paths per platform:

| Platform | BambuStudio | OrcaSlicer |
|----------|-------------|------------|
| macOS    | `/Applications/BambuStudio.app/...` | `/Applications/OrcaSlicer.app/...` |
| Linux    | `/usr/bin/bambu-studio` | `/usr/bin/orca-slicer` |
| Windows  | `C:\Program Files\BambuStudio\...` | `C:\Program Files\OrcaSlicer\...` |

Slicers on PATH are also detected (Flatpak, Snap, custom installs). Profile directories follow platform conventions (`~/Library/Application Support/` on macOS, `~/.config/` on Linux, `%APPDATA%` on Windows).

## How it works

1. **Arrange** — loads meshes, orients them, and bin-packs onto the build plate
2. **Export** — writes a 3MF with per-object extruder metadata for correct AMS slot mapping
3. **Slice** — calls OrcaSlicer CLI to produce a Bambu Connect-compatible `.gcode.3mf`
4. **Post-process** — patches the sliced 3MF to fix metadata issues Bambu Connect requires (see [docs/gcode-3mf-format.md](docs/gcode-3mf-format.md))
5. **Send** — delivers to the printer via LAN, Bambu Connect, or cloud API

## Contributing

```bash
git clone https://github.com/pzfreo/fabprint.git
cd fabprint
uv sync --extra dev
uv run pytest              # run tests
uv run ruff check src tests     # lint
uv run ruff format src tests    # format
```

## License

Apache 2.0
