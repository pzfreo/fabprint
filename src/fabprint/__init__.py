"""fabprint — Headless 3D print pipeline."""

from __future__ import annotations

from pathlib import Path

__version__ = "0.1.112"


class FabprintError(Exception):
    """User-facing error — printed without a traceback."""


def require_file(path: Path, label: str = "File") -> None:
    """Raise FileNotFoundError if *path* does not exist."""
    if not path.exists():
        raise FileNotFoundError(f"{label} not found: {path}")


__all__ = ["FabprintError", "__version__", "require_file"]
