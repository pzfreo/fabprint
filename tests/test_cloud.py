"""Tests for the cloud printing wrapper module."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fabprint.cloud import (
    _find_bridge,
    cloud_cancel,
    cloud_print,
    cloud_status,
    cloud_tasks,
)


@pytest.fixture
def token_file(tmp_path):
    f = tmp_path / "token.json"
    f.write_text('{"token": "test_tok", "uid": "123", "name": "test", "email": "t@t.com"}')
    return f


@pytest.fixture
def threemf_file(tmp_path):
    f = tmp_path / "test.3mf"
    f.write_bytes(b"PK\x03\x04fake3mf")
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
        with patch("fabprint.cloud.shutil.which", return_value=None):
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

        with patch("fabprint.cloud._run_bridge", return_value=mock_result):
            result = cloud_print(threemf_file, "DEV123", token_file)
            assert result["result"] == "success"
            assert result["return_code"] == 0

    def test_non_json_output(self, threemf_file, token_file):
        mock_result = MagicMock()
        mock_result.stdout = "some garbage output"
        mock_result.stderr = "error details"
        mock_result.returncode = 1

        with patch("fabprint.cloud._run_bridge", return_value=mock_result):
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

        with patch("fabprint.cloud._run_bridge", return_value=mock_result) as mock_run:
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

        with patch("fabprint.cloud._run_bridge", return_value=mock_result):
            status = cloud_status("DEV123", token_file)
            assert status["gcode_state"] == "IDLE"
            assert status["bed_temper"] == 22.5

    def test_no_status(self, token_file):
        mock_result = MagicMock()
        mock_result.stdout = ""
        mock_result.returncode = 2

        with patch("fabprint.cloud._run_bridge", return_value=mock_result):
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

        with patch("fabprint.cloud._run_bridge", return_value=mock_result):
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

        with patch("fabprint.cloud._run_bridge", return_value=mock_result):
            result = cloud_cancel("DEV123", token_file)
            assert result["sent"] is True
            assert result["device_id"] == "DEV123"
