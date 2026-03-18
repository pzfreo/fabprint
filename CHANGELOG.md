# Changelog

All notable changes to fabprint are documented here.

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
