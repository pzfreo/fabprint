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

Type "rm -f ~/.config/fabprint/credentials.toml"
Enter
Sleep 500ms

Type "rm -f ~/repos/decoy-case/fabprint.toml"
Enter
Sleep 500ms

Type "cd ~/repos/decoy-case && python decoy_case.py 2>/dev/null"
Enter
Sleep 8s

Type "clear"
Enter
Sleep 500ms

Show

# =====================================================================
# PART 1: fabprint setup — cloud printer
# =====================================================================

Type "# Step 1: Configure a cloud printer"
Enter
Sleep 1s

Type "fabprint setup"
Enter

# Printer name — accept default "workshop"
Wait+Screen /Printer name/
Enter

# Choose printer type — 2 = bambu-cloud
Wait+Screen /Choose type/
Type "2"
Enter

# Confirm cloud login
Wait+Screen /Log in now/
Type "y"
Enter

# Type email
Wait+Screen /Email/
Type "paul@fremantle.org"
Enter

# Password — copy it to clipboard before running, VHS pastes it
Wait+Screen /Password/
Sleep 5s
Paste
Enter

# Verification code — hide the wait, show a brief pause
Wait+Screen /Verification code sent/
Hide
Sleep 45s
Show
Sleep 2s
Paste
Enter

# Wait for login to succeed
Wait+Screen /Login successful/
Sleep 1s

# Pick printer from the list — select #1
Wait+Screen /Pick a printer/
Type "1"
Enter

Wait+Screen /Selected:/
Sleep 1s

Wait+Screen /Wrote.*credentials/
Sleep 2s

Type "clear"
Enter
Sleep 500ms

# =====================================================================
# PART 2: fabprint init — interactive config wizard
# =====================================================================

Type "# Step 2: Create a fabprint.toml config"
Enter
Sleep 1s

Type "fabprint init"
Enter

Wait+Screen /fabprint init/
Sleep 500ms

# --- Printer Profile (live search picker) ---
# Printer already configured, skips to profile picker
Wait+Screen /Printer Profile/
Sleep 500ms

# Search for P1S 0.4 nozzle — unique match, Enter auto-selects
Type@100ms "P1S 0.4"
Sleep 1s
Enter
Sleep 1s

# --- Process Profile (live search picker) ---
Wait+Screen /Process Profile/
Sleep 500ms

# Search for process profile — unique match, Enter auto-selects
Type@100ms "0.20mm Standard "
Sleep 200ms
Type@100ms "@BBL X1C"
Sleep 1s
Enter
Sleep 1s

# --- Slicer Overrides ---
Wait+Screen /Slicer Overrides/
Sleep 300ms

# Yes, add overrides
Type "y"
Enter

# Pick infill density (option 1)
Wait+Screen /Pick override/
Type "1"
Enter

# Enter infill value
Wait+Screen /Value for/
Type "15%"
Enter

# Don't add another override
Wait+Screen /Add another override/
Enter

# --- Filament (AMS connected — accept matched suggestions) ---
Wait+Screen /Filament Profiles/
Sleep 1s

# Accept AMS-matched filaments
Wait+Screen /Use these filaments/
Enter

# --- CAD Files ---
Wait+Screen /CAD Files/
Sleep 500ms

# Select all files (should find decoy_base.step + decoy_shell.step)
Type@100ms "all"
Sleep 300ms
Enter

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

# --- Printer Connection (live picker) ---
Wait+Screen /Printer Connection/
Sleep 500ms

# Select "workshop" (first option)
Type "1"
Sleep 300ms
Enter

# --- Project Name ---
Wait+Screen /Project name/
Sleep 300ms

# Accept default "decoy-case"
Enter

# --- Preview and write ---
Wait+Screen /Write to/

Type "y"
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
