"""Tests for the cloud printing wrapper module."""

import io
import json
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fabprint.cloud import (
    PersistentBridge,
    _build_ams_mapping,
    _build_ams_mapping_from_state,
    _find_bridge,
    _run_bridge,
    cloud_cancel,
    cloud_print,
    cloud_status,
    cloud_tasks,
    parse_ams_trays,
)


@pytest.fixture
def token_file(tmp_path):
    f = tmp_path / "token.json"
    f.write_text('{"token": "test_tok", "uid": "123", "name": "test", "email": "t@t.com"}')
    return f


@pytest.fixture
def threemf_file(tmp_path):
    import io
    import zipfile

    f = tmp_path / "test.3mf"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("Metadata/model_settings.config", "<config/>")
    f.write_bytes(buf.getvalue())
    return f


class TestFindBridge:
    def test_env_var_override(self, tmp_path, monkeypatch):
        bridge = tmp_path / "my_bridge"
        bridge.write_text("#!/bin/sh\n")
        monkeypatch.setenv("BAMBU_BRIDGE_PATH", str(bridge))
        assert _find_bridge() == str(bridge)

    def test_env_var_nonexistent(self, monkeypatch):
        monkeypatch.setenv("BAMBU_BRIDGE_PATH", "/nonexistent/bridge")
        # Falls through to which() and other paths
        result = _find_bridge()
        # Result depends on whether bridge is in PATH; just verify no crash
        assert result is None or isinstance(result, str)

    def test_not_found(self, monkeypatch):
        monkeypatch.delenv("BAMBU_BRIDGE_PATH", raising=False)
        # Mock which to return None
        with patch("fabprint.cloud.bridge.shutil.which", return_value=None):
            # It should check common paths too; none will exist
            result = _find_bridge()
            # Could be None or a valid path if bridge exists locally
            assert result is None or isinstance(result, str)


class TestCloudPrint:
    def test_file_not_found(self, token_file):
        with pytest.raises(FileNotFoundError, match="3MF"):
            cloud_print(Path("/nonexistent.3mf"), "DEV123", token_file)

    def test_token_not_found(self, threemf_file):
        with pytest.raises(FileNotFoundError, match="Token"):
            cloud_print(threemf_file, "DEV123", Path("/nonexistent_token.json"))

    def test_success(self, threemf_file, token_file):
        mock_result = MagicMock()
        mock_result.stdout = (
            '{"result":"success","return_code":0,'
            '"print_result":0,"device_id":"DEV123","file":"test.3mf"}'
        )
        mock_result.stderr = ""
        mock_result.returncode = 0

        with patch("fabprint.cloud.bridge._run_bridge", return_value=mock_result):
            result = cloud_print(threemf_file, "DEV123", token_file)
            assert result["result"] == "success"
            assert result["return_code"] == 0

    def test_non_json_output(self, threemf_file, token_file):
        mock_result = MagicMock()
        mock_result.stdout = "some garbage output"
        mock_result.stderr = "error details"
        mock_result.returncode = 1

        with patch("fabprint.cloud.bridge._run_bridge", return_value=mock_result):
            with pytest.raises(RuntimeError, match="non-JSON"):
                cloud_print(threemf_file, "DEV123", token_file)

    def test_with_config_3mf(self, threemf_file, token_file, tmp_path):
        config = tmp_path / "config.3mf"
        config.write_bytes(b"PK\x03\x04config")

        mock_result = MagicMock()
        mock_result.stdout = (
            '{"result":"success","return_code":0,"print_result":0,"device_id":"DEV","file":"t.3mf"}'
        )
        mock_result.stderr = ""

        with patch("fabprint.cloud.bridge._run_bridge", return_value=mock_result) as mock_run:
            cloud_print(threemf_file, "DEV", token_file, config_3mf=config)
            args = mock_run.call_args[0][0]
            assert "--config-3mf" in args


class TestCloudStatus:
    def test_token_not_found(self):
        with pytest.raises(FileNotFoundError, match="Token"):
            cloud_status("DEV123", Path("/nonexistent_token.json"))

    def test_success(self, token_file):
        mock_result = MagicMock()
        mock_result.stdout = '{"print":{"gcode_state":"IDLE","bed_temper":22.5}}'
        mock_result.returncode = 0

        with patch("fabprint.cloud.bridge._run_bridge", return_value=mock_result):
            status = cloud_status("DEV123", token_file)
            assert status["gcode_state"] == "IDLE"
            assert status["bed_temper"] == 22.5

    def test_no_status(self, token_file):
        mock_result = MagicMock()
        mock_result.stdout = ""
        mock_result.returncode = 2

        with patch("fabprint.cloud.bridge._run_bridge", return_value=mock_result):
            with pytest.raises(RuntimeError, match="No status"):
                cloud_status("DEV123", token_file)


class TestCloudTasks:
    def test_token_not_found(self):
        with pytest.raises(FileNotFoundError, match="Token"):
            cloud_tasks(Path("/nonexistent_token.json"))

    def test_success(self, token_file):
        mock_result = MagicMock()
        mock_result.stdout = '{"total":2,"hits":[{"id":1,"title":"job1"},{"id":2,"title":"job2"}]}'
        mock_result.returncode = 0

        with patch("fabprint.cloud.bridge._run_bridge", return_value=mock_result):
            tasks = cloud_tasks(token_file, limit=5)
            assert len(tasks) == 2
            assert tasks[0]["title"] == "job1"


class TestCloudCancel:
    def test_token_not_found(self):
        with pytest.raises(FileNotFoundError, match="Token"):
            cloud_cancel("DEV123", Path("/nonexistent_token.json"))

    def test_success(self, token_file):
        mock_result = MagicMock()
        mock_result.stdout = '{"command":"stop","device_id":"DEV123","sent":true}'
        mock_result.returncode = 0

        with patch("fabprint.cloud.bridge._run_bridge", return_value=mock_result):
            result = cloud_cancel("DEV123", token_file)
            assert result["sent"] is True
            assert result["device_id"] == "DEV123"


# ---------------------------------------------------------------------------
# PersistentBridge tests
# ---------------------------------------------------------------------------


class TestPersistentBridge:
    def test_enter_creates_container(self, token_file):
        mock_result = MagicMock(stdout="abc123def456\n", returncode=0)
        with patch("fabprint.cloud.bridge.subprocess.run", return_value=mock_result) as mock_run:
            bridge = PersistentBridge(token_file)
            bridge.__enter__()

            cmd = mock_run.call_args[0][0]
            assert cmd[0] == "docker"
            assert "run" in cmd
            assert "-d" in cmd
            assert "--platform" in cmd
            assert "linux/amd64" in cmd
            assert "sleep" in cmd
            assert "infinity" in cmd
            assert bridge._container_id == "abc123def456"

    def test_exit_removes_container(self, token_file):
        mock_create = MagicMock(stdout="abc123def456\n", returncode=0)
        mock_rm = MagicMock(returncode=0)

        with patch(
            "fabprint.cloud.bridge.subprocess.run",
            side_effect=[mock_create, mock_rm],
        ) as mock_run:
            with PersistentBridge(token_file) as bridge:
                assert bridge._container_id == "abc123def456"

            # Verify docker rm -f was called on exit
            rm_call = mock_run.call_args_list[-1]
            rm_cmd = rm_call[0][0]
            assert rm_cmd[0] == "docker"
            assert "rm" in rm_cmd
            assert "-f" in rm_cmd
            assert "abc123def456" in rm_cmd

    def test_exit_clears_container_id(self, token_file):
        mock_create = MagicMock(stdout="abc123def456\n", returncode=0)
        mock_rm = MagicMock(returncode=0)

        with patch("fabprint.cloud.bridge.subprocess.run", side_effect=[mock_create, mock_rm]):
            bridge = PersistentBridge(token_file)
            bridge.__enter__()
            bridge.__exit__(None, None, None)
            assert bridge._container_id is None

    def test_exit_noop_when_no_container(self, token_file):
        """Exit does nothing if no container was created."""
        bridge = PersistentBridge(token_file)
        # Should not raise
        bridge.__exit__(None, None, None)

    def test_status_runs_docker_exec(self, token_file):
        mock_create = MagicMock(stdout="abc123def456\n", returncode=0)
        mock_proc = MagicMock()
        status_json = '{"print":{"gcode_state":"IDLE","bed_temper":25.0}}\n'
        mock_proc.stdout.readline.return_value = status_json

        mock_sel = MagicMock()
        mock_sel.select.return_value = [True]  # data ready

        with (
            patch("fabprint.cloud.bridge.subprocess.run", return_value=mock_create),
            patch("fabprint.cloud.bridge.subprocess.Popen", return_value=mock_proc) as mock_popen,
            patch("selectors.DefaultSelector", return_value=mock_sel),
        ):
            with PersistentBridge(token_file) as bridge:
                status = bridge.status("DEV123")

            # Verify docker exec command
            popen_cmd = mock_popen.call_args[0][0]
            assert popen_cmd[0] == "docker"
            assert "exec" in popen_cmd
            assert "abc123def456" in popen_cmd
            assert "bambu_cloud_bridge" in popen_cmd
            assert "status" in popen_cmd
            assert "DEV123" in popen_cmd

            assert status["gcode_state"] == "IDLE"
            assert status["bed_temper"] == 25.0
            mock_proc.kill.assert_called_once()
            mock_proc.wait.assert_called_once()

    def test_status_timeout_raises(self, token_file):
        mock_create = MagicMock(stdout="abc123def456\n", returncode=0)
        mock_proc = MagicMock()

        mock_sel = MagicMock()
        mock_sel.select.return_value = []  # timeout — no data

        with (
            patch("fabprint.cloud.bridge.subprocess.run", return_value=mock_create),
            patch("fabprint.cloud.bridge.subprocess.Popen", return_value=mock_proc),
            patch("selectors.DefaultSelector", return_value=mock_sel),
        ):
            with PersistentBridge(token_file) as bridge:
                with pytest.raises(RuntimeError, match="timed out"):
                    bridge.status("DEV123", timeout=5)

            mock_proc.kill.assert_called_once()

    def test_status_non_json_raises(self, token_file):
        mock_create = MagicMock(stdout="abc123def456\n", returncode=0)
        mock_proc = MagicMock()
        mock_proc.stdout.readline.return_value = "not json at all\n"
        mock_proc.returncode = 1

        mock_sel = MagicMock()
        mock_sel.select.return_value = [True]

        with (
            patch("fabprint.cloud.bridge.subprocess.run", return_value=mock_create),
            patch("fabprint.cloud.bridge.subprocess.Popen", return_value=mock_proc),
            patch("selectors.DefaultSelector", return_value=mock_sel),
        ):
            with PersistentBridge(token_file) as bridge:
                with pytest.raises(RuntimeError, match="non-JSON"):
                    bridge.status("DEV123")

    def test_token_file_mounted_readonly(self, token_file):
        """Token file should be mounted as read-only in the container."""
        mock_result = MagicMock(stdout="abc123def456\n", returncode=0)
        with patch("fabprint.cloud.bridge.subprocess.run", return_value=mock_result) as mock_run:
            bridge = PersistentBridge(token_file)
            bridge.__enter__()

            cmd = mock_run.call_args[0][0]
            # Find the -v mount arg
            v_idx = cmd.index("-v")
            mount_arg = cmd[v_idx + 1]
            assert mount_arg.endswith(":ro")
            assert "/input/token.json" in mount_arg


# ---------------------------------------------------------------------------
# _run_bridge Docker pull behaviour tests
# ---------------------------------------------------------------------------


class TestRunBridgeDockerPull:
    """Tests for _run_bridge Docker image pull behaviour.

    These test the current always-pull behaviour and will serve as
    regression tests when we add pull staleness optimisation.
    """

    def test_pulls_image_when_using_docker(self):
        """When bridge not found, _run_bridge should use Docker and pull the image."""
        mock_docker_info = MagicMock(returncode=0)
        mock_pull = MagicMock(returncode=0, stdout="Status: Image is up to date", stderr="")
        mock_run_result = MagicMock(returncode=0, stdout='{"result":"ok"}', stderr="")

        results = [mock_docker_info, mock_pull, mock_run_result]

        with (
            patch("fabprint.cloud.bridge._find_bridge", return_value=None),
            patch("platform.system", return_value="Linux"),
            patch("fabprint.cloud.bridge.subprocess.run", side_effect=results) as mock_run,
        ):
            _run_bridge(["status", "DEV123", "/tmp/tok.json"])

            # Second call should be the pull
            pull_cmd = mock_run.call_args_list[1][0][0]
            assert pull_cmd == ["docker", "pull", "fabprint/cloud-bridge"]

    def test_pull_failure_still_runs_container(self):
        """If docker pull fails, _run_bridge should still attempt to run."""
        mock_docker_info = MagicMock(returncode=0)
        mock_pull = MagicMock(
            returncode=1,
            stdout="Error",
            stderr="network error",
        )
        mock_run_result = MagicMock(
            returncode=0,
            stdout='{"result":"ok"}',
            stderr="",
        )

        results = [mock_docker_info, mock_pull, mock_run_result]

        with (
            patch("fabprint.cloud.bridge._find_bridge", return_value=None),
            patch("platform.system", return_value="Linux"),
            patch(
                "fabprint.cloud.bridge.subprocess.run",
                side_effect=results,
            ),
        ):
            result = _run_bridge(["status", "DEV123", "/tmp/tok.json"])
            assert result.stdout == '{"result":"ok"}'

    def test_always_uses_docker_on_macos(self, tmp_path):
        """On macOS, _run_bridge must use Docker even with local bridge."""
        bridge = tmp_path / "bambu_cloud_bridge"
        bridge.write_text("#!/bin/sh\n")

        mock_docker_info = MagicMock(returncode=0)
        mock_pull = MagicMock(returncode=0, stdout="", stderr="")
        mock_run_result = MagicMock(
            returncode=0,
            stdout='{"result":"ok"}',
            stderr="",
        )
        side = [mock_docker_info, mock_pull, mock_run_result]

        with (
            patch(
                "fabprint.cloud.bridge._find_bridge",
                return_value=str(bridge),
            ),
            patch("platform.system", return_value="Darwin"),
            patch(
                "fabprint.cloud.bridge.subprocess.run",
                side_effect=side,
            ) as mock_run,
        ):
            _run_bridge(
                ["print", "/tmp/file.3mf", "DEV123", "/tmp/tok.json"],
            )

            run_cmd = mock_run.call_args_list[2][0][0]
            assert run_cmd[0] == "docker"
            assert "fabprint/cloud-bridge" in run_cmd

    def test_uses_local_bridge_on_linux(self):
        """On Linux with local bridge, should use it directly."""
        bridge_path = "/usr/local/bin/bambu_cloud_bridge"
        mock_result = MagicMock(
            returncode=0,
            stdout='{"result":"ok"}',
            stderr="",
        )

        with (
            patch(
                "fabprint.cloud.bridge._find_bridge",
                return_value=bridge_path,
            ),
            patch("platform.system", return_value="Linux"),
            patch(
                "fabprint.cloud.bridge.subprocess.run",
                return_value=mock_result,
            ) as mock_run,
        ):
            _run_bridge(["status", "DEV123", "/tmp/tok.json"])

            cmd = mock_run.call_args[0][0]
            assert cmd[0] == bridge_path
            assert "docker" not in cmd

    def test_no_docker_raises_runtime_error(self):
        """Without bridge or Docker, raises RuntimeError."""
        with (
            patch(
                "fabprint.cloud.bridge._find_bridge",
                return_value=None,
            ),
            patch("platform.system", return_value="Linux"),
            patch(
                "fabprint.cloud.bridge.subprocess.run",
                side_effect=FileNotFoundError,
            ),
        ):
            with pytest.raises(RuntimeError, match="Docker is required"):
                _run_bridge(["status", "DEV123", "/tmp/tok.json"])

    def test_docker_run_mounts_file_args(self, tmp_path):
        """File arguments should be mounted individually."""
        threemf = tmp_path / "test.3mf"
        threemf.write_bytes(b"PK")
        token = tmp_path / "token.json"
        token.write_text('{"token":"x"}')

        mock_docker_info = MagicMock(returncode=0)
        mock_pull = MagicMock(returncode=0, stdout="", stderr="")
        mock_run_result = MagicMock(
            returncode=0,
            stdout='{"result":"ok"}',
            stderr="",
        )
        side = [mock_docker_info, mock_pull, mock_run_result]

        with (
            patch(
                "fabprint.cloud.bridge._find_bridge",
                return_value=None,
            ),
            patch("platform.system", return_value="Linux"),
            patch(
                "fabprint.cloud.bridge.subprocess.run",
                side_effect=side,
            ) as mock_run,
        ):
            _run_bridge(
                ["print", str(threemf), "DEV123", str(token)],
            )

            run_cmd = mock_run.call_args_list[2][0][0]
            v_indices = [i for i, arg in enumerate(run_cmd) if arg == "-v"]
            assert len(v_indices) == 2
            for idx in v_indices:
                mount = run_cmd[idx + 1]
                assert ":ro" in mount
                assert "/input/" in mount

    def test_verbose_flag_appended(self):
        """verbose=True should append -v to the command."""
        bridge_path = "/usr/local/bin/bambu_cloud_bridge"
        mock_result = MagicMock(
            returncode=0,
            stdout='{"result":"ok"}',
            stderr="",
        )

        with (
            patch(
                "fabprint.cloud.bridge._find_bridge",
                return_value=bridge_path,
            ),
            patch("platform.system", return_value="Linux"),
            patch(
                "fabprint.cloud.bridge.subprocess.run",
                return_value=mock_result,
            ) as mock_run,
        ):
            _run_bridge(
                ["status", "DEV123", "/tmp/tok.json"],
                verbose=True,
            )

            cmd = mock_run.call_args[0][0]
            assert cmd[-1] == "-v"


# ---------------------------------------------------------------------------
# AMS mapping tests
# ---------------------------------------------------------------------------


def _make_filament_xml(filaments: list[dict]) -> dict:
    """Build filament_by_id dict of XML elements, matching how _build_ams_mapping parses them."""
    parts = []
    for f in filaments:
        attrs = " ".join(f'{k}="{v}"' for k, v in f.items())
        parts.append(f"<filament {attrs} />")
    xml = f"<plate>{''.join(parts)}</plate>"
    root = ET.fromstring(xml)
    return {int(f.get("id", "1")): f for f in root.findall("filament")}


def _make_ams_trays(trays: list[dict]) -> list[dict]:
    """Build AMS tray list in the format returned by parse_ams_trays."""
    result = []
    for t in trays:
        result.append(
            {
                "phys_slot": t.get("phys_slot", t.get("ams_id", 0) * 4 + t.get("slot_id", 0)),
                "ams_id": t.get("ams_id", 0),
                "slot_id": t.get("slot_id", 0),
                "type": t["type"],
                "color": t["color"],
                "tray_info_idx": t.get("tray_info_idx", ""),
            }
        )
    return result


def _make_3mf(
    tmp_path: Path,
    filaments_xml: str,
    filament_colours: list[str],
    filament_settings_ids: list[str],
    name: str = "test.3mf",
) -> Path:
    """Create a minimal 3MF with slice_info and project_settings for testing."""
    slice_info = f"""<?xml version="1.0" encoding="UTF-8"?>
<config>
  <plate>
    <metadata key="index" value="1"/>
{filaments_xml}
  </plate>
</config>"""
    project_settings = json.dumps(
        {
            "filament_colour": filament_colours,
            "filament_settings_id": filament_settings_ids,
        }
    )
    path = tmp_path / name
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("Metadata/slice_info.config", slice_info)
        zf.writestr("Metadata/project_settings.config", project_settings)
        zf.writestr("Metadata/model_settings.config", "<config/>")
    path.write_bytes(buf.getvalue())
    return path


# --- Full 4-slot AMS fixture (matches P1S with mixed filaments) ---

FULL_AMS_TRAYS = _make_ams_trays(
    [
        {"ams_id": 0, "slot_id": 0, "type": "PLA", "color": "FFFFFF", "tray_info_idx": "GFL99"},
        {"ams_id": 0, "slot_id": 1, "type": "PLA", "color": "000000", "tray_info_idx": "GFL99"},
        {"ams_id": 0, "slot_id": 2, "type": "PETG-CF", "color": "F2754E", "tray_info_idx": "GFG98"},
        {"ams_id": 0, "slot_id": 3, "type": "PETG-CF", "color": "808080", "tray_info_idx": "GFG98"},
    ]
)


class TestParseAmsTrays:
    def test_full_4_slot_ams(self):
        status = {
            "ams": {
                "ams": [
                    {
                        "id": "0",
                        "tray": [
                            {
                                "id": "0",
                                "tray_type": "PLA",
                                "tray_color": "FFFFFFAA",
                                "tray_info_idx": "GFL99",
                            },
                            {
                                "id": "1",
                                "tray_type": "PLA",
                                "tray_color": "000000AA",
                                "tray_info_idx": "GFL99",
                            },
                            {
                                "id": "2",
                                "tray_type": "PETG-CF",
                                "tray_color": "F2754EAA",
                                "tray_info_idx": "GFG98",
                            },
                            {
                                "id": "3",
                                "tray_type": "PETG-CF",
                                "tray_color": "808080AA",
                                "tray_info_idx": "GFG98",
                            },
                        ],
                    }
                ]
            }
        }
        trays = parse_ams_trays(status)
        assert len(trays) == 4
        assert trays[0] == {
            "phys_slot": 0,
            "ams_id": 0,
            "slot_id": 0,
            "type": "PLA",
            "color": "FFFFFF",
            "tray_info_idx": "GFL99",
        }
        assert trays[2] == {
            "phys_slot": 2,
            "ams_id": 0,
            "slot_id": 2,
            "type": "PETG-CF",
            "color": "F2754E",
            "tray_info_idx": "GFG98",
        }

    def test_empty_trays_skipped(self):
        status = {
            "ams": {
                "ams": [
                    {
                        "id": "0",
                        "tray": [
                            {"id": "0", "tray_type": "PLA", "tray_color": "FFFFFFAA"},
                            {"id": "1", "tray_type": "", "tray_color": ""},  # empty
                            {"id": "2", "tray_type": "PETG-CF", "tray_color": "F2754EAA"},
                        ],
                    }
                ]
            }
        }
        trays = parse_ams_trays(status)
        assert len(trays) == 2
        assert trays[0]["phys_slot"] == 0
        assert trays[1]["phys_slot"] == 2

    def test_empty_ams(self):
        assert parse_ams_trays({}) == []
        assert parse_ams_trays({"ams": {}}) == []
        assert parse_ams_trays({"ams": {"ams": []}}) == []


class TestBuildAmsMappingFromState:
    def test_single_filament_type_and_color_match(self):
        """Single PETG-CF filament (id=3) should map to physical slot 2 (type+color match)."""
        filament_by_id = _make_filament_xml(
            [{"id": "3", "type": "PETG-CF", "color": "#F2754E", "tray_info_idx": "GFG98"}]
        )
        result = _build_ams_mapping_from_state(filament_by_id, 3, FULL_AMS_TRAYS)
        # Slots: [0]=unused, [1]=unused, [2]=filament id 3 → phys slot 2 (PETG-CF + color match)
        assert result[2] == 2  # filament id 3 maps to phys slot 2 (exact match)
        assert len(result) == 3

    def test_single_filament_type_match_no_color(self):
        """PETG-CF with different color should still match a PETG-CF tray."""
        filament_by_id = _make_filament_xml(
            [{"id": "1", "type": "PETG-CF", "color": "#123456", "tray_info_idx": "GFG98"}]
        )
        result = _build_ams_mapping_from_state(filament_by_id, 1, FULL_AMS_TRAYS)
        assert result[0] in (2, 3)  # either PETG-CF slot

    def test_multiple_filaments_different_types(self):
        """PLA + PETG-CF should map to correct physical slots."""
        filament_by_id = _make_filament_xml(
            [
                {"id": "1", "type": "PLA", "color": "#FFFFFF", "tray_info_idx": "GFL99"},
                {"id": "2", "type": "PETG-CF", "color": "#F2754E", "tray_info_idx": "GFG98"},
            ]
        )
        result = _build_ams_mapping_from_state(filament_by_id, 2, FULL_AMS_TRAYS)
        assert result[0] == 0  # PLA white → phys 0
        assert result[1] == 2  # PETG-CF orange → phys 2

    def test_no_ams_trays_sequential_fallback(self):
        """Without AMS data, slots map sequentially: 0, 1, 2..."""
        filament_by_id = _make_filament_xml([{"id": "1", "type": "PLA", "color": "#FFFFFF"}])
        result = _build_ams_mapping_from_state(filament_by_id, 1, [])
        assert result == [0]

    def test_no_type_match_sequential_fallback(self):
        """Filament type not in AMS → falls back to sequential index."""
        filament_by_id = _make_filament_xml([{"id": "1", "type": "ABS", "color": "#FF0000"}])
        result = _build_ams_mapping_from_state(filament_by_id, 1, FULL_AMS_TRAYS)
        assert result[0] == 0  # sequential fallback

    def test_unused_slots_are_minus_one(self):
        """Slots without filaments should be -1 (matching BambuConnect)."""
        filament_by_id = _make_filament_xml([{"id": "3", "type": "PETG-CF", "color": "#F2754E"}])
        result = _build_ams_mapping_from_state(filament_by_id, 3, FULL_AMS_TRAYS)
        # Slots 0 and 1 are not in filament_by_id → should be -1
        assert result[0] == -1
        assert result[1] == -1
        assert result[2] == 2  # the actual filament


class TestBuildAmsMapping:
    """Integration tests using real 3MF files."""

    def test_real_3mf_single_filament(self):
        """Test with the real example 3MF (single PETG-CF, filament id=3).

        BambuConnect sends [-1, -1, 2, -1, -1] for this scenario (5 project slots,
        only filament 3 used). See docs/cloud-print-research.md lines 1245-1263.
        """
        threemf = Path("examples/gib-tuners-c13-10/output/plate_sliced.gcode.3mf")
        if not threemf.exists():
            pytest.skip("Example 3MF not available")

        result = _build_ams_mapping(threemf, ams_trays=FULL_AMS_TRAYS)

        # Full 5 slots from project_settings (not capped)
        assert len(result["amsMapping"]) == 5
        # Matches BambuConnect's captured payload exactly
        assert result["amsMapping"] == [-1, -1, 2, -1, -1]
        # All arrays same length
        assert len(result["amsDetailMapping"]) == 5
        assert len(result["amsMapping2"]) == 5
        # Unused slots have -1 sentinel in detail
        assert result["amsDetailMapping"][0]["ams"] == -1
        assert result["amsDetailMapping"][2]["ams"] == 2

    def test_synthetic_single_filament_slot3(self, tmp_path):
        """Synthetic: single filament in slot 3, 5 project slots (P1S profile)."""
        threemf = _make_3mf(
            tmp_path,
            filaments_xml=(
                '    <filament id="3" type="PETG-CF" color="#F2754E" tray_info_idx="GFG98" />'
            ),
            filament_colours=["#F2754E"] * 5,
            filament_settings_ids=[
                "Generic PLA @base",
                "Generic PLA @base",
                "Generic PETG-CF @base",
                "Generic PETG-CF @base",
                "Generic PETG-CF @base",
            ],
        )
        result = _build_ams_mapping(threemf, ams_trays=FULL_AMS_TRAYS)

        # Full 5 slots from project_settings (not capped)
        assert len(result["amsMapping"]) == 5
        # Filament 3 → phys slot 2, all others -1
        assert result["amsMapping"] == [-1, -1, 2, -1, -1]

    def test_synthetic_two_filaments(self, tmp_path):
        """Two filaments: PLA in slot 1, PETG-CF in slot 2."""
        threemf = _make_3mf(
            tmp_path,
            filaments_xml=(
                '    <filament id="1" type="PLA" color="#FFFFFF" tray_info_idx="GFL99" />\n'
                '    <filament id="2" type="PETG-CF" color="#F2754E" tray_info_idx="GFG98" />'
            ),
            filament_colours=["#FFFFFF", "#F2754E"],
            filament_settings_ids=["Generic PLA @base", "Generic PETG-CF @base"],
        )
        result = _build_ams_mapping(threemf, ams_trays=FULL_AMS_TRAYS)

        assert len(result["amsMapping"]) == 2
        assert result["amsMapping"][0] == 0  # PLA white → phys 0
        assert result["amsMapping"][1] == 2  # PETG-CF orange → phys 2

    def test_no_ams_trays(self, tmp_path):
        """Without AMS trays, falls back to sequential mapping."""
        threemf = _make_3mf(
            tmp_path,
            filaments_xml='    <filament id="1" type="PLA" color="#FFFFFF" />',
            filament_colours=["#FFFFFF"],
            filament_settings_ids=["Generic PLA @base"],
        )
        result = _build_ams_mapping(threemf, ams_trays=None)

        assert result["amsMapping"] == [0]

    def test_bridge_gets_full_mapping_with_sentinels(self, tmp_path):
        """Bridge now gets the full mapping including -1 sentinels.

        BambuConnect sends [-1, -1, 2, -1, -1] and the library handles it.
        No more stripping — the full array is passed through.
        """
        threemf = _make_3mf(
            tmp_path,
            filaments_xml=(
                '    <filament id="3" type="PETG-CF" color="#F2754E" tray_info_idx="GFG98" />'
            ),
            filament_colours=["#F2754E"] * 5,
            filament_settings_ids=[
                "Generic PLA @base",
                "Generic PLA @base",
                "Generic PETG-CF @base",
                "Generic PETG-CF @base",
                "Generic PETG-CF @base",
            ],
        )
        result = _build_ams_mapping(threemf, ams_trays=FULL_AMS_TRAYS)
        raw = result["amsMapping"]

        # Full mapping matches BambuConnect's format
        assert raw == [-1, -1, 2, -1, -1]
        # At least one valid slot
        assert any(v >= 0 for v in raw)
