"""Hamilton lifecycle adapters for pipeline observability.

These adapters plug into the Hamilton driver to provide per-node timing,
logging, and extensible hooks without modifying any pipeline node code.

Usage::

    from fabprint.adapters import TimingAdapter

    dr = (
        driver.Builder()
        .with_modules(pipeline)
        .with_adapters(TimingAdapter())
        .build()
    )
"""

from __future__ import annotations

import logging
import time

from hamilton.lifecycle import NodeExecutionHook

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
