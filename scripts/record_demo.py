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
import re
import subprocess
import sys
import time
from pathlib import Path

import pexpect

CAST_FILE = Path(__file__).parent.parent / "docs" / "recordings" / "demo.cast"
DEMO_DIR = Path.home() / "repos" / "decoy-case"
TYPING_DELAY = 0.04
EMAIL = "paul@fremantle.org"

# Max idle gap in the final recording (seconds)
MAX_IDLE = 2.0

# Escape sequences
DOWN = "\x1b[B"


def status(msg: str) -> None:
    """Print a status message to stderr (not captured in recording)."""
    print(f"  → {msg}", file=sys.stderr)


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


def clean_buffer(child: pexpect.spawn) -> str:
    """Return the last 500 chars of the child buffer with ANSI codes stripped."""
    buf = child.before or ""
    return re.sub(r"\x1b\[[^m]*m|\x1b\([^)]*\)", "", buf)[-500:]


def expect(child: pexpect.spawn, pattern: str, timeout: int = 60) -> None:
    """Wait for pattern, with detailed debug on failure."""
    status(f"waiting for: {pattern}")
    try:
        child.expect(pattern, timeout=timeout)
        status(f"  matched: {pattern}")
    except pexpect.TIMEOUT:
        print(f"\n{'=' * 60}", file=sys.stderr)
        print(f"TIMEOUT waiting for: {pattern}", file=sys.stderr)
        print(f"{'=' * 60}", file=sys.stderr)
        print(f"Buffer:\n{clean_buffer(child)}", file=sys.stderr)
        print(f"{'=' * 60}", file=sys.stderr)
        raise


def read_clipboard() -> str:
    """Read text from the system clipboard (macOS)."""
    return subprocess.check_output(["pbpaste"], text=True).strip()


def compress_idle(cast_path: Path, max_idle: float = MAX_IDLE) -> Path:
    """Compress idle gaps in a v3 asciicast file.

    v3 format uses relative timestamps (deltas from previous event).
    Caps any delta that exceeds max_idle.

    Writes compressed version to a new file (original preserved).
    Returns the path to the compressed file.
    """
    lines = cast_path.read_text().splitlines()
    if not lines:
        return cast_path

    header = lines[0]
    events = []
    for line in lines[1:]:
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    if not events:
        return cast_path

    # v3: timestamps are deltas — just cap any delta > max_idle
    adjusted = []
    for event in events:
        delta = event[0]
        capped = min(delta, max_idle)
        adjusted.append([round(capped, 3), *event[1:]])

    out_lines = [header]
    for event in adjusted:
        out_lines.append(json.dumps(event))

    compressed = cast_path.with_suffix(".compressed.cast")
    compressed.write_text("\n".join(out_lines) + "\n")
    return compressed


def do_setup(child: pexpect.spawn, password: str) -> None:
    """Step 1: fabprint setup — configure a cloud printer."""
    status("STEP 1: fabprint setup")
    type_comment(child, "# Step 1: fabprint setup — run once per printer")
    type_command(child, "fabprint setup")

    # Printer name — accept default "workshop"
    expect(child, "Printer name")
    time.sleep(0.5)
    child.send("\r")
    status("accepted default printer name")

    # Choose type — 2 = bambu-cloud
    expect(child, "Choose type")
    time.sleep(0.5)
    child.sendline("2")
    status("selected bambu-cloud")

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
    status(f"entered email: {EMAIL}")

    # Password — send pre-collected (masked on screen)
    expect(child, "Password")
    time.sleep(0.5)
    child.sendline(password)
    status("sent password")

    # Wait for verification code to be sent
    expect(child, "Verification code sent")
    time.sleep(1)
    status("verification code sent")

    # === INTERACTIVE: wait for user to get code ===
    print("\n" + "=" * 50, file=sys.stderr)
    print("CHECK YOUR EMAIL for the verification code.", file=sys.stderr)
    print("Copy the code to your clipboard, then press Enter.", file=sys.stderr)
    print("=" * 50, file=sys.stderr)
    input()

    code = read_clipboard()
    status(f"got code from clipboard: {code[:2]}****")

    expect(child, "Enter verification code")
    time.sleep(0.5)
    child.sendline(code)
    status("sent verification code")

    expect(child, "Login successful")
    time.sleep(1)
    status("login successful")

    # Pick printer #1
    expect(child, "Pick a printer")
    time.sleep(0.5)
    child.sendline("1")

    expect(child, "Selected:")
    time.sleep(1)

    expect(child, "Wrote.*credentials")
    time.sleep(2)
    status("setup complete")

    time.sleep(1)


def do_init(child: pexpect.spawn) -> None:
    """Step 2: fabprint init — project configuration wizard."""
    status("STEP 2: fabprint init")
    type_comment(child, "# Step 2: fabprint init — configure a print project")
    type_command(child, "fabprint init")

    # Project name — accept default
    expect(child, "Project name")
    time.sleep(1)
    child.send("\r")
    status("accepted project name")

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
    status("selected CAD files")

    # First file — copies + orient (accept defaults)
    expect(child, "copies")
    time.sleep(0.5)
    child.send("\r")
    expect(child, "orient")
    time.sleep(0.5)
    child.send("\r")
    status("configured first file")

    # Second file — copies + orient
    expect(child, "copies")
    time.sleep(0.5)
    child.send("\r")
    expect(child, "orient")
    time.sleep(0.5)
    child.send("\r")
    status("configured second file")

    # Printer Connection — select workshop
    expect(child, "Printer Connection")
    time.sleep(1)
    child.send("\r")
    time.sleep(1)
    status("selected printer connection")

    # Printer Profile — search P1S
    expect(child, "Printer Profile")
    time.sleep(1)
    type_slowly(child, "P1S 0.4")
    time.sleep(1)
    child.send("\r")
    time.sleep(1)
    status("selected printer profile")

    # Process Profile
    expect(child, "Process Profile")
    time.sleep(1)
    type_slowly(child, "0.20mm Standard @BBL X1C")
    time.sleep(1)
    child.send("\r")
    time.sleep(1)
    status("selected process profile")

    # Plate size — auto-detected, no prompt

    # Slicer Version — pick first
    expect(child, "Pick version")
    time.sleep(0.5)
    child.sendline("1")
    time.sleep(1)
    status("selected slicer version")

    # Filaments — accept AMS suggestions
    expect(child, "Use these filaments")
    time.sleep(1)
    child.sendline("y")
    time.sleep(1)
    status("accepted AMS filaments")

    # Filament Assignment — slot 3 for both
    expect(child, r"slot \(1-")
    time.sleep(0.5)
    child.sendline("3")
    expect(child, r"slot \(1-")
    time.sleep(0.5)
    child.sendline("3")
    time.sleep(1)
    status("assigned filament slots")

    # Slicer Overrides — pick infill density, then finish
    expect(child, "Pick override")
    time.sleep(0.5)
    child.sendline("1")

    expect(child, "Value for")
    time.sleep(0.5)
    type_slowly(child, "30")
    time.sleep(0.5)
    child.send("\r")
    status("set infill override to 30%")

    # Finish overrides
    expect(child, "Pick override")
    time.sleep(0.5)
    child.send("\r")
    status("finished overrides")

    # Preview — write
    expect(child, "Write.*Go back.*Quit")
    time.sleep(2)
    child.sendline("w")

    expect(child, "Wrote fabprint.toml")
    time.sleep(2)
    status("init complete — wrote fabprint.toml")

    time.sleep(1)


def do_validate(child: pexpect.spawn) -> None:
    """Step 3: fabprint validate."""
    status("STEP 3: fabprint validate")
    type_comment(child, "# Step 3: fabprint validate — check config")
    type_command(child, "fabprint validate")

    expect(child, "checks passed|warning")
    time.sleep(3)
    status("validate complete")

    time.sleep(1)


def do_run(child: pexpect.spawn, dry_run: bool = True) -> None:
    """Step 4: fabprint run."""
    mode = "--dry-run" if dry_run else ""
    status(f"STEP 4: fabprint run {mode}".strip())
    type_comment(child, "# Step 4: fabprint run — build and send to printer")
    cmd = "fabprint run --dry-run" if dry_run else "fabprint run"
    type_command(child, cmd)

    expect(child, "Loaded.*part")
    time.sleep(0.5)
    status("parts loaded")

    expect(child, "Arranged.*part")
    time.sleep(0.5)
    status("parts arranged")

    expect(child, "Plate exported")
    time.sleep(0.5)
    status("plate exported")

    expect(child, "Sliced", timeout=180)
    time.sleep(1)
    status("slicing complete")

    expect(child, "Print time|filament")
    time.sleep(1)

    expect(child, "Dry run|Sent to printer")
    time.sleep(3)
    status("run complete")

    time.sleep(1)


def do_status(child: pexpect.spawn) -> None:
    """Step 5: fabprint status -w (live dashboard)."""
    status("STEP 5: fabprint status -w")
    type_comment(child, "# Step 5: fabprint status -w — live printer dashboard")
    type_command(child, "fabprint status -w --interval 1")

    # Let the dashboard refresh a few times
    time.sleep(10)

    # Ctrl-C to stop
    child.send("\x03")
    time.sleep(2)
    status("status dashboard done")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Record fabprint demo")
    parser.add_argument(
        "--no-dry-run",
        action="store_true",
        help="Actually send to printer (default: dry run)",
    )
    args = parser.parse_args()
    dry_run = not args.no_dry_run

    # --- Collect credentials before recording ---
    print("=== fabprint demo recorder ===", file=sys.stderr)
    print(f"Email: {EMAIL}", file=sys.stderr)
    password = getpass.getpass("Bambu Cloud password (won't appear in recording): ")
    if not password:
        print("Password required.", file=sys.stderr)
        sys.exit(1)

    # --- Prep ---
    status("cleaning up for fresh demo")
    fabprint_toml = DEMO_DIR / "fabprint.toml"
    if fabprint_toml.exists():
        fabprint_toml.unlink()
        status("removed existing fabprint.toml")

    # Back up and clear credentials for a fresh setup demo
    cred_path = Path.home() / ".config" / "fabprint" / "credentials.toml"
    cred_backup = None
    if cred_path.exists():
        cred_backup = cred_path.read_text()
        cred_path.unlink()
        status("backed up and cleared credentials")

    CAST_FILE.parent.mkdir(parents=True, exist_ok=True)
    CAST_FILE.unlink(missing_ok=True)

    env = {
        **os.environ,
        "FABPRINT_SKIP_SLICER_DETECT": "1",
        "PROMPT_TOOLKIT_NO_CPR": "1",
    }

    status("starting asciinema recording")
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
    status("shell ready")

    try:
        do_setup(child, password)
        do_init(child)
        do_validate(child)
        do_run(child, dry_run=dry_run)
        do_status(child)
    finally:
        # Exit asciinema
        status("exiting asciinema")
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
            status("restored credentials backup")

    # Post-process: compress idle gaps into a separate file
    status(f"compressing idle gaps (max {MAX_IDLE}s)")
    compressed = compress_idle(CAST_FILE)

    print(f"\nOriginal:   {CAST_FILE}", file=sys.stderr)
    print(f"Compressed: {compressed}", file=sys.stderr)
    print(f"Play:       asciinema play {compressed}", file=sys.stderr)
    print(f"To GIF:     agg --font-size 20 {compressed} docs/recordings/demo.gif", file=sys.stderr)


if __name__ == "__main__":
    main()
