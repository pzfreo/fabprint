# Changelog

All notable changes to fabprint are documented here.

## 0.1.96 — 2026-03-19

- Standardize type annotations: `Optional[X]` → `X | None` across cli.py and pipeline.py
- Add `log.debug()` to all silent `except Exception` catches for easier debugging
- Replace `sys.exit(1)` in auth.py with `raise FabprintError` for consistent error handling
- Move `_PRINT_STAGES` dict to module level in cli.py
- Use `TYPE_CHECKING` guard for Rich `Status` import in adapters.py
- Replace bare `print()` with `log.info()` in printer.py
- Add `require_file()` helper to reduce duplicated file-existence checks across cloud.py, slicer.py, gcode.py
- Extract `_resolve_filaments()` helper from `load_config()` for readability
- Split `run_wizard()` into 6 focused step functions
- Add `PrinterCredentials` TypedDict for structured credential returns
- Improve test coverage from 60% to 69%: new tests for auth, adapters, ui, cli, pipeline, credentials, loader
- Split `cloud.py` (1180 lines) into `cloud/` package: `bridge.py`, `http.py`, `ams.py` with backward-compatible re-exports
- Extract thumbnail rendering from `slicer.py` into new `thumbnails.py` module

## 0.1.95 — 2026-03-19

- Fix duplicate printer table shown during cloud setup
- Fix Rich markup rendering in printer status column (green/dim colors now display correctly)
- Live interactive search in `fabprint init` — results filter as you type, auto-selects single match
- Auto-send verification code during cloud login (removes confusing prompt)
- Mask verification code and 2FA code input
- Add slicer override picker to `fabprint init` — choose common settings like infill, supports, seam position with value pickers
- Slicer version picker fetches available Docker image versions from DockerHub instead of free text input
- Enhanced `fabprint validate`: check part file readability, file extensions, duplicate parts, plate size sanity, and pipeline stage ordering

## 0.1.94 — 2026-03-19

- Override cadquery-ocp's vtk==9.3.1 pin to vtk>=9.4, enabling Python 3.13 support
- Remove Python 3.14 from CI matrix (cadquery-ocp lacks cp314 wheels)

## 0.1.92 — 2026-03-19

- Mask printer serial numbers in setup and status output for security (shows last 4 chars only)
- Redesign `setup` and `init` CLI with Rich: styled prompts, tables, section headings, syntax-highlighted TOML preview
- Add interactive search-and-pick for profile selection with highlighted matches
- Add password masking for cloud login input
- Replace manual ANSI escape codes with Rich color swatches
- Drop Python upper bound: now supports Python 3.11+ (including 3.13 and 3.14)

## 0.1.90 — 2026-03-18

- Add `fabprint watch` command — watches input files and re-runs pipeline on changes
- Refactor `run` command to share pipeline logic with `watch`

## 0.1.89 — 2026-03-18

- Add bundled OrcaSlicer profile name lists for Docker-only environments
- Add `fabprint profiles add` command to import custom/third-party profiles from files or URLs
- Add Docker fallback for `fabprint profiles pin` when OrcaSlicer isn't installed locally
- Fix false-positive profile warnings in `fabprint validate` for Docker-only users
- Unify profile discovery with three-tier fallback: system install → pinned → bundled

## 0.1.85 — 2026-03-18

- Add code-CAD workflow tutorial (`docs/code-cad.md`) for OpenSCAD, build123d, CadQuery
- Add common slicer overrides reference table to `docs/config.md`
- Expand `fabprint init` wizard documentation with full feature list
- Add `pipx install fabprint` recommendation in README
- Gitignore `squashfs-root/`, `fabprint_output/`, debug logs
- Remove tracked debug scratch files from docs/

## 0.1.84 — 2026-03-18

- Make `build123d` a default dependency (STEP file support out of the box)
- Require Python 3.11–3.12 (vtk doesn't have 3.13 wheels yet)
- Remove `[step]` optional extra

## 0.1.82 — 2026-03-18

- Default output directory is now `fabprint_output/{name}/` when `name` is set
- Default output directory without `name` is `fabprint_output/`
- Explicit `-o` overrides the default

## 0.1.81 — 2026-03-18

- Avoid resolving the same filament profile multiple times for gap slots
- Warn when `slicer.version` is not set in config (builds may not be reproducible)
- Fix GitHub Action: use `--local` to avoid Docker-in-Docker failure when slicing

## 0.1.80 — 2026-03-18

- Fix GitHub Action: use `--local` to avoid Docker-in-Docker failure when slicing

## 0.1.79 — 2026-03-18

- Include `gcode_stats` in `slice` stage so metrics are always available after slicing
- Fix GitHub Action: metrics now extracted from pipeline output (no extra Docker run)

## 0.1.78 — 2026-03-18

- Merge `watch` command into `status --watch` / `status -w`
- Remove standalone `watch` subcommand
- Warn when Docker not available for cloud printing or slicer fallback
- Fix GitHub Action: use project name in artifact name to avoid collisions
- Fix GitHub Action: per-project PR comment markers for multi-config workflows

## 0.1.77 — 2026-03-18

- Fix GitHub Action: project name extraction matched `[printer] name` in addition to top-level `name`
- Fix GitHub Action: guard metrics parsing against multi-line grep output

## 0.1.74 — 2026-03-18

- Add top-level `name` field to `fabprint.toml` to prefix all output filenames
- Add `docs/printers.md` documenting all printer types and testing status
- Mark bambu-lan as experimental (untested against real hardware)
- Verify Moonraker support against virtual-klipper-printer Docker image
- Fix GitHub Action: metrics parsing, artifact upload, and output wiring
- Fix wizard tests depending on user's real credentials file

## 0.1.53 — 2026-03-17

- Fix LICENSE copyright placeholder
- Add `py.typed` marker for PEP 561 type checking
- Add `__all__` to `__init__.py`
- Add `SECURITY.md`

## 0.1.51 — 2026-03-17

- Add project metadata to `pyproject.toml` (license, authors, keywords, URLs, classifiers)
- Fix Dockerfile missing LICENSE for hatchling build

## 0.1.50 — 2026-03-17

- Fix per-object filament resolution bug (`config.py:262`) — only the last part's
  `[parts.filaments]` overrides were applied in multi-part configs
- Replace `ValueError` with `FabprintError` for consistent user-facing errors
- Narrow bare `except Exception` to specific types in cloud.py
- Extract magic numbers into named constants (cloud.py, gcode.py)

## 0.1.49 — 2026-03-16

- Add "How is this different from OrcaSlicer CLI?" section to README
- Add asciinema recordings for `init` and `run` commands

## 0.1.48 — 2026-03-15

- Flag Moonraker support as experimental (untested against real hardware)

## 0.1.47 — 2026-03-15

- Add multi-printer-type `status`/`watch` commands
- Refactor printer system: unified `fabprint setup`, multi-printer-type support
  (bambu-lan, bambu-cloud, moonraker)
- Move printer secrets from project TOML to `~/.config/fabprint/credentials.toml`
- Skip file permission assertion on Windows

## 0.1.45 — 2026-03-14

- Improve `init` wizard UX: search filter for long profile lists
- Fix README images and links for PyPI rendering

## 0.1.43 — 2026-03-13

- Add `fabprint init`, `validate`, and interactive wizard commands
- Remove BambuStudio slicer engine support (OrcaSlicer only)

## 0.1.41 — 2026-03-12

- Keep build123d as optional extra (vtk lacks Python 3.13 wheels)
- Add developer docs and simplify README

## 0.1.40 — 2026-03-11

- Replace `plate`/`slice`/`print` commands with Hamilton-driven `run` pipeline
- Make config arg optional — auto-discover `./fabprint.toml`
- Split CLI and config reference into separate docs
- Add slicer.overrides support for per-project process tweaks

## 0.1.37 — 2026-03-09

- Add `fabprint login`, `status`, and `watch` subcommands
- Support multi-object 3MF files with per-object filament assignment
- Add sequential printing support
- Add `gcode-info` subcommand for extruder usage analysis

## 0.1.33 — 2026-03-06

- Add cloud printing via C++ bridge (bambu_cloud_bridge)
- Add X.509 RSA-SHA256 command signing for cloud MQTT
- Add pure Python HTTP cloud print mode (partial — signing limitation)

## 0.1.25 — 2026-02-28

- Add Bambu Connect-compatible `.gcode.3mf` export
- Render isometric plate thumbnails with shading
- Docker as default slicer with local fallback

## 0.1.15 — 2026-02-20

- Add `print` subcommand with LAN and cloud printer support
- Add slicer version pinning and Docker integration
- Preserve paint_color from pre-painted 3MF inputs

## 0.1.5 — 2026-02-10

- Add profile discovery, resolution, and pinning
- Add per-part AMS filament assignment
- Add uniform scale factor for parts

## 0.1.0 — 2026-02-05

- Initial release: core plate generation pipeline
- Load STL/3MF, orient, arrange via bin-packing, export plate 3MF
- OrcaSlicer CLI integration (local + Docker)
- Cross-platform support (macOS, Linux, Windows)
