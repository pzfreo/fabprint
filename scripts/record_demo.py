#!/usr/bin/env python3
"""Record fabprint init → validate → run demo using pexpect + asciinema.

Usage:
    # Pre-requisites: credentials configured, printer online, Docker running
    cd ~/repos/fabprint
    python scripts/record_demo.py

    # Convert to GIF:
    agg docs/recordings/demo.cast docs/recordings/demo.gif

Requires: pexpect, asciinema, agg (brew install agg)
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pexpect

CAST_FILE = Path(__file__).parent.parent / "docs" / "recordings" / "demo.cast"
DEMO_DIR = Path.home() / "repos" / "decoy-case"
TYPING_DELAY = 0.08

# Escape sequences
DOWN = "\x1b[B"
UP = "\x1b[A"


def type_slowly(child: pexpect.spawn, text: str, delay: float = TYPING_DELAY) -> None:
    """Type text character by character with a delay."""
    for ch in text:
        child.send(ch)
        time.sleep(delay)


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


def main() -> None:
    # --- Prep ---
    fabprint_toml = DEMO_DIR / "fabprint.toml"
    if fabprint_toml.exists():
        fabprint_toml.unlink()

    print("Pre-warming Docker bridge...")
    os.system(f"cd {DEMO_DIR} && fabprint status > /dev/null 2>&1")
    print("Docker warm.")

    CAST_FILE.parent.mkdir(parents=True, exist_ok=True)
    CAST_FILE.unlink(missing_ok=True)

    env = {
        **os.environ,
        "FABPRINT_SKIP_SLICER_DETECT": "1",
        "PROMPT_TOOLKIT_NO_CPR": "1",
    }

    # Start a bash shell inside asciinema
    # dimensions=(rows, cols) in pexpect
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

    # ================================================================
    # PART 1: fabprint init
    # ================================================================
    type_slowly(child, "fabprint init")
    time.sleep(0.5)
    child.send("\r")

    # Step 1: Project name — accept default
    expect(child, "Project name")
    time.sleep(1)
    child.send("\r")

    # Step 2: CAD Files — multi-select both files
    expect(child, "Select files")
    time.sleep(1)
    # Space toggles first, Down moves, Space toggles second, Enter confirms
    child.send(" ")
    time.sleep(0.3)
    child.send(DOWN)
    time.sleep(0.3)
    child.send(" ")
    time.sleep(0.3)
    child.send("\r")
    time.sleep(1)

    # First file — copies (accept default 1)
    expect(child, "copies")
    time.sleep(0.5)
    child.send("\r")

    # First file — orient (accept default "flat")
    expect(child, "orient")
    time.sleep(0.5)
    child.send("\r")

    # Second file — copies
    expect(child, "copies")
    time.sleep(0.5)
    child.send("\r")

    # Second file — orient
    expect(child, "orient")
    time.sleep(0.5)
    child.send("\r")

    # Step 3: Printer Connection — select first (workshop)
    expect(child, "Printer Connection")
    time.sleep(1)
    child.send("\r")
    time.sleep(1)

    # Step 4: Printer Profile — search for P1S
    expect(child, "Printer Profile")
    time.sleep(1)
    type_slowly(child, "P1S 0.4")
    time.sleep(1)
    child.send("\r")
    time.sleep(1)

    # Process Profile — search
    expect(child, "Process Profile")
    time.sleep(1)
    type_slowly(child, "0.20mm Standard @BBL X1C")
    time.sleep(1)
    child.send("\r")
    time.sleep(1)

    # Plate size — accept defaults
    expect(child, "Plate width")
    time.sleep(0.5)
    child.send("\r")

    expect(child, "Plate depth")
    time.sleep(0.5)
    child.send("\r")

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

    # Slicer Overrides — add infill density
    expect(child, "Add slicer overrides")
    time.sleep(0.5)
    child.sendline("y")

    expect(child, "Pick override")
    time.sleep(0.5)
    child.sendline("1")

    expect(child, "Value for")
    time.sleep(0.5)
    type_slowly(child, "30")
    time.sleep(0.5)
    child.send("\r")

    expect(child, "Add another override")
    time.sleep(0.5)
    child.send("\r")

    # Preview — write
    expect(child, "Write.*Go back.*Quit")
    time.sleep(2)
    child.sendline("w")

    expect(child, "Wrote fabprint.toml")
    time.sleep(2)

    # Wait for prompt
    time.sleep(1)

    # ================================================================
    # PART 2: fabprint validate
    # ================================================================
    type_slowly(child, "fabprint validate")
    time.sleep(0.5)
    child.send("\r")

    expect(child, "checks passed|warning")
    time.sleep(3)

    # ================================================================
    # PART 3: fabprint run --dry-run
    # ================================================================
    type_slowly(child, "fabprint run --dry-run")
    time.sleep(0.5)
    child.send("\r")

    expect(child, "Loaded.*part")
    time.sleep(0.5)

    expect(child, "Arranged.*part")
    time.sleep(0.5)

    expect(child, "Plate exported")
    time.sleep(0.5)

    # Slicing can take a while
    expect(child, "Sliced", timeout=120)
    time.sleep(1)

    expect(child, "Print time|filament")
    time.sleep(1)

    expect(child, "Dry run|Sent to printer")
    time.sleep(3)

    # Exit the shell to end asciinema recording
    child.sendline("exit")
    child.expect(pexpect.EOF, timeout=10)
    child.close()

    print(f"\nRecording saved to {CAST_FILE}")
    print(f"Play:    asciinema play {CAST_FILE}")
    print(f"To GIF:  agg {CAST_FILE} docs/recordings/demo.gif")


if __name__ == "__main__":
    main()
