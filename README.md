# fabprint

[![PyPI version](https://img.shields.io/pypi/v/fabprint)](https://pypi.org/project/fabprint/)
[![CI](https://github.com/pzfreo/fabprint/actions/workflows/ci.yml/badge.svg)](https://github.com/pzfreo/fabprint/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/pypi/pyversions/fabprint)](https://pypi.org/project/fabprint/)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)

**Reproducible 3D print pipeline**: define parts, slicer settings, and printer targets in a TOML file — arrange, slice, and print from the command line.

![fabprint pipeline](docs/images/pipeline.png)

## Why fabprint?

Code-CAD tools like [build123d](https://github.com/gumyr/build123d), [OpenSCAD](https://openscad.org) and [cadquery](https://github.com/cadquery/cadquery) let you define parts in code — parametric, testable, version-controlled. But the moment you print, that breaks: open a slicer GUI, drag in files, fiddle with settings. No diffs, no reproducibility.

fabprint closes the gap:

- **Everything is text** — TOML config, git-friendly, diffable
- **Pinned profiles** — lock exact slicer, filament, and process profiles in your repo
- **Slicer overrides** — tweak support, bed type, wall count without touching profile files
- **Versioned Docker slicing** — pin OrcaSlicer version for identical gcode across machines
- **One command** — `fabprint run` goes from STL files to a running print

## Quick start

```bash
pip install fabprint
```

Create `fabprint.toml` (see [full config reference](docs/config.md)):

```toml
[pipeline]
stages = ["load", "arrange", "plate", "slice", "print"]

[printer]
mode = "cloud-bridge"
name = "workshop"       # references ~/.config/fabprint/credentials.toml

[plate]
size = [256, 256]       # build plate dimensions in mm
padding = 5.0

[slicer]
engine = "orca"
version = "2.3.1"       # pin OrcaSlicer version for reproducibility
printer = "Bambu Lab P1S 0.4 nozzle"
process = "0.20mm Standard @BBL X1C"

[slicer.overrides]
enable_support = 1
curr_bed_type = "Textured PEI Plate"

[[parts]]
file = "frame.stl"
rotate = [180, 0, 0]    # flip so mounting plate faces down
filament = "Generic PETG-CF @base"

[[parts]]
file = "wheel.stl"
copies = 5
orient = "upright"
filament = "Generic PETG-CF @base"
```

Run it (see [full CLI reference](docs/cli.md)):

```bash
fabprint run                   # arrange, slice and send to printer
fabprint run --until plate     # stop after plating
fabprint run --until slice     # stop after slicing
fabprint run --dry-run         # full pipeline without sending to printer
```

The plate stage generates a `plate_preview.3mf` — open it in any 3MF viewer to check placement:

![plate preview](docs/images/plate_preview.png)

## Reproducibility

Pin profiles into your repo so builds are identical across machines:

```bash
fabprint profiles pin          # copies slicer profiles into ./profiles/
git add profiles/              # commit to lock them
```

Combined with `version = "2.3.1"` in `[slicer]` (which pins the Docker image), the same config always produces the same gcode.

## CLI overview

```bash
fabprint run                         # full pipeline
fabprint run --until plate           # stop after plating
fabprint run --only slice            # run just one stage
fabprint run --dry-run               # everything except sending to printer
fabprint login                       # log in to Bambu Cloud
fabprint watch                       # live printer dashboard
fabprint status                      # query printer status
fabprint profiles list               # list available slicer profiles
fabprint profiles pin                # pin profiles for reproducible builds
```

![fabprint watch](docs/images/watch.png)

## Documentation

- [CLI reference](docs/cli.md) — all commands, flags, and pipeline stages
- [Config reference](docs/config.md) — complete TOML format
- [Developing](docs/developing.md) — setup, testing, architecture

## License

Apache 2.0
