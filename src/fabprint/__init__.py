"""fabprint — Headless 3D print pipeline."""

__version__ = "0.1.83"


class FabprintError(Exception):
    """User-facing error — printed without a traceback."""


__all__ = ["FabprintError", "__version__"]
