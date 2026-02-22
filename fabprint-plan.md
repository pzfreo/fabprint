# fabprint — Headless 3D Print Pipeline

## Motivation

I have multiple build123d CAD projects (violin peg decorations, guitar tuner restorations) that produce STEP and STL files for printing on a Bambu Lab P1S. The current workflow is tedious:

1. Run a Python script to generate parts (STEP/STL/3MF)
2. Open Bambu Studio GUI
3. Import each file manually
4. Arrange parts on the build plate
5. Configure filaments and print settings
6. Slice
7. Send to printer

Steps 2–7 are entirely manual and repetitive. fabprint is a CLI tool that takes a project config file listing parts, quantities, and settings, then handles arrangement, slicing, and printing headlessly.

## Current State

### What's built (Phases 1 & 2 complete)

**Core pipeline** — 1,255 lines of source, 963 lines of tests (67 tests):

- **Config** (`config.py`) — TOML parsing with validation. Plate size, slicer settings, per-part orient/copies/filament/scale, slicer overrides.
- **Loader** (`loader.py`) — STL and STEP file loading via trimesh (STEP requires `build123d` optional dep).
- **Orientation** (`orient.py`) — flat, upright, side presets, plus custom `[rx, ry, rz]` rotations.
- **Arrangement** (`arrange.py`) — 2D bin packing via rectpack (MaxRectsBssf). Padding, overflow detection.
- **Plate assembly** (`plate.py`) — trimesh Scene → 3MF export. Origin-centered for slicer compatibility.
- **Slicing** (`slicer.py`) — BambuStudio and OrcaSlicer CLI integration. Profile flattening (resolves full `inherits` chain), overrides (values converted to strings), gcode stats parsing (filament grams/cm3, print time).
- **Profiles** (`profiles.py`) — Discover system profiles, 3-tier resolution (path → pinned → system), inheritance flattening, profile pinning for reproducibility.
- **Docker** — Versioned OrcaSlicer Docker images (`fabprint:orca-2.3.1`). `--docker` / `--docker-version` CLI flags. Automatic fallback when local slicer not installed. Published to Docker Hub (`fabprint/fabprint`).
- **Viewer** (`viewer.py`) — Optional OCP CAD viewer for plate preview (`--view`).
- **CLI** (`cli.py`) — `plate`, `slice`, `profiles list`, `profiles pin` subcommands. `--scale`, `--docker`, `--docker-version`, `--view`, `--verbose` flags.
- **CI** — GitHub Actions: lint + test on push, manual Docker slice workflow.
- **Cross-platform** — macOS, Linux, Windows slicer path detection with PATH fallback.

### Build output example

```
Parts:
  frame_lh_5gang  x1  slot 3  1.5x  15x218x15mm
  peg_head_lh     x5  slot 3  1.5x  42x19x13mm
  wheel_lh        x5  slot 3  1.5x  11x11x12mm
  string_post     x5  slot 3  1.5x  22x11x11mm

Plate: 16 parts on 256x256mm
Plate exported to plate.3mf
Sliced gcode in output/
  40.4g filament, estimated 2h 30m 2s
```

### Key technical decisions made

- **Profile flattening**: Slicer profiles use JSON with `inherits` chains up to 4 levels deep. All profiles are fully flattened before passing to slicer CLI, so overrides actually take effect and Docker containers don't need parent profiles.
- **All profile values are strings**: TOML integers in overrides must be `str()` converted.
- **3MF origin centering**: OrcaSlicer expects bed center at (0,0). Meshes are offset by -plate_center.
- **`--load-filament-ids` is STL-only**: Skipped for 3MF inputs (OrcaSlicer limitation).
- **Docker profile mounting**: Profiles written under `output_dir/.profiles/` to share the same volume mount (avoids macOS Docker temp-dir visibility issues).

## Project structure

```
fabprint/
├── pyproject.toml
├── Dockerfile
├── docker-compose.yml
├── scripts/build-docker.sh
├── .github/workflows/
│   ├── ci.yml                 # Lint + test
│   └── slice.yml              # Manual Docker slice
├── src/fabprint/
│   ├── cli.py                 # Entry point, subcommands
│   ├── config.py              # TOML parsing and validation
│   ├── loader.py              # STL/STEP → trimesh
│   ├── orient.py              # Orientation presets
│   ├── arrange.py             # 2D bin packing
│   ├── plate.py               # Scene → 3MF
│   ├── profiles.py            # Profile discovery, resolution, pinning
│   ├── slicer.py              # Slicer CLI + Docker integration
│   └── viewer.py              # OCP CAD viewer
├── tests/                     # 67 tests
├── examples/
│   └── gib-tuners-c13-10/     # Example with pinned profiles
└── fabprint-plan.md           # This file
```

### Dependencies

```toml
dependencies = ["trimesh[easy]>=4.0.0", "rectpack>=0.2.0"]

[project.optional-dependencies]
step = ["build123d>=0.10.0"]
cloud = ["bambu-lab-cloud-api"]
dev = ["pytest>=8.0.0", "ruff>=0.4.0"]
```

## Phase 3: Cloud Printing (not started)

Send sliced gcode to a Bambu Lab printer via the cloud API.

- `printer.py` — Authenticate via `bambu-lab-cloud-api`, discover printers, upload gcode, send print command
- `cli.py` — add `print` subcommand
- Credentials via `BAMBU_EMAIL` / `BAMBU_PASSWORD` env vars
- `--dry-run` flag for testing without printing
- End-to-end test: generate → slice → send to P1S

### Open questions for Phase 3

- **Bambu firmware auth changes**: January 2025 firmware restricts third-party access. P1S timeline unclear.
- **LAN mode**: Alternative to cloud for users who don't want cloud. Uses printer IP + access code via FTP/MQTT. Could be a Phase 4.

## Future work

- **Multi-colour preservation**: When a part has `paint_color` data in its 3MF, preserve through the arrange+export pipeline.
- **Multiple plates**: If parts don't fit on one plate, generate multiple plate files (rectpack supports multiple bins).
- **Per-plate filament assignment**: Currently filament IDs are only supported for STL inputs, not 3MF.
