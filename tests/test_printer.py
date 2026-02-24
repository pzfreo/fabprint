"""Tests for printer module."""

from pathlib import Path
from unittest.mock import patch

import pytest

from fabprint.config import PrinterConfig
from fabprint.gcode import parse_gcode_metadata
from fabprint.printer import (
    _resolve_credentials,
    send_print,
    wrap_gcode_3mf,
)


def test_resolve_credentials_from_config():
    config = PrinterConfig(mode="lan", ip="10.0.0.1", access_code="abc", serial="SN123")
    creds = _resolve_credentials(config)
    assert creds["mode"] == "lan"
    assert creds["ip"] == "10.0.0.1"
    assert creds["access_code"] == "abc"
    assert creds["serial"] == "SN123"


def test_resolve_credentials_env_overrides(monkeypatch):
    config = PrinterConfig(mode="lan", ip="10.0.0.1", access_code="abc", serial="SN123")
    monkeypatch.setenv("BAMBU_PRINTER_IP", "192.168.1.99")
    monkeypatch.setenv("BAMBU_ACCESS_CODE", "override_code")
    monkeypatch.setenv("BAMBU_SERIAL", "OVERRIDE_SN")
    creds = _resolve_credentials(config)
    assert creds["ip"] == "192.168.1.99"
    assert creds["access_code"] == "override_code"
    assert creds["serial"] == "OVERRIDE_SN"


def test_resolve_credentials_cloud_env(monkeypatch):
    config = PrinterConfig(mode="cloud")
    monkeypatch.setenv("BAMBU_EMAIL", "user@test.com")
    monkeypatch.setenv("BAMBU_PASSWORD", "secret")
    creds = _resolve_credentials(config)
    assert creds["mode"] == "cloud"
    assert creds["email"] == "user@test.com"
    assert creds["password"] == "secret"


def test_send_print_lan_dry_run(tmp_path, capsys):
    gcode = tmp_path / "test.gcode"
    gcode.write_text("; test gcode")
    config = PrinterConfig(mode="lan", ip="10.0.0.1", access_code="abc", serial="SN123")
    with patch("fabprint.printer._send_lan") as mock_send:
        send_print(gcode, config, dry_run=True)
        mock_send.assert_called_once_with(
            gcode,
            ip="10.0.0.1",
            access_code="abc",
            serial="SN123",
            dry_run=True,
            upload_only=False,
        )


def test_send_print_cloud_dispatches_bambu_connect(tmp_path):
    """Test that cloud mode dispatches to _send_bambu_connect."""
    gcode = tmp_path / "test.gcode"
    gcode.write_text("; test gcode")
    config = PrinterConfig(mode="cloud")

    with patch("fabprint.printer._send_bambu_connect") as mock_send:
        send_print(gcode, config, dry_run=True)
        mock_send.assert_called_once_with(gcode, dry_run=True)


def test_send_print_lan_missing_ip():
    config = PrinterConfig(mode="lan", ip=None, access_code="abc", serial="SN123")
    with pytest.raises(ValueError, match="ip"):
        send_print(Path("dummy.gcode"), config)


def test_send_print_lan_missing_access_code():
    config = PrinterConfig(mode="lan", ip="10.0.0.1", access_code=None, serial="SN123")
    with pytest.raises(ValueError, match="access_code"):
        send_print(Path("dummy.gcode"), config)


def test_send_print_lan_missing_serial():
    config = PrinterConfig(mode="lan", ip="10.0.0.1", access_code="abc", serial=None)
    with pytest.raises(ValueError, match="serial"):
        send_print(Path("dummy.gcode"), config)


def test_send_print_lan_dispatches(tmp_path):
    """Test that LAN mode dispatches to _send_lan with correct args."""
    gcode = tmp_path / "test.gcode"
    gcode.write_text("; test gcode")
    config = PrinterConfig(mode="lan", ip="10.0.0.1", access_code="abc", serial="SN123")

    with patch("fabprint.printer._send_lan") as mock_send:
        send_print(gcode, config, dry_run=False)
        mock_send.assert_called_once_with(
            gcode,
            ip="10.0.0.1",
            access_code="abc",
            serial="SN123",
            dry_run=False,
            upload_only=False,
        )


def test_parse_gcode_metadata(tmp_path):
    gcode = tmp_path / "test.gcode"
    gcode.write_text("; total estimated time: 1h 7m 32s\nG28\n; total filament used [g] = 14.02\n")
    stats = parse_gcode_metadata(gcode)
    assert stats["print_time"] == "1h 7m 32s"
    assert stats["filament_g"] == 14.02
    assert stats["print_time_secs"] == 4052


def test_wrap_gcode_3mf(tmp_path):
    """Test that wrap_gcode_3mf creates a valid zip with expected structure."""
    import zipfile

    gcode = tmp_path / "test.gcode"
    gcode.write_text("; total estimated time: 2m 30s\nG28\n; total filament used [g] = 1.50\n")

    result = wrap_gcode_3mf(gcode)
    assert result.suffix == ".3mf"
    assert result.exists()

    with zipfile.ZipFile(result, "r") as zf:
        names = zf.namelist()
        assert "Metadata/plate_1.gcode" in names
        assert "Metadata/plate_1.gcode.md5" in names
        assert "3D/3dmodel.model" in names
        assert "[Content_Types].xml" in names
        assert "Metadata/model_settings.config" in names
        assert "Metadata/slice_info.config" in names

        # Check gcode content is preserved
        assert zf.read("Metadata/plate_1.gcode") == gcode.read_bytes()

        # Check slice_info has correct stats
        slice_info = zf.read("Metadata/slice_info.config").decode()
        assert 'value="150"' in slice_info  # 2m30s = 150 secs
        assert 'value="1.50"' in slice_info


def test_wrap_gcode_3mf_custom_output(tmp_path):
    gcode = tmp_path / "test.gcode"
    gcode.write_text("G28\n")
    out = tmp_path / "custom.gcode.3mf"
    result = wrap_gcode_3mf(gcode, output_path=out)
    assert result == out
    assert out.exists()
