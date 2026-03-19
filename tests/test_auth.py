"""Tests for fabprint.auth — login flows, profile, device discovery."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from fabprint import FabprintError
from fabprint.auth import (
    API_BASE,
    SLICER_HEADERS,
    _get_devices,
    _get_user_profile,
    _login,
    _request_verification_code,
    _show_devices,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_response(
    status_code: int = 200,
    json_data: dict | None = None,
    raise_for_status_error: bool = False,
) -> MagicMock:
    """Build a fake requests.Response."""
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    if raise_for_status_error:
        resp.raise_for_status.side_effect = requests.HTTPError(
            response=resp,
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


def _silence_ui(monkeypatch):
    """Silence all UI output helpers."""
    for fn in ("heading", "success", "warn", "error", "info"):
        monkeypatch.setattr(f"fabprint.ui.{fn}", lambda text: None)
    monkeypatch.setattr("fabprint.ui.choice_table", lambda items, columns, **kw: None)
    monkeypatch.setattr("fabprint.ui.console", MagicMock())


# ---------------------------------------------------------------------------
# _request_verification_code
# ---------------------------------------------------------------------------


class TestRequestVerificationCode:
    def test_sends_email_code(self, monkeypatch):
        _silence_ui(monkeypatch)
        resp = _mock_response(200)
        with patch("fabprint.auth.requests.post", return_value=resp) as mock_post:
            _request_verification_code("user@example.com")
            mock_post.assert_called_once_with(
                f"{API_BASE}/v1/user-service/user/sendemail/code",
                headers=SLICER_HEADERS,
                json={"email": "user@example.com", "type": "codeLogin"},
            )
            resp.raise_for_status.assert_called_once()

    def test_raises_on_http_error(self, monkeypatch):
        _silence_ui(monkeypatch)
        resp = _mock_response(500, raise_for_status_error=True)
        with patch("fabprint.auth.requests.post", return_value=resp):
            with pytest.raises(requests.HTTPError):
                _request_verification_code("user@example.com")


# ---------------------------------------------------------------------------
# _login — password flow
# ---------------------------------------------------------------------------


class TestLoginPasswordFlow:
    def test_direct_password_success(self, monkeypatch):
        _silence_ui(monkeypatch)
        resp = _mock_response(
            200,
            json_data={
                "accessToken": "tok123",
                "refreshToken": "ref456",
                "loginType": "password",
            },
        )
        with patch("fabprint.auth.requests.post", return_value=resp):
            token, refresh = _login("user@example.com", "secret")
            assert token == "tok123"
            assert refresh == "ref456"

    def test_password_login_http_error(self, monkeypatch):
        _silence_ui(monkeypatch)
        resp = _mock_response(401, raise_for_status_error=True)
        with patch("fabprint.auth.requests.post", return_value=resp):
            with pytest.raises(requests.HTTPError):
                _login("user@example.com", "wrong")

    def test_no_token_and_no_special_flow_raises(self, monkeypatch):
        _silence_ui(monkeypatch)
        resp = _mock_response(
            200,
            json_data={"loginType": "unknown", "message": "something odd"},
        )
        with patch("fabprint.auth.requests.post", return_value=resp):
            with pytest.raises(FabprintError, match="Login failed"):
                _login("user@example.com", "pw")

    def test_missing_refresh_token_defaults_empty(self, monkeypatch):
        _silence_ui(monkeypatch)
        resp = _mock_response(
            200,
            json_data={"accessToken": "tok", "loginType": "password"},
        )
        with patch("fabprint.auth.requests.post", return_value=resp):
            token, refresh = _login("user@example.com", "pw")
            assert token == "tok"
            assert refresh == ""


# ---------------------------------------------------------------------------
# _login — verification code flow
# ---------------------------------------------------------------------------


class TestLoginVerificationCodeFlow:
    def test_verify_code_flow(self, monkeypatch):
        _silence_ui(monkeypatch)
        monkeypatch.setattr("fabprint.ui.prompt_password", lambda prompt: "123456")

        # First call returns verifyCode, second returns token
        first_resp = _mock_response(200, json_data={"loginType": "verifyCode"})
        code_send_resp = _mock_response(200)
        second_resp = _mock_response(
            200,
            json_data={"accessToken": "code_tok", "refreshToken": "code_ref"},
        )

        with patch("fabprint.auth.requests.post") as mock_post:
            mock_post.side_effect = [first_resp, code_send_resp, second_resp]
            token, refresh = _login("user@example.com", "pw")

        assert token == "code_tok"
        assert refresh == "code_ref"

        # Three POST calls: password login, send-code, code login
        assert mock_post.call_count == 3
        # Second call is the verification code email
        assert "sendemail/code" in mock_post.call_args_list[1][0][0]
        # Third call sends the code
        third_call_json = mock_post.call_args_list[2][1]["json"]
        assert third_call_json["code"] == "123456"
        assert third_call_json["account"] == "user@example.com"

    def test_verify_code_flow_no_token_raises(self, monkeypatch):
        """Verification code flow returns but still no token."""
        _silence_ui(monkeypatch)
        monkeypatch.setattr("fabprint.ui.prompt_password", lambda prompt: "000000")

        first_resp = _mock_response(200, json_data={"loginType": "verifyCode"})
        code_send_resp = _mock_response(200)
        second_resp = _mock_response(200, json_data={"message": "bad code"})

        with patch("fabprint.auth.requests.post") as mock_post:
            mock_post.side_effect = [first_resp, code_send_resp, second_resp]
            with pytest.raises(FabprintError, match="Login failed"):
                _login("user@example.com", "pw")


# ---------------------------------------------------------------------------
# _login — TFA flow
# ---------------------------------------------------------------------------


class TestLoginTfaFlow:
    def test_tfa_flow(self, monkeypatch):
        _silence_ui(monkeypatch)
        monkeypatch.setattr("fabprint.ui.prompt_password", lambda prompt: "654321")

        first_resp = _mock_response(200, json_data={"tfaKey": "tfa_key_abc"})
        tfa_resp = _mock_response(
            200,
            json_data={"accessToken": "tfa_tok", "refreshToken": "tfa_ref"},
        )

        with patch("fabprint.auth.requests.post") as mock_post:
            mock_post.side_effect = [first_resp, tfa_resp]
            token, refresh = _login("user@example.com", "pw")

        assert token == "tfa_tok"
        assert refresh == "tfa_ref"
        assert mock_post.call_count == 2
        # Second call is the TFA endpoint
        assert "/tfa" in mock_post.call_args_list[1][0][0]
        tfa_json = mock_post.call_args_list[1][1]["json"]
        assert tfa_json["tfaKey"] == "tfa_key_abc"
        assert tfa_json["tfaCode"] == "654321"

    def test_tfa_flow_no_token_raises(self, monkeypatch):
        """TFA response has no accessToken."""
        _silence_ui(monkeypatch)
        monkeypatch.setattr("fabprint.ui.prompt_password", lambda prompt: "000000")

        first_resp = _mock_response(200, json_data={"tfaKey": "k"})
        tfa_resp = _mock_response(200, json_data={"error": "invalid code"})

        with patch("fabprint.auth.requests.post") as mock_post:
            mock_post.side_effect = [first_resp, tfa_resp]
            with pytest.raises(FabprintError, match="Login failed"):
                _login("user@example.com", "pw")

    def test_tfa_http_error(self, monkeypatch):
        """HTTP error from TFA endpoint propagates."""
        _silence_ui(monkeypatch)
        monkeypatch.setattr("fabprint.ui.prompt_password", lambda prompt: "123")

        first_resp = _mock_response(200, json_data={"tfaKey": "k"})
        tfa_resp = _mock_response(403, raise_for_status_error=True)

        with patch("fabprint.auth.requests.post") as mock_post:
            mock_post.side_effect = [first_resp, tfa_resp]
            with pytest.raises(requests.HTTPError):
                _login("user@example.com", "pw")


# ---------------------------------------------------------------------------
# _get_user_profile
# ---------------------------------------------------------------------------


class TestGetUserProfile:
    def test_returns_profile_dict(self):
        resp = _mock_response(
            200,
            json_data={"uid": 12345, "name": "Alice", "avatar": "https://img/a.png"},
        )
        with patch("fabprint.auth.requests.get", return_value=resp) as mock_get:
            profile = _get_user_profile("my_token")

        assert profile == {
            "uid": "12345",
            "name": "Alice",
            "avatar": "https://img/a.png",
        }
        # Verify auth header
        call_headers = mock_get.call_args[1]["headers"]
        assert call_headers["Authorization"] == "Bearer my_token"

    def test_missing_fields_default(self):
        resp = _mock_response(200, json_data={})
        with patch("fabprint.auth.requests.get", return_value=resp):
            profile = _get_user_profile("tok")
        assert profile == {"uid": "", "name": "", "avatar": ""}

    def test_http_error_propagates(self):
        resp = _mock_response(401, raise_for_status_error=True)
        with patch("fabprint.auth.requests.get", return_value=resp):
            with pytest.raises(requests.HTTPError):
                _get_user_profile("bad_token")


# ---------------------------------------------------------------------------
# _get_devices
# ---------------------------------------------------------------------------


class TestGetDevices:
    def test_returns_device_list(self):
        devices = [
            {"dev_id": "SN001", "name": "Printer1", "online": True},
            {"dev_id": "SN002", "name": "Printer2", "online": False},
        ]
        resp = _mock_response(200, json_data={"devices": devices})
        with patch("fabprint.auth.requests.get", return_value=resp) as mock_get:
            result = _get_devices("tok")

        assert result == devices
        call_headers = mock_get.call_args[1]["headers"]
        assert call_headers["Authorization"] == "Bearer tok"

    def test_empty_devices(self):
        resp = _mock_response(200, json_data={"devices": []})
        with patch("fabprint.auth.requests.get", return_value=resp):
            assert _get_devices("tok") == []

    def test_missing_devices_key(self):
        resp = _mock_response(200, json_data={})
        with patch("fabprint.auth.requests.get", return_value=resp):
            assert _get_devices("tok") == []

    def test_http_error_propagates(self):
        resp = _mock_response(403, raise_for_status_error=True)
        with patch("fabprint.auth.requests.get", return_value=resp):
            with pytest.raises(requests.HTTPError):
                _get_devices("tok")


# ---------------------------------------------------------------------------
# _show_devices
# ---------------------------------------------------------------------------


class TestShowDevices:
    def test_displays_devices(self, monkeypatch):
        _silence_ui(monkeypatch)
        devices = [
            {
                "name": "Workshop",
                "dev_id": "01P00A451601106",
                "dev_product_name": "P1S",
                "online": True,
            },
            {
                "name": "Lab",
                "dev_id": "01S00B999999999",
                "dev_model_name": "X1C",
                "online": False,
            },
        ]
        with patch("fabprint.auth._get_devices", return_value=devices):
            # Should not raise
            _show_devices("tok")

    def test_no_devices(self, monkeypatch):
        _silence_ui(monkeypatch)
        with patch("fabprint.auth._get_devices", return_value=[]):
            _show_devices("tok")

    def test_device_missing_optional_fields(self, monkeypatch):
        _silence_ui(monkeypatch)
        devices = [{"dev_id": "SN1"}]  # minimal device dict
        with patch("fabprint.auth._get_devices", return_value=devices):
            _show_devices("tok")
