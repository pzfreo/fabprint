#!/usr/bin/env python3
"""Extract OrcaSlicer profile names from a fabprint Docker image.

Runs the image, lists profile JSON files, and writes
src/fabprint/data/profiles.orca.<version>.json.

Usage:
    python scripts/extract_profiles.py 2.3.1
    python scripts/extract_profiles.py 2.3.1 --image fabprint/fabprint:orca-2.3.1
    python scripts/extract_profiles.py 2.3.1 2.2.0   # multiple versions (uses default image names)
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

PROFILE_ROOT = "/home/fabprint/.config/OrcaSlicer/system/BBL"
CATEGORIES = ("machine", "process", "filament")
OUT_DIR = Path(__file__).parent.parent / "src" / "fabprint" / "data"


def extract(version: str, image: str) -> dict:
    """Pull profile names from the Docker image for the given OrcaSlicer version."""
    print(f"Extracting profiles from {image} ...", flush=True)

    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "find",
            image,
            PROFILE_ROOT,
            "-name",
            "*.json",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )

    if result.returncode != 0:
        print(f"  error: {result.stderr.strip()}", file=sys.stderr)
        sys.exit(1)

    profiles: dict[str, list[str]] = {cat: [] for cat in CATEGORIES}

    for line in result.stdout.splitlines():
        path = Path(line.strip())
        name = path.stem
        category = path.parent.name
        if category not in CATEGORIES:
            continue
        if "template" in name.lower() or name.startswith("fdm_"):
            continue
        profiles[category].append(name)

    for cat in CATEGORIES:
        profiles[cat] = sorted(profiles[cat])
        print(f"  {cat}: {len(profiles[cat])} profiles")

    return {"engine": "orca", "version": version, **profiles}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("versions", nargs="+", help="OrcaSlicer version(s) to extract")
    parser.add_argument(
        "--image",
        help="Docker image to use (default: fabprint/fabprint:<version>). "
        "Only valid when a single version is given.",
    )
    args = parser.parse_args()

    if args.image and len(args.versions) > 1:
        parser.error("--image can only be used with a single version")

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    for version in args.versions:
        image = args.image or f"fabprint/fabprint:{version}"
        data = extract(version, image)
        out = OUT_DIR / f"profiles.orca.{version}.json"
        out.write_text(json.dumps(data, indent=2) + "\n")
        print(f"  Written to {out}\n")


if __name__ == "__main__":
    main()
