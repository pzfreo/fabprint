# CLI reference

fabprint uses a single `run` command that executes a pipeline defined in your `fabprint.toml`. Utility commands (`login`, `status`, `watch`, `profiles`) handle printer management.

## `fabprint run`

Run all or part of the pipeline.

```
fabprint run <config> [options]
```

| Option              | Description                                          |
|---------------------|------------------------------------------------------|
| `<config>`          | Path to `fabprint.toml`                              |
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
# Full pipeline: arrange, slice, and print
fabprint run fabprint.toml

# Stop after plating (no slicer needed)
fabprint run fabprint.toml --until plate

# Only slice (requires plate.3mf already in output/)
fabprint run fabprint.toml --only slice

# Slice with a specific Docker image version
fabprint run fabprint.toml --until slice --docker-version 2.3.1

# Dry run — do everything except actually send to printer
fabprint run fabprint.toml --dry-run

# Verbose mode — shows per-stage timing
fabprint run fabprint.toml -v
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
fabprint profiles list [--engine orca|bambu] [--category machine|process|filament]
fabprint profiles pin <config>
```

- **`list`** — show available profiles from your slicer installation.
- **`pin`** — copy the profiles referenced in your config into a local `profiles/` directory. Commit this to git for reproducible builds across machines.
