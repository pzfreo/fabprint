#!/usr/bin/env python3
"""Fetch available OrcaSlicer Docker image versions and write to package data."""

import json
from pathlib import Path

import requests

DOCKERHUB_REPO = "fabprint/fabprint"
OUTPUT = Path(__file__).resolve().parent.parent / "src" / "fabprint" / "docker_versions.json"


def main():
    url = f"https://hub.docker.com/v2/repositories/{DOCKERHUB_REPO}/tags"
    resp = requests.get(url, params={"page_size": 100}, timeout=10)
    resp.raise_for_status()
    tags = [t["name"] for t in resp.json().get("results", [])]
    versions = sorted([t[5:] for t in tags if t.startswith("orca-")], reverse=True)
    OUTPUT.write_text(json.dumps(versions) + "\n")
    print(f"Wrote {len(versions)} version(s) to {OUTPUT}: {versions}")


if __name__ == "__main__":
    main()
