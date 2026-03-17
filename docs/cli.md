# CLI reference

fabprint provides commands for creating configs (`init`, `validate`), setting up printers (`setup`, `login`), running the pipeline (`run`), and managing printers (`status`, `watch`, `profiles`).

## `fabprint init`

Create a new `fabprint.toml` config file.

```
fabprint init [--template] [-o OUTPUT]
```

| Option        | Description                                         |
|---------------|-----------------------------------------------------|
| `--template`  | Dump a commented template to stdout (skip wizard)   |
| `-o, --output`| Output file path (default: `./fabprint.toml`)       |

Without `--template`, runs an interactive wizard that:
1. Discovers installed OrcaSlicer profiles (printer, process, filament)
2. Auto-discovers CAD files (STL, 3MF, STEP) in the current directory
3. Walks through plate size, slicer version, pipeline stages, and printer setup
4. Previews the generated TOML before writing

### Examples

```bash
fabprint init                              # interactive wizard
fabprint init --template                   # print commented template
fabprint init --template > fabprint.toml   # save template to file
fabprint init -o myproject.toml            # wizard writes to custom path
```

## `fabprint validate`

Check a `fabprint.toml` for issues and print actionable warnings.

```
fabprint validate [config]
```

If `config` is omitted, looks for `fabprint.toml` in the current directory.

Checks for:
- Missing `slicer.version` (reproducibility)
- Profile names not matching installed slicer profiles (with suggestions)
- Printer name not found in credentials file
- Absolute part file paths (portability)
- Unknown pipeline stages

### Examples

```bash
fabprint validate                  # check ./fabprint.toml
fabprint validate myproject.toml   # check a specific file
```

## `fabprint setup`

Interactively set up a printer in `~/.config/fabprint/credentials.toml`.

```
fabprint setup
```

Walks through:
1. **Printer name** — used to reference this printer in `fabprint.toml` (e.g. `name = "workshop"`)
2. **Printer type** — `bambu-lan` (direct LAN), `bambu-cloud` (cloud bridge), or `moonraker` (Klipper)
3. **Type-specific fields** — IP/access code/serial for Bambu LAN, serial for Bambu Cloud, URL for Moonraker
4. **Cloud login** — for `bambu-cloud` type, optionally logs in to Bambu Cloud

The credentials file is created with `600` permissions (owner read/write only). If the file already exists, new printers are added alongside existing ones.

### Supported printer types

| Type          | Required fields              | Description                          |
|---------------|------------------------------|--------------------------------------|
| `bambu-lan`   | ip, access_code, serial      | Direct LAN connection to Bambu Lab   |
| `bambu-cloud` | serial                       | Cloud bridge (requires cloud login)  |
| `moonraker`   | url (+ optional api_key)     | Klipper/Moonraker REST API           |

### Example session

```
$ fabprint setup
Printer name (e.g. 'workshop'): workshop

Printer types:
  [1] bambu-lan — Bambu Lab printer via LAN (direct connection)
  [2] bambu-cloud — Bambu Lab printer via cloud (requires cloud login)
  [3] moonraker — Klipper/Moonraker printer via REST API
Choose type [1]: 1

Setting up 'workshop' (bambu-lan)
  ip: 192.168.1.100
  access_code: 12345678
  serial: 01P00A451601106

Wrote ~/.config/fabprint/credentials.toml (mode 600)
Reference this printer in fabprint.toml with:
  [printer]
  name = "workshop"
```

## `fabprint run`

Run all or part of the pipeline.

```
fabprint run [config] [options]
```

If `config` is omitted, fabprint looks for `fabprint.toml` in the current directory.

| Option              | Description                                          |
|---------------------|------------------------------------------------------|
| `[config]`          | Path to config file (default: `./fabprint.toml`)     |
| `-o, --output-dir`  | Output directory (default: `output/`)                |
| `--until STAGE`     | Run pipeline up to and including this stage           |
| `--only STAGE`      | Run only this stage (fails if prerequisites missing)  |
| `--scale FACTOR`    | Scale all parts (multiplies per-part scale)           |
| `--local`           | Force local slicer (fail if not installed)            |
| `--docker-version`  | Pin OrcaSlicer Docker image version (e.g. `2.3.1`)   |
| `--filament-type`   | Override filament profile name                        |
| `--filament-slot`   | AMS slot for `--filament-type` (default: 1)           |
| `--dry-run`         | Do everything except send to printer                  |
| `--upload-only`     | Upload gcode but don't start printing                 |
| `--experimental`    | Enable experimental printer modes                     |
| `--no-ams-mapping`  | Skip AMS mapping (diagnostic)                         |
| `-v, --verbose`     | Enable debug logging with per-stage timing            |

### Pipeline stages

The default pipeline runs these stages in order:

| Stage       | What it does                                      | Output                    |
|-------------|---------------------------------------------------|---------------------------|
| `load`      | Load meshes, apply orientation and scaling         | Part summary              |
| `arrange`   | Bin-pack parts onto the build plate                | Placements                |
| `plate`     | Export arranged plate as 3MF (+ preview)           | `plate.3mf`, `plate_preview.3mf` |
| `slice`     | Slice via OrcaSlicer (Docker or local)             | gcode in output dir       |
| `gcode-info`| Parse print time and filament usage from gcode     | Stats summary             |
| `print`     | Send sliced gcode to printer                       | Print job                 |

### Examples

```bash
# Full pipeline: arrange, slice, and print (uses ./fabprint.toml)
fabprint run

# Stop after plating (no slicer needed)
fabprint run --until plate

# Only slice (requires plate.3mf already in output/)
fabprint run --only slice

# Slice with a specific Docker image version
fabprint run --until slice --docker-version 2.3.1

# Dry run — do everything except actually send to printer
fabprint run --dry-run

# Verbose mode — shows per-stage timing
fabprint run -v

# Explicit config path
fabprint run myproject.toml --until plate
```

### `--until` vs `--only`

- **`--until plate`** runs `load -> arrange -> plate`, computing everything from scratch.
- **`--only slice`** runs *just* the slice stage. It expects `output/plate.3mf` to already exist on disk (e.g. from a previous `--until plate` run). Fails with an error if the prerequisite is missing.

You cannot combine `--until` and `--only`.

## `fabprint login`

Log in to Bambu Cloud and cache your authentication token.

```
fabprint login [--email EMAIL] [--password PASSWORD]
```

If email/password are omitted, prompts interactively.

## `fabprint status`

Query printer status via the cloud API.

```
fabprint status [--serial SERIAL]
```

Without `--serial`, shows all printers on your account.

## `fabprint watch`

Live dashboard for all bound printers. Refreshes automatically.

```
fabprint watch [--interval SECONDS]
```

Default refresh interval is 10 seconds.

## `fabprint profiles`

Manage slicer profiles.

```
fabprint profiles list [--category machine|process|filament]
fabprint profiles pin [config]
```

- **`list`** — show available profiles from your slicer installation.
- **`pin`** — copy the profiles referenced in your config into a local `profiles/` directory. Commit this to git for reproducible builds across machines.
