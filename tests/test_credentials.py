"""Tests for fabprint credentials setup command."""

import tomllib

from fabprint.credentials import setup_credentials


class TestSetupCredentials:
    def test_creates_new_file(self, tmp_path, monkeypatch):
        cred_path = tmp_path / "credentials.toml"
        monkeypatch.setattr("fabprint.credentials._credentials_path", lambda: cred_path)

        inputs = iter(["workshop", "192.168.1.100", "12345678", "01P00A123"])
        monkeypatch.setattr("builtins.input", lambda _="": next(inputs))

        setup_credentials()

        assert cred_path.exists()
        assert cred_path.stat().st_mode & 0o777 == 0o600
        with open(cred_path, "rb") as f:
            data = tomllib.load(f)
        assert data["printers"]["workshop"]["ip"] == "192.168.1.100"
        assert data["printers"]["workshop"]["access_code"] == "12345678"
        assert data["printers"]["workshop"]["serial"] == "01P00A123"

    def test_adds_to_existing(self, tmp_path, monkeypatch):
        cred_path = tmp_path / "credentials.toml"
        cred_path.write_text('[printers.old]\nip = "10.0.0.1"\n')
        monkeypatch.setattr("fabprint.credentials._credentials_path", lambda: cred_path)

        inputs = iter(["new-printer", "192.168.1.50", "", "ABC123"])
        monkeypatch.setattr("builtins.input", lambda _="": next(inputs))

        setup_credentials()

        with open(cred_path, "rb") as f:
            data = tomllib.load(f)
        # Old printer preserved
        assert data["printers"]["old"]["ip"] == "10.0.0.1"
        # New printer added
        assert data["printers"]["new-printer"]["serial"] == "ABC123"
        assert "access_code" not in data["printers"]["new-printer"]

    def test_aborts_on_empty_name(self, tmp_path, monkeypatch, capsys):
        cred_path = tmp_path / "credentials.toml"
        monkeypatch.setattr("fabprint.credentials._credentials_path", lambda: cred_path)

        monkeypatch.setattr("builtins.input", lambda _="": "")

        setup_credentials()

        assert not cred_path.exists()
        assert "Aborted" in capsys.readouterr().out

    def test_aborts_on_no_fields(self, tmp_path, monkeypatch, capsys):
        cred_path = tmp_path / "credentials.toml"
        monkeypatch.setattr("fabprint.credentials._credentials_path", lambda: cred_path)

        inputs = iter(["my-printer", "", "", ""])
        monkeypatch.setattr("builtins.input", lambda _="": next(inputs))

        setup_credentials()

        assert not cred_path.exists()
        assert "No credentials" in capsys.readouterr().out

    def test_creates_parent_dirs(self, tmp_path, monkeypatch):
        cred_path = tmp_path / "deep" / "nested" / "credentials.toml"
        monkeypatch.setattr("fabprint.credentials._credentials_path", lambda: cred_path)

        inputs = iter(["p1", "10.0.0.1", "", ""])
        monkeypatch.setattr("builtins.input", lambda _="": next(inputs))

        setup_credentials()

        assert cred_path.exists()

    def test_cli_credentials(self, tmp_path, monkeypatch):
        """CLI wiring works."""
        from fabprint.cli import main

        cred_path = tmp_path / "credentials.toml"
        monkeypatch.setattr("fabprint.credentials._credentials_path", lambda: cred_path)

        inputs = iter(["test", "1.2.3.4", "99887766", ""])
        monkeypatch.setattr("builtins.input", lambda _="": next(inputs))

        main(["credentials"])

        assert cred_path.exists()
        with open(cred_path, "rb") as f:
            data = tomllib.load(f)
        assert data["printers"]["test"]["ip"] == "1.2.3.4"
