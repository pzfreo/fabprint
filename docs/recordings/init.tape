# fabprint full workflow demo
# Usage: cd ~/repos/fabprint && vhs docs/recordings/demo.tape
#
# BEFORE RECORDING:
#   1. Have your Bambu Cloud password ready to type live (masked prompt)
#   2. Have email open to grab the verification code (also typed live)
#   3. Ensure OrcaSlicer is installed locally (or Docker available)
#
# Everything is scripted EXCEPT password and verification code entry.
# Adjust profile search terms below if your printer isn't a P1S.
#
# The interactive picker uses simple-term-menu:
#   Single-select: type to filter, Enter to select highlighted item
#   Multi-select:  / to search, Space to toggle, Enter to confirm

Output docs/recordings/demo.gif

Require fabprint

Set Shell "bash"
Set FontSize 16
Set Width 1400
Set Height 800
Set Padding 20
Set Theme "Catppuccin Mocha"
Set WindowBar Colorful
Set BorderRadius 8
Set TypingSpeed 50ms
Set CursorBlink false

# =====================================================================
# Preparation (hidden)
# =====================================================================
Hide


Type "rm -f ~/repos/decoy-case/fabprint.toml"
Enter
Sleep 500ms

Type "cd ~/repos/decoy-case"
Enter
Sleep 1s

Type "export PATH=$PATH:/usr/local/bin"
Enter
Sleep 500ms

# Pre-warm Docker bridge so AMS query completes during init
Type "fabprint status > /tmp/vhs-status-debug.txt 2>&1"
Enter
Sleep 30s

Type "clear"
Enter
Sleep 500ms

Show


# =====================================================================
# PART 2: fabprint init — interactive config wizard
# =====================================================================

Type "# Step 2: Create a fabprint.toml config"
Enter
Sleep 1s

Type "fabprint init"
Sleep 1s
Enter

Wait+Screen /fabprint init/
Sleep 500ms

# --- Printer Profile (simple-term-menu, single-select) ---
# Printer already configured, skips to profile picker
Wait+Screen /Printer Profile/
Sleep 500ms

# Type to filter, Enter selects highlighted match
Type@100ms "P1S 0.4"
Sleep 500ms
Enter
Sleep 1s

# --- Process Profile (simple-term-menu, single-select) ---
Wait+Screen /Process Profile/
Sleep 500ms

# Type to filter, Enter selects highlighted match
Type "0.20mm Standard @BBL X1C"
Sleep 200ms
Enter
Sleep 5s

# --- Filament (AMS connected — accept matched suggestions) ---
# Accept AMS-matched filaments
Wait+Screen /Use these filaments/
Sleep 1s
Type "y"
Enter

# --- CAD Files (simple-term-menu, multi-select) ---
Wait+Screen /CAD Files/
Sleep 500ms

# Multi-select: Space toggles items, Enter confirms
# Toggle first item, move down, toggle second, confirm
Space
Down
Space
Enter
Sleep 500ms

# First file — copies (accept default 1)
Wait+Screen /copies/
Enter

# First file — orient (accept default "flat")
Wait+Screen /orient/
Enter

# First file — filament slot (pick slot 3)
Wait+Screen /filament slot/
Type "3"
Enter

# Second file — copies (accept default 1)
Wait+Screen /copies/
Enter

# Second file — orient (accept default "flat")
Wait+Screen /orient/
Enter

# Second file — filament slot (pick slot 3)
Wait+Screen /filament slot/
Type "3"
Enter

# --- Slicer Overrides (now after CAD files) ---
Wait+Screen /Slicer Overrides/
Sleep 300ms

# Yes, add overrides
Type "y"
Enter

# Pick infill density (option 1)
Wait+Screen /Pick override/
Type "1"
Enter

# Enter infill value (percent type — "15" auto-becomes "15%")
Wait+Screen /Value for/
Type "30"
Enter

# Don't add another override
Wait+Screen /Add another override/
Enter

# --- Build Plate ---
Wait+Screen /Build Plate/
Sleep 300ms

# Accept detected plate width
Wait+Screen /Plate width/
Enter

# Accept detected plate depth
Wait+Screen /Plate depth/
Enter

# --- Slicer Version ---
Wait+Screen /Slicer Version/
Sleep 500ms

# Pick first version from DockerHub list (or accept default)
Wait+Screen /Pick version|version to pin/
Type "1"
Enter

# --- Printer Connection (simple-term-menu, single-select) ---
Wait+Screen /Printer Connection/
Sleep 500ms

# Select "workshop" (first option) — Enter picks highlighted
Enter
Sleep 500ms

# --- Project Name ---
Wait+Screen /Project name/
Sleep 300ms

# Accept default "decoy-case"
Enter

# --- Preview and write ---
Wait+Screen /Write.*Go back.*Quit/

# "w" to write
Type "w"
Enter

Wait+Screen /Wrote fabprint.toml/
Sleep 2s

Type "clear"
Enter
Sleep 500ms

# =====================================================================
# PART 3: fabprint validate
# =====================================================================

Type "# Step 3: Validate the config"
Enter
Sleep 1s

Type "fabprint validate"
Enter

Wait+Screen /Validating/
Sleep 500ms

Wait+Screen /checks passed|warning/
Sleep 3s

Type "clear"
Enter
Sleep 500ms

# =====================================================================
# PART 4: fabprint run — full pipeline including print
# =====================================================================

Type "# Step 4: Run the full pipeline"
Enter
Sleep 1s

Type "fabprint run"
Enter

Wait+Screen /Loaded.*part/
Sleep 500ms

Wait+Screen /Arranged.*part/
Sleep 500ms

Wait+Screen /Plate exported/
Sleep 500ms

# Slicing can take 30-60s
Wait+Screen /Sliced/
Sleep 1s

Wait+Screen /Print time|filament/
Sleep 1s

Wait+Screen /Sent to printer/
Sleep 3s

# Hold final frame
Sleep 3s
