# `fabprint init` Command — Implementation Plan

## Background

Users need to create `fabprint.toml` config files to use fabprint. Currently they have to
write TOML by hand or copy from examples. This is error-prone and intimidating for new users.
We're building a `fabprint init` command with an interactive wizard, plus a `fabprint validate`
command for checking existing configs.

## Decision Log

- **Option 1 (interactive wizard)**: User's top pick. Lowest barrier for new users.
- **Option 2 (template dump)**: Easy to add as `fabprint init --template`. Fallback for users
  who prefer editing a file directly.
- **Option 3 (CLI flags)**: Rejected — too many flags, painful UX.
- **Option 4 (validate)**: User wants this too. Separate `fabprint validate` subcommand.
- **Slicer engine**: OrcaSlicer only — Bambu Studio support removed from scope.
- **Secrets**: Already handled in PR #82. Printer credentials live in
  `~/.config/fabprint/credentials.toml`, not in project TOML. The wizard should reference
  printers by name, not ask for secrets.

## `fabprint init` — Interactive Wizard

### Flow

1. **Discover OrcaSlicer profiles** using `profiles.discover_profiles("orca")`.
   This returns dicts of `{name: Path}` for categories: machine, process, filament.
   Profiles live in platform-specific system dirs (see `profiles.py:_system_dirs()`).

2. **Pick printer profile** (machine category).
   Show numbered list from discovered profiles. User picks one.
   This sets `[slicer].printer`.

3. **Pick process profile** (process category).
   Same numbered-list UX. Sets `[slicer].process`.

4. **Pick filament(s)** (filament category).
   Allow picking one or more. These populate `[slicer].filaments` list.
   If only one filament, all parts default to slot 1.

5. **Auto-discover CAD files** in current directory.
   Glob for `*.stl`, `*.3mf`, `*.step` files. Show list, let user select which to include.
   For each selected file, ask:
   - Copies (default: 1)
   - Orient: flat/upright/side (default: flat)
   - Filament slot (if multiple filaments configured)

6. **Set plate size** (default: 256x256 for P1S/X1C, could infer from printer profile).

7. **Set slicer version**.
   Auto-detect from installed OrcaSlicer or Docker image tags.
   This is important for reproducibility (see PR #83 — we now warn if missing).

8. **Optionally configure printer connection**.
   Ask if they want to set up printing. If yes:
   - Pick mode: bambu-lan, bambu-connect, cloud-bridge
   - Ask for printer name (references `~/.config/fabprint/credentials.toml`)
   - If credentials file doesn't exist or doesn't have the name, tell them how to create it

9. **Write `fabprint.toml`** to current directory.

### Key Implementation Details

- Use `input()` for prompts. Keep it simple — no curses/rich dependency.
- Numbered lists for selection: `[1] Bambu Lab P1S 0.4 nozzle`
- For multi-select (filaments, files): comma-separated numbers or "all"
- Validate choices as they go (e.g. file exists, profile exists)
- Show a preview of the TOML before writing, ask for confirmation
- If `fabprint.toml` already exists, warn and ask before overwriting

### Relevant Code

- `src/fabprint/profiles.py` — `discover_profiles(engine)` returns available profiles
- `src/fabprint/profiles.py` — `CATEGORIES = ("machine", "process", "filament")`
- `src/fabprint/config.py` — dataclasses define all valid config fields:
  - `PlateConfig`: size, padding
  - `SlicerConfig`: engine, version, printer, process, filaments, slots, overrides
  - `PartConfig`: file, copies, orient, rotate, filament, scale, object_filaments, object, sequence
  - `PrinterConfig`: mode, name (credentials are in separate file now)
- `src/fabprint/cli.py` — add `init` subcommand here, alongside existing plate/slice/print/profiles
- `src/fabprint/slicer.py` — `find_slicer("orca")` finds local install,
  `_docker_image()` / Docker image list can detect installed versions

### OrcaSlicer Version Detection

To auto-detect the slicer version for pinning:
- Local install: run `OrcaSlicer --version` or parse the binary
- Docker: parse `docker image ls` for `fabprint/fabprint:orca-*` tags
  (test_cli.py already has `_docker_orca_version()` that does this)

## `fabprint init --template`

Skip the wizard entirely. Dump a well-commented `fabprint.toml` to stdout (or to file
with `-o`). Contains all sections with sensible defaults and comments explaining each field.

This is a simple string template — no profile discovery needed. Users edit it manually.

## `fabprint validate`

Separate subcommand: `fabprint validate [config]`

1. Load the TOML via `load_config()` — this already validates structure, types, file existence
2. On top of that, add checks with actionable suggestions:
   - Missing `slicer.version` — suggest adding it
   - Missing `slicer.printer` or `slicer.process` — list available profiles
   - Filament names that don't match any installed profile — suggest closest match
   - `[printer]` section references a name not in credentials file — say how to add it
   - Part files that are absolute paths — suggest making them relative
3. Print a summary: "Config OK" or list of warnings/errors with fix suggestions

## Implementation Order

1. **`fabprint init --template`** — simplest, gets something useful shipped fast
2. **`fabprint validate`** — builds on existing `load_config()` error handling
3. **`fabprint init` wizard** — most code, depends on having the profile discovery working well

## Open Questions

- Should `fabprint init` create the credentials file too if the user wants to set up printing?
  Or just tell them the path and format?
- Should the wizard support `slicer.overrides` or keep that as an advanced/manual edit?
- Plate size: hardcode 256x256 default or try to infer from the printer profile JSON?
