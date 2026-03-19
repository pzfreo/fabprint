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

    def test_cleanup_on_exception(self, tmp_path, monkeypatch):
        """Temp file is cleaned up even if an exception occurs inside the context."""
        cred_path = tmp_path / "credentials.toml"
        cred_path.write_text(
            '[cloud]\ntoken = "tok"\nrefresh_token = "ref"\nemail = "a@b.com"\nuid = "1"\n'
        )
        monkeypatch.setattr("fabprint.credentials._credentials_path", lambda: cred_path)

        temp_path = None
        with pytest.raises(RuntimeError):
            with cloud_token_json() as path:
                temp_path = path
                assert path.exists()
                raise RuntimeError("deliberate error")

        assert temp_path is not None
        assert not temp_path.exists()

    def test_file_permissions(self, tmp_path, monkeypatch):
        """Temp JSON file should have 0o600 permissions."""
        cred_path = tmp_path / "credentials.toml"
        cred_path.write_text(
            '[cloud]\ntoken = "tok"\nrefresh_token = "ref"\nemail = "a@b.com"\nuid = "1"\n'
        )
        monkeypatch.setattr("fabprint.credentials._credentials_path", lambda: cred_path)

        if sys.platform != "win32":
            with cloud_token_json() as path:
                assert path.stat().st_mode & 0o777 == 0o600


class TestCredentialsPath:
    def test_env_var_override(self, tmp_path, monkeypatch):
        """FABPRINT_CREDENTIALS env var overrides default path."""
        from fabprint.credentials import _credentials_path

        custom_path = tmp_path / "custom_creds.toml"
        monkeypatch.setenv("FABPRINT_CREDENTIALS", str(custom_path))
        assert _credentials_path() == custom_path

    def test_windows_path(self, monkeypatch):
        """On Windows, credentials go to AppData/Roaming."""
        from fabprint.credentials import _credentials_path

        monkeypatch.delenv("FABPRINT_CREDENTIALS", raising=False)
        monkeypatch.setattr("sys.platform", "win32")
        path = _credentials_path()
        assert "AppData" in str(path) or "Roaming" in str(path)

    def test_linux_path(self, monkeypatch):
        """On Linux, credentials go to .config/fabprint."""
        from fabprint.credentials import _credentials_path

        monkeypatch.delenv("FABPRINT_CREDENTIALS", raising=False)
        monkeypatch.setattr("sys.platform", "linux")
        path = _credentials_path()
        assert ".config/fabprint" in str(path)


class TestLoadPrinterCredentials:
    def test_env_var_overrides(self, tmp_path, monkeypatch):
        """Environment variables override file credentials."""
        from fabprint.credentials import load_printer_credentials

        cred_path = tmp_path / "credentials.toml"
        cred_path.write_text(
            '[printers.test]\ntype = "bambu-lan"\nip = "10.0.0.1"\n'
            'access_code = "filecode"\nserial = "FILESERIAL"\n'
        )
        monkeypatch.setattr("fabprint.credentials._credentials_path", lambda: cred_path)
        monkeypatch.setenv("BAMBU_PRINTER_IP", "192.168.1.99")
        monkeypatch.setenv("BAMBU_ACCESS_CODE", "envcode")
        monkeypatch.setenv("BAMBU_SERIAL", "ENVSERIAL")

        creds = load_printer_credentials("test")
        assert creds["ip"] == "192.168.1.99"
        assert creds["access_code"] == "envcode"
        assert creds["serial"] == "ENVSERIAL"
        assert creds["type"] == "bambu-lan"

    def test_no_name_returns_env_only(self, monkeypatch):
        """With name=None, only env vars are returned."""
        from fabprint.credentials import load_printer_credentials

        monkeypatch.setenv("BAMBU_PRINTER_IP", "1.2.3.4")
        monkeypatch.setenv("BAMBU_ACCESS_CODE", "code")
        monkeypatch.setenv("BAMBU_SERIAL", "SN123")

        creds = load_printer_credentials(None)
        assert creds["ip"] == "1.2.3.4"
        assert creds["access_code"] == "code"
        assert creds["serial"] == "SN123"
        assert creds["type"] is None

    def test_missing_credentials_file_raises(self, tmp_path, monkeypatch):
        """Raises FabprintError when credentials file doesn't exist."""
        from fabprint import FabprintError
        from fabprint.credentials import load_printer_credentials

        cred_path = tmp_path / "nonexistent" / "credentials.toml"
        monkeypatch.setattr("fabprint.credentials._credentials_path", lambda: cred_path)

        with pytest.raises(FabprintError, match="Credentials file not found"):
            load_printer_credentials("myprinter")

    def test_printer_not_in_file_raises(self, tmp_path, monkeypatch):
        """Raises FabprintError when named printer isn't in the file."""
        from fabprint import FabprintError
        from fabprint.credentials import load_printer_credentials

        cred_path = tmp_path / "credentials.toml"
        cred_path.write_text('[printers.workshop]\ntype = "bambu-lan"\nip = "10.0.0.1"\n')
        monkeypatch.setattr("fabprint.credentials._credentials_path", lambda: cred_path)

        with pytest.raises(FabprintError, match="Printer 'missing' not found"):
            load_printer_credentials("missing")


class TestMaskSerialEdgeCases:
    def test_empty_string(self):
        assert mask_serial("") == ""

    def test_one_char(self):
        assert mask_serial("X") == "X"

    def test_three_chars(self):
        assert mask_serial("ABC") == "ABC"

    def test_eight_chars(self):
        assert mask_serial("12345678") == "****5678"


class TestSetupPrinterBambuCloud:
    def test_bambu_cloud_without_login(self, tmp_path, monkeypatch):
        """Bambu cloud setup with no existing token and user skips login."""
        cred_path = tmp_path / "credentials.toml"
        monkeypatch.setattr("fabprint.credentials._credentials_path", lambda: cred_path)

        # name, type=2 (bambu-cloud), skip login (n), serial
        _mock_ui_inputs(monkeypatch, ["cloudprinter", "2", "n", "SN_CLOUD_001"])
        # Mock _pick_cloud_printer to return None (no token)
        monkeypatch.setattr("fabprint.credentials._pick_cloud_printer", lambda cloud: None)

        setup_printer()

        assert cred_path.exists()
        with open(cred_path, "rb") as f:
            data = tomllib.load(f)
        assert data["printers"]["cloudprinter"]["type"] == "bambu-cloud"
        assert data["printers"]["cloudprinter"]["serial"] == "SN_CLOUD_001"

    def test_moonraker_no_api_key(self, tmp_path, monkeypatch):
        """Moonraker setup without optional api_key."""
        cred_path = tmp_path / "credentials.toml"
        monkeypatch.setattr("fabprint.credentials._credentials_path", lambda: cred_path)

        # name, type=3 (moonraker), url, api_key empty (optional)
        _mock_ui_inputs(monkeypatch, ["klipper", "3", "http://klipper.local:7125", ""])

        setup_printer()

        with open(cred_path, "rb") as f:
            data = tomllib.load(f)
        assert data["printers"]["klipper"]["type"] == "moonraker"
        assert data["printers"]["klipper"]["url"] == "http://klipper.local:7125"
        assert "api_key" not in data["printers"]["klipper"]
