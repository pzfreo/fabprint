# fabprint

Headless 3D print pipeline: arrange parts on a build plate, slice to gcode, and print — all from a TOML config file.

## Features

- **Multi-format input** — STL, 3MF, and STEP files
- **Automatic orientation** — flat, upright, side, or custom rotations
- **Bin packing** — efficient 2D arrangement with configurable padding
- **Part scaling** — uniform scale factor per part
- **Multi-filament** — AMS slot assignment per part
- **Slicer integration** — BambuStudio and OrcaSlicer CLI support
- **Profile management** — discover, pin, and override slicer profiles
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
pip install ".[step]"   # STEP file support (build123d)
pip install ".[cloud]"  # Bambu Cloud API
pip install ".[all]"    # Everything
pip install ".[dev]"    # pytest + ruff
```

## Quick start

1. Create a `fabprint.toml` config:

```toml
[plate]
size = [256, 256]
padding = 5.0

[slicer]
engine = "orca"
printer = "Bambu Lab P1S 0.4 nozzle"
process = "0.20mm Standard @BBL X1C"
filaments = ["Generic PLA @base"]

[[parts]]
file = "my_part.stl"
copies = 4
orient = "flat"
filament = 1
```

2. Generate a build plate:

```bash
fabprint plate fabprint.toml -o plate.3mf
```

3. Slice to gcode:

```bash
fabprint slice fabprint.toml
```

## Config reference

### `[plate]`

| Key       | Type         | Default | Description              |
|-----------|--------------|---------|--------------------------|
| `size`    | `[w, h]`    | —       | Build plate size in mm   |
| `padding` | `float`      | `5.0`   | Gap between parts in mm  |

### `[slicer]`

| Key         | Type       | Default   | Description                        |
|-------------|------------|-----------|------------------------------------|
| `engine`    | `string`   | `"orca"`  | `"orca"` or `"bambu"`              |
| `printer`   | `string`   | —         | Printer profile name               |
| `process`   | `string`   | —         | Process profile name               |
| `filaments` | `[string]` | —         | Filament profiles (one per AMS slot) |

### `[slicer.overrides]`

Key-value pairs applied on top of the process profile:

```toml
[slicer.overrides]
enable_support = 1
wall_loops = 4
```

### `[[parts]]`

| Key        | Type       | Default      | Description                          |
|------------|------------|--------------|--------------------------------------|
| `file`     | `string`   | —            | Path to mesh file (STL/3MF/STEP)     |
| `copies`   | `int`      | `1`          | Number of copies                     |
| `orient`   | `string`   | `"upright"`  | `"flat"`, `"upright"`, `"side"`, or `"custom"` |
| `rotate`   | `[x,y,z]`  | `[0,0,0]`   | Custom rotation in degrees           |
| `filament` | `int`      | `1`          | AMS filament slot (1-indexed)        |
| `scale`    | `float`    | `1.0`        | Uniform scale factor                 |

## CLI commands

```
fabprint plate <config>           # Arrange and export 3MF
fabprint plate <config> --view    # Preview in viewer first
fabprint slice <config>           # Arrange, export, and slice to gcode
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

fabprint includes a Docker image with OrcaSlicer pre-installed for reproducible slicing without any local slicer setup.

### Build the image

```bash
docker build -t fabprint .
```

### Run from your project directory

```bash
docker run --rm -v "$PWD:/project" fabprint slice fabprint.toml
docker run --rm -v "$PWD:/project" fabprint plate fabprint.toml -o plate.3mf
docker run --rm fabprint profiles list
```

Or with docker compose:

```bash
docker compose run --rm fabprint slice fabprint.toml
```

### Automatic Docker fallback

If OrcaSlicer isn't installed locally, `fabprint slice` automatically delegates slicing to the Docker image. Plate arrangement still runs locally (it's pure Python), only the slicer step uses Docker. Set `FABPRINT_DOCKER_IMAGE` to override the image name.

### Reproducible builds

For production workflows, pin both profiles and OrcaSlicer version:

```bash
# Pin profiles into your project
docker run --rm -v "$PWD:/project" fabprint profiles pin fabprint.toml

# Build with a specific OrcaSlicer version
docker build --build-arg ORCA_VERSION=2.3.1 -t fabprint:orca-2.3.1 .
```

Commit the `profiles/` directory to git so slicing results are identical across machines.

## License

Apache 2.0
