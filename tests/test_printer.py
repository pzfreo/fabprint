"""Tests for printer module."""

from pathlib import Path
from unittest.mock import patch

import pytest

from fabprint.config import PrinterConfig
from fabprint.printer import _resolve_credentials, send_print


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
            gcode, ip="10.0.0.1", access_code="abc", serial="SN123", dry_run=True, upload_only=False
        )


def test_send_print_cloud_dry_run(tmp_path, capsys, monkeypatch):
    gcode = tmp_path / "test.gcode"
    gcode.write_text("; test gcode")
    monkeypatch.setenv("BAMBU_EMAIL", "user@test.com")
    monkeypatch.setenv("BAMBU_PASSWORD", "secret")
    config = PrinterConfig(mode="cloud")
    with patch("fabprint.printer._send_cloud") as mock_send:
        send_print(gcode, config, dry_run=True)
        mock_send.assert_called_once_with(
            gcode,
            email="user@test.com",
            password="secret",
            serial=None,
            dry_run=True,
            upload_only=False,
        )


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


def test_send_print_cloud_missing_email(monkeypatch):
    monkeypatch.delenv("BAMBU_EMAIL", raising=False)
    monkeypatch.delenv("BAMBU_PASSWORD", raising=False)
    config = PrinterConfig(mode="cloud")
    with pytest.raises(ValueError, match="BAMBU_EMAIL"):
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


def test_send_print_cloud_dispatches(tmp_path, monkeypatch):
    """Test that cloud mode dispatches to _send_cloud with correct args."""
    gcode = tmp_path / "test.gcode"
    gcode.write_text("; test gcode")
    monkeypatch.setenv("BAMBU_EMAIL", "user@test.com")
    monkeypatch.setenv("BAMBU_PASSWORD", "secret")
    config = PrinterConfig(mode="cloud", serial="SN123")

    with patch("fabprint.printer._send_cloud") as mock_send:
        send_print(gcode, config, dry_run=False)
        mock_send.assert_called_once_with(
            gcode,
            email="user@test.com",
            password="secret",
            serial="SN123",
            dry_run=False,
            upload_only=False,
        )
