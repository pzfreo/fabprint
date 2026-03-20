#!/usr/bin/env python3
"""Record full fabprint demo: setup → init → validate → run → status.

Usage:
    cd ~/repos/fabprint
    python scripts/record_demo.py

    # Convert to GIF:
    agg --font-size 20 docs/recordings/demo.cast docs/recordings/demo.gif

Interactive steps:
    - Password: prompted before recording starts (via getpass)
    - Verification code: pause to check email + copy code to clipboard

The script post-processes the .cast file to compress idle gaps,
hiding the verification code wait in the final recording.

Requires: pexpect, asciinema, agg (brew install agg)
"""

from __future__ import annotations

import getpass
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pexpect

CAST_FILE = Path(__file__).parent.parent / "docs" / "recordings" / "demo.cast"
DEMO_DIR = Path.home() / "repos" / "decoy-case"
TYPING_DELAY = 0.08
EMAIL = "paul@fremantle.org"

# Max idle gap in the final recording (seconds)
MAX_IDLE = 2.0

# Escape sequences
DOWN = "\x1b[B"


def type_slowly(child: pexpect.spawn, text: str, delay: float = TYPING_DELAY) -> None:
    """Type text character by character with a delay."""
    for ch in text:
        child.send(ch)
        time.sleep(delay)


def type_comment(child: pexpect.spawn, text: str) -> None:
    """Type a bash comment, pause to let viewer read it."""
    type_slowly(child, text)
    time.sleep(0.5)
    child.send("\r")
    time.sleep(1)


def type_command(child: pexpect.spawn, text: str) -> None:
    """Type a command and press Enter."""
    type_slowly(child, text)
    time.sleep(0.5)
    child.send("\r")


def expect(child: pexpect.spawn, pattern: str, timeout: int = 60) -> None:
    """Wait for pattern, printing what we're waiting for on failure."""
    try:
        child.expect(pattern, timeout=timeout)
    except pexpect.TIMEOUT:
        print(f"\nTIMEOUT waiting for: {pattern}", file=sys.stderr)
        if child.before:
            import re

            clean = re.sub(r"\x1b\[[^m]*m|\x1b\([^)]*\)", "", child.before)
            print(f"Buffer (last 300 chars): {clean[-300:]}", file=sys.stderr)
        raise


def read_clipboard() -> str:
    """Read text from the system clipboard (macOS)."""
    return subprocess.check_output(["pbpaste"], text=True).strip()


def compress_idle(cast_path: Path, max_idle: float = MAX_IDLE) -> None:
    """Compress idle gaps in a v3 asciicast file.

    Rewrites timestamps so no gap between events exceeds max_idle seconds.
    """
    lines = cast_path.read_text().splitlines()
    if not lines:
        return

    header = lines[0]
    events = []
    for line in lines[1:]:
        if not line.strip():
            continue
        events.append(json.loads(line))

    if not events:
        return

    offset = 0.0
    prev_ts = events[0][0]
    adjusted = []
    for event in events:
        ts = event[0]
        gap = ts - prev_ts
        if gap > max_idle:
            offset += gap - max_idle
        adjusted.append([ts - offset, *event[1:]])
        prev_ts = ts

    out_lines = [header]
    for event in adjusted:
        out_lines.append(json.dumps(event))
    cast_path.write_text("\n".join(out_lines) + "\n")


def main() -> None:
    # --- Collect credentials before recording ---
    print("=== fabprint demo recorder ===")
    print(f"Email: {EMAIL}")
    password = getpass.getpass("Bambu Cloud password (won't appear in recording): ")
    if not password:
        print("Password required.", file=sys.stderr)
        sys.exit(1)

    # --- Prep ---
    fabprint_toml = DEMO_DIR / "fabprint.toml"
    if fabprint_toml.exists():
        fabprint_toml.unlink()

    # Back up and clear credentials for a fresh setup demo
    cred_path = Path.home() / ".config" / "fabprint" / "credentials.toml"
    cred_backup = None
    if cred_path.exists():
        cred_backup = cred_path.read_text()
        cred_path.unlink()

    CAST_FILE.parent.mkdir(parents=True, exist_ok=True)
    CAST_FILE.unlink(missing_ok=True)

    env = {
        **os.environ,
        "FABPRINT_SKIP_SLICER_DETECT": "1",
        "PROMPT_TOOLKIT_NO_CPR": "1",
    }

    child = pexpect.spawn(
        f"asciinema rec --cols 80 --rows 25 --overwrite {CAST_FILE}",
        cwd=str(DEMO_DIR),
        encoding="utf-8",
        timeout=120,
        dimensions=(25, 80),
        env=env,
    )
    child.delaybeforesend = 0

    # Wait for shell prompt
    time.sleep(2)

    try:
        # ============================================================
        # STEP 1: fabprint setup — configure a cloud printer
        # ============================================================
        type_comment(child, "# Step 1: fabprint setup — run once per printer")
        type_command(child, "fabprint setup")

        # Printer name — accept default "workshop"
        expect(child, "Printer name")
        time.sleep(0.5)
        child.send("\r")

        # Choose type — 2 = bambu-cloud
        expect(child, "Choose type")
        time.sleep(0.5)
        child.sendline("2")

        # Confirm cloud login
        expect(child, "Log in now")
        time.sleep(0.5)
        child.sendline("y")

        # Email
        expect(child, "Email")
        time.sleep(0.5)
        type_slowly(child, EMAIL)
        time.sleep(0.3)
        child.send("\r")

        # Password — send pre-collected (masked on screen)
        expect(child, "Password")
        time.sleep(0.5)
        child.sendline(password)

        # Wait for verification code to be sent
        expect(child, "Verification code sent")
        time.sleep(1)

        # === INTERACTIVE: wait for user to get code ===
        print("\n" + "=" * 50, file=sys.stderr)
        print("CHECK YOUR EMAIL for the verification code.", file=sys.stderr)
        print("Copy the code to your clipboard, then press Enter.", file=sys.stderr)
        print("=" * 50, file=sys.stderr)
        input()

        code = read_clipboard()
        print(f"Got code from clipboard: {code[:2]}****", file=sys.stderr)

        expect(child, "Enter verification code")
        time.sleep(0.5)
        child.sendline(code)

        expect(child, "Login successful")
        time.sleep(1)

        # Pick printer #1
        expect(child, "Pick a printer")
        time.sleep(0.5)
        child.sendline("1")

        expect(child, "Selected:")
        time.sleep(1)

        expect(child, "Wrote.*credentials")
        time.sleep(2)

        child.sendline("clear")
        time.sleep(1)

        # ============================================================
        # STEP 2: fabprint init — project configuration wizard
        # ============================================================
        type_comment(child, "# Step 2: fabprint init — configure a print project")
        type_command(child, "fabprint init")

        # Project name — accept default
        expect(child, "Project name")
        time.sleep(1)
        child.send("\r")

        # CAD Files — multi-select both
        expect(child, "Select files")
        time.sleep(1)
        child.send(" ")
        time.sleep(0.3)
        child.send(DOWN)
        time.sleep(0.3)
        child.send(" ")
        time.sleep(0.3)
        child.send("\r")
        time.sleep(1)

        # First file — copies + orient (accept defaults)
        expect(child, "copies")
        time.sleep(0.5)
        child.send("\r")
        expect(child, "orient")
        time.sleep(0.5)
        child.send("\r")

        # Second file — copies + orient
        expect(child, "copies")
        time.sleep(0.5)
        child.send("\r")
        expect(child, "orient")
        time.sleep(0.5)
        child.send("\r")

        # Printer Connection — select workshop
        expect(child, "Printer Connection")
        time.sleep(1)
        child.send("\r")
        time.sleep(1)

        # Printer Profile — search P1S
        expect(child, "Printer Profile")
        time.sleep(1)
        type_slowly(child, "P1S 0.4")
        time.sleep(1)
        child.send("\r")
        time.sleep(1)

        # Process Profile
        expect(child, "Process Profile")
        time.sleep(1)
        type_slowly(child, "0.20mm Standard @BBL X1C")
        time.sleep(1)
        child.send("\r")
        time.sleep(1)

        # Plate size — auto-detected, no prompt expected

        # Slicer Version — pick first
        expect(child, "Pick version")
        time.sleep(0.5)
        child.sendline("1")
        time.sleep(1)

        # Filaments — accept AMS suggestions
        expect(child, "Use these filaments")
        time.sleep(1)
        child.sendline("y")
        time.sleep(1)

        # Filament Assignment — slot 3 for both
        expect(child, r"slot \(1-")
        time.sleep(0.5)
        child.sendline("3")

        expect(child, r"slot \(1-")
        time.sleep(0.5)
        child.sendline("3")
        time.sleep(1)

        # Slicer Overrides — pick infill density, then finish
        expect(child, "Pick override")
        time.sleep(0.5)
        child.sendline("1")

        expect(child, "Value for")
        time.sleep(0.5)
        type_slowly(child, "30")
        time.sleep(0.5)
        child.send("\r")

        # Finish overrides
        expect(child, "Pick override")
        time.sleep(0.5)
        child.send("\r")

        # Preview — write
        expect(child, "Write.*Go back.*Quit")
        time.sleep(2)
        child.sendline("w")

        expect(child, "Wrote fabprint.toml")
        time.sleep(2)

        child.sendline("clear")
        time.sleep(1)

        # ============================================================
        # STEP 3: fabprint validate — check config for issues
        # ============================================================
        type_comment(child, "# Step 3: fabprint validate — check config")
        type_command(child, "fabprint validate")

        expect(child, "checks passed|warning")
        time.sleep(3)

        child.sendline("clear")
        time.sleep(1)

        # ============================================================
        # STEP 4: fabprint run — execute the pipeline
        # ============================================================
        type_comment(child, "# Step 4: fabprint run — build and send to printer")
        type_command(child, "fabprint run --dry-run")

        expect(child, "Loaded.*part")
        time.sleep(0.5)

        expect(child, "Arranged.*part")
        time.sleep(0.5)

        expect(child, "Plate exported")
        time.sleep(0.5)

        expect(child, "Sliced", timeout=180)
        time.sleep(1)

        expect(child, "Print time|filament")
        time.sleep(1)

        expect(child, "Dry run|Sent to printer")
        time.sleep(3)

        child.sendline("clear")
        time.sleep(1)

        # ============================================================
        # STEP 5: fabprint status — live printer dashboard
        # ============================================================
        type_comment(child, "# Step 5: fabprint status -w — live printer dashboard")
        type_command(child, "fabprint status -w -i 1")

        # Let the dashboard refresh a few times
        time.sleep(10)

        # Ctrl-C to stop
        child.send("\x03")
        time.sleep(2)

    finally:
        # Exit asciinema
        child.sendline("exit")
        try:
            child.expect(pexpect.EOF, timeout=10)
        except Exception:
            pass
        child.close()

        # Restore credentials backup
        if cred_backup:
            cred_path.parent.mkdir(parents=True, exist_ok=True)
            cred_path.write_text(cred_backup)
            cred_path.chmod(0o600)

    # Post-process: compress idle gaps
    print(f"\nCompressing idle gaps (max {MAX_IDLE}s)...")
    compress_idle(CAST_FILE)

    print(f"Recording saved to {CAST_FILE}")
    print(f"Play:    asciinema play {CAST_FILE}")
    print(f"To GIF:  agg --font-size 20 {CAST_FILE} docs/recordings/demo.gif")


if __name__ == "__main__":
    main()
