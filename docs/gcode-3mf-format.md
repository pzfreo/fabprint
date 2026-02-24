# The .gcode.3mf Format: Making Bambu Connect-Compatible Files from OrcaSlicer CLI

Bambu Connect is Bambu Lab's official middleware for sending sliced files to
printers from third-party tools. It accepts `.gcode.3mf` files -- ZIP archives
containing gcode and metadata produced by BambuStudio or OrcaSlicer.

This document covers what we learned getting OrcaSlicer's CLI to produce files
that Bambu Connect actually accepts. It was hard-won through extensive trial and
error, since Bambu Connect silently rejects malformed files with no error
message.

## Background: Two Different 3MF Export Formats

OrcaSlicer has two fundamentally different 3MF export modes:

| | **Project Export** | **Plate Sliced Export** |
|---|---|---|
| **GUI action** | File > Save Project | File > Export plate sliced file (Ctrl+G) |
| **Contains** | 3D models + mesh data + settings | Gcode + metadata only |
| **3dmodel.model** | Full mesh vertices/triangles | Empty `<resources/>` and `<build/>` |
| **model_settings.config** | Full object metadata (10KB+) | Simple plate config (~700 bytes) |
| **project_settings.config** | Full slicer settings | Full slicer settings |
| **File size** | Large (includes geometry) | Small (gcode + metadata) |
| **Bambu Connect** | Rejected | Accepted |

Bambu Connect only accepts the **plate sliced** format. The project format is
for re-opening in the slicer, not for printing.

## The CLI Flags You Need

```bash
orca-slicer \
  --load-settings "machine.json;process.json" \
  --load-filaments "filament_0.json;filament_1.json" \
  --slice 0 \
  --export-3mf plate_sliced.gcode.3mf \
  --min-save \
  --outputdir ./output \
  input.3mf
```

The two critical flags:

- **`--export-3mf <filename>`** -- export a 3mf archive alongside the gcode
- **`--min-save`** -- produce the "plate sliced" format (gcode-only, no 3D
  models). Without this flag, you get the project format that Bambu Connect
  rejects.

### Gotchas

- **`--min-save` takes no argument.** Writing `--min-save 1` fails with
  "No such file: 1" because OrcaSlicer treats `1` as an input filename.
  It's a standalone boolean flag.

- **`--export-3mf` must use a relative filename.** An absolute path like
  `--export-3mf /path/to/output/plate.3mf` gets prepended with `--outputdir`,
  producing a doubled path like `/path/to/output//path/to/output/plate.3mf`.
  Use just the filename: `--export-3mf plate_sliced.gcode.3mf`.

- **Use `.gcode.3mf` extension**, not `.3mf`. Bambu Connect uses the extension
  to distinguish sliced files from project files.

- **Shader errors in headless mode are harmless.** You'll see errors like
  "Unable to compile fragment shader" and "can not get shader for rendering
  thumbnail" when running without a display. Slicing still works; you just
  don't get thumbnails.

## Post-Processing: Three Fixes Required

The `--min-save` output is *almost* right, but Bambu Connect rejects it due to
three issues in the metadata. You need to patch the ZIP archive after slicing.

### Fix 1: project_settings.config -- Missing Keys and Short Arrays

`Metadata/project_settings.config` is a JSON file with ~530+ slicer setting
keys. The CLI export has two problems:

**Missing keys.** These 11 keys are absent from the CLI export but required by
Bambu Connect:

```json
{
  "bbl_use_printhost": "1",
  "default_bed_type": "",
  "filament_retract_lift_above": ["0"],
  "filament_retract_lift_below": ["0"],
  "filament_retract_lift_enforce": [""],
  "host_type": "octoprint",
  "pellet_flow_coefficient": "0",
  "pellet_modded_printer": "0",
  "printhost_authorization_type": "key",
  "printhost_ssl_ignore_revoke": "0",
  "thumbnails_format": "BTT_TFT"
}
```

**Short filament arrays.** The CLI sizes arrays to match the number of loaded
filaments (e.g. 3 elements if you loaded 3 filaments). Bambu Connect expects
arrays padded to the AMS slot count -- **5 for a P1S** (4 AMS slots + 1
external spool). Pad by repeating the last element.

For example, `filament_type` might be `["PLA", "PLA", "PETG-CF"]` from the CLI
but needs to be `["PLA", "PLA", "PETG-CF", "PETG-CF", "PETG-CF"]`.

### Fix 2: model_settings.config -- Missing Metadata Keys

`Metadata/model_settings.config` is an XML file describing the plate. The CLI
export is missing 5 metadata entries that Bambu Connect requires:

```xml
<metadata key="thumbnail_file" value="Metadata/plate_1.png"/>
<metadata key="thumbnail_no_light_file" value="Metadata/plate_no_light_1.png"/>
<metadata key="top_file" value="Metadata/top_1.png"/>
<metadata key="pick_file" value="Metadata/pick_1.png"/>
<metadata key="pattern_bbox_file" value="Metadata/plate_1.json"/>
```

These references must be present even if the actual PNG files don't exist in the
archive. Bambu Connect checks for the XML entries but doesn't validate that the
referenced files are present.

Additionally, the `filament_maps` value needs padding, same as in
project_settings: `"1"` should become `"1 1 1 1 1"` (space-separated, one per
AMS slot).

### Fix 3: Thumbnails (Optional but Recommended)

The CLI can't render thumbnails in headless mode. Without them, Bambu Connect
shows a blank/broken image. Adding placeholder PNGs at `Metadata/plate_1.png`
and `Metadata/plate_1_small.png` gives a cleaner appearance.

## Complete Archive Structure

A valid `.gcode.3mf` for Bambu Connect contains:

```
plate_sliced.gcode.3mf
  [Content_Types].xml              -- Standard OPC content types
  _rels/.rels                      -- Relationship to 3dmodel.model
  3D/3dmodel.model                 -- Empty model (no mesh data)
  Metadata/plate_1.gcode           -- The actual gcode
  Metadata/plate_1.gcode.md5       -- MD5 hex digest of gcode
  Metadata/model_settings.config   -- Plate config XML (with thumbnail refs)
  Metadata/_rels/model_settings.config.rels  -- Links gcode to plate
  Metadata/slice_info.config       -- Print time, weight, filament info
  Metadata/project_settings.config -- Full slicer settings JSON (~500+ keys)
  Metadata/plate_1.json            -- Plate bounding box / layout data
  Metadata/plate_1.png             -- Thumbnail (optional, but refs required)
  Metadata/plate_1_small.png       -- Small thumbnail (optional)
```

### What Doesn't Matter

Through testing, we confirmed these are **not** required:

- **Actual thumbnail PNG files** -- the XML references are required but the
  files themselves are optional
- **`slice_info.config` accuracy** -- empty `printer_model_id`, short
  `filament_maps`, different prediction/weight values are all accepted
- **`_rels/.rels` dangling references** -- referencing thumbnails that don't
  exist in the archive is fine
- **`[Content_Types].xml`** -- the CLI version works fine
- **`3D/3dmodel.model`** -- any empty model works (CLI and GUI produce
  identical files)

### What Matters

- **`project_settings.config`** must have all expected keys and arrays padded
  to AMS slot count
- **`model_settings.config`** must have thumbnail/bbox metadata references and
  padded `filament_maps`
- **File extension** must be `.gcode.3mf`
- **`--min-save` flag** must be used (no 3D model data)

## Example Post-Processing Script

Here's a minimal Python script to patch a `--min-save` export:

```python
import io
import json
import re
import zipfile

MISSING_KEYS = {
    "bbl_use_printhost": "1",
    "default_bed_type": "",
    "filament_retract_lift_above": ["0"],
    "filament_retract_lift_below": ["0"],
    "filament_retract_lift_enforce": [""],
    "host_type": "octoprint",
    "pellet_flow_coefficient": "0",
    "pellet_modded_printer": "0",
    "printhost_authorization_type": "key",
    "printhost_ssl_ignore_revoke": "0",
    "thumbnails_format": "BTT_TFT",
}

MIN_SLOTS = 5  # P1S with AMS


def fix_gcode_3mf(path: str) -> None:
    with zipfile.ZipFile(path, "r") as zin:
        # Fix project_settings.config
        ps = json.loads(zin.read("Metadata/project_settings.config"))
        for key, default in MISSING_KEYS.items():
            if key not in ps:
                ps[key] = default
        for key, val in ps.items():
            if isinstance(val, list) and 0 < len(val) < MIN_SLOTS:
                while len(val) < MIN_SLOTS:
                    val.append(val[-1])

        # Fix model_settings.config
        ms = zin.read("Metadata/model_settings.config").decode()

        # Pad filament_maps
        def pad_maps(m):
            parts = m.group(1).split()
            while len(parts) < MIN_SLOTS:
                parts.append(parts[-1] if parts else "1")
            return f'key="filament_maps" value="{" ".join(parts)}"'

        ms = re.sub(r'key="filament_maps" value="([^"]*)"', pad_maps, ms)

        # Add missing metadata keys
        for key, val in {
            "thumbnail_file": "Metadata/plate_1.png",
            "thumbnail_no_light_file": "Metadata/plate_no_light_1.png",
            "top_file": "Metadata/top_1.png",
            "pick_file": "Metadata/pick_1.png",
            "pattern_bbox_file": "Metadata/plate_1.json",
        }.items():
            if f'key="{key}"' not in ms:
                ms = ms.replace(
                    "  </plate>",
                    f'    <metadata key="{key}" value="{val}"/>\n  </plate>',
                )

        # Rewrite the archive
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                if item.filename == "Metadata/project_settings.config":
                    zout.writestr(item, json.dumps(ps, indent=4))
                elif item.filename == "Metadata/model_settings.config":
                    zout.writestr(item, ms)
                else:
                    zout.writestr(item, zin.read(item.filename))

    with open(path, "wb") as f:
        f.write(buf.getvalue())
```

## Opening in Bambu Connect Programmatically

On macOS, use the `bambu-connect://` URL scheme:

```bash
open "bambu-connect://import-file?path=%2Fpath%2Fto%2Fplate_sliced.gcode.3mf&name=my_print&version=1.0.0"
```

Parameters (all URL-encoded):
- `path` -- absolute filesystem path to the `.gcode.3mf`
- `name` -- display name for the print
- `version` -- fixed value `1.0.0`

## References

- [BambuStudio CLI issue #2930](https://github.com/bambulab/BambuStudio/issues/2930) --
  documents the `--min-save` flag
- [Bambu Connect Wiki](https://wiki.bambulab.com/en/software/bambu-connect) --
  official Bambu Connect documentation
- [Third-party Integration](https://wiki.bambulab.com/en/software/third-party-integration) --
  Bambu Lab's third-party integration docs
- [Bambu Connect file format error](https://forum.bambulab.com/t/bambu-connect-file-format-error/143571) --
  community discussion confirming sliced format requirement
