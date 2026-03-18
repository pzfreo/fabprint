# Configuration reference

fabprint is configured with a single TOML file (typically `fabprint.toml`). This page documents every section and field.

## Full example

```toml
name = "benchy"

[pipeline]
stages = ["load", "arrange", "plate", "slice", "print"]

[printer]
name = "workshop"

[plate]
size = [256, 256]
padding = 5.0

[slicer]
engine = "orca"
version = "2.3.1"
printer = "Bambu Lab P1S 0.4 nozzle"
process = "0.20mm Standard @BBL X1C"

[slicer.overrides]
enable_support = 1
curr_bed_type = "Textured PEI Plate"

[[parts]]
file = "frame.stl"
copies = 1
rotate = [180, 0, 0]
filament = "Generic PETG-CF @base"

[[parts]]
file = "wheel.stl"
copies = 5
orient = "upright"
filament = "Generic PETG-CF @base"
```

## `name`

Optional project name. When set, all output filenames are prefixed with this name (e.g. `benchy-plate.3mf`, `benchy-plate_preview.3mf`, `benchy-plate.gcode`).

| Key    | Type     | Default | Description                     |
|--------|----------|---------|---------------------------------|
| `name` | `string` | —       | Project name prefix for outputs |

```toml
name = "benchy"
```

## `[pipeline]`

Controls which stages run and in what order. Optional — defaults to the full pipeline.

| Key      | Type       | Default                                           | Description                |
|----------|------------|---------------------------------------------------|----------------------------|
| `stages` | `[string]` | `["load", "arrange", "plate", "slice", "print"]`  | Ordered list of stages     |

Valid stage names: `load`, `arrange`, `plate`, `slice`, `gcode-info`, `print`.

If your workflow doesn't need printing, omit `print`:

```toml
[pipeline]
stages = ["load", "arrange", "plate", "slice"]
```

## `[printer]`

Defines which printer to send gcode to. Optional — omit if you only need to plate and slice.

| Key    | Type     | Default | Description                                           |
|--------|----------|---------|-------------------------------------------------------|
| `name` | `string` | —       | Printer name in `~/.config/fabprint/credentials.toml` |

The `name` field references a printer configured via `fabprint setup`. All connection details (type, IP, credentials) are stored in `credentials.toml`, not in the project config.

### Credentials file

Run `fabprint setup` to create `~/.config/fabprint/credentials.toml`. It stores printer connection details and optional cloud login:

```toml
# ~/.config/fabprint/credentials.toml

[cloud]
token = "..."
refresh_token = "..."
email = "user@example.com"
uid = "12345"

[printers.workshop]
type = "bambu-lan"
ip = "192.168.1.100"
access_code = "12345678"
serial = "01P00A451601106"

[printers.p1s-cloud]
type = "bambu-cloud"
serial = "01P00A451601106"

[printers.voron]
type = "moonraker"
url = "http://voron.local:7125"
```

### Printer types

| Type          | Required fields              | Description                          |
|---------------|------------------------------|--------------------------------------|
| `bambu-lan`   | ip, access_code, serial      | Direct LAN connection (fastest)      |
| `bambu-cloud` | serial                       | Cloud bridge (requires `[cloud]` login) |
| `moonraker`   | url (+ optional api_key)     | Klipper/Moonraker REST API           |

**Environment variable overrides** (take precedence over credentials.toml):

| Env var              | Overrides      |
|----------------------|----------------|
| `BAMBU_PRINTER_IP`   | `ip`           |
| `BAMBU_ACCESS_CODE`  | `access_code`  |
| `BAMBU_SERIAL`       | `serial`       |

## `[plate]`

Build plate dimensions for bin-packing.

| Key       | Type     | Default    | Description              |
|-----------|----------|------------|--------------------------|
| `size`    | `[w, h]` | `[256, 256]` | Build plate size in mm |
| `padding` | `float`  | `5.0`      | Gap between parts in mm  |

## `[slicer]`

Slicer engine and profile selection.

| Key         | Type       | Default  | Description                                                |
|-------------|------------|----------|------------------------------------------------------------|
| `engine`    | `string`   | `"orca"` | Slicer engine (`"orca"`)                                   |
| `version`   | `string`   | —        | Required OrcaSlicer version (e.g. `"2.3.1"`)               |
| `printer`   | `string`   | —        | Printer profile name                                       |
| `process`   | `string`   | —        | Process profile name                                       |
| `filaments` | `[string]` | —        | Filament profiles (auto-derived from parts if omitted)     |

### `[slicer.slots]`

Explicit AMS slot-to-filament mapping:

```toml
[slicer.slots]
1 = "Generic PLA @base"
3 = "Generic PETG-CF @base"
5 = "Generic TPU @base"        # direct feed (bypass AMS)
```

Parts can reference slots by number (`filament = 3`) or by name (`filament = "Generic PLA @base"`).

### `[slicer.overrides]`

Key-value pairs applied on top of the process profile:

```toml
[slicer.overrides]
enable_support = 1
wall_loops = 4
curr_bed_type = "Textured PEI Plate"
```

Common bed types: `"Cool Plate"`, `"Engineering Plate"`, `"High Temp Plate"`, `"Textured PEI Plate"`.

## `[[parts]]`

Each `[[parts]]` entry defines a mesh to include on the build plate. At least one is required.

| Key        | Type          | Default  | Description                                          |
|------------|---------------|----------|------------------------------------------------------|
| `file`     | `string`      | —        | Path to mesh file (STL, 3MF, or STEP)               |
| `copies`   | `int`         | `1`      | Number of copies                                     |
| `orient`   | `string`      | `"flat"` | `"flat"`, `"upright"`, or `"side"`                   |
| `rotate`   | `[x, y, z]`   | —        | Custom rotation in degrees (overrides `orient`)      |
| `filament` | `int\|string` | `1`      | Filament profile name or slot index                  |
| `scale`    | `float`       | `1.0`    | Uniform scale factor                                 |
| `object`   | `string`      | —        | Select a named object from a multi-object 3MF        |
| `sequence` | `int`         | `1`      | Print order for sequential printing                  |

### Per-object filament overrides

For multi-object 3MF files, assign different filaments to individual objects:

```toml
[[parts]]
file = "widget.3mf"
filament = "Generic PETG-CF @base"       # default for unlisted objects

[parts.filaments]
inlay = "Bambu PLA Basic @BBL X1C"       # override for object named "inlay"
```

Objects from the same file are grouped as a single unit for bin packing.

### Sequential printing

For workflows that require printing one layer/object before another (e.g. bottom inlay):

```toml
[[parts]]
file = "widget.3mf"
object = "inlay"
filament = "Generic PLA @base"
sequence = 1

[[parts]]
file = "widget.3mf"
object = "body"
filament = "Generic PETG-CF @base"
sequence = 2
```

Both objects come from the same 3MF, so fabprint guarantees identical bed positioning. Run each sequence separately:

```bash
fabprint run fabprint.toml --only print   # after slicing sequence 1
```
