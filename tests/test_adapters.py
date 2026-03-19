"""Tests for Hamilton lifecycle adapters."""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fabprint.adapters import ProgressAdapter, TimingAdapter

# Common kwargs template for hook calls
_BASE_KWARGS = {
    "node_tags": {},
    "node_kwargs": {},
    "node_return_type": type(None),
    "task_id": None,
    "run_id": "test-run",
}


def _kw(**overrides):
    """Return base kwargs merged with overrides."""
    merged = {**_BASE_KWARGS, **overrides}
    return merged


# ======================================================================
# TimingAdapter
# ======================================================================


class TestTimingAdapter:
    def test_before_records_start(self):
        adapter = TimingAdapter()
        adapter.run_before_node_execution(node_name="foo", **_kw())
        assert "foo" in adapter._starts

    def test_after_success_logs_info(self, caplog):
        adapter = TimingAdapter()
        adapter.run_before_node_execution(node_name="foo", **_kw())
        with caplog.at_level(logging.INFO, logger="fabprint.adapters"):
            adapter.run_after_node_execution(
                node_name="foo", result=None, error=None, success=True, **_kw()
            )
        assert any("Completed: foo" in r.message for r in caplog.records)
        assert "foo" not in adapter._starts  # cleaned up

    def test_after_failure_logs_warning(self, caplog):
        adapter = TimingAdapter()
        adapter.run_before_node_execution(node_name="bar", **_kw())
        err = RuntimeError("boom")
        with caplog.at_level(logging.WARNING, logger="fabprint.adapters"):
            adapter.run_after_node_execution(
                node_name="bar", result=None, error=err, success=False, **_kw()
            )
        assert any("Failed: bar" in r.message for r in caplog.records)
        assert any("boom" in r.message for r in caplog.records)

    def test_after_missing_start_does_not_crash(self, caplog):
        """If run_after is called without a prior run_before, it should not error."""
        adapter = TimingAdapter()
        with caplog.at_level(logging.INFO, logger="fabprint.adapters"):
            adapter.run_after_node_execution(
                node_name="unknown", result=42, error=None, success=True, **_kw()
            )
        assert any("Completed: unknown" in r.message for r in caplog.records)


# ======================================================================
# ProgressAdapter helpers
# ======================================================================


class TestProgressAdapterOk:
    def _make_adapter(self):
        adapter = ProgressAdapter()
        adapter._console = MagicMock()
        return adapter

    def test_ok_no_elapsed(self):
        adapter = self._make_adapter()
        adapter._ok("Done", elapsed=0)
        call_args = adapter._console.print.call_args[0][0]
        assert "Done" in call_args
        # No elapsed time shown for <2s
        assert "s" not in call_args or "dim" not in call_args

    def test_ok_with_elapsed_above_threshold(self):
        adapter = self._make_adapter()
        adapter._ok("Done", elapsed=5.0)
        call_args = adapter._console.print.call_args[0][0]
        assert "5s" in call_args
        assert "dim" in call_args

    def test_ok_skip_elapsed(self):
        adapter = self._make_adapter()
        adapter._ok("Sliced", elapsed=10.0, show_elapsed=False)
        call_args = adapter._console.print.call_args[0][0]
        # show_elapsed=False means no elapsed string even if elapsed >= 2
        assert "10s" not in call_args

    def test_ok_elapsed_just_below_threshold(self):
        adapter = self._make_adapter()
        adapter._ok("Fast", elapsed=1.9)
        call_args = adapter._console.print.call_args[0][0]
        assert "dim" not in call_args


class TestProgressAdapterErr:
    def test_err_prints_red_x(self):
        adapter = ProgressAdapter()
        adapter._console = MagicMock()
        adapter._err("something broke")
        call_args = adapter._console.print.call_args[0][0]
        assert "something broke" in call_args
        assert "red" in call_args


# ======================================================================
# ProgressAdapter spinner management
# ======================================================================


class TestProgressAdapterSpinner:
    def test_start_spinner_creates_status(self):
        adapter = ProgressAdapter()
        adapter._console = MagicMock()
        with patch("fabprint.adapters.ProgressAdapter._start_spinner"):
            # Test the real method via a direct call
            pass

        # Test directly
        adapter2 = ProgressAdapter()
        adapter2._console = MagicMock()
        with patch("rich.status.Status") as MockStatus:
            mock_instance = MagicMock()
            MockStatus.return_value = mock_instance
            adapter2._start_spinner("Loading")
            MockStatus.assert_called_once_with("Loading", console=adapter2._console, spinner="dots")
            mock_instance.start.assert_called_once()
            assert adapter2._status is mock_instance

    def test_stop_spinner_stops_and_clears(self):
        adapter = ProgressAdapter()
        mock_status = MagicMock()
        adapter._status = mock_status
        adapter._stop_spinner()
        mock_status.stop.assert_called_once()
        assert adapter._status is None

    def test_stop_spinner_noop_when_none(self):
        adapter = ProgressAdapter()
        adapter._status = None
        adapter._stop_spinner()  # should not raise
        assert adapter._status is None


# ======================================================================
# ProgressAdapter.run_before_node_execution
# ======================================================================


class TestProgressAdapterBefore:
    def _make_adapter(self):
        adapter = ProgressAdapter()
        adapter._console = MagicMock()
        return adapter

    def test_non_stage_node_is_ignored(self):
        adapter = self._make_adapter()
        with patch.object(adapter, "_start_spinner") as mock:
            adapter.run_before_node_execution(node_name="config", **_kw())
        mock.assert_not_called()
        assert "config" not in adapter._starts

    def test_loaded_parts_starts_spinner(self):
        adapter = self._make_adapter()
        with patch.object(adapter, "_start_spinner") as mock:
            adapter.run_before_node_execution(node_name="loaded_parts", **_kw())
        mock.assert_called_once_with("Loading parts")

    def test_gcode_stats_no_spinner(self):
        adapter = self._make_adapter()
        with patch.object(adapter, "_start_spinner") as mock:
            adapter.run_before_node_execution(node_name="gcode_stats", **_kw())
        mock.assert_not_called()
        # But start time should still be recorded
        assert "gcode_stats" in adapter._starts

    def test_sliced_output_dir_with_config_version(self):
        adapter = self._make_adapter()
        cfg = SimpleNamespace(slicer=SimpleNamespace(version="2.1.0"))
        with patch.object(adapter, "_start_spinner") as mock:
            adapter.run_before_node_execution(
                node_name="sliced_output_dir",
                **_kw(node_kwargs={"config": cfg}),
            )
        mock.assert_called_once_with("Slicing with OrcaSlicer 2.1.0")
        assert adapter._slice_version == "2.1.0"

    def test_sliced_output_dir_with_docker_version(self):
        adapter = self._make_adapter()
        with patch.object(adapter, "_start_spinner") as mock:
            adapter.run_before_node_execution(
                node_name="sliced_output_dir",
                **_kw(node_kwargs={"docker_version": "1.9.0"}),
            )
        mock.assert_called_once_with("Slicing with OrcaSlicer 1.9.0")
        assert adapter._slice_version == "1.9.0"

    def test_sliced_output_dir_no_version(self):
        adapter = self._make_adapter()
        with patch.object(adapter, "_start_spinner") as mock:
            adapter.run_before_node_execution(node_name="sliced_output_dir", **_kw())
        mock.assert_called_once_with("Slicing")

    def test_print_result_with_printer_name(self):
        adapter = self._make_adapter()
        cfg = SimpleNamespace(printer=SimpleNamespace(name="MyP1S"))
        with patch.object(adapter, "_start_spinner") as mock:
            adapter.run_before_node_execution(
                node_name="print_result",
                **_kw(node_kwargs={"config": cfg}),
            )
        mock.assert_called_once_with('Sending to printer "MyP1S"')

    def test_print_result_no_printer(self):
        adapter = self._make_adapter()
        cfg = SimpleNamespace(printer=None)
        with patch.object(adapter, "_start_spinner") as mock:
            adapter.run_before_node_execution(
                node_name="print_result",
                **_kw(node_kwargs={"config": cfg}),
            )
        mock.assert_called_once_with("Sending to printer")


# ======================================================================
# ProgressAdapter.run_after_node_execution
# ======================================================================


class TestProgressAdapterAfter:
    def _make_adapter(self):
        adapter = ProgressAdapter()
        adapter._console = MagicMock()
        return adapter

    def _run_after(self, adapter, node_name, result, success=True, error=None, **kw):
        # Prime the start time so elapsed calculation works
        adapter._starts[node_name] = 0  # will give large elapsed but we don't care
        merged = _kw(node_kwargs=kw.get("node_kwargs", {}))
        with patch("time.monotonic", return_value=1.0):
            adapter.run_after_node_execution(
                node_name=node_name,
                result=result,
                error=error,
                success=success,
                **merged,
            )

    def test_non_stage_node_ignored(self):
        adapter = self._make_adapter()
        adapter.run_after_node_execution(
            node_name="config", result=None, error=None, success=True, **_kw()
        )
        adapter._console.print.assert_not_called()

    def test_failure_prints_error(self):
        adapter = self._make_adapter()
        adapter._starts["loaded_parts"] = 0
        with (
            patch.object(adapter, "_stop_spinner") as stop_mock,
            patch.object(adapter, "_err") as err_mock,
        ):
            with patch("time.monotonic", return_value=1.0):
                adapter.run_after_node_execution(
                    node_name="loaded_parts",
                    result=None,
                    error=RuntimeError("file not found"),
                    success=False,
                    **_kw(),
                )
        stop_mock.assert_called_once()
        err_mock.assert_called_once()
        assert "failed" in err_mock.call_args[0][0]
        assert "file not found" in err_mock.call_args[0][0]

    def test_loaded_parts_singular(self):
        adapter = self._make_adapter()
        result = SimpleNamespace(meshes=["m1"])
        with patch.object(adapter, "_ok") as ok_mock:
            self._run_after(adapter, "loaded_parts", result)
        ok_mock.assert_called_once()
        assert "Loaded 1 part" in ok_mock.call_args[0][0]
        assert "parts" not in ok_mock.call_args[0][0]

    def test_loaded_parts_plural(self):
        adapter = self._make_adapter()
        result = SimpleNamespace(meshes=["m1", "m2", "m3"])
        with patch.object(adapter, "_ok") as ok_mock:
            self._run_after(adapter, "loaded_parts", result)
        assert "Loaded 3 parts" in ok_mock.call_args[0][0]

    def test_placements_with_config(self):
        adapter = self._make_adapter()
        cfg = SimpleNamespace(plate=SimpleNamespace(size=(256, 256)))
        result = [1, 2]
        with patch.object(adapter, "_ok") as ok_mock:
            self._run_after(adapter, "placements", result, node_kwargs={"config": cfg})
        msg = ok_mock.call_args[0][0]
        assert "Arranged 2 parts" in msg
        assert "256" in msg

    def test_placements_no_config(self):
        adapter = self._make_adapter()
        result = [1]
        with patch.object(adapter, "_ok") as ok_mock:
            self._run_after(adapter, "placements", result)
        msg = ok_mock.call_args[0][0]
        assert "Arranged 1 part" in msg
        assert "dim" not in msg  # no plate_str

    def test_plate_3mf_path_result(self):
        adapter = self._make_adapter()
        result = Path("/tmp/plate.3mf")
        with patch.object(adapter, "_ok") as ok_mock:
            self._run_after(adapter, "plate_3mf_path", result)
        msg = ok_mock.call_args[0][0]
        assert "plate.3mf" in msg

    def test_plate_3mf_path_non_path(self):
        adapter = self._make_adapter()
        with patch.object(adapter, "_ok") as ok_mock:
            self._run_after(adapter, "plate_3mf_path", "not-a-path")
        msg = ok_mock.call_args[0][0]
        assert "plate.3mf" in msg  # fallback name

    def test_sliced_output_dir_with_version_and_time(self):
        adapter = self._make_adapter()
        adapter._slice_version = "2.1.0"
        adapter._starts["sliced_output_dir"] = 0
        # Use a tmp_path-like mock for result
        mock_dir = MagicMock(spec=Path)
        mock_dir.glob.return_value = [Path("/tmp/out/plate.gcode")]
        with patch.object(adapter, "_ok") as ok_mock, patch("time.monotonic", return_value=5.0):
            adapter.run_after_node_execution(
                node_name="sliced_output_dir",
                result=mock_dir,
                error=None,
                success=True,
                **_kw(),
            )
        msg = ok_mock.call_args[0][0]
        assert "OrcaSlicer 2.1.0" in msg
        assert "5s" in msg
        # Also prints the gcode filename
        adapter._console.print.assert_called_once()
        gcode_line = adapter._console.print.call_args[0][0]
        assert "plate.gcode" in gcode_line

    def test_sliced_output_dir_fast(self):
        adapter = self._make_adapter()
        adapter._slice_version = None
        adapter._starts["sliced_output_dir"] = 0.5
        mock_dir = MagicMock(spec=Path)
        mock_dir.glob.return_value = []
        with patch.object(adapter, "_ok") as ok_mock, patch("time.monotonic", return_value=1.0):
            adapter.run_after_node_execution(
                node_name="sliced_output_dir",
                result=mock_dir,
                error=None,
                success=True,
                **_kw(),
            )
        msg = ok_mock.call_args[0][0]
        assert "Sliced" in msg
        # No time string for fast slicing
        assert " in " not in msg
        # show_elapsed=False
        assert ok_mock.call_args[1].get("show_elapsed") is False

    def test_sliced_output_dir_non_path_result(self):
        """When result is not a Path, gcode glob is skipped."""
        adapter = self._make_adapter()
        adapter._slice_version = None
        adapter._starts["sliced_output_dir"] = 0
        with patch.object(adapter, "_ok") as ok_mock, patch("time.monotonic", return_value=1.0):
            adapter.run_after_node_execution(
                node_name="sliced_output_dir",
                result="not-a-path",
                error=None,
                success=True,
                **_kw(),
            )
        ok_mock.assert_called_once()
        # No gcode filename printed
        adapter._console.print.assert_not_called()

    def test_gcode_stats_print_time_and_filament_g(self):
        adapter = self._make_adapter()
        result = {"print_time": "1h 23m", "filament_g": 12.345}
        with patch.object(adapter, "_ok") as ok_mock:
            self._run_after(adapter, "gcode_stats", result)
        msg = ok_mock.call_args[0][0]
        assert "Print time: 1h 23m" in msg
        assert "12.3g filament" in msg

    def test_gcode_stats_filament_cm3(self):
        adapter = self._make_adapter()
        result = {"filament_cm3": 7.89}
        with patch.object(adapter, "_ok") as ok_mock:
            self._run_after(adapter, "gcode_stats", result)
        msg = ok_mock.call_args[0][0]
        assert "7.9cm" in msg

    def test_gcode_stats_empty(self):
        adapter = self._make_adapter()
        result = {}
        with patch.object(adapter, "_ok") as ok_mock:
            self._run_after(adapter, "gcode_stats", result)
        ok_mock.assert_not_called()

    def test_print_result_normal(self):
        adapter = self._make_adapter()
        cfg = SimpleNamespace(printer=SimpleNamespace(name="P1S"))
        with patch.object(adapter, "_ok") as ok_mock:
            self._run_after(
                adapter,
                "print_result",
                result="ok",
                node_kwargs={"config": cfg, "dry_run": False},
            )
        msg = ok_mock.call_args[0][0]
        assert 'Sent to printer "P1S"' in msg

    def test_print_result_dry_run(self):
        adapter = self._make_adapter()
        cfg = SimpleNamespace(printer=SimpleNamespace(name="P1S"))
        with patch.object(adapter, "_ok") as ok_mock:
            self._run_after(
                adapter,
                "print_result",
                result="ok",
                node_kwargs={"config": cfg, "dry_run": True},
            )
        msg = ok_mock.call_args[0][0]
        assert "Dry run" in msg
        assert '"P1S"' in msg

    def test_print_result_no_config(self):
        adapter = self._make_adapter()
        with patch.object(adapter, "_ok") as ok_mock:
            self._run_after(adapter, "print_result", result="ok")
        msg = ok_mock.call_args[0][0]
        assert '"printer"' in msg

    def test_print_result_config_no_printer(self):
        adapter = self._make_adapter()
        cfg = SimpleNamespace(printer=None)
        with patch.object(adapter, "_ok") as ok_mock:
            self._run_after(
                adapter,
                "print_result",
                result="ok",
                node_kwargs={"config": cfg},
            )
        msg = ok_mock.call_args[0][0]
        assert '"printer"' in msg
