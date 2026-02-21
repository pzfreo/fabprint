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

Steps 2–7 are entirely manual and repetitive. I want a CLI tool that takes a project config file listing parts, quantities, and settings, then handles arrangement, slicing, and printing headlessly. The slicer GUI should only be needed for a final visual check if desired.

## Research Summary

### Existing tools (none cover the full pipeline)

- **BambuStudio CLI** (`/Applications/BambuStudio.app/Contents/MacOS/BambuStudio`): Supports `--arrange`, `--orient`, `--slice`, `--load-settings`, `--load-filaments`, `--export-3mf`. However, `--arrange` has known bugs (parts placed off-plate instead of creating new plates). Installed locally, version 02.05.00.66.
- **OrcaSlicer CLI** (`/Applications/OrcaSlicer.app/Contents/MacOS/OrcaSlicer`): Similar flags, version 2.3.1. Same `--arrange` issues.
- **Plater** (github.com/Rhoban/Plater): C++ build plate arranger. STL only, no slicing/printing integration, not Python.
- **Printago**: Commercial SaaS print farm manager. Has an OSS slicer server component but the full pipeline is proprietary.
- **bambu-lab-cloud-api** (PyPI): Python library for Bambu Cloud API — authentication, file upload, print commands, MQTT monitoring. Well maintained, works with current firmware.
- **rectpack** (PyPI): 2D rectangle bin packing. MaxRectsBssf algorithm. Pure Python, battle-tested.
- **trimesh** (PyPI): Mesh loading, bounding boxes, transformations, scene export to 3MF. Already used in gib-tuners-mk2.

### Existing patterns to reuse

The `gib-tuners-mk2` project at `~/repos/gib-tuners-mk2/scripts/generate_print_plate.py` has a working pattern:
- `Packable` dataclass wrapping a trimesh mesh with translate/rotate/center operations
- `b3d_to_trimesh()` converts build123d shapes to trimesh via temp STL (needed for manifold mesh)
- Simple row-based bin packing with largest-first sorting
- Scene export to 3MF via trimesh
- Orientation presets per part type (flat, upright, tilted for resin)
- Build plate size constants (256×256mm for Bambu P1S)

### Multi-colour 3MF format

The `peg` project at `~/repos/peg/export_3mf.py` has working multi-colour export using BambuStudio's native `paint_color` per-triangle attribute format. Key details:
- Attribute: `paint_color` (no namespace prefix) on `<triangle>` elements
- Encoding: `hex(state << 2)` where state = extruder_idx + 1 ("4" = filament 1, "8" = filament 2)
- Metadata: `<metadata name="BambuStudio:MmPaintingVersion">0</metadata>`
- Requires STL round-trip for manifold mesh (build123d `tessellate()` produces non-manifold edges)

## Architecture

### Project structure

```
fabprint/
├── pyproject.toml
├── README.md
├── src/fabprint/
│   ├── __init__.py
│   ├── cli.py              # Entry point, argparse with subcommands
│   ├── config.py           # Load/validate fabprint.toml
│   ├── loader.py           # Load STEP/STL/3MF → trimesh
│   ├── orient.py           # Orientation presets (flat, upright, custom)
│   ├── arrange.py          # 2D bin packing via rectpack
│   ├── plate.py            # Assemble arranged parts → 3MF
│   ├── slicer.py           # Shell out to BambuStudio/OrcaSlicer CLI
│   └── printer.py          # Upload & print via bambu-lab-cloud-api
├── tests/
│   ├── test_config.py
│   ├── test_loader.py
│   ├── test_arrange.py
│   ├── test_plate.py
│   └── fixtures/           # Small test STL/STEP files
└── examples/
    └── fabprint.toml       # Example project config
```

### Config format (`fabprint.toml`)

```toml
[plate]
size = [256, 256]       # mm, Bambu P1S
padding = 5.0           # mm between parts

[slicer]
engine = "bambu"        # "bambu" or "orca"
print_profile = "profiles/0.2mm_standard.json"
filaments = ["profiles/pla_white.json", "profiles/pla_blue.json"]
printer_profile = "profiles/p1s.json"

[printer]
method = "cloud"        # "cloud" or "local" or "none"
# Credentials via BAMBU_EMAIL / BAMBU_PASSWORD env vars

[[parts]]
file = "ring.3mf"       # Supports .step, .stl, .3mf
copies = 4
orient = "flat"         # "flat" (default) | "upright" | "side"

[[parts]]
file = "pip.stl"
copies = 4
orient = "upright"
```

### CLI interface

```bash
# Install
pip install -e .

# Subcommands
fabprint plate fabprint.toml              # Arrange → export 3MF
fabprint plate fabprint.toml -o out.3mf   # Custom output path
fabprint slice fabprint.toml              # Arrange → slice → gcode
fabprint slice fabprint.toml -o out.gcode
fabprint print fabprint.toml              # Arrange → slice → send to printer
fabprint print fabprint.toml --dry-run    # Everything except sending to printer
```

### Module details

**`config.py`** — Parse `fabprint.toml` into dataclasses. Validate paths exist, plate size is reasonable, orient values are valid. Use `tomllib` (Python 3.11+) or `tomli` for older Python.

**`loader.py`** — Load mesh files into trimesh:
- `.stl`: `trimesh.load(path)` directly
- `.step`/`.stp`: `build123d.import_step(path)` → `export_stl` to temp file → `trimesh.load`
- `.3mf`: `trimesh.load(path)` directly
- Return a `trimesh.Trimesh` object for each part

**`orient.py`** — Apply orientation to a trimesh mesh:
- `flat`: Rotate so largest flat face is on XY plane (use trimesh's `trimesh.bounds.oriented_bounds` or principal inertia axes), then drop to Z=0
- `upright`: Keep as-is, just drop to Z=0
- `side`: Rotate 90° around X, drop to Z=0
- Custom: Accept `[rx, ry, rz]` rotation angles in config

**`arrange.py`** — Pack parts onto build plate:
- Compute XY bounding box for each oriented part
- Use `rectpack.newPacker(mode=rectpack.PackingMode.Offline, rotation=True)` with MaxRectsBssf
- Add plate as bin, add all parts as rectangles (with padding)
- Return list of (part_index, x, y, rotated) placements
- Translate each trimesh mesh to its packed position

**`plate.py`** — Combine arranged meshes into a single 3MF:
- Build a `trimesh.Scene` from all placed meshes
- Export via `scene.export("output.3mf")`
- If any input was a multi-colour 3MF (with paint_color), preserve those attributes (stretch goal — not MVP)

**`slicer.py`** — Call BambuStudio or OrcaSlicer CLI:
- Auto-detect slicer path on macOS (`/Applications/BambuStudio.app/...` or `/Applications/OrcaSlicer.app/...`)
- Build command: `[slicer_path, "--slice", input_3mf, "--load-settings", print_profile, "--load-filaments", *filaments, "-o", output_gcode]`
- Run via `subprocess.run`, capture stdout/stderr, raise on failure
- Return path to generated gcode

**`printer.py`** — Send to Bambu printer via cloud API:
- Read `BAMBU_EMAIL` and `BAMBU_PASSWORD` from environment
- Authenticate via `bambu-lab-cloud-api`
- Auto-discover printers on account, select first (or allow `--printer` flag)
- Upload gcode file
- Send print command
- Print confirmation message with job ID

### Dependencies

```toml
[project]
name = "fabprint"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "trimesh[easy]>=4.0.0",
    "rectpack>=0.2.0",
]

[project.optional-dependencies]
step = ["build123d>=0.10.0"]       # For loading STEP files
cloud = ["bambu-lab-cloud-api"]     # For sending to printer
all = ["fabprint[step,cloud]"]

[project.scripts]
fabprint = "fabprint.cli:main"
```

## Implementation Order

### Phase 1: Core plate generation (MVP)
1. Scaffold repo, pyproject.toml, src layout
2. `config.py` — TOML parsing and validation
3. `loader.py` — STL and STEP loading
4. `orient.py` — flat/upright/side presets
5. `arrange.py` — rectpack bin packing
6. `plate.py` — 3MF export via trimesh
7. `cli.py` — `plate` subcommand only
8. Test with parts from the `peg` project

### Phase 2: Slicing
9. `slicer.py` — BambuStudio CLI wrapper
10. `cli.py` — add `slice` subcommand
11. Test by slicing the generated plate 3MF, verify gcode output

### Phase 3: Printing
12. `printer.py` — Bambu Cloud API integration
13. `cli.py` — add `print` subcommand
14. Test end-to-end: generate → slice → send to P1S

## Testing Strategy

### Unit tests
- `test_config.py`: Valid TOML parsing, missing fields, bad values, relative/absolute paths
- `test_loader.py`: Load a small test STL, verify vertex count and bounds. Load a test STEP if build123d available, skip otherwise.
- `test_arrange.py`: Pack known rectangles onto a plate, verify all fit within bounds, no overlaps. Test with parts that don't all fit (expect error or warning).
- `test_plate.py`: Arrange 2 small STLs, export 3MF, reload and verify mesh count.

### Integration tests
- `test_slicer.py`: Skip if BambuStudio not installed. Slice a small 3MF, verify gcode file is created and non-empty.
- `test_printer.py`: Skip unless `BAMBU_EMAIL` is set. Test authentication only (don't actually print).

### Test fixtures
Include 2-3 small STL files in `tests/fixtures/` (e.g. a 10mm cube, a small cylinder) for fast deterministic tests.

### Running tests
```bash
pytest tests/
pytest tests/ -k "not slicer and not printer"  # Skip integration tests
```

## Code Quality

- **Type hints** on all public functions
- **Docstrings** on modules and public functions
- **ruff** for linting and formatting (pyproject.toml config)
- **pytest** for testing
- **No classes where functions suffice** — keep it simple, this is a CLI tool not a framework
- **Fail early with clear errors** — validate config upfront, check file existence, check slicer is installed before slicing
- **Logging** via Python `logging` module, `--verbose` flag for debug output

### pyproject.toml ruff config
```toml
[tool.ruff]
target-version = "py311"
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "W"]
```

## Environment Setup

```bash
mkdir ~/repos/fabprint && cd ~/repos/fabprint
git init
python -m venv .venv
source .venv/bin/activate
pip install -e ".[all]"
```

## Open Questions / Future Work

- **Multi-colour preservation**: When a part has `paint_color` data in its 3MF, this should survive the arrange+export pipeline. May need custom 3MF writing rather than trimesh's generic export. Not MVP.
- **Multiple plates**: If parts don't all fit on one plate, generate multiple plate files. rectpack supports multiple bins.
- **Print profiles**: BambuStudio profile JSONs are complex. May need a simpler abstraction (e.g. `profile = "0.2mm PLA"` that maps to a known profile path). Needs experimentation with BambuStudio CLI.
- **LAN mode**: Alternative to cloud API for users who don't want cloud. Uses printer IP + access code via FTP/MQTT. Defer to Phase 3+.
- **Bambu firmware auth changes**: January 2025 firmware restricts third-party access. P1S timeline unclear. Monitor and adapt.
