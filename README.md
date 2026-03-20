# fabprint

[![PyPI version](https://img.shields.io/pypi/v/fabprint)](https://pypi.org/project/fabprint/)
[![CI](https://github.com/pzfreo/fabprint/actions/workflows/ci.yml/badge.svg)](https://github.com/pzfreo/fabprint/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/pypi/pyversions/fabprint)](https://pypi.org/project/fabprint/)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![codecov](https://codecov.io/gh/pzfreo/fabprint/branch/main/graph/badge.svg)](https://codecov.io/gh/pzfreo/fabprint)

**Reproducible 3D print builds from code.**

fabprint turns a print job into a version-controlled build: parts, arrangement, slicer settings,
printer target, and output artifacts are defined in a single `fabprint.toml`.

If your team already treats CAD, firmware, and manufacturing data like software, fabprint gives
you the same discipline for slicing and print preparation:

- Pin the slicer version for repeatable output
- Pin slicer profiles into the repo
- Generate G-code in Docker or CI
- Optionally hand the result off to a printer

Built for engineers, makers, and teams who treat their prints like software. Works with STL, STEP,
and 3MF files, and pairs naturally with code-CAD tools like [build123d](https://github.com/gumyr/build123d),
[OpenSCAD](https://openscad.org), and [cadquery](https://github.com/cadquery/cadquery).

```toml
# fabprint.toml — a multi-part print with slicer overrides

[[parts]]
file = "enclosure_base.step"
orient = "flat"
filament = 1                    # AMS slot 1: PETG-CF

[[parts]]
file = "enclosure_lid.step"
orient = "upright"
filament = 1

[[parts]]
file = "button_cap.stl"
copies = 4
filament = 2                    # AMS slot 2: PLA

[slicer]
engine = "orca"
version = "2.3.1"               # pinned for reproducibility
printer = "Bambu Lab P1S 0.4 nozzle"
process = "0.20mm Standard @BBL X1C"
filaments = ["Generic PETG-CF @base", "Generic PLA @base"]

[slicer.overrides]
sparse_infill_density = "25%"
enable_support = 1
brim_type = "auto_brim"

[printer]
name = "workshop"
```

```bash
fabprint run        # arrange → slice → print, one command
```

```
  Output → fabprint_output/enclosure
✔ Loaded 3 parts
✔ Arranged 3 parts onto plate  (256×256mm)
✔ Plate exported → plate.3mf
✔ Preview exported → plate_preview.3mf
✔ Sliced with OrcaSlicer 2.3.1 in 48s
✔ Print time: 3h 42m, 24.6g filament
✔ Sent to printer "workshop"
```

## What fabprint does

![fabprint demo](https://raw.githubusercontent.com/pzfreo/fabprint/main/docs/recordings/demo.gif)

1. **Define** parts + settings in `fabprint.toml`
2. **Arrange** — bin-packs models onto the build plate
3. **Slice** — using a pinned OrcaSlicer version (via Docker) for identical G-code across machines
4. **Print** — sends the result to your printer

Everything is declared in a single TOML file — git-friendly, diffable, and committable alongside
your CAD files. Lock the slicer version, pin the profiles, and the output is reproducible on any
machine or in CI.

![fabprint pipeline](https://raw.githubusercontent.com/pzfreo/fabprint/main/docs/images/pipeline.png)

### Why not just use OrcaSlicer CLI?

OrcaSlicer CLI is great for slicing a prepared plate. fabprint builds a reproducible pipeline around it:

- **Arrangement** — bin-packs multiple STLs onto the build plate (OrcaSlicer CLI has no arrange step)
- **Multi-part filament mapping** — per-part filament slot assignment and paint color preservation, injected into the 3MF metadata
- **Reproducible builds** — pin slicer profiles into your repo + lock OrcaSlicer version in Docker = identical gcode on any machine
- **Partial execution** — `--until plate` to inspect layout, `--only slice` to re-slice, `--dry-run` to test everything
- **Send to printer** — Bambu LAN, Bambu Cloud, and Moonraker/Klipper (experimental), with live status monitoring
- **Headless Docker slicing** — no GUI, no display server, works in CI, uses a specific OrcaSlicer version

## Best fit

fabprint is best suited to:

- Hardware teams keeping CAD and manufacturing inputs in Git
- Engineers who want deterministic slicing in CI
- Makers who want a declarative print workflow instead of slicer click-ops

If you mostly want interactive print setup in a GUI, use OrcaSlicer directly.

## Status

### Stable

- Declarative print config in `fabprint.toml`
- Multi-part arrangement
- Docker-based slicing with pinned OrcaSlicer versions
- Slicing for any printer supported by OrcaSlicer
- Profile pinning into your repository
- CI slicing and artifact generation
- Network print initiation via Bambu Cloud



### Experimental

- Bambu LAN printing
- Moonraker printing

## Quick start

**Prerequisites:** Python 3.11+ and [Docker](https://docs.docker.com/get-docker/). Docker is
central to fabprint — it runs OrcaSlicer in a container with a pinned version so every machine
produces identical G-code, and it powers cloud printing via the Bambu Connect bridge. A local
[OrcaSlicer](https://github.com/SoftFever/OrcaSlicer) install can be used as an alternative but is not recommended.

```bash
pip install fabprint
# or, to install as an isolated CLI tool:
pipx install fabprint
```

Generate a config with the interactive wizard, or dump a commented template:

```bash
fabprint setup                      # configures printer targets
fabprint init                       # interactive wizard — discovers profiles and CAD files, creates TOML
fabprint init --template            # dump a commented template
```

Or create `fabprint.toml` by hand (see [full config reference](https://github.com/pzfreo/fabprint/blob/main/docs/config.md)):

```toml
[pipeline]
stages = ["load", "arrange", "plate", "slice", "print"]

[printer]
name = "workshop"       # references ~/.config/fabprint/credentials.toml

[plate]
size = [256, 256]       # build plate dimensions in mm
padding = 5.0

[slicer]
engine = "orca"
version = "2.3.1"       # pin OrcaSlicer version for reproducibility
printer = "Bambu Lab P1S 0.4 nozzle"
process = "0.20mm Standard @BBL X1C"

[slicer.overrides]      # simple way to define print settings without editing JSON
sparse_infill_density = "30%"       # stronger infill
wall_loops = 3                       # extra wall strength
enable_support = 1
brim_type = "auto_brim"             # help adhesion
curr_bed_type = "Textured PEI Plate"

[[parts]]               # define multiple parts using STEP, STL or 3MF inputs
file = "frame.step"
rotate = [180, 0, 0]    # flip so mounting plate faces down
filament = "Generic PETG-CF @base"

[[parts]]
file = "wheel.stl"
copies = 5
orient = "upright"
filament = "Generic PETG-CF @base"
```

Run it (see [full CLI reference](https://github.com/pzfreo/fabprint/blob/main/docs/cli.md)):

```bash
fabprint run                   # arrange, slice and send to printer
fabprint run --until slice     # stop after slicing
fabprint run --dry-run         # full pipeline without sending to printer
```

The arrangement (`plate`) stage generates a `plate_preview.3mf` — open it in any 3MF viewer to check placement:

![plate preview](https://raw.githubusercontent.com/pzfreo/fabprint/main/docs/images/plate_preview.png)

## Reproducibility

Pin profiles into your repo so builds are identical across machines:

```bash
fabprint profiles pin          # copies slicer profiles into ./profiles/
git add profiles/              # commit to lock them
```

Combined with `version = "2.3.1"` in `[slicer]` (which pins the Docker image), the same config always produces the same gcode.

### CI/CD example

Automate slicing in GitHub Actions — push a commit, get G-code as a build artifact with print metrics on your PR:

```yaml
# .github/workflows/slice.yml
name: Slice
on: [push, pull_request]
jobs:
  slice:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: pzfreo/fabprint@main
        with:
          orca-version: "2.3.1"
```

The action slices your model, uploads G-code as an artifact, and posts print time / filament stats as a PR comment. See [`action/README.md`](action/README.md) for all options.

## CLI overview

```bash
fabprint init                        # interactive config wizard
fabprint init --template             # dump commented TOML template
fabprint validate                    # check config for issues
fabprint setup                       # set up a printer (credentials + connection type)
fabprint run                         # full pipeline
fabprint run --until plate           # stop after plating
fabprint run --only slice            # run just one stage
fabprint run --dry-run               # everything except sending to printer
fabprint watch                       # re-run pipeline when input files change
fabprint status                      # query printer status
fabprint status -w                   # live printer dashboard
fabprint profiles list               # list available slicer profiles
fabprint profiles pin                # pin profiles for reproducible builds
```

![fabprint status --watch](https://raw.githubusercontent.com/pzfreo/fabprint/main/docs/images/watch.png)

## Credentials

Printer credentials are stored in `~/.config/fabprint/credentials.toml`, created by `fabprint setup`. The file is set to `600` permissions (owner read/write only) and is never committed to your repo — only the printer *name* appears in `fabprint.toml`. Credentials can also be supplied via environment variables (`BAMBU_PRINTER_IP`, `BAMBU_ACCESS_CODE`, `BAMBU_SERIAL`) for CI or shared environments.

## Documentation

- [CLI reference](https://github.com/pzfreo/fabprint/blob/main/docs/cli.md) — all commands, flags, and pipeline stages
- [Config reference](https://github.com/pzfreo/fabprint/blob/main/docs/config.md) — complete TOML format
- [Developing](https://github.com/pzfreo/fabprint/blob/main/docs/developing.md) — setup, testing, architecture

## License

Apache 2.0
