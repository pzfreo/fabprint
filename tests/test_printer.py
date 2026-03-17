"""Tests for printer module."""

from pathlib import Path
from unittest.mock import patch

import pytest

from fabprint.config import PrinterConfig
from fabprint.credentials import load_printer_credentials
from fabprint.gcode import parse_gcode_metadata
from fabprint.printer import (
    send_print,
    wrap_gcode_3mf,
)


def _write_credentials(tmp_path, content):
    """Write a credentials.toml and return its path."""
    cred_path = tmp_path / "credentials.toml"
    cred_path.write_text(content)
    return cred_path


def test_load_credentials_from_file(tmp_path, monkeypatch):
    cred_path = _write_credentials(
        tmp_path,
        """
[printers.workshop]
type = "bambu-lan"
ip = "10.0.0.1"
access_code = "abc"
serial = "SN123"
""",
    )
    monkeypatch.setenv("FABPRINT_CREDENTIALS", str(cred_path))
    monkeypatch.delenv("BAMBU_PRINTER_IP", raising=False)
    monkeypatch.delenv("BAMBU_ACCESS_CODE", raising=False)
    monkeypatch.delenv("BAMBU_SERIAL", raising=False)
    creds = load_printer_credentials("workshop")
    assert creds["type"] == "bambu-lan"
    assert creds["ip"] == "10.0.0.1"
    assert creds["access_code"] == "abc"
    assert creds["serial"] == "SN123"


def test_load_credentials_env_overrides(tmp_path, monkeypatch):
    cred_path = _write_credentials(
        tmp_path,
        """
[printers.workshop]
type = "bambu-lan"
ip = "10.0.0.1"
access_code = "abc"
serial = "SN123"
""",
    )
    monkeypatch.setenv("FABPRINT_CREDENTIALS", str(cred_path))
    monkeypatch.setenv("BAMBU_PRINTER_IP", "192.168.1.99")
    monkeypatch.setenv("BAMBU_ACCESS_CODE", "override_code")
    monkeypatch.setenv("BAMBU_SERIAL", "OVERRIDE_SN")
    creds = load_printer_credentials("workshop")
    assert creds["ip"] == "192.168.1.99"
    assert creds["access_code"] == "override_code"
    assert creds["serial"] == "OVERRIDE_SN"


def test_load_credentials_no_name(monkeypatch):
    monkeypatch.delenv("BAMBU_PRINTER_IP", raising=False)
    monkeypatch.delenv("BAMBU_ACCESS_CODE", raising=False)
    monkeypatch.delenv("BAMBU_SERIAL", raising=False)
    creds = load_printer_credentials(None)
    assert creds["type"] is None
    assert creds["ip"] is None


def test_load_credentials_missing_file(tmp_path, monkeypatch):
    monkeypatch.setenv("FABPRINT_CREDENTIALS", str(tmp_path / "nonexistent.toml"))
    from fabprint import FabprintError

    with pytest.raises(FabprintError, match="not found"):
        load_printer_credentials("workshop")


def test_load_credentials_missing_printer(tmp_path, monkeypatch):
    cred_path = _write_credentials(
        tmp_path,
        """
[printers.other]
type = "bambu-lan"
ip = "10.0.0.1"
""",
    )
    monkeypatch.setenv("FABPRINT_CREDENTIALS", str(cred_path))
    from fabprint import FabprintError

    with pytest.raises(FabprintError, match="workshop.*not found"):
        load_printer_credentials("workshop")


def test_send_print_lan_dry_run(tmp_path, monkeypatch, capsys):
    cred_path = _write_credentials(
        tmp_path,
        """
[printers.workshop]
type = "bambu-lan"
ip = "10.0.0.1"
access_code = "abc"
serial = "SN123"
""",
    )
    monkeypatch.setenv("FABPRINT_CREDENTIALS", str(cred_path))
    monkeypatch.delenv("BAMBU_PRINTER_IP", raising=False)
    monkeypatch.delenv("BAMBU_ACCESS_CODE", raising=False)
    monkeypatch.delenv("BAMBU_SERIAL", raising=False)
    gcode = tmp_path / "test.gcode"
    gcode.write_text("; test gcode")
    config = PrinterConfig(name="workshop")
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


def test_send_print_cloud_bridge_dispatches(tmp_path, monkeypatch):
    """Test that bambu-cloud type dispatches to _send_cloud_bridge."""
    cred_path = _write_credentials(
        tmp_path,
        """
[printers.workshop]
type = "bambu-cloud"
serial = "SN123"
""",
    )
    monkeypatch.setenv("FABPRINT_CREDENTIALS", str(cred_path))
    monkeypatch.delenv("BAMBU_SERIAL", raising=False)
    gcode = tmp_path / "test.gcode"
    gcode.write_text("; test gcode")
    config = PrinterConfig(name="workshop")

    with patch("fabprint.printer._send_cloud_bridge") as mock_send:
        send_print(gcode, config, dry_run=True)
        mock_send.assert_called_once_with(
            gcode, serial="SN123", dry_run=True, verbose=False, skip_ams_mapping=False
        )


def test_send_print_moonraker_dispatches(tmp_path, monkeypatch):
    """Test that moonraker type dispatches to _send_moonraker."""
    cred_path = _write_credentials(
        tmp_path,
        """
[printers.voron]
type = "moonraker"
url = "http://voron.local:7125"
api_key = "test-key"
""",
    )
    monkeypatch.setenv("FABPRINT_CREDENTIALS", str(cred_path))
    gcode = tmp_path / "test.gcode"
    gcode.write_text("; test gcode")
    config = PrinterConfig(name="voron")

    with patch("fabprint.printer._send_moonraker") as mock_send:
        send_print(gcode, config, dry_run=True)
        mock_send.assert_called_once_with(
            gcode,
            url="http://voron.local:7125",
            api_key="test-key",
            dry_run=True,
            upload_only=False,
        )


def test_send_print_lan_missing_ip(tmp_path, monkeypatch):
    cred_path = _write_credentials(
        tmp_path,
        """
[printers.workshop]
type = "bambu-lan"
access_code = "abc"
serial = "SN123"
""",
    )
    monkeypatch.setenv("FABPRINT_CREDENTIALS", str(cred_path))
    monkeypatch.delenv("BAMBU_PRINTER_IP", raising=False)
    config = PrinterConfig(name="workshop")
    with pytest.raises(ValueError, match="ip"):
        send_print(Path("dummy.gcode"), config)


def test_send_print_lan_missing_access_code(tmp_path, monkeypatch):
    cred_path = _write_credentials(
        tmp_path,
        """
[printers.workshop]
type = "bambu-lan"
ip = "10.0.0.1"
serial = "SN123"
""",
    )
    monkeypatch.setenv("FABPRINT_CREDENTIALS", str(cred_path))
    monkeypatch.delenv("BAMBU_ACCESS_CODE", raising=False)
    config = PrinterConfig(name="workshop")
    with pytest.raises(ValueError, match="access_code"):
        send_print(Path("dummy.gcode"), config)


def test_send_print_lan_missing_serial(tmp_path, monkeypatch):
    cred_path = _write_credentials(
        tmp_path,
        """
[printers.workshop]
type = "bambu-lan"
ip = "10.0.0.1"
access_code = "abc"
""",
    )
    monkeypatch.setenv("FABPRINT_CREDENTIALS", str(cred_path))
    monkeypatch.delenv("BAMBU_SERIAL", raising=False)
    config = PrinterConfig(name="workshop")
    with pytest.raises(ValueError, match="serial"):
        send_print(Path("dummy.gcode"), config)


def test_send_print_no_type(tmp_path, monkeypatch):
    """Printer without type in credentials should error."""
    cred_path = _write_credentials(
        tmp_path,
        """
[printers.workshop]
ip = "10.0.0.1"
""",
    )
    monkeypatch.setenv("FABPRINT_CREDENTIALS", str(cred_path))
    monkeypatch.delenv("BAMBU_PRINTER_IP", raising=False)
    config = PrinterConfig(name="workshop")
    with pytest.raises(ValueError, match="no 'type'"):
        send_print(Path("dummy.gcode"), config)


def test_send_print_lan_dispatches(tmp_path, monkeypatch):
    """Test that bambu-lan dispatches to _send_lan with correct args."""
    cred_path = _write_credentials(
        tmp_path,
        """
[printers.workshop]
type = "bambu-lan"
ip = "10.0.0.1"
access_code = "abc"
serial = "SN123"
""",
    )
    monkeypatch.setenv("FABPRINT_CREDENTIALS", str(cred_path))
    monkeypatch.delenv("BAMBU_PRINTER_IP", raising=False)
    monkeypatch.delenv("BAMBU_ACCESS_CODE", raising=False)
    monkeypatch.delenv("BAMBU_SERIAL", raising=False)
    gcode = tmp_path / "test.gcode"
    gcode.write_text("; test gcode")
    config = PrinterConfig(name="workshop")

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
