"""Hamilton lifecycle adapters for pipeline observability.

These adapters plug into the Hamilton driver to provide per-node timing,
logging, and extensible hooks without modifying any pipeline node code.

Usage::

    from fabprint.adapters import TimingAdapter, ProgressAdapter

    dr = (
        driver.Builder()
        .with_modules(pipeline)
        .with_adapters(ProgressAdapter())
        .build()
    )
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

from hamilton.lifecycle import NodeExecutionHook

if TYPE_CHECKING:
    from rich.status import Status

log = logging.getLogger(__name__)


class TimingAdapter(NodeExecutionHook):
    """Log elapsed time for every pipeline node."""

    def __init__(self) -> None:
        self._starts: dict[str, float] = {}

    def run_before_node_execution(
        self,
        *,
        node_name: str,
        node_tags: dict,
        node_kwargs: dict,
        node_return_type: type,
        task_id: str | None,
        run_id: str,
        **future_kwargs,
    ) -> None:
        self._starts[node_name] = time.monotonic()
        log.debug("Starting: %s", node_name)

    def run_after_node_execution(
        self,
        *,
        node_name: str,
        node_tags: dict,
        node_kwargs: dict,
        node_return_type: type,
        result,
        error: Exception | None,
        success: bool,
        task_id: str | None,
        run_id: str,
        **future_kwargs,
    ) -> None:
        elapsed = time.monotonic() - self._starts.pop(node_name, time.monotonic())
        if success:
            log.info("Completed: %s (%.2fs)", node_name, elapsed)
        else:
            log.warning("Failed: %s (%.2fs) — %s", node_name, elapsed, error)


class ProgressAdapter(NodeExecutionHook):
    """Rich spinner + checkmark progress for user-visible pipeline stages."""

    # Only these Hamilton nodes get a visible spinner / checkmark line.
    _STAGE_NODES: frozenset[str] = frozenset(
        {
            "loaded_parts",
            "placements",
            "plate_3mf_path",
            "sliced_output_dir",
            "gcode_stats",
            "print_result",
        }
    )

    _SPINNER_LABELS: dict[str, str] = {
        "loaded_parts": "Loading parts",
        "placements": "Arranging onto plate",
        "plate_3mf_path": "Exporting plate",
        "preview_path": "Exporting preview",
        "sliced_output_dir": "Slicing",
        "gcode_stats": "Reading gcode",
        "print_result": "Sending to printer",
    }

    def __init__(self) -> None:
        from rich.console import Console

        self._console = Console(highlight=False)
        self._starts: dict[str, float] = {}
        self._status: Status | None = None
        self._slice_version: str | None = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _start_spinner(self, label: str) -> None:
        from rich.status import Status

        self._status = Status(label, console=self._console, spinner="dots")
        self._status.start()

    def _stop_spinner(self) -> None:
        if self._status is not None:
            self._status.stop()
            self._status = None

    def _ok(self, msg: str, elapsed: float = 0, *, show_elapsed: bool = True) -> None:
        elapsed_str = ""
        if show_elapsed and elapsed >= 2:
            elapsed_str = f"[dim]{elapsed:.0f}s[/dim]"
        self._console.print(f"[green]✔[/green] {msg}  {elapsed_str}".rstrip())

    def _err(self, msg: str) -> None:
        self._console.print(f"[red]✗[/red] {msg}")

    # ------------------------------------------------------------------
    # NodeExecutionHook
    # ------------------------------------------------------------------

    def run_before_node_execution(
        self,
        *,
        node_name: str,
        node_tags: dict,
        node_kwargs: dict,
        node_return_type: type,
        task_id: str | None,
        run_id: str,
        **future_kwargs,
    ) -> None:
        if node_name not in self._STAGE_NODES:
            return

        self._starts[node_name] = time.monotonic()
        label = self._SPINNER_LABELS.get(node_name, node_name)

        if node_name == "sliced_output_dir":
            cfg = node_kwargs.get("config")
            ver = node_kwargs.get("docker_version")
            if not ver and cfg and cfg.slicer.version:
                ver = cfg.slicer.version
            if ver:
                label = f"Slicing with OrcaSlicer {ver}"
                self._slice_version = ver

        elif node_name == "print_result":
            cfg = node_kwargs.get("config")
            if cfg and cfg.printer:
                label = f'Sending to printer "{cfg.printer.name}"'

        # gcode_stats is fast — skip spinner, just print the result line
        if node_name != "gcode_stats":
            self._start_spinner(label)

    def run_after_node_execution(
        self,
        *,
        node_name: str,
        node_tags: dict,
        node_kwargs: dict,
        node_return_type: type,
        result,
        error: Exception | None,
        success: bool,
        task_id: str | None,
        run_id: str,
        **future_kwargs,
    ) -> None:
        if node_name not in self._STAGE_NODES:
            return

        elapsed = time.monotonic() - self._starts.pop(node_name, time.monotonic())
        self._stop_spinner()

        if not success:
            label = self._SPINNER_LABELS.get(node_name, node_name)
            self._err(f"{label} failed — {error}")
            return

        if node_name == "loaded_parts":
            n = len(result.meshes)
            self._ok(f"Loaded {n} part{'s' if n != 1 else ''}", elapsed)

        elif node_name == "placements":
            n = len(result)
            cfg = node_kwargs.get("config")
            plate_str = ""
            if cfg:
                w, d = cfg.plate.size
                plate_str = f"[dim]({w:.0f}×{d:.0f}mm)[/dim]"
            self._ok(
                f"Arranged {n} part{'s' if n != 1 else ''} onto plate  {plate_str}",
                elapsed,
            )

        elif node_name == "plate_3mf_path":
            name = result.name if isinstance(result, Path) else "plate.3mf"
            self._ok(f"Plate exported → [dim]{name}[/dim]", elapsed)

        elif node_name == "preview_path":
            name = result.name if isinstance(result, Path) else "plate_preview.3mf"
            self._ok(f"Preview exported → [dim]{name}[/dim]", elapsed)

        elif node_name == "sliced_output_dir":
            ver = self._slice_version
            ver_str = f"with OrcaSlicer {ver}" if ver else ""
            time_str = f" in {elapsed:.0f}s" if elapsed >= 2 else ""
            self._ok(f"Sliced {ver_str}{time_str}".rstrip(), show_elapsed=False)
            # Show gcode filename if available
            if isinstance(result, Path):
                gcode_files = list(result.glob("*.gcode"))
                if gcode_files:
                    self._console.print(f"  [dim]→ {gcode_files[0].name}[/dim]")

        elif node_name == "gcode_stats":
            parts: list[str] = []
            if "print_time" in result:
                parts.append(f"Print time: {result['print_time']}")
            if "filament_g" in result:
                parts.append(f"{result['filament_g']:.1f}g filament")
            elif "filament_cm3" in result:
                parts.append(f"{result['filament_cm3']:.1f}cm³ filament")
            if parts:
                self._ok(", ".join(parts), elapsed)

        elif node_name == "print_result":
            cfg = node_kwargs.get("config")
            dry_run = node_kwargs.get("dry_run", False)
            printer_name = cfg.printer.name if cfg and cfg.printer else "printer"
            if dry_run:
                self._ok(f'Dry run — would send to "{printer_name}"', elapsed)
            else:
                self._ok(f'Sent to printer "{printer_name}"', elapsed)
