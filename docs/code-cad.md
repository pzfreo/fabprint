# Code-CAD workflow

fabprint works with any tool that produces STL, STEP, or 3MF files. This guide shows how to integrate it with code-CAD tools like OpenSCAD, build123d, and CadQuery for a fully reproducible, version-controlled 3D printing workflow.

## The idea

Instead of manually importing files into a slicer GUI and configuring settings by hand, you define everything in a `fabprint.toml` alongside your CAD source. The entire print job — models, slicer settings, orientation, plate layout — is captured in text files you can commit to git.

```
my-project/
  widget.scad          # CAD source (OpenSCAD, build123d, etc.)
  widget.stl           # generated mesh
  fabprint.toml        # print config
  profiles/            # pinned slicer profiles (optional)
  .gitignore           # ignore fabprint_output/
```

## OpenSCAD

Generate STL from the command line, then slice with fabprint:

```bash
# Regenerate mesh from source
openscad -o widget.stl widget.scad

# Slice and print
fabprint run
```

`fabprint.toml`:
```toml
name = "widget"

[slicer]
engine = "orca"
version = "2.3.1"
printer = "Bambu Lab P1S 0.4 nozzle"
process = "0.20mm Standard @BBL X1C"

[slicer.overrides]
enable_support = 1

[[parts]]
file = "widget.stl"
copies = 2
orient = "flat"
filament = "Generic PLA @base"
```

For parametric designs, pass variables on the command line:

```bash
openscad -o bracket_m4.stl -D 'bolt_dia=4' bracket.scad
openscad -o bracket_m5.stl -D 'bolt_dia=5' bracket.scad
```

## build123d (Python)

build123d outputs STEP files directly, which fabprint loads via its built-in build123d integration:

```python
# widget.py
from build123d import *

with BuildPart() as widget:
    Box(50, 30, 10)
    # ...

export_step(widget.part, "widget.step")
```

```bash
python widget.py
fabprint run
```

`fabprint.toml`:
```toml
name = "widget"

[slicer]
engine = "orca"
version = "2.3.1"
printer = "Bambu Lab P1S 0.4 nozzle"
process = "0.20mm Standard @BBL X1C"

[[parts]]
file = "widget.step"
rotate = [180, 0, 0]
filament = "Generic PETG-CF @base"
```

STEP files are converted to mesh via build123d at load time. The tessellation quality matches build123d's defaults.

## CadQuery

CadQuery can export STL or STEP:

```python
import cadquery as cq

result = cq.Workplane("XY").box(50, 30, 10)
cq.exporters.export(result, "widget.step")
```

Then use the same `fabprint.toml` approach as build123d above.

## Reproducible builds

Three things make a fabprint build fully reproducible:

1. **Pin the OrcaSlicer version** — `version = "2.3.1"` in `[slicer]` ensures Docker uses the exact same slicer binary everywhere.

2. **Pin slicer profiles** — `fabprint profiles pin` copies the referenced profiles into a `profiles/` directory. Commit this to git so builds don't depend on locally installed profiles.

3. **Use Docker for slicing** — Docker is the default when available. It isolates the slicer from the host system, ensuring identical output across macOS, Linux, and CI.

```bash
fabprint profiles pin    # copies profiles into ./profiles/
git add profiles/        # commit pinned profiles
```

With all three, anyone can clone your repo and produce identical G-code with `fabprint run`.

## Git workflow

Commit:
- `*.scad`, `*.py` — CAD source files
- `*.stl`, `*.step` — generated meshes (or regenerate in CI)
- `fabprint.toml` — print configuration
- `profiles/` — pinned slicer profiles

Gitignore:
```
fabprint_output/
```

## CI integration

Use the fabprint GitHub Action to slice on every PR:

```yaml
- uses: pzfreo/fabprint@main
  with:
    config: fabprint.toml
    orca-version: "2.3.1"
```

This slices the model, posts print time and filament usage as a PR comment, and uploads the G-code as an artifact. See [action/README.md](../action/README.md) for all options.

## Partial runs

During iteration, you often want to re-run just part of the pipeline:

```bash
fabprint run --until plate     # stop after arrangement (check layout)
fabprint run --only slice      # re-slice without re-arranging
fabprint run --dry-run         # full pipeline without sending to printer
```
