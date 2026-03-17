# Developing fabprint

## Setup

Requires Python 3.11+.

```bash
git clone https://github.com/pzfreo/fabprint.git
cd fabprint
uv sync --extra dev
```

## Running tests

```bash
uv run pytest              # run all tests
uv run pytest -x -q        # stop on first failure, quiet output
uv run ruff check src tests     # lint
uv run ruff format src tests    # auto-format
```

## Pre-PR checklist

Before pushing a PR branch:

1. `uv run ruff check src tests` — lint must pass with zero errors
2. `uv run ruff format --check src tests` — formatting must pass
3. `uv run pytest` — all tests must pass

## Docker images

Pre-built OrcaSlicer images are on [Docker Hub](https://hub.docker.com/r/fabprint/fabprint). To build your own:

```bash
./scripts/build-docker.sh 2.3.2          # build only
./scripts/build-docker.sh 2.3.2 --push   # build and push
```

fabprint auto-detects Docker and uses it for slicing when available, falling back to a local slicer install. Force local with `--local`, or pin a Docker version with `--docker-version 2.3.1`.

## Platform support

fabprint auto-detects slicer paths per platform:

| Platform | BambuStudio | OrcaSlicer |
|----------|-------------|------------|
| macOS    | `/Applications/BambuStudio.app/...` | `/Applications/OrcaSlicer.app/...` |
| Linux    | `/usr/bin/bambu-studio` | `/usr/bin/orca-slicer` |
| Windows  | `C:\Program Files\BambuStudio\...` | `C:\Program Files\OrcaSlicer\...` |

Slicers on PATH are also detected (Flatpak, Snap, custom installs). Profile directories follow platform conventions (`~/Library/Application Support/` on macOS, `~/.config/` on Linux, `%APPDATA%` on Windows).

## Architecture

The pipeline is built on [Hamilton](https://github.com/DAGWorks-Inc/hamilton), a lightweight DAG framework. Each stage is a Python function in `src/fabprint/pipeline.py` — Hamilton auto-wires dependencies by matching parameter names to function names.

```
load → arrange → plate → slice → gcode-info → print
```

The `TimingAdapter` in `src/fabprint/adapters.py` hooks into Hamilton's lifecycle to log per-stage timing when `--verbose` is used.

### Key files

| File | Purpose |
|------|---------|
| `src/fabprint/pipeline.py` | Hamilton DAG nodes and stage registry |
| `src/fabprint/adapters.py` | TimingAdapter for observability |
| `src/fabprint/cli.py` | CLI entry point (`run`, `login`, `status`, `watch`, `profiles`) |
| `src/fabprint/config.py` | TOML parsing and validation |
| `src/fabprint/arrange.py` | 2D bin-packing |
| `src/fabprint/plate.py` | 3MF export with extruder metadata |
| `src/fabprint/slicer.py` | OrcaSlicer CLI integration (local + Docker) |
| `src/fabprint/printer.py` | Print dispatch (LAN, cloud, Bambu Connect) |
| `src/fabprint/credentials.py` | Credential loading from `~/.config/fabprint/credentials.toml` |
