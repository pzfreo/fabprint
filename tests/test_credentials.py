"""Tests for fabprint credentials and setup command."""

import json
import sys
import tomllib

import pytest

from fabprint.credentials import (
    cloud_token_json,
    list_printers,
    load_cloud_credentials,
    save_cloud_credentials,
    setup_printer,
)


class TestSetupPrinter:
    def test_creates_bambu_lan(self, tmp_path, monkeypatch):
        cred_path = tmp_path / "credentials.toml"
        monkeypatch.setattr("fabprint.credentials._credentials_path", lambda: cred_path)

        # name, type choice, ip, access_code, serial
        inputs = iter(["workshop", "1", "192.168.1.100", "12345678", "01P00A123"])
        monkeypatch.setattr("builtins.input", lambda _="": next(inputs))

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
        inputs = iter(["voron", "3", "http://voron.local:7125", "my-key"])
        monkeypatch.setattr("builtins.input", lambda _="": next(inputs))

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
        inputs = iter(["new-printer", "", "192.168.1.50", "99887766", "ABC123"])
        monkeypatch.setattr("builtins.input", lambda _="": next(inputs))

        setup_printer()

        with open(cred_path, "rb") as f:
            data = tomllib.load(f)
        # Old printer preserved
        assert data["printers"]["old"]["ip"] == "10.0.0.1"
        # New printer added
        assert data["printers"]["new-printer"]["type"] == "bambu-lan"
        assert data["printers"]["new-printer"]["serial"] == "ABC123"

    def test_aborts_on_empty_name(self, tmp_path, monkeypatch, capsys):
        cred_path = tmp_path / "credentials.toml"
        monkeypatch.setattr("fabprint.credentials._credentials_path", lambda: cred_path)

        monkeypatch.setattr("builtins.input", lambda _="": "")

        setup_printer()

        assert not cred_path.exists()
        assert "Aborted" in capsys.readouterr().out

    def test_creates_parent_dirs(self, tmp_path, monkeypatch):
        cred_path = tmp_path / "deep" / "nested" / "credentials.toml"
        monkeypatch.setattr("fabprint.credentials._credentials_path", lambda: cred_path)

        inputs = iter(["p1", "1", "10.0.0.1", "12345678", "SN001"])
        monkeypatch.setattr("builtins.input", lambda _="": next(inputs))

        setup_printer()

        assert cred_path.exists()

    def test_cli_setup(self, tmp_path, monkeypatch):
        """CLI wiring works."""
        from fabprint.cli import main

        cred_path = tmp_path / "credentials.toml"
        monkeypatch.setattr("fabprint.credentials._credentials_path", lambda: cred_path)

        inputs = iter(["test", "1", "1.2.3.4", "99887766", "SN001"])
        monkeypatch.setattr("builtins.input", lambda _="": next(inputs))

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
