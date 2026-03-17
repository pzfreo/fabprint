# Fabprint GitHub Action

Slice 3D models with OrcaSlicer on every push or PR — get build metrics (print time, filament usage) posted as a PR comment automatically.

## Usage

```yaml
name: Slice

on:
  push:
    branches: [main]
  pull_request:

jobs:
  slice:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: pzfreo/fabprint/action@main
        with:
          config: fabprint.toml
```

## Inputs

| Input | Default | Description |
|-------|---------|-------------|
| `config` | `fabprint.toml` | Path to your fabprint config file |
| `orca-version` | `2.3.1` | OrcaSlicer version |
| `until` | `slice` | Pipeline stage to stop at (`load`, `arrange`, `plate`, `slice`) |
| `comment` | `true` | Post/update a PR comment with build metrics |

## Outputs

| Output | Description |
|--------|-------------|
| `print-time` | Estimated print time (e.g., "1h 7m 32s") |
| `filament-grams` | Total filament in grams |
| `gcode-path` | Path to output directory |

## What it does

1. Builds a Docker image with OrcaSlicer and fabprint
2. Runs `fabprint run` against your config (stops before printing)
3. Uploads sliced `.gcode` and `.3mf` files as workflow artifacts
4. Posts a PR comment with print time and filament usage

## Requirements

- A `fabprint.toml` in your repo (see [config docs](../docs/config.md))
- STL/3MF/STEP model files referenced in your config
