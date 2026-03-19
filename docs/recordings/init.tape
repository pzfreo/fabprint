# fabprint init + validate + run demo
# Usage: cd ~/repos/fabprint && vhs docs/recordings/init.tape
#
# BEFORE RECORDING:
#   1. Ensure credentials are configured (fabprint setup already done)
#   2. Ensure OrcaSlicer is installed locally (or Docker available)
#   3. Printer must be online for AMS detection
#
# The interactive picker uses simple-term-menu:
#   Single-select: type to filter, Enter to select highlighted item
#   Multi-select:  / to search, Space to toggle, Enter to confirm

Output docs/recordings/demo.gif

Require fabprint

Set Shell "bash"
Set FontSize 16
Set Width 1400
Set Height 1200
Set Padding 20
Set Theme "Catppuccin Mocha"
Set WindowBar Colorful
Set BorderRadius 8
Set TypingSpeed 50ms
Set CursorBlink false
Set WaitTimeout 120s

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
Type "export FABPRINT_SKIP_SLICER_DETECT=1"
Enter
Sleep 500ms

# Pre-warm Docker bridge so AMS query completes during init
Type "fabprint status > /tmp/vhs-status-debug.txt 2>&1 && echo WARM"
Enter
Wait+Screen /WARM/
Sleep 500ms

Type "clear"
Enter
Sleep 500ms

Show

# =====================================================================
# fabprint init — interactive config wizard
# =====================================================================

Type "# Create a fabprint.toml config"
Enter
Sleep 1s

Type "fabprint init"
Sleep 1s
Enter

Wait+Screen /fabprint init/
Sleep 500ms

# --- Step 1: Project Name ---
Wait+Screen /Project name/
Sleep 300ms
Enter

# --- Step 2: CAD Files (simple-term-menu, multi-select) ---
Wait+Screen /CAD Files/
Sleep 500ms

# Multi-select: Space toggles items, Enter confirms
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

# Second file — copies (accept default 1)
Wait+Screen /copies/
Enter

# Second file — orient (accept default "flat")
Wait+Screen /orient/
Enter

# --- Step 3: Printer Connection (simple-term-menu, single-select) ---
Wait+Screen /Printer Connection/
Sleep 500ms

# Select "workshop" (first option)
Enter
Sleep 500ms

# --- Step 4: Printer Profile (simple-term-menu, single-select) ---
Wait+Screen /Printer Profile/
Sleep 500ms

Type@100ms "P1S 0.4"
Sleep 500ms
Enter
Sleep 1s

# --- Process Profile (simple-term-menu, single-select) ---
Wait+Screen /Process Profile/
Sleep 500ms

Type "0.20mm Standard @BBL X1C"
Sleep 200ms
Enter
Sleep 1s

# --- Plate size (auto-detected from printer profile) ---
Wait+Screen /Build Plate/
Sleep 300ms

Wait+Screen /Plate width/
Enter

Wait+Screen /Plate depth/
Sleep 500ms
Enter
Sleep 3s

# --- Step 5: Slicer Version ---
# Pick first version (loaded from bundled JSON, no network call)
Type "1"
Enter
Sleep 2s

# --- Step 6: Filaments (AMS connected — accept matched suggestions) ---
Wait+Screen /Use these filaments/
Sleep 1s
Type "y"
Enter
Sleep 1s

# --- Step 7: Filament Assignment (pick slot 3 for both parts) ---
Wait+Screen /Filament Assignment/
Sleep 500ms

Wait+Screen /filament slot/
Type "3"
Enter

Wait+Screen /filament slot/
Type "3"
Enter
Sleep 1s

# --- Step 8: Slicer Overrides ---
Wait+Screen /Slicer Overrides/
Sleep 300ms

# Yes, add overrides
Type "y"
Enter
Sleep 2s

# Pick infill density (option 1)
Type "1"
Enter
Sleep 1s

# Enter infill value (percent type — "30" auto-becomes "30%")
Type "30"
Enter
Sleep 1s

# Don't add another override
Wait+Screen /Add another override/
Enter
Sleep 2s

# --- Preview and write ---
Type "w"
Enter

Sleep 5s

Type "clear"
Enter
Sleep 500ms

# =====================================================================
# fabprint validate
# =====================================================================

Type "# Validate the config"
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
# fabprint run — full pipeline including print
# =====================================================================

Type "# Run the full pipeline"
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
