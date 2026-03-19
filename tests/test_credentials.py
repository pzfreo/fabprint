"""Tests for fabprint credentials and setup command."""

import json
import sys
import tomllib

import pytest

from fabprint.credentials import (
    cloud_token_json,
    list_printers,
    load_cloud_credentials,
    mask_serial,
    save_cloud_credentials,
    setup_printer,
)


class TestMaskSerial:
    def test_long_serial(self):
        assert mask_serial("01P00A451601106") == "***********1106"

    def test_short_serial(self):
        assert mask_serial("AB") == "AB"

    def test_exactly_four(self):
        assert mask_serial("ABCD") == "ABCD"

    def test_five_chars(self):
        assert mask_serial("ABCDE") == "*BCDE"


def _mock_ui_inputs(monkeypatch, inputs):
    """Mock ui prompt functions with an iterator of responses."""
    it = iter(inputs)

    def next_str(prompt, default=None):
        try:
            val = next(it)
        except StopIteration:
            return default or ""
        return val if val != "" else (default or "")

    def next_int(prompt, default=0):
        try:
            val = next(it)
        except StopIteration:
            return default
        return int(val) if val != "" else default

    def next_yn(prompt, default=True):
        try:
            val = next(it)
        except StopIteration:
            return default
        if val == "":
            return default
        return str(val).lower().startswith("y")

    monkeypatch.setattr("fabprint.ui.prompt_str", next_str)
    monkeypatch.setattr("fabprint.ui.prompt_int", next_int)
    monkeypatch.setattr("fabprint.ui.prompt_yn", next_yn)
    monkeypatch.setattr("fabprint.ui.prompt_password", lambda prompt: next_str(prompt))
    # Silence Rich output
    monkeypatch.setattr("fabprint.ui.heading", lambda text: None)
    monkeypatch.setattr("fabprint.ui.success", lambda text: None)
    monkeypatch.setattr("fabprint.ui.warn", lambda text: None)
    monkeypatch.setattr("fabprint.ui.error", lambda text: None)
    monkeypatch.setattr("fabprint.ui.info", lambda text: None)
    monkeypatch.setattr("fabprint.ui.choice_table", lambda items, columns: None)
    monkeypatch.setattr("fabprint.ui.preview_toml", lambda text: None)
    monkeypatch.setattr("fabprint.ui._show_options", lambda *a, **kw: None)


class TestSetupPrinter:
    def test_creates_bambu_lan(self, tmp_path, monkeypatch):
        cred_path = tmp_path / "credentials.toml"
        monkeypatch.setattr("fabprint.credentials._credentials_path", lambda: cred_path)

        # name, type choice, ip, access_code, serial
        _mock_ui_inputs(monkeypatch, ["workshop", "1", "192.168.1.100", "12345678", "01P00A123"])

        setup_printer()

        assert cred_path.exists()
        if sys.platform != "win32":
            assert cred_path.stat().st_mode & 0o777 == 0o600
        with open(cred_path, "rb") as f:
            data = tomllib.load(f)
        assert data["printers"]["workshop"]["type"] == "bambu-lan"
        assert data["printers"]["workshop"]["ip"] == "192.168.1.100"
        assert data["printers"]["workshop"]["access_code"] == "12345678"
        assert data["printers"]["workshop"]["serial"] == "01P00A123"

    def test_creates_moonraker(self, tmp_path, monkeypatch):
        cred_path = tmp_path / "credentials.toml"
        monkeypatch.setattr("fabprint.credentials._credentials_path", lambda: cred_path)

        # name, type choice (3=moonraker), url, api_key (optional)
        _mock_ui_inputs(monkeypatch, ["voron", "3", "http://voron.local:7125", "my-key"])

        setup_printer()

        with open(cred_path, "rb") as f:
            data = tomllib.load(f)
        assert data["printers"]["voron"]["type"] == "moonraker"
        assert data["printers"]["voron"]["url"] == "http://voron.local:7125"
        assert data["printers"]["voron"]["api_key"] == "my-key"

    def test_adds_to_existing(self, tmp_path, monkeypatch):
        cred_path = tmp_path / "credentials.toml"
        cred_path.write_text('[printers.old]\ntype = "bambu-lan"\nip = "10.0.0.1"\n')
        monkeypatch.setattr("fabprint.credentials._credentials_path", lambda: cred_path)

        # name, type (default=1), ip, access_code, serial
        _mock_ui_inputs(monkeypatch, ["new-printer", "1", "192.168.1.50", "99887766", "ABC123"])

        setup_printer()

        with open(cred_path, "rb") as f:
            data = tomllib.load(f)
        # Old printer preserved
        assert data["printers"]["old"]["ip"] == "10.0.0.1"
        # New printer added
        assert data["printers"]["new-printer"]["type"] == "bambu-lan"
        assert data["printers"]["new-printer"]["serial"] == "ABC123"

    def test_aborts_on_empty_name(self, tmp_path, monkeypatch):
        cred_path = tmp_path / "credentials.toml"
        monkeypatch.setattr("fabprint.credentials._credentials_path", lambda: cred_path)

        _mock_ui_inputs(monkeypatch, [""])

        setup_printer()

        assert not cred_path.exists()

    def test_creates_parent_dirs(self, tmp_path, monkeypatch):
        cred_path = tmp_path / "deep" / "nested" / "credentials.toml"
        monkeypatch.setattr("fabprint.credentials._credentials_path", lambda: cred_path)

        _mock_ui_inputs(monkeypatch, ["p1", "1", "10.0.0.1", "12345678", "SN001"])

        setup_printer()

        assert cred_path.exists()

    def test_cli_setup(self, tmp_path, monkeypatch):
        """CLI wiring works."""
        from fabprint.cli import main

        cred_path = tmp_path / "credentials.toml"
        monkeypatch.setattr("fabprint.credentials._credentials_path", lambda: cred_path)

        _mock_ui_inputs(monkeypatch, ["test", "1", "1.2.3.4", "99887766", "SN001"])

        main(["setup"])

        assert cred_path.exists()
        with open(cred_path, "rb") as f:
            data = tomllib.load(f)
        assert data["printers"]["test"]["type"] == "bambu-lan"
        assert data["printers"]["test"]["ip"] == "1.2.3.4"


class TestListPrinters:
    def test_lists_all(self, tmp_path, monkeypatch):
        cred_path = tmp_path / "credentials.toml"
        cred_path.write_text(
            '[printers.workshop]\ntype = "bambu-lan"\nip = "10.0.0.1"\n\n'
            '[printers.voron]\ntype = "moonraker"\nurl = "http://voron:7125"\n'
        )
        monkeypatch.setattr("fabprint.credentials._credentials_path", lambda: cred_path)

        printers = list_printers()
        assert "workshop" in printers
        assert "voron" in printers
        assert printers["workshop"]["type"] == "bambu-lan"
        assert printers["voron"]["type"] == "moonraker"

    def test_returns_empty_when_no_file(self, tmp_path, monkeypatch):
        cred_path = tmp_path / "credentials.toml"
        monkeypatch.setattr("fabprint.credentials._credentials_path", lambda: cred_path)

        assert list_printers() == {}


class TestCloudCredentials:
    def test_save_and_load(self, tmp_path, monkeypatch):
        cred_path = tmp_path / "credentials.toml"
        monkeypatch.setattr("fabprint.credentials._credentials_path", lambda: cred_path)

        save_cloud_credentials(
            token="tok123",
            refresh_token="ref456",
            email="user@test.com",
            uid="9999",
        )

        cloud = load_cloud_credentials()
        assert cloud["token"] == "tok123"
        assert cloud["refresh_token"] == "ref456"
        assert cloud["email"] == "user@test.com"
        assert cloud["uid"] == "9999"

    def test_load_returns_none_when_missing(self, tmp_path, monkeypatch):
        cred_path = tmp_path / "credentials.toml"
        monkeypatch.setattr("fabprint.credentials._credentials_path", lambda: cred_path)

        assert load_cloud_credentials() is None

    def test_preserves_printers(self, tmp_path, monkeypatch):
        """Saving cloud creds should not clobber existing printers."""
        cred_path = tmp_path / "credentials.toml"
        cred_path.write_text('[printers.workshop]\ntype = "bambu-lan"\nip = "10.0.0.1"\n')
        monkeypatch.setattr("fabprint.credentials._credentials_path", lambda: cred_path)

        save_cloud_credentials(
            token="tok",
            refresh_token="ref",
            email="user@test.com",
            uid="1",
        )

        with open(cred_path, "rb") as f:
            data = tomllib.load(f)
        assert data["cloud"]["token"] == "tok"
        assert data["printers"]["workshop"]["ip"] == "10.0.0.1"


class TestCloudTokenJson:
    def test_generates_temp_file(self, tmp_path, monkeypatch):
        cred_path = tmp_path / "credentials.toml"
        cred_path.write_text(
            '[cloud]\ntoken = "mytoken"\nrefresh_token = "myrefresh"\n'
            'email = "a@b.com"\nuid = "123"\n'
        )
        monkeypatch.setattr("fabprint.credentials._credentials_path", lambda: cred_path)

        with cloud_token_json() as path:
            assert path.exists()
            data = json.loads(path.read_text())
            assert data["token"] == "mytoken"
            assert data["refreshToken"] == "myrefresh"
            assert data["email"] == "a@b.com"
            assert data["uid"] == "123"

        # Cleaned up after context manager exits
        assert not path.exists()

    def test_raises_without_cloud_creds(self, tmp_path, monkeypatch):
        cred_path = tmp_path / "credentials.toml"
        monkeypatch.setattr("fabprint.credentials._credentials_path", lambda: cred_path)

        from fabprint import FabprintError

        with pytest.raises(FabprintError, match="No cloud credentials"):
            with cloud_token_json():
                pass
